/**
 * `Content-Disposition` filename parsing — one definition (2026-08-13).
 *
 * Two download buttons need the server-sent name and the first one to exist
 * (`ExportDraftButton`) grew a private `filenameFromDisposition` that read only
 * the ASCII `filename="..."` parameter. Copying it for the measurement-result
 * export would have copied its blind spot, so it lives here once.
 *
 * The backend builds the header from a single SSOT
 * (`platform_download_proxy.content_disposition_header`, RFC 6266) which
 * **always** emits the ASCII `filename="..."` fallback and appends the RFC 5987
 * `filename*=UTF-8''<percent-encoded>` only when the real name differs from that
 * fallback — i.e. exactly when it carries non-ASCII. Reading `filename=` alone is
 * therefore never wrong, but it is lossy in precisely the case the server went to
 * the trouble of encoding: a Korean model number in a measurement-result
 * workbook arrives as its transliterated fallback.
 *
 * `filename*` wins when present, per RFC 6266 §4.3. A malformed percent-encoding
 * falls through to the ASCII parameter rather than throwing — a download must not
 * fail because a header was odd, and the ASCII fallback is by construction always
 * there.
 */

/** RFC 5987 extended parameter: `filename*=UTF-8''fcc%20results.xlsx`.
 *  The charset and language segments are matched loosely (the server emits
 *  `UTF-8''`, and `decodeURIComponent` assumes UTF-8 regardless) — being strict
 *  here would only turn a readable name into the fallback. */
const EXTENDED_PARAM = /filename\*=(?:[^']*)'(?:[^']*)'([^;]+)/i;
/** RFC 6266 plain parameter, quoted or bare: `filename="a.xlsx"` / `filename=a.xlsx`. */
const PLAIN_PARAM = /filename="?([^";]+)"?/i;

/**
 * Read the download filename out of a `Content-Disposition` header.
 *
 * @param header the raw header value, or `null` when the response carried none.
 * @param fallback the name to use when the header is absent or names nothing —
 *   callers pass the same deterministic name the service derives, so a missing
 *   header degrades to the right file rather than to `download`.
 */
export function filenameFromContentDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const extended = EXTENDED_PARAM.exec(header)?.[1];
  if (extended !== undefined) {
    try {
      const decoded = decodeURIComponent(extended);
      if (decoded !== '') return decoded;
    } catch {
      // Malformed percent-encoding — fall through to the ASCII parameter, which
      // the server always sends alongside it.
    }
  }
  return PLAIN_PARAM.exec(header)?.[1] ?? fallback;
}
