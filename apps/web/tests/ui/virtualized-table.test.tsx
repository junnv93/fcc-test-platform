import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { VirtualizedTable } from '@/ui';

/**
 * VirtualizedTable primitive (B1/B2 shared table-virtualization contract). Proves
 * the windowing + ARIA-table scaffolding is OWNED BY THE PRIMITIVE — sessions is
 * no longer the only route that knows how to window a table. A route supplies
 * only items + header + per-row cells; the primitive owns role="table" /
 * aria-rowcount, the sticky header, the windowing (mounts ≪ total rows), and the
 * per-row role="row"/aria-rowindex chrome.
 */

// jsdom has no layout engine, so @tanstack/react-virtual measures every element
// as 0×0 and would window to zero rows. Stub a viewport so the window is real.
function stubLayout(): () => void {
  const oh = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight');
  const ow = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth');
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get: () => 600,
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get: () => 880,
  });
  return () => {
    if (oh) Object.defineProperty(HTMLElement.prototype, 'offsetHeight', oh);
    if (ow) Object.defineProperty(HTMLElement.prototype, 'offsetWidth', ow);
  };
}

afterEach(() => {
  // restore handled per-test
});

describe('VirtualizedTable', () => {
  it('renders an ARIA table with header + aria-rowcount and windows large lists', async () => {
    const restore = stubLayout();
    const items = Array.from({ length: 1000 }, (_, index) => ({ id: index }));
    render(
      <VirtualizedTable<{ id: number }>
        items={items}
        ariaLabel="rows"
        testId="vt"
        getRowKey={(item) => `row-${item.id}`}
        header={<span role="columnheader">ID</span>}
        renderRow={(item) => ({ testId: 'vt-row', cells: <span role="cell">{item.id}</span> })}
      />,
    );

    const table = screen.getByTestId('vt');
    expect(table).toHaveAttribute('role', 'table');
    expect(table).toHaveAttribute('aria-rowcount', '1000');
    expect(screen.getByRole('columnheader')).toHaveTextContent('ID');

    // The virtualizer measures in a mount effect; the first window appears next tick.
    await waitFor(() => expect(screen.getAllByTestId('vt-row').length).toBeGreaterThan(0));
    const rows = screen.getAllByTestId('vt-row');
    // Windowed: far fewer DOM rows than the 1000 items.
    expect(rows.length).toBeLessThan(1000);
    // Chrome (role/aria-rowindex) is supplied by the primitive, not the caller.
    expect(rows[0]).toHaveAttribute('role', 'row');
    expect(rows[0]).toHaveAttribute('aria-rowindex');
    restore();
  });

  it('defaults aria-rowcount to items.length but honours an explicit totalRowCount', () => {
    const items = Array.from({ length: 5 }, (_, index) => ({ id: index }));
    const header = <span role="columnheader">ID</span>;
    const renderRow = (item: { id: number }): { cells: JSX.Element } => ({
      cells: <span role="cell">{item.id}</span>,
    });

    // Default: full count.
    const { rerender } = render(
      <VirtualizedTable<{ id: number }>
        items={items}
        ariaLabel="rows"
        testId="vt-count"
        header={header}
        renderRow={renderRow}
      />,
    );
    expect(screen.getByTestId('vt-count')).toHaveAttribute('aria-rowcount', '5');

    // Keyset truncated: more pages remain → -1 ("size unknown" per WAI-ARIA),
    // NOT the partial loaded count.
    rerender(
      <VirtualizedTable<{ id: number }>
        items={items}
        ariaLabel="rows"
        testId="vt-count"
        totalRowCount={-1}
        header={header}
        renderRow={renderRow}
      />,
    );
    expect(screen.getByTestId('vt-count')).toHaveAttribute('aria-rowcount', '-1');
  });

  it('forwards a per-row className and falls back to the index key when getRowKey is omitted', async () => {
    const restore = stubLayout();
    render(
      <VirtualizedTable<{ label: string }>
        items={[{ label: 'alpha' }]}
        ariaLabel="rows"
        header={<span role="columnheader">Label</span>}
        renderRow={(item) => ({
          className: 'custom-row',
          cells: <span role="cell">{item.label}</span>,
        })}
      />,
    );
    await waitFor(() => expect(screen.getByText('alpha')).toBeInTheDocument());
    const row = screen.getByText('alpha').closest('[role="row"]');
    expect(row).not.toBeNull();
    expect(row).toHaveClass('virtual-table__virtual-item');
    expect(row).toHaveClass('custom-row');
    restore();
  });
});

/**
 * Keyboard-navigation contract (card F1). The windowed path must NOT silently
 * drop the keyboard workflow the non-virtualized DataTable provides — on the
 * largest datasets it is owned by THIS primitive. Roving tabindex + Arrow/j/k/
 * Home/End, interactive-child opt-out, and scrollToIndex-driven movement for
 * off-screen rows are all sealed here.
 */
