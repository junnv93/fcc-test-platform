/**
 * Theme SSOT — light/dark operator theme store (tester-ux redesign Phase T).
 *
 * Mirrors the i18n store shape (``@/i18n``): a module-level
 * ``useSyncExternalStore`` source so a theme toggle re-renders subscribers
 * without a reload, plus localStorage persistence so the choice survives across
 * sessions (chamber-room dark is opt-in; the app default is light).
 *
 * The applied theme lives on ``<html data-theme="…">``; ``global.css`` keys its
 * dark override off ``:root[data-theme='dark']`` (explicit) and
 * ``@media (prefers-color-scheme: dark)`` (OS default when no explicit choice).
 * Pre-paint application happens in ``public/theme-init.js`` (CSP ``script-src
 * 'self'`` forbids an inline head script) — its storage key is sealed against
 * ``THEME_STORAGE_KEY`` below by ``tests/test_frontend_theme_toggle.py`` so the
 * two can never drift.
 */
import { useSyncExternalStore } from 'react';

/** Theme-token SSOT. Adding a theme is an intentional code change here. */
export const SUPPORTED_THEMES = ['light', 'dark'] as const;

export type Theme = (typeof SUPPORTED_THEMES)[number];

/** Light-first: the operator-tool default (chamber-room dark is opt-in). */
export const DEFAULT_THEME: Theme = 'light';

/** localStorage key persisting the operator's theme choice across reloads.
 *  Kept in sync with ``public/theme-init.js`` (cross-language seal). */
export const THEME_STORAGE_KEY = 'fcc-theme';

/** The DOM attribute ``global.css`` reads (``:root[data-theme='dark']``). */
export const THEME_ATTRIBUTE = 'data-theme';

export function isSupportedTheme(value: unknown): value is Theme {
  return typeof value === 'string' && (SUPPORTED_THEMES as readonly string[]).includes(value);
}

/** OS preference at call time (``light`` when matchMedia is unavailable). */
function osPrefersDark(): boolean {
  try {
    return globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  } catch {
    return false;
  }
}

/**
 * Resolve the boot theme. Order: the attribute already on ``<html>`` (set
 * pre-paint by ``theme-init.js``) → stored choice → OS preference → default.
 * Reading the attribute first keeps the React store in lockstep with whatever
 * the pre-paint script applied, so there is no flash-then-correct.
 */
export function resolveInitialTheme(): Theme {
  try {
    const attr = globalThis.document?.documentElement.getAttribute(THEME_ATTRIBUTE);
    if (isSupportedTheme(attr)) return attr;
  } catch {
    // document may be unavailable (non-DOM test env).
  }
  try {
    const stored = globalThis.localStorage?.getItem(THEME_STORAGE_KEY);
    if (isSupportedTheme(stored)) return stored;
  } catch {
    // localStorage may be unavailable (privacy mode).
  }
  return osPrefersDark() ? 'dark' : DEFAULT_THEME;
}

/** Apply a theme to the document root (the channel ``global.css`` reads). */
export function applyTheme(theme: Theme): void {
  try {
    globalThis.document?.documentElement.setAttribute(THEME_ATTRIBUTE, theme);
  } catch {
    // No DOM (test env) — the store value still drives subscribers.
  }
}

let currentTheme: Theme = resolveInitialTheme();
const listeners = new Set<() => void>();

/** Current active theme (module-level, read by the store snapshot). */
export function getTheme(): Theme {
  return currentTheme;
}

/** Change the active theme, apply it to the DOM, persist it, and notify
 *  subscribers. No-op for an unsupported token or an unchanged value. */
export function setTheme(theme: Theme): void {
  if (!isSupportedTheme(theme) || theme === currentTheme) return;
  currentTheme = theme;
  applyTheme(theme);
  try {
    globalThis.localStorage?.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Persistence is best-effort; the in-memory switch still works this session.
  }
  for (const fn of listeners) fn();
}

/** Flip between light and dark. */
export function toggleTheme(): void {
  setTheme(currentTheme === 'dark' ? 'light' : 'dark');
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

/**
 * React hook — subscribes the caller to theme changes (React 18
 * ``useSyncExternalStore``, concurrent-safe, no tearing), mirroring ``useT``.
 */
export function useTheme(): {
  readonly theme: Theme;
  readonly setTheme: (t: Theme) => void;
  readonly toggleTheme: () => void;
} {
  const theme = useSyncExternalStore(subscribe, getTheme, getTheme);
  return { theme, setTheme, toggleTheme };
}

/** Test-only reset so a suite can restore the module-level store. */
export function __resetThemeForTest(theme: Theme = DEFAULT_THEME): void {
  currentTheme = theme;
  applyTheme(theme);
  for (const fn of listeners) fn();
}
