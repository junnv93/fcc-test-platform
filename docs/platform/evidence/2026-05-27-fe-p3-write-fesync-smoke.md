# FE-P3-write / FE-SYNC / duplicate-quality — Evidence (2026-05-27)

Separate deliverable for the goal's verification item:
*"live PostgreSQL smoke/perf evidence는 별도 산출물로 남긴다."*

## Scope

Three slices of the cross-engineer duplicate-prevention platform surface:

| Slice | What it adds |
|-------|--------------|
| **FE-P3-write** | `POST /platform/projects/{id}/claims` (acquire) + `.../claims/{claim_id}/release` — append-only `claim_events` ledger writes; acquire rejects a contended condition (409). |
| **FE-SYNC-status** | `GET /platform/projects/{id}/sync-status` — central-data freshness (last ingest + age + `is_stale`). |
| **duplicate-quality** | `coverage_by_condition_hash` + `distinct_session_count` / `distinct_operator_count` so a single engineer's re-measure is no longer mislabeled as a cross-engineer duplicate. |

## Available evidence (CI-runnable, no live DB)

| Check | Command | Result |
|-------|---------|--------|
| Platform contract / OpenAPI drift | `python scripts/export_session_api_schemas.py --verify` | exit 0 (no drift) |
| Central DDL drift | `python scripts/export_platform_central_db_ddl.py --write` (idempotent) | regenerated from JSON SSOT |
| Codegen drift | `cd apps/web && npm run codegen:check` | session/headless/platform up to date |
| Backend — platform read+write | `pytest tests/test_platform_read_api_fe_p0d.py tests/test_platform_claim_write_fe_p3.py -q` | 133 passed |
| Backend — central/ddl/migration/ingestion sweep | `pytest -k "platform or central_db or ddl or migration or ingestion"` | 710 passed |
| Frontend typecheck + lint + vitest | `npm run typecheck && npm run lint && npm test` | tsc/eslint clean, 199 vitest |
| Architecture purity / Protocol placement / drift gate | `pytest -k "Purity or Layer or Protocol or Drift"` | passed |

**Claim write race**: the atomic check-and-append is exercised against a real
SQLite fixture running the verbatim `active_claims` view SELECT
(`TestClaimWriteEndToEnd::test_acquire_then_conflicting_acquire_by_other_operator`,
`test_double_release_is_pairing_error`) plus a fake-port unit
(`TestClaimWriteServiceLogic`) so the conflict/pairing decision is covered without
a live DB. On PostgreSQL the adapter additionally sets `SET TRANSACTION ISOLATION
LEVEL SERIALIZABLE` (best-effort; the SQLite test backend serializes the single
connection), so two concurrent acquires of a free condition cannot both commit.

## Deferred-to-deployed (live PostgreSQL)

Consistent with the `coverage_by_condition_hash.performance_budget.runtime_benchmark`
= `deferred-to-deployed` convention (CI does not provision PostgreSQL;
`scripts/platform_performance_smoke.py` benchmarks HTTP routes, not the read
model), the following require the deployed central PostgreSQL and are owned by
the deployment environment:

1. **SERIALIZABLE acquire-race smoke** — two concurrent `POST .../claims` for the
   same free `(project, condition_hash)`: exactly one 200 + one 409 (the loser's
   commit fails with `serialization_failure` and retries into a conflict). The
   SQLite fixture proves the *logic*; only a live PG proves the *isolation*.
2. **distinct-count subquery cost** — `distinct_session_count` /
   `distinct_operator_count` are correlated `COUNT(DISTINCT ...)` subqueries on the
   materialized view; their refresh cost is amortized at ingestion time (the view
   is materialized), but the refresh-latency delta is a deployed measurement.
3. **sync-status aggregate latency** — `MAX(latest_measured_at)` + two `COUNT(*)`
   over the views per project.
4. **`REFRESH MATERIALIZED VIEW CONCURRENTLY`** with the added columns (ingestion
   writer post-commit hook) — unchanged DDL path, re-confirm on deploy.

`scripts/platform_db_migration_runner.py` applies the regenerated
`docs/platform/migrations/001_initial_central_db.sql` (with the two new coverage
columns) to the deployed PostgreSQL; the deployed environment owns capturing the
above into its smoke/perf evidence bundle.

## Known non-goal / limitation

- **pending outbox count is NOT on this surface.** The central platform API reads
  only the central database, so a remote station's local outbox (pending-to-sync
  count) is structurally unobservable here. FE-SYNC reports central freshness
  only (last ingest + age); the local sync backlog is a local-surface concern.
  (User-confirmed design decision, 2026-05-27.)
