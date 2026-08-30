import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import {
  claimErrorMessage,
  formatAge,
  isCrossEngineerDuplicate,
  isSingleEngineerRemeasure,
  ProjectsRoute,
  summarizeByTechnology,
} from '@/routes/projects';

import type { ReactElement } from 'react';

/**
 * FE-P3-write (2026-05-27) — claim acquire/release controls on the coverage
 * dashboard. Turns the FE-P3 lock overlay from read-only visualization into an
 * enforcement mechanism: the operator acquires a lock before measuring; a
 * conflicting acquire is rejected (409). Covers: button visibility per RBAC +
 * claim state, optimistic write + settle reconciliation (Increment 3,
 * 2026-06-13 — the write now applies to the cache immediately and reconciles via
 * invalidate on settle), the optimistic-placeholder release guard, conflict
 * surfacing, and the offline write guard.
 */

const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
  fetchProviderList: vi.fn().mockResolvedValue([]),
  fetchCoveragePage: vi.fn(),
  fetchClaimsPage: vi.fn(),
  fetchSyncStatus: vi.fn(),
  acquireClaim: vi.fn(),
  releaseClaim: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

function syncStatus(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    server_time: '2026-05-27T01:00:00+00:00',
    last_ingested_at: '2026-05-27T00:58:00+00:00',
    age_seconds: 120,
    is_stale: false,
    stale_threshold_seconds: 3600,
    condition_count: 1,
    active_claim_count: 0,
    ...over,
  };
}

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const ME = 'op-1';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

function authenticateAs(permissions: readonly string[], subject = ME): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: subject, [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function coverage(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    condition_hash: 'aaaaaaaaaaaa1',
    technology: 'UNII',
    attempt_count: 1,
    latest_verdict: 'Pass',
    latest_attempt_number: 1,
    latest_measured_at: '2026-05-27T00:00:00',
    latest_operator: 'alice',
    latest_session_id: 's1',
    ...over,
  };
}

interface Page {
  items: unknown[];
  nextCursor: string | null;
}
function page(items: unknown[], nextCursor: string | null = null): Page {
  return { items, nextCursor };
}
const EMPTY_PAGE: Page = page([]);

function claim(over: Record<string, unknown>): Record<string, unknown> {
  return {
    claim_id: 'c1',
    project_id: PROJECT_ID,
    condition_hash: 'aaaaaaaaaaaa1',
    technology: 'UNII',
    operator: ME,
    session_id: 's1',
    occurred_at: '2026-05-27T00:00:00',
    expires_at: null,
    ...over,
  };
}

function renderProjects(entry: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ProjectsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

function setOnline(value: boolean): void {
  Object.defineProperty(navigator, 'onLine', { value, configurable: true });
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  setOnline(true);
  platformApi.fetchProjectsPage.mockReset();
  platformApi.fetchProviderList.mockReset();
  platformApi.fetchCoveragePage.mockReset();
  platformApi.fetchClaimsPage.mockReset();
  platformApi.fetchSyncStatus.mockReset();
  platformApi.acquireClaim.mockReset();
  platformApi.releaseClaim.mockReset();
  // The route mounts the shared ProjectSelectField (project-picker-ssot), which
  // reads one keyset page under queryKeys.project.pickerOptions() — give it a
  // default page so these claim/sync tests (which drive the project via
  // ?project=) don't error on the picker's fetch.
  platformApi.fetchProjectsPage.mockResolvedValue({
    items: [{ project_id: PROJECT_ID, model_name: 'SM-S921U', management_number: null }],
    nextCursor: null,
  });
  platformApi.fetchProviderList.mockResolvedValue([]);
  platformApi.fetchClaimsPage.mockResolvedValue(EMPTY_PAGE);
  platformApi.fetchSyncStatus.mockResolvedValue(syncStatus({}));
});

afterEach(() => {
  vi.restoreAllMocks();
  setOnline(true);
});

