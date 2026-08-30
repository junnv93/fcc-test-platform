"""Per-chamber Keycloak confidential-client provisioning desired-state (pure).

ADR-0016 (per-chamber token binding) shipped the *enforcement seam*: a chamber
machine token carrying a ``chamber_id`` claim may only heartbeat that one
chamber (heartbeating another → 403). This module is the *credential
provisioning* half — it computes the desired Keycloak confidential-client
representation for one chamber so that the binding seam becomes active.

Design rules (SSOT):

- ``clientId = fcc-chamber-<chamber_id>`` — distinct from the committed generic
  ``fcc-chamber-node`` so no deployment-specific name pollutes the dev realm.
- ``serviceAccountsEnabled`` (client_credentials machine flow) only; never a
  public/standard/direct-grant flow.
- a hardcoded ``chamber_id`` claim mapper binds the token to one chamber.
- a hardcoded ``permissions`` claim equal to ``platform:chamber`` and nothing
  else (the node token is heartbeat-only; ``CHAMBER_PERMISSION`` is the single
  allowed permission token).
- the secret is an env placeholder (``${...}``) — a raw secret is NEVER emitted.

dependency-free (stdlib only) so it imports cleanly in frozen builds and in the
parity/evidence tests without dragging in infrastructure.
"""
from __future__ import annotations

import re
from typing import Mapping


__all__ = [
    'CHAMBER_CLIENT_ID_PREFIX',
    'CHAMBER_PERMISSION',
    'CHAMBER_ID_CLAIM',
    'CHAMBER_AUDIENCE',
    'AUTHZ_RELEVANT_CLAIMS',
    'chamber_client_id',
    'chamber_secret_env_var',
    'build_chamber_client_representation',
    'representation_permission_tokens',
    'representation_authz_permission_tokens',
]

#: One Keycloak confidential client per chamber, named off this prefix.
CHAMBER_CLIENT_ID_PREFIX = 'fcc-chamber-'

#: The ONLY permission a chamber machine token may carry (heartbeat-only).
CHAMBER_PERMISSION = 'platform:chamber'

#: Token claim that binds the machine token to a single chamber (ADR-0016).
CHAMBER_ID_CLAIM = 'chamber_id'

#: Audience the platform API validates against (mirrors the committed realm).
CHAMBER_AUDIENCE = 'fcc-platform-frontend'

#: The token claims the headless OIDC resolver
#: (``application.headless.oidc_principal_resolver.OidcJwtConfig``) treats as
#: authorization-relevant: it derives ``ApiPrincipal`` permissions from the
#: ``permissions`` claim, the ``scope`` claim, AND role-mapped values from the
#: ``roles`` claim. Therefore "grants only ``platform:chamber``" must be proven
#: across ALL THREE claims, not just ``permissions`` — a hardcoded ``scope`` or
#: ``roles`` mapper, or Keycloak's full-scope role mappers, could otherwise
#: inject extra authZ tokens. Kept as a literal SSOT here (this module stays
#: dependency-free / frozen-exe safe); parity with the resolver's default claim
#: names is sealed by ``tests/test_chamber_token_provisioning.py``.
AUTHZ_RELEVANT_CLAIMS = ('permissions', 'scope', 'roles')

_NORMALIZE = re.compile(r'[^A-Za-z0-9]+')


def chamber_client_id(chamber_id: str) -> str:
    """``fcc-chamber-<chamber_id>`` (trimmed). Raises on empty chamber id."""
    cid = str(chamber_id or '').strip()
    if not cid:
        raise ValueError('chamber_id is required')
    return f'{CHAMBER_CLIENT_ID_PREFIX}{cid}'


def chamber_secret_env_var(chamber_id: str) -> str:
    """Env var name that holds the per-chamber client secret (never the value).

    e.g. ``staging-chamber-a`` -> ``FCC_CHAMBER_STAGING_CHAMBER_A_CLIENT_SECRET``.
    """
    cid = str(chamber_id or '').strip()
    if not cid:
        raise ValueError('chamber_id is required')
    token = _NORMALIZE.sub('_', cid).strip('_').upper()
    return f'FCC_CHAMBER_{token}_CLIENT_SECRET'


