import { describe, expect, it } from 'vitest';

import {
  VISUAL_FIXED_NOW,
  VISUAL_ROUTE_PATTERNS,
  VISUAL_ROUTE_DEFINITIONS,
  WAVE_1_VISUAL_ROUTE_DEFINITIONS,
  wave1VisualSnapshotName,
  visualSnapshotName,
} from '@/../tests/e2e/helpers/visual-fixture';

describe('deterministic visual fixture contract', () => {
  it('covers exactly the seven representative route surfaces', () => {
    expect(VISUAL_ROUTE_DEFINITIONS).toHaveLength(7);
    expect(new Set(VISUAL_ROUTE_DEFINITIONS.map((route) => route.key)).size).toBe(7);
    for (const route of VISUAL_ROUTE_DEFINITIONS) {
      expect(route.path).not.toMatch(/^https?:/u);
      expect(route.ready).toMatch(/^\[data-testid=/u);
    }
  });

  it('names every desktop visual state without collisions', () => {
    const names = VISUAL_ROUTE_DEFINITIONS.flatMap((route) =>
      ['light', 'dark'].flatMap((theme) =>
        ['comfortable', 'compact'].flatMap((density) =>
          [1280, 1440].map((width) =>
            visualSnapshotName(
              route.key,
              theme as 'light' | 'dark',
              density as 'comfortable' | 'compact',
              width,
            ),
          ),
        ),
      ),
    );
    expect(names).toHaveLength(56);
    expect(new Set(names).size).toBe(56);
    expect(names.every((name) => name.endsWith('.png'))).toBe(true);
  });

  it('defines exactly five bilingual Wave 1 canonical surfaces', () => {
    expect(WAVE_1_VISUAL_ROUTE_DEFINITIONS).toHaveLength(5);
    expect(new Set(WAVE_1_VISUAL_ROUTE_DEFINITIONS.map((route) => route.key)).size).toBe(5);
    for (const route of WAVE_1_VISUAL_ROUTE_DEFINITIONS) {
      expect(route.path).not.toMatch(/^https?:/u);
      expect(route.ready).toMatch(/^\[data-testid=/u);
      expect(route.titleKey).toMatch(/^routes\.[^.]+\./u);
    }

    const names = WAVE_1_VISUAL_ROUTE_DEFINITIONS.flatMap((route) =>
      (['ko', 'en'] as const).map((locale) =>
        wave1VisualSnapshotName(route.key, locale, 'light', 'comfortable', 1440),
      ),
    );
    expect(names).toHaveLength(10);
    expect(new Set(names).size).toBe(10);
    expect(names.every((name) => /-(ko|en)-light-comfortable-1440\.png$/u.test(name))).toBe(true);
  });

  it('pins the fixture clock to the declared Seoul capture instant', () => {
    expect(VISUAL_FIXED_NOW).toBe('2026-08-02T09:00:00+09:00');
    expect(new Date(VISUAL_FIXED_NOW).toISOString()).toBe('2026-08-02T00:00:00.000Z');
  });

  it('pins the inventory fixture to the current list endpoint boundary', () => {
    expect(VISUAL_ROUTE_PATTERNS.sampleInventory.test('/platform/sample-inventory')).toBe(true);
    expect(
      VISUAL_ROUTE_PATTERNS.sampleInventory.test(
        '/platform/sample-inventory?project_id=11111111-1111-4111-8111-111111111111',
      ),
    ).toBe(true);
    expect(
      VISUAL_ROUTE_PATTERNS.sampleInventory.test(
        '/platform/projects/11111111-1111-4111-8111-111111111111/sample-inventory',
      ),
    ).toBe(false);
  });

  it('pins the projects fixture to the current provider/result API boundaries', () => {
    expect(VISUAL_ROUTE_PATTERNS.providersList.test('/platform/providers')).toBe(true);
    expect(
      VISUAL_ROUTE_PATTERNS.resultSelections.test(
        '/platform/projects/11111111-1111-4111-8111-111111111111/providers/fcc-unlicensed-conducted/result-selections?limit=1000',
      ),
    ).toBe(true);
    expect(
      VISUAL_ROUTE_PATTERNS.providersList.test(
        '/platform/projects/11111111-1111-4111-8111-111111111111',
      ),
    ).toBe(false);
    expect(
      VISUAL_ROUTE_PATTERNS.resultSelections.test(
        '/platform/projects/11111111-1111-4111-8111-111111111111/coverage',
      ),
    ).toBe(false);
  });
});
