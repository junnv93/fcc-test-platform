"""등재된 provider 가 **자기 계약을 검사했다는 증거**를 냈는가 — 그리고 그것이 낡지 않았는가.

판정: FCC 모노레포 `.claude/exec-plans/active/2026-08-31-kc-provider-identity-결정문.md`
**§6.6** (2026-09-04). 운영자 판정 2026-08-31 「나」안 — *provider 가 자기 레포에서 자기
계약을 검사하고 중앙은 그 결과만 받는다* — 의 「결과」가 무엇이고 무엇이 red 인가를 정한다.

## 이 축이 존재하는 이유 — 셋째 질문이 핵심이었다

`fcc-test-contracts/docs/OPEN-QUESTIONS.md` §1 이 남긴 설계 셋 중 셋째:
*"결과가 없거나 낡았을 때 무엇이 red 가 되는가."* 없으면 **결과가 오지 않은 상태와 결과가
통과한 상태가 같은 초록**이고, 그 축은 *"아무도 내지 않는 숙제"* 가 된다.

그래서 이 파일은 **fail-closed** 다. 증거 없는 등재 provider 는 「미지」가 아니라
**부적합**이다.

## ⚠️ 낡음의 축은 `version` 이 아니다 — 실측이 배제했다

===========  =========  ============  ==============================
시점          version    operations    내용 지문(`provider` 제외)
===========  =========  ============  ==============================
2026-08-31    `1.0.0`        39         `382331e4…`
2026-09-04    `1.0.0`        40         `0c490f88…`
===========  =========  ============  ==============================

계약이 실제로 바뀌었는데 `version` 은 안 움직였다. 그 축에서 두 계약이 같은 값이므로,
version 에 묶인 증거는 *「당신이 서비스하는 계약에 대해 검사했다」* 와 *「그 사이 바뀐 계약에
대해 검사했다」* 를 **구별하지 못한다.**

## 검증은 **파생이지 일정이 아니다**

만료일도 cron 도 없다. 이 게이트가 돌 때마다 **SSOT 에서 digest 를 다시 계산**하므로,
계약이 바뀌는 순간 기존 증거 전부가 다음 실행에서 자동으로 낡는다. 만료일을 두면 그것은
digest 옆의 **두 번째 의견**이고, 계약이 일정 밖에서 바뀌는 날 둘이 어긋난다.

## ⚠️ 정규형을 여기서 다시 구현하지 마라

`contract_identity_digest` 는 **계약 레인이 소유한다**. 그 함수는 체커가 비교 전에 양쪽을
줄이는 것과 **같은 함수**(`contract_comparison_document`)를 쓴다. 여기서 「같은 방식으로」
digest 를 다시 뜨면 *「같은 계약인가」* 의 정의가 둘이 되고, 갈라지는 날 어느 쪽도 그것을
말해주지 않는다.
"""
import json
import sys
import unittest
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / 'src'))

from fcc_test_contracts.common.tree_artifacts import (  # noqa: E402
    DependencyTreeUnavailable,
    resolve_dependency_artifact,
    resolve_repo_artifact,
)
from fcc_test_contracts.headless.contract_identity import (  # noqa: E402
    contract_identity_digest,
)

REGISTRY_PATH = resolve_repo_artifact(__file__, 'docs/api/headless_provider_registry.json')
EVIDENCE_DIR = resolve_repo_artifact(__file__, 'config/provider_conformance_evidence')
SSOT_ARTIFACT = 'fcc_test_contracts/artifacts/headless_api_contract.v1.json'

#: ⚠️ **줄어드는 목록이다. 늘리지 마라.**
#:
#: 이 셋은 증거를 낼 수 **없다** — 계약 레인이 자기 SSOT 에서 찍어낸 것이고, 뒤의 둘은
#: `.example.json` 이다(`OPEN-QUESTIONS.md` §1: *"아티팩트가 SSOT 의 byte-copy 라
#: 구조적으로 통과할 수밖에 없다"*). 진짜 발행처가 없으므로 검사할 구현도 없다.
#:
#: 이것을 「예외」로 두는 것과 「없는 축」으로 두는 것은 다르다. 목록에 이름으로 적혀 있고
#: 아래 래칫이 그 크기를 지킨다 — 넷째가 추가되면 red 다.
GRANDFATHERED = frozenset({
    'fcc-unlicensed-conducted',   # SSOT 자신
    'fcc-mmwave-headless',        # .example.json
    'fcc-licensed-headless',      # .example.json
})


