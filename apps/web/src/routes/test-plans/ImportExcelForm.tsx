import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';

import { importTestPlanWorkbook } from '@/api/headless-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import {
  DataTable,
  Button,
  describeApiError,
  ErrorState,
  FieldGroup,
  SectionBand,
  StatusMessage,
  Toolbar,
} from '@/ui';

import type { components } from '@/api/generated/headless-api.types';

type ImportResponse = components['schemas']['TestPlanImportResponse'];

/**
 * Import a test-plan from an uploaded Excel workbook (Phase 4 L5). The platform
 * only uploads + records the outcome; the provider owns the parse/capability
 * mapping (ADR-0010). On success the drafts list AND the publications list
 * invalidate (a new IMPORTED draft appears). The honest audit accounting +
 * blocking issues + observable exclusions are surfaced so the operator sees
 * exactly what was/wasn't imported (no proxy completion).
 *
 * The endpoint is multipart/form-data: openapi-fetch serializes the File into a
 * FormData body via `bodySerializer` (the generated type models the field as a
 * binary string, so the File is cast through it).
 */
export function ImportExcelForm({ projectId }: { readonly projectId: string }): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const importMutation = useMutation<ImportResponse, ApiError, File>({
    mutationFn: async (upload: File) => {
      return importTestPlanWorkbook(projectId, upload);
    },
    onSuccess: (data) => {
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.drafts(projectId),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.publications(projectId),
      });
      // Only clear the picked file when a draft was actually created. On a total
      // rejection (draft_id === null — every row excluded) keep the file so the
      // operator can fix the workbook and re-upload without re-picking it.
      if (data.draft_id) {
        setFile(null);
        if (inputRef.current) inputRef.current.value = '';
      }
    },
  });

  const result = importMutation.data;
  // Keep the picker name aligned with the section's operator action. The
  // selected filename remains the status/help text; the label must explain
  // that this workbook is being imported as a test-plan draft.
  const fileLabel = t('routes.testPlans.sectionImport');
  const filePresentation =
    file === null ? fileLabel : `${fileLabel}: ${file.name} (${file.size} B)`;

  return (
    <section aria-labelledby="test-plans-import-heading" data-testid="test-plans-import">
      <SectionBand
        title={t('routes.testPlans.sectionImport')}
        titleId="test-plans-import-heading"
      />
      <p className="section-hint">{t('routes.testPlans.importSectionDescription')}</p>
      <form
        data-testid="test-plans-import-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (file) importMutation.mutate(file);
        }}
      >
        <Toolbar ariaLabel={t('routes.testPlans.sectionImport')}>
          <FieldGroup
            label={fileLabel}
            htmlFor="test-plans-import-file"
            help={filePresentation}
            helpTestId="test-plans-import-file-status"
          >
            <input
              id="test-plans-import-file"
              className="sr-only"
              data-testid="test-plans-import-file"
              ref={inputRef}
              type="file"
              accept=".xlsx,.xlsm,.xls"
              disabled={importMutation.isPending}
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <Button
              type="button"
              variant="secondary"
              data-testid="test-plans-import-file-picker"
              aria-controls="test-plans-import-file"
              data-file-state={file === null ? 'empty' : 'selected'}
              disabled={importMutation.isPending}
              onClick={() => inputRef.current?.click()}
            >
              {fileLabel}
            </Button>
          </FieldGroup>
          <Button
            type="submit"
            variant="primary"
            data-testid="test-plans-import-submit"
            disabled={file === null || importMutation.isPending}
          >
            {importMutation.isPending
              ? t('routes.testPlans.importBusy')
              : t('routes.testPlans.importSubmit')}
          </Button>
        </Toolbar>
      </form>

      {importMutation.isError && (
        <ErrorState
          testId="test-plans-import-error"
          message={describeApiError(importMutation.error, 'headless', {
            forbidden: t('routes.testPlans.importForbidden'),
            network: t('routes.testPlans.importNetwork'),
            // 422 (unprocessable workbook) shares the default arm — the shared
            // errors.ts taxonomy owns status→arm mapping (no per-route branching).
            default: t('routes.testPlans.importFailed'),
          })}
        />
      )}

      {result && (
        <div data-testid="test-plans-import-result">
          <StatusMessage
            testId="test-plans-import-status"
            tone={result.draft_id ? 'success' : 'info'}
            message={
              result.draft_id
                ? t('routes.testPlans.importSuccessDraft', {
                    draft: result.draft_id,
                    accepted: String(result.audit.accepted_count),
                  })
                : t('routes.testPlans.importSuccessNoDraft', {
                    issues: String(result.audit.issue_count),
                    excluded: String(result.audit.excluded_count),
                  })
            }
          />
          <p data-testid="test-plans-import-audit" className="section-hint">
            {t('routes.testPlans.importAuditSummary', {
              raw: String(result.audit.raw_row_count),
              accepted: String(result.audit.accepted_count),
              issues: String(result.audit.issue_count),
              excluded: String(result.audit.excluded_count),
              legend: String(result.audit.legend_skipped_count),
            })}
          </p>

          {result.issues.length > 0 && (
            <DataTable
              testId="test-plans-import-issues"
              caption={t('routes.testPlans.importIssuesCaption')}
              head={
                <thead>
                  <tr>
                    <th scope="col">{t('routes.testPlans.colImportRow')}</th>
                    <th scope="col">{t('routes.testPlans.colImportField')}</th>
                    <th scope="col">{t('routes.testPlans.colImportMessage')}</th>
                  </tr>
                </thead>
              }
              body={
                <tbody>
                  {result.issues.map((issue, idx) => (
                    <tr key={`${issue.row_number}-${idx}`}>
                      <td>{issue.row_number}</td>
                      <td>{issue.field}</td>
                      <td>{issue.message}</td>
                    </tr>
                  ))}
                </tbody>
              }
            />
          )}

          {result.excluded.length > 0 && (
            <DataTable
              testId="test-plans-import-excluded"
              caption={t('routes.testPlans.importExcludedCaption')}
              head={
                <thead>
                  <tr>
                    <th scope="col">{t('routes.testPlans.colImportRow')}</th>
                    <th scope="col">{t('routes.testPlans.colImportReason')}</th>
                    <th scope="col">{t('routes.testPlans.colImportDetail')}</th>
                  </tr>
                </thead>
              }
              body={
                <tbody>
                  {result.excluded.map((row, idx) => (
                    <tr key={`${row.row_number}-${idx}`}>
                      <td>{row.row_number}</td>
                      <td>{row.reason}</td>
                      <td>{row.detail}</td>
                    </tr>
                  ))}
                </tbody>
              }
            />
          )}
        </div>
      )}
    </section>
  );
}
