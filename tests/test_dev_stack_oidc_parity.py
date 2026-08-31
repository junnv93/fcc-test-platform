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


if __name__ == "__main__":
    unittest.main()
