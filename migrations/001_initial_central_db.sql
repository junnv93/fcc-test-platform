-- Generated from docs/platform/central_db_schema.v1.json.
-- PostgreSQL initial migration planning artifact for fcc-test-platform.
-- Do not edit table definitions here without updating the JSON schema SSOT.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Applied central DB migration ledger (version + sha256 checksum) for the incremental migration runner. INSERT-on-apply; a checksum mismatch on an already-applied version is a drift error.
-- append-only: writers must INSERT only; updates of historical rows are forbidden.
CREATE TABLE IF NOT EXISTS "schema_migrations" (
    "id" UUID PRIMARY KEY,
    "version" TEXT NOT NULL UNIQUE,
    "checksum" TEXT NOT NULL,
    "applied_at" TIMESTAMPTZ NOT NULL,
    "applied_by" TEXT
);

-- Registered provider identities and capability cache.
CREATE TABLE IF NOT EXISTS "providers" (
    "id" UUID PRIMARY KEY,
    "provider_id" TEXT NOT NULL UNIQUE,
    "product_line" TEXT NOT NULL UNIQUE,
    "contract_family" TEXT NOT NULL,
    "contract_version" TEXT NOT NULL,
    "base_url" TEXT NOT NULL,
    "capabilities_json" JSONB NOT NULL,
    "enabled" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- Customer/project-level grouping for test campaigns.
CREATE TABLE IF NOT EXISTS "projects" (
    "id" UUID PRIMARY KEY,
    "project_code" TEXT NOT NULL UNIQUE,
    "name" TEXT NOT NULL,
    "customer" TEXT,
    "management_number" TEXT UNIQUE,
    "status" TEXT CONSTRAINT "ck_projects_status" CHECK ("status" IN ('active', 'completed')),
    "fcc_grantee_code" TEXT,
    "applicant_name" TEXT,
    "applicant_address" TEXT,
    "eut_description" TEXT,
    "test_standard" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- Device model metadata shared across providers.
CREATE TABLE IF NOT EXISTS "device_models" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "model_name" TEXT NOT NULL,
    "manufacturer" TEXT,
    "metadata_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- Physical or logical units under test.
CREATE TABLE IF NOT EXISTS "samples" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "model_id" UUID REFERENCES "device_models"("id"),
    "sample_code" TEXT NOT NULL,
    "serial_number" TEXT,
    "sample_number" TEXT,
    "test_category" TEXT,
    "label_number" TEXT,
    "smsn" TEXT,
    "intake_cert" TEXT,
    "assigned_team" TEXT,
    "sender" TEXT,
    "receiver" TEXT,
    "received_date" TEXT,
    "released_date" TEXT,
    "note" TEXT,
    "status" TEXT NOT NULL DEFAULT 'active' CONSTRAINT "ck_samples_status" CHECK ("status" IN ('active', 'deleted')),
    "row_version" INTEGER NOT NULL DEFAULT 1,
    "deleted_at" TIMESTAMPTZ,
    "deleted_by" TEXT,
    "metadata_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- Per-sample append-only intake history (시험원 입고 칸, 1:N to samples). Records firmware/calibration/hardware revision snapshots at intake time; web CRUD appends observations directly and no live Excel importer owns this table.
CREATE TABLE IF NOT EXISTS "sample_intakes" (
    "id" UUID PRIMARY KEY,
    "sample_id" UUID NOT NULL REFERENCES "samples"("id"),
    "intake_date" TEXT,
    "bl" TEXT,
    "ap" TEXT,
    "cp" TEXT,
    "csc" TEXT,
    "rf_cal" TEXT,
    "hw_rev" TEXT,
    "note" TEXT,
    "tech_group" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- Append-only full post-mutation snapshots for web sample CRUD. Every create, field patch, soft delete, and restore writes one monotonic revision in the same transaction as the current projection.
-- append-only: writers must INSERT only; updates of historical rows are forbidden.
CREATE TABLE IF NOT EXISTS "sample_inventory_revisions" (
    "id" UUID PRIMARY KEY,
    "sample_id" UUID NOT NULL REFERENCES "samples"("id"),
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "revision_number" INTEGER NOT NULL,
    "event_type" TEXT NOT NULL CONSTRAINT "ck_sample_inventory_revisions_event_type" CHECK ("event_type" IN ('created', 'updated', 'status_changed', 'restored', 'baseline')),
    "snapshot_json" JSONB NOT NULL,
    "changed_fields_json" JSONB NOT NULL,
    "actor_subject" TEXT NOT NULL,
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

-- Retired historical archive of legacy PM·RF sample-inventory Excel import runs. Existing rows remain readable for audit/migration evidence; the web CRUD path does not create new rows and no live reader/parser/diagnostics path depends on this table.
CREATE TABLE IF NOT EXISTS "sample_import_runs" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "model_name" TEXT,
    "samples_created" INTEGER NOT NULL,
    "samples_updated" INTEGER NOT NULL,
    "intakes_appended" INTEGER NOT NULL,
    "intakes_skipped" INTEGER NOT NULL,
    "sample_count" INTEGER NOT NULL,
    "diagnostics_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL
);

-- Platform identities after IdP integration. Identity is keyed by the OIDC-standard (issuer, subject) tuple; subject alone is only issuer-local.
CREATE TABLE IF NOT EXISTS "users" (
    "id" UUID PRIMARY KEY,
    "issuer" TEXT NOT NULL,
    "subject" TEXT NOT NULL,
    "display_name" TEXT,
    "email" TEXT,
    "enabled" BOOLEAN NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "password_hash" TEXT,
    "azure_ad_id" TEXT,
    "employee_id" TEXT,
    "department" TEXT,
    "position" TEXT,
    "site" TEXT,
    "phone_number" TEXT,
    "last_login" TIMESTAMPTZ,
    "failed_login_attempts" INTEGER,
    "locked_until" TIMESTAMPTZ,
    "last_failed_at" TIMESTAMPTZ,
    "password_changed_at" TIMESTAMPTZ,
    "force_password_change" BOOLEAN,
    "session_version" INTEGER
);

-- Named RBAC roles.
CREATE TABLE IF NOT EXISTS "roles" (
    "id" UUID PRIMARY KEY,
    "role_key" TEXT NOT NULL UNIQUE,
    "description" TEXT
);

-- Operation permissions aligned with the provider API contract.
CREATE TABLE IF NOT EXISTS "permissions" (
    "id" UUID PRIMARY KEY,
    "permission_key" TEXT NOT NULL UNIQUE,
    "description" TEXT
);

-- User to role membership.
CREATE TABLE IF NOT EXISTS "user_roles" (
    "user_id" UUID NOT NULL REFERENCES "users"("id"),
    "role_id" UUID NOT NULL REFERENCES "roles"("id")
);

-- Role to permission grants.
CREATE TABLE IF NOT EXISTS "role_permissions" (
    "role_id" UUID NOT NULL REFERENCES "roles"("id"),
    "permission_id" UUID NOT NULL REFERENCES "permissions"("id")
);

-- Global role to permission grants, separate from project membership grants. A global system_admin is the only role that may receive platform:sample-hard-delete.
CREATE TABLE IF NOT EXISTS "global_role_grants" (
    "role_key" TEXT NOT NULL REFERENCES "roles"("role_key"),
    "permission_key" TEXT NOT NULL REFERENCES "permissions"("permission_key")
);

-- Provider-normalized test sessions visible to the platform. Parent row of measurement_results / measurement_attempts / artifacts (session_id FK). The production ingestion pipeline upserts this row FIRST inside the same single-session transaction (INGESTION_TABLE_ORDER[0]) — before this, only dev_seed / live-proof scripts created it, so a live sync into a fresh DB violated the session_id FK. id is caller-supplied (deterministic uuid5 from provider_id + chamber_id + local session id for new chamber traffic; the legacy sentinel preserves the pre-multichamber UUID space), so re-sync is idempotent and the offline measurement loop needs no central round-trip); created_at/updated_at carry DB defaults so the mapper — which omits both — INSERTs without any caller stamp (same Option A ownership as measurement_results id/created_at). session_origin / workbook_handle are the per-PC mode-exclusivity observation axis (operator ruling 2026-08-16): a chamber PC either accepts web sessions or it does not, and "a project ends in the mode it started in" is an OPERATING rule with nothing enforcing it. Both are DECLARED by the composition root that created the session — never inferred, never client-supplied — and NULL means unknown, not LOCAL_PROGRAM. The vocabulary answers one provider-neutral sentence (did this measurement arrive as a web session); what a non-web PC runs is the provider’s business and central does not model it. workbook_handle is the upload handle verbatim, and because that handle is content-digest derived, "did two chambers use the same plan" becomes one query. This axis is OBSERVATION: nothing refuses a measurement, a session or an ingest because of these columns.
CREATE TABLE IF NOT EXISTS "test_sessions" (
    "id" UUID PRIMARY KEY,
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "provider_session_id" TEXT NOT NULL,
    "chamber_id" TEXT NOT NULL DEFAULT '__fcc_legacy__',
    "project_id" UUID REFERENCES "projects"("id"),
    "sample_id" UUID REFERENCES "samples"("id") ON DELETE SET NULL,
    "sample_snapshot_json" TEXT,
    "sample_snapshot_schema_version" TEXT,
    "project_result_reference_snapshot_json" TEXT,
    "project_result_reference_snapshot_schema_version" TEXT,
    "status" TEXT NOT NULL,
    "started_at" TIMESTAMPTZ,
    "completed_at" TIMESTAMPTZ,
    "metadata_json" JSONB,
    "session_origin" TEXT CONSTRAINT "ck_test_sessions_session_origin" CHECK ("session_origin" IN ('WEB_SESSION', 'LOCAL_PROGRAM')),
    "workbook_handle" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "ck_test_sessions_web_snapshot_complete" CHECK ("session_origin" <> 'WEB_SESSION' OR ("project_id" IS NOT NULL AND "sample_snapshot_json" IS NOT NULL AND "sample_snapshot_schema_version" IS NOT NULL))
);

-- Platform job requests sent to provider APIs.
CREATE TABLE IF NOT EXISTS "jobs" (
    "id" UUID PRIMARY KEY,
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "session_id" UUID REFERENCES "test_sessions"("id"),
    "provider_job_id" TEXT,
    "job_type" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "requested_by_user_id" UUID REFERENCES "users"("id"),
    "request_json" JSONB NOT NULL,
    "provider_response_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- Latest projection of provider-normalized measurement results per (session_id, condition_hash). FE-P0a extends with project_id direct FK + condition_hash + operator so coverage queries do not need a join through test_sessions. Append-only history lives in measurement_attempts. id/created_at carry DB defaults (gen_random_uuid()/now()) so the shared ingestion mapper — which omits both — INSERTs without any caller stamp; the production sync adapter and dev_seed share this DB owner.
CREATE TABLE IF NOT EXISTS "measurement_results" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "session_id" UUID NOT NULL REFERENCES "test_sessions"("id"),
    "project_id" UUID REFERENCES "projects"("id"),
    "provider_result_id" TEXT NOT NULL,
    "test_name" TEXT NOT NULL,
    "technology" TEXT NOT NULL,
    "condition_hash" TEXT,
    "condition_json" JSONB NOT NULL,
    "result_json" JSONB NOT NULL,
    "verdict" TEXT,
    "operator" TEXT,
    "measured_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only ledger of every measurement attempt. is_latest mirrors the latest projection in measurement_results. condition_hash is propagated from compute_condition_hash (no central recompute). Operator is the ApiPrincipal.subject (remote) or LOCAL_GUI_OPERATOR_SUBJECT (GUI default) captured at write time.
-- append-only: writers must INSERT only; updates of historical rows are forbidden.
CREATE TABLE IF NOT EXISTS "measurement_attempts" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "session_id" UUID NOT NULL REFERENCES "test_sessions"("id"),
    "project_id" UUID REFERENCES "projects"("id"),
    "measurement_result_id" UUID REFERENCES "measurement_results"("id"),
    "test_name" TEXT NOT NULL,
    "technology" TEXT NOT NULL,
    "condition_hash" TEXT NOT NULL,
    "attempt_number" INTEGER NOT NULL,
    "is_latest" BOOLEAN NOT NULL,
    "operator" TEXT,
    "status" TEXT NOT NULL,
    "verdict" TEXT,
    "margin" TEXT,
    "result_json" JSONB NOT NULL,
    "run_id" TEXT,
    "idempotency_key" TEXT,
    "recorded_by" TEXT,
    "provenance_json" JSONB,
    "measured_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only examiner selection ledger for one exact (project_id, provider_id, condition_hash) result partition. A selected event names one eligible completed attempt; a cleared event removes the manual override and restores deterministic latest selection. The revision and predecessor fields provide optimistic concurrency without mutating historical facts.
-- append-only: writers must INSERT only; updates of historical rows are forbidden.
CREATE TABLE IF NOT EXISTS "project_result_selection_events" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "condition_hash" TEXT NOT NULL,
    "action" TEXT NOT NULL CONSTRAINT "ck_project_result_selection_events_action" CHECK ("action" IN ('selected', 'cleared')),
    "attempt_id" UUID REFERENCES "measurement_attempts"("id"),
    "revision" INTEGER NOT NULL,
    "predecessor_event_id" UUID REFERENCES "project_result_selection_events"("id"),
    "expected_revision" INTEGER NOT NULL,
    "actor_subject" TEXT NOT NULL,
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "reason" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT "ck_project_result_selection_action_attempt" CHECK ((action = 'selected' AND attempt_id IS NOT NULL) OR (action = 'cleared' AND attempt_id IS NULL)),
    CONSTRAINT "ck_project_result_selection_revision_positive" CHECK (revision > 0 AND expected_revision >= 0 AND revision = expected_revision + 1)
);

-- Append-only generic project-result reference envelopes. The platform stores immutable source selection/attempt/session provenance plus an opaque provider-authored payload and exact content hash; provider compatibility and interpretation remain outside the central schema.
-- append-only: writers must INSERT only; updates of historical rows are forbidden.
CREATE TABLE IF NOT EXISTS "project_result_reference_revisions" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "producer_provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "revision_number" INTEGER NOT NULL,
    "reference_type" TEXT NOT NULL,
    "schema_version" TEXT NOT NULL,
    "source_selection_event_id" UUID NOT NULL REFERENCES "project_result_selection_events"("id"),
    "source_attempt_id" UUID NOT NULL REFERENCES "measurement_attempts"("id"),
    "source_session_id" UUID NOT NULL REFERENCES "test_sessions"("id"),
    "source_sample_id" UUID REFERENCES "samples"("id"),
    "source_chamber_id" TEXT,
    "payload_json" JSONB NOT NULL,
    "content_sha256" TEXT NOT NULL,
    "state" TEXT NOT NULL CONSTRAINT "ck_project_result_reference_revisions_state" CHECK ("state" IN ('published', 'retired')),
    "created_by" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "retired_by" TEXT,
    "retired_at" TIMESTAMPTZ,
    "retirement_reason" TEXT,
    CONSTRAINT "ck_project_result_reference_revision_positive" CHECK (revision_number > 0),
    CONSTRAINT "ck_project_result_reference_hash" CHECK (content_sha256 ~ '^[0-9a-fA-F]{64}$'),
    CONSTRAINT "ck_project_result_reference_retirement" CHECK ((state = 'published' AND retired_by IS NULL AND retired_at IS NULL AND retirement_reason IS NULL) OR (state = 'retired' AND retired_by IS NOT NULL AND retired_at IS NOT NULL AND retirement_reason IS NOT NULL))
);

-- Append-only ledger of measurement claim acquire/release/expire events. FE-P3 reads active_claims view derived from this ledger; operators acquire (project_id, technology, condition_hash) and other users see lock/warning. claim_id groups acquire/release of the same claim.
-- append-only: writers must INSERT only; updates of historical rows are forbidden.
CREATE TABLE IF NOT EXISTS "claim_events" (
    "id" UUID PRIMARY KEY,
    "claim_id" UUID NOT NULL,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "technology" TEXT NOT NULL,
    "condition_hash" TEXT NOT NULL,
    "operator" TEXT NOT NULL,
    "action" TEXT NOT NULL CONSTRAINT "ck_claim_events_action" CHECK ("action" IN ('acquired', 'released', 'expired')),
    "reason" TEXT,
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "expires_at" TIMESTAMPTZ,
    "session_id" UUID REFERENCES "test_sessions"("id"),
    "created_at" TIMESTAMPTZ NOT NULL
);

-- Project-scoped role assignment for a (user, role) pair (FE-P8). Mutable: assign upserts on (project_id, user_id, role_key); revoke deletes the row. Authorization reads through the project_member_permissions view (single-pass join). Every mutation appends to audit_events in the same DB transaction so a successful membership change is durable iff its audit is durable.
CREATE TABLE IF NOT EXISTS "project_membership" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "user_id" UUID NOT NULL REFERENCES "users"("id"),
    "role_key" TEXT NOT NULL,
    "team" TEXT,
    "assigned_at" TIMESTAMPTZ NOT NULL,
    "expires_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL
);

