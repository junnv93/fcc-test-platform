import { useVirtualizer } from '@tanstack/react-virtual';
import { useEffect, useRef, useState } from 'react';

import type { KeyboardEvent, ReactNode } from 'react';

/**
 * VirtualizedTable — shared windowed-table primitive (B1/B2 table-virtualization
 * contract, 2026-06-20).
 *
 * Card B2 originally grew the entire windowing + ARIA-table scaffolding inside
 * `sessions.tsx`, so virtualization behavior was owned by exactly one route and
 * could not be reused (or sealed) anywhere else. This primitive owns that
 * contract once:
 *
 *   - the scroll container as an ARIA table (`role="table"` + `aria-rowcount`),
 *   - the sticky header rowgroup,
 *   - `useVirtualizer` windowing (only on-screen rows mounted, every loaded row
 *     reachable by scrolling — NOT a fixed slice that hides data),
 *   - the total-height spacer + per-row `measureElement` + `translateY` offset,
 *   - the per-row `role="row"` / `aria-rowindex` chrome.
 *
 * The caller stays in control of *content*: it supplies the header cells and,
 * per item, the row's cells + a className/testId modifier via `renderRow`. The
 * primitive never needs to know the column schema, so a route with a different
 * shape (group headers interleaved with rows, N columns) composes it without
 * re-implementing the windowing. `@tanstack/react-virtual` is already a
 * dependency (grid-poc) — no new package.
 */

/** What the caller returns for one windowed row: its cells plus optional
 *  layout/test hooks. The primitive supplies the row chrome (role/aria/offset). */
export interface VirtualizedTableRow {
  /** The row's cell elements (each typically `<span role="cell">…</span>`). */
  readonly cells: ReactNode;
  /** Extra className for layout (e.g. a grid row vs a full-width group band). */
  readonly className?: string;
  /** Stable `data-testid` for the row element. */
  readonly testId?: string;
}

export interface VirtualizedTableProps<TItem> {
  /** Flat list of rows to window. */
  readonly items: readonly TItem[];
  /** Accessible name for the table (no visible `<caption>` in a div-table). */
  readonly ariaLabel: string;
  /** Total row count for `aria-rowcount`. Defaults to `items.length` (all rows
   *  loaded). When the loaded rows are a keyset page of a larger, not-yet-known
   *  total (more pages remain), pass `-1` — the WAI-ARIA value for "total size
   *  unknown" — so assistive tech does not announce the partial count as final. */
  readonly totalRowCount?: number;
  /** Header cells (each typically `<span role="columnheader">…</span>`). */
  readonly header: ReactNode;
  /** Map one item to its row content + hooks. */
  readonly renderRow: (item: TItem, index: number) => VirtualizedTableRow;
  /** Stable React key per item (falls back to the index when omitted). */
  readonly getRowKey?: (item: TItem, index: number) => string;
  /** Seed row height (px) for the initial window; `measureElement` corrects it. */
  readonly estimateRowHeight?: number;
  /** Rows rendered beyond the viewport on each side. */
  readonly overscan?: number;
  /** Extra className on the scroll container (route-specific column grid lives
   *  here, scoped to the primitive's BEM row classes). */
  readonly className?: string;
  /** Extra className on the header row (route-specific column grid + head look). */
  readonly headerClassName?: string;
  /** `data-testid` for the scroll container. */
  readonly testId?: string;
  /** Enable roving row tabindex + Arrow/j/k/Home/End navigation, matching the
   *  {@link DataTable} keyboard contract. Defaults off (a plain scroll table).
   *  Because the list is windowed, navigating to an off-screen row first calls
   *  `scrollToIndex` to mount it, then restores focus — so the keyboard workflow
   *  survives even on the largest datasets where rows are virtualized. */
  readonly keyboardNavigation?: boolean;
  /** Optional row activation for Enter/Space when keyboard navigation is on.
   *  Receives the activated item + its logical index. */
  readonly onRowActivate?: (item: TItem, index: number) => void;
}

const DEFAULT_ESTIMATE_ROW_HEIGHT = 36;
const DEFAULT_OVERSCAN = 12;

function isInteractiveElement(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.closest('input, select, textarea, button, a, [contenteditable="true"]') !== null;
}

