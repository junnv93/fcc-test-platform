import { Fragment, useEffect, useId, useRef } from 'react';

import { useT } from '@/i18n';

import { Button } from './Button';

import type { KeyboardEvent, ReactNode } from 'react';

/**
 * DataTable — dense + numeric-aligned + horizontal-overflow table primitive.
 *
 * Phase 1 §5.1 sets the minimum viable contract. Phase 2 adds sticky header
 * + sticky first column; Phase 3 adds column resize + keyboard cell-nav +
 * virtualization. Until then, routes compose this primitive plus the helper
 * classNames (`data-cell-numeric`, `data-cell-status`) directly.
 *
 * The horizontal overflow wrapper (--datatable-overflow-shadow) addresses
 * §5.2: tables exceed viewport width without breaking layout, and the
 * inset shadow signals there is more content to the right.
 *
 * ## W3 §M7.2 — responsive column priority (ADDITIVE, opt-in)
 *
 * Seventeen call sites own their own `<thead>`/`<tbody>`, and three of them
 * live under a path this work is not allowed to touch, so the descriptor API
 * is added ALONGSIDE the slot API rather than replacing it. A caller that
 * still passes `head`/`body` renders exactly the markup it rendered before —
 * no wrapper class, no extra rows (sealed by `tests/ui/data-table.test.tsx`).
 * A caller that passes `columns`/`rows` additionally gets, from one descriptor:
 *
 *   - the `<thead>`/`<tbody>` markup, each cell tagged `data-priority`,
 *   - a per-row overflow line carrying the `detail` columns, which the
 *     compact band swaps in as those columns leave the row grid, and
 *   - a card list for the phone band, whose body carries EVERY column.
 *
 * A 1:N table (one summary row expanding into its member rows) adds an
 * `expansion` descriptor on top: a full-width panel row under the data row on
 * wide bands, and the same panel inside a native `<details>` on the card.
 *
 * Both narrow-band forms are information-preserving on purpose: the contract
 * bans hiding a value outright. And both are toggled by media queries alone —
 * this primitive never reads `window.innerWidth` and never subscribes to
 * `resize`, so there is no viewport state to desynchronise (M7.2a).
 */

/** How aggressively a column may be folded away as the viewport narrows. */
export type DataTableColumnPriority = 'primary' | 'secondary' | 'detail';

export interface DataTableColumn<TRow> {
  /** Stable identity — React key, sort key, and card field key. */
  readonly key: string;
  /** Visible column header, reused as the card field term and as the mobile
   *  sort `<option>` label (M7.2b). */
  readonly header: string;
  /** Fold rank: `detail` leaves the row grid first (compact band). */
  readonly priority: DataTableColumnPriority;
  /** Cell renderer. Called for the row cell, the overflow line, and the card
   *  field, so a column is authored once. The surface argument exists for the
   *  one thing that must NOT be authored once: a document-unique id. */
  readonly cell: (row: TRow, surface: DataTableSurface) => ReactNode;
  /** Extra cell className (`data-cell-numeric` / `data-cell-status`). */
  readonly className?: string;
  /** `data-testid` for the ROW cell only. Deliberately not mirrored onto the
   *  overflow line or the card field: both narrow-band forms are additional
   *  renderings of the same value, and duplicating a route's testid would make
   *  `getByTestId` ambiguous. Narrow-band assertions use the primitive's own
   *  `data-table-card` / `data-table-overflow-row` ids. */
  readonly testId?: string;
  /** Offer this column in the header sort control and the mobile sort select. */
  readonly sortable?: boolean;
  /** Render the row cell as `<th scope="row">` — the column that NAMES the
   *  row. Screen readers then announce it with every other cell in that row. */
  readonly rowHeader?: boolean;
}

/** Controlled sort state. The caller owns the ordering; this primitive only
 *  renders the affordances (desktop header buttons + phone `<select>`). */
export interface DataTableSort {
  readonly columnKey: string;
  readonly direction: 'asc' | 'desc';
  readonly onChange: (columnKey: string, direction: 'asc' | 'desc') => void;
}

/**
 * Which of the two renderings a descriptor callback is currently producing.
 *
 * The table row and the phone card are two projections of the SAME row, so any
 * document-unique id (a route `data-testid`) must be stamped on exactly one of
 * them — otherwise `getByTestId` becomes ambiguous the moment the descriptor
 * form is adopted. `'row'` is that surface, which is the same rule already
 * stated for `DataTableColumn.testId`.
 */
export type DataTableSurface = 'row' | 'card';

