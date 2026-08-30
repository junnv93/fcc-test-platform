import { useMutation } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';

import { createSample, patchSample, type SampleInventoryItem } from '@/api/platform-client';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { BlockSkeleton, Button, describeApiError, ErrorState, FieldGroup, SectionBand } from '@/ui';

type FormKey =
  | 'sample_number'
  | 'sample_code'
  | 'test_category'
  | 'label_number'
  | 'smsn'
  | 'serial_number'
  | 'intake_cert'
  | 'assigned_team'
  | 'sender'
  | 'receiver'
  | 'received_date'
  | 'released_date'
  | 'note'
  | 'intake_date'
  | 'tech_group'
  | 'bl'
  | 'ap'
  | 'cp'
  | 'csc'
  | 'rf_cal'
  | 'hw_rev'
  | 'intake_note';

type FormValues = Record<FormKey, string>;

const SAMPLE_FIELDS: readonly { key: FormKey; labelKey: string; required?: boolean }[] = [
  { key: 'sample_number', labelKey: 'sampleNumber', required: true },
  { key: 'sample_code', labelKey: 'sampleCode' },
  { key: 'test_category', labelKey: 'testCategory' },
  { key: 'label_number', labelKey: 'labelNumber' },
  { key: 'smsn', labelKey: 'smsn' },
  { key: 'serial_number', labelKey: 'serialNumber' },
  { key: 'intake_cert', labelKey: 'intakeCert' },
  { key: 'assigned_team', labelKey: 'assignedTeam' },
  { key: 'sender', labelKey: 'sender' },
  { key: 'receiver', labelKey: 'receiver' },
  { key: 'received_date', labelKey: 'receivedDate' },
  { key: 'released_date', labelKey: 'releasedDate' },
  { key: 'note', labelKey: 'note' },
];

const INTAKE_FIELDS: readonly { key: FormKey; labelKey: string }[] = [
  { key: 'intake_date', labelKey: 'intakeDate' },
  { key: 'tech_group', labelKey: 'techGroup' },
  { key: 'bl', labelKey: 'bl' },
  { key: 'ap', labelKey: 'ap' },
  { key: 'cp', labelKey: 'cp' },
  { key: 'csc', labelKey: 'csc' },
  { key: 'rf_cal', labelKey: 'rfCal' },
  { key: 'hw_rev', labelKey: 'hwRev' },
  { key: 'intake_note', labelKey: 'intakeNote' },
];

function initialValues(sample?: SampleInventoryItem): FormValues {
  const intake = sample?.latest_intake;
  return {
    sample_number: sample?.sample_number ?? '',
    sample_code: sample?.sample_code ?? '',
    test_category: sample?.test_category ?? '',
    label_number: sample?.label_number ?? '',
    smsn: sample?.smsn ?? '',
    serial_number: sample?.serial_number ?? '',
    intake_cert: sample?.intake_cert ?? '',
    assigned_team: sample?.assigned_team ?? '',
    sender: sample?.sender ?? '',
    receiver: sample?.receiver ?? '',
    received_date: sample?.received_date ?? '',
    released_date: sample?.released_date ?? '',
    note: sample?.note ?? '',
    intake_date: intake?.intake_date ?? '',
    tech_group: intake?.tech_group ?? '',
    bl: intake?.bl ?? '',
    ap: intake?.ap ?? '',
    cp: intake?.cp ?? '',
    csc: intake?.csc ?? '',
    rf_cal: intake?.rf_cal ?? '',
    hw_rev: intake?.hw_rev ?? '',
    intake_note: intake?.note ?? '',
  };
}

