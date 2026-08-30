/*
 * Density FOUC guard (design-followup #3, 2026-06-20).
 *
 * Served from the same origin as the app (CSP `script-src 'self'`) and loaded
 * synchronously in <head> BEFORE the app bundle, so a compact-density user's
 * first paint is already compact — no comfortable(36px)→compact(28px) row
 * reflow (CLS) once React hydrates.
 *
 * This file runs pre-bundle and cannot import the TS SSOT, so the storage key
 * and the `compact` token are inlined; `tests/density-boot-ssot.test.ts` seals
 * them against `src/shared/density.ts::DENSITY_STORAGE_KEY` so the two never
 * drift. Keep this script tiny and dependency-free.
 */
(function () {
  try {
    if (window.localStorage.getItem('fcc-density') === 'compact') {
      document.documentElement.dataset.density = 'compact';
    }
  } catch (err) {
    /* localStorage blocked (private mode / disabled cookies) — harmless; the
       app renders at the comfortable default and the toggle still works. */
  }
})();
