import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { BlockSkeleton } from '@/ui/BlockSkeleton';

describe('BlockSkeleton', () => {
  it('reserves one placeholder line per requested line', () => {
    render(<BlockSkeleton lines={4} />);
    expect(screen.getAllByTestId('block-skeleton-line')).toHaveLength(4);
  });

  it('announces itself as a busy polite live region', () => {
    render(<BlockSkeleton lines={1} />);
    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-busy', 'true');
    expect(status).toHaveAttribute('aria-live', 'polite');
  });

  it('never collapses to zero lines (a 0-height placeholder reserves nothing)', () => {
    render(<BlockSkeleton lines={0} />);
    expect(screen.getAllByTestId('block-skeleton-line')).toHaveLength(1);
  });

  it('switches to the taller metric rung on request', () => {
    render(<BlockSkeleton lines={2} variant="metric" testId="metric-skeleton" />);
    expect(screen.getByTestId('metric-skeleton')).toHaveClass('block-skeleton--metric');
  });
});
