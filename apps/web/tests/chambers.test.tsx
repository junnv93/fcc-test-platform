import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { REFETCH_STRATEGIES } from '@/api/query-config';
import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { t } from '@/i18n';
import { ChambersRoute, isStartableChamber } from '@/routes/chambers';
import { HEARTBEAT_AGE_TICK_INTERVAL_MS } from '@/shared/heartbeat-age';
import {
  chamberStatusKind,
  isKnownChamberStatus,
  KNOWN_CHAMBER_STATUSES,
  streamStatusKind,
  streamStatusLabelToken,
} from '@/ui';

import { headlessOk } from './helpers/headless-contract';
import { spyHeadlessTransport } from './helpers/headless-transport';

import type { HeadlessOkBody } from './helpers/headless-contract';
import type { ReactElement } from 'react';

/**
 * 멀티챔버 P6 — 시험 챔버 화면 테스트.
 *
 * 화면은 platform read API(가용성)와 중앙 프록시(측정 시작/진행)를 typed client
 * 헬퍼(`fetchChambers`/`startChamberMeasurement`/`fetchChamberProgress`, 여기서
 * mock)로 소비한다. 테스트는 RBAC 게이트(platform:read 읽기 / platform:claim 시작) +
 * 가용성 렌더 + idle 챔버만 시작 가능 + 시작→진행 폴링 배선을 검증한다(와이어 형태는
 * platform-client.test.ts 가 담당).
 */

const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
  fetchSampleInventory: vi.fn(),
  fetchChambers: vi.fn(),
  registerChamber: vi.fn(),
  updateChamberWebSessionApproval: vi.fn(),
  startChamberMeasurement: vi.fn(),
  fetchChamberProgress: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

// The chamber starter sources plan suggestions from the headless publications
// read (G2 server SSOT); mock the typed client GET so the consumer wiring can
// be exercised without a backend.
const headlessClient = spyHeadlessTransport();

// M3 — the WS relay's connection lifecycle drives a first-class badge, so the
// tests need to drive it. Only the hook is replaced (the cache-write half is
// covered end-to-end in chamber-progress-stream.test.ts); the route consumes
// nothing else from this module.
const chamberStream = vi.hoisted(() => ({ status: 'open' }));
vi.mock('@/api/chamber-progress-stream', () => ({
  useChamberProgressStream: () => ({ status: chamberStream.status }),
}));

/** A syntactically valid UUID — the publications query is UUID-gated client-side. */
const PROJECT_ID = '22222222-2222-4222-8222-222222222222';

/** openapi-fetch success envelope for the publications GET. */
const PUBLICATIONS_PATH = '/headless/projects/{project_id}/test-plan/publications';

type Publications = HeadlessOkBody<'get', typeof PUBLICATIONS_PATH>['publications'];

function pubsOk(publications: Publications) {
  return headlessOk('get', PUBLICATIONS_PATH, { publications });
}

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

function authenticateAs(permissions: readonly string[]): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: 'op@corp', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function chamber(over: Record<string, unknown>): Record<string, unknown> {
  return {
    chamber_id: 'cham-1',
    name: 'Chamber 1',
    base_url: 'http://node-1:8000',
    enabled: true,
    status: 'idle',
    heartbeat_ttl_seconds: 30,
    last_heartbeat_at: '2026-06-16T00:00:00+00:00',
    reported_status: 'idle',
    session_id: null,
    last_error: null,
    last_error_at: null,
    unavailable_reason: null,
    ...over,
  };
}

function list(items: unknown[]): Record<string, unknown> {
  return { items, server_time: '2026-06-16T00:00:00+00:00' };
}

