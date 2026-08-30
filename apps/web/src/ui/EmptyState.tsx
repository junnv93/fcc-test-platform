import { STATE_HEADING_LEVEL } from './heading-levels';

import type { StateHeadingLevel } from './heading-levels';
import type { ReactNode } from 'react';

/**
 * EmptyState — "no data" placeholder.
 *
 * Phase 1 §5.1 primitive. Routes today render `<p>데이터가 없습니다</p>` ad
 * hoc; the primitive gives the empty branch a recognisable shape (dashed
 * border, centred copy) so it is not mistaken for a loading or error state.
 *
 * W4 §M8.3/§M8.4 changed two things:
 *   - the title is no longer a hardcoded `<h2>`. It defaults to the state rung
 *     (`STATE_HEADING_LEVEL`), one below the rung `SectionBand` owns, so an
 *     empty section no longer forges a peer section in the document outline.
 *   - a glyph is rendered above the copy. Loading (shimmer), empty (∅) and
 *     error (⚠) must be distinguishable at a glance and NOT by colour alone;
 *     the glyph is decorative (`aria-hidden`) because the semantics already
 *     live in `role="status"` + the copy.
 */
export interface EmptyStateProps {
  /** One-line headline ("데이터가 없습니다" / "조건이 없습니다"). */
  readonly title: string;
  /** Optional secondary copy — e.g. how to populate (run a measurement, ...). */
  readonly description?: string;
  /** Optional CTA slot — usually a single button or link. */
  readonly action?: ReactNode;
  /** Heading rung for the title. Defaults to the state rung; raise it only
   *  when the empty state stands in for a whole page rather than a section. */
  readonly headingLevel?: StateHeadingLevel;
  /** Override `data-testid` so route-specific test seals like
   *  `coverage-empty` / `descriptor-empty` can keep their stable id without
   *  a hidden `<span>` shadow element. Defaults to `empty-state`. */
  readonly testId?: string;
}

export function EmptyState({
  title,
  description,
  action,
  headingLevel = STATE_HEADING_LEVEL,
  testId,
}: EmptyStateProps): JSX.Element {
  const Heading = `h${headingLevel}` as const;
  return (
    <div
      className="empty-state"
      role="status"
      // `role="status"` is only an implicit polite live region in some AT;
      // make the announcement explicit so a screen reader voices the empty
      // result after a lookup/filter without a manual focus shift (WCAG 4.1.3).
      aria-live="polite"
      data-testid={testId ?? 'empty-state'}
    >
      <span className="empty-state__icon" aria-hidden="true" data-testid="empty-state-icon" />
      <Heading className="empty-state__title">{title}</Heading>
      {description !== undefined && <p className="empty-state__description">{description}</p>}
      {action !== undefined && <div data-testid="empty-state-action">{action}</div>}
    </div>
  );
}

export default EmptyState;
