import { describe, expect, it } from 'vitest';

import { DEPLOYED_PROVIDER_ID, technologiesForArea } from '@/shared/workbench-area-technologies';

import type { components } from '@/api/generated/platform-api.types';

type ProviderUiDescriptor = components['schemas']['ProviderUiDescriptor'];

/**
 * Phase 6 P6-1 — the frontend consumes the provider-owned `workbench_area →
 * technologies` mapping from the descriptor at runtime. It must NOT hardcode a
 * `{ area: [tech] }` table (ADR-0010). These tests seal that the selector reads
 * from the descriptor data and degrades safely.
 */
function descriptor(mapping: Record<string, string[]>): ProviderUiDescriptor {
  return {
    provider_id: 'fcc-unlicensed-conducted',
    display_name: 'unlicensed-conducted',
    ui_version: 1,
    features: [],
    test_plan_tables: [],
    equipment: [],
    reference_tables: [],
    correction_tables: [],
    workbench_area_technologies: mapping,
  };
}

describe('technologiesForArea', () => {
  it('returns the provider-declared technologies for a known area', () => {
    const d = descriptor({ unlicensed_conducted: ['BT', 'BLE', 'DTS', 'UNII'] });
    expect(technologiesForArea(d, 'unlicensed_conducted')).toEqual(['BT', 'BLE', 'DTS', 'UNII']);
  });

  it('returns [] for an area the descriptor does not declare', () => {
    const d = descriptor({ unlicensed_conducted: ['BT'] });
    expect(technologiesForArea(d, 'mmwave')).toEqual([]);
  });

  it('returns [] for an undefined/null descriptor (pre-fetch)', () => {
    expect(technologiesForArea(undefined, 'unlicensed_conducted')).toEqual([]);
    expect(technologiesForArea(null, 'unlicensed_conducted')).toEqual([]);
  });

  it('preserves the provider-defined technology order', () => {
    const d = descriptor({ unlicensed_conducted: ['UNII', 'BT', 'DTS', 'BLE'] });
    expect(technologiesForArea(d, 'unlicensed_conducted')).toEqual(['UNII', 'BT', 'DTS', 'BLE']);
  });
});

describe('DEPLOYED_PROVIDER_ID', () => {
  it('identifies the single deployed headless provider', () => {
    // The report surface reads THIS provider's descriptor to resolve an area's
    // technology scope. The identity is a deployment fact; the technology
    // *values* still come from the descriptor (ADR-0010), sealed above.
    expect(DEPLOYED_PROVIDER_ID).toBe('fcc-unlicensed-conducted');
  });
});
