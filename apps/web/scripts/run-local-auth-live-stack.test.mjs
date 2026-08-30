import { describe, expect, it } from 'vitest';

import {
  AUTH_PAIRING_SCRIPT,
  CENTRAL_COMPOSE_FILE,
  PAIRING_ENV_STREAM,
  LOCAL_AUTH_COMPOSE_FILE,
  SHARED_LIVE_RUNNER,
  assertBootstrapAudit,
  assertLoopbackBrowserOrigin,
  assertNoCallerSuppliedInputs,
  buildComposeExecArgs,
  buildComposeArgs,
  buildLaneImageCensusCommands,
  buildLaneImageReferences,
  buildProjectCensusCommands,
  parseBootstrapAudit,
  buildStackEnvironment,
  censusFromOutputs,
  censusIsEmpty,
  laneImageCensusFromOutputs,
  laneImageCensusIsEmpty,
  chooseNetworkProfile,
  generateCredentials,
  migrationExitCode,
  networkProfileFor,
  redactSecrets,
  runCommand,
  validateMergedComposeModel,
  validateServedRuntimeConfig,
  withTeardown,
} from './run-local-auth-live-stack.mjs';

describe('local-auth live stack command ownership', () => {
  it('composes only the central file and disposable additive override', () => {
    expect(buildComposeArgs('fcc-local-auth-live-test', 'config')).toEqual([
      'compose',
      '--project-name',
      'fcc-local-auth-live-test',
      '--file',
      CENTRAL_COMPOSE_FILE,
      '--file',
      LOCAL_AUTH_COMPOSE_FILE,
      'config',
      '--format',
      'json',
    ]);
  });

  it('uses wait, project-scoped teardown, and optional explicit build', () => {
    const up = buildComposeArgs('fcc-local-auth-live-test', 'up', {
      build: true,
      waitTimeoutSeconds: 17,
    });
    expect(up).toContain('--wait');
    expect(up).toContain('--wait-timeout');
    expect(up).toContain('17');
    expect(up).toContain('--build');

    const down = buildComposeArgs('fcc-local-auth-live-test', 'down');
    expect(down).toEqual(expect.arrayContaining(['down', '--volumes', '--remove-orphans']));
  });

  it('audits only resources carrying the generated Compose project label', () => {
    expect(buildProjectCensusCommands('fcc-local-auth-live-test')).toEqual([
      [
        'containers',
        expect.arrayContaining([
          '--filter',
          'label=com.docker.compose.project=fcc-local-auth-live-test',
        ]),
      ],
      [
        'volumes',
        expect.arrayContaining([
          '--filter',
          'label=com.docker.compose.project=fcc-local-auth-live-test',
        ]),
      ],
      [
        'networks',
        expect.arrayContaining([
          '--filter',
          'label=com.docker.compose.project=fcc-local-auth-live-test',
        ]),
      ],
    ]);
  });

  it('audits lane-built images by exact generated references, never shared tags', () => {
    const references = buildLaneImageReferences('fcc-local-auth-live-test');
    expect(references).toEqual({
      api: 'fcc-local-auth-live-api:fcc-local-auth-live-test',
      web: 'fcc-local-auth-live-web:fcc-local-auth-live-test',
    });
    expect(buildLaneImageCensusCommands(references)).toEqual([
      ['api', expect.arrayContaining(['--filter', `reference=${references.api}`])],
      ['web', expect.arrayContaining(['--filter', `reference=${references.web}`])],
    ]);
    expect(Object.values(references).every((reference) => !reference.endsWith(':latest'))).toBe(
      true,
    );
  });

  it('uses a Compose exec query without putting a password in its arguments', () => {
    const args = buildComposeExecArgs('fcc-local-auth-live-test', 'postgres', [
      'sh',
      '-c',
      'PGPASSWORD="$POSTGRES_PASSWORD" exec psql --no-password',
    ]);
    expect(args).toEqual(expect.arrayContaining(['exec', '--no-TTY', 'postgres']));
    expect(args.join(' ')).not.toContain('fcc-dev-password');
  });
});

