import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { StatusBadge, STATUS_KINDS } from '@/ui/StatusBadge';

describe('StatusBadge — status SSOT', () => {
  it('exposes exactly nine status kinds (SSOT cardinality)', () => {
    // 7 base kinds + draft/published lifecycle kinds (c3-status-kind).
    expect(STATUS_KINDS.length).toBe(9);
    expect(new Set(STATUS_KINDS).size).toBe(9);
  });

  it.each(STATUS_KINDS)('renders each status kind with icon + label (%s)', (status) => {
    render(<StatusBadge status={status} />);
    const badge = screen.getByTestId('status-badge');
    expect(badge).toHaveAttribute('data-status', status);
    expect(badge.className).toContain(`status-badge--${status}`);
    // Both an icon glyph (decorative) AND a label text must be present (§5.2).
    expect(badge.querySelector('.status-badge__icon')).toBeInTheDocument();
    expect(badge.querySelector('.status-badge__label')?.textContent ?? '').not.toEqual('');
  });

  // W4-A M4 — the badge is a LABEL, never a live region. It used to map
  // fail→alert / running→status, which reads fine for one badge and is a
  // defect at the cardinality badges actually have: a measurement table
  // renders one per row, so a page of failures was a page of assertive live
  // regions able to interrupt the operator on any re-render.
  it.each(STATUS_KINDS)('never announces itself as a live region (%s)', (status) => {
    render(<StatusBadge status={status} />);
    const badge = screen.getByTestId('status-badge');
    expect(badge).toHaveAttribute('role', 'note');
    expect(badge).not.toHaveAttribute('aria-live');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('keeps the verdict readable without the live region (icon + label)', () => {
    render(<StatusBadge status="fail" />);
    // The announcement was removed, not the information: the label is still
    // real text, so the failure survives for a screen reader and for anyone
    // who cannot distinguish the red token.
    expect(screen.getByTestId('status-badge').textContent ?? '').toContain('불합격');
  });

  it('honours an explicit label override', () => {
    render(<StatusBadge status="pass" label="합격 완료" />);
    expect(screen.getByText('합격 완료')).toBeInTheDocument();
  });

  it('decorative icon is aria-hidden so it is not announced twice', () => {
    render(<StatusBadge status="pass" />);
    const icon = screen.getByTestId('status-badge').querySelector('.status-badge__icon');
    expect(icon).toHaveAttribute('aria-hidden', 'true');
  });
});
