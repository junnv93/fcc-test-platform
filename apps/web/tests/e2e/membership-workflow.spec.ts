import { expect, test, type Page, type Route } from '@playwright/test';

import { injectAuthenticatedSession, TEST_ADMIN_PERMISSIONS } from './helpers/auth-fixture';
import { tableView } from './helpers/responsive-table';

/**
 * FE-P8 §membership — project membership / RBAC roster workflow smoke. Asserts
 * the route's acceptance criteria end-to-end via the platform API mocked at the
 * network layer (no live backend), mirroring projects-workflow.spec.ts.
 *
 * Covers:
 *   1. token-only miss (platform:read, no platform:admin) → assign/revoke still
 *      offered + advisory (backend authorizes token ∪ membership — no false block)
 *   2. admin token (platform:admin) → assign form + revoke controls, no advisory
 *   3. token-only miss + backend 403 → forbidden write error surfaced
 *   4. assign success → aria-live StatusMessage announces the outcome
 *   5. assign error (400 unknown role) → operator-facing write error
 *   6. role <datalist> is data-derived from the loaded roster (no hardcoded enum)
 */

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const ROSTER_GLOB = `**/platform/projects/${PROJECT_ID}/memberships`;
const ROSTER_GLOB_Q = `**/platform/projects/${PROJECT_ID}/memberships?*`;
const REVOKE_GLOB = `**/platform/projects/${PROJECT_ID}/memberships/revoke`;

interface MockPage {
  readonly items: readonly Record<string, unknown>[];
  readonly nextCursor?: string | null;
}

function member(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    user_subject: 'alice@corp',
    role_key: 'project_engineer',
    assigned_at: '2026-06-13T00:00:00+00:00',
    expires_at: null,
    ...over,
  };
}

/** Flat-array body + opaque `X-Next-Cursor` header — the keyset wire shape. */
function pageResponse(page: MockPage): { body: string; headers: Record<string, string> } {
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (page.nextCursor !== undefined && page.nextCursor !== null) {
    headers['X-Next-Cursor'] = page.nextCursor;
  }
  return { body: JSON.stringify(page.items), headers };
}

/** Mock the membership roster read (GET) + assign (POST) + revoke (POST). The
 *  GET/POST share the `/memberships` path, so the handler branches on method;
 *  `/memberships/revoke` is a distinct route registered last so it wins. */
async function mockMembership(
  page: Page,
  opts: {
    roster?: MockPage;
    assignStatus?: number;
    assignBody?: Record<string, unknown>;
  } = {},
): Promise<void> {
  const roster = opts.roster ?? { items: [] };
  const handleRoster = async (route: Route): Promise<void> => {
    const method = route.request().method();
    if (method === 'POST') {
      const status = opts.assignStatus ?? 200;
      const body = JSON.stringify(
        opts.assignBody ?? member({ user_subject: 'bob@corp', role_key: 'project_viewer' }),
      );
      await route.fulfill({ status, contentType: 'application/json', body });
      return;
    }
    const resp = pageResponse(roster);
    await route.fulfill({ status: 200, headers: resp.headers, body: resp.body });
  };
  await page.route(ROSTER_GLOB, handleRoster);
  await page.route(ROSTER_GLOB_Q, handleRoster);
  await page.route(REVOKE_GLOB, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(member({})),
    });
  });
}

async function open(page: Page, opts: { admin?: boolean } = {}): Promise<void> {
  await injectAuthenticatedSession(page, opts.admin ? { permissions: TEST_ADMIN_PERMISSIONS } : {});
  await page.goto(`/membership?project=${PROJECT_ID}`);
  await expect(page.getByRole('heading', { name: '프로젝트 권한', level: 1 })).toBeVisible();
  await expect(page.getByTestId('membership-workbench-overview')).toBeVisible();
  await expect(page.getByTestId('membership-workbench')).toBeVisible();
  await expect(page.getByTestId('membership-admin-state')).toContainText(PROJECT_ID);
  await expect(page.getByTestId('membership-next-projects')).toHaveAttribute('href', '/projects');
}