describe('claimErrorMessage', () => {
  it('maps statuses to operator-facing messages', () => {
    expect(claimErrorMessage(Object.assign(new Error(), { status: 409 }))).toContain('점유');
    expect(claimErrorMessage(Object.assign(new Error(), { status: 403 }))).toContain('권한');
    // No status property ⇒ unreachable central server (offline).
    expect(claimErrorMessage(new Error())).toContain('오프라인');
  });
});

describe('FE-P3-write claim controls — RBAC visibility', () => {
  it('shows a claim button on a free condition when the operator has platform:claim', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);
    expect(await screen.findByTestId('condition-claim-acquire')).toBeInTheDocument();
    expect(screen.queryByTestId('condition-claim-release')).not.toBeInTheDocument();
    // The token already carries platform:claim → no advisory hint.
    expect(screen.queryByTestId('claim-token-hint')).not.toBeInTheDocument();
  });

  // Membership-effective permission affordance (P2 debt, 2026-06-23): the backend
  // authorizes the UNION of token ∪ project-membership effective permissions, so a
  // read-only token user MAY hold claim via membership. The UI must NOT hard-hide
  // the claim control on a token-only miss (that would falsely block a legitimate
  // membership-granted operator) — it offers the control + a non-blocking advisory
  // and lets the backend be the authority. Mirrors the sample-inventory pattern.
  it('still offers the claim control on a token-only miss (membership may grant) and shows an advisory', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);
    // NOT falsely blocked: the acquire control is offered to the authenticated
    // operator even without the platform:claim token.
    expect(await screen.findByTestId('condition-claim-acquire')).not.toBeDisabled();
    // …with a non-blocking advisory that membership may grant the permission.
    expect(screen.getByTestId('claim-token-hint')).toBeInTheDocument();
  });

  it('surfaces a backend 403 as the forbidden claim error (membership lacking too)', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    platformApi.acquireClaim.mockRejectedValue(
      Object.assign(new Error('forbidden'), { status: 403 }),
    );
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);
    await userEvent.click(await screen.findByTestId('condition-claim-acquire'));
    await waitFor(() => expect(screen.getByTestId('claim-error')).toBeInTheDocument());
    // Existing forbidden copy is reused (no new error surface).
    expect(screen.getByTestId('claim-error')).toHaveTextContent('권한');
  });

  it('shows a release button only on the operator’s OWN held condition', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    platformApi.fetchClaimsPage.mockResolvedValue(page([claim({ operator: ME })]));
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);
    expect(await screen.findByTestId('condition-claim-release')).toBeInTheDocument();
    expect(screen.queryByTestId('condition-claim-acquire')).not.toBeInTheDocument();
  });

  it('shows no action button on a condition held by ANOTHER operator (read-only lock)', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    platformApi.fetchClaimsPage.mockResolvedValue(page([claim({ operator: 'someone-else' })]));
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);
    await waitFor(() =>
      expect(screen.getByTestId('condition-claimed')).toHaveTextContent('someone-else'),
    );
    expect(screen.queryByTestId('condition-claim-acquire')).not.toBeInTheDocument();
    expect(screen.queryByTestId('condition-claim-release')).not.toBeInTheDocument();
  });
});

