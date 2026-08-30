import { beforeEach, describe, expect, it } from 'vitest';

import {
  __resetRuntimeConfigCacheForTests,
  getRuntimeConfig,
  RuntimeConfigError,
  runtimeConfigSchema,
} from '@/config/runtime';

const VALID_PAYLOAD = {
  apiBaseUrl: 'https://api.example.com',
  platformApiBaseUrl: null,
  wsBaseUrl: 'wss://api.example.com',
  oidcIssuer: 'https://idp.example.com',
  oidcClientId: 'fcc-platform-frontend',
  oidcRedirectUri: 'https://app.example.com/auth/callback',
  oidcAudience: '',
  oidcScopes: ['openid', 'profile', 'email'],
  oidcPostLogoutRedirectUri: 'https://app.example.com/',
  environmentName: 'production' as const,
  buildVersion: '0.1.0',
  buildSha256: 'a'.repeat(64),
  sentryDsn: null,
  otelCollectorUrl: null,
  traceSampleRatio: 0.1,
  featureFlags: {
    providerDiagnosticMode: false,
    sessionReplay: false,
    betaResultBrowser: false,
  },
};

beforeEach(() => {
  __resetRuntimeConfigCacheForTests();
  window.__FCC_RUNTIME_CONFIG__ = VALID_PAYLOAD;
});

describe('runtime config Zod schema', () => {
  it('accepts a well-formed production payload', () => {
    const result = runtimeConfigSchema.safeParse(VALID_PAYLOAD);
    expect(result.success).toBe(true);
  });

  it('rejects http:// apiBaseUrl in production', () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      apiBaseUrl: 'http://api.example.com',
    });
    expect(result.success).toBe(false);
  });

  it('accepts a dedicated https platformApiBaseUrl (split deployment) and null (reuse)', () => {
    expect(
      runtimeConfigSchema.safeParse({
        ...VALID_PAYLOAD,
        platformApiBaseUrl: 'https://platform.example.com',
      }).success,
    ).toBe(true);
    expect(
      runtimeConfigSchema.safeParse({ ...VALID_PAYLOAD, platformApiBaseUrl: null }).success,
    ).toBe(true);
  });

  it('rejects http:// platformApiBaseUrl in production', () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      platformApiBaseUrl: 'http://platform.example.com',
    });
    expect(result.success).toBe(false);
  });

  it('rejects ws:// for non-loopback wsBaseUrl', () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      wsBaseUrl: 'ws://api.example.com',
    });
    expect(result.success).toBe(false);
  });

  it('rejects invalid build sha256', () => {
    const result = runtimeConfigSchema.safeParse({ ...VALID_PAYLOAD, buildSha256: 'short' });
    expect(result.success).toBe(false);
  });

  it('rejects traceSampleRatio out of [0,1]', () => {
    const result = runtimeConfigSchema.safeParse({ ...VALID_PAYLOAD, traceSampleRatio: 2 });
    expect(result.success).toBe(false);
  });

  it('rejects unknown top-level keys (strict mode)', () => {
    const result = runtimeConfigSchema.safeParse({ ...VALID_PAYLOAD, oidcClientSecret: 'leak' });
    expect(result.success).toBe(false);
  });

  it('rejects unknown feature flag keys (strict mode)', () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      featureFlags: { ...VALID_PAYLOAD.featureFlags, undocumentedFlag: true },
    });
    expect(result.success).toBe(false);
  });

  it('accepts loopback dev URLs', () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      apiBaseUrl: 'http://127.0.0.1:8000',
      wsBaseUrl: 'ws://127.0.0.1:8000',
      oidcIssuer: 'http://localhost:8081/realms/fcc-dev',
      oidcRedirectUri: 'http://localhost:5173/auth/callback',
      oidcPostLogoutRedirectUri: 'http://localhost:5173/',
      environmentName: 'development',
    });
    expect(result.success).toBe(true);
  });

  it('accepts the Playwright Docker host-gateway alias but still rejects arbitrary HTTP hosts', () => {
    expect(
      runtimeConfigSchema.safeParse({
        ...VALID_PAYLOAD,
        apiBaseUrl: 'http://hostmachine:4173',
        wsBaseUrl: 'ws://hostmachine:4173',
        oidcIssuer: 'http://hostmachine:8081/realms/fcc-dev',
        oidcRedirectUri: 'http://hostmachine:4173/auth/callback',
        oidcPostLogoutRedirectUri: 'http://hostmachine:4173/',
        environmentName: 'development',
      }).success,
    ).toBe(true);
    expect(
      runtimeConfigSchema.safeParse({
        ...VALID_PAYLOAD,
        apiBaseUrl: 'http://docker-host.example.com:4173',
      }).success,
    ).toBe(false);
  });

  it("rejects oidcScopes missing 'openid'", () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      oidcScopes: ['profile', 'email'],
    });
    expect(result.success).toBe(false);
  });

  it('rejects empty oidcScopes array', () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      oidcScopes: [],
    });
    expect(result.success).toBe(false);
  });

  it('accepts null oidcPostLogoutRedirectUri (signOut local-only)', () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      oidcPostLogoutRedirectUri: null,
    });
    expect(result.success).toBe(true);
  });

  it('accepts empty oidcAudience (Keycloak default)', () => {
    const result = runtimeConfigSchema.safeParse({
      ...VALID_PAYLOAD,
      oidcAudience: '',
    });
    expect(result.success).toBe(true);
  });
});

