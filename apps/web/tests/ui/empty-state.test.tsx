import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { EmptyState } from '@/ui/EmptyState';
import { SECTION_HEADING_LEVEL, STATE_HEADING_LEVEL } from '@/ui/heading-levels';

describe('EmptyState', () => {
  it('renders title + description with role=status', () => {
    render(<EmptyState title="조건이 없습니다" description="필터를 해제해 보세요." />);
    expect(screen.getByRole('status')).toContainElement(screen.getByText('조건이 없습니다'));
    expect(screen.getByText('필터를 해제해 보세요.')).toBeInTheDocument();
  });

  it('omits the action slot when absent', () => {
    render(<EmptyState title="t" />);
    expect(screen.queryByTestId('empty-state-action')).toBeNull();
  });

  it('renders the action slot when provided', () => {
    render(<EmptyState title="t" action={<button type="button">시작</button>} />);
    expect(screen.getByTestId('empty-state-action')).toBeInTheDocument();
  });

  it('titles at the state rung, one below the section rung (§M8.3)', () => {
    render(<EmptyState title="조건이 없습니다" />);
    // An empty SECTION must not announce a peer section in the outline.
    expect(screen.getByRole('heading', { level: STATE_HEADING_LEVEL })).toHaveTextContent(
      '조건이 없습니다',
    );
    expect(screen.queryByRole('heading', { level: SECTION_HEADING_LEVEL })).toBeNull();
  });

  it('accepts a caller-chosen rung for a page-level empty state', () => {
    render(<EmptyState title="t" headingLevel={2} />);
    expect(screen.getByRole('heading', { level: 2 })).toBeInTheDocument();
  });

  it('renders a decorative glyph so empty is not mistaken for loading (§M8.4)', () => {
    render(<EmptyState title="t" />);
    expect(screen.getByTestId('empty-state-icon')).toHaveAttribute('aria-hidden', 'true');
  });
});
