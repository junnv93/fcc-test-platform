import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { StatusMessage } from '@/ui/StatusMessage';

describe('StatusMessage', () => {
  it('renders an aria-live status region with the supplied message', () => {
    render(<StatusMessage message="역할을 부여했습니다." />);
    const node = screen.getByRole('status');
    expect(node).toHaveTextContent('역할을 부여했습니다.');
    expect(node).toHaveAttribute('aria-live', 'polite');
  });

  it('defaults to the success tone', () => {
    render(<StatusMessage message="m" />);
    expect(screen.getByRole('status').className).toContain('status-message--success');
  });

  it('applies the info tone when requested', () => {
    render(<StatusMessage message="m" tone="info" />);
    expect(screen.getByRole('status').className).toContain('status-message--info');
  });

  it('honors a custom testId', () => {
    render(<StatusMessage message="m" testId="claim-success" />);
    expect(screen.getByTestId('claim-success')).toBeInTheDocument();
  });
});
