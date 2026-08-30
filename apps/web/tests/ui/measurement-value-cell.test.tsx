import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { MeasurementValueCell, splitValueUnit } from '@/ui/MeasurementValueCell';

describe('splitValueUnit (FD-C parser)', () => {
  it('splits a combined string into numeric prefix + unit', () => {
    expect(splitValueUnit('22.0 dBm')).toEqual({ number: '22.0', unit: 'dBm' });
    expect(splitValueUnit('-3.21e-2 V')).toEqual({ number: '-3.21e-2', unit: 'V' });
  });

  it('strips surrounding whitespace and accepts no-unit input', () => {
    expect(splitValueUnit('  10  ')).toEqual({ number: '10', unit: '' });
  });

  it('returns null when the string has no numeric prefix', () => {
    expect(splitValueUnit('—')).toBeNull();
    expect(splitValueUnit('Pass')).toBeNull();
    expect(splitValueUnit('')).toBeNull();
  });
});

describe('MeasurementValueCell', () => {
  it('renders number-only when given a numeric value', () => {
    render(<MeasurementValueCell value={22.0} />);
    const cell = screen.getByTestId('measurement-value');
    expect(cell.querySelector('.measurement-value__number')?.textContent).toBe('22');
    expect(cell.querySelector('.measurement-value__unit')).toBeNull();
  });

  it('splits value+unit when supplied explicitly', () => {
    render(<MeasurementValueCell value={22} unit="dBm" />);
    expect(screen.getByTestId('measurement-value-unit')).toHaveTextContent('dBm');
  });

  it('best-effort parses a combined string when no unit is given', () => {
    render(<MeasurementValueCell value="22.0 dBm" />);
    expect(screen.getByTestId('measurement-value-unit')).toHaveTextContent('dBm');
  });

  it('renders the whole string as numeric when no prefix can be parsed', () => {
    render(<MeasurementValueCell value="Pass" />);
    const cell = screen.getByTestId('measurement-value');
    expect(cell.querySelector('.measurement-value__number')?.textContent).toBe('Pass');
    expect(cell.querySelector('.measurement-value__unit')).toBeNull();
  });

  it('renders an em-dash placeholder for null/undefined/empty values', () => {
    render(<MeasurementValueCell value={null} />);
    expect(screen.getByTestId('measurement-value')).toHaveAttribute('data-empty', 'true');
    expect(screen.getByTestId('measurement-value').textContent).toContain('—');
  });
});
