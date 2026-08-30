import { FieldGroup } from './FieldGroup';

/**
 * ProjectPicker — a presentational "choose a project from a list" control.
 *
 * project-picker-ssot (2026-06-26). The operator console identifies a project by
 * its human handles (model name / management number), never by the internal
 * `project_id` UUID. Before this primitive, five routes each rendered a raw
 * `<input>` that demanded a pasted UUID + an `isValidProjectId` gate — a pattern
 * the design direction had already replaced on `/my-projects` (pick a model, the
 * id flows in the URL). This primitive owns the accessible select contract once
 * (label↔control wiring via FieldGroup, an empty placeholder option, an optional
 * live status note); the data binding (fetch + option labels + loading/error
 * copy) lives in the `@/shared/ProjectSelectField` container, mirroring how
 * `NumericLookupForm` takes its data/callbacks from the route.
 *
 * A native `<select>` is deliberate: a native control is fully keyboard- and
 * screen-reader-accessible with type-ahead and zero dependencies — no custom
 * ARIA combobox to get wrong.
 *
 * W3-B M-C (2026-07-30) narrowed the *data* claim in that paragraph without
 * touching the *control* decision. The backend no longer ships the directory
 * unpaginated, so the container now reads one searchable page and passes an
 * optional `search` unit; this primitive renders that as a plain text input
 * **composed above** the select — it does NOT replace the select. The native
 * type-ahead, the screen-reader semantics and the mobile picker all stay
 * exactly as they were, which is the whole point of composing instead of
 * swapping in a custom `role="combobox"` widget (WAI-ARIA APG combobox would
 * have to be reimplemented in full, and a partial implementation is an
 * accessibility regression, not a feature).
 */
export interface ProjectPickerOption {
  /** The option's `project_id` (UUID) — the committed value, never shown raw. */
  readonly value: string;
  /** The human label (model · management number), built by the container. */
  readonly label: string;
}

/**
 * The optional search box composed above the select (W3-B M-C).
 *
 * These travel as ONE object rather than six independent optional props on
 * purpose: a search input without an `onChange` is a frozen field and one
 * without an `inputId`/`label` is an unlabelled control. Only a required-member
 * object makes "all of it or none of it" a *compile* error instead of a
 * runtime surprise.
 */
export interface ProjectPickerSearch {
  /** Input `id` + FieldGroup `htmlFor`. MUST differ from `selectId` — two
   *  separately labelled controls, not one label pointing at two nodes. */
  readonly inputId: string;
  /** Visible label for the search input (its own, not the select's). */
  readonly label: string;
  /** Placeholder copy (what is searchable). */
  readonly placeholder: string;
  /** Current draft text (the container owns debouncing/commit). */
  readonly value: string;
  /** Draft change handler — fires on every keystroke. */
  readonly onChange: (value: string) => void;
  /** Stable test id for the input. */
  readonly testId?: string;
}

export interface ProjectPickerProps {
  /** Visible field label (passes through to FieldGroup). */
  readonly label: string;
  /** Control `id` — also the FieldGroup `htmlFor` target (label↔select wiring). */
  readonly selectId: string;
  /** Selected `project_id`, or `''` when nothing is chosen (placeholder shown). */
  readonly value: string;
  /** Commit handler — receives the chosen `project_id` (`''` for the placeholder). */
  readonly onChange: (projectId: string) => void;
  /** Selectable projects (already mapped to value/label by the container). */
  readonly options: readonly ProjectPickerOption[];
  /** Copy for the leading empty option (e.g. "Select a project…"). */
  readonly placeholderLabel: string;
  /** Disable the control (loading / error — no options to choose). */
  readonly disabled?: boolean;
  /** Optional status note rendered under the select in a polite live region
   *  (loading / error / empty). Absent when a normal list is available. */
  readonly statusMessage?: string;
  /** Stable test id for the select. */
  readonly selectTestId?: string;
  /** Stable test id for the status note. */
  readonly statusTestId?: string;
  /** Optional server-side search box composed above the select. Absent ⇒ the
   *  pre-W3-B markup, byte-identical. */
  readonly search?: ProjectPickerSearch;
}

export function ProjectPicker({
  label,
  selectId,
  value,
  onChange,
  options,
  placeholderLabel,
  disabled,
  statusMessage,
  selectTestId,
  statusTestId,
  search,
}: ProjectPickerProps): JSX.Element {
  return (
    <div className="project-picker">
      {/* The search box is a SIBLING of the select's FieldGroup, above it —
          never inside. FieldGroup owns exactly one label↔control pairing, so
          nesting a second control in its children slot would leave the input
          unlabelled and point the select's label at the wrong node. */}
      {search !== undefined && (
        <FieldGroup label={search.label} htmlFor={search.inputId}>
          <input
            id={search.inputId}
            type="search"
            value={search.value}
            placeholder={search.placeholder}
            onChange={(e) => search.onChange(e.target.value)}
            {...(search.testId !== undefined ? { 'data-testid': search.testId } : {})}
          />
        </FieldGroup>
      )}
      <FieldGroup label={label} htmlFor={selectId}>
        <select
          id={selectId}
          className="project-picker__select"
          value={value}
          disabled={disabled === true}
          onChange={(e) => onChange(e.target.value)}
          {...(selectTestId !== undefined ? { 'data-testid': selectTestId } : {})}
        >
          <option value="">{placeholderLabel}</option>
          {options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </FieldGroup>
      {statusMessage !== undefined && (
        <p
          className="project-picker__status"
          role="status"
          {...(statusTestId !== undefined ? { 'data-testid': statusTestId } : {})}
        >
          {statusMessage}
        </p>
      )}
    </div>
  );
}

export default ProjectPicker;
