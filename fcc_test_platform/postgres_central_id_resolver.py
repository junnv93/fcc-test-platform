"""Production ``CentralIdResolverPort`` — local int/text → central uuid (FE-P0c WIRE).

``InMemoryCentralIdResolver`` (FE-P0c Phase B) is fed by an explicit dict and is
used by fakes/tests. This module is the *production* resolver wired by the
composition root.

Two mappings, two strategies — each the industry-standard choice for its shape:

``resolve_session_uuid`` — **deterministic uuid5** (RFC 4122 §4.3, namespace +
    name). The local session is an autoincrement int that only the provider PC
    knows; central uses uuid. Issuing a central uuid at *measurement-session
    start* would couple the offline measurement loop to central availability.
    New chamber traffic derives a stable, collision-free uuid from a canonical
    ``provider_id + chamber_id + local_session_id`` name; the legacy sentinel
    retains ``provider_id:local_session_id`` for existing rows. Properties this
    buys (the reason content-addressed ids are the standard for outbox / offline-
    first sync):
      - **idempotent re-sync** — replaying the same outbox event produces the
        same session uuid, so the central ``ON CONFLICT`` upsert is a no-op
        instead of a duplicate session.
      - **offline-capable** — no central round-trip on the hot measurement path.
      - **provider/chamber-scoped** — provider and chamber identity prevent two
        independent chamber PCs' local id 1 from colliding centrally.
    The namespace is itself *derived* (``uuid5(NAMESPACE_DNS, seed)``) so there
    is no hardcoded magic-UUID literal — the seed string is the SSOT.

``resolve_project_uuid`` — **central lookup** (``SELECT id FROM projects WHERE
    project_code = %s``). Projects are central-authored reference data (created
    once, before measurement), so a lookup is correct and cheap; the result is
    cached per ``project_code`` so a sync batch issues at most one query per
    distinct project. A missing project is a loud ``CentralIdResolutionError``
    (never a silent NULL FK).

frozen-exe safe: this module imports **no** ``psycopg``. It depends only on the
injected ``connection_factory`` (a ``DbConnection`` Protocol per
``platform_database_port``); the concrete psycopg connection is built lazily by
the composition root. Enforced by ``tests/test_postgres_central_id_resolver.py``.
"""
from __future__ import annotations

import threading
import uuid
from typing import Callable, Optional

from domain.models.measurement_target_identity import normalize_identity_component
from domain.ports.output.central_id_resolver_port import (
    CentralIdResolutionError,
    ModelProjectResolution,
)
from domain.ports.output.platform_database_port import DbConnection
from fcc_test_platform.session_identity import normalize_chamber_id, session_uuid_name


__all__ = [
    'CENTRAL_SESSION_UUID_NAMESPACE',
    'MODEL_PROJECT_LOOKUP_SQL',
    'PROJECT_LOOKUP_SQL',
    'PostgresCentralIdResolver',
]


# Seed for the session-uuid namespace. The namespace is derived from this DNS
# name so the codebase carries no opaque magic-UUID literal — change the seed
# here (SSOT) to rotate the entire deterministic session-uuid space.
_SESSION_UUID_NAMESPACE_SEED = 'session.central.fcc-test-platform'

#: Derived RFC 4122 namespace for ``resolve_session_uuid``. Deterministic —
#: identical across processes/hosts, so re-sync is idempotent.
CENTRAL_SESSION_UUID_NAMESPACE: uuid.UUID = uuid.uuid5(
    uuid.NAMESPACE_DNS, _SESSION_UUID_NAMESPACE_SEED
)

#: Project lookup SQL — PostgreSQL ``%s`` paramstyle (psycopg). Quoted
#: identifiers match the central schema (``projects.id`` / ``projects.project_code``,
#: ``ux_projects_project_code`` UNIQUE). Single statement SSOT.
PROJECT_LOOKUP_SQL = 'SELECT "id" FROM "projects" WHERE "project_code" = %s'

