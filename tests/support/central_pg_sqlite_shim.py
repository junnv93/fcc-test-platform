"""SQLite stand-in for the central PostgreSQL chamber registry — test SSOT.

The central chamber write/read adapters (``PostgresCentralChamber{Write,Read}Adapter``)
speak psycopg ``%s`` paramstyle against a ``() -> DbConnection`` factory. For
in-process tests we point that factory at a temp-file SQLite DB through a thin
``%s``→``?`` rewriting shim, and build the ``chamber_availability`` VIEW verbatim
from the schema JSON SSOT (``docs/platform/central_db_schema.v1.json``). This keeps
the SQLite path's VIEW semantics (ROW_NUMBER latest-per-chamber projection) a
byte-for-byte copy of the production PostgreSQL VIEW — the cross-DB equivalence
that ``test_chamber_distributed_ops_e2e_p9`` asserts against a *real* PostgreSQL.

This module is the **single owner of the shim, repository-wide** (2026-09-07).
It imports no domain / infrastructure code — only stdlib + the production
``SqliteConnectionFactory`` (the project's sole raw ``sqlite3.connect`` entry
point), which is why nothing here needs a raw-connect exemption.

``tests/test_central_pg_sqlite_shim_behaviour.py`` owns both halves of that
claim: it runs the translation against a real engine *with a control*, and it
fails if a private paramstyle wrapper reappears anywhere in ``tests/`` or
``scripts/``.
"""
from __future__ import annotations

import json
import socket
import re
import sqlite3
import tempfile
from pathlib import Path

from fcc_test_contracts.common.sqlite_connection_factory import SqliteConnectionFactory


