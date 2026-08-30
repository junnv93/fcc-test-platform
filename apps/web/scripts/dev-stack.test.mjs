import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  INFRA_COMPOSE_FILE,
  buildBackendCommands,
  buildGatewayCommand,
  buildInfraUpCommand,
  loadDevStackConfig,
  loadDevRuntimeConfig,
  resolveVenvPython,
  selectDevStackSurfaces,
  toWslPath,
} from './dev-stack.mjs';
import {
  DEFAULT_AUTH_MODE,
  JWKS_PATH_SUFFIX,
  OIDC_SURFACES,
  applyOidcDefaults,
  deriveOidcEnv,
} from './derive-oidc-env.mjs';

/**
 * Dev stack launcher seal (fe-dev-stack-launcher, 2026-06-14). The launcher and
 * vite.config.ts both consume dev-stack.config.json; these assert the launcher
 * builds correct uvicorn commands from that SSOT and that the gateway proxy
 * stays consistent with it (no port/prefix drift). The actual spawn is a manual
 * smoke (`npm run dev:stack`).
 */
const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

describe('dev-stack SSOT', () => {
  const config = loadDevStackConfig(APP_ROOT);

  it('declares the three API surfaces with backend ASGI factories', () => {
    const keys = config.surfaces.map((s) => s.key);
    expect(keys).toEqual(['session', 'headless', 'platform']);
    expect(config.surfaces.map((s) => s.uvicornFactory)).toEqual([
      'session_api_app:create_app',
      'headless_api_app:create_app',
      'platform_api_app:create_app',
    ]);
    // Only the session surface carries the WebSocket (/session/events).
    expect(config.surfaces.find((s) => s.key === 'session').ws).toBe(true);
    expect(config.surfaces.filter((s) => s.ws).map((s) => s.key)).toEqual(['session']);
  });
});

describe('buildBackendCommands', () => {
  const config = loadDevStackConfig(APP_ROOT);

  it('maps each surface to a uvicorn --factory command on its SSOT port', () => {
    const cmds = buildBackendCommands(config, { pythonBin: '/venv/python', host: '127.0.0.1' });
    expect(cmds).toHaveLength(3);
    const headless = cmds.find((c) => c.key === 'headless');
    expect(headless.pythonBin).toBe('/venv/python');
    expect(headless.args).toEqual([
      '-m',
      'uvicorn',
      '--factory',
      'headless_api_app:create_app',
      '--host',
      '127.0.0.1',
      '--port',
      '8001',
    ]);
    // Ports come only from the config (no literals in the launcher).
    for (const c of cmds) {
      const surface = config.surfaces.find((s) => s.key === c.key);
      expect(c.args).toContain(String(surface.port));
      expect(c.args).toContain(surface.uvicornFactory);
    }
  });
});

describe('selectDevStackSurfaces', () => {
  const config = loadDevStackConfig(APP_ROOT);

  it('derives a validated subset from the surface SSOT', () => {
    expect(
      selectDevStackSurfaces(config, 'headless,platform').map((surface) => surface.key),
    ).toEqual(['headless', 'platform']);
    expect(() => selectDevStackSurfaces(config, 'legacy-api')).toThrow(/Unknown/);
  });
});

describe('buildGatewayCommand', () => {
  it('derives host and port from one explicit gateway URL', () => {
    expect(buildGatewayCommand({ FCC_DEV_STACK_GATEWAY_URL: 'http://0.0.0.0:4173' })).toEqual([
      'run',
      'dev',
      '--',
      '--host',
      '0.0.0.0',
      '--port',
      '4173',
      '--strictPort',
    ]);
    expect(buildGatewayCommand({})).toEqual(['run', 'dev']);
  });
});

describe('resolveVenvPython', () => {
  it('honours FCC_PYTHON override', () => {
    expect(resolveVenvPython({ env: { FCC_PYTHON: '/custom/py' } })).toBe('/custom/py');
  });

  it('picks the platform venv interpreter when it exists', () => {
    // path.resolve may prepend a drive on Windows — assert the suffix only.
    const win = resolveVenvPython({
      platform: 'win32',
      env: {},
      repoRoot: '/repo',
      exists: () => true,
    });
    expect(win.replace(/\\/g, '/')).toMatch(/\/fcc_test_env\/Scripts\/python\.exe$/);
    const posix = resolveVenvPython({
      platform: 'linux',
      env: {},
      repoRoot: '/repo',
      exists: () => true,
    });
    expect(posix.replace(/\\/g, '/')).toMatch(/\/fcc_test_env\/bin\/python$/);
  });

  it('falls back to bare python when the venv is absent', () => {
    expect(
      resolveVenvPython({ platform: 'linux', env: {}, repoRoot: '/repo', exists: () => false }),
    ).toBe('python');
  });
});

