"""배포판은 자기가 싣지 않는 것을 실행하라고 말하면 안 된다 (2026-09-06).

⚠️ `scripts/` 는 **휠에 실리지 않는다** — `[tool.setuptools.packages.find]` 가
`fcc_test_platform*` 만 잡고 `scripts/` 에는 `__init__.py` 가 의도적으로 없다. 그러므로
설치된 배포판 안의 어떤 문자열도 `scripts/<무엇>.py` 를 «실행 지시»로 내놓아서는 안 된다.

실측 2026-09-06 — 이 봉인이 생기기 전에 여섯 자리가 그러고 있었다:

    bench_project_result_selection_cli:727          영수증에 기록되는 재현 명령
    cross_session_result_selection_evidence_cli:367 동상
    keyset_cursor_live_proof_cli:50,52              docstring 의 운영자 절차
    cutover_workflow_hints:344                      운영자에게 «주는» suggested_command
    …그 밖 docstring

⚠️ **둘은 산문이 아니라 「값」이었다.** 영수증에 적히고 운영자가 그대로 쳐서 재현하는
문자열이고, `suggested_command` 는 도구가 다음 단계로 지시하는 값이다. 그런데 이
레인 안에서는 그 경로가 **실재**하므로(껍데기가 남아 있다) 어떤 시험도 이것을 못 잡는다 —
소비 레인이 설치본으로 그 값을 단언하고 나서야 드러났다.

■ 이 봉인은 **공집합형**이다 — ratchet 이던 기간과 그것이 끝난 자리를 적는다

처음(2026-09-06 오전)에는 ratchet 이었다. «재현 명령» 축 셋을 고치고 나서 남은 자리가
`cutover_workflow_hints` 의 `suggested_command` 표와 두 파일이었는데, 그것을 같이 옮기면
**이 레인의 시험 계약 하나가 통째로 바뀌기** 때문이다 — `command[0] == 'python'` 이고
`command[1]` 이 실재 파일이라는 계약을 갈라야 했다.

그 웨이브를 같은 날 돌렸다. 갈래는 실행 «기계»다 — 그 분류는 이미
`EVIDENCE_RUNS_ON` 에 있었고, 위반 10건이 중앙 단계 10개와 정확히 1:1 이었다:

    중앙 PC 단계 10  →  `[project.scripts]` 에 «선언된» 콘솔 명령 이름
    챔버 PC 단계 4   →  provider 저장소의 «경로» (그쪽 소유라 경로가 맞는 값)

⚠️ 그 과정에서 위 목록의 분류 하나가 틀렸음이 드러났다 — `central_db_live_proof_cli` 를
「docstring 의 산문」으로 적어 뒀는데 실제로는 **영수증의 `command` 필드에 기록되는 값**
이었다. 이미 고친 셋과 같은 계급이다. **예외 목록은 읽고 믿을 것이 아니라 열어 볼 것이다.**

그래서 기준선이 비었고, 이 검사는 이제 **전면 적용**된다. 다시 예외를 더하지 마라 —
이름 하나를 더하는 것은 「이 자리는 봐주기로 했다」는 선언이고, 그 순간 소비자의
기계에서 죽는 지시가 하나 산다.

⚠️ **공집합형은 말뭉치가 줄면 조용해진다** — 패키지 루트가 사라지거나 파일이 0개가 되면
「위반 0」과 「아무것도 안 봤다」가 같은 출력을 낸다. 그래서 `_declared_package_files` 가
루트마다 비지 않았음을 요구하고, 아래 `scanned > 0` 과
`test_the_detector_would_see_a_violation` 이 함께 선다.
"""
from __future__ import annotations

import pathlib
import re
import unittest

