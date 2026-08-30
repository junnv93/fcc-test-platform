export type GridPocTechnology = 'BLE' | 'BT' | 'DTS' | 'UNII';

export interface GridPocRow {
  readonly id: string;
  readonly technologies: readonly GridPocTechnology[];
  readonly testItem: string;
  readonly band: string;
  readonly modulation: string;
  readonly channel: string;
  readonly bandwidthMhz: number | null;
  readonly targetPowerDbm: number | null;
  readonly limitDbm: number | null;
  readonly sampleCount: number;
  readonly verdict: 'ready' | 'warning' | 'blocked';
  readonly generatedFromCapability: string | null;
}

const TECHNOLOGIES: readonly GridPocTechnology[] = ['BLE', 'BT', 'DTS', 'UNII'];
const MODULATIONS = ['1M', '2M', '500k_coded', '1M_SYNC_ASK', '802.11g', '802.11ax', '802.11be'];
const BANDS = ['2.4 GHz', '5 GHz', '6 GHz'];
const CHANNELS = ['CH 0', 'CH 6', 'CH 11', 'CH 36', 'CH 149', 'CH 197'];

export function createGridPocRows(count = 500): GridPocRow[] {
  return Array.from({ length: count }, (_, index) => {
    const primary = TECHNOLOGIES[index % TECHNOLOGIES.length] ?? 'BLE';
    const isCrossTech = index % 37 === 0;
    const secondary = TECHNOLOGIES[(index + 1) % TECHNOLOGIES.length] ?? 'BT';
    const technologies = isCrossTech ? [primary, secondary] : [primary];
    const modulation = MODULATIONS[index % MODULATIONS.length] ?? '1M';
    const band = BANDS[index % BANDS.length] ?? '2.4 GHz';
    const channel = CHANNELS[index % CHANNELS.length] ?? 'CH 0';
    const blocked = index % 53 === 0;
    const warning = index % 17 === 0;

    return {
      id: `row-${String(index + 1).padStart(4, '0')}`,
      technologies,
      testItem: `${primary} ${modulation} emission ${index + 1}`,
      band,
      modulation,
      channel,
      bandwidthMhz: primary === 'BLE' ? 2 : primary === 'BT' ? 1 : 20 + (index % 4) * 20,
      targetPowerDbm: Number((10 + (index % 11) * 0.5).toFixed(1)),
      limitDbm: Number((20 + (index % 7)).toFixed(1)),
      sampleCount: 1 + (index % 5),
      verdict: blocked ? 'blocked' : warning ? 'warning' : 'ready',
      generatedFromCapability: `${primary}:${modulation}`,
    };
  });
}

export function validateGridPocRow(row: GridPocRow): readonly string[] {
  const issues: string[] = [];
  if (!row.testItem.trim()) issues.push('test item required');
  if (row.targetPowerDbm == null) issues.push('target power required');
  if (row.limitDbm != null && row.targetPowerDbm != null && row.targetPowerDbm > row.limitDbm) {
    issues.push('target exceeds limit');
  }
  if (row.technologies.length > 1) issues.push('cross-tech row requires coordinator');
  return issues;
}
