#!/usr/bin/env python3
"""삼종세트 판정 SSOT — `intent/<slug>/{intent,spec,plan}.md` 가 온전한가.

■ 무엇을 판정하는가

    1. 슬러그 모양      영문 소문자·숫자·하이픈만 (브랜치 이름이 되기 때문)
    2. 파일 짝          spec.md 는 intent.md 없이 존재할 수 없다 (plan 도 spec 없이)
    3. 머리말 필드      Author / Date / Status / Slug
    4. Slug 정합        머리말의 Slug 가 폴더 이름과 같아야 한다
    5. Status 어휘      draft | accepted | superseded: <slug> | withdrawn
    6. 승인 순서        spec.md 가 있으면 intent.md 는 accepted 여야 한다
    7. 폴더당 하나씩    한 폴더에 intent/spec/plan 각 1개

■ 왜 6번이 있는가

이 흐름의 요지는 「승인 없이 다음으로 못 간다」이다. 그것이 산문으로만 있으면
아무도 안 지킨다 — 이 저장소가 반복해 기록한 형태다.

■ ⚠️ 이 검사가 «못» 하는 것

**내용의 품질을 못 본다.** `Problem` 절에 해결책을 적어도 통과하고,
`Success criteria` 에 "빨라진다"를 적어도 통과한다. 형식은 「채워졌나」를
물을 뿐 「잴 수 있나」를 묻지 못한다. 그 판정은 사람이 한다.

이 문단을 지우지 마라. 지우면 다음 사람이 초록을 「좋은 의도」로 읽는다.

■ 종료코드
    0  온전하다
    1  문제가 있다 — 무엇이 왜인지 이름으로 말한다
    2  검사가 돌지 못했다 (intent/ 부재 등)
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

SLUG_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_FIELD = r'^{name}\s*:\s*(?P<value>.+?)\s*$'
#: 종류마다 «저자 필드의 이름이 다르다» — plan.md 는 개발 담당자가 확정하므로
#: `Engineer:` 다. 하나로 뭉치면 서식과 검사가 갈라지고, 갈라진 쪽을 읽은
#: 사람이 틀린 것을 믿는다(실측: 이 검사의 첫 판이 그것으로 실물을 빨갛게 했다).
REQUIRED_FIELDS = {
    'intent': ('Author', 'Date', 'Status', 'Slug'),
    'spec': ('Author', 'Date', 'Status', 'Slug'),
    'plan': ('Engineer', 'Date', 'Status', 'Slug'),
}
KINDS = ('intent', 'spec', 'plan')

#: `Status:` 가 가질 수 있는 값. 이 집합 밖은 오타이거나 새 어휘이고, 둘 다
#: 조용히 통과하면 안 된다 — 게이트가 읽는 «기계 판독 필드»이기 때문이다.
_STATUS_RE = re.compile(r'^(draft|accepted|withdrawn|superseded:\s*[a-z0-9-]+)$')

#: 서식 파일이 사는 곳. 판정 대상이 아니다.
TEMPLATE_DIR = '_templates'


def field(text: str, name: str) -> str | None:
    m = re.search(_FIELD.format(name=re.escape(name)), text, re.MULTILINE)
    return m.group('value') if m else None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


def problems(root: Path) -> list[str]:
    """온전하지 «않은» 것들을 이름으로 돌려준다. 빈 목록이면 온전하다."""
    out: list[str] = []
    base = root / 'intent'
    if not base.is_dir():
        return ['intent/ 디렉터리가 없다']

    for kind in KINDS:
        tpl = base / TEMPLATE_DIR / f'{kind}.md'
        if not tpl.is_file():
            out.append(f'intent/{TEMPLATE_DIR}/{kind}.md 서식이 없다 — '
                       '동료가 복사할 것이 사라진다')

    for slug_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        slug = slug_dir.name
        if slug == TEMPLATE_DIR:
            continue
        if not SLUG_RE.match(slug):
            out.append(f'intent/{slug}: 슬러그가 영문 소문자·숫자·하이픈이 아니다 '
                       '— 이것이 브랜치 이름 셋이 된다')

        present = {k: (slug_dir / f'{k}.md') for k in KINDS
                   if (slug_dir / f'{k}.md').is_file()}
        if 'intent' not in present:
            out.append(f'intent/{slug}: intent.md 가 없다 — '
                       '모든 폴더는 «왜»에서 시작한다')
        if 'spec' in present and 'intent' not in present:
            out.append(f'intent/{slug}: spec.md 가 intent.md 없이 있다')
        if 'plan' in present and 'spec' not in present:
            out.append(f'intent/{slug}: plan.md 가 spec.md 없이 있다')

        # 한 폴더에 같은 종류가 둘일 수는 없다(파일 이름이 고정). 대신 «변종»을 본다.
        strays = sorted(p.name for p in slug_dir.iterdir()
                        if p.is_file() and p.suffix == '.md'
                        and p.stem not in KINDS)
        if strays:
            out.append(f'intent/{slug}: 삼종세트 밖의 마크다운 {strays} — '
                       '스펙이 둘 필요하면 의도를 둘로 나눠라')

        for kind, path in present.items():
            text = _read(path)
            rel = f'intent/{slug}/{kind}.md'
            for name in REQUIRED_FIELDS[kind]:
                if field(text, name) is None:
                    out.append(f'{rel}: 머리말에 `{name}:` 이 없다')
            declared = field(text, 'Slug')
            if declared is not None and declared != slug:
                out.append(f'{rel}: 머리말 Slug({declared!r})가 폴더 이름({slug!r})과 다르다')
            status = field(text, 'Status')
            if status is not None and not _STATUS_RE.match(status):
                out.append(f'{rel}: Status({status!r})가 정의된 어휘가 아니다 — '
                           'draft | accepted | withdrawn | superseded: <slug>')

        if 'spec' in present and 'intent' in present:
            istatus = field(_read(present['intent']), 'Status')
            if istatus == 'draft':
                out.append(f'intent/{slug}: intent.md 가 draft 인데 spec.md 가 있다 — '
                           '승인 없이 설계로 내려갔다')
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description='삼종세트 형식·짝·상태 검사')
    ap.add_argument('--root', default='.')
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not (root / 'intent').is_dir():
        print(f'삼종세트 검사: {root}/intent 가 없다 — 돌지 못했다.', file=sys.stderr)
        print('  ⚠️ 이것은 «통과가 아니다».', file=sys.stderr)
        return 2
    found = problems(root)
    if not found:
        print('삼종세트: 온전하다.')
        return 0
    print('삼종세트가 온전하지 않다:\n')
    for p in found:
        print(f'  ✗ {p}')
    print('\n  규칙 본문: intent/README.md')
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
