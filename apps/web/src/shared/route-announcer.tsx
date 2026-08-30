import { useEffect } from 'react';
import { useMatches } from 'react-router-dom';

import { useT } from '@/i18n';

import type { ReactNode } from 'react';

/**
 * Route title + screen announcement — the ONLY consumer of `handle.titleKey`.
 *
 * `app.tsx` declares one `titleKey` per route object (sealed exhaustive: a route
 * that forgets it fails `tsc`). This module is the other half — it reads that
 * declaration and turns it into the two things a screen-reader user needs when
 * an SPA swaps its content without a document load:
 *
 *   1. `document.title` — what the browser tab, the window switcher and the
 *      history list say. Without it every screen is called "FCC Test Platform".
 *   2. A polite live-region announcement — because a `document.title` change
 *      alone is NOT reliably announced by screen readers on a client-side
 *      navigation (the document never reloads, so there is no page-load event
 *      to hang the announcement on).
 *
 * Focus movement is deliberately NOT here: "the start of the new screen" is the
 * `<main>` column, which only exists inside the shell (`routes/_layout.tsx`
 * owns it). `/auth/callback` renders no shell and no main — it is a transient
 * redirect screen with nothing to focus. Splitting the two concerns keeps each
 * with the component that owns the DOM it touches, instead of having this
 * module reach across a boundary and query `#content` by id.
 *
 * Both facts are derived, never hardcoded: the screen name comes from the route
 * handle through `t()`, and the title format itself is an i18n template so a
 * locale can reorder it.
 */

/**
 * Per-route metadata carried on the route object itself.
 *
 * Declared HERE, next to its only reader, and imported by `app.tsx`. `titleKey`
 * is an i18n key resolving to the screen's name — deliberately the SAME key the
 * screen's `<PageHeader>` renders, so the browser tab and the H1 are one fact
 * and cannot drift. It rides ON the route object rather than in a parallel
 * path→title map: a second map is a second thing to forget when a route is
 * added.
 */
export interface AppRouteHandle {
  readonly titleKey: string;
}

function titleKeyOf(handle: unknown): string | null {
  if (typeof handle !== 'object' || handle === null) return null;
  const { titleKey } = handle as { readonly titleKey?: unknown };
  return typeof titleKey === 'string' && titleKey !== '' ? titleKey : null;
}

/**
 * The deepest matched route that names a screen.
 *
 * Deepest wins because a child route is more specific than the shell it renders
 * inside: on `/jobs` both the `/` shell and the `jobs` child match, and the
 * screen is "Measurement Jobs", not "FCC Test Platform".
 */
function useScreenTitleKey(): string | null {
  const matches = useMatches();
  for (let index = matches.length - 1; index >= 0; index -= 1) {
    const key = titleKeyOf(matches[index]?.handle);
    if (key !== null) return key;
  }
  return null;
}

export function RouteAnnouncer({ children }: { readonly children: ReactNode }): JSX.Element {
  const { t } = useT();
  const titleKey = useScreenTitleKey();
  const screen = titleKey === null ? null : t(titleKey);

  // Resolved during render, not inside the effect, so the effect depends on a
  // STRING. `useT()` hands back a fresh `t` closure every render, so depending
  // on it would re-run the effect on every render instead of when the title
  // actually changes — and a locale switch still changes this string, which is
  // how the tab title follows the locale toggle.
  const documentTitle =
    screen === null
      ? null
      : t('routes.layout.documentTitle', { screen, app: t('routes.layout.appTitle') });

  useEffect(() => {
    if (documentTitle === null) return;
    document.title = documentTitle;
  }, [documentTitle]);

  // The live region is mounted for the app's whole lifetime (this component
  // wraps the routes rather than living inside one), which is what makes an
  // announcement work at all: a region inserted together with its text is not
  // announced — only a text change inside an already-present region is.
  // `role="status"` IS the polite live region (implicit `aria-live="polite"`);
  // a route change must never interrupt what the operator is doing, so this is
  // deliberately not `role="alert"`.
  return (
    <>
      {children}
      <p className="sr-only" role="status" data-testid="route-announcer">
        {screen === null ? '' : t('routes.layout.routeAnnouncement', { screen })}
      </p>
    </>
  );
}
