/** Display fallback — nullish OR empty/whitespace renders an em-dash. */
export function orDash(value: string | null | undefined): string {
  return value !== undefined && value !== null && value.trim() !== '' ? value : '—';
}
