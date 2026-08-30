import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { WorkbenchLayout } from '@/ui/WorkbenchLayout';

describe('WorkbenchLayout', () => {
  it('exposes labeled main and rail landmarks for a selected workbench', () => {
    render(
      <WorkbenchLayout
        main={<div>목록</div>}
        rail={<div>선택 항목</div>}
        mainLabel="프로젝트 목록"
        railLabel="프로젝트 상세"
        hasSelection
        testId="projects-workbench"
      />,
    );

    const layout = screen.getByTestId('projects-workbench');
    expect(layout).toHaveClass('workbench-layout', 'workbench-layout--has-rail');
    expect(layout).toHaveAttribute('data-has-selection', 'true');
    expect(screen.getByRole('main', { name: '프로젝트 목록' })).toHaveTextContent('목록');
    expect(screen.getByRole('complementary', { name: '프로젝트 상세' })).toHaveTextContent(
      '선택 항목',
    );
  });

  it('renders a single main column when no rail is supplied', () => {
    render(<WorkbenchLayout main={<div>개요</div>} mainLabel="개요" />);

    expect(screen.getByTestId('workbench-layout')).not.toHaveClass('workbench-layout--has-rail');
    expect(screen.queryByRole('complementary')).toBeNull();
    expect(screen.getByRole('main', { name: '개요' })).toHaveTextContent('개요');
  });
});
