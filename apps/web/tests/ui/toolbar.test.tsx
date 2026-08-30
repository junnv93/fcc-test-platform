import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Toolbar } from '@/ui/Toolbar';

describe('Toolbar', () => {
  it('exposes role=toolbar with the supplied aria-label', () => {
    render(
      <Toolbar ariaLabel="필터">
        <button type="button">a</button>
      </Toolbar>,
    );
    const region = screen.getByRole('toolbar', { name: '필터' });
    expect(region).toBeInTheDocument();
  });

  it('adds the inline modifier when inline is true', () => {
    render(
      <Toolbar ariaLabel="필터" inline>
        <span>x</span>
      </Toolbar>,
    );
    expect(screen.getByTestId('toolbar').className).toContain('toolbar--inline');
  });
});
