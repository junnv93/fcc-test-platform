# ADR-0005: Central DB read model

**Status**: Accepted (FE-P0a, 2026-05-25 — extends the original Proposed shape)
**Date**: 2026-05-23 (proposed); 2026-05-25 (FE-P0a alignment — measurement_attempts/claim_events/coverage view/refresh policy/project_membership stub)
**Deciders**: shared web/platform maintainers + user

## Context

웹 frontend 의 핵심 목표 (Sprint S6 Session Result Browser) 는 **여러 PC 의 측정 결과를 cross-PC aggregate 로 조회** 하는 것이다. 현재 backend 는:

- **per-PC SQLite DB** (`{excel_stem}.fcc.db`) — 각 측정 station 의 local result
- **SQLAlchemy + WAL mode** + `SqliteConnectionFactory` SSOT (2026-05-23)
- 5-repo split ADR (`docs/architecture/repository_split_adr.md`) 에서 `fcc-test-platform` 의 책임으로 "PostgreSQL schema" 명시

frontend 가 cross-PC result 를 query 하려면 central DB read model 필요. 본 ADR 이 그 schema 도입을 결정한다.

### Constraints

- 본 모노리포의 SQLite write path 무손상 (PERF-2 phase 4 + SHOULD S-1 SSOT 보존)
- per-PC SQLite → central DB sync 가 strangler fig pattern (incremental, 무중단)
- 측정 station 이 network outage 시 local SQLite 만으로 계속 동작 (local-first)
- read model 은 query optimization 위주 (write-optimized 가 아닌 read-optimized index)
- backend Headless API `GET /headless/sessions` 등 cross-PC route 가 central DB read model 위에 구축

## Decision

**PostgreSQL 15+ 채택 + per-PC SQLite → central PostgreSQL strangler fig sync (Sprint S3)** + **read model 4 table + 2 materialized view**

### Schema (Sprint S3 에서 SQL migration 으로 실행)

