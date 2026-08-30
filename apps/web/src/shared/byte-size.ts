/**
 * Byte count → display string (session-workbook-upload-ui, 2026-09-01).
 *
 * This is a **rendering** helper, not a derivation. Every byte count it shows —
 * the node's upload ceiling in a 413 refusal, the size the node stored — comes
 * from the server. The frontend never computes, defaults, or bounds one: the
 * ceiling is a per-node deployment setting
 * (`FCC_SESSION_MAX_WORKBOOK_UPLOAD_BYTES`), so a screen that spelled the
 * 64 MiB default would state a number this deployment may not be using
 * (`Derived-Value No-Client-Recompute SSOT`).
 *
 * Binary units, because that is what the backend's ceiling is expressed in
 * (`64 * 1024 * 1024`); showing "67.1 MB" for a bound the operator will read as
 * "64 MB" in the runbook would be a second, quieter kind of wrong number.
 *
 * i18n-free by construction. The unit suffixes (`B`/`KiB`/`MiB`/`GiB`) are IEC
 * symbols, not prose, and are the same in every locale this app ships — putting
 * them behind `t()` would invite a translation that changes the meaning of a
 * standardised symbol. The *sentence* around the value is translated; the value
 * is not.
 *
 * Total: a non-finite or negative input yields `undefined` rather than
 * `"NaN MiB"`. Callers already treat "the server did not name a bound" as a
 * distinct case, and this keeps that one observable state instead of two.
 */

/** IEC binary unit symbols, ascending. Index doubles as the exponent of 1024. */
const UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB'] as const;

const STEP = 1024;

/**
 * Render a byte count with an IEC binary unit.
 *
 * Whole values print without a decimal (`64 MiB`, not `64.0 MiB`); fractional
 * ones keep a single decimal (`1.5 MiB`). Bytes never get a decimal — a
 * fractional byte is not a thing.
 *
 * @param bytes a non-negative finite byte count, normally read from an RFC 9457
 *   `params.max` or a `size_bytes` response field.
 * @returns the display string, or `undefined` when `bytes` is not a usable count.
 */
export function formatByteSize(bytes: number): string | undefined {
  if (!Number.isFinite(bytes) || bytes < 0) return undefined;

  let value = bytes;
  let unit = 0;
  while (value >= STEP && unit < UNITS.length - 1) {
    value /= STEP;
    unit += 1;
  }

  const rendered =
    unit === 0 || Number.isInteger(value) ? String(Math.round(value)) : value.toFixed(1);
  return `${rendered} ${UNITS[unit]}`;
}