#: Model-number → project lookup. ``DISTINCT`` because one project may register
#: the same model name more than once (revisions, re-intake) and that is *not*
#: ambiguity — ambiguity is two **different** projects. Without DISTINCT a
#: duplicate row inside one project would be misread as a cross-project clash
#: and the project would never resolve.
#:
#: No ``LIMIT``: the second row is the signal. ``device_models`` carries no
#: UNIQUE index on ``model_name`` (verified against ``central_db_schema.v1.json``),
#: so a limit-1 query would silently pick a winner — and the loser's equipment
#: would land in the report.
#: Both sides are normalised with the **same** rule as
#: ``normalize_identity_component`` (trim + collapse internal whitespace +
#: upper-case), because that is this repository's SSOT for "is this the same
#: measurement target": ``'SM-S921U'`` and ``'sm-s921u '`` name one device.
#:
#: Comparing verbatim would make the join key case-sensitive while the identity
#: it stands for is not — an operator who typed the model in a different case on
#: the web form gets `project_id` NULL, an empty §6, and a refusal that names a
#: model which *is* registered. `normalize_test_item_key` in this same equipment
#: domain applies `strip().upper()` for exactly this reason.
#:
#: No index is lost: ``device_models`` carries only ``idx_device_models_project``
#: (verified against ``central_db_schema.v1.json``), so there was never an index
#: on ``model_name`` for the raw comparison to use.
def _python_whitespace_chars() -> str:
    """``str.split()`` 이 분리자로 취급하는 문자 — **파생한다, 나열하지 않는다.**

    도메인 정규화(:func:`normalize_identity_component`)는 ``' '.join(v.split())``
    이므로 접히는 문자 집합의 정의는 **Python 이 소유한다**. 손으로 적으면 Python
    버전이 그 집합을 넓힐 때 조용히 갈린다. BMP 의 공백류는 U+3000 이 마지막이므로
    거기까지만 훑는다(수만 회 루프도 아니고 import 시 1회다).

    ``str.split()`` 은 ``isspace()`` 참인 문자 전부를 분리한다 — ``\\x1c``~``\\x1f``
    (파일/그룹/레코드/단위 구분자)까지 포함이고, ``\\ufeff``(BOM)는 **아니다**(실측).
    """
    return ''.join(
        chr(code) for code in range(0x3001) if chr(code).isspace()
    )


def _translate_pairs() -> tuple[str, str]:
    """``translate(model_name, from, to)`` 의 두 인자 — 전부 ASCII 공백으로."""
    chars = _python_whitespace_chars()
    return chars, ' ' * len(chars)


def _sql_text_literal(value: str) -> str:
    """유니코드 이스케이프 문자열 리터럴 — ``E'...'``.

    비-ASCII 를 그대로 SQL 에 박으면 클라이언트 인코딩에 따라 다르게 읽힌다.
    """
    body = ''.join(
        f'\\u{ord(ch):04x}' if ord(ch) > 0x1f or ch in "'\\" else f'\\u{ord(ch):04x}'
        for ch in value
    )
    return f"E'{body}'"


_FROM_CHARS, _TO_CHARS = _translate_pairs()

#: Model-number → project 조회.
#:
#: **PostgreSQL 의 ``\\s`` 의미에 의존하지 않는다.** 초판은 ``regexp_replace(…,'\\s+',…)``
#: 로 접었는데, 실 엔진(PG 15.13, ``en_US.utf8``) 실측 결과 PG 의 ``\\s`` 는
#: ``\\x1c``~``\\x1f`` · NBSP(U+00A0) · U+1680 · U+202F 를 **접지 않는다**. Python
#: ``str.split()`` 은 접는다. 그래서 Excel/Word 에서 복사한 NBSP 하나가 조인을 깨고,
#: 시험원은 **등록돼 있는 모델**에 대해 "장비목록이 없음"을 본다.
#:
#: 그래서 공백의 정의를 **도메인에서 파생해 ``translate`` 로 명시 치환**하고, 접기는
#: 리터럴 공백(``' +'``)만 본다 — 로케일·버전·정규식 방언에 걸리는 축이 사라진다.
#: 순서는 치환 → 접기 → 다듬기 → 대문자다(다듬기를 먼저 하면 접기가 만든 앞뒤
#: 공백이 남는다).
#:
#: 잃는 인덱스 없음: ``device_models`` 에는 ``idx_device_models_project`` 뿐이라
#: ``model_name`` 에 쓸 인덱스가 애초에 없다.
MODEL_PROJECT_LOOKUP_SQL = (
    'SELECT DISTINCT "project_id" FROM "device_models" '
    'WHERE upper(btrim(regexp_replace(translate("model_name", '
    f'{_sql_text_literal(_FROM_CHARS)}, {_sql_text_literal(_TO_CHARS)}'
    "), ' +', ' ', 'g'))) = %s"
)


