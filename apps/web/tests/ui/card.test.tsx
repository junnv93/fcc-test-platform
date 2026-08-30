import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Card } from '@/ui/Card';

describe('Card', () => {
  it('renders a named semantic surface with a typed action slot', () => {
    render(
      <Card
        as="article"
        variant="summary"
        title="프로젝트 요약"
        titleId="project-summary-title"
        actions={<button type="button">열기</button>}
        testId="project-summary"
      >
        내용
      </Card>,
    );

    const card = screen.getByTestId('project-summary');
    expect(card.tagName).toBe('ARTICLE');
    expect(card).toHaveClass('card', 'card--summary');
    expect(card).toHaveAttribute('aria-labelledby', 'project-summary-title');
    expect(screen.getByRole('heading', { level: 2, name: '프로젝트 요약' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '열기' })).toBeInTheDocument();
  });

  it('keeps a body-only surface available for dense data groups', () => {
    render(
      <Card variant="data">
        <span>측정 데이터</span>
      </Card>,
    );

    expect(screen.getByTestId('card')).toHaveClass('card--data');
    expect(screen.getByText('측정 데이터')).toBeInTheDocument();
    expect(screen.queryByRole('heading')).toBeNull();
  });
});
