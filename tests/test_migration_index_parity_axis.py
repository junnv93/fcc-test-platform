"""증분 마이그레이션이 만드는 인덱스가 스키마 SSOT 와 같은가 (2026-09-04).

중앙 DB 의 DDL 은 두 경로로 생긴다.

  * **신규 DB** — `migrations/001_initial_central_db.sql`. 이 파일은 손으로 쓰지 않는다.
    `docs/platform/central_db_schema.v1.json` 에서 `scripts/export_platform_central_db_ddl.py`
    가 생성하고, `--check` 가 그 일치를 봉인한다.
  * **이미 배포된 DB** — `migrations/0NN_*.sql` 증분 파일. 이쪽은 **사람이 손으로 쓴다.**

⚠️ **그리고 두 번째 경로를 스키마 SSOT 와 대조하는 검사가 0건이었다.** 그래서 같은 인덱스가
두 경로에서 다르게 만들어져도 아무 데도 드러나지 않는다 — 신규 DB 와 운영 DB 가 조용히
갈라지고, 그 차이는 성능 문제로만 나타나므로 원인에 도달하기 어렵다.

실측 2026-09-04 — 그 일이 실제로 났다. `034_sample_custody_events.sql` 은
`idx_sample_custody_events_project_cert` 를 **부분 인덱스**(`WHERE intake_cert_number IS
NOT NULL`)로, `idx_sample_custody_events_sample_created` 를 **DESC 정렬**로 만드는데,
스키마 JSON 선언에는 그 둘이 빠져 있어 `001` 은 전체 인덱스·ASC 로 나왔다. 생성기는
`where` 와 `orders` 를 **이미 지원하고 있었다** — 선언이 그것을 쓰지 않았을 뿐이다.
즉 결함은 도구가 아니라 **선언과 손으로 쓴 SQL 사이에 대조가 없다는 것**이었다.

## 이 축이 허용하는 차이 둘 — 둘 다 「무엇을 만드는가」가 아니라 「어떻게 적용되는가」다

**① `CONCURRENTLY`** — 증분 마이그레이션은 **돌고 있는 데이터베이스**에 적용되므로 쓰기를
막지 않으려고 `CREATE INDEX CONCURRENTLY` 를 쓴다
(https://www.postgresql.org/docs/current/sql-createindex.html#SQL-CREATEINDEX-CONCURRENTLY).
`001` 은 첫 부팅의 빈 DB 에서 돌므로 그 키워드가 필요 없다.

**② `IF NOT EXISTS`** — `001` 은 `docker-entrypoint-initdb.d` 에서 **다시 돌 수 있으므로**
전부 `IF NOT EXISTS` 로 쓴다(그 파일 헤더가 그 사실을 적는다). 증분 마이그레이션은
`schema_migrations` 원장이 버전당 한 번만 적용하도록 보장하므로 그 절이 선택적이다.

⚠️ **정규화는 여기서 멈춘다.** 표기 차이(식별자 인용 등)는 지우지 않는다 — 그것을
허용하기 시작하면 이 축이 재는 것이 사라진다. 실측 2026-09-04: `030` 의 술어가
`status = ...`, 스키마 선언이 `"status" = ...` 로 갈려 있었고, 정규화가 아니라
**선언을 고쳐** 맞췄다.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = PROJECT_ROOT / 'migrations'
sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))

from export_platform_central_db_ddl import (  # noqa: E402
    _index_sql,
    load_schema,
)

#: 인덱스 선언이 붙을 수 있는 스키마 절. 손 목록이 아니라 **스키마의 구조**다 —
#: 새 절이 생기면 이 시험이 그것을 놓치므로 아래 `test_the_scan_is_not_vacuous` 가
#: 선언 개수로 그 사실을 붙잡는다.
_INDEX_BEARING_SECTIONS = ('tables', 'materialized_views', 'views')

#: `001` 은 생성물이고 `--check` 가 이미 봉인한다. 여기서 다시 세면 생성기 출력을
#: 생성기 출력과 비교하는 항등식이 되어 아무것도 증명하지 못한다.
_GENERATED = '001_initial_central_db.sql'

_LINE_COMMENT = re.compile(r'--[^\n]*')
_CREATE_INDEX = re.compile(
    r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?'
    r'"(?P<name>\w+)"[^;]*;',
    re.IGNORECASE | re.DOTALL,
)


def _declared_indexes() -> dict[str, str]:
    """스키마 SSOT 가 선언한 인덱스 → 생성기가 내는 정본 DDL."""
    schema = load_schema()
    declared: dict[str, str] = {}
    for section in _INDEX_BEARING_SECTIONS:
        for owner, spec in (schema.get(section) or {}).items():
            if not isinstance(spec, dict):
                continue
            for index in spec.get('indexes') or []:
                declared[index['name']] = _index_sql(owner, index)
    return declared


def _normalize(statement: str) -> str:
    """비교를 위한 정규형 — 공백과, 적용 방식을 말하는 두 절만 지운다.

    지우는 것은 `CONCURRENTLY` 와 `IF NOT EXISTS` 뿐이다(모듈 docstring 참조).
    ⚠️ 여기에 규칙을 더 넣지 마라. 정규화가 넓어질수록 이 축이 못 보는 차이가 넓어진다.
    """
    text = re.sub(r'\bCONCURRENTLY\b', ' ', statement, flags=re.IGNORECASE)
    text = re.sub(r'\bIF\s+NOT\s+EXISTS\b', ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip().rstrip(';')


def _incremental_index_statements() -> list[tuple[str, str, str]]:
    """(파일명, 인덱스 이름, 문) — 주석 줄은 제외한다.

    ⚠️ 주석 제거가 없으면 `--rollback CREATE UNIQUE INDEX …` 같은 되돌리기 안내가
    실제 문으로 읽힌다(실측 2026-09-04: `ux_users_subject` 가 그렇게 잡혔다).
    """
    found: list[tuple[str, str, str]] = []
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name == _GENERATED:
            continue
        sql = _LINE_COMMENT.sub('', path.read_text(encoding='utf-8'))
        for match in _CREATE_INDEX.finditer(sql):
            found.append((path.name, match.group('name'), match.group(0)))
    return found


_DROP_COLUMN = re.compile(
    r'ALTER\s+TABLE\s+"(?P<table>\w+)"\s+DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?"(?P<column>\w+)"',
    re.IGNORECASE,
)
_INDEX_TARGET = re.compile(r'\bON\s+"(?P<table>\w+)"(?P<body>.*)$', re.IGNORECASE | re.DOTALL)


def _dropped_columns() -> dict[str, set[str]]:
    """증분 마이그레이션이 **삭제하는** 칸 → ``{표: {칸, …}}``.

    주석을 먼저 지운다: ``--rollback`` 안내는 되돌리기 지시이지 이 원장이 적용하는
    문이 아니다(같은 이유로 :func:`_incremental_index_statements` 도 지운다).
    """
    dropped: dict[str, set[str]] = {}
    for path in sorted(MIGRATIONS.glob('*.sql')):
        if path.name == _GENERATED:
            continue
        sql = _LINE_COMMENT.sub('', path.read_text(encoding='utf-8'))
        for match in _DROP_COLUMN.finditer(sql):
            dropped.setdefault(match.group('table'), set()).add(match.group('column'))
    return dropped


def _retired_with_its_column(statement: str, dropped: dict[str, set[str]]) -> bool:
    """이 인덱스가 **자기 칸과 함께 은퇴**했는가.

    PostgreSQL 은 칸을 지울 때 그 칸에 의존하는 인덱스를 함께 지운다
    (https://www.postgresql.org/docs/current/sql-altertable.html). 그러므로 은퇴한 칸의
    인덱스는 "스키마 SSOT 가 모르는" 것이 **옳다** — 신규 DB 에는 그 칸이 애초에 없고,
    운영 DB 에서는 삭제 마이그레이션이 인덱스를 함께 걷어간다.

    ⚠️ 손 목록이 아니라 **원장에서 파생**한다. 예외를 이름으로 적으면 그 칸이 되살아나는
    날에도 예외가 남아 게이트가 조용해진다. 여기서는 삭제 문이 사라지면 예외도 사라진다.

    판정은 좁게 한다: 인덱스가 **거는 표**의 삭제된 칸이 인덱스 본문에 식별자로
    나타날 때만 은퇴로 읽는다. 오타로 없는 칸을 가리키는 인덱스는 여전히 고아다.
    """
    target = _INDEX_TARGET.search(statement)
    if target is None:
        return False
    for column in dropped.get(target.group('table'), ()):
        if re.search(rf'\b{re.escape(column)}\b', target.group('body')):
            return True
    return False


class TestMigrationIndexesMatchTheSchemaSsot(unittest.TestCase):

    def test_the_scan_is_not_vacuous(self):
        """양쪽이 비면 통과로 읽힌다 — 이 게이트가 스스로 꺼지는 것을 막는다."""
        declared = _declared_indexes()
        statements = _incremental_index_statements()
        self.assertGreater(len(declared), 50, f'선언된 인덱스가 {len(declared)}개뿐이다')
        self.assertGreater(
            len(statements), 20,
            f'증분 마이그레이션에서 찾은 CREATE INDEX 가 {len(statements)}개뿐이다',
        )

    def test_every_index_a_migration_creates_is_declared_in_the_schema(self):
        declared = _declared_indexes()
        dropped = _dropped_columns()
        orphans = sorted({
            f'{name}  ({file})'
            for file, name, statement in _incremental_index_statements()
            if name not in declared and not _retired_with_its_column(statement, dropped)
        })
        self.assertEqual(
            orphans, [],
            '마이그레이션이 만드는데 스키마 SSOT 가 모르는 인덱스가 있다 — 그러면 신규 DB 에는 '
            '그 인덱스가 없다:\n' + '\n'.join(f'  · {item}' for item in orphans)
            + '\n\n고치는 법: docs/platform/central_db_schema.v1.json 의 해당 표에 그 인덱스를 '
            '선언하고 `scripts/export_platform_central_db_ddl.py --write` 로 001 을 다시 내라.\n'
            '그 인덱스가 **은퇴**한 것이라면 선언하지 마라 — 신규 DB 에 없는 칸의 인덱스를 '
            '선언하면 001 이 만들 수 없다. 대신 그 칸을 지우는 마이그레이션이 원장에 있어야 '
            '한다(그러면 이 게이트가 파생으로 알아본다).',
        )

    def test_the_retirement_escape_is_narrow_and_not_vacuous(self):
        """은퇴 예외가 모든 고아를 삼키지 않는가 — 예외는 게이트를 끄는 가장 쉬운 길이다."""
        dropped = _dropped_columns()

        # ① 원장에서 실제로 파생되는가. 삭제 문이 하나도 안 읽히면 이 예외는 죽은
        #    코드이고, 살아 있다면 아래 좁힘 조건들이 의미를 갖는다.
        self.assertTrue(dropped, '삭제되는 칸을 하나도 못 읽었다 — 정규식이 원장과 어긋났다')

        table, column = next(
            (t, c) for t, cols in sorted(dropped.items()) for c in sorted(cols)
        )
        on_it = f'CREATE INDEX "i" ON "{table}" (lower({column}));'
        self.assertTrue(_retired_with_its_column(on_it, dropped))

        # ② 같은 이름의 칸이라도 **다른 표**면 은퇴가 아니다.
        self.assertFalse(
            _retired_with_its_column(
                f'CREATE INDEX "i" ON "a_table_that_drops_nothing" (lower({column}));',
                dropped,
            ),
        )

        # ③ 삭제되지 않은 칸의 인덱스는 여전히 고아다.
        self.assertFalse(
            _retired_with_its_column(
                f'CREATE INDEX "i" ON "{table}" ("a_column_no_migration_drops");', dropped,
            ),
        )

        # ④ 부분 일치로 새지 않는다 — `name` 이 지워졌다고 `applicant_name` 이
        #    은퇴로 읽히면 진짜 고아가 조용히 통과한다.
        self.assertFalse(
            _retired_with_its_column(
                f'CREATE INDEX "i" ON "{table}" ("prefix_{column}_suffix");', dropped,
            ),
        )

    def test_every_shared_index_has_the_same_definition_in_both_paths(self):
        declared = _declared_indexes()
        drift: list[str] = []
        for file, name, statement in _incremental_index_statements():
            canonical = declared.get(name)
            if canonical is None:
                continue  # 위 시험이 따로 보고한다
            if _normalize(statement) != _normalize(canonical):
                drift.append(
                    f'  · {name}  ({file})\n'
                    f'      마이그레이션: {_normalize(statement)}\n'
                    f'      스키마 SSOT : {_normalize(canonical)}'
                )
        self.assertEqual(
            drift, [],
            '같은 인덱스가 신규 DB 와 기존 DB 에서 다르게 만들어진다:\n' + '\n'.join(drift)
            + '\n\n고치는 법: 어느 쪽이 옳은지 정한 뒤 **스키마 SSOT 를 고치고** 001 을 다시 '
            '내라. 생성기는 `where`(부분 인덱스) · `orders`(정렬 방향) · `using`(접근 방법) · '
            '`expressions`(함수 인덱스) 를 이미 표현할 수 있다. 마이그레이션 쪽을 스키마에 '
            '맞춰 낮추는 선택은 마지막이다 — 운영 DB 가 이미 그 정의를 갖고 있으면 그것을 '
            '바꾸는 데 또 하나의 마이그레이션이 든다.',
        )

    def test_only_application_mode_differences_are_tolerated(self):
        """정규화가 넓어지면 이 축이 못 보는 차이가 넓어진다 — 그 폭을 봉인한다."""
        live = 'CREATE INDEX CONCURRENTLY IF NOT EXISTS "x" ON "t" ("a");'
        once = 'CREATE INDEX "x" ON "t" ("a");'
        boot = 'CREATE INDEX IF NOT EXISTS "x" ON "t" ("a");'
        self.assertEqual(_normalize(live), _normalize(boot))
        self.assertEqual(_normalize(once), _normalize(boot))
        # 표기 차이는 허용하지 않는다 — 선언을 고쳐 맞춰야 한다.
        self.assertNotEqual(
            _normalize('CREATE INDEX "x" ON "t" ("a") WHERE a IS NOT NULL;'),
            _normalize('CREATE INDEX "x" ON "t" ("a") WHERE "a" IS NOT NULL;'),
        )
        # 그 밖의 차이는 반드시 드러나야 한다.
        self.assertNotEqual(
            _normalize(boot),
            _normalize('CREATE INDEX IF NOT EXISTS "x" ON "t" ("a") WHERE "a" IS NOT NULL;'),
        )
        self.assertNotEqual(
            _normalize(boot),
            _normalize('CREATE INDEX IF NOT EXISTS "x" ON "t" ("a" DESC);'),
        )
        self.assertNotEqual(
            _normalize(boot),
            _normalize('CREATE UNIQUE INDEX IF NOT EXISTS "x" ON "t" ("a");'),
        )


if __name__ == '__main__':
    unittest.main()
