# ⚠️ 2026-08-31: 이 파일은 모노레포 `tests/test_oidc_principal_resolver.py` 에서 갈라져 왔다. 남은 것은
#    소비 대상이 이 레포에 있는 단위(TestWsBearerSubprotocolCrossLanguageParity)뿐이고,
#    나머지 형제 검사와 그것들만 쓰던 import 는 저쪽에 남았다.
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
# ⚠️ 이 모듈은 계약 패키지로 이사했다 — 경로가 아니라 임포트 이름으로 묻는다.
from _moved_module_source import moved_module_source  # noqa: E402
import re
import types
import unittest
from pathlib import Path

from fcc_test_contracts.common.ws_subprotocol_auth import (
    WS_BEARER_SUBPROTOCOL,
    bearer_credential_request,
    encode_bearer_subprotocols,
    parse_bearer_offer,
)
from fcc_test_contracts.common.oidc_principal_resolver import (
    BearerTokenPrincipalResolver,
    OidcJwtConfig,
)

# ⚠️ `resolve_repo_artifact` 는 모노레포의 **재배치 레이어**를 해소하는 헬퍼다.
#    이 레포에는 그 레이어가 없고 경로가 그대로이므로, 같은 이름으로 단순
#    루트 결합을 쓴다. 모노레포 판본을 부르면 `RelocationAmbiguity` 로 죽는다.
def resolve_repo_artifact(_anchor, relative):  # noqa: F811
    return _Path(__file__).resolve().parent.parent / relative











def _config() -> OidcJwtConfig:
    return OidcJwtConfig(
        issuer='https://login.example.com/tenant',
        audience='fcc-platform',
        jwks_uri='https://login.example.com/tenant/keys',
    )


def _fake_jwt(claims_or_error):
    module = types.SimpleNamespace()
    module.decode_kwargs = {}

    def get_unverified_header(token):
        return {'kid': 'key-1'}

    def decode(token, key, algorithms, audience, issuer, leeway=0):
        module.decode_kwargs = {
            'token': token,
            'key': key,
            'algorithms': algorithms,
            'audience': audience,
            'issuer': issuer,
            'leeway': leeway,
        }
        if isinstance(claims_or_error, Exception):
            raise claims_or_error
        return claims_or_error

    module.get_unverified_header = get_unverified_header
    module.decode = decode
    return module


# ════════════════════════════════════════════════════════════════════════════
# WS bearer subprotocol auth SSOT (W3-4, 2026-08-01) — moved here (rather than
# tests/test_ws_state_lifecycle_p1_3.py) so these pure/text-scan tests land in
# the fast ``-m invariant`` CI lane via this file's ``oidc`` filename token
# (``conftest.py::_INVARIANT_FILENAME_TOKENS`` has no token matching
# ``ws_state_lifecycle``). Only the FastAPI-runtime crash-path-closure test
# stays in the WS lifecycle file, next to the lifecycle tests it exercises.
# ════════════════════════════════════════════════════════════════════════════












class TestWsBearerSubprotocolCrossLanguageParity(unittest.TestCase):
    """M3 — ``apps/web/src/api/ws-bearer.ts`` must mirror the Python SSOT
    constant + encoding rule byte-for-byte. Reuses the established
    ``tests/support/parity.py`` helper (M1 §3 — no new convention)."""

    _TS_PATH = Path(__file__).parent.parent / 'apps' / 'web' / 'src' / 'api' / 'ws-bearer.ts'
    _PY_PATH = (

        moved_module_source('fcc_test_contracts.common.ws_subprotocol_auth')
    )

    def test_sentinel_constant_matches_python_ssot(self):
        from support.parity import parse_ts_export_const_strings

        self.assertTrue(self._TS_PATH.is_file(), 'ws-bearer.ts must exist')
        ts_source = self._TS_PATH.read_text(encoding='utf-8')
        ts_consts = parse_ts_export_const_strings(
            ts_source, name_pattern=r'WS_BEARER_SUBPROTOCOL',
        )
        self.assertIn(
            'WS_BEARER_SUBPROTOCOL', ts_consts,
            'ws-bearer.ts must declare export const WS_BEARER_SUBPROTOCOL',
        )
        self.assertEqual(ts_consts['WS_BEARER_SUBPROTOCOL'], WS_BEARER_SUBPROTOCOL)

    def test_encoding_rule_matches_python_ssot(self):
        """M5 axis 5 red→green: changing only the frontend constant/order
        (without touching the Python side) must fail this test."""
        from support.parity import strip_ts_comments

        ts_source = strip_ts_comments(self._TS_PATH.read_text(encoding='utf-8'))
        py_source = self._PY_PATH.read_text(encoding='utf-8')

        # Rule 1 — blank/null token encodes to empty (TS `[]` / Python `()`).
        self.assertIn('return []', ts_source)
        self.assertIn('return ()', py_source)
        # Rule 2 — non-blank token encodes sentinel-first, token-second.
        self.assertIn('return [WS_BEARER_SUBPROTOCOL, normalized]', ts_source)
        self.assertIn('return (WS_BEARER_SUBPROTOCOL, normalized)', py_source)

    def test_normal_form_unrelated_ts_file_is_not_silently_satisfying(self):
        """M5 normal-form control — the parity assertion targets
        ws-bearer.ts specifically; a file that merely imports (not
        re-declares) the constant must not silently pass the same check."""
        from support.parity import parse_ts_export_const_strings

        chamber_events = (
            Path(__file__).parent.parent / 'apps' / 'web' / 'src' / 'api' / 'chamber-events.ts'
        ).read_text(encoding='utf-8')
        self.assertNotIn(
            'WS_BEARER_SUBPROTOCOL',
            parse_ts_export_const_strings(chamber_events, name_pattern=r'WS_BEARER_SUBPROTOCOL'),
        )




if __name__ == '__main__':
    unittest.main()
