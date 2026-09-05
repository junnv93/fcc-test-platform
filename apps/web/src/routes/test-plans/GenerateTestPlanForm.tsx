import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  fetchTestPlanDraftRows,
  fetchTestPlanGenerationCatalogue,
  fetchTestPlanGenerationJob,
  fetchTestPlanGenerationMetadata,
  previewTestPlanGeneration,
  submitTestPlanGeneration,
} from '@/api/headless-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import {
  BlockSkeleton,
  Button,
  describeApiError,
  ErrorState,
  queueStatusLabelToken,
  SectionBand,
  StatusMessage,
} from '@/ui';

import type { components } from '@/api/generated/headless-api.types';

type Catalogue = components['schemas']['TestPlanGenerationCatalogue'];
type CatalogueResponse = components['schemas']['TestPlanGenerationCatalogueResponse'];
type GenerationRequest = components['schemas']['TestPlanGenerationRequest'];
type PreviewResponse = components['schemas']['TestPlanGenerationPreviewResponse'];
type SubmittedResponse = components['schemas']['TestPlanGenerationSubmittedResponse'];
type JobResponse = components['schemas']['TestPlanGenerationJobResponse'];
type MetadataResponse = components['schemas']['TestPlanGenerationMetadataResponse'];
type RowPageResponse = components['schemas']['TestPlanGenerationRowPageResponse'];
type Translator = ReturnType<typeof useT>['t'];

type AxisPicks = Record<string, string[]>;
interface GenerationRecoveryState {
  readonly jobId: string | null;
  readonly idempotencyKey: string;
}

type OperatorLabelState = 'localized' | 'unsupported-catalogue-identity';

interface OperatorLabel {
  readonly text: string;
  readonly state: OperatorLabelState;
}

const GENERATION_RECOVERY_PREFIX = 'fcc:test-plan-generation:';

/**
 * Production catalogue identity → locale key adapter.
 *
 * The server owns the catalogue identities and the locale bundles own the
 * visible copy. This adapter is the only mapping between those contracts; it
 * is deliberately not a second message dictionary. The QA census reads these
 * production call sites and verifies every consumed key in both bundles.
 */
export const GENERATOR_TRANSLATION_KEYS = {
  axis: {
    packets: 'routes.testPlans.generator.axis.packets',
    sub_families: 'routes.testPlans.generator.axis.subFamilies',
    modes: 'routes.testPlans.generator.axis.modes',
    test_types: 'routes.testPlans.colTestType',
    antennas: 'routes.testPlans.colAntenna',
    phys: 'routes.testPlans.generator.axis.phys',
    bandwidths: 'routes.testPlans.generator.axis.bandwidths',
    modulations: 'routes.testPlans.generator.axis.modulations',
    technologies: 'routes.testPlans.generator.axis.technologies',
    bands: 'routes.testPlans.generator.axis.bands',
    channels: 'routes.testPlans.generator.axis.channels',
    tests: 'routes.testPlans.generator.axis.tests',
    bands_per_subfamily: 'routes.testPlans.generator.axis.bandsPerSubfamily',
  },
  stage: {
    label: 'routes.testPlans.generator.stage.label',
    base: 'routes.testPlans.generator.stage.base',
    pretest: 'routes.testPlans.generator.stage.pretest',
    main_test: 'routes.testPlans.generator.stage.mainTest',
  },
  mainSource: {
    legend: 'routes.testPlans.generator.mainSource.legend',
    source_session_id: 'routes.testPlans.generator.mainSource.sourceSessionId',
    selected_channels: 'routes.testPlans.generator.mainSource.selectedChannels',
    worst_decision_snapshot_revision:
      'routes.testPlans.generator.mainSource.worstDecisionSnapshotRevision',
  },
  technology: {
    BT: 'routes.testPlans.generator.technology.bt',
    BLE: 'routes.testPlans.generator.technology.ble',
    DTS: 'routes.testPlans.generator.technology.dts',
    UNII: 'routes.testPlans.generator.technology.unii',
    WLAN: 'routes.testPlans.generator.axis.technologies',
  },
} as const;

