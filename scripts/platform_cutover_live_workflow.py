"""Config-driven live evidence workflow for platform cutover."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Callable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from scripts.platform_cutover_bundle import EVIDENCE_FILENAMES
from scripts.platform_cutover_completion_audit import build_completion_audit
from fcc_test_platform.cutover_workflow_hints import redact_command, suggested_command
from fcc_test_platform.application.platform_cutover_catalog import catalog_entry
from fcc_test_platform.application.platform_cutover_context import build_context_read_issue
from fcc_test_platform.cutover_readiness import validate_collector_manifest
from fcc_test_platform.application.platform_cutover_run_receipt import (
    FileObservation,
    ReceiptBinding,
    atomic_write_receipt,
    build_receipt,
    collector_identity,
    file_sha256,
    is_sha256_digest,
    observe_file,
    redact_env,
    redact_process_output,
    receipt_mismatches,
    receipt_path_for,
    safe_workflow_fingerprint,
)
from fcc_test_platform.evidence_primitives import (
    is_placeholder_token as _is_placeholder_token,
    placeholder_tokens as _placeholder_tokens,
)


CommandRunner = Callable[..., 'CommandResult']
FileObserver = Callable[[Path], FileObservation]
JsonLoader = Callable[[Path], Mapping]

WORKFLOW_SCHEMA_VERSION = 2
LEGACY_RESUME_FIELD = 'skip_if_output_exists'
RESUME_FIELD = 'reuse_verified_output'


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ''
    stderr: str = ''


@dataclass(frozen=True)
class _QuarantineResult:
    status: str
    path: Path | None = None


_QUARANTINE_ABSENT = 'absent'
_QUARANTINE_MOVED = 'moved'
_QUARANTINE_FAILED = 'failed'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Run configured live cutover evidence collection workflow.')
    subparsers = parser.add_subparsers(dest='command', required=True)
    template = subparsers.add_parser('template')
    template.add_argument('--evidence-root', default='artifacts/cutover/evidence')
    template.add_argument('--provider-id', default='fcc-unlicensed-conducted')
    template.add_argument('--cutover-candidate-id', default='cutover-YYYY-MM-DD')
    template.add_argument('--evaluated-at', default='<ISO-8601 timestamp>')
    template.add_argument('--collected-at', default='<ISO-8601 timestamp>')
    template.add_argument('--central-db-schema', default='docs/platform/central_db_schema.v1.json')
    template.add_argument('--extraction-manifest', default='docs/api/headless_contract_extraction_manifest.v1.json')
    template.add_argument('--bundle-output', default='artifacts/cutover/final_cutover_bundle.json')
    template.add_argument('--output', default='')
    plan = subparsers.add_parser('plan')
    plan.add_argument('--config', required=True)
    plan.add_argument('--summary-output', default='')
    render = subparsers.add_parser('render')
    render.add_argument('--config', required=True)
    render.add_argument('--values', required=True)
    render.add_argument('--output', required=True)
    render.add_argument('--allow-unresolved', action='store_true')
    values_template = subparsers.add_parser('values-template')
    values_template.add_argument('--config', required=True)
    values_template.add_argument('--output', default='')
    run = subparsers.add_parser('run')
    run.add_argument('--config', required=True)
    run.add_argument('--summary-output', default='')
    run.add_argument('--require-complete', action='store_true')
    args = parser.parse_args(argv)

    if args.command == 'template':
        config = build_workflow_template(
            evidence_root=args.evidence_root,
            provider_id=args.provider_id,
            cutover_candidate_id=args.cutover_candidate_id,
            evaluated_at=args.evaluated_at,
            collected_at=args.collected_at,
            central_db_schema=args.central_db_schema,
            extraction_manifest=args.extraction_manifest,
            bundle_output=args.bundle_output,
        )
        payload = json.dumps(config, sort_keys=True, indent=2)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + '\n', encoding='utf-8')
        print(payload)
        return 0

    config = _read_json(Path(args.config))
    if args.command == 'values-template':
        values = build_values_template(config)
        payload = json.dumps(values, sort_keys=True, indent=2)
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload + '\n', encoding='utf-8')
        print(payload)
        return 0

    if args.command == 'render':
        values = _read_json(Path(args.values))
        rendered, issues = render_workflow_config(config, values=values)
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(rendered, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        summary = {
            'schema_version': WORKFLOW_SCHEMA_VERSION,
            'rendered': not issues or args.allow_unresolved,
            'output': str(output_path),
            'issues': issues,
        }
        print(json.dumps(summary, sort_keys=True, indent=2))
        return 0 if summary['rendered'] else 1

    summary = run_workflow(config, execute=args.command == 'run')
    payload = json.dumps(summary, sort_keys=True, indent=2)
    if args.summary_output:
        output_path = Path(args.summary_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + '\n', encoding='utf-8')
    print(payload)
    if args.command == 'plan':
        return 0 if summary['config_valid'] else 2
    if args.require_complete and not summary['completion_audit']['complete']:
        return 1
    return 0 if summary['config_valid'] and summary['workflow_succeeded'] else 1


def run_workflow(
    config: Mapping,
    *,
    execute: bool,
    command_runner: CommandRunner | None = None,
    sleeper: Callable[[float], None] | None = None,
    file_observer: FileObserver | None = None,
    json_loader: JsonLoader | None = None,
    receipt_clock: Callable[[], str] | None = None,
) -> dict:
    runner = command_runner or _subprocess_runner
    sleep = sleeper or time.sleep
    observer = file_observer or observe_file
    loader = json_loader or _read_json
    contexts, context_digests, context_issues = _load_validation_contexts(config, loader)

    errors = validate_config(config, require_receipt_contexts=execute)
    if execute:
        errors.extend(_execution_config_errors(config))
        errors.extend(context_issues)
    execution_preflight_issues = [] if execute else _execution_config_errors(config)
    evidence_root = Path(str(config.get('evidence_root') or ''))
    steps = [_step_summary(step, evidence_root) for step in config.get('steps') or []]
    required_evidence_coverage = _required_evidence_coverage(config)
    if errors:
        return {
            'schema_version': WORKFLOW_SCHEMA_VERSION,
            'mode': 'run' if execute else 'plan',
            'config_sha256': _config_sha256(config),
            'config_valid': False,
            'workflow_succeeded': False,
            'config_errors': errors,
            'execution_preflight_issues': execution_preflight_issues,
            'validation_context_issues': context_issues,
            'steps': steps,
            'step_counts': _step_counts(steps),
            'required_evidence_coverage': required_evidence_coverage,
            'completion_audit': {'complete': False, 'decision': 'not_complete'},
        }

    workflow_fingerprint = safe_workflow_fingerprint(config)
    executed_steps = []
    if execute:
        evidence_root.mkdir(parents=True, exist_ok=True)
        halted = False
        stop_on_failure = bool(config.get('stop_on_failure'))
        for raw_step in config['steps']:
            if halted:
                executed_steps.append(_not_run_step(raw_step, evidence_root, reason='previous_step_failed'))
                continue
            step_result = _run_step(
                raw_step,
                config,
                evidence_root,
                runner,
                sleep,
                file_observer=observer,
                json_loader=loader,
                contexts=contexts,
                context_digests=context_digests,
                workflow_fingerprint=workflow_fingerprint,
                receipt_clock=receipt_clock,
            )
            executed_steps.append(step_result)
            if stop_on_failure and step_result.get('succeeded') is False:
                halted = True
    else:
        executed_steps = steps

    audit = build_completion_audit(
        evidence_root=evidence_root,
        provider_id=str(config['provider_id']),
        cutover_candidate_id=str(config['cutover_candidate_id']),
        evaluated_at=str(config['evaluated_at']),
        collected_at=str(config['collected_at']),
        central_db_schema_path=_context_path_for_audit(
            config,
            context_digests,
            'central_db_schema',
        ),
        extraction_manifest_path=_context_path_for_audit(
            config,
            context_digests,
            'extraction_manifest',
        ),
        workflow_config=config,
    )
    bundle_output = _write_final_bundle_if_complete(config, audit, write=execute)
    workflow_succeeded = all(step.get('succeeded') is not False for step in executed_steps)
    return {
        'schema_version': WORKFLOW_SCHEMA_VERSION,
        'mode': 'run' if execute else 'plan',
        'config_sha256': _config_sha256(config),
        'config_valid': True,
        'workflow_succeeded': workflow_succeeded,
        'execution_preflight_issues': execution_preflight_issues,
        'validation_context_issues': context_issues,
        'evidence_root': str(evidence_root),
        'steps': executed_steps,
        'step_counts': _step_counts(executed_steps),
        'required_evidence_coverage': required_evidence_coverage,
        'completion_audit': audit,
        'bundle_output': bundle_output,
    }


def _required_evidence_coverage(config: Mapping) -> dict:
    """Report which required cutover evidence keys have no configured step.

    Cutover completeness is orthogonal to per-step structural validity: a
    single-step config is structurally valid (for testing one collector) but
    is not a complete cutover workflow. This non-blocking diagnostic surfaces
    the gap at plan time so an operator sees every category that still needs a
    collector step — the final completion audit independently requires all
    evidence files to exist before declaring cutover complete.
    """
    covered = {
        _text(step.get('evidence_key'))
        for step in (config.get('steps') or [])
        if isinstance(step, Mapping)
    }
    missing = [key for key in EVIDENCE_FILENAMES if key not in covered]
    return {
        'required_count': len(EVIDENCE_FILENAMES),
        'covered_count': len(EVIDENCE_FILENAMES) - len(missing),
        'complete': not missing,
        'missing_required_evidence_steps': missing,
        'diagnostics': [
            {
                'code': 'missing_required_evidence_step',
                'evidence_key': key,
                'message': f'no workflow step configured for required cutover evidence {key}',
            }
            for key in missing
        ],
    }


def _load_validation_contexts(
    config: Mapping,
    loader: JsonLoader,
) -> tuple[dict[str, Mapping | None], dict[str, str], list[dict]]:
    """Load validator contexts once and bind their content to the run.

    Context loading is deliberately separate from config validation.  A
    workflow can be structurally valid while a live context is unavailable;
    the collector output still has to fail closed when its catalog validator
    requires that context.
    """
    contexts: dict[str, Mapping | None] = {}
    digests: dict[str, str] = {}
    issues: list[dict] = []
    for context_name, config_key in (
        ('central_db_schema', 'central_db_schema'),
        ('extraction_manifest', 'extraction_manifest'),
    ):
        path = _optional_path(config.get(config_key))
        if path is None:
            contexts[context_name] = None
            digests[context_name] = ''
            issues.append(_error(
                'missing_required_context',
                config_key,
                f'{context_name} is required for receipt-bound workflow execution',
            ))
            continue
        try:
            value = loader(path)
        except Exception:
            contexts[context_name] = None
            digests[context_name] = ''
            issues.append(build_context_read_issue(config_key))
            continue
        if not isinstance(value, Mapping):
            contexts[context_name] = None
            digests[context_name] = ''
            issues.append(_error(
                'context_invalid',
                config_key,
                f'{context_name} must be a JSON object',
            ))
            continue
        contexts[context_name] = value
        digest = file_sha256(path)
        if not is_sha256_digest(digest):
            digests[context_name] = ''
            issues.append(_error(
                'context_digest_invalid',
                config_key,
                f'{context_name} must have a non-empty 64-hex SHA-256 content digest',
            ))
            continue
        digests[context_name] = digest
    return contexts, digests, issues


def validate_config(config: Mapping, *, require_receipt_contexts: bool = False) -> list[dict]:
    errors: list[dict] = []
    if config.get('schema_version') != WORKFLOW_SCHEMA_VERSION:
        errors.append(_error(
            'workflow_config_unsupported_schema_version',
            'schema_version',
            f'workflow config schema_version must be {WORKFLOW_SCHEMA_VERSION}',
        ))
    for key in ('evidence_root', 'provider_id', 'cutover_candidate_id', 'evaluated_at', 'collected_at'):
        if not _text(config.get(key)):
            errors.append(_error('missing_required_field', key, f'{key} is required'))
    if require_receipt_contexts:
        for key in ('central_db_schema', 'extraction_manifest'):
            if not _text(config.get(key)):
                errors.append(_error(
                    'missing_required_context',
                    key,
                    f'{key} is required for receipt-bound workflow execution',
                ))
    if 'stop_on_failure' in config and not isinstance(config.get('stop_on_failure'), bool):
        errors.append(_error('invalid_stop_on_failure', 'stop_on_failure', 'stop_on_failure must be boolean'))
    steps = config.get('steps')
    if not isinstance(steps, list) or not steps:
        errors.append(_error('missing_steps', 'steps', 'at least one workflow step is required'))
        return errors
    seen_keys: set[str] = set()
    for index, raw in enumerate(steps):
        path = f'steps[{index}]'
        step = raw if isinstance(raw, Mapping) else {}
        evidence_key = _text(step.get('evidence_key'))
        if evidence_key not in EVIDENCE_FILENAMES:
            errors.append(_error('unknown_evidence_key', f'{path}.evidence_key', 'evidence_key must be a required cutover evidence key'))
        elif evidence_key in seen_keys:
            errors.append(_error('duplicate_evidence_key', f'{path}.evidence_key', 'evidence_key must be unique in the workflow'))
        seen_keys.add(evidence_key)
        expected_output = _text(step.get('output'))
        if evidence_key in EVIDENCE_FILENAMES and expected_output != EVIDENCE_FILENAMES[evidence_key]:
            errors.append(_error('output_filename_mismatch', f'{path}.output', f'output must be {EVIDENCE_FILENAMES[evidence_key]}'))
        command = step.get('command')
        if not isinstance(command, list) or not [_text(part) for part in command if _text(part)]:
            errors.append(_error('missing_command', f'{path}.command', 'command must be a non-empty array'))
        timeout = _float(step.get('timeout_seconds'), 0.0)
        if timeout <= 0:
            errors.append(_error('invalid_timeout', f'{path}.timeout_seconds', 'timeout_seconds must be positive'))
        retries = _int(step.get('retries'), 0)
        if retries < 0:
            errors.append(_error('invalid_retries', f'{path}.retries', 'retries must be >= 0'))
        backoff = _float(step.get('retry_backoff_seconds'), 0.0)
        if backoff < 0:
            errors.append(_error('invalid_retry_backoff', f'{path}.retry_backoff_seconds', 'retry_backoff_seconds must be >= 0'))
        if LEGACY_RESUME_FIELD in step:
            errors.append(_error(
                'workflow_config_legacy_skip_if_output_exists',
                f'{path}.{LEGACY_RESUME_FIELD}',
                f'{LEGACY_RESUME_FIELD} is not supported; use {RESUME_FIELD} with a verified receipt',
            ))
        if RESUME_FIELD in step and not isinstance(step.get(RESUME_FIELD), bool):
            errors.append(_error(
                'invalid_reuse_verified_output',
                f'{path}.{RESUME_FIELD}',
                f'{RESUME_FIELD} must be boolean',
            ))
        env = step.get('env')
        if env is not None:
            if not isinstance(env, Mapping):
                errors.append(_error('invalid_env', f'{path}.env', 'env must be an object of string values'))
            else:
                for name, value in env.items():
                    if not _text(name) or not isinstance(value, (str, int, float, bool)):
                        errors.append(_error('invalid_env', f'{path}.env.{name}', 'env names must be non-empty and values must be scalar'))
    return errors


def render_workflow_config(config: Mapping, *, values: Mapping) -> tuple[dict, list[dict]]:
    rendered = _render_object(config, values=values)
    steps = []
    for raw in rendered.get('steps') or []:
        step = dict(raw) if isinstance(raw, Mapping) else {}
        suggested = step.get('suggested_command')
        if isinstance(suggested, list) and suggested:
            step['command'] = [str(part) for part in suggested]
        steps.append(step)
    rendered['steps'] = steps
    issues = _unresolved_placeholder_issues(rendered)
    issues.extend(_rendered_output_issues(rendered))
    issues.extend(_rendered_workflow_contract_issues(rendered))
    return rendered, issues


def _rendered_workflow_contract_issues(config: Mapping) -> list[dict]:
    issues: list[dict] = []
    if config.get('schema_version') != WORKFLOW_SCHEMA_VERSION:
        issues.append(_error(
            'workflow_config_unsupported_schema_version',
            'schema_version',
            f'workflow config schema_version must be {WORKFLOW_SCHEMA_VERSION}',
        ))
    for index, raw_step in enumerate(config.get('steps') or []):
        if not isinstance(raw_step, Mapping):
            continue
        if LEGACY_RESUME_FIELD in raw_step:
            issues.append(_error(
                'workflow_config_legacy_skip_if_output_exists',
                f'steps[{index}].{LEGACY_RESUME_FIELD}',
                f'{LEGACY_RESUME_FIELD} is not supported; use {RESUME_FIELD} with a verified receipt',
            ))
    return issues


def _rendered_output_issues(config: Mapping) -> list[dict]:
    issues: list[dict] = []
    evidence_root = str(config.get('evidence_root') or '')
    steps = config.get('steps')
    if not isinstance(steps, list):
        return issues
    for index, raw in enumerate(steps):
        step = raw if isinstance(raw, Mapping) else {}
        output = _text(step.get('output'))
        if not output:
            continue
        expected_paths = {
            output.replace('\\', '/'),
            _config_path(evidence_root, output),
        }
        command_parts = [str(part).replace('\\', '/') for part in (step.get('command') or [])]
        if not _command_contains_output_path(command_parts, expected_paths):
            issues.append(_error(
                'missing_output_argument',
                f'steps[{index}].command',
                'rendered command must include the expected evidence output path',
            ))
    return issues


def _command_contains_output_path(command_parts: list[str], expected_paths: set[str]) -> bool:
    expected = {path.strip('/') for path in expected_paths if path}
    for part in command_parts:
        candidates = [part]
        if '=' in part:
            candidates.append(part.split('=', 1)[1])
        for candidate in candidates:
            normalized = candidate.strip().strip('"\'').replace('\\', '/').strip('/')
            if normalized in expected:
                return True
            if any(normalized.endswith('/' + path) for path in expected):
                return True
    return False


def build_values_template(config: Mapping) -> dict:
    tokens = sorted({
        token
        for _path, value in _walk_strings(config)
        for token in _placeholder_tokens(value)
    })
    return {token: f'<{token}>' for token in tokens}


def _execution_config_errors(config: Mapping) -> list[dict]:
    errors: list[dict] = []
    steps = config.get('steps')
    if not isinstance(steps, list):
        return errors
    for index, raw in enumerate(steps):
        step = raw if isinstance(raw, Mapping) else {}
        for issue in _command_placeholder_issues(step, index):
            errors.append(issue)
        joined = ' '.join(_text(part) for part in (step.get('command') or []))
        if 'replace this placeholder' in joined:
            errors.append(_error(
                'unresolved_placeholder',
                f'steps[{index}].command',
                'run mode requires replacing the template placeholder command',
            ))
    errors.extend(_rendered_output_issues(config))
    return errors


def _unresolved_placeholder_issues(config: Mapping) -> list[dict]:
    issues: list[dict] = []
    for path, value in _walk_strings(config):
        if '<' in value and '>' in value:
            issues.append(_error(
                'unresolved_placeholder',
                path,
                'replace placeholder value before running live workflow',
            ))
    return issues


def _command_placeholder_issues(step: Mapping, index: int) -> list[dict]:
    issues: list[dict] = []
    for part_index, part in enumerate(step.get('command') or []):
        text = _text(part)
        if _is_placeholder_token(text):
            issues.append(_error(
                'unresolved_placeholder',
                f'steps[{index}].command[{part_index}]',
                'run mode requires replacing placeholder command tokens with real collector values',
            ))
    return issues


def build_workflow_template(
    *,
    evidence_root: str,
    provider_id: str,
    cutover_candidate_id: str,
    evaluated_at: str,
    collected_at: str,
    central_db_schema: str,
    extraction_manifest: str,
    bundle_output: str = 'artifacts/cutover/final_cutover_bundle.json',
) -> dict:
    return {
        'schema_version': WORKFLOW_SCHEMA_VERSION,
        'evidence_root': evidence_root,
        'provider_id': provider_id,
        'cutover_candidate_id': cutover_candidate_id,
        'evaluated_at': evaluated_at,
        'collected_at': collected_at,
        'central_db_schema': central_db_schema,
        'extraction_manifest': extraction_manifest,
        'bundle_output': bundle_output,
        'stop_on_failure': True,
        'steps': [
            _template_step(key, evidence_root=evidence_root)
            for key in EVIDENCE_FILENAMES
        ],
    }


def _template_step(evidence_key: str, *, evidence_root: str) -> dict:
    filename = EVIDENCE_FILENAMES[evidence_key]
    output_path = _config_path(evidence_root, filename)
    return {
        'id': evidence_key.replace('_', '-'),
        'evidence_key': evidence_key,
        'output': filename,
        'command': [
            'python',
            '-c',
            (
                'raise SystemExit('
                '"replace this placeholder with the real collector command '
                f'for {evidence_key} writing {filename}"'
                ')'
            ),
        ],
        'timeout_seconds': 300,
        'retries': 0,
        'retry_backoff_seconds': 0,
        RESUME_FIELD: True,
        'env': {},
        'suggested_command': suggested_command(evidence_key, output_path),
    }


def _config_path(root: str, filename: str) -> str:
    root_text = root.replace('\\', '/').rstrip('/')
    return f'{root_text}/{filename}' if root_text else filename


def _parent_config_path(path: str) -> str:
    text = path.replace('\\', '/')
    return text.rsplit('/', 1)[0] if '/' in text else '.'


def _run_step(
    raw_step: Mapping,
    config: Mapping,
    evidence_root: Path,
    runner: CommandRunner,
    sleeper: Callable[[float], None],
    *,
    file_observer: FileObserver,
    json_loader: JsonLoader,
    contexts: Mapping[str, Mapping | None],
    context_digests: Mapping[str, str],
    workflow_fingerprint: str,
    receipt_clock: Callable[[], str] | None,
) -> dict:
    started = time.perf_counter()
    command = [str(part) for part in raw_step['command']]
    env_overrides = _step_env(raw_step)
    cwd = Path(str(raw_step.get('cwd') or PROJECT_ROOT))
    timeout = float(raw_step.get('timeout_seconds'))
    max_attempts = int(raw_step.get('retries', 0)) + 1
    retry_backoff_seconds = float(raw_step.get('retry_backoff_seconds', 0.0) or 0.0)
    output_path = evidence_root / str(raw_step['output'])
    evidence_key = _text(raw_step.get('evidence_key'))
    entry = catalog_entry(evidence_key)
    output_observation = file_observer(output_path)
    binding = ReceiptBinding(
        provider_id=str(config.get('provider_id') or ''),
        cutover_candidate_id=str(config.get('cutover_candidate_id') or ''),
        workflow_fingerprint=workflow_fingerprint,
        central_db_schema_sha256=str(context_digests.get('central_db_schema') or ''),
        extraction_manifest_sha256=str(context_digests.get('extraction_manifest') or ''),
        evidence_key=evidence_key,
        canonical_filename=entry.canonical_filename,
        collector_identity=collector_identity(evidence_key),
    )
    receipt_path = receipt_path_for(output_path)
    resume_diagnostics: list[dict] = []
    if bool(raw_step.get(RESUME_FIELD)):
        reused = _try_reuse_verified_output(
            raw_step,
            evidence_root=evidence_root,
            output_path=output_path,
            receipt_path=receipt_path,
            output_observation=output_observation,
            binding=binding,
            file_observer=file_observer,
            json_loader=json_loader,
            contexts=contexts,
        )
        if reused is not None:
            return {
                **_step_summary(raw_step, evidence_root),
                **reused,
                'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
            }
        resume_diagnostics.extend(_reuse_diagnostics(
            output_path=output_path,
            receipt_path=receipt_path,
            output_observation=output_observation,
            binding=binding,
            json_loader=json_loader,
            contexts=contexts,
        ))
    # A collector must create or replace the canonical output during this
    # invocation.  Moving any unverified pre-existing file out of the way makes
    # an exit-0 no-op observable instead of allowing stale valid JSON to become
    # current-run provenance.  An observed file and a failed/ambiguous move are
    # never interchangeable with an absent file: fail before invoking a
    # collector so stale bytes cannot receive current-run provenance.
    quarantine = _quarantine_output(output_path)
    expected_quarantine_status = (
        _QUARANTINE_MOVED if output_observation.exists else _QUARANTINE_ABSENT
    )
    if quarantine.status != expected_quarantine_status:
        if quarantine.path is not None:
            _discard_quarantined_output(quarantine.path)
        final_observation = file_observer(output_path)
        diagnostics = list(resume_diagnostics)
        diagnostics.append(_quarantine_issue(output_path, quarantine, before_collection=True))
        return {
            **_step_summary(raw_step, evidence_root),
            'command_succeeded': False,
            'succeeded': False,
            'skipped': False,
            'reused_verified_output': False,
            'collector_invoked': False,
            'resume_status': 'quarantine_failed',
            'attempts': [],
            'output_exists': final_observation.exists,
            'output_created_or_replaced': False,
            'receipt_path': str(receipt_path),
            'receipt_written': False,
            'validation_passed': False,
            'diagnostics': diagnostics,
            **_observation_metadata(final_observation),
            'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
        }
    quarantined_output = quarantine.path
    discarded_attempt_outputs: list[Path] = []
    attempts = []
    command_succeeded = False
    retry_quarantine_failure: _QuarantineResult | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = _invoke_runner(runner, command, cwd, timeout, env_overrides)
        except subprocess.TimeoutExpired as exc:
            result = CommandResult(
                returncode=-1,
                stdout=_process_output(exc.stdout),
                stderr='command timed out',
            )
        except Exception as exc:
            result = CommandResult(returncode=-1, stderr=f'collector invocation failed: {exc}')
        attempts.append({
            'attempt': attempt,
            'returncode': result.returncode,
            'stdout_tail': redact_process_output(result.stdout, source='stdout'),
            'stderr_tail': redact_process_output(result.stderr, source='stderr'),
        })
        if result.returncode == 0:
            command_succeeded = True
            break
        failed_attempt_output = _quarantine_output(output_path)
        if failed_attempt_output.status == _QUARANTINE_MOVED:
            discarded_attempt_outputs.append(failed_attempt_output.path)
        elif failed_attempt_output.status == _QUARANTINE_FAILED:
            retry_quarantine_failure = failed_attempt_output
            break
        if attempt < max_attempts and retry_backoff_seconds > 0:
            sleeper(retry_backoff_seconds)
    output_observation = file_observer(output_path)
    diagnostics = list(resume_diagnostics)
    validation_passed = False
    receipt_written = False
    if retry_quarantine_failure is not None:
        diagnostics.append(
            _quarantine_issue(
                output_path,
                retry_quarantine_failure,
                before_collection=False,
            )
        )
    elif command_succeeded and not output_observation.exists:
        diagnostics.append(_error(
            'collector_output_not_created' if quarantined_output else 'collector_output_missing',
            str(output_path),
            'collector returned success without creating or replacing the expected output file',
        ))
    elif command_succeeded:
        manifest, output_issues = _load_and_validate_output(
            evidence_key,
            output_path,
            json_loader=json_loader,
            contexts=contexts,
        )
        del manifest
        if output_issues:
            diagnostics.extend(output_issues)
        else:
            validation_passed = True
            try:
                receipt = build_receipt(
                    binding=binding,
                    observation=output_observation,
                    collected_at=receipt_clock() if receipt_clock else None,
                )
                atomic_write_receipt(receipt_path, receipt)
                receipt_written = True
            except Exception as exc:
                diagnostics.append(_error(
                    'receipt_write_failed',
                    str(receipt_path),
                    f'verified output could not be recorded atomically: {exc}',
                ))
    if not receipt_written:
        generated_output = _quarantine_output(output_path)
        if generated_output.status == _QUARANTINE_MOVED:
            discarded_attempt_outputs.append(generated_output.path)
        _restore_quarantined_output(quarantined_output, output_path)
    else:
        _discard_quarantined_output(quarantined_output)
    for discarded in discarded_attempt_outputs:
        _discard_quarantined_output(discarded)
    final_observation = file_observer(output_path)
    return {
        **_step_summary(raw_step, evidence_root),
        'command_succeeded': command_succeeded,
        'succeeded': command_succeeded and validation_passed and receipt_written,
        'skipped': False,
        'reused_verified_output': False,
        'collector_invoked': True,
        'resume_status': 'collected_verified_output' if validation_passed and receipt_written else 'collector_failed',
        'attempts': attempts,
        'output_exists': final_observation.exists,
        'output_created_or_replaced': command_succeeded and output_observation.exists,
        'receipt_path': str(receipt_path),
        'receipt_written': receipt_written,
        'validation_passed': validation_passed,
        'diagnostics': diagnostics,
        **_observation_metadata(final_observation),
        'elapsed_ms': round((time.perf_counter() - started) * 1000, 3),
    }


def _quarantine_output(path: Path) -> _QuarantineResult:
    """Atomically move an output aside with an explicit outcome state."""
    try:
        present = path.is_file()
    except OSError:
        return _QuarantineResult(_QUARANTINE_FAILED)
    if not present:
        return _QuarantineResult(_QUARANTINE_ABSENT)

    temporary_name: str | None = None
    temporary_path: Path | None = None
    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f'.{path.name}.unverified-',
            suffix='.tmp',
            dir=path.parent,
        )
        os.close(fd)
        temporary_path = Path(temporary_name)
        temporary_path.unlink()
        os.replace(path, temporary_path)
        return _QuarantineResult(_QUARANTINE_MOVED, temporary_path)
    except (FileNotFoundError, OSError):
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except (FileNotFoundError, OSError):
                pass
        return _QuarantineResult(_QUARANTINE_FAILED)


def _quarantine_issue(
    output_path: Path,
    result: _QuarantineResult,
    *,
    before_collection: bool,
) -> dict:
    if result.status == _QUARANTINE_FAILED:
        code = 'quarantine_failed'
    else:
        code = 'quarantine_state_mismatch'
    phase = 'before collector invocation' if before_collection else 'before retry'
    return _error(
        code,
        str(output_path),
        f'canonical output quarantine was not proven {phase}',
    )


def _restore_quarantined_output(quarantined: Path | None, destination: Path) -> None:
    if quarantined is None or not quarantined.exists() or destination.exists():
        return
    try:
        os.replace(quarantined, destination)
    except OSError:
        _discard_quarantined_output(quarantined)


def _discard_quarantined_output(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _try_reuse_verified_output(
    raw_step: Mapping,
    *,
    evidence_root: Path,
    output_path: Path,
    receipt_path: Path,
    output_observation: FileObservation,
    binding: ReceiptBinding,
    file_observer: FileObserver,
    json_loader: JsonLoader,
    contexts: Mapping[str, Mapping | None],
) -> dict | None:
    del raw_step, evidence_root, file_observer
    if not output_observation.exists:
        return None
    try:
        receipt = json_loader(receipt_path)
    except Exception:
        return None
    if not isinstance(receipt, Mapping):
        return None
    mismatches = receipt_mismatches(receipt, binding=binding, observation=output_observation)
    if mismatches:
        return None
    _manifest, issues = _load_and_validate_output(
        binding.evidence_key,
        output_path,
        json_loader=json_loader,
        contexts=contexts,
    )
    if issues:
        return None
    return {
        'command_succeeded': False,
        'succeeded': True,
        'skipped': True,
        'skip_reason': 'reused_verified_output',
        'reused_verified_output': True,
        'collector_invoked': False,
        'resume_status': 'reused_verified_output',
        'attempts': [],
        'output_exists': True,
        'receipt_path': str(receipt_path),
        'receipt_written': False,
        'validation_passed': True,
        'diagnostics': [],
        **_observation_metadata(output_observation),
    }


def _reuse_diagnostics(
    *,
    output_path: Path,
    receipt_path: Path,
    output_observation: FileObservation,
    binding: ReceiptBinding,
    json_loader: JsonLoader,
    contexts: Mapping[str, Mapping | None],
) -> list[dict]:
    diagnostics: list[dict] = []
    if not output_observation.exists:
        diagnostics.append(_error(
            'reuse_output_missing',
            str(output_path),
            'verified reuse requires the expected output file',
        ))
        return diagnostics
    try:
        receipt = json_loader(receipt_path)
    except FileNotFoundError as exc:
        diagnostics.append(_error(
            'receipt_missing',
            str(receipt_path),
            f'verified reuse requires a readable receipt: {exc}',
        ))
        return diagnostics
    except Exception as exc:
        diagnostics.append(_error(
            'receipt_corrupt',
            str(receipt_path),
            f'verified reuse receipt could not be decoded: {exc}',
        ))
        return diagnostics
    if not isinstance(receipt, Mapping):
        diagnostics.append(_error(
            'receipt_invalid',
            str(receipt_path),
            'receipt must be a JSON object',
        ))
        return diagnostics
    mismatches = receipt_mismatches(receipt, binding=binding, observation=output_observation)
    diagnostics.extend(dict(item) for item in mismatches)
    _manifest, validation_issues = _load_and_validate_output(
        binding.evidence_key,
        output_path,
        json_loader=json_loader,
        contexts=contexts,
    )
    if validation_issues:
        diagnostics.append(_error(
            'reuse_validator_failed',
            str(output_path),
            'current catalog validator rejected the existing output',
        ))
        diagnostics.extend(validation_issues)
    if not diagnostics:
        diagnostics.append(_error(
            'reuse_verification_failed',
            str(output_path),
            'existing output was not accepted for verified reuse',
        ))
    return diagnostics


def _load_and_validate_output(
    evidence_key: str,
    output_path: Path,
    *,
    json_loader: JsonLoader,
    contexts: Mapping[str, Mapping | None],
) -> tuple[Mapping | None, list[dict]]:
    try:
        manifest = json_loader(output_path)
    except Exception as exc:
        return None, [_error(
            'collector_output_read_error',
            str(output_path),
            f'collector output could not be read as JSON: {exc}',
        )]
    if not isinstance(manifest, Mapping):
        return None, [_error(
            'collector_output_invalid_json',
            str(output_path),
            'collector output must be a JSON object',
        )]
    issues = validate_collector_manifest(
        evidence_key,
        manifest,
        central_db_schema=contexts.get('central_db_schema'),
        extraction_manifest=contexts.get('extraction_manifest'),
    )
    return manifest, [
        issue.to_dict() if hasattr(issue, 'to_dict') else {
            'code': str(getattr(issue, 'code', 'invalid_manifest')),
            'path': str(getattr(issue, 'path', 'manifest')),
            'message': str(getattr(issue, 'message', 'manifest validation failed')),
            'evidence_key': evidence_key,
        }
        for issue in issues
    ]


def _process_output(value) -> str:
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _not_run_step(raw_step: Mapping, evidence_root: Path, *, reason: str) -> dict:
    output_path = evidence_root / str(raw_step['output'])
    return {
        **_step_summary(raw_step, evidence_root),
        'command_succeeded': False,
        'succeeded': False,
        'skipped': True,
        'skip_reason': reason,
        'reused_verified_output': False,
        'collector_invoked': False,
        'resume_status': 'not_run',
        'attempts': [],
        'output_exists': output_path.is_file(),
        'receipt_path': str(receipt_path_for(output_path)),
        'receipt_written': False,
        'validation_passed': False,
        'diagnostics': [],
        **_output_metadata(output_path),
        'elapsed_ms': 0.0,
    }


def _step_summary(step: Mapping, evidence_root: Path) -> dict:
    output = _text(step.get('output'))
    summary = {
        'id': _text(step.get('id')) or _text(step.get('evidence_key')),
        'evidence_key': _text(step.get('evidence_key')),
        'output': output,
        'expected_path': str(evidence_root / output) if output else '',
        'command': redact_command([str(part) for part in (step.get('command') or [])]),
        'timeout_seconds': _float(step.get('timeout_seconds'), 0.0),
        'retries': _int(step.get('retries'), 0),
        'retry_backoff_seconds': _float(step.get('retry_backoff_seconds'), 0.0),
        RESUME_FIELD: bool(step.get(RESUME_FIELD)),
    }
    suggested = step.get('suggested_command')
    if isinstance(suggested, list) and suggested:
        summary['suggested_command'] = redact_command([str(part) for part in suggested])
    env = step.get('env')
    if isinstance(env, Mapping) and env:
        summary['env'] = redact_env(env)
    return summary


def _subprocess_runner(command: list[str], cwd: Path, timeout: float, env_overrides: Mapping[str, str] | None = None) -> CommandResult:
    environment = os.environ.copy()
    environment.update({str(key): str(value) for key, value in (env_overrides or {}).items()})
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env=environment,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(returncode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)


def _invoke_runner(
    runner: CommandRunner,
    command: list[str],
    cwd: Path,
    timeout: float,
    env_overrides: Mapping[str, str],
) -> CommandResult:
    signature = inspect.signature(runner)
    parameters = list(signature.parameters.values())
    accepts_varargs = any(parameter.kind == inspect.Parameter.VAR_POSITIONAL for parameter in parameters)
    positional_capacity = sum(
        1
        for parameter in parameters
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    if accepts_varargs or positional_capacity >= 4:
        return runner(command, cwd, timeout, env_overrides)
    return runner(command, cwd, timeout)


def _output_metadata(path: Path) -> dict:
    if not path.is_file():
        return {
            'output_size_bytes': None,
            'output_sha256': '',
        }
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        'output_size_bytes': path.stat().st_size,
        'output_sha256': digest,
    }


def _write_final_bundle_if_complete(config: Mapping, audit: Mapping, *, write: bool) -> dict:
    output = _text(config.get('bundle_output'))
    if not output:
        return {'configured': False, 'written': False}
    if not write:
        return {
            'configured': True,
            'path': output,
            'written': False,
            'reason': 'plan_mode',
        }
    if not audit.get('complete'):
        return {
            'configured': True,
            'path': output,
            'written': False,
            'reason': 'completion_audit_not_complete',
        }
    bundle = audit.get('bundle')
    if not isinstance(bundle, Mapping):
        return {
            'configured': True,
            'path': output,
            'written': False,
            'reason': 'completion_audit_did_not_return_bundle',
        }
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bundle, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return {
        'configured': True,
        'path': str(output_path),
        'written': True,
    }


def _step_env(step: Mapping) -> dict[str, str]:
    env = step.get('env')
    if not isinstance(env, Mapping):
        return {}
    return {str(key): str(value) for key, value in env.items()}


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _config_sha256(config: Mapping) -> str:
    return safe_workflow_fingerprint(config)


def _step_counts(steps: list[dict]) -> dict:
    return {
        'total': len(steps),
        'succeeded': sum(1 for step in steps if step.get('succeeded') is True),
        'failed': sum(1 for step in steps if step.get('succeeded') is False and step.get('skipped') is not True),
        'skipped': sum(1 for step in steps if step.get('skipped') is True),
        'reused_verified_output': sum(1 for step in steps if step.get('reused_verified_output') is True),
        'collector_invocations': sum(1 for step in steps if step.get('collector_invoked') is True),
        'output_exists': sum(1 for step in steps if step.get('output_exists') is True),
    }


def _observation_metadata(observation: FileObservation) -> dict:
    return {
        'output_size_bytes': observation.size_bytes if observation.exists else None,
        'output_sha256': observation.sha256 or '',
    }


def _render_object(value, *, values: Mapping):
    if isinstance(value, Mapping):
        return {str(key): _render_object(item, values=values) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_object(item, values=values) for item in value]
    if isinstance(value, str):
        return _render_string(value, values=values)
    return value


def _render_string(value: str, *, values: Mapping) -> str:
    rendered = value
    for key, raw in values.items():
        token = f'<{key}>'
        rendered = rendered.replace(token, str(raw))
    return rendered


def _walk_strings(value, *, path: str = ''):
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f'{path}.{key}' if path else str(key)
            yield from _walk_strings(item, path=child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child = f'{path}[{index}]'
            yield from _walk_strings(item, path=child)
    elif isinstance(value, str):
        yield path, value


def _optional_path(value) -> Path | None:
    text = _text(value)
    return Path(text) if text else None


def _context_path_for_audit(
    config: Mapping,
    context_digests: Mapping[str, str],
    context_name: str,
) -> Path | None:
    """Pass only contexts already read and digested by the safe loader.

    ``build_completion_audit`` has its own filesystem reader and historically
    persisted raw read exceptions.  A failed workflow-context read therefore
    must not be handed back to that reader in plan mode; the bounded issue from
    ``_load_validation_contexts`` is the sole summary representation.
    """
    if not context_digests.get(context_name):
        return None
    return _optional_path(config.get(context_name))


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _error(code: str, path: str, message: str) -> dict:
    return {'code': code, 'path': path, 'message': message}


if __name__ == '__main__':
    raise SystemExit(main())