/**
 * Optional per-row detail panel — the 1:N shape (one summary row expands into
 * its member rows) that a flat column descriptor cannot express.
 *
 * Open state is caller-owned (a URL param, typically); this primitive stores
 * nothing. On the phone band the table is display:none, so the same panel is
 * re-offered inside the card as a native `<details>` disclosure: reachable
 * with no JS, no viewport branch, and no value lost (M7.2/M7.2a).
 */
export interface DataTableExpansion<TRow> {
  /** Is this row's panel open? */
  readonly isExpanded: (row: TRow) => boolean;
  /** Panel body. Called once per surface — see {@link DataTableSurface}. */
  readonly render: (row: TRow, surface: DataTableSurface) => ReactNode;
  /** `<summary>` label for the phone card's disclosure. */
  readonly cardSummary: (row: TRow) => string;
}

interface DataTableCommonProps {
  /** Caption text — required for §5.3 a11y (`<table caption>`). */
  readonly caption: string;
  /** Optional `data-testid` for the table itself. */
  readonly testId?: string;
  /** Optional `aria-label` override (when `caption` would be visually
   *  redundant; rare). */
  readonly ariaLabel?: string;
  /** Keep column headers visible while scrolling a dense table region. */
  readonly stickyHeader?: boolean;
  /** Enable roving row tabindex and Arrow/j/k/Home/End navigation. */
  readonly keyboardNavigation?: boolean;
  /** Optional row activation for Enter/Space when keyboard navigation is on. */
  readonly onRowActivate?: (row: HTMLTableRowElement) => void;
}

/** Legacy slot form — caller owns the markup. Unchanged. */
export interface DataTableSlotProps extends DataTableCommonProps {
  /** Whole `<thead>` markup — caller owns columns so DataTable does not need
   *  to know about the schema. */
  readonly head: ReactNode;
  /** Whole `<tbody>` markup — caller owns rows. */
  readonly body: ReactNode;
}

/** Descriptor form — caller declares columns, DataTable owns the responsive
 *  projections. */
export interface DataTableDescriptorProps<TRow> extends DataTableCommonProps {
  readonly columns: readonly DataTableColumn<TRow>[];
  readonly rows: readonly TRow[];
  /** Stable React key per row. The index is supplied as a last-resort
   *  tiebreaker for rows whose natural key can legitimately be absent. */
  readonly rowKey: (row: TRow, index: number) => string;
  /** Controlled sort state; omit for an unsorted table. */
  readonly sort?: DataTableSort;
  /** `data-testid` applied to every data `<tr>` (not to the overflow line). */
  readonly rowTestId?: string;
  /** Optional per-row detail panel (1:N rows). Omit for a flat table. */
  readonly expansion?: DataTableExpansion<TRow>;
}

export type DataTableProps<TRow = unknown> = DataTableSlotProps | DataTableDescriptorProps<TRow>;

export interface DataTableSortButtonProps {
  readonly direction: 'asc' | 'desc' | null;
  readonly onClick: () => void;
  readonly children: ReactNode;
}

/** Message key for the mobile sort direction toggle. Kept out of the JSX
 *  attribute so the key strings never read as inline English copy. */
function sortDirectionToken(direction: 'asc' | 'desc'): string {
  return direction === 'asc' ? 'ui.dataTable.sortDirectionAsc' : 'ui.dataTable.sortDirectionDesc';
}

function isDescriptorForm<TRow>(
  props: DataTableProps<TRow>,
): props is DataTableDescriptorProps<TRow> {
  return (props as DataTableDescriptorProps<TRow>).columns !== undefined;
}

