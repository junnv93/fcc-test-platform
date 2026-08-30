import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ProjectPicker } from '@/ui/ProjectPicker';

/**
 * ProjectPicker primitive render test (project-picker-ssot, 2026-06-26).
 * Seals the presentational contract the primitive owns: label↔select wiring, the
 * leading empty placeholder option, value/onChange commit (the chosen project_id
 * is the option value — never typed), disabled gating, and the optional
 * role="status" note (present only when a status message is supplied).
 */
describe('ProjectPicker', () => {
  const OPTIONS = [
    { value: 'p-1', label: 'SM-S921U · M-001' },
    { value: 'p-2', label: 'SM-A546 · M-002' },
  ] as const;

  function renderPicker(overrides: Partial<Parameters<typeof ProjectPicker>[0]> = {}) {
    const onChange = vi.fn();
    const { container } = render(
      <ProjectPicker
        label="프로젝트"
        selectId="proj"
        selectTestId="proj-select"
        value=""
        onChange={onChange}
        options={OPTIONS}
        placeholderLabel="프로젝트를 선택하세요…"
        {...overrides}
      />,
    );
    return { onChange, container };
  }

  it('wires label↔select and renders the placeholder + every option', () => {
    renderPicker();
    const select = screen.getByLabelText('프로젝트');
    expect(select).toHaveAttribute('id', 'proj');
    expect(select.tagName).toBe('SELECT');
    expect(screen.getByRole('option', { name: '프로젝트를 선택하세요…' })).toHaveValue('');
    expect(screen.getByRole('option', { name: 'SM-S921U · M-001' })).toHaveValue('p-1');
    expect(screen.getByRole('option', { name: 'SM-A546 · M-002' })).toHaveValue('p-2');
  });

  it('commits the chosen project_id (the value, not typed text)', async () => {
    const { onChange } = renderPicker();
    await userEvent.selectOptions(screen.getByTestId('proj-select'), 'p-2');
    expect(onChange).toHaveBeenCalledWith('p-2');
  });

  it('disables the select and shows a status note when supplied', () => {
    renderPicker({
      disabled: true,
      statusMessage: '프로젝트 불러오는 중…',
      statusTestId: 'proj-status',
    });
    expect(screen.getByTestId('proj-select')).toBeDisabled();
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent('프로젝트 불러오는 중…');
    expect(status).toHaveAttribute('data-testid', 'proj-status');
  });

  it('omits the status region when no message is supplied', () => {
    renderPicker();
    expect(screen.queryByRole('status')).toBeNull();
  });

  // ── S16 · 접근성 계약 (W3-B M-C) ───────────────────────────────────────────
  //
  // 계약 M6 은 "접근성 후퇴 절대 금지"이고, 그 구체적 의미는 검색을 얹으면서
  // native `<select>` 를 커스텀 ARIA combobox 로 **바꾸지 않았다**는 것이다.
  // 주석이 아니라 구조를 봉인한다 — 주석은 문구 다듬기에 깨지고, `role="combobox"`
  // 는 실제로 나타나면 반드시 APG 전량 구현을 요구하는 관측 가능한 신호다.
  describe('S16 — search composition keeps the native select contract', () => {
    const SEARCH = {
      inputId: 'proj-search',
      label: '프로젝트 검색',
      placeholder: '모델명 · 관리번호',
      value: '',
      onChange: vi.fn(),
      testId: 'proj-search-input',
    } as const;

    it('renders no search box at all when `search` is absent (pre-W3-B markup)', () => {
      renderPicker();
      expect(screen.queryByRole('searchbox')).toBeNull();
      expect(screen.queryByLabelText('프로젝트 검색')).toBeNull();
    });

    it('composes the search box ABOVE the select — two separately labelled controls', () => {
      const { container } = renderPicker({ search: { ...SEARCH, onChange: vi.fn() } });

      const search = screen.getByLabelText('프로젝트 검색');
      const select = screen.getByLabelText('프로젝트');
      // 각 컨트롤이 자기 label 로 조회된다 = label↔control 배선이 둘 다 성립.
      expect(search.tagName).toBe('INPUT');
      expect(select.tagName).toBe('SELECT');
      // id 충돌은 곧 label 오배선이다(한 label 이 두 노드를 가리키는 상태).
      expect(search).toHaveAttribute('id', 'proj-search');
      expect(select).toHaveAttribute('id', 'proj');
      expect(search.getAttribute('id')).not.toBe(select.getAttribute('id'));
      // 검색이 select **위**에 온다(DOM 순서 = 읽기 순서 = 탭 순서).
      expect(search.compareDocumentPosition(select)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
      // 커스텀 combobox 로 갈아타지 않았다는 구조적 증거.
      expect(container.querySelector('[role="combobox"]')).toBeNull();
      expect(container.querySelector('[aria-activedescendant]')).toBeNull();
      expect(container.querySelector('[aria-expanded]')).toBeNull();
    });

    it('reports every keystroke to the container (draft is not swallowed)', async () => {
      const onSearchChange = vi.fn();
      renderPicker({ search: { ...SEARCH, onChange: onSearchChange } });
      await userEvent.type(screen.getByTestId('proj-search-input'), 'S');
      expect(onSearchChange).toHaveBeenCalledWith('S');
    });

    it('never disables the search box, even while the select is disabled', () => {
      // 로딩/에러로 select 가 잠기는 동안에도 검색은 살아 있어야 한다 — 그 순간이
      // 바로 사용자가 목록을 좁히려고 타이핑하는 순간이기 때문이다.
      renderPicker({
        disabled: true,
        search: { ...SEARCH, value: 'SM', onChange: vi.fn() },
      });
      const search = screen.getByTestId('proj-search-input');
      expect(search).toBeEnabled();
      expect(search).toHaveValue('SM');
      expect(screen.getByTestId('proj-select')).toBeDisabled();
    });
  });
});
