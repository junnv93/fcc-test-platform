import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';

import { PERMISSION_PLATFORM_READ } from '@/api/permissions';
import {
  fetchProviderList,
  fetchProviderUiDescriptor,
  type ProviderUiDescriptor,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { RequirePermission } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { ROUTE_PATHS } from '@/shared/route-links';
import {
  BlockSkeleton,
  Button,
  DataTable,
  describeApiError,
  EmptyState,
  ErrorState,
  featureStatusKind,
  featureStatusLabelToken,
  FieldGroup,
  liveRegionProps,
  PageHeader,
  SectionBand,
  StatusBadge,
  Toolbar,
} from '@/ui';

/**
 * Operator-facing Test Types screen backed by the provider UI descriptor
 * (WEB-PROVIDER-UI-0 + FE Phase 2 §6.4, 2026-05-30).
 *
 * The platform descriptor remains the API source of truth, but this route
 * curates it for 시험원/관리자 workflow: human labels, status labels, and
 * configuration counts are shown by default. Internal ids, sheet names, and row
 * identity tokens stay out of the operating tables and are limited to the
 * collapsed diagnostics area where they are needed for support.
 *
 * Edit / save / publish controls remain forbidden until the Excel replacement
 * architecture explicitly introduces editable row identity and re-keying.
 */

// Phase 2 follow-up (2026-05-30) — `featureStatusKind` is now the
// `@/ui::status-mapping` SSOT (imported above). This file no longer owns
// the ProviderFeature.status → status mapping.

export function ProvidersRoute(): JSX.Element {
  const { t } = useT();
  return (
    <section className="providers" aria-labelledby="providers-heading">
      <PageHeader
        title={t('routes.providers.pageTitleBrand')}
        titleId="providers-heading"
        description={t('routes.providers.pageDescription')}
      />
      <RequirePermission permission={PERMISSION_PLATFORM_READ}>
        <ProvidersWorkbenchOverview />
        <DescriptorViewer />
      </RequirePermission>
    </section>
  );
}

function ProvidersWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="providers-workbench-overview"
      aria-label={t('routes.providers.workbenchNavAria')}
      data-testid="providers-workbench-overview"
    >
      <a className="providers-workbench-overview__item" href="#providers-lookup-heading">
        <span className="providers-workbench-overview__label">
          {t('routes.providers.stepLookup')}
        </span>
        <span className="providers-workbench-overview__detail">
          {t('routes.providers.stepLookupDetail')}
        </span>
      </a>
      <a className="providers-workbench-overview__item" href="#providers-capability-heading">
        <span className="providers-workbench-overview__label">
          {t('routes.providers.stepCapability')}
        </span>
        <span className="providers-workbench-overview__detail">
          {t('routes.providers.stepCapabilityDetail')}
        </span>
      </a>
      <a className="providers-workbench-overview__item" href="#providers-next-heading">
        <span className="providers-workbench-overview__label">
          {t('routes.providers.stepNext')}
        </span>
        <span className="providers-workbench-overview__detail">
          {t('routes.providers.stepNextDetail')}
        </span>
      </a>
    </nav>
  );
}

/**
 * Backend-driven provider selector (WEB-PROVIDER-UI-0). The options are the
 * platform registry's `GET /platform/providers` summaries — the browser never
 * hardcodes a provider list. A deep-linked / directly-typed id that is not in
 * the fetched list is still kept selectable so direct-lookup behaviour survives.
 */
