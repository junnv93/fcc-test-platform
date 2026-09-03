from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcc_test_platform.application.central_project_reference_adapter import (
    PostgresCentralProjectReferenceAdapter,
)
from fcc_test_kernel.domain.models.project_result_reference import canonical_payload_hash
from scripts import cross_session_result_selection_evidence as evidence


def test_evidence_runner_requires_both_independent_lanes_and_never_skips_missing_dsns(tmp_path, monkeypatch):
    output = tmp_path / 'receipt.json'
    monkeypatch.delenv(evidence.FRESH_ENV, raising=False)
    monkeypatch.delenv(evidence.UPGRADE_ENV, raising=False)

    exit_code = evidence.main(['--json-output', str(output)])

    assert exit_code == 2
    receipt = json.loads(output.read_text(encoding='utf-8'))
    assert receipt['status'] == 'BLOCKED'
    assert receipt['writes'] == (
        'disposable PostgreSQL migration application plus protected-data, CAS, '
        'publication, retirement, and snapshot-ingestion proof'
    )
    assert receipt['lanes']['fresh']['status'] == 'BLOCKED'
    assert receipt['lanes']['upgrade']['status'] == 'BLOCKED'
    assert 'silently skipped' in receipt['lanes']['fresh']['blocked_reason']
    assert receipt['migration']['migration_id'] == evidence.EXPECTED_MIGRATION


def test_fresh_lane_is_collected_when_upgrade_lane_is_unavailable(tmp_path, monkeypatch):
    output = tmp_path / 'receipt.json'
    calls: list[tuple[str, str]] = []
    seam_arguments: list[tuple[str | None, str]] = []

    # Mirror the production keyword-only seam. The defaults preserve the
    # historical two-argument callable contract for direct test callers while
    # making main()'s receipt context forwarding explicit and type-safe.
    def fake_lane(
        dsn: str,
        lane: str,
        *,
        cutoff: str | None = None,
        command: str = '',
    ) -> dict[str, str]:
        calls.append((dsn, lane))
        seam_arguments.append((cutoff, command))
        return {'lane': lane, 'status': 'PASS'}

    monkeypatch.setattr(evidence, '_lane', fake_lane)
    exit_code = evidence.main([
        '--fresh-dsn', 'postgresql://fresh.example/db',
        '--json-output', str(output),
    ])

    receipt = json.loads(output.read_text(encoding='utf-8'))
    assert exit_code == 2
    assert calls == [('postgresql://fresh.example/db', 'fresh')]
    assert seam_arguments[0][0] is None
    assert seam_arguments[0][1].startswith(
        'python scripts/cross_session_result_selection_evidence.py '
    )
    assert receipt['lanes']['fresh']['status'] == 'PASS'
    assert receipt['lanes']['upgrade']['status'] == 'BLOCKED'
    assert receipt['status'] == 'BLOCKED'


def test_evidence_receipts_redact_password_and_require_snapshot_columns():
    rendered = evidence._safe_dsn('postgresql://operator:secret@example.test:5432/fcc')
    assert 'secret' not in rendered
    assert 'example.test' in rendered
    assert evidence.EXPECTED_COLUMNS['test_sessions'] == {
        'project_result_reference_snapshot_json',
        'project_result_reference_snapshot_schema_version',
    }


def test_every_predicate_votes_so_a_later_assertion_cannot_be_forgotten():
    """The verdict is derived over the mapping, never a hand-kept enumeration.

    The mutation is applied one key at a time and the loop is derived from the
    mapping itself, so an assertion added tomorrow is covered the day it exists
    — which is exactly what the old hand-written eleven-term conjunction could
    not promise.
    """
    healthy = {
        'migration_ledger_row_present': True,
        'migration_rerun_noop': True,
        'reference_snapshot_columns_present': True,
        'selection_table_present.project_result_selection_events': True,
        'live_selection_snapshot_proof': True,
    }
    assert evidence._verdict(healthy) == 'PASS'

    for name in healthy:
        mutated = dict(healthy, **{name: False})
        assert evidence._verdict(mutated) == 'FAIL', (
            f'{name} was written into the receipt but did not reach the verdict'
        )


def test_a_diagnostic_cannot_vote_because_it_would_invert_the_verdict():
    """`missing_columns` is the reason the naive repair was worse than the defect.

    It is `{}` when the schema is healthy (falsy) and populated when it is broken
    (truthy), so folding it into `all(...)` reads a good tree as FAIL and a bad
    one as PASS. The type boundary refuses it before it can answer.
    """
    for diagnostic in ({}, {'test_sessions': ['project_result_reference_snapshot_json']}):
        with pytest.raises(TypeError) as excinfo:
            evidence._verdict({'reference_snapshot_columns_present': True,
                               'missing_columns': diagnostic})
        assert 'missing_columns' in str(excinfo.value)
        assert 'diagnostics do not vote' in str(excinfo.value)


def test_the_runner_keeps_the_diagnostic_out_of_the_predicate_mapping():
    """Non-emptiness for the two checks above — they must describe real receipts.

    A predicate mapping that never contains a diagnostic makes the type boundary
    vacuous, so this asserts the runner actually routes `missing_columns` to the
    diagnostics slot and that the slot is the only home it has.
    """
    source = Path(evidence.__file__).read_text(encoding='utf-8')
    assert "receipt['diagnostics'] = {'missing_columns': missing}" in source
    assert "'missing_columns': missing,\n" not in source.split('_verdict(')[0]


def test_an_empty_predicate_set_cannot_answer_pass():
    """A verdict derived over nothing is the quietest way to report success."""
    with pytest.raises(ValueError):
        evidence._verdict({})


def test_snapshot_ingestion_assertions_use_top_level_conflict_warning_receipt():
    snapshot = '{"project_id":"project-1"}'
    ingest = {
        'first': {'committed': True},
        'retry': {'committed': True},
        'conflict': {
            'committed': True,
            # The worker result has no warning field; the evidence wrapper owns
            # the observation after capturing the writer log.
            'conflict_warning_observed': False,
        },
        'conflict_warning_observed': True,
    }

    assertions = evidence._snapshot_ingestion_assertions(
        ingest, stored_json=snapshot, snapshot_json=snapshot,
    )

    assert assertions == {
        'first_snapshot_committed': True,
        'identical_retry_committed': True,
        'conflicting_replay_preserved_first_bytes': True,
    }