```sql
-- sessions table
CREATE TABLE sessions (
  session_id UUID PRIMARY KEY,
  station_id TEXT NOT NULL,            -- per-PC identifier
  model_number TEXT NOT NULL,
  sample_id TEXT,
  project TEXT,
  technology TEXT NOT NULL,            -- BT / BLE / WLAN / DTS
  started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  total_tests INTEGER NOT NULL,
  completed_tests INTEGER NOT NULL,
  status TEXT NOT NULL,                -- running / completed / failed / cancelled
  source_db_path TEXT,                 -- per-PC SQLite source (audit)
  sync_lsn BIGINT,                     -- WAL log sequence number for replay
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_sessions_model_started ON sessions(model_number, started_at DESC);
CREATE INDEX idx_sessions_station_started ON sessions(station_id, started_at DESC);
CREATE INDEX idx_sessions_status_started ON sessions(status, started_at DESC);

-- jobs table (headless job 등록부)
CREATE TABLE jobs (
  job_id UUID PRIMARY KEY,
  session_id UUID REFERENCES sessions(session_id) ON DELETE CASCADE,
  provider_id TEXT NOT NULL,
  technology TEXT NOT NULL,
  test_plan_artifact_id TEXT,
  model_number TEXT NOT NULL,
  state TEXT NOT NULL,                 -- queued / claimed / running / completed / failed / cancelled
  progress REAL,
  submitted_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_jobs_state_submitted ON jobs(state, submitted_at DESC);
CREATE INDEX idx_jobs_provider_state ON jobs(provider_id, state);

-- results table (per-row measurement)
CREATE TABLE results (
  result_id BIGSERIAL PRIMARY KEY,
  session_id UUID REFERENCES sessions(session_id) ON DELETE CASCADE,
  row_order INTEGER NOT NULL,
  technology TEXT NOT NULL,
  test TEXT NOT NULL,
  condition JSONB NOT NULL,
  result_value_1 NUMERIC,
  result_value_2 NUMERIC,
  result_value_3 NUMERIC,
  result_value_1_unit TEXT,
  result_value_2_unit TEXT,
  result_value_3_unit TEXT,
  result_sum NUMERIC,
  result_sum_unit TEXT,
  verdict TEXT,                        -- PASS / FAIL / NA
  margin NUMERIC,
  measured_at TIMESTAMPTZ NOT NULL,
  UNIQUE (session_id, row_order)
);

CREATE INDEX idx_results_session_row ON results(session_id, row_order);
CREATE INDEX idx_results_verdict_margin ON results(verdict, margin) WHERE verdict = 'FAIL';

-- attempts table (history per result)
CREATE TABLE attempts (
  attempt_id BIGSERIAL PRIMARY KEY,
  session_id UUID REFERENCES sessions(session_id) ON DELETE CASCADE,
  row_order INTEGER NOT NULL,
  measured_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL,
  verdict TEXT,
  result_value_1 NUMERIC,
  result_value_2 NUMERIC,
  result_value_3 NUMERIC,
  margin NUMERIC,
  is_latest BOOLEAN NOT NULL
);

CREATE INDEX idx_attempts_session_row_measured ON attempts(session_id, row_order, measured_at DESC);
CREATE INDEX idx_attempts_latest ON attempts(session_id, row_order) WHERE is_latest = true;

-- materialized view: session summary (cross-PC dashboard)
CREATE MATERIALIZED VIEW session_summary AS
SELECT
  s.station_id,
  s.model_number,
  s.technology,
  COUNT(*) AS total_sessions,
  SUM(s.total_tests) AS total_tests_aggregate,
  SUM(s.completed_tests) AS completed_tests_aggregate,
  MAX(s.started_at) AS last_session_at
FROM sessions s
GROUP BY s.station_id, s.model_number, s.technology;

CREATE UNIQUE INDEX idx_session_summary ON session_summary(station_id, model_number, technology);

-- materialized view: verdict aggregate
CREATE MATERIALIZED VIEW verdict_summary AS
SELECT
  s.model_number,
  s.technology,
  r.verdict,
  COUNT(*) AS count
FROM results r
JOIN sessions s ON r.session_id = s.session_id
GROUP BY s.model_number, s.technology, r.verdict;
```

### Sync 전략 (Sprint S3 에서 구현)

- **Phase 1**: per-PC SQLite → central PostgreSQL pull-based replication (cron / on-session-close)
- **Phase 2**: WAL-based logical replication 검토 (SQLite WAL → Postgres pub/sub adapter)
- **Phase 3**: PostgreSQL 가 single write target + SQLite 는 cache (full strangler)

본 ADR 은 Phase 1 만 결정. Phase 2/3 은 별도 ADR.

### FE-P0a 정합 (2026-05-25) — `docs/platform/central_db_schema.v1.json` 이 SSOT

FE-P0a sprint 는 본 ADR 의 원안에 다음을 보강하여 schema contract (`docs/platform/central_db_schema.v1.json`) 와의 drift 를 0 으로 봉인한다:

- **`measurement_attempts`** (append-only) — `is_latest` / `attempt_number` / `condition_hash` / `operator` / `project_id` direct FK / `idempotency_key` / `recorded_by` / `provenance_json` 포함. condition_hash 는 `src/domain/services/measurement_condition_hash::compute_condition_hash` 가 산출한 값을 전파만 — central 재계산 금지. 위 SQL 코드블록의 `attempts` 가 본 테이블의 시작점이며 schema contract 가 최종 형상의 SSOT.
- **`measurement_results.project_id` 직접 FK + `condition_hash` + `operator`** — coverage 쿼리가 `test_sessions` 를 거치는 join 없이 `(project_id, condition_hash)` 단일 인덱스로 중복 판정 가능.
- **`claim_events`** (append-only) + **`active_claims` 일반 view** — FE-P3 가 (project_id, technology, condition_hash, operator, action) 이벤트로 점유 가시화. throughput 이 측정 대비 매우 낮아 materialized 가 아닌 plain view 로 충분.
- **`project_membership` 컬럼 stub** — FE-P8 RBAC 전제. 본 sprint 에서는 schema 자리 예약만(reserved for FE-P8) — FE-P0a~P5 는 본 테이블을 소비하지 않는다.

