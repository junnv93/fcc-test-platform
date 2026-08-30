/**
 * Forced / voluntary password change — the one screen a pending change can reach.
 *
 * ⚠️ This route exists because the server's refusal has to be *navigable*. Every
 * other operation answers `AUTH_PASSWORD_CHANGE_REQUIRED` while a change is
 * pending; without a screen to send the operator to, that refusal is a dead end
 * on the first login of a brand-new deployment. EMS reached the same conclusion
 * (`apps/frontend/lib/auth/login-destination.ts` routes such a session to a
 * dedicated route and refuses every other destination).
 *
 * ⚠️ It is deliberately OUTSIDE `<RequireAuth>` in `app.tsx`, like `/login` and
 * `/auth/callback`. The caller IS authenticated — but sitting it under the shell
 * would render the navigation for an operator who is not allowed to use any of it.
 */
import { useCallback, useState, type FormEvent, type ReactElement } from 'react';
import { useNavigate } from 'react-router-dom';

import { LocalLoginError, localChangePassword } from '@/auth/local-login';
import { applyTokenSet, currentAccessToken } from '@/auth/session';
import { useT } from '@/i18n';
import { Button, FieldGroup } from '@/ui';
import { liveRegionProps } from '@/ui/live-region';

export default function ChangePasswordRoute(): ReactElement {
  const { t } = useT();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);

  const onSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (busy) return;
      const token = currentAccessToken();
      if (token === null) {
        // Session ended underneath this screen (another tab signed out, or the
        // token expired while the form sat open). Send them back to sign in
        // rather than posting a request that can only 403.
        navigate('/login', { replace: true });
        return;
      }
      setBusy(true);
      setErrorKey(null);
      try {
        const tokens = await localChangePassword(token, currentPassword, newPassword);
        // ⚠️ The server bumped `session_version`, so the token we arrived with is
        // already dead. Installing the fresh pair here is what keeps the operator
        // signed in instead of bouncing them back to the login screen right after
        // they did the one thing we demanded.
        applyTokenSet(tokens, { strategy: 'local' });
        navigate('/', { replace: true });
      } catch (error: unknown) {
        setErrorKey(
          error instanceof LocalLoginError && error.kind === 'invalid_credentials'
            ? 'auth.local.currentPasswordInvalid'
            : error instanceof LocalLoginError && error.kind === 'password_policy'
              ? 'auth.local.passwordChangePolicy'
              : 'auth.local.passwordChangeUnexpected',
        );
      } finally {
        setBusy(false);
      }
    },
    [busy, currentPassword, newPassword, navigate],
  );

  return (
    <main className="change-password-route">
      <h1>{t('auth.local.passwordChangeTitle')}</h1>
      <p>{t('auth.local.passwordChangeBody')}</p>
      {/* ⚠️ `void` — an async submit handler returns a Promise that React
          does not consume; `no-misused-promises` rejects handing it one
          directly, and that rejection fails `npm run build`. */}
      <form
        onSubmit={(event) => {
          void onSubmit(event);
        }}
        noValidate
      >
        <FieldGroup
          label={t('auth.local.currentPasswordLabel')}
          htmlFor="current-password"
          required
        >
          <input
            id="current-password"
            name="current_password"
            type="password"
            autoComplete="current-password"
            required
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            disabled={busy}
          />
        </FieldGroup>

        {/* `help` is stated up front rather than only on rejection — the byte
            ceiling is invisible to a Korean speaker counting characters. The
            primitive owns the `aria-describedby` wiring; doing it by hand here
            would be the twentieth copy of a link that gets forgotten once. */}
        <FieldGroup
          label={t('auth.local.newPasswordLabel')}
          htmlFor="new-password"
          help={t('auth.local.passwordRule')}
          required
        >
          <input
            id="new-password"
            name="new_password"
            type="password"
            autoComplete="new-password"
            required
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            disabled={busy}
          />
        </FieldGroup>

        {errorKey !== null && (
          <p {...liveRegionProps('inputRejected')} className="change-password-route__error">
            {t(errorKey)}
          </p>
        )}

        <Button type="submit" disabled={busy}>
          {busy ? t('auth.local.changeSubmitBusy') : t('auth.local.changeSubmit')}
        </Button>
      </form>
    </main>
  );
}
