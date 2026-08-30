const CAPABILITY_PATH_SEPARATOR = ' / ';

/** Display fallback — nullish OR empty/whitespace renders an em-dash. */
export function orDash(value: string | null | undefined): string {
  return value !== undefined && value !== null && value.trim() !== '' ? value : '—';
}

/** Trim an authoring text field; an empty/whitespace value becomes `null` so the
 *  add-row request carries an explicit "unset" rather than an empty string (the
 *  backend treats null as absent for the optional structural facets). */
export function trimToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

/** Render one capability path exactly the way the detail table does. */
export function formatCapabilityPath(segments: readonly string[]): string {
  return segments.join(CAPABILITY_PATH_SEPARATOR);
}

/** Parse the slash-separated capability-path input (e.g. `BLE / DTM / 1M`) into
 *  the non-empty segment array the API requires. Mirrors the detail table's
 *  `capability_path.join(' / ')` render so what the operator types reads back
 *  identically. Empty/whitespace-only segments are dropped. */
export function parseCapabilityPath(raw: string): string[] {
  return raw
    .split('/')
    .map((segment) => segment.trim())
    .filter((segment) => segment !== '');
}