### Materialized view refresh 정책 (ADR-0005:158-162 미결 해소)

본 ADR Negative 절에 `materialized view refresh 정책 결정 필요 (실시간 vs 5분 vs 1시간)` 으로 남아 있던 미결 항목을 FE-P0a 가 다음과 같이 결정한다.

- **대상 view**: `coverage_by_condition_hash` materialized view (technology × condition_hash → 상태/operator/측정시각/세션).
- **Trigger**: `on_ingest` — FE-P0c ingestion writer 가 attempts 적재 직후 `REFRESH MATERIALIZED VIEW CONCURRENTLY coverage_by_condition_hash` 를 호출. 사용자(FE-P2 coverage 화면)는 항상 최신 attempt 기준 view 를 본다.
- **Fallback interval**: `PT1H` — on_ingest 시그널이 누락된 경우의 안전망(cron). 6 시간 stale 가능성 차단.
- **Concurrent refresh**: `true` — 16k+ test items × 다중 세션 환경에서 read 트래픽 블로킹 회피(`CONCURRENTLY` 는 unique index 가 view 에 존재해야 동작 — schema 의 `ux_coverage_by_condition_hash` 가 그 조건 충족).
- **근거**: 일반 VIEW 는 read 마다 join 재계산. 16k 행 × 다중 세션 환경에서 background budget(3-5×) 초과. 5분/1시간 fixed interval 은 dedup UX latency 와 cron jitter 모두 악화. `on_ingest + PT1H fallback` 이 freshness 와 비용을 동시에 만족.

세부 SSOT 는 `docs/platform/central_db_schema.v1.json::refresh_policies.coverage_by_condition_hash` 에 위치하며, drift 는 `tests/test_platform_central_db_schema_contract.py::TestFeP0aCoverageMaterializedView` 가 봉인한다.

### Performance budget (acceptance 로 고정)

coverage 집계의 성능 budget 은 산문이 아니라 schema contract 의 **구조적 acceptance 필드**로 고정한다 — `coverage_by_condition_hash.performance_budget` (`class=background`, `ratio_multiplier_range_x=[3,5]`, basis=`16k+ test items × N concurrent sessions`).

- **budget class = background (3-5×)** — `/verify-core-column-projection` 와 동일 컨벤션. coverage 는 측정 동기 흐름이 아닌 read model 이므로 critical(5-10×) 이 아닌 background budget.
- **materialization 이 budget 강제 메커니즘** — 일반 VIEW 는 read 마다 `measurement_attempts` is_latest 스캔 + `attempt_count` 상관 서브쿼리를 재계산 → 16k+ 행 × 다중 세션에서 3-5× 초과. `on_ingest REFRESH ... CONCURRENTLY` 가 비용을 ingestion 시점으로 amortize → coverage read(FE-P2)는 단일 indexed materialized-view 스캔. **plain VIEW 로 회귀하면 "is materialized" 테스트는 통과해도 budget 이 깨지므로, budget 필드가 그 회귀를 별도로 차단한다.**
- **runtime benchmark = deferred-to-deployed** — machine-independent ratio 벤치마크는 live PostgreSQL 이 필요한데 CI 는 이를 provision 하지 않는다(`scripts/platform_performance_smoke.py` 는 HTTP 라우트만 측정, 본 read model 아님). schema contract 가 SSOT, `scripts/platform_db_migration_runner.py` 가 deployed PostgreSQL 에 apply, deployed 환경이 live coverage-read 를 측정. 본 contract 는 budget 을 acceptance 로 박아 materialization/index 전략의 silent regression 을 차단한다.