-- Append-only ledger of platform writes (claim acquire/release/expire + project_membership assign/revoke + local account unlock). account.unlocked (2026-08-23) is the administrator lifting a login lockout: the row names the actor and the target_user_subject, and it is written in the same transaction as the users UPDATE, so an unlock is durable iff its audit is. It is deliberately NOT project-scoped — a local account is not a project resource — which is why project_id is nullable on this table. Mirrors the claim_events append-only pattern (no UPDATE/DELETE of historical rows). Audit rows are written in the SAME DB transaction as the primary write so a successful platform change is durable iff its audit is durable (a failed audit INSERT rolls the primary write back, never a silent unaudited mutation). actor_subject is the authenticated ApiPrincipal.subject — never the request body (FE-P8 operator provenance).
-- append-only: writers must INSERT only; updates of historical rows are forbidden.
CREATE TABLE IF NOT EXISTS "audit_events" (
    "id" UUID PRIMARY KEY,
    "event_type" TEXT NOT NULL CONSTRAINT "ck_audit_events_event_type" CHECK ("event_type" IN ('claim.acquired', 'claim.released', 'claim.expired', 'membership.assigned', 'membership.revoked', 'account.unlocked', 'sample.hard_deleted')),
    "project_id" UUID REFERENCES "projects"("id"),
    "actor_subject" TEXT NOT NULL,
    "target_user_subject" TEXT,
    "target_claim_id" UUID,
    "role_key" TEXT,
    "detail_json" JSONB,
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL
);

