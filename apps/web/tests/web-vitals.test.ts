import { describe, expect, it } from 'vitest';

import { getActiveScreen, normalizeScreen, setActiveScreen } from '@/observability/web-vitals';

/**
 * Privacy-safe screen context for web-vitals (card D2).
 *
 * `normalizeScreen` is the SSOT that guarantees no concrete id, query param, or
 * hash fragment can ever reach a metric attribute — only the route pattern.
 */
describe('normalizeScreen', () => {
  it('strips query params', () => {
    expect(normalizeScreen('/sessions?session=123')).toBe('/sessions');
    expect(normalizeScreen('/reports?request=42&foo=bar')).toBe('/reports');
  });

  it('strips hash fragments', () => {
    expect(normalizeScreen('/sessions#tab=results')).toBe('/sessions');
    expect(normalizeScreen('/projects/42?x=1#y')).toBe('/projects/:id');
  });

  it('masks numeric id segments', () => {
    expect(normalizeScreen('/projects/42')).toBe('/projects/:id');
    expect(normalizeScreen('/sessions/7')).toBe('/sessions/:id');
    expect(normalizeScreen('/projects/42/sessions/7')).toBe('/projects/:id/sessions/:id');
  });

  it('masks uuid id segments', () => {
    expect(normalizeScreen('/projects/3fa85f64-5717-4562-b3fc-2c963f66afa6')).toBe('/projects/:id');
    expect(normalizeScreen('/chambers/3FA85F64-5717-4562-B3FC-2C963F66AFA6/jobs')).toBe(
      '/chambers/:id/jobs',
    );
  });

  it('preserves static route names', () => {
    expect(normalizeScreen('/sessions')).toBe('/sessions');
    expect(normalizeScreen('/test-plans')).toBe('/test-plans');
    expect(normalizeScreen('/membership')).toBe('/membership');
  });

  it('normalizes root and empty input', () => {
    expect(normalizeScreen('/')).toBe('/');
    expect(normalizeScreen('')).toBe('/');
  });

  it('collapses a trailing slash but keeps root', () => {
    expect(normalizeScreen('/sessions/')).toBe('/sessions');
    expect(normalizeScreen('/')).toBe('/');
  });

  it('never leaks digits or query/hash characters in the result', () => {
    const dangerous = [
      '/sessions?session=123&token=secret',
      '/projects/42#user=99',
      '/chambers/3fa85f64-5717-4562-b3fc-2c963f66afa6?op=delete',
    ];
    for (const raw of dangerous) {
      const out = normalizeScreen(raw);
      expect(out).not.toContain('?');
      expect(out).not.toContain('#');
      expect(out).not.toContain('=');
      // No bare numeric run survives (ids are masked to :id).
      expect(/\d/.test(out)).toBe(false);
    }
  });
});

describe('setActiveScreen / getActiveScreen', () => {
  it('stores the normalized pattern, not the raw value', () => {
    setActiveScreen('/projects/42?session=99');
    expect(getActiveScreen()).toBe('/projects/:id');

    setActiveScreen('/sessions');
    expect(getActiveScreen()).toBe('/sessions');
  });

  it('cannot store a concrete id even if a raw pathname is passed', () => {
    setActiveScreen('/sessions/12345');
    const stored = getActiveScreen();
    expect(stored).toBe('/sessions/:id');
    expect(/\d/.test(stored)).toBe(false);
  });
});
