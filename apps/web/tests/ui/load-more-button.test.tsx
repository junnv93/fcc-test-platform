import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { LoadMoreButton } from '@/ui/LoadMoreButton';

describe('LoadMoreButton', () => {
  it('renders the default label and fires onClick', async () => {
    const onClick = vi.fn();
    render(<LoadMoreButton onClick={onClick} isFetching={false} />);
    const button = screen.getByRole('button', { name: '더보기' });
    await userEvent.click(button);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('accepts a custom label', () => {
    render(<LoadMoreButton onClick={vi.fn()} isFetching={false} label="더 보기" />);
    expect(screen.getByRole('button', { name: '더 보기' })).toBeInTheDocument();
  });

  it('swaps the label and disables while fetching', () => {
    render(<LoadMoreButton onClick={vi.fn()} isFetching label="더보기" testId="x-load-more" />);
    const button = screen.getByTestId('x-load-more');
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent('불러오는 중...');
  });
});
