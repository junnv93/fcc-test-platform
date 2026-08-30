import { Button } from './Button';
import { FieldGroup } from './FieldGroup';

/**
 * NumericLookupForm — a self-contained "type a numeric id and look it up" form.
 *
 * Phase 1 §5.1 primitive (fe-lookup-form-primitive, 2026-06-14). Several
 * read screens (reports.tsx's request-id + session-id lookups, and any future
 * detail-by-id view) repeated the identical scaffold inline: a `<form>` with a
 * `FieldGroup` + numeric `<input aria-invalid>` + a submit button disabled while
 * the draft is invalid + a live-region validation message. Re-declaring the
 * a11y wiring per route risks drift (one screen forgetting `aria-invalid` or
 * the announcement). This primitive owns that contract once; routes supply only
 * the copy, ids, and the parse/submit callbacks.
 *
 * W4-A M3 — the validation message is no longer a sibling `<p>` after the
 * submit button. It is handed to `FieldGroup`, which derives its id from
 * `inputId` and links it through `aria-describedby`; a screen-reader user
 * focusing the field now hears WHY the button is dead instead of just finding
 * it dead. `FieldGroup` also owns the urgency ruling (`inputRejected`).
 *
 * The control element keeps `id`/`data-testid` overridable so each route's
 * stable test seals (`request-id-input`, `session-id-input`, ...) survive the
 * extraction unchanged.
 */
export interface NumericLookupFormProps {
  /** Visible field label (passes through to FieldGroup). */
  readonly label: string;
  /** Control `id` — also the FieldGroup `htmlFor` target (label↔input wiring). */
  readonly inputId: string;
  /** Controlled draft value. */
  readonly value: string;
  /** Draft change handler (receives the raw string). */
  readonly onChange: (value: string) => void;
  /** Commit handler — invoked on form submit (Enter or button). */
  readonly onSubmit: () => void;
  /** Submit button copy. */
  readonly buttonLabel: string;
  /** Disable submit while the draft does not parse to a valid id. */
  readonly submitDisabled: boolean;
  /** True when the non-empty draft is invalid — drives `aria-invalid` + the
   *  FieldGroup-owned validation region. */
  readonly invalid: boolean;
  /** Validation copy shown in the live region when `invalid`. */
  readonly invalidMessage: string;
  /** Stable test id for the input (defaults to the FieldGroup-owned control). */
  readonly inputTestId?: string;
  /** Stable test id for the submit button. */
  readonly submitTestId?: string;
  /** Stable test id for the validation alert. */
  readonly invalidTestId?: string;
}

export function NumericLookupForm({
  label,
  inputId,
  value,
  onChange,
  onSubmit,
  buttonLabel,
  submitDisabled,
  invalid,
  invalidMessage,
  inputTestId,
  submitTestId,
  invalidTestId,
}: NumericLookupFormProps): JSX.Element {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <FieldGroup
        label={label}
        htmlFor={inputId}
        {...(invalid ? { error: invalidMessage } : {})}
        {...(invalidTestId !== undefined ? { errorTestId: invalidTestId } : {})}
      >
        <input
          id={inputId}
          {...(inputTestId !== undefined ? { 'data-testid': inputTestId } : {})}
          inputMode="numeric"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          aria-invalid={invalid}
        />
      </FieldGroup>
      <Button
        type="submit"
        variant="primary"
        {...(submitTestId !== undefined ? { 'data-testid': submitTestId } : {})}
        disabled={submitDisabled}
      >
        {buttonLabel}
      </Button>
    </form>
  );
}

export default NumericLookupForm;
