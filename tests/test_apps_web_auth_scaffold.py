"""Apps/web auth scaffold invariant — Sprint S2 OIDC PKCE.

The backend Python pytest mirror seals the frontend OIDC PKCE module shape
so a refactor that breaks the SSOT chain (e.g., introduces a client_secret
literal, switches token storage to localStorage, hardcodes an
authorization_endpoint, demotes the 5 failure UX enum) fails CI from the
Python side as well as the JS side.

Cross-checks aligned with backend SSOT:

  * ``application/common/auth_config.py::HttpAuthConfig`` defaults for the
    three claim names (``permissions`` / ``scope`` / ``roles``) MUST match
    the frontend ``CLAIM_PERMISSIONS`` / ``CLAIM_SCOPE`` / ``CLAIM_ROLES``
    constants — drift means the JWT claims the backend reads will not be
    surfaced to the operator UI.
  * Public-client policy — neither ``runtime.ts`` nor any ``auth/`` module
    may carry a ``client_secret`` literal (PKCE only — public client).
  * Storage policy — ``apps/web/src/auth/**`` may not touch ``localStorage``
    or ``document.cookie``; ``sessionStorage`` is the single source.
  * 5 failure UX — the discriminated union must enumerate exactly the
    contract-mandated 5 kinds; adding a 6th requires a contract amendment.
  * Discovery is dynamic — ``authorization_endpoint`` / ``token_endpoint``
    may not appear as string literals in ``apps/web/src/auth/*.ts``; they
    must be read from the OIDC discovery document at runtime.
  * Legacy library ban — ``oidc-client-ts`` / ``@auth0/auth0-spa-js`` /
    ``@azure/msal-browser`` are not allowed in ``apps/web/package.json``
    dependencies (hand-roll posture per ADR-0002 stack revisit).
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

from support.parity import strip_ts_comments as _strip_ts_comments



# ⚠️ 2026-08-31 에 이 모듈들은 이사했다. 경로를 적으면 레포마다 다른 문자열이
# 필요하지만 임포트 이름은 양쪽에서 같다 — 모듈에게 자기 위치를 묻는다.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _moved_module_source import moved_module_source  # noqa: E402
project_root = Path(__file__).parent.parent
WEB_ROOT = project_root / "apps" / "web"
SRC_AUTH = WEB_ROOT / "src" / "auth"
TESTS_AUTH = WEB_ROOT / "tests" / "auth"
INFRA_ROOT = project_root / "infra"

# Backend SSOT for cross-check.
sys.path.insert(0, str(project_root / "src"))


REQUIRED_AUTH_FILES = (
    SRC_AUTH / "oidc-pkce.ts",
    SRC_AUTH / "session.ts",
    SRC_AUTH / "route-guard.tsx",
    SRC_AUTH / "failure-ui.tsx",
    SRC_AUTH / "storage-keys.ts",
    TESTS_AUTH / "oidc-pkce.test.ts",
    TESTS_AUTH / "session.test.ts",
    TESTS_AUTH / "route-guard.test.tsx",
    TESTS_AUTH / "failure-ui.test.tsx",
    WEB_ROOT / "tests" / "e2e" / "auth-flow.spec.ts",
    INFRA_ROOT / "docker-compose.idp.yml",
    INFRA_ROOT / "keycloak" / "fcc-dev-realm.json",
    INFRA_ROOT / "idp-policy.json",
    INFRA_ROOT / "README.md",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _iter_auth_sources():
    for path in SRC_AUTH.rglob("*.ts"):
        yield path
    for path in SRC_AUTH.rglob("*.tsx"):
        yield path


class TestAuthRequiredFilesExist(unittest.TestCase):
    """M1 — 8 contract deliverables exist."""

    def test_all_files_present(self):
        for path in REQUIRED_AUTH_FILES:
            self.assertTrue(path.is_file(), f"required auth scaffold file missing: {path.relative_to(project_root)}")


class TestSsotChainBackendFrontend(unittest.TestCase):
    """M2 — backend HttpAuthConfig claim defaults mirror to frontend constants."""

    def setUp(self):
        from fcc_test_contracts.common.auth_config import HttpAuthConfig

        self.auth = HttpAuthConfig()
        self.session_ts = _read(SRC_AUTH / "session.ts")

    def test_permissions_claim_default_matches(self):
        self.assertEqual(self.auth.oidc_permissions_claim, "permissions")
        self.assertIn("CLAIM_PERMISSIONS = 'permissions'", self.session_ts)

    def test_scope_claim_default_matches(self):
        self.assertEqual(self.auth.oidc_scope_claim, "scope")
        self.assertIn("CLAIM_SCOPE = 'scope'", self.session_ts)

    def test_role_claim_default_matches(self):
        self.assertEqual(self.auth.oidc_role_claim, "roles")
        self.assertIn("CLAIM_ROLES = 'roles'", self.session_ts)


class TestDiscoveryDynamic(unittest.TestCase):
    """M2.2 — authorization_endpoint / token_endpoint are NOT hardcoded in
    auth/*.ts. They MUST be read from the discovery document at runtime."""

    def test_no_hardcoded_idp_endpoint_url_in_auth_modules(self):
        # Stronger AST-free check: a hardcoded IdP auth/token endpoint URL
        # must not appear in any auth source. The discovery field NAMES
        # (`authorization_endpoint`, `token_endpoint`) are legitimately used
        # because they are JSON keys returned by the IdP response.
        forbidden_url_re = re.compile(r'["\']https?://[^"\']*/(auth|token)["\']')
        for path in _iter_auth_sources():
            stripped = _strip_ts_comments(_read(path))
            for match in forbidden_url_re.findall(stripped):
                self.fail(
                    f"{path.relative_to(WEB_ROOT)} hardcodes an IdP endpoint URL "
                    f"({match!r}). Discovery must be dynamic.",
                )

    def test_discovery_path_constant_present(self):
        text = _read(SRC_AUTH / "oidc-pkce.ts")
        self.assertIn(
            "OIDC_DISCOVERY_PATH = '/.well-known/openid-configuration'",
            text,
            "OIDC_DISCOVERY_PATH SSOT constant missing (RFC 8414 / OIDC Discovery 1.0 § 4)",
        )

    def test_legacy_oidc_config_path_purged(self):
        for path in _iter_auth_sources():
            stripped = _strip_ts_comments(_read(path))
            self.assertNotIn(
                "./oidc-config.json",
                stripped,
                f"{path.relative_to(WEB_ROOT)} retains the legacy oidc-config.json prototype pattern",
            )


class TestPublicClientPolicy(unittest.TestCase):
    """M2.3 — public PKCE client only. client_secret literal forbidden."""

    BANNED_LITERAL_RE = re.compile(r"client[_-]?secret", re.IGNORECASE)

    def test_no_client_secret_in_auth_modules(self):
        for path in _iter_auth_sources():
            stripped = _strip_ts_comments(_read(path))
            self.assertIsNone(
                self.BANNED_LITERAL_RE.search(stripped),
                f"{path.relative_to(WEB_ROOT)} mentions client_secret — public PKCE client only",
            )

    def test_no_client_secret_in_runtime_config(self):
        text = _read(WEB_ROOT / "src" / "config" / "runtime.ts")
        # Comments mentioning the ban-rule are fine; declarations / accesses
        # are not. Use the same property regex the scaffold invariant uses.
        property_decl = re.compile(r"oidcClientSecret\s*[:=]")
        property_access = re.compile(r"\.oidcClientSecret\b")
        self.assertIsNone(property_decl.search(text))
        self.assertIsNone(property_access.search(text))

    def test_keycloak_realm_client_is_public(self):
        payload = json.loads(_read(INFRA_ROOT / "keycloak" / "fcc-dev-realm.json"))
        clients = payload.get("clients", [])
        match = next((c for c in clients if c.get("clientId") == "fcc-platform-frontend"), None)
        self.assertIsNotNone(match, "fcc-platform-frontend client missing from Keycloak realm import")
        assert match is not None  # type narrowing for mypy
        self.assertIs(match.get("publicClient"), True, "client must be public (PKCE only)")
        # `secret` field MUST NOT appear, even with an empty value.
        self.assertNotIn("secret", match, "public PKCE client must not carry a `secret` field")


class TestSessionStorageOnly(unittest.TestCase):
    """M4 — sessionStorage SSOT. localStorage / document.cookie forbidden."""

    def test_no_localstorage_in_auth_modules(self):
        for path in _iter_auth_sources():
            stripped = _strip_ts_comments(_read(path))
            self.assertNotIn(
                "localStorage",
                stripped,
                f"{path.relative_to(WEB_ROOT)} touches localStorage — sessionStorage is the SSOT",
            )

    def test_no_document_cookie_in_auth_modules(self):
        for path in _iter_auth_sources():
            stripped = _strip_ts_comments(_read(path))
            self.assertNotIn(
                "document.cookie",
                stripped,
                f"{path.relative_to(WEB_ROOT)} touches document.cookie — sessionStorage is the SSOT",
            )


class TestFailureKindUnion(unittest.TestCase):
    """M5 — 5 failure kinds (and exactly 5) are enumerated."""

    EXPECTED = frozenset({
        "idp_config_missing",
        "token_expired",
        "idp_unreachable",
        "permission_denied",
        "backend_403",
    })

    def test_failure_ui_exports_all_kinds(self):
        text = _read(SRC_AUTH / "failure-ui.tsx")
        for kind in self.EXPECTED:
            self.assertIn(f"'{kind}'", text, f"failure-ui.tsx is missing kind {kind!r}")

    def test_failure_kinds_frozen_list_matches_union(self):
        text = _read(SRC_AUTH / "failure-ui.tsx")
        # The FAILURE_KINDS array literal must contain exactly the 5 strings.
        match = re.search(
            r"FAILURE_KINDS:\s*readonly\s+OidcFailureKind\[\]\s*=\s*Object\.freeze\(\[([^\]]+)\]\)",
            text,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "FAILURE_KINDS frozen array missing or malformed")
        assert match is not None
        kinds_in_array = set(re.findall(r"'([a-z_0-9]+)'", match.group(1)))
        self.assertEqual(
            kinds_in_array,
            self.EXPECTED,
            "FAILURE_KINDS array drift vs OidcFailureKind union",
        )


class TestRouteGuardSurface(unittest.TestCase):
    """M6 — RequireAuth + RequirePermission + AuthCallbackRoute + useAuthSession exported."""

    def setUp(self):
        self.text = _read(SRC_AUTH / "route-guard.tsx")

    def test_require_auth_exported(self):
        self.assertRegex(self.text, r"export\s+function\s+RequireAuth\b")

    def test_require_permission_exported(self):
        self.assertRegex(self.text, r"export\s+function\s+RequirePermission\b")

    def test_auth_callback_route_exported(self):
        self.assertRegex(self.text, r"export\s+function\s+AuthCallbackRoute\b")

    def test_use_auth_session_hook_exported(self):
        self.assertRegex(self.text, r"export\s+function\s+useAuthSession\b")

    def test_authorization_header_helper_exported(self):
        # Used by src/api/session-client.ts middleware — keeps the
        # client_secret-free SSOT.
        self.assertRegex(self.text, r"export\s+function\s+authorizationHeader\b")


class TestNoLegacyAuthLib(unittest.TestCase):
    """M7 — hand-roll posture. No OIDC convenience library may be added."""

    BANNED = frozenset({
        "oidc-client",
        "oidc-client-ts",
        "@auth0/auth0-spa-js",
        "@azure/msal-browser",
        "openid-client",
    })

    def test_package_json_has_no_legacy_oidc_lib(self):
        payload = json.loads(_read(WEB_ROOT / "package.json"))
        deps = {**payload.get("dependencies", {}), **payload.get("devDependencies", {})}
        for banned in self.BANNED:
            self.assertNotIn(
                banned,
                deps,
                f"banned auth library present: {banned} — Sprint S2 ADR mandates hand-roll",
            )


class TestNoHardcodedMagicNumbers(unittest.TestCase):
    """M3 — refresh margin is the only SSOT numeric in the auth modules.

    All other numeric literals must be either part of a named SSOT constant
    or accompanied by an inline comment explaining the domain meaning.
    """

    def test_refresh_margin_ssot_present_exactly_once(self):
        session_text = _read(SRC_AUTH / "session.ts")
        stripped = _strip_ts_comments(session_text)
        # Only one declaration must exist in src/ (in code, not in docstring).
        decls = re.findall(r"MIN_REFRESH_MARGIN_SECONDS\s*=\s*30", stripped)
        self.assertEqual(len(decls), 1, "MIN_REFRESH_MARGIN_SECONDS = 30 must be defined exactly once")
        # RFC 6749 citation must still appear in the *raw* file (comment is fine).
        self.assertIn(
            "RFC 6749",
            session_text,
            "RFC 6749 citation missing — industry standard provenance required",
        )

    def test_no_inline_token_ttl_literal(self):
        # No literal `expires_in: 3600` / `expiresIn: 600` etc. in src/auth/.
        # Tests are allowed to construct token sets with literals.
        pat = re.compile(r"(expires_in|expiresIn)\s*[:=]\s*\d+")
        for path in _iter_auth_sources():
            stripped = _strip_ts_comments(_read(path))
            for m in pat.finditer(stripped):
                self.fail(
                    f"{path.relative_to(WEB_ROOT)} hardcodes a token TTL literal "
                    f"({m.group()!r}). TTL comes from the IdP `expires_in` response."
                )


class TestRuntimeConfigOidcSurface(unittest.TestCase):
    """Runtime config must expose oidcScopes + oidcAudience + oidcPostLogoutRedirectUri
    so the PKCE flow gets its inputs from the same Zod-validated SSOT."""

    def setUp(self):
        self.text = _read(WEB_ROOT / "src" / "config" / "runtime.ts")

    def test_scopes_field_present(self):
        self.assertIn("oidcScopes:", self.text)
        self.assertIn("openid", self.text)
        # OIDC Core 1.0 § 5.4 mandates the 'openid' scope.
        self.assertRegex(self.text, r"OIDC Core 1\.0 § 5\.4")

    def test_audience_field_present_optional(self):
        self.assertIn("oidcAudience:", self.text)

    def test_post_logout_redirect_field_present_nullable(self):
        self.assertRegex(
            self.text,
            r"oidcPostLogoutRedirectUri:\s*absoluteUrl\.nullable\(\)",
        )


class TestKeycloakComposeShape(unittest.TestCase):
    """M10 — docker-compose + realm import are loadable + carry the contract-mandated shape."""

    def test_compose_yaml_references_keycloak(self):
        text = _read(INFRA_ROOT / "docker-compose.idp.yml")
        self.assertIn("quay.io/keycloak/keycloak:", text, "Keycloak image not pinned")
        # Realm import volume mount.
        self.assertIn("/opt/keycloak/data/import", text, "realm import volume missing")
        # Start-dev with --import-realm flag.
        self.assertIn("--import-realm", text, "Keycloak must boot with --import-realm")
        self.assertIn(
            "127.0.0.1/9000",
            text,
            "Keycloak 25 /health/ready must use the management port 9000",
        )
        self.assertNotIn(
            "127.0.0.1/8080",
            text,
            "Keycloak 25 /health/ready must not use application port 8080",
        )

    def test_realm_json_carries_three_roles_and_three_users(self):
        payload = json.loads(_read(INFRA_ROOT / "keycloak" / "fcc-dev-realm.json"))
        self.assertEqual(payload.get("realm"), "fcc-dev")
        realm_roles = {r["name"] for r in payload.get("roles", {}).get("realm", [])}
        self.assertEqual(realm_roles, {"viewer", "operator", "admin"})
        usernames = {u["username"] for u in payload.get("users", [])}
        self.assertEqual(usernames, {"viewer", "operator", "admin"})

    def test_realm_json_emits_permissions_claim(self):
        payload = json.loads(_read(INFRA_ROOT / "keycloak" / "fcc-dev-realm.json"))
        client = next(
            (c for c in payload.get("clients", []) if c.get("clientId") == "fcc-platform-frontend"),
            None,
        )
        self.assertIsNotNone(client)
        assert client is not None
        mappers = client.get("protocolMappers", [])
        permissions_mapper = next(
            (m for m in mappers if m.get("config", {}).get("claim.name") == "permissions"),
            None,
        )
        self.assertIsNotNone(
            permissions_mapper,
            "Keycloak client must emit a 'permissions' claim (matches backend default)",
        )

    def test_infra_readme_documents_dev_usage(self):
        text = _read(INFRA_ROOT / "README.md")
        self.assertIn("docker compose -f infra/docker-compose.idp.yml up", text)
        self.assertIn("/.well-known/openid-configuration", text)


class TestStorageKeysSsot(unittest.TestCase):
    """Sprint S2-α #6 — storage-keys.ts is the single source for OIDC
    sessionStorage keys; oidc-pkce.ts and session.ts must import (not
    re-declare) the constants."""

    def setUp(self):
        self.storage_keys = _read(SRC_AUTH / "storage-keys.ts")
        self.oidc = _strip_ts_comments(_read(SRC_AUTH / "oidc-pkce.ts"))
        self.session = _strip_ts_comments(_read(SRC_AUTH / "session.ts"))

    def test_storage_keys_module_defines_all_six_keys(self):
        for name in (
            "OIDC_STORAGE_PREFIX",
            "STORAGE_KEY_STATE",
            "STORAGE_KEY_VERIFIER",
            "STORAGE_KEY_NONCE",
            "STORAGE_KEY_RETURN_TO",
            "STORAGE_KEY_TOKENS",
            "STORAGE_KEY_FORCE_REAUTH",
        ):
            self.assertRegex(
                self.storage_keys,
                rf"export\s+const\s+{re.escape(name)}\b",
                f"storage-keys.ts must export {name}",
            )

    def test_storage_keys_module_freezes_all_keys_list(self):
        # `ALL_STORAGE_KEYS` is the SSOT enumeration for the cross-check.
        self.assertRegex(
            self.storage_keys,
            r"export\s+const\s+ALL_STORAGE_KEYS\s*=\s*Object\.freeze",
            "storage-keys.ts must export a frozen ALL_STORAGE_KEYS list",
        )

    def test_oidc_pkce_does_not_redeclare_storage_keys(self):
        # `STORAGE_KEY_*` may only appear as an IMPORT, not as a declaration.
        decl_re = re.compile(r"(const|let|var)\s+STORAGE_KEY_[A-Z_]+\s*[:=]")
        for m in decl_re.finditer(self.oidc):
            self.fail(
                f"oidc-pkce.ts redeclares a storage key ({m.group()!r}) — "
                f"must import from storage-keys.ts (S2-α #6 SSOT)",
            )

    def test_session_does_not_redeclare_storage_keys(self):
        decl_re = re.compile(r"(const|let|var)\s+STORAGE_KEY_[A-Z_]+\s*[:=]")
        for m in decl_re.finditer(self.session):
            self.fail(
                f"session.ts redeclares a storage key ({m.group()!r}) — "
                f"must import from storage-keys.ts (S2-α #6 SSOT)",
            )

    def test_oidc_pkce_imports_storage_keys(self):
        # Either a named import OR the storage-keys.ts module path appears.
        self.assertRegex(
            self.oidc,
            r"from\s+['\"]\./storage-keys['\"]",
            "oidc-pkce.ts must import from ./storage-keys",
        )

    def test_session_imports_storage_keys(self):
        self.assertRegex(
            self.session,
            r"from\s+['\"]\./storage-keys['\"]",
            "session.ts must import from ./storage-keys",
        )


class TestIdpPolicySsotCrossCheck(unittest.TestCase):
    """Sprint S2-α #9 — infra/idp-policy.json is the SSOT for IdP token
    lifetimes; infra/keycloak/fcc-dev-realm.json fields must match
    byte-identity. A future prod realm (infra/keycloak/fcc-prod-realm.json)
    pairs with idp-policy.production.json — never edit either file in
    isolation."""

    def setUp(self):
        self.policy = json.loads(_read(INFRA_ROOT / "idp-policy.json"))
        self.realm = json.loads(_read(INFRA_ROOT / "keycloak" / "fcc-dev-realm.json"))

    def test_policy_has_three_lifetime_fields(self):
        for field in ("accessTokenLifespanSeconds", "ssoSessionIdleSeconds", "ssoSessionMaxSeconds"):
            self.assertIn(field, self.policy, f"idp-policy.json missing {field}")
            self.assertIsInstance(self.policy[field], int)
            self.assertGreater(self.policy[field], 0)

    def test_realm_access_token_lifespan_matches_policy(self):
        self.assertEqual(
            self.realm["accessTokenLifespan"],
            self.policy["accessTokenLifespanSeconds"],
            "Keycloak realm accessTokenLifespan must equal idp-policy.json SSOT",
        )

    def test_realm_sso_idle_matches_policy(self):
        self.assertEqual(
            self.realm["ssoSessionIdleTimeout"],
            self.policy["ssoSessionIdleSeconds"],
            "Keycloak realm ssoSessionIdleTimeout must equal idp-policy.json SSOT",
        )

    def test_realm_sso_max_matches_policy(self):
        self.assertEqual(
            self.realm["ssoSessionMaxLifespan"],
            self.policy["ssoSessionMaxSeconds"],
            "Keycloak realm ssoSessionMaxLifespan must equal idp-policy.json SSOT",
        )


class TestTsCommentStripperStateMachine(unittest.TestCase):
    """Sprint S2-α #1 — replaces the regex `_strip_ts_comments` with a
    proper TS lexer. These cases break the old regex but must pass on
    the state machine."""

    def test_double_slash_inside_string_literal_is_preserved(self):
        src = 'const url = "https://example.com"; // comment'
        out = _strip_ts_comments(src)
        self.assertIn('"https://example.com"', out)
        # The trailing comment is gone.
        self.assertNotIn("// comment", out)

    def test_block_comment_inside_template_literal_is_preserved(self):
        src = "const t = `a /* not a comment */ b`;"
        out = _strip_ts_comments(src)
        self.assertIn("/* not a comment */", out)

    def test_quote_inside_string_with_escape_does_not_terminate(self):
        src = "const s = \"a \\\" b\"; // c"
        out = _strip_ts_comments(src)
        self.assertIn('"a \\" b"', out)
        self.assertNotIn("// c", out)

    def test_block_then_line_comment_in_sequence(self):
        src = "/* block */ const x = 1; // line\nconst y = 2;"
        out = _strip_ts_comments(src)
        self.assertNotIn("/* block */", out)
        self.assertNotIn("// line", out)
        self.assertIn("const x = 1;", out)
        self.assertIn("const y = 2;", out)

    def test_url_in_single_quoted_string_preserved(self):
        # The old regex used negative-lookbehind `(?<!:)//` to avoid URL
        # `://` — the lexer doesn't need that trick because it knows we're
        # inside a string literal.
        src = "const x = 'http://example.com/path'; // strip me"
        out = _strip_ts_comments(src)
        self.assertIn("'http://example.com/path'", out)
        self.assertNotIn("// strip", out)


class TestBackendFrontendAudienceSsot(unittest.TestCase):
    """Sprint S2-α #7 — backend HttpAuthConfig.oidc_audience default and
    frontend runtime.ts oidcAudience default must agree on the same
    'empty by default' semantics (so the Keycloak default deployment
    works without explicit audience config on either side)."""

    def test_backend_oidc_audience_default_is_empty(self):
        from fcc_test_contracts.common.auth_config import HttpAuthConfig

        self.assertEqual(HttpAuthConfig().oidc_audience, "")

    def test_frontend_oidc_audience_default_is_empty(self):
        # Frontend Zod schema: `oidcAudience: z.string().default('')`.
        # We assert the schema source carries that default literal so a
        # later refactor that bumps the default fails this cross-check.
        text = _read(WEB_ROOT / "src" / "config" / "runtime.ts")
        self.assertRegex(
            text,
            r"oidcAudience:\s*z\.string\(\)\.default\(['\"]\s*['\"]\)",
            "frontend runtime.ts oidcAudience must default to empty string "
            "(matches backend HttpAuthConfig.oidc_audience='' default)",
        )


class TestCrossTechTokenPolicyDocs(unittest.TestCase):
    """Sprint S2-α #8 — cross-tech docs explicitly tie frontend
    MIN_REFRESH_MARGIN_SECONDS to RFC 6749 § 5.1 and describe the
    Keycloak interaction matrix."""

    def setUp(self):
        self.doc = project_root / "docs" / "architecture" / "frontend" / "cross-tech-token-policy.md"

    def test_doc_exists(self):
        self.assertTrue(self.doc.is_file())

    def test_doc_cites_rfc_6749(self):
        text = _read(self.doc)
        self.assertIn("RFC 6749", text)
        self.assertIn("§ 5.1", text)

    def test_doc_describes_keycloak_interaction(self):
        text = _read(self.doc)
        for token in ("accessTokenLifespan", "MIN_REFRESH_MARGIN_SECONDS", "PyJWT", "leeway"):
            self.assertIn(token, text, f"cross-tech doc missing {token}")


class TestSecurityHardening(unittest.TestCase):
    """Sprint S2-α #10 #11 #12 #13 — the security primitives must be
    structurally present in the source. AST-level checks (not just
    runtime) so a regression in a code reorganisation fires here."""

    def setUp(self):
        self.oidc = _strip_ts_comments(_read(SRC_AUTH / "oidc-pkce.ts"))
        self.session = _strip_ts_comments(_read(SRC_AUTH / "session.ts"))
        self.route_guard = _strip_ts_comments(_read(SRC_AUTH / "route-guard.tsx"))

    def test_jose_lib_is_imported(self):
        # S2-α #11 — id_token verification via jose (no hand-rolled crypto).
        self.assertRegex(
            self.oidc,
            r"from\s+['\"]jose['\"]",
            "oidc-pkce.ts must import from 'jose' (industry-standard JWT lib)",
        )
        for symbol in ("createRemoteJWKSet", "jwtVerify"):
            self.assertIn(
                symbol,
                self.oidc,
                f"oidc-pkce.ts must use jose `{symbol}` for id_token verification",
            )

    def test_nonce_generator_and_storage(self):
        # S2-α #10 — nonce is generated and persisted.
        self.assertRegex(self.oidc, r"export\s+function\s+generateNonce\b")
        self.assertIn("STORAGE_KEY_NONCE", self.oidc)
        self.assertIn("nonce", self.oidc)

    def test_id_token_claims_surface_in_complete_login_result(self):
        # S2-α #11 — verified claims propagate to the caller.
        self.assertRegex(
            self.oidc,
            r"idTokenClaims:\s*JWTPayload\s*\|\s*null",
            "completeLogin result must expose verified idTokenClaims",
        )
        self.assertIn(
            "idTokenClaims",
            self.session,
            "session.ts applyTokenSet must accept verified idTokenClaims",
        )

    def test_complete_login_clears_transaction_state_before_network(self):
        # S2-α #12 — race fix. Locate `clearTransactionState()` and verify
        # it appears BEFORE any `await … exchangeCode` or
        # `await … discoverOidcConfig` line in completeLogin's body.
        completion = re.search(
            r"export\s+async\s+function\s+completeLogin\s*\([\s\S]*?\n\}\n",
            self.oidc,
        )
        self.assertIsNotNone(completion, "completeLogin function body not found")
        assert completion is not None
        body = completion.group(0)
        clear_pos = body.find("clearTransactionState()")
        exchange_pos = body.find("exchangeCode")
        self.assertGreater(clear_pos, 0, "completeLogin must call clearTransactionState()")
        self.assertGreater(exchange_pos, 0, "completeLogin must call exchangeCode")
        self.assertLess(
            clear_pos,
            exchange_pos,
            "S2-α #12 race fix — clearTransactionState() must run BEFORE exchangeCode",
        )

    def test_force_reauth_flag_consumed_in_start_login(self):
        # S2-α #13 — startLogin reads + clears the force-reauth flag and
        # adds `prompt=login`.
        self.assertIn("STORAGE_KEY_FORCE_REAUTH", self.oidc)
        self.assertRegex(
            self.oidc,
            r"prompt['\"]?\s*,\s*['\"]login['\"]",
            "startLogin must add `prompt=login` when force-reauth flag is set",
        )

    def test_sign_out_button_exposes_force_reauth_opt(self):
        self.assertRegex(
            self.route_guard,
            r"forceReauth\??:\s*boolean",
            "SignOutButton must expose forceReauth opt",
        )


class TestTokenGrantClassifierCoversEverySentGrant(unittest.TestCase):
    """OIDC 증거 판정기는 SPA 가 **실제로 보내는** grant 를 전부 알아야 한다.

    `family: 'token'` 은 **엔드포인트**이지 grant 가 아니다 — 한 경로로 서로 다른
    grant 가 온다. 판정기가 그중 하나만 알면, 나머지는 규격을 지켰는데도
    `unexpectedRequests` 로 떨어진다.

    ⚠️ 실측 2026-09-05: 정확히 그 일이 있었다. 판정기가 그 family 의 **모든** POST 에
    `authorization_code` + `code_verifier` 를 요구했고, 그래서 `refreshTokens()` 의
    갱신 교환(RFC 6749 § 6 — `code_verifier` 가 **없는 것이 규격**)이
    *"missing authorization-code PKCE body"* 로 잡혔다. 진단명이 읽는 사람을
    **인증 구현 쪽으로** 잘못 보냈고, oidc-conformance 잡이 그 상태로 빨갰다.

    그래서 목록을 손으로 유지하지 않는다 — `oidc-pkce.ts` 가 보내는 `grant_type`
    리터럴을 **파생**해서 판정기가 그것을 이름으로 다루는지 본다. 다음에 grant 가
    하나 더 늘면 판정기가 조용히 그것을 결함으로 세는 대신 이 검사가 멈춘다.
    """

    _OIDC = SRC_AUTH / "oidc-pkce.ts"
    _FIXTURE = WEB_ROOT / "tests" / "e2e" / "helpers" / "real-auth-fixture.ts"

    def _sent_grants(self) -> set[str]:
        """SPA 가 토큰 엔드포인트로 보내는 grant_type 리터럴."""
        body = _strip_ts_comments(_read(self._OIDC))
        return set(re.findall(r"grant_type:\s*'([^']+)'", body))

    def _classified_grants(self) -> set[str]:
        """판정기가 이름으로 다루는 grant_type."""
        body = _strip_ts_comments(_read(self._FIXTURE))
        return set(re.findall(r"case\s*'([^']+)':", body))

    def test_every_sent_grant_is_classified(self) -> None:
        sent = self._sent_grants()
        # 「일했다는 증거」 — 정규식이 아무것도 못 잡고 「차집합 없음」으로 초록이
        # 되는 자리를 막는다. 이 SPA 는 최소 둘을 보낸다(교환 + 갱신).
        self.assertGreaterEqual(
            len(sent), 2, f"grant_type literals not found in {self._OIDC.name}"
        )
        self.assertEqual(
            sorted(sent - self._classified_grants()),
            [],
            "the OIDC evidence classifier does not name grant(s) the SPA actually sends; "
            "they will be counted as unexpected requests",
        )


class TestSentryIntegrationImport(unittest.TestCase):
    """Sprint S2-α #3 — proper Integration type import."""

    def test_sentry_integration_imported_from_types(self):
        # 웨이브 fe-w4-bundle-observability-cost (2026-07-31) — Sentry 초기화(그리고
        # `Integration[]` 을 쓰는 유일한 코드)가 on-demand 청크 `sentry-runtime.ts`
        # 로 이동했다. `sentry.ts` 는 SDK 무게가 0인 capture 파사드로 남는다.
        text = _read(WEB_ROOT / "src" / "observability" / "sentry-runtime.ts")
        self.assertRegex(
            text,
            r"import\s+type\s+\{[^}]*\bIntegration\b[^}]*\}\s+from\s+['\"]@sentry/types['\"]",
            "sentry-runtime.ts must import Integration from @sentry/types (the canonical source)",
        )
        # Inferred-type workaround from S2 must NOT linger.
        self.assertNotIn(
            "ReturnType<typeof Sentry.browserTracingIntegration>",
            text,
            "S2 inferred-type workaround must be removed",
        )


class TestEslintResolverInstalled(unittest.TestCase):
    """Sprint S2-α #2 — eslint-import-resolver-typescript is a proper devDep."""

    def setUp(self):
        self.pkg = json.loads(_read(WEB_ROOT / "package.json"))
        self.config = _read(WEB_ROOT / "eslint.config.js")

    def test_resolver_devdep_present(self):
        dev = self.pkg.get("devDependencies", {})
        self.assertIn("eslint-import-resolver-typescript", dev)

    def test_eslint_config_uses_typescript_resolver(self):
        self.assertRegex(
            self.config,
            r"import/resolver['\"]?\s*:\s*\{\s*typescript",
            "eslint.config.js must use the typescript resolver (S2 node-only workaround removed)",
        )


class TestCodegenCrossPlatformSpawn(unittest.TestCase):
    """Sprint S2-α #5 — codegen.mjs invokes the CLI via process.execPath,
    not via the platform-dependent `.bin/openapi-typescript` shim."""

    def setUp(self):
        raw = _read(project_root / "apps" / "web" / "scripts" / "codegen.mjs")
        self.text_raw = raw
        # Comments may legitimately mention the forbidden path while
        # explaining why we don't spawn it. Strip them via the same TS
        # lexer the auth invariants use.
        self.text_code = _strip_ts_comments(raw)

    def test_uses_process_exec_path(self):
        self.assertIn(
            "process.execPath",
            self.text_code,
            "codegen.mjs must spawn via process.execPath for cross-platform safety",
        )

    def test_does_not_spawn_dot_bin_shim(self):
        # `.bin/openapi-typescript` is the Unix shell wrapper / Windows .cmd
        # shim that Sprint S2 used and broke on Windows. Must not appear in
        # CODE (comments are allowed to mention it for documentation).
        self.assertNotIn(
            "node_modules/.bin/openapi-typescript",
            self.text_code,
            "codegen.mjs must not spawn the .bin shim directly (S2 Windows defect)",
        )


class TestLockfilePresent(unittest.TestCase):
    """Sprint S2-α #4 — package-lock.json is tracked so `npm ci` is deterministic."""

    def test_lockfile_exists_and_lists_package(self):
        lockfile = WEB_ROOT / "package-lock.json"
        self.assertTrue(lockfile.is_file(), "apps/web/package-lock.json must be committed")
        data = json.loads(_read(lockfile))
        self.assertEqual(data.get("name"), "@fcc/web")
        self.assertIn("packages", data)


class TestReactConcurrentSafeHook(unittest.TestCase):
    """Sprint S2-β α-2 — useAuthSession uses React 18's useSyncExternalStore
    (concurrent-safe) instead of useState + manual subscribe."""

    def setUp(self):
        self.route_guard = _strip_ts_comments(_read(SRC_AUTH / "route-guard.tsx"))

    def test_use_sync_external_store_imported(self):
        self.assertRegex(
            self.route_guard,
            r"import\s+\{[^}]*\buseSyncExternalStore\b[^}]*\}\s+from\s+['\"]react['\"]",
            "route-guard.tsx must import useSyncExternalStore from React",
        )

    def test_use_auth_session_calls_use_sync_external_store(self):
        # Locate the useAuthSession function body.
        m = re.search(
            r"export\s+function\s+useAuthSession\s*\([^)]*\)[^{]*\{([^}]*?)\}",
            self.route_guard,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "useAuthSession function body not found")
        assert m is not None
        body = m.group(1)
        self.assertIn(
            "useSyncExternalStore",
            body,
            "useAuthSession must use useSyncExternalStore (S2-β α-2 concurrent-safe)",
        )
        # Old useState + useEffect pattern must NOT be inside useAuthSession.
        self.assertNotIn(
            "useState",
            body,
            "useAuthSession must not use useState (concurrent rendering tearing risk)",
        )
        self.assertNotIn(
            "useEffect",
            body,
            "useAuthSession must not use useEffect for subscription (use useSyncExternalStore)",
        )


class TestOidcResilience(unittest.TestCase):
    """Sprint S2-β α-3 α-4 α-6 α-7 — OIDC subsystem hardening."""

    def setUp(self):
        self.oidc = _strip_ts_comments(_read(SRC_AUTH / "oidc-pkce.ts"))
        self.session = _strip_ts_comments(_read(SRC_AUTH / "session.ts"))
        self.storage_keys = _read(SRC_AUTH / "storage-keys.ts")
        self.main_tsx = _strip_ts_comments(_read(WEB_ROOT / "src" / "main.tsx"))

    def test_discovery_in_flight_clears_on_reject(self):
        # α-3 — promise rejection path must clear discoveryInFlight.
        self.assertRegex(
            self.oidc,
            r"discoveryInFlight\s*=\s*null",
            "discoverOidcConfig must clear discoveryInFlight (reject path)",
        )
        # Two assignments: one for fulfillment, one for rejection.
        clears = re.findall(r"discoveryInFlight\s*=\s*null", self.oidc)
        self.assertGreaterEqual(
            len(clears), 2,
            "discoverOidcConfig should clear discoveryInFlight on both fulfillment "
            "AND rejection (α-3 — without the reject branch, a transient IdP "
            "outage permanently poisons the cache)",
        )

    def test_verify_id_token_exported_with_skip_nonce_option(self):
        # α-4 — verifyIdToken is exported + accepts skipNonce option.
        self.assertRegex(
            self.oidc,
            r"export\s+async\s+function\s+verifyIdToken\b",
            "verifyIdToken must be exported so session.ts silent-refresh can re-verify",
        )
        self.assertRegex(
            self.oidc,
            r"skipNonce\??:\s*boolean",
            "VerifyIdTokenOptions must include skipNonce for silent-refresh path",
        )

    def test_silent_refresh_calls_verify_id_token(self):
        # α-4 — performSilentRefresh re-verifies the new id_token.
        self.assertIn(
            "verifyIdToken",
            self.session,
            "session.ts must call verifyIdToken on silent refresh (α-4)",
        )
        self.assertIn(
            "skipNonce: true",
            self.session,
            "silent refresh must pass skipNonce: true (no fresh authorization round-trip)",
        )

    def test_jwks_cooldown_and_timeout_configured(self):
        # α-6 — createRemoteJWKSet has cooldownDuration + timeoutDuration.
        self.assertRegex(
            self.oidc,
            r"cooldownDuration:\s*JWKS_COOLDOWN_MS",
            "createRemoteJWKSet must pass cooldownDuration (JWKS rotation defence)",
        )
        self.assertRegex(
            self.oidc,
            r"timeoutDuration:\s*JWKS_TIMEOUT_MS",
            "createRemoteJWKSet must pass timeoutDuration (production budget)",
        )

    def test_purge_legacy_storage_helper_exists_and_main_calls_it(self):
        # α-7 — legacy prefix migration helper + boot-time invocation.
        self.assertRegex(
            self.storage_keys,
            r"export\s+function\s+purgeLegacyStorage\b",
            "storage-keys.ts must export purgeLegacyStorage()",
        )
        self.assertIn(
            "LEGACY_OIDC_STORAGE_PREFIX",
            self.storage_keys,
        )
        self.assertIn(
            "purgeLegacyStorage",
            self.main_tsx,
            "main.tsx must call purgeLegacyStorage on boot before restoreSession",
        )
        # Ordering: purgeLegacyStorage must run BEFORE restoreSession.
        purge_pos = self.main_tsx.find("purgeLegacyStorage()")
        restore_pos = self.main_tsx.find("restoreSession()")
        self.assertGreater(purge_pos, 0)
        self.assertGreater(restore_pos, 0)
        self.assertLess(
            purge_pos,
            restore_pos,
            "purgeLegacyStorage() must precede restoreSession() in main.tsx boot order",
        )

    def test_force_reauth_semantic_constant_used(self):
        # α-12 — OIDC_FORCE_REAUTH_FLAG_VALUE replaces literal '1'.
        # S2-γ β-P2-5 — value declaration may carry a Literal Union type
        # annotation (`: OidcForceReauthFlag`) between identifier and `=`.
        self.assertRegex(
            self.storage_keys,
            r"OIDC_FORCE_REAUTH_FLAG_VALUE(?::\s*\w+)?\s*=\s*['\"]pending['\"]",
            "OIDC_FORCE_REAUTH_FLAG_VALUE must be the 'pending' SSOT constant",
        )
        # signOutAtIdp + startLogin both reference the constant, not '1'.
        self.assertIn("OIDC_FORCE_REAUTH_FLAG_VALUE", self.oidc)
        # Literal `'1'` write must NOT appear next to STORAGE_KEY_FORCE_REAUTH.
        self.assertNotRegex(
            self.oidc,
            r"setItem\(\s*STORAGE_KEY_FORCE_REAUTH\s*,\s*['\"]1['\"]\s*\)",
            "literal '1' magic value forbidden — must use OIDC_FORCE_REAUTH_FLAG_VALUE",
        )


class TestCallbackPathCrossCheck(unittest.TestCase):
    """Sprint S2-β α-8 — the React Router /auth/callback path in app.tsx
    must match the pathname of `oidcRedirectUri` in the runtime config
    template, otherwise the IdP's redirect lands on a 404 route."""

    def test_app_tsx_callback_path_matches_runtime_redirect_uri(self):
        from urllib.parse import urlparse

        app_tsx = _read(WEB_ROOT / "src" / "app.tsx")
        m = re.search(r"path:\s*['\"]([^'\"]*?/auth/callback)['\"]", app_tsx)
        self.assertIsNotNone(m, "app.tsx must declare an /auth/callback route")
        assert m is not None
        app_callback_path = m.group(1)

        template = json.loads(_read(WEB_ROOT / "public" / "runtime-config.template.json"))
        redirect_uri = template.get("oidcRedirectUri", "")
        self.assertTrue(redirect_uri, "runtime-config.template.json oidcRedirectUri missing")
        template_path = urlparse(redirect_uri).path
        self.assertEqual(
            app_callback_path,
            template_path,
            f"app.tsx /auth/callback path ({app_callback_path!r}) must match runtime "
            f"config oidcRedirectUri pathname ({template_path!r}) — otherwise the IdP "
            f"redirect lands on a 404 route",
        )

    def test_test_setup_redirect_uri_pathname_matches(self):
        # Test setup also carries an oidcRedirectUri that the unit tests
        # exercise — keep it aligned with app.tsx + the template.
        from urllib.parse import urlparse

        setup_ts = _read(WEB_ROOT / "tests" / "setup.ts")
        m = re.search(r"oidcRedirectUri:\s*['\"]([^'\"]+)['\"]", setup_ts)
        self.assertIsNotNone(m, "tests/setup.ts oidcRedirectUri missing")
        assert m is not None
        path = urlparse(m.group(1)).path
        self.assertEqual(path, "/auth/callback")


class TestIdpPolicySchemaValid(unittest.TestCase):
    """Sprint S2-β α-9 — infra/idp-policy.json validates against its own
    JSON Schema, locking type/range/required."""

    def test_schema_exists(self):
        schema_path = INFRA_ROOT / "idp-policy.schema.json"
        self.assertTrue(schema_path.is_file(), "infra/idp-policy.schema.json missing")

    def test_idp_policy_validates_against_schema(self):
        # Sprint S2-γ β-P1-1 — `jsonschema` is now a hard requirement
        # (added to requirements.txt). Graceful-skip policy retired:
        # silently skipping an SSOT invariant is a sliding standard.
        import jsonschema  # type: ignore
        schema = json.loads(_read(INFRA_ROOT / "idp-policy.schema.json"))
        instance = json.loads(_read(INFRA_ROOT / "idp-policy.json"))
        # Raises jsonschema.ValidationError on any drift.
        jsonschema.validate(instance=instance, schema=schema)

    def test_idp_policy_references_its_schema(self):
        policy = json.loads(_read(INFRA_ROOT / "idp-policy.json"))
        self.assertIn("$schema", policy, "idp-policy.json must reference its schema")
        # Path-based $schema (relative) is allowed; just check non-empty.
        self.assertTrue(policy["$schema"])


class TestBackendLeewayCrossCheck(unittest.TestCase):
    """Sprint S2-β α-10 — backend `jwt.decode(leeway=N)` MUST satisfy
    N ≤ MIN_REFRESH_MARGIN_SECONDS (30 s, frontend SSOT). Higher backend
    leeway than frontend margin would erode the short-token guarantee
    by letting expired tokens through.

    Sprint S2-γ β-P0-3 — kept as a SECONDARY grep guard. The PRIMARY
    cross-check is now ``TestBackendFrontendRefreshMarginSsot`` below,
    which imports the actual constant value.
    """

    MIN_REFRESH_MARGIN_SECONDS = 30

    def test_backend_oidc_resolver_leeway_within_frontend_margin(self):
        # ⚠️ 이 모듈은 패키지로 이사했다(2026-08-31). 경로를 적어 두면 「사라졌다」와
        # 「옮겨졌다」가 둘 다 skip 이 되어, 검사가 조용히 초록으로 죽는다.
        from _moved_module_source import ModuleSourceUnavailable, moved_module_source
        try:
            path = moved_module_source('fcc_test_contracts.common.oidc_principal_resolver')
        except (ImportError, ModuleSourceUnavailable) as exc:
            self.fail(f'backend OIDC resolver 를 찾을 수 없다: {exc}')
        text = path.read_text(encoding="utf-8")
        # Find every jwt.decode(...) call and inspect leeway arg.
        for m in re.finditer(r"jwt\.decode\s*\(([^)]*)\)", text, re.DOTALL):
            args = m.group(1)
            leeway_int = re.search(r"leeway\s*=\s*(\d+)", args)
            if leeway_int is not None:
                value = int(leeway_int.group(1))
                self.assertLessEqual(
                    value,
                    self.MIN_REFRESH_MARGIN_SECONDS,
                    f"backend jwt.decode leeway={value} exceeds frontend "
                    f"MIN_REFRESH_MARGIN_SECONDS={self.MIN_REFRESH_MARGIN_SECONDS} "
                    "— would let stale tokens through after silent refresh deadline",
                )
                continue
            # leeway named constant — must be exactly OIDC_REFRESH_MARGIN_SECONDS
            # so the strong cross-check below catches drift.
            leeway_ident = re.search(r"leeway\s*=\s*([A-Z_][A-Z0-9_]*)", args)
            if leeway_ident is not None:
                self.assertEqual(
                    leeway_ident.group(1),
                    "OIDC_REFRESH_MARGIN_SECONDS",
                    "backend jwt.decode leeway MUST reference OIDC_REFRESH_MARGIN_SECONDS SSOT, "
                    f"got {leeway_ident.group(1)!r}",
                )


class TestBackendFrontendRefreshMarginSsot(unittest.TestCase):
    """Sprint S2-γ β-P0-3 — strong cross-tech SSOT: the backend module
    constant and the frontend literal MUST be the same integer.

    This replaces the regex-only grep guard with an actual value import
    on the backend side, eliminating the false-sense-of-security of a
    grep that misses indirection (``leeway=ALLOWED_LEEWAY`` where
    ``ALLOWED_LEEWAY`` is defined elsewhere)."""

    def test_backend_constant_defined(self):
        from fcc_test_contracts.common.oidc_principal_resolver import (
            OIDC_REFRESH_MARGIN_SECONDS,
        )
        self.assertIsInstance(OIDC_REFRESH_MARGIN_SECONDS, int)
        self.assertGreater(OIDC_REFRESH_MARGIN_SECONDS, 0)

    def test_backend_constant_equals_frontend_literal(self):
        from fcc_test_contracts.common.oidc_principal_resolver import (
            OIDC_REFRESH_MARGIN_SECONDS,
        )
        session_ts = _read(SRC_AUTH / "session.ts")
        m = re.search(
            r"export\s+const\s+MIN_REFRESH_MARGIN_SECONDS\s*=\s*(\d+)",
            session_ts,
        )
        self.assertIsNotNone(m, "frontend MIN_REFRESH_MARGIN_SECONDS literal not found")
        assert m is not None
        frontend_value = int(m.group(1))
        self.assertEqual(
            frontend_value,
            OIDC_REFRESH_MARGIN_SECONDS,
            f"frontend MIN_REFRESH_MARGIN_SECONDS ({frontend_value}) MUST equal "
            f"backend OIDC_REFRESH_MARGIN_SECONDS ({OIDC_REFRESH_MARGIN_SECONDS}). "
            "When tuning, change both atomically (see cross-tech-token-policy.md runbook).",
        )

    def test_backend_jwt_decode_uses_named_constant(self):
        text = _read(
            moved_module_source('fcc_test_contracts.common.oidc_principal_resolver')
        )
        # S2-δ γ-P0-1 — backend jwt.decode passes leeway as the
        # CLOCK_TOLERANCE constant (split from the prior REFRESH_MARGIN
        # mis-alias). Either named constant satisfies the contract
        # because both are 30s and the strong import-based parity check
        # above ensures the integer match.
        self.assertRegex(
            text,
            r"leeway\s*=\s*(OIDC_CLOCK_TOLERANCE_SECONDS|OIDC_REFRESH_MARGIN_SECONDS)",
            "backend jwt.decode MUST pass leeway as a named SSOT constant",
        )


class TestSecurityConstantCitations(unittest.TestCase):
    """Sprint S2-β α-13 — every random-byte SSOT constant in oidc-pkce.ts
    cites its RFC source so a future tuner cannot lower entropy below
    spec without seeing the rationale."""

    def setUp(self):
        self.text = _read(SRC_AUTH / "oidc-pkce.ts")

    def test_verifier_byte_count_cites_rfc_7636(self):
        # Citation must appear in a comment immediately preceding the
        # VERIFIER_RANDOM_BYTES declaration.
        m = re.search(
            r"(/\*\*?.*?\*/)\s*const\s+VERIFIER_RANDOM_BYTES",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "VERIFIER_RANDOM_BYTES must have a doc comment")
        assert m is not None
        comment = m.group(1)
        self.assertIn("RFC 7636", comment)

    def test_state_byte_count_cites_rfc_6749_or_6819(self):
        # Sprint S2-γ β-P2-4 — STATE_RANDOM_BYTES is now an alias of
        # OIDC_ANTI_REPLAY_TOKEN_BYTES; the citation lives on the source
        # SSOT constant's doc comment OR on the alias's adjacent comment.
        m = re.search(
            r"(/\*\*?.*?\*/)\s*const\s+OIDC_ANTI_REPLAY_TOKEN_BYTES",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "OIDC_ANTI_REPLAY_TOKEN_BYTES SSOT must exist (β-P2-4)")
        assert m is not None
        comment = m.group(1)
        self.assertTrue(
            "RFC 6749" in comment or "RFC 6819" in comment,
            "OIDC_ANTI_REPLAY_TOKEN_BYTES SSOT must cite RFC 6749 § 10.12 and/or RFC 6819 § 5.3.5 (state half)",
        )

    def test_nonce_byte_count_cites_oidc_core(self):
        # β-P2-4 — same SSOT, OIDC Core citation lives in the same doc
        # block alongside the RFC 6749/6819 (state) citation.
        m = re.search(
            r"(/\*\*?.*?\*/)\s*const\s+OIDC_ANTI_REPLAY_TOKEN_BYTES",
            self.text,
            re.DOTALL,
        )
        self.assertIsNotNone(m)
        assert m is not None
        comment = m.group(1)
        self.assertIn("OIDC Core", comment)

    def test_anti_replay_token_ssot_unifies_state_and_nonce(self):
        # β-P2-4 — STATE_RANDOM_BYTES + NONCE_RANDOM_BYTES MUST derive
        # from the shared SSOT, not duplicate the magic.
        stripped = _strip_ts_comments(self.text)
        self.assertRegex(
            stripped,
            r"STATE_RANDOM_BYTES\s*=\s*OIDC_ANTI_REPLAY_TOKEN_BYTES",
            "STATE_RANDOM_BYTES must alias OIDC_ANTI_REPLAY_TOKEN_BYTES",
        )
        self.assertRegex(
            stripped,
            r"NONCE_RANDOM_BYTES\s*=\s*OIDC_ANTI_REPLAY_TOKEN_BYTES",
            "NONCE_RANDOM_BYTES must alias OIDC_ANTI_REPLAY_TOKEN_BYTES",
        )


class TestIdpPolicyBasisOfValues(unittest.TestCase):
    """Sprint S2-β α-14 — _basisForValues must be a structured map
    citing NIST SP 800-63B + OAuth 2.0 BCP for each lifetime value."""

    def setUp(self):
        self.policy = json.loads(_read(INFRA_ROOT / "idp-policy.json"))

    def test_basis_is_structured_map(self):
        basis = self.policy.get("_basisForValues")
        self.assertIsInstance(basis, dict, "_basisForValues must be a structured map (per-key citation)")

    def test_each_lifetime_has_a_citation_entry(self):
        basis = self.policy.get("_basisForValues", {})
        for field in ("accessTokenLifespanSeconds", "ssoSessionIdleSeconds", "ssoSessionMaxSeconds"):
            self.assertIn(field, basis, f"_basisForValues missing entry for {field}")
            self.assertTrue(basis[field], f"_basisForValues[{field}] must be non-empty")

    def test_basis_cites_industry_standards(self):
        basis_text = json.dumps(self.policy.get("_basisForValues", {}))
        # At least one of these standards must appear per citation.
        for standard in ("NIST SP 800-63B", "RFC 6749", "OAuth"):
            self.assertIn(
                standard,
                basis_text,
                f"_basisForValues must cite {standard}",
            )


class TestCodegenMaxBufferSsot(unittest.TestCase):
    """Sprint S2-β α-16 — execFile maxBuffer is named via SSOT constant,
    not an inline magic number."""

    def test_codegen_uses_named_constant(self):
        text = _strip_ts_comments(
            _read(project_root / "apps" / "web" / "scripts" / "codegen.mjs"),
        )
        self.assertIn("OPENAPI_TS_OUTPUT_BUFFER_BYTES", text)
        # Inline magic literal `16 * 1024 * 1024` must NOT appear inside
        # the execFile options object.
        self.assertNotRegex(
            text,
            r"maxBuffer:\s*\d+\s*\*\s*\d+\s*\*\s*\d+",
            "codegen.mjs maxBuffer must reference OPENAPI_TS_OUTPUT_BUFFER_BYTES, not an inline magic literal",
        )


class TestMeasureBundleScriptPresent(unittest.TestCase):
    """Sprint S2-β α-15 — bundle size measurement script + npm script."""

    def test_script_file_exists(self):
        path = WEB_ROOT / "scripts" / "measure-bundle.mjs"
        self.assertTrue(path.is_file(), "apps/web/scripts/measure-bundle.mjs missing")

    def test_npm_script_registered(self):
        pkg = json.loads(_read(WEB_ROOT / "package.json"))
        self.assertIn("measure:bundle", pkg.get("scripts", {}))

    def test_script_emits_machine_readable_json(self):
        # Grep for the structured output keys so a future "let's switch to
        # plain text" refactor fails the contract.
        text = _read(WEB_ROOT / "scripts" / "measure-bundle.mjs")
        for key in ("totalGzipBytes", "totalRawBytes", "chunks", "buildId"):
            self.assertIn(key, text, f"measure-bundle.mjs must emit {key!r}")


class TestS2GammaSecurityHardening(unittest.TestCase):
    """Sprint S2-γ β-P0-1 β-P0-2 β-P2-5 — clock skew tolerance + max_age
    enforcement + force-reauth Literal Union."""

    def setUp(self):
        self.oidc = _strip_ts_comments(_read(SRC_AUTH / "oidc-pkce.ts"))
        self.storage_keys = _strip_ts_comments(_read(SRC_AUTH / "storage-keys.ts"))

    def test_clock_tolerance_constant_defined(self):
        # S2-δ γ-P0-1 + γ-P2-12 — `OIDC_CLOCK_TOLERANCE_SECONDS` (number,
        # seconds) replaces the prior string `OIDC_CLOCK_TOLERANCE = '30s'`.
        # Source-of-truth lives in session.ts so backend cross-check can
        # grep one authoritative literal.
        # S2-ε δ-P1-1 — value tuned to 60 (Auth0 default) so this is
        # genuinely distinct from MIN_REFRESH_MARGIN_SECONDS (30).
        session_ts = _read(SRC_AUTH / "session.ts")
        self.assertRegex(
            session_ts,
            r"OIDC_CLOCK_TOLERANCE_SECONDS\s*=\s*\d+",
            "OIDC_CLOCK_TOLERANCE_SECONDS (number) SSOT must exist in session.ts",
        )
        # Citation must appear in the raw text (jose docs or NIST). Look
        # in both session.ts (where the SSOT lives) and oidc-pkce.ts
        # (where it's consumed — original citation comments may live
        # there too).
        oidc_raw = _read(SRC_AUTH / "oidc-pkce.ts")
        combined = session_ts + oidc_raw
        self.assertTrue(
            "NIST SP 800-63B" in combined or "panva/jose" in combined,
            "OIDC_CLOCK_TOLERANCE_SECONDS doc comment must cite jose docs or NIST",
        )

    def test_clock_tolerance_passed_to_jwt_verify(self):
        # S2-δ γ-P0-1 — jose receives the number SSOT, not a string.
        self.assertIn("clockTolerance: OIDC_CLOCK_TOLERANCE_SECONDS", self.oidc)
        # String form purged.
        self.assertNotIn("clockTolerance: '30s'", self.oidc)
        self.assertNotIn('clockTolerance: "30s"', self.oidc)

    def test_max_age_storage_key_defined(self):
        # β-P0-2
        self.assertRegex(
            self.storage_keys,
            r"STORAGE_KEY_MAX_AGE\s*=",
        )

    def test_max_age_param_threads_through_start_and_complete(self):
        self.assertRegex(self.oidc, r"maxAge\??:\s*number")
        self.assertIn("max_age", self.oidc, "startLogin must emit max_age URL param")
        self.assertRegex(
            self.oidc,
            r"options\.maxAge\s*!==\s*undefined",
            "verifyIdToken must branch on maxAge presence",
        )
        self.assertIn("auth_time", self.oidc, "verifyIdToken must check auth_time claim")

    def test_max_age_validation_uses_auth_time_window(self):
        # S2-δ γ-P0-2 — comparison is now
        # `authTime + options.maxAge + OIDC_CLOCK_TOLERANCE_SECONDS < nowSeconds`
        # (clock-skew tolerance applied to client/server clock diff).
        self.assertRegex(
            self.oidc,
            r"authTime\s*\+\s*options\.maxAge\s*\+\s*OIDC_CLOCK_TOLERANCE_SECONDS\s*<",
            "verifyIdToken must compare auth_time + maxAge + tolerance against now (γ-P0-2)",
        )

    def test_force_reauth_flag_is_literal_union_type(self):
        # β-P2-5
        self.assertRegex(
            self.storage_keys,
            r"export\s+type\s+OidcForceReauthFlag\s*=\s*['\"]pending['\"]",
            "OidcForceReauthFlag must be a Literal Union type (future-extensible)",
        )


class TestS2GammaP1Alignments(unittest.TestCase):
    """Sprint S2-γ β-P1-1 β-P1-2 β-P1-3 β-P1-4 β-P1-6 — P1 정공법 정렬
    invariants. β-P1-5 (npm ci deterministic) is verified at commit
    time by actual execution, recorded in the commit body."""

    def test_jsonschema_is_required_dep(self):
        # β-P1-1 — graceful-skip retired; jsonschema must be in requirements.
        req = _read(project_root / "requirements.txt")
        self.assertIsNotNone(
            re.search(r"^jsonschema", req, re.MULTILINE),
            "jsonschema must be a top-level requirement (β-P1-1)",
        )

    def test_jwks_cooldown_constants_cite_owasp_or_jose(self):
        # β-P1-2 — JWKS cooldown/timeout SSOT must cite industry source.
        text = _read(SRC_AUTH / "oidc-pkce.ts")
        self.assertTrue(
            "OWASP" in text or "panva/jose" in text,
            "JWKS_COOLDOWN_MS / JWKS_TIMEOUT_MS docs must cite OWASP or jose",
        )

    def test_measure_bundle_supports_build_and_stdout_modes(self):
        # β-P1-3 — measure-bundle.mjs has --build (vite build integration)
        # and --stdout (override default file output).
        text = _read(WEB_ROOT / "scripts" / "measure-bundle.mjs")
        self.assertIn("--build", text)
        self.assertIn("--stdout", text)
        self.assertIn("STALENESS_WARN_MINUTES", text)
        self.assertIn("DEFAULT_OUTPUT_PATH", text)

    def test_jose_mock_state_reset_in_test_beforeach(self):
        # β-P1-4 — vi.hoisted mock state reset between tests so
        # mockResolvedValueOnce queues do not leak.
        text = _read(TESTS_AUTH / "oidc-pkce.test.ts")
        self.assertIn("joseMockState.verifySpy.mockReset()", text)
        self.assertIn("joseMockState.resolverSpy.mockReset()", text)

    def test_cross_tech_token_policy_has_runbook_section(self):
        # β-P1-6 + S2-δ γ-P1-1 — runbook now distinguishes clock-skew
        # tolerance vs silent-refresh schedule margin (concepts separated).
        text = _read(project_root / "docs" / "architecture" / "frontend" / "cross-tech-token-policy.md")
        self.assertRegex(
            text,
            r"##\s+Runbook",
            "cross-tech-token-policy.md must include a Runbook section (β-P1-6)",
        )
        for must_contain in (
            "OIDC_REFRESH_MARGIN_SECONDS",
            "MIN_REFRESH_MARGIN_SECONDS",
            "OIDC_CLOCK_TOLERANCE_SECONDS",
        ):
            self.assertIn(must_contain, text, f"Runbook must reference {must_contain}")
        # γ-P2-3 — runbook MUST NOT recommend `pytest -v` (debug-only flag).
        self.assertNotRegex(
            text,
            r"pytest\s+[^\n]*?-v\b",
            "Runbook should not recommend `pytest -v` — `-q` is the best practice",
        )


class TestS2GammaSchemaCitations(unittest.TestCase):
    """Sprint S2-γ β-P2-1 — JSON Schema min/max values now cite their
    industry source in the description field."""

    def setUp(self):
        self.schema = json.loads(_read(INFRA_ROOT / "idp-policy.schema.json"))

    def test_each_lifetime_field_cites_min_max_source(self):
        for field in ("accessTokenLifespanSeconds", "ssoSessionIdleSeconds", "ssoSessionMaxSeconds"):
            desc = self.schema.get("properties", {}).get(field, {}).get("description", "")
            self.assertIn("minimum=", desc, f"{field} description must cite minimum= source")
            self.assertIn("maximum=", desc, f"{field} description must cite maximum= source")
            self.assertTrue(
                "NIST" in desc or "BCP" in desc or "OAuth" in desc,
                f"{field} description must cite NIST / BCP / OAuth standard",
            )


class TestS2GammaObservabilityAndA11y(unittest.TestCase):
    """Sprint S2-γ β-P2-6 β-P2-7 β-P2-8 β-P2-9 β-P2-10 — observability
    + accessibility batch."""

    def setUp(self):
        self.storage = _strip_ts_comments(_read(SRC_AUTH / "storage-keys.ts"))
        self.session = _read(SRC_AUTH / "session.ts")
        self.route_guard = _read(SRC_AUTH / "route-guard.tsx")
        self.failure_ui = _read(SRC_AUTH / "failure-ui.tsx")

    def test_purge_legacy_storage_emits_metric(self):
        # β-P2-6 + S2-ε δ-P0-1 — purgeLegacyStorage logs count when > 0.
        # S2-ε switched from console.info (eslint not in allow list) to
        # console.warn (allowed). Either is acceptable as a log emit.
        self.assertTrue(
            "console.info" in self.storage or "console.warn" in self.storage,
            "purgeLegacyStorage must emit console.info or console.warn on purge",
        )
        self.assertIn("purged", self.storage)

    def test_route_guard_calls_sentry_capture_exception(self):
        # β-P2-7 — OidcFlowError throw paths captured by Sentry.
        self.assertIn(
            "import { captureException }",
            self.route_guard,
            "route-guard.tsx must import captureException from @/observability/sentry",
        )
        # Both flow entry points (startLogin + completeLogin) report.
        self.assertGreaterEqual(
            self.route_guard.count("captureException("),
            2,
            "captureException must be called from at least 2 auth flow entry points",
        )

    def test_sign_out_button_has_aria_busy_and_polite(self):
        # β-P2-8 — a11y for the in-progress sign-out.
        self.assertRegex(
            self.route_guard,
            r"aria-busy=\{busy\}",
            "SignOutButton must wire aria-busy",
        )
        # When busy, the polite live region announces the action.
        self.assertIn('aria-label={busy ?', self.route_guard)

    def test_failure_detail_uses_polite_live_region(self):
        # β-P2-9 — detail block should not interrupt the assertive parent
        # alert; polite defers to next reading pause.
        self.assertRegex(
            self.failure_ui,
            r'aria-live="polite"',
            "failure detail block must override the inherited assertive live region",
        )

    def test_subscribe_auth_documents_unsubscribe_obligation(self):
        # β-P2-10 — docstring warns non-React callers to unsubscribe.
        # Normalise multi-line JSDoc whitespace before searching.
        normalized = re.sub(r"\s+\*?\s+", " ", self.session)
        self.assertIn(
            "the returned unsubscribe function",
            normalized,
            "subscribeAuth docstring must instruct callers to invoke the returned unsubscribe function",
        )
        self.assertIn("automatic cleanup on unmount", normalized)




class TestSprintSelfAuditEnforcement(unittest.TestCase):
    """Sprint S2-δ γ-P2-7 + W.2 — every Sprint S2-x evaluation file must
    contain a self-audit section. Markdown-only checklists alone don't
    prevent skipping; this invariant turns the checklist into a hard
    gate for the front-end auth sprint family."""

    SELF_AUDIT_HEADER_PATTERN = re.compile(r"##\s+(자평\s+audit|Sprint\s+self-audit)", re.IGNORECASE)
    EVAL_DIR = project_root / ".claude" / "evaluations"
    # Sprint S2-ε δ-P2-10 — generalized from `frontend-s2-*` to all
    # frontend-* sprint families so future Sprint S3+, S4+, S5+
    # evaluations also pay the self-audit tax. Add other family
    # prefixes here as they emerge.
    EVAL_GLOB = "frontend-*-followup.md"

    def test_every_s2_evaluation_has_self_audit_section(self):
        if not self.EVAL_DIR.is_dir():
            self.skipTest("evaluation directory missing")
            return
        evaluations = list(self.EVAL_DIR.glob(self.EVAL_GLOB))
        if not evaluations:
            self.skipTest("no S2 evaluation files yet")
            return
        for path in evaluations:
            text = _read(path)
            self.assertRegex(
                text,
                self.SELF_AUDIT_HEADER_PATTERN,
                f"{path.name} must contain a '## 자평 audit' or "
                f"'## Sprint self-audit' section (γ-P2-7 enforcement)",
            )


class TestExternalAuditRoadmapPresent(unittest.TestCase):
    """Sprint S2-δ W.1 + S2-EXT-1 — external audit roadmap documents the
    boundary where self-audit chains stop being sufficient and external
    tools (axe-core / Semgrep / OIDC conformance / peer review) take
    over."""

    def test_roadmap_doc_exists(self):
        path = project_root / "docs" / "architecture" / "frontend" / "external-audit-roadmap.md"
        self.assertTrue(path.is_file(), "external-audit-roadmap.md must exist")
        text = _read(path)
        # Core tools the roadmap MUST name.
        for tool in ("axe-core", "Semgrep", "OIDC conformance", "OWASP"):
            self.assertIn(tool, text, f"external-audit-roadmap.md must reference {tool}")


class TestS2ExtFullChain(unittest.TestCase):
    """Sprint S2-EXT-2 ~ S2-EXT-5 — verify every external-audit tool's
    infrastructure has shipped (config files, specs, CI scaffolding).
    Actual tool execution is opt-in; this invariant ensures the
    scaffolding does not regress."""

    def test_semgrep_config_present_and_uses_typescript_pack(self):
        # EXT-2 — `.semgrep.yml` exists + custom rule for the auth
        # localStorage ban (defence in depth with TestSessionStorageOnly).
        path = project_root / ".semgrep.yml"
        self.assertTrue(path.is_file(), ".semgrep.yml must exist (S2-EXT-2)")
        text = _read(path)
        self.assertIn("fcc-no-localstorage-tokens-in-auth", text)
        self.assertIn("p/typescript", text, "config invocation must chain p/typescript")
        self.assertIn("apps/web/src/auth/", text)

    def test_oidc_conformance_spec_present(self):
        # EXT-3 — opt-in conformance spec exists.
        path = WEB_ROOT / "tests" / "e2e" / "oidc-conformance.spec.ts"
        self.assertTrue(path.is_file(), "oidc-conformance.spec.ts must exist (S2-EXT-3)")
        text = _read(path)
        for required in (
            "well-known/openid-configuration",
            "code_challenge_methods_supported",
            "S256",
            "id_token_signing_alg_values_supported",
            "RS256",
            "jwks_uri",
        ):
            self.assertIn(required, text, f"OIDC conformance spec must check {required}")

    def test_zap_baseline_compose_present(self):
        # EXT-4 — OWASP ZAP compose scaffolded.
        path = INFRA_ROOT / "docker-compose.zap.yml"
        self.assertTrue(path.is_file(), "infra/docker-compose.zap.yml must exist (S2-EXT-4)")
        text = _read(path)
        self.assertIn("zaproxy/zap-stable", text)
        self.assertIn("zap-baseline.py", text)

    def test_peer_review_scaffolding_present(self):
        # EXT-5 — CODEOWNERS + PR template.
        codeowners = project_root / ".github" / "CODEOWNERS"
        pr_template = project_root / ".github" / "pull_request_template.md"
        self.assertTrue(codeowners.is_file(), "CODEOWNERS must exist (S2-EXT-5)")
        self.assertTrue(pr_template.is_file(), "pull_request_template.md must exist (S2-EXT-5)")
        co_text = _read(codeowners)
        # 인증 표면은 자동 라우팅된다.
        self.assertIn("/apps/web/src/auth/", co_text)
        # ⚠️ 옛 판본은 백엔드 resolver 의 **경로 문자열**을 얼려 뒀다. 그 모듈이
        # `fcc-test-contracts` 로 이사하자 명제는 그대로 참인데 검사만 거짓이 됐다.
        # 문자열 대신 «적힌 경로가 실재하는가» 를 물으면 이사를 따라가고,
        # 아무것도 지키지 않는 유령 줄도 함께 잡힌다.
        listed = [
            line.split()[0]
            for line in co_text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(listed, "CODEOWNERS 에 규칙 줄이 하나도 없다")
        ghosts = [
            rule for rule in listed
            if rule != "*" and not (project_root / rule.lstrip("/")).exists()
        ]
        self.assertEqual(
            [], ghosts,
            f"CODEOWNERS 가 이 레포에 없는 경로를 든다 — 리뷰어를 배정하지 않는다: {ghosts}",
        )


class TestAxeCorePlaywrightInstalled(unittest.TestCase):
    """Sprint S2-EXT-1 — axe-core is the first external audit tool to
    move from roadmap-markdown to actual code."""

    def test_axe_core_playwright_is_devdep(self):
        pkg = json.loads(_read(WEB_ROOT / "package.json"))
        self.assertIn("@axe-core/playwright", pkg.get("devDependencies", {}))

    def test_a11y_spec_exists(self):
        path = WEB_ROOT / "tests" / "e2e" / "a11y.spec.ts"
        self.assertTrue(path.is_file(), "apps/web/tests/e2e/a11y.spec.ts must exist (S2-EXT-1)")

    def test_a11y_spec_enforces_critical_and_serious(self):
        text = _read(WEB_ROOT / "tests" / "e2e" / "a11y.spec.ts")
        # Both impact buckets must be enforced (toEqual([])).
        self.assertIn("'critical'", text)
        self.assertIn("'serious'", text)
        self.assertRegex(
            text,
            r"new\s+AxeBuilder\s*\(",
            "spec must use AxeBuilder",
        )

    def test_roadmap_marks_axe_core_done(self):
        text = _read(project_root / "docs" / "architecture" / "frontend" / "external-audit-roadmap.md")
        self.assertIn(
            "Status: ✅ DONE (Sprint S2-EXT-1",
            text,
            "external-audit-roadmap.md must reflect axe-core as DONE",
        )




class TestRuntimeConfigStubDriftGuard(unittest.TestCase):
    """Sprint S2-ζ ζ-1 — Cross-language SSOT for the runtime-config dev stub.

    The dev stub at ``apps/web/public/runtime-config.js`` and the Zod schema
    at ``apps/web/src/config/runtime.ts`` must declare the same top-level
    field set. The vitest spec ``apps/web/tests/runtime-config-stub.test.ts``
    runs the stub in a Node ``vm`` sandbox and asserts ``safeParse`` succeeds;
    this Python invariant adds a *cross-language* check that catches drift
    even when the frontend test suite is not run (e.g. backend-only CI lane).
    """

    STUB_PATH = WEB_ROOT / "public" / "runtime-config.js"
    SCHEMA_PATH = WEB_ROOT / "src" / "config" / "runtime.ts"
    VITEST_SPEC = WEB_ROOT / "tests" / "runtime-config-stub.test.ts"

    def _stub_keys(self) -> set[str]:
        # The stub is a tiny assignment ``window.__FCC_RUNTIME_CONFIG__ = { … };``.
        # We do not execute JS from Python; instead we extract top-level
        # property names by a forgiving regex that ignores values, nested
        # objects, and arrays. Any string token that appears as ``key:`` at
        # indent level 2 inside the outer object literal counts.
        text = _read(self.STUB_PATH)
        # Snip from the opening ``= {`` to the matching closing ``};``.
        start = text.find("__FCC_RUNTIME_CONFIG__")
        self.assertNotEqual(start, -1, "stub must assign __FCC_RUNTIME_CONFIG__")
        brace = text.find("{", start)
        # Walk braces to find matching close (the stub has nested
        # featureFlags object — a brace counter is the safe choice).
        depth = 0
        end = -1
        for i, ch in enumerate(text[brace:], start=brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        self.assertNotEqual(end, -1, "stub object literal must close")
        body = text[brace + 1 : end]
        # Match only top-level ``  key: `` lines (2-space indent + identifier
        # + colon). featureFlags' inner keys live at 4-space indent so they
        # are excluded automatically.
        pattern = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_]*)\s*:", re.MULTILINE)
        return set(pattern.findall(body))

    def _zod_schema_keys(self) -> set[str]:
        text = _read(self.SCHEMA_PATH)
        # Find the ``z.object({ … }).strict()`` literal. It lives on
        # ``runtimeConfigObjectSchema``; the exported ``runtimeConfigSchema`` wraps
        # it in a superRefine (ZodEffects) that enforces the transport policy,
        # which needs to read a sibling field and so cannot be a per-field refine.
        match = re.search(
            r"export\s+const\s+runtimeConfigObjectSchema\s*=\s*z\s*\.object\s*\(\s*\{(.*?)\}\s*\)\s*\.strict",
            text,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "runtimeConfigObjectSchema z.object literal not found")
        body = match.group(1)
        # Each field looks like ``    fieldName: <Zod expr>,`` at the start
        # of a line. We accept any non-zero indent because Prettier may
        # reflow. Comments (``// …``) are skipped by the leading-identifier
        # constraint.
        pattern = re.compile(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:", re.MULTILINE)
        return set(pattern.findall(body))

    def test_stub_top_level_keys_match_zod_schema(self):
        stub_keys = self._stub_keys()
        zod_keys = self._zod_schema_keys()
        self.assertEqual(
            stub_keys,
            zod_keys,
            (
                "runtime-config dev stub drifted from runtimeConfigSchema. "
                f"In stub but not schema: {sorted(stub_keys - zod_keys)}. "
                f"In schema but not stub: {sorted(zod_keys - stub_keys)}. "
                "Sync apps/web/public/runtime-config.js with the Zod field set "
                "in apps/web/src/config/runtime.ts (S2-ζ ζ-1)."
            ),
        )

    def test_stub_carries_s2_alpha_oidc_fields(self):
        # Explicit named assertions guarantee the *exact* fields that were
        # missing for 5 sprints stay present even if the regex above ever
        # over-permissive in the future.
        stub_keys = self._stub_keys()
        for required in ("oidcAudience", "oidcScopes", "oidcPostLogoutRedirectUri"):
            self.assertIn(required, stub_keys, f"S2-α field '{required}' must be in dev stub")

    def test_vitest_spec_exists_for_runtime_stub(self):
        self.assertTrue(
            self.VITEST_SPEC.is_file(),
            "apps/web/tests/runtime-config-stub.test.ts must exist (S2-ζ ζ-1)",
        )
        text = _read(self.VITEST_SPEC)
        self.assertIn("runtimeConfigSchema.safeParse", text)
        self.assertIn("oidcAudience", text)
        self.assertIn("oidcScopes", text)
        self.assertIn("oidcPostLogoutRedirectUri", text)


class TestRealAuthExternalRequestTaxonomy(unittest.TestCase):
    """Real-auth/visual external traffic is an explicit fail-closed contract."""

    REAL_AUTH = WEB_ROOT / "tests" / "e2e" / "helpers" / "real-auth-fixture.ts"
    VISUAL = WEB_ROOT / "tests" / "e2e" / "helpers" / "visual-fixture.ts"
    AUTH_FLOW = WEB_ROOT / "tests" / "e2e" / "auth-flow.spec.ts"
    OIDC_CONFORMANCE = WEB_ROOT / "tests" / "e2e" / "oidc-conformance.spec.ts"

    def setUp(self):
        self.real_auth = _strip_ts_comments(_read(self.REAL_AUTH))
        self.visual = _strip_ts_comments(_read(self.VISUAL))
        self.auth_flow = _strip_ts_comments(_read(self.AUTH_FLOW))
        self.oidc_conformance = _strip_ts_comments(_read(self.OIDC_CONFORMANCE))

    def test_taxonomy_names_only_the_six_contracted_families(self):
        for family in (
            "discovery",
            "authorization",
            "token",
            "credential-submit",
            "rp-logout",
            "static-asset",
        ):
            with self.subTest(family=family):
                self.assertIn(f"family: '{family}'", self.real_auth)
        for path in (
            "DISCOVERY_PATH",
            "JWKS_PATH",
            "AUTH_PATH",
            "TOKEN_PATH",
            "CREDENTIAL_SUBMIT_PATH",
            "RP_LOGOUT_PATH",
            "KEYCLOAK_STATIC_ASSET_PREFIX",
        ):
            with self.subTest(path=path):
                self.assertIn(path, self.real_auth)
        self.assertIn("KEYCLOAK_REQUEST_TAXONOMY", self.real_auth)
        self.assertIn("KEYCLOAK_EXTERNAL_REQUEST_POLICY", self.real_auth)
        self.assertIn("externalRequestPolicy", self.visual)

    def test_origin_wide_allowlists_are_absent(self):
        for label, source in (("real-auth", self.real_auth), ("visual", self.visual)):
            with self.subTest(label=label):
                self.assertNotIn("allowedExternalOrigins", source)
                self.assertNotIn("allowedOrigins", source)
        self.assertRegex(
            self.real_auth,
            r"url\.origin\s*!==\s*new URL\(KEYCLOAK_BASE_URL\)\.origin",
        )
        self.assertIn("decision === null", self.visual)
        self.assertIn("unclassified external request", self.visual)

    def test_planted_arbitrary_path_wrong_method_and_failed_status_are_red(self):
        """Static red controls mirror the exact classifier branches.

        The planted controls are deliberately concrete: an admin path is not a
        taxonomy path, GET token is not the token method, and HTTP 500 is outside
        every success class. The source checks prove the production classifier
        uses exact method/path matching and status classes rather than a broad
        origin predicate.
        """
        self.assertRegex(
            self.real_auth,
            r"candidate\.methods\.includes\(normalizedMethod\)",
        )
        self.assertRegex(
            self.real_auth,
            r"candidate\.exactPaths\?\.includes\(url\.pathname\)",
        )
        self.assertRegex(
            self.visual,
            r"status >= 200 && status < 300",
        )
        self.assertIn("return null", self.real_auth)
        planted_controls = (
            ("GET", "/realms/fcc-dev/admin/realms", 200),
            ("GET", "/realms/fcc-dev/protocol/openid-connect/token", 200),
            ("POST", "/realms/fcc-dev/protocol/openid-connect/token", 500),
        )
        for method, path, status in planted_controls:
            with self.subTest(method=method, path=path, status=status):
                self.assertNotIn(
                    f"exactPaths: [{path}]",
                    self.real_auth,
                    "planted red control accidentally became a declared exact path",
                )
                if method == "GET" and path.endswith("/token"):
                    self.assertNotRegex(
                        self.real_auth,
                        r"family: 'token'[^\n]*\n\s*methods: \['GET'\]",
                    )
                self.assertNotIn(f"status === {status}", self.real_auth)

    def test_logout_observes_all_storage_and_separates_fresh_transaction(self):
        for key in (
            "ALL_STORAGE_KEYS",
            "__fccRecordLogoutStorageWitness",
            "pre-app-bootstrap",
            "fresh-auth-transaction",
            "STORAGE_KEY_STATE",
            "STORAGE_KEY_VERIFIER",
            "STORAGE_KEY_NONCE",
        ):
            self.assertIn(key, self.auth_flow)
        self.assertIn("authorizationRequestStates", self.real_auth)
        self.assertIn("failedResponses", self.real_auth)
        self.assertIn(
            "real IdP and SPA callback/route guard proven; backend authorization not claimed.",
            self.auth_flow,
        )

    def test_enabled_oidc_conformance_fails_on_idp_failure(self):
        self.assertNotIn("test.skip(true", self.oidc_conformance)
        self.assertRegex(self.oidc_conformance, r"expect\(\s*resp\.ok\(\)")
        self.assertRegex(self.oidc_conformance, r"expect\(\s*discoveryResp\.ok\(\)")


class TestFrontendE2eOidcWorkflowWiring(unittest.TestCase):
    """W4-11 — the Keycloak job must exercise the real auth lane non-vacuously.

    The JavaScript report validator owns report semantics. This root invariant
    owns the workflow boundary: both spec paths are selected, IdP readiness is
    blocking, one validator is called, and compose teardown remains unconditional.
    """

    WORKFLOW = project_root / ".github" / "workflows" / "frontend.yml"

    def setUp(self):
        self.assertTrue(self.WORKFLOW.is_file(), "frontend.yml must exist")
        text = _read(self.WORKFLOW)
        marker = text.find("oidc-conformance:")
        self.assertGreaterEqual(marker, 0, "oidc-conformance job is missing")
        self.job = text[marker:]

    def test_job_explicitly_runs_both_required_specs(self):
        auth_spec = "tests/e2e/auth-flow.spec.ts"
        conformance_spec = "tests/e2e/oidc-conformance.spec.ts"
        self.assertIn(auth_spec, self.job)
        self.assertIn(conformance_spec, self.job)
        playwright_index = self.job.index("npx playwright test")
        self.assertLess(self.job.index(auth_spec), playwright_index + 200)
        self.assertLess(self.job.index(conformance_spec), playwright_index + 300)

    def test_job_is_real_idp_fail_closed_and_serial(self):
        self.assertRegex(self.job, r"E2E_OIDC:\s+['\"]1['\"]")
        self.assertIn("KEYCLOAK_BASE_URL: http://localhost:8081", self.job)
        self.assertIn("PLAYWRIGHT_JSON_OUTPUT_NAME: oidc-results.json", self.job)
        self.assertIn("--project=chromium-desktop", self.job)
        self.assertIn("--workers=1", self.job)
        self.assertIn("--retries=0", self.job)
        self.assertIn("exit 1", self.job)
        self.assertNotIn("continue-on-error", self.job)

    def test_one_report_validator_replaces_inline_report_parsers(self):
        parser = WEB_ROOT / "scripts" / "assert-oidc-e2e-report.mjs"
        self.assertTrue(parser.is_file(), "the external OIDC report parser must exist")
        self.assertEqual(self.job.count("node scripts/assert-oidc-e2e-report.mjs"), 1)
        self.assertIn("node scripts/assert-oidc-e2e-report.mjs oidc-results.json", self.job)
        self.assertNotIn("node -e", self.job)
        self.assertNotIn("JSON.parse", self.job)
        self.assertNotIn("stats", self.job)
        self.assertIn("oidc-results.json", self.job)

    def test_quality_gates_precede_the_oidc_path_via_the_build_dependency(self):
        """The gates still run first — they just run once, in the `build` job.

        ci-minutes-root-cause (2026-08-07): codegen/lint/unit/build used to be
        repeated inside this job, which is how one pull request paid for four
        builds. They now live in `build`, and `needs: build` is what orders them
        ahead of Keycloak — so the ordering is asserted across the dependency,
        not inside one step list.
        """
        self.assertIn("needs: build", self.job)
        whole = _read(self.WORKFLOW)
        build_job = whole[whole.index("  build:"):whole.index("  e2e:")]
        for gate in (
            "run: npm ci",
            "run: npm run codegen\n",
            "run: npm run codegen:check",
            "run: npm run lint",
            "run: npm test",
            "run: npm run build",
        ):
            with self.subTest(gate=gate.strip()):
                self.assertIn(gate, build_job)
        self.assertNotIn("npm run build", self.job)

        ordered_steps = (
            "run: npm ci",
            "download build output",
            "docker compose -f infra/docker-compose.idp.yml up -d",
            "wait for Keycloak readiness",
        )
        positions = [self.job.index(step) for step in ordered_steps]
        self.assertEqual(positions, sorted(positions))

    def test_fail_closed_selection_and_cleanup_are_explicit(self):
        self.assertIn("tests/e2e/auth-flow.spec.ts", self.job)
        self.assertIn("tests/e2e/oidc-conformance.spec.ts", self.job)
        self.assertIn("--retries=0", self.job)
        self.assertIn("exit 1", self.job)
        self.assertNotIn("continue-on-error", self.job)
        validator = self.job.index("node scripts/assert-oidc-e2e-report.mjs oidc-results.json")
        cleanup = self.job.rfind("docker compose -f infra/docker-compose.idp.yml down -v")
        self.assertGreater(cleanup, validator)
        self.assertIn("if: always()", self.job[max(0, cleanup - 180) : cleanup])

    def test_readiness_playwright_validation_and_cleanup_order_is_sealed(self):
        readiness = self.job.index("wait for Keycloak readiness")
        playwright = self.job.index("playwright real OIDC auth and conformance specs")
        validator = self.job.index("validate complete OIDC JSON report")
        cleanup = self.job.rfind("docker compose -f infra/docker-compose.idp.yml down -v")
        self.assertLess(readiness, playwright)
        self.assertLess(playwright, validator)
        self.assertGreater(cleanup, validator)
        self.assertIn("if: always()", self.job[max(0, cleanup - 180) : cleanup])



class TestS2DeltaP2Cleanup(unittest.TestCase):
    """S2-δ 정리 — 주제가 `apps/web` 인 것만 남았다.

    ⚠️ `test_sprint_self_audit_hook_has_powershell_variant` 는 소비하는 것이
    `.claude/hooks` 라 2026-08-31 에 모노레포로 돌아갔다
    (`tests/test_frontend_sprint_governance_records.py`).
    """

    def test_force_reauth_flag_is_literal_type_not_union(self):
        # γ-P2-2 — `'pending'` is a literal type, not a Literal Union.
        # The docstring must reflect that.
        text = _read(SRC_AUTH / "storage-keys.ts")
        self.assertRegex(
            text,
            r"export\s+type\s+OidcForceReauthFlag\s*=\s*['\"]pending['\"]",
        )
        # Comments now call it "literal type" not "Literal Union".
        self.assertIn("literal type", text.lower())

    def test_idp_policy_schema_no_meaningless_allof(self):
        # γ-P2-4 — the meaningless `allOf` block must be removed.
        schema = json.loads(_read(INFRA_ROOT / "idp-policy.schema.json"))
        self.assertNotIn(
            "allOf",
            schema,
            "Meaningless allOf block must be removed (γ-P2-4)",
        )

    def test_storage_key_max_age_avoids_http_cache_control_collision(self):
        # γ-P2-10 — the key suffix uses 'auth-max-age' (not bare 'max-age')
        # so a grep of sessionStorage doesn't get confused with HTTP
        # `Cache-Control: max-age`.
        text = _read(SRC_AUTH / "storage-keys.ts")
        self.assertRegex(
            text,
            r"STORAGE_KEY_MAX_AGE\s*=\s*`\$\{OIDC_STORAGE_PREFIX\}:auth-max-age`",
            "STORAGE_KEY_MAX_AGE suffix must be 'auth-max-age' (γ-P2-10)",
        )

    def test_idp_policy_carries_bcp_revision_marker(self):
        # γ-P2-11 — BCP citation is a draft; when it reaches RFC status,
        # the citation must be updated. A revision marker makes the
        # dependency explicit.
        policy = json.loads(_read(INFRA_ROOT / "idp-policy.json"))
        self.assertIn(
            "_basisCitationRevision",
            policy,
            "idp-policy.json must carry _basisCitationRevision marker (γ-P2-11)",
        )

if __name__ == "__main__":
    unittest.main()
