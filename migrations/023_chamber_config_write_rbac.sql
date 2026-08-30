-- 023_chamber_config_write_rbac.sql
-- Wave C (2026-08-11) — one chamber-attribute write token.
--
-- WHY
--   Operator decision 2026-08-10 (CLAUDE.md Deployment Policy §6): *"시험과 관련된
--   부분들은 당연히 시험원한테 권리가 모두 있어야 해."* Two chamber attributes were
--   on different tiers — instrument addresses on platform:equipment-write (landed
--   2026-08-10) and the plot storage root still on platform:admin. Both are
--   properties of the room, both are test-related, both are written by the tester.
--
--   A pair of tokens with the SAME actor and the SAME scope is just one token with
--   an extra drift surface. 016 refused to split authoring from publishing on
--   exactly that reasoning; this file applies it to the chamber axis.
--
-- WHY NOW
--   platform:equipment-write landed on 2026-08-10 and the web is NOT deployed, so
--   no realm anywhere holds a real grant for it. Renaming later would mean an
--   identity-provider migration and coordination; today it is a text change.
--
-- WHAT HAPPENS TO THE OLD TOKEN
--   Nothing, deliberately. tests/test_rbac_parity.py forbids DELETE FROM in any
--   migration that seeds permissions, and 016 set the precedent when it moved
--   reference writing off platform:admin without deleting anything. The old rows
--   are inert: no API operation requires platform:equipment-write any more, so the
--   grant grants nothing. Removing them would be a destructive migration bought
--   with no safety.
--
-- DIALECT: PostgreSQL, like 001..022.

BEGIN;

INSERT INTO "permissions" ("id", "permission_key", "description")
  VALUES (
    gen_random_uuid(),
    'platform:chamber-config-write',
    'Set a chamber''s configuration — instrument connection settings (analyzer / BT tester / switchbox GPIB and LAN addresses) AND where that chamber''s plots are stored. ONE token because the actor (the tester standing in the room) and the scope (the chamber) are the same for both.'
  )
  ON CONFLICT ("permission_key") DO NOTHING;

INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_engineer'
    AND p."permission_key" = 'platform:chamber-config-write'
  ON CONFLICT DO NOTHING;

INSERT INTO "role_permissions" ("role_id", "permission_id")
  SELECT r."id", p."id" FROM "roles" r, "permissions" p
  WHERE r."role_key" = 'project_admin'
    AND p."permission_key" = 'platform:chamber-config-write'
  ON CONFLICT DO NOTHING;

COMMIT;
