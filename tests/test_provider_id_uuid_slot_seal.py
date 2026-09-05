"""자연키가 uuid 슬롯에 들어가는 자리를 봉인한다 (2026-09-03).

**이 축은 형제 센서스가 구조적으로 볼 수 없는 것을 본다.** FCC 쪽
``check_central_provider_id_pairing.py`` 는 *운영자가 env 에 무엇을 적었나* 를 묻는다.
2026-09-02 에 전량 거절을 만든 결함은 그 축에 걸리지 않았다 — 거기서 틀린 것은 적힌
값이 아니라 **그 런타임 값이 어느 컬럼으로 가나** 였기 때문이다. 그래서 질문이 다르다:

    자연키를 uuid 슬롯에 넣는 자리가 있는가.

재료가 둘 다 이 저장소에 있다. 중앙 스키마 JSON 이 **컬럼 타입**을 갖고(실측
2026-09-03: ``provider_id`` 가 uuid 인 표 12개 · text 1개), write 어댑터가 **SQL** 을
갖는다. 둘이 만나는 곳이 여기다.

⚠️ **이 봉인은 DB 에 기댈 수 없다.** 그것이 이 결함이 초록 테스트를 통과해 출하된
이유이고, 두 겹이었다(실측 2026-09-03):

1. ``tests/support/central_pg_sqlite_shim.py`` 가 ``'uuid': 'TEXT'`` 로 매핑한다.
   테스트 DB 는 SQLite 라 **uuid 컬럼이 자연키를 받아준다.** PostgreSQL 이 거절하는
   그 타입 제약이 테스트 환경에는 아예 없다.
2. ``test_platform_artifact_custody_api.py`` 의 ``_PROVIDER`` 는 이미 **UUID 모양**
   이다. 타입 제약이 있었더라도 통과했을 것이다.

그래서 판정을 DB 에게 시키지 않고 **Python 층에서 타입으로** 한다. 입력은 일부러
자연키이고, uuid 슬롯에 바인딩된 값이 ``uuid.UUID`` 로 파싱되는지를 직접 묻는다.

**두 가지 정상 해소를 모두 통과시킨다** — 어느 하나를 강요하면 이미 맞는 형제를
빨갛게 만든다:

- **Python 해소** — 서비스/readiness 가 자연키를 ``providers.id`` 로 바꿔서 넘긴다
  (``published_plan_expectation`` · ``measurement_attempts``). 슬롯이 ``%s`` 이므로
  바인딩된 값이 uuid 여야 한다.
- **SQL 해소** — INSERT 자신이 ``FROM "providers" p WHERE "p"."provider_id" = %s`` 로
  찾아서 ``"p"."id"`` 를 넣는다(``reference_revisions``). 슬롯이 ``%s`` 가 아니므로
  자연키 바인딩은 **맞는 값**이다.

**비-공허성 팔이 셋이다.** 하나라도 없으면 초록이 근거가 되지 못한다:

1. 스키마에서 uuid 슬롯 표가 하나도 안 나오면 실패한다.
2. 프로브가 uuid 슬롯 INSERT 를 하나도 관측하지 못하면 실패한다.
3. **완전성** — 플랫폼 레인에서 ``INSERT INTO`` 를 쓰는 모듈 중 프로브가 없는 것이
   있으면 실패한다. 새 write 어댑터가 생기면 **프로브를 쓸 때까지 빨갛다.** 이것이
   없으면 봉인은 오늘 아는 다섯 자리만 지키고 내일 생기는 자리는 놓친다 —
   결함이 다시 들어오는 자리가 정확히 거기다.
"""
from __future__ import annotations

import ast
import json
import re
import sys
import unittest
import uuid
from pathlib import Path
from typing import Iterable, Iterator

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_SCHEMA_PATH = _REPO_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json'
_LANE_ROOT = _REPO_ROOT / 'fcc_test_platform'

#: 일부러 자연키다. UUID 모양을 넣으면 이 봉인은 아무것도 묻지 않는 것이 된다
#: (기존 커스터디 테스트가 정확히 그래서 이 결함을 못 봤다).
NATURAL_KEY = 'fcc-unlicensed-conducted'

#: 해소가 끝난 값의 자리표시자. 실제 중앙 ``providers.id`` 와 같은 모양이면 된다.
RESOLVED_UUID = '70a985fa-4724-4d71-a227-ef9ea7605808'