def _declared_package_files() -> list[pathlib.Path]:
    """선언된 패키지 아래의 모든 파이썬 파일 — 루트마다 «비지 않았음»을 함께 요구한다."""
    files: list[pathlib.Path] = []
    for root in _package_roots():
        found = sorted(root.rglob('*.py'))
        if not found:
            raise AssertionError(
                f'선언된 패키지 {root.name} 아래에 파이썬 파일이 없다 — 루트 하나가 통째로 '
                '비어도 «합계»만 보는 검사는 초록으로 지나간다(형제 세션 -91 실측: '
                '로컬 59 + platform 28 = 108 > 100 이라 커널 루트가 없어도 통과했다). '
                '루트별로 묻는다.'
            )
        files.extend(found)
    return files


def _package_roots() -> list[pathlib.Path]:
    """이 배포판이 «싣는다고 선언한» 최상위 패키지들.

    ⚠️ **루트를 적어 두지 않는다.** 형제 세션(-91)이 같은 날 같은 형태를 잡았다 —
    Port 인구조사가 「어떤 Port 가 있나」는 디렉터리 순회로 파생하면서 **「Port 가 어디
    사나」는 상수로 적어 두었고**, 그 디렉터리가 이사한 날 조사가 통째로 조용해졌다.
    같은 결함의 두 축을 한쪽만 고쳐 둔 자리다.

    여기서는 답이 이미 `[tool.setuptools.packages.find].include` 에 «선언»돼 있다 —
    휠이 무엇을 싣는지 정하는 바로 그 값이다. 그것을 읽으므로, 실리는 이름이 늘거나
    줄면 이 검사의 시야도 함께 움직인다.
    """
    import tomllib

    root = pathlib.Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    patterns = config['tool']['setuptools']['packages']['find']['include']
    # ⚠️ `__init__.py` 를 요구하지 **않는다.** 이 저장소의 `fcc_test_platform/` 에는
    #    그 파일이 없다(네임스페이스 패키지). 「패키지 = __init__.py 가 있는 것」은
    #    가정이고, 그 가정이 여기서 거짓이라 첫 판은 루트를 0개로 셌다 — 루트를 적어
    #    두는 것을 고치면서 그 «판정 방법»을 또 가정한 것이다. 실리는 것은 setuptools 가
    #    정하므로 디렉터리인지만 묻는다.
    roots = [
        candidate
        for pattern in patterns
        for candidate in sorted(root.glob(pattern.rstrip('*')))
        if candidate.is_dir()
    ]
    if not roots:
        raise AssertionError(
            f'`packages.find.include` = {patterns!r} 가 이 트리에서 아무 패키지도 '
            '가리키지 않는다 — 이 검사가 공집합을 훑게 된다.'
        )
    return roots

#: 「실행 지시」로 읽히는 형태만 본다 — 단순 언급(주석에서 옛 이름을 회고하는 것)은
#: 사실의 기록이므로 금지하지 않는다.
_RUN_FORMS = (
    re.compile(r'\bpython3?\s+scripts/([A-Za-z0-9_]+)\.py'),
    re.compile(r"""['"]scripts/([A-Za-z0-9_]+)\.py['"]"""),
)


def _platform_owned_stems() -> set[str]:
    """이 배포판이 «자기 도구»로 싣는 이름들.

    ⚠️ **provider 소유 도구는 금지 대상이 아니다.** `cutover_workflow_hints` 의 힌트 표는
    운영자가 *provider 저장소에서* 돌릴 것도 함께 지시하고, 그 자리에서는
    `scripts/headless_hardware_smoke_evidence.py` 가 정확한 값이다. 금지해야 할 것은
    **이 배포판이 자기가 싣지 않는 «자기 도구»** 를 가리키는 자리뿐이다.

    소유 판정은 산문이 아니라 `[project.scripts]` 에 묻는다 — 이름 규칙이
    `fcc-platform-` + 모듈 이름에서 `_cli` 를 떼고 `_`→`-` 이므로 역으로 풀 수 있다.
    """
    import tomllib

    pyproject = pathlib.Path(__file__).resolve().parents[1] / 'pyproject.toml'
    table = tomllib.loads(pyproject.read_text(encoding='utf-8'))['project']['scripts']
    stems: set[str] = set()
    for target in table.values():
        module = target.split(':')[0].rsplit('.', 1)[-1]
        stems.add(module.removesuffix('_cli'))
    return stems


