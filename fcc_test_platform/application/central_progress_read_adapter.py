"""Central PostgreSQL Phase 6 progress read adapter (P6-C).

``PostgresCentralProgressReadAdapter`` computes the time-weighted progress rollup
with a single grouped query: ``published_plan_expectation`` (the snapshot-frozen
denominator) LEFT JOINed to the DISTINCT latest measurement coverage on the F1
provider-scoped key ``(project_id, provider_id, condition_hash)``, grouped by
``(progress_area, progress_bucket_id)``.

Why this read model and NOT ``coverage_by_condition_hash``: that materialized view
keys on ``(project_id, technology, condition_hash)`` — no provider_id — so it
cannot distinguish a conducted headless's coverage from a radiated one's (F1).
Joining ``measurement_attempts`` directly with ``is_latest`` keeps the existing
coverage view (and its FE-P0a byte-identical seals) untouched while scoping the
progress join by provider.

The platform performs NO taxonomy derivation here — bucket/area are read straight
from the stored columns (ADR-0010). Percent / honest-gap logic lives in the
``ProgressBucketRollup`` domain DTO. Injected ``connection_factory`` + ``%s``
paramstyle (psycopg / QmarkConnection shim), loud-fail via
``CentralProgressReadError``.
"""
from __future__ import annotations

from typing import Callable, Sequence

from domain.models.progress_rollup import ProgressBucketRollup
from domain.ports.output.central_progress_read_port import (
    CentralProgressReadError,
    CentralProgressReadPort,
)
from domain.ports.output.platform_database_port import DbConnection


__all__ = ['PROGRESS_ROLLUP_SQL', 'PostgresCentralProgressReadAdapter']


# Grouped rollup. Two derived tables guard correctness:
#   * ``lp`` (latest plan) keeps, per (project_id, provider_id), ONLY the plan with
#     the newest plan_published_at (ROW_NUMBER window; NULL publish-time sorts
#     oldest, plan_id DESC tiebreak). This is the read-side latest-wins (P6L-3):
#     the denominator counts the current plan once, never double-counting an older
#     superseded plan, and an old in-flight expectation sync committing late can
#     never regress the total (its older published_at simply loses the window).
#     With a single plan per (project, provider) — the operational norm — lp keeps
#     every row, so the rollup is byte-identical to the pre-P6L-3 form.
#   * ``m`` is DISTINCT latest attempts per (project, provider, condition) so the
#     LEFT JOIN stays 1:1 with the denominator (a covered condition contributes its
#     snapshot once, not once per attempt).
PROGRESS_ROLLUP_SQL = (
    'SELECT '
    '  e."progress_area", '
    '  e."progress_bucket_id", '
    '  COALESCE(SUM(CASE WHEN e."pricing_status" = \'priced\' '
    '    THEN e."planned_minutes_snapshot" ELSE 0 END), 0) AS planned_minutes, '
    '  COALESCE(SUM(CASE WHEN e."pricing_status" = \'priced\' '
    '    AND m."condition_hash" IS NOT NULL '
    '    THEN e."planned_minutes_snapshot" ELSE 0 END), 0) AS completed_minutes, '
    '  COUNT(*) AS total_conditions, '
    '  SUM(CASE WHEN e."pricing_status" = \'priced\' THEN 1 ELSE 0 END) AS priced_conditions, '
    '  SUM(CASE WHEN e."pricing_status" = \'unpriced\' THEN 1 ELSE 0 END) AS unpriced_conditions, '
    '  SUM(CASE WHEN e."progress_bucket_id" IS NULL THEN 1 ELSE 0 END) AS unbucketable_conditions '
    'FROM "published_plan_expectation" e '
    'JOIN ( '
    '  SELECT "project_id", "provider_id", "plan_id" FROM ( '
    '    SELECT "project_id", "provider_id", "plan_id", '
    '      ROW_NUMBER() OVER ( '
    '        PARTITION BY "project_id", "provider_id" '
    '        ORDER BY ("plan_published_at" IS NULL) ASC, '
    '          "plan_published_at" DESC, "plan_id" DESC '
    '      ) AS rn '
    '    FROM ( '
    '      SELECT DISTINCT "project_id", "provider_id", "plan_id", "plan_published_at" '
    '      FROM "published_plan_expectation" WHERE "project_id" = %s '
    '    ) plans '
    '  ) ranked WHERE rn = 1 '
    ') lp ON e."project_id" = lp."project_id" '
    '  AND e."provider_id" = lp."provider_id" '
    '  AND e."plan_id" = lp."plan_id" '
    'LEFT JOIN ( '
    '  SELECT DISTINCT "project_id", "provider_id", "condition_hash" '
    '  FROM "measurement_attempts" '
    '  WHERE "is_latest" = %s AND "project_id" = %s '
    ') m ON e."project_id" = m."project_id" '
    '  AND e."provider_id" = m."provider_id" '
    '  AND e."condition_hash" = m."condition_hash" '
    'WHERE e."project_id" = %s '
    'GROUP BY e."progress_area", e."progress_bucket_id" '
    'ORDER BY e."progress_area", e."progress_bucket_id"'
)


class PostgresCentralProgressReadAdapter(CentralProgressReadPort):
    def __init__(self, connection_factory: Callable[[], DbConnection]) -> None:
        self._connect = connection_factory

    def get_project_progress(
        self, project_id: str
    ) -> Sequence[ProgressBucketRollup]:
        try:
            conn = self._connect()
        except Exception as exc:  # noqa: BLE001 — loud-fail boundary
            raise CentralProgressReadError(str(exc)) from exc
        try:
            cur = conn.cursor()
            # %s order: latest-plan window (project_id), coverage subquery
            # (is_latest, project_id), outer filter (project_id).
            cur.execute(
                PROGRESS_ROLLUP_SQL, (project_id, True, project_id, project_id)
            )
            rows = cur.fetchall()
            cur.close()
        except Exception as exc:  # noqa: BLE001
            raise CentralProgressReadError(str(exc)) from exc
        finally:
            close = getattr(conn, 'close', None)
            if callable(close):
                close()
        return [
            ProgressBucketRollup(
                progress_area=row[0],
                progress_bucket_id=row[1],
                planned_minutes=float(row[2]),
                completed_minutes=float(row[3]),
                total_conditions=int(row[4]),
                priced_conditions=int(row[5]),
                unpriced_conditions=int(row[6]),
                unbucketable_conditions=int(row[7]),
            )
            for row in rows
        ]
