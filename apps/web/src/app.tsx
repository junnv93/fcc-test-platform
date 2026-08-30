import { QueryClientProvider } from '@tanstack/react-query';
import { ReactQueryDevtools } from '@tanstack/react-query-devtools';
import { lazy, StrictMode, useEffect, useState } from 'react';
import { createBrowserRouter, RouterProvider, useLocation } from 'react-router-dom';

import { AuthCallbackRoute, RequireAuth } from '@/auth/route-guard';
import { getRuntimeConfig } from '@/config/runtime';
import { setActiveScreen } from '@/observability/web-vitals';
import { AppLayout } from '@/routes/_layout';
import { AppErrorBoundary, RouteErrorPage, ShellErrorPage } from '@/shared/error-boundary';
import { queryClient } from '@/shared/query-client';
import { RouteAnnouncer } from '@/shared/route-announcer';

import type { AppRouteHandle } from '@/shared/route-announcer';
import type { ComponentType } from 'react';
import type { ReactNode } from 'react';
import type { RouteObject } from 'react-router-dom';

/**
 * Pushes the current route's privacy-safe pattern to the web-vitals layer so
 * INP/CLS/LCP can be sliced per logical screen. `setActiveScreen` normalizes the
 * pathname (strips query/hash, masks numeric/uuid ids) — concrete ids never
 * reach telemetry. Wraps the layout without touching _layout.tsx.
 */
function ScreenTracker({ children }: { children: ReactNode }): JSX.Element {
  const location = useLocation();
  useEffect(() => {
    setActiveScreen(location.pathname);
  }, [location.pathname]);
  return <>{children}</>;
}

const OverviewRoute = lazy(() => import('@/routes/overview'));
const DiagnosticsRoute = lazy(() => import('@/routes/diagnostics'));
const ChambersRoute = lazy(() => import('@/routes/chambers'));
const TestPlansRoute = lazy(() => import('@/routes/test-plans'));
const MyProjectsRoute = lazy(() => import('@/routes/my-projects'));
const FieldsRoute = lazy(() => import('@/routes/fields'));
const ProgressRoute = lazy(() => import('@/routes/progress'));
const ProjectsRoute = lazy(() => import('@/routes/projects'));
const MembershipRoute = lazy(() => import('@/routes/membership'));
const InventoryRoute = lazy(() => import('@/routes/inventory'));
const ProvidersRoute = lazy(() => import('@/routes/providers'));
const ControlRoute = lazy(() => import('@/routes/control'));
const ReportsRoute = lazy(() => import('@/routes/reports'));
const TestReportsRoute = lazy(() => import('@/routes/test-reports'));
const EquipmentListsRoute = lazy(() => import('@/routes/equipment-lists'));
const ReferenceDataRoute = lazy(() => import('@/routes/reference-data'));
const ArtifactCustodyRoute = lazy(() => import('@/routes/artifact-custody'));
const SessionsRoute = lazy(() => import('@/routes/sessions'));
const JobsRoute = lazy(() => import('@/routes/jobs'));
const LoginRoute = lazy(() => import('@/routes/login'));
const ChangePasswordRoute = lazy(() => import('@/routes/change-password'));
const NotFoundRoute = lazy(() => import('@/routes/not-found'));

// `AppRouteHandle` (the `titleKey` contract) is declared next to its only
// reader, `@/shared/route-announcer`, and re-exported here so the route
// declaration site still names the type it satisfies.
export type { AppRouteHandle };

interface AppRouteCommon {
  readonly element: JSX.Element;
  /** REQUIRED, not optional — a route that forgets its boundary must fail
   *  `tsc`, not wait for a reviewer to notice (see the three layers below). */
  readonly errorElement: JSX.Element;
  readonly handle: AppRouteHandle;
}

interface AppIndexRoute extends AppRouteCommon {
  readonly index: true;
}

interface AppPathRoute extends AppRouteCommon {
  readonly path: string;
  readonly children?: readonly AppRoute[];
}

export type AppRoute = AppIndexRoute | AppPathRoute;

