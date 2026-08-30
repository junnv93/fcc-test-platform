/**
 * Deterministic browser contract for the current test-plan generation API.
 *
 * The live-stack and static-preview lanes must exercise the same catalogue
 * shape. Keeping this response in one fixture prevents a route from silently
 * falling back to the retired scope-options contract when the production UI
 * moves to catalogue → preview → job generation.
 */

/**
 * The limits object and its scalars now live in `tests/helpers/`, shared with
 * the vitest suites that stub the same catalogue — see that module for why one
 * definition replaced four. Re-exported under the historical names so the
 * browser specs that read them are unchanged.
 */
import {
  GENERATION_CACHE_PAGE_LIMIT,
  GENERATION_DOM_ROW_LIMIT,
  GENERATION_IDLE_TIMEOUT_MS,
  GENERATION_PAGE_SIZE,
  GENERATION_SEEDED_ROWS,
  TEST_PLAN_GENERATION_LIMITS,
} from '../../helpers/test-plan-generation-limits';

export const GENERATION_BROWSER_PAGE_SIZE = GENERATION_PAGE_SIZE;
export const GENERATION_BROWSER_CACHE_PAGE_LIMIT = GENERATION_CACHE_PAGE_LIMIT;
export const GENERATION_BROWSER_DOM_ROW_LIMIT = GENERATION_DOM_ROW_LIMIT;
export const GENERATION_BROWSER_SEEDED_ROWS = GENERATION_SEEDED_ROWS;
export const GENERATION_BROWSER_IDLE_TIMEOUT_MS = GENERATION_IDLE_TIMEOUT_MS;

export const GENERATION_BROWSER_LIMITS = TEST_PLAN_GENERATION_LIMITS;

/**
 * The values are deterministic test data, while the field names and union
 * shape are the generated OpenAPI contract. Revisions are fixture identities;
 * they are deliberately not presented as production catalogue revisions.
 */
export const GENERATION_BROWSER_CATALOGUE = {
  catalogues: {
    BT: {
      technology: 'BT',
      stages: [],
      axes: [
        { name: 'packets', values: ['DH5'] },
        { name: 'sub_families', values: ['BR'] },
        { name: 'modes', values: ['SISO'] },
        { name: 'test_types', values: ['Pk power'] },
        { name: 'antennas', values: ['ANT1'] },
      ],
      bands_per_subfamily: { BR: ['2.4G'] },
      revision: 'catalogue:browser-bt',
      sha256: 'b'.repeat(64),
      limits: GENERATION_BROWSER_LIMITS,
    },
    BLE: {
      technology: 'BLE',
      stages: [],
      axes: [
        {
          name: 'sub_families',
          values: ['LE_1M', 'LE_2M', 'LE_CODED_125K', 'LE_CODED_500K'],
        },
        { name: 'phys', values: ['PHY_1M', 'PHY_2M', 'PHY_CODED_S2', 'PHY_CODED_S8'] },
        {
          name: 'test_types',
          values: ['Pk power', 'Av power', 'Duty', 'OBW', 'PSD', 'CSE', 'CBE'],
        },
        { name: 'antennas', values: ['ANT1', 'ANT2', 'ALL1', 'ALL2'] },
        { name: 'modulations', values: ['255pkt', '37pkt'] },
      ],
      bands_per_subfamily: {
        LE_1M: ['2.4G'],
        LE_2M: ['2.4G'],
        LE_CODED_125K: ['2.4G'],
        LE_CODED_500K: ['2.4G'],
      },
      revision: 'catalogue:browser-ble',
      sha256: 'd'.repeat(64),
      limits: GENERATION_BROWSER_LIMITS,
    },
    WLAN: {
      technology: 'WLAN',
      stages: ['base', 'pretest', 'main_test'],
      axes: [
        { name: 'technologies', values: ['11ax'] },
        { name: 'bands', values: ['2.4GHz'] },
        { name: 'bandwidths', values: ['20MHz'] },
        { name: 'channels', values: ['1'] },
        { name: 'modulations', values: ['802.11ax'] },
        { name: 'tests', values: ['PSD'] },
        { name: 'antennas', values: ['ANT1'] },
      ],
      bands_per_subfamily: { '802.11ax_2.4': ['2.4G'] },
      revision: 'catalogue:browser-wlan',
      sha256: 'c'.repeat(64),
      limits: GENERATION_BROWSER_LIMITS,
    },
  },
} as const;