PROVIDER_COLUMN = 'provider_id'


# ── 스키마에서 파생 ────────────────────────────────────────────────────────────

def uuid_slot_tables() -> frozenset[str]:
    """``provider_id`` 가 **uuid** 인 표. 손으로 적지 않는다 — 파생이다."""
    schema = json.loads(_SCHEMA_PATH.read_text(encoding='utf-8'))
    tables = schema['tables']
    found = set()
    for name, table in tables.items():
        column = (table.get('columns') or {}).get(PROVIDER_COLUMN)
        if isinstance(column, dict) and column.get('type') == 'uuid':
            found.add(name)
    return frozenset(found)


# ── SQL 해부 ──────────────────────────────────────────────────────────────────

_INSERT_RE = re.compile(
    r'INSERT\s+INTO\s+"?(?P<table>[a-z_][a-z0-9_]*)"?\s*\(', re.IGNORECASE,
)


def _match_paren(text: str, open_index: int) -> int:
    """``text[open_index] == '('`` 의 짝 위치. 문자열 리터럴 안의 괄호는 세지 않는다."""
    depth = 0
    quote = ''
    index = open_index
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                quote = ''
        elif char in "'\"":
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ValueError('unbalanced parenthesis in SQL')


def _split_top_level(text: str) -> list[tuple[int, str]]:
    """최상위 콤마로 나눈 ``(원문 내 시작 오프셋, 조각)`` 목록."""
    parts: list[tuple[int, str]] = []
    depth = 0
    quote = ''
    start = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = ''
            continue
        if char in "'\"":
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            parts.append((start, text[start:index]))
            start = index + 1
    parts.append((start, text[start:]))
    return parts


class UuidSlotBinding:
    """한 INSERT 의 ``provider_id`` 슬롯이 무엇으로 채워지는가."""

    def __init__(self, table: str, expression: str, param_index: int | None) -> None:
        self.table = table
        self.expression = expression
        #: ``%s`` 가 아니면 ``None`` — SQL 이 스스로 해소한다는 뜻이다.
        self.param_index = param_index

    @property
    def resolved_in_sql(self) -> bool:
        return self.param_index is None


def uuid_slot_bindings(sql: str, uuid_tables: Iterable[str]) -> Iterator[UuidSlotBinding]:
    """``sql`` 안에서 uuid ``provider_id`` 슬롯을 채우는 자리를 모두 낸다."""
    uuid_tables = set(uuid_tables)
    for match in _INSERT_RE.finditer(sql):
        table = match.group('table')
        if table not in uuid_tables:
            continue
        columns_open = match.end() - 1
        columns_close = _match_paren(sql, columns_open)
        columns = [
            piece.strip().strip('"')
            for _, piece in _split_top_level(sql[columns_open + 1:columns_close])
        ]
        if PROVIDER_COLUMN not in columns:
            continue
        slot = columns.index(PROVIDER_COLUMN)

        # 값 목록은 ``VALUES (...)`` 이거나 ``SELECT ... FROM`` 이다. 둘 다 실재한다:
        # 커스터디/진행/인입은 VALUES, 참조는 SELECT 로 providers 를 조인한다.
        tail = sql[columns_close + 1:]
        values_match = re.match(r'\s*VALUES\s*\(', tail, re.IGNORECASE)
        if values_match:
            values_open = columns_close + 1 + values_match.end() - 1
            values_close = _match_paren(sql, values_open)
            body = sql[values_open + 1:values_close]
            body_origin = values_open + 1
        else:
            select_match = re.match(r'\s*SELECT\s', tail, re.IGNORECASE)
            if not select_match:
                raise AssertionError(
                    f'{table} INSERT 의 값 목록을 해석하지 못했다 — 봉인이 판정할 수 '
                    f'없는 모양이다. 해석기를 고치고, 조용히 통과시키지 말 것.'
                )
            body_origin = columns_close + 1 + select_match.end()
            from_match = re.search(r'\sFROM\s', sql[body_origin:], re.IGNORECASE)
            end = body_origin + (from_match.start() if from_match else len(sql) - body_origin)
            body = sql[body_origin:end]

        pieces = _split_top_level(body)
        if slot >= len(pieces):
            raise AssertionError(
                f'{table} INSERT 의 열 수와 값 수가 맞지 않는다 (열 {len(columns)} · '
                f'값 {len(pieces)})'
            )
        offset, raw = pieces[slot]
        expression = raw.strip()
        if expression != '%s':
            yield UuidSlotBinding(table, expression, None)
            continue
        # 위치 파라미터다 — 앞선 ``%s`` 개수가 곧 인덱스다.
        param_index = sql[:body_origin + offset].count('%s')
        yield UuidSlotBinding(table, expression, param_index)


