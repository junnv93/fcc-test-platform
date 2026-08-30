import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ProgressBar } from '@/ui/ProgressBar';

/**
 * ProgressBar primitive — WAI-ARIA progressbar contract (A2 fe-progress-bar).
 *
 * Seals the role + value attributes, determinate/indeterminate behaviour,
 * tone modifier classes, and the aria-hidden visible caption. The CSS token
 * consumption + no-inline-hex is sealed separately by the Python invariant
 * `tests/test_fe_phase1_ui_foundation.py`.
 */
describe('ProgressBar — determinate', () => {
  it('exposes role=progressbar with the full value contract', () => {
    render(<ProgressBar label="진행률" valueNow={40} />);
    const bar = screen.getByRole('progressbar', { name: '진행률' });
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
    expect(bar).toHaveAttribute('aria-valuenow', '40');
    // No aria-valuetext on a determinate bar — AT derives % from valuenow.
    expect(bar).not.toHaveAttribute('aria-valuetext');
  });

  it('honours a custom min/max range for aria-valuenow', () => {
    render(<ProgressBar label="x" valueNow={5} valueMin={0} valueMax={10} />);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuemax', '10');
    expect(bar).toHaveAttribute('aria-valuenow', '5');
  });

  it('clamps the fill width within 0–100% of the range', () => {
    const { container, rerender } = render(<ProgressBar label="x" valueNow={150} valueMax={100} />);
    const fill = container.querySelector('.progress-bar__fill');
    if (!(fill instanceof HTMLElement)) throw new Error('progress-bar__fill missing');
    expect(fill.style.inlineSize).toBe('100%');
    rerender(<ProgressBar label="x" valueNow={-20} valueMax={100} />);
    expect(fill.style.inlineSize).toBe('0%');
    rerender(<ProgressBar label="x" valueNow={5} valueMin={0} valueMax={10} />);
    expect(fill.style.inlineSize).toBe('50%');
  });

  it('yields a 0% fill for a non-finite value (never NaN%)', () => {
    const { container } = render(<ProgressBar label="x" valueNow={Number.NaN} />);
    const fill = container.querySelector('.progress-bar__fill');
    if (!(fill instanceof HTMLElement)) throw new Error('progress-bar__fill missing');
    expect(fill.style.inlineSize).toBe('0%');
  });

  // aria-valuenow shares the clampValue SSOT with the fill width: a
  // NaN/Infinity/out-of-range input must never leak a NaN announcement nor an
  // aria-valuenow outside [valueMin, valueMax].
  it('clamps aria-valuenow into range (never NaN, never out of bounds)', () => {
    const { rerender } = render(<ProgressBar label="x" valueNow={Number.NaN} />);
    const read = () => screen.getByRole('progressbar').getAttribute('aria-valuenow');
    // NaN -> min (finite, in range), not "NaN".
    expect(read()).toBe('0');
    rerender(<ProgressBar label="x" valueNow={Number.POSITIVE_INFINITY} />);
    expect(read()).toBe('0');
    rerender(<ProgressBar label="x" valueNow={Number.NEGATIVE_INFINITY} />);
    expect(read()).toBe('0');
    // Over the upper bound -> clamped to valueMax.
    rerender(<ProgressBar label="x" valueNow={150} valueMax={100} />);
    expect(read()).toBe('100');
    // Under the lower bound -> clamped to valueMin.
    rerender(<ProgressBar label="x" valueNow={-20} valueMax={100} />);
    expect(read()).toBe('0');
    // Custom range, over bound -> clamped to that range's max.
    rerender(<ProgressBar label="x" valueNow={99} valueMin={0} valueMax={10} />);
    expect(read()).toBe('10');
    // Every announced value parses to a finite number within [min, max].
    const announced = Number(read());
    expect(Number.isFinite(announced)).toBe(true);
    expect(announced).toBeGreaterThanOrEqual(0);
    expect(announced).toBeLessThanOrEqual(10);
  });

  it('keeps aria-valuenow and the fill width in agreement (same SSOT)', () => {
    const { container } = render(
      <ProgressBar label="x" valueNow={150} valueMin={0} valueMax={100} />,
    );
    const fill = container.querySelector('.progress-bar__fill');
    if (!(fill instanceof HTMLElement)) throw new Error('progress-bar__fill missing');
    // value 150 clamps to 100 -> aria-valuenow 100 AND fill 100%.
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100');
    expect(fill.style.inlineSize).toBe('100%');
  });

  // Public bounds share the finiteBound sanitize SSOT: a NaN/Infinity
  // valueMin/valueMax must never leak into aria-valuemin/aria-valuemax.
  it('sanitizes a non-finite valueMin/valueMax out of the ARIA range', () => {
    const { rerender } = render(<ProgressBar label="x" valueNow={40} valueMin={Number.NaN} />);
    const bar = () => screen.getByRole('progressbar');
    // NaN valueMin -> default 0.
    expect(bar()).toHaveAttribute('aria-valuemin', '0');
    expect(bar()).toHaveAttribute('aria-valuemax', '100');
    rerender(<ProgressBar label="x" valueNow={40} valueMax={Number.POSITIVE_INFINITY} />);
    // Infinity valueMax -> default 100.
    expect(bar()).toHaveAttribute('aria-valuemax', '100');
    // Every declared bound parses to a finite number.
    expect(Number.isFinite(Number(bar().getAttribute('aria-valuemin')))).toBe(true);
    expect(Number.isFinite(Number(bar().getAttribute('aria-valuemax')))).toBe(true);
  });

  it('keeps the fill finite when bounds are non-finite', () => {
    const { container } = render(<ProgressBar label="x" valueNow={50} valueMax={Number.NaN} />);
    const fill = container.querySelector('.progress-bar__fill');
    if (!(fill instanceof HTMLElement)) throw new Error('progress-bar__fill missing');
    // valueMax NaN -> default 100, so 50/100 -> 50%.
    expect(fill.style.inlineSize).toBe('50%');
  });

  it('renders the visible caption aria-hidden so it is not double-announced', () => {
    render(<ProgressBar label="x" valueNow={40} valueText="40%" testId="pb" />);
    const caption = screen.getByTestId('pb').querySelector('.progress-bar__value');
    expect(caption).toHaveTextContent('40%');
    expect(caption).toHaveAttribute('aria-hidden', 'true');
  });

  it('applies a tone modifier class (status SSOT reuse)', () => {
    render(<ProgressBar label="x" valueNow={40} tone="running" testId="pb" />);
    expect(screen.getByTestId('pb').className).toContain('progress-bar--running');
    // The default `accent` tone adds no modifier class.
    render(<ProgressBar label="y" valueNow={40} testId="pb2" />);
    expect(screen.getByTestId('pb2').className).not.toContain('progress-bar--');
  });
});

