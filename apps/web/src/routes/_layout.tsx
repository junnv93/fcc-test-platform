import { Suspense, useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';

import { SignOutButton, useAuthSession } from '@/auth/route-guard';
import { getRuntimeConfig } from '@/config/runtime';
import { useT } from '@/i18n';
import { LocaleToggle } from '@/i18n/LocaleToggle';
import {
  APP_SHELL_GRID_POC_ITEM,
  APP_SHELL_NAV_GROUPS,
  PROJECT_SIDEBAR_ACTIONS,
  SESSION_ONLY_NAV_TARGETS,
  SETTINGS_GROUP_LABEL_KEY,
} from '@/shared/app-shell-navigation';
import { isValidProjectId } from '@/shared/project-id';
import { projectWorkflowActions } from '@/shared/project-workflow';
import { PROJECT_QUERY_PARAM, ROUTE_PATHS } from '@/shared/route-links';
import { GLOBAL_SHORTCUTS, type GlobalShortcutId } from '@/shared/shortcuts';
import { type Hotkey, useHotkeys } from '@/shared/use-hotkeys';
import { ThemeToggle } from '@/theme/ThemeToggle';
import { BlockSkeleton, Button, DensityToggle, NavIcon, ShortcutHelp } from '@/ui';

const ROUTES_WITH_OWN_MAIN_LANDMARK = new Set([
  '/',
  '/chambers',
  '/my-projects',
  '/projects',
  '/reports',
  '/test-plans',
  ROUTE_PATHS.testReports,
]);

/**
 * Root layout shell — single source of navigation truth.
 *
 * Subsequent sprints (S5–S7) add the missing routes; for S1 the layout
 * intentionally lists them so the shell shape is locked, and unknown
 * routes 404 instead of silently rendering nothing.
 *
 * Sprint S2: HeaderUserMenu surfaces the authenticated principal + sign-out.
 */
export function AppLayout(): JSX.Element {
  const { t } = useT();
  const sessionApiEnabled = getRuntimeConfig().sessionApiEnabled;
  const location = useLocation();
  const currentProjectId = currentProjectIdFromSearch(location.search);

  // Keyboard shortcut layer (design-system reconciliation, card B3). Handlers
  // are keyed by GlobalShortcutId so a new global shortcut in the SSOT without a
  // handler fails `tsc` (compile-time completeness — see @/shared/shortcuts).
  const navigate = useNavigate();
  const [shortcutHelpOpen, setShortcutHelpOpen] = useState(false);
  const closeShortcutHelp = useCallback(() => setShortcutHelpOpen(false), []);
  const handlers = useMemo<Record<GlobalShortcutId, (event: KeyboardEvent) => void>>(
    () => ({
      help: () => setShortcutHelpOpen((open) => !open),
      search: (event) => {
        const field = document.querySelector<HTMLElement>('#content input, #content select');
        if (field !== null) {
          event.preventDefault();
          field.focus();
        }
      },
      'goto-sessions': () => navigate('/sessions'),
      'goto-projects': () => navigate('/projects'),
      'goto-jobs': () => navigate('/jobs'),
      'goto-chambers': () => navigate('/chambers'),
      'goto-test-plans': () => navigate('/test-plans'),
    }),
    [navigate],
  );
  const hotkeys = useMemo<Hotkey[]>(
    () =>
      GLOBAL_SHORTCUTS.map((shortcut) => ({
        sequence: shortcut.sequence,
        handler: handlers[shortcut.id],
      })),
    [handlers],
  );
  useHotkeys(hotkeys);

  // Route-change focus. On a client-side navigation the DOM under `<main>` is
  // replaced but focus stays wherever it was — typically on the nav link the
  // operator just followed — so a screen-reader user is left reading the old
  // position and a keyboard user tabs back through the whole nav to reach the
  // new content. Moving focus to the main column puts both at the start of the
  // new screen; `@/shared/route-announcer` says WHICH screen.
  //
  // Two deliberate restraints so this never fights the operator:
  //   - keyed on `pathname` only, so a query-string change (`?project=…`,
  //     pagination, filters) does not yank focus mid-interaction;
  //   - skipped on first mount, because arriving at a page is not a navigation
  //     — focus belongs at the document start where the skip-link is.
  const mainRef = useRef<HTMLElement | null>(null);
  const { pathname } = location;
  const routeOwnsMainLandmark = ROUTES_WITH_OWN_MAIN_LANDMARK.has(pathname);
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      return;
    }
    mainRef.current?.focus();
  }, [pathname]);

  return (
    <div className="app-shell">
      {/* Skip-to-content link — first focusable, visible on focus only (a11y). */}
      <a href="#content" className="skip-link">
        {t('routes.layout.skipToContent')}
      </a>
      <header className="app-header" role="banner">
        {/* Brand mark. Inline SVG for the same reason as `NavIcon` — one
            dependency for one glyph would put a second source of visual truth
            beside `global.css`.

            🔴 2026-09-09 (2차): 이름이 ZENITH 로 바뀌면서 글리프도 따라간다.
            직전 판은 「쌓인 판(plate) 세 장」이었다 — 「플랫폼」이라는 말을
            그대로 그린 것이라 이름과 그림이 같은 말을 두 번 했고, 이제는
            이름이 다른 말을 한다.

            🔴 그 다음 판은 «천구의 돔»이었는데, 그것도 버렸다. 돔은 하늘의
            «모양»이지 하늘에서 보는 «것»이 아니다 — 그리고 20px 로 줄이면
            돔은 그냥 반원, 즉 아무 말도 하지 않는 도형이 된다.

            그래서 지금 판은 «대각으로 지나가는 유성»이다. 별 하나와 그 뒤로
            남는 자취 둘, 전부 45° 대각선 위에 놓인다.

            ⚠️ 대각선이 실제로 일을 한다. 이 셸에서 45° 로 놓인 것은 여기뿐이라 —
            내비·카드·표는 전부 수직·수평이다 — 마크가 «주변과 다른 축»을 갖는다.
            로고가 아이콘 무리에서 튀어나오는 가장 싼 방법이고, 색이나 크기를
            더 쓰지 않는다.

            ⚠️ 자취는 별에 «닿지 않는다». 붙이면 획이 하나로 이어져 사선 막대가
            되고, 떨어뜨리면 눈이 그 사이를 «속도»로 채운다.

            ⚠️ 별은 4각이다. 5각 별은 즐겨찾기·평점의 어휘라 이 화면에서 이미
            다른 뜻을 갖고, 6각 이상은 20px 에서 뭉갠다. 그리고 4각 별의 오목한
            변은 «빛나는 점»으로 읽히지 «도형»으로 읽히지 않는다.

            ⚠️ 원소 3개다. 직전 판보다 하나 줄었고, 그만큼 작은 크기에서 버틴다.

            `aria-hidden` because the adjacent text already names the product;
            announcing both would read the name twice. */}
        <span className="app-brand">
          <span className="app-brand__mark" aria-hidden="true">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinejoin="round"
              strokeLinecap="round"
            >
              <path
                d="M16.4 2.5c.46 3.36 1.43 4.33 4.79 4.79-3.36.46-4.33 1.43-4.79 4.79-.46-3.36-1.43-4.33-4.79-4.79 3.36-.46 4.33-1.43 4.79-4.79Z"
                fill="currentColor"
                stroke="none"
              />
              <path d="M12.5 11 4.6 18.9" />
              <path d="M9.3 7.9 6.2 11" />
            </svg>
          </span>
          {/* 워드마크와 «종류»는 다른 것이다 — 앞은 이름이고 뒤는 이것이
              무엇인지다. 한 문자열로 두면 둘에 같은 서체·같은 무게가 걸려
              「ZENITH Test Platform」 전체가 하나의 긴 이름으로 읽힌다.
              쪼개 두면 이름은 이름대로 서고, 종류는 조용히 따라온다. */}
          <span className="app-title">
            <b className="app-title__mark">{t('routes.layout.appTitle')}</b>
            <span className="app-title__kind">{t('routes.layout.appKind')}</span>
          </span>
        </span>
        <PrimaryNav sessionApiEnabled={sessionApiEnabled} currentProjectId={currentProjectId} />
        <div className="app-header__controls">
          <LocaleToggle />
          <DensityToggle />
          <ThemeToggle />
          <HeaderUserMenu />
        </div>
      </header>
      {/* `tabIndex={-1}` makes the column programmatically focusable. It is
          load-bearing for BOTH focus paths: the route-change effect above, and
          the skip-link — a browser only moves focus to a fragment target that
          can hold focus, so `href="#content"` scrolled here without focusing
          and the next Tab went back to the top of the nav. */}
      {/* No error boundary here. Every child route declares its own
          `errorElement` (app.tsx, route layer), and react-router renders that
          boundary INSIDE the `<Outlet/>` — i.e. BELOW this position — so a
          boundary here can no longer see a route error at all. Measured:
          adding one back leaves tests/route-error-boundary.test.tsx fully
          green, which is exactly the problem — it would be a second net that
          never catches anything. The residual net moved to `app.tsx`, outside
          `<RouterProvider>`, where there is still something for it to see. */}
      {/* Route-chunk fallback. A bare text line collapsed the main column
          to one row and then snapped it open; the skeleton holds a page's
          worth of height instead (§M8.1). */}
      {routeOwnsMainLandmark ? (
        // Workbench routes provide their own named `<main>`/`<aside>` slots;
        // keeping the shell container non-landmark avoids nesting those
        // landmarks in a second app-level main.
        <div
          className="app-main"
          id="content"
          ref={(node) => {
            mainRef.current = node;
          }}
          tabIndex={-1}
        >
          <Suspense
            fallback={
              <BlockSkeleton lines={6} label={t('common.loadingPage')} testId="page-loading" />
            }
          >
            {/* ⚠️ `key` 가 이 애니메이션의 «전부»다. 키가 없으면 React 는 같은
                DOM 노드를 재사용하고, 재사용된 노드에서는 CSS 애니메이션이
                다시 재생되지 않는다 — 클래스만 붙여 놓고 「왜 안 움직이지」
                하게 되는 자리다. 키가 바뀌면 노드가 새로 나고, 새 노드는
                애니메이션을 처음부터 재생한다.

                ⚠️ 키는 `pathname` «만» 이다. `location.key` 나 `search` 까지
                넣으면 필터 하나 바꿀 때마다 화면 전체가 다시 페이드되는데,
                그건 부드러운 게 아니라 산만한 것이다. 화면이 바뀔 때만 움직인다.

                Suspense «안»에 있는 이유: 밖에 두면 스켈레톤이 뜰 때 한 번
                재생되고 정작 내용이 도착할 때는 조용하다. */}
            <div className="route-swap" key={location.pathname}>
              <Outlet />
            </div>
          </Suspense>
        </div>
      ) : (
        // Legacy and utility routes still use the shell-owned main landmark;
        // this preserves skip-link and route-change focus for those screens.
        <main
          className="app-main"
          id="content"
          tabIndex={-1}
          ref={(node) => {
            mainRef.current = node;
          }}
        >
          <Suspense
            fallback={
              <BlockSkeleton lines={6} label={t('common.loadingPage')} testId="page-loading" />
            }
          >
            {/* ⚠️ `key` 가 이 애니메이션의 «전부»다. 키가 없으면 React 는 같은
                DOM 노드를 재사용하고, 재사용된 노드에서는 CSS 애니메이션이
                다시 재생되지 않는다 — 클래스만 붙여 놓고 「왜 안 움직이지」
                하게 되는 자리다. 키가 바뀌면 노드가 새로 나고, 새 노드는
                애니메이션을 처음부터 재생한다.

                ⚠️ 키는 `pathname` «만» 이다. `location.key` 나 `search` 까지
                넣으면 필터 하나 바꿀 때마다 화면 전체가 다시 페이드되는데,
                그건 부드러운 게 아니라 산만한 것이다. 화면이 바뀔 때만 움직인다.

                Suspense «안»에 있는 이유: 밖에 두면 스켈레톤이 뜰 때 한 번
                재생되고 정작 내용이 도착할 때는 조용하다. */}
            <div className="route-swap" key={location.pathname}>
              <Outlet />
            </div>
          </Suspense>
        </main>
      )}
      <ShortcutHelp open={shortcutHelpOpen} onClose={closeShortcutHelp} />
    </div>
  );
}