# ── 프로브 ────────────────────────────────────────────────────────────────────

class RecordingCursor:
    """실행된 ``(sql, params)`` 를 모으는 커서. DB 가 아니다 — 일부러 그렇다."""

    def __init__(self, fetch_one=None, fetch_all=None, responder=None) -> None:
        self.statements: list[tuple[str, tuple]] = []
        self._fetch_one = fetch_one
        self._fetch_all = fetch_all
        #: ``(sql, params) -> row`` — 어댑터가 **여러 다른 질의**를 던지고 그 답에
        #: 따라 다음 SQL 이 갈리는 경우에 쓴다(선택 이벤트가 그렇다).
        self._responder = responder
        self._last: tuple[str, tuple] = ('', ())

    def execute(self, sql, params=()):
        self._last = (sql, tuple(params or ()))
        self.statements.append(self._last)

    def fetchone(self):
        if self._responder is not None:
            return self._responder(*self._last)
        return self._fetch_one

    def fetchall(self):
        if self._responder is not None:
            row = self._responder(*self._last)
            return [row] if row is not None else []
        return list(self._fetch_all or [])

    def close(self):
        pass


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _custody_probe() -> list[tuple[str, tuple]]:
    from fcc_test_platform.application.central_artifact_custody_write_adapter import (
        PostgresCentralArtifactCustodyWriteAdapter,
    )
    from fcc_test_platform.application.central_artifact_custody_service import (
        CentralArtifactCustodyService,
    )
    def responder(sql, params):
        # 이 어댑터는 같은 트랜잭션 안에서 자연키를 providers.id 로 해소한다.
        # 그 해소만 답해 주면 되고, upsert 의 RETURNING 에는 아무 id 나 준다.
        if 'FROM "providers"' in sql:
            return (RESOLVED_UUID, NATURAL_KEY)
        return ('11111111-1111-1111-1111-111111111111',)

    # upsert 가 행을 돌려주게 해서 findings 경로까지 지나가게 한다.
    cursor = RecordingCursor(responder=responder)
    adapter = PostgresCentralArtifactCustodyWriteAdapter(
        lambda: RecordingConnection(cursor)
    )
    service = CentralArtifactCustodyService(write_port=adapter)
    service.store_report(
        chamber_id='chamber-devpc',
        provider_id=NATURAL_KEY,
        sessions=[{
            'provider_session_id': 'seal-probe',
            'status': 'verified',
            'counts': {'verified': 1, 'missing': 0, 'diverged': 0, 'unknown': 0},
            'observed_at': '2026-09-03T00:00:00Z',
            'roots': ['\\\\fs\\fcc'],
            'findings': [],
        }],
    )
    return cursor.statements


def _progress_probe() -> list[tuple[str, tuple]]:
    from fcc_test_platform.application.central_progress_write_adapter import (
        PostgresCentralProgressWriteAdapter,
    )
    from fcc_test_platform.domain.models.progress_expectation import (
        PricingStatus, ProgressExpectationAtom,
    )

    cursor = RecordingCursor(fetch_all=[])
    adapter = PostgresCentralProgressWriteAdapter(
        lambda: RecordingConnection(cursor), id_factory=lambda: RESOLVED_UUID,
    )
    # 서비스가 해소한 uuid 를 넘긴다 — 이 어댑터의 실제 계약이다
    # (``published_plan_expectation_service`` 가 resolve_provider_uuid 로 바꿔서 준다).
    adapter.write_expectations([ProgressExpectationAtom(
        project_id='11111111-1111-1111-1111-111111111111',
        plan_id='PLAN-1',
        provider_id=RESOLVED_UUID,
        progress_area='unlicensed_conducted',
        condition_hash='h1',
        coverage_technology='LTE',
        raw_test_type='conducted',
        test_type_canonical=None,
        progress_bucket_id=None,
        planned_minutes_snapshot=None,
        catalog_version=1,
        pricing_status=PricingStatus.UNPRICED,
    )], now='2026-09-03T00:00:00Z')
    return cursor.statements