describe('getRuntimeConfig() fail-fast contract', () => {
  it('returns the validated payload on success', () => {
    const config = getRuntimeConfig();
    expect(config.apiBaseUrl).toBe(VALID_PAYLOAD.apiBaseUrl);
  });

  it('caches the first successful read', () => {
    const first = getRuntimeConfig();
    // Mutate window after first call — cache should not refresh.
    window.__FCC_RUNTIME_CONFIG__ = { ...VALID_PAYLOAD, environmentName: 'staging' };
    const second = getRuntimeConfig();
    expect(second).toBe(first);
  });

  it('throws RuntimeConfigError when the window payload is missing', () => {
    __resetRuntimeConfigCacheForTests();
    delete window.__FCC_RUNTIME_CONFIG__;
    expect(() => getRuntimeConfig()).toThrow(RuntimeConfigError);
  });

  it('throws RuntimeConfigError when the payload fails validation', () => {
    __resetRuntimeConfigCacheForTests();
    window.__FCC_RUNTIME_CONFIG__ = { ...VALID_PAYLOAD, apiBaseUrl: 'not-a-url' };
    expect(() => getRuntimeConfig()).toThrow(RuntimeConfigError);
  });
});

describe('insecureTransportAllowed — explicit on-prem opt-in', () => {
  const LAN_HTTP = {
    ...VALID_PAYLOAD,
    apiBaseUrl: 'http://10.0.0.5:8080',
    wsBaseUrl: 'ws://10.0.0.5:8080',
    oidcIssuer: 'http://10.0.0.5:8081/realms/fcc-dev',
    oidcRedirectUri: 'http://10.0.0.5:8080/auth/callback',
    oidcPostLogoutRedirectUri: 'http://10.0.0.5:8080/',
  };

  it('defaults to false, so a plain-http LAN deployment still fails fast', () => {
    const result = runtimeConfigSchema.safeParse(LAN_HTTP);
    expect(result.success).toBe(false);
    if (result.success) return;
    const paths = result.error.issues.map((issue) => issue.path.join('.')).sort();
    // Every transport-bearing field must be reported, not just the first one —
    // an operator fixing them one boot at a time is the failure this prevents.
    expect(paths).toEqual([
      'apiBaseUrl',
      'oidcIssuer',
      'oidcPostLogoutRedirectUri',
      'oidcRedirectUri',
      'wsBaseUrl',
    ]);
  });

  it('accepts the same payload when the deployment declares the deviation AND runs local auth', () => {
    const result = runtimeConfigSchema.safeParse({
      ...LAN_HTTP,
      insecureTransportAllowed: true,
      authMode: 'local',
    });
    expect(result.success).toBe(true);
  });

  it('refuses the deviation while authMode is oidc (D-6, identity axis 2026-08-21)', () => {
    // ⚠️ This case USED to pass, and that was the defect. The flag only ever
    // silenced this validator — it could not give the browser back `crypto.subtle`,
    // which is what OIDC's PKCE step needs and which browsers expose only in a
    // secure context. So a deployment could set the flag, boot clean, and still be
    // unable to log in, with the flag's own name implying the opposite. Diagnosing
    // that from the resulting TypeError deep inside the PKCE path cost a day.
    //
    // Refusing at boot is the point: a warning here would be read by nobody.
    const result = runtimeConfigSchema.safeParse({
      ...LAN_HTTP,
      insecureTransportAllowed: true,
      authMode: 'oidc',
    });
    expect(result.success).toBe(false);
    if (result.success) return;
    expect(result.error.issues.map((issue) => issue.path.join('.'))).toContain('authMode');
  });

  it('defaults authMode to oidc, so the refusal covers a config that omits it', () => {
    // The flag alone, with no authMode declared, is the exact shape an operator
    // would reach for — it must not slip through on the default.
    const result = runtimeConfigSchema.safeParse({
      ...LAN_HTTP,
      insecureTransportAllowed: true,
    });
    expect(result.success).toBe(false);
  });

  it('does not relax anything else — a malformed URL still fails when enabled', () => {
    const result = runtimeConfigSchema.safeParse({
      ...LAN_HTTP,
      insecureTransportAllowed: true,
      authMode: 'local',
      apiBaseUrl: 'not-a-url',
    });
    expect(result.success).toBe(false);
  });

  it('leaves local mode over https perfectly valid', () => {
    // ⚠️ local auth is not a synonym for plaintext. TLS remains the only real fix,
    // and a deployment that has a certificate should use both.
    const result = runtimeConfigSchema.safeParse({ ...VALID_PAYLOAD, authMode: 'local' });
    expect(result.success).toBe(true);
  });

  it('keeps https deployments valid and unchanged when the flag is absent', () => {
    const result = runtimeConfigSchema.safeParse(VALID_PAYLOAD);
    expect(result.success).toBe(true);
    if (!result.success) return;
    expect(result.data.insecureTransportAllowed).toBe(false);
  });
});
