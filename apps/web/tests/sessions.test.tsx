import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { SessionsRoute, groupByCondition, parseSessionParam } from '@/routes/sessions';

import {
  headlessDownload,
  headlessOk,
  headlessProblem,
  problemDetails,
} from './helpers/headless-contract';
import { spyHeadlessTransport } from './helpers/headless-transport';

import type { HeadlessEnvelope, HeadlessOkBody } from './helpers/headless-contract';
import type { ReactElement } from 'react';

/**
 * FE-P4 frontend (2026-05-26) — session / result browser tests.
 *
 * Consumes the FE-P4 backend attempt-history API (mocked). Covers id/param
 * parsing, condition grouping (trend axis), RBAC, URL-query-driven lookup,
 * grouped attempt history rendering, and technology filtering.
 */

const headlessClient = spyHeadlessTransport();

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
    accessToken: makeJwt({ sub: 'op-1', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

const ATTEMPTS_PATH = '/headless/sessions/{session_id}/attempts';
const EXPORT_PATH = '/headless/sessions/{session_id}/results/export';

type AttemptPage = HeadlessOkBody<'get', typeof ATTEMPTS_PATH>;
type Attempt = AttemptPage['items'][number];

function attempt(over: Partial<Attempt> = {}): Attempt {
  return {
    provider_id: 'p',
    session_id: '3',
    attempt_id: String(Math.random()),
    condition_hash: 'h1',
    sheet_name: 'Test Plan',
    row_order: 1,
    technology: 'BLE',
    attempt_number: 1,
    result: { result1: '10', result2: '', result_sum: '', margin: '1', dccf: '' },
    verdict: 'Pass',
    status: 'completed',
    recorded_by: 'alice',
    measured_at: '2026-05-26T00:00:00',
    ...over,
  };
}

function renderSessions(entry: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <SessionsRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  headlessClient.GET.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('parseSessionParam', () => {
  it('accepts positive integers and rejects everything else', () => {
    expect(parseSessionParam('3')).toBe(3);
    expect(parseSessionParam('0')).toBeNull();
    expect(parseSessionParam('-1')).toBeNull();
    expect(parseSessionParam('abc')).toBeNull();
    expect(parseSessionParam(null)).toBeNull();
  });
});

describe('groupByCondition', () => {
  it('groups by condition_hash and sorts each group by attempt_number', () => {
    const groups = groupByCondition([
      attempt({ condition_hash: 'h1', attempt_number: 2 }),
      attempt({ condition_hash: 'h2', attempt_number: 1 }),
      attempt({ condition_hash: 'h1', attempt_number: 1 }),
    ] as never);
    expect(groups).toHaveLength(2);
    const h1 = groups.find((g) => g.conditionHash === 'h1');
    expect(h1?.attempts.map((a) => a.attempt_number)).toEqual([1, 2]);
  });

  it('does NOT merge same condition_hash with different row_order (compound key)', () => {
    const groups = groupByCondition([
      attempt({ condition_hash: 'h1', row_order: 1 }),
      attempt({ condition_hash: 'h1', row_order: 5 }),
    ] as never);
    expect(groups).toHaveLength(2);
    expect(new Set(groups.map((g) => g.groupKey)).size).toBe(2);
  });
});

describe('SessionsRoute RBAC', () => {
  it('denies the browser without headless:read', async () => {
    authenticateAs(['session:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, { items: [], next_cursor: null }),
    );
    renderSessions('/sessions?session=3');
    expect(await screen.findByTestId('auth-failure-permission_denied')).toBeInTheDocument();
    expect(screen.queryByTestId('session-input')).not.toBeInTheDocument();
  });
});

describe('SessionsRoute lookup + grouping + filter', () => {
  it('renders a table skeleton during the initial attempt load', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockImplementation(() => new Promise(() => undefined));
    renderSessions('/sessions?session=3');
    expect(await screen.findByTestId('data-table-skeleton')).toBeInTheDocument();
  });

  it('renders grouped attempt history for a session from the URL query', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, {
        items: [
          attempt({ condition_hash: 'h1', attempt_number: 1, technology: 'BLE', verdict: 'Fail' }),
          attempt({ condition_hash: 'h1', attempt_number: 2, technology: 'BLE', verdict: 'Pass' }),
          attempt({ condition_hash: 'h2', attempt_number: 1, technology: 'WLAN', verdict: 'Pass' }),
        ],
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3');

    expect(screen.getByTestId('sessions-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('sessions-workbench')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '측정 이력 작업 흐름' })).toBeInTheDocument();
    expect(screen.getByTestId('sessions-next-state')).toHaveTextContent('3');
    expect(screen.getByTestId('sessions-next-reports')).toHaveAttribute('href', '/reports');
    await waitFor(() =>
      expect(screen.getByTestId('attempts-summary')).toHaveTextContent('조건 2개'),
    );
    expect(screen.getAllByTestId('condition-group')).toHaveLength(2);
    // h1 has 2 attempts (trend), h2 has 1 → 3 attempt rows total
    expect(screen.getAllByTestId('attempt-row')).toHaveLength(3);
    expect(screen.getAllByTestId('attempt-history')[0]).toHaveClass('data-table--sticky-header');
    expect(headlessClient.GET).toHaveBeenCalledWith('/headless/sessions/{session_id}/attempts', {
      params: { path: { session_id: 3 } },
    });
  });

  it('limits rendered attempt rows for large loaded histories', async () => {
    authenticateAs(['headless:read']);
    // jsdom has no layout engine, so @tanstack/react-virtual (which sizes via
    // offsetWidth/offsetHeight) measures every element as 0×0 and would window
    // down to zero rows. Stub a viewport height so the virtualizer renders a
    // realistic on-screen window; the assertion then proves DOM rows are
    // windowed (≪ loaded rows), not that the list is empty.
    const originalOffsetHeight = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'offsetHeight',
    );
    const originalOffsetWidth = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'offsetWidth',
    );
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get: () => 600,
    });
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get: () => 880,
    });
    const restoreLayout = (): void => {
      if (originalOffsetHeight) {
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', originalOffsetHeight);
      }
      if (originalOffsetWidth) {
        Object.defineProperty(HTMLElement.prototype, 'offsetWidth', originalOffsetWidth);
      }
    };
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, {
        items: Array.from({ length: 501 }).map((_, index) =>
          attempt({
            attempt_id: `a-${index}`,
            attempt_number: index + 1,
            condition_hash: 'large',
          }),
        ),
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3');
    expect(await screen.findByTestId('virtualized-attempt-history')).toBeInTheDocument();
    // The virtualizer measures the scroll element in a mount effect, so the
    // first windowed rows appear on the following render tick.
    await waitFor(() => expect(screen.getAllByTestId('attempt-row').length).toBeGreaterThan(0));
    // Windowed: far fewer DOM rows than the 501 loaded attempts.
    expect(screen.getAllByTestId('attempt-row').length).toBeLessThan(501);
    restoreLayout();
  });

  it('keeps the keyboard workflow on the virtualized large-history path (card F1)', async () => {
    authenticateAs(['headless:read']);
    const originalOffsetHeight = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'offsetHeight',
    );
    const originalOffsetWidth = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      'offsetWidth',
    );
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
      configurable: true,
      get: () => 600,
    });
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
      configurable: true,
      get: () => 880,
    });
    const restoreLayout = (): void => {
      if (originalOffsetHeight)
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', originalOffsetHeight);
      if (originalOffsetWidth)
        Object.defineProperty(HTMLElement.prototype, 'offsetWidth', originalOffsetWidth);
    };
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, {
        items: Array.from({ length: 501 }).map((_, index) =>
          attempt({ attempt_id: `a-${index}`, attempt_number: index + 1, condition_hash: 'large' }),
        ),
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3');
    const table = await screen.findByTestId('virtualized-attempt-history');
    // The windowed path must NOT silently drop keyboard navigation.
    expect(table).toHaveAttribute('data-keyboard-navigation', 'true');
    await waitFor(() => expect(table.querySelector('[role="row"][data-index]')).not.toBeNull());
    const firstRow = table.querySelector<HTMLElement>('[role="row"][data-index="0"]');
    if (firstRow === null) throw new Error('first virtualized row not mounted');
    expect(firstRow).toHaveAttribute('tabindex', '0');
    firstRow.focus();
    fireEvent.keyDown(firstRow, { key: 'ArrowDown' });
    const secondRow = table.querySelector<HTMLElement>('[role="row"][data-index="1"]');
    expect(document.activeElement).toBe(secondRow);
    restoreLayout();
  });

  it('filters by technology via the ?tech query param', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, {
        items: [
          attempt({ condition_hash: 'h1', technology: 'BLE' }),
          attempt({ condition_hash: 'h2', technology: 'WLAN' }),
        ],
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3&tech=WLAN');
    await waitFor(() =>
      expect(screen.getByTestId('attempts-summary')).toHaveTextContent('조건 1개'),
    );
  });

  it('warns on an invalid session id', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, { items: [], next_cursor: null }),
    );
    renderSessions('/sessions?session=abc');
    expect(await screen.findByTestId('session-invalid')).toBeInTheDocument();
  });

  it('does not query until the lookup is submitted (no per-keystroke fetch)', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, { items: [], next_cursor: null }),
    );
    renderSessions('/sessions'); // no committed session param
    const input = await screen.findByTestId('session-input');
    await userEvent.type(input, '123'); // typing must NOT fire queries
    expect(headlessClient.GET).not.toHaveBeenCalled();
    await userEvent.click(screen.getByTestId('session-lookup'));
    await waitFor(() =>
      expect(headlessClient.GET).toHaveBeenCalledWith('/headless/sessions/{session_id}/attempts', {
        params: { path: { session_id: 123 } },
      }),
    );
  });
});

