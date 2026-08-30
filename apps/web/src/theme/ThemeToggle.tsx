import { useT } from '@/i18n';
import { useTheme } from '@/theme';
import { Button } from '@/ui';

/**
 * Header light/dark theme toggle (tester-ux redesign Phase T).
 *
 * Display-only control over the {@link useTheme} store: flips the applied theme
 * and persists the choice (chamber-room dark is opt-in over the light default).
 * The icon is decorative (`aria-hidden`); the accessible name + visible label
 * route through i18n, and `aria-pressed` exposes the current state to AT.
 */
export function ThemeToggle(): JSX.Element {
  const { t } = useT();
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === 'dark';
  return (
    <Button
      type="button"
      variant="ghost"
      className="theme-toggle"
      data-testid="theme-toggle"
      onClick={toggleTheme}
      aria-label={t('routes.layout.themeToggle.ariaLabel')}
      aria-pressed={isDark}
      title={
        isDark ? t('routes.layout.themeToggle.toLight') : t('routes.layout.themeToggle.toDark')
      }
    >
      <span className="theme-toggle__icon" aria-hidden="true">
        {isDark ? '☾' : '☀'}
      </span>
      <span className="theme-toggle__label">
        {isDark ? t('routes.layout.themeToggle.dark') : t('routes.layout.themeToggle.light')}
      </span>
    </Button>
  );
}
