from __future__ import annotations

import json

import pytest

from fcc_test_platform import bench_project_result_selection_cli as benchmark


def test_benchmark_fixture_has_exact_contract_cardinality_and_edge_cases():
    assert benchmark.PROJECT_COUNT == 1
    assert benchmark.PROVIDER_COUNT == 2
    assert benchmark.CONDITIONS_PER_PROVIDER == 16_000
    assert benchmark.ATTEMPTS_PER_CONDITION == 3
    assert benchmark.SESSIONS_PER_PROVIDER == 100
    assert benchmark.MANUAL_PIN_RATIO == pytest.approx(0.10)
    assert benchmark.EXPECTED_ATTEMPTS == 96_000
    assert benchmark.EXPECTED_MANUAL_PINS == 3_200
    assert benchmark.P95_RATIO_LIMIT == pytest.approx(1.5)
    assert {
        'incomplete_attempt_count',
        'equal_timestamp_attempt_count',
        'null_timestamp_attempt_count',
        'out_of_order_timestamp_attempt_count',
        'replay_case_count',
    }.issubset(benchmark.SeedManifest.__dataclass_fields__)


def test_benchmark_queries_are_keyset_or_bounded_and_emit_json_explain_contract():
    query_sources = (
        benchmark.BASELINE_EFFECTIVE_RESULTS_QUERY_SQL,
    )
    from fcc_test_platform.application.central_result_selection_adapter import (
        CANDIDATE_ATTEMPTS_QUERY_SQL,
        EFFECTIVE_RESULTS_QUERY_SQL,
    )

    query_sources += (CANDIDATE_ATTEMPTS_QUERY_SQL, EFFECTIVE_RESULTS_QUERY_SQL)
    assert all('OFFSET' not in query.upper() for query in query_sources)
    assert all('LIMIT %S' in query.upper() for query in query_sources)
    assert any(
        'EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)' in str(constant)
        for constant in benchmark._explain.__code__.co_consts
    )
    assert 'p95_us' in benchmark._budget_result({
        'effective_page:provider': {'p95_us': 1.0},
    })['effective_page:provider']['budget']['metric']


def test_benchmark_ratio_receipt_requires_same_host_baseline():
    samples = {
        'effective_page:provider-a': {'p95_us': 120.0},
        'baseline_effective_page:provider-a': {'p95_us': 100.0},
    }
    receipt = benchmark._p95_ratios(samples)
    assert receipt['provider-a'] == {
        'feature_p95_us': 120.0,
        'baseline_p95_us': 100.0,
        'ratio': 1.2,
        'limit': 1.5,
        'within_ratio': True,
    }


SHA = '2169dc28af33bb234f3841a379ae9355fb222550'


def _bound_receipt() -> dict:
    """The shape a real passing run writes — nothing more, nothing less."""
    return {
        'status': 'PASS',
        'code_cutoff': SHA,
        'repository': {
            'head': SHA,
            'requested_cutoff': SHA,
            'cutoff_matches_head': True,
            'clean': True,
            'status_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            'status_entries': 0,
        },
        'command': 'python scripts/bench_project_result_selection.py --seed --explain',
        'started_at': '2026-08-26T07:28:28+00:00',
        'completed_at': '2026-08-26T07:29:02+00:00',
        'fixture': {'expected_attempts': 96_000, 'no_offset': True},
        'seed': {'attempt_count': 96_000},
        'samples': {'effective_page:a': {'p95_us': 1.0}},
        'latency_budgets': {'effective-page': {'within_budget': True}},
        'p95_ratios': {'a': {'ratio': 1.11, 'within_ratio': True}},
        'cleanup': {'status': 'PASS', 'remaining_rows': {}},
    }


def test_the_reviewers_fabricated_receipt_is_rejected():
    """The counterexample lives in the tree, not in a review comment.

    The first version of this predicate asked only for a 40-character
    `code_cutoff` and two booleans. An independent reviewer showed that the
    object below — which contains no measurement whatsoever — satisfied it, and
    that the test of the day *asserted* that shape was acceptable. The predicate
    was checking the label rather than the thing.
    """
    fabricated = {
        'code_cutoff': 'a' * 40,
        'repository': {'cutoff_matches_head': True, 'clean': True},
    }

    assert benchmark.receipt_binds_cutoff(fabricated) is False