/** Fail closed without presenting a generic or raw server identity. */
function unsupportedCatalogueLabel(): OperatorLabel {
  return { text: '', state: 'unsupported-catalogue-identity' };
}

function localizedLabel(text: string): OperatorLabel {
  return { text, state: 'localized' };
}

function operatorAxisLabel(t: Translator, axisName: string): OperatorLabel {
  const key =
    GENERATOR_TRANSLATION_KEYS.axis[axisName as keyof typeof GENERATOR_TRANSLATION_KEYS.axis];
  return key === undefined ? unsupportedCatalogueLabel() : localizedLabel(t(key));
}

function operatorStageLabel(t: Translator): OperatorLabel {
  return localizedLabel(t(GENERATOR_TRANSLATION_KEYS.stage.label));
}

function operatorStageValueLabel(t: Translator, stageName: string): OperatorLabel {
  const key =
    GENERATOR_TRANSLATION_KEYS.stage[stageName as keyof typeof GENERATOR_TRANSLATION_KEYS.stage];
  return key === undefined ? unsupportedCatalogueLabel() : localizedLabel(t(key));
}

function operatorGenerationStatusLabel(t: Translator, status: string): OperatorLabel {
  const normalized = status.trim().toLowerCase();
  const canonicalStatus =
    normalized === 'succeeded' ? 'completed' : normalized === 'submitted' ? 'queued' : normalized;
  return localizedLabel(t(`routes.jobs.counts.${queueStatusLabelToken(canonicalStatus)}`));
}

function operatorTechnologyLabel(t: Translator, technology: string): OperatorLabel {
  const key =
    GENERATOR_TRANSLATION_KEYS.technology[
      technology as keyof typeof GENERATOR_TRANSLATION_KEYS.technology
    ];
  return key === undefined ? unsupportedCatalogueLabel() : localizedLabel(t(key));
}

function operatorMainSourceLabel(t: Translator): OperatorLabel {
  return localizedLabel(t(GENERATOR_TRANSLATION_KEYS.mainSource.legend));
}

function operatorMainFieldLabel(t: Translator, fieldName: string): OperatorLabel {
  const key =
    GENERATOR_TRANSLATION_KEYS.mainSource[
      fieldName as keyof typeof GENERATOR_TRANSLATION_KEYS.mainSource
    ];
  return key === undefined ? unsupportedCatalogueLabel() : localizedLabel(t(key));
}

function generationErrorMessage(error: ApiError | null, t: Translator): string {
  const field = error?.status === 400 ? error.params?.field : undefined;
  if (typeof field === 'string' && field.trim() !== '') {
    const fieldLabel = operatorAxisLabel(t, field.trim());
    if (fieldLabel.state === 'unsupported-catalogue-identity') {
      return t('routes.testPlans.generator.failed');
    }
    return t('errors.badRequestField', {
      field: fieldLabel.text,
    });
  }
  return describeApiError(error, 'headless', {
    forbidden: t('routes.testPlans.generator.forbidden'),
    notFound: t('routes.testPlans.generator.notFound'),
    network: t('routes.testPlans.generator.network'),
    badRequest: t('routes.testPlans.generator.failed'),
    default: t('routes.testPlans.generator.failed'),
  });
}

function isGenerationRecoveryState(value: unknown): value is GenerationRecoveryState {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    (candidate.jobId === null || typeof candidate.jobId === 'string') &&
    typeof candidate.idempotencyKey === 'string'
  );
}

function readGenerationRecovery(projectId: string): GenerationRecoveryState {
  const empty: GenerationRecoveryState = { jobId: null, idempotencyKey: '' };
  if (typeof window === 'undefined') return empty;
  try {
    const raw = window.sessionStorage.getItem(`${GENERATION_RECOVERY_PREFIX}${projectId}`);
    if (raw === null) return empty;
    const parsed: unknown = JSON.parse(raw);
    return isGenerationRecoveryState(parsed) ? parsed : empty;
  } catch {
    return empty;
  }
}

