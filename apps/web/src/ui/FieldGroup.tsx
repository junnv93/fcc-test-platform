import { Children, cloneElement, isValidElement } from 'react';

import { liveRegionProps } from './live-region';

import type { ReactElement, ReactNode } from 'react';

/**
 * FieldGroup — label/control/description triplet with standardised spacing.
 *
 * Phase 1 §5.1 primitive. The label is uppercased (xs + uppercase tracking)
 * so a row of filter inputs reads as a compact header strip rather than a
 * verbose form. Routes pass the control element (or any node) as children.
 *
 * W4-A M3 — the primitive also owns `aria-describedby`. Before this, a hint or
 * a validation message sat next to its control with no programmatic link: a
 * screen-reader user focusing the field heard the label and nothing else. The
 * wiring lives HERE rather than in the 19 routes that render fields, because a
 * per-route copy is a per-route omission waiting to happen — and the ids have
 * to be derived from something unique anyway, which the primitive already has
 * (`htmlFor`, the control's own id).
 *
 * The description is attached to the child whose `props.id === htmlFor`, not to
 * the first element child. Routes legitimately pass siblings alongside the
 * control (`providers.tsx` passes a select + a skeleton + a notice;
 * `membership.tsx` passes an input + its `<datalist>`), so a positional rule
 * would decorate the wrong node in exactly the screens that need it most.
 */
export interface FieldGroupProps {
  /** Visible label text. The `<label>` `htmlFor` must match the control's
   *  `id` — the route owns the control, so it owns that wiring. */
  readonly label: string;
  /** `htmlFor` target — the id of the control rendered as children. Also the
   *  stem the description ids are derived from. */
  readonly htmlFor: string;
  /** Optional static help / hint shown under the control and linked to it
   *  through `aria-describedby`. Not a live region: it is present from the
   *  first render and is announced on focus, not on change. */
  readonly help?: string;
  /** Optional validation message for what the operator just typed. Linked
   *  through `aria-describedby` AND announced as `inputRejected` (assertive —
   *  see `./live-region`), because it answers an action taken a moment ago. */
  readonly error?: string;
  /** Successful validation copy. Error takes precedence when both are supplied. */
  readonly success?: string;
  /** Exposes required state on both the visible label and the actual control. */
  readonly required?: boolean;
  /** Comfortable is the default; compact is used for dense operator tables. */
  readonly density?: 'comfortable' | 'compact';
  /** Stable selector for the help node when a route asserts on it. */
  readonly helpTestId?: string;
  /** Stable selector for the validation node — routes that used to own their
   *  own alert element keep their existing test ids through this. */
  readonly errorTestId?: string;
  /** The actual control element (input/select/button), plus any siblings. */
  readonly children: ReactNode;
}

/** Description id suffixes. Derived from `htmlFor` (unique per control by the
 *  label↔control contract) so two FieldGroups in one container cannot collide.
 *  A constant id here would collide the moment a route renders fields in a
 *  loop — which `my-projects`, `test-reports` and `AddRowForm` all do. */
const HELP_ID_SUFFIX = '-help';
const ERROR_ID_SUFFIX = '-error';

/** Merge the derived description ids into whatever the control already
 *  declared, so a route that wired its own `aria-describedby` keeps it. */
interface ControlProps {
  readonly id?: unknown;
  readonly 'aria-describedby'?: string;
  readonly 'aria-invalid'?: boolean | 'false' | 'true';
  readonly 'aria-required'?: boolean | 'false' | 'true';
}

function withControlA11y(
  children: ReactNode,
  controlId: string,
  describedBy: string,
  required: boolean,
  invalid: boolean,
): ReactNode {
  return Children.map(children, (child) => {
    if (!isValidElement(child)) return child;
    const props = child.props as ControlProps;
    if (props.id !== controlId) return child;
    const existing = typeof props['aria-describedby'] === 'string' ? props['aria-describedby'] : '';
    const nextDescribedBy = [existing, describedBy].filter(Boolean).join(' ');
    const a11yProps: Partial<ControlProps> = {
      ...(nextDescribedBy === '' ? {} : { 'aria-describedby': nextDescribedBy }),
      ...(required ? { 'aria-required': true } : {}),
      ...(invalid ? { 'aria-invalid': true } : {}),
    };
    return cloneElement(child as ReactElement<ControlProps>, a11yProps);
  });
}

export function FieldGroup({
  label,
  htmlFor,
  help,
  error,
  success,
  required = false,
  density = 'comfortable',
  helpTestId,
  errorTestId,
  children,
}: FieldGroupProps): JSX.Element {
  const helpId = `${htmlFor}${HELP_ID_SUFFIX}`;
  const errorId = `${htmlFor}${ERROR_ID_SUFFIX}`;
  const successId = `${htmlFor}-success`;
  const visibleError = error !== undefined;
  const activeMessageId = visibleError ? errorId : success !== undefined ? successId : null;
  // Reading order: the hint explains the field, the error explains the
  // rejection. `aria-describedby` is announced in the order listed.
  const describedBy = [help !== undefined ? helpId : null, activeMessageId]
    .filter((id): id is string => id !== null)
    .join(' ');

  return (
    <div
      className={`field-group field-group--${density}`}
      data-density={density}
      data-testid="field-group"
    >
      <label htmlFor={htmlFor} className="field-group__label">
        {label}
        {required && (
          <span className="field-group__required" aria-hidden="true">
            {' '}
            *
          </span>
        )}
      </label>
      {!required && describedBy === ''
        ? children
        : withControlA11y(children, htmlFor, describedBy, required, visibleError)}
      {help !== undefined && (
        <p
          id={helpId}
          className="field-group__help"
          {...(helpTestId !== undefined ? { 'data-testid': helpTestId } : {})}
        >
          {help}
        </p>
      )}
      {error !== undefined && (
        <p
          id={errorId}
          className="field-group__error"
          {...liveRegionProps('inputRejected')}
          {...(errorTestId !== undefined ? { 'data-testid': errorTestId } : {})}
        >
          {error}
        </p>
      )}
      {error === undefined && success !== undefined && (
        <p id={successId} className="field-group__success" {...liveRegionProps('inlineNotice')}>
          {success}
        </p>
      )}
    </div>
  );
}

export default FieldGroup;
