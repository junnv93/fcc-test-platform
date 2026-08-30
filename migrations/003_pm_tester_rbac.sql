-- 003_pm_tester_rbac.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs (Phase D — PM·시험원 RBAC).
--
-- 001_initial_central_db.sql seeds roles/permissions/role_permissions only on
-- FIRST boot. An existing DB never picks up the Phase D additions (project_pm
-- role + the two sample-write permissions + the new role_permission grants) nor
-- the project_membership.team column. This migration applies them additively and
-- idempotently (safe to re-run; every INSERT is ON CONFLICT DO NOTHING).
--
-- Source of truth is rbac_role_grants in docs/platform/central_db_schema.v1.json
-- (the DDL exporter renders the SAME seed INSERTs into 001). Sealed by
-- tests/test_sample_inventory_rbac.py.

-- New grantable permissions (PM 칸 write vs 시험원 입고 칸 write).
INSERT INTO "permissions" ("id", "permission_key", "description")
  VALUES (gen_random_uuid(), 'platform:sample-pm-write', 'Write sample PM 칸 (label_number/serial_number/assigned_team/물류일자) via the PM-status Excel import (POST /platform/projects/{id}/sample-inventory/pm-imports).')
  ON CONFLICT ("permission_key") DO NOTHING;
INSERT INTO "permissions" ("id", "permission_key", "description")
  VALUES (gen_random_uuid(), 'platform:sample-intake-write', 'Append sample intake history (시험원 칸 — bl/ap/cp/csc/rf_cal/hw_rev) via the RF-data Excel import (POST /platform/projects/{id}/sample-inventory/rf-imports).')
  ON CONFLICT ("permission_key") DO NOTHING;

-- New PM role (logistics/asset branch — read + PM-칸 write).
INSERT INTO "roles" ("id", "role_key", "description")
  VALUES (gen_random_uuid(), 'project_pm', 'PM (시료 물류/자산) role: write sample PM 칸 (label/serial/team/물류일자) via the PM-status import (Phase D). Reads project state; does not run measurements.')
  ON CONFLICT ("role_key") DO NOTHING;

-- New role → permission grants (engineer gains intake-write; admin gains both
-- sample writes; pm gets read + pm-write).
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_engineer' AND p."permission_key" = 'platform:sample-intake-write'
  ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:sample-pm-write'
  ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:sample-intake-write'
  ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_pm' AND p."permission_key" = 'platform:read'
  ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_pm' AND p."permission_key" = 'platform:sample-pm-write'
  ON CONFLICT DO NOTHING;

-- Phase D — 시험원 하위 team(RF/SAR) 분류 축. Power-orthogonal attribution/filter
-- attribute on the membership (set at role-assign time). Nullable (legacy rows).
ALTER TABLE "project_membership" ADD COLUMN IF NOT EXISTS "team" TEXT;