function renderChambers(withStartContext = true): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter
        initialEntries={[
          withStartContext ? `/chambers?project=${PROJECT_ID}&sample=sample-1` : '/chambers',
        ]}
      >
        <ChambersRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  localStorage.clear();
  platformApi.fetchProjectsPage.mockReset();
  platformApi.fetchSampleInventory.mockReset();
  platformApi.fetchChambers.mockReset();
  platformApi.registerChamber.mockReset();
  platformApi.updateChamberWebSessionApproval.mockReset();
  platformApi.startChamberMeasurement.mockReset();
  platformApi.fetchChamberProgress.mockReset();
  headlessClient.GET.mockReset();
  headlessClient.GET.mockResolvedValue(pubsOk([]));
  platformApi.fetchProjectsPage.mockResolvedValue({
    items: [
      {
        project_id: PROJECT_ID,
        project_code: 'SM-TEST',
        model_name: 'SM-TEST',
        customer: null,
        manufacturer: null,
        management_number: 'M-001',
        status: 'active',
        sample_count: 0,
      },
    ],
    nextCursor: null,
  });
  platformApi.fetchSampleInventory.mockResolvedValue({
    items: [
      {
        sample_id: 'sample-1',
        project_id: PROJECT_ID,
        status: 'active',
        sample_number: 'S-001',
        label_number: null,
        sample_code: null,
        label: 'Sample 1',
      },
    ],
    next_cursor: null,
  });
  platformApi.fetchChambers.mockResolvedValue(list([]));
  platformApi.registerChamber.mockResolvedValue({ chamber_id: 'cham-1' });
  platformApi.updateChamberWebSessionApproval.mockResolvedValue({ chamber_id: 'cham-1' });
  platformApi.fetchChamberProgress.mockResolvedValue({
    chamber_id: '',
    progress: { is_running: false, completed: 0, total: 0, ratio: 0 },
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('isStartableChamber', () => {
  it('is true only for an enabled idle chamber', () => {
    expect(isStartableChamber(chamber({ status: 'idle', enabled: true }) as never)).toBe(true);
    expect(isStartableChamber(chamber({ status: 'in_use', enabled: true }) as never)).toBe(false);
    expect(isStartableChamber(chamber({ status: 'offline', enabled: true }) as never)).toBe(false);
    expect(isStartableChamber(chamber({ status: 'idle', enabled: false }) as never)).toBe(false);
  });
});

describe('ChambersRoute', () => {
  it('denies the availability view without platform:read', () => {
    authenticateAs([]);
    renderChambers();
    expect(screen.getByTestId('auth-failure-permission_denied')).toBeInTheDocument();
    expect(platformApi.fetchChambers).not.toHaveBeenCalled();
  });

  it('renders chamber availability rows with status badges', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'a', name: 'Alpha', status: 'idle' })]),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-table')).toBeInTheDocument());
    expect(screen.getByTestId('chambers-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('chambers-workbench')).toBeInTheDocument();
    expect(screen.getByTestId('chambers-next-sessions')).toHaveAttribute('href', '/sessions');
    // Scoped to the availability table (SPLIT-6 ③). The assertion always meant
    // "the availability table lists Alpha"; an unscoped `getByText` only stood in
    // for that while the chamber name appeared exactly once on the route. The
    // equipment-config picker is a second, legitimate place for it, so the query
    // now says what it always meant rather than the panel avoiding the name.
    expect(within(screen.getByTestId('chambers-table')).getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByTestId('chambers-status')).toBeInTheDocument();
  });

  it('shows an empty state when no chambers are registered', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(list([]));
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-empty')).toBeInTheDocument());
    expect(screen.queryByTestId('chambers-admin-bootstrap')).not.toBeInTheDocument();
  });

  it('lets a platform admin bootstrap the first chamber with registration only', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchChambers
      .mockResolvedValueOnce(list([]))
      .mockResolvedValueOnce(list([chamber({ chamber_id: 'boot-1', name: 'Boot Chamber' })]));
    platformApi.registerChamber.mockResolvedValue({ chamber_id: 'boot-1' });

    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-admin-bootstrap')).toBeInTheDocument());
    await userEvent.type(screen.getByTestId('chambers-bootstrap-id'), 'boot-1');
    await userEvent.type(screen.getByTestId('chambers-bootstrap-name'), 'Boot Chamber');
    await userEvent.type(
      screen.getByTestId('chambers-bootstrap-base-url'),
      'http://node-boot:9000',
    );
    await userEvent.clear(screen.getByTestId('chambers-bootstrap-ttl'));
    await userEvent.type(screen.getByTestId('chambers-bootstrap-ttl'), '90');
    await userEvent.click(screen.getByTestId('chambers-bootstrap-submit'));

    await waitFor(() =>
      expect(platformApi.registerChamber).toHaveBeenCalledWith({
        chamber_id: 'boot-1',
        name: 'Boot Chamber',
        base_url: 'http://node-boot:9000',
        enabled: true,
        heartbeat_ttl_seconds: 90,
      }),
    );
    expect(platformApi.updateChamberWebSessionApproval).not.toHaveBeenCalled();
    await waitFor(() => expect(platformApi.fetchChambers).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId('chambers-admin-success')).toHaveTextContent(/Boot Chamber|boot-1/);
  });

  it('rejects invalid first-chamber bootstrap input before POST', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchChambers.mockResolvedValue(list([]));
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-admin-bootstrap')).toBeInTheDocument());

    await userEvent.type(screen.getByTestId('chambers-bootstrap-id'), 'boot-1');
    await userEvent.type(screen.getByTestId('chambers-bootstrap-name'), 'Boot Chamber');
    await userEvent.type(screen.getByTestId('chambers-bootstrap-base-url'), 'not-a-url');
    await userEvent.clear(screen.getByTestId('chambers-bootstrap-ttl'));
    await userEvent.type(screen.getByTestId('chambers-bootstrap-ttl'), '0');
    fireEvent.submit(screen.getByTestId('chambers-admin-bootstrap-form'));

    expect(await screen.findByTestId('chambers-admin-bootstrap-validation')).toBeInTheDocument();
    expect(platformApi.registerChamber).not.toHaveBeenCalled();
  });

  it('keeps the P12 overview visible for a successful zero-chamber response', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(list([]));
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-fleet')).toBeInTheDocument());
    expect(screen.getByTestId('chambers-fleet-total')).toHaveTextContent('0');
    expect(screen.getByTestId('chambers-fleet-idle')).toHaveTextContent('0');
    expect(screen.getByTestId('chambers-fleet-in_use')).toHaveTextContent('0');
    expect(screen.getByTestId('chambers-fleet-offline')).toHaveTextContent('0');
    expect(screen.getByTestId('chambers-run-empty')).toBeInTheDocument();
    expect(platformApi.fetchChamberProgress).not.toHaveBeenCalled();
  });

  it('hides the start form for a viewer without platform:claim', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(list([chamber({ status: 'idle' })]));
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-table')).toBeInTheDocument());
    expect(screen.queryByTestId('chambers-start-form')).not.toBeInTheDocument();
  });

  it('hides chamber administration without platform:admin', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(list([chamber({ chamber_id: 'a' })]));
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-table')).toBeInTheDocument());
    expect(screen.queryByTestId('chambers-admin')).not.toBeInTheDocument();
  });

  it('lets a platform admin update chamber registry fields without exposing token secrets', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'a', name: 'Alpha', last_heartbeat_at: '2026-06-15T23:59:30+00:00' }),
      ]),
    );
    platformApi.registerChamber.mockResolvedValue({ chamber_id: 'a' });

    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-admin')).toBeInTheDocument());

    await userEvent.clear(screen.getByTestId('chambers-admin-name'));
    await userEvent.type(screen.getByTestId('chambers-admin-name'), 'Alpha Lab');
    await userEvent.clear(screen.getByTestId('chambers-admin-base-url'));
    await userEvent.type(screen.getByTestId('chambers-admin-base-url'), 'http://node-a:9000');
    await userEvent.clear(screen.getByTestId('chambers-admin-ttl'));
    await userEvent.type(screen.getByTestId('chambers-admin-ttl'), '45');
    await userEvent.click(screen.getByTestId('chambers-admin-save'));

    await waitFor(() =>
      expect(platformApi.registerChamber).toHaveBeenCalledWith({
        chamber_id: 'a',
        name: 'Alpha Lab',
        base_url: 'http://node-a:9000',
        enabled: true,
        heartbeat_ttl_seconds: 45,
      }),
    );
    // The age now flows with the wall clock (M4), so its exact value depends on
    // how long the interactions above took. The *value* is sealed deterministically
    // under fake timers in the "heartbeat age flows" suite below; here we only
    // assert the cell still renders a seconds-resolution age.
    expect(screen.getByTestId('chambers-admin-heartbeat-age')).toHaveTextContent(/^\d+초$/);
    // M2 — no node-reported error ⇒ non-noisy placeholder, no error message node.
    expect(screen.getByTestId('chambers-admin-last-error')).toHaveTextContent(
      /보고된 오류 없음|No error reported/,
    );
    expect(screen.queryByTestId('chambers-admin-last-error-message')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chambers-admin-token')).not.toBeInTheDocument();
  });

  it('renders the M2 diagnostics overlay (last_error + unavailable_reason) when present', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({
          chamber_id: 'a',
          status: 'offline',
          last_error: 'analyzer connect failed',
          last_error_at: '2026-06-15T23:50:00+00:00',
          unavailable_reason: 'heartbeat_timeout',
        }),
      ]),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-admin')).toBeInTheDocument());

    expect(screen.getByTestId('chambers-admin-last-error-message')).toHaveTextContent(
      'analyzer connect failed',
    );
    // Phase L (§4): "heartbeat" jargon → tester-language "통신 없음 / No signal".
    expect(screen.getByTestId('chambers-admin-unavailable-reason')).toHaveTextContent(
      /통신 없음|No signal/,
    );
  });

  it('lets a platform admin disable a chamber through the registry upsert', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchChambers.mockResolvedValue(list([chamber({ chamber_id: 'a' })]));
    platformApi.registerChamber.mockResolvedValue({ chamber_id: 'a' });

    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-admin')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('chambers-admin-toggle'));

    await waitFor(() =>
      expect(platformApi.registerChamber).toHaveBeenCalledWith({
        chamber_id: 'a',
        name: 'Chamber 1',
        base_url: 'http://node-1:8000',
        enabled: false,
        heartbeat_ttl_seconds: 30,
      }),
    );
  });

  it('keeps web-session approval as a separate PATCH after registration', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchChambers.mockResolvedValue(list([chamber({ chamber_id: 'a' })]));
    platformApi.updateChamberWebSessionApproval.mockResolvedValue({ chamber_id: 'a' });

    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-admin')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-admin-approval'), 'yes');

    await waitFor(() =>
      expect(platformApi.updateChamberWebSessionApproval).toHaveBeenCalledWith('a', true),
    );
    expect(platformApi.registerChamber).not.toHaveBeenCalled();
  });

  it('lists only startable chambers and starts a measurement, then polls progress', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' }),
        chamber({ chamber_id: 'busy-1', name: 'Busy', status: 'in_use' }),
      ]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 0, total: 10, ratio: 0 },
    });
    platformApi.fetchChamberProgress.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: false, completed: 10, total: 10, ratio: 1 },
    });

    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());

    const select = screen.getByTestId('chambers-start-select');
    // Only the idle chamber is an option (plus the placeholder).
    expect(within(select).queryByText('Busy')).not.toBeInTheDocument();
    await userEvent.selectOptions(select, 'idle-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));

    await waitFor(() =>
      expect(platformApi.startChamberMeasurement).toHaveBeenCalledWith('idle-1', {
        published_plan_id: null,
        project_id: PROJECT_ID,
        sample_id: 'sample-1',
      }),
    );
    await waitFor(() => expect(screen.getByTestId('chambers-start-success')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('chambers-progress')).toBeInTheDocument());
    await waitFor(() => expect(platformApi.fetchChamberProgress).toHaveBeenCalledWith('idle-1'));
    // A2: the post-start panel renders the ProgressBar primitive alongside the
    // metric strip — completed 10/10 ⇒ determinate 100%.
    await waitFor(() =>
      expect(
        within(screen.getByTestId('chambers-progress-bar')).getByRole('progressbar'),
      ).toHaveAttribute('aria-valuenow', '100'),
    );
  });

  it('shows an indeterminate progress bar for a started run with no test count yet', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 0, total: 0, ratio: 0 },
    });
    // Running but the test count has not been seeded yet (total 0) — the bar
    // must be indeterminate, never a misleading 0% determinate.
    platformApi.fetchChamberProgress.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 0, total: 0, ratio: 0 },
    });
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));

    await waitFor(() =>
      expect(screen.getByTestId('chambers-progress-bar')).toHaveAttribute(
        'data-state',
        'indeterminate',
      ),
    );
    expect(
      within(screen.getByTestId('chambers-progress-bar')).getByRole('progressbar'),
    ).not.toHaveAttribute('aria-valuenow');
  });

  it('forwards a published_plan_id when provided', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: false, completed: 0, total: 0, ratio: 0 },
    });
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.type(screen.getByTestId('chambers-start-plan'), 'plan-42');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));
    await waitFor(() =>
      expect(platformApi.startChamberMeasurement).toHaveBeenCalledWith('idle-1', {
        published_plan_id: 'plan-42',
        project_id: PROJECT_ID,
        sample_id: 'sample-1',
      }),
    );
  });

  it('forwards a sample number when entered (ADR-0017 Phase 3)', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: false, completed: 0, total: 0, ratio: 0 },
    });
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.selectOptions(screen.getByTestId('chambers-start-sample'), 'sample-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));
    await waitFor(() =>
      expect(platformApi.startChamberMeasurement).toHaveBeenCalledWith('idle-1', {
        published_plan_id: null,
        project_id: PROJECT_ID,
        sample_id: 'sample-1',
      }),
    );
  });
});