/**
 * Error boundary layers — three, with different reach. See
 * `shared/error-boundary.tsx` for the full contract.
 *
 * 1. ROUTE layer (`ROUTE_ERROR_ELEMENT`, every child of `/`) — replaces only
 *    the `<Outlet/>` slot, so header / nav / skip-link / hotkeys survive one
 *    broken screen, and react-router clears the error on the next location
 *    change (`RenderErrorBoundary.getDerivedStateFromProps`).
 * 2. SHELL layer (`SHELL_ERROR_ELEMENT`, the `/` shell and `/auth/callback`) —
 *    for failures of the shell itself (auth gate, layout chrome). react-router
 *    already wraps the root match in a boundary whether we ask or not
 *    (`route.errorElement || index === 0`); declaring one here REPLACES its
 *    untranslated, un-instrumented `DefaultErrorComponent` with ours.
 * 3. RESIDUAL layer (`<AppErrorBoundary>` around `<RouterProvider>` in `App`)
 *    — everything outside the router's render tree. It is NOT the shell net;
 *    layer 2 is.
 *
 * The elements are constructed once and shared: a React element is an immutable
 * descriptor, and one instance per layer keeps "which boundary" a single fact.
 */
const ROUTE_ERROR_ELEMENT = <RouteErrorPage />;
const SHELL_ERROR_ELEMENT = <ShellErrorPage />;

const gridPocEnabled = import.meta.env['VITE_GRID_POC'] === '1';
const gridPocModulePath = '/src/routes/grid-poc.tsx';
const GridPocRoute = gridPocEnabled
  ? lazy(
      () =>
        import(/* @vite-ignore */ gridPocModulePath) as Promise<{
          default: ComponentType;
        }>,
    )
  : NotFoundRoute;
const gridPocRoutes: readonly AppRoute[] = gridPocEnabled
  ? [
      {
        // ADR-0008 PoC only — never expose without VITE_GRID_POC=1.
        path: 'grid-poc',
        element: <GridPocRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.layout.nav.gridPoc' },
      },
    ]
  : [];

/**
 * Route registration SSOT.
 *
 * Exported so tests can drive the REAL nesting (real `AppLayout`, real
 * boundaries) instead of a hand-rebuilt approximation that drifts.
 *
 * ⚠️ `path:` and `handle.titleKey` values must stay STRING LITERALS. The
 * conformance seals now derive the registered route set STRUCTURALLY, through
 * the module graph (`tests/support/frontend_route_registry.py`), so the array
 * may live in another module and arrive here through a `...spread` — that IS
 * followed. What cannot be followed is a value that is not a literal (a
 * constant reference, a call), and the walk answers that by RAISING rather than
 * by quietly registering one route less.
 *
 * The note this replaces asked for the same literals for a weaker reason — a
 * regex scan of this file alone — and an independent review used exactly that
 * gap: routes spread in from a second module were missing from BOTH sides of
 * every count equality, so boundary-less, title-less screens registered with
 * the whole lane green.
 */
