"""운영 문서가 `PUBLIC_HOST`/LAN IP 를 «`hostname -I` 로» 판정하지 않는가.

■ 왜 이 검사가 있는가

`hostname -I` 는 중앙 PC 에서 **원리적으로 틀린 판정자**다. 뿌리와 처방의 SSOT 는
`docs/operations/fcc-central-pc-reboot-ops-guide.md` §1.1 이고, 요지는 두 기계의
**WSL 네트워킹 모드가 다르다**는 것이다 — 개발 PC 는 mirrored 라 LAN IP 가 나오고,
중앙 PC 는 NAT 라 `172.25.x` 와 도커 브리지만 나온다. **같은 명령이 두 기계에서
다른 것을 답하는데 문서는 한쪽만 봤다.**

■ 그런데 «한 곳만» 고치면 안 됐다 (2026-09-07 실측)

그 SSOT 가 이미 있는데도 같은 형태가 **세 문서에 더** 남아 있었다:

    central-pc-operational-validation-runbook.md   §S0 판정
    central-pc-fcc-platform-verification-guide.md  판단 기준 2곳
    fcc-central-pc-port-topology.md                중앙 PC 블록

**고쳐진 사실이 다른 문서로 전파되지 않는다.** 그것이 이 검사의 존재 이유다 —
산문은 한 곳을 고쳐도 나머지가 조용히 낡는다.

■ 무엇을 «파생»하는가

대상 파일 목록을 하드코딩하지 않는다. `docs/operations/` 를 훑어, `hostname -I` 가
**LAN/PUBLIC_HOST 판정 문맥 안에** 나타나는 자리를 찾는다. 새 문서가 같은 결함을
갖고 들어와도 걸린다.

■ ⚠️ 이 검사가 «못» 하는 것

`hostname -I` 자체를 금지하지 않는다. 그 명령이 **WSL 자신의 IP** 를 묻는 자리는
정당하다 — `fcc-central-pc-port-topology.md` 의 portproxy 대조가 그것이다.
가르는 것은 명령이 아니라 **그 옆에서 무엇을 판정하는가**이다.

⚠️ 그리고 이 축은 **완전히 파생할 수 없다.** 「Windows 호스트가 가진 IP」는 저장소
밖 런타임 사실이고, CI 리눅스 컨테이너에서 `hostname -I` 는 전혀 다른 답을 낸다.
그래서 이 봉인은 **문서가 무엇을 지시하는가**만 지킨다. 실측 증거는 여기 남긴다:

    이 기계(중앙 PC 급 WSL), 2026-09-07
      hostname -I            9개  ← WSL 내부 + docker 브리지가 섞임
      Windows 호스트 IPv4    7개
      교집합                 1개  ← 브라우저가 닿을 수 있는 것은 이것뿐

즉 그 검사는 **오답 여덟 개를 통과시킨다.**
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OPS = _REPO_ROOT / 'docs' / 'operations'
_SSOT = _OPS / 'fcc-central-pc-reboot-ops-guide.md'

#: 판정 문맥으로 세는 낱말. 이 중 하나가 `hostname -I` «가까이» 있으면 그 자리는
#: 「LAN 소속을 판정하는」 자리다.
_VERDICT_MARKERS = ('PUBLIC_HOST', 'CENTRAL_IP', '사내망', 'LAN')

#: 같은 «판정 블록» 으로 볼 거리(줄).
_WINDOW = 6

#: `hostname -I` 가 정당한 자리 — WSL 자신의 IP 를 묻는 경우.
_LEGITIMATE = ('현재 WSL IP', 'portproxy')


def _offending_lines(text: str) -> list[tuple[int, str]]:
    """지시하는 자리만 센다 — 경고하는 산문은 세지 않는다.

    ⚠️ 이 함수의 첫 판은 «문자열 존재»로 셌고, 그러자 SSOT 자신이 걸렸다.
    §1.1 은 `hostname -I` 를 **경고하려고** 언급하는데 문자열 검사는 「지시」와
    「경고」를 구별하지 못한다 — 산문을 코드로 세는 형태다.

    축을 바꿨다: **코드 펜스 안**의, **주석이 아닌** 줄만 «명령»으로 센다.
    그러면 경고 일곱 줄이 자동으로 빠지고, 남는 것이 실제 지시다.
    """
    lines = text.splitlines()
    in_fence = False
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('```'):
            in_fence = not in_fence
            continue
        if not in_fence or 'hostname -I' not in line:
            continue
        if stripped.startswith('#'):
            continue                      # 코드블록 «주석» 은 경고다
        window = '\n'.join(lines[max(0, i - _WINDOW):i + _WINDOW + 1])
        if any(ok in window for ok in _LEGITIMATE):
            continue
        if any(m in window for m in _VERDICT_MARKERS):
            hits.append((i + 1, stripped))
    return hits


class TestTheDerivationSourceIsReadable(unittest.TestCase):
    """비-공허성 — 볼 것이 없어서 통과하는 것과 구별한다."""

    def test_the_operations_directory_has_documents(self) -> None:
        docs = sorted(_OPS.glob('*.md'))
        self.assertGreaterEqual(len(docs), 5,
                                f'docs/operations 에 문서가 {len(docs)}개뿐이다 — '
                                '훑을 것이 없으면 이 검사는 항상 통과한다')

    def test_the_ssot_section_exists(self) -> None:
        """근거를 담은 절이 사라지면 이 검사의 «이유»가 사라진다."""
        self.assertTrue(_SSOT.is_file(), f'{_SSOT.name} 이 없다')
        self.assertIn('`hostname -I` 는 이 기계에서 **원리적으로** 틀린 판정자다',
                      _SSOT.read_text(encoding='utf-8'),
                      'SSOT 절이 사라졌다 — 그러면 다른 문서의 «가리킴»이 고아가 된다')


class TestNoOperationalDocJudgesLanIpWithHostnameDashI(unittest.TestCase):

    def test_every_operations_document_is_clean(self) -> None:
        offenders: list[str] = []
        scanned = 0
        for doc in sorted(_OPS.glob('*.md')):
            scanned += 1
            for lineno, line in _offending_lines(doc.read_text(encoding='utf-8')):
                offenders.append(f'{doc.name}:{lineno}  {line}')
        self.assertGreater(scanned, 0, '훑은 문서가 0개다')
        self.assertEqual(
            [], offenders,
            '운영 문서가 LAN/PUBLIC_HOST 를 `hostname -I` 로 판정한다 — '
            '중앙 PC 는 WSL NAT 라 그 목록에 LAN 주소가 없다. '
            'fcc-central-pc-reboot-ops-guide.md §1.1 을 가리켜라:\n  ' +
            '\n  '.join(offenders))


class TestTheScannerHasTeeth(unittest.TestCase):
    """주입 — 「아무것도 안 잡는 스캐너」와 구별한다."""

    def test_a_verdict_next_to_hostname_dash_i_is_caught(self) -> None:
        bad = '판정:\n```bash\nhostname -I    # PUBLIC_HOST 와 같아야 한다\n```\n'
        self.assertTrue(_offending_lines(bad),
                        '판정 문맥의 hostname -I 를 못 봤다')

    def test_a_legitimate_wsl_ip_lookup_is_not_caught(self) -> None:
        """과발화도 결함이다 — portproxy 대조는 정당하다."""
        ok = ("재부팅 점검:\n```bash\n"
              "hostname -I | awk '{print $1}'   # 현재 WSL IP\n"
              'powershell.exe -NoProfile -Command "netsh interface portproxy show all"\n```\n')
        self.assertEqual([], _offending_lines(ok),
                         'WSL 자신의 IP 를 묻는 자리를 막았다 — 과발화하면 꺼진다')

    def test_a_bare_mention_far_from_a_verdict_is_not_caught(self) -> None:
        far = '```bash\nPUBLIC_HOST 이야기\n' + '\n' * 20 + 'hostname -I\n```\n'
        self.assertEqual([], _offending_lines(far),
                         '창(window) 밖의 언급을 잡았다')

    def test_prose_that_warns_against_it_is_not_caught(self) -> None:
        """⚠️ 첫 판이 여기서 틀렸다 — SSOT 자신을 잡았다.

        경고하는 산문과 지시하는 명령을 구별하지 못하면, 이 검사는 **결함을
        고친 문서를 결함으로 읽는다.** 그러면 고친 사람이 검사를 끈다.
        """
        warning = ('### 1.1 `hostname -I` 는 원리적으로 틀린 판정자다\n'
                   '이 절은 오래 PUBLIC_HOST 를 `hostname -I` 로 적었다.\n')
        self.assertEqual([], _offending_lines(warning),
                         '경고하는 산문을 지시로 셌다 — 산문을 코드로 세는 형태다')

    def test_a_comment_inside_a_fence_is_not_caught(self) -> None:
        """코드블록 «주석» 도 경고다 — reboot-ops-guide:24 가 그 형태다."""
        commented = ('```bash\n'
                     '# ⚠️ PUBLIC_HOST 를 hostname -I 로 판정하지 마라\n'
                     'echo ok\n```\n')
        self.assertEqual([], _offending_lines(commented),
                         '주석으로 «경고»한 것을 지시로 셌다')


if __name__ == '__main__':
    unittest.main()
