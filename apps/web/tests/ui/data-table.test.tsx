import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { DataTable, DataTableSortButton } from '@/ui/DataTable';

import type { DataTableColumn } from '@/ui/DataTable';

describe('DataTable', () => {
  it('wraps the table in the horizontal overflow container', () => {
    render(
      <DataTable
        caption="시도 이력"
        head={
          <thead>
            <tr>
              <th>회차</th>
              <th className="data-cell-numeric">측정값</th>
            </tr>
          </thead>
        }
        body={
          <tbody>
            <tr>
              <td>1</td>
              <td className="data-cell-numeric">22.0</td>
            </tr>
          </tbody>
        }
      />,
    );
    expect(screen.getByTestId('data-table-overflow')).toBeInTheDocument();
    expect(screen.getByRole('table')).toBeInTheDocument();
    // caption rendered (a11y §5.3)
    expect(screen.getByText('시도 이력')).toBeInTheDocument();
  });

  it('numeric cells receive tabular-nums via the data-cell-numeric class', () => {
    render(
      <DataTable
        caption="t"
        head={
          <thead>
            <tr>
              <th>v</th>
            </tr>
          </thead>
        }
        body={
          <tbody>
            <tr>
              <td className="data-cell-numeric" data-testid="numeric-cell">
                22.0
              </td>
            </tr>
          </tbody>
        }
      />,
    );
    expect(screen.getByTestId('numeric-cell').className).toContain('data-cell-numeric');
  });

  it('adds the sticky header class when requested', () => {
    render(
      <DataTable
        caption="t"
        stickyHeader
        head={
          <thead>
            <tr>
              <th>v</th>
            </tr>
          </thead>
        }
        body={
          <tbody>
            <tr>
              <td>1</td>
            </tr>
          </tbody>
        }
      />,
    );
    expect(screen.getByRole('table')).toHaveClass('data-table--sticky-header');
  });

  it('supports opt-in row keyboard navigation', async () => {
    render(
      <DataTable
        caption="t"
        keyboardNavigation
        head={
          <thead>
            <tr>
              <th>v</th>
            </tr>
          </thead>
        }
        body={
          <tbody>
            <tr data-testid="row-1">
              <td>1</td>
            </tr>
            <tr data-testid="row-2">
              <td>2</td>
            </tr>
            <tr data-testid="row-3">
              <td>3</td>
            </tr>
          </tbody>
        }
      />,
    );

    const first = screen.getByTestId('row-1');
    const second = screen.getByTestId('row-2');
    const third = screen.getByTestId('row-3');
    expect(first).toHaveAttribute('tabindex', '0');
    first.focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(second).toHaveFocus();
    await userEvent.keyboard('j');
    expect(third).toHaveFocus();
    await userEvent.keyboard('{Home}');
    expect(first).toHaveFocus();
    await userEvent.keyboard('{End}');
    expect(third).toHaveFocus();
    await userEvent.keyboard('k');
    expect(second).toHaveFocus();
  });

  it('renders a sort helper button for caller-owned aria-sort headers', async () => {
    let clicked = false;
    render(
      <table>
        <thead>
          <tr>
            <th aria-sort="ascending">
              <DataTableSortButton
                direction="asc"
                onClick={() => {
                  clicked = true;
                }}
              >
                Name
              </DataTableSortButton>
            </th>
          </tr>
        </thead>
      </table>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Name' }));
    expect(clicked).toBe(true);
  });
});

interface Row {
  readonly id: string;
  readonly verdict: string;
  readonly recordedBy: string;
}

const ROWS: readonly Row[] = [
  { id: '1', verdict: 'PASS', recordedBy: '홍길동' },
  { id: '2', verdict: 'FAIL', recordedBy: '김측정' },
];

function columns(): readonly DataTableColumn<Row>[] {
  return [
    { key: 'id', header: '시도', priority: 'primary', cell: (r) => r.id, sortable: true },
    { key: 'verdict', header: '판정', priority: 'secondary', cell: (r) => r.verdict },
    { key: 'recordedBy', header: '기록자', priority: 'detail', cell: (r) => r.recordedBy },
  ];
}

describe('DataTable — responsive column descriptor (§M7.2)', () => {
  it('tags every cell with its fold priority', () => {
    render(
      <DataTable<Row>
        caption="c"
        columns={columns()}
        rows={ROWS}
        rowKey={(r) => r.id}
        testId="t"
      />,
    );
    const table = screen.getByTestId('t');
    expect(table.querySelectorAll("[data-priority='detail']").length).toBeGreaterThan(0);
    expect(table).toHaveClass('data-table--responsive');
  });

  it('re-renders folded detail values in a per-row overflow line (no data loss)', () => {
    render(<DataTable<Row> caption="c" columns={columns()} rows={ROWS} rowKey={(r) => r.id} />);
    const overflowRows = screen.getAllByTestId('data-table-overflow-row');
    expect(overflowRows).toHaveLength(ROWS.length);
    // The value the compact band removes from the row grid is still reachable.
    expect(overflowRows[0]).toHaveTextContent('홍길동');
  });

  it('renders a card per row carrying EVERY column', () => {
    render(
      <DataTable<Row> caption="시도 이력" columns={columns()} rows={ROWS} rowKey={(r) => r.id} />,
    );
    const cards = screen.getAllByTestId('data-table-card');
    expect(cards).toHaveLength(ROWS.length);
    for (const header of ['판정', '기록자']) {
      expect(cards[0]).toHaveTextContent(header);
    }
    expect(cards[0]).toHaveTextContent('PASS');
    expect(cards[0]).toHaveTextContent('홍길동');
  });

  it('offers the mobile sort select built from the sortable headers (§M7.2b)', async () => {
    const onChange = vi.fn();
    render(
      <DataTable<Row>
        caption="c"
        columns={columns()}
        rows={ROWS}
        rowKey={(r) => r.id}
        sort={{ columnKey: 'id', direction: 'asc', onChange }}
      />,
    );
    const select = screen.getByTestId('data-table-mobile-sort-select');
    // Only sortable columns become options — a header that cannot sort must
    // not appear as if it could.
    expect(select.querySelectorAll('option')).toHaveLength(1);
    await userEvent.click(screen.getByTestId('data-table-mobile-sort-direction'));
    expect(onChange).toHaveBeenCalledWith('id', 'desc');
  });

  it('omits the sort affordances entirely when the table is unsorted', () => {
    render(<DataTable<Row> caption="c" columns={columns()} rows={ROWS} rowKey={(r) => r.id} />);
    expect(screen.queryByTestId('data-table-mobile-sort')).toBeNull();
  });

  it('leaves the legacy slot form byte-identical (no modifier, no extra rows)', () => {
    render(
      <DataTable
        caption="c"
        testId="legacy"
        head={
          <thead>
            <tr>
              <th scope="col">A</th>
            </tr>
          </thead>
        }
        body={
          <tbody>
            <tr>
              <td>1</td>
            </tr>
          </tbody>
        }
      />,
    );
    const table = screen.getByTestId('legacy');
    expect(table).not.toHaveClass('data-table--responsive');
    expect(screen.queryByTestId('data-table-cards')).toBeNull();
    expect(screen.queryByTestId('data-table-overflow-row')).toBeNull();
  });

  it('renders a full-width expansion panel only for the rows the caller opened', () => {
    render(
      <DataTable<Row>
        caption="c"
        columns={columns()}
        rows={ROWS}
        rowKey={(r) => r.id}
        testId="t"
        expansion={{
          isExpanded: (r) => r.id === '1',
          cardSummary: (r) => `${r.id} 상세`,
          render: (r, surface) => <p data-surface={surface}>panel-{r.id}</p>,
        }}
      />,
    );
    const panels = screen.getByTestId('t').querySelectorAll('.data-table__expansion-row');
    expect(panels).toHaveLength(1);
    // Full width by construction — a new column can never leave it misaligned.
    expect(panels[0]?.querySelector('td')).toHaveAttribute('colspan', String(columns().length));
    expect(panels[0]).toHaveTextContent('panel-1');
  });

  it('re-offers the same panel inside the card as a native <details> (phone band)', () => {
    render(
      <DataTable<Row>
        caption="c"
        columns={columns()}
        rows={ROWS}
        rowKey={(r) => r.id}
        expansion={{
          isExpanded: (r) => r.id === '1',
          cardSummary: (r) => `${r.id} 상세`,
          render: (r, surface) => <p data-surface={surface}>panel-{r.id}</p>,
        }}
      />,
    );
    // The table hides below --bp-sm, so EVERY row's panel must be reachable
    // from its card — including the ones the table surface left collapsed.
    const disclosures = screen.getAllByTestId('data-table-card-expansion');
    expect(disclosures).toHaveLength(ROWS.length);
    expect(disclosures[0]).toHaveTextContent('panel-1');
    expect(disclosures[1]).toHaveTextContent('panel-2');
    // Caller-owned open state seeds the disclosure; the widget itself is the
    // platform's, so no JS and no viewport branch is involved (M7.2a).
    expect(disclosures[0]).toHaveAttribute('open');
    expect(disclosures[1]).not.toHaveAttribute('open');
    expect(screen.getByText('1 상세')).toBeInTheDocument();
  });

  it('tells each surface apart so a caller can keep document-unique ids unique', () => {
    render(
      <DataTable<Row>
        caption="c"
        columns={[
          {
            key: 'id',
            header: '시도',
            priority: 'primary',
            cell: (r, surface) => <span data-testid={`cell-${surface}`}>{r.id}</span>,
          },
        ]}
        rows={ROWS.slice(0, 1)}
        rowKey={(r) => r.id}
        expansion={{
          isExpanded: () => true,
          cardSummary: () => 's',
          render: (_r, surface) => <span data-testid={`panel-${surface}`}>p</span>,
        }}
      />,
    );
    expect(screen.getByTestId('cell-row')).toBeInTheDocument();
    expect(screen.getByTestId('cell-card')).toBeInTheDocument();
    expect(screen.getByTestId('panel-row')).toBeInTheDocument();
    expect(screen.getByTestId('panel-card')).toBeInTheDocument();
  });

  it('omits the expansion surfaces entirely when no expansion descriptor is given', () => {
    render(
      <DataTable<Row>
        caption="c"
        columns={columns()}
        rows={ROWS}
        rowKey={(r) => r.id}
        testId="t"
      />,
    );
    expect(screen.getByTestId('t').querySelectorAll('.data-table__expansion-row')).toHaveLength(0);
    expect(screen.queryByTestId('data-table-card-expansion')).toBeNull();
  });

  it('keeps the expansion row out of the roving-tabindex row sequence', () => {
    render(
      <DataTable<Row>
        caption="c"
        columns={columns()}
        rows={ROWS}
        rowKey={(r) => r.id}
        rowTestId="data-row"
        keyboardNavigation
        expansion={{
          isExpanded: () => true,
          cardSummary: () => 's',
          render: () => <p>p</p>,
        }}
      />,
    );
    const dataRows = screen.getAllByTestId('data-row');
    expect(dataRows[0]).toHaveAttribute('tabindex', '0');
    expect(dataRows[1]).toHaveAttribute('tabindex', '-1');
    for (const panel of screen.getAllByTestId('data-table-expansion-row')) {
      expect(panel).not.toHaveAttribute('tabindex');
    }
  });

  it('keeps the overflow line out of the roving-tabindex row sequence', () => {
    render(
      <DataTable<Row>
        caption="c"
        columns={columns()}
        rows={ROWS}
        rowKey={(r) => r.id}
        rowTestId="data-row"
        keyboardNavigation
      />,
    );
    const dataRows = screen.getAllByTestId('data-row');
    expect(dataRows[0]).toHaveAttribute('tabindex', '0');
    expect(dataRows[1]).toHaveAttribute('tabindex', '-1');
    for (const overflow of screen.getAllByTestId('data-table-overflow-row')) {
      expect(overflow).not.toHaveAttribute('tabindex');
    }
  });
});
