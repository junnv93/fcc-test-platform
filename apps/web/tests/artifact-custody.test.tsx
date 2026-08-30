import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ArtifactCustodyRoute from '@/routes/artifact-custody';

import type { ReactElement } from 'react';

const platformApi = vi.hoisted(() => ({
  fetchProjectArtifactCustody: vi.fn(),
  fetchArtifactCustodySnapshot: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

function custody(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    status: 'unknown',
    counts: { verified: 4, missing: 0, diverged: 0, unknown: 0 },
    session_count: 0,
    blocking_session_count: 0,
    unresolved_session_count: 0,
    missing_snapshot_session_count: 1,
    oldest_observed_at: null,
    newest_observed_at: null,
    sessions: [],
    ...over,
  };
}

function renderRoute(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/artifact-custody?project=${PROJECT_ID}`]}>
        <ArtifactCustodyRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(tree);
}

beforeEach(() => {
  platformApi.fetchProjectArtifactCustody.mockReset();
  platformApi.fetchArtifactCustodySnapshot.mockReset();
});

describe('artifact custody route missing-snapshot semantics', () => {
  it('renders the server missing count and UNKNOWN status even with no snapshot rows', async () => {
    platformApi.fetchProjectArtifactCustody.mockResolvedValue(custody());

    renderRoute();

    expect(await screen.findByTestId('artifact-custody-project-status')).toHaveTextContent(
      '판정 불가',
    );
    expect(screen.getByTestId('artifact-custody-missing-snapshot-count')).toHaveTextContent('1');
    expect(screen.getByTestId('artifact-custody-missing-snapshot-hint')).toHaveTextContent(
      '보관 판정 보고 자체가 없습니다',
    );
    expect(screen.queryByTestId('artifact-custody-empty')).not.toBeInTheDocument();
  });

  it('does not recompute the project status from session rows', async () => {
    platformApi.fetchProjectArtifactCustody.mockResolvedValue(
      custody({
        status: 'unknown',
        session_count: 1,
        missing_snapshot_session_count: 1,
        sessions: [
          {
            snapshot_id: 'snapshot-1',
            provider_session_id: 'provider-session-1',
            chamber_id: 'chamber-a',
            status: 'verified',
            counts: { verified: 4, missing: 0, diverged: 0, unknown: 0 },
            observed_at: '2026-08-11T00:00:00Z',
            is_blocking: false,
            roots: [],
          },
        ],
      }),
    );

    renderRoute();

    expect(await screen.findByTestId('artifact-custody-project-status')).toHaveTextContent(
      '판정 불가',
    );
    expect(screen.getByTestId('artifact-custody-missing-snapshot-count')).toHaveTextContent('1');
  });
});