def build_chamber_client_representation(
    chamber_id: str,
    *,
    secret_env_var: str | None = None,
) -> dict:
    """Desired Keycloak client representation for one chamber.

    The ``secret`` field is an env-substitution placeholder so a raw secret is
    never serialized into evidence, fixtures, or committed realm exports.
    """
    cid = str(chamber_id or '').strip()
    if not cid:
        raise ValueError('chamber_id is required')
    env_var = (secret_env_var or chamber_secret_env_var(cid)).strip()
    return {
        'clientId': chamber_client_id(cid),
        'name': f'FCC Chamber {cid} (machine token)',
        'description': (
            f'Per-chamber machine token (client_credentials) bound to '
            f'chamber_id={cid}. Emits permissions={CHAMBER_PERMISSION} + '
            f'{CHAMBER_ID_CLAIM}={cid} + aud={CHAMBER_AUDIENCE}. Secret from '
            f'{env_var} env (never raw in file).'
        ),
        'enabled': True,
        'protocol': 'openid-connect',
        'publicClient': False,
        'standardFlowEnabled': False,
        'implicitFlowEnabled': False,
        'directAccessGrantsEnabled': False,
        'serviceAccountsEnabled': True,
        'secret': '${' + env_var + '}',
        # ``fullScopeAllowed=false`` is load-bearing for the "only
        # platform:chamber" guarantee: with full scope ON, Keycloak's built-in
        # role/scope mappers would inject every realm role this client's service
        # account holds into the token's ``roles``/``scope`` claims — both of
        # which the headless OIDC resolver treats as authorization-relevant
        # (see AUTHZ_RELEVANT_CLAIMS). A heartbeat-only machine client must
        # carry ONLY its explicit hardcoded mappers below, so full scope is off.
        'fullScopeAllowed': False,
        'protocolMappers': [
            {
                'name': 'fcc-chamber-permissions',
                'protocol': 'openid-connect',
                'protocolMapper': 'oidc-hardcoded-claim-mapper',
                'consentRequired': False,
                'config': {
                    'claim.name': 'permissions',
                    'claim.value': CHAMBER_PERMISSION,
                    'jsonType.label': 'String',
                    'access.token.claim': 'true',
                    'id.token.claim': 'false',
                    'userinfo.token.claim': 'false',
                },
            },
            {
                'name': 'fcc-chamber-id',
                'protocol': 'openid-connect',
                'protocolMapper': 'oidc-hardcoded-claim-mapper',
                'consentRequired': False,
                'config': {
                    'claim.name': CHAMBER_ID_CLAIM,
                    'claim.value': cid,
                    'jsonType.label': 'String',
                    'access.token.claim': 'true',
                    'id.token.claim': 'false',
                    'userinfo.token.claim': 'false',
                },
            },
            {
                'name': 'fcc-chamber-audience',
                'protocol': 'openid-connect',
                'protocolMapper': 'oidc-audience-mapper',
                'consentRequired': False,
                'config': {
                    'included.client.audience': CHAMBER_AUDIENCE,
                    'access.token.claim': 'true',
                    'id.token.claim': 'false',
                },
            },
        ],
    }


def representation_permission_tokens(representation: Mapping) -> set[str]:
    """Every ``permissions`` claim value declared by hardcoded-claim mappers.

    Used to assert a chamber client grants ``platform:chamber`` and nothing else.
    """
    return _hardcoded_claim_tokens(representation, ('permissions',))


def representation_authz_permission_tokens(representation: Mapping) -> set[str]:
    """Every authorization-relevant token a chamber client's mappers can emit.

    Scans hardcoded-claim mappers across ALL ``AUTHZ_RELEVANT_CLAIMS``
    (``permissions`` + ``scope`` + ``roles``) — the same three claims the OIDC
    resolver derives ``ApiPrincipal`` permissions from. Asserting this equals
    ``{platform:chamber}`` (with ``fullScopeAllowed=false`` blocking implicit
    realm-role injection) proves the client cannot emit any additional
    authZ-relevant scope/role permission under the resolver contract.
    """
    return _hardcoded_claim_tokens(representation, AUTHZ_RELEVANT_CLAIMS)


def _hardcoded_claim_tokens(representation: Mapping, claim_names: tuple[str, ...]) -> set[str]:
    wanted = {name.strip() for name in claim_names}
    tokens: set[str] = set()
    for mapper in representation.get('protocolMappers') or []:
        if not isinstance(mapper, Mapping):
            continue
        config = mapper.get('config')
        if not isinstance(config, Mapping):
            continue
        if str(config.get('claim.name') or '').strip() not in wanted:
            continue
        for value in str(config.get('claim.value') or '').split(','):
            value = value.strip()
            if value:
                tokens.add(value)
    return tokens
