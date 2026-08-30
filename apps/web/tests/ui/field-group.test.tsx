import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { FieldGroup } from '@/ui/FieldGroup';
import { NumericLookupForm } from '@/ui/NumericLookupForm';

/**
 * S7/S8 — `aria-describedby` is owned by the primitive (W4-A M3).
 *
 * S7 asserts the LINK exists and resolves. S8 asserts it survives a container:
 * the W3-B M-C lesson is that a primitive test which hands in `id="a"` / `id="b"`
 * by hand stays green even when the real container derives BOTH ids to the same
 * value — so the collision has to be asserted where the ids are DERIVED, with
 * the container supplying the stems from data.
 */

/** Read the describedby tokens off a control, failing loudly when absent. */
function describedByTokens(control: HTMLElement): string[] {
  const raw = control.getAttribute('aria-describedby');
  expect(raw, 'control has no aria-describedby').not.toBeNull();
  return (raw ?? '').split(' ').filter((token) => token !== '');
}

describe('FieldGroup', () => {
  it('wires the label htmlFor to the supplied control id', () => {
    render(
      <FieldGroup label="측정 ID" htmlFor="session-input">
        <input id="session-input" />
      </FieldGroup>,
    );
    const label = screen.getByText('측정 ID');
    expect(label).toHaveAttribute('for', 'session-input');
    const input = screen.getByLabelText('측정 ID');
    expect(input).toBeInTheDocument();
  });

  it('renders help text when supplied', () => {
    render(
      <FieldGroup label="L" htmlFor="x" help="형식: UUID">
        <input id="x" />
      </FieldGroup>,
    );
    expect(screen.getByText('형식: UUID')).toBeInTheDocument();
  });

  // ---- S7 — the description is programmatically linked, not merely adjacent.

  it('links the help text to the control through aria-describedby', () => {
    render(
      <FieldGroup label="L" htmlFor="x" help="형식: UUID">
        <input id="x" />
      </FieldGroup>,
    );
    const [helpId, ...rest] = describedByTokens(screen.getByLabelText('L'));
    expect(rest).toEqual([]);
    expect(document.getElementById(helpId ?? '')).toHaveTextContent('형식: UUID');
  });

  it('links the validation message and announces it assertively', () => {
    render(
      <FieldGroup label="L" htmlFor="x" error="숫자만 입력하세요">
        <input id="x" />
      </FieldGroup>,
    );
    const [errorId] = describedByTokens(screen.getByLabelText('L'));
    const node = document.getElementById(errorId ?? '');
    expect(node).toHaveTextContent('숫자만 입력하세요');
    // The urgency comes from the live-region ruling table, not from this file.
    expect(node).toHaveAttribute('role', 'alert');
    expect(node).toHaveAttribute('aria-live', 'assertive');
  });

  it('lists help before error, and both resolve to rendered nodes', () => {
    render(
      <FieldGroup label="L" htmlFor="x" help="형식: 정수" error="숫자만 입력하세요">
        <input id="x" />
      </FieldGroup>,
    );
    const tokens = describedByTokens(screen.getByLabelText('L'));
    expect(tokens).toHaveLength(2);
    expect(document.getElementById(tokens[0] ?? '')).toHaveTextContent('형식: 정수');
    expect(document.getElementById(tokens[1] ?? '')).toHaveTextContent('숫자만 입력하세요');
  });

  it('adds no describedby when there is nothing to describe', () => {
    render(
      <FieldGroup label="L" htmlFor="x">
        <input id="x" />
      </FieldGroup>,
    );
    // A dangling describedby pointing at nothing is worse than none — screen
    // readers announce an empty description and the operator learns nothing.
    expect(screen.getByLabelText('L')).not.toHaveAttribute('aria-describedby');
  });

  it('describes the control, not whichever child happens to come first', () => {
    // `providers.tsx` and `membership.tsx` both pass siblings next to the
    // control; a positional rule would decorate the wrong node there.
    render(
      <FieldGroup label="L" htmlFor="the-control" help="형식: 정수">
        <p id="decoy">먼저 오는 형제</p>
        <input id="the-control" />
        <datalist id="the-control-options" />
      </FieldGroup>,
    );
    expect(screen.getByLabelText('L')).toHaveAttribute('aria-describedby');
    expect(document.getElementById('decoy')).not.toHaveAttribute('aria-describedby');
  });

  it('preserves a describedby the route wired itself', () => {
    render(
      <FieldGroup label="L" htmlFor="x" help="형식: 정수">
        <input id="x" aria-describedby="route-owned" />
      </FieldGroup>,
    );
    const tokens = describedByTokens(screen.getByLabelText('L'));
    expect(tokens[0]).toBe('route-owned');
    expect(tokens).toHaveLength(2);
  });

  it('marks required controls and exposes a compact density hook', () => {
    render(
      <FieldGroup label="필수 입력" htmlFor="required-field" required density="compact">
        <input id="required-field" />
      </FieldGroup>,
    );

    const input = screen.getByLabelText(/필수 입력/);
    expect(input).toHaveAttribute('aria-required', 'true');
    expect(input).not.toHaveAttribute('aria-describedby');
    expect(screen.getByTestId('field-group')).toHaveAttribute('data-density', 'compact');
    expect(screen.getByText('*')).toHaveAttribute('aria-hidden', 'true');
  });

  it('announces success and gives errors precedence over success copy', () => {
    const { rerender } = render(
      <FieldGroup label="검증" htmlFor="validated" success="사용할 수 있습니다">
        <input id="validated" />
      </FieldGroup>,
    );

    const input = screen.getByLabelText('검증');
    const [successId] = describedByTokens(input);
    expect(document.getElementById(successId ?? '')).toHaveTextContent('사용할 수 있습니다');
    expect(document.getElementById(successId ?? '')).toHaveAttribute('role', 'status');

    rerender(
      <FieldGroup
        label="검증"
        htmlFor="validated"
        success="사용할 수 있습니다"
        error="값을 확인하세요"
      >
        <input id="validated" />
      </FieldGroup>,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('값을 확인하세요');
    expect(screen.queryByText('사용할 수 있습니다')).toBeNull();
    expect(screen.getByLabelText('검증')).toHaveAttribute('aria-invalid', 'true');
  });
});

describe('FieldGroup — describedby ids survive a container (S8)', () => {
  /** A container shaped like the real ones (`my-projects`, `test-reports`,
   *  `AddRowForm` all map over a field list): the stems come from DATA, so a
   *  constant id inside the primitive collides here and only here. */
  function FieldList({ fields }: { readonly fields: readonly string[] }): JSX.Element {
    return (
      <form>
        {fields.map((field) => (
          <FieldGroup
            key={field}
            label={`라벨 ${field}`}
            htmlFor={`meta-${field}`}
            help={`도움말 ${field}`}
            error={`오류 ${field}`}
          >
            <input id={`meta-${field}`} />
          </FieldGroup>
        ))}
      </form>
    );
  }

  it('gives every field in a loop a distinct, resolvable description', () => {
    const fields = ['edition', 'grantee', 'model'];
    render(<FieldList fields={fields} />);

    const tokens = fields.flatMap((field) =>
      describedByTokens(screen.getByLabelText(`라벨 ${field}`)),
    );
    expect(tokens).toHaveLength(fields.length * 2);
    expect(new Set(tokens).size, `describedby ids collided: ${tokens.join(', ')}`).toBe(
      tokens.length,
    );

    // Distinct is not enough — each id must resolve to THAT field's copy.
    for (const field of fields) {
      const [helpId, errorId] = describedByTokens(screen.getByLabelText(`라벨 ${field}`));
      expect(document.getElementById(helpId ?? '')).toHaveTextContent(`도움말 ${field}`);
      expect(document.getElementById(errorId ?? '')).toHaveTextContent(`오류 ${field}`);
    }
  });

  it('keeps two NumericLookupForms on one screen from sharing a description', () => {
    // The real shape: `reports.tsx` renders three of these, each with its own
    // inputId, and any of them can be invalid at the same time.
    render(
      <div>
        <NumericLookupForm
          label="요청 ID"
          inputId="report-request-id"
          value="abc"
          onChange={() => undefined}
          onSubmit={() => undefined}
          buttonLabel="조회"
          submitDisabled
          invalid
          invalidMessage="요청 ID가 올바르지 않습니다"
          invalidTestId="request-id-invalid"
        />
        <NumericLookupForm
          label="측정 ID"
          inputId="artifact-session-id"
          value="xyz"
          onChange={() => undefined}
          onSubmit={() => undefined}
          buttonLabel="조회"
          submitDisabled
          invalid
          invalidMessage="측정 ID가 올바르지 않습니다"
          invalidTestId="session-id-invalid"
        />
      </div>,
    );

    const [requestId] = describedByTokens(screen.getByLabelText('요청 ID'));
    const [sessionId] = describedByTokens(screen.getByLabelText('측정 ID'));
    expect(requestId).not.toBe(sessionId);
    expect(document.getElementById(requestId ?? '')).toHaveTextContent(
      '요청 ID가 올바르지 않습니다',
    );
    expect(document.getElementById(sessionId ?? '')).toHaveTextContent(
      '측정 ID가 올바르지 않습니다',
    );
    // The route-owned test ids survived the move into the primitive.
    expect(screen.getByTestId('request-id-invalid')).toBeInTheDocument();
    expect(screen.getByTestId('session-id-invalid')).toBeInTheDocument();
  });
});
