import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession } from './helpers/auth-fixture';
import { VISUAL_PROJECT_ID } from './helpers/visual-fixture';

import type {
  CreateReportRequest,
  ProjectEnvelope,
  ReportCitationEnvelope,
  ReportEnvelope,
  ReportList,
} from '../../src/api/platform-client';

const PROJECT_ID = VISUAL_PROJECT_ID;
const PROJECTS_PATH = '**/platform/projects?*';
const PROJECTS_BARE_PATH = '**/platform/projects';
const REPORTS_PATH = `**/platform/projects/${PROJECT_ID}/reports`;
const CITATION_PATH = `**/platform/projects/${PROJECT_ID}/report-citation*`;

const PROJECT: ProjectEnvelope = {
  project_id: PROJECT_ID,
  project_code: 'SM-TEST',
  model_name: 'SM-TEST',
  management_number: 'KTL-2026-0001',
  status: 'active',
  sample_count: 0,
};

function report(edition: string, reportId: string): ReportEnvelope {
  return {
    report_id: reportId,
    project_id: PROJECT_ID,
    edition,
    report_number: `S-KTL-2026-0001-${edition}`,
    date_tested_start: '2026-07-01',
    date_tested_end: '2026-07-10',
    date_of_issue: '2026-07-20',
    prepared_by: '홍길동',
    prepared_site: '수원',
    created_at: '2026-07-20T09:00:00Z',
  };
}

function citation(): ReportCitationEnvelope {
  return {
    project_id: PROJECT_ID,
    report_number: 'S-KTL-2026-0001-1',
    management_number: 'KTL-2026-0001',
    fcc_id: 'A3LSM-X100',
    applicant_name: 'Samsung Electronics',
    applicant_address: null,
    eut_description: '',
    test_standard: 'FCC Part 15.247',
    samples: [],
  };
}

async function mockTestReports(page: Page): Promise<{
  readonly createRequests: CreateReportRequest[];
}> {
  const createRequests: CreateReportRequest[] = [];
  let reports: ReportList = [report('1', 'report-1')];

  const projectHandler = async (route: Route): Promise<void> => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([PROJECT]),
    });
  };
  await page.route(PROJECTS_PATH, projectHandler);
  await page.route(PROJECTS_BARE_PATH, projectHandler);

  await page.route(REPORTS_PATH, async (route: Route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as CreateReportRequest;
      createRequests.push(body);
      const created = report(body.edition, `report-${body.edition}`);
      reports = [...reports, created];
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify(created),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(reports),
    });
  });
  await page.route(CITATION_PATH, async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(citation()),
    });
  });

  return { createRequests };
}

test('test-reports registers an edition, shows the server report number, and cites it', async ({
  page,
}) => {
  const requests = await mockTestReports(page);
  await injectAuthenticatedSession(page);
  await page.goto(`/test-reports?project=${PROJECT_ID}`);

  await expect(page.getByRole('heading', { name: '성적서 대장', level: 1 })).toBeVisible();
  const reportsTable = page.getByTestId('test-reports-table');
  await expect(reportsTable.getByTestId('test-report-number').first()).toHaveText(
    'S-KTL-2026-0001-1',
  );

  await reportsTable.getByTestId('test-report-cite').first().click();
  await expect(page.getByTestId('citation-report-number')).toHaveText('S-KTL-2026-0001-1');
  await expect(page.getByTestId('citation-body')).toContainText('Samsung Electronics');

  await page.getByTestId('new-report-edition').fill('2');
  await page.getByTestId('new-report-submit').click();
  await expect(page.getByTestId('new-report-success')).toBeVisible();
  expect(requests.createRequests).toEqual([{ edition: '2' }]);
  await expect(page.getByTestId('test-reports-table').getByTestId('test-report-row')).toHaveCount(
    2,
  );
  await expect(
    page.getByTestId('test-reports-table').getByTestId('test-report-number').nth(1),
  ).toHaveText('S-KTL-2026-0001-2');
});
