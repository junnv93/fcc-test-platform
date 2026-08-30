-- 021_equipment_write_rbac.sql
-- Incremental migration for ALREADY-DEPLOYED central DBs
-- (계측기 설정 쓰기 권한 — 시험원이 장비 주소를 직접 고친다, 2026-08-10).
--
-- 001_initial_central_db.sql seeds roles/permissions/role_permissions only on
-- FIRST boot, so an existing DB never picks up a permission added later. This
-- migration applies the addition additively and idempotently (every INSERT is
-- ON CONFLICT DO NOTHING; safe to re-run).
--
-- WHY a new grantable token rather than reusing one:
--   * platform:reference-write was REJECTED, and not on taste. Its membership
--     half only opens for PROJECT-scoped families — PlatformApiAdapter feeds a
--     project_id into the authorization union only when the family's scope kind
--     is PROJECT, and its own docstring records the reason: "a room outlives
--     every project, and one project spans two rooms". The equipment-config
--     route has no project_id at all (/platform/chambers/{chamber_id}/…).
--     Reusing the token would therefore not extend a mechanism; it would make
--     three descriptions false at once (this table, the API permission docs,
--     and 016's body) and would permanently fuse "what this room measures with"
--     to "which box the session talks to", so neither could be narrowed again.
--   * platform:admin was REJECTED by operator decision (2026-08-10): testers
--     edit equipment addresses directly. The storage-root axis (018) sits on
--     admin and the symmetry argument does apply — but the person who reads the
--     analyzer's new address off the instrument after a re-cabling is the
--     tester, and the operator's decision outranks the symmetry. Recording the
--     tension here rather than hiding it.
--   * platform:claim was REJECTED for the same reason 016 rejected it: it
--     belongs to the measurement claim ledger and every engineer already holds
--     it, so reusing it would silently make "every tester may re-point this
--     room's instruments" true with no decision recorded anywhere.
--   * ONE token, not a read/write pair: the operator-facing READ is gated by
--     the existing platform:read, and the node's own read keeps its node-scoped
--     platform:chamber binding. A third token would have no distinct actor.
--
-- FOOTNOTE (so the next session does not re-derive it): the bijection invariant
-- requires every grantable permission to appear in rbac_role_grants, but the
-- chamber-scoped route carries no project_id, so this grant's membership half
-- can never fire — it is realized through the Keycloak group attribute alone.
-- That is not a defect; platform:reference-write already has exactly this shape
-- for room-scoped families.
--
-- Source of truth is rbac_role_grants in docs/platform/central_db_schema.v1.json
-- (the DDL exporter renders the SAME seed INSERTs into 001). Sealed by
-- tests/test_rbac_parity.py + tests/test_chamber_equipment_config_axis.py.

-- New grantable permission (chamber instrument connection settings).
INSERT INTO "permissions" ("id", "permission_key", "description")
  VALUES (gen_random_uuid(), 'platform:equipment-write', 'Set a chamber''s instrument connection settings — the analyzer / BT tester / switchbox GPIB and LAN addresses (PATCH /platform/chambers/{id}/equipment-config). Granted to project_engineer (=시험원) + project_admin: the person who knows the analyzer''s new address after a re-cabling is the tester standing in the room, not an administrator. Deliberately NOT platform:reference-write — that token''s membership path only opens for PROJECT-scoped families, and a chamber is not a project (a room outlives every project and one project spans two rooms). Deliberately NOT platform:admin — that is the tier the storage-root axis uses, and holding to it would mean the tester cannot record the address they just read off the instrument.')
  ON CONFLICT ("permission_key") DO NOTHING;

-- New role → permission grants. 시험원(project_engineer) is the actor;
-- project_admin holds every lower write, per the Phase D / 016 precedent.
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_engineer' AND p."permission_key" = 'platform:equipment-write'
  ON CONFLICT DO NOTHING;
INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_admin' AND p."permission_key" = 'platform:equipment-write'
  ON CONFLICT DO NOTHING;
