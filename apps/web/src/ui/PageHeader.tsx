import { useT } from '@/i18n';

import type { ReactNode } from 'react';

/**
 * PageHeader — route shell title + description + actions.
 *
 * Phase 1 §5.1 primitive. Replaces ad-hoc `<h1>` + inline description that
 * existing routes use today (overview/sessions/reports/projects/...). One
 * primitive owns the typography (h1 = `--font-size-lg`), spacing
 * (--space-4 below, divider) and action slot — routes only supply content.
 */
export interface PageHeaderProps {
  /** Route title — rendered as the page `<h1>` (single per route per a11y). */
  readonly title: string;
  /** Context line ABOVE the title — e.g. a time window ("최근 24시간") or the
   *  lane a screen belongs to (flowdeck-console-ui, R7).
   *
   *  ⚠️ Deliberately NOT a heading element. A screen-reader user navigating by
   *  heading wants the screen name; an `<h2>` above the `<h1>` would also
   *  invert the outline. It renders as a plain span and is read in document
   *  order, just before the title it qualifies. */
  readonly eyebrow?: string;
  /** Optional one-line description shown under the title. */
  readonly description?: string;
  /** Right-aligned action slot (toolbar buttons, link, badge). */
  readonly actions?: ReactNode;
  /** `id` for the title — wire to `aria-labelledby` of the section root. */
  readonly titleId?: string;
  /** Optional breadcrumb trail shown above the title for drill-down routes.
   *  PageHeader wraps the supplied content in a labeled `<nav>` landmark; the
   *  caller owns the trail markup (links + current item). */
  readonly breadcrumb?: ReactNode;
  /** Accessible label for the breadcrumb nav landmark. Defaults to the i18n
   *  `ui.pageHeader.breadcrumbLabel` SSOT when a breadcrumb is provided without
   *  an explicit (already-localised) override. */
  readonly breadcrumbLabel?: string;
}

export function PageHeader({
  title,
  eyebrow,
  description,
  actions,
  titleId,
  breadcrumb,
  breadcrumbLabel,
}: PageHeaderProps): JSX.Element {
  const { t } = useT();
  return (
    <header className="page-header" data-testid="page-header">
      {breadcrumb !== undefined && (
        <nav
          className="page-header__breadcrumb"
          aria-label={breadcrumbLabel ?? t('ui.pageHeader.breadcrumbLabel')}
          data-testid="page-header-breadcrumb"
        >
          {breadcrumb}
        </nav>
      )}
      {eyebrow !== undefined && (
        <span className="eyebrow page-header__eyebrow" data-testid="page-header-eyebrow">
          {eyebrow}
        </span>
      )}
      <h1 className="page-header__title" id={titleId}>
        {title}
      </h1>
      {actions !== undefined && (
        <div className="page-header__actions" data-testid="page-header-actions">
          {actions}
        </div>
      )}
      {description !== undefined && (
        <p className="page-header__description" data-testid="page-header-description">
          {description}
        </p>
      )}
    </header>
  );
}

export default PageHeader;