function nullable(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

function hasIntakeValue(values: FormValues): boolean {
  return INTAKE_FIELDS.some(({ key }) => values[key].trim() !== '');
}

function intakeChanged(values: FormValues, sample?: SampleInventoryItem): boolean {
  if (sample === undefined) return hasIntakeValue(values);
  const intake = sample.latest_intake;
  return INTAKE_FIELDS.some(({ key }) => {
    const intakeFields = intake as
      | (Record<string, string | null | undefined> & { note?: string | null })
      | null
      | undefined;
    const initial = key === 'intake_note' ? intakeFields?.note : intakeFields?.[key];
    return values[key].trim() !== String(initial ?? '').trim();
  });
}

function buildIntake(values: FormValues) {
  return {
    intake_date: nullable(values.intake_date),
    tech_group: nullable(values.tech_group),
    bl: nullable(values.bl),
    ap: nullable(values.ap),
    cp: nullable(values.cp),
    csc: nullable(values.csc),
    rf_cal: nullable(values.rf_cal),
    hw_rev: nullable(values.hw_rev),
    note: nullable(values.intake_note),
  };
}

function buildSampleFields(values: FormValues) {
  return {
    sample_number: values.sample_number.trim(),
    sample_code: nullable(values.sample_code),
    test_category: nullable(values.test_category),
    label_number: nullable(values.label_number),
    smsn: nullable(values.smsn),
    serial_number: nullable(values.serial_number),
    intake_cert: nullable(values.intake_cert),
    assigned_team: nullable(values.assigned_team),
    sender: nullable(values.sender),
    receiver: nullable(values.receiver),
    received_date: nullable(values.received_date),
    released_date: nullable(values.released_date),
    note: nullable(values.note),
  };
}

export interface SampleEditorProps {
  readonly projectId: string;
  readonly sample?: SampleInventoryItem;
  readonly readOnly?: boolean;
  readonly onSaved: (sample: SampleInventoryItem) => void;
  /** Refetch the authoritative row after a 409 without changing selection. */
  readonly onConflict?: () => void;
}

export function SampleEditor({
  projectId,
  sample,
  readOnly = false,
  onSaved,
  onConflict,
}: SampleEditorProps): JSX.Element {
  const { t } = useT();
  const [values, setValues] = useState<FormValues>(() => initialValues(sample));

  useEffect(() => {
    setValues(initialValues(sample));
  }, [sample]);

  const save = useMutation({
    mutationFn: async (): Promise<SampleInventoryItem> => {
      const fields = buildSampleFields(values);
      // An existing intake is immutable. Only append a new observation when an
      // intake field actually changed; ordinary sample-field edits must not
      // duplicate the latest intake row.
      const intake = intakeChanged(values, sample) ? { latest_intake: buildIntake(values) } : {};
      if (sample === undefined) {
        return createSample(projectId, {
          ...fields,
          ...intake,
        });
      }
      return patchSample(projectId, sample.sample_id, {
        expected_version: sample.row_version,
        ...fields,
        ...intake,
      });
    },
    onSuccess: onSaved,
  });

  function update(key: FormKey, value: string): void {
    setValues((current) => ({ ...current, [key]: value }));
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    if (!readOnly && !save.isPending) save.mutate();
  }

  const saveConflict = save.isError && (save.error as ApiError | undefined)?.status === 409;

  return (
    <section className="sample-editor" aria-labelledby="sample-editor-heading">
      <SectionBand
        title={
          sample === undefined
            ? t('routes.sampleInventory.editor.createTitle')
            : t('routes.sampleInventory.editor.editTitle')
        }
        titleId="sample-editor-heading"
      />
      <form onSubmit={submit} data-testid="sample-editor-form">
        <fieldset disabled={readOnly || save.isPending}>
          <legend className="sr-only">{t('routes.sampleInventory.editor.sampleFields')}</legend>
          <div className="sample-editor__grid">
            {SAMPLE_FIELDS.map(({ key, labelKey, required }) => (
              <FieldGroup
                key={key}
                label={t(`routes.sampleInventory.editor.fields.${labelKey}`)}
                htmlFor={`sample-editor-${key}`}
              >
                <input
                  id={`sample-editor-${key}`}
                  data-testid={`sample-editor-${key}`}
                  value={values[key]}
                  required={required}
                  onChange={(event) => update(key, event.target.value)}
                />
              </FieldGroup>
            ))}
          </div>
        </fieldset>

        <fieldset disabled={readOnly || save.isPending}>
          <legend>{t('routes.sampleInventory.editor.intakeFields')}</legend>
          <div className="sample-editor__grid">
            {INTAKE_FIELDS.map(({ key, labelKey }) => (
              <FieldGroup
                key={key}
                label={t(`routes.sampleInventory.editor.fields.${labelKey}`)}
                htmlFor={`sample-editor-${key}`}
              >
                <input
                  id={`sample-editor-${key}`}
                  data-testid={`sample-editor-${key}`}
                  value={values[key]}
                  onChange={(event) => update(key, event.target.value)}
                />
              </FieldGroup>
            ))}
          </div>
        </fieldset>

        {!readOnly && (
          <Button
            type="submit"
            variant="primary"
            loading={save.isPending}
            loadingLabel={t('routes.sampleInventory.editor.saving')}
            data-testid="sample-editor-save"
          >
            {sample === undefined
              ? t('routes.sampleInventory.editor.create')
              : t('routes.sampleInventory.editor.save')}
          </Button>
        )}
        {save.isPending && <BlockSkeleton lines={1} testId="sample-editor-saving" />}
        {save.isError && (
          <>
            <ErrorState
              testId="sample-editor-error"
              message={describeApiError(save.error, 'platform', {
                default: t('routes.sampleInventory.editor.saveFailed'),
                conflict: t('routes.sampleInventory.editor.conflict'),
              })}
            />
            {saveConflict && onConflict !== undefined && (
              <Button
                type="button"
                variant="secondary"
                onClick={onConflict}
                data-testid="sample-editor-reload"
              >
                {t('common.retry')}
              </Button>
            )}
          </>
        )}
      </form>
    </section>
  );
}

export default SampleEditor;