describe('VirtualizedTable keyboard navigation', () => {
  function renderNav(count: number): { restore: () => void } {
    const restore = stubLayout();
    const items = Array.from({ length: count }, (_, index) => ({ id: index }));
    render(
      <VirtualizedTable<{ id: number }>
        items={items}
        ariaLabel="rows"
        testId="vt-nav"
        keyboardNavigation
        getRowKey={(item) => `row-${item.id}`}
        header={<span role="columnheader">ID</span>}
        renderRow={(item) => ({
          testId: `nav-row-${item.id}`,
          cells: <span role="cell">{item.id}</span>,
        })}
      />,
    );
    return { restore };
  }

  it('makes the first row tabbable (tabIndex 0) and the rest -1', async () => {
    const { restore } = renderNav(12);
    expect(screen.getByTestId('vt-nav')).toHaveAttribute('data-keyboard-navigation', 'true');
    await waitFor(() => expect(screen.getByTestId('nav-row-0')).toBeInTheDocument());
    expect(screen.getByTestId('nav-row-0')).toHaveAttribute('tabindex', '0');
    expect(screen.getByTestId('nav-row-1')).toHaveAttribute('tabindex', '-1');
    restore();
  });

  it('moves focus to the next row on ArrowDown and j', async () => {
    const { restore } = renderNav(12);
    await waitFor(() => expect(screen.getByTestId('nav-row-0')).toBeInTheDocument());
    const first = screen.getByTestId('nav-row-0');
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(screen.getByTestId('nav-row-1'));
    fireEvent.keyDown(screen.getByTestId('nav-row-1'), { key: 'j' });
    expect(document.activeElement).toBe(screen.getByTestId('nav-row-2'));
    restore();
  });

  it('moves to the first/last loaded row on Home/End', async () => {
    const { restore } = renderNav(12);
    await waitFor(() => expect(screen.getByTestId('nav-row-0')).toBeInTheDocument());
    const first = screen.getByTestId('nav-row-0');
    first.focus();
    fireEvent.keyDown(first, { key: 'End' });
    expect(document.activeElement).toBe(screen.getByTestId('nav-row-11'));
    fireEvent.keyDown(screen.getByTestId('nav-row-11'), { key: 'Home' });
    expect(document.activeElement).toBe(screen.getByTestId('nav-row-0'));
    restore();
  });

  it('uses scrollToIndex (scrollTo) to pull an off-screen row into view on End', async () => {
    const restore = stubLayout();
    // Ensure the scroll method exists in jsdom, then spy on it.
    if (typeof Element.prototype.scrollTo !== 'function') {
      Element.prototype.scrollTo = vi.fn();
    }
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollTo');
    const items = Array.from({ length: 1000 }, (_, index) => ({ id: index }));
    render(
      <VirtualizedTable<{ id: number }>
        items={items}
        ariaLabel="rows"
        testId="vt-nav-big"
        keyboardNavigation
        getRowKey={(item) => `row-${item.id}`}
        header={<span role="columnheader">ID</span>}
        renderRow={(item) => ({
          testId: `big-row-${item.id}`,
          cells: <span role="cell">{item.id}</span>,
        })}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('big-row-0')).toBeInTheDocument());
    // Row 999 is NOT initially mounted (windowed). End must scroll it into view.
    expect(screen.queryByTestId('big-row-999')).toBeNull();
    scrollSpy.mockClear();
    const first = screen.getByTestId('big-row-0');
    first.focus();
    fireEvent.keyDown(first, { key: 'End' });
    expect(scrollSpy).toHaveBeenCalled();
    scrollSpy.mockRestore();
    restore();
  });

  it('does not navigate when the keydown originates from an interactive child', async () => {
    const restore = stubLayout();
    const items = Array.from({ length: 12 }, (_, index) => ({ id: index }));
    render(
      <VirtualizedTable<{ id: number }>
        items={items}
        ariaLabel="rows"
        testId="vt-nav-int"
        keyboardNavigation
        getRowKey={(item) => `row-${item.id}`}
        header={<span role="columnheader">ID</span>}
        renderRow={(item) => ({
          testId: `int-row-${item.id}`,
          cells: (
            <span role="cell">
              <button type="button" data-testid={`int-btn-${item.id}`}>
                {item.id}
              </button>
            </span>
          ),
        })}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('int-btn-0')).toBeInTheDocument());
    const button = screen.getByTestId('int-btn-0');
    button.focus();
    fireEvent.keyDown(button, { key: 'ArrowDown' });
    // Focus stays on the button — the row navigation did not hijack it.
    expect(document.activeElement).toBe(button);
    restore();
  });

  it('activates a row on Enter/Space when onRowActivate is provided', async () => {
    const restore = stubLayout();
    const onRowActivate = vi.fn();
    const items = Array.from({ length: 12 }, (_, index) => ({ id: index }));
    render(
      <VirtualizedTable<{ id: number }>
        items={items}
        ariaLabel="rows"
        testId="vt-nav-act"
        keyboardNavigation
        onRowActivate={onRowActivate}
        getRowKey={(item) => `row-${item.id}`}
        header={<span role="columnheader">ID</span>}
        renderRow={(item) => ({
          testId: `act-row-${item.id}`,
          cells: <span role="cell">{item.id}</span>,
        })}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('act-row-0')).toBeInTheDocument());
    const row = screen.getByTestId('act-row-0');
    row.focus();
    fireEvent.keyDown(row, { key: 'Enter' });
    expect(onRowActivate).toHaveBeenCalledWith({ id: 0 }, 0);
    restore();
  });
});
