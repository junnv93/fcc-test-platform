import { useT } from '@/i18n';
import { type ChamberFleetSummary } from '@/shared/chamber-fleet';
import { KNOWN_CHAMBER_STATUSES, MetricStrip, SectionBand, type MetricStripItem } from '@/ui';

/** Fleet-wide count summary (P12 "데이터 한눈에"). One MetricStrip: total + a
 *  count per canonical status (가용/측정중/오프라인), reusing the existing
 *  `routes.chambers.status.*` labels and the {@link KNOWN_CHAMBER_STATUSES}
 *  SSOT (no status literal in this view). A forward-compat status, if any, is
 *  surfaced as an `unknown` count rather than silently dropped. */
export function ChamberFleetSummaryStrip({
  summary,
}: {
  readonly summary: ChamberFleetSummary;
}): JSX.Element {
  const { t } = useT();
  const items: MetricStripItem[] = [
    {
      key: 'total',
      label: t('routes.chambers.fleet.total'),
      value: String(summary.total),
      valueTestId: 'chambers-fleet-total',
    },
    ...KNOWN_CHAMBER_STATUSES.map((status) => ({
      key: status,
      label: t(`routes.chambers.status.${status}`),
      value: String(summary.counts[status]),
      valueTestId: `chambers-fleet-${status}`,
    })),
    ...(summary.unknown > 0
      ? [
          {
            key: 'unknown',
            label: t('routes.chambers.fleet.unknown'),
            value: String(summary.unknown),
            valueTestId: 'chambers-fleet-unknown',
          },
        ]
      : []),
  ];
  return (
    <section aria-labelledby="chambers-fleet-heading" data-testid="chambers-fleet">
      <SectionBand title={t('routes.chambers.sectionFleet')} titleId="chambers-fleet-heading" />
      <MetricStrip ariaLabel={t('routes.chambers.fleetStripLabel')} items={items} />
    </section>
  );
}
