import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { Button } from '@/ui/Button';

describe('Button', () => {
  it('keeps native action attributes while applying the shared hierarchy', () => {
    render(
      <Button
        type="submit"
        name="scope"
        value="all"
        variant="primary"
        size="lg"
        data-testid="submit-action"
      >
        저장
      </Button>,
    );

    const button = screen.getByRole('button', { name: '저장' });
    expect(button).toHaveAttribute('type', 'submit');
    expect(button).toHaveAttribute('name', 'scope');
    expect(button).toHaveAttribute('value', 'all');
    expect(button).toHaveAttribute('data-testid', 'submit-action');
    expect(button).toHaveClass('button', 'button--primary', 'button--lg');
  });

  it('exposes loading state and prevents duplicate activation', () => {
    render(
      <Button loading loadingLabel="저장 중">
        저장
      </Button>,
    );

    const button = screen.getByRole('button', { name: /저장/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('status')).toHaveTextContent('저장 중');
  });

  it('requires a name for icon-only actions and preserves events', () => {
    const onClick = vi.fn();
    render(
      <Button iconOnly variant="ghost" aria-label="닫기" onClick={onClick}>
        ×
      </Button>,
    );

    const button = screen.getByRole('button', { name: '닫기' });
    expect(button).toHaveClass('button--icon-only', 'button--ghost');
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('enforces an exclusive accessible-name type contract for icon-only actions', () => {
    // @ts-expect-error icon-only buttons must provide an accessible name.
    const unnamedIconOnly = <Button iconOnly>×</Button>;
    const doublyNamedIconOnly = (
      // @ts-expect-error icon-only buttons accept exactly one accessible-name source.
      <Button iconOnly aria-label="닫기" aria-labelledby="close-label">
        ×
      </Button>
    );

    expect(unnamedIconOnly).toBeDefined();
    expect(doublyNamedIconOnly).toBeDefined();
  });

  it('keeps the runtime guard active for empty icon-only names', () => {
    expect(() =>
      render(
        <Button iconOnly aria-label=" ">
          ×
        </Button>,
      ),
    ).toThrow('Button iconOnly requires aria-label or aria-labelledby');
  });
});
