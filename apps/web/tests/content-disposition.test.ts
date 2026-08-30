import { describe, expect, it } from 'vitest';

import { filenameFromContentDisposition } from '@/shared/content-disposition';

/**
 * `Content-Disposition` filename parsing SSOT (2026-08-13).
 *
 * The header shapes below are the ones the backend actually builds
 * (`platform_download_proxy.content_disposition_header`): always the ASCII
 * `filename="..."`, plus the RFC 5987 `filename*` when the real name differs
 * from that fallback.
 */
describe('filenameFromContentDisposition', () => {
  it('falls back when there is no header', () => {
    expect(filenameFromContentDisposition(null, 'fallback.xlsx')).toBe('fallback.xlsx');
  });

  it('reads the plain quoted parameter', () => {
    expect(
      filenameFromContentDisposition('attachment; filename="measurement-results-3.xlsx"', 'f.xlsx'),
    ).toBe('measurement-results-3.xlsx');
  });

  it('reads an unquoted parameter', () => {
    expect(filenameFromContentDisposition('attachment; filename=plain.xlsx', 'f.xlsx')).toBe(
      'plain.xlsx',
    );
  });

  it('prefers the RFC 5987 filename* over the ASCII fallback', () => {
    // The regression this SSOT exists for: the private parser it replaced read
    // only `filename=`, so a non-ASCII model number arrived transliterated even
    // though the server had encoded the real name alongside it.
    const header =
      'attachment; filename="measurement-results--3.xlsx"; ' +
      "filename*=UTF-8''measurement-results-%EA%B0%80%EB%82%98-3.xlsx";
    expect(filenameFromContentDisposition(header, 'f.xlsx')).toBe(
      'measurement-results-가나-3.xlsx',
    );
  });

  it('falls through to the ASCII parameter when filename* is malformed', () => {
    // A download must not fail because a header was odd, and by construction the
    // ASCII parameter is always present next to it.
    const header = 'attachment; filename="ascii.xlsx"; filename*=UTF-8\'\'%E0%A4%A';
    expect(filenameFromContentDisposition(header, 'f.xlsx')).toBe('ascii.xlsx');
  });

  it('falls back when the header names nothing', () => {
    expect(filenameFromContentDisposition('attachment', 'fallback.xlsx')).toBe('fallback.xlsx');
  });
});
