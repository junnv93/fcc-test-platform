import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { GLOBAL_SHORTCUTS, SHORTCUTS } from '@/shared/shortcuts';

/**
 * Shortcut SSOT seal (design-followup #1, 2026-06-20).
 *
 * Before the `@/shared/shortcuts` SSOT, the shortcut universe lived in two
 * hand-maintained arrays (AppLayout's `useHotkeys` input + ShortcutHelp's row
 * table) kept in step by a comment. These guards make divergence a build
 * failure instead of a silent UX bug:
 *
 *   1. Every `descriptionKey` resolves in BOTH locale bundles (ko + en) — a
 *      shortcut added without copy fails here, not in production.
 *   2. The handler-completeness for global shortcuts is enforced at the type
 *      level (AppLayout's `Record<GlobalShortcutId, …>`); this file additionally
 *      asserts the global/modal partition is well-formed (every global shortcut
 *      has a non-empty sequence; Esc is the only modal shortcut).
 *
 * The "ShortcutHelp renders exactly the SSOT sequences" half of the contract is
 * covered by `tests/ui/shortcut-help.test.tsx` (render-level), so this file
 * stays a pure data/locale gate with no DOM dependency.
 */

interface LocaleNode {
  [key: string]: string | LocaleNode;
}

function loadLocale(name: string): LocaleNode {
  const path = resolve(process.cwd(), `src/locales/${name}.json`);
  return JSON.parse(readFileSync(path, 'utf-8')) as LocaleNode;
}

/** Resolve a dotted i18n key against a locale tree, or undefined if absent. */
function resolveKey(tree: LocaleNode, dottedKey: string): string | undefined {
  let node: string | LocaleNode | undefined = tree;
  for (const segment of dottedKey.split('.')) {
    if (typeof node !== 'object' || node === null) return undefined;
    node = node[segment];
  }
  return typeof node === 'string' ? node : undefined;
}

const KO = loadLocale('ko');
const EN = loadLocale('en');

describe('shortcuts SSOT', () => {
  it('every descriptionKey resolves in both ko and en locales', () => {
    for (const shortcut of SHORTCUTS) {
      expect(resolveKey(KO, shortcut.descriptionKey), `ko: ${shortcut.descriptionKey}`).toBeTypeOf(
        'string',
      );
      expect(resolveKey(EN, shortcut.descriptionKey), `en: ${shortcut.descriptionKey}`).toBeTypeOf(
        'string',
      );
    }
  });

  it('ids are unique', () => {
    const ids = SHORTCUTS.map((shortcut) => shortcut.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('sequences are unique', () => {
    const sequences = SHORTCUTS.map((shortcut) => shortcut.sequence);
    expect(new Set(sequences).size).toBe(sequences.length);
  });

  it('global shortcuts partition: every global shortcut has a non-empty sequence', () => {
    expect(GLOBAL_SHORTCUTS.length).toBeGreaterThan(0);
    for (const shortcut of GLOBAL_SHORTCUTS) {
      expect(shortcut.scope).toBe('global');
      expect(shortcut.sequence.trim().length).toBeGreaterThan(0);
    }
  });

  it('the only modal-scope shortcut is Esc (handled by the surface, not a global hotkey)', () => {
    const modal = SHORTCUTS.filter((shortcut) => shortcut.scope === 'modal');
    expect(modal.map((shortcut) => shortcut.sequence)).toEqual(['Esc']);
  });
});