def test_every_clause_of_the_binding_predicate_is_load_bearing():
    """Mutate one field at a time; the loop is derived from the required set.

    Deriving the field list from the module constant means a field added to
    `_BOUND_RECEIPT_REQUIRED_FIELDS` tomorrow is mutated the day it exists,
    rather than waiting for someone to remember to extend this test.
    """
    good = _bound_receipt()
    assert benchmark.receipt_binds_cutoff(good) is True

    for field in benchmark._BOUND_RECEIPT_REQUIRED_FIELDS:
        missing = {k: v for k, v in good.items() if k != field}
        assert benchmark.receipt_binds_cutoff(missing) is False, (
            f'{field} was declared required but dropping it still bound the cutoff'
        )

    for mutation, why in (
        ({'code_cutoff': 'UNFROZEN'}, 'an unfrozen run names no SHA'),
        ({'code_cutoff': SHA[:-1]}, 'a truncated SHA is not a SHA'),
        ({'code_cutoff': 'Z' * 40}, 'a 40-character non-hex string is not an object id'),
        ({'status': 'FAIL'}, 'a failed run measured something, but not a passing budget'),
        ({'status': 'BLOCKED'}, 'a blocked run measured nothing'),
        ({'cleanup': {'status': 'FAIL'}}, 'rows left behind make the next run untrustworthy'),
        ({'p95_ratios': {}}, 'a verdict derived over no ratio is not a verdict'),
        ({'p95_ratios': {'a': {'ratio': 9.9, 'within_ratio': False}}}, 'the budget was exceeded'),
        ({'repository': dict(good['repository'], cutoff_matches_head=False)},
         'the tree was at some other commit'),
        ({'repository': dict(good['repository'], clean=False)},
         'uncommitted work was in the measured tree'),
        ({'repository': dict(good['repository'], head='b' * 40)},
         'the head it reports is not the cutoff it claims'),
        ({'repository': {'cutoff_matches_head': True, 'clean': True}},
         'two bare booleans are not a repository observation'),
        ({'repository': 'clean, honest'}, 'a sentence is not a repository state'),
    ):
        assert benchmark.receipt_binds_cutoff({**good, **mutation}) is False, why


def test_the_blocked_receipt_still_records_its_provenance(tmp_path, monkeypatch):
    """A refused run is also a fact about a tree, and says so.

    If provenance were only written on the success path, a BLOCKED receipt would
    be unattributable — which is precisely when knowing the tree matters most.
    """
    monkeypatch.delenv('FCC_CENTRAL_DB_BENCHMARK_URL', raising=False)
    receipt_path = tmp_path / 'blocked.json'

    benchmark.main(['--seed', '--json-output', str(receipt_path)])

    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    assert receipt['status'] == 'BLOCKED'
    assert set(receipt['repository']) >= {
        'head', 'requested_cutoff', 'cutoff_matches_head', 'clean',
        'status_sha256', 'status_entries',
    }
    assert receipt['command'].startswith('python scripts/bench_project_result_selection.py')
    assert receipt['started_at'] and receipt['completed_at']
    assert receipt['binds_cutoff'] is False  # no --cutoff was supplied


def test_the_receipt_command_never_carries_the_dsn():
    """The receipt is committed; the DSN is a credential."""
    secret = 'postgresql://user:hunter2@127.0.0.1:5999/bench'
    rendered = benchmark._redacted_command(
        ['--dsn', secret, '--seed', '--explain'], dsn=secret,
    )

    assert 'hunter2' not in rendered
    assert secret not in rendered
    assert '<redacted-dsn>' in rendered
    assert '--seed' in rendered and '--explain' in rendered


def test_missing_benchmark_infrastructure_is_a_blocked_receipt(tmp_path, monkeypatch):
    monkeypatch.delenv('FCC_CENTRAL_DB_BENCHMARK_URL', raising=False)
    receipt_path = tmp_path / 'project-result-selection-benchmark.json'

    exit_code = benchmark.main([
        '--seed', '--explain', '--json-output', str(receipt_path),
    ])

    assert exit_code == 2
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    assert receipt['status'] == 'BLOCKED'
    assert receipt['fixture']['expected_attempts'] == 96_000
    assert 'blocked_reason' in receipt
    assert 'FCC_CENTRAL_DB_BENCHMARK_URL' in receipt['blocked_reason']