describe('ProgressBar — indeterminate', () => {
  it('omits aria-valuenow and marks the indeterminate state', () => {
    render(<ProgressBar label="측정 중" indeterminate testId="pb" />);
    const bar = screen.getByRole('progressbar', { name: '측정 중' });
    expect(bar).not.toHaveAttribute('aria-valuenow');
    expect(bar).toHaveAttribute('aria-valuemin', '0');
    expect(bar).toHaveAttribute('aria-valuemax', '100');
    expect(screen.getByTestId('pb')).toHaveAttribute('data-state', 'indeterminate');
    expect(screen.getByTestId('pb').className).toContain('progress-bar--indeterminate');
  });

  it('treats an omitted valueNow as indeterminate', () => {
    render(<ProgressBar label="x" testId="pb" />);
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
    expect(screen.getByTestId('pb')).toHaveAttribute('data-state', 'indeterminate');
  });

  it('surfaces valueText as aria-valuetext when indeterminate', () => {
    render(<ProgressBar label="x" indeterminate valueText="측정 진행 중..." />);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '측정 진행 중...');
  });

  it('does not set an inline fill width when indeterminate', () => {
    const { container } = render(<ProgressBar label="x" indeterminate />);
    const fill = container.querySelector('.progress-bar__fill');
    if (!(fill instanceof HTMLElement)) throw new Error('progress-bar__fill missing');
    expect(fill.style.inlineSize).toBe('');
  });
});
