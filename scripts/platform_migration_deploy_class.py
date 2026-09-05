"""각 마이그레이션이 **배포 경계를 가로지르는가** — 원장에서 파생해 판정한다.

기존 배포 런북은 "이미지 재빌드 → 스택 기동 = 마이그레이션 적용"을 **무정지 정상
경로**로 적는다. 대부분의 마이그레이션이 실제로 그렇다(칸을 더하거나 인덱스를
`CONCURRENTLY` 로 만드는 것은 도는 서비스를 깨지 않는다).

⚠️ **그런데 그 경로에 없는 부류가 있고, 런북에는 그 부류를 알아보는 방법이 없었다.**
칸을 **지우는** 마이그레이션은 지금 서빙 중인 코드를 깬다 — 그 코드는 그 마이그레이션보다
먼저 빌드됐으므로 지워진 칸을 여전히 SQL 에 적고 있을 수 있다. 반대로 새 코드는 그 칸을
채우지 않으므로 `NOT NULL` 이 새 코드를 깬다. 두 제약이 반대 방향이면 **배포와 적용
사이에 순서가 생기고**, 그 순서는 사람이 알아야 한다.

실측 2026-09-04/05 에 그런 계열이 실제로 왔다(`031`→`032`→`033`: 검색 축 이전 ·
`customer` 폐기 · write-only 칸 삭제).

## 이 판정이 손 목록이 아닌 이유

부류를 파일 이름으로 적으면 다음 사람이 그 목록에 자기 파일을 **넣는 것을 잊는다**.
여기서는 SQL 자체에서 파생한다 — 원장이 곧 선언이다.

  ONLINE       더하기만 한다. `CREATE INDEX CONCURRENTLY` 포함. 도는 서비스를 깨지 않는다.
  STOP-WINDOW  `DROP COLUMN` · `DROP TABLE` · `SET NOT NULL` 중 하나라도 한다.
               지금 서빙 중인 코드가 그 모양에 의존할 수 있다.
  CAN-REFUSE   `RAISE EXCEPTION` 가드를 들고 있다 — **창 안에서 거부하면 창이 길어진다.**
               창을 열기 전에 읽기 전용으로 미리 돌려야 하는 마이그레이션이다.

`CAN-REFUSE` 는 STOP-WINDOW 와 **직교**한다. 한 파일이 둘 다일 수 있고, 실제로 032 가
그렇다.

Usage::

    python3 scripts/platform_migration_deploy_class.py             # 전부
    python3 scripts/platform_migration_deploy_class.py --json      # 기계가 읽는 형태
    python3 scripts/platform_migration_deploy_class.py 031 032 033 # 이 계열만
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = PROJECT_ROOT / 'migrations'

#: `001` 은 생성물이고 **신규 DB 의 첫 부팅**에서만 돈다. 배포 경계라는 개념이
#: 적용되지 않는다(그 시점에 서빙 중인 코드가 없다).
GENERATED = '001_initial_central_db.sql'

_LINE_COMMENT = re.compile(r'--[^\n]*')

#: 도는 코드가 의존할 수 있는 모양을 **없애는** 문. 각각이 왜 위험한지 함께 든다 —
#: 판정만 주고 이유를 안 주면 운영자가 그 판정을 신뢰할 근거가 없다.
#:
#: ⚠️ **같은 마이그레이션이 방금 만든 대상에 대한 제거는 위험이 아니다.** 도는 코드는
#: 그 대상을 본 적이 없다. 실측 2026-09-05: `034` 가 자기가 만든 표의 제약을
#: `DROP CONSTRAINT IF EXISTS` → `ADD CONSTRAINT` 로 재생성하는데(멱등 관용구), 이
#: 좁힘이 없으면 그 파일이 정지 창 부류로 잘못 읽혔다. 아래 `_created_here` 가 그
#: 좁힘을 원장에서 파생한다.
_DROP_COLUMN_ON = re.compile(
    r'ALTER\s+TABLE\s+(?:ONLY\s+)?"?(?P<table>\w+)"?\s+DROP\s+COLUMN\b', re.IGNORECASE)
_DROP_TABLE_OF = re.compile(r'DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?"?(?P<table>\w+)"?', re.IGNORECASE)
_SET_NOT_NULL_ON = re.compile(
    r'ALTER\s+TABLE\s+(?:ONLY\s+)?"?(?P<table>\w+)"?[^;]*?\bSET\s+NOT\s+NULL\b',
    re.IGNORECASE | re.DOTALL)
_DROP_CONSTRAINT_NAMED = re.compile(
    r'DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?"?(?P<name>\w+)"?', re.IGNORECASE)
_RENAME_ON = re.compile(
    r'ALTER\s+TABLE\s+(?:ONLY\s+)?"?(?P<table>\w+)"?[^;]*?\bRENAME\b',
    re.IGNORECASE | re.DOTALL)

_CREATE_TABLE = re.compile(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"?(?P<table>\w+)"?', re.IGNORECASE)
_ADD_CONSTRAINT = re.compile(r'ADD\s+CONSTRAINT\s+"?(?P<name>\w+)"?', re.IGNORECASE)

_REFUSAL = re.compile(r'\bRAISE\s+EXCEPTION\b', re.IGNORECASE)


def _created_here(sql: str) -> tuple[set[str], set[str]]:
    """이 파일이 **스스로 만드는** 표와 제약. 그 대상의 제거는 위험이 아니다."""
    return (
        {m.group('table') for m in _CREATE_TABLE.finditer(sql)},
        {m.group('name') for m in _ADD_CONSTRAINT.finditer(sql)},
    )


def _hazards(sql: str) -> list[str]:
    tables, constraints = _created_here(sql)
    found: list[str] = []

    for match in _DROP_COLUMN_ON.finditer(sql):
        if match.group('table') not in tables:
            found.append(
                f"DROP COLUMN on \"{match.group('table')}\" — 도는 코드가 그 칸을 "
                'SELECT/INSERT 열 목록에 적고 있으면 즉시 깨진다')
    for match in _DROP_TABLE_OF.finditer(sql):
        if match.group('table') not in tables:
            found.append(f"DROP TABLE \"{match.group('table')}\" — 같은 이유이고 범위가 더 넓다")
    for match in _SET_NOT_NULL_ON.finditer(sql):
        if match.group('table') not in tables:
            found.append(
                f"SET NOT NULL on \"{match.group('table')}\" — 그 칸을 채우지 않는 코드의 "
                'INSERT 가 즉시 깨진다')
    for match in _DROP_CONSTRAINT_NAMED.finditer(sql):
        if match.group('name') not in constraints:
            found.append(
                f"DROP CONSTRAINT \"{match.group('name')}\" — 그 제약을 전제로 읽던 코드의 "
                '가정이 사라진다 (같은 파일이 다시 ADD 하지 않는다)')
    for match in _RENAME_ON.finditer(sql):
        if match.group('table') not in tables:
            found.append(f"RENAME on \"{match.group('table')}\" — 옛 이름을 적은 코드가 즉시 깨진다")

    # 순서를 안정시키되 중복은 접는다 — 같은 문이 여러 번 나와도 이유는 한 번이면 된다.
    seen: list[str] = []
    for item in found:
        if item not in seen:
            seen.append(item)
    return seen


def _executable_sql(path: Path) -> str:
    """주석을 지운 본문. ``--rollback`` 안내는 되돌리기 지시이지 이 파일이 적용하는
    문이 아니다 — 지우지 않으면 모든 되돌리기 안내가 실제 문으로 읽힌다."""
    return _LINE_COMMENT.sub('', path.read_text(encoding='utf-8'))


def classify(path: Path) -> dict:
    sql = _executable_sql(path)
    reasons = _hazards(sql)
    return {
        'file': path.name,
        'deploy_class': 'STOP-WINDOW' if reasons else 'ONLINE',
        'can_refuse': bool(_REFUSAL.search(sql)),
        'reasons': reasons,
    }


def collect(selectors: list[str]) -> list[dict]:
    rows = []
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name == GENERATED:
            continue
        if selectors and not any(path.name.startswith(s) for s in selectors):
            continue
        rows.append(classify(path))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('selector', nargs='*', help='버전 접두사 (예: 031 032)')
    parser.add_argument('--json', action='store_true', help='기계가 읽는 형태로 낸다')
    args = parser.parse_args(argv)

    rows = collect(args.selector)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0

    if not rows:
        print('해당하는 마이그레이션이 없다.')
        return 0

    width = max(len(r['file']) for r in rows)
    for row in rows:
        flag = ' · CAN-REFUSE' if row['can_refuse'] else ''
        print(f"{row['file']:<{width}}  {row['deploy_class']}{flag}")
        for why in row['reasons']:
            print(f"{'':<{width}}    · {why}")
        if row['can_refuse']:
            print(f"{'':<{width}}    · RAISE EXCEPTION 가드 — 창을 열기 전에 읽기 전용으로 미리 돌려라")

    windowed = [r['file'] for r in rows if r['deploy_class'] == 'STOP-WINDOW']
    print()
    if windowed:
        print(f'정지 창이 필요한 파일 {len(windowed)}건: ' + ', '.join(windowed))
        print('절차: docs/operations/central-pc-update-deploy-runbook.md §4-a')
    else:
        print('전부 ONLINE — 평소 재배포 경로(§5)로 적용해도 된다.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