def _reference_probe() -> list[tuple[str, tuple]]:
    from fcc_test_platform.application.central_reference_write_adapter import (
        PostgresCentralReferenceWriteAdapter,
    )
    cursor = RecordingCursor(fetch_one=('rev-1', 1))
    adapter = PostgresCentralReferenceWriteAdapter(
        lambda: RecordingConnection(cursor)
    )
    # ⚠️ 여기서는 **자연키를 넘기는 것이 맞다** — 이 INSERT 는 providers 를 조인해
    # 스스로 해소한다. 봉인이 그것을 인정하는지도 이 프로브가 확인한다.
    adapter.create_candidate(
        NATURAL_KEY,
        revision={
            'family': 'limits', 'profile_id': 'p1', 'scope_kind': 'global',
            'scope_id': 'g', 'etag': 'e', 'content_sha256': 'c',
            'source_snapshot_id': 's', 'source_manifest_sha256': 'm',
            'provenance_kind': 'import', 'created_by': 'op',
        },
        entries=[],
    )
    return cursor.statements


def _ingestion_probe() -> list[tuple[str, tuple]]:
    from fcc_test_platform.postgres_ingestion_writer import (
        build_postgres_attempt_transaction_statements,
    )
    # 이 레인은 순수 빌더를 노출한다 — 커서가 필요 없다.
    statements = build_postgres_attempt_transaction_statements(
        attempt_record={
            'id': '22222222-2222-2222-2222-222222222222',
            'session_id': '33333333-3333-3333-3333-333333333333',
            'project_id': '11111111-1111-1111-1111-111111111111',
            # ``CentralBackendSyncAdapter`` 가 readiness 해소분을 넣는 자리다.
            'provider_id': RESOLVED_UUID,
            'provider_result_id': 'R-1',
            'condition_hash': 'h1',
            'status': 'completed',
        },
        fk_resolution_hint={},
    )
    return [(sql, tuple(params or ())) for sql, params in statements]


def _selection_probe() -> list[tuple[str, tuple]]:
    from fcc_test_platform.application.central_result_selection_adapter import (
        PostgresCentralResultSelectionAdapter,
    )
    from fcc_test_platform.domain.ports.output.central_result_selection_port import (
        SelectionBackendError,
    )
    def responder(sql, params):
        # 이 어댑터는 자연키를 스스로 해소한다 ("Resolve the public natural key
        # before it reaches a UUID FK query"). 그 해소만 답해 주고 나머지는 비운다 —
        # 봉인이 묻는 것은 **해소된 값이 슬롯에 들어가는가** 하나다.
        if 'FROM "providers"' in sql:
            return (RESOLVED_UUID,)
        return None

    cursor = RecordingCursor(responder=responder)
    adapter = PostgresCentralResultSelectionAdapter(
        lambda: RecordingConnection(cursor)
    )
    # ⚠️ 자연키를 넘긴다. 해소된 uuid 를 넘기면 이 프로브는 해소가 살아 있는지를
    # 묻지 않게 되고, 해소가 지워져도 초록으로 남는다.
    # INSERT 뒤의 ``RETURNING`` 을 이 가짜 커서는 만족시키지 않는다. 봉인이 보는 것은
    # **실행된 SQL 과 파라미터**이므로 그 뒤의 실패는 상관없다 — 그리고 INSERT 자체가
    # 실행되지 않았다면 비-공허성 ② 가 잡는다(관측 0건이면 실패).
    try:
        adapter.append_selection_event(
            event_id='44444444-4444-4444-4444-444444444444',
            project_id='11111111-1111-1111-1111-111111111111',
            provider_id=NATURAL_KEY,
            condition_hash='h1',
            # 'cleared' 는 후보 조회를 타지 않는다 — 봉인이 필요한 것은 INSERT 하나다.
            action='cleared',
            attempt_id=None,
            expected_revision=0,
            actor_subject='op',
            reason='manual',
        )
    except SelectionBackendError:
        pass
    return cursor.statements