function ProviderPicker({
  providerId,
  onSelect,
}: {
  providerId: string;
  onSelect: (provider: string) => void;
}): JSX.Element {
  const { t } = useT();
  const providers = useQuery({
    queryKey: queryKeys.provider.list(),
    queryFn: fetchProviderList,
  });
  const options = providers.data ?? [];
  const currentIsListed = providerId === '' || options.some((p) => p.provider_id === providerId);
  // Success with zero registered providers — an explicit empty state, distinct
  // from a still-loading or failed list (WEB-PROVIDER-UI-0 follow-up).
  const isEmptyList = providers.isSuccess && options.length === 0;

  // The list is a convenience layer: even while it is loading / failed / empty,
  // the operator can still drive a lookup through the direct-entry ?provider=
  // form (and a deep-linked id stays selectable via the synthetic option), so
  // these states are surfaced as an inline note — never a hard block.
  return (
    <FieldGroup label={t('routes.providers.pickerLabel')} htmlFor="provider-picker">
      <select
        id="provider-picker"
        data-testid="provider-picker"
        value={providerId}
        // Only the list-load is a reason to disable the select; a failed/empty
        // list still lets the operator clear to the placeholder or keep a
        // deep-linked id, so it stays enabled in those branches.
        disabled={providers.isLoading}
        aria-label={t('routes.providers.pickerAria')}
        onChange={(event) => onSelect(event.target.value)}
      >
        <option value="">{t('routes.providers.pickerPlaceholder')}</option>
        {!currentIsListed && <option value={providerId}>{providerId}</option>}
        {options.map((provider) => (
          <option key={provider.provider_id} value={provider.provider_id}>
            {provider.display_name}
          </option>
        ))}
      </select>
      {providers.isLoading && (
        <BlockSkeleton
          lines={1}
          label={t('routes.providers.pickerLoading')}
          testId="provider-picker-loading"
        />
      )}
      {providers.isError && (
        <p
          className="provider-picker__status"
          // `inlineNotice`, not a failure alert: the comment above is the whole
          // argument — the direct-entry form still works, so this never blocks
          // the operator and must not interrupt them (`@/ui/live-region`).
          {...liveRegionProps('inlineNotice')}
          data-testid="provider-picker-error"
        >
          {describeApiError(providers.error, 'platform', {
            // 403 (platform:read missing) and 503 (registry unavailable) get
            // distinct copy; any other list-load failure and the network arm
            // share the generic "couldn't load — enter an id directly" copy.
            forbidden: t('routes.providers.pickerForbidden'),
            serviceUnavailable: t('routes.providers.pickerUnavailable'),
            network: t('routes.providers.pickerError'),
            default: t('routes.providers.pickerError'),
          })}
        </p>
      )}
      {isEmptyList && (
        <p
          className="provider-picker__status"
          {...liveRegionProps('inlineNotice')}
          data-testid="provider-picker-empty"
        >
          {t('routes.providers.pickerEmpty')}
        </p>
      )}
    </FieldGroup>
  );
}

