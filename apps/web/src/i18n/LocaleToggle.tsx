import { useT } from '@/i18n';
import { Button } from '@/ui';

/**
 * Header language toggle (English ⇄ Korean).
 *
 * The app is English-first ({@link DEFAULT_LOCALE} = 'en'); this control lets a
 * tester switch the whole UI to Korean (and back). The choice persists in
 * localStorage via `setLocale`, so it survives reloads. The icon is decorative
 * (`aria-hidden`); the accessible name + visible label route through i18n, and
 * the button label shows the language it will switch TO (standard toggle
 * affordance), matching the {@link ThemeToggle} pattern.
 */
export function LocaleToggle(): JSX.Element {
  const { t, locale, setLocale } = useT();
  const isKorean = locale === 'ko';
  const next = isKorean ? 'en' : 'ko';
  return (
    <Button
      type="button"
      variant="ghost"
      className="locale-toggle"
      data-testid="locale-toggle"
      onClick={() => setLocale(next)}
      aria-label={t('routes.layout.localeToggle.ariaLabel')}
      title={
        isKorean
          ? t('routes.layout.localeToggle.toEnglish')
          : t('routes.layout.localeToggle.toKorean')
      }
    >
      <span className="locale-toggle__icon" aria-hidden="true">
        🌐
      </span>
      <span className="locale-toggle__label" lang={next}>
        {isKorean
          ? t('routes.layout.localeToggle.english')
          : t('routes.layout.localeToggle.korean')}
      </span>
    </Button>
  );
}
