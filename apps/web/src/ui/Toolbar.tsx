import type { ReactNode } from 'react';

/**
 * Toolbar — compact filter/action row above a table or list.
 *
 * Phase 1 §5.1 primitive. Wraps free-form children in a horizontal flex
 * with consistent gap/padding and an underline divider. `inline` variant
 * drops the divider (used inside a Section, where the SectionBand already
 * separates).
 */
export interface ToolbarProps {
  readonly children: ReactNode;
  /** Accessibility label — required because Toolbar uses `role="toolbar"`
   *  (a11y axe rule). */
  readonly ariaLabel: string;
  /** Drop the bottom border + outer margin (when embedded under another
   *  divider). */
  readonly inline?: boolean;
}

export function Toolbar({ children, ariaLabel, inline }: ToolbarProps): JSX.Element {
  return (
    <div
      className={inline ? 'toolbar toolbar--inline' : 'toolbar'}
      role="toolbar"
      aria-label={ariaLabel}
      data-testid="toolbar"
    >
      {children}
    </div>
  );
}

export default Toolbar;