drift 는 `tests/test_platform_central_db_schema_contract.py::TestFeP0aCoverageMaterializedView::test_coverage_view_declares_performance_budget` 가 봉인한다.

### Local SQLite ↔ central PostgreSQL field mapping

FE-P0a 는 `docs/platform/central_db_schema.v1.json::local_central_field_mappings` 에 `src/infrastructure/database/models.py:410-470` 의 로컬 `MeasurementAttempt` ↔ central `measurement_attempts` 간 field 단위 매핑 표를 동봉한다(Codex 2차 drift 위험 해소: integer id ↔ uuid, condition_id ↔ condition_hash, project_id/operator/run_id/idempotency_key 대응). 매핑 무결성은 `tests/test_platform_central_db_schema_contract.py::TestFeP0aLocalCentralFieldMapping` 이 봉인한다. 본 ADR 은 매핑 결정의 동기와 정합만 진술하고 매핑 형상의 SSOT 는 schema 파일이다.

### FE-P0a Commit 3 후속 정공 (2026-05-25)

Commit 1·2 직후 시니어 자평에서 발견한 6 결함을 schema/SQL 정공으로 일괄 해소:

- **active_claims view SQL**: outer alias `claim_events acquired` + `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY occurred_at DESC) WHERE rn = 1` 로 재작성. correlated subquery 의 outer/inner self-reference 모호성 제거 + PG `DISTINCT ON` 의존 제거(PostgreSQL/SQLite 양쪽 호환). 회귀 가드: `TestFeP0aViewSqlSemantics` (SQLite live execution — acquire-only/acquire+release/acquire+expire/two-acquire-same-partition 4 시나리오).
- **coverage_by_condition_hash view SQL**: `attempt_count` 를 `COUNT(*) OVER (PARTITION BY ...)` 대신 `(SELECT COUNT(*) FROM measurement_attempts b WHERE b.project_id IS NOT DISTINCT FROM a.project_id AND b.technology = a.technology AND b.condition_hash = a.condition_hash)` correlated subquery 로 재작성. `is_latest=true` 필터로 partition 당 1 행이라 window count 가 항상 1 이던 논리 결함 해소. NULL-safe 비교는 `IS NOT DISTINCT FROM` 표준 SQL 사용. 회귀 가드: `TestFeP0aViewSqlSemantics::test_coverage_view_attempt_count_reflects_all_attempts`.
- **claim_events.action allowed_values SSOT**: `["acquired", "released", "expired"]` 를 schema 컬럼 메타데이터로 명시 + DDL exporter 가 `CONSTRAINT ck_claim_events_action CHECK (action IN ('acquired','released','expired'))` 렌더 + `TestFeP0aActionEnumSsot` 가 schema 어휘 ↔ view SQL 리터럴 ↔ DDL CHECK constraint ↔ SQLite live rejection 4-way 정합 봉인. 어휘를 SQL view 본문에 묻어두는 magic literal 안티패턴 해소.
- **idempotency_key partial UNIQUE**: `ux_measurement_attempts_idempotency_key` 에 `where: "idempotency_key IS NOT NULL"` 명시 + DDL exporter 가 `CREATE UNIQUE INDEX ... WHERE idempotency_key IS NOT NULL` 렌더. PostgreSQL/SQLite 모두 표준 partial unique. NULL 행은 다중 허용(local Attempt 가 idempotency_key 없이 적재 가능), non-null 행은 dedup 강제. 회귀 가드: `TestFeP0aIdempotencyPartialIndex` (SQLite live — NULL 다중 허용 + duplicate non-null 거부 검증).
- **ingestion_contract 절 신설**: schema 에 5 rule (atomic toggle / projection consistency / append-only pairing / coverage refresh post-commit / condition_hash propagation) 의 atomicity·ordering·조건 명시. FE-P0c writer 가 따라야 할 transaction shape (SERIALIZABLE / SELECT FOR UPDATE / 단일 commit / post-commit refresh) 와 그 rule 을 enforce 하는 schema element (unique index / allowed_values / refresh policy) 가 cross-reference 됨. 회귀 가드: `TestFeP0aIngestionContract` (5 rule 존재 + atomic toggle 이 SERIALIZABLE/FOR UPDATE 언급 + condition_hash recompute 금지 명시).
- **SQL semantic invariant SSOT**: `TestFeP0aViewSqlSemantics` 가 schema 의 view SELECT 본문을 그대로 받아 SQLite (PostgreSQL 호환 ROW_NUMBER + IS NOT DISTINCT FROM + correlated EXISTS dialect) 에서 실측. SQL string 의 substring match 만으로는 잡지 못하는 시멘틱 결함을 실제 데이터 흐름으로 검출. AST 가드 (`test_active_claims_select_qualifies_self_reference_with_outer_alias` / `test_coverage_view_select_uses_subquery_for_attempt_count`) 가 시멘틱 회귀 차단의 ratchet 역할.

