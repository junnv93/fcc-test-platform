import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DataTableSkeleton } from '@/ui/DataTableSkeleton';

describe('DataTableSkeleton', () => {
  it('renders aria-busy + role=status for assistive tech', () => {
    render(<DataTableSkeleton columns={3} rows={4} />);
    const skeleton = screen.getByTestId('data-table-skeleton');
    expect(skeleton).toHaveAttribute('role', 'status');
    expect(skeleton).toHaveAttribute('aria-busy', 'true');
  });

  it('renders rows × columns placeholder cells', () => {
    render(<DataTableSkeleton columns={3} rows={4} />);
    expect(screen.getAllByTestId('data-table-skeleton-row')).toHaveLength(4);
    expect(screen.getAllByTestId('data-table-skeleton-cell')).toHaveLength(12);
  });
});