class _SessionIdentityCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._row = (
            'provider-uuid',
            'seeded-provider-session',
            'seeded-chamber',
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((sql, params))

    def fetchone(self):
        return self._row


class _SessionIdentityConnection:
    def __init__(self) -> None:
        self.cursor_instance = _SessionIdentityCursor()
        self.closed = False

    def cursor(self) -> _SessionIdentityCursor:
        return self.cursor_instance

    def close(self) -> None:
        self.closed = True


def test_snapshot_ingestion_binds_to_existing_central_session_parent(monkeypatch):
    connection = _SessionIdentityConnection()
    monkeypatch.setattr(evidence, '_connect', lambda _dsn: connection)

    natural_key = evidence._read_session_ingestion_identity(
        'postgresql://disposable.example/fcc',
        {'session_a': 'session-1', 'provider_uuid': 'provider-uuid'},
    )

    assert natural_key == ('seeded-provider-session', 'seeded-chamber')
    assert connection.closed is True
    assert connection.cursor_instance.calls == [
        (
            'SELECT provider_id, provider_session_id, chamber_id '
            'FROM test_sessions WHERE id = %s',
            ('session-1',),
        ),
    ]


class _ReferencePublishCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self._rows: list[tuple[object, ...]] = []

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.calls.append((sql, params))
        if sql.startswith('SET TRANSACTION') or 'pg_advisory_xact_lock' in sql:
            self._rows = []
        elif 'SELECT id, provider_id FROM providers' in sql:
            self._rows = [('11111111-1111-4111-8111-111111111111', 'provider-natural')]
        elif 'FROM project_result_selection_events e' in sql:
            self._rows = [
                (
                    'event-1', 'selected', 'attempt-1', 'project-1',
                    'provider-natural', 'condition-1', 'session-1', None,
                    'chamber-1', 'completed',
                ),
            ]
        elif 'SELECT COALESCE(MAX(revision_number)' in sql:
            self._rows = [(1,)]
        elif sql.startswith('INSERT INTO project_result_reference_revisions'):
            self._rows = []
        else:  # pragma: no cover - catches an accidental query-shape drift
            raise AssertionError(f'unexpected reference publish query: {sql}')

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows

    def close(self) -> None:
        pass


class _ReferencePublishConnection:
    def __init__(self) -> None:
        self.cursor_instance = _ReferencePublishCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _ReferencePublishCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        pass


def test_reference_publish_translates_natural_provider_key_before_uuid_event_query():
    from psycopg.types.json import Jsonb

    payload = {'condition': 'condition-1'}
    connection = _ReferencePublishConnection()
    adapter = PostgresCentralProjectReferenceAdapter(lambda: connection)
    result = adapter.publish_reference({
        'id': 'revision-1',
        'project_id': 'project-1',
        'producer_provider_id': 'provider-natural',
        'reference_type': 'fcc.evidence.reference',
        'schema_version': '1.0',
        'source_selection_event_id': 'event-1',
        'condition_hash': 'condition-1',
        'payload_json': payload,
        'content_sha256': canonical_payload_hash(payload),
        'state': 'published',
        'created_by': 'operator-1',
        'created_at': '2026-08-26T00:00:00+00:00',
    })

    source_calls = [
        params for sql, params in connection.cursor_instance.calls
        if 'FROM project_result_selection_events e' in sql
    ]
    assert source_calls == [
        (
            'event-1', 'project-1',
            '11111111-1111-4111-8111-111111111111', 'condition-1',
        ),
    ]
    insert_calls = [
        params for sql, params in connection.cursor_instance.calls
        if sql.startswith('INSERT INTO project_result_reference_revisions')
    ]
    assert len(insert_calls) == 1
    assert isinstance(insert_calls[0][10], Jsonb)
    assert insert_calls[0][10].obj == payload
    assert result['producer_provider_id'] == 'provider-natural'
    assert result['payload'] == payload
    assert result['content_sha256'] == canonical_payload_hash(payload)
    assert connection.commits == 1


# --------------------------------------------------------------------- manifest
#
# ⚠️ **이 축이 왜 생겼는가.** MUST-9 의 manifest 는 모든 receipt 을 하나의 불변 SHA 에
# 결박하는 아티팩트인데, 2026-08-26 까지 그것을 **만드는 코드가 한 줄도 없었다**. 두 개의
# manifest 가 손으로 쓰여 있었고 둘 다 ``schema_version: 1`` 을 선언하면서 **스키마가 서로
# 완전히 달랐다**(``receipts`` vs ``evidence_artifacts``). 그것이 손 저작의 직접 증거다 —
# 같은 버전 번호를 붙인 두 문서가 다른 모양일 수 있는 이유는 아무도 읽지 않기 때문이다.
#
# 아래 검사는 세 가지를 묻는다: 결박 대상이 **디렉터리에서 파생**되는가(선언 누락으로
# 빠뜨릴 수 없는가), manifest 가 **자기를 해시하지 않는가**, 그리고 **손 편집이 red 인가**.


def _receipt_dir(root: Path, cutoff: str, names=('alpha.json', 'beta.json')) -> Path:
    directory = root / cutoff
    directory.mkdir(parents=True)
    for name in names:
        (directory / name).write_text(
            json.dumps({'status': 'PASS', 'code_cutoff': cutoff}) + '\n', encoding='utf-8')
    return directory


#: A cutoff label for trees that cannot answer ``git rev-parse``. Forty hex
#: characters because that is the shape every consumer of a cutoff expects, and
#: a constant because a *label* is all these fixtures need — none of them
#: asserts that it equals this tree's HEAD, they assert that a manifest is
#: internally consistent with whatever cutoff it was built at.
_SYNTHETIC_CUTOFF = 'de11e7ed0000000000000000000000000000ba51'


def _head_cutoff() -> str:
    """A usable cutoff label in *any* tree — total by construction.

    ⚠️ **This returned ``''`` in a delivered box and that emptied a path, not an
    assertion.** ``_receipt_dir`` builds ``root / cutoff``; with an empty cutoff
    that is ``root`` itself, which pytest's ``tmp_path`` has already created, so
    ``mkdir(parents=True)`` raised ``FileExistsError`` before a single assertion
    in these tests ever ran. Thirty call sites, one empty string.

    Making the label total is the repair rather than guarding every caller,
    because a guard is a second thing to remember and the next call site is the
    one nobody guards. The real observation is asserted where it belongs — in
    :func:`test_the_default_observation_really_reads_git`, which now asks the
    question separately for a tree that has git and a tree that does not.
    """
    observation = evidence.repository_metadata(None)
    return observation['head'] if observation['repository_observed'] else _SYNTHETIC_CUTOFF


def _clean_observation(cutoff: str) -> dict:
    """깨끗한 트리 관측. **주입**하므로 이 검사들이 워킹 트리 상태에 의존하지 않는다."""
    return {
        'head': cutoff,
        'requested_cutoff': cutoff,
        'cutoff_matches_head': True,
        'clean': True,
        'status_sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
        'status_entries': 0,
    }


#: 실제 manifest 는 **언제나** 게이트를 싣는다 — MUST-9 가 각 명령·시작/종료·종료 코드를
#: 요구한다. 게이트 없는 픽스처로 다른 축을 시험하면 그 검사는 자기 명제가 아니라 게이트
#: 요건에 먼저 걸리고, 그러면 시험 대상 자물쇠가 고립되지 않는다.
_GATES = {
    'lane:routine': {
        'command': 'python3 scripts/run_test_lanes.py routine',
        'exit': 1,
        'started_at': '2026-08-27T00:00:00+00:00',
        'ended_at': '2026-08-27T00:06:00+00:00',
    },
}


def _write_gates(directory, cutoff, gates=None, limitations=()):
    """게이트를 **형제 영수증**으로 기록한다 — manifest 인자가 아니라.

    ⚠️ 인자로 실으면 그 값은 manifest 의 *입력* 이 되고, 입력은 재파생이 그대로 되먹이므로
    **어떤 값이든 자기 자신과 일치한다**(2026-08-27 독립 검토 실측). 그래서 이 웨이브는
    게이트를 증거 디렉터리 안으로 옮겼고, 픽스처도 프로덕션과 같은 경로를 지나야 한다.
    """
    evidence._write(str(directory / evidence.GATES_RECEIPT_NAME), {
        'receipt': 'gate-run',
        'schema_version': 1,
        'status': 'RECORDED',
        'code_cutoff': cutoff,
        'production_cutover': 'NOT_READY',
        'gates': _GATES if gates is None else gates,
        'limitations': list(limitations),
    })


def _build(directory, cutoff, gates=None, limitations=(), **kwargs):
    _write_gates(directory, cutoff, gates=gates, limitations=limitations)
    return evidence.build_manifest(
        directory, cutoff=cutoff, repository=_clean_observation(cutoff), **kwargs)


def test_the_default_observation_really_reads_git(tmp_path):
    """주입이 기본값을 대체하지 않는다는 것을 단언한다 — 주입 가능한 관측의 대표 실패 모드.

    관측을 인자로 뺀 뒤 **기본값이 상수가 되어 버리면** 프로덕션 경로는 트리를 보지 않는다.

    ⚠️ **이 검사는 ``len(head) == 40`` 하나였고 그것은 납품 상자에서 구조적으로 거짓이다** —
    상자는 git 저장소가 아니다. 그렇다고 «git 이 없으면 건너뛴다» 로 바꾸면 monorepo 에서
    git 이 깨졌을 때도 조용히 통과한다(이 저장소가 이미 이름 붙인 «skip 은 failure 보다
    조용하다»). 그래서 **양쪽 트리에 각각 참인 명제**로 나눈다: 관측이 됐으면 40-hex HEAD 를
    실제로 읽었어야 하고, 관측이 안 됐으면 그 사실이 **``clean`` 을 참으로 만들지 않아야**
    한다. 두 분기 모두 단언을 갖고, 어느 쪽도 «아무것도 검사하지 않음» 이 아니다.
    """
    observed = evidence.repository_metadata(None)
    assert set(observed) >= {
        'head', 'clean', 'cutoff_matches_head', 'status_entries', 'repository_observed',
    }
    if observed['repository_observed']:
        assert len(observed['head']) == 40, observed
        assert int(observed['head'], 16) >= 0, observed
    else:
        # 관측 불가는 «깨끗함» 이 아니다. 이 한 줄이 없으면 읽을 수 없는 트리가
        # clean: true 로 증언한다 — 프로덕션에서 실제로 도달 가능했던 경로다.
        assert observed['head'] == '', observed
        assert observed['clean'] is False, observed


def test_an_unobservable_tree_does_not_attest_that_it_is_clean(tmp_path):
    """위 검사의 else 분기를 **실제로 발화시켜** 봉인이 공허하지 않음을 보인다.

    monorepo 에서 회귀를 돌리면 위 검사는 언제나 if 분기로 간다. 그러면 else 분기는
    한 번도 실행되지 않고, 실행되지 않은 단언은 결함 코드에서도 초록이다. 여기서는
    ``ROOT`` 를 git 이 없는 디렉터리로 바꿔 그 분기를 강제한다.
    """
    monkey = tmp_path / 'not-a-git-tree'
    monkey.mkdir()
    original = evidence.ROOT
    before = evidence.repository_metadata(None)
    try:
        evidence.ROOT = monkey
        observed = evidence.repository_metadata(None)
    finally:
        evidence.ROOT = original
    assert observed['repository_observed'] is False, observed
    assert observed['head'] == '', observed
    assert observed['clean'] is False, observed
    # 되돌린 뒤 답이 원래대로 돌아온다 — 위 조작이 영구적이지 않음을 같은 검사가 말한다.
    #
    # ⚠️ **여기서 ``is True`` 를 단언하면 안 된다.** 이 파일은 **납품 상자 안에서도**
    # 돌고 그 상자는 git 저장소가 아니다 — 즉 «원래 답» 이 False 인 트리가 실재한다.
    # 초판이 그렇게 적었다가 상자에서 그 한 줄만 red 였다: 「답이 어느 트리가 묻느냐에
    # 달렸다」는, 이 웨이브가 고치고 있는 바로 그 형태다. 비교 대상은 상수가 아니라
    # **조작 전의 답** 이다.
    assert evidence.repository_metadata(None)['repository_observed'] is before[
        'repository_observed'
    ]


def test_the_manifest_binds_every_sibling_the_directory_actually_contains(tmp_path):
    """결박 대상은 **선언이 아니라 디렉터리**다 — 적지 않는 방법으로 빠뜨릴 수 없다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)

    manifest = evidence.build_manifest(directory, cutoff=cutoff)

    # 비-공허성: 정말로 형제를 보고 있는가.
    assert set(manifest['artifacts']) == {'alpha.json', 'beta.json'}
    assert manifest['artifact_count'] == 2
    assert all(entry['sha256'] for entry in manifest['artifacts'].values())

    # 형제가 하나 늘면 결박 대상도 는다 — 손 목록이라면 늘지 않는다.
    (directory / 'gamma.json').write_text(
        json.dumps({'status': 'PASS', 'code_cutoff': cutoff}) + '\n', encoding='utf-8')
    assert set(evidence.build_manifest(directory, cutoff=cutoff)['artifacts']) == {
        'alpha.json', 'beta.json', 'gamma.json'}


def test_the_manifest_never_hashes_itself(tmp_path):
    """자기 참조는 계산 불가다. 결박은 evidence 커밋/트리가 한다(계약 MUST-9)."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    (directory / evidence.MANIFEST_NAME).write_text('{"stale": true}\n', encoding='utf-8')

    manifest = evidence.build_manifest(directory, cutoff=cutoff)

    assert evidence.MANIFEST_NAME not in manifest['artifacts']
    assert manifest['manifest_self_hash'] == 'EXCLUDED_BY_CONSTRUCTION'


def test_a_receipt_that_names_another_cutoff_cannot_be_bound_silently(tmp_path):
    """다른 SHA 에서 나온 receipt 을 모아 담는 것이 이 축의 대표 실패 모드다.

    머지 한 번이면 SHA 가 바뀌고, 그때 옛 receipt 의 이름만 고치는 것은 **위조**다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    (directory / 'stale.json').write_text(
        json.dumps({'status': 'PASS', 'code_cutoff': 'f' * 40}) + '\n', encoding='utf-8')

    manifest = _build(directory, cutoff)

    assert manifest['status'] == 'BLOCKED'
    assert manifest['artifacts']['stale.json']['binds_cutoff'] is False
    assert any('stale.json' in finding and 'names cutoff' in finding
               for finding in manifest['findings']), manifest['findings']
    # 그리고 결박된 형제는 여전히 True 다 — 판정이 전부를 뭉개지 않는다.
    assert manifest['artifacts']['alpha.json']['binds_cutoff'] is True


def test_the_manifest_status_is_derived_not_declared(tmp_path):
    """상태는 형제 상태 + 트리 상태에서 파생된다. 손으로 PASS 를 적을 자리가 없다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    assert _build(directory, cutoff)['status'] == 'PASS'

    (directory / 'beta.json').write_text(
        json.dumps({'status': 'BLOCKED', 'code_cutoff': cutoff}) + '\n', encoding='utf-8')
    blocked = _build(directory, cutoff)
    assert blocked['status'] == 'BLOCKED'
    assert any('beta.json' in finding and 'BLOCKED' in finding
               for finding in blocked['findings']), blocked['findings']


def test_an_empty_evidence_directory_cannot_answer_pass(tmp_path):
    """빈 컬렉션을 순회한 단언은 언제나 통과한다 — 이 저장소가 반복해 이름 붙인 형태."""
    cutoff = _head_cutoff()
    directory = tmp_path / cutoff
    directory.mkdir(parents=True)

    # ⚠️ 게이트 형제도 쓰지 않는다 — 그것을 쓰면 디렉터리가 더 이상 비어 있지 않아
    # 이 검사가 자기 명제(«빈 컬렉션») 를 잃는다.
    manifest = evidence.build_manifest(
        directory, cutoff=cutoff, repository=_clean_observation(cutoff))

    assert manifest['status'] == 'BLOCKED'
    assert manifest['artifact_count'] == 0
    assert any('no receipt to bind' in finding for finding in manifest['findings'])


def test_a_directory_not_named_by_its_cutoff_cannot_answer_pass(tmp_path):
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, 'not-a-sha')
    manifest = _build(directory, cutoff)
    assert manifest['status'] == 'BLOCKED'
    assert any('basename' in finding for finding in manifest['findings'])