/**
 * Primary navigation — responsive disclosure.
 *
 * At/above the `--bp-lg` shell breakpoint the grouped nav renders inline
 * (CSS shows `.app-nav__panel` regardless of `data-open`, hides the toggle).
 * Below it (tablet + mobile) the whole nav collapses behind a compact menu
 * button so the header never overflows the document — the doc's PREFERRED
 * mobile model, not the scrollable-strip fallback.
 *
 * A native `<details>` cannot express "open inline on desktop, collapsed on
 * mobile" from a single initial state (a closed `<details>`'s content is not
 * CSS-revealable — verified empirically), so this is a controlled disclosure
 * with FULL ARIA equivalence: `aria-expanded`/`aria-controls` on the toggle,
 * `Escape`-to-close returning focus to the toggle, and close-on-navigation.
 * The route/permission/query-key surface is unchanged — only the shell form.
 */
function currentProjectIdFromSearch(search: string): string | null {
  const projectId = new URLSearchParams(search).get(PROJECT_QUERY_PARAM)?.trim() ?? '';
  return isValidProjectId(projectId) ? projectId : null;
}

function PrimaryNav({
  sessionApiEnabled,
  currentProjectId,
}: {
  readonly sessionApiEnabled: boolean;
  readonly currentProjectId: string | null;
}): JSX.Element {
  const { t } = useT();
  const [menuOpen, setMenuOpen] = useState(false);
  const panelId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const location = useLocation();

  // Close on navigation: when the route changes (a nav link was followed), the
  // mobile dropdown collapses. Keyed on `pathname` so it only fires on an
  // actual route change, never on the settings sub-group toggle.
  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  // Close on Escape from anywhere while open, returning focus to the toggle
  // (standard disclosure a11y) — a document listener avoids attaching
  // interactive handlers to the non-interactive panel container.
  useEffect(() => {
    if (!menuOpen) return undefined;
    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        setMenuOpen(false);
        toggleRef.current?.focus();
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [menuOpen]);

  return (
    <nav className="app-nav" aria-label={t('routes.layout.navMenuToggle')}>
      <Button
        type="button"
        variant="ghost"
        ref={toggleRef}
        className="app-nav__toggle touch-target touch-target--glove"
        data-testid="nav-menu-toggle"
        aria-expanded={menuOpen}
        aria-controls={panelId}
        onClick={() => setMenuOpen((open) => !open)}
      >
        <span className="app-nav__toggle-icon" aria-hidden="true">
          ☰
        </span>
        {t('routes.layout.navMenuToggle')}
      </Button>
      <div id={panelId} className="app-nav__panel" data-open={menuOpen} data-testid="nav-panel">
        {APP_SHELL_NAV_GROUPS.map((group, groupIndex) => {
          // Dev-gated Grid PoC rides along in the last group so it stays
          // hidden from a production build (mirrors verify-grid-poc-exclusion).
          const isLastGroup = groupIndex === APP_SHELL_NAV_GROUPS.length - 1;
          const baseItems = sessionApiEnabled
            ? [...group.items]
            : group.items.filter((item) => !SESSION_ONLY_NAV_TARGETS.has(item.to));
          const items =
            isLastGroup && import.meta.env['VITE_GRID_POC'] === '1'
              ? [...baseItems, APP_SHELL_GRID_POC_ITEM]
              : baseItems;
          const navItems = (
            <ul>
              {items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
                  >
                    <NavIcon name={item.icon} />
                    <span className="nav-link__label">{t(item.labelKey)}</span>
                  </NavLink>
                </li>
              ))}
            </ul>
          );
          // §5 설정 — 관리자용 그룹은 하단에 접이식으로(기본 닫힘). 시험원의
          // 일상 흐름(시험하기/결과)을 위에서 방해하지 않도록 native
          // <details>/<summary> 로 접는다(키보드/스크린리더 기본 지원, JS 상태
          // 불필요). 라우트/권한/쿼리키는 불변 — 렌더 형태만 변경.
          const isSettingsGroup = group.labelKey === SETTINGS_GROUP_LABEL_KEY;
          return isSettingsGroup ? (
            <details
              key={group.labelKey}
              className="app-nav__group app-nav__group--collapsible"
              data-testid="nav-settings-group"
            >
              <summary className="app-nav__group-label">{t(group.labelKey)}</summary>
              {navItems}
            </details>
          ) : (
            <div key={group.labelKey} className="app-nav__group">
              <span className="app-nav__group-label">{t(group.labelKey)}</span>
              {navItems}
            </div>
          );
        })}
        {currentProjectId !== null && <ProjectNavSection projectId={currentProjectId} />}
      </div>
    </nav>
  );
}

