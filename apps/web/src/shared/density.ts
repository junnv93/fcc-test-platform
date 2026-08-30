/**
 * Density preference SSOT (design-followup #3, 2026-06-20).
 *
 * "Dense but readable" is reified in global.css as the `[data-density="compact"]`
 * token override (28px row / 4·8 inner padding) vs the comfortable default
 * (36px / 8·12). This module owns the *preference* side: the localStorage key,
 * the read, and the DOM application. Two consumers share it:
 *
 *   - `DensityToggle.tsx` — the in-header control (runtime toggle).
 *   - `public/density-boot.js` — a static `self`-origin boot script that applies
 *     the stored density BEFORE the app bundle paints, so a compact user never
 *     sees a comfortable→compact reflow (FOUC/CLS). That script cannot import
 *     this module (it runs pre-bundle, and the CSP forbids inline script), so it
 *     hard-codes the key string; `tests/density-boot-ssot.test.ts` seals the two
 *     against drift.
 */

/** localStorage key for the persisted density preference. */
export const DENSITY_STORAGE_KEY = 'fcc-density';

export type Density = 'comfortable' | 'compact';

export const DENSITY_COMFORTABLE: Density = 'comfortable';
export const DENSITY_COMPACT: Density = 'compact';

/** Read the stored preference, defaulting to comfortable when absent/unreadable
 *  (private mode, SSR). Only `compact` is persisted as a non-default. */
export function readStoredDensity(): Density {
  if (typeof window === 'undefined') return DENSITY_COMFORTABLE;
  try {
    return window.localStorage.getItem(DENSITY_STORAGE_KEY) === DENSITY_COMPACT
      ? DENSITY_COMPACT
      : DENSITY_COMFORTABLE;
  } catch {
    return DENSITY_COMFORTABLE;
  }
}

/** Apply (or clear) the `data-density` attribute that drives the compact token
 *  override. Comfortable is the absence of the attribute (the default tokens). */
export function applyDensity(density: Density): void {
  if (typeof document === 'undefined') return;
  if (density === DENSITY_COMPACT) {
    document.documentElement.dataset.density = DENSITY_COMPACT;
  } else {
    delete document.documentElement.dataset.density;
  }
}

/** Persist the preference (best-effort; storage may be unavailable). */
export function storeDensity(density: Density): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(DENSITY_STORAGE_KEY, density);
  } catch {
    /* private mode / quota — the in-memory toggle still works this session. */
  }
}