-- Registry of distributed chamber PC nodes (멀티챔버 P1). One row per chamber: identity (chamber_id) + the node Session API base_url (used by the central measurement proxy in Phase 5) + per-chamber offline TTL. heartbeat_ttl_seconds default is sourced from domain DEFAULT_HEARTBEAT_TTL_SECONDS (no magic number in this schema). Mutable registry (like providers) — operational status is NOT stored here; it is derived from the chamber_heartbeat_events ledger via the chamber_availability view. artifact_storage_root (2026-08-09, plot-custody ②) is where that chamber's PC must write measurement plots: 운영자가 웹에서 지정하는 **기계의 속성**이고 노드가 부트에 pull 한다. nullable — 미설정이면 노드는 오늘처럼 워크북 Save Data 'File Structure' 칸을 쓴다(기능 상실 0). 자가 등록은 이 값을 **덮지 않는다**(COALESCE fill-only): 노드는 자기가 어디에 써야 하는지 모르고, 재부팅마다 운영자 설정이 지워지면 이 축이 존재할 이유가 없다. 명시적 변경/삭제는 update_chamber operation 이 한다. equipment_config_json (2026-08-10, SPLIT-6 ②) 은 그 방의 계측기 연결 설정(분석기/BT 테스터/스위치박스의 GPIB·LAN 주소)이고 같은 이유로 **방의 속성**이다. platform 은 이 map 의 키를 **모른다** — 키를 아는 것은 provider descriptor 와 노드 도메인뿐이고, 키를 컬럼으로 승격하면 provider 어휘가 중앙 스키마에 들어와 provider 가 늘 때마다 중앙 마이그레이션이 필요해진다(ADR-0018 D-6 3축 소유표). nullable 이고 default 가 없다 — NULL 은 '아무도 설정한 적 없다'(노드는 워크북 Chamber Config 시트를 쓴다)이고 '{}' 는 '비우기로 결정했다'라 접으면 폴백 규칙이 무너진다. 자가 등록은 이 값을 **쓰지 못한다**: artifact_storage_root 는 이미 등록 요청에 실려 있어 COALESCE fill-only 로 소급 방어했지만, 이 축은 첫날부터 전용 PATCH 를 가지므로 등록 컬럼 목록에 아예 없다(방어가 아니라 부재). accepts_web_sessions (per-PC mode exclusivity, operator ruling 2026-08-16) is the APPROVAL half of a two-axis fact: approval is a company-policy fact the operator manages centrally, and REALISATION ("I actually opened a listener") is observed by the node via heartbeat. The mismatch between them is the signal, so they must not collapse into one column. It is deliberately NULLABLE and three-valued — NULL means nobody has ruled, which is a different operator action from an explicit false. It is ALSO deliberately absent from the self-registration column list: a node does not know whether it has been approved, and the admin screen edits chambers by re-POSTing the registration, so absence (not COALESCE defence) is what protects the operator’s ruling. The vocabulary is provider-neutral — central knows only "does this chamber accept web sessions"; what a non-web PC runs is the provider’s business. enabled is NOT reused for this: that column already carries operational enable/disable and mixing deployment policy into it makes both unreadable. This axis is OBSERVATION: nothing is refused because of this column.
CREATE TABLE IF NOT EXISTS "chamber_nodes" (
    "id" UUID PRIMARY KEY,
    "chamber_id" TEXT NOT NULL UNIQUE,
    "name" TEXT NOT NULL,
    "base_url" TEXT NOT NULL,
    "enabled" BOOLEAN NOT NULL,
    "heartbeat_ttl_seconds" INTEGER NOT NULL,
    "artifact_storage_root" TEXT,
    "equipment_config_json" JSONB,
    "accepts_web_sessions" BOOLEAN,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- Append-only ledger of chamber heartbeat pushes (멀티챔버 P1 + C1 heartbeat-carried progress + M2 diagnostics). Each push appends one row with the node's self-reported operational status (idle/in_use). OFFLINE is NEVER stored — it is a derived state: when the latest heartbeat is older than heartbeat_ttl_seconds (or no heartbeat exists) the read service derives OFFLINE against an injected clock. progress_json carries the node's measurement progress snapshot (is_running/completed/total/ratio) and is populated ONLY on in_use heartbeats (idle nodes have no running measurement — domain Heartbeat invariant). last_error_json (M2, 2026-06-20) carries the node's latest operational error as a redacted {message, occurred_at} payload — OPTIONAL on any heartbeat status (an error is orthogonal to progress; a node may report a last error while idle/in_use). The write service redacts secrets/identifiers (URLs/tokens/paths/device ids) via the domain redaction SSOT BEFORE persistence. Both progress_json and last_error_json let the chamber_availability VIEW expose every chamber's live progress + last error in a single read, eliminating per-chamber N+1 lookups (the single get_chamber_measurement_progress endpoint remains as a fresh-fallback for one chamber). Mirrors the claim_events → active_claims append-only pattern; chamber_availability is the read projection.
-- append-only: writers must INSERT only; updates of historical rows are forbidden.
CREATE TABLE IF NOT EXISTS "chamber_heartbeat_events" (
    "id" UUID PRIMARY KEY,
    "chamber_id" TEXT NOT NULL REFERENCES "chamber_nodes"("chamber_id"),
    "reported_status" TEXT NOT NULL CONSTRAINT "ck_chamber_heartbeat_events_reported_status" CHECK ("reported_status" IN ('idle', 'in_use')),
    "session_id" UUID REFERENCES "test_sessions"("id"),
    "occurred_at" TIMESTAMPTZ NOT NULL,
    "expires_at" TIMESTAMPTZ,
    "detail_json" JSONB,
    "progress_json" JSONB,
    "last_error_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL
);

-- Metadata for files stored on the company file server or object store.
CREATE TABLE IF NOT EXISTS "artifacts" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "session_id" UUID NOT NULL REFERENCES "test_sessions"("id"),
    "measurement_result_id" UUID REFERENCES "measurement_results"("id"),
    "artifact_type" TEXT NOT NULL,
    "relative_path" TEXT NOT NULL,
    "original_filename" TEXT,
    "sha256" TEXT,
    "byte_size" INTEGER,
    "storage_backend" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 챔버 노드가 보고한 플롯 원본 보관 관측 스냅샷 (plot-dual-custody ① 조회 축, 2026-08-09). 중앙은 원본 보관소(회사 파일서버/챔버 PC 로컬)를 열 수 없으므로 판정하지 않는다 — 판정은 증거가 있는 노드에서 나오고 이 테이블은 그것을 받아 보관한다(참조 데이터 origin/replica 계층의 거울상: 참조는 중앙→로컬 PULL, 보관 판정은 로컬→중앙 PUSH). 자연키는 test_sessions 와 같은 (provider_id, chamber_id, provider_session_id) 이고 session_id FK 를 두지 않는다 — 읽기 시 그 자연키로 조인하면 나중에 test_sessions.project_id 가 해소될 때 이 스냅샷의 프로젝트 귀속이 백필 없이 따라오고, 두 번째 정체성 파생이 생기지 않는다. observed_at 은 노드가 관측한 시각 = watermark 이고 쓰기는 그 값 기준 latest-wins 다 — 재시도로 늦게 도착한 낡은 관측이 새 판정을 덮으면 화면이 과거로 되돌아간다. findings 는 조치 가능한 것(비-verified)만 담고 verified 개수는 여기 카운터에 남는다: 세션당 약 1,000장을 그대로 복제하면 전송·저장이 개수에 비례해 커지는데 그 목록으로 할 수 있는 일이 없다. 설계 근거: .claude/exec-plans/active/2026-08-09-plot-custody-web-and-chamber.md
CREATE TABLE IF NOT EXISTS "artifact_custody_snapshots" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "chamber_id" TEXT NOT NULL,
    "provider_session_id" TEXT NOT NULL,
    "status" TEXT NOT NULL CONSTRAINT "ck_artifact_custody_snapshots_status" CHECK ("status" IN ('verified', 'missing', 'diverged', 'unknown')),
    "verified_count" INTEGER NOT NULL DEFAULT 0,
    "missing_count" INTEGER NOT NULL DEFAULT 0,
    "diverged_count" INTEGER NOT NULL DEFAULT 0,
    "unknown_count" INTEGER NOT NULL DEFAULT 0,
    "roots_json" TEXT,
    "session_label" TEXT,
    "observed_at" TIMESTAMPTZ NOT NULL,
    "reported_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 보관 스냅샷의 조치 가능한 항목 (비-verified). 시험원이 '무엇을 옮겨야 하나'를 화면에서 답하려면 개수만으로는 부족하고 주소가 필요하다. verified 행은 담지 않는다 — 개수는 스냅샷 카운터에 남으므로 숨기는 것이 아니고, 담으면 세션당 약 1,000행이 된다. 스냅샷이 새 관측으로 갱신되면 같은 트랜잭션에서 전량 교체된다(부분 갱신이면 이미 옮긴 파일이 목록에 남아 시험원이 없는 일을 한다).
CREATE TABLE IF NOT EXISTS "artifact_custody_findings" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "snapshot_id" UUID NOT NULL REFERENCES "artifact_custody_snapshots"("id"),
    "relative_path" TEXT NOT NULL,
    "status" TEXT NOT NULL CONSTRAINT "ck_artifact_custody_findings_status" CHECK ("status" IN ('missing', 'diverged', 'unknown')),
    "artifact_type" TEXT,
    "expected_sha256" TEXT,
    "observed_sha256" TEXT,
    "reason" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Platform-visible report generation requests and outcomes. Ingestion supplies the deterministic id and required provider/session/status evidence; the database owns created_at with now() for fresh and upgraded databases.
CREATE TABLE IF NOT EXISTS "report_runs" (
    "id" UUID PRIMARY KEY,
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "session_id" UUID NOT NULL REFERENCES "test_sessions"("id"),
    "provider_report_request_id" TEXT,
    "status" TEXT NOT NULL,
    "requested_by_user_id" UUID REFERENCES "users"("id"),
    "report_types_json" JSONB,
    "warnings_json" JSONB,
    "error_message" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "completed_at" TIMESTAMPTZ
);

-- Metadata for generated DOCX/PDF/XLSX report files.
CREATE TABLE IF NOT EXISTS "report_outputs" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "report_run_id" UUID NOT NULL REFERENCES "report_runs"("id"),
    "file_name" TEXT NOT NULL,
    "relative_path" TEXT NOT NULL,
    "sha256" TEXT,
    "byte_size" INTEGER,
    "storage_backend" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 성적서(FCC test certificate) instances issued per project/model (Phase G, 2026-06-23). One project (= one model) has N test_reports (editions/revisions). report_number is NOT a column — it is DERIVED from projects.management_number + edition (report_number_policy SSOT, mirror of the derived fcc_id: never stored). edition is the edition/version token (e.g. 'E2V1') that completes 'S-{management_number}-{edition}'. date_tested_start/end are stored as provided (free text, consistent with sample received_date/intake_date); auto-deriving them from the measurement min~max requires the local measurement DB and is a hardware-session follow-up. rev_history_json carries the revision history array (rev/issue_date/presented_by). The report header citation (SN / firmware / FCC ID / applicant / test_standard) is ASSEMBLED at read time from projects + samples + sample_intakes (report_citation domain SSOT) — not duplicated here.
CREATE TABLE IF NOT EXISTS "test_reports" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "edition" TEXT NOT NULL,
    "date_of_issue" TEXT,
    "date_tested_start" TEXT,
    "date_tested_end" TEXT,
    "prepared_by" TEXT,
    "prepared_site" TEXT,
    "rev_history_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- 성적서 §6 TEST AND MEASUREMENT EQUIPMENT 표에 실리는, 프로젝트가 실제로 사용한 장비 목록 (2026-08-07). EMS(사내 장비관리시스템)가 팀 × 시험항목 × 챔버별 '표준 장비리스트'의 SSOT 이고, 이 테이블은 그 표준 목록을 성적서 작성 시점에 pull 해서 시험원이 실사용본으로 편집·확정한 결과다. EMS 를 성적서 생성 시점에 실시간 조회하지 않는다 — 저장 순간 값을 items 에 복사해 고정하므로 과거 성적서를 재생성해도 값이 흔들리지 않는다. test_item_key 는 시험항목 = 성적서 한 편(DTS/BLE/BT/UNII, 도메인 test_equipment_list_policy.TestItemKey SSOT + reporting ReportTech parity)이며 coverage_technology 도, EMS 표기도 아니다 (2026-08-08 축 정정): 어떤 측정이 어떤 성적서를 발행하는지는 FCC 가 아는 사실이고, EMS 표기('DFS, UNII')는 성적서 여러 편에 걸쳐 '이 성적서의 장비목록'을 집을 수 없게 만든다. 컬럼은 text 로 두고 CHECK 를 걸지 않는다 — provider 확장(mmWave/UWB)이 곧 다른 성적서라 CHECK 로 굳히면 확장마다 중앙 마이그레이션이 필요하다; 검증은 애플리케이션 경계가 한다. test_report_id 는 nullable — 시험원은 성적서 행을 만들기 전(시험 ~90% 시점)부터 장비를 고를 수 있고, 그 단계의 목록은 프로젝트에만 귀속된다(부분 unique 인덱스 2개가 두 단계를 각각 봉인). 같은 모델에 성적서를 여러 판 내면서 장비가 달라질 수 있으므로 판마다 별도 행을 갖는다. 설계 근거: .claude/exec-plans/active/2026-08-07-report-equipment-list-domain-migration.md
CREATE TABLE IF NOT EXISTS "test_equipment_lists" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "test_report_id" UUID REFERENCES "test_reports"("id"),
    "test_item_key" TEXT NOT NULL,
    "test_item_name" TEXT,
    "status" TEXT NOT NULL DEFAULT 'draft' CONSTRAINT "ck_test_equipment_lists_status" CHECK ("status" IN ('draft', 'confirmed')),
    "source_profile_key" TEXT,
    "source_revision_key" TEXT,
    "source_pulled_at" TIMESTAMPTZ,
    "confirmed_at" TIMESTAMPTZ,
    "created_at" TIMESTAMPTZ NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL
);

