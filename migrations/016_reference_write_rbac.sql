-- 016_reference_write_rbac.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs
-- (참조 데이터 소유권 이전 — 시험원 참조-쓰기 권한, 2026-08-08).
--
-- 001_initial_central_db.sql seeds roles/permissions/role_permissions only on
-- FIRST boot, so an existing DB never picks up a permission added later. This
-- migration applies the addition additively and idempotently (every INSERT is
-- ON CONFLICT DO NOTHING; safe to re-run).
--
-- WHY a new grantable token rather than reusing one:
--   * platform:admin (the previous gate) puts reference authoring in the
--     project-management tier. But the person who re-measures a cable loss
--     after a re-cabling and records it is the TESTER, not an administrator.
--     Leaving it on admin means the workbook can never stop being authoritative,
--     because the one person who knows the new number cannot enter it.
--   * platform:claim was REJECTED. It belongs to the measurement claim ledger
--     and project_engineer already holds it, so reusing it would silently make
--     "every tester may publish this room's cable loss" true with no decision
--     recorded anywhere.
--   * ONE token, not an author/publish pair: Phase D split sample writes in two
--     because the actors were two (PM ↔ 시험원). Here the same person does both,
--     and a pair that is always granted together is just one token with an extra
--     drift surface. A 4-eyes axis, if it ever arrives, belongs in the already
--     existing approved_by / approval_reason columns (migration 036 on the
--     chamber side) and ValidationIssueCode.APPROVAL_REQUIRED — not in RBAC.
--
-- Source of truth is rbac_role_grants in docs/platform/central_db_schema.v1.json
-- (the DDL exporter renders the SAME seed INSERTs into 001). Sealed by
-- tests/test_rbac_parity.py + tests/test_reference_write_rbac.py.

-- New grantable permission (reference-catalog authoring + publication).
INSERT INTO "permissions" ("id", "permission_key", "description")
  VALUES (gen_random_uuid(), 'platform:reference-write', 'Author a reference-catalog candidate revision and publish it (POST /platform/providers/{id}/reference-revisions and .../publish). Publishing changes what every chamber in that scope measures with from its NEXT session. Granted to project_engineer (=시험원) + project_admin.')
  ON CONFLICT ("permission_key") DO NOTHING;

-- New role → permission grants. 시험원(project_engineer) is the actor;
-- project_admin holds every lower write, per the Phase D precedent.
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_engineer' AND p."permission_key" = 'platform:reference-write'
  ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:reference-write'
  ON CONFLICT DO NOTHING;
