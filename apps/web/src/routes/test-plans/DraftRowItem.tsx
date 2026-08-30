import { type components } from '@/api/generated/headless-api.types';
import { useT } from '@/i18n';
import { Button } from '@/ui';

import { orDash } from './util';

type DraftRow = components['schemas']['TestPlanDraftRowView'];

/**
 * One draft test-item row + a manage cell — DISPLAY ONLY (W2-C M4).
 *
 * This component used to own a `useMutation` per row, which meant a 16,000-row
 * draft created 16,000 mutation observers (each with its own cache subscription
 * and its own re-render path) before a single row was ever removed. The mutation
 * now lives once in {@link DraftDetail}.
 *
 * The original per-row mutation was a deliberate ISOLATION choice — "removing
 * one row's busy/error state never bleeds into siblings" — and hoisting it
 * naively would have made one row's failure light up every row. That property is
 * preserved instead by ATTRIBUTION: the parent passes the id it is currently
 * mutating (`pendingRowId`) so exactly one row reads as busy, and the failure is
 * reported against the specific row that failed. The isolation is now a fact
 * about the data rather than a side effect of how many hooks exist.
 */
export interface DraftRowActions {
  readonly editable: boolean;
  /** The row id whose removal is in flight (`null` when idle) — exactly one. */
  readonly pendingRowId: number | null;
  readonly onRemove: (rowId: number) => void;
}

/**
 * The eight display values of a draft row, in column order.
 *
 * A single SSOT consumed by BOTH render paths (the `<table>` below the
 * virtualization threshold and the windowed div-table above it). Without it the
 * two paths would each spell out the column projection and could silently
 * disagree about what a row shows — the same class of drift the shared
 * `VirtualizedTable` primitive exists to prevent for row chrome.
 */
export function draftRowCellValues(row: DraftRow): readonly string[] {
  return [
    String(row.draft_row_id),
    orDash(row.capability_path.join(' / ')),
    orDash(row.test_type),
    orDash(row.mode_family),
    orDash(row.antenna),
    orDash(row.tone),
    orDash(row.location),
    orDash(row.origin),
  ];
}

/** Label for the per-row remove control, given whether this row is the one in
 *  flight. Shared by both render paths so the busy wording cannot drift. */
export function removeRowLabel(t: ReturnType<typeof useT>['t'], pending: boolean): string {
  return pending ? t('routes.testPlans.removeRowBusy') : t('routes.testPlans.removeRowButton');
}

/** `<tr>` projection — the non-virtualized path (small drafts). */
export function DraftRowItem({
  row,
  actions,
}: {
  readonly row: DraftRow;
  readonly actions: DraftRowActions;
}): JSX.Element {
  const { t } = useT();
  const [rowId, ...rest] = draftRowCellValues(row);
  const pending = actions.pendingRowId === row.draft_row_id;

  return (
    <tr data-testid="test-plans-detail-row">
      <th scope="row" className="data-cell-numeric">
        {rowId}
      </th>
      {/* Column order is fixed by `draftRowCellValues`, so the index IS the
          column identity here — a stable key by construction. */}
      {rest.map((value, index) => (
        <td key={index}>{value}</td>
      ))}
      <td>
        {!actions.editable ? (
          <span>—</span>
        ) : (
          <Button
            type="button"
            variant="danger"
            data-testid="test-plans-remove-row"
            disabled={actions.pendingRowId !== null}
            onClick={() => actions.onRemove(row.draft_row_id)}
          >
            {removeRowLabel(t, pending)}
          </Button>
        )}
      </td>
    </tr>
  );
}