describe('local-auth live stack safety boundaries', () => {
  it('redacts credentials, DSNs, bearer values, and credential-shaped diagnostics', () => {
    const secret = 'generated-password';
    const output = redactSecrets(
      `password=${secret} postgres://user:${secret}@127.0.0.1:5432/db ` +
        `Authorization: Bearer ${secret} FCC_PLATFORM_LOCAL_JWT_SECRET=${secret}`,
      [secret],
    );
    expect(output).not.toContain(secret);
    expect(output).toContain('[redacted]');
  });

  it('rejects deployment inputs, caller credentials, and evidence paths', () => {
    expect(() =>
      assertNoCallerSuppliedInputs({ FCC_CENTRAL_DB_URL: 'postgresql://prod/db' }),
    ).toThrow('FCC_CENTRAL_DB_URL');
    expect(() =>
      assertNoCallerSuppliedInputs({ FCC_PLATFORM_LOCAL_JWT_SECRET: 'caller-secret' }),
    ).toThrow('FCC_PLATFORM_LOCAL_JWT_SECRET');
    expect(() => assertNoCallerSuppliedInputs({ COMPOSE_PROJECT_NAME: 'fcc-central' })).toThrow(
      'COMPOSE_PROJECT_NAME',
    );
    expect(() =>
      assertNoCallerSuppliedInputs({ REPORT_PATH: '.claude/evidence/live.log' }),
    ).toThrow('REPORT_PATH');
    expect(() => assertNoCallerSuppliedInputs({ FCC_PLAYWRIGHT_RUNTIME: 'docker-server' })).toThrow(
      'FCC_PLAYWRIGHT_RUNTIME',
    );
    expect(() =>
      assertNoCallerSuppliedInputs({ SHARED_DATABASE_URL: 'postgresql://shared/db' }),
    ).toThrow('SHARED_DATABASE_URL');
  });

  it('accepts only the loopback HTTP browser origin', () => {
    expect(assertLoopbackBrowserOrigin('http://127.0.0.1:4173')).toBe('http://127.0.0.1:4173');
    expect(() => assertLoopbackBrowserOrigin('https://127.0.0.1:4173')).toThrow();
    expect(() => assertLoopbackBrowserOrigin('http://10.0.0.5:4173')).toThrow();
    expect(() => assertLoopbackBrowserOrigin('http://user:pass@127.0.0.1:4173')).toThrow();
  });

  it('chooses a private subnet that does not overlap existing Docker networks', () => {
    expect(networkProfileFor(23)).toEqual({
      subnet: '10.240.23.0/24',
      dynamicRange: '10.240.23.128/25',
      proxyIp: '10.240.23.10',
    });
    expect(chooseNetworkProfile(['10.240.23.0/24'], [23, 24]).subnet).toBe('10.240.24.0/24');
  });
});

