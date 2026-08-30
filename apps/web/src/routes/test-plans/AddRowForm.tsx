import { useRef, useState } from 'react';

import { type components } from '@/api/generated/headless-api.types';
import { addTestPlanDraftRow } from '@/api/headless-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { useOptimisticMutation } from '@/shared/use-optimistic-mutation';
import { Button, describeApiError, ErrorState, FieldGroup, SectionBand, Toolbar } from '@/ui';

import { parseCapabilityPath, trimToNull } from './util';

type DraftListView = components['schemas']['ListTestPlanDraftsResponse'];
type DraftRowView = components['schemas']['TestPlanDraftRowView'];
type DraftView = components['schemas']['TestPlanDraftView'];
type AddRowRequest = components['schemas']['AddTestPlanDraftRowRequest'];
interface AddRowResponse {
  readonly draft_row_id: number;
}

function buildOptimisticRow(draftRowId: number, body: AddRowRequest): DraftRowView {
  return {
    draft_row_id: draftRowId,
    capability_path: body.capability_path,
    origin: 'manual',
    derived_kind: null,
    generation_key: null,
    scope_revision: null,
    ...(body.test_type !== undefined ? { test_type: body.test_type } : {}),
    ...(body.mode_family !== undefined ? { mode_family: body.mode_family } : {}),
    ...(body.antenna !== undefined ? { antenna: body.antenna } : {}),
    ...(body.tone !== undefined ? { tone: body.tone } : {}),
    ...(body.location !== undefined ? { location: body.location } : {}),
  };
}

/**
 * Add a manual test-item row to an editable (DRAFT) draft. `capability_path` is
 * required (slash-separated → non-empty segments); the structural facets
 * (test type / mode / antenna / tone / location) are optional and sent as null
 * when blank. On success the draft detail AND the drafts list invalidate (the
 * summary `row_count` grows) and the form clears so the operator can add the
 * next row. The submit is disabled until the capability path parses non-empty.
 */
