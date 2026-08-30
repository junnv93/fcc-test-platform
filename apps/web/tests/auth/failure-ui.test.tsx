import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { FAILURE_KINDS, OidcFailureView, type OidcFailureKind } from '@/auth/failure-ui';

const EXPECTED_KINDS: readonly OidcFailureKind[] = [
  'idp_config_missing',
  'token_expired',
  'idp_unreachable',
  'permission_denied',
  'backend_403',
];

describe('FAILURE_KINDS SSOT', () => {
  it('enumerates exactly the contract-mandated 5 failure kinds', () => {
    expect([...FAILURE_KINDS].sort()).toEqual([...EXPECTED_KINDS].sort());
  });
});

describe('<OidcFailureView>', () => {
  for (const kind of EXPECTED_KINDS) {
    it(`renders an accessible alert for kind=${kind}`, () => {
      render(<OidcFailureView kind={kind} />);
      const node = screen.getByTestId(`auth-failure-${kind}`);
      expect(node).toBeInTheDocument();
      expect(node).toHaveAttribute('role', 'alert');
      expect(node).toHaveAttribute('aria-live', 'assertive');
    });
  }

  it('renders the optional detail block when provided', () => {
    render(<OidcFailureView kind="permission_denied" detail="missing: platform:admin" />);
    expect(screen.getByText('missing: platform:admin')).toBeInTheDocument();
  });

  it('omits the detail block when detail is null / empty', () => {
    const { container } = render(<OidcFailureView kind="token_expired" detail={null} />);
    expect(container.querySelector('.auth-failure__detail')).toBeNull();
  });

  it('invokes onRetry when the retry button is clicked', async () => {
    const onRetry = vi.fn();
    render(<OidcFailureView kind="idp_unreachable" onRetry={onRetry} />);
    await userEvent.click(screen.getByRole('button', { name: '다시 시도' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
