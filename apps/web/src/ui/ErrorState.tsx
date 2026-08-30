import { useT } from '@/i18n';

import { Button } from './Button';
import { ERROR_VARIANT_CONTRACT } from './error-variants';
import { liveRegionProps } from './live-region';

import type { ErrorVariant } from './error-variants';
import type { ReactElement } from 'react';

/**
 * ErrorState — error surface with a recovery slot (FD-D + W4 §M8.4–§M8.6).
 *
 * Phase 1 §5.1 primitive. The message is supplied by the caller — typically
 * via `describeApiError(error, context, overrides)` from `./errors`. This
 * primitive itself contains NO status-code branching; centralising that
 * logic was the whole point of FD-D, and W4 preserves it: the optional
 * `variant` is a DOMAIN classification the route chooses, never something
 * derived here from a response code.
 *
 * A variant contributes three things from `error-variants` (the SSOT): the
 * glyph, a one-line domain hint under the message, and the label of the
 * recovery control. The caller still supplies the behaviour — only the route
 * knows where "재측정" or "게인 재계산" leads.
 *
 * Dead-end ban (§M8.6), enforced by the TYPE not by review: `ErrorStateProps`
 * is a discriminated union on `variant`. Choosing a domain failure mode forces
 * exactly one of `onRecover`, `action`, or `noActionReason` — `variant` alone
 * does not compile. A runtime guard cannot achieve this; a route that forgets
 * the recovery would ship and only strand the operator in production. Making
 * it a compile error means the dead end never reaches a build.
 */

/** Props shared by every branch of the union. */
interface ErrorStateCommonProps {
  /** Operator-facing summary (usually `describeApiError(...)` output). */
  readonly message: string;
  /** Optional verbose detail (request id, response body excerpt) shown in
   *  mono with `white-space: pre-wrap`. */
  readonly details?: string;
  /** Override `data-testid` when a route needs a stable selector. */
  readonly testId?: string;
}

/** No domain classification — the plain FD-D surface. The recovery slots are
 *  closed here because their label comes from the variant contract; a handler
 *  without a variant would render an unlabelled button. */
interface ErrorStatePlainProps extends ErrorStateCommonProps {
  readonly variant?: undefined;
  readonly onRecover?: undefined;
  readonly action?: undefined;
  readonly noActionReason?: undefined;
}

/** Recovery handler — rendered as a button labelled by the variant. */
interface ErrorStateHandlerRecoveryProps extends ErrorStateCommonProps {
  readonly variant: ErrorVariant;
  readonly onRecover: () => void;
  readonly action?: undefined;
  readonly noActionReason?: undefined;
}

/** Fully custom recovery control (link, multi-button group).
 *
 *  The type is `ReactElement`, NOT `ReactNode` or even `NonNullable<ReactNode>`.
 *  `ReactNode` minus null/undefined still admits `false`, `''`, `0` and `[]` —
 *  every one of which type-checks and then renders NOTHING, reproducing the
 *  exact dead end §M8.6 bans. `action={isAdmin && <Btn/>}` is the realistic
 *  shape of that mistake: it is `false` for a non-admin, i.e. precisely the
 *  operator who most needs to be told where to go. `ReactElement` accepts an
 *  element or a fragment (`<><A/><B/></>` for multi-control recoveries) and
 *  rejects the render-nothing primitives at compile time. */
interface ErrorStateCustomRecoveryProps extends ErrorStateCommonProps {
  readonly variant: ErrorVariant;
  readonly action: ReactElement;
  readonly onRecover?: undefined;
  readonly noActionReason?: undefined;
}

/** No recovery is possible — then say WHY, in the operator's words. */
interface ErrorStateNoRecoveryProps extends ErrorStateCommonProps {
  readonly variant: ErrorVariant;
  readonly noActionReason: string;
  readonly onRecover?: undefined;
  readonly action?: undefined;
}

export type ErrorStateProps =
  | ErrorStatePlainProps
  | ErrorStateHandlerRecoveryProps
  | ErrorStateCustomRecoveryProps
  | ErrorStateNoRecoveryProps;

export function ErrorState({
  message,
  details,
  variant,
  onRecover,
  action,
  noActionReason,
  testId,
}: ErrorStateProps): JSX.Element {
  const { t } = useT();
  const contract = variant === undefined ? null : ERROR_VARIANT_CONTRACT[variant];
  // `??` is exact here (never `||`): `action` is a `ReactElement` or absent —
  // never a falsy-but-present node — so only an omitted slot falls through to
  // the handler-rendered button.
  const recovery =
    action ??
    (onRecover !== undefined && contract !== null ? (
      <Button
        type="button"
        variant="primary"
        className="touch-target"
        data-testid="error-state-recover"
        onClick={onRecover}
      >
        {t(contract.recoveryToken)}
      </Button>
    ) : null);

  return (
    <div
      className="error-state"
      // `blockingFailure` — the operator asked for something and it is not
      // there. Urgency ruling + rationale live in `./live-region`.
      {...liveRegionProps('blockingFailure')}
      {...(variant === undefined ? {} : { 'data-variant': variant })}
      data-testid={testId ?? 'error-state'}
    >
      <div className="error-state__header">
        <span className="error-state__icon" aria-hidden="true" data-testid="error-state-icon" />
        <p className="error-state__title">{message}</p>
      </div>
      {contract !== null && (
        <p className="error-state__hint" data-testid="error-state-hint">
          {t(contract.hintToken)}
        </p>
      )}
      {details !== undefined && (
        <pre className="error-state__details" data-testid="error-state-details">
          {details}
        </pre>
      )}
      {recovery !== null && (
        <div className="error-state__actions" data-testid="error-state-actions">
          {recovery}
        </div>
      )}
      {recovery === null && contract !== null && noActionReason !== undefined && (
        <p className="error-state__no-action" data-testid="error-state-no-action">
          {noActionReason}
        </p>
      )}
    </div>
  );
}

export default ErrorState;
