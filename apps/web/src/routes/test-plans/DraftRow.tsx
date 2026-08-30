import { useT } from '@/i18n';
import { Button, draftStatusKind, StatusBadge } from '@/ui';

import { draftStatusLabel } from './status';
import { orDash } from './util';

/**
 * One draft row in the workbench list panel. The row is a SELECTOR only — its
 * action is "open this draft in the editor + readiness rail". Publish lives in
 * the readiness rail (`DraftReadinessPanel`), not per-row, so there is exactly
 * one publish definition co-located with the readiness context for the selected
 * draft (Phase 3).
 */
export function DraftRow({
  draftId,
  status,
  rowCount,
  updatedAt,
  selected,
  onSelect,
}: {
  readonly draftId: string;
  readonly status: string;
  readonly rowCount: number;
  readonly updatedAt: string | null | undefined;
  readonly selected: boolean;
  readonly onSelect: () => void;
}): JSX.Element {
  const { t } = useT();

  return (
    <tr data-testid="test-plans-row" data-selected={selected ? 'true' : undefined}>
      <th scope="row">
        <code>{orDash(draftId)}</code>
      </th>
      <td>
        <StatusBadge
          status={draftStatusKind(status)}
          label={draftStatusLabel(t, status)}
          testId="test-plans-status"
        />
      </td>
      <td className="data-cell-numeric">{rowCount}</td>
      <td>{orDash(updatedAt)}</td>
      <td>
        <Button
          type="button"
          variant="ghost"
          data-testid="test-plans-detail-button"
          aria-pressed={selected}
          onClick={onSelect}
        >
          {t('routes.testPlans.detailButton')}
        </Button>
      </td>
    </tr>
  );
}