describe('local-auth tuple and readiness contracts', () => {
  it('generates one exact local-auth tuple for both APIs', () => {
    const credentials = generateCredentials();
    const env = buildStackEnvironment({
      credentials,
      ports: { web: 4173, postgres: 55432, headless: 58001, platform: 58002, keycloak: 58080 },
      network: networkProfileFor(31),
      projectName: 'fcc-local-auth-live-test',
    });
    expect(env).toMatchObject({
      FCC_PLATFORM_AUTH_MODE: 'local_jwt',
      FCC_HEADLESS_AUTH_MODE: 'local_jwt',
      WEB_AUTH_MODE: 'local',
      ALLOW_INSECURE_TRANSPORT: 'true',
      FCC_PLATFORM_LOCAL_JWT_ISSUER: env.FCC_HEADLESS_LOCAL_JWT_ISSUER,
      FCC_PLATFORM_LOCAL_JWT_AUDIENCE: env.FCC_HEADLESS_LOCAL_JWT_AUDIENCE,
      FCC_PLATFORM_LOCAL_JWT_TTL_SECONDS: env.FCC_HEADLESS_LOCAL_JWT_TTL_SECONDS,
      FCC_PLATFORM_LOCAL_JWT_REFRESH_TTL_SECONDS: env.FCC_HEADLESS_LOCAL_JWT_REFRESH_TTL_SECONDS,
      FCC_PLATFORM_LOCAL_JWT_SECRET: env.FCC_HEADLESS_LOCAL_JWT_SECRET,
      FCC_PLATFORM_BOOTSTRAP_ADMIN_EMAIL: credentials.email,
      FCC_PLATFORM_BOOTSTRAP_ADMIN_PASSWORD: credentials.initialPassword,
      FCC_LOCAL_AUTH_LIVE_API_IMAGE: 'fcc-local-auth-live-api:fcc-local-auth-live-test',
      FCC_LOCAL_AUTH_LIVE_WEB_IMAGE: 'fcc-local-auth-live-web:fcc-local-auth-live-test',
    });
  });

  it('requires the served web runtime to report local auth and insecure loopback transport', () => {
    expect(
      validateServedRuntimeConfig(
        "window.__FCC_RUNTIME_CONFIG__ = { authMode: 'local', insecureTransportAllowed: true };",
      ),
    ).toBe(true);
    expect(() =>
      validateServedRuntimeConfig(
        "window.__FCC_RUNTIME_CONFIG__ = { authMode: 'oidc', insecureTransportAllowed: true };",
      ),
    ).toThrow('authMode=local');
  });

  it('recognizes a completed central migration only at exit code zero', () => {
    expect(migrationExitCode([{ Service: 'central-migrate', ExitCode: 0 }])).toBe(0);
    expect(migrationExitCode([{ Service: 'central-migrate', ExitCode: 1 }])).toBe(1);
    expect(migrationExitCode([{ Service: 'web', ExitCode: 0 }])).toBeNull();
  });

  it('has an empty census only when all project resource kinds are empty', () => {
    const census = censusFromOutputs({ containers: '\n', volumes: '', networks: '  \n' });
    expect(censusIsEmpty(census)).toBe(true);
    expect(censusIsEmpty({ ...census, containers: ['container-id'] })).toBe(false);
  });

  it('keeps lane image census separate from shared project-label resources', () => {
    const references = buildLaneImageReferences('fcc-local-auth-live-test');
    const census = laneImageCensusFromOutputs(
      {
        api: `sha256:api|${references.api}\n`,
        web: '',
      },
      references,
    );
    expect(laneImageCensusIsEmpty(census)).toBe(false);
    expect(
      laneImageCensusIsEmpty(laneImageCensusFromOutputs({ api: '', web: '' }, references)),
    ).toBe(true);
  });

  it('requires the merged model to expose only one loopback web gateway port', () => {
    const model = {
      services: {
        postgres: { ports: [] },
        keycloak: {},
        'headless-api': {},
        'platform-api': { ports: [] },
        web: { ports: [{ host_ip: '127.0.0.1', published: 4173, target: 80, protocol: 'tcp' }] },
      },
    };
    expect(validateMergedComposeModel(model)).toEqual({ webPort: 4173 });
    expect(() =>
      validateMergedComposeModel({
        services: {
          ...model.services,
          'platform-api': { ports: [{ host_ip: '127.0.0.1', published: 8002, target: 8002 }] },
        },
      }),
    ).toThrow('platform-api');
    expect(() =>
      validateMergedComposeModel({
        services: {
          ...model.services,
          web: {
            ports: [
              { host_ip: '127.0.0.1', published: 4173, target: 80 },
              { host_ip: '127.0.0.1', published: 4173, target: 80 },
            ],
          },
        },
      }),
    ).toThrow('exactly one web gateway port');

    const references = buildLaneImageReferences('fcc-local-auth-live-test');
    const imageModel = {
      services: {
        ...model.services,
        'central-migrate': { image: references.api },
        'headless-api': { image: references.api },
        'platform-api': { ports: [], image: references.api },
        web: { ...model.services.web, image: references.web },
      },
    };
    expect(validateMergedComposeModel(imageModel, references)).toEqual({ webPort: 4173 });
    expect(() => validateMergedComposeModel(model, references)).toThrow('generated lane API image');
  });

  it('keeps bootstrap audits to safe fields and proves the post-login session advanced', () => {
    const pre = parseBootstrapAudit(
      'user-1|operator@example.invalid|t|t|t|0\n',
      '1\n',
      'operator@example.invalid',
      'pre-browser',
    );
    expect(assertBootstrapAudit(pre, { expectedForcePasswordChange: true }).sessionVersion).toBe(0);
    const post = parseBootstrapAudit(
      'user-1|operator@example.invalid|t|t|f|1\n',
      '1\n',
      'operator@example.invalid',
      'post-browser',
    );
    expect(
      assertBootstrapAudit(post, {
        expectedForcePasswordChange: false,
        previousSessionVersion: pre.sessionVersion,
      }).sessionVersion,
    ).toBe(1);
    expect(JSON.stringify(pre)).not.toContain('password_hash');
  });

  it('parses PostgreSQL text booleans as well as psql-style t/f booleans', () => {
    const audit = parseBootstrapAudit(
      'user-1|operator@example.invalid|true|true|false|1\n',
      '1\n',
      'operator@example.invalid',
      'post-browser',
    );
    expect(audit.enabled).toBe(true);
    expect(audit.hashPresent).toBe(true);
    expect(audit.forcePasswordChange).toBe(false);
  });

  it('escalates a timeout from TERM to KILL without leaving the child running', async () => {
    const result = await runCommand(
      process.execPath,
      ['-e', 'process.on("SIGTERM", () => {}); setInterval(() => {}, 1000);'],
      // Leave enough time for the child to install its SIGTERM handler before
      // the timeout fires; the assertion is about escalation, not spawn jitter.
      { timeoutMs: 250, termGraceMs: 100, allowFailure: true },
    );
    expect(result.timedOut).toBe(true);
    expect(result.signal).toBe('SIGKILL');
  });
});

describe('local-auth live failure propagation', () => {
  it('always invokes cleanup and preserves the primary failure', async () => {
    const events = [];
    await expect(
      withTeardown(
        async () => {
          events.push('work');
          throw new Error('primary failure');
        },
        async () => {
          events.push('cleanup');
          throw new Error('cleanup failure');
        },
        { onCleanupError: (error) => events.push(error.message) },
      ),
    ).rejects.toThrow('primary failure');
    expect(events).toEqual(['work', 'cleanup', 'cleanup failure']);
  });

  it('propagates cleanup failure when the lane itself succeeded', async () => {
    await expect(
      withTeardown(
        async () => 'ok',
        async () => {
          throw new Error('cleanup failure');
        },
      ),
    ).rejects.toThrow('cleanup failure');
  });
});

describe('local-auth runner wiring', () => {
  it('keeps the required current scripts in the runner module', async () => {
    expect(AUTH_PAIRING_SCRIPT).toBe('scripts/check_auth_mode_pairing.py');
    expect(SHARED_LIVE_RUNNER).toBe('apps/web/scripts/run-live-e2e.mjs');
    expect(PAIRING_ENV_STREAM).toBe('--env-stdin');
  });
});
