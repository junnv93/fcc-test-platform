/**
 * i18n SSOT — lightweight en/ko message routing (Increment 4,
 * `fe-i18n-en-ko-parity`, 2026-06-13).
 *
 * The `apps/web` UI strings were all Korean inline literals (no localisation).
 * The equipment reference platform routes copy through an i18n layer with a
 * ko/en key-parity pre-commit gate (`scripts/check-i18n-keys.mjs`). FCC adopts
 * the same shape, but **without** a heavyweight runtime dependency: this is a
 * Vite SPA (no SSR), so a self-contained `t()` resolver over two JSON message
 * bundles is sufficient and adds zero new npm dependencies (the conformance
 * seal forbids un-vetted client deps).
 *
 * Design:
 *   - `SUPPORTED_LOCALES` (['ko','en']) is the single locale-token SSOT.
 *   - `DEFAULT_LOCALE = 'en'` → English-first. With no stored override the UI
 *     renders the `en` bundle; testers switch to Korean via the header locale
 *     toggle (the choice persists in localStorage). The ko/en bundles have an
 *     enforced key-parity gate, so the default-locale fallback never renders a
 *     bare key for either locale.
 *   - Messages live in `src/locales/{ko,en}.json` (nested namespaces:
 *     common / ui / errors / auth / routes). They are flattened to dotted
 *     keys at module load; `t('routes.overview.title')` resolves against the
 *     current locale, falling back to the default locale, then the raw key.
 *   - A tiny external store (`subscribe`/`getLocale`/`setLocale`) lets the
 *     `useT()` hook re-render components via React 18 `useSyncExternalStore`
 *     when the locale changes. Non-component modules (e.g. `ui/errors.ts`)
 *     import `t` directly — it reads the live locale at call time.
 *
 * Parity is sealed cross-language by `tests/test_frontend_i18n_parity.py`
 * (ko key-set == en key-set, no inline Hangul UI literal outside this layer)
 * and `apps/web/tests/i18n-parity.test.ts` (runtime resolver behaviour).
 */
import { useSyncExternalStore } from 'react';

import enMessages from '@/locales/en.json';
import koMessages from '@/locales/ko.json';

/** Locale-token SSOT. Adding a locale is an intentional code change here. */
export const SUPPORTED_LOCALES = ['ko', 'en'] as const;

export type Locale = (typeof SUPPORTED_LOCALES)[number];

/** English-first: testers switch to Korean via the header locale toggle. Also
 *  the fallback locale for `t()` — never user-visible because ko/en keys are at
 *  parity (`tests/test_frontend_i18n_parity.py`). */
export const DEFAULT_LOCALE: Locale = 'en';

/** localStorage key persisting the operator's locale choice across reloads. */
export const LOCALE_STORAGE_KEY = 'fcc-locale';

/** Interpolation params for `{token}` placeholders in a message. */
export type TranslationParams = Readonly<Record<string, string | number>>;

interface MessageTree {
  readonly [key: string]: string | MessageTree;
}

/** Flatten a nested message tree to dotted keys (`a.b.c` → string). */
function flatten(tree: MessageTree, prefix = ''): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') {
      out[path] = value;
    } else {
      Object.assign(out, flatten(value, path));
    }
  }
  return out;
}

const MESSAGES: Readonly<Record<Locale, Readonly<Record<string, string>>>> = {
  ko: flatten(koMessages),
  en: flatten(enMessages),
};

export function isSupportedLocale(value: unknown): value is Locale {
  return typeof value === 'string' && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

function resolveInitialLocale(): Locale {
  try {
    const stored = globalThis.localStorage?.getItem(LOCALE_STORAGE_KEY);
    if (isSupportedLocale(stored)) return stored;
  } catch {
    // localStorage may be unavailable (privacy mode / SSR-less test env).
  }
  return DEFAULT_LOCALE;
}

let currentLocale: Locale = resolveInitialLocale();
const listeners = new Set<() => void>();

/**
 * Mirror the active locale onto `<html lang>` — the attribute a screen reader
 * reads to choose its speech engine. Owned by THIS layer rather than by a route
 * effect: `setLocale` is the only mutation point for the locale, so applying the
 * attribute here makes "the operator toggled the locale but `lang` still says
 * the old language" structurally impossible. A per-screen `useEffect` would
 * instead have to be remembered on every new route.
 *
 * `public/lang-init.js` does the same thing pre-paint (it runs before this
 * module exists); the two are sealed against drift by
 * `tests/test_frontend_architecture_conformance.py::TestDocumentLangFollowsLocale`.
 */
function applyDocumentLang(locale: Locale): void {
  try {
    globalThis.document?.documentElement.setAttribute('lang', locale);
  } catch {
    // No DOM (non-browser test env) — the locale still switches in memory.
  }
}

// Boot-time application: re-assert the resolved locale even if the pre-paint
// script was dropped or blocked, so the attribute never disagrees with `t()`.
applyDocumentLang(currentLocale);

/** Current active locale (module-level, read by both `t` and the store). */
export function getLocale(): Locale {
  return currentLocale;
}

/** Change the active locale, persist it, and notify subscribers. No-op for an
 *  unsupported token (defensive — the SSOT is the only allowed token set). */
export function setLocale(locale: Locale): void {
  if (!isSupportedLocale(locale) || locale === currentLocale) return;
  currentLocale = locale;
  applyDocumentLang(locale);
  try {
    globalThis.localStorage?.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Persistence is best-effort; an in-memory switch still works this session.
  }
  for (const fn of listeners) fn();
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => {
    listeners.delete(onChange);
  };
}

function interpolate(message: string, params?: TranslationParams): string {
  if (!params) return message;
  return message.replace(/\{(\w+)\}/gu, (match, token: string) =>
    token in params ? String(params[token]) : match,
  );
}

/**
 * Resolve a dotted message key for the active locale.
 *
 * Resolution order: active locale → default locale → the raw key (so a missing
 * key is loud in the UI rather than rendering blank). `{token}` placeholders
 * are filled from `params`.
 */
export function t(key: string, params?: TranslationParams): string {
  const message = MESSAGES[currentLocale][key] ?? MESSAGES[DEFAULT_LOCALE][key] ?? key;
  return interpolate(message, params);
}

export interface UseTranslation {
  readonly t: (key: string, params?: TranslationParams) => string;
  readonly locale: Locale;
  readonly setLocale: (locale: Locale) => void;
}

/**
 * React hook — subscribes the calling component to locale changes so a locale
 * switch re-renders translated copy without a page reload (React 18
 * `useSyncExternalStore`, concurrent-safe, no tearing).
 */
export function useT(): UseTranslation {
  const locale = useSyncExternalStore(subscribe, getLocale, getLocale);
  return {
    t: (key: string, params?: TranslationParams): string => t(key, params),
    locale,
    setLocale,
  };
}
