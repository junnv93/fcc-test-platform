#!/usr/bin/env node
/** Measure the production font payload and fail when its delivery contract drifts. */

import { readdir, readFile, writeFile } from 'node:fs/promises';
import { basename, resolve } from 'node:path';
import { gzipSync } from 'node:zlib';

const appRoot = resolve(import.meta.dirname, '..');
const distRoot = resolve(appRoot, 'dist');
const assetsRoot = resolve(distRoot, 'assets');
const outputPath = resolve(distRoot, 'font-assets.json');

const entries = await readdir(assetsRoot, { withFileTypes: true });
const fontNames = entries
  .filter((entry) => entry.isFile() && entry.name.endsWith('.woff2'))
  .map((entry) => entry.name)
  .sort();

if (fontNames.length === 0) throw new Error('No production WOFF2 assets were emitted.');
const assets = await Promise.all(
  fontNames.map(async (name) => {
    const bytes = await readFile(resolve(assetsRoot, name));
    return {
      name: basename(name),
      rawBytes: bytes.byteLength,
      gzipBytes: gzipSync(bytes).byteLength,
    };
  }),
);
const cssEntries = entries.filter((entry) => entry.isFile() && entry.name.endsWith('.css'));
const css = (
  await Promise.all(cssEntries.map((entry) => readFile(resolve(assetsRoot, entry.name), 'utf8')))
).join('\n');

const payload = {
  fontDisplaySwap: /font-display:\s*swap/u.test(css),
  unicodeRangePartitioned: /unicode-range:\s*U\+/u.test(css),
  emittedAssetCount: assets.length,
  totalRawBytes: assets.reduce((total, asset) => total + asset.rawBytes, 0),
  totalGzipBytes: assets.reduce((total, asset) => total + asset.gzipBytes, 0),
  assets,
};
if (!payload.fontDisplaySwap) throw new Error('Production CSS must declare font-display: swap.');
if (!payload.unicodeRangePartitioned) {
  throw new Error('Korean font assets must stay unicode-range partitioned for on-demand loading.');
}

await writeFile(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
process.stdout.write(`${JSON.stringify(payload, null, 2)}\n`);