test.describe('Membership route — RBAC roster', () => {
  test('membership-effective: a token-only miss still offers assign + revoke with an advisory (no false block)', async ({
    page,
  }) => {
    // The backend authorizes token ∪ project-membership effective permissions, so
    // a user without the platform:admin token may still hold admin via membership
    // (project_admin). The UI must offer the controls (not hard-hide them) and
    // only show a non-blocking advisory; the backend stays the authority.
    await mockMembership(page, { roster: { items: [member({})], nextCursor: null } });
    await open(page); // default operator: platform:read, NO platform:admin
    await expect(page.getByTestId('membership-roster')).toBeVisible();
    await expect(page.getByTestId('membership-row')).toBeVisible();
    await expect(page.getByTestId('membership-assign-form')).toBeVisible();
    await expect(
      tableView(page, 'membership-roster').getByTestId('membership-revoke'),
    ).toBeVisible();
    await expect(page.getByTestId('membership-token-hint')).toBeVisible();
  });

  test('admin token sees assign + revoke controls without the advisory', async ({ page }) => {
    await mockMembership(page, { roster: { items: [member({})], nextCursor: null } });
    await open(page, { admin: true });
    await expect(page.getByTestId('membership-assign-form')).toBeVisible();
    await expect(
      tableView(page, 'membership-roster').getByTestId('membership-revoke'),
    ).toBeVisible();
    await expect(page.getByTestId('membership-token-hint')).toHaveCount(0);
  });

  test('membership-effective: a token-only miss surfaces a backend 403 as the forbidden write error', async ({
    page,
  }) => {
    await mockMembership(page, {
      roster: { items: [], nextCursor: null },
      assignStatus: 403,
      assignBody: { title: 'forbidden', status: 403, code: 'forbidden' },
    });
    await open(page); // platform:read, NO platform:admin
    await page.getByTestId('assign-user-input').fill('bob@corp');
    await page.getByTestId('assign-role-input').fill('project_viewer');
    await page.getByTestId('assign-submit').click();
    await expect(page.getByTestId('membership-write-error')).toBeVisible();
    await expect(page.getByTestId('membership-write-error')).toContainText(/권한/u);
  });

  test('admin assign announces success via an aria-live status region', async ({ page }) => {
    await mockMembership(page, {
      roster: { items: [], nextCursor: null },
      assignBody: member({ user_subject: 'bob@corp', role_key: 'project_viewer' }),
    });
    await open(page, { admin: true });
    await page.getByTestId('assign-user-input').fill('bob@corp');
    await page.getByTestId('assign-role-input').fill('project_viewer');
    await page.getByTestId('assign-submit').click();
    const success = page.getByTestId('membership-write-success');
    await expect(success).toBeVisible();
    await expect(success).toHaveAttribute('aria-live', 'polite');
    await expect(success).toContainText('bob@corp');
  });

  test('admin assign error surfaces an operator-facing write error', async ({ page }) => {
    await mockMembership(page, {
      roster: { items: [], nextCursor: null },
      assignStatus: 400,
      assignBody: { detail: 'unknown role' },
    });
    await open(page, { admin: true });
    await page.getByTestId('assign-user-input').fill('bob@corp');
    await page.getByTestId('assign-role-input').fill('nope');
    await page.getByTestId('assign-submit').click();
    await expect(page.getByTestId('membership-write-error')).toBeVisible();
  });

  test('role datalist is data-derived from the loaded roster (no hardcoded enum)', async ({
    page,
  }) => {
    await mockMembership(page, {
      roster: {
        items: [
          member({ user_subject: 'a@corp', role_key: 'project_admin' }),
          member({ user_subject: 'b@corp', role_key: 'project_viewer' }),
        ],
        nextCursor: null,
      },
    });
    await open(page, { admin: true });
    const options = page.locator('#assign-role-options option');
    await expect(options).toHaveCount(2);
    await expect(options.nth(0)).toHaveAttribute('value', 'project_admin');
    await expect(options.nth(1)).toHaveAttribute('value', 'project_viewer');
  });
});