@pytest.mark.parametrize(
    'tamper, expected_fragment',
    [
        ('edit_a_receipt', 'sha256 does not match'),
        ('add_a_receipt', 'present on disk but absent'),
        ('remove_a_receipt', 'declared by the manifest but absent'),
        ('claim_a_different_cutover', 'production_cutover'),
        ('hash_itself', 'hashes itself'),
    ],
)
def test_a_hand_edited_manifest_is_red(tmp_path, tamper, expected_fragment):
    """**손 편집이 red 여야 이 축이 존재할 이유가 있다.**

    각 변이는 적용 뒤 실제로 검증기를 태워 판정한다 — 철자를 묻지 않고 효과를 묻는다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    manifest_path = _written_manifest(directory, cutoff)

    # 변이 이전에 **정말로 통과하는지** 먼저 단언한다. 그러지 않으면 red 가 변이 때문인지
    # 픽스처가 애초에 깨졌기 때문인지 구분할 수 없다.
    assert evidence.validate_manifest(directory)['status'] == 'PASS'

    if tamper == 'edit_a_receipt':
        (directory / 'alpha.json').write_text('{"status": "PASS", "extra": 1}\n',
                                              encoding='utf-8')
    elif tamper == 'add_a_receipt':
        (directory / 'delta.json').write_text('{"status": "PASS"}\n', encoding='utf-8')
    elif tamper == 'remove_a_receipt':
        (directory / 'beta.json').unlink()
    elif tamper == 'claim_a_different_cutover':
        stored = json.loads(manifest_path.read_text(encoding='utf-8'))
        stored['production_cutover'] = 'READY'
        evidence._write(str(manifest_path), stored)
    elif tamper == 'hash_itself':
        stored = json.loads(manifest_path.read_text(encoding='utf-8'))
        stored['artifacts'][evidence.MANIFEST_NAME] = {'sha256': 'x' * 64}
        evidence._write(str(manifest_path), stored)

    result = evidence.validate_manifest(directory)
    assert result['status'] == 'FAIL', result
    assert any(expected_fragment in finding for finding in result['findings']), result


def test_a_legacy_hand_authored_manifest_is_named_not_silently_accepted(tmp_path):
    """옛 손 저작 manifest 는 **이름 붙여** 남긴다 — 검증한 척하지 않는다.

    ⚠️ 이것이 이 축의 착수 사유다: 버전 1 을 공유하던 두 문서의 **스키마가 서로 달랐다**.
    지우면 역사가 사라지고, PASS 로 세면 그것이 바로 고치려는 결함이다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    (directory / evidence.MANIFEST_NAME).write_text(
        json.dumps({'schema_version': 1, 'receipts': {}}) + '\n', encoding='utf-8')

    result = evidence.validate_manifest(directory)

    assert result['status'] == 'LEGACY_UNVERIFIABLE'
    assert any('machine-checkable' in finding for finding in result['findings'])


