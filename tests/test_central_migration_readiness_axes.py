"""중앙 마이그레이션 사전점검의 판정이 **실제 대상과 같은 질문을 하는가** (2026-09-06).

## Why

`fcc_test_platform/central_migration_readiness_cli.py` 는 창을 열기 «전»에 읽기 전용으로
여섯 축을 묻는다. 그 도구가 조용히 틀리면 **가장 나쁜 자리에서** 틀린다 — 사람이
「준비됐다」를 읽고 창을 연 뒤에 드러난다.

이 파일이 지키는 것 셋:

1. **두 SSOT 문제.** `REFUSAL_GUARD_SQL` 은 `032` 의 `RAISE EXCEPTION` 가드 술어를
   **다시 적은 것**이다. 마이그레이션이 술어를 바꾸면 사전점검은 **다른 질문**을 하면서
   초록을 답한다. 그래서 두 술어를 정규화해 대조한다.
2. **판정 어휘.** BLOCKED 가 UNKNOWN 을 이겨야 한다 — 아는 결함이 먼저 조치 대상이고,
   그래야 UNKNOWN 하나가 BLOCKED 를 가리지 않는다.
3. **각 축이 결함 모양에서 실제로 BLOCKED 를 내는가.** 초록만 보는 검사는 변이가
   살아남는다.

## 한계

판정은 순수 함수라 docker 없이 시험된다. **수집**(`collect_*`)은 여기서 돌지 않는다 —
그 축은 실제 중앙 PC 에서만 관측되고, 그래서 도구가 못 읽은 것을 **UNKNOWN 으로**
답하도록 설계돼 있다(통과가 아니다).
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

from fcc_test_platform.central_migration_readiness_cli import (
    EXIT_BLOCKED,
    EXIT_READY,
    EXIT_UNKNOWN,
    RECONCILABLE,
    REFUSAL_GUARD_SQL,
    VERDICT_BLOCKED,
    VERDICT_READY,
    VERDICT_UNKNOWN,
    AxisResult,
    _box_markers_from_source,
    collect_box_markers,
    judge_checkout,
    judge_deploy_class,
    judge_ledger,
    judge_machine,
    judge_refusal_guard,
    judge_stop_list,
    overall_exit_code,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GUARD_MIGRATION = _REPO_ROOT / 'migrations' / '032_retire_project_customer_column.sql'


def _normalise(sql: str) -> str:
    """식별자 인용과 공백을 걷어낸 술어 — 두 사본을 비교할 수 있는 형태로."""
    text = sql.replace('"', '')
    text = re.sub(r'\s+', ' ', text)
    return text.strip().rstrip(';').strip().lower()


def _predicate_of(sql: str) -> str:
    """`WHERE …` 부터 끝까지. 두 사본에서 같은 방법으로 뽑는다."""
    normalised = _normalise(sql)
    index = normalised.find('where ')
    return normalised[index:] if index >= 0 else ''


def _migration_guard_predicate() -> str:
    """`032` 의 **거부 가드** 술어. 그 파일에서 파생한다 — 여기 적지 않는다."""
    body = _GUARD_MIGRATION.read_text(encoding='utf-8')
    # 가드는 DO 블록의 첫 SELECT … INTO conflicting 이다. 그 문장만 잘라낸다.
    start = body.find('INTO conflicting')
    end = body.find(';', start)
    return _predicate_of(body[start:end])


class TestTheToolAsksTheSameQuestionAsTheMigration(unittest.TestCase):
    """⚠️ 이 파일의 본체 — 두 SSOT 가 갈라지는 날을 잡는다."""

    def test_the_migration_that_this_axis_is_about_exists(self) -> None:
        self.assertTrue(
            _GUARD_MIGRATION.is_file(),
            f'{_GUARD_MIGRATION} 가 없다 — 대조할 대상이 사라지면 이 검사가 공허하다',
        )

    def test_the_predicate_was_actually_extracted(self) -> None:
        """읽지 못한 것을 초록으로 세지 않는다."""
        predicate = _migration_guard_predicate()
        self.assertTrue(predicate.startswith('where '), f'술어를 뽑지 못했다: {predicate!r}')
        self.assertIn('applicant_name', predicate)

    def test_the_readiness_predicate_matches_the_migration_guard(self) -> None:
        self.assertEqual(
            _predicate_of(REFUSAL_GUARD_SQL), _migration_guard_predicate(),
            '사전점검의 술어가 032 의 거부 가드와 다르다. 그러면 이 도구는 «다른 질문»을 '
            '하면서 초록을 답하고, 사람은 그것을 읽고 창을 연다. '
            'REFUSAL_GUARD_SQL 을 마이그레이션에 맞춰라.',
        )


class TestTheVerdictVocabularyIsEnforced(unittest.TestCase):
    def test_an_unknown_verdict_cannot_be_constructed(self) -> None:
        with self.assertRaises(ValueError):
            AxisResult('x', 'MAYBE', '')

    def test_blocked_beats_unknown(self) -> None:
        results = [
            AxisResult('a', VERDICT_UNKNOWN, ''),
            AxisResult('b', VERDICT_BLOCKED, ''),
            AxisResult('c', VERDICT_READY, ''),
        ]
        self.assertEqual(overall_exit_code(results), EXIT_BLOCKED)

    def test_unknown_is_not_folded_into_pass(self) -> None:
        results = [AxisResult('a', VERDICT_READY, ''), AxisResult('b', VERDICT_UNKNOWN, '')]
        self.assertEqual(
            overall_exit_code(results), EXIT_UNKNOWN,
            '미확인을 통과와 같은 코드로 접으면 이 점검은 아무것도 하지 않으면서 초록으로 보인다',
        )

    def test_all_ready_is_zero(self) -> None:
        self.assertEqual(overall_exit_code([AxisResult('a', VERDICT_READY, '')]), EXIT_READY)


class TestEachAxisBlocksOnItsDefectShape(unittest.TestCase):
    """초록만 보지 않는다 — 각 축에 결함 모양을 먹인다."""

    def test_machine_blocks_on_the_development_pc(self) -> None:
        result = judge_machine('명준진경', 'SUW0521PC1WNBRE')
        self.assertEqual(result.verdict, VERDICT_BLOCKED)
        self.assertIn('명준진경', result.detail)

    def test_machine_is_unknown_when_the_name_cannot_be_read(self) -> None:
        self.assertEqual(judge_machine(None, 'X').verdict, VERDICT_UNKNOWN)

    def test_machine_passes_on_central(self) -> None:
        self.assertEqual(judge_machine('SUW0521PC1WNBRE', 'SUW0521PC1WNBRE').verdict, VERDICT_READY)

    def test_checkout_blocks_when_the_layout_record_is_not_a_box_marker(self) -> None:
        """이미지는 pip 설치 뒤 pyproject.toml 을 지운다 — 그 판이 컨테이너에서 죽는다."""
        result = judge_checkout(['pyproject.toml'])
        self.assertEqual(result.verdict, VERDICT_BLOCKED)
        self.assertIn('exit 2', result.detail)

    def test_checkout_passes_with_both_markers(self) -> None:
        self.assertEqual(
            judge_checkout(['pyproject.toml', '.extraction-layout.json']).verdict, VERDICT_READY)


class TestTheCheckoutAxisAnswersBeforeAnythingIsInstalled(unittest.TestCase):
    '''⚠️ 이 도구의 «대상»은 설치 전이다.

    중앙 세션은 `git pull` 직후 맨 `python3` 로 이것을 돌린다. 그때
    `fcc_test_platform.repository_anchor` 는 import 되지 않는다 — 그 모듈이
    `fcc_test_contracts` 에서 상수를 가져오는데 그 배포판이 없기 때문이다.
    실측 2026-09-06: 그 상태에서 이 축이 UNKNOWN 이었고, **하필 그 축이 exit 2 를
    막아 주는 축이다.** 도구가 가장 필요한 순간에 가장 값진 축이 침묵하면 없는 것과 같다.
    '''

    def test_the_source_reader_finds_the_marker_symbol(self) -> None:
        markers = _box_markers_from_source()
        self.assertIsNotNone(markers, 'repository_anchor.py 에서 BOX_MARKERS 를 못 읽었다')
        self.assertIn(
            'LAYOUT_RECORD_NAME', markers,
            '소스 모드는 «이름»을 낸다 — 값을 여기서 해소하면 두 SSOT 가 된다',
        )

    def test_source_mode_is_accepted_by_the_judge(self) -> None:
        result = judge_checkout(['pyproject.toml', 'LAYOUT_RECORD_NAME'], 'source')
        self.assertEqual(result.verdict, VERDICT_READY)
        self.assertIn('source', result.detail, '무엇을 쟀는지 감추지 않는다')

    def test_source_mode_still_blocks_on_the_defect_shape(self) -> None:
        result = judge_checkout(['pyproject.toml'], 'source')
        self.assertEqual(result.verdict, VERDICT_BLOCKED)
        self.assertIn('source', result.detail)

    def test_installed_mode_is_labelled_as_such(self) -> None:
        markers, how = collect_box_markers()
        self.assertEqual(how, 'installed', '이 시험은 설치된 환경에서 돈다')
        self.assertIn('.extraction-layout.json', markers)

    def test_both_modes_agree_on_this_tree(self) -> None:
        '''두 모드가 같은 답을 내야 한다 — 아니면 하나가 거짓말한다.'''
        installed, _ = collect_box_markers()
        source = _box_markers_from_source()
        self.assertEqual(
            judge_checkout(installed, 'installed').verdict,
            judge_checkout(source, 'source').verdict,
        )

    def test_refusal_guard_blocks_and_names_the_projects(self) -> None:
        result = judge_refusal_guard(1, "SM-X (customer='가', applicant_name='나')", True)
        self.assertEqual(result.verdict, VERDICT_BLOCKED)
        self.assertIn('SM-X', result.detail)

    def test_refusal_guard_is_ready_once_the_column_is_gone(self) -> None:
        """032 적용 뒤에는 그 칸이 없다 — 실패가 아니라 지난 관문이다."""
        self.assertEqual(judge_refusal_guard(None, '', False).verdict, VERDICT_READY)

    def test_refusal_guard_is_unknown_when_the_query_failed(self) -> None:
        self.assertEqual(judge_refusal_guard(None, '', True).verdict, VERDICT_UNKNOWN)

    def test_ledger_blocks_on_a_drift_that_is_not_the_declared_exception(self) -> None:
        result = judge_ledger({'drift': ['017_artifact_custody: checksum …'], 'pending': []})
        self.assertEqual(result.verdict, VERDICT_BLOCKED)
        self.assertIn('reconcile 하지 마라', result.detail)

    def test_ledger_accepts_the_declared_exception(self) -> None:
        result = judge_ledger({'drift': [f'{RECONCILABLE}: checksum …'], 'pending': ['031_x']})
        self.assertEqual(result.verdict, VERDICT_READY)
        self.assertIn('031_x', result.detail)

    def test_stop_list_keeps_postgres_running_and_finds_every_api_service(self) -> None:
        """⚠️ 런북 §4-a ② 가 빠뜨리는 `platform-api-node` 를 파생으로 잡는다."""
        result = judge_stop_list({
            'postgres': 'postgres:16-alpine',
            'central-migrate': 'fcc-central-platform-api:latest',
            'keycloak': 'quay.io/keycloak/keycloak:25.0',
            'platform-api': 'fcc-central-platform-api:latest',
            'platform-api-node': 'fcc-central-platform-api:latest',
            'headless-api': 'fcc-unlicensed-headless-api:latest',
            'web': 'fcc-central-web:latest',
        })
        self.assertEqual(result.verdict, VERDICT_READY)
        self.assertIn('platform-api-node', result.detail)
        self.assertNotIn('stop postgres', result.detail)
        self.assertNotIn('central-migrate', result.detail)

    def test_stop_list_is_unknown_without_compose(self) -> None:
        self.assertEqual(judge_stop_list(None).verdict, VERDICT_UNKNOWN)

    def test_deploy_class_names_the_stop_window_files(self) -> None:
        out = (
            '031_project_applicant_search_axis.sql        ONLINE\n'
            '032_retire_project_customer_column.sql       STOP-WINDOW · CAN-REFUSE\n'
            '033_drop_write_only_project_columns.sql      STOP-WINDOW\n'
        )
        result = judge_deploy_class(out)
        self.assertEqual(result.verdict, VERDICT_READY)
        self.assertIn('032_retire_project_customer_column.sql', result.detail)
        self.assertIn('033_drop_write_only_project_columns.sql', result.detail)
        self.assertNotIn('031_project_applicant_search_axis.sql', result.detail.split('거부')[0])

    def test_deploy_class_is_unknown_when_the_deriver_did_not_run(self) -> None:
        self.assertEqual(judge_deploy_class(None).verdict, VERDICT_UNKNOWN)


if __name__ == '__main__':
    unittest.main()
