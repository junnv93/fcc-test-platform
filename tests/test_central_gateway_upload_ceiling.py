# -*- coding: utf-8 -*-
"""게이트웨이는 자기가 감싸는 정책보다 **엄격하면 안 된다**.

실측된 결함 (2026-09-01) — ``infra/central/nginx.conf`` 에
``client_max_body_size`` 가 **한 줄도 없었고**, 그래서 nginx 기본값 ``1m`` 이
적용됐다. 업로드 상한의 SSOT 는 ``DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES``(64MB)인데
게이트웨이가 **64배 엄격**했다. 5.4MB 워크북으로 시험 계획을 들여오려 하자
브라우저가 받은 것은 우리 화면의 문장이 아니라 ``<h1>413 Request Entity Too
Large</h1>`` 라는 **날것 HTML** 이었다.

⚠️ 실해는 크기가 아니라 **누가 답하는가**다. 앱의 거부는 타입이 있고 화면이
그것을 문장으로 렌더한다. 게이트웨이가 먼저 자르면 시험원은 어느 파일이 왜
거부됐는지 알 수 없다.

이 저장소가 **배포되는 web 이미지를 빌드한다** (모노레포는 2026-09-01 에 이
자산의 소유를 여기로 넘겼다). 그러므로 이 파일이 읽는 ``nginx.conf`` 가
**실제로 도는 게이트웨이**다.

왜 봉인의 축이 모노레포 판과 다른가
------------------------------------
모노레포 판은 ``samples/test-plans/*.xlsx`` 의 실제 파일 크기를 세 번째 축으로
썼다. **이 상자에는 그 파일이 없다.** 그대로 옮기면 그 검사는 영구 ``skip`` 이
되고, 조용한 skip 은 *"어디서도 검증되지 않으면서 검증되는 것처럼 읽히는"* 상태다
— 이 저장소가 2026-09-01 에 이름 붙여 거부한 바로 그 형태.

그래서 여기서는 **이 상자가 실제로 답할 수 있는 축**으로 파생한다: multipart 를
나르는 operation 을 **계약 아티팩트에서 열거**하고, 그 경로를 nginx 의 location
매칭 규칙으로 풀어 **유효 천장**을 계산한다. 새 multipart 라우트가 생겼는데
게이트웨이를 잊으면 그 순간 red 다 — 사람이 목록을 갱신할 필요가 없다.

⚠️ 파생 봉인의 고질병은 **공허한 통과**다. 열거가 0건이면 «모든 라우트가 천장을
만족한다» 는 참이 되고 검사는 아무것도 재지 않은 채 초록이다. 그래서
:meth:`test_the_multipart_census_is_not_empty` 가 census 자체를 단언한다.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Optional

from fcc_test_kernel.domain.services.workbook_upload_policy import (
    DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
NGINX_CONF = PROJECT_ROOT / 'infra' / 'central' / 'nginx.conf'
API_ARTIFACT_DIR = PROJECT_ROOT / 'docs' / 'api'

_SIZE_UNITS = {'k': 1024, 'm': 1024 ** 2, 'g': 1024 ** 3}
_DIRECTIVE = re.compile(r'client_max_body_size\s+([0-9]+[kKmMgG]?)\s*;')
#: ``location [modifier] uri {`` — the modifier is optional and may be ``=``,
#: ``~``, ``~*``, ``^~``. Only prefix locations (no modifier, or ``=``/``^~``)
#: are resolvable without running nginx, which is all this census needs.
_LOCATION = re.compile(r'location\s+(?:(=|\^~|~\*|~)\s+)?(\S+)\s*\{')


def _to_bytes(token: str) -> int:
    token = token.strip().lower()
    if token[-1] in _SIZE_UNITS:
        return int(token[:-1]) * _SIZE_UNITS[token[-1]]
    return int(token)


class _GatewayConfig:
    """The server block's ceilings, keyed by the location that declares them.

    Deliberately a small hand parser rather than a dependency: the file is a
    fixed shape this repository owns, and a parser that silently accepted an
    unparsable file would be the quiet-green failure this seal exists to stop.
    Every ``client_max_body_size`` in the file is accounted for — the parser
    asserts that below, so a directive in a block it does not model cannot be
    dropped on the floor.
    """

    def __init__(self, text: str) -> None:
        self.server_default: Optional[int] = None
        self.by_location: dict[str, int] = {}
        self._parse(text)

    def _parse(self, text: str) -> None:
        depth = 0
        server_depth: Optional[int] = None
        location_stack: list[tuple[int, str, str]] = []
        for raw_line in text.splitlines():
            line = raw_line.split('#', 1)[0]
            location_match = _LOCATION.search(line)
            size_match = _DIRECTIVE.search(line)
            if size_match is not None:
                value = _to_bytes(size_match.group(1))
                if location_stack:
                    self.by_location[location_stack[-1][2]] = value
                elif server_depth is not None:
                    self.server_default = value
            opens = line.count('{')
            closes = line.count('}')
            if location_match is not None and opens:
                location_stack.append(
                    (depth + 1, location_match.group(1) or '', location_match.group(2)),
                )
            elif re.search(r'\bserver\s*\{', line) and server_depth is None:
                server_depth = depth + 1
            depth += opens - closes
            while location_stack and location_stack[-1][0] > depth:
                location_stack.pop()
            if server_depth is not None and server_depth > depth:
                server_depth = None

    def effective_ceiling_for(self, path: str) -> Optional[int]:
        """Ceiling nginx would apply to ``path``.

        Prefix matching, longest wins — the rule nginx uses for the locations
        this file declares. An exact (``=``) location is not a prefix and is
        therefore matched only on equality.
        """
        best_uri = ''
        for uri in self.by_location:
            if path.startswith(uri) and len(uri) > len(best_uri):
                best_uri = uri
        if best_uri:
            return self.by_location[best_uri]
        return self.server_default


def _multipart_operations() -> list[tuple[str, str, str]]:
    """``(artifact, path, operation_id)`` for every multipart operation.

    Read from the contract artifacts rather than a hand list so that a new
    upload route cannot be added without this census seeing it.
    """
    found: list[tuple[str, str, str]] = []
    for artifact in sorted(API_ARTIFACT_DIR.glob('*.openapi.json')):
        document = json.loads(artifact.read_text(encoding='utf-8'))
        for path, operations in (document.get('paths') or {}).items():
            for operation in operations.values():
                if not isinstance(operation, dict):
                    continue
                content = (operation.get('requestBody') or {}).get('content') or {}
                if 'multipart/form-data' in content:
                    found.append(
                        (artifact.name, path, operation.get('operationId') or '?'),
                    )
    return found


class TestGatewayUploadCeiling(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = NGINX_CONF.read_text(encoding='utf-8')
        cls.config = _GatewayConfig(cls.text)

    def test_the_gateway_declares_a_ceiling_at_all(self):
        """미선언은 「제한 없음」이 아니라 **nginx 기본값 1m** 이다.

        그 사실을 모르면 「설정하지 않았으니 통과하겠지」로 읽힌다 — 이 결함이
        정확히 그렇게 살아 있었다.
        """
        self.assertIsNotNone(
            self.config.server_default,
            'nginx.conf 의 server 블록이 client_max_body_size 를 선언하지 않으면 '
            'nginx 기본값 1m 이 적용되어 실 워크북(5~6MB) 업로드가 전부 413 이다.',
        )

    def test_the_parser_accounts_for_every_declaration(self):
        """파서가 못 읽은 선언이 있으면 이 봉인의 판정은 신뢰할 수 없다."""
        declared = len(_DIRECTIVE.findall(self.text))
        modelled = len(self.config.by_location) + (
            1 if self.config.server_default is not None else 0
        )
        self.assertEqual(
            declared, modelled,
            f'nginx.conf 에 client_max_body_size 선언이 {declared}개인데 파서는 '
            f'{modelled}개만 자리에 넣었다 — 모델하지 않은 블록의 선언은 판정에서 '
            f'조용히 빠진다.',
        )

    def test_the_multipart_census_is_not_empty(self):
        """열거가 0건이면 아래 검사는 아무것도 재지 않고 초록이다."""
        census = _multipart_operations()
        self.assertTrue(
            census,
            f'{API_ARTIFACT_DIR} 에서 multipart operation 을 하나도 찾지 못했다 — '
            f'아티팩트 경로나 스키마 모양이 바뀌었다면 아래 천장 검사는 공허하게 '
            f'통과한다.',
        )

    def test_every_gateway_proxied_multipart_route_reaches_the_policy_ceiling(self):
        """게이트웨이를 지나는 업로드 라우트의 천장 = 앱 정책의 천장.

        게이트웨이가 통과시키지 않는 경로(``/session/*`` — 노드는 챔버 PC 에서
        직접 응답하고 중앙 게이트웨이를 지나지 않는다)는 **이 게이트웨이의
        판정 대상이 아니다.** 그것을 여기서 단언하면 존재하지 않는 location 에
        대해 영원히 red 이거나, 반대로 「없으니 통과」로 접히면 진짜 누락과
        구분이 안 된다. 그래서 어느 쪽인지 **이름으로 나눠 적는다.**
        """
        ceiling = int(DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES)
        proxied: list[tuple[str, int]] = []
        not_proxied: list[str] = []
        for _artifact, path, operation_id in _multipart_operations():
            effective = self.config.effective_ceiling_for(path)
            declares_location = any(
                path.startswith(uri) for uri in self.config.by_location
            )
            if declares_location:
                proxied.append((f'{operation_id} {path}', effective))
            else:
                not_proxied.append(f'{operation_id} {path}')

        self.assertTrue(
            proxied,
            f'게이트웨이가 감싸는 multipart 라우트가 0건이다 — 게이트웨이를 지나지 '
            f'않는다고 기록된 것: {not_proxied}. 하나도 감싸지 않는다면 이 봉인은 '
            f'재는 것이 없다.',
        )
        for label, effective in proxied:
            self.assertEqual(
                effective, ceiling,
                f'{label} 의 유효 천장이 {effective} bytes 인데 앱 정책 SSOT 는 '
                f'{ceiling} bytes 다. 게이트웨이가 더 엄격하면 앱의 타입 있는 거부가 '
                f'말할 기회를 얻지 못하고(시험원은 날것 413 HTML 을 본다), 더 '
                f'느슨하면 방어층이 사라진다.',
            )


if __name__ == '__main__':
    unittest.main()
