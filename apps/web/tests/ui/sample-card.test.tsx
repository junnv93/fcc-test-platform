import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { SampleCard } from '@/ui/SampleCard';

describe('SampleCard', () => {
  it('renders the 시료번호 heading and team label', () => {
    render(
      <SampleCard
        testId="sc"
        sample={{
          sample_number: '#3',
          assigned_team: 'SAR',
          test_category: 'Main Conduction',
          label_number: 'ZB00003M',
        }}
      />,
    );
    expect(screen.getByTestId('sc')).toBeInTheDocument();
    expect(screen.getByText('#3')).toBeInTheDocument();
    // team is rendered as a visible label (not color alone).
    expect(screen.getByTestId('sample-card-team')).toHaveTextContent('SAR');
  });

  it('omits the team label when no team is assigned', () => {
    render(<SampleCard sample={{ sample_number: '#1' }} />);
    expect(screen.queryByTestId('sample-card-team')).not.toBeInTheDocument();
  });

  it('renders the latest intake firmware (시험원칸) from latest_intake', () => {
    render(
      <SampleCard
        testId="sc"
        sample={{
          sample_number: '#46',
          // Compact read-back — the backend ships only the pre-selected latest
          // intake + a total count (no full history array).
          latest_intake: { sample_intake_id: 'i-new', intake_date: '2025-11-19', cp: 'CP_B' },
          intake_count: 2,
        }}
      />,
    );
    expect(screen.getByTestId('sample-card-intake')).toBeInTheDocument();
    expect(screen.getByText('CP_B')).toBeInTheDocument(); // the latest
    expect(screen.queryByText('CP_A')).not.toBeInTheDocument();
    // The history badge uses the backend-supplied intake_count (>1), not a length.
    expect(screen.getByTestId('sample-card-intake')).toHaveTextContent('2');
  });

  it('omits the 시험원칸 when no latest_intake', () => {
    render(<SampleCard sample={{ sample_number: '#1', intake_count: 0 }} />);
    expect(screen.queryByTestId('sample-card-intake')).not.toBeInTheDocument();
  });

  it('skips empty fields (no blank rows)', () => {
    render(
      <SampleCard sample={{ sample_number: '#5', label_number: null, serial_number: null }} />,
    );
    // PM section title still renders; the empty fields produce no value nodes.
    expect(screen.getByText('#5')).toBeInTheDocument();
  });
});
