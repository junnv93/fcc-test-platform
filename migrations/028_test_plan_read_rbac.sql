-- 028_test_plan_read_rbac.sql
--
-- The headless test-plan read operations already require test_plan:read.  The
-- central RBAC catalog did not contain that token, so existing databases could
-- never resolve the Local Auth project_admin grant even though the OIDC realm
-- and frontend token mirror already declared it.  Keep this migration additive
-- and idempotent: 001 remains the generated seed for new databases, while this
-- file converges databases that have already run 001.

INSERT INTO "permissions" ("id", "permission_key", "description")
VALUES (
  gen_random_uuid(),
  'test_plan:read',
  'Read and validate test-plan drafts and published plans without mutating test-plan state. Granted to project_admin for project administration''s read surface; test-plan authoring remains separately gated by test_plan:author.'
)
ON CONFLICT ("permission_key") DO NOTHING;

INSERT INTO "role_permissions" ("role_id", "permission_id")
SELECT r."id", p."id"
FROM "roles" r
JOIN "permissions" p ON p."permission_key" = 'test_plan:read'
WHERE r."role_key" = 'project_admin'
ON CONFLICT DO NOTHING;