def _normalize_model_number(value: object) -> str:
    """Model number → the canonical identity token used on both sides of the join.

    Delegates to the domain SSOT rather than re-implementing `strip().upper()`:
    the rule includes internal-whitespace collapsing, and a second copy of it
    would drift from the one that names the local measurement DB file.
    """
    return normalize_identity_component(value)


class PostgresCentralIdResolver:
    """Production resolver implementing ``CentralIdResolverPort``.

    ``connection_factory`` returns a fresh ``DbConnection`` per call (the
    composition root passes the same psycopg factory used by the ingestion
    writer). Project lookups open → query → close one connection each; the
    result cache means this happens at most once per distinct ``project_code``.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        connection_factory: Callable[[], DbConnection],
        session_uuid_namespace: uuid.UUID = CENTRAL_SESSION_UUID_NAMESPACE,
    ) -> None:
        if not provider_id or not str(provider_id).strip():
            raise ValueError('provider_id is required')
        self._provider_id = str(provider_id).strip()
        self._connection_factory = connection_factory
        self._session_uuid_namespace = session_uuid_namespace
        self._lock = threading.Lock()
        self._session_cache: dict[tuple[str, int], str] = {}
        self._project_cache: dict[str, str] = {}
        # Model→project cache holds **resolved projects only** — see
        # ``resolve_project_by_model_number`` for why an unresolved outcome must
        # never be cached (a transient DB blip would otherwise pin the model as
        # unresolvable for the daemon's lifetime, and registering the model
        # would have no effect until a restart).
        self._model_project_cache: dict[str, ModelProjectResolution] = {}
        # Number of project lookups actually issued to the DB — a cache hit
        # does NOT increment this. Exposed for the cache hit/miss invariant
        # (a real behavioural measurement, not a substring check).
        self._project_db_queries = 0
        self._model_project_db_queries = 0

    # ── session: deterministic uuid5 (no DB) ────────────────────────────────

    def resolve_session_uuid(
        self,
        local_session_id: int,
        *,
        chamber_id: str | None = None,
        target_identity: str | None = None,
    ) -> str:
        try:
            key = int(local_session_id)
        except (TypeError, ValueError) as exc:
            raise CentralIdResolutionError(
                f'local_session_id must be an int, got {local_session_id!r}'
            ) from exc
        chamber = normalize_chamber_id(chamber_id)
        target = str(target_identity or '').strip()
        # The target belongs in the cache key because it belongs in the name.
        # One process measures several targets in sequence, and each of their
        # databases starts its session numbering at 1 — keying on
        # ``(chamber, local_id)`` alone would hand the first target's uuid to
        # the second, reinstating through the cache the very collision the
        # name now avoids.
        cache_key = (chamber, target, key)
        with self._lock:
            cached = self._session_cache.get(cache_key)
            if cached is not None:
                return cached
            name = session_uuid_name(self._provider_id, chamber, key, target)
            resolved = str(uuid.uuid5(self._session_uuid_namespace, name))
            self._session_cache[cache_key] = resolved
            return resolved

    # ── project: central lookup + cache (loud-fail on miss) ──────────────────

    def resolve_project_uuid(self, local_project_id: Optional[str]) -> Optional[str]:
        if local_project_id is None or local_project_id == '':
            return None
        code = str(local_project_id)
        with self._lock:
            cached = self._project_cache.get(code)
            if cached is not None:
                return cached
        # Query outside the lock — DB I/O must not hold the cache mutex.
        resolved = self._lookup_project_uuid(code)
        with self._lock:
            self._project_cache[code] = resolved
        return resolved

    def _lookup_project_uuid(self, project_code: str) -> str:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(PROJECT_LOOKUP_SQL, (project_code,))
                with self._lock:
                    self._project_db_queries += 1
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        if not row or row[0] is None:
            raise CentralIdResolutionError(
                f'no central project uuid registered for project_code={project_code!r} '
                f'(SELECT id FROM projects WHERE project_code=... returned no row)'
            )
        return str(row[0])

    # ── model → project: central lookup + cache (never raises) ───────────────

    def resolve_project_by_model_number(
        self, model_number: Optional[str]
    ) -> ModelProjectResolution:
        token = _normalize_model_number(model_number)
        if not token:
            return ModelProjectResolution(reason='model_number is empty')
        with self._lock:
            cached = self._model_project_cache.get(token)
            if cached is not None:
                return cached
        # Query outside the lock — DB I/O must not hold the cache mutex.
        resolved = self._lookup_project_by_model_number(token)
        # **Only a resolved project is cached.** Caching an unresolved outcome
        # conflates two different things:
        #
        #   - "this model has no central project" — true until someone registers
        #     it, which is exactly the action the warning asks the operator to
        #     take. Caching it means the registration has no effect until the
        #     process restarts, and nobody is told that.
        #   - "the lookup failed" — a *transient* condition. One connection reset
        #     during a sync tick would otherwise pin the model as unresolvable for
        #     the daemon's whole lifetime, silently reinstating the permanent-NULL
        #     defect this method exists to fix.
        #
        # The cost of not caching is bounded: this is called **once per session
        # bucket**, not per event, so a miss costs one statement per bucket per
        # sync tick. The earlier "must not re-query per event" justification was
        # simply wrong about the call frequency.
        if resolved.is_resolved:
            with self._lock:
                self._model_project_cache[token] = resolved
        return resolved

    def _lookup_project_by_model_number(self, model_number: str) -> ModelProjectResolution:
        """One statement, all rows, then decide. Never raises on a miss.

        An unreachable/erroring central DB is reported as unresolved rather than
        propagated: this lookup is an *enrichment* of a measurement batch whose
        facts are already true, and raising here would stop the whole result
        sync because a project has not been registered yet.
        """
        try:
            connection = self._connection_factory()
        except Exception as exc:  # pragma: no cover - exercised via injected factory
            return ModelProjectResolution(
                reason=f'central project lookup unavailable: {exc}'
            )
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(MODEL_PROJECT_LOOKUP_SQL, (model_number,))
                with self._lock:
                    self._model_project_db_queries += 1
                rows = cursor.fetchall()
            finally:
                cursor.close()
        except Exception as exc:
            return ModelProjectResolution(
                reason=f'central project lookup failed: {exc}'
            )
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()

        project_ids = [str(row[0]) for row in (rows or []) if row and row[0] is not None]
        if not project_ids:
            return ModelProjectResolution(
                reason=(
                    f'model_number={model_number!r} is not registered centrally '
                    f'(no device_models row) — register the model/project to link '
                    f'this session'
                )
            )
        if len(project_ids) > 1:
            return ModelProjectResolution(
                reason=(
                    f'model_number={model_number!r} maps to {len(project_ids)} central '
                    f'projects — refusing to guess which one measured this session'
                )
            )
        return ModelProjectResolution(project_uuid=project_ids[0])

    # ── diagnostics ──────────────────────────────────────────────────────────

    @property
    def project_db_query_count(self) -> int:
        """DB lookups issued for projects (cache hits excluded). Test seam for
        the cache hit/miss invariant."""
        with self._lock:
            return self._project_db_queries

    @property
    def model_project_db_query_count(self) -> int:
        """DB lookups issued for model→project (cache hits excluded)."""
        with self._lock:
            return self._model_project_db_queries
