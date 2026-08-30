import { describe, expect, it } from 'vitest';

import { t } from '@/i18n';
import { describeApiError } from '@/ui/errors';

/**
 * A mapped RFC 9457 `code` outranks a route's generic override
 * (provider-identity-coherence, 2026-08-25).
 *
 * ⚠️ This file exists because the seal it replaces could not see the defect.
 * The Python-side guard read `errors.ts` as TEXT and asserted the code map
 * exists and is consulted — both true — while every real call on the
 * reference-data screen passes `errorCopy`, whose `notFound` short-circuited
 * the map. An unregistered PROVIDER rendered "that revision could not be
 * found": the wrong object and the wrong remedy, and strictly worse than the
 * empty table the wave set out to replace.
 *
 * The rule: an override says what a route wants shown when it does NOT know why
 * the request failed. A mapped code means the server said why. So the code wins,
 * and the override remains the fallback for every unmapped code — which is what
 * keeps existing callers byte-identical.
 */
const problem = (status: number, code?: string) =>
  Object.assign(new Error('boom'), { status, code });

describe('describeApiError — code beats override', () => {
  it('renders the code-specific copy even when the route overrides notFound', () => {
    const message = describeApiError(
      problem(404, 'REFERENCE_PROVIDER_NOT_REGISTERED'),
      'platform',
      { notFound: 'THE ROUTE GENERIC ONE' },
    );
    expect(message).toBe(t('errors.referenceProviderNotRegistered'));
    expect(message).not.toBe('THE ROUTE GENERIC ONE');
  });

  it('still honours the override for an unmapped 404 code', () => {
    expect(
      describeApiError(problem(404, 'NOT_FOUND'), 'platform', {
        notFound: 'THE ROUTE GENERIC ONE',
      }),
    ).toBe('THE ROUTE GENERIC ONE');
  });

  it('still honours the override when there is no code at all', () => {
    expect(describeApiError(problem(404), 'platform', { notFound: 'THE ROUTE GENERIC ONE' })).toBe(
      'THE ROUTE GENERIC ONE',
    );
  });

  it('falls back to the generic key when neither code nor override applies', () => {
    expect(describeApiError(problem(404), 'platform')).toBe(t('errors.notFound'));
  });

  it('applies the same precedence on the 422 arm', () => {
    // Harmless today only because no route supplies `unprocessable` — a latent
    // copy of the same defect, fixed in the same commit so it cannot surface
    // the first time a route does.
    expect(
      describeApiError(problem(422, 'DRAFT_EMPTY'), 'platform', {
        unprocessable: 'THE ROUTE GENERIC ONE',
      }),
    ).toBe(t('errors.draftEmpty'));
  });

  it('applies the same precedence on the 503 arm', () => {
    // headless-boundary-default-honesty (2026-08-27). Until this wave the 503
    // arm was override-first with no code map at all, so "a dependency is down,
    // retry" and "a lookup table has no rows, retrying never helps" printed the
    // same sentence.
    expect(
      describeApiError(problem(503, 'REFERENCE_DATA_NOT_PROVISIONED'), 'headless', {
        serviceUnavailable: 'THE ROUTE GENERIC ONE',
      }),
    ).toBe(t('errors.referenceDataNotProvisioned'));
  });

  it('still honours the override for an unmapped 503 code', () => {
    expect(
      describeApiError(problem(503, 'UPSTREAM_UNAVAILABLE'), 'headless', {
        serviceUnavailable: 'THE ROUTE GENERIC ONE',
      }),
    ).toBe('THE ROUTE GENERIC ONE');
  });

  it('does not leak the copy into an unrelated status', () => {
    expect(describeApiError(problem(409, 'REFERENCE_PROVIDER_NOT_REGISTERED'), 'platform')).toBe(
      t('errors.conflict'),
    );
    expect(describeApiError(problem(409, 'REFERENCE_DATA_NOT_PROVISIONED'), 'headless')).toBe(
      t('errors.conflict'),
    );
  });
});

