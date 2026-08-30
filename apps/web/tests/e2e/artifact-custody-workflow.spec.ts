import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';
import { VISUAL_PROJECT_ID } from './helpers/visual-fixture';

import type {
  ArtifactCustodySnapshotDetail,
  ProjectArtifactCustody,
} from '../../src/api/platform-client';

const PROJECT_ID = VISUAL_PROJECT_ID;
const SNAPSHOT_ID = 'snapshot-1';
const SUMMARY_PATH = `**/platform/projects/${PROJECT_ID}/artifact-custody`;
const DETAIL_PATH = `${SUMMARY_PATH}/${SNAPSHOT_ID}`;

const SUMMARY: ProjectArtifactCustody = {
  project_id: PROJECT_ID,
  status: 'missing',
  counts: { verified: 4, missing: 1, diverged: 0, unknown: 0 },
  session_count: 1,
  blocking_session_count: 1,
  unresolved_session_count: 0,
  missing_snapshot_session_count: 0,
  oldest_observed_at: '2026-08-11T00:00:00Z',
  newest_observed_at: '2026-08-11T00:00:00Z',
  sessions: [
    {
      snapshot_id: SNAPSHOT_ID,
      provider_session_id: 'provider-session-1',
      chamber_id: 'chamber-a',
      status: 'missing',
      counts: { verified: 4, missing: 1, diverged: 0, unknown: 0 },
      observed_at: '2026-08-11T00:00:00Z',
      is_blocking: true,
      roots: ['/evidence'],
      session_label: 'Session 1',
    },
  ],
};

const DETAIL: ArtifactCustodySnapshotDetail = {
  snapshot_id: SNAPSHOT_ID,
  provider_session_id: 'provider-session-1',
  chamber_id: 'chamber-a',
  status: 'missing',
  counts: { verified: 4, missing: 1, diverged: 0, unknown: 0 },
  observed_at: '2026-08-11T00:00:00Z',
  roots: ['/evidence'],
  findings: [
    {
      relative_path: 'plot.png',
      status: 'missing',
      reason: 'file is not present in the custody root',
    },
  ],
};

async function mockArtifactCustody(page: Page): Promise<{ readonly detailRequests: string[] }> {
  const detailRequests: string[] = [];
  await page.route(SUMMARY_PATH, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(SUMMARY),
    });
  });
  await page.route(DETAIL_PATH, async (route: Route) => {
    detailRequests.push(new URL(route.request().url()).pathname);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(DETAIL),
    });
  });
  return { detailRequests };
}

test('artifact-custody exposes the blocking session and its missing artifact', async ({ page }) => {
  const requests = await mockArtifactCustody(page);
  await injectAuthenticatedSession(page);
  await page.goto(`/artifact-custody?project=${PROJECT_ID}`);

  await expect(page.getByTestId('artifact-custody-route')).toBeVisible();
  await expect(page.getByTestId('artifact-custody-project-status')).toContainText('보관소에 없음');
  await expect(page.getByTestId('artifact-custody-session-count')).toHaveText('1');
  await expect(page.getByTestId('artifact-custody-blocking-count')).toHaveText('1');
  await expect(page.getByTestId('artifact-custody-sessions')).toContainText('Session 1');

  await page.getByTestId(`artifact-custody-detail-${SNAPSHOT_ID}`).first().click();
  await expect(page.getByTestId('artifact-custody-detail-roots')).toContainText('/evidence');
  await expect(page.getByTestId('artifact-custody-findings')).toContainText('plot.png');
  expect(requests.detailRequests).toEqual([
    `/platform/projects/${PROJECT_ID}/artifact-custody/${SNAPSHOT_ID}`,
  ]);
});
