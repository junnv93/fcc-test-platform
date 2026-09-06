"""push 된 **모든 커밋이 자기 검증 기록을 갖는가** (2026-09-06).

## Why — 무엇이 실제로 일어났는가

`concurrency` 블록 둘이 각각 이렇게 적고 있었다:

    # main 런을 취소하면 그 커밋에 검증 기록이 남지 않는다
    # never abandon a main/scheduled run half-verified — a cancelled
    # post-merge run leaves that commit with no verification record.

**둘 다 그 의도를 달성하지 못했다.** 2026-09-06 에 한 세션이 PR 다섯을 5~7초 간격으로
머지하자 main 커밋 넷이 `cancelled` 로 끝났다:

    99f5d61  success     ← 첫 런: 자리를 잡고 달린다
    8c33bc3  cancelled   ← 대기하다 다음 것에 밀렸다
    4ba0585  cancelled
    7827b50  cancelled
    64abb7b  cancelled
    ae043a5  success     ← 마지막 대기자

원인은 `cancel-in-progress: false` 가 **대기 중인 런을 지키지 않는다**는 데 있다.
그 값은 「이미 «달리는» 런을 취소하지 마라」만 말한다. 한 그룹에서 대기할 수 있는
런은 하나뿐이고, 새 런이 그룹에 들어오면 대기하던 것이 취소된다.

⚠️ **이것이 왜 조용했는가.** 머지 둘까지는 증상이 없다 — 대기 자리를 다투는 셋째가
없기 때문이다. 같은 날 10초 간격의 머지 쌍(`4189dca`·`28b2bc4`)은 둘 다 `success` 다.
즉 「빠르게 머지하면 깨진다」가 아니라 **「셋 이상을 연달아 머지하면 가운데가
사라진다」**이고, 평소의 한두 개짜리 머지로는 관측되지 않는다.

⚠️ 그리고 **`cancelled` 는 빨간 X 가 아니다.** 다운스트림에서 「돌지 않았다」와
「통과했다」가 구별되지 않는다 — 이 저장소가 여러 번 이름 붙인 «공허함은 초록과 같은
모양이다» 형태다.

## What — 이 검사가 재는 성질

**형태가 아니라 성질을 잰다.** 「`github.sha` 라는 글자가 들어 있는가」를 물으면
다른 올바른 형태를 거짓 빨강으로 만들고, 잘못된 형태 하나 — PR 축까지 커밋별로
갈라서 옛 반복이 취소되지 않게 만드는 것(CI 분을 태운다) — 를 거짓 초록으로 놓친다.

그래서 그룹 표현식을 **두 맥락에서 실제로 평가**하고 두 축을 각각 묻는다:

    push,         같은 ref · 다른 sha  →  그룹이 **달라야** 한다 (취소가 불가능해진다)
    pull_request, 같은 ref · 다른 sha  →  그룹이 **같아야** 한다 (옛 반복이 취소된다)

대상 집합은 손으로 나열하지 않고 **워크플로 선언에서 파생한다** — `push` 트리거를
가진 워크플로 전부. 새 워크플로가 생겨도 이 파일을 고치지 않아도 따라간다.

## 정직한 한계

여기 있는 것은 GitHub Actions 표현식의 **부분집합** 평가기다. 이 저장소가 실제로
쓰는 형태(문맥 참조 · 문자열 비교 · `&&`/`||` 삼항)만 안다. 모르는 문법을 만나면
**통과시키지 않고 그 자리에서 멈춘다** — 평가하지 못한 것을 초록으로 세는 것이
이 부류 검사의 유일한 진짜 실패 방식이기 때문이다.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

import yaml

_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = _ROOT / '.github' / 'workflows'

#: `${{ … }}` 한 덩어리.
_EXPR = re.compile(r'\$\{\{(.+?)\}\}', re.DOTALL)

#: 평가기가 아는 유일한 조건 형태: `<context> == '<literal>'`
_COMPARISON = re.compile(r"^([A-Za-z0-9_.]+)\s*==\s*'([^']*)'$")


class UnsupportedExpression(Exception):
    """평가기가 모르는 문법. **초록으로 세지 않는다.**"""


def _workflows() -> list[Path]:
    return sorted(p for p in WORKFLOW_DIR.glob('*.y*ml'))


def _triggers(doc: dict) -> dict:
    """YAML 은 맨 `on:` 을 불리언 `True` 로 읽는다. 두 키를 다 본다."""
    on = doc.get('on', doc.get(True))
    if isinstance(on, dict):
        return on
    if isinstance(on, list):
        return {name: None for name in on}
    if isinstance(on, str):
        return {on: None}
    return {}


def _resolve_atom(atom: str, ctx: dict[str, str]) -> str:
    atom = atom.strip()
    if atom.startswith("'") and atom.endswith("'"):
        return atom[1:-1]
    if atom in ctx:
        return ctx[atom]
    raise UnsupportedExpression(f'모르는 참조: {atom!r}')


def _evaluate_fragment(body: str, ctx: dict[str, str]) -> str:
    """`${{ … }}` 안쪽 하나를 평가한다.

    아는 형태는 둘뿐이다:
      · 단일 참조                    `github.ref`
      · 삼항                          `<비교> && <참> || <거짓>`
    """
    body = body.strip()
    if '&&' in body or '||' in body:
        m = re.match(r'^(.+?)\s*&&\s*(.+?)\s*\|\|\s*(.+)$', body)
        if not m:
            raise UnsupportedExpression(f'삼항으로 읽히지 않는다: {body!r}')
        cond, when_true, when_false = m.groups()
        c = _COMPARISON.match(cond.strip())
        if not c:
            raise UnsupportedExpression(f'모르는 조건 형태: {cond!r}')
        left, literal = c.groups()
        taken = when_true if _resolve_atom(left, ctx) == literal else when_false
        return _resolve_atom(taken, ctx)
    return _resolve_atom(body, ctx)


def _evaluate(template: str, ctx: dict[str, str]) -> str:
    return _EXPR.sub(lambda m: _evaluate_fragment(m.group(1), ctx), template)


def _context(event: str, ref: str, sha: str, workflow: str) -> dict[str, str]:
    return {
        'github.event_name': event,
        'github.ref': ref,
        'github.sha': sha,
        'github.workflow': workflow,
    }


def _group_for(doc: dict, path: Path, event: str, sha: str) -> str | None:
    concurrency = doc.get('concurrency')
    if concurrency is None:
        return None
    if isinstance(concurrency, str):
        template = concurrency
    else:
        template = concurrency['group']
    ctx = _context(event, 'refs/heads/main', sha, doc.get('name', path.stem))
    return _evaluate(template, ctx)


class TestTheEvaluatorUnderstandsThisTree(unittest.TestCase):
    """평가기가 **모든** 그룹 표현식을 실제로 읽어냈는가.

    읽지 못한 것을 조용히 건너뛰면 아래 두 검사가 공허해진다.
    """

    def test_every_concurrency_group_is_evaluable(self) -> None:
        seen = 0
        for path in _workflows():
            doc = yaml.safe_load(path.read_text(encoding='utf-8'))
            if doc.get('concurrency') is None:
                continue
            seen += 1
            try:
                _group_for(doc, path, 'push', 'a' * 40)
                _group_for(doc, path, 'pull_request', 'a' * 40)
            except UnsupportedExpression as exc:
                self.fail(
                    f'{path.name}: concurrency.group 을 평가하지 못했다 — {exc}\n'
                    '평가하지 못한 것을 통과로 세지 않는다. 평가기를 넓히거나 '
                    '표현식을 이 저장소가 쓰는 형태로 되돌려라.'
                )
        self.assertGreater(seen, 0, 'concurrency 를 가진 워크플로가 0개 — 이 검사가 공허하다')


class TestEveryPushedCommitCanKeepItsRecord(unittest.TestCase):
    """축 1 — push 런은 커밋마다 그룹이 갈라져 서로를 취소할 수 없어야 한다."""

    def test_push_runs_do_not_share_a_concurrency_group(self) -> None:
        checked = 0
        for path in _workflows():
            doc = yaml.safe_load(path.read_text(encoding='utf-8'))
            if 'push' not in _triggers(doc):
                continue
            if doc.get('concurrency') is None:
                continue  # 그룹이 없으면 취소도 없다 — 이 축에서 안전하다
            checked += 1
            first = _group_for(doc, path, 'push', '1' * 40)
            second = _group_for(doc, path, 'push', '2' * 40)
            self.assertNotEqual(
                first, second,
                f'{path.name}: 서로 다른 커밋의 push 런이 **같은 concurrency 그룹**을 쓴다 '
                f'({first!r}). 셋 이상을 연달아 머지하면 가운데 커밋의 런이 `cancelled` 로 '
                '끝나 검증 기록이 남지 않는다 — 그리고 `cancelled` 는 빨간 X 가 아니라서 '
                '「돌지 않았다」와 「통과했다」가 구별되지 않는다. '
                "push 축은 `github.sha` 로 갈라라.",
            )
        self.assertGreater(checked, 0, 'push 트리거를 가진 워크플로가 0개 — 이 검사가 공허하다')


class TestPullRequestIterationsStillSupersede(unittest.TestCase):
    """축 2 — PR 축은 그대로 ref 별로 묶여야 한다.

    ⚠️ 축 1 만 있으면 「전부 `github.sha` 로 바꾼다」가 통과한다. 그러면 PR 을 밀
    때마다 옛 런이 살아남아 CI 분을 태운다 — 이 저장소가 2026-08-07 에
    `ci-minutes-root-cause` 로 이름 붙인 바로 그 비용이다.
    """

    def test_pull_request_runs_of_one_branch_share_a_group(self) -> None:
        checked = 0
        for path in _workflows():
            doc = yaml.safe_load(path.read_text(encoding='utf-8'))
            if 'pull_request' not in _triggers(doc):
                continue
            if doc.get('concurrency') is None:
                continue
            checked += 1
            first = _group_for(doc, path, 'pull_request', '1' * 40)
            second = _group_for(doc, path, 'pull_request', '2' * 40)
            self.assertEqual(
                first, second,
                f'{path.name}: 같은 브랜치의 PR 런들이 **서로 다른 그룹**을 쓴다 '
                f'({first!r} vs {second!r}). 그러면 새 push 가 옛 반복을 취소하지 못해 '
                'CI 분이 낭비된다. PR 축은 `github.ref` 로 묶어라.',
            )
        self.assertGreater(checked, 0, 'pull_request 트리거를 가진 워크플로가 0개 — 이 검사가 공허하다')


class TestTheCheckWouldSeeTheDefect(unittest.TestCase):
    """이 검사가 **고쳐지기 전 형태**를 실제로 잡는가.

    봉인이 오늘의 트리에서 초록인 것과, 그 봉인에 이빨이 있는 것은 다른 명제다.
    """

    #: 2026-09-06 에 실제로 두 워크플로에 있던 형태.
    _DEFECTIVE = "${{ github.workflow }}-${{ github.ref }}"

    #: 축 1 만 보면 통과해 버리는 과잉 교정.
    _OVERCORRECTED = "${{ github.workflow }}-${{ github.sha }}"

    def _group(self, template: str, event: str, sha: str) -> str:
        return _evaluate(template, _context(event, 'refs/heads/main', sha, 'checks'))

    def test_the_old_form_would_fail_the_push_axis(self) -> None:
        self.assertEqual(
            self._group(self._DEFECTIVE, 'push', '1' * 40),
            self._group(self._DEFECTIVE, 'push', '2' * 40),
            '옛 형태가 축 1 에서 잡히지 않는다 — 이 봉인은 이빨이 없다',
        )

    def test_the_over_corrected_form_would_fail_the_pull_request_axis(self) -> None:
        self.assertNotEqual(
            self._group(self._OVERCORRECTED, 'pull_request', '1' * 40),
            self._group(self._OVERCORRECTED, 'pull_request', '2' * 40),
            '과잉 교정이 축 2 에서 잡히지 않는다 — 축 1 만으로는 부족하다',
        )

    def test_the_shipped_form_satisfies_both_axes(self) -> None:
        shipped = (
            "${{ github.workflow }}-"
            "${{ github.event_name == 'pull_request' && github.ref || github.sha }}"
        )
        self.assertNotEqual(
            self._group(shipped, 'push', '1' * 40),
            self._group(shipped, 'push', '2' * 40),
        )
        self.assertEqual(
            self._group(shipped, 'pull_request', '1' * 40),
            self._group(shipped, 'pull_request', '2' * 40),
        )

    def test_an_unknown_expression_stops_instead_of_passing(self) -> None:
        with self.assertRaises(UnsupportedExpression):
            self._group('${{ github.event.number }}', 'push', '1' * 40)


if __name__ == '__main__':
    unittest.main()
