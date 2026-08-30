"""Validate chamber token lifecycle evidence (pure, secret-free).

A chamber token lifecycle evidence bundle records the operator-repeatable
provision / rotate / revoke / binding-check actions for per-chamber Keycloak
machine clients (see ``chamber_token_provisioning`` + ADR-0016). It is the M3
operational counterpart of ``platform_rbac_assignment_evidence`` (RBAC DB
assignments): this one covers the *machine token* lifecycle.

Hard rule: evidence is **secret-free**. Every event's ``secret`` field must be
exactly the redacted marker; a raw-looking secret is a validation error, so a
leaked secret can never masquerade as valid evidence.

This module owns the lifecycle vocabulary SSOT (actions / results / required
fields / redacted marker). The committed JSON Schema artifact
(``docs/platform/chamber_token_lifecycle.schema.json``) is parity-sealed against
these constants by ``tests/test_chamber_token_provisioning.py``.

dependency-free (stdlib only).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


__all__ = [
    'SCHEMA_VERSION',
    'CHAMBER_TOKEN_ACTIONS',
    'ACTION_ALL',
    'CHAMBER_TOKEN_ACTION_CHOICES',
    'CHAMBER_TOKEN_RESULTS',
    'EVIDENCE_MODES',
    'REDACTED_SECRET_MARKER',
    'EVENT_REQUIRED_FIELDS',
    'ChamberTokenEvidenceIssue',
    'is_chamber_token_evidence_valid',
    'chamber_token_evidence_errors',
    'resolve_lifecycle_actions',
]

SCHEMA_VERSION = 1

#: Lifecycle actions a single evidence event can record.
CHAMBER_TOKEN_ACTIONS = ('provision', 'rotate', 'revoke', 'binding_check')

#: CLI meta-token selecting every lifecycle action (not a recordable event
#: action — only an ``--action`` selector). Kept distinct from
#: ``CHAMBER_TOKEN_ACTIONS`` so the schema/event vocabulary never accepts it.
ACTION_ALL = 'all'

#: Valid ``--action`` selector vocabulary (recordable actions + the ``all`` meta).
CHAMBER_TOKEN_ACTION_CHOICES = CHAMBER_TOKEN_ACTIONS + (ACTION_ALL,)

#: Outcome of a recorded action. ``planned`` = dry-run desired-state only.
CHAMBER_TOKEN_RESULTS = ('ok', 'planned', 'failed', 'skipped')

#: How the bundle was produced.
EVIDENCE_MODES = ('dry-run', 'live')

#: The ONLY value an event ``secret`` field may carry — never a raw secret.
REDACTED_SECRET_MARKER = '***REDACTED***'

#: Per-event required fields (the plan's record contract).
EVENT_REQUIRED_FIELDS = (
    'client_id',
    'chamber_id',
    'action',
    'timestamp',
    'actor',
    'result',
    'secret',
    'verification_probe',
)


@dataclass(frozen=True)
class ChamberTokenEvidenceIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {'code': self.code, 'path': self.path, 'message': self.message}


def resolve_lifecycle_actions(raw: Sequence[str] | None) -> tuple[str, ...]:
    """Normalize an ``--action`` selection into canonical recordable actions.

    ``None`` / empty / a selection containing ``all`` → every action in
    ``CHAMBER_TOKEN_ACTIONS`` (canonical order). Otherwise the selected subset,
    deduplicated and re-ordered to the canonical sequence (so evidence ordering
    is deterministic regardless of CLI argument order). Unknown tokens raise.
    """
    if not raw:
        return CHAMBER_TOKEN_ACTIONS
    selected = {str(a).strip() for a in raw if str(a).strip()}
    unknown = selected - set(CHAMBER_TOKEN_ACTION_CHOICES)
    if unknown:
        raise ValueError(
            f"unknown action(s): {', '.join(sorted(unknown))}; "
            f"choose from {', '.join(CHAMBER_TOKEN_ACTION_CHOICES)}")
    if ACTION_ALL in selected or not selected:
        return CHAMBER_TOKEN_ACTIONS
    return tuple(a for a in CHAMBER_TOKEN_ACTIONS if a in selected)


def is_chamber_token_evidence_valid(manifest: Mapping) -> bool:
    return not chamber_token_evidence_errors(manifest)


def chamber_token_evidence_errors(manifest: Mapping) -> list[ChamberTokenEvidenceIssue]:
    issues: list[ChamberTokenEvidenceIssue] = []
    if not isinstance(manifest, Mapping):
        return [_issue('invalid_manifest', '', 'manifest must be an object')]

    if manifest.get('schema_version') != SCHEMA_VERSION:
        issues.append(_issue('invalid_schema_version', 'schema_version',
                             f'schema_version must be {SCHEMA_VERSION}'))
    for key in ('evidence_id', 'collected_at', 'mode', 'keycloak_realm'):
        _require_text(manifest, key, key, issues)
    mode = _text(manifest.get('mode'))
    if mode and mode not in EVIDENCE_MODES:
        issues.append(_issue('invalid_mode', 'mode',
                             f"mode must be one of {', '.join(EVIDENCE_MODES)}"))

    _validate_events(manifest.get('events'), issues)
    return issues


def _validate_events(value, issues: list[ChamberTokenEvidenceIssue]) -> None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        issues.append(_issue('missing_events', 'events',
                             'at least one lifecycle event is required'))
        return
    for index, raw in enumerate(value):
        path = f'events[{index}]'
        event = raw if isinstance(raw, Mapping) else {}
        if not event:
            issues.append(_issue('invalid_event', path, 'event must be an object'))
            continue
        for field in EVENT_REQUIRED_FIELDS:
            if field == 'verification_probe':
                if not isinstance(event.get(field), Mapping):
                    issues.append(_issue('missing_required_field', f'{path}.{field}',
                                         'verification_probe must be an object'))
                continue
            if not _text(event.get(field)):
                issues.append(_issue('missing_required_field', f'{path}.{field}',
                                     f'{field} is required'))
        action = _text(event.get('action'))
        if action and action not in CHAMBER_TOKEN_ACTIONS:
            issues.append(_issue('invalid_action', f'{path}.action',
                                 f"action must be one of {', '.join(CHAMBER_TOKEN_ACTIONS)}"))
        result = _text(event.get('result'))
        if result and result not in CHAMBER_TOKEN_RESULTS:
            issues.append(_issue('invalid_result', f'{path}.result',
                                 f"result must be one of {', '.join(CHAMBER_TOKEN_RESULTS)}"))
        # Secret-free invariant: the only acceptable value is the redacted marker.
        secret = event.get('secret')
        if secret is not None and _text(secret) != REDACTED_SECRET_MARKER:
            issues.append(_issue('raw_secret_marker', f'{path}.secret',
                                 f'secret must be the redacted marker {REDACTED_SECRET_MARKER!r}'))
    return None


def _require_text(mapping: Mapping, key: str, path: str,
                  issues: list[ChamberTokenEvidenceIssue]) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _issue(code: str, path: str, message: str) -> ChamberTokenEvidenceIssue:
    return ChamberTokenEvidenceIssue(code=code, path=path, message=message)
