/**
 * SampleCard — PM·RF 시료 인벤토리 통합 카드 (Phase F, 2026-06-23).
 *
 * Renders one ``SampleEnvelope`` as a card with: the PM 물류/자산 칸
 * (label/serial/assigned_team/물류일자/반입증), the 시료 식별 칸 (시료번호/
 * test_category/SMSN), and — when ``latest_intake`` is present — the 시험원 입고 칸
 * showing the latest intake's firmware (bl/ap/cp/csc) + rf_cal + hw_rev +
 * intake_date (read back via the project-detail envelope). The project detail
 * carries only the latest intake + an ``intake_count`` (not the full append-only
 * history), so the history badge reads the backend-supplied count rather than an
 * array length.
 *
 * Token-driven (no inline hex/px — sealed by verify-frontend-conformance); the
 * ``.sample-card*`` classes live in ``global.css``. The team is rendered as a
 * visible label (color alone never conveys meaning — §5.2 a11y).
 */
import { useT } from '@/i18n';

export interface SampleCardIntake {
  readonly sample_intake_id?: string | null;
  readonly intake_date?: string | null;
  readonly bl?: string | null;
  readonly ap?: string | null;
  readonly cp?: string | null;
  readonly csc?: string | null;
  readonly rf_cal?: string | null;
  readonly hw_rev?: string | null;
  readonly note?: string | null;
}

export interface SampleCardSample {
  readonly sample_number?: string | null;
  readonly sample_code?: string | null;
  readonly test_category?: string | null;
  readonly smsn?: string | null;
  readonly serial_number?: string | null;
  readonly label_number?: string | null;
  readonly assigned_team?: string | null;
  readonly sender?: string | null;
  readonly receiver?: string | null;
  readonly received_date?: string | null;
  readonly released_date?: string | null;
  readonly intake_cert?: string | null;
  /** 시험원 입고 최신 펌웨어/CAL 상태 (read 레이어가 created_at DESC 로 사전 선정). */
  readonly latest_intake?: SampleCardIntake | null;
  /** 입고 이력 총 개수 (백엔드 계산 — 프론트가 배열 길이로 추론하지 않음). */
  readonly intake_count?: number | null;
}

export interface SampleCardProps {
  readonly sample: SampleCardSample;
  readonly testId?: string;
}

function Field({
  label,
  value,
}: {
  readonly label: string;
  readonly value?: string | null | undefined;
}): JSX.Element | null {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div className="sample-card__field">
      <span className="sample-card__field-label">{label}</span>
      <span className="sample-card__field-value">{value}</span>
    </div>
  );
}

export function SampleCard({ sample, testId }: SampleCardProps): JSX.Element {
  const { t } = useT();
  const heading = sample.sample_number ?? sample.sample_code ?? '—';
  const team = sample.assigned_team ?? null;
  const latestIntake = sample.latest_intake ?? undefined;
  const intakeCount = sample.intake_count ?? 0;
  return (
    <article className="sample-card" data-testid={testId} aria-label={heading}>
      <header className="sample-card__head">
        <span className="sample-card__number">{heading}</span>
        {team !== null && team !== '' && (
          <span className="sample-card__team" data-testid="sample-card-team">
            {t('routes.sampleInventory.teamLabel', { team })}
          </span>
        )}
      </header>
      <div className="sample-card__grid">
        <section
          className="sample-card__section"
          aria-label={t('routes.sampleInventory.pmSection')}
        >
          <h4 className="sample-card__section-title">{t('routes.sampleInventory.pmSection')}</h4>
          <Field label={t('routes.sampleInventory.fieldLabelNumber')} value={sample.label_number} />
          <Field label={t('routes.sampleInventory.fieldSerial')} value={sample.serial_number} />
          <Field label={t('routes.sampleInventory.fieldIntakeCert')} value={sample.intake_cert} />
          <Field label={t('routes.sampleInventory.fieldReceived')} value={sample.received_date} />
          <Field label={t('routes.sampleInventory.fieldReleased')} value={sample.released_date} />
        </section>
        <section
          className="sample-card__section"
          aria-label={t('routes.sampleInventory.identitySection')}
        >
          <h4 className="sample-card__section-title">
            {t('routes.sampleInventory.identitySection')}
          </h4>
          <Field label={t('routes.sampleInventory.fieldCategory')} value={sample.test_category} />
          <Field label={t('routes.sampleInventory.fieldSmsn')} value={sample.smsn} />
          <Field label={t('routes.sampleInventory.fieldSender')} value={sample.sender} />
          <Field label={t('routes.sampleInventory.fieldReceiver')} value={sample.receiver} />
        </section>
      </div>
      {latestIntake !== undefined && (
        <section
          className="sample-card__intake"
          aria-label={t('routes.sampleInventory.intakeSection')}
          data-testid="sample-card-intake"
        >
          <h4 className="sample-card__section-title">
            {t('routes.sampleInventory.intakeSection')}
            {intakeCount > 1
              ? ` ${t('routes.sampleInventory.intakeHistoryCount', {
                  count: String(intakeCount),
                })}`
              : ''}
          </h4>
          <div className="sample-card__grid">
            <Field
              label={t('routes.sampleInventory.fieldIntakeDate')}
              value={latestIntake.intake_date}
            />
            <Field label={t('routes.sampleInventory.fieldBl')} value={latestIntake.bl} />
            <Field label={t('routes.sampleInventory.fieldAp')} value={latestIntake.ap} />
            <Field label={t('routes.sampleInventory.fieldCp')} value={latestIntake.cp} />
            <Field label={t('routes.sampleInventory.fieldCsc')} value={latestIntake.csc} />
            <Field label={t('routes.sampleInventory.fieldRfCal')} value={latestIntake.rf_cal} />
            <Field label={t('routes.sampleInventory.fieldHwRev')} value={latestIntake.hw_rev} />
          </div>
        </section>
      )}
    </article>
  );
}
