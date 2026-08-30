/**
 * DataTableSkeleton — loading placeholder for a DataTable.
 *
 * Phase 1 §5.1 primitive (FD-P1-3 perceived performance). A large session
 * load can take a beat; rendering text ("불러오는 중...") flashes a layout
 * jump when the table arrives. The skeleton reserves the row height
 * (`--row-height`) and shimmer animates within `--motion-emphasis` (collapsed
 * to 0 under `prefers-reduced-motion`).
 */
import { useT } from '@/i18n';

export interface DataTableSkeletonProps {
  /** Column count — determines the grid template of each placeholder row. */
  readonly columns: number;
  /** Row count — typically 5–10 to communicate "table coming" without
   *  reserving an unbounded scrolling area. */
  readonly rows: number;
}

export function DataTableSkeleton({ columns, rows }: DataTableSkeletonProps): JSX.Element {
  const { t } = useT();
  const gridTemplateColumns = `repeat(${columns}, 1fr)`;
  return (
    <div
      className="data-table-skeleton"
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={t('ui.dataTableSkeleton.ariaLabel')}
      data-testid="data-table-skeleton"
    >
      {Array.from({ length: rows }).map((_, rowIdx) => (
        <div
          key={rowIdx}
          className="data-table-skeleton__row"
          style={{ gridTemplateColumns }}
          data-testid="data-table-skeleton-row"
        >
          {Array.from({ length: columns }).map((__, colIdx) => (
            <span
              key={colIdx}
              className="data-table-skeleton__cell"
              data-testid="data-table-skeleton-cell"
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export default DataTableSkeleton;
