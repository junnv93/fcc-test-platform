"""Platform central read service (FE-P0d, 2026-05-27).

``CentralReadService`` is the application boundary between the platform driving
adapter (``PlatformApiAdapter``) and the central read model
(``CentralReadPort``). It:

1. validates ``project_id`` is a well-formed uuid at the boundary — a malformed
   id raises ``ValueError`` (→ 400) instead of reaching PostgreSQL and surfacing
   as an opaque 5xx ``invalid input syntax for type uuid``;
2. converts raw central view rows into stable platform envelopes (the shape the
   OpenAPI contract + generated TS client expect).

``condition_hash`` is passed through verbatim from the central view — never
recomputed here (the local measurement path is the single hashing site).

dependency-free of infrastructure / FastAPI / SQL — only the domain port +
stdlib ``uuid``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

from fcc_test_platform.application.central_read_adapter import (
    ACTIVE_CLAIM_KEYSET,
    ACTIVE_CLAIM_KEYSET_DOMAINS,
    COVERAGE_KEYSET,
    COVERAGE_KEYSET_DOMAINS,
)
from fcc_test_kernel.application.central_contract.envelope_helpers import (
    int_or_zero,
    optional_int,
    optional_text,
    parse_timestamp,
    require_uuid,
    text,
)
from fcc_test_kernel.application.central_contract.pagination import (
    CursorValueDomain,
    clamp_limit,
    decode_cursor,
    encode_cursor,
)
from domain.ports.output.central_read_port import CentralReadPort
from domain.services.central_session_identity import local_session_id_from_natural_key


__all__ = ['CentralReadService', 'STALE_THRESHOLD_SECONDS']


#: FE-SYNC: a project's central coverage is flagged "stale" when the newest
#: central measurement is older than this. 1 hour mirrors the
#: coverage_by_condition_hash PT1H refresh-policy fallback (docs/platform/
#: central_db_schema.v1.json) — past that window the dashboard may not reflect a
#: just-completed measurement, so the duplicate-prevention guarantee is softened.
STALE_THRESHOLD_SECONDS = 3600


class CentralReadService:
    def __init__(
        self,
        read_port: CentralReadPort,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._read = read_port
        # Injectable for deterministic sync-status tests; defaults to wall-clock
        # UTC. server_time is the API host clock (co-located with / close to the
        # central DB) — adequate for a freshness indicator.
        self._clock = clock or _utcnow

    def project_coverage(
        self, project_id: str, *, technology: Optional[str] = None,
        limit: Optional[int] = None, cursor: Optional[str] = None,
    ) -> dict:
        """Coverage page for one central project uuid (FE-P2 source).

        Returns ``{'items': [CoverageEnvelope, ...], 'next_cursor': str | None}``.
        ``limit=None`` returns all rows (backward-compatible default — the
        already-shipped dashboard reads unbounded); an explicit ``limit`` enables
        keyset pagination resuming from ``cursor``. ``technology`` (Phase B facet)
        filters to a single technology when given (empty/None ⇒ all).
        """
        normalized = _validate_project_uuid(project_id)
        return self._read_page(
            self._read.read_project_coverage, normalized, _facet(technology),
            limit, cursor, COVERAGE_KEYSET, COVERAGE_KEYSET_DOMAINS,
            _coverage_envelope,
        )

    def project_claims(
        self, project_id: str, *, technology: Optional[str] = None,
        limit: Optional[int] = None, cursor: Optional[str] = None,
    ) -> dict:
        """Active-claim page for one central project uuid (FE-P3 source).

        Same opt-in pagination + ``technology`` facet contract as
        :meth:`project_coverage`.
        """
        normalized = _validate_project_uuid(project_id)
        return self._read_page(
            self._read.read_active_claims, normalized, _facet(technology),
            limit, cursor, ACTIVE_CLAIM_KEYSET, ACTIVE_CLAIM_KEYSET_DOMAINS,
            _claim_envelope,
        )

    def project_sync_status(self, project_id: str) -> dict:
        """Central-data freshness for one project uuid (FE-SYNC source).

        Returns ``{project_id, server_time, last_ingested_at, age_seconds,
        is_stale, stale_threshold_seconds, condition_count, active_claim_count,
        expired_open_claim_count}``.
        ``age_seconds`` is the gap between server_time and the newest central
        measurement (``None`` for an empty project or an unparseable timestamp);
        ``is_stale`` is ``age_seconds > STALE_THRESHOLD_SECONDS``. An empty
        project is NOT stale (nothing measured yet → no staleness to report).
        ``expired_open_claim_count`` flags claim rows that are still open in the
        append-only ledger (no later release/expired event) but whose
        ``expires_at`` timestamp has passed relative to ``server_time``. Those
        rows are excluded from ``active_claim_count`` so stale orphan-open claims
        cannot make the freshness surface report a live lock forever.
        """
        normalized = _validate_project_uuid(project_id)
        raw = self._read.read_sync_status(normalized)
        server_dt = self._clock()
        last_ingested = raw.get('last_ingested_at')
        parsed = parse_timestamp(last_ingested)
        age_seconds: Optional[int] = None
        if parsed is not None:
            age_seconds = max(0, int((server_dt - parsed).total_seconds()))
        active_claim_count, expired_open_claim_count = _claim_counts_by_expiry(
            self._read.read_active_claims(normalized, limit=None),
            server_dt,
        )
        return {
            'project_id': normalized,
            'server_time': server_dt.isoformat(),
            'last_ingested_at': optional_text(last_ingested),
            'age_seconds': age_seconds,
            'is_stale': age_seconds is not None and age_seconds > STALE_THRESHOLD_SECONDS,
            'stale_threshold_seconds': STALE_THRESHOLD_SECONDS,
            'condition_count': int_or_zero(raw.get('condition_count')),
            'active_claim_count': active_claim_count,
            'expired_open_claim_count': expired_open_claim_count,
        }

    def project_report_sessions(self, project_id: str) -> list[dict]:
        """Report-generation session choices for a project.

        The central session UUID stays server-side. The public envelope carries
        the node-local ``provider_session_id`` as ``submit_session_id`` because
        the node Headless report API is local-SQLite scoped and accepts its
        integer session id. The natural key now also carries the measurement
        target, so the local id is recovered through the domain SSOT that owns
        that grammar rather than by parsing the whole key as an integer — the
        latter silently emptied this list for every identified session.
        Keys with no recoverable local id are excluded.
        """
        normalized = _validate_project_uuid(project_id)
        grouped: dict[tuple[str, str], dict] = {}
        for row in self._read.read_project_report_sessions(normalized):
            submit_session_id = local_session_id_from_natural_key(
                row.get('provider_session_id')
            )
            node_base_url = text(row.get('node_base_url')).strip()
            if submit_session_id is None or not node_base_url:
                continue
            key = (node_base_url, str(submit_session_id))
            item = grouped.get(key)
            if item is None:
                item = {
                    'project_id': normalized,
                    'submit_session_id': submit_session_id,
                    'node_id': text(row.get('node_id')),
                    'node_name': text(row.get('node_name')),
                    'node_base_url': node_base_url,
                    'latest_measured_at': optional_text(row.get('latest_measured_at')),
                    'latest_verdict': optional_text(row.get('latest_verdict')),
                    'completed_conditions': 0,
                    '_technologies': set(),
                }
                grouped[key] = item
            item['completed_conditions'] += 1
            tech = text(row.get('technology')).strip()
            if tech:
                item['_technologies'].add(tech)
            latest = optional_text(row.get('latest_measured_at'))
            if latest and (
                item['latest_measured_at'] is None or latest > item['latest_measured_at']
            ):
                item['latest_measured_at'] = latest
                item['latest_verdict'] = optional_text(row.get('latest_verdict'))
        sessions = []
        for item in grouped.values():
            techs = sorted(item.pop('_technologies'))
            item['technologies'] = techs
            sessions.append(item)
        return sorted(
            sessions,
            key=lambda item: (
                item.get('latest_measured_at') or '',
                str(item.get('node_id') or ''),
                int(item.get('submit_session_id') or 0),
            ),
            reverse=True,
        )

    def _read_page(
        self,
        read_fn: Callable[..., list[dict]],
        project_id: str,
        technology: Optional[str],
        limit: Optional[int],
        cursor: Optional[str],
        keyset: Sequence[str],
        keyset_domains: Sequence[CursorValueDomain],
        envelope_fn: Callable[[dict], dict],
    ) -> dict:
        if limit is None and not cursor:
            # Fully unbounded read — all rows, no continuation (backward
            # compatible: the shipped dashboard passes neither limit nor cursor).
            rows = read_fn(project_id, technology=technology, limit=None)
            return {'items': [envelope_fn(row) for row in rows], 'next_cursor': None}
        # A cursor (with or without an explicit limit) means the client is
        # paginating — decode/validate it (malformed → loud) and apply the
        # default page size when no explicit limit was given.
        size = clamp_limit(limit)
        after = (
            decode_cursor(cursor, arity=len(keyset), domains=keyset_domains)
            if cursor else None
        )
        # Fetch one extra row to detect a further page without a second query.
        rows = read_fn(project_id, technology=technology, limit=size + 1, after=after)
        has_more = len(rows) > size
        items = [envelope_fn(row) for row in rows[:size]]
        next_cursor = None
        if has_more and items:
            next_cursor = encode_cursor([items[-1][column] for column in keyset])
        return {'items': items, 'next_cursor': next_cursor}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _claim_counts_by_expiry(rows: list[dict], server_dt: datetime) -> tuple[int, int]:
    active = 0
    expired_open = 0
    for row in rows:
        expires_at = parse_timestamp(row.get('expires_at'))
        if expires_at is not None and expires_at <= server_dt:
            expired_open += 1
        else:
            active += 1
    return active, expired_open


def _facet(value: Optional[str]) -> Optional[str]:
    """Normalize an optional facet filter — trim whitespace; empty ⇒ ``None`` (no
    filter). Keeps "" and "   " equivalent to an omitted filter so the route's
    default empty-string query param means "all technologies"."""
    if value is None:
        return None
    text = value.strip()
    return text or None


def _positive_int(value: object) -> Optional[int]:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _validate_project_uuid(project_id: str) -> str:
    # Thin delegate to the shared boundary validator (envelope_helpers SSOT).
    return require_uuid(project_id, 'project_id')


def _coverage_envelope(row: dict) -> dict:
    return {
        'project_id': text(row.get('project_id')),
        'technology': text(row.get('technology')),
        'condition_hash': text(row.get('condition_hash')),
        'latest_session_id': text(row.get('latest_session_id')),
        'latest_operator': text(row.get('latest_operator')),
        'latest_measured_at': text(row.get('latest_measured_at')),
        'latest_verdict': text(row.get('latest_verdict')),
        'latest_attempt_number': optional_int(row.get('latest_attempt_number')),
        'attempt_count': int_or_zero(row.get('attempt_count')),
        'distinct_session_count': int_or_zero(row.get('distinct_session_count')),
        'distinct_operator_count': int_or_zero(row.get('distinct_operator_count')),
    }


def _claim_envelope(row: dict) -> dict:
    return {
        'project_id': text(row.get('project_id')),
        'claim_id': text(row.get('claim_id')),
        'technology': text(row.get('technology')),
        'condition_hash': text(row.get('condition_hash')),
        'operator': text(row.get('operator')),
        'occurred_at': text(row.get('occurred_at')),
        'expires_at': optional_text(row.get('expires_at')),
        'session_id': text(row.get('session_id')),
    }