## Consequences

### Positive
- cross-PC result aggregation 가능 (frontend Session Result Browser 동작 가능)
- PostgreSQL 표준 SQL + JSONB + materialized view + advanced index
- backend Headless API 가 single central DB 호출 — provider proxy layer 단순
- Sprint F-2 strangler fig pattern (SQLite → PostgreSQL) 와 정합

### Negative
- 인프라 추가: PostgreSQL 15+ deployment + backup + monitoring + Connection pool (PgBouncer)
- per-PC SQLite → central sync mechanism 추가 작업 (Sprint S3)
- network outage 시 frontend cross-PC view 가 stale — `stale_provider_sync` empty state (이전 sprint 가 명시한 4 empty state 중 1) 가 실제 의미 가짐
- ~~materialized view refresh 정책 결정 필요 (실시간 vs 5분 vs 1시간)~~ ✅ FE-P0a (2026-05-25) — `coverage_by_condition_hash` 는 `on_ingest` trigger + `PT1H` fallback + `CONCURRENTLY` refresh 로 결정 (위 “Materialized view refresh 정책” 절 참조).

## Alternatives Considered

### SQLite-extended (per-PC SQLite + central SQLite aggregation)
- **rejected because**: SQLite 가 cross-PC sync 표준 없음 + concurrent write 한계 + materialized view 미지원

### MySQL / MariaDB
- **rejected because**: JSONB 지원 약함 (PostgreSQL JSONB indexing 강력)

### MongoDB
- **rejected because**: relational schema (sessions ↔ results ↔ attempts FK chain) 가 강해서 SQL 우위. 또한 backend Python sqlalchemy stack 과 호환성.

### Snowflake / BigQuery (warehouse)
- **rejected because**: realtime query latency (< 1s) 요구 + operational DB 가 아닌 analytical store — overkill.

### TimescaleDB (Postgres extension)
- **considered**: time-series measurement data 에 적합. Sprint S3 Phase 2 에서 measurement event 가 high-volume 시 검토.

## Offline / Local-First Duplicate-Prevention Boundary (FE-SYNC, 2026-05-26)