describe('SessionsRoute keyset pagination (FE-P4-PAGE)', () => {
  it('shows 더 보기 when truncated and appends the next page with the cursor', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValueOnce(
      headlessOk('get', ATTEMPTS_PATH, {
        items: [attempt({ condition_hash: 'h1', attempt_number: 1 })],
        next_cursor: 'CUR1',
      }),
    ).mockResolvedValueOnce(
      headlessOk('get', ATTEMPTS_PATH, {
        items: [attempt({ condition_hash: 'h2', attempt_number: 1 })],
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3');

    // First page: truncation surfaced + only the first page's rows shown.
    await waitFor(() =>
      expect(screen.getByTestId('attempts-summary')).toHaveTextContent('더 있음'),
    );
    expect(screen.getAllByTestId('attempt-row')).toHaveLength(1);

    // 더 보기 → second page fetched with the opaque cursor query.
    await userEvent.click(screen.getByTestId('attempts-load-more'));
    await waitFor(() => expect(screen.getAllByTestId('attempt-row')).toHaveLength(2));
    expect(headlessClient.GET).toHaveBeenLastCalledWith(
      '/headless/sessions/{session_id}/attempts',
      { params: { path: { session_id: 3 }, query: { cursor: 'CUR1' } } },
    );
    // next_cursor === null on the last page → the button is gone.
    expect(screen.queryByTestId('attempts-load-more')).not.toBeInTheDocument();
  });

  it('does not show 더 보기 when the first page is the last (next_cursor null)', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, {
        items: [attempt({ condition_hash: 'h1', attempt_number: 1 })],
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3');
    await waitFor(() => expect(screen.getByTestId('attempts-summary')).toBeInTheDocument());
    expect(screen.getByTestId('attempts-summary')).not.toHaveTextContent('더 있음');
    expect(screen.queryByTestId('attempts-load-more')).not.toBeInTheDocument();
  });
});

/**
 * S7 — measurement unit metadata reaches the screen (fe-w2-a M4).
 *
 * D5: the backend has always emitted `result1_unit`/`result2_unit`, but the
 * OpenAPI schema described `MeasurementAttemptEnvelope.result` as a bare
 * `{"type":"object"}`, so the generated TypeScript typed it as an empty object
 * and the consumer could not reach the fields even if it wanted to. The operator
 * read `"22.0"` with no way to tell dBm from kHz from msec — in a regulatory
 * report that is not a cosmetic gap.
 *
 * The second test is the regression half: an attempt WITHOUT unit metadata must
 * render exactly as it did before, because `MeasurementValueCell` disables its
 * suffix-parsing fallback the moment a `unit` prop is defined — passing `''`
 * would have silently blanked the unit on legacy combined-string payloads.
 */
describe('attempt result units (S7)', () => {
  it('renders the unit that came with the measurement', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, {
        items: [
          attempt({
            condition_hash: 'h1',
            attempt_number: 1,
            result: {
              result1: '22.0',
              result1_unit: 'dBm',
              result2: '1500',
              result2_unit: 'kHz',
              result_sum: '',
              result_sum_unit: '',
              margin: '1',
              dccf: '',
            },
          }),
        ],
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3');

    const row = (await screen.findAllByTestId('attempt-row'))[0];
    if (!row) throw new Error('expected an attempt row');
    const units = within(row).getAllByTestId('measurement-value-unit');
    expect(units.map((node) => node.textContent)).toEqual(['dBm', 'kHz']);
    // The value itself is untouched — the frontend never recomputes a
    // measurement, it only stops hiding the unit it was given.
    expect(within(row).getAllByTestId('measurement-value')[0]).toHaveTextContent('22.0');
  });

  it('renders an attempt WITHOUT unit metadata exactly as before', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, {
        items: [
          attempt({
            condition_hash: 'h1',
            attempt_number: 1,
            // No `*_unit` keys at all, and a legacy combined-string value whose
            // unit only exists as a suffix — the primitive's fallback parse must
            // still run.
            result: { result1: '22.0 dBm', result2: '', result_sum: '', margin: '1', dccf: '' },
          }),
        ],
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3');

    const row = (await screen.findAllByTestId('attempt-row'))[0];
    if (!row) throw new Error('expected an attempt row');
    expect(within(row).getAllByTestId('measurement-value-unit')[0]).toHaveTextContent('dBm');
  });

  it('treats a blank unit as absent rather than as an authoritative empty unit', async () => {
    authenticateAs(['headless:read']);
    headlessClient.GET.mockResolvedValue(
      headlessOk('get', ATTEMPTS_PATH, {
        items: [
          attempt({
            condition_hash: 'h1',
            attempt_number: 1,
            // The backend `_text` helper yields '' for a missing column, so a
            // blank unit is the COMMON case — it must not suppress the fallback.
            result: { result1: '22.0 dBm', result1_unit: '', result2: '', margin: '1', dccf: '' },
          }),
        ],
        next_cursor: null,
      }),
    );
    renderSessions('/sessions?session=3');

    const row = (await screen.findAllByTestId('attempt-row'))[0];
    if (!row) throw new Error('expected an attempt row');
    expect(within(row).getAllByTestId('measurement-value-unit')[0]).toHaveTextContent('dBm');
  });
});

/**
 * Measurement-result export (2026-08-13) — the button the Measurement Result
 * Export SSOT (2026-08-11) shipped an operation for and no screen consumed.
 */
describe('SessionsRoute measurement-result export', () => {
  /** Route `headlessClient.GET` per path so the attempt history and the export
   *  can answer differently in the same render. */
  function mockPaths(
    exportResult: HeadlessEnvelope<'get', typeof EXPORT_PATH>,
    attempts: Attempt[] = [attempt()],
  ): void {
    headlessClient.routes({
      [EXPORT_PATH]: { get: () => exportResult },
      [ATTEMPTS_PATH]: {
        get: () => headlessOk('get', ATTEMPTS_PATH, { items: attempts, next_cursor: null }),
      },
    });
  }

  function stubDownload(): {
    anchorClick: ReturnType<typeof vi.fn>;
    restore: () => void;
  } {
    /* eslint-disable @typescript-eslint/unbound-method -- captured verbatim so
       restore() puts back exactly what was there. */
    const originalCreate = URL.createObjectURL;
    const originalRevoke = URL.revokeObjectURL;
    const originalClick = HTMLAnchorElement.prototype.click;
    /* eslint-enable @typescript-eslint/unbound-method */
    URL.createObjectURL = vi.fn(() => 'blob:stub');
    URL.revokeObjectURL = vi.fn();
    const anchorClick = vi.fn();
    HTMLAnchorElement.prototype.click = anchorClick;
    return {
      anchorClick,
      restore: () => {
        URL.createObjectURL = originalCreate;
        URL.revokeObjectURL = originalRevoke;
        HTMLAnchorElement.prototype.click = originalClick;
      },
    };
  }

  function okExport(headers: Record<string, string>): HeadlessEnvelope<'get', typeof EXPORT_PATH> {
    return headlessDownload(
      'get',
      EXPORT_PATH,
      new Blob(['xlsx'], { type: 'application/octet-stream' }),
      headers,
    );
  }

  it('downloads the workbook under the server-sent filename', async () => {
    authenticateAs(['headless:read']);
    mockPaths(
      okExport({ 'content-disposition': 'attachment; filename="measurement-results-A__1-3.xlsx"' }),
    );
    const download = stubDownload();
    try {
      renderSessions('/sessions?session=3');
      fireEvent.click(await screen.findByTestId('sessions-export'));

      await waitFor(() => expect(download.anchorClick).toHaveBeenCalledTimes(1));
      const anchor = download.anchorClick.mock.instances[0] as HTMLAnchorElement;
      expect(anchor.download).toBe('measurement-results-A__1-3.xlsx');
      /* eslint-disable-next-line @typescript-eslint/unbound-method -- asserting
         on the stub itself, never calling it. */
      expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:stub');
      // The export is session-scoped: the request carries the session id and no
      // filter, because the workbook is the whole session (see the component).
      expect(headlessClient.GET).toHaveBeenCalledWith(
        '/headless/sessions/{session_id}/results/export',
        { params: { path: { session_id: 3 } }, parseAs: 'blob' },
      );
    } finally {
      download.restore();
    }
  });

  it('falls back to the name the service itself derives when no header is sent', async () => {
    authenticateAs(['headless:read']);
    mockPaths(okExport({}));
    const download = stubDownload();
    try {
      renderSessions('/sessions?session=7');
      fireEvent.click(await screen.findByTestId('sessions-export'));

      await waitFor(() => expect(download.anchorClick).toHaveBeenCalledTimes(1));
      const anchor = download.anchorClick.mock.instances[0] as HTMLAnchorElement;
      expect(anchor.download).toBe('measurement-results-7.xlsx');
    } finally {
      download.restore();
    }
  });

  it('says the session measured nothing on 422 SESSION_RESULTS_EMPTY', async () => {
    authenticateAs(['headless:read']);
    mockPaths(
      headlessProblem(
        'get',
        EXPORT_PATH,
        422,
        problemDetails(422, 'SESSION_RESULTS_EMPTY', {
          title: 'Session has no measurement results',
        }),
      ),
    );
    renderSessions('/sessions?session=3');
    fireEvent.click(await screen.findByTestId('sessions-export'));

    const error = await screen.findByTestId('sessions-export-error');
    // The taxonomy message, not the generic 422 one — this is the whole reason
    // the headless artifact had to publish the code.
    expect(error).toHaveTextContent('측정된 행이 없습니다');
    expect(error).not.toHaveTextContent('입력을 확인해 주세요');
  });

  it('stays available when the tech filter hides every row', async () => {
    // The regression this guards: `view.isEmpty` is computed AFTER the tech
    // filter, so gating the button on it would withhold the download from a
    // session that HAS results but none matching the current filter. Whether a
    // session truly measured nothing is the server's answer (the 422 above).
    authenticateAs(['headless:read']);
    mockPaths(okExport({}), [attempt({ technology: 'BLE' })]);
    renderSessions('/sessions?session=3&tech=WLAN');

    expect(await screen.findByTestId('sessions-export')).toBeInTheDocument();
  });

  it('is not reachable without headless:read', async () => {
    authenticateAs(['session:read']);
    mockPaths(okExport({}));
    renderSessions('/sessions?session=3');

    await waitFor(() => expect(screen.queryByTestId('sessions-export')).not.toBeInTheDocument());
  });
});
