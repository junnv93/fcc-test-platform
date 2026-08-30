import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * Design-token conformance (card E1) — JS-toolchain gate that complements the
 * Python `test_frontend_architecture_conformance.py` (no inline hex / no raw
 * fetch) and `test_fe_phase1_ui_foundation.py` (token names exist). This file
 * adds two guards that those do not cover:
 *
 *   1. WCAG contrast of every status badge against its ACTUAL rendered
 *      background — the `--status-{kind}-bg` translucent tint composited over
 *      `--surface-bg` — not against the bare surface. A badge renders its fg
 *      text on the tint, so the tint (slightly off-surface) is the real
 *      background; gating against pure surface over-reports contrast. Both the
 *      operator-default dark theme and the opt-in light theme are held to AA
 *      normal-text (4.5:1) — status badges render badge text at normal
 *      weight/size, so the 3:1 UI-component floor is not enough. This is a
 *      regression guard, NOT a palette redesign.
 *   2. No inline `style={{ ... }}` colour-hex or `fontSize` literal in any TSX
 *      component — colour/size must flow from the global.css token classes.
 *   3. (card F3) No *imperative* DOM style assignment (`element.style.<prop> =
 *      '24px' / '#b30000'`, or `.style.cssText = …`) carrying a colour / spacing
 *      / font literal — across every `.ts` AND `.tsx` source, not just TSX
 *      components. React-rendered UI and React-free boot fallbacks alike must
 *      pull colour/spacing/font from the global.css token system. Boundary with
 *      the Python seals: the Python `test_frontend_architecture_conformance.py`
 *      guards legacy/primitive architecture invariants; this Vitest gate owns
 *      the apps/web TS/TSX *runtime* hardcoding invariant, including DOM style
 *      assignments the Python AST scan does not see.
 */

// vitest runs with cwd = apps/web (the package root).
const GLOBAL_CSS = readFileSync(resolve(process.cwd(), 'src/styles/global.css'), 'utf-8');

type TokenLayer = 'primitive' | 'semantic' | 'component' | 'unknown';

const TOKEN_REFERENCE = /var\(\s*(--[a-z0-9-]+)\s*\)/gi;
const TOKEN_DECLARATION = /(--[a-z0-9-]+)\s*:\s*([^;{}]+);/gi;
const RAW_COLOR = /#[0-9a-f]{3,8}\b|rgba?\(/i;
const RAW_DIMENSION = /(?:^|[^a-z])\d+(?:\.\d+)?(?:px|rem|em|vh|vw)\b/i;
const STRUCTURAL_COMPONENT_TOKENS = new Set(['--c-workbench-main-track']);

function tokenLayer(name: string): TokenLayer {
  if (name.startsWith('--p-')) return 'primitive';
  if (name.startsWith('--c-')) return 'component';
  // The first layer predates the p-/c- naming convention. These stable
  // families are primitives; the remaining declarations are semantic aliases.
  if (
    /^--(?:space|font|radius|motion|tracking|line-height|font-weight|row-height|header-height|app-sidebar-width|bp-)/.test(
      name,
    )
  ) {
    return 'primitive';
  }
  if (name.startsWith('--')) return 'semantic';
  return 'unknown';
}

function parseTokenDeclarations(source: string): Map<string, string> {
  const declarations = new Map<string, string>();
  for (const match of source.matchAll(TOKEN_DECLARATION)) {
    const [, name, value] = match;
    if (name !== undefined && value !== undefined) declarations.set(name, value.trim());
  }
  return declarations;
}

/**
 * Validate the graph without resolving computed CSS. A token may point to a
 * lower layer, but a primitive cannot depend on semantic/component meaning,
 * and a component token cannot smuggle a literal colour or spacing into a
 * route-owned surface. The returned labels are intentionally stable so the
 * synthetic-offender tests below prove the guard is not vacuous.
 */
