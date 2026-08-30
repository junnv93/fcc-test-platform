"""Execute central DB ingestion plans through an injected transaction port."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from fcc_test_platform.provider_ingestion_plan import (
    IngestionPlanStep,
    PlatformIngestionPlan,
    build_platform_ingestion_plan,
    provider_scoped_idempotency_key,
)
from domain.ports.output.platform_ingestion_port import (
    PlatformIngestionTransaction,
    PlatformIngestionWriter,
)


__all__ = [
    'COVERAGE_REFRESH_FAILED',
    'COVERAGE_REFRESH_NOT_REQUIRED',
    'COVERAGE_REFRESH_SUCCEEDED',
    'COVERAGE_REFRESH_TOKENS',
    'IngestionExecutionResult',
    'IngestionRetryPolicy',
    'IngestionStepExecution',
    'PermanentIngestionError',
    'PlatformIngestionTransaction',
    'PlatformIngestionWriter',
    'TransientIngestionError',
    'execute_platform_ingestion_plan',
]


# Coverage materialized view refresh outcome tokens (SSOT). The refresh runs on
# a dedicated autocommit connection AFTER the durable commit, so a failure is
# non-fatal (the measurement fact is already committed; a PT1H fallback cron
# re-refreshes). Recording the outcome here turns the previous silent swallow
# into an auditable signal carried through the ingestion execution evidence.
COVERAGE_REFRESH_SUCCEEDED = 'succeeded'
COVERAGE_REFRESH_FAILED = 'failed'
COVERAGE_REFRESH_NOT_REQUIRED = 'not_required'
COVERAGE_REFRESH_TOKENS = (
    COVERAGE_REFRESH_SUCCEEDED,
    COVERAGE_REFRESH_FAILED,
    COVERAGE_REFRESH_NOT_REQUIRED,
)


class TransientIngestionError(RuntimeError):
    """Retryable ingestion failure such as a deadlock or temporary disconnect."""


class PermanentIngestionError(RuntimeError):
    """Non-retryable ingestion failure such as a constraint or schema error."""


@dataclass(frozen=True)
class IngestionRetryPolicy:
    max_attempts: int = 3
    retry_backoff_seconds: tuple[float, ...] = (0.0, 0.0)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError('max_attempts must be >= 1')


@dataclass(frozen=True)
class IngestionStepExecution:
    order: int
    target_table: str
    operation: str
    idempotency_key: tuple[str, ...]
    affected_rows: int

    def to_dict(self) -> dict:
        return {
            'order': self.order,
            'target_table': self.target_table,
            'operation': self.operation,
            'idempotency_key': list(self.idempotency_key),
            'affected_rows': self.affected_rows,
        }


@dataclass(frozen=True)
class IngestionExecutionResult:
    attempted_steps: int
    applied_steps: int
    attempts: int
    committed: bool
    rolled_back: bool
    errors: tuple[str, ...] = field(default_factory=tuple)
    retry_errors: tuple[str, ...] = field(default_factory=tuple)
    steps: tuple[IngestionStepExecution, ...] = field(default_factory=tuple)
    coverage_refresh: str = COVERAGE_REFRESH_NOT_REQUIRED
    coverage_refresh_error: str = ''

    def to_dict(self) -> dict:
        return {
            'attempted_steps': self.attempted_steps,
            'applied_steps': self.applied_steps,
            'attempts': self.attempts,
            'committed': self.committed,
            'rolled_back': self.rolled_back,
            'errors': list(self.errors),
            'retry_errors': list(self.retry_errors),
            'steps': [step.to_dict() for step in self.steps],
            'coverage_refresh': self.coverage_refresh,
            'coverage_refresh_error': self.coverage_refresh_error,
        }


def execute_platform_ingestion_plan(
    plan: PlatformIngestionPlan | Mapping,
    writer: PlatformIngestionWriter,
    *,
    retry_policy: IngestionRetryPolicy | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> IngestionExecutionResult:
    policy = retry_policy or IngestionRetryPolicy()
    steps = _steps(plan)
    retry_errors: list[str] = []
    sleep = sleeper or (lambda _seconds: None)

    # Phase F (2026-05-26) — pre-scan the plan once so SERIALIZABLE isolation
    # can be set BEFORE the first statement executes (PostgreSQL rejects
    # SET TRANSACTION ISOLATION after any statement). Same scan informs
    # post-commit coverage refresh, which runs on a SEPARATE autocommit
    # connection because REFRESH MATERIALIZED VIEW CONCURRENTLY is disallowed
    # inside a transaction block.
    needs_serializable = _plan_requires_serializable(steps)
    needs_refresh = needs_serializable  # latest attempts ⇒ coverage materialized view stale

    for attempt in range(1, policy.max_attempts + 1):
        tx = writer.begin_transaction()
        if needs_serializable:
            tx.set_serializable_isolation()
        applied = 0
        executed_steps: list[IngestionStepExecution] = []
        try:
            for step in steps:
                affected_rows = _execute_step(tx, step)
                applied += 1
                executed_steps.append(_executed_step(step, affected_rows))
            tx.commit()
            coverage_refresh = COVERAGE_REFRESH_NOT_REQUIRED
            coverage_refresh_error = ''
            if needs_refresh:
                # Coverage refresh runs on a SEPARATE autocommit connection.
                # Refresh failure MUST NOT roll back the just-committed fact;
                # the PT1H fallback cron retries. Instead of swallowing it
                # silently, the outcome is recorded as an auditable signal on
                # the result (and surfaced through the execution evidence) so a
                # composition-boundary consumer can alarm on a stale view.
                try:
                    writer.refresh_coverage_materialized_view()
                    coverage_refresh = COVERAGE_REFRESH_SUCCEEDED
                except Exception as exc:
                    coverage_refresh = COVERAGE_REFRESH_FAILED
                    coverage_refresh_error = str(exc)
            return IngestionExecutionResult(
                attempted_steps=len(steps),
                applied_steps=applied,
                attempts=attempt,
                committed=True,
                rolled_back=False,
                errors=(),
                retry_errors=tuple(retry_errors),
                steps=tuple(executed_steps),
                coverage_refresh=coverage_refresh,
                coverage_refresh_error=coverage_refresh_error,
            )
        except PermanentIngestionError as exc:
            _rollback(tx)
            return _failed_result(
                len(steps),
                applied,
                attempt,
                [str(exc)],
                executed_steps,
                retry_errors=retry_errors,
            )
        except Exception as exc:
            _rollback(tx)
            error = str(exc)
            if not isinstance(exc, TransientIngestionError) or attempt >= policy.max_attempts:
                return _failed_result(
                    len(steps),
                    applied,
                    attempt,
                    [error],
                    executed_steps,
                    retry_errors=retry_errors,
                )
            retry_errors.append(error)
            _sleep_before_retry(policy, attempt, sleep)

    return _failed_result(len(steps), 0, policy.max_attempts, (), retry_errors=retry_errors)


def _steps(plan: PlatformIngestionPlan | Mapping) -> tuple[IngestionPlanStep, ...]:
    if isinstance(plan, PlatformIngestionPlan):
        return plan.steps
    payload = dict(plan)
    if 'steps' in payload:
        return tuple(
            IngestionPlanStep(
                order=int(step['order']),
                target_table=str(step['target_table']),
                operation=str(step['operation']),
                idempotency_key=tuple(str(value) for value in step['idempotency_key']),
                record=dict(step['record']),
                fk_resolution_hint=dict(step.get('fk_resolution_hint') or {}),
            )
            for step in payload['steps']
        )
    return build_platform_ingestion_plan(payload).steps


def _plan_requires_serializable(steps: tuple[IngestionPlanStep, ...]) -> bool:
    """True if any step is a measurement_attempts row with is_latest=true.

    These are the rows that trigger the FE-P0a Rule 1 prior-toggle transaction
    body — SERIALIZABLE must be set before any statement executes.
    """
    for step in steps:
        if step.target_table != 'measurement_attempts':
            continue
        if step.record.get('is_latest') is True:
            return True
    return False


def _execute_step(tx: PlatformIngestionTransaction, step: IngestionPlanStep) -> int:
    """Dispatch step to the right transaction method.

    measurement_attempts/is_latest=true → upsert_attempt (Rule 1) +
    project_results_from_latest_attempt (Rule 2 SAME-transaction projection).
    Everything else → generic upsert.
    """
    storage_key = step.idempotency_key
    if step.target_table == 'measurement_attempts':
        # The serialized plan remains legacy-compatible, while every real
        # central attempt write carries provider_id in its conflict identity.
        storage_key = provider_scoped_idempotency_key(
            step.target_table, step.record, step.idempotency_key,
        )
    if step.target_table == 'measurement_attempts' and step.record.get('is_latest') is True:
        affected = tx.upsert_attempt(
            step.record,
            storage_key,
            fk_resolution_hint=step.fk_resolution_hint or {},
        )
        # Rule 2 — measurement_results projection in SAME transaction.
        provider_result_id = (step.fk_resolution_hint or {}).get('provider_result_id')
        if provider_result_id:
            tx.project_results_from_latest_attempt(
                provider_id=step.record['provider_id'],
                provider_result_id=str(provider_result_id),
                verdict=step.record.get('verdict'),
                result_json=step.record['result_json'],
                operator=step.record.get('operator'),
                measured_at=step.record.get('measured_at'),
                condition_hash=step.record['condition_hash'],
                session_id=step.record['session_id'],
                attempt_number=int(step.record['attempt_number']),
            )
        return int(affected or 0)
    return int(tx.upsert(step.target_table, step.record, storage_key) or 0)


def _rollback(tx: PlatformIngestionTransaction) -> None:
    try:
        tx.rollback()
    except Exception:
        pass


def _sleep_before_retry(policy: IngestionRetryPolicy, attempt: int, sleeper: Callable[[float], None]) -> None:
    index = attempt - 1
    if index < len(policy.retry_backoff_seconds):
        delay = policy.retry_backoff_seconds[index]
        if delay > 0:
            sleeper(delay)


def _failed_result(
    attempted_steps: int,
    applied_steps: int,
    attempts: int,
    errors: list[str] | tuple[str, ...],
    executed_steps: list[IngestionStepExecution] | None = None,
    *,
    retry_errors: list[str] | tuple[str, ...] = (),
) -> IngestionExecutionResult:
    return IngestionExecutionResult(
        attempted_steps=attempted_steps,
        applied_steps=applied_steps,
        attempts=attempts,
        committed=False,
        rolled_back=True,
        errors=tuple(errors),
        retry_errors=tuple(retry_errors),
        steps=tuple(executed_steps or ()),
    )


def _executed_step(step: IngestionPlanStep, affected_rows: int | None) -> IngestionStepExecution:
    row_count = 0 if affected_rows is None or affected_rows < 0 else int(affected_rows)
    return IngestionStepExecution(
        order=step.order,
        target_table=step.target_table,
        operation=step.operation,
        idempotency_key=step.idempotency_key,
        affected_rows=row_count,
    )
