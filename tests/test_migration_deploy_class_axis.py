"""마이그레이션 배포 부류 판정이 원장에서 파생되는가 (2026-09-05).

`scripts/platform_migration_deploy_class.py` 는 각 마이그레이션이 **도는 서비스를
깨는가**를 SQL 자체에서 판정한다. 손 목록이 아니라 파생이라는 것이 이 축의 전부이므로,
파생이 조용히 넓어지거나(전부 ONLINE) 좁아지는(전부 STOP-WINDOW) 것을 여기서 막는다.

⚠️ 이 파일이 봉인하는 실측 결함: 첫 판이 `DROP CONSTRAINT` 를 무조건 위험으로 읽어
`034_sample_custody_events.sql` 을 정지 창 부류로 **잘못** 분류했다. 그 파일은 자기가
방금 만든 표의 제약을 `DROP ... IF EXISTS` → `ADD` 로 재생성하는 멱등 관용구를 쓴다.
도는 코드는 그 표를 본 적이 없으므로 위험이 아니다.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))

from platform_migration_deploy_class import (  # noqa: E402
    _executable_sql,
    _hazards,
    classify,
    collect,
)

MIGRATIONS = PROJECT_ROOT / 'migrations'


class TestTheDerivationIsNotVacuous(unittest.TestCase):
    """양쪽이 다 나와야 판정이 살아 있다 — 한쪽만 나오면 상수 함수다."""

    def test_both_verdicts_occur_in_the_real_ledger(self):
        verdicts = {row['deploy_class'] for row in collect([])}
        self.assertEqual(verdicts, {'ONLINE', 'STOP-WINDOW'})

    def test_most_migrations_are_online(self):
        rows = collect([])
        online = [r for r in rows if r['deploy_class'] == 'ONLINE']
        # 대부분이 더하기만 한다. 이 비율이 뒤집히면 규칙이 과잉 발화한 것이다.
        self.assertGreater(len(online), len(rows) // 2, '절반 넘게 정지 창으로 읽혔다')


class TestCreatingAndThenDroppingIsNotAHazard(unittest.TestCase):
    """같은 파일이 만든 대상의 제거는 위험이 아니다 — 도는 코드는 본 적이 없다."""

    def test_the_recreate_idiom_reads_as_online(self):
        sql = (
            'CREATE TABLE IF NOT EXISTS "t" ("a" TEXT);'
            'ALTER TABLE "t" DROP CONSTRAINT IF EXISTS "ck_t";'
            'ALTER TABLE "t" ADD CONSTRAINT "ck_t" CHECK ("a" <> \'\');'
        )
        self.assertEqual(_hazards(sql), [])

    def test_the_real_file_that_first_broke_this_is_online(self):
        path = MIGRATIONS / '034_sample_custody_events.sql'
        if not path.is_file():          # 이 상자에 그 파일이 없는 배송본
            self.skipTest(f'{path.name} 부재')
        self.assertEqual(classify(path)['deploy_class'], 'ONLINE')

    def test_dropping_a_constraint_it_does_not_recreate_is_a_hazard(self):
        sql = 'ALTER TABLE "t" DROP CONSTRAINT IF EXISTS "ck_t";'
        self.assertTrue(_hazards(sql))

    def test_dropping_a_column_of_a_pre_existing_table_is_a_hazard(self):
        sql = 'ALTER TABLE "projects" DROP COLUMN IF EXISTS "customer";'
        hazards = _hazards(sql)
        self.assertTrue(hazards)
        self.assertIn('projects', hazards[0])

    def test_dropping_a_column_of_a_table_it_just_created_is_not(self):
        sql = (
            'CREATE TABLE IF NOT EXISTS "scratch" ("a" TEXT, "b" TEXT);'
            'ALTER TABLE "scratch" DROP COLUMN IF EXISTS "b";'
        )
        self.assertEqual(_hazards(sql), [])


class TestRollbackAnnotationsAreNotExecutableStatements(unittest.TestCase):
    """``--rollback`` 은 되돌리기 안내다. 문으로 읽으면 모든 파일이 자기 반대를 한다."""

    def test_a_commented_drop_is_not_read(self):
        sql = _executable_sql_of('--rollback ALTER TABLE "projects" DROP COLUMN "x";\nSELECT 1;')
        self.assertEqual(_hazards(sql), [])

    def test_an_uncommented_one_is(self):
        self.assertTrue(_hazards('ALTER TABLE "projects" DROP COLUMN "x";'))


class TestThisWaveIsClassifiedAsTheRunbookSays(unittest.TestCase):
    """런북 §4-a 가 이름으로 부르는 계열 — 문서와 도구가 갈라지지 않게 못박는다."""

    EXPECTED = {
        '031_project_applicant_search_axis.sql': 'ONLINE',
        '032_retire_project_customer_column.sql': 'STOP-WINDOW',
        '033_drop_write_only_project_columns.sql': 'STOP-WINDOW',
        '035_drop_write_only_sample_column.sql': 'STOP-WINDOW',
    }

    def test_each_file_lands_where_the_runbook_says(self):
        for name, expected in self.EXPECTED.items():
            path = MIGRATIONS / name
            if not path.is_file():
                self.skipTest(f'{name} 부재')
            self.assertEqual(classify(path)['deploy_class'], expected, name)

    def test_the_one_that_can_refuse_is_flagged(self):
        path = MIGRATIONS / '032_retire_project_customer_column.sql'
        if not path.is_file():
            self.skipTest('032 부재')
        self.assertTrue(
            classify(path)['can_refuse'],
            '032 는 RAISE EXCEPTION 가드를 든다 — 창을 열기 전에 미리 돌려야 한다',
        )


def _executable_sql_of(text: str) -> str:
    """문자열을 파일처럼 통과시킨다(임시 파일 없이 같은 주석 제거를 쓴다)."""
    import tempfile
    with tempfile.NamedTemporaryFile('w', suffix='.sql', delete=False, encoding='utf-8') as handle:
        handle.write(text)
        name = handle.name
    try:
        return _executable_sql(Path(name))
    finally:
        Path(name).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
