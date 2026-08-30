/**
 * Local sign-in screen — reached only when `runtimeConfig.authMode === 'local'`.
 *
 * ⚠️ In `oidc` mode this route is never navigated to: `RequireAuth` still redirects
 * to the IdP. Registering it unconditionally is deliberate — a route that exists
 * but is unreachable is harmless, whereas conditional route registration would make
 * the router's shape depend on runtime config and break the app-level seal that
 * derives the registered route set by reading `app.tsx`.
 *
 * ⚠️ **No Korean string literal may appear in this file.** Copy comes from
 * `auth.local.*` in both locales; a repo-wide gate (`test_frontend_i18n_parity.py`)
 * fails on an inline Hangul UI literal and on any key present in one locale only.
 */
import { useCallback, useState, type FormEvent, type ReactElement } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { LocalLoginError, localLogin, resolveLoginDestination } from '@/auth/local-login';
import { applyTokenSet } from '@/auth/session';
import { useT } from '@/i18n';
import { Button, FieldGroup } from '@/ui';
import { liveRegionProps } from '@/ui/live-region';

interface LoginLocationState {
  readonly returnTo?: string;
}

interface RenderedFailure {
  readonly key: string;
  readonly params?: Readonly<Record<string, string | number>>;
}

/**
 * Map a failure to the message the tester should read.
 *
 * ⚠️ `throttled` carries the wait. Storing `Retry-After` and then not showing it
 * would leave the stated defect open: the reason this kind exists is that a
 * message with no wait invites the immediate retry that keeps the window pinned.
 * When the header is absent the generic variant is used rather than rendering
 * "wait undefined seconds".
 */
function renderedFailure(error: unknown): RenderedFailure {
  if (error instanceof LocalLoginError) {
    if (error.kind === 'invalid_credentials') return { key: 'auth.local.invalidCredentials' };
    if (error.kind === 'unreachable') return { key: 'auth.local.unreachable' };
    if (error.kind === 'throttled') {
      return error.retryAfterSeconds === undefined
        ? { key: 'auth.local.throttled' }
        : { key: 'auth.local.throttledWithWait', params: { seconds: error.retryAfterSeconds } };
    }
  }
  return { key: 'auth.local.unexpected' };
}

export default function LoginRoute(): ReactElement {
  const { t } = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<RenderedFailure | null>(null);

  const onSubmit = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      if (busy) return;
      setBusy(true);
      setFailure(null);
      try {
        const result = await localLogin(email, password);
        // ⚠️ `strategy: 'local'` is load-bearing, not decoration. It is persisted so
        // that after a page reload the silent refresh takes the local path; without
        // it the refresh would present a locally-issued refresh_token to the IdP,
        // fail, and sign the operator out about fifteen minutes later.
        applyTokenSet(result.tokens, { strategy: 'local' });
        const state = location.state as LoginLocationState | null;
        navigate(resolveLoginDestination(result.forcePasswordChange, state?.returnTo), {
          replace: true,
        });
      } catch (error: unknown) {
        setFailure(renderedFailure(error));
      } finally {
        setBusy(false);
      }
    },
    [busy, email, password, navigate, location.state],
  );

  return (
    <main className="login-route">
      <h1>{t('auth.local.title')}</h1>
      <p>{t('auth.local.subtitle')}</p>
      {/* ⚠️ `void` — an async submit handler returns a Promise that React
          does not consume; `no-misused-promises` rejects handing it one
          directly, and that rejection fails `npm run build`. */}
      <form
        onSubmit={(event) => {
          void onSubmit(event);
        }}
        noValidate
      >
        <FieldGroup label={t('auth.local.emailLabel')} htmlFor="login-email" required>
          <input
            id="login-email"
            name="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={busy}
          />
        </FieldGroup>

        <FieldGroup label={t('auth.local.passwordLabel')} htmlFor="login-password" required>
          <input
            id="login-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
          />
        </FieldGroup>

        {/* ⚠️ Urgency comes from `liveRegionProps`, not from a hand-written
            `role="alert"`. A failed sign-in is `inputRejected`: it answers the
            action the operator took a moment ago, on the one screen they cannot
            get past. Deciding that inline here would answer "is this urgent?"
            for the ninth time in a ninth place, and nowhere would it be written
            down — which is the rule `@/ui/live-region` exists to enforce. */}
        {failure !== null && (
          <p {...liveRegionProps('inputRejected')} className="login-route__error">
            {t(failure.key, failure.params)}
          </p>
        )}

        <Button type="submit" disabled={busy}>
          {busy ? t('auth.local.submitBusy') : t('auth.local.submit')}
        </Button>
      </form>
    </main>
  );
}
