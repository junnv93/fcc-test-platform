import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  DEFAULT_THEME,
  SUPPORTED_THEMES,
  THEME_STORAGE_KEY,
  __resetThemeForTest,
  getTheme,
  isSupportedTheme,
  resolveInitialTheme,
  setTheme,
  toggleTheme,
} from '@/theme';
import { ThemeToggle } from '@/theme/ThemeToggle';

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
  __resetThemeForTest('light');
});

afterEach(() => {
  __resetThemeForTest('light');
});

describe('theme store', () => {
  it('defaults to the light operator-tool theme', () => {
    expect(DEFAULT_THEME).toBe('light');
    expect(getTheme()).toBe('light');
    expect(SUPPORTED_THEMES).toEqual(['light', 'dark']);
  });

  it('setTheme applies data-theme on <html>, persists, and is reflected by getTheme', () => {
    setTheme('dark');
    expect(getTheme()).toBe('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  it('toggleTheme flips between light and dark', () => {
    toggleTheme();
    expect(getTheme()).toBe('dark');
    toggleTheme();
    expect(getTheme()).toBe('light');
  });

  it('ignores unsupported tokens (SSOT-guarded)', () => {
    setTheme('plaid' as never);
    expect(getTheme()).toBe('light');
    expect(isSupportedTheme('plaid')).toBe(false);
    expect(isSupportedTheme('dark')).toBe(true);
  });

  it('resolveInitialTheme prefers the pre-paint data-theme attribute', () => {
    document.documentElement.setAttribute('data-theme', 'dark');
    expect(resolveInitialTheme()).toBe('dark');
  });

  it('resolveInitialTheme falls back to the stored choice', () => {
    // No pre-paint attribute present → stored choice wins (the reset helper
    // applies the attribute, so clear it to exercise the localStorage path).
    document.documentElement.removeAttribute('data-theme');
    localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    expect(resolveInitialTheme()).toBe('dark');
  });
});

describe('ThemeToggle', () => {
  it('toggles the theme on click and exposes the state via aria-pressed', async () => {
    render(<ThemeToggle />);
    const btn = screen.getByTestId('theme-toggle');
    expect(btn).toHaveAttribute('aria-pressed', 'false');
    expect(btn).toHaveAccessibleName();

    await userEvent.click(btn);

    expect(getTheme()).toBe('dark');
    expect(btn).toHaveAttribute('aria-pressed', 'true');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });
});
