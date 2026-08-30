import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ProgressRoute } from '@/routes/progress';
import {
  formatBucketLabel,
  formatHours,
  groupByArea,
  overallTotals,
  percentTone,
} from '@/shared/project-progress';

import type { ProgressBucketEnvelope } from '@/api/platform-client';

/**
 * Phase 6 (2026-06-23) — 시간가중 진행률 대시보드.
 * (1) 순수 헬퍼: 백엔드 정직 규칙(가짜 0% 금지 = percent null, unpriced/unbucketable = 수)을
 *     프론트 합산/표기 헬퍼가 그대로 보존하는지 봉인.
 * (2) 라우트 렌더: no-project / fetch 호출 / percent null → 배지(바 아님) / unpriced warning.
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
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <ProgressRoute />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('formatHours', () => {
  it('converts minutes to one-decimal hours', () => {
    expect(formatHours(90)).toBe('1.5');
    expect(formatHours(0)).toBe('0.0');
    expect(formatHours(30)).toBe('0.5');
  });
});

describe('percentTone', () => {
  it('maps completion to a tone', () => {
    expect(percentTone(100)).toBe('pass');
    expect(percentTone(150)).toBe('pass');
    expect(percentTone(42)).toBe('running');
    expect(percentTone(0)).toBe('accent');
  });
});

describe('formatBucketLabel', () => {
  it('formats base buckets', () => {
    expect(formatBucketLabel('bt', '미분류')).toBe('BT');
    expect(formatBucketLabel('ble', '미분류')).toBe('BLE');
    expect(formatBucketLabel('dts', '미분류')).toBe('DTS');
  });
  it('formats UNII + sub-band', () => {
    expect(formatBucketLabel('unii_1', '미분류')).toBe('UNII 1');
    expect(formatBucketLabel('unii_2a', '미분류')).toBe('UNII 2A');
    expect(formatBucketLabel('unii_8', '미분류')).toBe('UNII 8');
  });
  it('formats 11ax next-gen variants', () => {
    expect(formatBucketLabel('dts_11ax', '미분류')).toBe('DTS (11ax)');
    expect(formatBucketLabel('unii_5_11ax', '미분류')).toBe('UNII 5 (11ax)');
  });
  it('uses the unbucketed label for null', () => {
    expect(formatBucketLabel(null, '미분류')).toBe('미분류');
  });
});

describe('groupByArea', () => {
  it('groups consecutive rows by area, preserving backend order', () => {
    const rows = [
      bucket({ progress_area: 'unlicensed_conducted', progress_bucket_id: 'bt' }),
      bucket({ progress_area: 'unlicensed_conducted', progress_bucket_id: 'ble' }),
      bucket({ progress_area: 'unlicensed_radiated', progress_bucket_id: 'bt' }),
    ];
    const groups = groupByArea(rows);
    expect(groups.map((g) => g.area)).toEqual(['unlicensed_conducted', 'unlicensed_radiated']);
    expect(groups[0]?.buckets).toHaveLength(2);
    expect(groups[1]?.buckets).toHaveLength(1);
  });

  it('returns [] for no rows', () => {
    expect(groupByArea([])).toEqual([]);
  });
});

describe('overallTotals', () => {
  it('sums priced minutes and computes percent', () => {
    const rows = [
      bucket({ planned_minutes: 12, completed_minutes: 3, percent: 25 }),
      bucket({ planned_minutes: 8, completed_minutes: 8, percent: 100 }),
    ];
    const totals = overallTotals(rows);
    expect(totals.plannedMinutes).toBe(20);
    expect(totals.completedMinutes).toBe(11);
    expect(totals.percent).toBe(55);
  });

  it('returns null percent when there is no priced time (no fake 0%)', () => {
    const rows = [
      bucket({ planned_minutes: 0, completed_minutes: 0, percent: null, unpriced_conditions: 3 }),
    ];
    const totals = overallTotals(rows);
    expect(totals.percent).toBeNull();
    expect(totals.unpricedConditions).toBe(3);
  });

  it('sums unpriced / unbucketable counts', () => {
    const rows = [
      bucket({ unpriced_conditions: 2, unbucketable_conditions: 1 }),
      bucket({ unpriced_conditions: 1, unbucketable_conditions: 0 }),
    ];
    const totals = overallTotals(rows);
    expect(totals.unpricedConditions).toBe(3);
    expect(totals.unbucketableConditions).toBe(1);
  });
});

describe('ProgressRoute render', () => {
  beforeEach(() => {
    platformApi.fetchProjectProgress.mockReset();
  });

  it('shows the no-project empty state and does not fetch without a project id', () => {
    renderRoute('/progress');
    expect(screen.getByTestId('progress-no-project')).toBeTruthy();
    expect(platformApi.fetchProjectProgress).not.toHaveBeenCalled();
  });

  it('fetches progress with the project id from the query string', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([]);
    renderRoute(`/progress?project=${PROJECT_ID}`);
    await waitFor(() => expect(platformApi.fetchProjectProgress).toHaveBeenCalledWith(PROJECT_ID));
  });

  it('renders a 시간 미설정 badge (not a progress bar) when percent is null', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({ progress_bucket_id: 'dts_11ax', percent: null }),
    ]);
    renderRoute(`/progress?project=${PROJECT_ID}`);
    expect(screen.getByTestId('progress-workbench-overview')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('progress-bucket')).toBeTruthy());
    expect(screen.getByTestId('progress-workbench')).toBeInTheDocument();
    expect(screen.getByTestId('progress-project-context')).toHaveTextContent(PROJECT_ID);
    expect(screen.getByTestId('progress-next-coverage')).toHaveAttribute(
      'href',
      `/projects?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('progress-next-test-plans')).toHaveAttribute(
      'href',
      `/test-plans?project=${PROJECT_ID}`,
    );
    expect(screen.getByTestId('progress-next-chambers')).toHaveAttribute(
      'href',
      `/chambers?project=${PROJECT_ID}`,
    );
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(screen.getAllByText('시간 미설정').length).toBeGreaterThan(0);
  });

  it('shows the quality warning when there are unpriced conditions', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({
        progress_bucket_id: 'unii_1',
        percent: 50,
        planned_minutes: 10,
        completed_minutes: 5,
        unpriced_conditions: 2,
      }),
    ]);
    renderRoute(`/progress?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getByTestId('progress-quality-warning')).toBeTruthy());
  });

  it('filters to a single area (with a clear affordance) when ?area= is present', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({
        progress_area: 'unlicensed_conducted',
        progress_bucket_id: 'bt',
        percent: 50,
        planned_minutes: 10,
        completed_minutes: 5,
      }),
      bucket({
        progress_area: 'unlicensed_radiated',
        progress_bucket_id: 'bt',
        percent: 100,
        planned_minutes: 4,
        completed_minutes: 4,
      }),
    ]);
    renderRoute(`/progress?project=${PROJECT_ID}&area=unlicensed_conducted`);
    await waitFor(() => expect(screen.getAllByTestId('progress-area').length).toBeGreaterThan(0));
    // Only the selected area's section renders (the other area is filtered out).
    const areas = screen.getAllByTestId('progress-area');
    expect(areas).toHaveLength(1);
    expect(areas[0]).toHaveAttribute('data-area', 'unlicensed_conducted');
    // The area filter is reversible — a "전체 보기" clear link drops ?area=.
    expect(screen.getByTestId('progress-area-filter')).toBeInTheDocument();
    expect(screen.getByTestId('progress-area-filter-clear')).toHaveAttribute(
      'href',
      `/progress?project=${PROJECT_ID}`,
    );
  });

  it('ignores ?area= that matches no data (honest empty state, not a blank filter)', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({
        progress_area: 'unlicensed_conducted',
        progress_bucket_id: 'bt',
        percent: 50,
        planned_minutes: 10,
        completed_minutes: 5,
      }),
    ]);
    renderRoute(`/progress?project=${PROJECT_ID}&area=mmwave`);
    await waitFor(() => expect(screen.getByTestId('progress-empty')).toBeInTheDocument());
    // The filter banner is still shown so the user can clear the empty scope.
    expect(screen.getByTestId('progress-area-filter')).toBeInTheDocument();
  });

  it('shows all areas (no filter banner) when ?area= is absent — byte-identical', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({
        progress_area: 'unlicensed_conducted',
        progress_bucket_id: 'bt',
        percent: 50,
        planned_minutes: 10,
        completed_minutes: 5,
      }),
      bucket({
        progress_area: 'unlicensed_radiated',
        progress_bucket_id: 'bt',
        percent: 100,
        planned_minutes: 4,
        completed_minutes: 4,
      }),
    ]);
    renderRoute(`/progress?project=${PROJECT_ID}`);
    await waitFor(() => expect(screen.getAllByTestId('progress-area').length).toBe(2));
    expect(screen.queryByTestId('progress-area-filter')).not.toBeInTheDocument();
  });
});
