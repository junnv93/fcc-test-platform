import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MetricStrip } from '@/ui/MetricStrip';

describe('MetricStrip', () => {
  it('renders each item with label + value', () => {
    render(
      <MetricStrip
        ariaLabel="큐 통계"
        items={[
          { key: 'q', label: '대기', value: 12 },
          { key: 'r', label: '실행 중', value: 3 },
        ]}
      />,
    );
    expect(screen.getByRole('region', { name: '큐 통계' })).toBeInTheDocument();
    expect(screen.getAllByTestId('metric-strip-item')).toHaveLength(2);
    expect(screen.getByText('대기')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
  });

  it('honours custom valueTestId per item', () => {
    render(
      <MetricStrip
        ariaLabel="x"
        items={[{ key: 'q', label: '대기', value: 7, valueTestId: 'stat-queued' }]}
      />,
    );
    expect(screen.getByTestId('stat-queued')).toHaveTextContent('7');
  });
});