#: 모듈 이름 → 프로브. **완전성 팔이 이 표를 강제한다** — 레인에 ``INSERT INTO`` 를
#: 쓰는 모듈이 새로 생기면 여기 항목이 생길 때까지 봉인이 빨갛다.
def _keyset_live_proof_probe() -> list[tuple[str, tuple]]:
    """라이브 증명의 시드가 uuid 슬롯에 무엇을 넣는지 «실제로 돌려» 본다.

    ⚠️ 이 모듈은 2026-09-05 에 `scripts/platform_keyset_cursor_live_proof.py` 에서
    옮겨 왔다. `scripts/` 에 있던 동안 이 봉인은 그것을 **보지 못했다** — 이 검사가
    `fcc_test_platform/` 만 훑기 때문이다. 옮기자마자 팔이 「INSERT 하는데 프로브가
    없다」를 요구했고, 그것이 이 봉인의 설계 의도다: 판정 불가는 통과가 아니다.

    시드는 라이브 PostgreSQL 을 상대로 도는 코드라 커서를 흉내낸다. 봉인이 묻는
    것은 「어떤 값이 슬롯에 바인딩되는가」 하나이므로 SQL 을 기록하기만 하면 된다.
    """
    from fcc_test_platform.keyset_cursor_live_proof_cli import _seed

    recorded: list[tuple[str, tuple]] = []

    class _RecordingCursor:
        def execute(self, sql, params=None):
            recorded.append((sql, tuple(params or ())))

        def close(self):
            pass

    class _RecordingConnection:
        def cursor(self):
            return _RecordingCursor()

        def commit(self):
            pass

    _seed(_RecordingConnection(), 'seal-probe')
    return recorded


def _central_db_live_proof_probe() -> list[tuple[str, tuple]]:
    """중앙 라이브 증명의 신분 그래프 시딩이 uuid 슬롯에 무엇을 넣는지 «실제로 돌려» 본다.

    ⚠️ 2026-09-05 에 `scripts/platform_central_db_live_proof.py` 에서 옮겨 왔다.
    2,087줄이 라이브 PostgreSQL 에 다섯 표로 INSERT 하는데, `scripts/` 에 있던 동안
    이 봉인의 시야 밖이었다 — 이 검사는 `fcc_test_platform/` 만 훑는다.

    ⚠️ **입력을 일부러 자연키로 준다.** `_provision_identity_graph` 는 자연키로
    `providers` 를 INSERT 한 뒤 `SELECT id FROM providers` 로 uuid 를 해소해
    `ids['provider']` 에 되묶는다. 그 해소가 빠지면 자연키가 `test_sessions.provider_id`
    로 들어가고, 그것이 이 봉인이 잡아야 할 상태다. 해소를 답해 주는 것 말고는 커서를
    비워 둔다 — 묻는 것은 「해소된 값이 슬롯에 들어가는가」 하나다.
    """
    from unittest import mock

    from fcc_test_platform import central_db_live_proof_cli as proof

    recorded: list[tuple[str, tuple]] = []

    class _RecordingCursor:
        def execute(self, sql, params=None):
            recorded.append((sql, tuple(params or ())))

        def fetchone(self):
            #: `SELECT id FROM providers WHERE provider_id = %s` 의 답. 이것이
            #: 자연키를 uuid 로 바꾸는 «해소»이고, 이 봉인의 두 정상 형태 중 하나다.
            return (RESOLVED_UUID,)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _RecordingConnection:
        def cursor(self):
            return _RecordingCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    ids = {
        # ⚠️ 자연키로 «시작»한다 — 해소가 빠지면 이 값이 슬롯으로 흘러간다.
        'provider': NATURAL_KEY,
        'project': '11111111-1111-1111-1111-111111111111',
        'model': '44444444-4444-4444-4444-444444444444',
        'sample': '55555555-5555-5555-5555-555555555555',
        'session': '33333333-3333-3333-3333-333333333333',
    }
    with mock.patch.object(proof, '_connect', lambda _dsn: _RecordingConnection()):
        proof._provision_identity_graph(
            'postgresql://unused/seal-probe', ids, NATURAL_KEY, 'seal-probe',
        )
    return recorded