def test_the_manifest_cli_is_reachable_and_touches_no_database(tmp_path, monkeypatch):
    """생성기는 **호출 가능**해야 한다 — 부르는 사람이 없는 게이트는 게이트가 아니다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    monkeypatch.delenv(evidence.FRESH_ENV, raising=False)
    monkeypatch.delenv(evidence.UPGRADE_ENV, raising=False)

    def _refuse(*_args, **_kwargs):  # pragma: no cover - 발화하면 그 자체가 결함이다
        raise AssertionError('manifest mode opened a database connection')

    monkeypatch.setattr(evidence, '_connect', _refuse)

    write_code = evidence.main([
        '--write-manifest', '--receipt-dir', str(directory), '--cutoff', cutoff])
    check_code = evidence.main(['--check-manifest', '--receipt-dir', str(directory)])

    assert (directory / evidence.MANIFEST_NAME).is_file()
    written = json.loads((directory / evidence.MANIFEST_NAME).read_text(encoding='utf-8'))

    # ⚠️ 종료 코드를 0 으로 **단언하지 않는다**. 이 검사는 개발 중인 워킹 트리에서 돌고,
    # 그 트리는 더럽다 — 그리고 더러운 트리에서 PASS 를 답하지 않는 것이 이 생성기의 요지다.
    # 단언하는 것은 *종료 코드가 파생된 상태와 일치하는가* 이지 특정 값이 아니다.
    assert write_code == (0 if written['status'] == 'PASS' else 2)
    assert check_code in (0, 1)
    assert written['production_cutover'] == 'NOT_READY'
    assert set(written['artifacts']) == {'alpha.json', 'beta.json'}

    # 그리고 더러운 트리는 실제로 PASS 를 낼 수 없다 — 주입으로 그 분기를 직접 시험한다.
    dirty = dict(_clean_observation(cutoff), clean=False, status_entries=3)
    refused = evidence.build_manifest(directory, cutoff=cutoff, repository=dirty)
    assert refused['status'] == 'BLOCKED'
    assert any('dirty' in finding for finding in refused['findings']), refused['findings']


# ---------------------------------------------------------------------------
# 결박 판정 — 모순 제거이지 완화가 아니다 (2026-08-27)
#
# 옛 판정은 `HEAD == cutoff` 와 «완전 청결» 을 동시에 요구했고, manifest 가 트리 안에 사는
# 한 그 둘은 함께 만족될 수 없었다: 커밋 전이면 더럽고, 커밋하면 HEAD 가 움직인다. 그래서
# 계약이 명시적으로 허용하는 evidence-only 커밋이 실제로는 불가능했다.
#
# ⚠️ 아래 첫 검사가 이 축의 하중이다 — 이 생성기를 만든 **위조 형상**(머지로 SHA 가 움직인
# receipt 을 개명)이 여전히 BLOCKED 인지 묻는다. 나머지가 다 통과해도 이것이 통과하면
# 이 변경은 완화이지 수리가 아니다.
# ---------------------------------------------------------------------------


def _observation(cutoff: str, head: str, *, status_paths=()) -> dict:
    return {
        'head': head,
        'requested_cutoff': cutoff,
        'cutoff_matches_head': cutoff == head,
        'clean': not status_paths,
        'status_sha256': 'x' * 64,
        'status_entries': len(status_paths),
        'status_paths': tuple(status_paths),
    }


def test_the_forgery_this_generator_exists_for_is_still_blocked():
    """머지로 SHA 가 움직인 뒤 코드까지 바뀐 형상 — 이름을 고치면 위조다."""
    findings = evidence.evidence_binding_findings(
        _observation('5738ae5e', '4439eb8d'),
        cutoff='5738ae5e',
        code_changes=lambda _a, _b: ('src/application/platform/thing.py',),
    )
    assert findings, '코드가 바뀐 뒤의 cutoff 가 결박된 것으로 읽혔다 — 이 변경은 완화다'
    assert any('non-evidence path' in finding for finding in findings), findings


def test_an_evidence_only_commit_still_carries_the_cutoff():
    """증거만 담은 커밋은 판정을 무효화하지 않는다 — 계약이 그 커밋을 명시적으로 허용한다."""
    findings = evidence.evidence_binding_findings(
        _observation('cut', 'evidence-commit'),
        cutoff='cut',
        code_changes=lambda _a, _b: (),  # 증거 밖에서 바뀐 것이 없다
    )
    assert findings == [], findings


def test_a_cutoff_that_is_not_an_ancestor_is_refused():
    def _not_ancestor(_a, _b):
        raise evidence.CarryOverUnobservable('다른 갈래')

    findings = evidence.evidence_binding_findings(
        _observation('cut', 'other'), cutoff='cut', code_changes=_not_ancestor)
    assert any('확인할 수 없다' in finding for finding in findings), findings


def test_dirt_outside_the_evidence_root_still_blocks():
    findings = evidence.evidence_binding_findings(
        _observation('cut', 'cut', status_paths=('src/main.py',)), cutoff='cut')
    assert any('dirty outside' in finding for finding in findings), findings
    assert any('src/main.py' in finding for finding in findings), findings


def test_dirt_confined_to_the_evidence_root_is_tolerated():
    """이것이 허용되지 않으면 manifest 는 자기 자신을 쓰는 순간 BLOCKED 다."""
    findings = evidence.evidence_binding_findings(
        _observation(
            'cut', 'cut',
            status_paths=(f'{evidence.EVIDENCE_ROOT}/slug/cut/alpha.json',),
        ),
        cutoff='cut',
    )
    assert findings == [], findings


def test_an_observation_that_cannot_name_its_dirt_is_refused():
    """«더럽다» 고만 말하고 어디인지 못 말하면 증거 안팎을 가릴 수 없다 — 가릴 수 없으면 거부."""
    legacy = {
        'head': 'cut', 'requested_cutoff': 'cut', 'cutoff_matches_head': True,
        'clean': False, 'status_sha256': 'x' * 64, 'status_entries': 3,
    }
    findings = evidence.evidence_binding_findings(legacy, cutoff='cut')
    assert any('does not name the paths' in finding for finding in findings), findings


def test_the_status_paths_observation_survives_non_ascii():
    """git 기본값은 한글 경로를 이스케이프한다 — 그러면 접두사 비교가 조용히 빗나간다."""
    korean = f'{evidence.EVIDENCE_ROOT}/슬러그/컷오프/영수증.json'
    assert evidence.evidence_binding_findings(
        _observation('cut', 'cut', status_paths=(korean,)), cutoff='cut') == []
    outside_korean = 'docs/education/2026-08-27-검사가-스스로를-검사한다.html'
    assert evidence.evidence_binding_findings(
        _observation('cut', 'cut', status_paths=(outside_korean,)), cutoff='cut')


def test_the_manifest_carries_the_gates_it_is_given(tmp_path):
    """``gates`` 가 비면 MUST-9 의 «각 명령과 종료 코드» 요구가 미충족이다.

    ⚠️ 2026-08-27 5차 독립 검토가 ``gates: {}`` 를 FAIL 사유로 들었다. 이 검사는 그 통로가
    실제로 뚫려 있는지 묻는다 — 인자를 줘도 manifest 에 도달하지 않으면 통로가 없는 것이고,
    그러면 다음 세션은 «채웠는데 왜 비어 있지» 를 처음부터 조사한다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    given = {
        'routine': {
            'command': 'pytest -m "not hardware..."', 'exit': 1, 'failing': 3,
            'started_at': '2026-08-27T00:00:00+00:00',
            'ended_at': '2026-08-27T00:06:00+00:00',
        },
    }
    manifest = _build(
        directory, cutoff, gates=given,
        limitations=['live/benchmark lanes were not run at this cutoff'],
    )
    assert manifest['gates'] == given
    assert manifest['limitations'] == ['live/benchmark lanes were not run at this cutoff']


