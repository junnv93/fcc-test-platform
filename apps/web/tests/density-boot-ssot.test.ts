import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { DENSITY_COMPACT, DENSITY_STORAGE_KEY } from '@/shared/density';

/**
 * Density boot-script drift seal (design-followup #3, 2026-06-20).
 *
 * `public/density-boot.js` runs pre-bundle (CSP forbids inline script, and it
 * must execute before the app paints to prevent the comfortable→compact reflow),
 * so it cannot import the TS SSOT and instead inlines the storage key + the
 * `compact` token. This gate fails the build if those inlined literals drift
 * from `src/shared/density.ts`, AND if the script ever stops being loaded from
 * index.html (a silently dropped guard reads as "FOUC fixed" when it is not).
 */

const BOOT = readFileSync(resolve(process.cwd(), 'public/density-boot.js'), 'utf-8');
const INDEX_HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf-8');

describe('density boot script SSOT', () => {
  it('inlines the same storage key as the density SSOT', () => {
    expect(BOOT).toContain(`'${DENSITY_STORAGE_KEY}'`);
  });

  it('inlines the same compact token as the density SSOT', () => {
    expect(BOOT).toContain(`'${DENSITY_COMPACT}'`);
  });

  it('applies the data-density attribute (the compact token driver)', () => {
    expect(BOOT).toContain('dataset.density');
  });

  it('does not reference any other fcc-* storage key (no stray literal)', () => {
    const keys = BOOT.match(/'fcc-[a-z-]+'/g) ?? [];
    for (const key of keys) {
      expect(key).toBe(`'${DENSITY_STORAGE_KEY}'`);
    }
  });

  it('is loaded as an external self-origin script from index.html (CSP-safe, pre-bundle)', () => {
    expect(INDEX_HTML).toContain('/density-boot.js');
    // The script tag must precede the app module so it runs before first paint.
    const bootIdx = INDEX_HTML.indexOf('/density-boot.js');
    const appIdx = INDEX_HTML.indexOf('/src/main.tsx');
    expect(bootIdx).toBeGreaterThan(-1);
    expect(appIdx).toBeGreaterThan(-1);
    expect(bootIdx).toBeLessThan(appIdx);
  });
});
