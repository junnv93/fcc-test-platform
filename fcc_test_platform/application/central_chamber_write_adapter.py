"""Central PostgreSQL chamber registry / heartbeat write adapter (멀티챔버 P2, 2026-06-15).

``PostgresCentralChamberWriteAdapter`` implements ``CentralChamberWritePort`` against
the central ``chamber_nodes`` registry + the ``chamber_heartbeat_events`` append-only
ledger (docs/platform/central_db_schema.v1.json SSOT). It is the single write path for
chamber registration + heartbeat push.

Design (mirrors ``PostgresCentralClaimWriteAdapter``):

- **injected ``connection_factory``** (``() -> DbConnection``). The concrete psycopg
  connection is built lazily by the composition root; this module imports no
  PostgreSQL driver (frozen-exe safe — enforced by ``tests/test_platform_chamber_api_p2.py``).
- **append-only heartbeat**: ``append_heartbeat`` only ever ``INSERT INTO
  chamber_heartbeat_events`` — OFFLINE is never stored, it is derived by the read
  service from heartbeat absence/staleness (mirrors claim_events → active_claims).
- **failure-mode separation** (부채 청산 M2, 2026-07-30): the heartbeat INSERT is
  *conditional* — ``INSERT ... SELECT %s, ... WHERE EXISTS (SELECT 1 FROM
  chamber_nodes WHERE chamber_id = %s)``. An unregistered chamber writes 0 rows,
  which the adapter promotes to ``ChamberNotFoundError`` (→ 404 "register first"),
  leaving ``ChamberWriteError`` (→ 503) to mean what it says: the backend is down.
  Three properties make this the right shape rather than a pre-flight SELECT:
  (1) **no extra round trip** on a high-frequency path — one statement, same as
  before; (2) **no TOCTOU** — the existence test and the insert are the *same*
  statement, so there is no window between "checked" and "wrote"; (3) **no driver
  string parsing** — the verdict rides on ``DbCursor.rowcount``, a value the port
  Protocol already declares, not on a constraint-violation message that shifts
  with driver/locale/PG version. Residual window: a chamber deleted *during* the
  statement is still caught by the ``chamber_id`` foreign key and surfaces as
  ``ChamberWriteError`` (503) — a genuine race, reported as a backend failure
  rather than silently swallowed.
- **idempotent registry upsert**: ``register_chamber`` upserts on the natural key
  ``chamber_id`` (``INSERT ... ON CONFLICT (chamber_id) DO UPDATE``) so re-registering
  the same chamber refreshes its address/name (the registry is mutable, like
  ``providers`` — not a ledger).
- **``%s`` paramstyle** (psycopg) — every value is a bound parameter (never
  interpolated) so the writes are injection-safe.
- **loud-fail**: a connection/query failure raises ``ChamberWriteError`` (never a
  silent no-op that would make a live chamber invisible / appear OFFLINE).
- column SSOT: ``CHAMBER_NODE_WRITE_COLUMNS`` / ``HEARTBEAT_EVENT_COLUMNS`` mirror the
  schema table columns; order defines the INSERT column list + the positional params.
"""
from __future__ import annotations

import json
from typing import Callable, Mapping, Optional

