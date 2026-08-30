import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { RunProgress } from '@/ui/RunProgress';

describe('RunProgress', () => {
  it('reports the percentage, the step and the ETA (§M8.7)', () => {
    render(
      <RunProgress label="측정 진행률" percent={42.4} step="BLE 1M · CH 0" etaSeconds={150} />,
    );
    expect(screen.getByRole('progressbar', { name: '측정 진행률' })).toHaveAttribute(
      'aria-valuenow',
      '42.4',
    );
    expect(screen.getByTestId('run-progress-step')).toHaveTextContent('BLE 1M · CH 0');
    expect(screen.getByTestId('run-progress-percent')).toHaveTextContent('42');
    expect(screen.getByTestId('run-progress-eta')).toHaveTextContent('3');
  });

  it('stays indeterminate rather than claiming 0% when no ratio is known', () => {
    render(<RunProgress label="측정 진행률" step="준비 중" />);
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(screen.queryByTestId('run-progress-eta')).toBeNull();
  });
});
