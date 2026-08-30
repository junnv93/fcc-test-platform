import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import type { VisualLocale } from './visual-fixture';

/**
 * The one place an e2e spec reads the i18n bundle.
 *
 * ⚠️ **This exists because there were three copies, which is the count this
 * repository has already paid for.** `a11y.spec.ts` and
 * `ui-visual-regression.spec.ts` each carried a private `messageAt` with the same
 * body and slightly different signatures, and `test-plans-workflow.spec.ts`
 * skipped the reader entirely and hardcoded Korean copy in four places — which
 * turned red the moment a locale wave renamed one word (`Excel 파일로 가져오기`
 * → `엑셀 …`). A fourth copy was the obvious next move and would have been the
 * wrong one.
 *
 * ⚠️ **Deriving the expected text is not weaker than a literal, because the
 * proposition under test is different from the one the literal states.** A spec
 * that writes the Korean out asserts *these exact words render*; that question is
 * owned by the locale gates (`tests/test_frontend_i18n_parity.py`), which judge
 * the bundle itself. What an e2e spec is for is *the accessible name equals the
 * visible label the component renders from this key* — and that survives a copy
 * change, while a key that stops resolving fails loudly here at collection time
 * instead of fifteen seconds later as an opaque locator timeout.
 *
 * ⚠️ **Read with `fs`, not `import … from '*.json'`.** Playwright runs specs
 * through Node's ESM loader (`"type": "module"`), which rejects a JSON specifier
 * without an import attribute. `fs` sidesteps the loader question entirely and
 * costs one synchronous read at collection time.
 */
const bundles: Record<VisualLocale, unknown> = {
  ko: JSON.parse(
    readFileSync(fileURLToPath(new URL('../../../src/locales/ko.json', import.meta.url)), 'utf8'),
  ),
  en: JSON.parse(
    readFileSync(fileURLToPath(new URL('../../../src/locales/en.json', import.meta.url)), 'utf8'),
  ),
};

/**
 * Resolve a dotted i18n key against the bundle for `locale`.
 *
 * Throws — deliberately, and at collection time — when the key does not resolve
 * to a non-empty string. A missing key is a spec defect, and the alternative
 * (returning the key, or an empty string) makes it look like a UI defect.
 */
export function messageAt(key: string, locale: VisualLocale = 'ko'): string {
  const message = key
    .split('.')
    .reduce<unknown>(
      (node, part) =>
        typeof node === 'object' && node !== null
          ? (node as Record<string, unknown>)[part]
          : undefined,
      bundles[locale],
    );
  if (typeof message !== 'string' || message.length === 0) {
    throw new Error(
      `e2e locale: i18n key '${key}' does not resolve to a message in ${locale}.json`,
    );
  }
  return message;
}

/**
 * `messageAt` with `{placeholder}` slots filled, for copy the UI interpolates.
 *
 * ⚠️ **Every declared slot must be supplied.** Leaving one unfilled would let a
 * spec assert against a string still containing `{count}`, which passes only
 * because the component happens to render the same defect.
 */
export function messageWith(
  key: string,
  values: Readonly<Record<string, string | number>>,
  locale: VisualLocale = 'ko',
): string {
  const template = messageAt(key, locale);
  const filled = template.replace(/\{(\w+)\}/g, (match, slot: string) =>
    slot in values ? String(values[slot]) : match,
  );
  const unresolved = filled.match(/\{(\w+)\}/g);
  if (unresolved) {
    throw new Error(
      `e2e locale: key '${key}' still has unfilled slots ${unresolved.join(', ')} — ` +
        'supply every placeholder or the assertion is checking a defect against itself',
    );
  }
  return filled;
}
