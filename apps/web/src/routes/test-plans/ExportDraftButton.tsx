import { useMutation } from '@tanstack/react-query';

import { exportTestPlanDraft } from '@/api/headless-client';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { Button, describeApiError, ErrorState, Toolbar } from '@/ui';

/**
 * Download the open draft as an Excel (.xlsx) workbook. The export route is
 * RBAC-gated (test_plan:read — the same token required to view the draft), so
 * unlike the presigned report download we cannot navigate a bare URL; the
 * browser MUST attach the auth header. We therefore fetch via openapi-fetch with
 * `parseAs: 'blob'` and trigger a client-side Blob download
 * (createObjectURL → anchor click → revokeObjectURL).
 *
 * The filename prefers the server-sent `Content-Disposition` value and falls
 * back to a deterministic `test-plan-{draftId}.xlsx` (the same name the service
 * derives). A download is an explicit user action, so this uses a `useMutation`
 * (not a query). Errors render through the shared `describeApiError` RFC9457
 * taxonomy (forbidden / notFound / network / default).
 *
 * The header parsing moved to `@/shared/content-disposition` when the
 * measurement-result export gained a second download button (2026-08-13): the
 * private version here read only the ASCII `filename=` parameter, so copying it
 * would have copied that blind spot. Behaviour for ASCII names is unchanged.
 */
export function ExportDraftButton({
  projectId,
  draftId,
}: {
  readonly projectId: string;
  readonly draftId: string;
}): JSX.Element {
  const { t } = useT();

  const exportMutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      const { blob, filename } = await exportTestPlanDraft(
        projectId,
        draftId,
        `test-plan-${draftId}.xlsx`,
      );
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = filename;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
      } finally {
        URL.revokeObjectURL(url);
      }
    },
  });

  return (
    <Toolbar ariaLabel={t('routes.testPlans.exportButton')}>
      <Button
        type="button"
        variant="secondary"
        data-testid="test-plans-export"
        disabled={exportMutation.isPending}
        onClick={() => exportMutation.mutate()}
      >
        {exportMutation.isPending
          ? t('routes.testPlans.exportBusy')
          : t('routes.testPlans.exportButton')}
      </Button>
      {exportMutation.isError && (
        <ErrorState
          testId="test-plans-export-error"
          message={describeApiError(exportMutation.error, 'headless', {
            forbidden: t('routes.testPlans.exportForbidden'),
            notFound: t('routes.testPlans.exportNotFound'),
            network: t('routes.testPlans.exportNetwork'),
            default: t('routes.testPlans.exportFailed'),
          })}
        />
      )}
    </Toolbar>
  );
}
