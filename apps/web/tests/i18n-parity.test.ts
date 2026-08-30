import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  DEFAULT_LOCALE,
  getLocale,
  isSupportedLocale,
  setLocale,
  SUPPORTED_LOCALES,
  t,
} from '@/i18n';
import enMessages from '@/locales/en.json';
import koMessages from '@/locales/ko.json';

/** Flatten a nested message tree to dotted keys (mirrors the runtime helper). */
function flattenKeys(tree: unknown, prefix = '', out: string[] = []): string[] {
  for (const [k, v] of Object.entries(tree as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') flattenKeys(v, path, out);
    else out.push(path);
  }
  return out;
}

beforeEach(() => {
  // The global test setup (tests/setup.ts) pins the rendered locale to `ko`;
  // this suite asserts locale mechanics directly, so reset to the production
  // default (`en`) before each case and restore it after.
  setLocale(DEFAULT_LOCALE);
});

afterEach(() => {
  // The locale is module-global state; restore the default so cases stay
  // order-independent.
  setLocale(DEFAULT_LOCALE);
});

describe('i18n SSOT', () => {
  it('exposes the ko/en locale-token SSOT with en as default', () => {
    expect(SUPPORTED_LOCALES).toEqual(['ko', 'en']);
    expect(DEFAULT_LOCALE).toBe('en');
    expect(getLocale()).toBe('en');
  });

  it('narrows supported locale tokens', () => {
    expect(isSupportedLocale('ko')).toBe(true);
    expect(isSupportedLocale('en')).toBe(true);
    expect(isSupportedLocale('fr')).toBe(false);
    expect(isSupportedLocale(undefined)).toBe(false);
  });
});

describe('i18n key parity (runtime bundles)', () => {
  it('ko key-set === en key-set (no missing translation)', () => {
    const koKeys = new Set(flattenKeys(koMessages));
    const enKeys = new Set(flattenKeys(enMessages));
    const koOnly = [...koKeys].filter((k) => !enKeys.has(k));
    const enOnly = [...enKeys].filter((k) => !koKeys.has(k));
    expect(koOnly).toEqual([]);
    expect(enOnly).toEqual([]);
    expect(koKeys.size).toBe(enKeys.size);
  });

  it('every interpolation placeholder set matches across ko/en', () => {
    const flatKo = flattenEntries(koMessages);
    const flatEn = flattenEntries(enMessages);
    const placeholders = (s: string): string[] =>
      [...s.matchAll(/\{(\w+)\}/gu)].map((m) => m[1] ?? '').sort();
    for (const key of Object.keys(flatKo)) {
      expect(placeholders(flatEn[key] ?? '')).toEqual(placeholders(flatKo[key] ?? ''));
    }
  });
});

describe('t() resolver', () => {
  it('resolves the active locale and falls back to the raw key', () => {
    setLocale('ko');
    expect(t('common.retry')).toBe('다시 시도');
    setLocale('en');
    expect(t('common.retry')).toBe('Retry');
    expect(t('this.key.does.not.exist')).toBe('this.key.does.not.exist');
  });

  it('fills {token} placeholders from params', () => {
    setLocale('en');
    expect(t('routes.membership.assignSuccess', { user: 'alice', role: 'admin' })).toBe(
      "Granted the 'admin' role to 'alice'.",
    );
    setLocale('ko');
    expect(t('routes.membership.assignSuccess', { user: 'alice', role: 'admin' })).toBe(
      "'alice' 에게 'admin' 역할을 부여했습니다.",
    );
  });

  it('resolves the ko bundle copy when ko is active', () => {
    // The ko locale resolves the ko bundle values verbatim.
    // (routes.overview.title became routes.diagnostics.title='진단' in the
    // tester-ux Phase S-diag move.)
    setLocale('ko');
    expect(t('routes.diagnostics.title')).toBe('진단');
    expect(t('ui.statusBadge.pass')).toBe('합격');
    expect(t('errors.network')).toBe('서버에 연결할 수 없습니다.');
  });
});

function flattenEntries(
  tree: unknown,
  prefix = '',
  out: Record<string, string> = {},
): Record<string, string> {
  for (const [k, v] of Object.entries(tree as Record<string, unknown>)) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === 'object') flattenEntries(v, path, out);
    else out[path] = v as string;
  }
  return out;
}