function tokenGraphOffenders(source: string): string[] {
  const declarations = parseTokenDeclarations(source);
  const offenders: string[] = [];
  for (const [name, value] of declarations) {
    const layer = tokenLayer(name);
    const references = [...value.matchAll(TOKEN_REFERENCE)]
      .map((match) => match[1])
      .filter((reference): reference is string => reference !== undefined);
    for (const reference of references) {
      const referencedLayer = tokenLayer(reference);
      if (layer === 'primitive' && referencedLayer !== 'primitive') {
        offenders.push(`${name}: primitive references ${reference}`);
      }
      if (layer === 'semantic' && referencedLayer === 'component') {
        offenders.push(`${name}: semantic references ${reference}`);
      }
      if (layer === 'component' && referencedLayer === 'component') {
        offenders.push(`${name}: component references ${reference}`);
      }
    }
    if (layer === 'component' && !STRUCTURAL_COMPONENT_TOKENS.has(name)) {
      if (RAW_COLOR.test(value)) offenders.push(`${name}: component contains a raw color`);
      if (RAW_DIMENSION.test(value)) offenders.push(`${name}: component contains a raw dimension`);
    }
  }
  return offenders;
}

const ROUTE_TOKEN_RULES = [
  { id: 'route-button-variant', pattern: /button--(?:primary|secondary|danger|ghost)/i },
  { id: 'route-panel-ratio', pattern: /(?:grid-template-columns|gridTemplateColumns)\s*:/i },
  {
    id: 'route-field-message-color',
    pattern:
      /(?:field[^\n]*?(?:error|success)|(?:error|success)[^\n]*?field)[^\n]*(?:color|background)\s*:/i,
  },
] as const;

function routeTokenOffenders(source: string): string[] {
  return ROUTE_TOKEN_RULES.filter(({ pattern }) => pattern.test(source)).map(({ id }) => id);
}

describe('primitive → semantic → component token graph', () => {
  it('keeps the production stylesheet layered', () => {
    expect(tokenGraphOffenders(GLOBAL_CSS)).toEqual([]);
  });

  it('detects synthetic primitive, semantic and component dependency leaks', () => {
    expect(tokenGraphOffenders('--p-test: var(--surface-bg);')).toEqual([
      '--p-test: primitive references --surface-bg',
    ]);
    expect(tokenGraphOffenders('--surface-bg: var(--c-card-surface);')).toEqual([
      '--surface-bg: semantic references --c-card-surface',
    ]);
    expect(tokenGraphOffenders('--c-test: var(--c-card-surface);')).toEqual([
      '--c-test: component references --c-card-surface',
    ]);
  });

  it('detects raw component color and spacing literals', () => {
    expect(tokenGraphOffenders('--c-test-color: #b30000;')).toContain(
      '--c-test-color: component contains a raw color',
    );
    expect(tokenGraphOffenders('--c-test-spacing: 12px;')).toContain(
      '--c-test-spacing: component contains a raw dimension',
    );
  });
});

