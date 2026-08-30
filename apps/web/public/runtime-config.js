// AUTO-GENERATED — do not edit by hand.
// Source of truth: public/runtime-config.dev.json (single dev runtime-config SSOT).
// Regenerate: `npm run dev` (predev hook) or `node scripts/write-dev-runtime-config.mjs`.
// Drift-checked: `node scripts/write-dev-runtime-config.mjs --check` (CI) +
// vitest vite/dev-runtime-config-gen.test.ts (byte-equality vs runtime-config.dev.json).
window.__FCC_RUNTIME_CONFIG__ = {
  apiBaseUrl: 'http://localhost:5173',
  platformApiBaseUrl: null,
  wsBaseUrl: 'ws://localhost:5173',
  sessionApiEnabled: true,
  oidcIssuer: 'http://localhost:8081/realms/fcc-dev',
  oidcClientId: 'fcc-platform-frontend',
  oidcRedirectUri: 'http://localhost:5173/auth/callback',
  oidcAudience: '',
  oidcScopes: ['openid', 'profile', 'email'],
  oidcPostLogoutRedirectUri: 'http://localhost:5173/',
  insecureTransportAllowed: false,
  authMode: 'oidc',
  environmentName: 'development',
  buildVersion: '0.1.0-dev',
  buildSha256: '0000000000000000000000000000000000000000000000000000000000000000',
  sentryDsn: null,
  otelCollectorUrl: null,
  traceSampleRatio: 1,
  featureFlags: {
    providerDiagnosticMode: true,
    sessionReplay: false,
    betaResultBrowser: true,
  },
};