function writeGenerationRecovery(projectId: string, state: GenerationRecoveryState): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(
      `${GENERATION_RECOVERY_PREFIX}${projectId}`,
      JSON.stringify(state),
    );
  } catch {
    // A restricted browser storage context must not break the generation route.
  }
}

function initialPicks(catalogue: Catalogue): AxisPicks {
  return Object.fromEntries(catalogue.axes.map((axis) => [axis.name, axis.values.slice(0, 1)]));
}

function selectedBands(catalogue: Catalogue, picks: AxisPicks): Record<string, string[]> {
  const selectedSubFamilies = picks.sub_families;
  return Object.fromEntries(
    Object.entries(catalogue.bands_per_subfamily).filter(
      ([subFamily]) => selectedSubFamilies === undefined || selectedSubFamilies.includes(subFamily),
    ),
  );
}

function requiredAxis(picks: AxisPicks, name: string): string[] {
  const values = picks[name] ?? [];
  if (values.length === 0) throw new Error(`axis ${name} needs a value`);
  return values;
}

function buildRequest(
  catalogue: Catalogue,
  picks: AxisPicks,
  stage: string,
  mainTest: {
    sourceSessionId: string;
    selectedChannels: string;
    worstDecisionRevision: string;
  },
): GenerationRequest {
  const technology = catalogue.technology;
  const bands_per_subfamily = selectedBands(catalogue, picks);

  if (technology === 'BT') {
    return {
      technology: 'BT',
      packets: requiredAxis(picks, 'packets'),
      modes: requiredAxis(picks, 'modes'),
      test_types: requiredAxis(picks, 'test_types'),
      antennas: requiredAxis(picks, 'antennas'),
      bands_per_subfamily,
    };
  }

  if (technology === 'BLE') {
    return {
      technology: 'BLE',
      sub_families: requiredAxis(picks, 'sub_families'),
      phys: requiredAxis(picks, 'phys'),
      test_types: requiredAxis(picks, 'test_types'),
      antennas: requiredAxis(picks, 'antennas'),
      modulations: requiredAxis(picks, 'modulations'),
      bands_per_subfamily,
    };
  }

  const wlan = {
    technology: 'WLAN' as const,
    stage: stage as 'base' | 'pretest' | 'main_test',
    technologies: requiredAxis(picks, 'technologies'),
    bands: requiredAxis(picks, 'bands'),
    bandwidths: requiredAxis(picks, 'bandwidths'),
    channels: requiredAxis(picks, 'channels'),
    modulations: requiredAxis(picks, 'modulations'),
    tests: requiredAxis(picks, 'tests'),
    antennas: requiredAxis(picks, 'antennas'),
    bands_per_subfamily,
  };
  if (stage === 'main_test') {
    return {
      ...wlan,
      stage: 'main_test',
      source_session_id: mainTest.sourceSessionId.trim(),
      selected_channels: mainTest.selectedChannels
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean),
      worst_decision_snapshot_revision: mainTest.worstDecisionRevision.trim(),
    };
  }
  if (stage === 'pretest') return { ...wlan, stage: 'pretest' };
  return { ...wlan, stage: 'base' };
}

function newIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID();
  return `generation-${Date.now()}`;
}

/**
 * Current provider-neutral generation flow.
 *
 * The generated OpenAPI union is the only request type used here. Axis names,
 * values, stages, bands, and the production/representative revisions all come
 * from the current catalogue/preview responses; this component owns only
 * selection state and async workflow state.
 */
