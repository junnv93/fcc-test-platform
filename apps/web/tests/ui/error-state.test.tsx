import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ERROR_VARIANT_CONTRACT, ERROR_VARIANTS } from '@/ui/error-variants';
import { describeApiError } from '@/ui/errors';
import { ErrorState } from '@/ui/ErrorState';

describe('ErrorState', () => {
  it('renders role=alert with the supplied message', () => {
    render(<ErrorState message="서버에 연결할 수 없습니다." />);
    expect(screen.getByRole('alert')).toHaveTextContent('서버에 연결할 수 없습니다.');
  });

  it('renders optional mono details block', () => {
    render(<ErrorState message="m" details="request_id=abc-123" />);
    expect(screen.getByTestId('error-state-details')).toHaveTextContent('request_id=abc-123');
  });

  it('omits the details block when absent', () => {
    render(<ErrorState message="m" />);
    expect(screen.queryByTestId('error-state-details')).toBeNull();
  });

  it('integrates with describeApiError taxonomy (display-only contract)', () => {
    const error = Object.assign(new Error('boom'), { status: 403 });
    render(<ErrorState message={describeApiError(error, 'platform')} />);
    // Phase L (§4): renders the generic forbidden copy, never the raw token.
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent(/권한/);
    expect(alert).not.toHaveTextContent('platform:read');
  });

  it('adds the domain hint + a recovery control for a variant (§M8.6)', () => {
    const onRecover = vi.fn();
    render(
      <ErrorState
        message="계측기 응답이 없습니다."
        variant="instrument-offline"
        onRecover={onRecover}
      />,
    );
    expect(screen.getByTestId('error-state-hint')).toHaveTextContent(/LAN|GPIB/);
    const recover = screen.getByTestId('error-state-recover');
    expect(recover).toBeInTheDocument();
    fireEvent.click(recover);
    expect(onRecover).toHaveBeenCalledTimes(1);
  });

  it('explains WHY there is no action rather than leaving a dead end', () => {
    render(
      <ErrorState
        message="권한이 없습니다."
        variant="forbidden"
        noActionReason="프로젝트 담당자만 역할을 부여할 수 있습니다."
      />,
    );
    expect(screen.queryByTestId('error-state-actions')).toBeNull();
    expect(screen.getByTestId('error-state-no-action')).toHaveTextContent('담당자');
  });

  it('makes a recovery-less variant a COMPILE error, not a silent dead end (§M8.6)', () => {
    // The assertion here is `@ts-expect-error` itself: it INVERTS, failing
    // `npm run typecheck` the moment `<ErrorState variant>` without one of
    // onRecover / action / noActionReason starts compiling again. A runtime
    // check could never catch this — the offending route would simply render
    // a classified failure with nowhere for the operator to go.
    // @ts-expect-error — variant demands one of onRecover / action / noActionReason
    const deadEnd = <ErrorState message="m" variant="dccf-missing" />;
    expect(deadEnd).toBeTruthy();
  });

  it('makes a render-nothing custom action a COMPILE error too (§M8.6)', () => {
    // `NonNullable<ReactNode>` was not enough: it still admits `false`, `''`,
    // `0` and `[]`, all of which render nothing. `action={isAdmin && <Btn/>}`
    // is how that reaches production — `false` for exactly the operator who
    // most needs a next move. `ReactElement` rejects each of them at compile
    // time, and every `@ts-expect-error` below INVERTS: it fails typecheck the
    // moment the render-nothing value starts being accepted again.

    // @ts-expect-error — `false` renders nothing; a variant demands a real control
    const shortCircuit = <ErrorState message="m" variant="forbidden" action={false} />;
    // @ts-expect-error — an empty string renders nothing
    const emptyText = <ErrorState message="m" variant="forbidden" action={''} />;
    // @ts-expect-error — an empty array renders nothing
    const emptyList = <ErrorState message="m" variant="forbidden" action={[]} />;
    expect([shortCircuit, emptyText, emptyList].every(Boolean)).toBe(true);
  });

  it('accepts a custom recovery control in place of a handler', () => {
    render(
      <ErrorState
        message="Duty 측정이 없습니다."
        variant="dccf-missing"
        action={<a href="/test-plans">Pre-test 생성</a>}
      />,
    );
    expect(screen.getByTestId('error-state-actions')).toHaveTextContent('Pre-test 생성');
  });

  it('still accepts a fragment so multi-control recoveries are not blocked', () => {
    // Guards against over-narrowing: `ReactElement` must keep the legitimate
    // "two ways out" shape compiling, otherwise the tightening would push
    // routes back toward a single button they cannot express.
    render(
      <ErrorState
        message="장비에 연결할 수 없습니다."
        variant="instrument-offline"
        action={
          <>
            <button type="button">재시도</button>
            <a href="/chambers">챔버 설정</a>
          </>
        }
      />,
    );
    expect(screen.getByTestId('error-state-actions')).toHaveTextContent('챔버 설정');
  });

  it('stays plain when no variant is chosen (additive contract)', () => {
    render(<ErrorState message="m" />);
    expect(screen.queryByTestId('error-state-hint')).toBeNull();
    expect(screen.getByRole('alert')).not.toHaveAttribute('data-variant');
  });

  it('exposes every declared variant through the contract map', () => {
    for (const variant of ERROR_VARIANTS) {
      expect(ERROR_VARIANT_CONTRACT[variant].recoveryToken).toMatch(/^ui\.errorState\./u);
      expect(ERROR_VARIANT_CONTRACT[variant].hintToken).toMatch(/^ui\.errorState\./u);
    }
  });
});
