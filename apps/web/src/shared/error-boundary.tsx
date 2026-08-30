import { useEffect } from 'react';
import { ErrorBoundary as ReactErrorBoundary, type FallbackProps } from 'react-error-boundary';
import { Link, useRouteError } from 'react-router-dom';

import { t, useT } from '@/i18n';
import { captureException } from '@/observability/sentry';
import { Button, liveRegionProps } from '@/ui';

import type { ReactNode } from 'react';

/**
 * Error fallbacks — one presentational view, three boundary layers.
 *
 * The layers are NOT redundant; each sees a different part of the tree and the
 * comments on `app.tsx` name which is which. What they share is this file's
 * `ErrorFallbackView`: a non-PII fallback that says WHAT failed and WHAT the
 * operator can do next (never a stack trace — the full stack lives in Sentry,
 * ADR-0006).
 *
 *   - `RouteErrorPage`  — route layer. Rendered by react-router in the
 *     `<Outlet/>` slot, so the header / nav / skip-link / hotkeys survive and
 *     the operator can simply navigate away. react-router's
 *     `RenderErrorBoundary` clears the error when `location` changes, so
 *     navigating IS the recovery path (no manual `resetKeys` — that would be a
 *     second implementation of what the router already does).
 *   - `ShellErrorPage`  — shell layer. Rendered when the shell itself (auth
 *     gate, layout chrome) throws, replacing react-router's untranslated,
 *     un-instrumented `DefaultErrorComponent`. There is no shell left to
 *     navigate within, so recovery is a full document load.
 *   - `AppErrorBoundary` — residual layer, kept for what falls OUTSIDE the
 *     router's render tree (see `app.tsx`).
 *
 * react-router does not report route errors to Sentry, so both router-facing
 * pages forward them explicitly.
 */

/** Presentational fallback. Holds the assertive live region: a crash removes
 *  the content the operator was using, which is the textbook case for
 *  interrupting them. That judgement is not made here — it is the
 *  `blockingFailure` ruling in `@/ui/live-region`, shared with `ErrorState`
 *  and the OIDC failure panels so the three cannot drift apart. */
function ErrorFallbackView({
  title,
  message,
  testId,
  children,
}: {
  readonly title: string;
  readonly message: string;
  readonly testId: string;
  readonly children: ReactNode;
}): JSX.Element {
  return (
    <div {...liveRegionProps('blockingFailure')} className="error-fallback" data-testid={testId}>
      <h2>{title}</h2>
      <p>{message}</p>
      <div className="error-fallback__actions">{children}</div>
    </div>
  );
}

/**
 * Residual error boundary — wraps `<RouterProvider>` in `app.tsx`.
 *
 * NOTE (honesty): this is NOT the shell net. react-router always wraps the root
 * match in its own `RenderErrorBoundary` (`route.errorElement || index === 0`),
 * so anything thrown inside the routed tree is caught BELOW this boundary by
 * the route/shell layers. What is left for this one is what lives outside the
 * router's render tree — the router provider's own failure modes and any future
 * sibling rendered next to it. Removing it would leave those with nothing.
 */
export function AppErrorBoundary({ children }: { children: ReactNode }): JSX.Element {
  return (
    <ReactErrorBoundary
      FallbackComponent={ErrorFallback}
      onError={(error, info) => {
        captureException(error, { componentStack: info.componentStack ?? undefined });
      }}
    >
      {children}
    </ReactErrorBoundary>
  );
}

function ErrorFallback({ error, resetErrorBoundary }: FallbackProps): JSX.Element {
  const { t } = useT();
  return (
    <ErrorFallbackView
      title={t('shared.errorBoundary.title')}
      message={getDisplayMessage(error)}
      testId="error-fallback"
    >
      <Button type="button" variant="primary" onClick={resetErrorBoundary}>
        {t('common.retry')}
      </Button>
    </ErrorFallbackView>
  );
}

/** Route layer — the shell around this fallback is still mounted. */
export function RouteErrorPage(): JSX.Element {
  const { t } = useT();
  const error = useRouteError();
  useReportRouteError(error, 'route');
  return (
    <ErrorFallbackView
      title={t('shared.errorBoundary.title')}
      message={getDisplayMessage(error)}
      testId="route-error-fallback"
    >
      {/* SPA navigation: react-router discards the error on location change,
          so this link recovers without dropping the loaded application. */}
      <Link to="/">{t('shared.errorBoundary.backHome')}</Link>
      <Button type="button" variant="primary" onClick={reloadDocument}>
        {t('shared.errorBoundary.reload')}
      </Button>
    </ErrorFallbackView>
  );
}

/** Shell layer — the shell is gone, so both recovery paths reload the document
 *  (a `<Link>` would re-render the same failing shell element in place). */
export function ShellErrorPage(): JSX.Element {
  const { t } = useT();
  const error = useRouteError();
  useReportRouteError(error, 'shell');
  return (
    <ErrorFallbackView
      title={t('shared.errorBoundary.shellTitle')}
      message={getDisplayMessage(error)}
      testId="shell-error-fallback"
    >
      <a href="/">{t('shared.errorBoundary.backHome')}</a>
      <Button type="button" variant="primary" onClick={reloadDocument}>
        {t('shared.errorBoundary.reload')}
      </Button>
    </ErrorFallbackView>
  );
}

/** react-router swallows route errors into its own state; forward them so the
 *  crash rate is observable (ADR-0006) instead of only visible on screen. */
function useReportRouteError(error: unknown, layer: 'route' | 'shell'): void {
  useEffect(() => {
    captureException(error, { errorBoundaryLayer: layer });
  }, [error, layer]);
}

function reloadDocument(): void {
  globalThis.location.reload();
}

function getDisplayMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return t('shared.errorBoundary.unknown');
}
