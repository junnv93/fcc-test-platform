/**
 * UI primitives barrel — Phase 1 (fe-phase1-ui-foundation, 2026-05-30).
 *
 * Routes import from `@/ui` to compose Phase 2's rework on a single source
 * of typography + spacing + status tokens. Adding a primitive requires
 *   1. a file in `src/ui/`,
 *   2. an export here,
 *   3. matching CSS class(es) in `global.css` (token-driven, no inline hex),
 *   4. a vitest render test under `tests/ui/`.
 * Test #4 is sealed by `tests/test_fe_phase1_ui_foundation.py` (M2.2).
 */

export { PageHeader } from './PageHeader';
export type { PageHeaderProps } from './PageHeader';

export { Button } from './Button';
export type { ButtonProps, ButtonSize, ButtonVariant } from './Button';

export { Card } from './Card';
export type { CardProps } from './Card';

export { WorkbenchLayout } from './WorkbenchLayout';
export type { WorkbenchLayoutProps } from './WorkbenchLayout';

export { Toolbar } from './Toolbar';
export type { ToolbarProps } from './Toolbar';

export { FieldGroup } from './FieldGroup';
export type { FieldGroupProps } from './FieldGroup';

export {
  ASSERTIVE_LIVE_REGION_KINDS,
  LIVE_REGION_KINDS,
  LIVE_REGION_RULINGS,
  liveRegionProps,
} from './live-region';
export type {
  LiveRegionAttributes,
  LiveRegionKind,
  LiveRegionRole,
  LiveRegionRuling,
  LiveRegionUrgency,
} from './live-region';

export { StatusBadge, STATUS_KINDS } from './StatusBadge';
export type { StatusBadgeProps, StatusKind } from './StatusBadge';

export { MetricStrip } from './MetricStrip';
export type { MetricStripItem, MetricStripProps } from './MetricStrip';

export { ProgressBar } from './ProgressBar';
export type { ProgressBarProps, ProgressTone } from './ProgressBar';

export { DataTable, DataTableSortButton } from './DataTable';
export type {
  DataTableColumn,
  DataTableColumnPriority,
  DataTableDescriptorProps,
  DataTableExpansion,
  DataTableProps,
  DataTableSlotProps,
  DataTableSort,
  DataTableSortButtonProps,
  DataTableSurface,
} from './DataTable';

export { VirtualizedTable } from './VirtualizedTable';
export type { VirtualizedTableProps, VirtualizedTableRow } from './VirtualizedTable';

export { DensityToggle } from './DensityToggle';

export { ShortcutHelp } from './ShortcutHelp';
export type { ShortcutHelpProps } from './ShortcutHelp';

export { DataTableSkeleton } from './DataTableSkeleton';
export type { DataTableSkeletonProps } from './DataTableSkeleton';

export { EmptyState } from './EmptyState';
export type { EmptyStateProps } from './EmptyState';

export { ErrorState } from './ErrorState';
export type { ErrorStateProps } from './ErrorState';

export { ERROR_VARIANTS, ERROR_VARIANT_CONTRACT } from './error-variants';
export type { ErrorVariant, ErrorVariantContract } from './error-variants';

export type { Translate } from './translate';

export { SECTION_HEADING_LEVEL, STATE_HEADING_LEVEL } from './heading-levels';
export type { StateHeadingLevel } from './heading-levels';

export { BlockSkeleton } from './BlockSkeleton';
export type { BlockSkeletonProps } from './BlockSkeleton';

export { RefetchRegion } from './RefetchRegion';
export type { RefetchRegionProps } from './RefetchRegion';

export { RunProgress } from './RunProgress';
export type { RunProgressProps } from './RunProgress';

export { StatusMessage } from './StatusMessage';
export type { StatusMessageProps } from './StatusMessage';

export { LoadMoreButton } from './LoadMoreButton';
export type { LoadMoreButtonProps } from './LoadMoreButton';

export { MeasurementValueCell, splitValueUnit } from './MeasurementValueCell';
export type { MeasurementValueCellProps } from './MeasurementValueCell';

export { NumericLookupForm } from './NumericLookupForm';
export type { NumericLookupFormProps } from './NumericLookupForm';

export { ProjectPicker } from './ProjectPicker';
export type { ProjectPickerOption, ProjectPickerProps, ProjectPickerSearch } from './ProjectPicker';

export { SectionBand } from './SectionBand';
export type { SectionBandProps } from './SectionBand';

export { SampleCard } from './SampleCard';
export type { SampleCardProps, SampleCardSample } from './SampleCard';

export { describeApiError } from './errors';
export type { ApiErrorContext, ApiErrorOverrides } from './errors';

export {
  chamberStatusKind,
  draftStatusKind,
  featureStatusKind,
  featureStatusLabelToken,
  isKnownChamberStatus,
  jobStatusToStatusKind,
  KNOWN_CHAMBER_STATUSES,
  projectStatusKind,
  QUEUE_STATUS_TOKENS,
  queueStatusLabelToken,
  streamStatusKind,
  streamStatusLabelToken,
  verdictToStatusKind,
} from './status-mapping';
export type { StreamStatus } from './status-mapping';