/**
 * Chamber remote-measurement UX follow-up — actionable recovery guidance for
 * start/progress failures + contextual next-action affordances. These verify
 * that a failed start does not dead-end (each failure class surfaces a recovery
 * hint plus the right hub-backed follow-up screen, never a chamber node), and
 * that a running/errored progress panel offers next steps in place.
 */
async function startAndFail(err: unknown): Promise<void> {
  authenticateAs(['platform:read', 'platform:claim']);
  platformApi.fetchChambers.mockResolvedValue(
    list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
  );
  platformApi.startChamberMeasurement.mockRejectedValue(err);
  renderChambers();
  await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
  await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
  await userEvent.click(screen.getByTestId('chambers-start-submit'));
  await waitFor(() => expect(screen.getByTestId('chambers-start-error')).toBeInTheDocument());
}

/** Decorate an Error with an HTTP status (and optionally the RFC 9457 `code`
 *  extension), mirroring the api-error shape routes throw
 *  (`status === undefined` ⇒ network/offline). */
function apiError(message: string, status?: number, code?: string): Error {
  const fields: Record<string, unknown> = { status };
  if (code !== undefined) fields.code = code;
  return Object.assign(new Error(message), fields);
}

describe('chamber start failure recovery guidance', () => {
  it('offers a permission-request hint with no self-service link on a 403', async () => {
    await startAndFail(apiError('forbidden', 403));
    expect(screen.getByTestId('chambers-start-recovery-text')).toHaveTextContent(/\S/u);
    // A permission failure's only fix is an admin grant — no follow-up screen.
    expect(screen.queryByTestId('chambers-start-recovery-sessions')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chambers-start-recovery-diagnostics')).not.toBeInTheDocument();
  });

  it('links to measurement history on a 409 not-idle conflict', async () => {
    await startAndFail(apiError('conflict', 409));
    expect(screen.getByTestId('chambers-start-recovery-text')).toHaveTextContent(/\S/u);
    expect(screen.getByTestId('chambers-start-recovery-sessions')).toHaveAttribute(
      'href',
      '/sessions',
    );
    // Conflict is not a connectivity problem — no diagnostics link.
    expect(screen.queryByTestId('chambers-start-recovery-diagnostics')).not.toBeInTheDocument();
  });

  it('links to diagnostics when the hub is unreachable (no status)', async () => {
    await startAndFail(apiError('offline'));
    expect(screen.getByTestId('chambers-start-recovery-diagnostics')).toHaveAttribute(
      'href',
      '/diagnostics',
    );
  });

  it('links to diagnostics on an unknown server failure (500)', async () => {
    await startAndFail(apiError('boom', 500));
    expect(screen.getByTestId('chambers-start-recovery-diagnostics')).toHaveAttribute(
      'href',
      '/diagnostics',
    );
  });

  it('offers a diagnostics affordance when no chamber is startable', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'busy-1', name: 'Busy', status: 'in_use' }),
        chamber({ chamber_id: 'off-1', name: 'Off', status: 'offline' }),
      ]),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-none-startable')).toBeInTheDocument());
    expect(screen.getByTestId('chambers-none-startable-diagnostics')).toHaveAttribute(
      'href',
      '/diagnostics',
    );
  });
});