describe('FE-P3-write claim controls — optimistic write + reconcile', () => {
  it('acquires with the operator identity and reconciles by refetching claims on settle', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    platformApi.acquireClaim.mockResolvedValue(claim({}));
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);

    const button = await screen.findByTestId('condition-claim-acquire');
    const claimsCallsBefore = platformApi.fetchClaimsPage.mock.calls.length;
    await userEvent.click(button);

    await waitFor(() =>
      expect(platformApi.acquireClaim).toHaveBeenCalledWith(PROJECT_ID, {
        technology: 'UNII',
        condition_hash: 'aaaaaaaaaaaa1',
        operator: ME,
      }),
    );
    // Settle reconciliation: the optimistic write invalidates the claims query →
    // refetch replaces the placeholder with the server's real claim.
    await waitFor(() =>
      expect(platformApi.fetchClaimsPage.mock.calls.length).toBeGreaterThan(claimsCallsBefore),
    );
  });

  it('shows an immediate optimistic lock on acquire but cannot release the placeholder before reconciliation', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    // First claims load → empty (condition is free). The post-acquire settle
    // refetch stays pending forever, so the optimistic placeholder is NOT yet
    // replaced by a real server claim — this is exactly the reconciliation window
    // the release guard must cover (acquire already settled, refetch in flight).
    platformApi.fetchClaimsPage.mockReset();
    platformApi.fetchClaimsPage
      .mockResolvedValueOnce(EMPTY_PAGE)
      .mockImplementation(() => new Promise<Page>(() => undefined));
    platformApi.acquireClaim.mockResolvedValue(claim({}));
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);

    await userEvent.click(await screen.findByTestId('condition-claim-acquire'));

    // The lock overlay reflects the acquire immediately (optimistic), and the
    // acquire button is replaced by the holder's release control.
    await waitFor(() => expect(screen.getByTestId('condition-claimed')).toHaveTextContent(ME));
    const release = await screen.findByTestId('condition-claim-release');

    // …but the release is disabled while the placeholder id is unreconciled, and
    // even forcing a click never sends the `optimistic:*` id to the server.
    expect(release).toBeDisabled();
    await userEvent.click(release).catch(() => undefined);
    expect(platformApi.releaseClaim).not.toHaveBeenCalled();
  });

  it('releases the operator’s own claim and refetches', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    platformApi.fetchClaimsPage.mockResolvedValue(page([claim({ claim_id: 'cZZ', operator: ME })]));
    platformApi.releaseClaim.mockResolvedValue(claim({ claim_id: 'cZZ', action: 'released' }));
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);

    const button = await screen.findByTestId('condition-claim-release');
    await userEvent.click(button);
    await waitFor(() =>
      expect(platformApi.releaseClaim).toHaveBeenCalledWith(PROJECT_ID, 'cZZ', { operator: ME }),
    );
  });

  it('surfaces a 409 conflict when another operator already holds the condition', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    platformApi.acquireClaim.mockRejectedValue(
      Object.assign(new Error('claim acquire failed'), { status: 409 }),
    );
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);

    await userEvent.click(await screen.findByTestId('condition-claim-acquire'));
    await waitFor(() => expect(screen.getByTestId('claim-error')).toBeInTheDocument());
    expect(screen.getByTestId('claim-error')).toHaveTextContent('점유');
  });
});

describe('FE-P3 duplicate-quality — distinct operator count', () => {
  it('classifies a cross-engineer duplicate vs a single-engineer re-measure', () => {
    const crossEngineer = coverage({
      attempt_count: 2,
      distinct_session_count: 2,
      distinct_operator_count: 2,
    });
    const reMeasure = coverage({
      attempt_count: 3,
      distinct_session_count: 3,
      distinct_operator_count: 1,
    });
    expect(isCrossEngineerDuplicate(crossEngineer as never)).toBe(true);
    expect(isCrossEngineerDuplicate(reMeasure as never)).toBe(false);
    // A repeated measure by one engineer is NOT a cross-engineer duplicate.
    expect(isSingleEngineerRemeasure(reMeasure as never)).toBe(true);
    expect(isSingleEngineerRemeasure(crossEngineer as never)).toBe(false);
  });

  it('summarizeByTechnology counts cross-engineer duplicates separately from re-measures', () => {
    const summaries = summarizeByTechnology([
      coverage({
        technology: 'UNII',
        condition_hash: 'a',
        attempt_count: 2,
        distinct_operator_count: 2,
      }),
      coverage({
        technology: 'UNII',
        condition_hash: 'b',
        attempt_count: 3,
        distinct_operator_count: 1,
      }),
      coverage({
        technology: 'UNII',
        condition_hash: 'c',
        attempt_count: 1,
        distinct_operator_count: 1,
      }),
    ] as never);
    const unii = summaries.find((s) => s.technology === 'UNII');
    expect(unii?.crossEngineerDuplicate).toBe(1); // only 'a' has 2 operators
    expect(unii?.repeated).toBe(2); // 'a' + 'b' have attempt_count > 1
  });

  it('renders a cross-engineer-duplicate warning but a softer re-measure note', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchCoveragePage.mockResolvedValue(
      page([
        coverage({ condition_hash: 'aaaaaaaaaaaa1', attempt_count: 2, distinct_operator_count: 2 }),
        coverage({ condition_hash: 'bbbbbbbbbbbb2', attempt_count: 3, distinct_operator_count: 1 }),
      ]),
    );
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument());
    // Tech-level: ⚠ cross-duplicate count (1) distinct from re-measure count (2).
    expect(screen.getByTestId('tech-cross-duplicate')).toHaveTextContent('1');
    expect(screen.getByTestId('tech-repeated')).toHaveTextContent('2');
    // Condition-level: the 2-operator condition is a cross-engineer duplicate; the
    // 1-operator one is only a re-measure (no ⚠ duplicate label).
    expect(screen.getByTestId('condition-cross-duplicate')).toBeInTheDocument();
    expect(screen.getByTestId('condition-repeated')).toHaveTextContent('재측정');
  });
});