function ProjectNavSection({ projectId }: { readonly projectId: string }): JSX.Element {
  const { t } = useT();
  const links = projectWorkflowActions(
    projectId,
    PROJECT_SIDEBAR_ACTIONS.map((item) => item.id),
  );
  const labelById = new Map(PROJECT_SIDEBAR_ACTIONS.map((item) => [item.id, item.labelKey]));
  return (
    <div className="app-nav__group app-nav__group--project" data-testid="nav-project-group">
      <span className="app-nav__group-label">{t('routes.layout.projectNav.title')}</span>
      <p className="app-nav__project-id" data-testid="nav-project-id">
        {t('routes.layout.projectNav.selectedProject', { project: projectId })}
      </p>
      <ul>
        {links.map((link) => (
          <li key={link.id}>
            <NavLink
              to={link.href}
              end={false}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
            >
              {t(labelById.get(link.id) ?? 'routes.layout.nav.projects')}
            </NavLink>
          </li>
        ))}
      </ul>
      <p className="app-nav__project-hint">{t('routes.layout.projectNav.hint')}</p>
    </div>
  );
}

function HeaderUserMenu(): JSX.Element | null {
  const state = useAuthSession();
  if (state.kind !== 'authenticated') return null;
  const display = state.principal.name ?? state.principal.email ?? state.principal.subject;
  return (
    <div className="app-user" data-testid="header-user-menu">
      <span className="app-user__name" title={state.principal.subject}>
        {display}
      </span>
      <SignOutButton />
    </div>
  );
}
