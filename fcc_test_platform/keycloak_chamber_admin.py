"""Live Keycloak admin REST helper for per-chamber client lifecycle.

Thin, stdlib-only (urllib) wrapper around the Keycloak admin REST API used by
``platform_chamber_token_evidence.py --live``. Kept separate so the dry-run path
(the unit-tested offline path) imports no HTTP machinery.

The REST surface is isolated behind ``KeycloakAdminClient`` (a narrow seam over
``_request``) so the lifecycle loop (``build_live_evidence``) is unit-testable
with a fake client — no real Keycloak, no faked HTTP success.

Honesty rules (no faked IdP success):

* Every recorded ``result`` reflects the actual admin REST outcome
  (``ok`` / ``failed`` / ``skipped``). A missing client on rotate/revoke is
  ``failed`` (the operator's pre-condition was not met), never silently ``ok``.
* Secrets are NEVER returned, printed, or written — evidence only ever carries
  the redacted marker. ``rotate`` calls Keycloak's client-secret regeneration
  endpoint but discards the returned secret value entirely.
* ``binding_check`` stays ``planned``: executing the real heartbeat 200/403
  probe needs a live node + platform URL/token, which this admin helper does not
  have. The evidence says so honestly rather than fabricating a probe result.

⚠️ 이것은 `scripts/_keycloak_chamber_admin.py` 의 **알맹이**다. `scripts/` 는 패키지가 아니라
**휠이 나르지 못한다** — 이 레인을 핀으로 받는 소비자에게 그 파일은 오지 않는다.
그래서 로직은 여기 살고 `scripts/` 에는 재수출 shim 만 남는다.
⚠️ **이것은 CLI 가 아니라 라이브러리다** — `main()` 이 없고 `chamber_token_evidence`
가 `run_lifecycle_live` 를 가져다 쓴다. 그래서 이름에 `_cli` 를 붙이지 않는다. 껍데기는 양쪽 레포에
두되, 담긴 것이 그뿐이라 **갈라질 것이 없다.**
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable, Sequence
import urllib.error
import urllib.parse
import urllib.request


from fcc_test_platform.application.chamber_token_evidence import (  # noqa: E402
    CHAMBER_TOKEN_ACTIONS,
    REDACTED_SECRET_MARKER,
    resolve_lifecycle_actions,
)
from fcc_test_platform.application.chamber_token_provisioning import (  # noqa: E402
    CHAMBER_PERMISSION,
    build_chamber_client_representation,
    chamber_client_id,
)


class KeycloakAdminClient:
    """Narrow REST seam over the Keycloak admin API (one realm, one bearer).

    Methods return only what the lifecycle loop needs and NEVER surface a secret
    value. ``request`` is injectable purely so the transport can be exercised in
    isolation; the lifecycle tests instead substitute a fake of this whole class.
    """

    def __init__(
        self,
        base_url: str,
        realm: str,
        token: str,
        *,
        request: Callable | None = None,
    ) -> None:
        self._base = base_url.rstrip('/')
        self._realm = realm
        self._token = token
        self._request = request or _request

    def find_client_uuid(self, client_id: str) -> str | None:
        existing = self._request(
            'GET',
            f'{self._clients_url()}?clientId={urllib.parse.quote(client_id)}',
            headers=self._bearer(),
        )
        if existing:
            return existing[0]['id']
        return None

    def create_client(self, representation: dict) -> None:
        self._request('POST', self._clients_url(),
                      data=_json_body(representation),
                      headers={**self._bearer(), 'Content-Type': 'application/json'})

    def update_client(self, uuid: str, representation: dict) -> None:
        self._request('PUT', f'{self._clients_url()}/{uuid}',
                      data=_json_body(representation),
                      headers={**self._bearer(), 'Content-Type': 'application/json'})

    def rotate_secret(self, uuid: str) -> None:
        # Keycloak regenerates and RETURNS the new secret here; we discard it.
        # The node fetches its own secret out-of-band (secret store) — evidence
        # must never carry the value.
        self._request('POST', f'{self._clients_url()}/{uuid}/client-secret',
                      headers=self._bearer())

    def disable_client(self, uuid: str) -> None:
        self._request('PUT', f'{self._clients_url()}/{uuid}',
                      data=_json_body({'enabled': False}),
                      headers={**self._bearer(), 'Content-Type': 'application/json'})

    def _clients_url(self) -> str:
        return f'{self._base}/admin/realms/{urllib.parse.quote(self._realm)}/clients'

    def _bearer(self) -> dict:
        return {'Authorization': f'Bearer {self._token}'}


def run_lifecycle_live(
    *,
    base_url: str,
    admin: str,
    password: str,
    realm: str,
    chamber_ids: Sequence[str],
    actor: str,
    actions: Sequence[str] | None = None,
    evidence_id: str | None = None,
) -> dict:
    """Authenticate, then run the selected lifecycle actions against Keycloak.

    Thin IO wrapper: fetches an admin token and builds the real
    ``KeycloakAdminClient``, then delegates to the testable ``build_live_evidence``.
    """
    token = _admin_token(base_url, admin, password)
    client = KeycloakAdminClient(base_url, realm, token)
    return build_live_evidence(
        client=client, realm=realm, chamber_ids=chamber_ids, actor=actor,
        actions=actions, evidence_id=evidence_id,
    )


def build_live_evidence(
    *,
    client,
    realm: str,
    chamber_ids: Sequence[str],
    actor: str,
    actions: Sequence[str] | None = None,
    evidence_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    """Run the selected lifecycle actions via ``client`` and record honest results.

    ``client`` must satisfy the ``KeycloakAdminClient`` surface
    (``find_client_uuid`` / ``create_client`` / ``update_client`` /
    ``rotate_secret`` / ``disable_client``). Pure orchestration otherwise — no
    direct IO — so it is fully unit-testable with a fake client + injected clock.
    """
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    stamp = now.isoformat()
    actor_text = (actor or 'ops:redacted').strip()
    resolved = resolve_lifecycle_actions(actions)
    events: list[dict] = []
    for chamber_id in chamber_ids:
        cid = str(chamber_id).strip()
        client_id = chamber_client_id(cid)
        for action in resolved:
            events.append(_run_action(client, realm, action, cid, client_id, stamp, actor_text))
    return {
        'schema_version': 1,
        'evidence_id': (evidence_id or f'chamber-token-{stamp}').strip(),
        'collected_at': stamp,
        'mode': 'live',
        'keycloak_realm': realm,
        'provisioning_permission': CHAMBER_PERMISSION,
        'events': events,
    }


def _run_action(client, realm, action, cid, client_id, stamp, actor) -> dict:
    if action == 'binding_check':
        # No live node/platform probe env in the admin helper — record honestly.
        return _event(client_id, cid, 'binding_check', stamp, actor, 'planned', {
            'endpoint': 'POST /platform/chambers/heartbeat',
            'bound_chamber_id': cid,
            'expect_own_status': 200,
            'expect_other_status': 403,
            'note': ('run the heartbeat probe from the live node per runbook; '
                     'this admin helper has no node/platform probe env'),
        })
    if action == 'provision':
        return _safe(client_id, cid, 'provision', stamp, actor,
                     {'realm': realm}, lambda: _upsert_client(client, cid),
                     ok_note='confidential client created/updated with chamber_id claim '
                             f'and only {CHAMBER_PERMISSION}')
    if action == 'rotate':
        return _safe(client_id, cid, 'rotate', stamp, actor,
                     {'realm': realm, 'secret_returned': False},
                     lambda: _rotate_client(client, client_id),
                     ok_note='client secret regenerated (value discarded; never written)')
    if action == 'revoke':
        return _safe(client_id, cid, 'revoke', stamp, actor,
                     {'realm': realm}, lambda: _disable_client(client, client_id),
                     ok_note='confidential client disabled (enabled=false)')
    # Unreachable: resolve_lifecycle_actions validates the vocabulary.
    raise ValueError(f'unsupported action: {action}')


def _safe(client_id, cid, action, stamp, actor, probe_base, op, *, ok_note: str) -> dict:
    probe = dict(probe_base)
    try:
        op()
        result = 'ok'
        probe['note'] = ok_note
    except _ClientNotFound as exc:
        result = 'failed'
        probe['note'] = str(exc)
    except Exception as exc:  # noqa: BLE001 — record failure honestly
        result = 'failed'
        probe['note'] = f'admin REST error: {exc}'
    return _event(client_id, cid, action, stamp, actor, result, probe)


class _ClientNotFound(Exception):
    pass


def _upsert_client(client, chamber_id: str) -> None:
    representation = build_chamber_client_representation(chamber_id)
    # The admin API auto-generates a secret for confidential clients, so strip
    # the placeholder and never set nor read a raw value here.
    representation.pop('secret', None)
    uuid = client.find_client_uuid(representation['clientId'])
    if uuid:
        client.update_client(uuid, representation)
    else:
        client.create_client(representation)


def _rotate_client(client, client_id: str) -> None:
    uuid = client.find_client_uuid(client_id)
    if not uuid:
        raise _ClientNotFound(f'cannot rotate: client {client_id} does not exist')
    client.rotate_secret(uuid)


def _disable_client(client, client_id: str) -> None:
    uuid = client.find_client_uuid(client_id)
    if not uuid:
        raise _ClientNotFound(f'cannot revoke: client {client_id} does not exist')
    client.disable_client(uuid)


def _event(client_id, chamber_id, action, stamp, actor, result, probe) -> dict:
    return {
        'client_id': client_id,
        'chamber_id': chamber_id,
        'action': action,
        'timestamp': stamp,
        'actor': actor,
        'result': result,
        'secret': REDACTED_SECRET_MARKER,
        'verification_probe': probe,
    }


def _admin_token(base_url: str, admin: str, password: str) -> str:
    data = urllib.parse.urlencode({
        'grant_type': 'password',
        'client_id': 'admin-cli',
        'username': admin,
        'password': password,
    }).encode('utf-8')
    url = f'{base_url.rstrip("/")}/realms/master/protocol/openid-connect/token'
    payload = _request('POST', url, data=data,
                       headers={'Content-Type': 'application/x-www-form-urlencoded'})
    return payload['access_token']


def _json_body(payload: dict) -> bytes:
    return json.dumps(payload).encode('utf-8')


def _request(method: str, url: str, *, data=None, headers=None):
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 — admin REST
        body = response.read().decode('utf-8') or 'null'
    return json.loads(body)


# Backward-compatible alias — the previous provision-only entrypoint now maps to
# the full lifecycle with the default (all-action) selection.
def provision_chambers_live(
    *,
    base_url: str,
    admin: str,
    password: str,
    realm: str,
    chamber_ids: Sequence[str],
    actor: str,
    evidence_id: str | None = None,
) -> dict:
    return run_lifecycle_live(
        base_url=base_url, admin=admin, password=password, realm=realm,
        chamber_ids=chamber_ids, actor=actor, actions=CHAMBER_TOKEN_ACTIONS,
        evidence_id=evidence_id,
    )
