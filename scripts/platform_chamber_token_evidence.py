"""Produce chamber token lifecycle evidence (provision / rotate / revoke / binding_check).

Two modes:

* ``--dry-run`` — build a schema-valid, secret-free evidence bundle that records
  the *desired-state* provisioning plan and the binding-check probe contract,
  WITHOUT contacting Keycloak. This is the supported offline evidence when no
  live staging Keycloak is available.
* live (``--live``) — only runs when Keycloak admin env is present
  (``FCC_KEYCLOAK_BASE_URL`` + ``KEYCLOAK_ADMIN`` + ``KEYCLOAK_ADMIN_PASSWORD``).
  It creates/updates one confidential client per chamber via the Keycloak admin
  REST API and records the outcome. It NEVER prints or writes a raw secret.

Vocabulary + validation SSOT: ``application.platform.chamber_token_evidence``.
Client desired-state SSOT: ``application.platform.chamber_token_provisioning``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Callable, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / 'src'
for path in (PROJECT_ROOT, SRC_ROOT):
    path_text = str(path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)

from fcc_test_platform.application.chamber_token_evidence import (  # noqa: E402
    CHAMBER_TOKEN_ACTION_CHOICES,
    REDACTED_SECRET_MARKER,
    chamber_token_evidence_errors,
    resolve_lifecycle_actions,
)
from fcc_test_platform.application.chamber_token_provisioning import (  # noqa: E402
    CHAMBER_PERMISSION,
    chamber_client_id,
    chamber_secret_env_var,
)

# Keycloak admin env anchors (mirror infra/central/central.env naming).
ENV_KEYCLOAK_BASE_URL = 'FCC_KEYCLOAK_BASE_URL'
ENV_KEYCLOAK_ADMIN = 'KEYCLOAK_ADMIN'
ENV_KEYCLOAK_ADMIN_PASSWORD = 'KEYCLOAK_ADMIN_PASSWORD'
DEFAULT_REALM = 'fcc-dev'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Chamber token lifecycle evidence helper.')
    parser.add_argument('--chamber-id', action='append', dest='chamber_ids', default=[],
                        help='Chamber id (repeatable).')
    parser.add_argument('--actor', default='ops:redacted',
                        help='Redacted operator subject performing the lifecycle action.')
    parser.add_argument('--evidence-id', default=None)
    parser.add_argument('--realm', default=os.environ.get('FCC_KEYCLOAK_REALM', DEFAULT_REALM))
    parser.add_argument('--action', action='append', dest='actions', default=None,
                        choices=list(CHAMBER_TOKEN_ACTION_CHOICES),
                        help='Lifecycle action(s) to record (repeatable). '
                             'Default: all (provision + rotate + revoke + binding_check).')
    parser.add_argument('--output', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='Build evidence without contacting Keycloak.')
    mode.add_argument('--live', action='store_true', help='Run lifecycle against Keycloak (requires admin env).')
    parser.add_argument('--require-valid', action='store_true')
    args = parser.parse_args(argv)

    chamber_ids = [c.strip() for c in args.chamber_ids if c and c.strip()]
    if not chamber_ids:
        print(json.dumps({'collected': False, 'error': '--chamber-id is required'},
                         sort_keys=True, indent=2), file=sys.stderr)
        return 2

    if args.live:
        return _run_live(args, chamber_ids)

    manifest = build_dry_run_evidence(
        chamber_ids=chamber_ids,
        actor=args.actor,
        realm=args.realm,
        evidence_id=args.evidence_id,
        actions=args.actions,
    )
    return _write_and_validate(Path(args.output), manifest, require_valid=args.require_valid)


def build_dry_run_evidence(
    *,
    chamber_ids: Sequence[str],
    actor: str,
    realm: str = DEFAULT_REALM,
    evidence_id: str | None = None,
    actions: Sequence[str] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Secret-free desired-state evidence covering the selected lifecycle actions.

    Pure (no IO) so it is fully unit-testable; ``clock`` is injectable for
    determinism. ``actions`` defaults to every lifecycle action (provision /
    rotate / revoke / binding_check).
    """
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    stamp = now.isoformat()
    actor_text = (actor or 'ops:redacted').strip()
    resolved = resolve_lifecycle_actions(actions)
    events: list[dict] = []
    for chamber_id in chamber_ids:
        cid = str(chamber_id).strip()
        client_id = chamber_client_id(cid)
        env_var = chamber_secret_env_var(cid)
        for action in resolved:
            events.append({
                'client_id': client_id,
                'chamber_id': cid,
                'action': action,
                'timestamp': stamp,
                'actor': actor_text,
                'result': 'planned',
                'secret': REDACTED_SECRET_MARKER,
                'secret_env_var': env_var,
                'verification_probe': _probe_for(action, cid),
            })
    return {
        'schema_version': 1,
        'evidence_id': (evidence_id or f'chamber-token-{stamp}').strip(),
        'collected_at': stamp,
        'mode': 'dry-run',
        'keycloak_realm': (realm or DEFAULT_REALM).strip(),
        'provisioning_permission': CHAMBER_PERMISSION,
        'events': events,
    }


