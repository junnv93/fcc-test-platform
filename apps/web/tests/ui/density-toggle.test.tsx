import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { DENSITY_STORAGE_KEY } from '@/shared/density';
import { DensityToggle } from '@/ui';

/**
 * DensityToggle (design-followup #3) — header control over the `data-density`
 * token override. Verifies the SSOT round-trip (read → apply → persist) and the
 * aria-pressed contract. The pre-paint boot script is sealed separately by
 * `tests/density-boot-ssot.test.ts`.
 */

beforeEach(() => {
  window.localStorage.removeItem(DENSITY_STORAGE_KEY);
  delete document.documentElement.dataset.density;
});

afterEach(() => {
  window.localStorage.removeItem(DENSITY_STORAGE_KEY);
  delete document.documentElement.dataset.density;
});

describe('DensityToggle', () => {
  it('renders both options with comfortable pressed by default', () => {
    render(<DensityToggle />);
    expect(screen.getByRole('button', { name: '기본' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '압축' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('applies data-density=compact and persists when compact is chosen', async () => {
    render(<DensityToggle />);
    await userEvent.click(screen.getByRole('button', { name: '압축' }));
    expect(document.documentElement.dataset.density).toBe('compact');
    expect(window.localStorage.getItem(DENSITY_STORAGE_KEY)).toBe('compact');
    expect(screen.getByRole('button', { name: '압축' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('clears the attribute when comfortable is chosen again', async () => {
    render(<DensityToggle />);
    await userEvent.click(screen.getByRole('button', { name: '압축' }));
    await userEvent.click(screen.getByRole('button', { name: '기본' }));
    expect(document.documentElement.dataset.density).toBeUndefined();
    expect(window.localStorage.getItem(DENSITY_STORAGE_KEY)).toBe('comfortable');
  });

  it('hydrates from a stored compact preference', () => {
    window.localStorage.setItem(DENSITY_STORAGE_KEY, 'compact');
    render(<DensityToggle />);
    expect(screen.getByRole('button', { name: '압축' })).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement.dataset.density).toBe('compact');
  });
});
