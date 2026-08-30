/**
 * live-region — the assertive/polite ruling table (W4-A M4).
 *
 * `role="alert"` is an ASSERTIVE live region: assistive tech interrupts
 * whatever the operator is currently reading or typing to announce it. Used
 * for the wrong thing it is worse than silence — a table of failed rows, each
 * badge an alert, machine-guns the screen-reader user on every render.
 *
 * Before this module every surface picked its own role inline, so "is this
 * urgent?" was answered ad hoc 8 times and never written down. The judgement
 * now lives here ONCE, keyed by the KIND of announcement rather than by the
 * component making it, and every surface spreads `liveRegionProps(kind)`
 * instead of hardcoding `role=`/`aria-live=`. A new surface has to name which
 * kind it is — which is exactly the question that was going unasked.
 *
 * The rule the table encodes:
 *
 *   assertive  ⟺  the change removed what the operator was working on, or
 *                 rejected the action they just took. Waiting is not an
 *                 option because there is nothing left to wait through.
 *   polite     ⟺  worth announcing, but the operator's current task still
 *                 works. It waits for the next reading pause.
 *   off        ⟺  a label on content that is already on screen. Announcing
 *                 it is noise, and its cardinality is unbounded.
 *
 * Sealed by `tests/test_frontend_architecture_conformance.py`
 * (`TestAssertiveLiveRegionsStayOnTheUrgentAxis`) — a literal `role="alert"`
 * anywhere else under `src/` fails the backend lane.
 */

/** How loudly assistive tech announces the region. `off` = not a live region. */
export type LiveRegionUrgency = 'assertive' | 'polite' | 'off';

/** The ARIA role that carries the urgency. */
export type LiveRegionRole = 'alert' | 'status' | 'note';

/**
 * The kinds of announcement this app makes. Keyed by WHAT HAPPENED, never by
 * which component renders it — two components announcing the same kind of
 * event must not disagree about urgency.
 */
export type LiveRegionKind =
  | 'blockingFailure'
  | 'inputRejected'
  | 'writeOutcome'
  | 'backgroundLoad'
  | 'inlineNotice'
  | 'staticLabel';

export interface LiveRegionRuling {
  readonly urgency: LiveRegionUrgency;
  readonly role: LiveRegionRole;
  /** Why this kind sits on this axis. Read by the next person adding a
   *  surface — and asserted non-empty by the seal, because a ruling table
   *  without reasons is just a second place to copy `role="alert"` from. */
  readonly rationale: string;
}

export const LIVE_REGION_RULINGS: Readonly<Record<LiveRegionKind, LiveRegionRuling>> = {
  blockingFailure: {
    urgency: 'assertive',
    role: 'alert',
    rationale:
      'The content the operator asked for is gone or never arrived — a render crash, ' +
      'a failed load, a rejected sign-in. There is nothing underneath to interrupt, ' +
      'so silence strands them on a screen that will never fill in.',
  },
  inputRejected: {
    urgency: 'assertive',
    role: 'alert',
    rationale:
      'The operator just typed something the form refuses, and the submit they are ' +
      'reaching for is disabled because of it. The announcement answers an action ' +
      'taken a moment ago; deferring it lets them press a dead button in silence.',
  },
  writeOutcome: {
    urgency: 'polite',
    role: 'status',
    rationale:
      'A write finished. Worth announcing — silence after a mutation reads as a hang — ' +
      'but nothing the operator does next is blocked by it, so it waits for a pause.',
  },
  backgroundLoad: {
    urgency: 'polite',
    role: 'status',
    rationale:
      'Skeletons, refetch spinners, empty results. The screen is busy or empty on its ' +
      'own initiative rather than in answer to the operator; assertive here would fire ' +
      'on every navigation and every background refetch.',
  },
  inlineNotice: {
    urgency: 'polite',
    role: 'status',
    rationale:
      'A secondary surface degraded while the task stays reachable by another path — a ' +
      'picker whose list failed but whose direct-entry field still works, a freshness ' +
      'banner. Not urgent by construction: the operator can keep going.',
  },
  staticLabel: {
    urgency: 'off',
    role: 'note',
    rationale:
      'A label on content that is already on screen, rendered once per row. A live ' +
      'region here has unbounded cardinality — N rows means N announcements on every ' +
      'render — and it announces nothing the operator did not already scroll past.',
  },
};

/** Iteration order for exhaustiveness probes (tests, future switch tables). */
export const LIVE_REGION_KINDS: readonly LiveRegionKind[] = Object.freeze([
  'blockingFailure',
  'inputRejected',
  'writeOutcome',
  'backgroundLoad',
  'inlineNotice',
  'staticLabel',
]);

/** The kinds that interrupt. Exported so the seal derives the urgent axis from
 *  the table instead of re-listing it. */
export const ASSERTIVE_LIVE_REGION_KINDS: readonly LiveRegionKind[] = Object.freeze(
  LIVE_REGION_KINDS.filter((kind) => LIVE_REGION_RULINGS[kind].urgency === 'assertive'),
);

/** ARIA attributes to spread onto the announcing element. */
export interface LiveRegionAttributes {
  readonly role: LiveRegionRole;
  readonly 'aria-live'?: 'assertive' | 'polite';
}

/**
 * The attributes for an announcement of `kind`.
 *
 * `aria-live` is emitted explicitly alongside the role even though `alert`
 * and `status` imply it: several screen readers only treat `role="status"` as
 * a live region when the attribute is present (the reason `EmptyState` and
 * `StatusMessage` already wrote both by hand).
 */
export function liveRegionProps(kind: LiveRegionKind): LiveRegionAttributes {
  const ruling = LIVE_REGION_RULINGS[kind];
  if (ruling.urgency === 'off') {
    return { role: ruling.role };
  }
  return { role: ruling.role, 'aria-live': ruling.urgency };
}
