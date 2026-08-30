import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setLocale } from '@/i18n';
import { ProjectResultSelection } from '@/routes/projects/ProjectResultSelection';

import type { ReactElement } from 'react';

const platformApi = vi.hoisted(() => ({
  fetchResultSelections: vi.fn(),
  fetchResultAttempts: vi.fn(),
  selectResult: vi.fn(),
  clearResultSelection: vi.fn(),
}));

vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const PROVIDER_ID = 'fcc-unlicensed';
const providers = [{ provider_id: PROVIDER_ID, display_name: 'FCC Unlicensed', ui_version: 1 }];

function row(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    provider_id: PROVIDER_ID,
    condition_hash: 'condition-a',
    session_id: 'central-session-a',
    provider_session_id: 'provider-session-a',
    sample_id: 'sample-a',
    chamber_id: 'chamber-a',
    operator: 'examiner-a',
    measured_at: '2026-08-25T00:00:00Z',
    created_at: '2026-08-25T00:00:00Z',
    verdict: 'Pass',
    status: 'completed',
    attempt_number: 2,
    result_json: { opaque: true },
    provenance_json: { source: 'central' },
    selection_source: 'latest',
    selected_attempt_id: null,
    selection_revision: 1,
    ...over,
  };
}

function attempt(attemptId: string, number: number): Record<string, unknown> {
  return {
    attempt_id: attemptId,
    project_id: PROJECT_ID,
    provider_id: PROVIDER_ID,
    condition_hash: 'condition-a',
    session_id: `session-${attemptId}`,
    provider_session_id: `provider-${attemptId}`,
    sample_id: 'sample-a',
    chamber_id: 'chamber-a',
    operator: 'examiner-a',
    measured_at: '2026-08-25T00:00:00Z',
    created_at: '2026-08-25T00:00:00Z',
    verdict: 'Pass',
    status: 'completed',
    attempt_number: number,
    result_json: { opaque: true },
    provenance_json: { source: 'central' },
  };
}

function page(items: readonly Record<string, unknown>[], nextCursor: string | null = null) {
  return { items, nextCursor };
}

function renderSelection(): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <ProjectResultSelection projectId={PROJECT_ID} providers={providers} />
    </QueryClientProvider>
  );
  render(ui);
}

function routeSingleCondition(initialRow = row()): void {
  platformApi.fetchResultSelections.mockResolvedValue(page([initialRow]));
  platformApi.fetchResultAttempts.mockResolvedValue(page([attempt('attempt-a', 2)]));
}