export const appRoutes: readonly AppRoute[] = [
  // OIDC callback — kept outside <RequireAuth> so the callback can run for
  // unauthenticated tabs (that's the whole point of the redirect).
  {
    path: '/auth/callback',
    element: (
      <RouteAnnouncer>
        <AuthCallbackRoute />
      </RouteAnnouncer>
    ),
    errorElement: SHELL_ERROR_ELEMENT,
    handle: { titleKey: 'auth.routeGuard.callbackTitle' },
  },
  // Local sign-in (identity axis, 2026-08-21) — a sibling of '/', OUTSIDE
  // <RequireAuth> for the same reason the callback is: an unauthenticated tab has
  // to be able to reach it. Registered in BOTH auth modes on purpose; in 'oidc'
  // mode nothing navigates here, and making route registration depend on runtime
  // config would make the router's shape unknowable to the seal that reads this
  // file (see the note above about single-quoted path literals).
  {
    path: '/login',
    element: (
      <RouteAnnouncer>
        <LoginRoute />
      </RouteAnnouncer>
    ),
    errorElement: SHELL_ERROR_ELEMENT,
    handle: { titleKey: 'auth.local.title' },
  },
  // Password change. Authenticated, but deliberately OUTSIDE the shell: a
  // principal with a pending change may reach no other operation, so rendering
  // the navigation around it would offer only dead links.
  {
    path: '/change-password',
    element: (
      <RouteAnnouncer>
        <ChangePasswordRoute />
      </RouteAnnouncer>
    ),
    errorElement: SHELL_ERROR_ELEMENT,
    handle: { titleKey: 'auth.local.passwordChangeTitle' },
  },
  {
    // `<RouteAnnouncer>` wraps BOTH top-level elements rather than sitting
    // inside `AppLayout`. `useMatches()` needs to run inside the router, but
    // `/auth/callback` is a SIBLING of `/`, not a child — there is no shared
    // component to hook. Wrapping each top-level element is what makes the
    // `titleKey` declaration exhaustive in consumption, not just in
    // declaration. (`ScreenTracker` above sets the same precedent: a
    // cross-cutting concern applied at the route element, leaving
    // `_layout.tsx` alone.)
    path: '/',
    element: (
      <RequireAuth>
        <ScreenTracker>
          <RouteAnnouncer>
            <AppLayout />
          </RouteAnnouncer>
        </ScreenTracker>
      </RequireAuth>
    ),
    errorElement: SHELL_ERROR_ELEMENT,
    // The shell is not a screen; every child supplies its own title, so this
    // one only ever surfaces if a child somehow matches without a handle.
    handle: { titleKey: 'routes.layout.appTitle' },
    children: [
      {
        index: true,
        element: <OverviewRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.home.title' },
      },
      // 멀티챔버 P6 (2026-06-16): chamber availability + distributed remote
      // measurement (central proxy — start + progress poll).
      {
        path: 'chambers',
        element: <ChambersRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.chambers.pageTitle' },
      },
      // ADR-0017 Phase 1 (2026-06-22): 프로젝트(모델) 선택·생성 진입층. 진입
      // 워크플로우 첫 단계 — UUID 직접입력을 모델 고르기/만들기로 대체, 선택 시
      // ?project=<id> 컨텍스트를 이후 화면에 전파.
      {
        path: 'my-projects',
        element: <MyProjectsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.myProjects.title' },
      },
      // PM·RF 통합 샘플관리 Phase F (2026-06-23): 시료 인벤토리 — PM/시험원 엑셀
      // import(권한 게이트) + team 필터 + 시료 통합 카드. ?project= 컨텍스트.
      {
        path: 'inventory',
        element: <InventoryRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.sampleInventory.pageTitle' },
      },
      // ADR-0017 Phase 2 (2026-06-22): 분야(시험 분야) 선택 — 프로젝트 선택 후
      // 4개 분야(workbench area) 1급 선택, ?project=&area= 컨텍스트 전파.
      {
        path: 'fields',
        element: <FieldsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.fields.title' },
      },
      // Phase 6 (2026-06-23): 시간가중 진행률 대시보드 — 분야×버킷 시험 진행률
      // (중앙 read model, ?project= 컨텍스트). fields → 진척 대시보드 흐름.
      {
        path: 'progress',
        element: <ProgressRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.progress.title' },
      },
      // 멀티챔버 P6 (2026-06-16): test-plan draft authoring view + publish
      // (headless API — plan→측정 연결).
      {
        path: 'test-plans',
        element: <TestPlansRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.testPlans.pageTitle' },
      },
      // FE-P2 (2026-05-27): project coverage dashboard (central read model).
      {
        path: 'projects',
        element: <ProjectsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.projects.title' },
      },
      // WEB-PROVIDER-UI-0 (2026-05-29): read-only provider UI descriptor viewer.
      {
        path: 'providers',
        element: <ProvidersRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.providers.pageTitleBrand' },
      },
      // FE-P8 (2026-06-13): project membership / RBAC roster (Platform read +
      // admin assign/revoke; backend membership API already shipped & sealed).
      {
        path: 'membership',
        element: <MembershipRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.membership.pageTitle' },
      },
      // FE-P5 (2026-05-26): remote measurement control (Session API).
      {
        path: 'control',
        element: <ControlRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.control.pageTitle' },
      },
      // FE-P6 (2026-05-26): report / artifact view (Headless API).
      {
        path: 'reports',
        element: <ReportsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.reports.page.title' },
      },
      // Phase G 배선 (2026-07-29): 성적서 대장 — 중앙 test_reports 인스턴스 조회/
      // 생성 + 자동 인용(Platform API). `/reports`(headless 생성 요청 큐)와 도메인·
      // 권한·쿼리 키가 모두 분리된 별개 화면이다.
      {
        path: 'test-reports',
        element: <TestReportsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.testReports.title' },
      },
      // 성적서 §6 장비목록 (2026-08-07): 프로젝트가 실제로 사용한 계측기/
      // 시험용 소프트웨어를 시험원이 고르고 확정하는 화면. EMS 가 표준
      // 리스트의 SSOT 이고 이 화면은 실사용 기록을 만든다.
      {
        path: 'equipment-lists',
        element: <EquipmentListsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.equipmentLists.title' },
      },
      // 참조 데이터 워크벤치 (2026-08-08): 측정 참조값의 권위 있는 원본은 중앙
      // 플랫폼이고 챔버 PC 는 복제본이다. 시험원이 후보를 검토하고 게시하는
      // 자리 — 이 화면이 없으면 게시가 0이라 모든 패밀리가 워크북 소유로 남는다.
      {
        path: 'reference-data',
        element: <ReferenceDataRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.referenceData.title' },
      },
      // 플롯 보관 현황 (plot-custody ①, 2026-08-09): 심사 증거인 플롯 **원본**이
      // 보관소에 실재하는가. 이 화면이 없으면 그 답이 나오는 곳은 측정 종료 로그
      // 한 줄과 발행 직전 거부 메시지뿐이고, 둘 다 그 자리에 있던 사람만 본다.
      {
        path: 'artifact-custody',
        element: <ArtifactCustodyRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.artifactCustody.title' },
      },
      // FE-P4 frontend (2026-05-26): session / result browser (attempt history).
      {
        path: 'sessions',
        element: <SessionsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.sessions.title' },
      },
      // FE-P7 (2026-06-14): measurement jobs console (Headless API — queue
      // status counts + job list + control-gated remote stop).
      {
        path: 'jobs',
        element: <JobsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.jobs.page.title' },
      },
      // tester-ux Phase S-diag: runtime/session diagnostics, moved out of the
      // operator landing into [설정]>진단 (gated /session/info call lives here).
      {
        path: 'diagnostics',
        element: <DiagnosticsRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.diagnostics.title' },
      },
      ...gridPocRoutes,
      {
        path: '*',
        element: <NotFoundRoute />,
        errorElement: ROUTE_ERROR_ELEMENT,
        handle: { titleKey: 'routes.notFound.title' },
      },
    ],
  },
];