export function GenerateTestPlanForm({
  projectId,
  onGenerated,
}: {
  readonly projectId: string;
  readonly onGenerated: (draftId: string) => void;
}): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const [recoveryState] = useState<GenerationRecoveryState>(() =>
    readGenerationRecovery(projectId),
  );
  const [technology, setTechnology] = useState('');
  const [stage, setStage] = useState('');
  const [picks, setPicks] = useState<AxisPicks>({});
  const [mainTest, setMainTest] = useState({
    sourceSessionId: '',
    selectedChannels: '',
    worstDecisionRevision: '',
  });
  const [activeRequest, setActiveRequest] = useState<GenerationRequest | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [jobId, setJobId] = useState<string | null>(recoveryState.jobId);
  const [rowAfter, setRowAfter] = useState<number | null>(null);
  const [idempotencyKeyValue, setIdempotencyKeyValue] = useState(recoveryState.idempotencyKey);
  const idempotencyKey = useRef(recoveryState.idempotencyKey);
  const openedDraft = useRef<string | null>(null);

  useEffect(() => {
    writeGenerationRecovery(projectId, { jobId, idempotencyKey: idempotencyKeyValue });
  }, [idempotencyKeyValue, jobId, projectId]);

  const catalogueQuery = useQuery({
    queryKey: queryKeys.testPlans.generationCatalogue(),
    ...REFETCH_STRATEGIES.STATIC,
    queryFn: async (): Promise<CatalogueResponse> => {
      return fetchTestPlanGenerationCatalogue();
    },
  });

  const catalogues = useMemo<CatalogueResponse['catalogues']>(
    () => catalogueQuery.data?.catalogues ?? {},
    [catalogueQuery.data?.catalogues],
  );
  const selectedCatalogue = technology === '' ? undefined : catalogues[technology];
  const catalogueHasUnsupportedIdentity = Object.entries(catalogues).some(
    ([catalogueTechnology, catalogue]) =>
      operatorTechnologyLabel(t, catalogueTechnology).state !== 'localized' ||
      catalogue.axes.some((axis) => operatorAxisLabel(t, axis.name).state !== 'localized') ||
      catalogue.stages.some(
        (stageName) => operatorStageValueLabel(t, stageName).state !== 'localized',
      ),
  );

  useEffect(() => {
    const keys = Object.keys(catalogues);
    const firstTechnology = keys[0];
    if (firstTechnology === undefined) return;
    const nextTechnology =
      technology !== '' && catalogues[technology] ? technology : firstTechnology;
    if (nextTechnology !== technology) {
      const nextCatalogue = catalogues[nextTechnology];
      setTechnology(nextTechnology);
      setStage(nextCatalogue?.stages[0] ?? '');
      setPicks(nextCatalogue === undefined ? {} : initialPicks(nextCatalogue));
    }
  }, [catalogues, technology]);

  const blockers = useMemo(() => {
    if (selectedCatalogue === undefined) return [];
    if (catalogueHasUnsupportedIdentity) {
      return [t('routes.testPlans.generator.failed')];
    }
    const missing = selectedCatalogue.axes
      .filter((axis) => (picks[axis.name] ?? []).length === 0)
      .map((axis) => operatorAxisLabel(t, axis.name).text);
    if (Object.keys(selectedBands(selectedCatalogue, picks)).length === 0) {
      missing.push(operatorAxisLabel(t, 'bands_per_subfamily').text);
    }
    if (selectedCatalogue.stages.length > 0 && stage === '') {
      missing.push(operatorStageLabel(t).text);
    }
    if (stage === 'main_test') {
      if (mainTest.sourceSessionId.trim() === '') {
        missing.push(operatorMainFieldLabel(t, 'source_session_id').text);
      }
      if (
        mainTest.selectedChannels
          .split(',')
          .map((value) => value.trim())
          .filter(Boolean).length === 0
      ) {
        missing.push(operatorMainFieldLabel(t, 'selected_channels').text);
      }
      if (mainTest.worstDecisionRevision.trim() === '') {
        missing.push(operatorMainFieldLabel(t, 'worst_decision_snapshot_revision').text);
      }
    }
    return missing;
  }, [catalogueHasUnsupportedIdentity, mainTest, picks, selectedCatalogue, stage, t]);

  const previewMutation = useMutation<PreviewResponse, ApiError, void>({
    mutationFn: async () => {
      if (selectedCatalogue === undefined || blockers.length > 0) {
        throw new Error('generation selection is incomplete');
      }
      const request = buildRequest(selectedCatalogue, picks, stage, mainTest);
      return previewTestPlanGeneration(projectId, request);
    },
    onSuccess: (data) => {
      if (selectedCatalogue === undefined) return;
      setActiveRequest(buildRequest(selectedCatalogue, picks, stage, mainTest));
      setPreview(data);
      setJobId(null);
      setRowAfter(null);
      const nextIdempotencyKey = newIdempotencyKey();
      idempotencyKey.current = nextIdempotencyKey;
      setIdempotencyKeyValue(nextIdempotencyKey);
    },
  });

  const submitMutation = useMutation<SubmittedResponse, ApiError, void>({
    mutationFn: async () => {
      if (activeRequest === null || preview === null || idempotencyKey.current === '') {
        throw new Error('preview is required before submit');
      }
      return submitTestPlanGeneration(projectId, idempotencyKey.current, activeRequest, preview);
    },
    onSuccess: (data) => {
      // ⚠️ contract v0.1.22 — the submit response now spells the handle the
      // way the route consumes it (`{generation_job_id}`); before, the path
      // parameter had no producing field at all.
      setJobId(data.generation_job_id);
      setRowAfter(null);
    },
  });

  const jobQuery = useQuery({
    queryKey: queryKeys.testPlans.generationJob(projectId, jobId),
    enabled: jobId !== null,
    ...REFETCH_STRATEGIES.CRITICAL,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'succeeded' || status === 'failed' || status === 'cancelled'
        ? false
        : REFETCH_STRATEGIES.CRITICAL.refetchInterval;
    },
    queryFn: async (): Promise<JobResponse> => {
      if (jobId === null) throw new Error('generation job is not selected');
      return fetchTestPlanGenerationJob(projectId, jobId);
    },
  });

  // A terminal failure is not a generated draft. Keep the row query and its
  // rendered state honest even if a malformed/stale response happens to carry
  // a draft id alongside the failed status.
  const draftId = jobQuery.data?.status === 'succeeded' ? (jobQuery.data.draft_id ?? null) : null;
  const generationRowsKey = queryKeys.testPlans.generationRows(projectId, draftId ?? '', rowAfter);
  const generationRowsPrefixKey = queryKeys.testPlans.generationRowsPrefix(
    projectId,
    draftId ?? '',
  );
  const cachedGenerationPageCount = queryClient.getQueryCache().findAll({
    queryKey: generationRowsPrefixKey,
    exact: false,
  }).length;
  const metadataQuery = useQuery({
    queryKey: queryKeys.testPlans.generationMetadata(projectId, draftId),
    enabled: draftId !== null,
    ...REFETCH_STRATEGIES.NORMAL,
    queryFn: async (): Promise<MetadataResponse> => {
      if (draftId === null) throw new Error('generation draft is not selected');
      return fetchTestPlanGenerationMetadata(projectId, draftId);
    },
  });

  const rowsQuery = useQuery({
    queryKey: generationRowsKey,
    enabled: draftId !== null,
    ...REFETCH_STRATEGIES.NORMAL,
    queryFn: async (): Promise<RowPageResponse> => {
      if (draftId === null) throw new Error('generation draft is not selected');
      const pageSize = selectedCatalogue?.limits.page_size;
      if (pageSize === undefined) throw new Error('generation catalogue limits are not loaded');
      const currentDraftId = draftId;
      const query = {
        limit: pageSize,
        ...(rowAfter === null ? {} : { after_draft_row_id: rowAfter }),
      };
      return fetchTestPlanDraftRows(projectId, currentDraftId, query);
    },
  });

  useEffect(() => {
    if (
      draftId !== null &&
      jobQuery.data?.status === 'succeeded' &&
      openedDraft.current !== draftId
    ) {
      openedDraft.current = draftId;
      onGenerated(draftId);
    }
  }, [draftId, jobQuery.data?.status, onGenerated]);

  const goToRowPage = (nextAfter: number | null): void => {
    if (draftId !== null) {
      const cachePageLimit = selectedCatalogue?.limits.browser_cache_page_limit;
      if (cachePageLimit !== undefined) {
        const keepCursors = new Set([rowAfter, nextAfter]);
        queryClient.removeQueries({
          queryKey: generationRowsPrefixKey,
          predicate: (query) => {
            const cursor = query.queryKey[generationRowsPrefixKey.length];
            return !keepCursors.has(cursor as number | null);
          },
        });
        const cachedPages = queryClient.getQueryCache().findAll({
          queryKey: generationRowsPrefixKey,
          exact: false,
        });
        const overflow = Math.max(0, cachedPages.length - cachePageLimit);
        for (const query of cachedPages.slice(0, overflow)) {
          queryClient.removeQueries({ queryKey: query.queryKey, exact: true });
        }
      }
    }
    setRowAfter(nextAfter);
  };

  const updatePick = (axis: string, value: string): void => {
    setPicks((current) => {
      const values = current[axis] ?? [];
      return {
        ...current,
        [axis]: values.includes(value)
          ? values.filter((item) => item !== value)
          : [...values, value],
      };
    });
    setPreview(null);
    setActiveRequest(null);
  };

  const selectTechnology = (value: string): void => {
    const nextCatalogue = catalogues[value];
    setTechnology(value);
    setStage(nextCatalogue?.stages[0] ?? '');
    setPicks(nextCatalogue === undefined ? {} : initialPicks(nextCatalogue));
    setPreview(null);
    setActiveRequest(null);
    setJobId(null);
  };

  const technologyLabel =
    selectedCatalogue === undefined
      ? null
      : operatorTechnologyLabel(t, selectedCatalogue.technology);
  const stageLabel =
    selectedCatalogue !== undefined && selectedCatalogue.stages.length > 0
      ? operatorStageLabel(t)
      : null;
  const bandsLabel = operatorAxisLabel(t, 'bands_per_subfamily');
  const mainSourceLabel = operatorMainSourceLabel(t);

  return (
    <section aria-labelledby="test-plans-generator-heading" data-testid="test-plans-generator">
      <SectionBand
        title={t('routes.testPlans.generator.sectionTitle')}
        titleId="test-plans-generator-heading"
      />
      <p className="section-hint">{t('routes.testPlans.generator.description')}</p>

      {catalogueQuery.isPending && (
        <BlockSkeleton lines={3} testId="test-plans-generator-loading" />
      )}
      {catalogueQuery.isError && (
        <ErrorState
          testId="test-plans-generator-options-error"
          message={describeApiError(catalogueQuery.error, 'headless', {
            forbidden: t('routes.testPlans.generator.optionsForbidden'),
            network: t('routes.testPlans.generator.optionsNetwork'),
            default: t('routes.testPlans.generator.optionsFailed'),
          })}
        />
      )}

      {selectedCatalogue !== undefined && (
        <div
          data-testid="test-plans-generator-budgets"
          data-page-size={selectedCatalogue.limits.page_size}
          data-browser-cache-page-limit={selectedCatalogue.limits.browser_cache_page_limit}
          data-dom-row-limit={selectedCatalogue.limits.dom_row_limit}
          data-initial-payload-row-limit={selectedCatalogue.limits.initial_payload_row_limit}
          data-cached-generation-pages={cachedGenerationPageCount}
        />
      )}

      {selectedCatalogue !== undefined && catalogueHasUnsupportedIdentity && (
        <ErrorState
          testId="test-plans-generator-unsupported-catalogue"
          message={t('routes.testPlans.generator.optionsFailed')}
        />
      )}

      {selectedCatalogue !== undefined && !catalogueHasUnsupportedIdentity && (
        <form
          data-testid="test-plans-generator-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (preview === null) previewMutation.mutate();
            else submitMutation.mutate();
          }}
        >
          <label
            data-label-state={technologyLabel?.state}
            data-technology-name={selectedCatalogue.technology}
          >
            <span>{technologyLabel?.text}</span>
            <select
              data-testid="test-plans-generator-technology"
              data-label-state={technologyLabel?.state}
              value={technology}
              onChange={(event) => selectTechnology(event.target.value)}
            >
              {Object.keys(catalogues).map((key) => (
                <option
                  key={key}
                  value={key}
                  data-label-state={operatorTechnologyLabel(t, key).state}
                  data-technology-name={key}
                >
                  {operatorTechnologyLabel(t, key).text}
                </option>
              ))}
            </select>
          </label>

          {selectedCatalogue.stages.length > 0 && (
            <label data-label-state={stageLabel?.state}>
              <span>{stageLabel?.text}</span>
              <select
                data-testid="test-plans-generator-stage"
                data-label-state={stageLabel?.state}
                data-stage-value={stage}
                value={stage}
                onChange={(event) => {
                  setStage(event.target.value);
                  setPreview(null);
                  setActiveRequest(null);
                }}
              >
                {selectedCatalogue.stages.map((value) => {
                  const valueLabel = operatorStageValueLabel(t, value);
                  return (
                    <option
                      key={value}
                      value={value}
                      data-label-state={valueLabel.state}
                      data-stage-value={value}
                    >
                      {valueLabel.text}
                    </option>
                  );
                })}
              </select>
            </label>
          )}

          {selectedCatalogue.axes.map((axis) => {
            const axisLabel = operatorAxisLabel(t, axis.name);
            return (
              <fieldset
                key={axis.name}
                data-testid={`test-plans-generator-axis-${axis.name}`}
                data-axis-name={axis.name}
                data-label-state={axisLabel.state}
              >
                <legend>{axisLabel.text}</legend>
                {axis.values.map((value) => (
                  <label key={value}>
                    <input
                      type="checkbox"
                      data-testid={`test-plans-generator-axis-${axis.name}-${value}`}
                      checked={(picks[axis.name] ?? []).includes(value)}
                      onChange={() => updatePick(axis.name, value)}
                    />
                    <span>{value}</span>
                  </label>
                ))}
              </fieldset>
            );
          })}

          <fieldset
            data-testid="test-plans-generator-bands"
            data-axis-name="bands_per_subfamily"
            data-label-state={bandsLabel.state}
          >
            <legend>{bandsLabel.text}</legend>
            {Object.entries(selectedBands(selectedCatalogue, picks)).map(([key, values]) => (
              <output key={key}>
                {key}: {values.join(', ')}
              </output>
            ))}
          </fieldset>

          {stage === 'main_test' && (
            <fieldset
              data-testid="test-plans-generator-main-source"
              data-label-state={mainSourceLabel.state}
              data-technology-name={selectedCatalogue.technology}
            >
              <legend>{mainSourceLabel.text}</legend>
              <label>
                <span
                  data-field-name="source_session_id"
                  data-label-state={operatorMainFieldLabel(t, 'source_session_id').state}
                >
                  {operatorMainFieldLabel(t, 'source_session_id').text}
                </span>
                <input
                  value={mainTest.sourceSessionId}
                  onChange={(event) =>
                    setMainTest((current) => ({ ...current, sourceSessionId: event.target.value }))
                  }
                />
              </label>
              <label>
                <span
                  data-field-name="selected_channels"
                  data-label-state={operatorMainFieldLabel(t, 'selected_channels').state}
                >
                  {operatorMainFieldLabel(t, 'selected_channels').text}
                </span>
                <input
                  value={mainTest.selectedChannels}
                  onChange={(event) =>
                    setMainTest((current) => ({ ...current, selectedChannels: event.target.value }))
                  }
                />
              </label>
              <label>
                <span
                  data-field-name="worst_decision_snapshot_revision"
                  data-label-state={
                    operatorMainFieldLabel(t, 'worst_decision_snapshot_revision').state
                  }
                >
                  {operatorMainFieldLabel(t, 'worst_decision_snapshot_revision').text}
                </span>
                <input
                  value={mainTest.worstDecisionRevision}
                  onChange={(event) =>
                    setMainTest((current) => ({
                      ...current,
                      worstDecisionRevision: event.target.value,
                    }))
                  }
                />
              </label>
            </fieldset>
          )}

          {blockers.length > 0 && (
            <StatusMessage
              tone="info"
              testId="test-plans-generator-blockers"
              message={t('routes.testPlans.generator.blocked', {
                technologies: blockers.join(', '),
              })}
            />
          )}

          {preview !== null && (
            <div data-testid="test-plans-generator-preview">
              <p>
                {preview.production_matrix.purpose}: {preview.production_matrix.revision}
              </p>
              <p>
                {preview.representative_matrix.purpose}: {preview.representative_matrix.revision}
              </p>
              <p data-testid="test-plans-generator-preview-count">
                {preview.production_estimate.exact_count ?? preview.production_estimate.lower_bound}
              </p>
            </div>
          )}

          <Button
            type="submit"
            variant="primary"
            data-testid="test-plans-generator-submit"
            disabled={
              blockers.length > 0 ||
              previewMutation.isPending ||
              submitMutation.isPending ||
              (preview !== null && activeRequest === null)
            }
          >
            {previewMutation.isPending || submitMutation.isPending
              ? t('routes.testPlans.generator.busy')
              : t('routes.testPlans.generator.submit')}
          </Button>
        </form>
      )}

      {(previewMutation.isError || submitMutation.isError || jobQuery.isError) && (
        <ErrorState
          testId="test-plans-generator-error"
          message={generationErrorMessage(
            previewMutation.error ?? submitMutation.error ?? jobQuery.error,
            t,
          )}
        />
      )}

      {jobQuery.data !== undefined && (
        <div
          data-testid="test-plans-generator-status"
          data-label-state={operatorGenerationStatusLabel(t, jobQuery.data.status).state}
          data-status-token={jobQuery.data.status}
        >
          <StatusMessage
            tone="info"
            testId="test-plans-generator-job-status"
            message={operatorGenerationStatusLabel(t, jobQuery.data.status).text}
          />
          {jobQuery.data.status === 'failed' && jobQuery.data.error_message !== null && (
            <ErrorState
              testId="test-plans-generator-error"
              message={t('routes.testPlans.generator.failed')}
              details={jobQuery.data.error_message}
            />
          )}
        </div>
      )}

      {draftId !== null && rowsQuery.data !== undefined && (
        <section data-testid="test-plans-generator-rows" aria-label={draftId}>
          <p>
            {t('routes.testPlans.generator.result', {
              draft: draftId,
              rows: String(rowsQuery.data.rows.length),
            })}
          </p>
          {metadataQuery.data?.metadata !== null && metadataQuery.data?.metadata !== undefined && (
            <details
              data-label-state={operatorGenerationStatusLabel(t, metadataQuery.data.status).state}
              data-metadata-status-token={metadataQuery.data.status}
            >
              <summary data-testid="test-plans-generator-metadata-status">
                {operatorGenerationStatusLabel(t, metadataQuery.data.status).text}
              </summary>
              <pre>{JSON.stringify(metadataQuery.data.metadata, null, 2)}</pre>
            </details>
          )}
          <div data-testid="test-plans-generator-row-page">
            {rowsQuery.data.rows
              .slice(0, selectedCatalogue?.limits.dom_row_limit ?? 0)
              .map((row) => (
                <div key={row.draft_row_id} data-testid="test-plans-generator-row">
                  {row.draft_row_id}: {row.capability_path.join(' / ')} {row.generation_key ?? ''}
                </div>
              ))}
          </div>
          {rowsQuery.data.next_after_draft_row_id !== null && (
            <Button
              type="button"
              variant="secondary"
              data-testid="test-plans-generator-next-page"
              onClick={() => goToRowPage(rowsQuery.data?.next_after_draft_row_id ?? null)}
            >
              {rowsQuery.data.next_after_draft_row_id}
            </Button>
          )}
          {rowAfter !== null && (
            <Button
              type="button"
              variant="secondary"
              data-testid="test-plans-generator-previous-page"
              onClick={() => goToRowPage(null)}
            >
              {rowAfter}
            </Button>
          )}
        </section>
      )}
    </section>
  );
}