export function VirtualizedTable<TItem>({
  items,
  ariaLabel,
  totalRowCount,
  header,
  renderRow,
  getRowKey,
  estimateRowHeight = DEFAULT_ESTIMATE_ROW_HEIGHT,
  overscan = DEFAULT_OVERSCAN,
  className,
  headerClassName,
  testId,
  keyboardNavigation = false,
  onRowActivate,
}: VirtualizedTableProps<TItem>): JSX.Element {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateRowHeight,
    overscan,
  });

  // Roving-tabindex active row (logical index into `items`, not the DOM-mounted
  // subset). Only one row is tabbable (`tabIndex=0`); the rest are `-1`. The
  // first row seeds it so the table is reachable by Tab.
  const [activeIndex, setActiveIndex] = useState(0);
  // When navigation targets an off-screen (un-mounted) row, we scrollToIndex it
  // and remember the index to focus once it mounts on the next render.
  const pendingFocusRef = useRef<number | null>(null);
  const rowCount = items.length;
  const clampedActiveIndex = rowCount === 0 ? 0 : Math.min(activeIndex, rowCount - 1);

  function focusPendingRow(): void {
    const index = pendingFocusRef.current;
    if (index === null) return;
    const row = scrollRef.current?.querySelector(`[data-index="${index}"]`);
    if (row instanceof HTMLElement) {
      row.focus();
      pendingFocusRef.current = null;
    }
  }

  // Runs after every render: once scrollToIndex has mounted the pending row,
  // move focus to it. Clears itself after one successful focus per navigation.
  useEffect(focusPendingRow);

  function moveToRow(nextIndex: number): void {
    const clamped = Math.max(0, Math.min(nextIndex, rowCount - 1));
    setActiveIndex(clamped);
    pendingFocusRef.current = clamped;
    // Pull the target into the window first (no-op if already mounted), then
    // focus — directly if already in the DOM, otherwise via the post-render effect.
    virtualizer.scrollToIndex(clamped);
    focusPendingRow();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>): void {
    if (!keyboardNavigation || rowCount === 0 || isInteractiveElement(event.target)) return;
    const rowEl = (event.target as HTMLElement).closest('[role="row"][data-index]');
    if (!(rowEl instanceof HTMLElement)) return;
    const index = Number(rowEl.getAttribute('data-index'));
    if (!Number.isInteger(index)) return;

    const key = event.key;
    const nextIndex =
      key === 'ArrowDown' || key === 'j'
        ? index + 1
        : key === 'ArrowUp' || key === 'k'
          ? index - 1
          : key === 'Home'
            ? 0
            : key === 'End'
              ? rowCount - 1
              : null;

    if (nextIndex !== null) {
      event.preventDefault();
      moveToRow(nextIndex);
      return;
    }

    if ((key === 'Enter' || key === ' ') && onRowActivate !== undefined) {
      const item = items[index];
      if (item !== undefined) {
        event.preventDefault();
        onRowActivate(item, index);
      }
    }
  }

  const rootClassName = className ? `virtual-table ${className}` : 'virtual-table';
  const headerRowClassName = headerClassName
    ? `virtual-table__row virtual-table__row--head ${headerClassName}`
    : 'virtual-table__row virtual-table__row--head';

  return (
    // The keydown handler implements the roving-tabindex grid-navigation pattern
    // (Arrow/j/k/Home/End across focusable rows) on a legitimately interactive
    // table. It is omitted entirely unless keyboardNavigation is opted in, so the
    // non-interactive-element lint rule is a false positive here.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      ref={scrollRef}
      className={rootClassName}
      role="table"
      aria-rowcount={totalRowCount ?? items.length}
      aria-label={ariaLabel}
      data-testid={testId ?? 'virtual-table'}
      data-keyboard-navigation={keyboardNavigation ? 'true' : undefined}
      onKeyDown={keyboardNavigation ? onKeyDown : undefined}
    >
      <div role="rowgroup" className="virtual-table__head">
        <div role="row" className={headerRowClassName}>
          {header}
        </div>
      </div>
      <div
        role="rowgroup"
        className="virtual-table__body"
        style={{ height: `${virtualizer.getTotalSize()}px` }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const item = items[virtualRow.index];
          if (item === undefined) return null;
          const row = renderRow(item, virtualRow.index);
          const rowClassName = row.className
            ? `virtual-table__virtual-item ${row.className}`
            : 'virtual-table__virtual-item';
          return (
            <div
              key={getRowKey ? getRowKey(item, virtualRow.index) : virtualRow.index}
              ref={virtualizer.measureElement}
              data-index={virtualRow.index}
              role="row"
              aria-rowindex={virtualRow.index + 1}
              tabIndex={
                keyboardNavigation ? (virtualRow.index === clampedActiveIndex ? 0 : -1) : undefined
              }
              className={rowClassName}
              data-testid={row.testId}
              style={{ transform: `translateY(${virtualRow.start}px)` }}
            >
              {row.cells}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default VirtualizedTable;