describe('FE-SYNC central freshness banner', () => {
  it('formatAge renders seconds / minutes / hours / days', () => {
    expect(formatAge(30)).toBe('30초');
    expect(formatAge(120)).toBe('2분');
    expect(formatAge(7200)).toBe('2시간');
    expect(formatAge(172800)).toBe('2일');
  });

  it('shows a fresh (non-alert) banner with the last central ingest', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    platformApi.fetchSyncStatus.mockResolvedValue(
      syncStatus({ is_stale: false, age_seconds: 120 }),
    );
    renderProjects(`/projects?project=${PROJECT_ID}`);
    const banner = await screen.findByTestId('sync-status');
    expect(banner).toHaveAttribute('data-stale', 'false');
    expect(banner).toHaveTextContent('마지막 중앙 반영');
    expect(banner).toHaveTextContent('2분 전');
    // Same role as the stale branch below — the banner's identity must not
    // change with its content (W4-A M4).
    expect(banner).toHaveAttribute('role', 'status');
  });

  it('softens the duplicate-prevention guarantee when stale', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    platformApi.fetchSyncStatus.mockResolvedValue(
      syncStatus({ is_stale: true, age_seconds: 7200 }),
    );
    renderProjects(`/projects?project=${PROJECT_ID}`);
    const banner = await screen.findByTestId('sync-status');
    expect(banner).toHaveAttribute('data-stale', 'true');
    // W4-A M4 — polite, and the SAME role in both freshness states. Staleness
    // is a condition the screen was already in when it loaded, not an answer
    // to something the operator just did; and a role that flips under
    // assistive tech re-announces on every refetch.
    expect(banner).toHaveAttribute('role', 'status');
    expect(banner).toHaveAttribute('aria-live', 'polite');
    expect(banner).toHaveTextContent('보장이 약해집니다');
  });

  it('notes when the project has no central measurement yet', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchCoveragePage.mockResolvedValue(EMPTY_PAGE);
    platformApi.fetchSyncStatus.mockResolvedValue(
      syncStatus({ last_ingested_at: null, age_seconds: null, condition_count: 0 }),
    );
    renderProjects(`/projects?project=${PROJECT_ID}`);
    const banner = await screen.findByTestId('sync-status');
    expect(banner).toHaveTextContent('반영된 측정이 아직 없습니다');
  });
});

describe('FE-P3-write claim controls — offline guard', () => {
  it('disables the claim button and shows an offline note when offline', async () => {
    setOnline(false);
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchCoveragePage.mockResolvedValue(page([coverage({})]));
    renderProjects(`/projects?project=${PROJECT_ID}&tech=UNII`);

    const button = await screen.findByTestId('condition-claim-acquire');
    expect(button).toBeDisabled();
    expect(screen.getByTestId('claim-offline')).toBeInTheDocument();
    // A disabled button cannot fire the write.
    await userEvent.click(button).catch(() => undefined);
    expect(platformApi.acquireClaim).not.toHaveBeenCalled();
  });
});