describe('route-owned token escape hatches', () => {
  const routeSources = import.meta.glob('../src/routes/**/*.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  });

  it('keeps button variants, panel ratios and field message colors in shared CSS', () => {
    const offenders = Object.entries(routeSources).flatMap(([path, source]) =>
      routeTokenOffenders(source as string).map((rule) => `${path}: ${rule}`),
    );
    expect(offenders).toEqual([]);
  });

  it('detects every synthetic route escape hatch', () => {
    expect(routeTokenOffenders('<div className="button--primary" />')).toEqual([
      'route-button-variant',
    ]);
    expect(routeTokenOffenders('style={{ gridTemplateColumns: "1fr 2fr" }}')).toEqual([
      'route-panel-ratio',
    ]);
    expect(routeTokenOffenders('.field-error { color: #b30000; }')).toEqual([
      'route-field-message-color',
    ]);
  });
});

/** Slice the declaration body of a CSS rule (`selector { ... }`). Rules in
 *  global.css are flat (no nested braces), so the first `}` closes the block. */
function ruleBody(css: string, selector: string): string {
  const start = css.indexOf(selector);
  if (start === -1) throw new Error(`selector not found: ${selector}`);
  const open = css.indexOf('{', start);
  const close = css.indexOf('}', open);
  return css.slice(open + 1, close);
}

/**
 * Expand `var(--name)` references to their literal value, looked up from the
 * `--p-*` / `--*` primitive definitions anywhere in the stylesheet. main's
 * theme SSOT (this app's, NOT the codex source branch's) keeps the LIGHT theme
 * as the `:root` default with literal hex, and resolves the DARK theme's
 * `--status-*`/`--surface-bg` through a `--p-dark-*` primitive layer
 * (primitive→semantic indirection — the documented 3-layer token discipline).
 * So a status token in the dark block reads `var(--p-dark-status-pass)`; this
 * resolves it to the underlying literal so the contrast math below sees a real
 * colour. Single-level resolution is sufficient (primitives are literals).
 */
function expandVars(block: string, css: string): string {
  return block.replace(/var\(\s*(--[a-z0-9-]+)\s*\)/gi, (_whole, name: string) => {
    const re = new RegExp(`${name}\\s*:\\s*([^;]+);`);
    const m = re.exec(css);
    return m?.[1]?.trim() ?? _whole;
  });
}

function hexToRgb(hex: string): [number, number, number] {
  const v = hex.replace('#', '');
  const full =
    v.length === 3
      ? v
          .split('')
          .map((c) => c + c)
          .join('')
      : v;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

function relativeLuminance([r, g, b]: [number, number, number]): number {
  const channel = (c: number): number => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

type Rgb = [number, number, number];

function contrastRgb(a: Rgb, b: Rgb): number {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

/** Composite a translucent `over` colour at `alpha` on top of an opaque
 *  `base` — the source-over alpha blend the browser performs for a badge tint
 *  on its surface. Returns the resulting opaque colour. */
function compositeOver(over: Rgb, alpha: number, base: Rgb): Rgb {
  const blend = (o: number, b: number): number => Math.round(alpha * o + (1 - alpha) * b);
  return [blend(over[0], base[0]), blend(over[1], base[1]), blend(over[2], base[2])];
}

function statusForegrounds(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  const re = /--status-([a-z]+)-fg:\s*(#[0-9a-fA-F]{3,6})/g;
  for (let m = re.exec(block); m !== null; m = re.exec(block)) {
    const [, kind, hex] = m;
    if (kind !== undefined && hex !== undefined) out[kind] = hex;
  }
  return out;
}

/** `--status-{kind}-bg: rgba(r, g, b, a)` → {kind: {rgb, alpha}}. The badge
 *  background is this tint, composited over `--surface-bg` at render time. */
function statusBackgrounds(block: string): Record<string, { rgb: Rgb; alpha: number }> {
  const out: Record<string, { rgb: Rgb; alpha: number }> = {};
  const re = /--status-([a-z]+)-bg:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/g;
  for (let m = re.exec(block); m !== null; m = re.exec(block)) {
    const [, kind, r, g, b, a] = m;
    if (kind && r && g && b && a) {
      out[kind] = { rgb: [Number(r), Number(g), Number(b)], alpha: Number(a) };
    }
  }
  return out;
}

function surfaceBg(block: string): string {
  const hex = /--surface-bg:\s*(#[0-9a-fA-F]{3,6})/.exec(block)?.[1];
  if (hex === undefined) throw new Error('--surface-bg not found in block');
  return hex;
}

const THEMES: readonly { name: string; selector: string; minRatio: number }[] = [
  // Light is this app's default theme (the `:root` block, literal hex). Held to
  // AA normal-text — status badge text is normal weight/size, so the 3:1
  // UI-component floor is insufficient; the pass/running/stale/missing/published
  // fg tints clear AA against the composited badge background.
  { name: 'light (:root)', selector: ':root {', minRatio: 4.5 },
  // Dark is the explicit opt-in / chamber-room theme — resolved through the
  // `--p-dark-*` primitive layer (expandVars below). Also held to AA 4.5:1.
  {
    name: "dark (:root[data-theme='dark'])",
    selector: ":root[data-theme='dark'] {",
    minRatio: 4.5,
  },
];

describe('design-token contrast conformance', () => {
  for (const theme of THEMES) {
    const block = expandVars(ruleBody(GLOBAL_CSS, theme.selector), GLOBAL_CSS);
    const surface = hexToRgb(surfaceBg(block));
    const fgs = statusForegrounds(block);
    const bgs = statusBackgrounds(block);

    it(`${theme.name} exposes status foregrounds`, () => {
      expect(Object.keys(fgs).length).toBeGreaterThanOrEqual(7);
    });

    it(`${theme.name} pairs every status fg with a tint background`, () => {
      const missing = Object.keys(fgs).filter((kind) => bgs[kind] === undefined);
      expect(missing, `status kinds with no --status-*-bg tint: ${missing.join(', ')}`).toEqual([]);
    });

    for (const [kind, fg] of Object.entries(fgs)) {
      const tint = bgs[kind];
      if (tint === undefined) continue; // reported by the pairing test above
      // The badge's real background = tint composited over the theme surface.
      const badgeBg = compositeOver(tint.rgb, tint.alpha, surface);
      it(`${theme.name} status "${kind}" fg ${fg} meets ${theme.minRatio}:1 on its badge tint`, () => {
        expect(contrastRgb(hexToRgb(fg), badgeBg)).toBeGreaterThanOrEqual(theme.minRatio);
      });
    }
  }
});

describe('inline-style hardcoding conformance', () => {
  // Glob is resolved by vitest's import.meta.glob (Vite). `as: 'raw'` returns
  // file text so we can scan for inline literals across every component.
  const modules = import.meta.glob('../src/**/*.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  });

  it('declares no inline hex color in a style prop', () => {
    const offenders: string[] = [];
    for (const [path, source] of Object.entries(modules)) {
      if (/style=\{\{[^}]*#[0-9a-fA-F]{3,6}/.test(source as string)) offenders.push(path);
    }
    expect(offenders, `inline hex color in style props: ${offenders.join(', ')}`).toEqual([]);
  });

  it('declares no inline fontSize literal (use --font-size-* tokens, not a raw length)', () => {
    // Mirror the inline-hex guard above: flag only a HARDCODED size literal
    // (`fontSize: 14`, `fontSize: '13px'`, `'1.5rem'`, `calc(...)`), NOT a
    // token reference (`fontSize: 'var(--font-size-sm)'`) — the var() form IS
    // the compliant 3-layer-token path, exactly as `color: 'var(--fg)'` is
    // allowed by the hex guard. A bare `/fontSize:/` over-reports token usage.
    const LITERAL_FONT_SIZE = /fontSize\s*:\s*['"`]?\s*(?:\d|calc\b)/;
    const offenders: string[] = [];
    for (const [path, source] of Object.entries(modules)) {
      if (LITERAL_FONT_SIZE.test(source as string)) offenders.push(path);
    }
    expect(offenders, `inline fontSize literal: ${offenders.join(', ')}`).toEqual([]);
  });
});

describe('imperative DOM style hardcoding conformance (card F3)', () => {
  // Scan EVERY .ts and .tsx source — boot fallbacks (main.tsx) live outside
  // React and were missed by the TSX-only inline-style scan above.
  const allSources = import.meta.glob('../src/**/*.{ts,tsx}', {
    query: '?raw',
    import: 'default',
    eager: true,
  });

  // `element.style.<prop> = '<…>'` or `.style.cssText = '<…>'` whose literal
  // carries a colour hex or a spacing/font length (px/rem/em/pt). Layout-only
  // assignments without such a literal (e.g. `.style.whiteSpace = 'pre-wrap'`)
  // are intentionally NOT flagged.
  const IMPERATIVE_STYLE_LITERAL =
    /\.style\.(?:cssText\s*=|[A-Za-z][A-Za-z0-9]*\s*=)\s*(['"`])(?:(?!\1).)*?(#[0-9a-fA-F]{3,6}\b|\b\d+(?:px|rem|em|pt)\b)(?:(?!\1).)*\1/;

  it('scans .ts sources too (main.tsx boot path is covered)', () => {
    const paths = Object.keys(allSources);
    expect(paths.some((p) => p.endsWith('/main.tsx'))).toBe(true);
    expect(paths.some((p) => p.endsWith('.ts'))).toBe(true);
  });

  it('declares no imperative element.style color/spacing/font literal', () => {
    const offenders: string[] = [];
    for (const [path, source] of Object.entries(allSources)) {
      if (IMPERATIVE_STYLE_LITERAL.test(source as string)) offenders.push(path);
    }
    expect(
      offenders,
      `imperative element.style color/spacing/font literal: ${offenders.join(', ')}`,
    ).toEqual([]);
  });

  it('detects an offender fixture (guards against a vacuous regex)', () => {
    const offending = [
      `pre.style.padding = '24px';`,
      `el.style.color = "#b30000";`,
      `node.style.cssText = 'color:#fff;padding:8px';`,
    ];
    for (const line of offending) {
      expect(IMPERATIVE_STYLE_LITERAL.test(line), `should flag: ${line}`).toBe(true);
    }
    // Layout-only assignment without a colour/length literal is allowed.
    expect(IMPERATIVE_STYLE_LITERAL.test(`pre.style.whiteSpace = 'pre-wrap';`)).toBe(false);
  });
});
