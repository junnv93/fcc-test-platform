/**
 * Frontend mirror of the backend permission-token universe (B2 RBAC parity).
 *
 * SSOT authority is the BACKEND — this module is only the mirror:
 *   - `rbac_role_grants` (docs/platform/central_db_schema.v1.json) — the
 *     project-scoped role → permission grant graph (project_viewer / engineer /
 *     admin → platform:read / claim / admin).
 *   - `PLATFORM_API_PERMISSIONS` (src/application/central_contract/api_contracts.py) —
 *     per-operation gate for the central platform read/write API.
 *   - `HEADLESS_API_PERMISSIONS`
 *     (src/application/headless/api_contract_constants.py) — per-operation gate
 *     for the headless provider API the web app proxies.
 *   - `SESSION_API_PERMISSIONS` (src/application/session/api_contracts.py) —
 *     per-operation gate for the live measurement-session API (read / control /
 *     events).
 *
 * `tests/test_rbac_parity.py` asserts the union of those backend sources
 * is SET-EQUAL to the constants declared here (`export const PERMISSION_… =
 * '<token>'`), so a permission added or removed on either side without a
 * matching update on the other fails the build (mirror of the equipment
 * repo's `check-role-config-sync.mjs` set-equality pre-push gate).
 *
 * Excluded from the mirror: the headless `'public'` sentinel. It denotes
 * "no authorization required" (health/contract discovery + the presigned
 * download stream) — it is NOT a permission a user can hold or be granted
 * (`rbac_role_grants` never lists it), so it is not part of the mirrored
 * permission universe.
 *
 * Route modules MUST import these constants instead of re-declaring the raw
 * literal — the inline `const PERMISSION_… = 'platform:read'` pattern scattered
 * across routes is the drift this SSOT removes.
 */

// Platform read/write API (central read model + claim ledger + membership).
export const PERMISSION_PLATFORM_READ = 'platform:read';
export const PERMISSION_PLATFORM_CLAIM = 'platform:claim';
export const PERMISSION_PLATFORM_ADMIN = 'platform:admin';
// Node-scoped chamber heartbeat token (멀티챔버 P2). A chamber PC self-reports
// idle/in_use with this machine token; it is NOT a project-membership grant
// (never listed in rbac_role_grants) and the web operator never holds it — it is
// mirrored here only to keep the backend↔frontend permission universe set-equal.
export const PERMISSION_PLATFORM_CHAMBER = 'platform:chamber';
// Web sample inventory CRUD. PM and engineer share the complete field editor;
// physical deletion is a separate global system-admin operation.
export const PERMISSION_PLATFORM_SAMPLE_WRITE = 'platform:sample-write';
export const PERMISSION_PLATFORM_SAMPLE_HARD_DELETE = 'platform:sample-hard-delete';
// 참조 데이터 소유권 이전 (2026-08-08) — 참조 카탈로그 후보 저작 + 게시. 저작과
// 게시를 **한 토큰**이 함께 게이트하는 이유는 행위자가 하나이기 때문이다: 재배선
// 뒤 케이블 손실을 다시 재는 사람과 그 값을 올리는 사람이 같은 시험원이다.
// project_engineer + project_admin 이 보유(rbac_role_grants SSOT).
export const PERMISSION_PLATFORM_REFERENCE_WRITE = 'platform:reference-write';
// 챔버 **속성** 쓰기 (2026-08-11) — 계측기 연결 설정(분석기/BT 테스터/스위치박스의
// GPIB·LAN 주소)과 플롯 저장 위치. **한 토큰**인 이유는 행위자(그 방에 서 있는
// 시험원)와 스코프(챔버)가 둘 다 같기 때문이다 — 늘 함께 부여되는 토큰 쌍은 drift
// 표면이 하나 더 있는 한 토큰일 뿐이고, 016 이 저작/게시 분리를 같은 논거로 기각했다.
// platform:reference-write 재사용이 아닌 이유는 그 토큰의 멤버십 경로가 PROJECT
// 스코프 패밀리에서만 열리는데 챔버는 프로젝트가 아니기 때문이고(방은 프로젝트보다
// 오래 존속하고 한 프로젝트가 두 방에 걸친다), platform:admin 이 아닌 이유는 운영자
// 판정(2026-08-10)이 시험 관련 권한을 전부 시험원에게 두었기 때문이다.
// project_engineer + project_admin 이 보유(rbac_role_grants SSOT).
export const PERMISSION_PLATFORM_CHAMBER_CONFIG_WRITE = 'platform:chamber-config-write';

// Headless provider API (jobs / results / artifacts).
export const PERMISSION_HEADLESS_READ = 'headless:read';
export const PERMISSION_HEADLESS_CONTROL = 'headless:control';

// Report-automation surface.
export const PERMISSION_REPORT_AUTOMATION_READ = 'report_automation:read';
export const PERMISSION_REPORT_AUTOMATION_CONTROL = 'report_automation:control';

// Test-plan draft authoring surface.
export const PERMISSION_TEST_PLAN_READ = 'test_plan:read';
export const PERMISSION_TEST_PLAN_AUTHOR = 'test_plan:author';

// Live measurement-session API (read / control / events).
export const PERMISSION_SESSION_READ = 'session:read';
export const PERMISSION_SESSION_CONTROL = 'session:control';
export const PERMISSION_SESSION_EVENTS = 'session:events';

/**
 * Read-only catalog of every mirrored permission token. Useful for exhaustive
 * UI affordances (e.g. a permission picker) and keeps the token set in one
 * referenceable place. The parity test parses the `export const` declarations
 * above, not this array, so this stays a derived convenience.
 */
export const PLATFORM_PERMISSIONS = [
  PERMISSION_PLATFORM_READ,
  PERMISSION_PLATFORM_CLAIM,
  PERMISSION_PLATFORM_ADMIN,
  PERMISSION_PLATFORM_CHAMBER,
  PERMISSION_PLATFORM_SAMPLE_WRITE,
  PERMISSION_PLATFORM_SAMPLE_HARD_DELETE,
  PERMISSION_PLATFORM_REFERENCE_WRITE,
  PERMISSION_PLATFORM_CHAMBER_CONFIG_WRITE,
  PERMISSION_HEADLESS_READ,
  PERMISSION_HEADLESS_CONTROL,
  PERMISSION_REPORT_AUTOMATION_READ,
  PERMISSION_REPORT_AUTOMATION_CONTROL,
  PERMISSION_TEST_PLAN_READ,
  PERMISSION_TEST_PLAN_AUTHOR,
  PERMISSION_SESSION_READ,
  PERMISSION_SESSION_CONTROL,
  PERMISSION_SESSION_EVENTS,
] as const;

export type PermissionToken = (typeof PLATFORM_PERMISSIONS)[number];