-- test_equipment_lists 한 건에 속한 장비/시험용 소프트웨어 행. 확정 시점의 불변 스냅샷이며 EMS master 를 조인하지 않는다(값 복사). 컬럼은 성적서 §6 의 두 표를 그대로 따른다 — 장비표 Description/Manufacturer/Model/S-N/Cal Due, 소프트웨어표 Description/Manufacturer/Model/Version. calibration_due_date 는 date 가 아니라 text 다: 원천 값이 'N/A' 를 포함하고 형식도 섞여 있어 제공된 그대로 보관한다(test_reports.date_tested_* 선례). 교정성적서 번호·인정기관·불확도는 성적서 표에 나가지 않으므로 컬럼을 두지 않는다. remarks 는 화면 전용 운영 메모로 §6 표에는 출력되지 않는다.
CREATE TABLE IF NOT EXISTS "test_equipment_list_items" (
    "id" UUID PRIMARY KEY,
    "list_id" UUID NOT NULL REFERENCES "test_equipment_lists"("id"),
    "item_type" TEXT NOT NULL CONSTRAINT "ck_test_equipment_list_items_item_type" CHECK ("item_type" IN ('equipment', 'test_software')),
    "section_name" TEXT,
    "sort_order" INTEGER NOT NULL,
    "description" TEXT,
    "manufacturer" TEXT,
    "model_name" TEXT,
    "serial_number" TEXT,
    "calibration_due_date" TEXT,
    "software_version" TEXT,
    "remarks" TEXT,
    "created_at" TIMESTAMPTZ NOT NULL
);

-- Operator-visible diagnostics and review items from providers or platform processing.
CREATE TABLE IF NOT EXISTS "diagnostics" (
    "id" UUID PRIMARY KEY,
    "provider_id" UUID REFERENCES "providers"("id"),
    "session_id" UUID REFERENCES "test_sessions"("id"),
    "measurement_result_id" UUID REFERENCES "measurement_results"("id"),
    "report_run_id" UUID REFERENCES "report_runs"("id"),
    "severity" TEXT NOT NULL,
    "code" TEXT NOT NULL,
    "message" TEXT NOT NULL,
    "details_json" JSONB,
    "created_at" TIMESTAMPTZ NOT NULL
);

-- Provider-owned standard-time catalog (Phase 6 time-weighted progress). Maps a canonical measurement test type (test_type_canonical — the MeasurementType dispatch token) to its planned minutes at test-type granularity (D-P6-GRAN). The catalog is the provider's DEFAULT-minutes SSOT only; published_plan_expectation snapshots the value + version at ingest so editing the catalog never retroactively changes already-published progress (D-P6-SNAP). source is 'workbook_seed' (initial seed from the operator workbook) or 'manual' (edited in-app). Provider-owned: the platform stores and reads these rows but never derives the minutes (ADR-0010).
CREATE TABLE IF NOT EXISTS "standard_time_catalog" (
    "id" UUID PRIMARY KEY,
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "test_type_canonical" TEXT NOT NULL,
    "planned_minutes" NUMERIC NOT NULL,
    "version" INTEGER NOT NULL,
    "source" TEXT NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL,
    "updated_by" TEXT
);

-- Phase 6 time-weighted progress DENOMINATOR. One append-only row per published-plan condition, carrying the provider-computed (ADR-0010 — platform never derives these) progress bucket / area / canonical test type, plus the catalog minutes snapshotted at publish time (catalog_version stamped, D-P6-SNAP). The progress rollup LEFT JOINs measurement coverage on (project_id, provider_id, condition_hash): provider_id is REQUIRED in the join because condition_hash does NOT distinguish conducted / radiated / licensed / mmWave headless (F1) — without it, one headless's measurement would satisfy another's expectation. condition_hash is the VERBATIM stable published-plan hash (never recomputed — sealed by test_p6_2_condition_hash_join_key_seal.py). pricing_status is 'priced' (test_type_canonical in catalog → planned_minutes_snapshot set, counts toward the percent denominator) or 'unpriced' (unknown/absent type → planned_minutes_snapshot NULL, surfaced separately as unpriced_minutes, never folded into the percent as zero). progress_bucket_id NULL = unbucketable condition (surfaced, not mis-bucketed). plan_published_at carries the originating plan's publish time so the progress read can apply read-side latest-wins: per (project_id, provider_id) only the row set of the MAX(plan_published_at) plan is rolled up (ROW_NUMBER window). Writes stay pure additive upserts (no destructive supersession), so an old in-flight expectation sync committing after a newer plan can never regress the denominator — its older published_at simply loses the window. NULL plan_published_at sorts oldest.
CREATE TABLE IF NOT EXISTS "published_plan_expectation" (
    "id" UUID PRIMARY KEY,
    "project_id" UUID NOT NULL REFERENCES "projects"("id"),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "plan_id" TEXT NOT NULL,
    "condition_hash" TEXT NOT NULL,
    "coverage_technology" TEXT NOT NULL,
    "raw_test_type" TEXT NOT NULL,
    "test_type_canonical" TEXT,
    "progress_bucket_id" TEXT,
    "progress_area" TEXT NOT NULL,
    "planned_minutes_snapshot" NUMERIC,
    "catalog_version" INTEGER,
    "pricing_status" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL,
    "plan_published_at" TIMESTAMPTZ
);

-- Wave 3 (2026-08-07) — the AUTHORITATIVE ORIGIN of measurement reference data (Correction / Switch port mapping / Frequency Table / Analyzer Settings / Ant gain). The chamber PC's logs/reference_catalog.db is a READ-ONLY REPLICA of these rows, not the origin: the workbook is a one-time import source that lands HERE, editing and publishing happen in the web UI, and chamber nodes pull a delivery bundle. This mirrors the measurement-result axis in the opposite direction — results flow local→central while the offline measurement loop keeps zero central round-trips; reference data flows central→local under the same rule, so an unreachable central degrades to a stale replica rather than a stopped chamber. IDENTITY is (provider_id, family, profile_id, scope_id): provider_id is REQUIRED because 'correction' means different things to unlicensed / mmWave / licensed headless (same reason published_plan_expectation carries it), and omitting it would let one provider's cable loss satisfy another's lookup. scope_kind/scope_id carry the reference_scope_policy axis — ROOM for the cabling bolted into one shield room (correction, switch port mapping), PROJECT for values that follow the device under test. The room key is the chamber identity (one PC per room). PUBLISH UNIQUENESS IS ENFORCED HERE IN DDL by a partial unique index, not merely detected by the reader: the local adapter can only raise AmbiguousPublishedRevisionError after the fact, whereas the origin can refuse to create the second published row at all. state/scope_kind vocabularies mirror the domain enums RevisionState / ReferenceScopeKind and are sealed against them by a parity test — the DB constraint buys integrity against a corrupt writer, the test buys no-drift. Lifecycle actor/instant/reason triples are stored as provided (consistent with test_reports date fields). COLUMN SET IS A FAITHFUL SUPERSET of the chamber replica's reference_catalog_revisions: version and the approved_by/at/reason triple exist here because the replica's schema requires them and a replica must be constructible from central rows without inventing values. family_identity_key is deliberately absent — it is derived (family|profile|scope) by the domain, and storing a derived identifier is the mistake report_number and fcc_id already avoid. content_sha256 and scope_kind are central-only: the former is integrity the origin owns, the latter is derivable from the family but is stored so a bundle query can filter by axis without loading the policy. provenance_kind (2026-08-09) splits a question source_snapshot_id used to answer alone. Before web authoring existed the only way to make a revision was to import a workbook, so 'where this edition started' and 'where these values came from' were the same fact; forking snapshot X and re-measuring one port's cable loss separates them, and X stays the honest starting point while some values are no longer X's. It is stated in its own column rather than inferred from forked_from_revision_id because a field that quietly acquires a second meaning is the failure this repository has paid for repeatedly, and this axis carries audit evidence. The lattice is monotone (import=WORKBOOK, fork inherits, an entry edit promotes to FORK_EDIT at the moment a value actually changes) and the vocabulary mirrors the domain enum RevisionProvenanceKind, sealed against it and against the 017 CHECK by a three-way parity test. Clients never send it: which operation was used IS the value, so accepting it in a request body would make it forgeable. WEB_AUTHORED (2026-08-11) is the third and terminal kind: once the workbook is no longer something a tester opens, a revision can be born on the web with no workbook behind it, and source_snapshot_id/source_manifest_sha256 are therefore NULLABLE for exactly that kind. They are not simply relaxed — a CHECK ties the nullability to the provenance (a WORKBOOK/FORK_EDIT revision must still carry its snapshot link), because dropping NOT NULL alone would silently let the importer create a revision nobody can justify.
CREATE TABLE IF NOT EXISTS "reference_revisions" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "provider_id" UUID NOT NULL REFERENCES "providers"("id"),
    "family" TEXT NOT NULL,
    "profile_id" TEXT NOT NULL,
    "scope_kind" TEXT NOT NULL CONSTRAINT "ck_reference_revisions_scope_kind" CHECK ("scope_kind" IN ('room', 'project')),
    "scope_id" TEXT NOT NULL,
    "revision_number" INTEGER NOT NULL,
    "state" TEXT NOT NULL CONSTRAINT "ck_reference_revisions_state" CHECK ("state" IN ('CANDIDATE', 'PUBLISHED', 'RETIRED')),
    "version" INTEGER NOT NULL DEFAULT 1,
    "etag" TEXT NOT NULL,
    "content_sha256" TEXT NOT NULL,
    "source_snapshot_id" TEXT,
    "source_manifest_sha256" TEXT,
    "official_manifest_sha256" TEXT,
    "forked_from_revision_id" UUID REFERENCES "reference_revisions"("id"),
    "provenance_kind" TEXT NOT NULL DEFAULT 'WORKBOOK' CONSTRAINT "ck_reference_revisions_provenance_kind" CHECK ("provenance_kind" IN ('WORKBOOK', 'FORK_EDIT', 'WEB_AUTHORED')),
    "created_by" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "updated_by" TEXT NOT NULL,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "approved_by" TEXT,
    "approved_at" TIMESTAMPTZ,
    "approval_reason" TEXT,
    "published_by" TEXT,
    "published_at" TIMESTAMPTZ,
    "publish_reason" TEXT,
    "retired_by" TEXT,
    "retired_at" TIMESTAMPTZ,
    "retirement_reason" TEXT,
    CONSTRAINT "ck_reference_revisions_snapshot_link" CHECK ("provenance_kind" = 'WEB_AUTHORED' OR ("source_snapshot_id" IS NOT NULL AND "source_manifest_sha256" IS NOT NULL))
);