beforeEach(() => {
  setLocale('ko');
  platformApi.fetchResultSelections.mockReset();
  platformApi.fetchResultAttempts.mockReset();
  platformApi.selectResult.mockReset();
  platformApi.clearResultSelection.mockReset();
  platformApi.selectResult.mockResolvedValue({ revision: 2 });
  platformApi.clearResultSelection.mockResolvedValue({ revision: 2 });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ProjectResultSelection', () => {
  it('uses the shared skeleton while providers are loading', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <ProjectResultSelection projectId={PROJECT_ID} providers={[]} providersLoading />
      </QueryClientProvider>,
    );

    const loading = screen.getByTestId('project-results-provider-loading');
    expect(loading).toHaveAttribute('role', 'status');
    expect(loading).toHaveAttribute('aria-busy', 'true');
    expect(loading).toHaveAttribute('aria-live', 'polite');
    expect(loading.querySelectorAll('[data-testid="block-skeleton-line"]')).toHaveLength(1);
    expect(screen.queryByTestId('project-results-no-providers')).not.toBeInTheDocument();
  });

  it('shows the localized empty state instead of an empty list when nothing is completed', async () => {
    platformApi.fetchResultSelections.mockResolvedValue(page([]));
    platformApi.fetchResultAttempts.mockResolvedValue(page([]));

    renderSelection();

    const empty = await screen.findByTestId('project-results-empty');
    expect(empty).toHaveTextContent('완료된 결과가 없습니다');
    // An empty list element would be an accessible dead end — the empty state
    // replaces the list rather than rendering an unlabelled zero-row one.
    expect(screen.queryByRole('list', { name: '프로젝트 유효 결과' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('project-results-error')).not.toBeInTheDocument();
  });

  it('surfaces a fetch failure as an error state and never as an empty result set', async () => {
    platformApi.fetchResultSelections.mockRejectedValue(
      Object.assign(new Error('platform unavailable'), { status: 503 }),
    );
    platformApi.fetchResultAttempts.mockResolvedValue(page([]));

    renderSelection();

    const error = await screen.findByTestId('project-results-error');
    expect(error).toHaveTextContent('프로젝트 결과를 불러오지 못했습니다.');
    // The distinction that matters to an examiner: "the query failed" must not
    // be rendered with the same words as "this project has no results yet".
    expect(screen.queryByTestId('project-results-empty')).not.toBeInTheDocument();
  });

  it('names its landmark, its list, and each row disclosure for assistive technology', async () => {
    routeSingleCondition();

    renderSelection();

    const list = await screen.findByRole('list', { name: '프로젝트 유효 결과' });
    expect(list).toBeInTheDocument();

    const section = screen.getByTestId('project-result-selection');
    const headingId = section.getAttribute('aria-labelledby');
    expect(headingId).toBe('project-results-heading');
    expect(document.getElementById(headingId ?? '')).not.toBeNull();

    // The row detail is a disclosure, so its trigger must say what it controls
    // and whether that region is open — otherwise the state change is invisible
    // to anyone not watching the pixels.
    const [disclosure] = screen.getAllByRole('button', { expanded: false });
    // Narrowing rather than asserting: if the row stops rendering a disclosure
    // the failure names that fact, instead of a cast quietly carrying undefined
    // into the click and reporting something unrelated.
    if (!disclosure) throw new Error('the result row rendered no disclosure trigger');
    const controls = disclosure.getAttribute('aria-controls');
    expect(controls).toBeTruthy();
    await userEvent.click(disclosure);
    await waitFor(() => expect(disclosure).toHaveAttribute('aria-expanded', 'true'));
    expect(document.getElementById(controls ?? '')).not.toBeNull();
  });

  it('renders the same states in English when the locale changes', async () => {
    setLocale('en');
    platformApi.fetchResultSelections.mockResolvedValue(page([]));
    platformApi.fetchResultAttempts.mockResolvedValue(page([]));

    renderSelection();

    // Parity is enforced repository-wide by the i18n gate; this asserts the
    // feature's own strings actually resolve through it rather than being
    // Korean literals that happen to sit in a bundle.
    expect(await screen.findByTestId('project-results-empty')).toHaveTextContent(
      'No completed results',
    );
  });

  it('accumulates and deduplicates pages, keeps the natural provider key, and selects an attempt', async () => {
    const first = row({ selection_revision: 1 });
    const duplicate = row({ selection_revision: 1 });
    const second = row({ condition_hash: 'condition-b', selection_revision: 0 });
    platformApi.fetchResultSelections.mockImplementation(
      (_projectId: string, _provider: string, cursor?: string) =>
        Promise.resolve(
          cursor === undefined ? page([first], 'selection-2') : page([duplicate, second]),
        ),
    );
    platformApi.fetchResultAttempts.mockImplementation(
      (_projectId: string, _provider: string, _condition: string, cursor?: string) =>
        Promise.resolve(
          cursor === undefined
            ? page([attempt('attempt-old', 1)], 'attempts-2')
            : page([attempt('attempt-new', 2), attempt('attempt-old', 1)]),
        ),
    );

    renderSelection();
    expect(await screen.findByTestId('project-result-row')).toBeInTheDocument();
    expect(screen.getAllByTestId('project-result-row')).toHaveLength(1);
    await userEvent.click(screen.getByTestId('project-results-next'));
    await waitFor(() => expect(screen.getAllByTestId('project-result-row')).toHaveLength(2));
    expect(platformApi.fetchResultSelections).toHaveBeenLastCalledWith(
      PROJECT_ID,
      PROVIDER_ID,
      'selection-2',
    );

    const [firstCondition] = screen.getAllByTestId('project-result-condition');
    if (!firstCondition) throw new Error('condition row did not render');
    await userEvent.click(firstCondition);
    await screen.findByTestId('project-result-attempts');
    await userEvent.click(screen.getByRole('button', { name: '시도 더 불러오기' }));
    await waitFor(() => expect(screen.getAllByTestId('project-result-select')).toHaveLength(2));
    expect(platformApi.fetchResultAttempts).toHaveBeenLastCalledWith(
      PROJECT_ID,
      PROVIDER_ID,
      'condition-a',
      'attempts-2',
    );

    await userEvent.type(screen.getByRole('textbox'), '검토 완료');
    const resultButtons = screen.getAllByTestId('project-result-select');
    const newestResultButton = resultButtons[1];
    if (!newestResultButton) throw new Error('newest attempt did not render');
    await userEvent.click(newestResultButton);
    expect(screen.getByTestId('project-result-confirmation')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('project-result-confirm'));

    await waitFor(() =>
      expect(platformApi.selectResult).toHaveBeenCalledWith(
        PROJECT_ID,
        PROVIDER_ID,
        'condition-a',
        {
          attempt_id: 'attempt-new',
          expected_revision: 1,
          reason: '검토 완료',
        },
      ),
    );
  });

  it('clears a manual selection with the current revision and localized copy', async () => {
    setLocale('en');
    routeSingleCondition(row({ selection_source: 'manual', selected_attempt_id: 'attempt-a' }));
    renderSelection();

    expect(await screen.findByTestId('project-result-row')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('project-result-clear'));
    expect(
      screen.getByText('Clear the manual pin? The latest eligible result will be used.'),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('project-result-confirm'));

    await waitFor(() =>
      expect(platformApi.clearResultSelection).toHaveBeenCalledWith(
        PROJECT_ID,
        PROVIDER_ID,
        'condition-a',
        { expected_revision: 1 },
      ),
    );
  });

  it('reconciles a stale 409 before allowing an explicit retry', async () => {
    let selectionReads = 0;
    platformApi.fetchResultSelections.mockImplementation(() => {
      selectionReads += 1;
      return Promise.resolve(page([row({ selection_revision: selectionReads === 1 ? 1 : 3 })]));
    });
    platformApi.fetchResultAttempts.mockResolvedValue(page([attempt('attempt-a', 2)]));
    platformApi.selectResult
      .mockRejectedValueOnce(Object.assign(new Error('stale'), { status: 409 }))
      .mockResolvedValueOnce({ revision: 4 });

    renderSelection();
    await screen.findByText('condition-a');
    await userEvent.click(screen.getByTestId('project-result-condition'));
    await screen.findByTestId('project-result-attempts');
    await userEvent.click(screen.getByTestId('project-result-select'));
    await userEvent.click(screen.getByTestId('project-result-confirm'));

    const retry = await screen.findByTestId('project-results-conflict-retry');
    await waitFor(() => expect(retry).not.toBeDisabled());
    await userEvent.click(retry);

    await waitFor(() => expect(platformApi.selectResult).toHaveBeenCalledTimes(2));
    expect(platformApi.selectResult).toHaveBeenLastCalledWith(
      PROJECT_ID,
      PROVIDER_ID,
      'condition-a',
      { attempt_id: 'attempt-a', expected_revision: 3 },
    );
  });
});