describe('toWslPath', () => {
  it('maps a Windows drive path to its /mnt/<drive> WSL form', () => {
    expect(toWslPath('C:\\FCC_mobile_test_automation')).toBe('/mnt/c/FCC_mobile_test_automation');
    expect(toWslPath('D:/repo/sub')).toBe('/mnt/d/repo/sub');
  });

  it('leaves a POSIX path unchanged', () => {
    expect(toWslPath('/home/kmjkds/fcc')).toBe('/home/kmjkds/fcc');
  });
});

describe('buildInfraUpCommand', () => {
  it('shells into WSL Ubuntu with docker-native readiness on win32', () => {
    const cmd = buildInfraUpCommand({ platform: 'win32', repoRoot: 'C:\\repo' });
    expect(cmd.command).toBe('wsl.exe');
    const joined = cmd.args.join(' ');
    // docker-native readiness — up -d --wait (no host curl polling).
    expect(joined).toContain('docker compose -f ' + INFRA_COMPOSE_FILE + ' up -d --wait');
    // repo path converted to /mnt/<drive> so WSL can cd into it.
    expect(joined).toContain('cd /mnt/c/repo');
    // SSOT compose path, no port/realm literals.
    expect(joined).toContain('infra/docker-compose.idp.yml');
  });

  it('calls docker directly on POSIX', () => {
    const cmd = buildInfraUpCommand({ platform: 'linux', repoRoot: '/home/u/fcc' });
    expect(cmd.command).toBe('docker');
    expect(cmd.args).toEqual([
      'compose',
      '-f',
      'infra/docker-compose.idp.yml',
      'up',
      '-d',
      '--wait',
      '--wait-timeout',
      '180',
    ]);
    expect(cmd.cwd).toBe('/home/u/fcc');
  });

  it('returns null when skipped (infra managed separately)', () => {
    expect(buildInfraUpCommand({ platform: 'linux', skip: true })).toBeNull();
  });
});

describe('gateway proxy ↔ dev-stack SSOT consistency', () => {
  it('vite.config.ts derives its proxy from dev-stack.config.json (no duplicated port literals)', () => {
    const vite = readFileSync(resolve(APP_ROOT, 'vite.config.ts'), 'utf8');
    // The proxy is derived from the shared config, not inline literals.
    expect(vite).toContain('dev-stack.config.json');
    expect(vite).toContain('apiGatewayProxy');
    // The per-surface backend ports must NOT be re-hardcoded in the Vite config.
    const config = loadDevStackConfig(APP_ROOT);
    for (const s of config.surfaces) {
      expect(vite).not.toContain(`:${s.port}'`);
      expect(vite).not.toContain(`:${s.port}"`);
    }
  });
});

describe('dev-stack OIDC env derivation (single SSOT)', () => {
  const rc = loadDevRuntimeConfig(APP_ROOT);

  it('derives every backend OIDC value from runtime-config.dev.json only', () => {
    const env = deriveOidcEnv(rc);
    const jwks = `${rc.oidcIssuer}${JWKS_PATH_SUFFIX}`;
    for (const surface of OIDC_SURFACES) {
      expect(env[`FCC_${surface}_OIDC_ISSUER`]).toBe(rc.oidcIssuer);
      expect(env[`FCC_${surface}_OIDC_AUDIENCE`]).toBe(rc.oidcClientId);
      expect(env[`FCC_${surface}_OIDC_JWKS_URI`]).toBe(jwks);
    }
  });

  it('returns an empty map when the SSOT carries no IdP (no forced auth)', () => {
    expect(deriveOidcEnv({})).toEqual({});
    expect(deriveOidcEnv({ oidcIssuer: 'http://x' })).toEqual({});
  });

  it('fills OIDC defaults but never clobbers an explicit value', () => {
    const out = applyOidcDefaults({ FCC_SESSION_OIDC_ISSUER: 'http://custom' }, rc);
    expect(out.FCC_SESSION_OIDC_ISSUER).toBe('http://custom');
    expect(out.FCC_HEADLESS_OIDC_ISSUER).toBe(rc.oidcIssuer);
  });

  it('defaults each surface to oidc_jwt only when not opted out', () => {
    const out = applyOidcDefaults({ FCC_PLATFORM_ALLOW_INSECURE: '1' }, rc);
    // Opted-out surface keeps NO injected auth mode; the others go oidc_jwt.
    expect(out.FCC_PLATFORM_AUTH_MODE).toBeUndefined();
    expect(out.FCC_HEADLESS_AUTH_MODE).toBe(DEFAULT_AUTH_MODE);
    expect(out.FCC_SESSION_AUTH_MODE).toBe(DEFAULT_AUTH_MODE);
    // An explicit mode is preserved verbatim.
    const out2 = applyOidcDefaults({ FCC_SESSION_AUTH_MODE: 'trusted_headers' }, rc);
    expect(out2.FCC_SESSION_AUTH_MODE).toBe('trusted_headers');
  });
});
