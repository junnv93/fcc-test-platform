import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { RefetchRegion } from '@/ui/RefetchRegion';

describe('RefetchRegion', () => {
  it('keeps the previous content mounted while refetching (§M8.7)', () => {
    render(
      <RefetchRegion refetching>
        <p>이전 결과</p>
      </RefetchRegion>,
    );
    // The whole point: a filter change must not blank what the operator reads.
    expect(screen.getByText('이전 결과')).toBeInTheDocument();
    expect(screen.getByTestId('refetch-region')).toHaveAttribute('data-refetching', 'true');
    expect(screen.getByTestId('refetch-region-badge')).toBeInTheDocument();
  });

  it('shows no badge when idle', () => {
    render(
      <RefetchRegion refetching={false}>
        <p>이전 결과</p>
      </RefetchRegion>,
    );
    expect(screen.getByTestId('refetch-region')).toHaveAttribute('data-refetching', 'false');
    expect(screen.queryByTestId('refetch-region-badge')).toBeNull();
  });
});
