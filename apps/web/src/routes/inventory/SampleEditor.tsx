import { useMutation } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';

import { createSample, patchSample, type SampleInventoryItem } from '@/api/platform-client';
import { useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { BlockSkeleton, Button, describeApiError, ErrorState, FieldGroup, SectionBand } from '@/ui';

type FormKey =
  | 'sample_number'
  | 'sample_kind'
  | 'sample_description'
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

/** 시료를 식별하고 분류하는 칸 — PM 이 반입 시 문서·조회·라벨에서 옮겨 적는다.
 *
 * ⚠️ `sample_code` 는 여기 없다 (ADR-0002 결정 2). DB 컬럼과 기존 데이터는 그대로
 * 두지만 사람이 입력하는 칸에서는 내렸다 — 앱 코드 어디에서도 읽지 않는 값이었고,
 * 서버가 비어 있을 때 `sample_number` 로 채운다.
 */
const SAMPLE_FIELDS: readonly { key: FormKey; labelKey: string; required?: boolean }[] = [
  { key: 'sample_number', labelKey: 'sampleNumber', required: true },
  { key: 'sample_description', labelKey: 'sampleDescription' },
  { key: 'label_number', labelKey: 'labelNumber' },
  { key: 'smsn', labelKey: 'smsn' },
  { key: 'serial_number', labelKey: 'serialNumber' },
  { key: 'assigned_team', labelKey: 'assignedTeam' },
  { key: 'sender', labelKey: 'sender' },
  { key: 'receiver', labelKey: 'receiver' },
];

/** 엑셀에서 그대로 옮겨온 원문 칸 (ADR-0002 결정 9).
 *
 * 이 넷은 한 칸에 여러 값이 줄바꿈으로 쌓여 있고 칸끼리 개수가 맞지 않아 자동으로
 * 짝지을 수 없다. 그래서 변환하지 않고 보존한다 — 새 반입·반출은 custody 패널에
 * 적고, 여기 있는 원문은 사람이 옮길 때까지 한 줄도 잃지 않는다.
 */
const LEGACY_FIELDS: readonly { key: FormKey; labelKey: string }[] = [
  { key: 'intake_cert', labelKey: 'intakeCert' },
  { key: 'received_date', labelKey: 'receivedDate' },
  { key: 'released_date', labelKey: 'releasedDate' },
  { key: 'note', labelKey: 'note' },
];

/** 값 어휘의 SSOT 는 커널이지만(SAMPLE_KINDS/TEST_CATEGORIES) DB CHECK 는 없다.
 * 그래서 기존 행이 목록 밖의 값을 갖고 있을 수 있고, 드롭다운이 그것을 조용히
 * 다른 값으로 바꾸면 안 된다 — `optionsFor` 가 현재 값을 항상 포함시킨다. */
const SAMPLE_KINDS = ['Device', 'Accessory'] as const;
const TEST_CATEGORIES = ['Conduction', 'Radiation'] as const;

function optionsFor(vocabulary: readonly string[], current: string): string[] {
  return current !== '' && !vocabulary.includes(current)
    ? [...vocabulary, current]
    : [...vocabulary];
}

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
    sample_kind: sample?.sample_kind ?? '',
    sample_description: sample?.sample_description ?? '',
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
    sample_kind: nullable(values.sample_kind),
    sample_description: nullable(values.sample_description),
    // Accessory 는 Conducted/Radiated 를 갖지 않는다 (ADR-0002 결정 8). 종류를
    // Accessory 로 바꾸면 숨긴 칸의 옛 값이 남아 흘러가지 않도록 여기서 비운다.
    test_category: values.sample_kind === 'Accessory' ? null : nullable(values.test_category),
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
            <FieldGroup
              label={t('routes.sampleInventory.editor.fields.sampleKind')}
              htmlFor="sample-editor-sample_kind"
            >
              <select
                id="sample-editor-sample_kind"
                data-testid="sample-editor-sample_kind"
                value={values.sample_kind}
                onChange={(event) => update('sample_kind', event.target.value)}
              >
                <option value="" />
                {optionsFor(SAMPLE_KINDS, values.sample_kind).map((kind) => (
                  <option key={kind} value={kind}>
                    {kind}
                  </option>
                ))}
              </select>
            </FieldGroup>
            {/* Accessory 는 Conducted/Radiated 를 갖지 않는다 (ADR-0002 결정 8). */}
            {values.sample_kind !== 'Accessory' && (
              <FieldGroup
                label={t('routes.sampleInventory.editor.fields.testCategory')}
                htmlFor="sample-editor-test_category"
              >
                <select
                  id="sample-editor-test_category"
                  data-testid="sample-editor-test_category"
                  value={values.test_category}
                  onChange={(event) => update('test_category', event.target.value)}
                >
                  <option value="" />
                  {optionsFor(TEST_CATEGORIES, values.test_category).map((category) => (
                    <option key={category} value={category}>
                      {category}
                    </option>
                  ))}
                </select>
              </FieldGroup>
            )}
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
          <legend>{t('routes.sampleInventory.legacyFields')}</legend>
          <p className="sample-editor__legacy-hint">
            {t('routes.sampleInventory.legacyFieldsHint')}
          </p>
          <div className="sample-editor__grid">
            {LEGACY_FIELDS.map(({ key, labelKey }) => (
              <FieldGroup
                key={key}
                label={t(`routes.sampleInventory.editor.fields.${labelKey}`)}
                htmlFor={`sample-editor-${key}`}
              >
                <textarea
                  id={`sample-editor-${key}`}
                  data-testid={`sample-editor-${key}`}
                  rows={3}
                  value={values[key]}
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