/**
 * M1 (D1) + M2 (D2) — the backend already distinguishes 404 (chamber not
 * registered) / 409 (not idle) / 503 (node unreachable) and ships the
 * distinction twice: as an HTTP status AND as the RFC 9457 `code`. The FE used
 * to project all of it onto a 4-arm status ladder whose `default` swallowed 404
 * and 503 into one "unknown server failure" sentence, so two failures with
 * opposite recovery actions read identically.
 */
describe('M1 — start failure taxonomy consumes the backend distinction', () => {
  /** The recovery sentence currently on screen. */
  const recoveryText = (): string =>
    screen.getByTestId('chambers-start-recovery-text').textContent ?? '';

  it('separates an unregistered chamber (404) from an unreachable node (503)', async () => {
    await startAndFail(apiError('missing', 404, 'NOT_FOUND'));
    const notFoundCopy = recoveryText();
    expect(notFoundCopy).toMatch(/\S/u);
    // 404 is not a connectivity problem — the chamber simply is not registered,
    // and no self-service screen fixes that.
    expect(screen.queryByTestId('chambers-start-recovery-diagnostics')).not.toBeInTheDocument();

    cleanup();
    await startAndFail(apiError('node down', 503, 'UPSTREAM_UNAVAILABLE'));
    const unavailableCopy = recoveryText();
    expect(unavailableCopy).toMatch(/\S/u);
    // A node that does not answer IS a connectivity question.
    expect(screen.getByTestId('chambers-start-recovery-diagnostics')).toBeInTheDocument();

    // The point of the milestone: two different failures, two different
    // sentences (both previously collapsed into `recoveryUnknown`).
    expect(notFoundCopy).not.toBe(unavailableCopy);
  });

  it('neither 404 nor 503 falls back to the generic unknown-failure sentence', async () => {
    await startAndFail(apiError('boom', 500));
    const unknownCopy = recoveryText();
    cleanup();

    await startAndFail(apiError('missing', 404, 'NOT_FOUND'));
    expect(recoveryText()).not.toBe(unknownCopy);
    cleanup();

    await startAndFail(apiError('node down', 503, 'UPSTREAM_UNAVAILABLE'));
    expect(recoveryText()).not.toBe(unknownCopy);
  });

  it('classifies on the RFC 9457 code even when the status disagrees (S2)', async () => {
    // A response that carries `code` but no usable status is precisely the case
    // the status ladder cannot serve: status-first would read this as a network
    // failure and tell the operator to check their connection.
    await startAndFail(apiError('missing', undefined, 'NOT_FOUND'));
    const codeOnlyCopy = recoveryText();
    cleanup();

    await startAndFail(apiError('offline', undefined));
    expect(codeOnlyCopy).not.toBe(recoveryText());
    cleanup();

    await startAndFail(apiError('missing', 404, 'NOT_FOUND'));
    expect(recoveryText()).toBe(codeOnlyCopy);
  });

  it('keeps the error message and the recovery hint telling the same story', async () => {
    // The module contract: the message taxonomy (`describeApiError`) and this
    // recovery taxonomy must not disagree. `describeApiError` collapses BOTH 404
    // and 503 into copy the route never specialised — 503 even lands on the
    // generic `errors.default`, i.e. LESS specific than the 500 it outranks in
    // diagnosability. Widening the recovery arms without widening the overrides
    // would leave the message and the hint saying different things.
    await startAndFail(apiError('node down', 503, 'UPSTREAM_UNAVAILABLE'));
    expect(screen.getByTestId('chambers-start-error')).not.toHaveTextContent(t('errors.default'));
    cleanup();
    await startAndFail(apiError('missing', 404, 'NOT_FOUND'));
    expect(screen.getByTestId('chambers-start-error')).not.toHaveTextContent(t('errors.default'));
  });
});

describe('M2 — the 409 cause is shown, and shown to readers', () => {
  const SERVER_NOW_MS = Date.parse('2026-06-16T00:00:00+00:00');

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the unavailability reason to a read-only operator (S3)', async () => {
    // The reason used to render only inside the `platform:admin` panel, binding
    // an information axis to a write-permission axis. The operator deciding
    // which chamber to use is exactly the one without admin.
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({
          chamber_id: 'off-1',
          status: 'offline',
          unavailable_reason: 'heartbeat_timeout',
        }),
      ]),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-table')).toBeInTheDocument());

    expect(screen.queryByTestId('chambers-admin')).not.toBeInTheDocument();
    expect(screen.getByTestId('chambers-unavailable-reason')).toHaveTextContent(
      /통신 없음|No signal/,
    );
  });

  it('does not tell the operator to retry a chamber that has gone offline (S4)', async () => {
    // `fireEvent`, not `userEvent`: this case needs fake timers to land the
    // supervision poll deterministically, and userEvent's internal delays do not
    // resolve against them.
    vi.useFakeTimers({ now: SERVER_NOW_MS });
    authenticateAs(['platform:read', 'platform:claim']);
    // Two chambers so the start form survives the transition (a fleet with no
    // startable chamber renders the empty state instead).
    platformApi.fetchChambers
      .mockResolvedValueOnce(
        list([
          chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' }),
          chamber({ chamber_id: 'idle-2', name: 'Spare', status: 'idle' }),
        ]),
      )
      .mockResolvedValue(
        list([
          chamber({
            chamber_id: 'idle-1',
            name: 'Free',
            status: 'offline',
            unavailable_reason: 'heartbeat_timeout',
          }),
          chamber({ chamber_id: 'idle-2', name: 'Spare', status: 'idle' }),
        ]),
      );
    platformApi.startChamberMeasurement.mockRejectedValue(apiError('not idle', 409, 'CONFLICT'));

    renderChambers();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    act(() => {
      fireEvent.change(screen.getByTestId('chambers-start-select'), {
        target: { value: 'idle-1' },
      });
    });
    expect(screen.getByTestId('chambers-start-select')).toHaveValue('idle-1');

    // The supervision poll lands the offline transition while the operator is
    // still filling the form — the realistic path to a 409 whose cause the
    // screen already knows.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(REFETCH_STRATEGIES.MONITORED.refetchInterval);
    });
    // The chamber is gone from the selectable set, but it is still the submitted
    // one — the exact state in which the old code lost the evidence.
    expect(screen.getByTestId('chambers-start-select')).not.toHaveValue('idle-1');

    act(() => {
      fireEvent.click(screen.getByTestId('chambers-start-submit'));
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });

    expect(screen.getByTestId('chambers-start-recovery-text')).toBeInTheDocument();
    // "wait and retry" is advice that never comes true for an offline chamber.
    expect(screen.getByTestId('chambers-start-recovery-text')).not.toHaveTextContent(
      /다시 시도|retry/iu,
    );
    // Connectivity IS the question here, unlike a plain in-use conflict.
    expect(screen.getByTestId('chambers-start-recovery-diagnostics')).toBeInTheDocument();
  });

  it('keeps the generic conflict advice when the cause is not known (safe degrade)', async () => {
    // The availability row still says idle (the poll has not caught up), so the
    // screen must not invent a cause — it degrades to the existing wait-and-retry
    // sentence rather than guessing "offline".
    await startAndFail(apiError('not idle', 409, 'CONFLICT'));
    expect(screen.getByTestId('chambers-start-recovery-text')).toHaveTextContent(/다시 시도/u);
    expect(screen.getByTestId('chambers-start-recovery-sessions')).toBeInTheDocument();
  });
});

