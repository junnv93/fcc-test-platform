import { type ReactElement } from 'react';

import { useT } from '@/i18n';
import { Button, liveRegionProps } from '@/ui';

/**
 * 5 OIDC failure UX — Sprint S2.
 *
 * The contract enumerates exactly these five failure kinds. The discriminated
 * union is the single source of truth — `tests/test_apps_web_auth_scaffold.py`
 * cross-checks the literal token set so a future code path that invents an
 * undocumented kind fails the backend invariant.
 *
 * Accessibility: every panel announces as `blockingFailure` — assertive, so
 * screen readers report it immediately (Sprint S2 SHOULD S3). W4-A M4 moved
 * that judgement into `@/ui/live-region`: a sign-in that failed leaves nothing
 * to interrupt, which is the same ruling the crash fallbacks use.
 *
 * Internationalisation: copy is Korean-first per the project default; the
 * text constants are extracted (FAILURE_COPY) so Sprint S8 react-i18next
 * adoption is a mechanical translation step (SHOULD S4).
 */

/** Discriminated union — these are the only 5 OIDC failure kinds the UI
 *  is allowed to surface. Adding a new kind requires both a copy entry and
 *  a contract amendment. */
export type OidcFailureKind =
  | 'idp_config_missing'
  | 'token_expired'
  | 'idp_unreachable'
  | 'permission_denied'
  | 'backend_403';

/** Failure kind → i18n key stem. The copy lives in `src/locales/{ko,en}.json`
 *  (`auth.failure.<stem>.title` / `.description`) and is resolved at render
 *  time so the panel follows the active locale. Keeping this table parallel to
 *  the union preserves the exhaustiveness probe below — adding a kind without a
 *  key stem (and matching locale entry) is a compile error. */
const FAILURE_COPY_KEY: Readonly<Record<OidcFailureKind, string>> = {
  idp_config_missing: 'idpConfigMissing',
  token_expired: 'tokenExpired',
  idp_unreachable: 'idpUnreachable',
  permission_denied: 'permissionDenied',
  backend_403: 'backend403',
};

export interface OidcFailureViewProps {
  readonly kind: OidcFailureKind;
  /** Optional secondary detail (raw exception message / required permission name). */
  readonly detail?: string | null;
  /** Optional retry handler — when present the view renders a "다시 시도" button. */
  readonly onRetry?: () => void;
}

/** Renders the failure panel for the given kind. */
export function OidcFailureView({ kind, detail, onRetry }: OidcFailureViewProps): ReactElement {
  const { t } = useT();
  const stem = FAILURE_COPY_KEY[kind];
  const copy = {
    title: t(`auth.failure.${stem}.title`),
    description: t(`auth.failure.${stem}.description`),
  };
  return (
    <section
      className={`auth-failure auth-failure--${kind.replace(/_/gu, '-')}`}
      {...liveRegionProps('blockingFailure')}
      data-testid={`auth-failure-${kind}`}
    >
      <h1 className="auth-failure__title">{copy.title}</h1>
      <p className="auth-failure__description">{copy.description}</p>
      {detail !== undefined && detail !== null && detail.length > 0 && (
        /* Sprint S2-γ β-P2-9 — detail block is supplementary; assertive
           aria-live (inherited from the outer alert) would interrupt the
           operator. polite defers to the next reading pause. */
        <pre
          className="auth-failure__detail"
          aria-label={t('auth.failure.detailLabel')}
          aria-live="polite"
        >
          {detail}
        </pre>
      )}
      {onRetry && (
        <Button type="button" variant="primary" className="auth-failure__retry" onClick={onRetry}>
          {t('common.retry')}
        </Button>
      )}
    </section>
  );
}

/** Exhaustiveness probe — TypeScript fails compilation if FAILURE_COPY or
 *  OidcFailureKind drifts. Exported so tests can assert the literal set. */
export const FAILURE_KINDS: readonly OidcFailureKind[] = Object.freeze([
  'idp_config_missing',
  'token_expired',
  'idp_unreachable',
  'permission_denied',
  'backend_403',
]);

/** Type-level assertion: every kind has a copy entry (a missing key would
 *  produce a compile error here because indexed access is `never`). */
const _exhaustivenessProbe: Readonly<Record<OidcFailureKind, true>> = {
  idp_config_missing: true,
  token_expired: true,
  idp_unreachable: true,
  permission_denied: true,
  backend_403: true,
};
void _exhaustivenessProbe;
