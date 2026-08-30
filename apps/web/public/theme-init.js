/*
 * Pre-paint theme application (tester-ux redesign Phase T).
 *
 * Runs in <head> BEFORE the app bundle so <html data-theme> is set before the
 * first paint — no light-then-dark flash for chamber-room (dark) operators.
 * CSP is `script-src 'self'`, so this is an external same-origin file rather
 * than an inline script.
 *
 * SSOT: the storage key and attribute below MUST equal `THEME_STORAGE_KEY` /
 * `THEME_ATTRIBUTE` in `src/theme/index.ts`; the equality is sealed by
 * `tests/test_frontend_theme_toggle.py` so the pre-paint script and the React
 * store can never drift. Resolution order mirrors `resolveInitialTheme()`:
 * stored choice → OS preference → light default.
 */
(function () {
  var STORAGE_KEY = 'fcc-theme';
  var ATTRIBUTE = 'data-theme';
  var theme;
  try {
    var stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'light' || stored === 'dark') {
      theme = stored;
    }
  } catch (e) {
    /* localStorage unavailable (privacy mode) — fall through to OS preference. */
  }
  if (!theme) {
    try {
      theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    } catch (e) {
      theme = 'light';
    }
  }
  document.documentElement.setAttribute(ATTRIBUTE, theme);
})();