def _probe_for(action: str, chamber_id: str) -> dict:
    """Describe how the action's effect is verified (no secret, no token)."""
    other = f'{chamber_id}-other'
    if action == 'binding_check':
        return {
            'endpoint': 'POST /platform/chambers/heartbeat',
            'bound_chamber_id': chamber_id,
            'expect_own_status': 200,
            'expect_other_chamber_id': other,
            'expect_other_status': 403,
            'expect_claimless_status': 200,
            'note': 'claimless token allowed only in backward-compatible mode',
        }
    if action == 'revoke':
        return {
            'endpoint': 'POST /platform/chambers/heartbeat',
            'chamber_id': chamber_id,
            'expect_status_after_revoke': 401,
        }
    return {
        'endpoint': 'POST /platform/chambers/heartbeat',
        'chamber_id': chamber_id,
        'expect_status': 200,
        'permission': CHAMBER_PERMISSION,
    }


def _write_and_validate(output: Path, manifest: dict, *, require_valid: bool) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    issues = [issue.to_dict() for issue in chamber_token_evidence_errors(manifest)]
    print(json.dumps({'collected': True, 'mode': manifest.get('mode'),
                      'valid': not issues, 'issues': issues}, sort_keys=True, indent=2))
    if require_valid and issues:
        return 1
    return 0


def _run_live(args, chamber_ids: list[str]) -> int:
    base_url = os.environ.get(ENV_KEYCLOAK_BASE_URL, '').strip()
    admin = os.environ.get(ENV_KEYCLOAK_ADMIN, '').strip()
    password = os.environ.get(ENV_KEYCLOAK_ADMIN_PASSWORD, '').strip()
    missing = [name for name, value in (
        (ENV_KEYCLOAK_BASE_URL, base_url),
        (ENV_KEYCLOAK_ADMIN, admin),
        (ENV_KEYCLOAK_ADMIN_PASSWORD, password),
    ) if not value]
    if missing:
        print(json.dumps({
            'collected': False,
            'error': f"live mode requires Keycloak admin env: {', '.join(missing)}",
        }, sort_keys=True, indent=2), file=sys.stderr)
        return 2

    # Live lifecycle intentionally lives in a thin infra helper so dry-run
    # (the tested path) stays IO-free. Imported lazily to avoid a urllib import
    # cost on the dry-run path.
    from scripts._keycloak_chamber_admin import run_lifecycle_live  # noqa: E402

    manifest = run_lifecycle_live(
        base_url=base_url, admin=admin, password=password,
        realm=args.realm, chamber_ids=chamber_ids, actor=args.actor,
        actions=args.actions, evidence_id=args.evidence_id,
    )
    return _write_and_validate(Path(args.output), manifest, require_valid=args.require_valid)


if __name__ == '__main__':
    raise SystemExit(main())