function DescriptorViewer(): JSX.Element {
  const { t } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const providerId = (searchParams.get('provider') ?? '').trim();

  const descriptor = useQuery({
    queryKey: queryKeys.provider.uiDescriptor(providerId),
    enabled: providerId !== '',
    queryFn: () => fetchProviderUiDescriptor(providerId),
  });

  // Single write path for the ?provider= deep-link (shared by the picker and the
  // direct-entry form) so selection stays bookmarkable/shareable.
  const applyProvider = (provider: string): void => {
    setSearchParams(provider ? { provider } : {});
  };

  return (
    <div className="providers-workbench" data-testid="providers-workbench">
      <div className="providers-workbench__main">
        <section className="providers-workbench-panel" aria-labelledby="providers-lookup-heading">
          <SectionBand
            title={t('routes.providers.lookupSection')}
            titleId="providers-lookup-heading"
          />
          <p className="readonly-banner" role="note" data-testid="readonly-banner">
            {t('routes.providers.readonlyBannerPrefix')}
            <strong>{t('routes.providers.readonlyBannerEmphasis')}</strong>
            {t('routes.providers.readonlyBannerSuffix')}
          </p>

          <form
            onSubmit={(event) => {
              event.preventDefault();
              const value = new FormData(event.currentTarget).get('provider');
              applyProvider(typeof value === 'string' ? value.trim() : '');
            }}
          >
            <Toolbar ariaLabel={t('routes.providers.toolbarAriaLabel')}>
              {/* Backend-driven selector (WEB-PROVIDER-UI-0) — options come from the
                  platform registry, never a browser-local list. */}
              <ProviderPicker providerId={providerId} onSelect={applyProvider} />
              {/* Direct lookup / deep-link entry — an id typed here (or arriving in
                  the ?provider= URL) still resolves, even if unregistered. */}
              <FieldGroup label={t('routes.providers.providerFieldLabel')} htmlFor="provider-input">
                <input
                  id="provider-input"
                  name="provider"
                  data-testid="provider-input"
                  defaultValue={providerId}
                  aria-label={t('routes.providers.providerIdAria')}
                />
              </FieldGroup>
              <Button type="submit" variant="primary">
                {t('routes.providers.searchButton')}
              </Button>
            </Toolbar>
          </form>
        </section>

        <section
          className="providers-workbench-panel"
          aria-labelledby="providers-capability-heading"
        >
          <SectionBand
            title={t('routes.providers.capabilitySection')}
            titleId="providers-capability-heading"
          />
          {providerId === '' && (
            <EmptyState
              testId="descriptor-empty"
              title={t('routes.providers.emptyTitle')}
              description={t('routes.providers.emptyDescription')}
            />
          )}
          {descriptor.isLoading && <BlockSkeleton lines={4} testId="provider-descriptor-loading" />}
          {descriptor.isError && (
            <ErrorState
              testId="descriptor-error"
              message={describeApiError(descriptor.error, 'platform', {
                notFound: t('routes.providers.errorNotFound'),
                forbidden: t('routes.providers.errorForbidden'),
                default: t('routes.providers.errorDefault'),
              })}
            />
          )}
          {descriptor.isSuccess && <DescriptorReadView descriptor={descriptor.data} />}
        </section>
      </div>

      <aside
        className="providers-workbench__rail"
        aria-labelledby="providers-next-heading"
        data-testid="providers-next-actions"
      >
        <ProvidersNextActions providerId={providerId} />
      </aside>
    </div>
  );
}

function ProvidersNextActions({ providerId }: { readonly providerId: string }): JSX.Element {
  const { t } = useT();
  return (
    <section className="providers-next">
      <SectionBand title={t('routes.providers.nextSection')} titleId="providers-next-heading" />
      <p className="providers-next__state" data-testid="providers-next-state">
        {providerId === ''
          ? t('routes.providers.nextNoProvider')
          : t('routes.providers.nextProvider', { provider: providerId })}
      </p>
      <div className="providers-next__actions">
        <Link
          to={ROUTE_PATHS.testPlans}
          className="providers-next__action"
          data-testid="providers-next-test-plans"
        >
          {t('routes.providers.nextTestPlans')}
        </Link>
        <Link
          to={ROUTE_PATHS.chambers}
          className="providers-next__action"
          data-testid="providers-next-chambers"
        >
          {t('routes.providers.nextChambers')}
        </Link>
        <Link
          to={ROUTE_PATHS.diagnostics}
          className="providers-next__action"
          data-testid="providers-next-diagnostics"
        >
          {t('routes.providers.nextDiagnostics')}
        </Link>
      </div>
      <p className="section-hint">{t('routes.providers.nextHint')}</p>
    </section>
  );
}

