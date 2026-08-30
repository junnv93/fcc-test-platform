import { useMutation } from '@tanstack/react-query';

import {
  exportSampleInventory,
  type SampleInventoryFilters,
  type SampleInventoryStatusFilter,
} from '@/api/platform-client';
import { useT } from '@/i18n';
import { Button, describeApiError, ErrorState } from '@/ui';

export interface SampleExportActionsProps {
  readonly projectId: string;
  readonly team?: string;
  readonly status?: SampleInventoryStatusFilter;
  readonly asOf?: string;
  readonly includeDeleted?: boolean;
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function SampleExportActions({
  projectId,
  team,
  status,
  asOf,
  includeDeleted,
}: SampleExportActionsProps): JSX.Element {
  const { t } = useT();
  const filters: Omit<SampleInventoryFilters, 'projectId' | 'after' | 'limit'> = {
    ...(team === undefined ? {} : { team }),
    ...(status === undefined ? {} : { status }),
    ...(asOf === undefined ? {} : { asOf }),
    ...(includeDeleted === undefined ? {} : { includeDeleted }),
  };
  const exportMutation = useMutation({
    mutationFn: (template: 'pm-status' | 'rf-data') =>
      exportSampleInventory(projectId, template, filters),
    onSuccess: (download) => saveBlob(download.blob, download.filename),
  });

  const disabled = projectId === '' || exportMutation.isPending;
  return (
    <section className="sample-export-actions" aria-labelledby="sample-export-heading">
      <h3 id="sample-export-heading">{t('routes.sampleInventory.export.title')}</h3>
      <div className="sample-export-actions__buttons">
        <Button
          type="button"
          onClick={() => exportMutation.mutate('pm-status')}
          disabled={disabled}
          loading={exportMutation.isPending && exportMutation.variables === 'pm-status'}
          data-testid="sample-export-pm"
        >
          {t('routes.sampleInventory.export.pm')}
        </Button>
        <Button
          type="button"
          onClick={() => exportMutation.mutate('rf-data')}
          disabled={disabled}
          loading={exportMutation.isPending && exportMutation.variables === 'rf-data'}
          data-testid="sample-export-rf"
        >
          {t('routes.sampleInventory.export.rf')}
        </Button>
      </div>
      <p className="section-hint">{t('routes.sampleInventory.export.hint')}</p>
      {exportMutation.isError && (
        <ErrorState
          testId="sample-export-error"
          message={describeApiError(exportMutation.error, 'platform', {
            default: t('routes.sampleInventory.export.failed'),
          })}
        />
      )}
    </section>
  );
}

export default SampleExportActions;
