import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInThisContext } from 'node:vm';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_LOCALE, LOCALE_STORAGE_KEY, setLocale, SUPPORTED_LOCALES } from '@/i18n';

/**
 * S1 — `<html lang>` follows the active locale (W4-A M-1, 2026-07-31).
 *
 * `lang` is what tells a screen reader WHICH speech engine to use. With
 * `<html lang="ko">` hard-coded and `DEFAULT_LOCALE = 'en'`, the default state
 * reads English sentences through Korean pronunciation rules: the screen looks
 * perfect, only the *delivery* fails — so visual QA never finds it.
 *
 * Two layers have to agree, and this file exercises both AS BEHAVIOUR (the
 * cross-file literal drift is sealed separately by
 * `tests/test_frontend_architecture_conformance.py::TestDocumentLangFollowsLocale`):
 *
 *   1. `public/lang-init.js` — pre-paint, runs before the app bundle, so the very
 *      first paint already declares the stored locale. It cannot import the TS
 *      SSOT (it executes before any module graph exists, and CSP is
 *      `script-src 'self'` so it cannot be inlined either), hence the mirrored
 *      literals. Here we EXECUTE it against jsdom rather than grep it — a script
 *      that contains the right tokens but resolves them in the wrong order is
 *      still broken, and only execution catches that.
 *   2. `src/i18n/index.ts` — owns the runtime reflection. `setLocale` is the only
 *      mutation point for the locale, so applying `lang` there makes
 *      "toggled the locale but `lang` stayed" structurally impossible; no route
 *      or component carries an effect of its own.
 */

const BOOT = readFileSync(resolve(process.cwd(), 'public/lang-init.js'), 'utf-8');
const INDEX_HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf-8');

/** Run the pre-paint boot script against the jsdom document, as the browser would. */
function runBootScript(): void {
  document.documentElement.removeAttribute('lang');
  runInThisContext(BOOT);
}

/** The "other" locale, derived from the SSOT — never a hard-coded token. The
 *  throw doubles as a vacuity guard: a single-locale SSOT would make every
 *  switch assertion below trivially true. */
const [otherLocale] = SUPPORTED_LOCALES.filter((locale) => locale !== DEFAULT_LOCALE);
if (otherLocale === undefined) {
  throw new Error('SUPPORTED_LOCALES must expose a locale other than DEFAULT_LOCALE');
}
const NON_DEFAULT_LOCALE: typeof DEFAULT_LOCALE = otherLocale;

describe('pre-paint lang boot script', () => {
  beforeEach(() => {
    localStorage.removeItem(LOCALE_STORAGE_KEY);
  });

  afterEach(() => {
    localStorage.removeItem(LOCALE_STORAGE_KEY);
  });

  it('declares the stored locale before the app bundle runs', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, NON_DEFAULT_LOCALE);

    runBootScript();

    expect(document.documentElement.lang).toBe(NON_DEFAULT_LOCALE);
  });

  it('falls back to the default locale when nothing is stored', () => {
    runBootScript();

    expect(document.documentElement.lang).toBe(DEFAULT_LOCALE);
  });

  it('ignores an unsupported stored token (mirrors resolveInitialLocale)', () => {
    localStorage.setItem(LOCALE_STORAGE_KEY, 'de');

    runBootScript();

    expect(document.documentElement.lang).toBe(DEFAULT_LOCALE);
  });

  it('is loaded from index.html before the app module (pre-paint guarantee)', () => {
    const bootIdx = INDEX_HTML.indexOf('/lang-init.js');
    const appIdx = INDEX_HTML.indexOf('/src/main.tsx');
    expect(bootIdx).toBeGreaterThan(-1);
    expect(appIdx).toBeGreaterThan(-1);
    expect(bootIdx).toBeLessThan(appIdx);
  });

  it('agrees with the static index.html lang attribute (no-JS / pre-script window)', () => {
    expect(INDEX_HTML).toContain(`<html lang="${DEFAULT_LOCALE}">`);
  });
});

describe('runtime lang reflection', () => {
  it('mirrors every locale switch onto <html lang>', () => {
    setLocale(DEFAULT_LOCALE);
    expect(document.documentElement.lang).toBe(DEFAULT_LOCALE);

    setLocale(NON_DEFAULT_LOCALE);
    expect(document.documentElement.lang).toBe(NON_DEFAULT_LOCALE);

    setLocale(DEFAULT_LOCALE);
    expect(document.documentElement.lang).toBe(DEFAULT_LOCALE);
  });

  it('applies the resolved locale at module load, not only on a later switch', async () => {
    // A user whose stored choice differs from the static index.html attribute
    // must not be left with the boot-time value if the boot script is ever
    // dropped: the module itself re-asserts the truth as it initialises.
    localStorage.setItem(LOCALE_STORAGE_KEY, NON_DEFAULT_LOCALE);
    document.documentElement.removeAttribute('lang');
    vi.resetModules();

    await import('@/i18n');

    expect(document.documentElement.lang).toBe(NON_DEFAULT_LOCALE);
    localStorage.removeItem(LOCALE_STORAGE_KEY);
  });
});
