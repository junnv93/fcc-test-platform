import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { OverviewRoute } from '@/routes/overview';

import type { ReactElement } from 'react';

/**
 * Operator home ('/') tests (tester-ux Phase H — "오늘 할 일").
 *
 * Covers: the six-step G1 onboarding guide, the G2 in_use-measurements section
 * (from the existing chambers.list query), the empty state, and the
 * proxy-completion guard — G3 cards (최근 작업 / 내 실패 / 보고서 준비됨) must NOT
 * render without a backend read contract.
 */

const platformApi = vi.hoisted(() => ({
  fetchChambers: vi.fn(),
  registerChamber: vi.fn(),
  startChamberMeasurement: vi.fn(),
  fetchChamberProgress: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

function chamber(over: Record<string, unknown>): Record<string, unknown> {
  return {
    chamber_id: 'c1',
    name: 'Chamber A',
    status: 'idle',
    enabled: true,
    last_heartbeat_at: null,
    session_id: null,
    unavailable_reason: null,
    ...over,
  };
}

function renderHome(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <OverviewRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(tree);
}

beforeEach(() => {
  platformApi.fetchChambers.mockResolvedValue({ items: [] });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('OverviewRoute (home)', () => {
  it('renders the six-step onboarding guide as nav links', async () => {
    renderHome();
    expect(screen.getByTestId('home-workbench')).toBeInTheDocument();
    expect(screen.getByTestId('home-onboarding-panel')).toBeInTheDocument();
    const steps = await screen.findByTestId('home-steps');
    expect(steps).toBeInTheDocument();
    for (const key of ['testPlans', 'chambers', 'measure', 'progress', 'results', 'reports']) {
      expect(screen.getByTestId(`home-step-${key}`)).toBeInTheDocument();
    }
    // Each step is a real navigation link (G1 — no data call).
    expect(screen.getByTestId('home-step-testPlans')).toHaveAttribute('href', '/test-plans');
    expect(screen.getByTestId('home-step-reports')).toHaveAttribute('href', '/reports');
    // Phase 6 wiring: the 진척 onboarding step reaches the progress dashboard.
    expect(screen.getByTestId('home-step-progress')).toHaveAttribute('href', '/progress');
  });

  it('shows a first-action CTA (G1/G2 only — no localStorage / no G3 aggregate)', async () => {
    renderHome();
    const cta = await screen.findByTestId('home-cta');
    expect(cta).toBeInTheDocument();
    expect(screen.getByTestId('home-cta-test-plans')).toHaveAttribute('href', '/test-plans');
    expect(screen.getByTestId('home-cta-chambers')).toHaveAttribute('href', '/chambers');
    // Phase 6 wiring: the 진행률 CTA now points at the progress dashboard
    // (previously mis-pointed at /projects coverage).
    expect(screen.getByTestId('home-cta-progress')).toHaveAttribute('href', '/progress');
    // The CTA is link-only — it must NOT resurrect a G3 recent/aggregate card.
    expect(screen.queryByTestId('home-recent')).not.toBeInTheDocument();
  });

  it('shows in-progress measurements from the chambers query (G2)', async () => {
    platformApi.fetchChambers.mockResolvedValue({
      items: [chamber({ chamber_id: 'c-busy', name: 'Chamber B', status: 'in_use' }), chamber({})],
    });
    renderHome();

    const list = await screen.findByTestId('home-inprogress');
    expect(list).toHaveTextContent('Chamber B');
    // Only the in_use chamber appears (the idle one is filtered out).
    expect(list).not.toHaveTextContent('Chamber A');
  });

  it('shows an empty state when nothing is in progress', async () => {
    platformApi.fetchChambers.mockResolvedValue({ items: [chamber({ status: 'idle' })] });
    renderHome();
    expect(await screen.findByTestId('home-inprogress-empty')).toBeInTheDocument();
  });

  it('does NOT render G3 cards (no proxy completion without a contract)', async () => {
    renderHome();
    await screen.findByTestId('home-steps');
    // 최근 작업 / 내 실패 / 보고서 준비됨 require backend read contracts (§3/§B) —
    // they must never be faked on the home.
    expect(screen.queryByTestId('home-recent')).not.toBeInTheDocument();
    expect(screen.queryByTestId('home-failed')).not.toBeInTheDocument();
    expect(screen.queryByTestId('home-report-ready')).not.toBeInTheDocument();
  });

  it('does not crash when the chambers query rejects (shows the error surface)', async () => {
    platformApi.fetchChambers.mockRejectedValue(
      Object.assign(new Error('forbidden'), { status: 403 }),
    );
    renderHome();
    await waitFor(() => expect(screen.getByTestId('home-progress-error')).toBeInTheDocument());
    // The onboarding guide still renders regardless of the data call.
    expect(screen.getByTestId('home-steps')).toBeInTheDocument();
  });
});