def test_gates_default_to_empty_rather_than_invented(tmp_path):
    """형제 영수증이 없으면 비워 둔다 — 지어내지 않고, 그 부재를 이름으로 남긴다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    manifest = evidence.build_manifest(
        directory, cutoff=cutoff, repository=_clean_observation(cutoff))
    assert manifest['gates'] == {}
    assert manifest['limitations'] == []
    assert any(evidence.GATES_RECEIPT_NAME in finding and 'absent' in finding
               for finding in manifest['findings']), manifest['findings']


# ---------------------------------------------------------------------------
# 게이트 완전성 — 비어 있음은 «읽히는 사실» 이지만 «통과» 는 아니다 (2026-08-27)
#
# ⚠️ 2026-08-27 5차 독립 검토가 `gates: {}` 를 FAIL 사유로 들었고, 그때의 수리는 **채우는
# 통로를 뚫는 것**이었다. 통로는 뚫렸지만 *비워 두는 것을 막는 것*은 아무것도 없었다 —
# 그래서 다음 실행이 다시 비워도 판정은 PASS 였다. 아래가 그 자리를 막는다.
# ---------------------------------------------------------------------------


def test_a_manifest_with_no_gate_cannot_answer_pass(tmp_path):
    """게이트가 0 개인 문서는 MUST-9 의 «각 명령과 종료 코드» 를 원리적으로 만족할 수 없다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)

    # 비-공허성: 같은 픽스처가 게이트만 채우면 실제로 PASS 다 — 그러니 red 의 원인은 게이트다.
    assert _build(directory, cutoff)['status'] == 'PASS'

    empty = _build(directory, cutoff, gates={})
    assert empty['status'] == 'BLOCKED'
    assert any('records no gate' in finding for finding in empty['findings']), empty['findings']


_WHOLE_GATE = {
    'command': 'x', 'exit': 0,
    'started_at': '2026-08-27T00:00:00+00:00',
    'ended_at': '2026-08-27T00:01:00+00:00',
}


@pytest.mark.parametrize(
    'broken, fragment',
    [
        ({'lane:routine': dict(_WHOLE_GATE, exit=None)}, 'records no integer exit code'),
        ({'lane:routine': dict(_WHOLE_GATE, exit='1')}, 'records no integer exit code'),
        ({'lane:routine': dict(_WHOLE_GATE, exit=True)}, 'records no integer exit code'),
        ({'lane:routine': 'exit 0'}, 'not a mapping'),
        # ⚠️ MUST-9 는 «각 명령, 시작/종료» 도 요구한다. 2026-08-27 독립 검토 실측:
        # 27 개 게이트 중 그 셋을 가진 것이 0 개였고, 그래서 어느 것도 재현할 수 없었다.
        ({'lane:routine': {k: v for k, v in _WHOLE_GATE.items() if k != 'command'}},
         'records no command'),
        ({'lane:routine': {k: v for k, v in _WHOLE_GATE.items() if k != 'started_at'}},
         'records no started_at'),
        ({'lane:routine': {k: v for k, v in _WHOLE_GATE.items() if k != 'ended_at'}},
         'records no ended_at'),
        ({'lane:routine': dict(_WHOLE_GATE, command='   ')}, 'records no command'),
    ],
)
def test_a_gate_without_an_exit_code_is_named(tmp_path, broken, fragment):
    """명령을 돌렸다는 주장만 있고 답이 없으면 그 줄은 관측이 아니라 문장이다.

    ⚠️ ``exit: True`` 도 거부한다 — 파이썬에서 ``bool`` 은 ``int`` 의 하위형이라 순진한
    ``isinstance(x, int)`` 는 ``True`` 를 종료 코드 1 로 읽는다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)

    manifest = _build(directory, cutoff, gates=broken)

    assert manifest['status'] == 'BLOCKED'
    assert any(fragment in finding for finding in manifest['findings']), manifest['findings']
    assert any("'lane:routine'" in finding for finding in manifest['findings']), manifest


def test_a_nonzero_exit_code_is_recorded_not_refused(tmp_path):
    """0 을 요구하면 차분 판정(규칙 5항)과 정면 충돌한다 — 충돌하는 게이트는 꺼진다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    manifest = _build(directory, cutoff, gates={'lane:routine': dict(_WHOLE_GATE, exit=1)})
    assert manifest['status'] == 'PASS', manifest['findings']