/**
 * The upload arms (session-workbook-upload-ui M2, 2026-09-01).
 *
 * 413 and 415 had no arm at all: both workbook-upload refusals fell through to
 * `errors.default` — "요청이 실패했습니다" — for a tester who had just been told
 * nothing about a file they can see is 90 MB or is a .docx.
 *
 * The 413 arm is the one that differs in kind from its siblings. The *kind* of
 * failure never varies (the file is too big); what the operator needs is the
 * bound, and no fixed set of codes can carry a per-node number. So it reads the
 * RFC 9457 `params.max` the backend now declares — the same relationship the 400
 * arm has with `params.field`.
 */
const withParams = (status: number, code: string, params: Record<string, unknown>) =>
  Object.assign(new Error('boom'), { status, code, params });

describe('describeApiError — the workbook upload arms', () => {
  it('names the ceiling the server sent, in binary units', () => {
    expect(
      describeApiError(
        withParams(413, 'WORKBOOK_UPLOAD_TOO_LARGE', { max: 64 * 1024 * 1024 }),
        'session',
      ),
    ).toBe(t('errors.uploadTooLargeMax', { max: '64 MiB' }));
  });

  it('reads the bound rather than assuming the default', () => {
    // A node configured with a smaller ceiling must not be described with the
    // 64 MiB default. This is the whole reason the number rides on the wire.
    const message = describeApiError(
      withParams(413, 'WORKBOOK_UPLOAD_TOO_LARGE', { max: 8 * 1024 * 1024 }),
      'session',
    );
    expect(message).toBe(t('errors.uploadTooLargeMax', { max: '8 MiB' }));
    expect(message).not.toContain('64');
  });

  it('degrades to a bound-less sentence when the server named no bound', () => {
    // Still better than the generic default: it says the file was too large.
    expect(describeApiError(problem(413, 'WORKBOOK_UPLOAD_TOO_LARGE'), 'session')).toBe(
      t('errors.workbookUploadTooLarge'),
    );
  });

  it('degrades rather than rendering NaN for an unusable bound', () => {
    for (const max of ['64MB', Number.NaN, Number.POSITIVE_INFINITY, -1, null]) {
      expect(
        describeApiError(withParams(413, 'WORKBOOK_UPLOAD_TOO_LARGE', { max }), 'session'),
      ).toBe(t('errors.workbookUploadTooLarge'));
    }
  });

  it('tells a 415 which format the node stores', () => {
    expect(describeApiError(problem(415, 'WORKBOOK_UPLOAD_UNSUPPORTED_TYPE'), 'session')).toBe(
      t('errors.workbookUploadUnsupportedType'),
    );
  });

  it('separates a reclaimed handle from a generic 404', () => {
    expect(describeApiError(problem(404, 'WORKBOOK_HANDLE_NOT_FOUND'), 'session')).toBe(
      t('errors.workbookHandleNotFound'),
    );
    expect(describeApiError(problem(404, 'NOT_FOUND'), 'session')).toBe(t('errors.notFound'));
  });

  it('separates an upload-less node from a dependency being down', () => {
    // Both are 503 and the operator's next step differs: one is "use the
    // node's own workbook", the other is "wait and retry".
    expect(describeApiError(problem(503, 'SESSION_UPLOAD_UNSUPPORTED'), 'session')).toBe(
      t('errors.sessionUploadUnsupported'),
    );
    expect(describeApiError(problem(503, 'UPSTREAM_UNAVAILABLE'), 'session')).toBe(
      t('errors.default'),
    );
  });

  it('keeps the upload copy out of unrelated statuses', () => {
    expect(describeApiError(problem(409, 'WORKBOOK_UPLOAD_TOO_LARGE'), 'session')).toBe(
      t('errors.conflict'),
    );
    expect(
      describeApiError(withParams(409, 'WORKBOOK_UPLOAD_TOO_LARGE', { max: 1024 }), 'session'),
    ).toBe(t('errors.conflict'));
  });

  it('does not answer a 413 with another status arm copy', () => {
    // Non-emptiness of the per-status keying: a single flat code→key map would
    // make this pass by accident, so assert the status axis actually partitions.
    expect(describeApiError(problem(413, 'REFERENCE_PROVIDER_NOT_REGISTERED'), 'session')).toBe(
      t('errors.default'),
    );
  });
});
