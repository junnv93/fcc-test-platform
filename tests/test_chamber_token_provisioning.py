"""M3 per-chamber provisioning + token lifecycle evidence (P1).

Seals the provisioning desired-state SSOT, the secret-free evidence vocabulary,
the JSON-Schema ↔ Python parity, and the dry-run CLI builder. Live Keycloak
behavior is out of scope here (no faked IdP success) — only the offline,
deterministic surface is unit-tested.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
for path in (ROOT, SRC):
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)

from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

from fcc_test_platform.application.chamber_token_provisioning import (  # noqa: E402
    AUTHZ_RELEVANT_CLAIMS,
    CHAMBER_CLIENT_ID_PREFIX,
    CHAMBER_ID_CLAIM,
    CHAMBER_PERMISSION,
    build_chamber_client_representation,
    chamber_client_id,
    chamber_secret_env_var,
    representation_authz_permission_tokens,
    representation_permission_tokens,
)
from fcc_test_platform.application.chamber_token_evidence import (  # noqa: E402
    ACTION_ALL,
    CHAMBER_TOKEN_ACTIONS,
    CHAMBER_TOKEN_RESULTS,
    EVENT_REQUIRED_FIELDS,
    EVIDENCE_MODES,
    REDACTED_SECRET_MARKER,
    chamber_token_evidence_errors,
    is_chamber_token_evidence_valid,
    resolve_lifecycle_actions,
)
from fcc_test_contracts.common.oidc_principal_resolver import OidcJwtConfig  # noqa: E402

import scripts.platform_chamber_token_evidence as cli  # noqa: E402
import scripts._keycloak_chamber_admin as kc_admin  # noqa: E402

_FIXED_CLOCK = lambda: __import__('datetime').datetime(  # noqa: E731
    2026, 6, 20, tzinfo=__import__('datetime').timezone.utc)

SCHEMA_PATH = ROOT / 'docs' / 'platform' / 'chamber_token_lifecycle.schema.json'
PROVISIONING_MODULE = resolve_repo_artifact(__file__, 'src/application/platform/chamber_token_provisioning.py')
EVIDENCE_MODULE = resolve_repo_artifact(__file__, 'src/application/platform/chamber_token_evidence.py')


class TestChamberClientRepresentation(unittest.TestCase):
    def test_client_id_uses_prefix(self):
        self.assertEqual(chamber_client_id('staging-a'), f'{CHAMBER_CLIENT_ID_PREFIX}staging-a')
        self.assertTrue(chamber_client_id('staging-a').startswith(CHAMBER_CLIENT_ID_PREFIX))

    def test_empty_chamber_id_rejected(self):
        for fn in (chamber_client_id, chamber_secret_env_var, build_chamber_client_representation):
            with self.assertRaises(ValueError):
                fn('  ')

    def test_secret_env_var_normalized(self):
        self.assertEqual(chamber_secret_env_var('staging-chamber-a'),
                         'FCC_CHAMBER_STAGING_CHAMBER_A_CLIENT_SECRET')

    def test_representation_is_confidential_service_account(self):
        rep = build_chamber_client_representation('staging-a')
        self.assertIs(rep['serviceAccountsEnabled'], True)
        self.assertIs(rep['publicClient'], False)
        self.assertIs(rep['standardFlowEnabled'], False)
        self.assertIs(rep['directAccessGrantsEnabled'], False)

    def test_representation_binds_chamber_id_claim(self):
        rep = build_chamber_client_representation('staging-a')
        claim_values = {
            m['config'].get('claim.value')
            for m in rep['protocolMappers']
            if m['config'].get('claim.name') == CHAMBER_ID_CLAIM
        }
        self.assertEqual(claim_values, {'staging-a'})

    def test_representation_grants_only_platform_chamber(self):
        rep = build_chamber_client_representation('staging-a')
        self.assertEqual(representation_permission_tokens(rep), {CHAMBER_PERMISSION})

    def test_authz_relevant_claims_match_oidc_resolver_contract(self):
        # The "only platform:chamber" guarantee must cover EVERY claim the OIDC
        # resolver derives permissions from — not just ``permissions``. Seal
        # parity with the resolver's default claim names so a resolver change
        # (e.g. a new authZ claim) forces this guard to be revisited.
        config = OidcJwtConfig(issuer='i', audience='a', jwks_uri='j')
        self.assertEqual(
            set(AUTHZ_RELEVANT_CLAIMS),
            {config.permissions_claim, config.scope_claim, config.role_claim},
        )

    def test_representation_emits_no_extra_authz_scope_or_role_tokens(self):
        # Across permissions + scope + roles claims, the ONLY authZ token is
        # platform:chamber — no hardcoded scope/role mapper sneaks in another.
        rep = build_chamber_client_representation('staging-a')
        self.assertEqual(representation_authz_permission_tokens(rep), {CHAMBER_PERMISSION})

    def test_representation_disables_full_scope(self):
        # fullScopeAllowed=false blocks Keycloak's built-in role/scope mappers
        # from injecting realm roles into the token's roles/scope claims (which
        # the resolver would treat as authZ-relevant permissions).
        rep = build_chamber_client_representation('staging-a')
        self.assertIs(rep['fullScopeAllowed'], False)

    def test_no_mapper_targets_scope_or_role_claims(self):
        # Defense in depth: the only hardcoded claim mappers are permissions,
        # chamber_id, and audience — none target scope/roles.
        rep = build_chamber_client_representation('staging-a')
        claim_names = {
            str(m['config'].get('claim.name') or '').strip()
            for m in rep['protocolMappers'] if isinstance(m.get('config'), dict)
        }
        self.assertNotIn('scope', claim_names)
        self.assertNotIn('roles', claim_names)

    def test_representation_secret_is_env_placeholder_not_raw(self):
        rep = build_chamber_client_representation('staging-a')
        self.assertTrue(rep['secret'].startswith('${'))
        self.assertTrue(rep['secret'].endswith('}'))
        # No raw-looking secret anywhere in the serialized representation.
        serialized = json.dumps(rep)
        self.assertNotIn('CLIENT_SECRET=', serialized)
        self.assertIn('FCC_CHAMBER_STAGING_A_CLIENT_SECRET', serialized)


class TestEvidenceValidator(unittest.TestCase):
    def _valid_manifest(self) -> dict:
        return cli.build_dry_run_evidence(
            chamber_ids=['staging-a'], actor='ops:redacted',
            clock=lambda: __import__('datetime').datetime(2026, 6, 20, tzinfo=__import__('datetime').timezone.utc),
        )

    def test_dry_run_builder_is_valid(self):
        manifest = self._valid_manifest()
        self.assertTrue(is_chamber_token_evidence_valid(manifest), chamber_token_evidence_errors(manifest))
        self.assertEqual(manifest['mode'], 'dry-run')

    def test_builder_covers_all_actions_per_chamber(self):
        manifest = cli.build_dry_run_evidence(chamber_ids=['a', 'b'], actor='ops')
        actions_a = {e['action'] for e in manifest['events'] if e['chamber_id'] == 'a'}
        self.assertEqual(actions_a, set(CHAMBER_TOKEN_ACTIONS))
        self.assertEqual(len(manifest['events']), 2 * len(CHAMBER_TOKEN_ACTIONS))

    def test_every_event_secret_is_redacted_marker(self):
        manifest = cli.build_dry_run_evidence(chamber_ids=['a'], actor='ops')
        self.assertTrue(all(e['secret'] == REDACTED_SECRET_MARKER for e in manifest['events']))

    def test_raw_secret_rejected(self):
        manifest = self._valid_manifest()
        manifest['events'][0]['secret'] = 'super-secret-value'
        codes = {i.code for i in chamber_token_evidence_errors(manifest)}
        self.assertIn('raw_secret_marker', codes)

    def test_invalid_action_rejected(self):
        manifest = self._valid_manifest()
        manifest['events'][0]['action'] = 'delete-everything'
        codes = {i.code for i in chamber_token_evidence_errors(manifest)}
        self.assertIn('invalid_action', codes)

    def test_missing_events_rejected(self):
        manifest = self._valid_manifest()
        manifest['events'] = []
        codes = {i.code for i in chamber_token_evidence_errors(manifest)}
        self.assertIn('missing_events', codes)

    def test_binding_check_probe_encodes_403_contract(self):
        manifest = cli.build_dry_run_evidence(chamber_ids=['a'], actor='ops')
        probe = next(e['verification_probe'] for e in manifest['events']
                     if e['action'] == 'binding_check')
        self.assertEqual(probe['expect_own_status'], 200)
        self.assertEqual(probe['expect_other_status'], 403)
        self.assertEqual(probe['expect_claimless_status'], 200)


class TestSchemaParity(unittest.TestCase):
    """The committed JSON Schema vocabulary must equal the Python SSOT."""

    def setUp(self):
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
        self.event = self.schema['properties']['events']['items']

    def test_mode_enum_matches(self):
        self.assertEqual(set(self.schema['properties']['mode']['enum']), set(EVIDENCE_MODES))

    def test_action_enum_matches(self):
        self.assertEqual(set(self.event['properties']['action']['enum']), set(CHAMBER_TOKEN_ACTIONS))

    def test_result_enum_matches(self):
        self.assertEqual(set(self.event['properties']['result']['enum']), set(CHAMBER_TOKEN_RESULTS))

    def test_required_event_fields_match(self):
        self.assertEqual(set(self.event['required']), set(EVENT_REQUIRED_FIELDS))

    def test_secret_const_is_redacted_marker(self):
        self.assertEqual(self.event['properties']['secret']['const'], REDACTED_SECRET_MARKER)


class TestModulePurity(unittest.TestCase):
    """Provisioning + evidence SSOT stay dependency-free (frozen-exe safe)."""

    _FORBIDDEN = {'psycopg', 'psycopg2', 'asyncpg', 'pyvisa', 'openpyxl',
                  'pandas', 'PySide6', 'fastapi', 'sqlalchemy'}

    def _imports(self, path: Path) -> set:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        names: set = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_modules_dependency_free(self):
        for path in (PROVISIONING_MODULE, EVIDENCE_MODULE):
            offenders = {n for n in self._imports(path) if n.split('.')[0] in self._FORBIDDEN}
            self.assertEqual(set(), offenders, f'{path.name}: {offenders}')


class TestCliWritesValidEvidence(unittest.TestCase):
    def test_dry_run_cli_writes_valid_bundle(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'evidence.json'
            rc = cli.main(['--dry-run', '--chamber-id', 'staging-a',
                           '--chamber-id', 'staging-b', '--actor', 'ops:redacted',
                           '--require-valid', '--output', str(out)])
            self.assertEqual(rc, 0)
            manifest = json.loads(out.read_text(encoding='utf-8'))
            self.assertTrue(is_chamber_token_evidence_valid(manifest))
            self.assertNotIn('super-secret', out.read_text(encoding='utf-8'))

    def test_cli_requires_chamber_id(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'e.json'
            self.assertEqual(cli.main(['--dry-run', '--output', str(out)]), 2)


class TestActionSelection(unittest.TestCase):
    def test_default_is_all_actions(self):
        self.assertEqual(resolve_lifecycle_actions(None), CHAMBER_TOKEN_ACTIONS)
        self.assertEqual(resolve_lifecycle_actions([]), CHAMBER_TOKEN_ACTIONS)
        self.assertEqual(resolve_lifecycle_actions([ACTION_ALL]), CHAMBER_TOKEN_ACTIONS)

    def test_subset_is_canonical_ordered(self):
        # CLI order does not matter — evidence ordering is deterministic.
        self.assertEqual(resolve_lifecycle_actions(['revoke', 'provision']),
                         ('provision', 'revoke'))

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError):
            resolve_lifecycle_actions(['provision', 'nuke'])

    def test_dry_run_respects_action_subset(self):
        manifest = cli.build_dry_run_evidence(
            chamber_ids=['a'], actor='ops', actions=['rotate'], clock=_FIXED_CLOCK)
        self.assertEqual({e['action'] for e in manifest['events']}, {'rotate'})

    def test_dry_run_default_covers_all_actions(self):
        manifest = cli.build_dry_run_evidence(
            chamber_ids=['a'], actor='ops', clock=_FIXED_CLOCK)
        self.assertEqual({e['action'] for e in manifest['events']},
                         set(CHAMBER_TOKEN_ACTIONS))


class _FakeKeycloakAdminClient:
    """Records REST calls without any HTTP — substitutes KeycloakAdminClient."""

    def __init__(self, *, existing=None, fail_on=None):
        # existing: set of client_ids that already exist (→ update / rotate / revoke)
        self._existing = set(existing or [])
        # fail_on: set of method names that should raise (failure recording)
        self._fail_on = set(fail_on or [])
        self.calls: list[tuple] = []

    def _maybe_fail(self, name):
        if name in self._fail_on:
            raise RuntimeError(f'boom:{name}')

    def find_client_uuid(self, client_id):
        self.calls.append(('find', client_id))
        self._maybe_fail('find')
        return f'uuid-{client_id}' if client_id in self._existing else None

    def create_client(self, representation):
        self.calls.append(('create', representation['clientId']))
        self._maybe_fail('create')

    def update_client(self, uuid, representation):
        self.calls.append(('update', uuid, representation['clientId']))
        self._maybe_fail('update')

    def rotate_secret(self, uuid):
        self.calls.append(('rotate', uuid))
        self._maybe_fail('rotate')
        # A real Keycloak returns the new secret here — the helper must discard it.
        return {'type': 'secret', 'value': 'RAW-SECRET-MUST-NOT-LEAK'}

    def disable_client(self, uuid):
        self.calls.append(('disable', uuid))
        self._maybe_fail('disable')


class TestLiveLifecycle(unittest.TestCase):
    def _build(self, *, existing=None, fail_on=None, actions=None, chamber_ids=('staging-a',)):
        client = _FakeKeycloakAdminClient(existing=existing, fail_on=fail_on)
        manifest = kc_admin.build_live_evidence(
            client=client, realm='fcc-dev', chamber_ids=list(chamber_ids),
            actor='ops:redacted', actions=actions, clock=_FIXED_CLOCK,
        )
        return client, manifest

    def _event(self, manifest, action, chamber_id='staging-a'):
        return next(e for e in manifest['events']
                    if e['action'] == action and e['chamber_id'] == chamber_id)

    def test_live_provision_creates_new_client(self):
        client, manifest = self._build(actions=['provision'])
        self.assertIn(('create', 'fcc-chamber-staging-a'), client.calls)
        self.assertEqual(self._event(manifest, 'provision')['result'], 'ok')

    def test_live_provision_updates_existing_client(self):
        client, manifest = self._build(existing={'fcc-chamber-staging-a'}, actions=['provision'])
        self.assertTrue(any(c[0] == 'update' for c in client.calls))
        self.assertFalse(any(c[0] == 'create' for c in client.calls))
        self.assertEqual(self._event(manifest, 'provision')['result'], 'ok')

    def test_live_rotate_calls_secret_endpoint(self):
        client, manifest = self._build(existing={'fcc-chamber-staging-a'}, actions=['rotate'])
        self.assertIn(('rotate', 'uuid-fcc-chamber-staging-a'), client.calls)
        self.assertEqual(self._event(manifest, 'rotate')['result'], 'ok')

    def test_live_rotate_missing_client_is_failed_not_ok(self):
        client, manifest = self._build(existing=set(), actions=['rotate'])
        # No client → cannot rotate → honest failure, never silent ok.
        self.assertFalse(any(c[0] == 'rotate' for c in client.calls))
        self.assertEqual(self._event(manifest, 'rotate')['result'], 'failed')

    def test_live_revoke_disables_client(self):
        client, manifest = self._build(existing={'fcc-chamber-staging-a'}, actions=['revoke'])
        self.assertIn(('disable', 'uuid-fcc-chamber-staging-a'), client.calls)
        self.assertEqual(self._event(manifest, 'revoke')['result'], 'ok')

    def test_live_revoke_missing_client_is_failed(self):
        _client, manifest = self._build(existing=set(), actions=['revoke'])
        self.assertEqual(self._event(manifest, 'revoke')['result'], 'failed')

    def test_live_rest_error_recorded_as_failed(self):
        _client, manifest = self._build(fail_on={'create'}, actions=['provision'])
        event = self._event(manifest, 'provision')
        self.assertEqual(event['result'], 'failed')
        self.assertIn('admin REST error', event['verification_probe']['note'])

    def test_live_binding_check_is_planned_not_fabricated(self):
        _client, manifest = self._build(actions=['binding_check'])
        event = self._event(manifest, 'binding_check')
        self.assertEqual(event['result'], 'planned')
        self.assertEqual(event['verification_probe']['expect_other_status'], 403)

    def test_live_secret_never_leaks_even_on_rotate(self):
        _client, manifest = self._build(existing={'fcc-chamber-staging-a'}, actions=None)
        serialized = json.dumps(manifest)
        self.assertNotIn('RAW-SECRET-MUST-NOT-LEAK', serialized)
        self.assertTrue(all(e['secret'] == REDACTED_SECRET_MARKER for e in manifest['events']))

    def test_live_all_action_evidence_covers_every_action_per_chamber(self):
        _client, manifest = self._build(
            existing={'fcc-chamber-staging-a', 'fcc-chamber-staging-b'},
            actions=None, chamber_ids=('staging-a', 'staging-b'),
        )
        for chamber_id in ('staging-a', 'staging-b'):
            actions = {e['action'] for e in manifest['events'] if e['chamber_id'] == chamber_id}
            self.assertEqual(actions, set(CHAMBER_TOKEN_ACTIONS), chamber_id)
        self.assertTrue(is_chamber_token_evidence_valid(manifest),
                        chamber_token_evidence_errors(manifest))
        self.assertEqual(manifest['mode'], 'live')


if __name__ == '__main__':
    unittest.main()