/**
 * M3 (D3) — the relay's connection state was produced and thrown away: the
 * workbench called `useChamberProgressStream()` for its cache side-effect and
 * discarded the returned status, so a dead relay looked exactly like a live one
 * and the fallback poll was the only thing keeping the numbers moving.
 */
describe('M3 — live/polling state is visible on the multi-chamber surface', () => {
  afterEach(() => {
    chamberStream.status = 'open';
  });

  async function renderWithStream(status: string): Promise<void> {
    chamberStream.status = status;
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(list([chamber({ chamber_id: 'a' })]));
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-table')).toBeInTheDocument());
  }

  it.each(['connecting', 'open', 'reconnecting', 'closed'])(
    'renders a %s relay as a badge carrying the shared status kind',
    async (status) => {
      await renderWithStream(status);
      const badge = screen.getByTestId('chambers-stream-status');
      expect(badge).toHaveClass(`status-badge--${streamStatusKind(status as never)}`);
    },
  );

  it('labels the badge from the shared vocabulary, not a chambers-local one (S5)', async () => {
    await renderWithStream('reconnecting');
    // `/control` renders the SAME token through the SAME key namespace — the
    // property that keeps one state from having two names across two screens.
    expect(screen.getByTestId('chambers-stream-status')).toHaveTextContent(
      t(`ui.streamStatus.${streamStatusLabelToken('reconnecting')}`),
    );
  });

  it('surfaces a policy-rejected / retry-exhausted relay as a closed channel', async () => {
    // W1 produced these terminal states but left "표시 자체는 W2 범위". `closed`
    // is what the operator must see: the numbers below are poll-only from here.
    await renderWithStream('closed');
    const badge = screen.getByTestId('chambers-stream-status');
    expect(badge).toHaveClass('status-badge--missing');
    expect(badge).toHaveTextContent(t('ui.streamStatus.closed'));
  });
});

describe('chamber progress next-action affordances', () => {
  it('surfaces history/queue/reports next steps in the running progress panel', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 2, total: 10, ratio: 0.2 },
    });
    platformApi.fetchChamberProgress.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 2, total: 10, ratio: 0.2 },
    });
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));

    const nav = await screen.findByTestId('chambers-progress-next');
    expect(within(nav).getByTestId('chambers-progress-next-sessions')).toHaveAttribute(
      'href',
      '/sessions',
    );
    expect(within(nav).getByTestId('chambers-progress-next-jobs')).toHaveAttribute('href', '/jobs');
    expect(within(nav).getByTestId('chambers-progress-next-reports')).toHaveAttribute(
      'href',
      `/reports?project=${PROJECT_ID}`,
    );
  });

  it('project-scopes the progress reports link when a valid project is selected', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 2, total: 10, ratio: 0.2 },
    });
    platformApi.fetchChamberProgress.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 2, total: 10, ratio: 0.2 },
    });
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    // Selecting a valid project carries `projectId` into the post-start progress
    // panel, so the reports next-action lands filtered to that project while the
    // session/queue screens (global) stay unscoped.
    await userEvent.selectOptions(screen.getByTestId('chambers-start-project-select'), PROJECT_ID);
    await userEvent.selectOptions(screen.getByTestId('chambers-start-sample'), 'sample-1');
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));

    const nav = await screen.findByTestId('chambers-progress-next');
    expect(within(nav).getByTestId('chambers-progress-next-reports')).toHaveAttribute(
      'href',
      `/reports?project=${PROJECT_ID}`,
    );
    // Global operator screens are never project-scoped.
    expect(within(nav).getByTestId('chambers-progress-next-sessions')).toHaveAttribute(
      'href',
      '/sessions',
    );
    expect(within(nav).getByTestId('chambers-progress-next-jobs')).toHaveAttribute('href', '/jobs');
  });

  it('offers a diagnostics affordance when progress cannot be loaded (hard error)', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 0, total: 0, ratio: 0 },
    });
    // The first progress poll fails with no prior data ⇒ hard error branch.
    platformApi.fetchChamberProgress.mockRejectedValue(apiError('offline'));
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));

    await waitFor(() => expect(screen.getByTestId('chambers-progress-error')).toBeInTheDocument());
    expect(screen.getByTestId('chambers-progress-error-diagnostics')).toHaveAttribute(
      'href',
      '/diagnostics',
    );
  });
});

