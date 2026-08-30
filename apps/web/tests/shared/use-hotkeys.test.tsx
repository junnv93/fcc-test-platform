import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useHotkeys, type Hotkey } from '@/shared/use-hotkeys';

/**
 * use-hotkeys form-field isolation (card F2). The integration paths (g s, /,
 * "/"-in-field) are sealed in tests/layout.test.tsx; this unit test pins the
 * SUBTLE buffer-leak contract those cannot reach: a key pressed inside a form
 * field must never enter the sequence buffer (unless an `allowInField` binding
 * is registered), so it cannot become the prefix of an out-of-field sequence.
 * It also pins the `allowInField` hook API so the opt-in path does not regress.
 */

function Harness({ hotkeys }: { hotkeys: readonly Hotkey[] }): JSX.Element {
  useHotkeys(hotkeys);
  return <input data-testid="field" aria-label="field" />;
}

afterEach(() => {
  vi.useRealTimers();
});

describe('useHotkeys form-field buffer isolation', () => {
  it('does not let an in-field key prefix an out-of-field sequence', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<Harness hotkeys={[{ sequence: 'g s', handler }]} />);

    const field = screen.getByTestId<HTMLInputElement>('field');
    // Type "g" INSIDE the field — must be ignored AND not buffered.
    field.focus();
    await user.keyboard('g');
    expect(field.value).toBe('g'); // literal typing preserved
    expect(handler).not.toHaveBeenCalled();

    // Now press "s" OUTSIDE any field. If the in-field "g" had leaked into the
    // buffer, "g s" would now complete. It must not.
    field.blur();
    await user.keyboard('s');
    expect(handler).not.toHaveBeenCalled();
  });

  it('does not preventDefault "/" typed inside a field (stays literal)', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<Harness hotkeys={[{ sequence: '/', handler }]} />);

    const field = screen.getByTestId<HTMLInputElement>('field');
    field.focus();
    await user.keyboard('/');
    expect(field.value).toBe('/');
    expect(handler).not.toHaveBeenCalled();
  });

  it('still completes an out-of-field g s sequence', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<Harness hotkeys={[{ sequence: 'g s', handler }]} />);

    document.body.focus();
    await user.keyboard('gs');
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('fires an allowInField binding even while a field is focused', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<Harness hotkeys={[{ sequence: 'k', handler, allowInField: true }]} />);

    const field = screen.getByTestId<HTMLInputElement>('field');
    field.focus();
    await user.keyboard('k');
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('does not fire a non-allowInField binding while a field is focused', async () => {
    const user = userEvent.setup();
    const handler = vi.fn();
    render(<Harness hotkeys={[{ sequence: 'k', handler }]} />);

    const field = screen.getByTestId<HTMLInputElement>('field');
    field.focus();
    await user.keyboard('k');
    expect(handler).not.toHaveBeenCalled();
  });
});
