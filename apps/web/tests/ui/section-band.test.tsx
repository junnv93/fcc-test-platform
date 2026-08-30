import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SectionBand } from '@/ui/SectionBand';

describe('SectionBand', () => {
  it('renders the title as h2 with the supplied id', () => {
    render(<SectionBand title="큐 통계" titleId="queue-heading" />);
    const heading = screen.getByRole('heading', { level: 2, name: '큐 통계' });
    expect(heading).toHaveAttribute('id', 'queue-heading');
  });

  it('renders the meta caption when supplied', () => {
    render(<SectionBand title="t" meta="3 / 12 결과" />);
    expect(screen.getByTestId('section-band-meta')).toHaveTextContent('3 / 12 결과');
  });

  it('omits the meta caption when absent', () => {
    render(<SectionBand title="t" />);
    expect(screen.queryByTestId('section-band-meta')).toBeNull();
  });
});
