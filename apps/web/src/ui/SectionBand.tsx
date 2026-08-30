import { SECTION_HEADING_LEVEL } from './heading-levels';

import type { ReactNode } from 'react';

/**
 * SectionBand — section divider that avoids nested-card visuals (§5.2).
 *
 * Phase 1 §5.1 primitive. Replaces `<h2>` + manual margin/border that today
 * separates "리포트 자동화 큐" / "리포트 요청 조회" / "세션 아티팩트 조회".
 * Renders an `<h2>` with an optional metadata caption next to it (e.g.
 * row count), divided by a single bottom border (one card, never nested).
 */
export interface SectionBandProps {
  /** Section title — rendered as `<h2>`. */
  readonly title: string;
  /** Optional meta caption ("3 / 12 결과", "마지막 갱신 2시간 전"). */
  readonly meta?: ReactNode;
  /** `id` for the title — wire to the section's `aria-labelledby`. */
  readonly titleId?: string;
}

export function SectionBand({ title, meta, titleId }: SectionBandProps): JSX.Element {
  // The section rung is declared once in `heading-levels` and shared with the
  // state placeholders, which derive the rung BELOW it (contract §M8.3).
  const Heading = `h${SECTION_HEADING_LEVEL}` as const;
  return (
    <div className="section-band" data-testid="section-band">
      <Heading className="section-band__title" id={titleId}>
        {title}
      </Heading>
      {meta !== undefined && (
        <p className="section-band__meta" data-testid="section-band-meta">
          {meta}
        </p>
      )}
    </div>
  );
}

export default SectionBand;