from application.central_contract.envelope_helpers import apply_flat_merge_patch
from domain.ports.output.central_chamber_write_port import (
    ChamberNotFoundError,
    ChamberWriteError,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = [
    'CHAMBER_HEARTBEAT_EVENTS_TABLE',
    'CHAMBER_NODES_TABLE',
    'CHAMBER_NODE_WRITE_COLUMNS',
    'CHAMBER_REGISTRY_NATURAL_KEY',
    'HEARTBEAT_EVENT_COLUMNS',
    'INSERT_HEARTBEAT_EVENT_SQL',
    'UPDATE_CHAMBER_STORAGE_ROOT_SQL',
    'UPDATE_CHAMBER_WEB_SESSION_APPROVAL_SQL',
    'SELECT_CHAMBER_SETTINGS_SQL',
    'SELECT_CHAMBER_EQUIPMENT_CONFIG_SQL',
    'SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL',
    'UPDATE_CHAMBER_EQUIPMENT_CONFIG_SQL',
    'UPSERT_CHAMBER_NODE_SQL',
    'PostgresCentralChamberWriteAdapter',
]


CHAMBER_NODES_TABLE = 'chamber_nodes'
CHAMBER_HEARTBEAT_EVENTS_TABLE = 'chamber_heartbeat_events'

#: Registry natural key — the column the heartbeat ledger references and the
#: column the registry upsert resolves conflicts on. One name, two uses: the
#: existence predicate and the ON CONFLICT target must never drift apart.
CHAMBER_REGISTRY_NATURAL_KEY = 'chamber_id'

#: Registry columns written on register (docs/platform/central_db_schema.v1.json
#: chamber_nodes). Order defines the INSERT column list + the positional params.
CHAMBER_NODE_WRITE_COLUMNS: tuple[str, ...] = (
    'id',
    'chamber_id',
    'name',
    'base_url',
    'enabled',
    'heartbeat_ttl_seconds',
    'artifact_storage_root',
    'created_at',
    'updated_at',
)

#: On a chamber_id conflict, refresh the mutable fields (NOT id/chamber_id/created_at).
_CHAMBER_NODE_UPDATE_COLUMNS: tuple[str, ...] = (
    'name',
    'base_url',
    'enabled',
    'heartbeat_ttl_seconds',
    'artifact_storage_root',
    'updated_at',
)

#: ⚠️ Columns a *re-registration* may fill but must never blank (plot-custody ②).
#:
#: ``artifact_storage_root`` is set by an OPERATOR through the web UI; the node
#: does not know where it is supposed to write and never supplies one. The node
#: self-registers on every boot (``chamber_registration_client``), so a plain
#: ``= EXCLUDED.x`` would blank the operator's setting at each restart and this
#: whole axis would have no reason to exist. ``COALESCE(EXCLUDED.x, table.x)``
#: keeps the stored value when the caller omits it, exactly like the central
#: ingestion contract's ``CONFLICT_FILL_ONLY_COLUMNS``.
#:
#: Clearing is therefore NOT expressible through registration — that is
#: deliberate. ``update_chamber`` owns explicit edit and clear.
_CHAMBER_NODE_FILL_ONLY_COLUMNS: frozenset[str] = frozenset({'artifact_storage_root'})

#: Ledger columns written on every heartbeat (docs/platform/central_db_schema.v1.json
#: chamber_heartbeat_events). reported_status is constrained to idle/in_use by the
#: schema CHECK + the domain Heartbeat validation.
HEARTBEAT_EVENT_COLUMNS: tuple[str, ...] = (
    'id',
    'chamber_id',
    'reported_status',
    'session_id',
    'occurred_at',
    'expires_at',
    'detail_json',
    # C1 heartbeat-carried progress — JSON snapshot, NULL except on in_use heartbeats.
    'progress_json',
    # M2 diagnostics — redacted {message, occurred_at} JSON, NULL unless the node
    # reported an operational error.
    'last_error_json',
    'created_at',
)


def _build_insert(table: str, columns: tuple[str, ...]) -> str:
    column_sql = ', '.join(f'"{column}"' for column in columns)
    placeholders = ', '.join(['%s'] * len(columns))
    return f'INSERT INTO "{table}" ({column_sql}) VALUES ({placeholders})'


def _build_conditional_insert(table: str, columns: tuple[str, ...], *,
                              parent_table: str, parent_key: str) -> str:
    """``INSERT ... SELECT %s, ... WHERE EXISTS (SELECT 1 FROM parent ...)``.

    A single statement that inserts the ledger row **only if** the parent registry
    row exists, so "does this chamber exist?" and "append the heartbeat" cannot
    disagree (no TOCTOU) and cost one round trip, not two. ``SELECT`` here is not
    a read of ledger data — it is the VALUES list plus the existence predicate;
    the statement remains append-only (no UPDATE/DELETE/ON CONFLICT).

    Parameter order: the ``columns`` values, then the parent key value. The ``%s``
    placeholder types are resolved by the server from the INSERT target list —
    verified on live PostgreSQL 16 by
    ``scripts/platform_keyset_cursor_live_proof.py`` (``conditional_heartbeat_insert``
    section) and on SQLite by ``tests/test_platform_chamber_api_p2.py``.
    """
    column_sql = ', '.join(f'"{column}"' for column in columns)
    placeholders = ', '.join(['%s'] * len(columns))
    return (
        f'INSERT INTO "{table}" ({column_sql}) SELECT {placeholders} '
        f'WHERE EXISTS (SELECT 1 FROM "{parent_table}" WHERE "{parent_key}" = %s)'
    )


def _build_upsert(table: str, columns: tuple[str, ...],
                  conflict_column: str, update_columns: tuple[str, ...],
                  fill_only: frozenset[str] = frozenset()) -> str:
    insert = _build_insert(table, columns)
    set_clause = ', '.join(
        (
            f'"{column}" = COALESCE(EXCLUDED."{column}", "{table}"."{column}")'
            if column in fill_only
            else f'"{column}" = EXCLUDED."{column}"'
        )
        for column in update_columns
    )
    return f'{insert} ON CONFLICT ("{conflict_column}") DO UPDATE SET {set_clause}'


#: append-only INSERT, gated on the parent registry row (no ON CONFLICT — each
#: heartbeat is a fresh-id ledger row). 0 rows written ⇒ unregistered chamber.
INSERT_HEARTBEAT_EVENT_SQL = _build_conditional_insert(
    CHAMBER_HEARTBEAT_EVENTS_TABLE, HEARTBEAT_EVENT_COLUMNS,
    parent_table=CHAMBER_NODES_TABLE, parent_key=CHAMBER_REGISTRY_NATURAL_KEY,
)
#: idempotent registry upsert on the natural key chamber_id.
#: The ``RETURNING`` projection the registry upsert and both chamber PATCHes share (= ``ChamberNodeEnvelope``).
#:
#: One declaration, because the tuple and the SQL's RETURNING list are the same
#: fact: a second copy silently mislabels every column after the point where the
#: two drift, and the symptom would be a correct-looking envelope with shifted
#: values. Derived-order parity with the SQL is asserted by the axis test.
_NODE_ENVELOPE_COLUMNS: tuple[str, ...] = (
    'chamber_id', 'name', 'base_url', 'enabled',
    'heartbeat_ttl_seconds', 'artifact_storage_root', 'accepts_web_sessions',
)


UPSERT_CHAMBER_NODE_SQL = _build_upsert(
    CHAMBER_NODES_TABLE, CHAMBER_NODE_WRITE_COLUMNS,
    CHAMBER_REGISTRY_NATURAL_KEY, _CHAMBER_NODE_UPDATE_COLUMNS,
    fill_only=_CHAMBER_NODE_FILL_ONLY_COLUMNS,
) + ' RETURNING ' + ', '.join(f'"{column}"' for column in _NODE_ENVELOPE_COLUMNS)
#: ⚠️ **``RETURNING`` 은 이 웨이브가 더한 것이고 사유가 있다.** 옛 형상은 성공 응답을
#: *입력 record 를 되울려* 만들었는데, 등록이 **쓰지 못하는** 컬럼(승인 칸)이 생긴 순간
#: 그 되울림은 저장된 값을 잃는다 — 저장소에 ``true`` 가 있어도 재등록 응답은 ``null``
#: 이 되고, 화면의 관리자 패널이 등록 재-POST 로 챔버를 편집하므로 시험원은 자기가 방금
#: 승인을 지운 것처럼 본다(codex 교차 검증 2026-08-16 P1). 이제 DB 가 답한다.

#: Operator edit of the plot storage root (plot-custody ②).
#:
#: A conditional UPDATE keyed on the natural key, ``RETURNING`` the row so zero
#: rows means "no such chamber" (404) without a preceding existence SELECT — no
#: TOCTOU window and no extra round trip. The value is written **verbatim**,
#: including ``NULL``: unlike registration this operation is the operator saying
#: what the value should be, and "no root configured" is a legitimate answer.
UPDATE_CHAMBER_STORAGE_ROOT_SQL = (
    f'UPDATE "{CHAMBER_NODES_TABLE}" SET "artifact_storage_root" = %s, '
    f'"updated_at" = %s WHERE "{CHAMBER_REGISTRY_NATURAL_KEY}" = %s '
    'RETURNING "chamber_id", "name", "base_url", "enabled", '
    '"heartbeat_ttl_seconds", "artifact_storage_root", "accepts_web_sessions"'
)



#: Operator ruling on whether this chamber may accept web sessions (챔버 모드 축).
#:
#: Same conditional-UPDATE shape as the storage-root statement above and for the
#: same reasons (zero rows ⇒ 404 without a preceding SELECT). The value is written
#: **verbatim including ``NULL``**: ``NULL`` is not "unset by accident", it is the
#: operator withdrawing a ruling back to *"nobody has decided"*, which is a
#: different state from an explicit ``false`` and must be reachable.
#:
#: ⚠️ There is deliberately **no** counterpart in ``CHAMBER_NODE_WRITE_COLUMNS`` —
#: self-registration cannot touch this column at all. A node does not know whether
#: it has been approved, and the admin screen edits chambers by re-POSTing the
#: registration, so the column's *absence* there is what protects the ruling.
UPDATE_CHAMBER_WEB_SESSION_APPROVAL_SQL = (
    f'UPDATE "{CHAMBER_NODES_TABLE}" SET "accepts_web_sessions" = %s, '
    f'"updated_at" = %s WHERE "{CHAMBER_REGISTRY_NATURAL_KEY}" = %s '
    'RETURNING "chamber_id", "name", "base_url", "enabled", '
    '"heartbeat_ttl_seconds", "artifact_storage_root", "accepts_web_sessions"'
)


#: Node-scoped settings read (plot-custody ②). The node pulls this at boot to
#: learn where it must write plots. Deliberately narrow — a chamber machine token
#: gets its own row and nothing else.
SELECT_CHAMBER_SETTINGS_SQL = (
    'SELECT "chamber_id", "artifact_storage_root", "equipment_config_json" '
    f'FROM "{CHAMBER_NODES_TABLE}" WHERE "{CHAMBER_REGISTRY_NATURAL_KEY}" = %s'
)

#: Operator-facing read of the instrument connection settings (SPLIT-6 ②).
#:
#: A separate statement from the node read above even though both project the
#: same column: they answer to different audiences and the node one must stay
#: narrow. ``updated_at`` rides along so the screen can say when the value was
#: last changed without a second query.
SELECT_CHAMBER_EQUIPMENT_CONFIG_SQL = (
    'SELECT "chamber_id", "equipment_config_json", "updated_at" '
    f'FROM "{CHAMBER_NODES_TABLE}" WHERE "{CHAMBER_REGISTRY_NATURAL_KEY}" = %s'
)

#: The read half of the per-key merge — and the ``FOR UPDATE`` is not optional.
#:
#: The patch is a read-modify-write on one JSON document. Under READ COMMITTED,
#: two concurrent PATCHes that touch DIFFERENT keys both read the pre-image and
#: the second write wins wholesale, so one operator's edit disappears with no
#: error anywhere. Row-locking the read serializes exactly the pair that would
#: have collided and costs nothing otherwise (one row, held for one statement).
#:
#: This is also why there is no CAS token on this operation: per-key merge under
#: a row lock already lets two people edit two fields at once, which is the
#: concurrency property a version check would have been bought for.
SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL = (
    'SELECT "equipment_config_json" '
    f'FROM "{CHAMBER_NODES_TABLE}" WHERE "{CHAMBER_REGISTRY_NATURAL_KEY}" = %s '
    'FOR UPDATE'
)

#: The write half. ``RETURNING`` mirrors the storage-root statement so the caller
#: never needs a follow-up read.
UPDATE_CHAMBER_EQUIPMENT_CONFIG_SQL = (
    f'UPDATE "{CHAMBER_NODES_TABLE}" SET "equipment_config_json" = %s, '
    f'"updated_at" = %s WHERE "{CHAMBER_REGISTRY_NATURAL_KEY}" = %s '
    'RETURNING "chamber_id", "equipment_config_json", "updated_at"'
)


def _decode_config(raw: object) -> dict:
    """Read the stored document back as a flat string map.

    Drivers disagree about JSONB: psycopg hands back a parsed ``dict`` while a
    plain-text column (or the SQLite shim the tests run on) hands back the JSON
    text. Accept both rather than pinning one driver's behaviour into the
    boundary. Anything unreadable degrades to "nothing configured" — this value
    decides which instrument addresses a node uses, and a half-parsed document
    is more dangerous than an absent one, because the node's fallback (its own
    workbook) is a correct answer while a truncated map is not.
    """
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        source: object = raw
    else:
        try:
            source = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(source, Mapping):
        return {}
    return {
        str(key): str(value)
        for key, value in source.items()
        if value is not None
    }


def _equipment_config_envelope(row) -> dict:
    """Project ``(chamber_id, equipment_config_json, updated_at)`` to the envelope."""
    updated_at: Optional[str] = None if row[2] is None else str(row[2])
    return {
        'chamber_id': row[0],
        'equipment_config': _decode_config(row[1]),
        'updated_at': updated_at,
    }


#: Exceptions that are already a decided boundary outcome — the transaction
#: helper rolls back and re-raises them verbatim instead of wrapping. Adding a
#: new typed failure mode to this adapter means adding it here too; forgetting
#: turns it back into an indistinguishable 503.
_PASS_THROUGH_ERRORS: tuple[type[Exception], ...] = (
    ChamberWriteError,
    ChamberNotFoundError,
)


class PostgresCentralChamberWriteAdapter:
    """``CentralChamberWritePort`` over a central PostgreSQL connection factory."""

    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        if not callable(connection_factory):
            raise ValueError('connection_factory must be callable')
        self._connection_factory = connection_factory

    def register_chamber(self, record: Mapping) -> dict:
        chamber_id = record.get('chamber_id')
        if not chamber_id:
            raise ValueError('register record requires chamber_id')
        values = tuple(record.get(column) for column in CHAMBER_NODE_WRITE_COLUMNS)

        def _txn(cursor) -> dict:
            cursor.execute(UPSERT_CHAMBER_NODE_SQL, values)
            rows = list(cursor.fetchall())
            if not rows:  # pragma: no cover — an upsert always returns its row
                return {column: record.get(column) for column in CHAMBER_NODE_WRITE_COLUMNS}
            return dict(zip(_NODE_ENVELOPE_COLUMNS, rows[0]))

        # ⚠️ 입력을 되울리지 않고 **DB 가 답한 행**을 돌려준다. 등록은 승인 칸을 쓰지
        # 못하므로(부재가 곧 보호) 입력에는 그 값이 아예 없고, 되울리면 저장된 판정이
        # 응답에서 사라진다.
        return self._in_transaction(_txn)

    def append_heartbeat(self, record: Mapping) -> None:
        """Append one heartbeat, **only if** the chamber is registered.

        Raises ``ChamberNotFoundError`` (→ 404) when ``chamber_id`` has no
        ``chamber_nodes`` row: that is a client fact ("register first"), not a
        backend outage, and reporting it as 503 sends the operator hunting the
        wrong system. Backend failures still raise ``ChamberWriteError`` (→ 503).
        """
        chamber_id = record.get('chamber_id')
        if not chamber_id:
            raise ValueError('heartbeat record requires chamber_id')
        values = tuple(record.get(column) for column in HEARTBEAT_EVENT_COLUMNS)

        def _txn(cursor) -> None:
            cursor.execute(INSERT_HEARTBEAT_EVENT_SQL, values + (chamber_id,))
            # ``rowcount`` is declared by the ``DbCursor`` port Protocol. A driver
            # that cannot report it yields a negative/None value — treated as
            # "unknown", never as "missing", so an under-reporting driver can only
            # fall back to the old behaviour (the FK is the backstop), never
            # invent a 404 for a chamber that is actually registered.
            written = getattr(cursor, 'rowcount', None)
            if written == 0:
                raise ChamberNotFoundError(
                    f'unknown chamber_id: {chamber_id!r} — register the chamber '
                    'in the central registry before pushing heartbeats'
                )

        self._in_transaction(_txn)

    def update_chamber_storage_root(
        self, chamber_id: str, *, artifact_storage_root, updated_at: str,
    ) -> dict:
        """Set (or clear) where this chamber writes plots — operator action.

        Zero rows returned ⇒ unknown chamber ⇒ ``ChamberNotFoundError`` (404).
        That is a client fact ("register the chamber first"), not a backend
        outage, and reporting it as 503 sends the operator hunting the wrong
        system — the same separation ``append_heartbeat`` makes.
        """
        if not str(chamber_id or '').strip():
            raise ValueError('update requires chamber_id')

        def _txn(cursor) -> dict:
            cursor.execute(
                UPDATE_CHAMBER_STORAGE_ROOT_SQL,
                (artifact_storage_root, updated_at, chamber_id),
            )
            rows = list(cursor.fetchall())
            if not rows:
                raise ChamberNotFoundError(
                    f'unknown chamber_id: {chamber_id!r} — register the chamber '
                    'in the central registry before configuring its storage root'
                )
            return dict(zip(_NODE_ENVELOPE_COLUMNS, rows[0]))

        return self._in_transaction(_txn)

    def update_chamber_web_session_approval(
        self, chamber_id: str, *, accepts_web_sessions, updated_at: str,
    ) -> dict:
        """운영자가 *"이 챔버는 웹 세션을 받는다/받지 않는다"* 를 선언한다 (챔버 모드 축).

        형제 ``update_chamber_storage_root`` 와 같은 조건부 UPDATE 형상이고 같은 이유다
        (0행 ⇒ 404, 선행 SELECT 없음). 값은 **``None`` 포함 verbatim** 으로 쓴다 —
        ``None`` 은 실수로 비운 것이 아니라 운영자가 판정을 *"아무도 결정하지 않음"* 으로
        되돌리는 것이고, 그 상태는 명시적 ``False`` 와 **다르며** 도달 가능해야 한다.
        """
        if not str(chamber_id or '').strip():
            raise ValueError('update requires chamber_id')

        def _txn(cursor) -> dict:
            cursor.execute(
                UPDATE_CHAMBER_WEB_SESSION_APPROVAL_SQL,
                (accepts_web_sessions, updated_at, chamber_id),
            )
            rows = list(cursor.fetchall())
            if not rows:
                raise ChamberNotFoundError(
                    f'unknown chamber_id: {chamber_id!r} — register the chamber '
                    'in the central registry before ruling on web sessions'
                )
            return dict(zip(_NODE_ENVELOPE_COLUMNS, rows[0]))

        return self._in_transaction(_txn)

    def read_chamber_settings(self, chamber_id: str) -> dict:
        """Node-scoped settings read — what this chamber must be configured with.

        Deliberately narrow: a chamber machine token gets its own row and nothing
        else. An unknown chamber is a 404 rather than an empty answer, because
        "no settings" and "no such chamber" lead the operator to different fixes.
        """
        if not str(chamber_id or '').strip():
            raise ValueError('settings read requires chamber_id')

        def _txn(cursor) -> dict:
            cursor.execute(SELECT_CHAMBER_SETTINGS_SQL, (chamber_id,))
            rows = list(cursor.fetchall())
            if not rows:
                raise ChamberNotFoundError(f'unknown chamber_id: {chamber_id!r}')
            return {
                'chamber_id': rows[0][0],
                'artifact_storage_root': rows[0][1],
                'equipment_config': _decode_config(rows[0][2]),
            }

        return self._in_transaction(_txn)

    def read_chamber_equipment_config(self, chamber_id: str) -> dict:
        """Operator-facing read of this chamber's instrument connection settings."""
        if not str(chamber_id or '').strip():
            raise ValueError('equipment-config read requires chamber_id')

        def _txn(cursor) -> dict:
            cursor.execute(SELECT_CHAMBER_EQUIPMENT_CONFIG_SQL, (chamber_id,))
            rows = list(cursor.fetchall())
            if not rows:
                raise ChamberNotFoundError(f'unknown chamber_id: {chamber_id!r}')
            return _equipment_config_envelope(rows[0])

        return self._in_transaction(_txn)

    def patch_chamber_equipment_config(
        self, chamber_id: str, *, patch: Mapping, updated_at: str,
    ) -> dict:
        """Merge ``patch`` into the stored map — read, merge and write as one act.

        The read, the merge and the write share one transaction and the read
        holds the row (``FOR UPDATE``). Splitting them would make two operators
        editing two different fields of the same chamber lose one of the two
        edits silently, which is the exact failure the per-key shape exists to
        prevent.

        Zero rows ⇒ unknown chamber ⇒ ``ChamberNotFoundError`` (404), the same
        client-fact-vs-outage separation ``update_chamber_storage_root`` makes.
        """
        if not str(chamber_id or '').strip():
            raise ValueError('equipment-config patch requires chamber_id')

        def _txn(cursor) -> dict:
            cursor.execute(
                SELECT_CHAMBER_EQUIPMENT_CONFIG_FOR_UPDATE_SQL, (chamber_id,),
            )
            rows = list(cursor.fetchall())
            if not rows:
                raise ChamberNotFoundError(
                    f'unknown chamber_id: {chamber_id!r} — register the chamber '
                    'in the central registry before configuring its instruments'
                )
            merged = apply_flat_merge_patch(_decode_config(rows[0][0]), patch)
            # An empty result is stored as NULL, not as '{}'. The two are
            # different answers downstream: NULL means nobody ever configured
            # this chamber (the node falls back to its workbook), '{}' would
            # claim someone configured it to have nothing.
            document = json.dumps(merged, sort_keys=True) if merged else None
            cursor.execute(
                UPDATE_CHAMBER_EQUIPMENT_CONFIG_SQL,
                (document, updated_at, chamber_id),
            )
            written = list(cursor.fetchall())
            if not written:
                raise ChamberNotFoundError(f'unknown chamber_id: {chamber_id!r}')
            return _equipment_config_envelope(written[0])

        return self._in_transaction(_txn)

    def _in_transaction(self, body: Callable[[object], object]):
        """Run ``body`` in one transaction and **return its value**.

        The return channel matters for the statements that answer with a row:
        a conditional ``UPDATE … RETURNING`` reports "no such chamber" by
        returning nothing, so swallowing the value would force a second
        existence query (an extra round trip and a TOCTOU window).
        """
        try:
            connection = self._connection_factory()
        except Exception as exc:  # noqa: BLE001 — wrap as loud ChamberWriteError
            raise ChamberWriteError(
                f'central chamber write connection failed: {exc}'
            ) from exc
        result = None
        try:
            cursor = connection.cursor()
            try:
                result = body(cursor)
            finally:
                cursor.close()
            connection.commit()
        except _PASS_THROUGH_ERRORS:
            # Already a typed boundary fact (503 backend failure / 404 unknown
            # chamber). Re-wrapping would collapse them back into one status —
            # exactly the failure-mode conflation this path exists to prevent.
            _safe_rollback(connection)
            raise
        except Exception as exc:  # noqa: BLE001
            _safe_rollback(connection)
            raise ChamberWriteError(f'central chamber write failed: {exc}') from exc
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        return result


def _safe_rollback(connection) -> None:
    rollback = getattr(connection, 'rollback', None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001 — never mask the original error
            pass
