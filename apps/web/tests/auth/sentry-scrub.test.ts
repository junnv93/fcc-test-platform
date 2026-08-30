import { describe, expect, it } from 'vitest';

import { scrubAuthPiiFromEvent } from '@/observability/sentry';

// Loose extra shape for test assertions — Sentry Event's extra is `unknown`-
// typed by SDK; we narrow per test.
interface ExtraDict {
  authFlow?: unknown;
  returnTo?: string;
  userId?: string;
  sessionId?: string;
  self?: unknown;
  contact?: string;
  nested?: unknown;
}
interface EventWithExtra {
  extra?: ExtraDict;
}

describe('Sentry PII scrubber (S2-δ γ-P0-3 + S2-ε δ-P0-2)', () => {
  it('redacts email / UUID / numeric-ID patterns in extra context', () => {
    const event = {
      extra: {
        authFlow: 'startLogin',
        returnTo: '/users/john@example.com/settings',
        userId: '12345678',
        sessionId: 'a1b2c3d4-e5f6-7890-abcd-ef0123456789',
      },
    };
    const scrubbed: EventWithExtra = scrubAuthPiiFromEvent(event);
    const extra: ExtraDict = scrubbed.extra ?? {};
    expect(extra.returnTo).not.toContain('john@example.com');
    expect(extra.returnTo).toContain('<email-redacted>');
    expect(extra.userId).toBe('<id-redacted>');
    expect(extra.sessionId).toBe('<uuid-redacted>');
    // Non-PII keys preserved.
    expect(extra.authFlow).toBe('startLogin');
  });

  it('S2-ε δ-P0-2 — handles circular references without stack overflow', () => {
    const cycleHost: Record<string, unknown> = { authFlow: 'completeLogin' };
    cycleHost['self'] = cycleHost;
    cycleHost['contact'] = 'admin@fcc.test';
    const event = { extra: cycleHost };
    // Must not throw RangeError: Maximum call stack size exceeded.
    const scrubbed: EventWithExtra = scrubAuthPiiFromEvent(event);
    const extra: ExtraDict = scrubbed.extra ?? {};
    expect(extra.self).toBe('<circular>');
    expect(extra.contact).toContain('<email-redacted>');
    expect(extra.authFlow).toBe('completeLogin');
  });

  it('preserves nested non-circular objects fully', () => {
    const event = {
      extra: {
        authFlow: 'silentRefresh',
        nested: {
          deep: {
            email: 'leaf@fcc.test',
            keep: 'literal',
          },
        },
      },
    };
    const scrubbed: EventWithExtra = scrubAuthPiiFromEvent(event);
    const nested = scrubbed.extra?.nested as Record<string, Record<string, unknown>> | undefined;
    const deep = nested?.deep;
    expect(deep?.email).toContain('<email-redacted>');
    expect(deep?.keep).toBe('literal');
  });
});
