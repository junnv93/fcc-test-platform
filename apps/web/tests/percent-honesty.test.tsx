import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChamberProgressBar } from '@/routes/chambers/ChamberProgressBar';
import { ProgressRoute } from '@/routes/progress';
import {
  classifyPercent,
  formatPercent,
  PERCENT_COMPLETE_THRESHOLD,
  PERCENT_UNSTARTED_THRESHOLD,
  percentDisplayRank,
} from '@/shared/percent-display';
import { RunProgress } from '@/ui/RunProgress';

import type { ProgressBucketEnvelope } from '@/api/platform-client';

/**
 * S1/S2 — percent display honesty seal (fe-w2-a-result-report-honesty M1).
 *
 * The defect this seals: both progress surfaces formatted with `Math.round` /
 * `toFixed(0)`, so `99.6` rendered `"100%"` (a run with work left announcing
 * itself finished) and `0.4` rendered `"0%"` (a started run announcing itself
 * untouched, while its own bar tone said "running"). In a regulatory test tool
 * a screen that asserts an untrue completion is the worst failure mode there is.
 *
 * S1 pins the four postconditions as *properties*, not as spot values:
 *   P1 complete rendering ⟺ `>= 100`      P2 unstarted rendering ⟺ `<= 0`
 *   P3 every interior value reads as started-and-unfinished
 *   P4 monotone — a larger input never displays as less progress
 *
 * S2 pins that both surfaces route through the SAME formatter, so the two can
 * never drift back apart into per-site rounding.
 */

const platformApi = vi.hoisted(() => ({ fetchProjectProgress: vi.fn() }));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

/** The boundary table from the contract, plus the two non-finite inputs. */
const BOUNDARY_INPUTS = [
  -1,
  0,
  0.0001,
  0.4,
  0.5,
  1,
  50,
  99,
  99.4,
  99.5,
  99.6,
  99.999,
  100,
  100.1,
  Number.NaN,
  Number.POSITIVE_INFINITY,
  Number.NEGATIVE_INFINITY,
] as const;

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

describe('percent display SSOT — boundary postconditions (S1)', () => {
  it('P1: renders complete IF AND ONLY IF the input is >= 100', () => {
    for (const value of BOUNDARY_INPUTS) {
      const isComplete = classifyPercent(value).kind === 'complete';
      expect(isComplete, `input ${String(value)} classified complete=${String(isComplete)}`).toBe(
        value >= PERCENT_COMPLETE_THRESHOLD,
      );
    }
  });

  it('P2: renders unstarted IF AND ONLY IF the input is <= 0', () => {
    for (const value of BOUNDARY_INPUTS) {
      const isUnstarted = classifyPercent(value).kind === 'unstarted';
      expect(
        isUnstarted,
        `input ${String(value)} classified unstarted=${String(isUnstarted)}`,
      ).toBe(value <= PERCENT_UNSTARTED_THRESHOLD);
    }
  });

  it('P3: every 0 < v < 100 reads as started AND unfinished', () => {
    const interior = [0.0001, 0.4, 0.5, 1, 50, 99, 99.4, 99.5, 99.6, 99.999];
    for (const value of interior) {
      const text = formatPercent(value);
      expect(text, `input ${value}`).not.toBe('100%');
      expect(text, `input ${value}`).not.toBe('0%');
      expect(text.length, `input ${value}`).toBeGreaterThan(0);
    }
  });

  it('P4: the displayed progress is monotone in the input', () => {
    const ordered = [...BOUNDARY_INPUTS].filter((v) => !Number.isNaN(v)).sort((a, b) => a - b);
    let previous = Number.NEGATIVE_INFINITY;
    for (const value of ordered) {
      const rank = percentDisplayRank(classifyPercent(value));
      expect(rank, `rank regressed at input ${String(value)}`).toBeGreaterThanOrEqual(previous);
      previous = rank;
    }
  });

  it('the two dishonest boundary crossings are specifically gone', () => {
    // The exact defect from the review: 99.6 → "100%" and 0.4 → "0%".
    expect(formatPercent(99.6)).not.toBe('100%');
    expect(formatPercent(0.4)).not.toBe('0%');
    // …while the honest ends still render as the plain boundary values.
    expect(formatPercent(100)).toBe('100%');
    expect(formatPercent(0)).toBe('0%');
    // Interior values keep their familiar rounded form — the symbolic band is
    // exactly the rounding-crosses-a-boundary set, no wider, so a value that
    // rounds honestly is NOT smudged into a symbol.
    expect(formatPercent(50)).toBe('50%');
    expect(formatPercent(42.4)).toBe('42%');
    expect(formatPercent(99.4)).toBe('99%');
    expect(formatPercent(0.6)).toBe('1%');
    // …and `Math.round` is half-up, so 99.5 must be INSIDE the band: a `>`
    // comparison here would leak a literal "100%" for a run with work left.
    expect(formatPercent(99.5)).not.toBe('100%');
    expect(formatPercent(0.5)).toBe('1%');
  });

  it('NaN is neither complete nor unstarted', () => {
    const display = classifyPercent(Number.NaN);
    expect(display.kind).toBe('unknown');
    expect(formatPercent(Number.NaN)).not.toBe('100%');
    expect(formatPercent(Number.NaN)).not.toBe('0%');
  });
});

