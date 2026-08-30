import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { PageHeader } from '@/ui/PageHeader';

describe('PageHeader', () => {
  it('renders the title as an h1 with the supplied id', () => {
    render(<PageHeader title="개요" titleId="overview-heading" />);
    const heading = screen.getByRole('heading', { level: 1, name: '개요' });
    expect(heading).toHaveAttribute('id', 'overview-heading');
  });

  it('renders a description when supplied', () => {
    render(<PageHeader title="t" description="요약" />);
    expect(screen.getByTestId('page-header-description')).toHaveTextContent('요약');
  });

  it('omits the description block when not supplied', () => {
    render(<PageHeader title="t" />);
    expect(screen.queryByTestId('page-header-description')).toBeNull();
  });

  it('renders the actions slot when supplied', () => {
    render(<PageHeader title="t" actions={<button type="button">새로고침</button>} />);
    expect(screen.getByTestId('page-header-actions')).toContainHTML('button');
  });

  it('omits the breadcrumb nav when not supplied', () => {
    render(<PageHeader title="t" />);
    expect(screen.queryByTestId('page-header-breadcrumb')).toBeNull();
  });

  it('renders the breadcrumb in a labeled nav landmark when supplied', () => {
    render(
      <PageHeader
        title="세션 3"
        breadcrumbLabel="현재 위치"
        breadcrumb={<span aria-current="page">세션 3</span>}
      />,
    );
    const nav = screen.getByRole('navigation', { name: '현재 위치' });
    expect(nav).toHaveAttribute('data-testid', 'page-header-breadcrumb');
    expect(nav.querySelector('[aria-current="page"]')).not.toBeNull();
  });
});
