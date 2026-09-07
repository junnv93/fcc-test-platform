#!/usr/bin/env python3
"""방아쇠 판정 SSOT — 「이 변경이 사람의 «기록된» 결정을 요구하는가」.

■ 왜 이 파일이 있는가

이 레인의 승인 모델은 **예외 승인**이다. 리드는 상시 승인자가 아니라 예외
판단자이고, 기본은 동료 1인 승인이다. 규칙 본문 SSOT 는 `intent/README.md`.

그 모델이 실질이 되려면 「예외」가 **기계가 판정할 수 있는 것**이어야 한다.
사람이 「이건 중요해 보이니 리드를 부르자」로 판단하면, 바쁜 날에는 안 부른다.

■ 왜 «알림»이 아니라 «red» 인가

알림은 무시할 수 있고, 무시된 알림은 없는 알림과 같은 모양이다. 그래서 이
판정기는 사람을 부르지 않는다 — **아티팩트 안에 「이름 붙은 결정」이 적히기
전까지 검사가 초록이 되지 않게** 한다. 이 레포의 17항목 자가점검이 이미 그
형태다: 상태 뒤에 사유가 없으면 거부된다.

■ 다섯 방아쇠

    T1  되돌릴 수 없는 경로 + 게이트 자신을 건드림
    T2  delivered_test_run_baseline.json 에 실패 «이름이 추가»됨   (부채 등재)
    T3  spec.md 에 답 없는 열린 질문이 남음
    T4  plan.md §1 표에 없는 파일이 diff 에 있음
    T5  변경 파일 수가 plan.md 선언의 2배를 넘음

■ ⚠️ T1 은 오늘 «게이트가 아니다»

`CODEOWNERS` 는 `require_code_owner_reviews: false` 이면 리뷰어를 자동 요청할
뿐 막지 않는다. 그리고 **켤 수도 없다** — 협업자가 1명이고 GitHub 은 자기 PR
자기 승인을 금지하므로, 켜면 그 경로를 아무도 고칠 수 없게 된다. 이 규모에서
CODEOWNERS 는 원리적으로 게이트가 될 수 없다(2026-09-07 실측).

그래서 이 판정기는 T1 을 **보고만 하고 실패로 세지 않는다**(`advisory`).
그 사실을 숨기면 「이름이 보장처럼 생겼는데 보장이 아닌」 상태가 된다 —
이 레포가 반복해 값을 치른 계급이다.

■ T1 경로 목록의 출처

**`CODEOWNERS` 를 파싱한다. 하드코딩하지 않는다.** 같은 집합이 두 곳에 있으면
갈라지고, 갈라지면 「서버가 (언젠가) 막는 것」과 「검사가 말하는 것」이 달라진다.
「무엇이」는 파생하고 「어디」만 적는다.

■ 종료코드
    0  방아쇠 없음 — 사람의 기록된 결정이 필요하지 않다
    1  방아쇠 발화 — 무엇이 왜 걸렸는지 이름으로 말한다
    2  판정기 자신이 돌지 못했다 (입력 부재 등)
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import subprocess
import sys

#: 답 슬롯 «전부». 이름이 있든 없든, 내용이 있든 없든 일단 여기 걸린다.
#: ⚠️ 먼저 «모든 슬롯»을 세고, 그중 «온전한 답»을 빼는 순서여야 한다.
#:    빈 괄호(`답( ):`)만 찾으면 「이름은 있는데 내용이 없는」 형태를 놓친다.
#:    그것이 이 방아쇠의 가장 싼 우회(3초)이고, 주입 ⑤ 가 그것을 잡았다.
_ANSWER_SLOT = re.compile(r'^[ \t]*(?:→|->)[ \t]*답[ \t]*\(', re.MULTILINE)

#: «온전한» 답. 이름과 내용이 둘 다 있어야 한다.
#: 17항목 자가점검의 「사유 축」과 같은 판정이다 — 상태 뒤에 아무것도 없는
#: 행은 어테스테이션이 아니라 단어다.
_ANSWERED = re.compile(
    r'^[ \t]*(?:→|->)[ \t]*답[ \t]*\([ \t]*(?P<who>[^)\s][^)]*?)[ \t]*\)[ \t]*:[ \t]*(?P<what>\S.*)$',
    re.MULTILINE)

#: 부채 등재 · 범위 확대의 어테스테이션. 이름과 사유가 둘 다 필요하다.
_ATTESTATION = re.compile(
    r'^\s*\*{0,2}(?P<key>Debt-Accepted-By|Scope-Extended-By)\*{0,2}\s*:\s*(?P<value>.*)$',
    re.MULTILINE)

#: 어테스테이션의 «빈» 값. 서식의 기본값이 여기 걸린다.
_EMPTY_ATTESTATION = re.compile(r'^\s*(?:\(없음\)|없음|N/?A|-|—)?\s*$', re.IGNORECASE)

#: T5 — 변경 파일 수가 선언의 몇 배를 넘으면 발화하는가.
#: ⚠️ 이 값에는 근거가 없다. 표본이 없어 임의로 골랐고, 첫 5개 의도를 돌린 뒤
#:    실제 분포로 다시 정한다(`intent/intent-driven-workflow/spec.md` §7).
#:    그때까지 이 상수는 «선언된 임의값»이지 측정값이 아니다.
SCOPE_MULTIPLIER = 2.0

#: 계획 표에 없어도 T4 를 발화시키지 않는 경로.
#: 근거: 이 셋은 «계획의 산출물»이지 계획의 대상이 아니다 — 계획서 자신을
#: 계획서에 적게 하면 모든 계획이 자기 자신을 첫 행으로 갖는다.
_PLAN_EXEMPT_SUFFIXES = ('/intent.md', '/spec.md', '/plan.md')


@dataclass(frozen=True)
class Trigger:
    """발화한 방아쇠 하나."""

    name: str
    subject: str
    remedy: str
    advisory: bool = False

    def render(self) -> str:
        mark = '·' if self.advisory else '✗'
        tail = '  (권고 — 오늘 막지 않는다)' if self.advisory else ''
        return f'  {mark} {self.name}  {self.subject}\n      → {self.remedy}{tail}'


@dataclass
class Verdict:
    triggers: list[Trigger] = field(default_factory=list)

    @property
    def blocking(self) -> list[Trigger]:
        return [t for t in self.triggers if not t.advisory]

    @property
    def advisory(self) -> list[Trigger]:
        return [t for t in self.triggers if t.advisory]


def read_codeowners_paths(root: Path) -> list[str]:
    """`CODEOWNERS` 에서 소유자가 «있는» 경로 패턴만 뽑는다.

    ⚠️ 이것이 T1 의 SSOT 다. 여기 없는 경로는 T1 이 아니다.
    파일이 없으면 빈 목록을 돌려준다 — 그리고 그 사실을 호출부가 말한다.
    """
    codeowners = root / 'CODEOWNERS'
    if not codeowners.is_file():
        return []
    patterns: list[str] = []
    for line in codeowners.read_text(encoding='utf-8').splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            # 소유자가 없는 줄은 CODEOWNERS 문법상 무효다. 무시하되 조용하지 않게.
            continue
        patterns.append(parts[0])
    return patterns


def _matches_codeowner_pattern(path: str, pattern: str) -> bool:
    """gitignore 류 패턴 하나가 경로에 걸리는지.

    ⚠️ `fnmatch` 를 쓰지 않는다 — `fnmatch` 의 `*` 는 `/` 를 넘는다. CODEOWNERS 의
    `/intent/*/spec.md` 는 `intent/a/spec.md` 에는 걸리고 `intent/a/b/spec.md` 에는
    걸리지 않아야 한다. 그 차이를 못 보면 T1 이 조용히 넓어진다.
    """
    pat = pattern.lstrip('/')
    if pat.endswith('/'):
        return path == pat.rstrip('/') or path.startswith(pat)
    segments = pat.split('/')
    parts = path.split('/')
    if len(segments) > len(parts):
        return False
    for seg, part in zip(segments, parts):
        if seg == '*':
            continue
        if '*' in seg:
            regex = '^' + '[^/]*'.join(re.escape(p) for p in seg.split('*')) + '$'
            if not re.match(regex, part):
                return False
        elif seg != part:
            return False
    # 디렉터리 패턴이 아니면 정확히 그 길이여야 한다.
    return len(segments) == len(parts)


def check_t1(changed: list[str], owned: list[str]) -> list[Trigger]:
    hits = sorted({p for p in changed
                   for pat in owned if _matches_codeowner_pattern(p, pat)})
    if not hits:
        return []
    shown = ', '.join(hits[:4]) + (f' 외 {len(hits) - 4}건' if len(hits) > 4 else '')
    return [Trigger(
        'T1', f'CODEOWNERS 가 소유를 선언한 경로를 건드린다 — {shown}',
        'CODEOWNERS 상 소유자의 리뷰. ⚠️ 오늘 이것은 강제되지 않는다 '
        '(require_code_owner_reviews=false, 그리고 협업자 1명이라 켤 수도 없다).',
        advisory=True)]


def check_t2(root: Path, baseline_before: set[str], baseline_after: set[str],
             spec_texts: dict[str, str]) -> list[Trigger]:
    added = sorted(baseline_after - baseline_before)
    if not added:
        return []
    for text in spec_texts.values():
        for m in _ATTESTATION.finditer(text):
            if m.group('key') == 'Debt-Accepted-By' and not _EMPTY_ATTESTATION.match(m.group('value')):
                return []
    shown = ', '.join(added[:3]) + (f' 외 {len(added) - 3}건' if len(added) > 3 else '')
    return [Trigger(
        'T2', f'기준선에 실패 이름이 추가된다 ({len(added)}건) — {shown}',
        '이것은 «부채 등재»다. 해당 spec.md 에 '
        '`Debt-Accepted-By: <이름> — <사유>` 를 적어라.')]


def check_t3(spec_texts: dict[str, str]) -> list[Trigger]:
    out: list[Trigger] = []
    for rel, text in sorted(spec_texts.items()):
        slots = len(_ANSWER_SLOT.findall(text))
        answered = len(_ANSWERED.findall(text))
        blanks = slots - answered
        if blanks > 0:
            out.append(Trigger(
                'T3', f'{rel} 에 답 없는 열린 질문 {blanks}건 '
                      f'(슬롯 {slots} · 온전한 답 {answered})',
                '각 질문 아래에 `→ 답(<이름>): <내용>` 을 적어라. '
                '이름만 적고 내용을 비우면 답이 아니다. '
                '「없음」이 답이면 그것도 이름을 붙여라.'))
    return out


def _planned_paths(text: str) -> set[str]:
    """`plan.md` §1 표에서 파일 경로를 뽑는다.

    표 셀 안의 백틱 코드 스팬을 읽는다. 산문 안의 경로는 세지 않는다 —
    「무엇을 바꿀 것인가」의 선언은 표이지 산문이 아니기 때문이다.
    """
    planned: set[str] = set()
    for line in text.splitlines():
        if not line.lstrip().startswith('|'):
            continue
        for token in re.findall(r'`([^`]+)`', line):
            token = token.strip()
            if '/' in token or token.endswith('.md') or token.endswith('.py'):
                planned.add(token.lstrip('/'))
    return planned


def check_t4_t5(changed: list[str], plan_texts: dict[str, str]) -> list[Trigger]:
    if not plan_texts:
        return []
    planned: set[str] = set()
    attested = False
    for text in plan_texts.values():
        planned |= _planned_paths(text)
        for m in _ATTESTATION.finditer(text):
            if m.group('key') == 'Scope-Extended-By' and not _EMPTY_ATTESTATION.match(m.group('value')):
                attested = True
    if attested:
        return []

    def is_planned(path: str) -> bool:
        if path.endswith(_PLAN_EXEMPT_SUFFIXES):
            return True
        return any(path == p or path.startswith(p.rstrip('/') + '/') for p in planned)

    out: list[Trigger] = []
    unplanned = sorted(p for p in changed if not is_planned(p))
    if unplanned:
        shown = ', '.join(unplanned[:4]) + (f' 외 {len(unplanned) - 4}건' if len(unplanned) > 4 else '')
        out.append(Trigger(
            'T4', f'plan.md §1 표에 없는 파일 {len(unplanned)}건 — {shown}',
            '표에 추가하거나, plan.md 에 `Scope-Extended-By: <이름> — <사유>` 를 적어라.'))
    if planned and len(changed) > len(planned) * SCOPE_MULTIPLIER:
        out.append(Trigger(
            'T5', f'변경 {len(changed)}건이 선언 {len(planned)}건의 '
                  f'{SCOPE_MULTIPLIER:g}배를 넘는다',
            'T4 와 같다. ⚠️ 이 배수에는 근거가 없다 — 표본이 모이면 다시 정한다.'))
    return out


# ── 입력 수집 ────────────────────────────────────────────────────────────────

def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(['git', '-C', str(root), *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)} 실패: {proc.stderr.strip()}')
    return proc.stdout


def changed_paths(root: Path, base: str, head: str) -> list[str]:
    out = _git(root, 'diff', '--name-only', f'{base}...{head}')
    return sorted(p for p in out.splitlines() if p.strip())


def _baseline_names(blob: str) -> set[str]:
    """`delivered_test_run_baseline.json` 한 판의 실패 «이름 집합».

    ⚠️ 개수가 아니라 이름이다. 하나 고치고 하나 깨뜨리면 개수는 같다 —
    `scripts/lane_check.py` 가 같은 이유로 이름으로 판정한다.
    """
    try:
        data = json.loads(blob) if blob.strip() else {}
    except json.JSONDecodeError:
        return set()
    return {str(x) for x in data.get('baseline', [])}


def collect(root: Path, base: str, head: str) -> tuple[list[str], set[str], set[str],
                                                       dict[str, str], dict[str, str]]:
    changed = changed_paths(root, base, head)

    def show(ref: str, path: str) -> str:
        try:
            return _git(root, 'show', f'{ref}:{path}')
        except RuntimeError:
            return ''  # 그 판에 그 파일이 없었다 — 빈 것으로 읽는다.

    baseline_before = _baseline_names(show(base, 'delivered_test_run_baseline.json'))
    baseline_after = _baseline_names(show(head, 'delivered_test_run_baseline.json'))

    # spec/plan 은 «head 판»을 읽는다 — 어테스테이션은 이 변경이 담는 것이기 때문.
    specs = {p: show(head, p) for p in changed if p.endswith('/spec.md')}
    plans = {p: show(head, p) for p in changed if p.endswith('/plan.md')}
    # 계획은 바뀌지 않고 코드만 바뀌는 것이 정상이므로, 변경에 없어도 브랜치가
    # 속한 의도의 plan.md 를 찾아 읽는다. 없으면 T4/T5 는 판정하지 않는다.
    if not plans:
        for slug_dir in sorted((root / 'intent').glob('*/plan.md')) if (root / 'intent').is_dir() else []:
            rel = str(slug_dir.relative_to(root))
            if any(c.startswith('intent/' + slug_dir.parent.name + '/') for c in changed):
                plans[rel] = show(head, rel)
    return changed, baseline_before, baseline_after, specs, plans


def evaluate(root: Path, base: str, head: str) -> Verdict:
    changed, b_before, b_after, specs, plans = collect(root, base, head)
    v = Verdict()
    v.triggers += check_t1(changed, read_codeowners_paths(root))
    v.triggers += check_t2(root, b_before, b_after, specs)
    v.triggers += check_t3(specs)
    v.triggers += check_t4_t5(changed, plans)
    return v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description='방아쇠 판정 — 「이 변경이 사람의 «기록된» 결정을 요구하는가」')
    ap.add_argument('--root', default='.', help='상자 루트 (기본: 현재 디렉터리)')
    ap.add_argument('--base', default='origin/main', help='비교 기준 (기본: origin/main)')
    ap.add_argument('--head', default='HEAD', help='판정 대상 (기본: HEAD)')
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / '.git').exists() and not (root / 'pyproject.toml').is_file():
        print(f'방아쇠 판정: {root} 는 이 상자의 루트가 아니다.', file=sys.stderr)
        return 2
    try:
        verdict = evaluate(root, args.base, args.head)
    except RuntimeError as exc:
        print(f'방아쇠 판정: 돌지 못했다 — {exc}', file=sys.stderr)
        print('  ⚠️ 이것은 «통과가 아니다».', file=sys.stderr)
        return 2

    if not (root / 'CODEOWNERS').is_file():
        print('  ⚠️ CODEOWNERS 가 없다 — T1 은 판정하지 못했다 (부재는 통과가 아니다).',
              file=sys.stderr)

    for t in verdict.advisory:
        print(t.render())
    if not verdict.blocking:
        print('방아쇠 없음 — 사람의 «기록된» 결정이 필요하지 않다.')
        return 0
    print('\n방아쇠가 발화했다 — 아티팩트에 「이름 붙은 결정」이 없다:\n')
    for t in verdict.blocking:
        print(t.render())
    print('\n  ⚠️ 이것은 「리드를 불러라」가 아니다. «결정에 주인이 있다»는 사실을')
    print('     파일에 적으라는 것이다. 이름은 리드일 필요가 없다.')
    print('     규칙 본문: intent/README.md §방아쇠')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