__all__ = [
    'QmarkCursor',
    'QmarkConnection',
    'RowcountBlindCursor',
    'RowcountBlindConnection',
    'AdoptedQmarkConnection',
    'UnknownCentralColumnType',
    'make_sqlite_central',
    'create_tables_from_schema',
    'central_view_select',
    'create_central_view',
    'pg_select_to_sqlite',
    'chamber_availability_view_select',
    'free_port',
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / 'docs' / 'platform' / 'central_db_schema.v1.json'


#: ``FOR UPDATE`` (and its ``NOWAIT``/``SKIP LOCKED`` variants) at the tail of a
#: statement. Anchored to the end so a column or literal containing the words
#: cannot be clipped out of the middle of a query.
#: ``now()`` as a whole call, not as the tail of an identifier. A plain
#: ``str.replace('now()', …)`` also rewrites ``'know()'`` inside a string
#: literal — measured by an independent review.
_NOW_CALL = re.compile(r'\bnow\(\)', re.IGNORECASE)


def _substitute_paramstyle(statement: str) -> str:
    """psycopg ``%s`` -> sqlite ``?``, honouring the ``%%`` literal escape.

    Left-to-right, two characters at a time, because that is the grammar
    psycopg itself implements: ``%%`` is one literal percent and ``%s`` is a
    placeholder. A blanket ``str.replace`` conflates them and corrupts
    ``LIKE '%sample'`` into ``LIKE '?ample'`` — wrong rows, no error, and the
    execute-time post-condition cannot see it because the ``%s`` really is gone.

    Any other ``%`` is left exactly as written. psycopg would reject it, and
    inventing a repair here would make this shim disagree with the driver it
    stands in for.
    """
    out: list[str] = []
    index = 0
    length = len(statement)
    while index < length:
        char = statement[index]
        if char != '%' or index + 1 >= length:
            out.append(char)
            index += 1
            continue
        nxt = statement[index + 1]
        if nxt == '%':
            out.append('%')
            index += 2
        elif nxt == 's':
            out.append('?')
            index += 2
        else:
            out.append(char)
            index += 1
    return ''.join(out)


_FOR_UPDATE_SUFFIX = re.compile(r'\s+FOR\s+UPDATE(\s+(NOWAIT|SKIP\s+LOCKED))?\s*$', re.IGNORECASE)


class QmarkCursor:
    """psycopg ``%s`` → sqlite ``?`` rewriting cursor wrapper.

    ⚠️ **This class is the repository's single ``%s``→``?`` translating cursor,
    and that singularity is load-bearing** (2026-09-07). Thirteen fixtures used
    to re-implement the same six lines — eight of them over a raw
    ``sqlite3.connect``, which is why the raw-connect ratchet had to decide,
    from source text alone, whether each copy "describes a translating
    wrapper". (The other five were invisible to that census precisely because
    they wrapped a connection somebody else had opened: counting the copies by
    the ratchet's own key understated them by 60%.) Three independent adversarial
    reviews broke that decision procedure by the same mechanism: the exemption
    is a claim about a *runtime object*, ``obj.execute`` resolves over
    ``type(obj).__mro__`` and ``obj.__dict__``, and every slot in that search is
    writable by ordinary code — so no enumeration of "ways a definition can lie"
    closes. Owning the wrapper here replaces an undecidable question ("does this
    source describe a translator?") with a decidable one ("is this the identity
    we tested?"). ``TestQmarkShimIsTheOnlyParamstyleShim`` keeps it that way.
    """

    def __init__(self, raw: sqlite3.Cursor) -> None:
        self._raw = raw

    @property
    def rowcount(self) -> int:
        """Rows affected by the last statement, delegated to the real cursor.

        psycopg reports the affected-row count here and ``-1`` when it does not
        know. This used to be a plain ``-1`` attribute that nothing ever
        updated, which is not "unknown" — it is *always* unknown, i.e. a driver
        that can never answer. Adapters in ``src/`` branch on
        ``cursor.rowcount == 0`` to distinguish "no such row" from "updated", so
        a permanently-``-1`` shim silently removed those branches from every
        test that used it. Delegation is the faithful emulation; SQLite already
        answers ``-1`` for SELECT, so only UPDATE/DELETE change.

        A driver that genuinely cannot report is modelled by
        :class:`RowcountBlindCursor` — deliberately a *subclass*, so the
        deficiency is one named object rather than a second private copy.
        """
        return self._raw.rowcount

    def translate(self, statement: str) -> str:
        """PostgreSQL statement → the SQLite text this cursor will execute.

        Composition of the two halves below. Callers and tests use this; the
        execute path does **not** — see :meth:`_run`.
        """
        return self.translate_dialect(_substitute_paramstyle(statement))

    def translate_dialect(self, statement: str) -> str:
        """Cross-dialect concessions, applied *after* paramstyle substitution.

        ⚠️ **Paramstyle is deliberately not part of this seam.** It used to be:
        one overridable ``translate`` did both, and :meth:`execute` asserted
        afterwards that no ``%s`` survived. Two things killed that design.

        First, the assertion cannot be a function of the output. Honouring
        psycopg's ``%%`` escape means a *correct* translation of
        ``LIKE '%%sample'`` is ``LIKE '%sample'`` — which contains the two
        characters ``%s`` and is nonetheless right. An output-only check must
        either miss real failures or reject correct ones.

        Second, and decisive: an assertion is something an override must not
        defeat, and ``python -O`` deletes assertions outright. The property is
        better held by **construction** — :meth:`_run` calls the module-level
        :func:`_substitute_paramstyle` directly, so no subclass can route
        around it. This seam stays overridable because dialect concessions are
        legitimately per-fixture; it receives text whose placeholders are
        already ``?``, so forgetting to call ``super()`` here loses a
        concession, never the paramstyle.
        """
        # ``now()`` is PostgreSQL's clock function; SQLite spells it
        # ``CURRENT_TIMESTAMP``. This is a **shim concession**, not production
        # behaviour: the rewrite exists so adapter SQL can run verbatim here
        # instead of being retyped for the test (a retyped copy is an oracle that
        # shares its assumptions with nothing — it stops testing the real query).
        # ⚠️ Word-anchored: the plain ``str.replace`` this used to be also
        # rewrote the tail of ``'know()'``.
        rewritten = _NOW_CALL.sub('CURRENT_TIMESTAMP', statement)
        # ``SELECT … FOR UPDATE`` is row-level locking, which SQLite has no
        # syntax for — a write transaction locks the database instead. Same kind
        # of concession as ``now()`` above, and the same reason: the adapter's
        # SQL runs here verbatim rather than being retyped.
        #
        # ⚠️ The concession has a consequence worth naming: this shim CANNOT
        # reproduce a lost update caused by a MISSING ``FOR UPDATE``, because
        # SQLite would have serialized the writers anyway. A test that asserts
        # per-key merge behaviour here therefore proves the merge rule and NOT
        # the locking — which is why the equipment-config seal asserts the
        # presence of ``FOR UPDATE`` in the adapter SQL directly, and why the
        # locking itself belongs to the live-PostgreSQL proof script.
        return _FOR_UPDATE_SUFFIX.sub('', rewritten)

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        self._run(statement, parameters)

    def executemany(self, statement: str, seq_of_parameters) -> None:
        """DB-API ``executemany``, translated through the same seam.

        Present so a consumer that needs it does not add a private wrapper —
        which is how every one of the thirteen copies started. Anything routed
        around :meth:`_run` skips the post-condition, so new DB-API surface
        belongs here rather than in a subclass.
        """
        self._run(statement, seq_of_parameters, many=True)

    def _run(self, statement: str, parameters, *, many: bool = False) -> None:
        # ⚠️ ``_substitute_paramstyle`` is called here, **not** through
        # ``self``. That is the whole guarantee: translation is not something a
        # subclass can forget, disable, or optimise away. The previous design
        # routed it through an overridable seam and asserted afterwards, which
        # an independent review defeated twice over — by overriding the seam,
        # and by running the suite under ``python -O``.
        rewritten = self.translate_dialect(_substitute_paramstyle(statement))
        if many:
            self._raw.executemany(rewritten, parameters)
        else:
            self._raw.execute(rewritten, parameters)

    def fetchall(self):
        return self._raw.fetchall()

    def fetchone(self):
        return self._raw.fetchone()

    def close(self) -> None:
        self._raw.close()


class RowcountBlindCursor(QmarkCursor):
    """A cursor that translates but never reports ``rowcount`` — a *deficiency* double.

    Some drivers cannot report affected rows. Reading their ``-1``/``None`` as
    "no such row" turns every heartbeat on a correctly-registered chamber into a
    404, which is the quietest way a conditional INSERT can break. The test that
    pins that behaviour needs a cursor with the deficiency and nothing else —
    so it inherits the translation instead of restating it.
    """

    @property
    def rowcount(self) -> int:
        return -1


class QmarkConnection:
    """``DbConnection``-shaped SQLite connection (psycopg paramstyle shim).

    The raw ``sqlite3.Connection`` is private: no method returns it and no
    attribute aliases it, so ordinary use reaches SQL only through
    :class:`QmarkCursor`.

    ⚠️ **"Private" is not "unreachable", and the difference is worth stating
    rather than overclaiming.** An earlier version of this docstring said the
    connection *is never handed out*; an independent review showed
    ``conn.cursor()._raw.connection is conn._conn`` — two hops through DB-API's
    own back-reference. Nothing in Python can close that, and it does not need
    closing: reaching it is explicit, and untranslated SQL sent that way fails
    **loudly** at the engine (``near "%": syntax error``). The property this
    class actually guarantees is that the *ordinary* path translates, and that
    every escape from it is noisy.
    """

    #: The cursor class handed to callers. Subclasses override it to swap in a
    #: deficiency double; nothing else about the connection changes.
    cursor_class: type[QmarkCursor] = QmarkCursor

    def __init__(self, db_path: str) -> None:
        self._conn = SqliteConnectionFactory(db_path).create()

    def cursor(self) -> QmarkCursor:
        return self.cursor_class(self._conn.cursor())

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


class RowcountBlindConnection(QmarkConnection):
    """:class:`QmarkConnection` whose cursors cannot report ``rowcount``."""

    cursor_class = RowcountBlindCursor


class AdoptedQmarkConnection(QmarkConnection):
    """Qmark wrapper over a connection whose lifetime the **caller** owns.

    Some fixtures hold one shared connection (an in-memory DB dies with its
    connection) and hand a fresh wrapper to each reader. Those wrappers must not
    close what they did not open, so ``close()`` is a no-op here and the
    ownership is stated by the class name rather than by a comment on a private
    copy. Everything else — the translation, the cursor class, the assertion —
    is inherited, which is the point: ownership is a lifetime question, not a
    paramstyle question, and only the lifetime differs.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:  # noqa: D107 - see class docstring
        self._conn = conn

    def close(self) -> None:
        """No-op — the caller opened this connection and will close it."""


#: 중앙 스키마 타입 → SQLite 타입. SQLite 는 타입에 관대하지만 **선언은 SSOT 에서
#: 파생**해야 컬럼 이름/집합이 드리프트하면 픽스처가 깨진다 — 손으로 베낀 DDL 은
#: 프로덕션이 컬럼을 하나 더해도 조용히 옛 모양을 계속 테스트한다.
_SQLITE_TYPES = {
    'uuid': 'TEXT', 'text': 'TEXT', 'integer': 'INTEGER',
    'boolean': 'INTEGER', 'timestamp': 'TEXT', 'jsonb': 'TEXT', 'json': 'TEXT',
    'numeric': 'REAL', 'double': 'REAL',
}


#: PostgreSQL 기본값 → SQLite 표현. **기본값을 버리면 안 된다** — 이 저장소에서
#: "DB 가 id/타임스탬프를 소유한다"는 것은 실제 불변식이고(중앙 유입 계약), 기본값을
#: 뺀 픽스처는 그 소유권이 없는 세계를 테스트한다(id 가 NULL 로 남는다).
_SQLITE_DEFAULTS = {
    'gen_random_uuid()': "(lower(hex(randomblob(16))))",
    'now()': 'CURRENT_TIMESTAMP',
}


class UnknownCentralColumnType(KeyError):
    """중앙 스키마 SSOT 가 이 shim 에 매핑 없는 컬럼 타입을 선언했다.

    **조용한 폴백보다 이 예외가 낫다.** 이전 구현은 미지 타입을 ``TEXT`` 로 떨어뜨렸는데,
    그러면 SSOT 에 새 타입이 도입된 날 그 사실이 *실패가 아니라 침묵으로* 지나가고 픽스처는
    프로덕션과 다른 타입을 시험한다. 실측 2026-08-26: 선언된 433 컬럼의 타입 어휘는 7종
    (``text`` 201 · ``uuid`` 100 · ``timestamp`` 72 · ``integer`` 27 · ``json`` 25 ·
    ``boolean`` 6 · ``numeric`` 2)이고 아래 매핑이 전부 커버한다 — 즉 이 예외는 **오늘 한
    번도 발화하지 않으며**, 발화하는 날이 정확히 사람이 판정해야 하는 날이다.
    """


def _sqlite_column_type(table: str, column: str, spec) -> str:
    """SSOT 컬럼 선언 → SQLite 타입. 미지 타입/타입 부재는 **loud** 하게 거부한다."""
    if not isinstance(spec, dict) or 'type' not in spec:
        raise UnknownCentralColumnType(
            f'{table}.{column}: central schema column declares no "type"; '
            f'the shim will not guess one'
        )
    declared = str(spec['type']).lower()
    try:
        return _SQLITE_TYPES[declared]
    except KeyError:
        raise UnknownCentralColumnType(
            f'{table}.{column}: central schema type {declared!r} has no SQLite mapping. '
            f'Known types: {sorted(_SQLITE_TYPES)}. Add the mapping here — do not let the '
            f'fixture silently test a different type than production.'
        ) from None


#: PostgreSQL SELECT → SQLite 방언. 규칙은 **둘뿐이고 닫혀 있다**, 그리고 이 상수가
#: 저장소 전체의 유일한 정의다. 번역 규칙이 소비자마다 갈라지면 두 픽스처가 서로 다른
#: SQLite 를 시험하면서 둘 다 "SSOT 에서 파생했다" 고 주장한다 — 실측 2026-08-26 에
#: 번역 사본 4개와 무번역 호출 3곳이 공존했고, 무번역 쪽은 실행 환경의 SQLite 버전에
#: 조용히 의존하고 있었다.
_PG_TO_SQLITE_SELECT_RULES = (
    ('= true', '= 1'),
    ('IS NOT DISTINCT FROM', 'IS'),
)


def pg_select_to_sqlite(select_sql: str) -> str:
    """중앙 뷰 SELECT 의 PostgreSQL 방언을 SQLite 방언으로 옮긴다."""
    for pg_form, sqlite_form in _PG_TO_SQLITE_SELECT_RULES:
        select_sql = select_sql.replace(pg_form, sqlite_form)
    return select_sql


def central_view_select(kind: str, name: str) -> str:
    """스키마 JSON SSOT 의 뷰/구체화뷰 SELECT 를 SQLite 방언으로 돌려준다.

    ``kind`` 는 ``'views'`` 또는 ``'materialized_views'``. 뷰마다 전용 함수를 만드는 것은
    손-복사의 다른 얼굴이므로 **하나의 파라미터화된 진입점**만 둔다.
    """
    schema = json.loads(_SCHEMA_PATH.read_text(encoding='utf-8'))
    if kind not in ('views', 'materialized_views'):
        raise KeyError(
            f'unknown central view kind {kind!r}; expected "views" or "materialized_views"'
        )
    try:
        select_sql = schema[kind][name]['select']
    except KeyError:
        raise KeyError(
            f'central schema declares no {kind} named {name!r}; '
            f'known: {sorted(schema[kind])}'
        ) from None
    return pg_select_to_sqlite(select_sql)


def create_central_view(conn, kind: str, name: str) -> None:
    """SSOT 에서 파생한 SELECT 로 SQLite VIEW 를 만든다.

    구체화뷰도 SQLite 에서는 평범한 VIEW 로 선다 — 이 픽스처가 묻는 것은 *투영이 어떤
    컬럼을 참조하는가* 이지 갱신 정책이 아니다.
    """
    conn.execute(f'CREATE VIEW IF NOT EXISTS "{name}" AS {central_view_select(kind, name)}')


def create_tables_from_schema(conn, table_names) -> None:
    """중앙 스키마 JSON SSOT 에서 CREATE TABLE + 인덱스를 **파생**해 SQLite 에 만든다.

    FK 는 만들지 않는다 — 이 헬퍼로 테이블 부분집합만 세우는 것이 목적이고, 참조
    대상까지 전부 끌어오면 픽스처가 스키마 전체가 된다. UNIQUE 인덱스는 만든다:
    upsert 의 conflict target 이라 없으면 대상 SQL 자체가 성립하지 않는다.
    """
    schema = json.loads(_SCHEMA_PATH.read_text(encoding='utf-8'))
    for name in table_names:
        table = schema['tables'][name]
        columns = []
        for column, spec in table['columns'].items():
            sql_type = _sqlite_column_type(name, column, spec)
            declaration = f'"{column}" {sql_type}'
            raw_default = spec.get('default')
            if raw_default is not None:
                default = _SQLITE_DEFAULTS.get(str(raw_default), str(raw_default))
                declaration += f' DEFAULT {default}'
            columns.append(declaration)
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{name}" ({", ".join(columns)})')
        for index in table.get('indexes', []):
            unique = 'UNIQUE ' if index.get('unique') else ''
            if index.get('where'):
                # 부분 인덱스는 술어까지 옮겨야 의미가 같다.
                predicate = f' WHERE {index["where"]}'
            else:
                predicate = ''
            cols = ', '.join(f'"{c}"' for c in index['columns'])
            conn.execute(
                f'CREATE {unique}INDEX IF NOT EXISTS "{index["name"]}" '
                f'ON "{name}" ({cols}){predicate}'
            )
    conn.commit()


def chamber_availability_view_select() -> str:
    """The ``chamber_availability`` VIEW SELECT from the schema JSON SSOT.

    ``central_view_select`` 로 위임한다. 그 함수는 SQLite 방언 번역을 적용하지만 이 뷰의
    산출은 **byte-identical** 이다 — 실측 2026-08-26: 세 일반 뷰(``active_claims`` ·
    ``project_member_permissions`` · ``chamber_availability``) 어디에도 번역 대상 토큰이
    없고, 번역이 실제로 필요한 것은 ``coverage_by_condition_hash`` 하나뿐이다.
    """
    return central_view_select('views', 'chamber_availability')


def make_sqlite_central() -> str:
    """Create a temp-file SQLite central registry + the schema-JSON availability VIEW.

    Returns the DB path (caller owns cleanup). Mirrors the production
    ``chamber_nodes`` + ``chamber_heartbeat_events`` columns the adapters write +
    the ``chamber_availability`` VIEW the read adapter selects.
    """
    fd = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    fd.close()
    conn = SqliteConnectionFactory(fd.name).create()
    # ⚠️ 손으로 베낀 DDL 이 아니라 **스키마 JSON SSOT 에서 파생**한다. 예전에는 컬럼을
    # 여기 그대로 적어 두었는데, 프로덕션이 컬럼을 하나 더한 순간 이 픽스처는 옛 모양을
    # 계속 테스트했고 실패는 어댑터 SQL 이 실행될 때에야 드러났다(실측).
    create_tables_from_schema(conn, ['chamber_nodes', 'chamber_heartbeat_events'])
    conn.execute(f'CREATE VIEW chamber_availability AS {chamber_availability_view_select()}')
    conn.commit()
    conn.close()
    return fd.name


def free_port() -> int:
    """Bind an ephemeral port, release it, and return the number (loopback test SSOT)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]
    finally:
        s.close()
