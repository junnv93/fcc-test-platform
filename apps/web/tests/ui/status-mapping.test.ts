import { describe, expect, it } from 'vitest';

import {
  draftStatusKind,
  jobStatusToStatusKind,
  projectStatusKind,
  queueStatusLabelToken,
  streamStatusKind,
  streamStatusLabelToken,
} from '@/ui';

import type { ChamberStreamStatus } from '@/api/chamber-events';
import type { SessionStreamStatus } from '@/api/session-events';

/**
 * M3 — the session WS and the chamber progress relay report the SAME four
 * lifecycle tokens. Before this milestone only `/control` mapped them (to a
 * badge kind) and it labelled the badge with the RAW token, so a Korean screen
 * read "reconnecting". The multi-chamber surface showed nothing at all.
 *
 * These cases pin BOTH unions through the SAME functions. The `satisfies` below
 * is the compile-time half of the claim, and it checks BOTH directions:
 * `Record<Union, true>` fails if a union member is missing from the map, and the
 * excess-property check fails if the map invents one. So if either union gains,
 * loses, or renames a member, this file stops compiling rather than silently
 * testing a stale vocabulary.
 */
const STREAM_TOKEN_SET = {
  connecting: true,
  open: true,
  reconnecting: true,
  closed: true,
} satisfies Record<SessionStreamStatus, true> & Record<ChamberStreamStatus, true>;

const STREAM_TOKENS = Object.keys(STREAM_TOKEN_SET) as readonly (keyof typeof STREAM_TOKEN_SET)[];

describe('streamStatusKind / streamStatusLabelToken (M3 vocabulary SSOT)', () => {
  it('maps every stream lifecycle token to the status SSOT', () => {
    expect(streamStatusKind('open')).toBe('pass');
    expect(streamStatusKind('connecting')).toBe('running');
    expect(streamStatusKind('reconnecting')).toBe('running');
    expect(streamStatusKind('closed')).toBe('missing');
  });

  it('applies the SAME mapping to a chamber stream status as to a session one', () => {
    for (const token of STREAM_TOKENS) {
      const sessionStatus: SessionStreamStatus = token;
      const chamberStatus: ChamberStreamStatus = token;
      expect(streamStatusKind(chamberStatus)).toBe(streamStatusKind(sessionStatus));
      expect(streamStatusLabelToken(chamberStatus)).toBe(streamStatusLabelToken(sessionStatus));
    }
  });

  it('gives each token its own i18n leaf token and never leaks a raw unknown one', () => {
    for (const token of STREAM_TOKENS) {
      expect(streamStatusLabelToken(token)).toBe(token);
    }
    // A forward-compat state on a LIVE channel is loud (`fail`) on the colour
    // axis and anonymous on the label axis — never echoed raw.
    expect(streamStatusKind('exploded' as SessionStreamStatus)).toBe('fail');
    expect(streamStatusLabelToken('exploded' as SessionStreamStatus)).toBe('unknown');
  });
});

describe('jobStatusToStatusKind', () => {
  it('maps every backend measurement job status to the status SSOT', () => {
    expect(jobStatusToStatusKind('queued')).toBe('stale');
    expect(jobStatusToStatusKind('running')).toBe('running');
    expect(jobStatusToStatusKind('completed')).toBe('pass');
    expect(jobStatusToStatusKind('failed')).toBe('fail');
    expect(jobStatusToStatusKind('cancelled')).toBe('missing');
  });

  it('normalizes casing/spacing and treats forward-compatible statuses as informational', () => {
    expect(jobStatusToStatusKind(' Completed ')).toBe('pass');
    expect(jobStatusToStatusKind('future-status')).toBe('stale');
  });
});

describe('queueStatusLabelToken', () => {
  it('maps each known queue status to its own i18n leaf token', () => {
    expect(queueStatusLabelToken('queued')).toBe('queued');
    expect(queueStatusLabelToken('running')).toBe('running');
    expect(queueStatusLabelToken('completed')).toBe('completed');
    expect(queueStatusLabelToken('failed')).toBe('failed');
    expect(queueStatusLabelToken('cancelled')).toBe('cancelled');
  });

  it('normalizes casing/spacing and degrades an unknown status to the unknown token (never the raw token)', () => {
    expect(queueStatusLabelToken(' Completed ')).toBe('completed');
    expect(queueStatusLabelToken('future-status')).toBe('unknown');
    expect(queueStatusLabelToken('')).toBe('unknown');
  });
});

describe('projectStatusKind', () => {
  it('maps the central project status token onto an existing status kind (Phase A)', () => {
    expect(projectStatusKind('active')).toBe('running');
    expect(projectStatusKind('completed')).toBe('pass');
  });

  it('normalizes casing/spacing and treats unknown/unset status as informational', () => {
    expect(projectStatusKind(' Active ')).toBe('running');
    expect(projectStatusKind('COMPLETED')).toBe('pass');
    expect(projectStatusKind('future-status')).toBe('stale');
    expect(projectStatusKind(null)).toBe('stale');
    expect(projectStatusKind(undefined)).toBe('stale');
  });
});

describe('draftStatusKind', () => {
  it('maps the test-plan draft lifecycle to first-class status kinds (no borrowing)', () => {
    // draft/published are now their own kinds — NOT the borrowed stale/pass.
    expect(draftStatusKind('draft')).toBe('draft');
    expect(draftStatusKind('published')).toBe('published');
    // archived is retired/withdrawn — surfaced like a removed artifact.
    expect(draftStatusKind('archived')).toBe('missing');
  });

  it('normalizes casing/spacing and treats forward-compatible statuses as informational', () => {
    expect(draftStatusKind(' Published ')).toBe('published');
    expect(draftStatusKind('DRAFT')).toBe('draft');
    expect(draftStatusKind('future-lifecycle')).toBe('stale');
  });
});
