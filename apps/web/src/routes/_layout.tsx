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
import { BlockSkeleton, Button, DensityToggle, ShortcutHelp } from '@/ui';

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
        <span className="app-title">{t('routes.layout.appTitle')}</span>
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
            <Outlet />
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
            <Outlet />
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
                    {t(item.labelKey)}
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
