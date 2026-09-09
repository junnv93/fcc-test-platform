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
/** 아이콘·라벨은 «다음에 무엇이 오는가»가 아니라 «지금 무엇인가»를 말한다.
 *  버튼이 순환하므로 다음 상태를 적으면 누를 때마다 라벨이 한 칸 어긋나 읽힌다. */
const FACE: Record<string, { icon: string; key: string }> = {
  light: { icon: '☀', key: 'light' },
  dark: { icon: '☾', key: 'dark' },
  nord: { icon: '❄', key: 'nord' },
};

export function ThemeToggle(): JSX.Element {
  const { t } = useT();
  const { theme, toggleTheme } = useTheme();
  const face = FACE[theme] ?? FACE['light'];
  return (
    <Button
      type="button"
      variant="ghost"
      className="theme-toggle"
      data-testid="theme-toggle"
      onClick={toggleTheme}
      aria-label={t('routes.layout.themeToggle.ariaLabel')}
      data-theme-face={theme}
      title={t('routes.layout.themeToggle.next')}
    >
      <span className="theme-toggle__icon" aria-hidden="true">
        {face?.icon ?? '☀'}
      </span>
      <span className="theme-toggle__label">
        {t(`routes.layout.themeToggle.${face?.key ?? 'light'}`)}
      </span>
    </Button>
  );
}
