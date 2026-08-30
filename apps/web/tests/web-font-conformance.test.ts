import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const require = createRequire(import.meta.url);
const WEB_ROOT = process.cwd();
const MAIN_SOURCE = readFileSync(resolve(WEB_ROOT, 'src/main.tsx'), 'utf-8');
const GLOBAL_CSS = readFileSync(resolve(WEB_ROOT, 'src/styles/global.css'), 'utf-8');
const FONT_PACKAGE = JSON.parse(
  readFileSync(require.resolve('@fontsource-variable/noto-sans-kr/package.json'), 'utf-8'),
) as { description: string; license: string; name: string };

describe('self-hosted Korean web font conformance', () => {
  it('imports the local OFL Noto Sans KR Variable package through the Vite entry graph', () => {
    expect(MAIN_SOURCE).toContain("import '@fontsource-variable/noto-sans-kr';");
    expect(FONT_PACKAGE).toMatchObject({
      name: '@fontsource-variable/noto-sans-kr',
      license: 'OFL-1.1',
    });
    expect(FONT_PACKAGE.description).toContain('Self-host');
  });

  it('makes the bundled Korean face the first global sans choice and preserves fallback safety', () => {
    const fontSans = /--font-sans:\s*([^;]+);/s.exec(GLOBAL_CSS)?.[1]?.replace(/\s+/g, ' ');

    expect(fontSans).toMatch(/^'Noto Sans KR Variable',/);
    expect(fontSans).toContain("'Apple SD Gothic Neo'");
    expect(fontSans).toContain("'Noto Sans KR'");
    expect(fontSans).toContain("'Malgun Gothic'");
    expect(fontSans).toContain('sans-serif');
    expect(GLOBAL_CSS).toContain('font-family: var(--font-sans);');
  });

  it('keeps the established title, body, and monospace policies intact', () => {
    expect(GLOBAL_CSS).toContain('--font-size-xl: 26px;');
    expect(GLOBAL_CSS).toContain('--font-size-sm: 14px;');
    expect(GLOBAL_CSS).toContain('--font-weight-normal: 400;');
    expect(GLOBAL_CSS).toContain('--font-weight-semibold: 600;');
    expect(GLOBAL_CSS).toContain(
      "--font-mono: ui-monospace, SFMono-Regular, 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;",
    );
  });
});