export function DataTable<TRow>(props: DataTableProps<TRow>): JSX.Element {
  const { t } = useT();
  const {
    caption,
    testId,
    ariaLabel,
    stickyHeader = false,
    keyboardNavigation = false,
    onRowActivate,
  } = props;
  const tableRef = useRef<HTMLTableElement>(null);
  const sortSelectId = useId();

  const descriptor = isDescriptorForm(props) ? props : null;
  // Narrow on the guard rather than on `descriptor`, so TypeScript keeps the
  // slot branch's `head`/`body` in scope.
  const head = isDescriptorForm(props) ? renderDescriptorHead(props) : props.head;
  const body = isDescriptorForm(props) ? renderDescriptorBody(props) : props.body;

  useEffect(() => {
    if (!keyboardNavigation) return;
    const rows = getNavigableRows(tableRef.current);
    rows.forEach((row, index) => {
      row.tabIndex = index === 0 ? 0 : -1;
    });
  }, [keyboardNavigation, body]);

  function focusRow(row: HTMLTableRowElement): void {
    const rows = getNavigableRows(tableRef.current);
    rows.forEach((candidate) => {
      candidate.tabIndex = candidate === row ? 0 : -1;
    });
    row.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTableElement>): void {
    if (!keyboardNavigation || isInteractiveElement(event.target)) return;
    const currentRow = (event.target as HTMLElement).closest('tbody tr');
    if (!(currentRow instanceof HTMLTableRowElement)) return;
    const rows = getNavigableRows(tableRef.current);
    const index = rows.indexOf(currentRow);
    if (index === -1) return;

    const key = event.key;
    const nextIndex =
      key === 'ArrowDown' || key === 'j'
        ? Math.min(index + 1, rows.length - 1)
        : key === 'ArrowUp' || key === 'k'
          ? Math.max(index - 1, 0)
          : key === 'Home'
            ? 0
            : key === 'End'
              ? rows.length - 1
              : null;

    if (nextIndex !== null) {
      const nextRow = rows[nextIndex];
      if (nextRow === undefined) return;
      event.preventDefault();
      focusRow(nextRow);
      return;
    }

    if ((key === 'Enter' || key === ' ') && onRowActivate !== undefined) {
      event.preventDefault();
      onRowActivate(currentRow);
    }
  }

  const classNames = ['data-table'];
  if (stickyHeader) classNames.push('data-table--sticky-header');
  // Only the descriptor form carries the responsive modifier, so every
  // narrow-band rule is inert for the legacy slot form.
  if (descriptor !== null) classNames.push('data-table--responsive');

  const sort = descriptor?.sort;
  const sortableColumns = descriptor?.columns.filter((column) => column.sortable === true) ?? [];

  return (
    <div className="data-table-overflow" data-testid="data-table-overflow">
      {/* M7.2b — the card list has no column headers, so sorting would simply
          vanish on a phone. The header labels are reused as `<option>`s plus a
          direction toggle. Visible only in the phone band (CSS), but always in
          the DOM so no viewport branch enters JS. */}
      {sort !== undefined && sortableColumns.length > 0 && (
        <div className="data-table__mobile-sort" data-testid="data-table-mobile-sort">
          <label className="sr-only" htmlFor={sortSelectId}>
            {t('ui.dataTable.sortFieldLabel')}
          </label>
          <select
            id={sortSelectId}
            value={sort.columnKey}
            data-testid="data-table-mobile-sort-select"
            onChange={(event) => {
              sort.onChange(event.target.value, sort.direction);
            }}
          >
            {sortableColumns.map((column) => (
              <option key={column.key} value={column.key}>
                {column.header}
              </option>
            ))}
          </select>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="touch-target"
            data-testid="data-table-mobile-sort-direction"
            aria-label={t(sortDirectionToken(sort.direction))}
            onClick={() => {
              sort.onChange(sort.columnKey, sort.direction === 'asc' ? 'desc' : 'asc');
            }}
          >
            <span aria-hidden="true">{sort.direction === 'asc' ? '↑' : '↓'}</span>
          </Button>
        </div>
      )}
      {/* The keydown handler implements the roving-tabindex grid-navigation
          pattern (Arrow/j/k/Home/End across focusable rows) — a legitimate
          interactive table. The handler is inert unless keyboardNavigation is
          opted in, so the non-interactive-element lint rule is a false positive
          here. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions */}
      <table
        ref={tableRef}
        className={classNames.join(' ')}
        data-testid={testId ?? 'data-table'}
        aria-label={ariaLabel}
        data-keyboard-navigation={keyboardNavigation ? 'true' : undefined}
        onKeyDown={onKeyDown}
      >
        <caption>{caption}</caption>
        {head}
        {body}
      </table>
      {descriptor !== null && renderDescriptorCards(descriptor, caption)}
    </div>
  );
}

function renderDescriptorHead<TRow>(props: DataTableDescriptorProps<TRow>): ReactNode {
  const { columns, sort } = props;
  return (
    <thead>
      <tr>
        {columns.map((column) => (
          <th key={column.key} scope="col" data-priority={column.priority}>
            {column.sortable === true && sort !== undefined ? (
              <DataTableSortButton
                direction={sort.columnKey === column.key ? sort.direction : null}
                onClick={() => {
                  sort.onChange(
                    column.key,
                    sort.columnKey === column.key && sort.direction === 'asc' ? 'desc' : 'asc',
                  );
                }}
              >
                {column.header}
              </DataTableSortButton>
            ) : (
              column.header
            )}
          </th>
        ))}
      </tr>
    </thead>
  );
}

