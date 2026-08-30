import { describe, expect, it } from 'vitest';

import { CAPTURE_ROUTE_PATTERNS } from '../scripts/capture-fixtures/project-workspace-demo.mjs';

describe('project workspace capture fixture routes', () => {
  it('matches only the project list endpoint for the list route', () => {
    expect(CAPTURE_ROUTE_PATTERNS.projectsList.test('/platform/projects')).toBe(true);
    expect(CAPTURE_ROUTE_PATTERNS.projectsList.test('/platform/projects?status=active')).toBe(true);
    expect(CAPTURE_ROUTE_PATTERNS.projectsList.test('/platform/projects/111/coverage')).toBe(false);
    expect(CAPTURE_ROUTE_PATTERNS.projectsList.test('/platform/projects/111/sync-status')).toBe(
      false,
    );
  });

  it('matches every project workspace detail endpoint explicitly', () => {
    expect(CAPTURE_ROUTE_PATTERNS.coverage.test('/platform/projects/111/coverage?limit=200')).toBe(
      true,
    );
    expect(CAPTURE_ROUTE_PATTERNS.claims.test('/platform/projects/111/claims?limit=200')).toBe(
      true,
    );
    expect(CAPTURE_ROUTE_PATTERNS.syncStatus.test('/platform/projects/111/sync-status')).toBe(true);
    expect(
      CAPTURE_ROUTE_PATTERNS.reportSessions.test('/platform/projects/111/report-sessions'),
    ).toBe(true);
  });
});
