/*
 * Pre-paint document-language declaration (W4-A M-1, 2026-07-31).
 *
 * `<html lang>` is what a screen reader uses to pick its speech engine. The
 * attribute in index.html can only carry ONE value, so an operator who chose
 * Korean would have every sentence spoken by an English engine (or the reverse)
 * for the whole pre-bundle window — content that renders perfectly but is
 * delivered wrong, which visual QA never catches.
 *
 * Runs in <head> BEFORE the app bundle so the first paint already declares the
 * stored locale. CSP is `script-src 'self'`, so this is an external same-origin
 * file rather than an inline script — same shape as `theme-init.js`
 * (`data-theme`) and `density-boot.js` (`data-density`).
 *
 * SSOT: the storage key, the locale tokens and the default below MUST equal
 * `LOCALE_STORAGE_KEY` / `SUPPORTED_LOCALES` / `DEFAULT_LOCALE` in
 * `src/i18n/index.ts`. This script runs before any module graph exists so it
 * cannot import them; the equality is sealed by
 * `tests/test_frontend_architecture_conformance.py::TestDocumentLangFollowsLocale`
 * and the resolution ORDER (stored choice → default) — which mirrors
 * `resolveInitialLocale()` — by `tests/lang-boot-ssot.test.ts`.
 */
(function () {
  var STORAGE_KEY = 'fcc-locale';
  var LOCALES = ['ko', 'en'];
  var DEFAULT_LOCALE = 'en';
  var locale;
  try {
    var stored = window.localStorage.getItem(STORAGE_KEY);
    if (LOCALES.indexOf(stored) !== -1) {
      locale = stored;
    }
  } catch (e) {
    /* localStorage blocked (private mode / disabled cookies) — fall through to
       the default locale, which is also what the static index.html attribute
       declares, so nothing regresses. */
  }
  document.documentElement.lang = locale || DEFAULT_LOCALE;
})();
