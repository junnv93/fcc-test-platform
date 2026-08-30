import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { type components } from '@/api/generated/headless-api.types';
import { replaceTestPlanDraftRows } from '@/api/headless-client';
import { queryKeys } from '@/api/query-config';
import { clientOriginatedApiError } from '@/api/to-api-error';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { Button, describeApiError, ErrorState, FieldGroup, StatusMessage, Toolbar } from '@/ui';

import { orDash } from './util';

type DraftRow = components['schemas']['TestPlanDraftRowView'];
type AddRowRequest = components['schemas']['AddTestPlanDraftRowRequest'];

const CSV_HEADER = 'capability_path,test_type,mode_family,antenna,tone,location';

function escapeCsv(value: string | null | undefined): string {
  const text = value ?? '';
  return /[",\n\r]/u.test(text) ? `"${text.replace(/"/gu, '""')}"` : text;
}

function rowsToCsv(rows: readonly DraftRow[]): string {
  return [
    CSV_HEADER,
    ...rows.map((row) =>
      [
        row.capability_path.join(' / '),
        row.test_type,
        row.mode_family,
        row.antenna,
        row.tone,
        row.location,
      ]
        .map(escapeCsv)
        .join(','),
    ),
  ].join('\n');
}

function splitCsvLine(line: string): string[] {
  const values: string[] = [];
  let current = '';
  let quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index];
    const next = line[index + 1];
    if (char === '"' && quoted && next === '"') {
      current += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === ',' && !quoted) {
      values.push(current.trim());
      current = '';
    } else {
      current += char;
    }
  }
  values.push(current.trim());
  return values;
}

function blankToNull(value: string | undefined): string | null {
  const trimmed = (value ?? '').trim();
  return trimmed === '' ? null : trimmed;
}

function parsePath(value: string | undefined): string[] {
  return (value ?? '')
    .split('/')
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

function parseRows(csv: string): AddRowRequest[] {
  const lines = csv
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
  const dataLines =
    lines[0]?.toLowerCase().replace(/\s+/gu, '') === CSV_HEADER ? lines.slice(1) : lines;
  return dataLines.map((line) => {
    const [path, testType, modeFamily, antenna, tone, location] = splitCsvLine(line);
    return {
      capability_path: parsePath(path),
      test_type: blankToNull(testType),
      mode_family: blankToNull(modeFamily),
      antenna: blankToNull(antenna),
      tone: blankToNull(tone),
      location: blankToNull(location),
    };
  });
}

export function BulkRowsEditor({
  projectId,
  draftId,
  rows,
  editable,
}: {
  readonly projectId: string;
  readonly draftId: string;
  readonly rows: readonly DraftRow[];
  readonly editable: boolean;
}): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const exportedCsv = useMemo(() => rowsToCsv(rows), [rows]);

  // W2-C M2 — the textarea holds a LOCAL OVERRIDE, not a mirror of the server.
  //
  // `null` means pristine: nothing has been typed, so the editor derives its
  // text from the server rows on every render and stays fresh for free. The
  // moment the operator types, the override wins and no refetch can overwrite
  // it. The prior `useEffect([exportedCsv]) → setCsv(exportedCsv)` had no
  // pristine gate, so any invalidation (add-row, remove-row, publish, another
  // operator's write) silently reverted the operator's text — and because
  // "가져오기" is a PUT replace-all, pressing it afterwards CONFIRMED the
  // reverted content as the whole server row set. That is the contract's
  // "복구 불가 쓰기".
  const [localCsv, setLocalCsv] = useState<string | null>(null);
  // The server text as it stood when this editing session began. Comparing it
  // against `exportedCsv` is what detects "the draft moved under my edit".
  const [editBaseline, setEditBaseline] = useState<string | null>(null);
  // Acknowledgements are stored AS the snapshot they were granted for, never as
  // a bare boolean. A boolean would need an effect to expire when the server
  // moves again (the very anti-pattern this milestone removes); keying by the
  // snapshot makes expiry a pure derivation.
  const [overwriteAckFor, setOverwriteAckFor] = useState<string | null>(null);
  const [wipeAckFor, setWipeAckFor] = useState<string | null>(null);

  const csv = localCsv ?? exportedCsv;
  const pristine = localCsv === null;

  const parsedRows = useMemo(() => parseRows(csv), [csv]);
  const hasInvalidRows = parsedRows.some((row) => row.capability_path.length === 0);

  // The draft changed on the server after this editing session started. The
  // local text deliberately wins on screen, so importing now would replace a row
  // set the operator has never seen.
  const serverMovedUnderEdit = !pristine && editBaseline !== null && editBaseline !== exportedCsv;
  const overwriteAcknowledged = overwriteAckFor === exportedCsv;
  const blockedByStaleBaseline = serverMovedUnderEdit && !overwriteAcknowledged;

  // An empty CSV is a legal PUT body, and the server honours it literally: every
  // row of the draft is deleted in one transaction. One click on a textarea the
  // operator merely cleared would wipe the draft with no undo.
  const wipesAllRows = parsedRows.length === 0 && rows.length > 0;
  const wipeAcknowledged = wipeAckFor === csv;
  const blockedByWipe = wipesAllRows && !wipeAcknowledged;

  const importBlocked = blockedByStaleBaseline || blockedByWipe;

  const replaceMutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      if (hasInvalidRows) {
        throw clientOriginatedApiError('draft bulk import invalid', { status: 400 });
      }
      // Single atomic transaction (PUT replace-all): the server deletes the
      // existing rows + inserts the new set in one transaction, so a partial
      // failure can never lose rows (replaces the prior DELETE-loop + POST-loop,
      // which left the draft half-edited on any mid-loop failure).
      await replaceTestPlanDraftRows(projectId, draftId, parsedRows);
    },
    onSuccess: () => {
      // The local text IS the server state now, so the override has served its
      // purpose. Returning to pristine lets the refetched rows flow back in —
      // without this the editor would keep showing a snapshot that is no longer
      // distinguishable from an unsaved edit.
      setLocalCsv(null);
      setEditBaseline(null);
      setOverwriteAckFor(null);
      setWipeAckFor(null);
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.draft(projectId, draftId),
      });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.testPlans.drafts(projectId),
      });
    },
  });

  /** Drop the local override and follow the server rows again. */
  function revertToServer(): void {
    setLocalCsv(null);
    setEditBaseline(null);
    setOverwriteAckFor(null);
    setWipeAckFor(null);
    replaceMutation.reset();
  }

  function reload(): void {
    replaceMutation.reset();
    void queryClient.invalidateQueries({
      queryKey: queryKeys.testPlans.draft(projectId, draftId),
    });
  }

  return (
    // Phase 4 — bulk CSV is advanced/power-user editing, disclosed behind a
    // native <details> so it never visually competes with normal row inspection
    // (the rows table + add-row form above are the primary editing surface).
    <details className="advanced-disclosure" data-testid="test-plans-bulk-editor">
      <summary className="advanced-disclosure__summary" data-testid="test-plans-bulk-toggle">
        <span className="advanced-disclosure__title">{t('routes.testPlans.sectionBulkEdit')}</span>
        <span className="advanced-disclosure__meta">
          {t('routes.testPlans.bulkRowCount', { rows: parsedRows.length })}
        </span>
      </summary>
      <FieldGroup label={t('routes.testPlans.bulkCsvLabel')} htmlFor="test-plans-bulk-csv">
        <textarea
          id="test-plans-bulk-csv"
          data-testid="test-plans-bulk-csv"
          value={csv}
          rows={Math.min(12, Math.max(5, parsedRows.length + 1))}
          readOnly={!editable}
          onChange={(event) => {
            // First keystroke of an editing session pins the server text the
            // operator started from; later keystrokes must not move the baseline
            // or the drift comparison would always compare against itself.
            if (localCsv === null) setEditBaseline(exportedCsv);
            setLocalCsv(event.currentTarget.value);
            replaceMutation.reset();
          }}
        />
      </FieldGroup>
      {!pristine && (
        <StatusMessage
          tone="info"
          testId="test-plans-bulk-unsaved"
          message={t('routes.testPlans.bulkUnsaved')}
        />
      )}
      <Toolbar ariaLabel={t('routes.testPlans.sectionBulkEdit')}>
        <Button
          type="button"
          variant="secondary"
          data-testid="test-plans-export-csv"
          onClick={revertToServer}
        >
          {t('routes.testPlans.exportCsvButton')}
        </Button>
        <Button
          type="button"
          variant="primary"
          data-testid="test-plans-import-csv"
          disabled={!editable || replaceMutation.isPending || hasInvalidRows || importBlocked}
          onClick={() => replaceMutation.mutate()}
        >
          {replaceMutation.isPending
            ? t('routes.testPlans.importCsvBusy')
            : t('routes.testPlans.importCsvButton')}
        </Button>
        {replaceMutation.isError && (
          <Button
            type="button"
            variant="secondary"
            data-testid="test-plans-reload-detail"
            onClick={reload}
          >
            {t('routes.testPlans.reloadButton')}
          </Button>
        )}
      </Toolbar>
      {/* The draft moved while this text was being edited. The import is held
          until the operator explicitly chooses which side wins — dropping the
          local edit, or overwriting the newer server rows on purpose. */}
      {serverMovedUnderEdit && (
        <div data-testid="test-plans-bulk-stale">
          <StatusMessage
            tone="info"
            testId="test-plans-bulk-stale-notice"
            message={t('routes.testPlans.bulkStaleNotice', { rows: rows.length })}
          />
          <Toolbar ariaLabel={t('routes.testPlans.bulkStaleNotice', { rows: rows.length })}>
            <Button
              type="button"
              variant="ghost"
              data-testid="test-plans-bulk-stale-discard"
              onClick={revertToServer}
            >
              {t('routes.testPlans.bulkStaleDiscard')}
            </Button>
            <Button
              type="button"
              variant="primary"
              data-testid="test-plans-bulk-stale-acknowledge"
              disabled={overwriteAcknowledged}
              onClick={() => setOverwriteAckFor(exportedCsv)}
            >
              {overwriteAcknowledged
                ? t('routes.testPlans.bulkStaleAcknowledged')
                : t('routes.testPlans.bulkStaleAcknowledge')}
            </Button>
          </Toolbar>
        </div>
      )}
      {/* Empty CSV over a non-empty draft = delete every row. Second click required. */}
      {wipesAllRows && (
        <div data-testid="test-plans-bulk-wipe">
          <StatusMessage
            tone="info"
            testId="test-plans-bulk-wipe-notice"
            message={t('routes.testPlans.bulkWipeNotice', { rows: rows.length })}
          />
          <Toolbar ariaLabel={t('routes.testPlans.bulkWipeNotice', { rows: rows.length })}>
            <Button
              type="button"
              variant="danger"
              data-testid="test-plans-bulk-wipe-acknowledge"
              disabled={wipeAcknowledged}
              onClick={() => setWipeAckFor(csv)}
            >
              {wipeAcknowledged
                ? t('routes.testPlans.bulkWipeAcknowledged')
                : t('routes.testPlans.bulkWipeAcknowledge')}
            </Button>
          </Toolbar>
        </div>
      )}
      {hasInvalidRows && (
        <ErrorState testId="test-plans-bulk-invalid" message={t('routes.testPlans.bulkInvalid')} />
      )}
      {replaceMutation.isError && (
        <ErrorState
          testId="test-plans-bulk-error"
          message={describeApiError(replaceMutation.error, 'headless', {
            conflict: t('routes.testPlans.reloadConflict'),
            network: t('routes.testPlans.addRowNetwork'),
            default: t('routes.testPlans.bulkFailed'),
          })}
        />
      )}
      {replaceMutation.isSuccess && (
        <StatusMessage
          tone="success"
          testId="test-plans-bulk-success"
          message={t('routes.testPlans.bulkSuccess', { rows: parsedRows.length })}
        />
      )}
      {/* Round-trip caveat — the CSV projection carries six columns, so an
          import rebuilds every row WITHOUT its server-side provenance
          (origin / derived_kind / generation_key / scope_revision). Preserving
          those through the CSV is tracked as follow-up debt; until then the
          honest move is to say so next to the destructive action rather than to
          let the loss be discovered after the fact. */}
      {editable && (
        <p className="section-hint" data-testid="test-plans-bulk-roundtrip-note">
          {t('routes.testPlans.bulkRoundTripNote')}
        </p>
      )}
      <p data-testid="test-plans-bulk-preview">
        {t('routes.testPlans.bulkPreview', {
          first: orDash(parsedRows[0]?.capability_path.join(' / ')),
        })}
      </p>
    </details>
  );
}
