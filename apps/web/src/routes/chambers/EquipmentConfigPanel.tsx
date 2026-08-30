import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { PERMISSION_PLATFORM_CHAMBER_CONFIG_WRITE } from '@/api/permissions';
import {
  fetchChamberEquipmentConfig,
  fetchProviderUiDescriptor,
  updateChamberEquipmentConfig,
  type ChamberAvailabilityEnvelope,
  type ProviderFieldDescriptor,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { hasPermission } from '@/auth/session';
import { useT } from '@/i18n';
import { DEPLOYED_PROVIDER_ID } from '@/shared/workbench-area-technologies';
import {
  Button,
  DataTable,
  describeApiError,
  EmptyState,
  ErrorState,
  SectionBand,
  StatusMessage,
} from '@/ui';

interface EquipmentConfigPanelProps {
  /** The fleet the workbench already loaded — the panel picks one, it does not refetch. */
  readonly chambers: readonly ChamberAvailabilityEnvelope[];
}

/**
 * `data_type` → HTML input type. TOTAL over the closed vocabulary the contract
 * declares, which is the whole reason SPLIT-6 ⓪ closed it: the platform must
 * render every provider's fields with ZERO per-provider branching, and that is
 * only possible when every token it can receive already maps to a widget it has.
 *
 * All three are text today, and that is not an argument for collapsing the map.
 * `ipv4` and `gpib_address` are separate tokens because they are separate
 * decisions — the day one of them earns a mask or an inputmode, this is the one
 * place that changes, and nothing has to learn a provider's vocabulary to do it.
 */
const INPUT_TYPE_BY_DATA_TYPE: Record<ProviderFieldDescriptor['data_type'], string> = {
  string: 'text',
  ipv4: 'text',
  gpib_address: 'text',
};

/**
 * Instrument connection settings for one chamber (SPLIT-6 ③).
 *
 * Two rules shape this component, and both come from ADR-0018 D-6:
 *
 * 1. **Every input is built from the descriptor.** Not one field name is written
 *    here. `equipment[].fields` says which inputs exist, what to label them, and
 *    which widget to use; a second provider adds its fields by shipping its own
 *    descriptor, with no release of this screen.
 * 2. **The screen stores; the node interprets.** Whether a blank LAN address
 *    falls back to GPIB, what SCPI follows a connection, when a relay actually
 *    actuates — none of that is knowable here and none of it is decided here.
 */
export function EquipmentConfigPanel({ chambers }: EquipmentConfigPanelProps): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const canWrite = hasPermission(PERMISSION_PLATFORM_CHAMBER_CONFIG_WRITE);
  const [searchParams, setSearchParams] = useSearchParams();
  // Deep-linked like the provider picker: which room you are configuring is
  // worth bookmarking and worth pasting to a colleague.
  const requested = (searchParams.get('chamber') ?? '').trim();
  const chamberId =
    chambers.find((chamber) => chamber.chamber_id === requested)?.chamber_id ?? null;

  const descriptor = useQuery({
    queryKey: queryKeys.provider.uiDescriptor(DEPLOYED_PROVIDER_ID),
    queryFn: () => fetchProviderUiDescriptor(DEPLOYED_PROVIDER_ID),
  });
  // `?? ''` rather than a non-null assertion: `enabled` already stops the fetch
  // while nothing is selected, and an assertion would be a claim the type system
  // cannot check against a value that comes from the URL.
  const config = useQuery({
    queryKey: queryKeys.chambers.equipmentConfig(chamberId),
    enabled: chamberId !== null,
    queryFn: () => fetchChamberEquipmentConfig(chamberId ?? ''),
  });

  // LOCAL EDITS ONLY — never a mirror of the server payload. Same discipline as
  // ChamberAdminPanel: an untouched field reads straight from the server on every
  // render, so a refetch cannot overwrite what the operator is typing and cannot
  // leave an untouched field stale either.
  //
  // It is also what makes the request correct. The keys in here ARE the dirty
  // set, so "send only what was edited" needs no separate bookkeeping to be true.
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [success, setSuccess] = useState<string | null>(null);

  // The chamber travels WITH the patch rather than being read off the closure.
  // A save is "these keys, for that room", and binding the two together is what
  // makes it impossible for a room switch mid-flight to land one room's
  // addresses on another — the failure would be silent and the values plausible.
  const mutation = useMutation({
    mutationFn: (variables: { chamberId: string; patch: Record<string, string | null> }) =>
      updateChamberEquipmentConfig(variables.chamberId, {
        equipment_config: variables.patch,
      }),
    onSuccess: (_saved, variables) => {
      setSuccess(t('routes.chambers.equipmentSaved'));
      // Drop the local overrides and re-read. Deliberately NOT optimistic: the
      // stored document is the merge of this patch with whatever else is there,
      // and computing that here would be re-implementing the server's merge in
      // the browser — the second definition of a rule this repo keeps in one.
      setEdits({});
      void queryClient.invalidateQueries({
        queryKey: queryKeys.chambers.equipmentConfig(variables.chamberId),
      });
    },
  });

  function selectChamber(next: string): void {
    const params = new URLSearchParams(searchParams);
    if (next === '') params.delete('chamber');
    else params.set('chamber', next);
    setSearchParams(params);
    // A different room is a different document — carrying the edits over would
    // silently write one room's addresses into another's.
    setEdits({});
    setSuccess(null);
    mutation.reset();
  }

  const picker = (
    <label data-testid="chambers-equipment-picker">
      {t('routes.chambers.equipmentPickerLabel')}
      <select
        value={chamberId ?? ''}
        onChange={(event) => selectChamber(event.currentTarget.value)}
      >
        <option value="">{t('routes.chambers.equipmentPickerPlaceholder')}</option>
        {chambers.map((chamber) => (
          <option key={chamber.chamber_id} value={chamber.chamber_id}>
            {chamber.name ?? chamber.chamber_id}
          </option>
        ))}
      </select>
    </label>
  );

  if (chamberId === null) {
    return (
      <section aria-labelledby="chambers-equipment-heading" data-testid="chambers-equipment">
        <SectionBand
          title={t('routes.chambers.sectionEquipment')}
          titleId="chambers-equipment-heading"
        />
        {picker}
        <EmptyState
          testId="chambers-equipment-unselected"
          title={t('routes.chambers.equipmentPickChamber')}
          description={t('routes.chambers.equipmentPickChamberHint')}
        />
      </section>
    );
  }

  // Narrowed past the early return — every use below is for THIS room.
  const selectedChamberId: string = chamberId;
  const groups = descriptor.data?.equipment ?? [];
  const stored = config.data?.equipment_config ?? {};
  const dirtyKeys = Object.keys(edits);

  function valueOf(fieldId: string): string {
    return edits[fieldId] ?? stored[fieldId] ?? '';
  }

  function editField(fieldId: string, next: string): void {
    setEdits((current) => ({ ...current, [fieldId]: next }));
    setSuccess(null);
    mutation.reset();
  }

  function save(): void {
    // Only the edited keys. An emptied field means "delete this key", which the
    // per-key contract spells `null` — sending '' instead would store a blank
    // address and the node would treat it as configured-but-empty.
    const patch: Record<string, string | null> = {};
    for (const fieldId of dirtyKeys) {
      const value = (edits[fieldId] ?? '').trim();
      patch[fieldId] = value === '' ? null : value;
    }
    mutation.mutate({ chamberId: selectedChamberId, patch });
  }

  return (
    <section aria-labelledby="chambers-equipment-heading" data-testid="chambers-equipment">
      <SectionBand
        title={t('routes.chambers.sectionEquipment')}
        titleId="chambers-equipment-heading"
      />
      {picker}
      {descriptor.isError && (
        <ErrorState
          testId="chambers-equipment-descriptor-error"
          message={describeApiError(descriptor.error, 'platform', {
            default: t('routes.chambers.equipmentDescriptorFailed'),
          })}
        />
      )}
      {config.isError && (
        <ErrorState
          testId="chambers-equipment-error"
          message={describeApiError(config.error, 'platform', {
            default: t('routes.chambers.equipmentLoadFailed'),
          })}
        />
      )}
      {success !== null && (
        <StatusMessage message={success} tone="success" testId="chambers-equipment-success" />
      )}
      {mutation.isError && (
        <ErrorState
          testId="chambers-equipment-save-error"
          message={describeApiError(mutation.error, 'platform', {
            forbidden: t('routes.chambers.equipmentForbidden'),
            network: t('routes.chambers.equipmentNetwork'),
            default: t('routes.chambers.equipmentSaveFailed'),
          })}
        />
      )}
      {groups.length === 0 && descriptor.isSuccess && (
        <EmptyState
          testId="chambers-equipment-empty"
          title={t('routes.chambers.equipmentNoFields')}
          description={t('routes.chambers.equipmentNoFieldsHint')}
        />
      )}
      {groups.map((group) => (
        <DataTable
          key={group.group_id}
          testId="chambers-equipment-table"
          caption={group.label}
          head={
            <thead>
              <tr>
                <th scope="col">{t('routes.chambers.colEquipmentField')}</th>
                <th scope="col">{t('routes.chambers.colEquipmentValue')}</th>
              </tr>
            </thead>
          }
          body={
            <tbody>
              {group.fields.map((field) => (
                <tr key={field.field_id} data-testid="chambers-equipment-row">
                  <th scope="row">{field.label}</th>
                  <td>
                    <input
                      aria-label={field.label}
                      data-testid="chambers-equipment-input"
                      data-field-id={field.field_id}
                      type={INPUT_TYPE_BY_DATA_TYPE[field.data_type]}
                      value={valueOf(field.field_id)}
                      readOnly={!canWrite}
                      onChange={(event) => editField(field.field_id, event.currentTarget.value)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          }
        />
      ))}
      {canWrite && groups.length > 0 && (
        <div>
          {dirtyKeys.length > 0 && (
            <small data-testid="chambers-equipment-unsaved">
              {t('routes.chambers.equipmentUnsaved', { count: dirtyKeys.length })}
            </small>
          )}
          <Button
            type="button"
            data-testid="chambers-equipment-save"
            disabled={dirtyKeys.length === 0 || mutation.isPending}
            onClick={save}
          >
            {t('routes.chambers.equipmentSave')}
          </Button>
          {/* The value the node reads is pulled at ITS next boot, not applied to
              a run in flight — saying so here is cheaper than a tester wondering
              why the measurement they are watching did not change. */}
          <small data-testid="chambers-equipment-effect">
            {t('routes.chambers.equipmentTakesEffect')}
          </small>
        </div>
      )}
    </section>
  );
}
