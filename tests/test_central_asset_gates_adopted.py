"""중앙 자산을 읽던 게이트 — provider 저장소에서 **함수 단위로** 이관 (2026-09-03).

**왜 파일째가 아니라 함수 단위인가.** 그쪽 요청의 핵심이 이것이다: 예를 들어
`test_proxy_trust_policy.py` 는 78 테스트인데 중앙 자산을 읽는 것은 **2개**다. 파일째
가져가면 provider 의 프록시 코드 테스트 76개가 그 저장소에서 사라진다.

**무엇이 중앙 자산인가.** 런북 · `docker-compose.central.yml` · `fcc-dev-realm.json` ·
`central.env.example` — 전부 2026-09-03 에 이 레인으로 왔다. 그것을 읽는 게이트는
그 자산과 함께 와야 한다. 「고아가 된다」가 아니라 **한 저장소이던 시절의 잔류**다.

⚠️ **여섯 중 셋만 여기서 돈다.** 나머지는 provider 코드나 그쪽 워크플로 파일을 필요로
한다. 실측하고 그 사실을 이름으로 적는다 — 「가져왔다」와 「가져와서 돈다」는 다른 사실이고,
안 도는 것을 가져오면 그쪽에서는 지워지고 여기서는 skip 이라 **아무 데서도 안 돈다.**

    돈다        compose 의 FORWARDED_ALLOW_IPS 값 · 보관 축출 문구 · realm 의 sample 토큰
    절반        런북의 진단 명령 (문자열은 여기, 그것을 내는 코드는 provider 쪽)
    못 돈다     rbac 권한 우주 (headless 권한 집합이 필요) · 백업 리허설 워크플로 (파일이 여기 없다)
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNBOOK = (
    _REPO_ROOT / 'docs' / 'operations'
    / 'central-pc-operational-validation-runbook.md'
)
_COMPOSE = _REPO_ROOT / 'infra' / 'docker-compose.central.yml'
_REALM = _REPO_ROOT / 'infra' / 'keycloak' / 'fcc-dev-realm.json'


class TestTheAssetsAreHere(unittest.TestCase):
    """⚠️ 비-공허성 — 자산이 없으면 아래 전부가 「비교할 것이 없어서」 통과한다."""

    def test_the_central_assets_this_file_reads_exist(self):
        for path in (_RUNBOOK, _COMPOSE, _REALM):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), f'{path} 가 없다')


class TestProxyTrustValueTheComposeActuallySets(unittest.TestCase):
    """compose 가 **실제로 싣는** 값이 정책에 받아들여지는가.

    이 파일의 상수를 쓰면 「정책이 맞다」만 증명한다 — 배포가 그 값을 쓴다는 보장이
    없다. compose 에서 **읽어** 같은 판정을 돌리므로, 배포가 대역으로 바뀌면 red 다.

    판정 코드(`proxy_trust_policy`)는 계약 레인에 있어 두 저장소가 같은 것을 쓴다.
    """

    def test_the_value_the_shipped_compose_actually_sets_is_accepted(self):
        from fcc_test_contracts.common import proxy_trust_policy

        compose = _COMPOSE.read_text(encoding='utf-8')
        values = set(re.findall(
            r'^\s*FORWARDED_ALLOW_IPS:\s*\$\{[A-Z_]+:-([^}]*)\}\s*$',
            compose, re.MULTILINE,
        ))
        self.assertTrue(
            values,
            'compose 에서 FORWARDED_ALLOW_IPS 기본값을 못 읽었다 — 그러면 이 검사는 '
            '아무것도 판정하지 않는다.',
        )
        for value in sorted(values):
            with self.subTest(value=value):
                self.assertEqual((), tuple(proxy_trust_policy.trust_defects(value)))
                self.assertEqual(
                    proxy_trust_policy.PEER_AXIS_PER_SOURCE,
                    proxy_trust_policy.peer_axis_mode(value),
                )


class TestTheRunbookNamesPhrasesTheCodeEmits(unittest.TestCase):
    """런북이 grep 하라고 적은 문구가 **코드의 상수와 같은가.**

    ⚠️ provider 저장소가 이 축을 값비싸게 배웠다 — 한 웨이브가 문구를 다시 쓰면서
    런북을 고치지 않아, 런북이 지목한 두 문자열이 코드에 **0건**이었고 의미도
    **반대**였다. 그 런북을 따른 운영자는 정상 포화에 무고한 시험원의 비밀번호를
    바꾸게 된다.

    그래서 판정은 산문 대조가 아니라 **같은 상수**다 — 개명이 둘을 함께 움직인다.
    이 상수는 이 레인이 소유한다(`fcc_test_platform.application.local_auth_service`).
    """

    def test_the_runbook_names_the_phrases_the_code_actually_emits(self):
        from fcc_test_platform.application.local_auth_service import (
            CUSTODY_EVICTION_PHRASE,
            DEGENERATE_EVICTION_PHRASE,
        )

        runbook = _RUNBOOK.read_text(encoding='utf-8')
        for phrase in (CUSTODY_EVICTION_PHRASE, DEGENERATE_EVICTION_PHRASE):
            with self.subTest(phrase=phrase):
                self.assertIn(
                    phrase, runbook,
                    f'런북이 코드가 내지 않는 문구를 지목한다: {phrase!r}',
                )

    def test_the_runbook_still_carries_the_proxy_trust_diagnostic(self):
        """⚠️ **이 검사는 절반이다 — 그 사실을 숨기지 않는다.**

        provider 저장소의 원본은 두 가지를 함께 봤다: 런북이 그 문자열을 적는가, 그리고
        **부팅이 실제로 그 문자열을 내는가.** 뒤쪽은 provider 코드를 부팅해야 하고
        그 코드는 여기 없다. 절반만 확인하면서 전부를 확인한 척하면, 문자열은 남아
        있는데 코드가 그것을 더는 내지 않는 상태가 초록으로 지나간다.

        그러므로 **나머지 절반은 provider 저장소에 남아야 한다.** 여기서 지우지 말라고
        그쪽에 알렸고, 이 docstring 이 그 분담을 기록한다.
        """
        self.assertIn(
            "grep -i 'proxy trust", _RUNBOOK.read_text(encoding='utf-8'),
            '런북의 프록시 신뢰 진단 명령이 사라졌다',
        )


class TestDevRealmSamplePermissionTokens(unittest.TestCase):
    """realm 이 노출하는 sample 권한 토큰이 정확히 둘인가.

    realm 파일이 이 레인 소유이므로 이 판정도 여기가 자리다.
    """

    def test_dev_realm_exposes_only_the_sample_write_and_hard_delete_tokens(self):
        realm = json.loads(_REALM.read_text(encoding='utf-8'))
        permissions = {
            value
            for group in realm.get('groups', [])
            for value in (group.get('attributes') or {}).get('permissions', [])
        }
        self.assertTrue(
            permissions,
            'realm 의 그룹에서 permissions 를 하나도 못 읽었다 — 아래 비교가 공허해진다.',
        )
        sample_permissions = {
            value for value in permissions if value.startswith('platform:sample-')
        }
        self.assertEqual(
            {'platform:sample-write', 'platform:sample-hard-delete'},
            sample_permissions,
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