function DescriptorReadView({
  descriptor,
}: {
  readonly descriptor: ProviderUiDescriptor;
}): JSX.Element {
  const { t } = useT();
  return (
    <div className="descriptor" data-testid="descriptor">
      {/* R2: operator-facing "Test Types" screen — show the human display name
          prominently and the human-readable configuration counts/labels. The
          internal identifiers (provider id / UI version / feature·table·group
          ids / sheet names / row-identity source) are NOT operator copy; the
          provider id + UI version live in a collapsed admin/diagnostics
          <details>, and the per-row internal ids/sheet/row-identity columns are
          dropped from the operator tables entirely (the descriptor payload is
          unchanged — only this view is curated). */}
      <header className="descriptor-header">
        <dl>
          <dt>{t('routes.providers.displayName')}</dt>
          <dd data-testid="descriptor-display-name">{descriptor.display_name}</dd>
        </dl>
        <details className="descriptor-diagnostics" data-testid="descriptor-diagnostics">
          <summary>{t('routes.providers.diagnosticsLabel')}</summary>
          <dl>
            <dt>{t('routes.providers.providerIdTermLabel')}</dt>
            <dd data-testid="descriptor-provider-id">{descriptor.provider_id}</dd>
            <dt>{t('routes.providers.uiVersion')}</dt>
            <dd data-testid="descriptor-ui-version">{descriptor.ui_version}</dd>
          </dl>
        </details>
      </header>

      <section aria-label={t('routes.providers.featureMatrixAria')}>
        <SectionBand
          title={t('routes.providers.featuresTitle')}
          meta={t('routes.providers.featuresMeta', { count: descriptor.features.length })}
        />
        <DataTable
          testId="feature-matrix"
          caption={t('routes.providers.featuresCaption')}
          head={
            <thead>
              <tr>
                <th scope="col">{t('routes.providers.colLabel')}</th>
                <th scope="col">{t('routes.providers.colStatus')}</th>
              </tr>
            </thead>
          }
          body={
            <tbody>
              {descriptor.features.map((feature) => (
                <tr key={feature.feature_id} data-testid="feature-row">
                  <td>{feature.label}</td>
                  <td>
                    <StatusBadge
                      status={featureStatusKind(feature.status)}
                      label={t(
                        `routes.providers.featureStatus.${featureStatusLabelToken(feature.status)}`,
                      )}
                      testId="feature-status"
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          }
        />
      </section>

      <section aria-label={t('routes.providers.testPlanTablesAria')}>
        <SectionBand
          title={t('routes.providers.testPlanTablesTitle')}
          meta={t('routes.providers.tablesMeta', { count: descriptor.test_plan_tables.length })}
        />
        <DataTable
          testId="test-plan-tables"
          caption={t('routes.providers.testPlanTablesCaption')}
          head={
            <thead>
              <tr>
                <th scope="col">{t('routes.providers.colLabel')}</th>
                <th scope="col">{t('routes.providers.colColumns')}</th>
              </tr>
            </thead>
          }
          body={
            <tbody>
              {descriptor.test_plan_tables.map((table) => (
                <tr key={table.table_id} data-testid="test-plan-table">
                  <td>{table.label}</td>
                  <td className="data-cell-numeric">{table.columns.length}</td>
                </tr>
              ))}
            </tbody>
          }
        />
      </section>

      <section aria-label={t('routes.providers.equipmentAria')}>
        <SectionBand
          title={t('routes.providers.equipmentTitle')}
          meta={t('routes.providers.equipmentMeta', { count: descriptor.equipment.length })}
        />
        <DataTable
          testId="equipment-groups"
          caption={t('routes.providers.equipmentCaption')}
          head={
            <thead>
              <tr>
                <th scope="col">{t('routes.providers.colLabel')}</th>
              </tr>
            </thead>
          }
          body={
            <tbody>
              {descriptor.equipment.map((group) => (
                <tr key={group.group_id} data-testid="equipment-group">
                  <td>{group.label}</td>
                </tr>
              ))}
            </tbody>
          }
        />
      </section>

      <section aria-label={t('routes.providers.referenceTablesAria')}>
        <SectionBand
          title={t('routes.providers.referenceTablesTitle')}
          meta={t('routes.providers.tablesMeta', { count: descriptor.reference_tables.length })}
        />
        <DataTable
          testId="reference-tables"
          caption={t('routes.providers.referenceTablesCaption')}
          head={
            <thead>
              <tr>
                <th scope="col">{t('routes.providers.colLabel')}</th>
              </tr>
            </thead>
          }
          body={
            <tbody>
              {descriptor.reference_tables.map((table) => (
                <tr key={table.table_id} data-testid="reference-table">
                  <td>{table.label}</td>
                </tr>
              ))}
            </tbody>
          }
        />
      </section>
    </div>
  );
}

export default ProvidersRoute;