def _load_registry():
    return json.loads(REGISTRY_PATH.read_text(encoding='utf-8')).get('providers') or []


def _load_evidence(provider_id: str):
    path = EVIDENCE_DIR / f'{provider_id}.json'
    if not path.is_file():
        return None, path
    return json.loads(path.read_text(encoding='utf-8')), path


class TestConformanceEvidenceAxisCanRun(unittest.TestCase):
    """비-공허성 팔. **아래 판정들이 0회 돌면 그것은 통과가 아니다.**

    각 팔에 대해 `.claude/rules/check-axis-blindness.md` §비-공허성 팔이 성공을 금지하는
    경우가 묻는 것: *이 검사가 성공하면 이 팔이 red 가 되는가?* → **아니오.** 계약이 몇
    operation 이든 digest 는 계산되고, provider 가 몇이든 목록은 비지 않는다.
    """

    def test_ssot_digest_is_computable(self):
        try:
            resolved = resolve_dependency_artifact(SSOT_ARTIFACT)
        except DependencyTreeUnavailable as exc:
            self.fail(
                f'계약 레인이 SSOT 를 내놓지 못한다 — 낡음을 판정할 기준값이 없다.\n  {exc}\n'
                '⚠️ 이것은 provider 문제가 아니라 설치 형상 문제다.'
            )
        self.assertTrue(resolved.exists(), f'SSOT 아티팩트가 없다: {resolved}')
        digest = contract_identity_digest(json.loads(resolved.read_text(encoding='utf-8')))
        self.assertRegex(
            digest, r'^[0-9a-f]{64}$',
            '기준 digest 를 못 구했다 — 이 상태에서 「낡은 증거 0건」은 '
            '「낡음을 못 잰다」와 같은 값이다',
        )

    def test_registry_names_at_least_one_provider(self):
        self.assertGreater(
            len(_load_registry()), 0,
            f'{REGISTRY_PATH} 가 provider 를 하나도 등재하지 않았다 — '
            '증거 검사가 0회 돌면 그것은 통과가 아니다',
        )


class TestConformanceEvidence(unittest.TestCase):
    def setUp(self):
        self.providers = _load_registry()
        resolved = resolve_dependency_artifact(SSOT_ARTIFACT)
        self.ssot_digest = contract_identity_digest(
            json.loads(resolved.read_text(encoding='utf-8'))
        )

    def _checked(self):
        """증거를 내야 하는 provider 만. grandfather 는 이름으로 빠진다."""
        return [p for p in self.providers if p.get('provider_id') not in GRANDFATHERED]

    def test_evidence_missing(self):
        """**evidence missing** — 등재됐는데 문서가 없다.

        ⚠️ fail-closed. 없는 것은 「미지」가 아니라 「부적합」이다.
        """
        missing = [
            f"  {p.get('provider_id')!r}  →  {path}"
            for p in self._checked()
            for evidence, path in [_load_evidence(p.get('provider_id'))]
            if evidence is None
        ]
        self.assertEqual(
            missing, [],
            '등재된 provider 의 적합성 증거가 도착하지 않았다:\n' + '\n'.join(missing)
            + '\n\n⚠️ 증거를 손으로 지어내 초록으로 만들지 마라. 발행처가 자기 CI 에서 '
              '내는 것이다(판정문 §6.6). 아직 낼 수 없는 provider 라면 **등재를 되돌려라** '
              '— 등재는 ①~③ 이 끝난 뒤다.',
        )

    def test_evidence_stale(self):
        """**evidence stale** — 검사 대상이 지금 SSOT 가 아니다."""
        stale = []
        for provider in self._checked():
            evidence, path = _load_evidence(provider.get('provider_id'))
            if evidence is None:
                continue   # test_evidence_missing 이 이름 붙인다
            claimed = (evidence.get('contract_identity') or {}).get('digest')
            if claimed != self.ssot_digest:
                stale.append(
                    f"  {provider.get('provider_id')!r}\n"
                    f"    증거가 검사한 계약  {claimed}\n"
                    f"    지금의 SSOT        {self.ssot_digest}"
                )
        self.assertEqual(
            stale, [],
            '증거가 낡았다 — 그 사이 계약이 바뀌었다:\n' + '\n'.join(stale)
            + '\n\n발행처가 다시 검사하고 새 증거를 내야 한다. '
              '⚠️ digest 를 손으로 고쳐 맞추지 마라 — 그것은 검사를 끄는 것이다.',
        )

    def test_evidence_non_conformant(self):
        """**evidence non-conformant** — 증거가 스스로 부적합을 말하거나 앞뒤가 안 맞는다.

        `subject.digest == contract_identity.digest` 를 요구하는 근거: 체커는 비교 전에
        양쪽에서 `provider` 를 떼어내므로, 같은 family 에서 적합하다면 두 값이 같아야 한다.
        **이 등식이 중앙이 아티팩트를 갖지 않고도 판정할 수 있게 하는 지점이다.**
        """
        bad = []
        for provider in self._checked():
            evidence, path = _load_evidence(provider.get('provider_id'))
            if evidence is None:
                continue
            pid = provider.get('provider_id')
            result = evidence.get('result') or {}
            subject = (evidence.get('subject') or {}).get('digest')
            claimed = (evidence.get('contract_identity') or {}).get('digest')
            if result.get('compatible') is not True:
                bad.append(f"  {pid!r}: result.compatible={result.get('compatible')!r} "
                           f"issues={result.get('issues')!r}")
            elif subject != claimed:
                bad.append(
                    f"  {pid!r}: subject.digest 가 contract_identity.digest 와 다르다\n"
                    f"    subject           {subject}\n"
                    f"    contract_identity {claimed}"
                )
        self.assertEqual(
            bad, [], '증거가 적합을 증명하지 못한다:\n' + '\n'.join(bad))


