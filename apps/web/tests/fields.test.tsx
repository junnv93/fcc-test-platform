import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { FieldsRoute } from '@/routes/fields';
import { findWorkbenchArea, isAvailableArea, WORKBENCH_AREAS } from '@/shared/workbench-areas';

import type { ProgressBucketEnvelope } from '@/api/platform-client';
import type { ReactElement } from 'react';

/**
 * ADR-0017 Phase 2 (2026-06-22) — 분야(시험 분야) 선택 화면 + workbench-area SSOT.
 * Phase 6 wiring (2026-07-03) — available 분야 카드에 진행률 배지 + progress deep-link.
 */

const platformApi = vi.hoisted(() => ({ fetchProjectProgress: vi.fn() }));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

function bucket(over: Partial<ProgressBucketEnvelope>): ProgressBucketEnvelope {
  return {
    progress_area: 'unlicensed_conducted',
    progress_bucket_id: 'unii_1',
    planned_minutes: 0,
    completed_minutes: 0,
    percent: null,
    total_conditions: 0,
    priced_conditions: 0,
    unpriced_conditions: 0,
    unbucketable_conditions: 0,
    ...over,
  };
}

function renderRoute(entry: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <FieldsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  platformApi.fetchProjectProgress.mockReset();
  platformApi.fetchProjectProgress.mockResolvedValue([]);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('workbench areas SSOT', () => {
  it('defines exactly the four product-defined fields', () => {
    expect(WORKBENCH_AREAS.map((a) => a.id)).toEqual([
      'unlicensed_conducted',
      'unlicensed_radiated',
      'mmwave',
      'licensed_conducted',
    ]);
  });

  it('marks only the deployed provider area available', () => {
    expect(isAvailableArea('unlicensed_conducted')).toBe(true);
    expect(isAvailableArea('mmwave')).toBe(false);
    expect(findWorkbenchArea('nope')).toBeUndefined();
  });
});

describe('FieldsRoute', () => {
  it('prompts to pick a project when no valid project is in context', () => {
    renderRoute('/fields');
    expect(screen.getByTestId('fields-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('fields-no-project')).toBeInTheDocument();
    expect(screen.getByTestId('fields-pick-project')).toHaveAttribute('href', '/my-projects');
    // No project → the progress endpoint is not called (project-scoped).
    expect(platformApi.fetchProjectProgress).not.toHaveBeenCalled();
  });

  it('renders all four field cards under a project context', () => {
    renderRoute(`/fields?project=${PROJECT_ID}`);
    expect(screen.getByTestId('fields-workbench')).toBeInTheDocument();
    expect(screen.getByTestId('fields-context-panel')).toBeInTheDocument();
    expect(screen.getByTestId('fields-project-context')).toHaveTextContent(PROJECT_ID);
    expect(screen.getByTestId('fields-available-count')).toHaveTextContent('1');
    expect(screen.getByTestId('fields-planned-count')).toHaveTextContent('3');
    expect(screen.getAllByTestId('field-card')).toHaveLength(4);
  });

  it('links the available field into the PROGRESS dashboard carrying ?project=&area=', () => {
    renderRoute(`/fields?project=${PROJECT_ID}`);
    const link = screen.getByTestId('field-card-link');
    expect(link).toHaveAttribute(
      'href',
      `/progress?project=${PROJECT_ID}&area=unlicensed_conducted`,
    );
    // Coverage stays reachable as a SECONDARY action (not the primary link).
    expect(screen.getByTestId('field-card-coverage')).toHaveAttribute(
      'href',
      `/projects?project=${PROJECT_ID}&area=unlicensed_conducted`,
    );
  });

  it('disables planned fields instead of linking them (no progress badge on planned)', async () => {
    renderRoute(`/fields?project=${PROJECT_ID}`);
    // exactly one available (link) + three planned (disabled).
    expect(screen.getAllByTestId('field-card-link')).toHaveLength(1);
    expect(screen.getAllByTestId('field-card-disabled')).toHaveLength(3);
    // Once progress resolves, the available card carries the progress badge and
    // the three planned cards keep the "준비 중" availability badge — a progress
    // badge is NEVER placed on a planned (un-deployed) field.
    await screen.findByTestId('field-progress');
    expect(screen.getAllByTestId('field-status')).toHaveLength(3);
    expect(screen.getAllByTestId('field-progress')).toHaveLength(1);
  });

  it('fetches project progress once when a project is in context', async () => {
    renderRoute(`/fields?project=${PROJECT_ID}`);
    await waitFor(() => expect(platformApi.fetchProjectProgress).toHaveBeenCalledWith(PROJECT_ID));
    // Single project fetch — no N+1 (one call regardless of area count).
    expect(platformApi.fetchProjectProgress).toHaveBeenCalledTimes(1);
  });

  it('shows a percent badge on the available field when priced time exists', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({
        progress_area: 'unlicensed_conducted',
        planned_minutes: 10,
        completed_minutes: 5,
        percent: 50,
      }),
    ]);
    renderRoute(`/fields?project=${PROJECT_ID}`);
    const badge = await screen.findByTestId('field-progress');
    expect(badge).toHaveTextContent('50%');
  });

  it('shows 시간 미설정 (not a 0% badge) when the area has no priced time', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({
        progress_area: 'unlicensed_conducted',
        planned_minutes: 0,
        percent: null,
        unpriced_conditions: 3,
      }),
    ]);
    renderRoute(`/fields?project=${PROJECT_ID}`);
    const badge = await screen.findByTestId('field-progress');
    expect(badge).toHaveTextContent('시간 미설정');
    expect(badge).not.toHaveTextContent('0%');
  });

  it('shows 시간 미설정 when the available area is absent from the progress data', async () => {
    // Data exists for another area only — the available card must not fabricate 0%.
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({
        progress_area: 'unlicensed_radiated',
        planned_minutes: 10,
        completed_minutes: 10,
        percent: 100,
      }),
    ]);
    renderRoute(`/fields?project=${PROJECT_ID}`);
    const badge = await screen.findByTestId('field-progress');
    expect(badge).toHaveTextContent('시간 미설정');
  });
});