/**
 * `AppRoute` → react-router `RouteObject`, the single conversion site.
 *
 * Exported so the vitest harness builds its memory router from the same
 * mapping the browser router uses — otherwise a test could pass against a
 * shape production never renders.
 */
export function toRouteObjects(routes: readonly AppRoute[]): RouteObject[] {
  return routes.map((route) =>
    'index' in route
      ? {
          index: true,
          element: route.element,
          errorElement: route.errorElement,
          handle: route.handle,
        }
      : {
          path: route.path,
          element: route.element,
          errorElement: route.errorElement,
          handle: route.handle,
          ...(route.children ? { children: toRouteObjects(route.children) } : {}),
        },
  );
}

export function App(): JSX.Element {
  const config = getRuntimeConfig();
  const showDevtools =
    config.environmentName === 'development' || config.environmentName === 'test';
  // Lazily created and held for the component's lifetime. Building the router
  // at module scope subscribed to browser history as an IMPORT side effect,
  // which made `appRoutes` unimportable from a jsdom test without booting a
  // history listener.
  const [router] = useState(() => createBrowserRouter(toRouteObjects(appRoutes)));
  return (
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        {/* Residual layer — see the boundary-layer note above. The shell net is
            the `/` route's `errorElement`, NOT this. */}
        <AppErrorBoundary>
          <RouterProvider router={router} />
        </AppErrorBoundary>
        {showDevtools && <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-right" />}
      </QueryClientProvider>
    </StrictMode>
  );
}
