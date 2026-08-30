import { describe, expect, it } from 'vitest';

import { describeApiError } from '@/ui/errors';

/**
 * describeApiError taxonomy seal (FD-D, Phase 1, 2026-05-30).
 *
 * Nine arms (400 / 403 / 404 / 409 / 410 / 422 / 503 / network / default) +
 * per-arm override hook. Phase L (§4): the 403 copy is generic tester language
 * and never leaks the permission token, regardless of context.
 *
 * The 422 arm (test-plan-draft-create-422, 2026-08-01) is the only one that
 * refines its copy by the RFC 9457 `code` — a bare 422 cannot distinguish an
 * undecodable scope snapshot from an empty draft, and those call for opposite
 * next actions.
 */

/** An error carrying an RFC 9457 `params` extension, the shape `toApiError`
 *  builds from a problem body. */
function problem(
  status: number,
  params: Record<string, unknown>,
): Error & { status: number; params: Record<string, unknown> } {
  return Object.assign(new Error('synthetic'), { status, params });
}

function err(status?: number, code?: string): Error & { status?: number; code?: string } {
  const e = new Error('synthetic') as Error & { status?: number; code?: string };
  if (status !== undefined) e.status = status;
  if (code !== undefined) e.code = code;
  return e;
}

describe('describeApiError taxonomy', () => {
  it('403 returns a generic forbidden copy without context', () => {
    expect(describeApiError(err(403))).toMatch(/권한/);
  });

  it('403 forbidden copy never leaks the permission token (Phase L §4 — security)', () => {
    // The context still routes (platform/headless/session) but the rendered copy
    // is the generic tester-language message — the internal permission token is
    // never exposed in UI text.
    for (const ctx of ['platform', 'headless', 'session'] as const) {
      const msg = describeApiError(err(403), ctx);
      expect(msg).toMatch(/권한/);
      expect(msg).not.toMatch(/platform:|headless:|session:/);
    }
  });

  it('400 shares the default copy unless specialised (zero-regression)', () => {
    // Without a badRequest override, 400 must be byte-identical to the prior
    // behaviour (it fell to the default arm before the 400 arm existed).
    expect(describeApiError(err(400))).toBe(describeApiError(err(500)));
  });

  it('400 honours a badRequest override (validation copy)', () => {
    const out = describeApiError(err(400), 'platform', {
      badRequest: '역할이 올바르지 않습니다.',
    });
    expect(out).toBe('역할이 올바르지 않습니다.');
  });

  it('400 names the offending field when params.field says which (2026-08-31)', () => {
    const out = describeApiError(problem(400, { field: 'packets' }));
    expect(out).toContain('packets');
    expect(out).not.toBe(describeApiError(err(500)));
  });

  it('400 field copy outranks a badRequest override', () => {
    // Same precedence the 404/422/503 arms use: an override is what a route
    // wants said when it does NOT know why; a named field means the server said.
    const out = describeApiError(problem(400, { field: 'bandwidths' }), 'headless', {
      badRequest: 'THE ROUTE GENERIC ONE',
    });
    expect(out).toContain('bandwidths');
    expect(out).not.toBe('THE ROUTE GENERIC ONE');
  });

  it('400 still honours the override when the server named no field', () => {
    // The server declines to guess when two axes are candidates; the screen must
    // then fall back rather than render a gap where a field name should be.
    expect(describeApiError(problem(400, {}), 'headless', { badRequest: 'ROUTE COPY' })).toBe(
      'ROUTE COPY',
    );
  });

  it('400 ignores a params.field that is not a usable name', () => {
    for (const bad of [42, null, '', '   ', { nested: 1 }]) {
      expect(
        describeApiError(problem(400, { field: bad }), 'headless', {
          badRequest: 'ROUTE COPY',
        }),
      ).toBe('ROUTE COPY');
    }
  });

  it('400 ignores a params that is not an object at all', () => {
    const e = err(400) as Error & { params?: unknown };
    for (const bad of ['nope', 7, null, undefined]) {
      e.params = bad;
      expect(describeApiError(e, 'headless', { badRequest: 'ROUTE COPY' })).toBe('ROUTE COPY');
    }
  });

  it('404 returns a not-found copy', () => {
    expect(describeApiError(err(404))).toMatch(/찾을 수 없/);
  });

  it('409 returns a conflict copy', () => {
    expect(describeApiError(err(409))).toMatch(/충돌/);
  });

  it('410 returns an expired copy', () => {
    expect(describeApiError(err(410))).toMatch(/만료/);
  });

  it('undefined status (network) returns an unreachable copy', () => {
    expect(describeApiError(err(undefined))).toMatch(/연결할 수 없/);
  });

  it('default arm covers other 4xx/5xx statuses', () => {
    expect(describeApiError(err(500))).toMatch(/실패/);
    expect(describeApiError(err(503))).toMatch(/실패/);
  });

  it('503 shares the default copy unless serviceUnavailable is supplied (zero-regression)', () => {
    // Without an override, a 503 must be byte-identical to the default arm — the
    // arm is additive, so existing callers keep their prior copy.
    expect(describeApiError(err(503))).toBe(describeApiError(err(500)));
  });

  it('503 honours a serviceUnavailable override (temporarily-unavailable copy)', () => {
    const out = describeApiError(err(503), 'platform', {
      serviceUnavailable: '서비스를 일시적으로 사용할 수 없습니다.',
    });
    expect(out).toBe('서비스를 일시적으로 사용할 수 없습니다.');
  });

  it('overrides per-arm copy without forking the SSOT', () => {
    const out = describeApiError(err(409), 'headless', {
      conflict: '파일이 변경되어 다운로드를 완료할 수 없습니다. 다시 시도해 주세요.',
    });
    expect(out).toBe('파일이 변경되어 다운로드를 완료할 수 없습니다. 다시 시도해 주세요.');
  });

  it('422 returns an unprocessable copy distinct from the default arm', () => {
    const out = describeApiError(err(422));
    expect(out).toMatch(/처리할 수 없/);
    // Before this arm existed a 422 fell through to `default` and the operator
    // saw nothing but "요청이 실패했습니다" — the whole diagnosis was lost.
    expect(out).not.toBe(describeApiError(err(500)));
  });

  it('422 refines its copy by the RFC 9457 code', () => {
    const unprocessable = describeApiError(err(422, 'DRAFT_UNPROCESSABLE'));
    const empty = describeApiError(err(422, 'DRAFT_EMPTY'));
    expect(unprocessable).not.toBe(empty);
    expect(unprocessable).not.toBe(describeApiError(err(422)));
    expect(empty).toMatch(/행/);
  });

  it('422 with an unmapped code degrades to the generic 422 copy', () => {
    // A backend code the FE has no copy for must not blank the message out.
    expect(describeApiError(err(422, 'RATE_LIMITED'))).toBe(describeApiError(err(422)));
  });

  it('422 never renders the server detail prose', () => {
    // `detail` is server-internal (it names Python-side fields). Even when the
    // thrown error carries one, the operator-facing copy must not contain it.
    const e = Object.assign(err(422, 'DRAFT_UNPROCESSABLE'), {
      detail: "필수 필드 'project_id' 누락 (got keys: [])",
    });
    const out = describeApiError(e);
    expect(out).not.toMatch(/project_id/);
    expect(out).not.toMatch(/got keys/);
  });

  it('422 honours an unprocessable override', () => {
    const out = describeApiError(err(422), 'headless', {
      unprocessable: '초안을 만들 수 없습니다.',
    });
    expect(out).toBe('초안을 만들 수 없습니다.');
  });

  it('the other arms stay byte-identical after the 422 arm was added', () => {
    // C-4 zero-regression: adding an arm must not disturb its neighbours. A
    // `code` on a non-422 error is ignored — only the 422 arm reads it.
    for (const status of [400, 403, 404, 409, 410, 503, 500, undefined]) {
      expect(describeApiError(err(status, 'DRAFT_UNPROCESSABLE'))).toBe(
        describeApiError(err(status)),
      );
    }
  });

  it('non-Error inputs collapse to the network arm', () => {
    expect(describeApiError(null)).toMatch(/연결할 수 없/);
    expect(describeApiError('boom')).toMatch(/연결할 수 없/);
    expect(describeApiError({ message: 'no status field' })).toMatch(/연결할 수 없/);
  });
});
