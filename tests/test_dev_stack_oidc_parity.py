# ⚠️ 2026-08-31: 이 파일은 모노레포 `tests/test_dev_stack_oidc_parity.py` 에서 갈라져 왔다. 남은 것은
#    소비 대상이 이 레포에 있는 단위(TestDevRealmIssuesStandardSubjectClaim)뿐이고,
#    나머지 형제 검사와 그것들만 쓰던 import 는 저쪽에 남았다.
"""Seal the dev-stack OIDC SSOT derivation (dev-oidc-ssot-standardization, 2026-06-26).

The local dev-stack launcher (`apps/web/scripts/dev-stack.mjs`) derives the backend
OIDC env for all three ASGI surfaces (session / headless / platform) from ONE
browser-facing SSOT — `apps/web/public/runtime-config.dev.json` — via the pure
`apps/web/scripts/derive-oidc-env.mjs`. These invariants prevent that wiring from
silently regressing into per-developer hardcoding or drifting from the Keycloak
realm the SPA logs into.

Three layers:
  1. cross-source value parity (always-on, pure Python) — the realm client whose
     token the APIs consume must emit the audience the backend verifies, and the
     SSOT/realm/backend-contract must agree.
  2. no-hardcode source contract — the launcher derives from the SSOT file and
     carries no OIDC origin/realm/audience literal.
  3. behavioural derivation (skipped when node is absent) — runs the actual
     `derive-oidc-env.mjs` and checks the emitted env against the formula.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


from application.headless.runtime_config import FCC_HEADLESS_AUTH_ENV_PREFIX
from fcc_test_platform.application.runtime_config import PLATFORM_AUTH_ENV_PREFIX
from application.session.runtime_config import FCC_SESSION_AUTH_ENV_PREFIX
import subprocess
from fcc_test_contracts.common.auth_config import HttpAuthConfig  # noqa: E402
from tests.support.parity import assert_set_equality  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_CONFIG = _ROOT / "apps" / "web" / "public" / "runtime-config.dev.json"
_REALM = _ROOT / "infra" / "keycloak" / "fcc-dev-realm.json"
_DERIVE_MJS = _ROOT / "apps" / "web" / "scripts" / "derive-oidc-env.mjs"
_DEV_STACK_MJS = _ROOT / "apps" / "web" / "scripts" / "dev-stack.mjs"

#: OIDC discovery JWKS path — MUST match derive-oidc-env.mjs::JWKS_PATH_SUFFIX.
_JWKS_PATH_SUFFIX = "/protocol/openid-connect/certs"

#: The three surface prefixes the launcher injects, paired with the runtime config.
_SURFACE_PREFIXES = (
    FCC_SESSION_AUTH_ENV_PREFIX,
    FCC_HEADLESS_AUTH_ENV_PREFIX,
    PLATFORM_AUTH_ENV_PREFIX,
)

#: Realm clients whose access token the platform/headless APIs consume — every
#: one MUST carry an audience mapper for the API audience or its token is rejected.
_API_CONSUMER_CLIENT_IDS = (
    "fcc-platform-frontend",
    "fcc-chamber-node",
    "fcc-staging-cli",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _audience_mapper_values(client: dict) -> list[str]:
    """All `included.client.audience` values declared on a realm client."""
    values = []
    for mapper in client.get("protocolMappers", []) or []:
        if mapper.get("protocolMapper") == "oidc-audience-mapper":
            config = mapper.get("config") or {}
            audience = config.get("included.client.audience")
            if audience:
                values.append(audience)
    return values








class TestDevRealmIssuesStandardSubjectClaim(unittest.TestCase):
    """The dev realm must issue the OIDC standard `sub` claim.

    Defect this seals (dev-environment-contract-parity, 2026-08-01): a dev login
    succeeded, the access token carried all 14 `permissions` entries — and every
    single `/platform/*` request still answered
    ``{"detail":"JWT subject claim is required","code":"FORBIDDEN"}``. The token
    simply had no `sub`. Not an authorisation problem: an IDENTITY problem.

    Root cause: `defaultClientScopes` listed `openid`, which is not a Keycloak
    client scope at all, and omitted the scope that actually mints `sub`. The
    realm file therefore *looked* configured while the effective scope set read
    back from the admin API was ``['roles','profile','email']``.

    WHY THE REASON, NOT THE NAME, IS THE CONTRACT: the scope name below is a
    Keycloak implementation detail that already moved once (the claim lived in
    the always-on token core before Keycloak 24). On the next upgrade, do not
    ask "is `basic` still listed" — ask "which scope issues `sub` in this
    version" and update the mapping with a fresh reason. The realm import JSON
    cannot carry the rationale itself: Keycloak 25's `--import-realm`
    deserialises into `RealmRepresentation` and rejects unknown properties, so an
    inline `_comment` key makes the container exit(1) (sealed by
    ``tests/test_central_docker_compose.py::TestCentralKeycloakRealmImportable``).
    This module is where the reasoning has to live.

    Static layer only. The behavioural counterpart — decoding a token the running
    Keycloak actually issued — is ``apps/web/tests/e2e/auth-flow.spec.ts``, which
    is inert unless ``E2E_OIDC=1``. So this class is the layer that runs in CI.
    """

    #: Client scope → why the SPA client cannot issue a usable token without it.
    _REQUIRED_SCOPES: dict[str, str] = {
        "basic": (
            "mints the OIDC standard claims `sub` and `auth_time`. Keycloak >= 24 "
            "moved them out of the always-on token core into this dedicated "
            "client scope, so a client missing it receives an access token with "
            "no subject and every platform API call fails with "
            "'JWT subject claim is required' (403) despite correct permissions."
        ),
    }

    #: Values that LOOK like scopes but are not — Keycloak drops them without a
    #: warning, so listing one manufactures the illusion of configuration.
    _PHANTOM_SCOPES: dict[str, str] = {
        "openid": (
            "`openid` is an authorization-REQUEST scope parameter, not a Keycloak "
            "client scope. Keycloak silently ignores it in defaultClientScopes — "
            "the effective set read back through the admin API was "
            "['roles','profile','email'], i.e. the entry did nothing at all. A "
            "silently-ignored entry is worse than a missing one because it "
            "reads as configured."
        ),
    }

    def setUp(self) -> None:
        self.realm = _load_json(_REALM)
        # Clients that inherit the realm-level defaults never spell the key out;
        # only an explicit list can be wrong, so only an explicit list is judged.
        self.declaring = [
            c for c in self.realm["clients"] if c.get("defaultClientScopes") is not None
        ]

    def test_at_least_one_client_declares_default_scopes(self) -> None:
        """Non-vacuity: deleting the key everywhere must not make this class pass.

        Without this, the two assertions below would degrade into no-ops the
        moment someone removed `defaultClientScopes` — a green suite guarding
        nothing, which is the failure mode that let the original defect ship.
        """
        self.assertTrue(
            self.declaring,
            "no realm client declares defaultClientScopes — this class would "
            "then assert nothing. Either restore the SPA client's explicit scope "
            "list or delete this seal deliberately.",
        )

    def test_declared_scopes_include_the_subject_claim_scope(self) -> None:
        for client in self.declaring:
            client_id = client.get("clientId")
            scopes = set(client["defaultClientScopes"])
            for scope, reason in self._REQUIRED_SCOPES.items():
                with self.subTest(client=client_id, scope=scope):
                    self.assertIn(
                        scope,
                        scopes,
                        f"realm client {client_id!r} must carry the {scope!r} "
                        f"client scope — it {reason} Present scopes: "
                        f"{sorted(scopes)}.",
                    )

    def test_declared_scopes_contain_no_phantom_entries(self) -> None:
        for client in self.declaring:
            client_id = client.get("clientId")
            scopes = set(client["defaultClientScopes"])
            for phantom, reason in self._PHANTOM_SCOPES.items():
                with self.subTest(client=client_id, scope=phantom):
                    self.assertNotIn(
                        phantom,
                        scopes,
                        f"realm client {client_id!r} lists {phantom!r} in "
                        f"defaultClientScopes, but {reason}",
                    )



# ── 모노레포 `tests/test_dev_stack_oidc_parity.py` 에서 옮겨왔다 (2026-08-31).
#    이 세 클래스가 읽는 것은 전부 `apps/web/…` 이고 그 트리는 여기 있다.


class TestDevStackOidcSsotValueParity(unittest.TestCase):
    """Cross-source value agreement — pure Python, always enforced."""

    def setUp(self) -> None:
        self.runtime = _load_json(_RUNTIME_CONFIG)
        self.realm = _load_json(_REALM)
        self.issuer = self.runtime["oidcIssuer"]
        self.client_id = self.runtime["oidcClientId"]

    def test_runtime_config_carries_the_two_ssot_fields(self) -> None:
        self.assertTrue(self.issuer, "runtime-config.dev.json oidcIssuer is empty")
        self.assertTrue(self.client_id, "runtime-config.dev.json oidcClientId is empty")
        self.assertTrue(
            self.issuer.startswith("http://") or self.issuer.startswith("https://"),
            f"oidcIssuer must be an absolute URL, got {self.issuer!r}",
        )

    def test_backend_audience_equals_runtime_client_id(self) -> None:
        # The token `aud` the backend verifies (= OIDC_CLIENT_ID convention, same
        # as infra/docker-compose.central.yml) is the SSOT oidcClientId.
        client = next(
            (c for c in self.realm["clients"] if c.get("clientId") == self.client_id),
            None,
        )
        self.assertIsNotNone(
            client, f"realm has no client {self.client_id!r} (runtime-config oidcClientId)"
        )
        self.assertIn(
            self.client_id,
            _audience_mapper_values(client),
            f"realm client {self.client_id!r} must emit aud={self.client_id!r} via an "
            "oidc-audience-mapper, else oidc_jwt backends reject its tokens "
            "(MissingRequiredClaimError 'aud').",
        )

    def test_every_api_consumer_client_emits_the_api_audience(self) -> None:
        # Seal against the regression where a token-bearing client lacks the
        # audience mapper (the original create_project 403 root cause).
        for client_id in _API_CONSUMER_CLIENT_IDS:
            client = next(
                (c for c in self.realm["clients"] if c.get("clientId") == client_id),
                None,
            )
            self.assertIsNotNone(client, f"realm missing API-consumer client {client_id!r}")
            self.assertIn(
                self.client_id,
                _audience_mapper_values(client),
                f"realm client {client_id!r} must emit aud={self.client_id!r} so the "
                "platform/headless APIs accept its token.",
            )

    def test_backend_env_key_contract_covers_the_nine_oidc_keys(self) -> None:
        # The launcher injects exactly these keys; assert the backend contract
        # (HttpAuthConfig.env_keys) actually names them for all three prefixes.
        expected = {
            f"{prefix}{suffix}"
            for prefix in _SURFACE_PREFIXES
            for suffix in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URI")
        }
        produced = set()
        for prefix in _SURFACE_PREFIXES:
            keys = HttpAuthConfig.env_keys(prefix)
            produced |= {keys["oidc_issuer"], keys["oidc_audience"], keys["oidc_jwks_uri"]}
        assert_set_equality(
            self,
            name="dev-stack OIDC env keys",
            left_label="expected FCC_<SURFACE>_OIDC_* (derive-oidc-env.mjs surfaces)",
            left=expected,
            right_label="HttpAuthConfig.env_keys() backend contract",
            right=produced,
        )

class TestDevStackOidcNoHardcode(unittest.TestCase):
    """The launcher derives from the SSOT file; no OIDC origin literal leaks in."""

    _BANNED_LITERALS = (
        "localhost:8081",
        "/protocol/openid-connect/certs",
        "fcc-platform-frontend",
        "realms/fcc-dev",
    )

    def test_dev_stack_reads_the_runtime_config_ssot(self) -> None:
        source = _DEV_STACK_MJS.read_text(encoding="utf-8")
        self.assertIn("runtime-config.dev.json", source)
        self.assertIn("applyOidcDefaults", source)

    def test_dev_stack_has_no_oidc_origin_literals(self) -> None:
        source = _DEV_STACK_MJS.read_text(encoding="utf-8")
        for literal in self._BANNED_LITERALS:
            self.assertNotIn(
                literal,
                source,
                f"dev-stack.mjs must derive OIDC from the SSOT, not hardcode {literal!r}",
            )

    def test_derive_module_only_constant_is_the_protocol_suffix(self) -> None:
        # The single permitted literal is the protocol-standard JWKS path; no
        # origin / realm / audience literal may appear in the derivation module.
        source = _DERIVE_MJS.read_text(encoding="utf-8")
        for literal in ("localhost:8081", "fcc-platform-frontend", "realms/fcc-dev"):
            self.assertNotIn(
                literal,
                source,
                f"derive-oidc-env.mjs must stay origin-agnostic, found {literal!r}",
            )

class TestDevStackOidcDerivationBehaviour(unittest.TestCase):
    """Run the ACTUAL derivation module and check it against the formula."""

    def _derive(self) -> dict:
        script = (
            "import {deriveOidcEnv} from "
            f"{json.dumps(str(_DERIVE_MJS))};"
            "import {readFileSync} from 'node:fs';"
            f"const rc=JSON.parse(readFileSync({json.dumps(str(_RUNTIME_CONFIG))},'utf8'));"
            "process.stdout.write(JSON.stringify(deriveOidcEnv(rc)));"
        )
        out = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
        return json.loads(out.stdout)

    def test_emitted_env_matches_the_derivation_formula(self) -> None:
        runtime = _load_json(_RUNTIME_CONFIG)
        issuer = runtime["oidcIssuer"]
        client_id = runtime["oidcClientId"]
        jwks = issuer.rstrip("/") + _JWKS_PATH_SUFFIX
        derived = self._derive()
        for prefix in _SURFACE_PREFIXES:
            self.assertEqual(derived[f"{prefix}OIDC_ISSUER"], issuer)
            self.assertEqual(derived[f"{prefix}OIDC_AUDIENCE"], client_id)
            self.assertEqual(derived[f"{prefix}OIDC_JWKS_URI"], jwks)

if __name__ == "__main__":
    unittest.main()