-- Wave 3 (2026-08-07) — the rows one reference revision carries. payload_json is DELIBERATELY OPAQUE to the platform: it is a runtime lookup row whose field set is owned by the provider's PROJECTION_FIELD_CONTRACT (e.g. correction = correction_index/frequency_hz/correction_db). Normalising those 5 families × ~25 columns into central columns would fork that contract into a THIRD schema and would put measurement arithmetic fields in the platform — the same boundary forbidden_platform_columns draws, and the same reason condition_json / result_json stay opaque (ADR-0005 / ADR-0010). entry_order preserves the source row order so a replica projects rows in the order seeding would have produced them. content_sha256 is the per-entry hash from the provider's reference_hashing SSOT, reused (not recomputed centrally — the platform package may not import hashlib) so that carry-forward of effective_from across re-imports compares the same bytes on both sides. reference_id / identity_key are the provider's stable natural-key derivation; the platform stores and returns them without interpreting them.
CREATE TABLE IF NOT EXISTS "reference_entries" (
    "id" UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    "revision_id" UUID NOT NULL REFERENCES "reference_revisions"("id"),
    "entry_order" INTEGER NOT NULL,
    "reference_id" TEXT NOT NULL,
    "identity_key" TEXT NOT NULL,
    "payload_json" JSONB NOT NULL,
    "test_condition_ids_json" JSONB,
    "effective_from" TEXT,
    "effective_to" TEXT,
    "source_sheet_name" TEXT,
    "source_row_number" INTEGER,
    "content_sha256" TEXT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes: schema_migrations
CREATE UNIQUE INDEX IF NOT EXISTS "ux_schema_migrations_version" ON "schema_migrations" ("version");

-- Indexes: providers
CREATE UNIQUE INDEX IF NOT EXISTS "ux_providers_provider_id" ON "providers" ("provider_id");
CREATE UNIQUE INDEX IF NOT EXISTS "ux_providers_product_line" ON "providers" ("product_line");

-- Indexes: projects
CREATE UNIQUE INDEX IF NOT EXISTS "ux_projects_project_code" ON "projects" ("project_code");
CREATE UNIQUE INDEX IF NOT EXISTS "ux_projects_management_number" ON "projects" ("management_number");
CREATE INDEX IF NOT EXISTS "idx_projects_directory" ON "projects" ("created_at", "id");
CREATE INDEX IF NOT EXISTS "idx_projects_status_directory" ON "projects" ("status", "created_at", "id");
CREATE INDEX IF NOT EXISTS "idx_projects_search_management_number" ON "projects" USING gin (lower(management_number) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS "idx_projects_search_project_code" ON "projects" USING gin (lower(project_code) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS "idx_projects_search_customer" ON "projects" USING gin (lower(customer) gin_trgm_ops);

-- Indexes: device_models
CREATE INDEX IF NOT EXISTS "idx_device_models_project" ON "device_models" ("project_id");

-- Indexes: samples
CREATE INDEX IF NOT EXISTS "idx_samples_project" ON "samples" ("project_id");
CREATE INDEX IF NOT EXISTS "idx_samples_model" ON "samples" ("model_id");
CREATE UNIQUE INDEX IF NOT EXISTS "ux_samples_project_sample_number" ON "samples" ("project_id", "sample_number");

-- Indexes: sample_intakes
CREATE INDEX IF NOT EXISTS "idx_sample_intakes_sample" ON "sample_intakes" ("sample_id");

-- Indexes: sample_inventory_revisions
CREATE UNIQUE INDEX IF NOT EXISTS "ux_sample_inventory_revisions_sample_revision" ON "sample_inventory_revisions" ("sample_id", "revision_number");
CREATE INDEX IF NOT EXISTS "idx_sample_inventory_revisions_project_occurred_sample" ON "sample_inventory_revisions" ("project_id", "occurred_at", "sample_id");
CREATE INDEX IF NOT EXISTS "idx_sample_inventory_revisions_sample_occurred" ON "sample_inventory_revisions" ("sample_id", "occurred_at");

-- Indexes: sample_import_runs
CREATE INDEX IF NOT EXISTS "idx_sample_import_runs_project" ON "sample_import_runs" ("project_id", "created_at");

-- Indexes: users
CREATE UNIQUE INDEX IF NOT EXISTS "ux_users_issuer_subject" ON "users" ("issuer", "subject");
CREATE UNIQUE INDEX IF NOT EXISTS "ux_users_email_lower" ON "users" (LOWER("email")) WHERE "issuer" = 'urn:fcc:identity:local' AND "email" IS NOT NULL AND "email" <> '';

-- Indexes: roles
CREATE UNIQUE INDEX IF NOT EXISTS "ux_roles_role_key" ON "roles" ("role_key");

-- Indexes: permissions
CREATE UNIQUE INDEX IF NOT EXISTS "ux_permissions_permission_key" ON "permissions" ("permission_key");

-- Indexes: user_roles
CREATE UNIQUE INDEX IF NOT EXISTS "ux_user_roles_user_role" ON "user_roles" ("user_id", "role_id");

-- Indexes: role_permissions
CREATE UNIQUE INDEX IF NOT EXISTS "ux_role_permissions_role_permission" ON "role_permissions" ("role_id", "permission_id");

-- Indexes: global_role_grants
CREATE UNIQUE INDEX IF NOT EXISTS "ux_global_role_grants_role_permission" ON "global_role_grants" ("role_key", "permission_key");
CREATE INDEX IF NOT EXISTS "idx_global_role_grants_permission" ON "global_role_grants" ("permission_key");

-- Indexes: test_sessions
CREATE UNIQUE INDEX IF NOT EXISTS "ux_test_sessions_provider_chamber_session" ON "test_sessions" ("provider_id", "chamber_id", "provider_session_id");
CREATE INDEX IF NOT EXISTS "idx_test_sessions_project_sample" ON "test_sessions" ("project_id", "sample_id");
CREATE INDEX IF NOT EXISTS "idx_test_sessions_status" ON "test_sessions" ("status");

-- Indexes: jobs
CREATE INDEX IF NOT EXISTS "idx_jobs_provider_status" ON "jobs" ("provider_id", "status");
CREATE INDEX IF NOT EXISTS "idx_jobs_session" ON "jobs" ("session_id");
CREATE INDEX IF NOT EXISTS "idx_jobs_created_at" ON "jobs" ("created_at");

-- Indexes: measurement_results
CREATE UNIQUE INDEX IF NOT EXISTS "ux_measurement_results_provider_result" ON "measurement_results" ("provider_id", "provider_result_id");
CREATE INDEX IF NOT EXISTS "idx_measurement_results_session" ON "measurement_results" ("session_id");
CREATE INDEX IF NOT EXISTS "idx_measurement_results_technology_test" ON "measurement_results" ("technology", "test_name");
CREATE INDEX IF NOT EXISTS "idx_measurement_results_verdict" ON "measurement_results" ("verdict");
CREATE INDEX IF NOT EXISTS "idx_measurement_results_project_condition_hash" ON "measurement_results" ("project_id", "condition_hash");
CREATE INDEX IF NOT EXISTS "idx_measurement_results_operator" ON "measurement_results" ("operator");

-- Indexes: measurement_attempts
CREATE UNIQUE INDEX IF NOT EXISTS "ux_measurement_attempts_idempotency_key" ON "measurement_attempts" ("idempotency_key") WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS "ux_measurement_attempts_session_condition_attempt" ON "measurement_attempts" ("session_id", "condition_hash", "attempt_number");
CREATE UNIQUE INDEX IF NOT EXISTS "ux_measurement_attempts_provider_session_condition_attempt" ON "measurement_attempts" ("provider_id", "session_id", "condition_hash", "attempt_number");
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_project_condition_hash" ON "measurement_attempts" ("project_id", "condition_hash");
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_project_provider_condition_hash" ON "measurement_attempts" ("project_id", "provider_id", "condition_hash");
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_session_measured_at" ON "measurement_attempts" ("session_id", "measured_at");
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_result" ON "measurement_attempts" ("measurement_result_id");
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_is_latest" ON "measurement_attempts" ("project_id", "provider_id", "condition_hash", "is_latest");
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_project_provider_condition_recency" ON "measurement_attempts" ("project_id", "provider_id", "condition_hash", "measured_at" DESC NULLS LAST, "created_at" DESC, "id" DESC) WHERE status = 'completed';
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_progress_join" ON "measurement_attempts" ("project_id", "provider_id", "condition_hash", "is_latest");
CREATE INDEX IF NOT EXISTS "idx_measurement_attempts_operator" ON "measurement_attempts" ("operator");

-- Indexes: project_result_selection_events
CREATE UNIQUE INDEX IF NOT EXISTS "ux_project_result_selection_partition_revision" ON "project_result_selection_events" ("project_id", "provider_id", "condition_hash", "revision");
CREATE INDEX IF NOT EXISTS "idx_project_result_selection_partition_latest" ON "project_result_selection_events" ("project_id", "provider_id", "condition_hash", "revision", "occurred_at");
CREATE INDEX IF NOT EXISTS "idx_project_result_selection_attempt" ON "project_result_selection_events" ("attempt_id");

-- Indexes: project_result_reference_revisions
CREATE UNIQUE INDEX IF NOT EXISTS "ux_project_result_reference_project_provider_type_revision" ON "project_result_reference_revisions" ("project_id", "producer_provider_id", "reference_type", "revision_number");
CREATE INDEX IF NOT EXISTS "idx_project_result_reference_source_selection" ON "project_result_reference_revisions" ("source_selection_event_id");
CREATE INDEX IF NOT EXISTS "idx_project_result_reference_provider_state" ON "project_result_reference_revisions" ("producer_provider_id", "state", "reference_type", "schema_version");
CREATE INDEX IF NOT EXISTS "idx_project_result_reference_project_created" ON "project_result_reference_revisions" ("project_id", "created_at", "id");

-- Indexes: claim_events
CREATE INDEX IF NOT EXISTS "idx_claim_events_project_condition_hash_occurred" ON "claim_events" ("project_id", "condition_hash", "occurred_at");
CREATE INDEX IF NOT EXISTS "idx_claim_events_claim_id_occurred" ON "claim_events" ("claim_id", "occurred_at");
CREATE INDEX IF NOT EXISTS "idx_claim_events_operator_occurred" ON "claim_events" ("operator", "occurred_at");
CREATE INDEX IF NOT EXISTS "idx_claim_events_action" ON "claim_events" ("action", "occurred_at");

-- Indexes: project_membership
CREATE UNIQUE INDEX IF NOT EXISTS "ux_project_membership_project_user_role" ON "project_membership" ("project_id", "user_id", "role_key");
CREATE INDEX IF NOT EXISTS "idx_project_membership_user" ON "project_membership" ("user_id");

-- Indexes: audit_events
CREATE INDEX IF NOT EXISTS "idx_audit_events_project_occurred" ON "audit_events" ("project_id", "occurred_at");
CREATE INDEX IF NOT EXISTS "idx_audit_events_actor_occurred" ON "audit_events" ("actor_subject", "occurred_at");
CREATE INDEX IF NOT EXISTS "idx_audit_events_event_type_occurred" ON "audit_events" ("event_type", "occurred_at");

-- Indexes: chamber_nodes
CREATE UNIQUE INDEX IF NOT EXISTS "ux_chamber_nodes_chamber_id" ON "chamber_nodes" ("chamber_id");

-- Indexes: chamber_heartbeat_events
CREATE INDEX IF NOT EXISTS "idx_chamber_heartbeat_events_chamber_occurred" ON "chamber_heartbeat_events" ("chamber_id", "occurred_at");
CREATE INDEX IF NOT EXISTS "idx_chamber_heartbeat_events_status_occurred" ON "chamber_heartbeat_events" ("reported_status", "occurred_at");

-- Indexes: artifacts
CREATE UNIQUE INDEX IF NOT EXISTS "ux_artifacts_provider_relative_path" ON "artifacts" ("provider_id", "relative_path");
CREATE INDEX IF NOT EXISTS "idx_artifacts_session_type" ON "artifacts" ("session_id", "artifact_type");
CREATE INDEX IF NOT EXISTS "idx_artifacts_result" ON "artifacts" ("measurement_result_id");
CREATE INDEX IF NOT EXISTS "idx_artifacts_provider_sha256" ON "artifacts" ("provider_id", "sha256");

-- Indexes: artifact_custody_snapshots
CREATE UNIQUE INDEX IF NOT EXISTS "ux_artifact_custody_snapshots_session" ON "artifact_custody_snapshots" ("provider_id", "chamber_id", "provider_session_id");
CREATE INDEX IF NOT EXISTS "idx_artifact_custody_snapshots_status" ON "artifact_custody_snapshots" ("status");

-- Indexes: artifact_custody_findings
CREATE INDEX IF NOT EXISTS "idx_artifact_custody_findings_snapshot" ON "artifact_custody_findings" ("snapshot_id");

-- Indexes: report_runs
CREATE INDEX IF NOT EXISTS "idx_report_runs_session_status" ON "report_runs" ("session_id", "status");
CREATE INDEX IF NOT EXISTS "idx_report_runs_provider_request" ON "report_runs" ("provider_id", "provider_report_request_id");

-- Indexes: report_outputs
CREATE INDEX IF NOT EXISTS "idx_report_outputs_run" ON "report_outputs" ("report_run_id");
CREATE UNIQUE INDEX IF NOT EXISTS "ux_report_outputs_run_path" ON "report_outputs" ("report_run_id", "relative_path");

-- Indexes: test_reports
CREATE UNIQUE INDEX IF NOT EXISTS "ux_test_reports_project_edition" ON "test_reports" ("project_id", "edition");
CREATE INDEX IF NOT EXISTS "idx_test_reports_project" ON "test_reports" ("project_id");

-- Indexes: test_equipment_lists
CREATE UNIQUE INDEX IF NOT EXISTS "ux_test_equipment_lists_project_item" ON "test_equipment_lists" ("project_id", "test_item_key") WHERE test_report_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS "ux_test_equipment_lists_report_item" ON "test_equipment_lists" ("test_report_id", "test_item_key") WHERE test_report_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS "idx_test_equipment_lists_project" ON "test_equipment_lists" ("project_id");
CREATE INDEX IF NOT EXISTS "idx_test_equipment_lists_report" ON "test_equipment_lists" ("test_report_id");

-- Indexes: test_equipment_list_items
CREATE INDEX IF NOT EXISTS "idx_test_equipment_list_items_list_order" ON "test_equipment_list_items" ("list_id", "sort_order");
CREATE INDEX IF NOT EXISTS "idx_test_equipment_list_items_list_type" ON "test_equipment_list_items" ("list_id", "item_type");

-- Indexes: diagnostics
CREATE INDEX IF NOT EXISTS "idx_diagnostics_session_severity" ON "diagnostics" ("session_id", "severity");
CREATE INDEX IF NOT EXISTS "idx_diagnostics_report_run" ON "diagnostics" ("report_run_id");
CREATE INDEX IF NOT EXISTS "idx_diagnostics_code" ON "diagnostics" ("code");

-- Indexes: standard_time_catalog
CREATE UNIQUE INDEX IF NOT EXISTS "ux_standard_time_catalog_provider_test_type" ON "standard_time_catalog" ("provider_id", "test_type_canonical");

-- Indexes: published_plan_expectation
CREATE UNIQUE INDEX IF NOT EXISTS "ux_published_plan_expectation_project_provider_plan_condition" ON "published_plan_expectation" ("project_id", "provider_id", "plan_id", "condition_hash");
CREATE INDEX IF NOT EXISTS "idx_published_plan_expectation_join" ON "published_plan_expectation" ("project_id", "provider_id", "condition_hash");
CREATE INDEX IF NOT EXISTS "idx_published_plan_expectation_rollup" ON "published_plan_expectation" ("project_id", "provider_id", "progress_area", "progress_bucket_id");

-- Indexes: reference_revisions
CREATE UNIQUE INDEX IF NOT EXISTS "ux_reference_revisions_identity_number" ON "reference_revisions" ("provider_id", "family", "profile_id", "scope_id", "revision_number");
CREATE UNIQUE INDEX IF NOT EXISTS "ux_reference_revisions_published" ON "reference_revisions" ("provider_id", "family", "profile_id", "scope_id") WHERE state = 'PUBLISHED';
CREATE INDEX IF NOT EXISTS "idx_reference_revisions_scope" ON "reference_revisions" ("provider_id", "scope_kind", "scope_id", "family");
CREATE INDEX IF NOT EXISTS "idx_reference_revisions_state" ON "reference_revisions" ("provider_id", "state", "updated_at");

-- Indexes: reference_entries
CREATE UNIQUE INDEX IF NOT EXISTS "ux_reference_entries_revision_reference" ON "reference_entries" ("revision_id", "reference_id");
CREATE UNIQUE INDEX IF NOT EXISTS "ux_reference_entries_revision_order" ON "reference_entries" ("revision_id", "entry_order");
CREATE INDEX IF NOT EXISTS "idx_reference_entries_identity" ON "reference_entries" ("revision_id", "identity_key");

-- Additive column upgrades (idempotent, PostgreSQL 9.6+).
-- Existing central DBs created before a column was introduced receive it via
-- ALTER ... ADD COLUMN IF NOT EXISTS BEFORE the views below are (re)created,
-- so re-running this single migration against an older DB stays additive-safe.
-- Fresh DBs already have the column from CREATE TABLE above (these are no-ops).

-- users.password_hash: added in local identity — EMS schema parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "password_hash" TEXT;
-- users.azure_ad_id: added in local identity — EMS schema parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "azure_ad_id" TEXT;
-- users.employee_id: added in local identity — EMS schema parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "employee_id" TEXT;
-- users.department: added in local identity — EMS schema parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "department" TEXT;
-- users.position: added in local identity — EMS schema parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "position" TEXT;
-- users.site: added in local identity — EMS schema parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "site" TEXT;
-- users.phone_number: added in local identity — EMS schema parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "phone_number" TEXT;
-- users.last_login: added in local identity — EMS schema parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "last_login" TIMESTAMPTZ;
-- users.failed_login_attempts: added in local identity — login lockout, EMS 0065 parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "failed_login_attempts" INTEGER;
-- users.locked_until: added in local identity — login lockout, EMS 0065 parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "locked_until" TIMESTAMPTZ;
-- users.last_failed_at: added in local identity — login lockout, EMS 0065 parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "last_failed_at" TIMESTAMPTZ;
-- users.password_changed_at: added in local identity — forced password change, EMS 0065 parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "password_changed_at" TIMESTAMPTZ;
-- users.force_password_change: added in local identity — forced password change, EMS 0065 parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "force_password_change" BOOLEAN;
-- users.session_version: added in local identity — token revocation, EMS 0065 parity (2026-08-21).
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "session_version" INTEGER;
-- chamber_nodes.accepts_web_sessions: added in per-PC mode exclusivity — chamber mode axis (2026-08-16).
ALTER TABLE "chamber_nodes" ADD COLUMN IF NOT EXISTS "accepts_web_sessions" BOOLEAN;
-- chamber_heartbeat_events.progress_json: added in C1 heartbeat-carried progress (2026-06-17).
ALTER TABLE "chamber_heartbeat_events" ADD COLUMN IF NOT EXISTS "progress_json" JSONB;
-- chamber_heartbeat_events.last_error_json: added in M2 chamber diagnostics (2026-06-20).
ALTER TABLE "chamber_heartbeat_events" ADD COLUMN IF NOT EXISTS "last_error_json" JSONB;

-- Materialized views (FE-P0a): coverage read model derived from append-only attempts.

-- Project × provider × condition_hash coverage derived from the latest eligible completed append-only measurement attempt. FE-P0a SSOT for coverage; provider_id is part of the durable identity so one provider cannot satisfy another provider's condition. A regular VIEW would recompute its join on every read across 16k+ test items × N concurrent sessions — that is why this is materialized. The per-partition aggregates are computed once in a GROUP BY derived table joined to the is_latest=true projection. The join is 1:1 (one latest row plus one aggregate row per exact provider partition).
-- refresh policy: see refresh_policies.coverage_by_condition_hash.
CREATE MATERIALIZED VIEW IF NOT EXISTS "coverage_by_condition_hash" AS
    SELECT a.project_id, a.provider_id, a.technology, a.condition_hash, a.session_id AS latest_session_id, a.operator AS latest_operator, a.measured_at AS latest_measured_at, a.verdict AS latest_verdict, a.attempt_number AS latest_attempt_number, agg.attempt_count, agg.distinct_session_count, agg.distinct_operator_count FROM measurement_attempts a JOIN (SELECT project_id, technology, condition_hash, provider_id, COUNT(*) AS attempt_count, COUNT(DISTINCT session_id) AS distinct_session_count, COUNT(DISTINCT operator) AS distinct_operator_count FROM measurement_attempts WHERE status = 'completed' GROUP BY project_id, technology, condition_hash, provider_id) agg ON (agg.project_id IS NOT DISTINCT FROM a.project_id) AND agg.provider_id = a.provider_id AND agg.technology = a.technology AND agg.condition_hash = a.condition_hash WHERE a.is_latest = true AND a.status = 'completed';

CREATE UNIQUE INDEX IF NOT EXISTS "ux_coverage_by_condition_hash" ON "coverage_by_condition_hash" ("project_id", "provider_id", "technology", "condition_hash");
CREATE INDEX IF NOT EXISTS "idx_coverage_by_condition_hash_operator" ON "coverage_by_condition_hash" ("latest_operator");
CREATE INDEX IF NOT EXISTS "idx_coverage_by_condition_hash_measured" ON "coverage_by_condition_hash" ("latest_measured_at");

-- Plain views (FE-P0a): active claim projection over append-only claim_events.

-- Latest acquired claim per (project_id, condition_hash) where there is no later release/expire event for the same claim_id. FE-P3 reads this view to render lock/warning UX. Plain VIEW (not materialized) — claim throughput << measurement throughput and freshness must be sub-second. Uses ROW_NUMBER() OVER + outer alias 'acquired' so the NOT EXISTS subquery unambiguously self-references the outer claim_events row (avoids PG correlated-subquery alias collision) and runs on PostgreSQL + SQLite alike.
CREATE OR REPLACE VIEW "active_claims" AS
    SELECT ranked.project_id, ranked.claim_id, ranked.technology, ranked.condition_hash, ranked.operator, ranked.occurred_at, ranked.expires_at, ranked.session_id FROM (SELECT acquired.project_id, acquired.claim_id, acquired.technology, acquired.condition_hash, acquired.operator, acquired.occurred_at, acquired.expires_at, acquired.session_id, ROW_NUMBER() OVER (PARTITION BY acquired.project_id, acquired.condition_hash ORDER BY acquired.occurred_at DESC) AS rn FROM claim_events acquired WHERE acquired.action = 'acquired' AND NOT EXISTS (SELECT 1 FROM claim_events later WHERE later.claim_id = acquired.claim_id AND later.occurred_at > acquired.occurred_at AND later.action IN ('released', 'expired'))) ranked WHERE ranked.rn = 1;

-- Effective platform permissions per (project_id, user_issuer, user_subject) derived from project_membership joined through the role → permission grant graph (roles → role_permissions → permissions). FE-P8 SSOT for membership-based authorization — PlatformApiAdapter.authorize unions the principal's token permissions with rows from this view to decide allow/deny. The view exposes users.enabled and expires_at verbatim; the application service filters disabled users and expired rows against an injected clock so authorization tests stay deterministic (no DB-side now()). Single-pass read: one SELECT per (project, issuer, user) authz check — no N+1 even at 1000s of memberships.
CREATE OR REPLACE VIEW "project_member_permissions" AS
    SELECT pm.project_id, u.subject AS user_subject, p.permission_key, pm.role_key, pm.assigned_at, pm.expires_at, u.issuer AS user_issuer, u.enabled AS user_enabled FROM project_membership pm JOIN users u ON u.id = pm.user_id JOIN roles r ON r.role_key = pm.role_key JOIN role_permissions rp ON rp.role_id = r.id JOIN permissions p ON p.id = rp.permission_id;

-- Latest heartbeat per registered chamber joined to the chamber_nodes registry (멀티챔버 P1 + C1 heartbeat-carried progress + M2 diagnostics). Exposes reported_status + last_heartbeat_at + heartbeat_ttl_seconds + progress_json + last_error_json VERBATIM — OFFLINE is NOT computed here (no DB-side now()/CURRENT_TIMESTAMP); the read service derives OFFLINE against an injected clock via domain.models.chamber_node.derive_chamber_status (mirrors active_claims / project_member_permissions expires_at injected-clock handling) so availability tests stay deterministic. The same injected clock derives unavailable_reason (heartbeat_timeout/disabled/never_seen/unknown) via derive_unavailable_reason — an operator-diagnostic overlay orthogonal to status (a disabled-but-heartbeating chamber keeps status=idle with reason=disabled). progress_json is the latest heartbeat's measurement-progress snapshot (in_use only) exposed verbatim so a single availability read carries every chamber's live progress (per-chamber progress-poll N+1 eliminated); the read service parses + gates its exposure on the derived in_use status. last_error_json is the latest heartbeat's redacted error payload exposed verbatim so the same single read carries every chamber's last error (no per-chamber error lookup). LEFT JOIN so a registered chamber with zero heartbeats still appears (last_heartbeat_at NULL → service derives OFFLINE / never_seen). Plain VIEW (not materialized) — chamber count is small and freshness must be sub-second. ROW_NUMBER() OVER runs on PostgreSQL + SQLite alike. ⚠️ Column order is APPEND-ONLY. PostgreSQL CREATE OR REPLACE VIEW cannot insert or reorder columns of an existing view — measured on PostgreSQL 16.14: inserting a column in the middle fails with 'cannot change name of view column', appending at the end succeeds. A new column therefore goes LAST even when it reads oddly next to its table siblings, or the incremental migration breaks every already-deployed central DB.
CREATE OR REPLACE VIEW "chamber_availability" AS
    SELECT n.chamber_id, n.name, n.base_url, n.enabled, n.heartbeat_ttl_seconds, latest.reported_status, latest.last_heartbeat_at, latest.heartbeat_expires_at, latest.session_id, latest.progress_json, latest.last_error_json, n.accepts_web_sessions FROM chamber_nodes n LEFT JOIN (SELECT ranked.chamber_id, ranked.reported_status, ranked.occurred_at AS last_heartbeat_at, ranked.expires_at AS heartbeat_expires_at, ranked.session_id, ranked.progress_json, ranked.last_error_json FROM (SELECT h.chamber_id, h.reported_status, h.occurred_at, h.expires_at, h.session_id, h.progress_json, h.last_error_json, ROW_NUMBER() OVER (PARTITION BY h.chamber_id ORDER BY h.occurred_at DESC) AS rn FROM chamber_heartbeat_events h) ranked WHERE ranked.rn = 1) latest ON latest.chamber_id = n.chamber_id;

-- Refresh policies (FE-P0a). These are operational contracts implemented by
-- the ingestion writer (FE-P0c) and a cron fallback; not Postgres objects.
-- Declaring them here resolves the open question at ADR-0005:158-162.

-- refresh_policies.coverage_by_condition_hash:
--   trigger: on_ingest
--   fallback_interval: PT1H
--   concurrent_refresh: True
--   rationale: ADR-0005:158-162 미결 해소. FE-P0c ingestion writer가 attempts를 적재한 직후 `REFRESH MATERIALIZED VIEW
--             CONCURRENTLY coverage_by_condition_hash`를 호출하여 사용자가 보는 coverage 화면(FE-P2)이 항상 최신 attempt 기준. PT1H
--             fallback은 ingest 신호가 누락된 경우의 안전망 — 16k+ test items × 다중 세션에 일반 VIEW 매 read 재계산은 background budget
--             3-5×를 초과한다.

-- Ingestion contract (FE-P0a). Atomicity/ordering rules the FE-P0c
-- writer MUST satisfy. Documentation contract; enforced by
-- tests/test_platform_central_db_schema_contract.py::TestFeP0aIngestionContract
-- and by the application-level transaction in FE-P0c.

-- Atomicity and ordering rules that FE-P0c ingestion writer MUST satisfy when writing into
-- measurement_attempts/measurement_results. The schema contract owns the rules; the writer owns the
-- implementation. Contract test TestFeP0aIngestionContract verifies the rules are present and that the
-- indexes/columns required to enforce them exist.

-- rule attempts_insert_and_is_latest_toggle_atomic:
--   For a given (project_id, provider_id, condition_hash) partition the ingestion writer MUST execute
--   the attempt write and latest repair as one SQL transaction. The serialized plan and legacy callers
--   continue to expose the three-part (session_id, condition_hash, attempt_number) replay key; the
--   worker upgrades that key to the provider-qualified storage identity before writing. The database
--   ranks eligible completed attempts by measured_at DESC NULLS LAST, created_at DESC, id DESC and sets
--   exactly that row is_latest=true, so attempt_number is never a cross-session recency key. The
--   transaction MUST use SERIALIZABLE isolation OR SELECT ... FOR UPDATE on the prior is_latest=true row
--   so concurrent ingestions do not produce two is_latest=true rows for the same provider partition.
--   enforced_by:
--     - ux_measurement_attempts_session_condition_attempt UNIQUE (session_id, condition_hash,
--       attempt_number) — retained for legacy replay compatibility
--     - ux_measurement_attempts_provider_session_condition_attempt UNIQUE (provider_id, session_id,
--       condition_hash, attempt_number) — provider-qualified worker storage identity
--     - idx_measurement_attempts_project_provider_condition_recency — measured_at DESC NULLS LAST,
--       created_at DESC, id DESC candidate order
--     - FE-P0c ingestion writer transaction body

-- rule measurement_results_is_projection_of_latest_attempt:
--   measurement_results.(verdict, result_json, operator, measured_at, condition_hash) MUST equal the
--   database-ranked completed measurement_attempts row that has is_latest=true for the same
--   provider-scoped result. The ingestion writer updates measurement_results within the SAME transaction
--   that inserts the attempt — never as a delayed projection — and an out-of-order or replayed row MUST
--   NOT overwrite a newer projection.
--   enforced_by:
--     - Same-transaction write contract in FE-P0c
--     - TestFeP0aViewSqlSemantics fixtures (semantic equivalence)

-- rule claim_events_append_only_and_acquire_release_pairing:
--   claim_events MUST be INSERT only. A 'released' or 'expired' row MUST reference a claim_id that
--   previously had an 'acquired' row; the writer SHOULD reject mismatched release in application code.
--   The DB MUST NOT enforce pairing as a CHECK constraint — pairing is a temporal property and rejecting
--   writes here would block legitimate expiry sweepers.
--   enforced_by:
--     - claim_events.action allowed_values: acquired/released/expired
--     - FE-P3 application validator (out of scope for FE-P0a)

-- rule coverage_refresh_within_same_unit_of_work:
--   After the attempt INSERT transaction commits, FE-P0c MUST enqueue (or directly call) REFRESH
--   MATERIALIZED VIEW CONCURRENTLY coverage_by_condition_hash. Refresh failure MUST be logged and
--   retried by the PT1H fallback cron; the ingestion transaction MUST NOT be rolled back because of
--   refresh failure (measurement fact is durable).
--   enforced_by:
--     - refresh_policies.coverage_by_condition_hash
--     - FE-P0c ingestion writer post-commit hook

-- rule condition_hash_is_propagated_never_recomputed:
--   central writers MUST take condition_hash verbatim from the local payload (which itself comes from
--   src/domain/services/measurement_condition_hash::compute_condition_hash). Central MUST NOT recompute
--   the hash from condition_json or any other column — drift between the local hash and a central
--   recompute would silently break dedup.
--   enforced_by:
--     - local_central_field_mappings.tables.measurement_attempts.mappings
--     - TestFeP0aLocalCentralFieldMapping

-- Local SQLite ↔ central PostgreSQL field mappings (FE-P0a, Codex 2차 drift 위험 해소).
-- Not a Postgres object — documentation contract enforced by
-- tests/test_platform_central_db_schema_contract.py::TestFeP0aLocalCentralFieldMapping.

-- Local SQLite ↔ central PostgreSQL field correspondence. Drift between models.py and this schema is a
-- contract failure. condition_hash is propagated from
-- src/domain/services/measurement_condition_hash::compute_condition_hash — central never recomputes.

-- measurement_attempts: src/infrastructure/database/models.py:410-470 (MeasurementAttempt) -> measurement_attempts
--   id (Integer autoincrement) -> id (uuid)
--     note: Central uses uuid for cross-station uniqueness. Local integer is mapped at ingestion (FE-P0c) via
--           deterministic uuid5 or freshly generated uuid4 stored alongside.
--   condition_id (FK measurement_conditions.id) -> condition_hash (text)
--     note: Local FK resolves to compute_condition_hash on ingestion. Central never recomputes — propagate
--           verbatim.
--   session_id (FK test_sessions.id) -> session_id (uuid)
--     note: Local integer session id ↔ central uuid session id resolved by ingestion writer (FE-P0c).
--   test_result_id (FK test_results.id) -> measurement_result_id (uuid)
--     note: Nullable — attempt may precede projection cache write.
--   attempt_number -> attempt_number
--     note: Verbatim integer.
--   result1/result2/result_sum + units -> result_json
--     note: Local separate columns are normalized into one JSON envelope. Unit metadata (P0-1 migration 013)
--           preserved verbatim.
--   margin -> margin
--     note: Local Float vs central text — central preserves provider-supplied formatting for byte-identical
--           reproduction.
--   pass_fail -> verdict
--     note: Verbatim PASS/FAIL/NA token.
--   status -> status
--     note: Verbatim string ('completed' default — server_default mirrors local).
--   project_id (text) -> project_id (uuid FK projects.id)
--     note: Local text project id resolved to central uuid via project_code lookup at ingestion.
--   run_id -> run_id
--     note: Verbatim.
--   idempotency_key -> idempotency_key
--     note: Verbatim unique key — drives ingestion dedup.
--   recorded_by (FE-P0b adds) -> operator AND recorded_by
--     note: FE-P0b will add recorded_by to the local model. Operator is the ApiPrincipal.subject (remote) or
--           LOCAL_GUI_OPERATOR_SUBJECT module constant (GUI default); recorded_by preserves the raw provenance
--           string. Both populated from the same source at ingestion.
--   measured_at -> measured_at
--     note: Verbatim UTC timestamp.
--   metadata_json -> provenance_json
--     note: Local opaque metadata is preserved under provenance_json so audit can reconstruct the local payload
--           exactly.

-- measurement_results: src/infrastructure/database/models.py (TestResult — latest projection) -> measurement_results
--   TestResult.row_order + session_id -> (session_id, condition_hash) plus provider_result_id
--     note: Local destructive overwrite is by MATCH_DEFAULT 10 columns. Central uses condition_hash (propagated)
--           + provider_result_id for unique identification.
--   TestResult project_id (text) -> project_id (uuid)
--     note: Resolved by ingestion writer (FE-P0c). Adds direct FK so coverage view does not need a join through
--           test_sessions.
--   (derived from MeasurementAttempt.recorded_by) -> operator
--     note: Materialized into measurement_results as a projection of the latest attempt's operator — FE-P0c
--           populates.

-- RBAC role grants (FE-P8). Idempotent seed INSERTs derived from
-- rbac_role_grants in central_db_schema.v1.json. The role catalog is data,
-- not code — every role/permission consumed by project_member_permissions
-- and PlatformApiAdapter.authorize flows from this single declaration.

-- Project-scoped role → platform permission grants (FE-P8). The single source of truth for the role
-- catalog used by project_membership. The DDL exporter renders this section as idempotent seed INSERTs
-- into the roles/permissions/role_permissions tables (ON CONFLICT DO NOTHING on natural keys,
-- gen_random_uuid() for surrogate ids), and the project_member_permissions view joins through that
-- grant graph to resolve effective permissions per (project, user). The application loads
-- PROJECT_ROLE_KEYS / permissions_for(role_key) from this section verbatim — no role/permission grant
-- lives in code. permission_descriptions mirrors PlatformApiAdapter security-scheme docs so the API
-- contract + RBAC schema cannot drift.

-- permissions
INSERT INTO "permissions" ("id", "permission_key", "description") VALUES (gen_random_uuid(), 'platform:admin', 'Assign / revoke project_membership roles. Audited via audit_events (membership.assigned / membership.revoked).') ON CONFLICT ("permission_key") DO NOTHING;
INSERT INTO "permissions" ("id", "permission_key", "description") VALUES (gen_random_uuid(), 'platform:chamber-config-write', 'Set a chamber''s configuration — the instrument connection settings (analyzer / BT tester / switchbox GPIB and LAN addresses, PATCH /platform/chambers/{id}/equipment-config) AND where that chamber''s plots are stored (PATCH /platform/chambers/{id}/storage-root). ONE token, because the actor and the scope are the same for both: a pair that is always granted together is just one token with an extra drift surface, which is the reasoning 016 used to refuse splitting authoring from publishing. Granted to project_engineer (=시험원) + project_admin: the person who knows the analyzer''s new address after a re-cabling is the tester standing in the room, not an administrator, and operator decision 2026-08-10 put every test-related right with the tester. Deliberately NOT platform:reference-write — that token''s membership path only opens for PROJECT-scoped families, and a chamber is not a project (a room outlives every project and one project spans two rooms). Deliberately NOT platform:admin — the storage-root axis used that tier until 2026-08-11 and it is exactly what the operator decision moved.') ON CONFLICT ("permission_key") DO NOTHING;
INSERT INTO "permissions" ("id", "permission_key", "description") VALUES (gen_random_uuid(), 'platform:claim', 'Acquire / release measurement claims on the central claim_events append-only ledger.') ON CONFLICT ("permission_key") DO NOTHING;
INSERT INTO "permissions" ("id", "permission_key", "description") VALUES (gen_random_uuid(), 'platform:read', 'Read project coverage / active claims / sync status / membership list from the central read model.') ON CONFLICT ("permission_key") DO NOTHING;
INSERT INTO "permissions" ("id", "permission_key", "description") VALUES (gen_random_uuid(), 'platform:reference-write', 'Author a reference-catalog candidate revision and publish it (POST /platform/providers/{id}/reference-revisions and .../publish). Publishing changes what every chamber in that scope measures with from its NEXT session. Granted to project_engineer (=시험원) + project_admin.') ON CONFLICT ("permission_key") DO NOTHING;
INSERT INTO "permissions" ("id", "permission_key", "description") VALUES (gen_random_uuid(), 'platform:sample-hard-delete', 'Global system_admin-only physical deletion of a sample current projection, intakes, and revisions; leaves a PII-free audit tombstone.') ON CONFLICT ("permission_key") DO NOTHING;
INSERT INTO "permissions" ("id", "permission_key", "description") VALUES (gen_random_uuid(), 'platform:sample-write', 'Create and edit all web sample and append-only intake fields; status changes and ordinary delete also write sample revisions.') ON CONFLICT ("permission_key") DO NOTHING;
INSERT INTO "permissions" ("id", "permission_key", "description") VALUES (gen_random_uuid(), 'test_plan:read', 'Read and validate test-plan drafts and published plans without mutating test-plan state. Granted to project_admin for project administration''s read surface; test-plan authoring remains separately gated by test_plan:author.') ON CONFLICT ("permission_key") DO NOTHING;

-- roles
INSERT INTO "roles" ("id", "role_key", "description") VALUES (gen_random_uuid(), 'project_admin', 'Manage project membership (assign/revoke roles) — every change audited via audit_events. Also edits every web sample and reference-catalog revision.') ON CONFLICT ("role_key") DO NOTHING;
INSERT INTO "roles" ("id", "role_key", "description") VALUES (gen_random_uuid(), 'project_engineer', 'Acquire and release measurement claims on the project''s central claim ledger; edit every web sample and intake field; author and publish reference-catalog revisions.') ON CONFLICT ("role_key") DO NOTHING;
INSERT INTO "roles" ("id", "role_key", "description") VALUES (gen_random_uuid(), 'project_pm', 'PM (시료 물류/자산) role: create and edit every web sample field, including intake history. Reads project state; does not run measurements.') ON CONFLICT ("role_key") DO NOTHING;
INSERT INTO "roles" ("id", "role_key", "description") VALUES (gen_random_uuid(), 'project_viewer', 'Read-only access to project coverage / active claims / sync status.') ON CONFLICT ("role_key") DO NOTHING;
INSERT INTO "roles" ("id", "role_key", "description") VALUES (gen_random_uuid(), 'system_admin', 'Global platform operator. May physically delete sample operational rows only through the dedicated hard-delete operation.') ON CONFLICT ("role_key") DO NOTHING;

-- role_permissions (role_id, permission_id resolved by natural-key JOIN)
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:admin' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:chamber-config-write' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:claim' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:read' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:reference-write' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:sample-write' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'test_plan:read' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_engineer' AND p."permission_key" = 'platform:chamber-config-write' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_engineer' AND p."permission_key" = 'platform:claim' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_engineer' AND p."permission_key" = 'platform:read' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_engineer' AND p."permission_key" = 'platform:reference-write' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_engineer' AND p."permission_key" = 'platform:sample-write' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_pm' AND p."permission_key" = 'platform:read' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_pm' AND p."permission_key" = 'platform:sample-write' ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'project_viewer' AND p."permission_key" = 'platform:read' ON CONFLICT DO NOTHING;

-- global_role_grants (global system roles, separate from project membership)
INSERT INTO "global_role_grants" ("role_key", "permission_key") VALUES ('system_admin', 'platform:sample-hard-delete') ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id") SELECT r."id", p."id" FROM "roles" r, "permissions" p WHERE r."role_key" = 'system_admin' AND p."permission_key" = 'platform:sample-hard-delete' ON CONFLICT DO NOTHING;