class TestNoRunnableScriptsPathEscapesIntoTheDistribution(unittest.TestCase):
    def test_no_module_names_a_scripts_path_as_something_to_run(self):
        owned = _platform_owned_stems()
        self.assertTrue(owned, '`[project.scripts]` 가 비었다 — 이 검사가 공허해진다')
        offenders: list[str] = []
        scanned = 0
        for path in _declared_package_files():
            scanned += 1
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                for form in _RUN_FORMS:
                    hit = form.search(line)
                    if not hit:
                        continue
                    stem = hit.group(1)
                    # 이름 규칙은 접두사가 붙거나(`platform_db_migrate` → `db_migrate`)
                    # 안 붙는다(`check_auth_mode_pairing`). 둘 다 본다.
                    if stem in owned or stem.removeprefix('platform_') in owned:
                        offenders.append(f'{path.name}:{number}: {line.strip()[:100]}')
                    break
        self.assertGreater(
            scanned, 0,
            f'{[str(r) for r in _package_roots()]} 아래에 파이썬 파일이 없다 — 이 검사는 공집합을 훑고 '
            '«참이지만 아무것도 재지 않는 참»이 된다.',
        )
        # ⚠️ **기준선이 비었다 — 이 봉인은 이제 공집합 등호다** (2026-09-06 상환).
        #    ratchet 이던 동안 남아 있던 셋을 전부 처분했으므로 예외가 없다.
        #    다시 채우지 마라: 여기 이름 하나를 더하는 것은 「이 자리는 봐주기로
        #    했다」는 선언이고, 그 순간 소비자의 기계에서 죽는 지시가 하나 산다.
        self.assertEqual(
            [], offenders,
            '배포판이 자기가 싣지 않는 `scripts/…` 를 실행 지시로 내놓는다. '
            f'명령 이름(`fcc-platform-…`)을 써라: {offenders}',
        )

    def test_the_detector_would_see_a_violation(self):
        """비공허성 — 탐지기가 실제 형태를 잡고, provider 소유는 놓아 주는지."""
        owned = _platform_owned_stems()
        # ⚠️ 이 두 샘플은 «위반의 모양»이므로 고쳐지면 안 된다. 조각으로 조립해 두는 것은
        #    이 저장소를 훑는 일괄 치환이 여기까지 들어와 «탐지기의 표본»을 지워 버리는
        #    것을 막기 위해서다 — 실측 2026-09-06: 실제로 한 번 그렇게 지워졌다.
        _bad = 'scripts/' + 'platform_db_migrate' + '.py'
        _bad2 = 'scripts/' + 'check_auth_mode_pairing' + '.py'
        for sample in (f"        return shlex.join(['python', '{_bad}'])",
                       f'   python3 {_bad2} --env-file x'):
            hit = next((f.search(sample) for f in _RUN_FORMS if f.search(sample)), None)
            self.assertIsNotNone(hit, f'탐지기가 이 형태를 놓친다: {sample!r}')
            stem = hit.group(1)
            self.assertTrue(stem in owned or stem.removeprefix('platform_') in owned)

        provider = "        'scripts/headless_hardware_smoke_evidence.py',"
        hit = next((f.search(provider) for f in _RUN_FORMS if f.search(provider)), None)
        self.assertIsNotNone(hit)
        stem = hit.group(1)
        self.assertFalse(
            stem in owned or stem.removeprefix('platform_') in owned,
            'provider 소유 도구를 이 배포판의 것으로 오인한다 — 그러면 이 봉인이 '
            '«옳은 힌트»를 위반으로 신고한다',
        )


if __name__ == '__main__':
    unittest.main()
