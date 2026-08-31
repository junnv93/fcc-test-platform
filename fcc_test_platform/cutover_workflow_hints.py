"""Shared helpers for cutover workflow hints and next-command diagnostics.

⚠️ 2026-08-31 — `scripts/` 에서 패키지로 옮겼다. 이름은 스크립트였지만 **CLI 장치가
0개인 순수 라이브러리**였고, `scripts/` 에 있는 동안은 **휠이 나르지 못했다**:
모노레포가 핀으로 이 레인을 받아도 이 파일은 오지 않아, 그것에 의존하는 컷오버
판정 도구 4개가 `ModuleNotFoundError` 로 죽었다(실측: 이사 직전 21 passed → 17 failed).

⚠️ 소비자는 **양쪽 레포에 다 있다**(모노 3 · 여기 6). 그러면 「어디로 옮길까」는
잘못된 질문이고 — 어느 쪽으로 옮겨도 반대쪽이 깨진다 — 답은 **양쪽이 같은 것을
부를 수 있는 자리**, 즉 휠이 나르는 패키지 안이다.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Mapping

_SRC_ROOT = Path(__file__).resolve().parents[1] / 'src'
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from fcc_test_contracts.common.tree_artifacts import discover_tree_artifact  # noqa: E402

_EXTRACTION_MANIFEST = discover_tree_artifact(
    __file__, 'docs', 'api', 'headless_contract_extraction_manifest.v1.json',
)


def _extraction_target_ref_flags() -> list[str]:
    """``--target-ref LANE=<lane-ref>`` for each lane the manifest extracts.

    Reads the manifest rather than restating the lanes, so a hint cannot come to
    advertise a lane set the runner no longer has. Falls back to the declared
    default when the document is unreadable: a hint is guidance, and failing to
    produce one should not take the surrounding workflow down.
    """
    try:
        manifest = json.loads(_EXTRACTION_MANIFEST.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        manifest = None
    flags: list[str] = []
    for lane in extraction_target_lanes(manifest):
        flags.extend(['--target-ref', f'{lane}=<{lane}-ref>'])
    return flags

from fcc_test_platform.evidence_primitives import placeholder_tokens as _scan_placeholder_tokens  # noqa: E402
from fcc_test_platform.extraction_evidence import (  # noqa: E402
    extraction_target_lanes,
)
from fcc_test_platform.application.platform_cutover_catalog import catalog_entry  # noqa: E402
from fcc_test_platform.application.platform_cutover_run_receipt import (  # noqa: E402
    redact_command,
    redact_env,
)
def load_workflow_hints(
    path: Path | None,
    evidence_filenames: Mapping[str, str],
    issues: list[dict] | None = None,
) -> dict[str, dict]:
    if path is None:
        return {}
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        if issues is not None:
            issues.append({
                'code': 'workflow_config_read_error',
                'path': str(path),
                'message': str(exc),
                'evidence_key': 'workflow_config',
            })
            return {}
        return {
            'workflow_config': {
                'step_id': 'workflow-config',
                'step_index': -1,
                'expected_output': str(path),
                'error': str(exc),
            }
        }
    return workflow_hints_from_config(config, evidence_filenames, str(path), issues)


def workflow_hints_from_config(
    config: Mapping | None,
    evidence_filenames: Mapping[str, str],
    path: str,
    issues: list[dict] | None = None,
) -> dict[str, dict]:
    hints: dict[str, dict] = {}
    steps = config.get('steps') if isinstance(config, Mapping) else None
    if isinstance(config, Mapping) and config.get('schema_version') != 2:
        if issues is not None:
            issues.append({
                'code': 'workflow_config_unsupported_schema_version',
                'path': f'{path}.schema_version',
                'message': 'workflow config schema_version must be 2; migrate skip_if_output_exists to reuse_verified_output',
                'evidence_key': 'workflow_config',
            })
    if not isinstance(steps, list):
        if issues is not None:
            issues.append({
                'code': 'workflow_config_missing_steps',
                'path': path,
                'message': 'workflow config must contain a steps array',
                'evidence_key': 'workflow_config',
            })
        return hints

    for index, raw_step in enumerate(steps):
        if not isinstance(raw_step, Mapping):
            if issues is not None:
                issues.append({
                    'code': 'workflow_config_invalid_step',
                    'path': f'{path}.steps[{index}]',
                    'message': 'workflow config step must be an object',
                    'evidence_key': 'workflow_config',
                })
            continue
        evidence_key = str(raw_step.get('evidence_key') or '')
        if evidence_key not in evidence_filenames:
            if issues is not None:
                issues.append({
                    'code': 'workflow_config_unknown_evidence_key',
                    'path': f'{path}.steps[{index}].evidence_key',
                    'message': f'workflow config step has unknown evidence_key {evidence_key}',
                    'evidence_key': evidence_key or 'workflow_config',
                })
            continue
        if evidence_key in hints:
            if issues is not None:
                issues.append({
                    'code': 'workflow_config_duplicate_evidence_key',
                    'path': f'{path}.steps[{index}].evidence_key',
                    'message': f'workflow config has duplicate step for {evidence_key}',
                    'evidence_key': evidence_key,
                })
            continue
        if 'skip_if_output_exists' in raw_step:
            if issues is not None:
                issues.append({
                    'code': 'workflow_config_legacy_skip_if_output_exists',
                    'path': f'{path}.steps[{index}].skip_if_output_exists',
                    'message': 'skip_if_output_exists is not supported; use reuse_verified_output with a verified receipt',
                    'evidence_key': evidence_key,
                })
            continue
        if 'reuse_verified_output' in raw_step and not isinstance(raw_step.get('reuse_verified_output'), bool):
            if issues is not None:
                issues.append({
                    'code': 'workflow_config_invalid_reuse_verified_output',
                    'path': f'{path}.steps[{index}].reuse_verified_output',
                    'message': 'reuse_verified_output must be boolean',
                    'evidence_key': evidence_key,
                })
            continue
        if raw_step.get('output') not in (None, evidence_filenames[evidence_key]):
            if issues is not None:
                issues.append({
                    'code': 'workflow_config_noncanonical_output',
                    'path': f'{path}.steps[{index}].output',
                    'message': f'workflow output must be {evidence_filenames[evidence_key]}',
                    'evidence_key': evidence_key,
                })
        catalog_entry(evidence_key)
        hint = {
            'step_id': str(raw_step.get('id') or evidence_key),
            'step_index': index,
            'expected_output': raw_step.get('output') or evidence_filenames[evidence_key],
            'reuse_verified_output': bool(raw_step.get('reuse_verified_output', False)),
        }
        command = raw_step.get('command')
        suggested_command = raw_step.get('suggested_command')
        if isinstance(command, list):
            hint['command'] = redact_command([str(part) for part in command])
        if isinstance(suggested_command, list):
            hint['suggested_command'] = redact_command([str(part) for part in suggested_command])
        env = raw_step.get('env')
        if isinstance(env, Mapping) and env:
            hint['env'] = redact_env(env)
        hints[evidence_key] = hint
    if issues is not None:
        for evidence_key in evidence_filenames:
            if evidence_key not in hints:
                issues.append({
                    'code': 'workflow_config_missing_evidence_key',
                    'path': f'{path}.steps',
                    'message': f'workflow config has no step for {evidence_key}',
                    'evidence_key': evidence_key,
                })
    return hints


def attach_issue_hints(issues: list[dict], workflow_hints: Mapping[str, dict]) -> list[dict]:
    if not workflow_hints:
        return issues
    enriched = []
    for issue in issues:
        evidence_key = issue.get('evidence_key')
        if evidence_key in workflow_hints:
            with_hint = dict(issue)
            with_hint['workflow_hint'] = workflow_hints[evidence_key]
            enriched.append(with_hint)
        else:
            enriched.append(issue)
    return enriched


def next_commands(
    issues: list[dict],
    workflow_hints: Mapping[str, dict],
    evidence_filenames: Mapping[str, str],
    evidence_root: str | Path | None = None,
) -> list[dict]:
    by_key: dict[str, set[str]] = {}
    for issue in issues:
        evidence_key = str(issue.get('evidence_key') or '')
        if evidence_key in evidence_filenames:
            by_key.setdefault(evidence_key, set()).add(str(issue.get('code') or 'issue'))
    commands = []
    evidence_order = {key: index for index, key in enumerate(evidence_filenames)}
    ordered_keys = sorted(
        by_key,
        key=lambda key: (
            0 if not workflow_hints or key in workflow_hints else 1,
            _int(workflow_hints.get(key, {}).get('step_index'), evidence_order.get(key, 9999)),
            evidence_order.get(key, 9999),
        ),
    )
    for evidence_key in ordered_keys:
        hint = workflow_hints.get(key := evidence_key) or default_workflow_hint(
            key,
            evidence_filenames[key],
            evidence_root=evidence_root,
            step_index=evidence_order.get(key, 9999),
        )
        suggested_command = hint.get('suggested_command') or hint.get('command') or []
        tokens = placeholder_tokens(suggested_command)
        commands.append({
            'evidence_key': evidence_key,
            'reason_codes': sorted(by_key[evidence_key]),
            'step_id': hint.get('step_id', evidence_key),
            'expected_output': hint.get('expected_output', evidence_filenames[evidence_key]),
            'suggested_command': suggested_command,
            'contains_placeholders': bool(tokens),
            'placeholder_tokens': tokens,
        })
    return commands


def default_workflow_hint(
    evidence_key: str,
    filename: str,
    *,
    evidence_root: str | Path | None = None,
    step_index: int = 9999,
) -> dict:
    output_path = _config_path(str(evidence_root or '').replace('\\', '/'), filename)
    return {
        'step_id': evidence_key.replace('_', '-'),
        'step_index': step_index,
        'expected_output': filename,
        'suggested_command': suggested_command(evidence_key, output_path),
    }


def suggested_command(evidence_key: str, output_path: str) -> list[str]:
    catalog_entry(evidence_key)
    if evidence_key == 'hardware_smoke':
        return [
            'python',
            'scripts/headless_hardware_smoke_evidence.py',
            'collect',
            '--db-path', '<lab-headless-db-path>',
            '--job-id', '<completed-hardware-smoke-job-id>',
            '--output', output_path,
        ]
    if evidence_key == 'db_migration':
        return [
            'python',
            'scripts/platform_db_migration_runner.py',
            '--dsn', '<postgres-dsn>',
            '--output', output_path,
            '--applied-by', '<operator-or-automation-id>',
            '--require-valid',
        ]
    if evidence_key == 'ingestion_execution':
        return [
            'python',
            'scripts/platform_ingestion_execution_evidence.py',
            'execute',
            '--dsn', '<postgres-dsn>',
            '--plan', '<ingestion-plan.json>',
            '--provider-id', '<provider-id>',
            '--session-id', '<session-id>',
            '--database-name', '<database-name>',
            '--evidence-id', '<ingestion-evidence-id>',
            '--output', output_path,
        ]
    if evidence_key == 'artifact_sync':
        return [
            'python',
            'scripts/platform_artifact_sync_worker.py',
            '--source-root', '<lab-artifact-root>',
            '--destination-root', '<platform-artifact-root>',
            '--source-root-id', '<source-root-id>',
            '--destination-root-id', '<destination-root-id>',
            '--provider-id', '<provider-id>',
            '--session-id', '<session-id>',
            '--sync-job-id', '<sync-job-id>',
            '--records-json', '<artifact-sync-records.json>',
            '--copy-missing',
            '--output', output_path,
            '--require-valid',
        ]
    if evidence_key == 'db_only_report_reconstruction':
        return [
            'python',
            'scripts/unlicensed_report_reconstruction_evidence.py',
            'execute',
            '--dsn', '<postgres-dsn>',
            '--provider-id', '<provider-id>',
            '--session-id', '<session-id>',
            '--report-run-id', '<report-run-id>',
            '--migration-evidence-id', '<migration-evidence-id>',
            '--ingestion-batch-id', '<ingestion-batch-id>',
            '--output-dir', '<report-output-dir>',
            '--artifact-root', '<platform-artifact-root>',
            '--reviewed-by', '<reviewer>',
            '--output', output_path,
        ]
    if evidence_key == 'extraction_package':
        # Derived from the manifest, like the runner it is a hint for. Naming
        # the lanes here as well is how a hint comes to describe a command that
        # no longer exists — and this is an operator's first contact with it.
        return [
            'python',
            'scripts/platform_extraction_runner.py',
            '--target-root', 'artifacts/extraction/staged',
            '--output', output_path,
            '--evidence-id', '<extraction-evidence-id>',
            *_extraction_target_ref_flags(),
            '--require-valid',
        ]
    if evidence_key == 'performance_smoke':
        return [
            'python',
            'scripts/headless_api_benchmark.py',
            '--target-base-url', '<deployed-platform-base-url>',
            '--benchmark-id', '<benchmark-id>',
            '--iterations', '10',
            '--workers', '4',
            '--max-p95-ms', '500',
            '--output', output_path,
        ]
    if evidence_key == 'backup_restore_drill':
        return [
            'python',
            'scripts/platform_backup_restore_runner.py',
            '--restore-point-id', '<restore-point-id>',
            '--database-name', '<database-name>',
            '--db-backup-file', '<db-backup-file>',
            '--source-root', '<platform-artifact-root>',
            '--backup-root', '<backup-root>',
            '--restore-root', '<restore-root>',
            '--artifact', '<artifact-relative-path>',
            '--report-output', '<report-output-relative-path>',
            '--output', output_path,
            '--require-valid',
        ]
    if evidence_key == 'identity_policy':
        return [
            'python',
            'scripts/platform_identity_policy.py',
            'publish',
            '--manifest', '<validated-identity-policy.json>',
            '--output', output_path,
            '--require-valid',
        ]
    if evidence_key == 'service_deployment':
        return [
            'python',
            'scripts/provider_service_deployment_evidence.py',
            'collect',
            '--provider-id', '<provider-id>',
            '--host-id', '<lab-host-id>',
            '--deployed-at', '<ISO-8601 timestamp>',
            '--service-manager', '<nssm|task-scheduler|company-process-manager>',
            '--start-mode', '<automatic|manual>',
            '--log-collector', '<log-collector-name>',
            '--log-retention-days', '<log-retention-days>',
            '--alert-channel', '<alert-channel>',
            '--alert-target', '<alert-target>',
            '--alert-interval-seconds', '<alert-interval-seconds>',
            '--output', output_path,
            '--require-valid',
        ]
    if evidence_key == 'idp_deployment':
        return [
            'python',
            'scripts/platform_idp_deployment_collect.py',
            '--evidence-id', '<idp-evidence-id>',
            '--provider-key', '<idp-provider-key>',
            '--issuer', '<idp-issuer-url>',
            '--client-id', '<frontend-client-id>',
            '--redirect-uri', '<frontend-redirect-uri>',
            '--scope', '<oidc-scope>',
            '--public-client',
            '--pkce-required',
            '--browser-login-verified',
            '--redirect-state-verified',
            '--token-exchange-verified',
            '--access-token-audience-verified',
            '--role-claim-verified',
            '--logout-clears-session',
            '--output', output_path,
            '--require-valid',
        ]
    if evidence_key == 'frontend_deployment':
        return [
            'python',
            'scripts/platform_frontend_deployment_collect.py',
            '--evidence-id', '<frontend-deployment-evidence-id>',
            '--app-url', '<deployed-frontend-app-url>',
            '--backend-base-url', '<deployed-platform-api-url>',
            '--hosting-provider', '<frontend-hosting-provider>',
            '--build-version', '<frontend-build-version>',
            '--build-root', '<frontend-build-root>',
            '--environment-name', '<frontend-environment-name>',
            '--environment-variable', 'BACKEND_BASE_URL=<deployed-platform-api-url>',
            '--immutable-asset-url', '<deployed-immutable-asset-url>',
            '--output', output_path,
            '--require-valid',
        ]
    if evidence_key == 'frontend_browser_qa':
        return [
            'python',
            'scripts/platform_frontend_browser_qa.py',
            '--app-url', '<deployed-frontend-app-url>',
            '--provider-api-url', '<deployed-platform-api-url>',
            '--evidence-id', '<frontend-browser-qa-evidence-id>',
            '--output', output_path,
            '--artifact-root', '<frontend-qa-artifact-root>',
            '--screenshot-dir', '<frontend-qa-screenshot-dir>',
            '--browser', '<chrome|edge>',
            '--auth-flow-verified',
            '--require-valid',
        ]
    if evidence_key == 'rbac_assignment':
        return [
            'python',
            'scripts/platform_rbac_assignment_collect.py',
            '--dsn', '<postgres-dsn>',
            '--output', output_path,
            '--evidence-id', '<rbac-evidence-id>',
            '--require-valid',
        ]
    raise RuntimeError(f'no suggested-command builder bound to catalog key: {evidence_key}')


def placeholder_tokens(command: object) -> list[str]:
    if not isinstance(command, list):
        return []
    tokens: set[str] = set()
    for part in command:
        tokens.update(_scan_placeholder_tokens(str(part)))
    return sorted(tokens)


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _config_path(root: str, filename: str) -> str:
    root_text = root.replace('\\', '/').rstrip('/')
    return f'{root_text}/{filename}' if root_text else filename