def test_the_gate_judgement_names_no_gate_of_its_own(tmp_path):
    """게이트 **이름 목록**을 갖지 않는다 — 목록은 웨이브마다 다르고 반드시 낡는다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    # 서로 완전히 다른 두 게이트 집합이 **둘 다** 통과한다. 이름을 요구했다면 하나는 red 다.
    for names in (('web:build', 'web:lint'), ('migration:030', 'bench:selection')):
        gates = {name: dict(_WHOLE_GATE) for name in names}
        assert _build(directory, cutoff, gates=gates)['status'] == 'PASS', names


# ---------------------------------------------------------------------------
# 재검증은 «해시 다시 세기» 가 아니라 «판정 다시 파생하기» 다 (2026-08-27)
#
# ⚠️ 옛 `validate_manifest` 는 형제 receipt 을 재해시하고 `production_cutover` 리터럴과
# 디렉터리 이름을 봤다. 그것이 전부였고, **이 축이 막으라고 만들어진 위조는 전부 판정 쪽에
# 있었다** — status/findings/gates/repository/artifact_count/binds_cutoff 는 파일 해시를
# 하나도 건드리지 않고 바꿀 수 있다. 아래 다섯이 그 다섯 통로다.
# ---------------------------------------------------------------------------


def _written_manifest(directory: Path, cutoff: str, **kwargs) -> Path:
    """⚠️ **프로덕션 writer 를 지난다.** ``json.dumps`` 로 직접 쓰면 ``_write`` 가 붙이는
    무결성 필드가 빠지고, 그러면 픽스처가 프로덕션과 다른 문서가 된다 — 검증기가 그 차이를
    «생성기가 만들지 않은 문서» 로 읽는 것이 옳고, 그것을 피하려고 검사를 무르면 안 된다.
    """
    manifest = _build(directory, cutoff, **kwargs)
    path = directory / evidence.MANIFEST_NAME
    evidence._write(str(path), manifest)
    return path


def _forge_status(stored: dict) -> None:
    stored['status'] = 'PASS'
    stored['findings'] = []


def _forge_observation(stored: dict) -> None:
    stored['repository'] = dict(
        stored['repository'], clean=True, status_entries=0, status_paths=[])


def _forge_empty_gates(stored: dict) -> None:
    stored['gates'] = {}


def _forge_artifact_count(stored: dict) -> None:
    stored['artifact_count'] = len(stored['artifacts']) + 7


def _forge_binding_flag(stored: dict) -> None:
    for entry in stored['artifacts'].values():
        entry['binds_cutoff'] = not entry['binds_cutoff']


@pytest.mark.parametrize(
    'forge, dirty_tree, fragment',
    [
        (_forge_status, True, 'status:'),
        (_forge_observation, True, 'findings:'),
        (_forge_empty_gates, False, 'gates:'),
        (_forge_artifact_count, False, 'artifact_count:'),
        (_forge_binding_flag, False, 'artifacts:'),
    ],
)
def test_a_forged_verdict_is_red_even_though_no_file_hash_changed(
    tmp_path, forge, dirty_tree, fragment,
):
    """**판정 위조 다섯 통로.** 어느 것도 형제 파일을 건드리지 않는다.

    각 변이는 **적용 뒤 실제로 검증기를 태워** 판정한다. 그리고 적용 **이전에** 저장된 상태가
    무엇인지 먼저 단언한다 — 그러지 않으면 red 가 변이 때문인지 픽스처가 애초에 그랬기
    때문인지 구분할 수 없다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    observation = _clean_observation(cutoff)
    if dirty_tree:
        # 증거 **밖**이 더러웠던 실행. 그 사실이 BLOCKED 를 낳았고, 위조는 그것을 지운다.
        observation = dict(
            observation, clean=False, status_entries=1, status_paths=['src/main.py'])
    _write_gates(directory, cutoff)
    manifest = evidence.build_manifest(
        directory, cutoff=cutoff, repository=observation)
    path = directory / evidence.MANIFEST_NAME
    evidence._write(str(path), manifest)

    # ⚠️ 재검증이 묻는 것은 «그 실행이 성공했는가» 가 아니라 «이 문서가 자기 기록과
    # 일치하는가» 다. BLOCKED 를 정직하게 적은 문서는 **유효한 문서**이고, 위조가 지우는
    # 것이 바로 그 정직한 BLOCKED 다. 그래서 두 사실을 따로 단언한다.
    before = evidence.validate_manifest(directory)
    assert before == {'status': 'PASS', 'findings': []}, before
    assert manifest['status'] == ('BLOCKED' if dirty_tree else 'PASS'), manifest
    hashes_before = {
        p.name: evidence._file_hash(p) for p in evidence.manifest_siblings(directory)}

    stored = json.loads(path.read_text(encoding='utf-8'))
    forge(stored)
    # ⚠️ 무결성 해시를 **갱신해서** 다시 쓴다. 갱신하지 않으면 모든 변이가 그 한 검사에서
    # 걸리고, 그러면 이 파라미터화는 재파생 축에 대해 아무것도 말하지 않는다
    # («여러 가드가 같은 식을 지키면 거절 검사는 가장 먼저 발화하는 것만 증명한다»).
    evidence._write(str(path), stored)

    # ⚠️ 위조가 **파일 해시를 하나도 바꾸지 않았음**을 단언한다. 그러지 않으면 이 검사는
    # 옛 해시 재계산만으로도 통과하고, 새 축이 하중을 지는지 말해 주지 않는다.
    assert hashes_before == {
        p.name: evidence._file_hash(p) for p in evidence.manifest_siblings(directory)}

    result = evidence.validate_manifest(directory)
    assert result['status'] == 'FAIL', result
    assert any(finding.startswith(fragment) for finding in result['findings']), result


# ---------------------------------------------------------------------------
# 2026-08-27 독립 검토가 **실측으로 통과시킨** 위조들. 여기 있는 것은 가정이 아니라
# 그 검토가 실제로 재현한 14 건 중 판정 축에 속하는 것들이고, 각각 그때 `PASS` 였다.
#
# ⚠️ 검토자가 남긴 일반형이 이 블록의 요지다: **입력 필드는 재파생이 그대로 되먹이므로
# 어떤 값이든 자기 자신과 일치한다.** 그래서 수리는 검사 항목을 늘린 것이 아니라
# 그 값들을 증거 디렉터리 안으로 옮겨 **파생 대상으로 만든** 것이다.
# ---------------------------------------------------------------------------


def _forge_gate_exit(stored: dict) -> None:
    """X11 — 실패한 게이트의 종료 코드를 0 으로. MUST-9 «skipped/blocked 를 green 으로 적지 않는다»."""
    for entry in stored['gates'].values():
        entry['exit'] = 0


def _forge_drop_gates(stored: dict) -> None:
    """X12 — 게이트 대부분을 지운다."""
    keep = sorted(stored['gates'])[:1]
    stored['gates'] = {name: stored['gates'][name] for name in keep}


def _forge_limitations_emptied(stored: dict) -> None:
    """X03 — 한계 목록을 비운다. 미실행 축이 침묵으로 사라진다."""
    stored['limitations'] = []


def _forge_limitations_rewritten(stored: dict) -> None:
    """X04 — 한계 목록을 «전부 실행했다» 로 다시 쓴다."""
    stored['limitations'] = ['Everything was run. Nothing was skipped.']


def _forge_base_sha(stored: dict) -> None:
    """X05 — 차분 base 커밋을 0 으로."""
    stored['base_sha'] = '0' * 40


def _forge_extra_claim(stored: dict) -> None:
    """X08 — 문서에 없는 주장을 덧붙인다."""
    stored['reviewer_verdict'] = 'APPROVED'
    stored['hardware'] = 'all green'