class TestEvidenceOrphans(unittest.TestCase):
    """⚠️ 등재가 사라진 이름의 증거가 남아도 아무것도 말하지 않았다.

    실측 2026-09-04 — provider 레인이 *「지금 증거를 내면 (d) 가 되나」* 라고 물어와서
    답을 확인하다 드러났다. 답은 **(d) 가 아니라 무음**이었다: 위 검사들은
    **레지스트리를 돌면서 증거를 찾지** 그 반대가 아니므로, 등재되지 않은 이름의 증거
    파일은 **아무도 열지 않는다.**

    등재 전이라면 그것이 옳다(아직 admit 되지 않은 provider 다). 문제는 **등재가
    사라진 뒤**다 — 그때 증거는 *「검사받았다」* 고 말하는 낡은 기록인데 그것을 읽는
    축이 없다. `TestGrandfatherRatchet` 이 예외 목록에 대해 갖는 고아 검사를 증거
    파일은 갖고 있지 않았다. 같은 계급의 비대칭이다.
    """

    def test_every_evidence_document_names_a_registered_provider(self):
        if not EVIDENCE_DIR.is_dir():
            return   # 아직 아무도 증거를 내지 않았다 — 고아도 없다
        registered = {p.get('provider_id') for p in _load_registry()}
        orphans = sorted(
            path.name for path in EVIDENCE_DIR.glob('*.json')
            if path.stem not in registered
        )
        self.assertEqual(
            orphans, [],
            f'레지스트리에 없는 이름의 증거가 남아 있다: {orphans}\n'
            '⚠️ 등재를 되돌렸다면 증거도 함께 지워라. 남겨 두면 「검사받았다」고 '
            '말하는 낡은 기록이 아무도 읽지 않는 채로 산다 — 이 축이 끝내려던 '
            '침묵과 같은 모양이다.',
        )


class TestGrandfatherRatchet(unittest.TestCase):
    """⚠️ 예외 목록은 **줄어들기만** 해야 한다."""

    def test_the_list_does_not_grow(self):
        self.assertLessEqual(
            len(GRANDFATHERED), 3,
            'grandfather 목록이 자랐다 — 새 provider 는 예외가 아니라 증거를 내야 한다. '
            '판정문 §6.6.',
        )

    def test_the_list_does_not_keep_names_the_registry_dropped(self):
        """등재가 사라진 이름이 목록에 남으면 목록이 안 줄어든다.

        ⚠️ *이 검사가 성공하면 이 팔이 red 가 되는가?* → 아니오. 셋이 계속 등재돼 있는
        동안 초록이고, 하나가 빠지면 목록도 줄이라고 말한다.
        """
        registered = {p.get('provider_id') for p in _load_registry()}
        orphans = sorted(GRANDFATHERED - registered)
        self.assertEqual(
            orphans, [],
            f'레지스트리에 없는 이름이 grandfather 목록에 남아 있다: {orphans} — '
            '목록에서 지워라.',
        )


if __name__ == '__main__':
    unittest.main()
