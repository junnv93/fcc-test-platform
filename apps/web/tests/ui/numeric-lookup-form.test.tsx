import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { NumericLookupForm } from '@/ui/NumericLookupForm';

/**
 * NumericLookupForm primitive render test (fe-lookup-form-primitive, 2026-06-14).
 * Seals the a11y contract the primitive owns: label↔input wiring, numeric
 * inputmode, aria-invalid, submit-disabled gating, and the role="alert"
 * validation region (only present while invalid).
 */
describe('NumericLookupForm', () => {
  function renderForm(overrides: Partial<Parameters<typeof NumericLookupForm>[0]> = {}) {
    const onSubmit = vi.fn();
    const onChange = vi.fn();
    render(
      <NumericLookupForm
        label="요청 ID"
        inputId="req-id"
        inputTestId="req-input"
        value=""
        onChange={onChange}
        onSubmit={onSubmit}
        buttonLabel="조회"
        submitTestId="req-submit"
        submitDisabled={false}
        invalid={false}
        invalidMessage="양의 정수를 입력하세요."
        invalidTestId="req-invalid"
        {...overrides}
      />,
    );
    return { onSubmit, onChange };
  }

  it('wires label↔input and marks the control numeric', () => {
    renderForm();
    const input = screen.getByLabelText('요청 ID');
    expect(input).toHaveAttribute('id', 'req-id');
    expect(input).toHaveAttribute('inputmode', 'numeric');
    expect(input).toHaveAttribute('aria-invalid', 'false');
  });

  it('submits on the button and disables it when gated', async () => {
    const { onSubmit } = renderForm();
    await userEvent.click(screen.getByTestId('req-submit'));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it('disables submit + surfaces the alert region only when invalid', () => {
    renderForm({ invalid: true, submitDisabled: true });
    expect(screen.getByTestId('req-submit')).toBeDisabled();
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('양의 정수를 입력하세요.');
    expect(screen.getByLabelText('요청 ID')).toHaveAttribute('aria-invalid', 'true');
  });

  it('omits the alert region while valid', () => {
    renderForm({ invalid: false });
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