describe('P12 chamber fleet overview', () => {
  it('shows a fleet summary strip with per-status counts', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'a', name: 'Alpha', status: 'idle' }),
        chamber({ chamber_id: 'b', name: 'Bravo', status: 'in_use', session_id: '7' }),
        chamber({ chamber_id: 'c', name: 'Charlie', status: 'offline' }),
      ]),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-fleet')).toBeInTheDocument());
    expect(screen.getByTestId('chambers-fleet-total')).toHaveTextContent('3');
    expect(screen.getByTestId('chambers-fleet-idle')).toHaveTextContent('1');
    expect(screen.getByTestId('chambers-fleet-in_use')).toHaveTextContent('1');
    expect(screen.getByTestId('chambers-fleet-offline')).toHaveTextContent('1');
  });

  it('shows progress of running chambers without starting a measurement', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' }),
        chamber({ chamber_id: 'busy-1', name: 'Busy', status: 'in_use', session_id: '42' }),
      ]),
    );
    platformApi.fetchChamberProgress.mockResolvedValue({
      chamber_id: 'busy-1',
      progress: { is_running: true, completed: 4, total: 10, ratio: 0.4 },
    });
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-run-table')).toBeInTheDocument());
    // Progress is polled for the in-use chamber, not the idle one.
    await waitFor(() => expect(platformApi.fetchChamberProgress).toHaveBeenCalledWith('busy-1'));
    expect(platformApi.fetchChamberProgress).not.toHaveBeenCalledWith('idle-1');
    await waitFor(() =>
      expect(screen.getByTestId('chambers-run-completed')).toHaveTextContent('4 / 10'),
    );
    expect(screen.getByTestId('chambers-run-ratio')).toHaveTextContent('40%');
    // A2: the ratio cell renders the ProgressBar primitive (determinate) — the
    // bar visualizes the same C1 ratio with the full ARIA value contract.
    const runBar = within(screen.getByTestId('chambers-run-progress-bar')).getByRole('progressbar');
    expect(runBar).toHaveAttribute('aria-valuenow', '40');
    expect(runBar).toHaveAttribute('aria-valuemax', '100');
    // A numeric session id deep-links into the session history.
    expect(screen.getByTestId('chambers-run-session-link')).toHaveAttribute(
      'href',
      '/sessions?session=42',
    );
    // No measurement was started to see this.
    expect(platformApi.startChamberMeasurement).not.toHaveBeenCalled();
  });

  it('renders a session id as plain text when it is not a numeric id', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'busy-1', name: 'Busy', status: 'in_use', session_id: 'node-xyz' }),
      ]),
    );
    platformApi.fetchChamberProgress.mockResolvedValue({
      chamber_id: 'busy-1',
      progress: { is_running: false, completed: 10, total: 10, ratio: 1 },
    });
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-run-row')).toBeInTheDocument());
    expect(screen.queryByTestId('chambers-run-session-link')).not.toBeInTheDocument();
    expect(screen.getByTestId('chambers-run-row')).toHaveTextContent('node-xyz');
  });

  it('shows an empty run state when no chamber is in use', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'a', name: 'Alpha', status: 'idle' })]),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-run-empty')).toBeInTheDocument());
    expect(platformApi.fetchChamberProgress).not.toHaveBeenCalled();
  });

  it('shows a hard error in the run row when the initial progress fetch fails', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'busy-1', name: 'Busy', status: 'in_use', session_id: '42' })]),
    );
    platformApi.fetchChamberProgress.mockRejectedValue(
      Object.assign(new Error('progress unreachable'), { status: undefined }),
    );
    renderChambers();
    await waitFor(() =>
      expect(screen.getByTestId('chambers-run-completed')).toHaveTextContent(
        /진행 상황 조회 실패|Failed to load progress/,
      ),
    );
  });

  it('keeps last-known run metrics and shows a transient note on a poll error', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'busy-1', name: 'Busy', status: 'in_use', session_id: '42' })]),
    );
    platformApi.fetchChamberProgress.mockRejectedValue(
      Object.assign(new Error('blip'), { status: undefined }),
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    queryClient.setQueryData(['chambers', 'progress', 'busy-1'], {
      chamber_id: 'busy-1',
      progress: { is_running: true, completed: 6, total: 10, ratio: 0.6 },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/chambers']}>
          <ChambersRoute />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('chambers-run-transient')).toBeInTheDocument());
    expect(screen.getByTestId('chambers-run-completed')).toHaveTextContent('6 / 10');
    expect(screen.getByTestId('chambers-run-ratio')).toHaveTextContent('60%');
  });
});

describe('isKnownChamberStatus (P10 status fallback SSOT)', () => {
  it('is true only for the canonical chamber statuses', () => {
    expect(isKnownChamberStatus('idle')).toBe(true);
    expect(isKnownChamberStatus('in_use')).toBe(true);
    expect(isKnownChamberStatus('offline')).toBe(true);
    // Whitespace / case variants normalize.
    expect(isKnownChamberStatus('  IDLE ')).toBe(true);
    // Forward-compat / unknown statuses are not known.
    expect(isKnownChamberStatus('degraded')).toBe(false);
    expect(isKnownChamberStatus('')).toBe(false);
  });

  it('keeps the known-set and the badge-kind map paired (no drift)', () => {
    // The known set is the canonical ChamberNodeStatus mirror.
    expect([...KNOWN_CHAMBER_STATUSES]).toEqual(['idle', 'in_use', 'offline']);
    // Both the known-set and the color map derive from one object, so every
    // known status MUST map to a non-fallback kind, and an unknown status MUST
    // get the neutral `stale` fallback — pinning the derivation against drift.
    for (const status of KNOWN_CHAMBER_STATUSES) {
      expect(chamberStatusKind(status)).not.toBe('stale');
    }
    expect(chamberStatusKind('idle')).toBe('pass');
    expect(chamberStatusKind('in_use')).toBe('running');
    expect(chamberStatusKind('offline')).toBe('missing');
    expect(chamberStatusKind('degraded')).toBe('stale');
    // A known status is exactly one with a non-fallback badge kind.
    expect(isKnownChamberStatus('degraded')).toBe(chamberStatusKind('degraded') !== 'stale');
  });
});

describe('P10 chamber status fallback', () => {
  it('renders a generic fallback label (not a raw key) for an unknown status', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'x', name: 'Xeno', status: 'degraded' })]),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-table')).toBeInTheDocument());
    const badge = screen.getByTestId('chambers-status');
    // Color degrades to the neutral `stale` kind.
    expect(badge).toHaveAttribute('data-status', 'stale');
    // The label echoes the raw status, never the un-resolved i18n key.
    expect(badge).toHaveTextContent('degraded');
    expect(badge).not.toHaveTextContent('routes.chambers.status.degraded');
  });
});

describe('P10 mutation reset', () => {
  it('clears a prior start outcome when the chamber selection changes', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'idle-1', name: 'Free A', status: 'idle' }),
        chamber({ chamber_id: 'idle-2', name: 'Free B', status: 'idle' }),
      ]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: false, completed: 0, total: 0, ratio: 0 },
    });
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));
    await waitFor(() => expect(screen.getByTestId('chambers-start-success')).toBeInTheDocument());

    // Changing the selection resets the mutation → the success banner clears.
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-2');
    await waitFor(() =>
      expect(screen.queryByTestId('chambers-start-success')).not.toBeInTheDocument(),
    );
  });
});

