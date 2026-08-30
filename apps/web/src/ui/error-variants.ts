/**
 * Error variant SSOT — FCC failure modes, not HTTP status codes (§M8.6).
 *
 * The generic "요청에 실패했습니다 / 다시 시도" pair is the wrong shape for this
 * product. An operator standing at a chamber does not need to know that a
 * request failed; they need to know WHICH physical or procedural precondition
 * is missing and what the next move is. The six modes below are the ones that
 * actually strand a measurement session, and each fixes an icon + an
 * explanation + a recovery label in one place so a route cannot invent a
 * seventh wording for the same failure.
 *
 * This module is display-layer only. It contains NO status-code branching —
 * `describeApiError` remains the single owner of turning an API error into an
 * operator sentence (FD-D), and the variant only adds the domain layer on top
 * of whatever sentence it produced. Choosing the variant is the route's job,
 * because only the route knows which precondition it depends on.
 *
 * Icons are declared as CSS custom properties in `global.css`
 * (`--error-<variant>-icon`) following the `--status-*-icon` precedent, so the
 * glyph is never the only channel: the copy carries the same information.
 */

export const ERROR_VARIANTS = [
  /** Analyzer / BT tester / switch unreachable over VISA, GPIB or LAN. */
  'instrument-offline',
  /** The instrument answered too late — a SCPI round trip timed out. */
  'scpi-timeout',
  /** No sample (model number + sample no) is selected for the session. */
  'sample-not-selected',
  /** Antenna gain has not arrived yet, so PSD/CP carry the `(Gain누락)` tag. */
  'antenna-gain-missing',
  /** No duty measurement exists, so DCCF cannot be derived. */
  'dccf-missing',
  /** The signed-in operator lacks the permission the operation requires. */
  'forbidden',
] as const;

export type ErrorVariant = (typeof ERROR_VARIANTS)[number];

/** What a variant fixes: the glyph, the domain explanation, and the label of
 *  the recovery control. `recoveryToken` is never null for the six modes below
 *  — a stranded operator must always be offered a next move — but the
 *  primitive still handles the "no action available" case explicitly so a
 *  caller that genuinely cannot offer one has to say why (dead-end ban). */
export interface ErrorVariantContract {
  readonly hintToken: string;
  readonly recoveryToken: string;
}

export const ERROR_VARIANT_CONTRACT: Readonly<Record<ErrorVariant, ErrorVariantContract>> = {
  'instrument-offline': {
    hintToken: 'ui.errorState.variants.instrumentOffline.hint',
    recoveryToken: 'ui.errorState.variants.instrumentOffline.recovery',
  },
  'scpi-timeout': {
    hintToken: 'ui.errorState.variants.scpiTimeout.hint',
    recoveryToken: 'ui.errorState.variants.scpiTimeout.recovery',
  },
  'sample-not-selected': {
    hintToken: 'ui.errorState.variants.sampleNotSelected.hint',
    recoveryToken: 'ui.errorState.variants.sampleNotSelected.recovery',
  },
  'antenna-gain-missing': {
    hintToken: 'ui.errorState.variants.antennaGainMissing.hint',
    recoveryToken: 'ui.errorState.variants.antennaGainMissing.recovery',
  },
  'dccf-missing': {
    hintToken: 'ui.errorState.variants.dccfMissing.hint',
    recoveryToken: 'ui.errorState.variants.dccfMissing.recovery',
  },
  forbidden: {
    hintToken: 'ui.errorState.variants.forbidden.hint',
    recoveryToken: 'ui.errorState.variants.forbidden.recovery',
  },
};