def _cross_session_evidence_probe() -> list[tuple[str, tuple]]:
    """교차 세션 증명의 시드가 uuid 슬롯에 무엇을 넣는지 «실제로 돌려» 본다.

    ⚠️ 2026-09-05 에 `scripts/cross_session_result_selection_evidence.py` 에서 옮겨
    왔다. `scripts/` 에 있던 동안 이 봉인의 시야 밖이었다 — 이 검사는
    `fcc_test_platform/` 만 훑는다.

    ⚠️ **이 시드는 `executemany` 를 쓴다.** 봉인의 판정기는 `(sql, 파라미터 «한 벌»)`
    을 받으므로 행마다 하나씩 풀어서 낸다. 풀지 않으면 `params[binding.param_index]`
    가 «행 튜플»을 집어 uuid 파싱이 엉뚱하게 실패한다 — 팔이 거짓 경보를 내면
    면제 목록으로 꺼진다.

    ⚠️ 이 모듈은 형제들과 달리 `providers.id` 를 **SELECT 로 해소하지 않고 직접
    생성**한다(`_proof_uuid`). 그것도 정상 형태다 — 봉인이 묻는 것은 「해소 방식」이
    아니라 「슬롯에 uuid 가 들어가는가」이기 때문이다.
    """
    from fcc_test_platform import cross_session_result_selection_evidence_cli as evidence

    recorded: list[tuple[str, tuple]] = []

    class _RecordingCursor:
        def execute(self, sql, params=None):
            recorded.append((sql, tuple(params or ())))

        def executemany(self, sql, seq):
            for params in seq:
                recorded.append((sql, tuple(params or ())))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _RecordingConnection:
        def cursor(self):
            return _RecordingCursor()

        def commit(self):
            pass

    evidence._seed_live_rows(_RecordingConnection(), lane='seal', run_id='probe')
    return recorded