측정 station 은 network outage 시 local SQLite 만으로 계속 동작한다(local-first 제약 — 측정은 절대 멈추지 않는다). 이로 인해 중복 방지(cross-engineer)가 central read model 에 의존할 때, **online/offline 보장 수준이 다르다**. 본 절은 그 경계를 명문화한다(Codex 2차 Top#3 — 정직한 한계 명시).

### 보장 경계

- **online 에서만 중복 방지 보장**: central read model(`coverage_by_condition_hash` + `active_claims`)이 최신일 때만, operator 는 측정 착수 전 "이미 측정됨/진행중"을 신뢰성 있게 확인할 수 있다.
- **offline 중복 방지 미보장**: network 단절 중에는 두 station 이 같은 `(project_id, condition_hash)` 를 서로 모른 채 각각 측정할 수 있다. 이를 막을 수 없다(local-first 의 불가피한 trade-off). 프론트는 offline/stale 동안 **'중복 방지 미보장(stale)' 배지**를 명시 표시해야 한다(`stale_provider_sync` empty state 의 실질 의미).
- **재동기화는 무손실 idempotent**: append-only fact + `idempotency_key` partial UNIQUE(`ux_measurement_attempts_idempotency_key WHERE idempotency_key IS NOT NULL`, `TestFeP0aIdempotencyPartialIndex`) + 로컬 outbox 의 `sync_status`(`ResultOutboxStore`, 실패 시 pending 복귀)로, offline 누적분을 나중에 push 해도 **합집합(중복 적재 0)**. 같은 event 를 두 번 sync 해도 central 은 dedup 한다(`BackendSyncService` + `platform_postgres_ingestion_writer` Rule 1 — UPDATE prior is_latest + INSERT new, FE-P0c).
- **중복 fact 는 보존(자동 병합 아님)**: 재동기화 시 두 station 이 만든 같은 `(project_id, condition_hash)` 의 별개 measurement_attempts 는 모두 보존되고 최신 1개만 `is_latest=true`. **자동 병합/충돌 해소를 하지 않는다** — 어느 측정이 정본인지의 판단은 사용자에게 표시하여 위임한다(중복 탐지 → UI 경고).

### Phase 경계

- **본 boundary(Phase 1)**: pull-based replication + stale 표시 + idempotent 재동기화 + 중복 탐지/표시까지.
- **자동 conflict resolution(동시 claim 경합의 자동 해소, last-writer-wins/CRDT 등)은 Phase 2/3 후속** — 과설계 회피를 위해 본 ADR 범위 밖(별도 ADR). 위 "Future (별도 ADR)" 의 Phase 2(WAL logical replication) / Phase 3(PostgreSQL single write target) 와 정합.

### 미완 (frontend 의존)

프론트 stale view + '중복 방지 미보장' 배지 + 재동기화 시 중복 탐지 UI 는 **FE-P2(coverage 대시보드)에 부착**되므로, FE-P2(현재 central read model 선행 부재로 DEFERRED — `FE-P0d` 권고) 이후 구현한다. 백엔드 idempotent 재동기화 기반은 FE-P0a/FE-P0c 로 충족(위 mechanism). 명시적 offline-accumulation→re-sync→무중복 e2e 회귀 테스트는 후속 권장(tech-debt).

## Revisit Conditions

1. **per-station daily measurement volume > 1M row** → TimescaleDB extension 검토
2. **frontend cross-PC view query p95 > 1s** → read replica 또는 materialized view refresh 주기 단축
3. **central DB 가 single point of failure** → multi-region replication
4. **PostgreSQL major version upgrade (16, 17, 18)** → schema migration 정책 갱신

## References

- backend `infrastructure/database/sqlite_connection_factory.py` — per-PC SQLite SSOT
- `docs/architecture/repository_split_adr.md` — 5-repo split (PostgreSQL central DB 책임)
- [PostgreSQL 15 release notes](https://www.postgresql.org/docs/15/release-15.html)
- [Strangler Fig pattern](https://martinfowler.com/bliki/StranglerFigApplication.html)
- backend Sprint F-2-D2~D4 — Web Migration phase 2
- **schema contract SSOT**: `docs/platform/central_db_schema.v1.json` (FE-P0a — measurement_attempts/claim_events/coverage view/refresh policy/project_membership stub/local-central field mappings)
- **migration evidence contract**: `docs/platform/central_db_migration_evidence.schema.v1.json`
- **DDL generator**: `scripts/export_platform_central_db_ddl.py` → `docs/platform/migrations/001_initial_central_db.sql`
- **contract test**: `tests/test_platform_central_db_schema_contract.py` (incl. `TestFeP0aAdrAlignmentDrift` — ADR ↔ contract drift 0 봉인)
