import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import { SHORTCUTS } from '@/shared/shortcuts';
import { ShortcutHelp } from '@/ui';

/**
 * ShortcutHelp (card B3 + design-followup #1/#2) — keyboard shortcut reference
 * overlay. Verifies the modal a11y contract (role/aria-modal/labelledby), that
 * it is hidden when closed, Esc / close button / backdrop dismiss it, that the
 * rendered key set is DERIVED from the `@/shared/shortcuts` SSOT (no drift), and
 * that focus is trapped in the dialog and restored to the trigger on close.
 */

function Harness(): JSX.Element {
  const [open, setOpen] = useState(true);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)} data-testid="reopen">
        open
      </button>
      <ShortcutHelp open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

/** Starts closed so a test can focus the trigger, open via click (which focuses
 *  the trigger), then assert focus restoration on close. */
function ClosedHarness(): JSX.Element {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)} data-testid="reopen">
        open
      </button>
      <ShortcutHelp open={open} onClose={() => setOpen(false)} />
    </div>
  );
}

describe('ShortcutHelp', () => {
  it('renders nothing when closed', () => {
    render(<ShortcutHelp open={false} onClose={() => undefined} />);
    expect(screen.queryByTestId('shortcut-help')).not.toBeInTheDocument();
  });

  it('renders an accessible modal dialog when open', () => {
    render(<ShortcutHelp open onClose={() => undefined} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-labelledby', 'shortcut-help-title');
    // Lists the goto + help rows.
    expect(screen.getByText('g s')).toBeInTheDocument();
    expect(screen.getByText('?')).toBeInTheDocument();
  });

  it('closes on Escape', async () => {
    render(<Harness />);
    expect(screen.getByTestId('shortcut-help')).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByTestId('shortcut-help')).not.toBeInTheDocument();
  });

  it('closes when the close button is clicked', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: '단축키 도움말 닫기' }));
    expect(screen.queryByTestId('shortcut-help')).not.toBeInTheDocument();
  });

  it('closes when the backdrop is clicked but not when the dialog body is', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByTestId('shortcut-help'));
    expect(screen.getByTestId('shortcut-help')).toBeInTheDocument();
    await userEvent.click(screen.getByTestId('shortcut-help-backdrop'));
    expect(screen.queryByTestId('shortcut-help')).not.toBeInTheDocument();
  });

  it('renders exactly the SSOT shortcut sequences (no drift)', () => {
    render(<ShortcutHelp open onClose={() => undefined} />);
    const rendered = screen
      .getAllByRole('term')
      .map((dt) => dt.textContent?.trim())
      .filter((text): text is string => text !== undefined && text.length > 0);
    const expected = SHORTCUTS.map((shortcut) => shortcut.sequence);
    expect(new Set(rendered)).toEqual(new Set(expected));
    expect(rendered).toHaveLength(expected.length);
  });

  it('moves focus into the dialog on open and restores it to the trigger on close', async () => {
    render(<ClosedHarness />);
    const trigger = screen.getByTestId('reopen');
    await userEvent.click(trigger); // focuses the trigger, then opens the modal
    // On open, focus lands on the first focusable inside the dialog (close btn).
    const closeButton = screen.getByRole('button', { name: '단축키 도움말 닫기' });
    expect(document.activeElement).toBe(closeButton);
    // Esc closes and restores focus to the element focused before opening.
    await userEvent.keyboard('{Escape}');
    expect(screen.queryByTestId('shortcut-help')).not.toBeInTheDocument();
    expect(document.activeElement).toBe(trigger);
  });

  it('traps Tab within the dialog (focus never leaves the modal)', async () => {
    render(<Harness />);
    const closeButton = screen.getByRole('button', { name: '단축키 도움말 닫기' });
    expect(document.activeElement).toBe(closeButton);
    // The dialog's only focusable is the close button, so Tab/Shift+Tab keep
    // focus on it rather than escaping to the backdrop/trigger behind the modal.
    await userEvent.keyboard('{Tab}');
    expect(document.activeElement).toBe(closeButton);
    await userEvent.keyboard('{Shift>}{Tab}{/Shift}');
    expect(document.activeElement).toBe(closeButton);
  });
});