describe('both progress surfaces share one formatter (S2)', () => {
  beforeEach(() => {
    platformApi.fetchProjectProgress.mockReset();
  });

  it('renders the identical display string for the identical percent', async () => {
    const NEAR_COMPLETE = 99.6;

    // Surface A — the progress dashboard's per-bucket bar.
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({ planned_minutes: 100, completed_minutes: 99.6, percent: NEAR_COMPLETE }),
    ]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const routeView = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/progress?project=${PROJECT_ID}`]}>
          <ProgressRoute />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const bucketRow = await screen.findByTestId('progress-bucket');
    await waitFor(() => expect(within(bucketRow).getByTestId('progress-bar')).toBeInTheDocument());
    const routeText = within(bucketRow)
      .getByTestId('progress-bar')
      .querySelector('.progress-bar__value')?.textContent;
    routeView.unmount();

    // Surface B — the running-measurement widget.
    const runView = render(
      <RunProgress label="측정 진행률" percent={NEAR_COMPLETE} step="BLE 1M" />,
    );
    const runText = screen
      .getByTestId('progress-bar')
      .querySelector('.progress-bar__value')?.textContent;

    expect(routeText).toBe(formatPercent(NEAR_COMPLETE));
    expect(runText).toBe(routeText);
    // …and neither of them claims completion.
    expect(routeText).not.toBe('100%');
    runView.unmount();
  });

  it('keeps aria-valuenow numeric while the display string is honest', () => {
    render(<RunProgress label="측정 진행률" percent={99.6} step="BLE 1M" />);
    // The accessibility VALUE axis is untouched by the honesty fix — only the
    // human-readable string changed (contract M1 note).
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '99.6');
    expect(screen.getByTestId('run-progress-percent')).not.toHaveTextContent('100%');
  });

  /**
   * S11 (D7) — W2-A fixed the display STRING and recorded this file as carried
   * debt, because `ChamberProgressBar` is a second, worse instance of the same
   * defect: it decided the completion TONE from the already-rounded value
   * (`Math.round(ratio * 100) >= 100`). So a 99.6% run was painted in the pass
   * palette as well as labelled "100%" — the colour axis lying in the same
   * direction as the string, which is precisely how a rounding artefact becomes
   * an operator's belief that a run has finished.
   */
  describe('S11 — the chamber progress bar', () => {
    function renderBar(ratio: number, isRunning = false): HTMLElement {
      render(
        <ChamberProgressBar
          progress={{ is_running: isRunning, completed: 996, total: 1000, ratio }}
          testId="chamber-bar"
        />,
      );
      return screen.getByTestId('chamber-bar');
    }

    function valueText(bar: HTMLElement): string | null | undefined {
      return bar.querySelector('.progress-bar__value')?.textContent;
    }

    it('gives neither the completion tone nor "100%" to a 99.6% run', () => {
      const bar = renderBar(0.996);
      expect(bar).not.toHaveClass('progress-bar--pass');
      expect(valueText(bar)).not.toBe('100%');
      expect(valueText(bar)).toBe(formatPercent(0.996 * 100));
    });

    it('still gives a genuinely finished run the completion tone', () => {
      // Non-vacuity: the fix must not be "never show complete".
      const bar = renderBar(1);
      expect(bar).toHaveClass('progress-bar--pass');
      expect(valueText(bar)).toBe(formatPercent(100));
    });

    it('decides the tone on the SAME boundary the SSOT uses, for every input', () => {
      // The milestone's real content: the tone axis and the string axis must not
      // be able to disagree, or the bar reads "complete" while the number reads
      // 99% (a new contradiction in place of the old one).
      for (const percent of BOUNDARY_INPUTS) {
        if (Number.isNaN(percent)) continue;
        cleanup();
        const bar = renderBar(percent / 100);
        const expectComplete = classifyPercent(percent).kind === 'complete';
        expect(bar.classList.contains('progress-bar--pass'), `percent=${percent}`).toBe(
          expectComplete,
        );
      }
    });

    it('keeps a live run in the running tone regardless of the ratio', () => {
      const bar = renderBar(1, true);
      expect(bar).toHaveClass('progress-bar--running');
    });
  });

  it('the overall metric on the dashboard does not round up to 100%', async () => {
    platformApi.fetchProjectProgress.mockResolvedValue([
      bucket({ planned_minutes: 1000, completed_minutes: 996, percent: 99.6 }),
    ]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/progress?project=${PROJECT_ID}`]}>
          <ProgressRoute />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const metric = await screen.findByTestId('progress-metric-percent');
    expect(metric).not.toHaveTextContent('100%');
    expect(metric).toHaveTextContent(formatPercent(99.6));
  });
});