function renderDescriptorBody<TRow>(props: DataTableDescriptorProps<TRow>): ReactNode {
  const { columns, rows, rowKey, rowTestId, expansion } = props;
  const detailColumns = columns.filter((column) => column.priority === 'detail');
  return (
    <tbody>
      {rows.map((row, index) => (
        <Fragment key={rowKey(row, index)}>
          <tr {...(rowTestId === undefined ? {} : { 'data-testid': rowTestId })}>
            {columns.map((column) => {
              const Cell = column.rowHeader === true ? 'th' : 'td';
              return (
                <Cell
                  key={column.key}
                  {...(column.rowHeader === true ? { scope: 'row' as const } : {})}
                  data-priority={column.priority}
                  {...(column.className === undefined ? {} : { className: column.className })}
                  {...(column.testId === undefined ? {} : { 'data-testid': column.testId })}
                >
                  {column.cell(row, 'row')}
                </Cell>
              );
            })}
          </tr>
          {detailColumns.length > 0 && (
            // Rendered always, displayed only in the compact band. This is the
            // half of M7.2 that keeps the fold lossless: values that leave the
            // row grid reappear here instead of disappearing.
            <tr className="data-table__overflow-row" data-testid="data-table-overflow-row">
              <td colSpan={columns.length}>
                <dl className="data-table__overflow-list">
                  {detailColumns.map((column) => (
                    <Fragment key={column.key}>
                      <dt className="data-table__overflow-term">{column.header}</dt>
                      <dd className="data-table__overflow-value">{column.cell(row, 'row')}</dd>
                    </Fragment>
                  ))}
                </dl>
              </td>
            </tr>
          )}
          {expansion !== undefined && expansion.isExpanded(row) && (
            // 1:N detail panel. Full-width by construction, so adding or
            // removing a column can never leave it misaligned.
            <tr className="data-table__expansion-row" data-testid="data-table-expansion-row">
              <td colSpan={columns.length}>{expansion.render(row, 'row')}</td>
            </tr>
          )}
        </Fragment>
      ))}
    </tbody>
  );
}

function renderDescriptorCards<TRow>(
  props: DataTableDescriptorProps<TRow>,
  caption: string,
): ReactNode {
  const { columns, rows, rowKey, expansion } = props;
  const titleColumn = columns.find((column) => column.priority === 'primary') ?? columns[0];
  const fieldColumns = columns.filter((column) => column !== titleColumn);
  return (
    <ul className="data-table-cards" aria-label={caption} data-testid="data-table-cards">
      {rows.map((row, index) => (
        <li key={rowKey(row, index)} className="data-table-card" data-testid="data-table-card">
          {titleColumn !== undefined && (
            <p className="data-table-card__title">{titleColumn.cell(row, 'card')}</p>
          )}
          <dl className="data-table-card__fields">
            {fieldColumns.map((column) => (
              <Fragment key={column.key}>
                <dt className="data-table-card__term">{column.header}</dt>
                <dd className="data-table-card__value">{column.cell(row, 'card')}</dd>
              </Fragment>
            ))}
          </dl>
          {expansion !== undefined && (
            // The phone band hides the table, so the expansion row goes with
            // it. `<details>` restores the same panel with zero JS — the
            // disclosure is the platform's, not a viewport branch of ours.
            <details
              className="data-table-card__expansion"
              data-testid="data-table-card-expansion"
              open={expansion.isExpanded(row)}
            >
              <summary className="touch-target">{expansion.cardSummary(row)}</summary>
              {expansion.render(row, 'card')}
            </details>
          )}
        </li>
      ))}
    </ul>
  );
}

export function DataTableSortButton({
  direction,
  onClick,
  children,
}: DataTableSortButtonProps): JSX.Element {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="data-table__sort-button"
      data-sort-direction={direction ?? 'none'}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

function getNavigableRows(table: HTMLTableElement | null): HTMLTableRowElement[] {
  if (table === null) return [];
  // The descriptor form interleaves non-interactive overflow / expansion rows
  // after each data row; neither may become a roving-tabindex stop.
  return Array.from(
    table.querySelectorAll<HTMLTableRowElement>(
      'tbody tr:not(.data-table__overflow-row):not(.data-table__expansion-row)',
    ),
  );
}

function isInteractiveElement(target: EventTarget): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.closest('input, select, textarea, button, a, [contenteditable="true"]') !== null;
}

export default DataTable;
