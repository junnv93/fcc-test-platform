/**
 * Cross-route deep-link SSOT (멀티챔버 P12, 2026-06-16).
 *
 * When one route links into another (e.g. the chamber fleet overview → a
 * chamber's session history), the target path + query param must live in one
 * place so a route rename does not silently break the link. The app-shell
 * navigation composition lives in `shared/app-shell-navigation.ts`; this
 * module owns the canonical route values it and programmatic deep-links share.
 *
 * The session deep-link reuses the `parsePositiveId` SSOT to validate the id
 * before building an href — the `/sessions` route only accepts a positive
 * integer session id (`?session=`), so a non-numeric chamber `session_id`
 * (forward-compat / node-local opaque id) yields `null` and the caller renders
 * plain text instead of a link that would dead-end on the lookup form.
 */
import { parsePositiveId } from './numeric-id';
import { isValidProjectId } from './project-id';

/** Canonical route values shared by app-shell navigation and programmatic
 *  deep-links. The router still owns its relative matching declarations; this
 *  map keeps every cross-module path consumer on one named value. A key must
 *  have a real consumer, otherwise it becomes dead SSOT. */
export const ROUTE_PATHS = {
  // 프로젝트(모델) 선택·생성 진입층. "먼저 프로젝트를 고르세요" 안내의 목적지라
  // 프로젝트 컨텍스트가 없는 화면이 프로그램적으로 링크한다. Consumed by
  // `routes/test-reports.tsx`.
  myProjects: '/my-projects',
  fields: '/fields',
  projects: '/projects',
  jobs: '/jobs',
  sessions: '/sessions',
  progress: '/progress',
  reports: '/reports',
  // Phase G 성적서 대장 — 중앙 `test_reports` 인스턴스(edition 단위). `/reports`
  // (headless 리포트 생성 요청 큐)와는 별개 도메인이다. Consumed by
  // `my-projects.tsx` (프로젝트 카드의 다음 행동 링크).
  testReports: '/test-reports',
  // 성적서 §6 장비목록 — 프로젝트가 실제로 사용한 계측기/시험용 소프트웨어.
  // EMS 가 표준 리스트의 SSOT 이고 여기는 실사용 기록이다. Consumed by
  // Consumed by `app-shell-navigation.ts`(전역 nav). 화면 자신은 `/test-reports`
  // 로 나가는 링크를 갖지만 그 반대 방향은 아직 없다 — 상호 이동은 후속.
  equipmentLists: '/equipment-lists',
  referenceData: '/reference-data',
  // plot-custody ① 플롯 보관 현황 — 심사 증거(플롯) 원본이 보관소에 실재하는가.
  // 판정은 챔버 노드가 내리고 중앙은 받아 보관한다. Consumed by
  // `app-shell-navigation.ts`(전역 nav).
  artifactCustody: '/artifact-custody',
  testPlans: '/test-plans',
  diagnostics: '/diagnostics',
  inventory: '/inventory',
  membership: '/membership',
  providers: '/providers',
  control: '/control',
  // ③ 측정 돌리기 — the measurement screen a failed result re-measures into
  // (§5⑤ "재측정"→③). `/control` is central-hidden, so the operator-facing
  // measurement entry point is the chamber fleet. Consumed by `sessions.tsx`.
  chambers: '/chambers',
} as const;

/** The query param every project-scoped screen reads the selected project from.
 *  Paired with {@link projectScopedHref} so the deep-link producer and the route
 *  readers cannot drift on the param name. */
export const PROJECT_QUERY_PARAM = 'project';

/**
 * Build a project-scoped deep link (`/projects?project=<id>`), or the bare path
 * when `projectId` is not a project id the target route can resolve — a
 * `?project=` carrying a malformed value would dead-end on the target's lookup.
 *
 * Introduced as the SSOT for a shape that five routes each re-declared locally;
 * inventory and the other project screens now consume the same project query.
 *
 * fe-honesty-debt M1 (2026-07-31): the five incumbents were migrated here —
 * `routes/projects.tsx`, `routes/my-projects.tsx`, `routes/inventory/index.tsx`,
 * `routes/test-plans/TestPlansWorkbench.tsx`, and `routes/progress.tsx` (whose
 * copy was named `projectHref`, so a name-based grep never saw it — the seal
 * `TestProjectScopedHrefIsSingleSsot` scans the *shape* for that reason). Two of
 * the copies (`my-projects`, `progress`) had NO id gate; adopting the SSOT means
 * they gain one. `progress` gates its whole subtree on `isValidProjectId`
 * already, so that is unreachable-branch-only; on `my-projects` it is a real
 * (and intended) correction — a card whose server-sent `project_id` is not a
 * uuid now links to the bare path instead of a `?project=` the target route's
 * lookup would reject.
 */
export function projectScopedHref(path: string, projectId: string): string {
  return isValidProjectId(projectId)
    ? `${path}?${PROJECT_QUERY_PARAM}=${encodeURIComponent(projectId)}`
    : path;
}

/** The `/sessions` route reads the selected session from this URL query param.
 *  Consumed by `sessions.tsx` (`searchParams.get`/`updateParam`) AND by
 *  {@link sessionHistoryHref} below, so the deep-link producer and the route
 *  reader cannot drift on the param name. */
export const SESSIONS_SESSION_PARAM = 'session';

/**
 * Build a deep-link to a session's attempt/result history, or `null` when
 * `sessionId` is not a positive-integer id the `/sessions` route can resolve.
 * Returning `null` (not a broken href) lets the caller fall back to plain text.
 */
export function sessionHistoryHref(sessionId: string | null | undefined): string | null {
  if (sessionId === null || sessionId === undefined) return null;
  const id = parsePositiveId(sessionId);
  if (id === null) return null;
  return `${ROUTE_PATHS.sessions}?${SESSIONS_SESSION_PARAM}=${id}`;
}