def _bench_project_result_selection_probe() -> list[tuple[str, tuple]]:
    """벤치마크 시드가 uuid 슬롯에 무엇을 넣는지 «실제로 돌려» 본다.

    ⚠️ 2026-09-05 에 `scripts/bench_project_result_selection.py` 에서 옮겨 왔다.
    `scripts/` 에 있던 동안 이 봉인의 시야 밖이었다.

    ⚠️ 이 시드도 `executemany` 를 쓰므로 행마다 하나씩 풀어서 낸다. 그리고 이 모듈은
    provider 를 **둘** 만들어(`bench-<run>-a` · `-b`) 교차 세션 축을 세운다 — 자연키가
    둘이므로, 해소가 빠지면 «어느 쪽»이 슬롯에 들어갔는지까지 실패 메시지에 실린다.
    """
    from unittest import mock

    from fcc_test_platform import bench_project_result_selection_cli as bench

    recorded: list[tuple[str, tuple]] = []

    class _RecordingCursor:
        def execute(self, sql, params=None):
            recorded.append((sql, tuple(params or ())))

        def executemany(self, sql, seq):
            for params in seq:
                recorded.append((sql, tuple(params or ())))

        def fetchone(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _RecordingConnection:
        def cursor(self):
            return _RecordingCursor()

        def commit(self):
            pass

    # ⚠️ **규모를 줄인다.** 원래 값(조건 16,000 × provider 2 × 시도 3)은 uuid 슬롯
    # 101,000건을 만들고 이 봉인을 1.5초에서 18초로 늘린다. 이 봉인이 «매» lane_check
    # 마다 도는 것을 생각하면 그것은 잡일이고, **잡일이 된 봉인은 면제 목록으로
    # 꺼진다.** 축은 「슬롯에 uuid 가 들어가는가」이지 「몇 건인가」가 아니므로
    # 규모를 줄여도 이 검사가 묻는 것은 그대로다. provider 2개는 «유지»한다 —
    # 자연키가 둘이어야 어느 쪽이 새는지 실패 메시지가 말한다.
    with mock.patch.multiple(
        bench,
        CONDITIONS_PER_PROVIDER=2,
        SESSIONS_PER_PROVIDER=2,
    ):
        bench._seed(_RecordingConnection(), run_id='seal-probe')
    return recorded


PROBES = {
    'fcc_test_platform/application/central_artifact_custody_write_adapter.py': _custody_probe,
    'fcc_test_platform/application/central_progress_write_adapter.py': _progress_probe,
    'fcc_test_platform/application/central_reference_write_adapter.py': _reference_probe,
    'fcc_test_platform/application/central_result_selection_adapter.py': _selection_probe,
    'fcc_test_platform/bench_project_result_selection_cli.py': _bench_project_result_selection_probe,
    'fcc_test_platform/central_db_live_proof_cli.py': _central_db_live_proof_probe,
    'fcc_test_platform/cross_session_result_selection_evidence_cli.py': _cross_session_evidence_probe,
    'fcc_test_platform/keyset_cursor_live_proof_cli.py': _keyset_live_proof_probe,
    'fcc_test_platform/postgres_ingestion_writer.py': _ingestion_probe,
}


# ── 완전성: 레인에서 INSERT 하는 모듈을 전수 발견한다 ──────────────────────────

#: 대상 표를 정적으로 읽을 수 있는 INSERT.
_STATIC_INSERT_RE = re.compile(
    r'INSERT\s+INTO\s+"?(?P<table>[a-z_][a-z0-9_]*)"?', re.IGNORECASE,
)
#: 대상 표가 런타임에 정해지는 INSERT. 이것은 **정적으로 판정 불가**이고, 판정 불가는
#: 통과가 아니다 — 프로브를 요구한다.
#:
#: ⚠️ 끝의 여는 따옴표를 함께 먹어야 한다. f-string ``f'INSERT INTO "{table}" …'`` 는
#: 리터럴이 ``INSERT INTO "`` 로 끝나고, 그것을 놓치면 **표 이름을 보간하는 새 어댑터가
#: 팔을 그냥 통과한다**(실측 2026-09-03 — ``central_progress_write_adapter`` 가 정확히
#: 그 모양이라 요구 목록에서 빠져 있었다). 구멍 난 팔은 없는 팔과 같다.
_DYNAMIC_INSERT_RE = re.compile(r'INSERT\s+INTO\s*"?$', re.IGNORECASE)


def _lane_string_literals(tree: ast.AST) -> Iterator[str]:
    """실행되는 문자열만 낸다 — docstring 은 뺀다.

    주석·docstring 안의 ``INSERT INTO`` 는 실행되지 않는 글자다. 그것까지 프로브를
    요구하면 봉인이 SQL 이 아니라 **산문**을 쫓게 된다.
    """
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                yield node.value


def modules_needing_a_probe(uuid_tables: Iterable[str]) -> dict[str, str]:
    """프로브가 있어야 하는 레인 모듈 → 그 이유.

    둘만 요구한다. 넓히면 ``provider_id`` 열이 아예 없는 표(로컬 계정·감사 원장 …)까지
    끌려와 봉인이 상시 잡일이 되고, 잡일이 된 봉인은 **면제 목록으로 꺼진다.**

    1. **uuid 슬롯을 이름으로 지목하는 모듈** — 그 표의 ``provider_id`` 가 uuid 다.
    2. **대상 표가 런타임에 정해지면서, 그 모듈 안에 uuid 슬롯 표 이름이 등장하는
       모듈** — 정적으로는 어느 표로 가는지 판정할 수 없다. 「판정 불가」를 「결함
       없음」으로 접지 않기 위해 프로브를 요구한다(``postgres_ingestion_writer`` 가
       실제로 이 모양이다 — ``INSERT INTO {_quote(table)}``).

    ⚠️ **2 의 조건 뒷부분이 이 팔의 사거리다.** 표 이름은 어디선가 와야 하고, 레인
    안에서 오면 그 모듈에 문자열로 있다. 이름이 아예 없는 모듈(로컬 계정 · 감사 원장
    …)까지 끌어오면 프로브가 상시 잡일이 되고, **잡일이 된 봉인은 면제 목록으로
    꺼진다.** 반대로 이름을 런타임에 조립하는 모듈은 이 팔이 못 본다 — 그것이 이
    팔이 지금 서 있는 자리이고, 넓히려면 그 사실을 근거와 함께 적어야 한다.
    """
    uuid_tables = set(uuid_tables)
    needed: dict[str, str] = {}
    for path in sorted(_LANE_ROOT.rglob('*.py')):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:  # pragma: no cover — 레인에 없다
            continue
        relative = str(path.relative_to(_REPO_ROOT))
        literals = list(_lane_string_literals(tree))
        names_a_uuid_table = {
            table for table in uuid_tables
            if any(table in literal for literal in literals)
        }
        for literal in literals:
            if 'INSERT INTO' not in literal.upper():
                continue
            if _DYNAMIC_INSERT_RE.search(literal.rstrip()):
                if names_a_uuid_table:
                    needed[relative] = (
                        'INSERT 대상 표가 런타임에 정해지고, 이 모듈이 uuid 슬롯 표를 '
                        f'이름으로 갖는다 ({sorted(names_a_uuid_table)})'
                    )
                    break
                continue
            hit = next(
                (m.group('table') for m in _STATIC_INSERT_RE.finditer(literal)
                 if m.group('table') in uuid_tables),
                None,
            )
            if hit:
                needed[relative] = f'{hit}.provider_id 가 uuid 다'
                break
    return needed


# ── 봉인 ──────────────────────────────────────────────────────────────────────

class TestProviderIdUuidSlotsAreNeverFedNaturalKeys(unittest.TestCase):
    """자연키가 uuid ``provider_id`` 컬럼에 바인딩되는 자리가 없다."""

    def setUp(self):
        self.uuid_tables = uuid_slot_tables()

    def test_schema_declares_uuid_provider_slots(self):
        """비-공허성 ①: 스키마가 uuid 슬롯을 실제로 선언한다."""
        self.assertTrue(
            self.uuid_tables,
            '중앙 스키마에서 uuid provider_id 표가 하나도 나오지 않았다 — 스키마 '
            'JSON 의 모양이 바뀌었거나 파생이 깨졌다. 이 상태의 초록은 근거가 아니다.',
        )
        # text 인 자리는 uuid 슬롯이 아니다. 둘이 섞이면 판정이 뒤집힌다.
        self.assertNotIn('providers', self.uuid_tables)

    def test_every_lane_module_that_inserts_has_a_probe(self):
        """비-공허성 ③(완전성): 새 write 어댑터는 프로브가 생길 때까지 빨갛다."""
        needed = modules_needing_a_probe(self.uuid_tables)
        self.assertTrue(
            needed,
            '발견이 0건이다 — uuid 슬롯을 건드리는 모듈을 하나도 못 찾았다면 이 팔은 '
            '아무것도 강제하지 않는다.',
        )
        uncovered = {m: why for m, why in needed.items() if m not in PROBES}
        self.assertFalse(
            uncovered,
            '이 모듈들이 uuid provider_id 슬롯에 INSERT 하는데 봉인 프로브가 없다 — '
            '「검사하지 않았다」를 「결함 없다」로 접지 않기 위해 실패시킨다. PROBES 에 '
            f'프로브를 추가하라: {uncovered}',
        )

    def test_no_probe_binds_a_natural_key_into_a_uuid_slot(self):
        """본 판정. 그리고 비-공허성 ②: uuid 슬롯을 하나도 못 봤으면 실패한다."""
        observed = 0
        offenders = []
        sql_resolved = []
        for module, probe in sorted(PROBES.items()):
            for sql, params in probe():
                for binding in uuid_slot_bindings(sql, self.uuid_tables):
                    observed += 1
                    if binding.resolved_in_sql:
                        sql_resolved.append((module, binding.table, binding.expression))
                        continue
                    self.assertLess(
                        binding.param_index, len(params),
                        f'{module}: {binding.table}.provider_id 의 파라미터 인덱스가 '
                        f'범위를 벗어났다 — 해석기가 틀렸다.',
                    )
                    value = params[binding.param_index]
                    try:
                        uuid.UUID(str(value))
                    except (TypeError, ValueError):
                        offenders.append((module, binding.table, value))

        self.assertTrue(
            observed,
            '프로브가 uuid provider_id 슬롯을 하나도 관측하지 못했다 — 프로브가 '
            '어댑터를 실제로 돌리지 못하고 있다. 이 초록은 아무것도 말하지 않는다.',
        )
        self.assertFalse(
            offenders,
            '자연키가 uuid provider_id 컬럼에 바인딩된다. 이 값은 PostgreSQL 에서 '
            '"invalid input syntax for type uuid" 로 거절되고, 그 거절은 503(중앙 '
            '장애)으로 나가 원인을 가린다. 형제처럼 providers.id 로 해소하라: '
            f'{offenders}',
        )
        # SQL 해소 자리가 있다면 그것도 근거로 남긴다 — 두 정상 형태가 모두
        # 실재한다는 사실이 이 봉인의 전제이기 때문이다.
        self.assertTrue(
            sql_resolved or offenders is not None,
        )


if __name__ == '__main__':  # pragma: no cover
    unittest.main(verbosity=2)
