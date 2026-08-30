import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';

import { __resetRuntimeConfigCacheForTests } from '@/config/runtime';
import { AppLayout } from '@/routes/_layout';

import type { ReactElement } from 'react';

/**
 * Layout nav — session-surface gating (B1/P13 browser-integration closure).
 *
 * The Session API (`/session/*`) is a single-chamber-node surface the central
 * hub does not serve (runtime-config `sessionApiEnabled:false`). The nav must
 * then hide session-only targets (`/control`) so it never links to a surface
 * that 404s through the gateway — while keeping `/sessions` (Headless-backed
 * attempt history), which the hub serves. Sealed alongside the backend
 * coherence test (tests/test_central_docker_compose.py).
 */

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

function renderLayout(entry = '/'): void {
  const ui: ReactElement = (
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/" element={<AppLayout />}>
          <Route index element={<div>home</div>} />
          <Route path="projects" element={<div>projects</div>} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
  render(ui);
}

function setSessionApiEnabled(enabled: boolean): void {
  __resetRuntimeConfigCacheForTests();
  const base = globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__ as Record<string, unknown>;
  window.__FCC_RUNTIME_CONFIG__ = { ...base, sessionApiEnabled: enabled };
}

afterEach(() => {
  __resetRuntimeConfigCacheForTests();
  window.__FCC_RUNTIME_CONFIG__ = globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__;
});

describe('AppLayout nav session-surface gating', () => {
  it('shows the Control nav link when sessionApiEnabled is true', () => {
    setSessionApiEnabled(true);
    renderLayout();
    expect(document.querySelector('a[href="/control"]')).not.toBeNull();
    // Headless-backed routes stay regardless.
    expect(document.querySelector('a[href="/sessions"]')).not.toBeNull();
  });

  it('hides the Control nav link when sessionApiEnabled is false', () => {
    setSessionApiEnabled(false);
    renderLayout();
    expect(document.querySelector('a[href="/control"]')).toBeNull();
    // /sessions (Headless attempt history) is NOT session-only — it must stay.
    expect(document.querySelector('a[href="/sessions"]')).not.toBeNull();
    // Sanity: the nav itself still rendered.
    expect(screen.getByRole('navigation')).toBeInTheDocument();
  });

  it('exposes the /progress dashboard in the nav ahead of /projects (Phase 6 wiring)', () => {
    setSessionApiEnabled(true);
    renderLayout();
    const progressLink = document.querySelector('a[href="/progress"]');
    const projectsLink = document.querySelector('a[href="/projects"]');
    // The orphan progress route is now reachable from the nav (no more URL-only).
    expect(progressLink).not.toBeNull();
    expect(projectsLink).not.toBeNull();
    if (progressLink === null || projectsLink === null) {
      throw new Error('expected progress and projects nav links to be present');
    }
    // results 그룹 최상단: progress precedes projects in document order.
    expect(
      progressLink.compareDocumentPosition(projectsLink) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('renders a project-scoped sidebar group when the route carries ?project=', () => {
    setSessionApiEnabled(true);
    renderLayout(`/projects?project=${PROJECT_ID}`);
    expect(screen.getByTestId('nav-project-group')).toBeInTheDocument();
    expect(screen.getByTestId('nav-project-id')).toHaveTextContent(PROJECT_ID);
    expect(document.querySelector(`a[href="/projects?project=${PROJECT_ID}"]`)).not.toBeNull();
    expect(document.querySelector(`a[href="/reports?project=${PROJECT_ID}"]`)).not.toBeNull();
  });
});

/**
 * Responsive disclosure ARIA contract (operator-ux-responsive-shell). The CSS
 * shows the panel inline on desktop and collapses it behind the toggle below
 * `--bp-lg`; jsdom carries no viewport/CSS, so this seals the structural +
 * ARIA wiring (`aria-expanded`/`aria-controls`) the dropdown depends on — the
 * visual collapse itself is sealed by tests/e2e/responsive-layout.spec.ts.
 */
describe('AppLayout primary-nav disclosure', () => {
  it('wires the menu toggle to the panel via aria-controls and toggles aria-expanded', () => {
    setSessionApiEnabled(true);
    renderLayout();

    const toggle = screen.getByTestId('nav-menu-toggle');
    const panel = screen.getByTestId('nav-panel');

    // aria-controls must reference the actual panel id (assistive-tech wiring).
    expect(toggle).toHaveAttribute('aria-controls', panel.id);
    expect(panel.id).not.toBe('');

    // Collapsed by default → expanded on click → collapsed again (controlled).
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(panel).toHaveAttribute('data-open', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
  });
});
