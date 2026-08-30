import { liveRegionProps } from './live-region';

/**
 * StatusMessage — display-only success/info surface (WCAG 2.1 SC 4.1.3).
 *
 * The accessible counterpart to {@link ErrorState}. Routes that mutate central
 * state (claim acquire/release, membership assign/revoke) had no programmatic
 * success announcement — a screen-reader user got silence after a write. This
 * primitive renders an `aria-live="polite"` status region (role="status") so
 * the outcome of an async write is announced without moving focus.
 *
 * Like ErrorState, it is display-only: the message is supplied by the caller
 * (the route owns the domain wording); the primitive contains NO status-code
 * branching. `tone` selects the token palette only.
 */
export interface StatusMessageProps {
  /** Operator-facing outcome message (the route owns the wording). */
  readonly message: string;
  /** Visual tone — `success` (positive write) or `info` (neutral notice). */
  readonly tone?: 'success' | 'info';
  /** Override `data-testid` when a route needs a stable selector. */
  readonly testId?: string;
}

export function StatusMessage({
  message,
  tone = 'success',
  testId,
}: StatusMessageProps): JSX.Element {
  return (
    <p
      className={`status-message status-message--${tone}`}
      // `writeOutcome` — the mutation finished; announce it, but the operator
      // is not blocked, so it waits for a pause (`./live-region`).
      {...liveRegionProps('writeOutcome')}
      data-testid={testId ?? 'status-message'}
    >
      {message}
    </p>
  );
}

export default StatusMessage;