@pytest.mark.parametrize(
    'forge, fragment',
    [
        (_forge_gate_exit, 'gates:'),
        (_forge_drop_gates, 'gates:'),
        (_forge_limitations_emptied, 'limitations:'),
        (_forge_limitations_rewritten, 'limitations:'),
        (_forge_base_sha, 'base_sha:'),
        (_forge_extra_claim, 'never writes'),
    ],
)
def test_the_forgeries_an_independent_review_measured_as_surviving_are_now_red(
    tmp_path, forge, fragment,
):
    """무결성 해시를 **갱신한 뒤에도** red 여야 한다 — 그러지 않으면 축이 하나뿐이다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    # base 커밋을 말하는 형제를 하나 둔다 — 그래야 base_sha 가 파생되고 X05 가 의미를 갖는다.
    (directory / 'lane-differential-routine.json').write_text(
        json.dumps({
            'status': 'PASS', 'code_cutoff': cutoff, 'base_tree_sha': 'b' * 40,
        }) + '\n',
        encoding='utf-8',
    )
    # ⚠️ 게이트를 **둘 이상** 둔다. 하나뿐이면 «대부분을 지운다» 변이가 아무것도 지우지
    # 못하고, 그 파라미터는 자기 명제에 대해 아무것도 말하지 않는다(적용되지 않은 변이와
    # 살아남은 변이는 출력이 같다).
    gates = {
        'lane:routine': dict(_WHOLE_GATE, exit=1),
        'web:build': dict(_WHOLE_GATE, exit=0),
    }
    path = _written_manifest(
        directory, cutoff, gates=gates,
        limitations=['hardware was not exercised at this cutoff'])
    assert evidence.validate_manifest(directory) == {'status': 'PASS', 'findings': []}
    stored_now = json.loads(path.read_text(encoding='utf-8'))
    assert stored_now['base_sha'] == 'b' * 40
    assert len(stored_now['gates']) == 2

    stored = json.loads(path.read_text(encoding='utf-8'))
    forge(stored)
    evidence._write(str(path), stored)

    result = evidence.validate_manifest(directory)

    assert result['status'] == 'FAIL', result
    assert any(fragment in finding for finding in result['findings']), result


def test_an_edit_that_does_not_refresh_the_integrity_hash_is_red(tmp_path):
    """무결성 해시 축을 **고립해서** 시험한다.

    ⚠️ 위 파라미터화는 위조 뒤 ``_write`` 로 해시를 **갱신**한다 — 그래야 재파생 축이
    시험되기 때문이다. 그 결과 이 자물쇠는 거기서 한 번도 발화하지 않는다. 여러 가드가 같은
    식을 지키면 거절 검사는 가장 먼저 발화하는 것만 증명한다는 이 저장소의 규율 그대로,
    여기서는 **해시를 갱신하지 않는** 손 편집을 따로 시험한다. 그것이 실제 손 편집의 모습이고,
    독립 검토가 *"살아남은 위조 14 건 중 13 건이 이미 이 해시를 깨고 있었다"* 고 실측한 자리다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    path = _written_manifest(directory, cutoff)
    assert evidence.validate_manifest(directory) == {'status': 'PASS', 'findings': []}

    stored = json.loads(path.read_text(encoding='utf-8'))
    # 재파생이 **볼 수 없는** 곳만 건드린다 — 관측은 입력이므로 다른 축은 침묵한다.
    stored['repository'] = dict(stored['repository'], status_entries=99)
    path.write_text(
        json.dumps(stored, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    result = evidence.validate_manifest(directory)

    assert result['status'] == 'FAIL', result
    assert any(evidence.RECEIPT_PAYLOAD_HASH_FIELD in finding
               and 'edited after it was written' in finding
               for finding in result['findings']), result


def test_the_integrity_hash_is_computed_by_one_function(tmp_path):
    """쓰는 쪽과 검사하는 쪽이 **같은 함수**를 지난다 — 두 벌이면 한쪽 렌더가 바뀌는 날 전부 red 다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    path = _written_manifest(directory, cutoff)
    stored = json.loads(path.read_text(encoding='utf-8'))

    assert stored[evidence.RECEIPT_PAYLOAD_HASH_FIELD] == evidence.receipt_payload_hash(stored)
    # 그리고 그 값은 문서 자신을 뺀 나머지의 해시다 — 자기 참조가 아니다.
    without = {k: v for k, v in stored.items() if k != evidence.RECEIPT_PAYLOAD_HASH_FIELD}
    assert evidence.receipt_payload_hash(stored) == evidence.receipt_payload_hash(without)


def test_a_receipt_hidden_by_renaming_is_still_seen(tmp_path):
    """X18 — 확장자를 바꿔 숨기면 옛 판정은 «없는 파일» 로 읽고 통과했다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    path = _written_manifest(directory, cutoff)
    assert evidence.validate_manifest(directory)['status'] == 'PASS'

    stored = json.loads(path.read_text(encoding='utf-8'))
    (directory / 'alpha.json').rename(directory / 'alpha.jsonx')
    del stored['artifacts']['alpha.json']
    stored['artifact_count'] = len(stored['artifacts'])
    evidence._write(str(path), stored)

    result = evidence.validate_manifest(directory)

    assert result['status'] == 'FAIL', result
    assert any('alpha.jsonx' in finding and 'present on disk' in finding
               for finding in result['findings']), result


def test_a_gate_naming_a_raw_output_nobody_has_is_red(tmp_path):
    """*"아무도 가지고 있지 않은 파일의 해시는 해시가 없는 것보다 나쁘다"* — 있는 척하기 때문이다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    gates = {
        'lane:routine': dict(
            _WHOLE_GATE, exit=1, command_log='routine.log', output_sha256='0' * 64),
    }

    missing = _build(directory, cutoff, gates=gates)
    assert missing['status'] == 'BLOCKED'
    assert any('routine.log' in finding and 'not in this directory' in finding
               for finding in missing['findings']), missing['findings']

    # 파일을 두면 통과한다 — 그리고 **그 파일의 실제 해시**를 요구한다.
    (directory / 'routine.log').write_text('=== lane routine: exit=1\n', encoding='utf-8')
    still_wrong = _build(directory, cutoff, gates=gates)
    assert any('output_sha256 does not match' in finding
               for finding in still_wrong['findings']), still_wrong['findings']

    gates['lane:routine']['output_sha256'] = evidence._file_hash(directory / 'routine.log')
    assert _build(directory, cutoff, gates=gates)['status'] == 'PASS'


def test_the_raw_output_is_bound_like_every_other_sibling(tmp_path):
    """MUST-9 는 «manifest 와 **그가 참조하는 원시 출력**» 을 요구한다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    (directory / 'routine.log').write_text('output\n', encoding='utf-8')

    manifest = _build(directory, cutoff)

    assert 'routine.log' in manifest['artifacts']
    assert manifest['artifacts']['routine.log']['status'] == 'RAW_OUTPUT'
    # 로그는 컷오프를 «말할 수» 없으므로 요구하지 않는다 — 요구하면 로그를 커밋할 수 없다.
    assert manifest['artifacts']['routine.log']['binds_cutoff'] is None
    assert manifest['status'] == 'PASS', manifest['findings']


def test_a_base_that_two_receipts_disagree_about_cannot_answer_pass(tmp_path):
    """서로 다른 base 에서 잰 차분을 한 manifest 로 묶는 것이 이 축의 대표 실패 모드다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    for name, base in (('lane-routine.json', 'b' * 40), ('lane-gui.json', 'c' * 40)):
        (directory / name).write_text(
            json.dumps({'status': 'PASS', 'code_cutoff': cutoff, 'base_tree_sha': base})
            + '\n',
            encoding='utf-8',
        )

    manifest = _build(directory, cutoff)

    assert manifest['status'] == 'BLOCKED'
    assert manifest['base_sha'] is None
    assert any('more than one differential base' in finding
               for finding in manifest['findings']), manifest['findings']


def test_an_untampered_manifest_re_derives_identically(tmp_path):
    """비-공허성 — 재파생 대조가 **정상 문서를 red 로 만들지 않는다**."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    _written_manifest(directory, cutoff)
    result = evidence.validate_manifest(directory)
    assert result == {'status': 'PASS', 'findings': []}, result


_PARTITION = (
    evidence.MANIFEST_INPUT_FIELDS
    | evidence.MANIFEST_DERIVED_FIELDS
    | evidence.MANIFEST_INTEGRITY_FIELDS
)


def test_the_field_partition_is_derived_from_the_written_document_not_declared(tmp_path):
    """분할은 **디스크에 기록된 문서**의 키와 상등이다.

    ⚠️ 2026-08-27 독립 검토가 이 자리를 짚었다: 옛 봉인은 :func:`build_manifest` 의 **반환값**
    과 비교했는데, ``_write`` 가 그 뒤에 무결성 필드를 하나 더 붙인다. 그래서 상등은 성립하고
    디스크의 문서는 13 개 키를 가졌으며, 그 한 개를 **아무도 검증하지 않았다**. 오라클은
    소비자가 실제로 읽는 산출물이어야 한다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    path = _written_manifest(directory, cutoff)
    written = set(json.loads(path.read_text(encoding='utf-8')))

    assert evidence.MANIFEST_INPUT_FIELDS.isdisjoint(evidence.MANIFEST_DERIVED_FIELDS)
    assert evidence.MANIFEST_INPUT_FIELDS.isdisjoint(evidence.MANIFEST_INTEGRITY_FIELDS)
    assert evidence.MANIFEST_DERIVED_FIELDS.isdisjoint(evidence.MANIFEST_INTEGRITY_FIELDS)
    assert _PARTITION == written


def test_an_unregistered_new_field_makes_the_partition_red(tmp_path, monkeypatch):
    """반례 — 분할에 등록되지 않은 필드가 생기면 위 상등이 실제로 깨진다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    _write_gates(directory, cutoff)
    produced = evidence.build_manifest(
        directory, cutoff=cutoff, repository=_clean_observation(cutoff))
    path = directory / evidence.MANIFEST_NAME
    evidence._write(str(path), dict(produced, reviewer_note='added without registering'))
    written = set(json.loads(path.read_text(encoding='utf-8')))

    assert _PARTITION != written

    # 그리고 그 문서는 검증기에서도 red 다 — 상등이 깨지는 것만으로는 아무도 모른다.
    assert evidence.validate_manifest(directory)['status'] == 'FAIL'


def test_a_hand_authored_document_squatting_the_version_is_named(tmp_path):
    """버전 번호만 참칭한 손 저작 문서는 **빠진 필드를 이름으로** 대며 거부된다.

    ⚠️ 이 형태는 실재했다 — 이 저장소의 ``.claude/evidence/…/2169dc28…/manifest.json`` 이
    ``artifacts`` 매핑 없이 ``schema_version: 2`` 를 선언한다. 옛 판정은 그것을 «artifacts 가
    매핑이 아니다» 로 답했고, 그 사유는 생성물 결함을 가리켜 다음 세션을 엉뚱한 곳으로 보냈다.

    ⚠️ **다만 그 문서는 이제 이 분기에 도달하지 않는다**(2026-08-27 독립 검토 실측): 버전이
    3 으로 오른 뒤 그것은 **버전 검사에서 먼저** 걸린다. 그래서 아래 입력은 **합성 문서**이고,
    이 docstring 이 그 사실을 적는 이유는 «실측이다» 라고 적힌 문장이 없는 동작을 가리키는 것이
    이 저장소가 이미 값을 치른 형태이기 때문이다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    (directory / evidence.MANIFEST_NAME).write_text(
        json.dumps({
            'schema_version': evidence.MANIFEST_SCHEMA_VERSION,
            'status': 'FEATURE_EVIDENCE_PASS',
            'code_cutoff_sha': cutoff,
            'gates': {'web:build': {'exit': 0}},
        }) + '\n',
        encoding='utf-8',
    )

    result = evidence.validate_manifest(directory)

    assert result['status'] == 'LEGACY_UNVERIFIABLE', result
    assert any('was not produced by this generator' in finding
               for finding in result['findings']), result
    # 그리고 **어느 필드가 없는지** 실제로 이름을 댄다.
    assert any('artifacts' in finding and 'repository' in finding
               for finding in result['findings']), result


def test_the_previous_schema_version_is_not_silently_accepted(tmp_path):
    """판정이 바뀌면 버전이 오른다 — 옛 버전 문서를 새 판정으로 재파생하면 거짓말을 한다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    stored = _build(directory, cutoff)
    stored['schema_version'] = evidence.MANIFEST_SCHEMA_VERSION - 1
    evidence._write(str(directory / evidence.MANIFEST_NAME), stored)

    result = evidence.validate_manifest(directory)

    assert result['status'] == 'LEGACY_UNVERIFIABLE', result
    assert any('machine-checkable' in finding for finding in result['findings']), result


def test_an_unusable_recorded_observation_is_refused_not_skipped(tmp_path):
    """재파생이 **불가능한** 것을 «차이 없음» 으로 읽으면 그것이 조용한 false PASS 다."""
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    stored = _build(directory, cutoff)
    stored['repository'] = 'clean'
    evidence._write(str(directory / evidence.MANIFEST_NAME), stored)

    result = evidence.validate_manifest(directory)

    assert result['status'] == 'FAIL', result
    assert any('cannot be re-derived' in finding for finding in result['findings']), result


def test_a_re_derivation_that_raises_is_refused_not_swallowed(tmp_path):
    """관측 형이 맞는데 **파생 도중 터지는** 경우 — 앞의 가드가 아니라 이 자물쇠를 시험한다.

    ⚠️ 위 검사는 ``repository`` 가 매핑이 아닌 경우이고, 그것은 **다른 자물쇠**(형 가드)에서
    먼저 걸린다. 여러 가드가 같은 식을 지키면 거절 검사는 **가장 먼저 발화하는 것**만
    증명한다 — 실제로 이 자리에서 «예외를 빈 목록으로 삼키는» 변이가 살아남았다.
    여기서는 형 가드를 통과시키고 파생 자체를 터뜨린다.
    """
    cutoff = _head_cutoff()
    directory = _receipt_dir(tmp_path, cutoff)
    # cutoff != head 여야 결박 관측자가 실제로 호출된다 — 그 자리가 유일한 주입점이다.
    observation = dict(
        _clean_observation(cutoff), head='0' * 40, cutoff_matches_head=False)
    _write_gates(directory, cutoff)
    manifest = evidence.build_manifest(
        directory, cutoff=cutoff, repository=observation,
        code_changes=lambda _a, _b: (),
    )
    evidence._write(str(directory / evidence.MANIFEST_NAME), manifest)

    def _explode(_a, _b):
        raise RuntimeError('git 이 다른 이유로 죽었다')

    # 비-공허성: 같은 문서가 정상 관측자로는 PASS 다 — red 의 원인은 예외 경로다.
    assert evidence.validate_manifest(
        directory, code_changes=lambda _a, _b: ())['status'] == 'PASS'

    result = evidence.validate_manifest(directory, code_changes=_explode)

    assert result['status'] == 'FAIL', result
    assert any('could not be re-derived' in finding for finding in result['findings']), result
    assert any('RuntimeError' in finding for finding in result['findings']), result