export function AddRowForm({
  projectId,
  draftId,
  capabilityPathSuggestions,
}: {
  readonly projectId: string;
  readonly draftId: string;
  readonly capabilityPathSuggestions: readonly string[];
}): JSX.Element {
  const { t } = useT();
  const [path, setPath] = useState('');
  const [testType, setTestType] = useState('');
  const [modeFamily, setModeFamily] = useState('');
  const [antenna, setAntenna] = useState('');
  const [tone, setTone] = useState('');
  const [location, setLocation] = useState('');
  const nextOptimisticRowId = useRef(-1);

  const segments = parseCapabilityPath(path);
  const canSubmit = segments.length > 0;
  const detailKey = queryKeys.testPlans.draft(projectId, draftId);
  const draftsKey = queryKeys.testPlans.drafts(projectId);
  const pathListId = `test-plans-add-row-path-suggestions-${draftId}`;

  const addMutation = useOptimisticMutation<
    AddRowResponse,
    { body: AddRowRequest; optimisticRow: DraftRowView },
    DraftView
  >({
    mutationFn: async ({ body }) => {
      return addTestPlanDraftRow(projectId, draftId, body);
    },
    queryKey: detailKey,
    optimisticUpdate: (current, { optimisticRow }) =>
      current === undefined ? current : { ...current, rows: [...current.rows, optimisticRow] },
    extraOptimisticUpdates: [
      {
        queryKey: draftsKey,
        optimisticUpdate: (current) => {
          const page = current as DraftListView | undefined;
          if (page === undefined) return page;
          return {
            ...page,
            drafts: page.drafts.map((draft) =>
              draft.draft_id === draftId ? { ...draft, row_count: draft.row_count + 1 } : draft,
            ),
          };
        },
      },
    ],
    invalidateKeys: [detailKey, draftsKey],
    onSuccess: () => {
      setPath('');
      setTestType('');
      setModeFamily('');
      setAntenna('');
      setTone('');
      setLocation('');
    },
  });

  return (
    <section aria-labelledby="test-plans-add-row-heading">
      <SectionBand
        title={t('routes.testPlans.sectionAddRow')}
        titleId="test-plans-add-row-heading"
      />
      <form
        data-testid="test-plans-add-row-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!canSubmit) return;
          const body: AddRowRequest = {
            capability_path: segments,
            test_type: trimToNull(testType),
            mode_family: trimToNull(modeFamily),
            antenna: trimToNull(antenna),
            tone: trimToNull(tone),
            location: trimToNull(location),
          };
          const optimisticRow = buildOptimisticRow(nextOptimisticRowId.current, body);
          nextOptimisticRowId.current -= 1;
          addMutation.mutate({ body, optimisticRow });
        }}
      >
        <Toolbar ariaLabel={t('routes.testPlans.sectionAddRow')}>
          <FieldGroup
            label={t('routes.testPlans.addRowPathLabel')}
            htmlFor="test-plans-add-row-path"
          >
            <input
              id="test-plans-add-row-path"
              data-testid="test-plans-add-row-path"
              value={path}
              list={capabilityPathSuggestions.length > 0 ? pathListId : undefined}
              placeholder={t('routes.testPlans.addRowPathPlaceholder')}
              onChange={(e) => setPath(e.target.value)}
            />
            {capabilityPathSuggestions.length > 0 && (
              <datalist id={pathListId} data-testid="test-plans-add-row-path-suggestions">
                {capabilityPathSuggestions.map((suggestion) => (
                  <option key={suggestion} value={suggestion} />
                ))}
              </datalist>
            )}
          </FieldGroup>
          <FieldGroup
            label={t('routes.testPlans.addRowTestTypeLabel')}
            htmlFor="test-plans-add-row-test-type"
          >
            <input
              id="test-plans-add-row-test-type"
              data-testid="test-plans-add-row-test-type"
              value={testType}
              onChange={(e) => setTestType(e.target.value)}
            />
          </FieldGroup>
          <FieldGroup
            label={t('routes.testPlans.addRowModeFamilyLabel')}
            htmlFor="test-plans-add-row-mode-family"
          >
            <input
              id="test-plans-add-row-mode-family"
              data-testid="test-plans-add-row-mode-family"
              value={modeFamily}
              onChange={(e) => setModeFamily(e.target.value)}
            />
          </FieldGroup>
          <FieldGroup
            label={t('routes.testPlans.addRowAntennaLabel')}
            htmlFor="test-plans-add-row-antenna"
          >
            <input
              id="test-plans-add-row-antenna"
              data-testid="test-plans-add-row-antenna"
              value={antenna}
              onChange={(e) => setAntenna(e.target.value)}
            />
          </FieldGroup>
          <FieldGroup
            label={t('routes.testPlans.addRowToneLabel')}
            htmlFor="test-plans-add-row-tone"
          >
            <input
              id="test-plans-add-row-tone"
              data-testid="test-plans-add-row-tone"
              value={tone}
              onChange={(e) => setTone(e.target.value)}
            />
          </FieldGroup>
          <FieldGroup
            label={t('routes.testPlans.addRowLocationLabel')}
            htmlFor="test-plans-add-row-location"
          >
            <input
              id="test-plans-add-row-location"
              data-testid="test-plans-add-row-location"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
            />
          </FieldGroup>
          <Button
            type="submit"
            variant="primary"
            data-testid="test-plans-add-row-submit"
            disabled={!canSubmit || addMutation.isPending}
          >
            {addMutation.isPending
              ? t('routes.testPlans.addRowBusy')
              : t('routes.testPlans.addRowSubmit')}
          </Button>
        </Toolbar>
      </form>
      {addMutation.isError && (
        <ErrorState
          testId="test-plans-add-row-error"
          message={describeApiError(addMutation.error, 'headless', {
            forbidden: t('routes.testPlans.addRowForbidden'),
            conflict: t('routes.testPlans.addRowConflict'),
            notFound: t('routes.testPlans.addRowNotFound'),
            network: t('routes.testPlans.addRowNetwork'),
            default: t('routes.testPlans.addRowInvalid'),
          })}
        />
      )}
    </section>
  );
}
