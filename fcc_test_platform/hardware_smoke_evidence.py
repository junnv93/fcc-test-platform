"""Validate captured Unlicensed headless hardware smoke evidence."""
from __future__ import annotations

from dataclasses import dataclass


__all__ = [
    'HARDWARE_SMOKE_REQUIRED_EVENT_TYPES',
    'HardwareSmokeIssue',
    'hardware_smoke_errors',
    'is_hardware_smoke_valid',
]


HARDWARE_SMOKE_REQUIRED_EVENT_TYPES = {
    'job_execution_started',
    'provider_session_starting',
    'provider_session_completed',
    'job_execution_completed',
}


@dataclass(frozen=True)
class HardwareSmokeIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {
            'code': self.code,
            'path': self.path,
            'message': self.message,
        }


def is_hardware_smoke_valid(evidence: dict) -> bool:
    return not hardware_smoke_errors(evidence)


def hardware_smoke_errors(evidence: dict) -> list[HardwareSmokeIssue]:
    issues: list[HardwareSmokeIssue] = []
    if not isinstance(evidence, dict):
        return [_issue('invalid_evidence', 'evidence', 'evidence must be an object')]
    if evidence.get('schema_version') != 1:
        issues.append(_issue('invalid_schema_version', 'schema_version', 'schema_version must be 1'))

    job = evidence.get('job')
    if not isinstance(job, dict):
        return [_issue('missing_job', 'job', 'job object is required')]
    if job.get('status') != 'completed':
        issues.append(_issue('job_not_completed', 'job.status', f"job.status must be completed, got {job.get('status')!r}"))
    if job.get('session_id') in {None, ''}:
        issues.append(_issue('missing_session_id', 'job.session_id', 'job.session_id is required'))

    events = _list(evidence.get('events'))
    event_types = {str(event.get('event_type') or '') for event in events if isinstance(event, dict)}
    missing_events = sorted(HARDWARE_SMOKE_REQUIRED_EVENT_TYPES - event_types)
    if missing_events:
        issues.append(_issue('missing_event_types', 'events', f"missing required event types: {', '.join(missing_events)}"))

    artifacts = _list(evidence.get('artifacts'))
    if not artifacts:
        issues.append(_issue('missing_artifacts', 'artifacts', 'at least one artifact is required'))
    for index, artifact in enumerate(artifacts):
        path = f'artifacts[{index}]'
        if not isinstance(artifact, dict):
            issues.append(_issue('invalid_artifact', path, 'artifact must be an object'))
            continue
        if not str(artifact.get('artifact_type') or '').strip():
            issues.append(_issue('missing_artifact_type', f'{path}.artifact_type', f'{path}.artifact_type is required'))
        artifact_path = str(artifact.get('relative_path') or artifact.get('path_or_uri') or '').strip()
        if not artifact_path:
            issues.append(_issue('missing_artifact_path', path, f'{path} must include relative_path or path_or_uri'))
        elif _unsafe_relative_path(artifact_path):
            issues.append(_issue('unsafe_artifact_path', path, f'{path} path must be a safe relative path'))

    completed_reports = [
        report for report in _list(evidence.get('report_requests'))
        if isinstance(report, dict) and report.get('status') == 'completed'
    ]
    if not completed_reports:
        issues.append(_issue('missing_completed_report', 'report_requests', 'at least one completed report request is required'))
    for index, report in enumerate(completed_reports):
        generated_paths = _generated_paths(report.get('generated_paths_json'))
        if not generated_paths:
            issues.append(_issue('missing_generated_paths', f'report_requests[{index}].generated_paths_json', f'completed report_requests[{index}] must include generated paths'))
        for generated_path in generated_paths:
            if _unsafe_relative_path(generated_path):
                issues.append(_issue('unsafe_generated_path', f'report_requests[{index}].generated_paths_json', 'generated paths must be safe relative paths'))

    return issues


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _generated_paths(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item or '').strip()]
    import json
    try:
        raw = json.loads(str(value))
    except ValueError:
        return []
    return [str(item) for item in raw if str(item or '').strip()] if isinstance(raw, list) else []


def _unsafe_relative_path(value: str) -> bool:
    text = value.strip().replace('\\', '/')
    if not text:
        return True
    return text.startswith('/') or ':' in text.split('/')[0] or '..' in text.split('/')


def _issue(code: str, path: str, message: str) -> HardwareSmokeIssue:
    return HardwareSmokeIssue(code=code, path=path, message=message)