describe('A1 published-plan suggestions (server SSOT)', () => {
  it("offers the project's server-published plans as datalist suggestions", async () => {
    // The publications read is gated `test_plan:read` on the backend. The
    // project-scoped roles a chamber operator holds (project_viewer/
    // project_engineer/project_admin) grant only platform:read|claim|admin per
    // rbac_role_grants (central_db_schema.v1.json), never test_plan:read — so
    // the success path must hold it alongside platform:claim (RBAC parity with
    // the headless endpoint).
    authenticateAs(['platform:read', 'platform:claim', 'test_plan:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    // Plans published on ANY browser/session — sourced from the G2 read, not a
    // browser-local cache.
    headlessClient.GET.mockResolvedValue(
      pubsOk([
        {
          plan_id: 'plan-server-1',
          draft_id: 'draft-9',
          project_id: PROJECT_ID,
          status: 'published',
          row_count: 7,
          published_by: 'other@corp',
          published_at: '2026-06-16T00:00:00+00:00',
        },
      ]),
    );
    renderChambers(false);
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());

    // No suggestions until a valid project is entered (UUID-gated query).
    expect(screen.queryByTestId('chambers-start-plan-options')).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByTestId('chambers-start-project-select'), PROJECT_ID);

    const datalist = await screen.findByTestId('chambers-start-plan-options');
    expect(within(datalist).getByRole('option', { hidden: true })).toHaveValue('plan-server-1');
    // The free-text input is linked to the datalist (suggestions, not a hard select).
    expect(screen.getByTestId('chambers-start-plan')).toHaveAttribute(
      'list',
      'chambers-start-plan-options',
    );
    // The query is scoped to the entered project.
    expect(headlessClient.GET).toHaveBeenCalledWith(
      '/headless/projects/{project_id}/test-plan/publications',
      { params: { path: { project_id: PROJECT_ID } } },
    );
  });

  it('does not query (or show a datalist) until a project is selected', async () => {
    authenticateAs(['platform:read', 'platform:claim', 'test_plan:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    renderChambers(false);
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await screen.findByTestId('chambers-start-project-select');
    expect(headlessClient.GET).not.toHaveBeenCalled();
    expect(screen.queryByTestId('chambers-start-plan-options')).not.toBeInTheDocument();
    expect(screen.getByTestId('chambers-start-plan')).not.toHaveAttribute('list');
  });

  it('shows no datalist when the project has no published plans', async () => {
    authenticateAs(['platform:read', 'platform:claim', 'test_plan:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    headlessClient.GET.mockResolvedValue(pubsOk([]));
    renderChambers(false);
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-project-select'), PROJECT_ID);
    await waitFor(() => expect(headlessClient.GET).toHaveBeenCalled());
    expect(screen.queryByTestId('chambers-start-plan-options')).not.toBeInTheDocument();
    expect(screen.getByTestId('chambers-start-plan')).not.toHaveAttribute('list');
  });

  it('does not query publications without test_plan:read, but keeps manual start', async () => {
    // A platform:claim operator who lacks test_plan:read (the project-scoped
    // roles in rbac_role_grants — project_viewer/project_engineer/project_admin
    // — grant only platform:* and never test_plan:read) must NOT trigger the
    // publications read —
    // the backend would 403. Even with a valid project id the query is skipped,
    // no datalist appears, and free-text manual plan entry + start still work.
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: false, completed: 0, total: 0, ratio: 0 },
    });
    headlessClient.GET.mockResolvedValue(
      pubsOk([
        {
          plan_id: 'plan-server-1',
          draft_id: 'draft-9',
          project_id: PROJECT_ID,
          status: 'published',
          row_count: 1,
          published_at: null,
        },
      ]),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());

    // Entering a perfectly valid project id must still cost no round trip and
    // surface no datalist when the permission is absent.
    await userEvent.selectOptions(screen.getByTestId('chambers-start-project-select'), PROJECT_ID);
    expect(headlessClient.GET).not.toHaveBeenCalled();
    expect(screen.queryByTestId('chambers-start-plan-options')).not.toBeInTheDocument();
    expect(screen.getByTestId('chambers-start-plan')).not.toHaveAttribute('list');

    // The platform:claim start flow is untouched — manual plan entry forwards.
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.type(screen.getByTestId('chambers-start-plan'), 'plan-manual');
    await userEvent.selectOptions(screen.getByTestId('chambers-start-sample'), 'sample-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));
    await waitFor(() =>
      expect(platformApi.startChamberMeasurement).toHaveBeenCalledWith('idle-1', {
        published_plan_id: 'plan-manual',
        project_id: PROJECT_ID,
        sample_id: 'sample-1',
      }),
    );
  });
});

describe('P10 transient progress polling', () => {
  it('shows a hard error when the initial progress fetch fails with no data', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 0, total: 10, ratio: 0 },
    });
    // The very first progress poll fails — no last-known data to fall back on.
    platformApi.fetchChamberProgress.mockRejectedValue(
      Object.assign(new Error('progress unreachable'), { status: undefined }),
    );
    renderChambers();
    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));

    await waitFor(() => expect(screen.getByTestId('chambers-progress-error')).toBeInTheDocument());
    // No transient note + no metric strip when there is no data at all.
    expect(screen.queryByTestId('chambers-progress-transient')).not.toBeInTheDocument();
  });

  it('keeps the last-known metrics and shows a transient note on a mid-run poll error', async () => {
    authenticateAs(['platform:read', 'platform:claim']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'idle-1', name: 'Free', status: 'idle' })]),
    );
    platformApi.startChamberMeasurement.mockResolvedValue({
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 3, total: 10, ratio: 0.3 },
    });
    // Every progress fetch fails — but the cache already holds the last-known
    // running snapshot, so the on-mount refetch errors WITH retained data
    // (the transient branch), deterministically and without poll-timer timing.
    platformApi.fetchChamberProgress.mockRejectedValue(
      Object.assign(new Error('blip'), { status: undefined }),
    );

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    queryClient.setQueryData(['chambers', 'progress', 'idle-1'], {
      chamber_id: 'idle-1',
      progress: { is_running: true, completed: 3, total: 10, ratio: 0.3 },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/chambers?project=${PROJECT_ID}&sample=sample-1`]}>
          <ChambersRoute />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('chambers-start-form')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('chambers-start-select'), 'idle-1');
    await userEvent.click(screen.getByTestId('chambers-start-submit'));

    // The transient note appears (poll errored) while the seeded metrics persist.
    await waitFor(() =>
      expect(screen.getByTestId('chambers-progress-transient')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('chambers-progress-running')).toBeInTheDocument();
    // It is NOT escalated to the destructive hard-error state (data is retained).
    expect(screen.queryByTestId('chambers-progress-error')).not.toBeInTheDocument();
  });
});

/**
 * M4 (fe-w2-b-execution-freshness, 2026-07-28) — availability freshness.
 *
 * The defect: `heartbeatAge` divided by the *fetch-time* `server_time` snapshot,
 * so the cell froze on the value it had when the page loaded ("12s" forever)
 * while the chamber went quiet. Combined with `REFETCH_STRATEGIES.IMPORTANT`
 * (`refetchInterval: false`) the whole availability table was a still photo
 * presented as a live view — the single most dangerous shape for a screen an
 * operator leaves open on a wall monitor.
 *
 * These cases drive the real component under fake timers, which is the only way
 * to tell "the age is recomputed" from "the age happens to be right once".
 */
describe('M4 — chamber availability freshness', () => {
  /** The anchor instant used by `list()`; fixing the clock here makes the age exact. */
  const SERVER_NOW_MS = Date.parse('2026-06-16T00:00:00+00:00');

  afterEach(() => {
    vi.useRealTimers();
  });

  /** Settle the mount fetch without leaving fake timers. */
  async function settle(): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  it('the heartbeat age advances with the wall clock between fetches (D4)', async () => {
    vi.useFakeTimers({ now: SERVER_NOW_MS });
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'a', last_heartbeat_at: '2026-06-15T23:59:30+00:00' })]),
    );

    renderChambers();
    await settle();

    const cell = (): HTMLElement => screen.getByTestId('chambers-admin-heartbeat-age');
    // At the observation instant the age is the server-side gap, exactly.
    expect(cell()).toHaveTextContent('30초');

    // One tick of wall clock — no refetch involved (the poll cadence is far
    // longer), so a frozen implementation still reads "30초" here.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEARTBEAT_AGE_TICK_INTERVAL_MS);
    });
    expect(cell()).toHaveTextContent('31초');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEARTBEAT_AGE_TICK_INTERVAL_MS * 4);
    });
    expect(cell()).toHaveTextContent('35초');
  });

  it('shows the flowing age to a read-only operator, not just to admins', async () => {
    // Freshness is a read-side fact. Gating it behind `platform:admin` (where
    // the only age cell used to live) means the people watching the fleet are
    // exactly the people who cannot tell whether what they see is current.
    vi.useFakeTimers({ now: SERVER_NOW_MS });
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'a', last_heartbeat_at: '2026-06-15T23:59:30+00:00' })]),
    );

    renderChambers();
    await settle();

    expect(screen.queryByTestId('chambers-admin')).not.toBeInTheDocument();
    const cell = (): HTMLElement => screen.getByTestId('chambers-heartbeat-age');
    expect(cell()).toHaveTextContent('30초');

    await act(async () => {
      await vi.advanceTimersByTimeAsync(HEARTBEAT_AGE_TICK_INTERVAL_MS * 2);
    });
    expect(cell()).toHaveTextContent('32초');
  });

  it('says so, in Korean, when a chamber has never reported', async () => {
    vi.useFakeTimers({ now: SERVER_NOW_MS });
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(
      list([chamber({ chamber_id: 'a', last_heartbeat_at: null })]),
    );

    renderChambers();
    await settle();

    // The old code rendered the bare English word "unknown" here.
    expect(screen.getByTestId('chambers-heartbeat-age')).not.toHaveTextContent('unknown');
  });

  it('re-reads availability on the MONITORED cadence instead of freezing at load', async () => {
    vi.useFakeTimers({ now: SERVER_NOW_MS });
    authenticateAs(['platform:read']);
    platformApi.fetchChambers.mockResolvedValue(list([chamber({ chamber_id: 'a' })]));

    renderChambers();
    await settle();
    expect(platformApi.fetchChambers).toHaveBeenCalledTimes(1);

    const cadence = REFETCH_STRATEGIES.MONITORED.refetchInterval;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(cadence);
    });
    expect(platformApi.fetchChambers).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(cadence * 2);
    });
    expect(platformApi.fetchChambers).toHaveBeenCalledTimes(4);
  });
});

describe('W2-C M1 — the admin panel does not eat in-progress edits', () => {
  /**
   * The regression this suite seals is not hypothetical timing: W2-B raised the
   * chambers query to the MONITORED tier, and `last_heartbeat_at` moves on every
   * poll, so the panel's old `useEffect([chambers]) → setDrafts(...)` re-seeded
   * from the server on EVERY cadence tick. An operator typing a base URL lost it
   * roughly every 45 seconds, with no indication anything had happened.
   *
   * The tests drive the real cadence rather than calling a refetch helper, so a
   * future change that reintroduces a sync effect fails here for the same reason
   * an operator would notice it.
   */
  const SERVER_NOW_MS = Date.parse('2026-06-16T00:00:00+00:00');
  const CADENCE = REFETCH_STRATEGIES.MONITORED.refetchInterval;

  afterEach(() => {
    vi.useRealTimers();
  });

  async function settle(): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
  }

  async function tick(times = 1): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CADENCE * times);
    });
  }

  /** Nth admin-row control, narrowed — index access alone is `T | undefined`
   *  under the strict build config, and a silent `undefined` here would turn a
   *  missing row into a confusing downstream failure. */
  function nth(testId: string, index: number): HTMLElement {
    const found = screen.getAllByTestId(testId)[index];
    if (found === undefined) throw new Error(`no \`${testId}\` at index ${index}`);
    return found;
  }

  function nameInput(index: number): HTMLInputElement {
    const input = nth('chambers-admin-name', index);
    if (!(input instanceof HTMLInputElement)) throw new Error('name cell is not an input');
    return input;
  }

  async function mountWithTwoChambers(): Promise<void> {
    vi.useFakeTimers({ now: SERVER_NOW_MS });
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'a', name: 'Alpha' }),
        chamber({ chamber_id: 'b', name: 'Bravo' }),
      ]),
    );
    renderChambers();
    await settle();
  }

  it('keeps the edited field and still follows the server on untouched rows (S1 + S2)', async () => {
    await mountWithTwoChambers();
    expect(screen.getByTestId('chambers-admin')).toBeInTheDocument();

    fireEvent.change(nameInput(0), { target: { value: 'Alpha Lab' } });

    // The server moves under the operator: BOTH chambers are renamed centrally.
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'a', name: 'Alpha Central' }),
        chamber({ chamber_id: 'b', name: 'Bravo Central' }),
      ]),
    );
    await tick(2);

    // S1 — the edited row keeps the operator's text across refetches.
    expect(nameInput(0).value).toBe('Alpha Lab');
    // S2 — the untouched row is NOT frozen; freshness is preserved for it.
    expect(nameInput(1).value).toBe('Bravo Central');
    // The overridden server value is surfaced rather than silently discarded.
    expect(screen.getByTestId('chambers-admin-unsaved')).toBeInTheDocument();
    expect(screen.getByTestId('chambers-admin-server-changed')).toBeInTheDocument();
  });

  it('marks an edit unsaved without claiming the server moved when it did not', async () => {
    await mountWithTwoChambers();
    fireEvent.change(nameInput(0), { target: { value: 'Alpha Lab' } });
    await tick();

    expect(screen.getByTestId('chambers-admin-unsaved')).toBeInTheDocument();
    // Nothing changed centrally — announcing a conflict here would be noise the
    // operator learns to ignore, which is how a real conflict gets missed.
    expect(screen.queryByTestId('chambers-admin-server-changed')).not.toBeInTheDocument();
  });

  it('lets the row follow the server again after the edit is saved (S3)', async () => {
    await mountWithTwoChambers();
    fireEvent.change(nameInput(0), { target: { value: 'Alpha Lab' } });

    platformApi.registerChamber.mockResolvedValue({ chamber_id: 'a' });
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'a', name: 'Alpha Saved' }),
        chamber({ chamber_id: 'b', name: 'Bravo' }),
      ]),
    );
    fireEvent.click(nth('chambers-admin-save', 0));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    await tick();

    // The override is released on success — otherwise "protect the edit" would
    // become "freeze this row forever".
    expect(nameInput(0).value).toBe('Alpha Saved');
    expect(screen.queryByTestId('chambers-admin-unsaved')).not.toBeInTheDocument();
    expect(screen.queryByTestId('chambers-admin-discard')).not.toBeInTheDocument();
  });

  it('offers an explicit way back to the server value', async () => {
    await mountWithTwoChambers();
    fireEvent.change(nameInput(0), { target: { value: 'Alpha Lab' } });
    platformApi.fetchChambers.mockResolvedValue(
      list([
        chamber({ chamber_id: 'a', name: 'Alpha Central' }),
        chamber({ chamber_id: 'b', name: 'Bravo' }),
      ]),
    );
    await tick();

    fireEvent.click(screen.getByTestId('chambers-admin-discard'));
    await settle();

    expect(nameInput(0).value).toBe('Alpha Central');
    expect(screen.queryByTestId('chambers-admin-server-changed')).not.toBeInTheDocument();
  });
});
