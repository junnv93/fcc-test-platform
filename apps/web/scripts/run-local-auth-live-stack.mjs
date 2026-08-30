#!/usr/bin/env node
/**
 * Own the disposable PostgreSQL-backed local-auth live lane.
 *
 * The stack is composed from the current central services plus the additive
 * local-auth override. All credentials, host ports, network values and the
 * Compose project name are generated for one invocation and remain in process
 * memory (or the child process environment) only. The browser sees the
 * loopback web gateway; it never receives an API host port.
 */
import { randomBytes, randomInt } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import process from 'node:process';

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
export const APP_ROOT = resolve(SCRIPT_DIR, '..');
export const REPO_ROOT = resolve(APP_ROOT, '..', '..');
export const CENTRAL_COMPOSE_FILE = 'infra/docker-compose.central.yml';
export const LOCAL_AUTH_COMPOSE_FILE = 'infra/central/docker-compose.local-auth-live.yml';
export const AUTH_PAIRING_SCRIPT = 'scripts/check_auth_mode_pairing.py';
export const SHARED_LIVE_RUNNER = 'apps/web/scripts/run-live-e2e.mjs';
export const COMPOSE_WAIT_TIMEOUT_SECONDS = 180;
export const PAIRING_ENV_STREAM = '--env-stdin';
export const CENTRAL_PROJECT_NAME = 'fcc-central';

const LOOPBACK_HOST = '127.0.0.1';
const COMPOSE_TIMEOUT_SECONDS = 30;
const COMMAND_TERM_GRACE_MS = 5_000;
const LOCAL_AUTH_IDENTITY_ISSUER = 'urn:fcc:identity:local';
const POSTGRES_QUERY_SCRIPT =
  'PGPASSWORD="$POSTGRES_PASSWORD" exec psql --no-psqlrc --no-password --host=127.0.0.1 ' +
  '--username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --tuples-only --no-align ' +
  '--field-separator "|" --command "$1"';
const GENERATED_CREDENTIAL_ENV_KEYS = Object.freeze([
  'FCC_PLATFORM_LOCAL_JWT_SECRET',
  'FCC_HEADLESS_LOCAL_JWT_SECRET',
  'FCC_PLATFORM_BOOTSTRAP_ADMIN_EMAIL',
  'FCC_PLATFORM_BOOTSTRAP_ADMIN_PASSWORD',
  'FCC_LOCAL_AUTH_LIVE_EMAIL',
  'FCC_LOCAL_AUTH_LIVE_INITIAL_PASSWORD',
  'FCC_LOCAL_AUTH_LIVE_NEW_PASSWORD',
]);

const CALLER_CONTROLLED_STACK_KEYS = Object.freeze([
  'COMPOSE_FILE',
  'COMPOSE_PROJECT_NAME',
  'DOCKER_CONTEXT',
  'DOCKER_HOST',
  'FCC_CAPTURE_BROWSER_CHANNEL',
  'FCC_CAPTURE_RUNTIME',
  'FCC_DOCKER_EXECUTABLE',
  'FCC_PLAYWRIGHT_DOCKER_BASE',
  'FCC_PLAYWRIGHT_DOCKER_IMAGE',
  'FCC_PLAYWRIGHT_DOCKER_SERVER_PORT',
  'FCC_PLAYWRIGHT_DOCKER_HOST_ALIAS',
  'FCC_PLAYWRIGHT_RUNTIME',
  'E2E_BASE_URL',
  'E2E_WEB_SERVER_URL',
  'E2E_WEB_SERVER_HOST',
  'PW_TEST_CONNECT_WS_ENDPOINT',
  'FCC_CENTRAL_DB_URL',
  'FCC_CENTRAL_LIVE_PROOF_BUNDLE',
  'FCC_HEADLESS_DB_PATH',
  'FCC_HEADLESS_AUTH_MODE',
  'FCC_HEADLESS_LOCAL_JWT_AUDIENCE',
  'FCC_HEADLESS_LOCAL_JWT_ISSUER',
  'FCC_HEADLESS_LOCAL_JWT_REFRESH_TTL_SECONDS',
  'FCC_HEADLESS_LOCAL_JWT_SECRET',
  'FCC_HEADLESS_LOCAL_JWT_TTL_SECONDS',
  'FCC_PLATFORM_AUTH_MODE',
  'FCC_PLATFORM_BOOTSTRAP_ADMIN_EMAIL',
  'FCC_PLATFORM_BOOTSTRAP_ADMIN_PASSWORD',
  'FCC_PLATFORM_LOCAL_JWT_AUDIENCE',
  'FCC_PLATFORM_LOCAL_JWT_ISSUER',
  'FCC_PLATFORM_LOCAL_JWT_REFRESH_TTL_SECONDS',
  'FCC_PLATFORM_LOCAL_JWT_SECRET',
  'FCC_PLATFORM_LOCAL_JWT_TTL_SECONDS',
  'FCC_SESSION_EXCEL_PATH',
  'FCC_LOCAL_AUTH_LIVE_PROJECT',
  'FCC_LOCAL_AUTH_LIVE_CONTAINER_PREFIX',
  'FCC_LOCAL_AUTH_LIVE_API_IMAGE',
  'FCC_LOCAL_AUTH_LIVE_WEB_IMAGE',
  'FCC_LOCAL_AUTH_LIVE_EMAIL',
  'FCC_LOCAL_AUTH_LIVE_INITIAL_PASSWORD',
  'FCC_LOCAL_AUTH_LIVE_NEW_PASSWORD',
  'PUBLIC_HOST',
  'WEB_AUTH_MODE',
  'WEB_PORT',
  'HEADLESS_API_PORT',
  'PLATFORM_API_PORT',
  'KEYCLOAK_PORT',
  'POSTGRES_PORT',
  'POSTGRES_PASSWORD',
  'POSTGRES_USER',
  'POSTGRES_DB',
  'CENTRAL_PROXY_IP',
  'CENTRAL_APP_SUBNET',
  'CENTRAL_APP_DYNAMIC_RANGE',
  'ALLOW_INSECURE_TRANSPORT',
]);

let activeChild = null;
let activeChildTerminate = null;
let requestedSignal = null;

export class LocalAuthLiveCommandError extends Error {
  constructor(message, result) {
    super(message);
    this.name = 'LocalAuthLiveCommandError';
    this.result = result;
  }
}

function asError(error) {
  return error instanceof Error ? error : new Error(String(error));
}

function nonEmptyLines(value) {
  return String(value ?? '')
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Redact exact generated values and common credential-bearing diagnostics. */
export function redactSecrets(value, secrets = []) {
  let redacted = String(value ?? '');
  const exact = [
    ...new Set(secrets.filter((secret) => typeof secret === 'string' && secret.length > 0)),
  ].sort((a, b) => b.length - a.length);
  for (const secret of exact) redacted = redacted.split(secret).join('[redacted]');
  return redacted
    .replace(/postgres(?:ql)?:\/\/[^\s'"`]+/giu, 'postgresql://[redacted]')
    .replace(/\bBearer\s+[^\s'"`]+/giu, 'Bearer [redacted]')
    .replace(/((?:password|passwd|secret|token|jwt)[=:]\s*)[^\s,;)}]+/giu, '$1[redacted]')
    .replace(/(FCC_[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN)[A-Z0-9_]*=)[^\s]+/giu, '$1[redacted]');
}

function commandLabel(command, args) {
  if (command === 'docker' && args[0] === 'compose') return 'docker compose';
  return command;
}

/** Run a child without a shell and keep its output private unless it fails. */
export function runCommand(
  command,
  args,
  {
    cwd = REPO_ROOT,
    env = process.env,
    timeoutMs = 5 * 60_000,
    allowFailure = false,
    redactions = [],
    input = null,
    termGraceMs = COMMAND_TERM_GRACE_MS,
  } = {},
) {
  return new Promise((resolvePromise, reject) => {
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let settled = false;
    let terminationRequested = false;
    let killTimer = null;
    const child = spawn(command, args, {
      cwd,
      env,
      shell: false,
      stdio: [input === null ? 'ignore' : 'pipe', 'pipe', 'pipe'],
    });

    const terminate = () => {
      if (settled || terminationRequested) return;
      terminationRequested = true;
      child.kill('SIGTERM');
      killTimer = setTimeout(() => {
        if (!settled) child.kill('SIGKILL');
      }, termGraceMs);
    };

    if (input !== null) child.stdin.end(input);
    activeChild = child;
    activeChildTerminate = terminate;
    const timer = setTimeout(() => {
      timedOut = true;
      terminate();
    }, timeoutMs);

    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (killTimer !== null) clearTimeout(killTimer);
      if (activeChild === child) activeChild = null;
      if (activeChildTerminate === terminate) activeChildTerminate = null;
      if (result.code === 0 && !result.signal && !result.timedOut) {
        resolvePromise(result);
        return;
      }
      if (allowFailure) {
        resolvePromise(result);
        return;
      }
      const output = redactSecrets(`${result.stderr}\n${result.stdout}`.trim(), redactions).trim();
      const suffix = output === '' ? '' : `: ${output.slice(-4000)}`;
      reject(
        new LocalAuthLiveCommandError(
          `${commandLabel(command, args)} failed${result.timedOut ? ' by timeout' : ''}${suffix}`,
          result,
        ),
      );
    };

    child.stdout.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.once('error', (error) => {
      finish({ code: null, signal: null, stdout, stderr: asError(error).message, timedOut });
    });
    child.once('close', (code, signal) => {
      finish({ code, signal, stdout, stderr, timedOut });
    });
  });
}

export function validateProjectName(projectName) {
  if (!/^[a-z0-9][a-z0-9_-]{2,62}$/u.test(projectName)) {
    throw new Error('local-auth live lane generated an invalid Compose project name');
  }
  return projectName;
}

/**
 * Return the only image references this lane is allowed to build or remove.
 *
 * The central compose file's `fcc-central-*:latest` tags are deployment tags,
 * not disposable-project resources. They can also carry a Compose project
 * label left by an earlier build, so an image census based on that label would
 * either miss ownership or delete a shared tag. Exact generated references are
 * the ownership boundary for the two images built by this lane.
 */
export function buildLaneImageReferences(projectName) {
  validateProjectName(projectName);
  return Object.freeze({
    api: `fcc-local-auth-live-api:${projectName}`,
    web: `fcc-local-auth-live-web:${projectName}`,
  });
}

export function buildComposeArgs(
  projectName,
  operation,
  { build = true, waitTimeoutSeconds = COMPOSE_WAIT_TIMEOUT_SECONDS } = {},
) {
  validateProjectName(projectName);
  const prefix = [
    'compose',
    '--project-name',
    projectName,
    '--file',
    CENTRAL_COMPOSE_FILE,
    '--file',
    LOCAL_AUTH_COMPOSE_FILE,
  ];
  if (operation === 'config') return [...prefix, 'config', '--format', 'json'];
  if (operation === 'up') {
    return [
      ...prefix,
      'up',
      '--detach',
      '--wait',
      '--wait-timeout',
      String(waitTimeoutSeconds),
      ...(build ? ['--build'] : []),
    ];
  }
  if (operation === 'down') {
    return [
      ...prefix,
      'down',
      '--volumes',
      '--remove-orphans',
      '--timeout',
      String(COMPOSE_TIMEOUT_SECONDS),
    ];
  }
  if (operation === 'ps') return [...prefix, 'ps', '--all', '--format', 'json'];
  if (operation === 'logs') {
    return [...prefix, 'logs', '--no-color', '--tail', '80'];
  }
  throw new Error(`unsupported local-auth compose operation: ${operation}`);
}

export function buildComposeExecArgs(projectName, service, commandArgs) {
  validateProjectName(projectName);
  if (typeof service !== 'string' || service.trim() === '') {
    throw new Error('local-auth Compose exec requires a service name');
  }
  return [
    'compose',
    '--project-name',
    projectName,
    '--file',
    CENTRAL_COMPOSE_FILE,
    '--file',
    LOCAL_AUTH_COMPOSE_FILE,
    'exec',
    '--no-TTY',
    service,
    ...commandArgs,
  ];
}

export function buildProjectCensusCommands(projectName) {
  validateProjectName(projectName);
  const filter = `label=com.docker.compose.project=${projectName}`;
  return [
    ['containers', ['ps', '--all', '--quiet', '--filter', filter]],
    ['volumes', ['volume', 'ls', '--quiet', '--filter', filter]],
    ['networks', ['network', 'ls', '--quiet', '--filter', filter]],
  ];
}

export function buildLaneImageCensusCommands(imageReferences) {
  if (
    imageReferences === null ||
    typeof imageReferences !== 'object' ||
    typeof imageReferences.api !== 'string' ||
    typeof imageReferences.web !== 'string'
  ) {
    throw new Error('local-auth live image census requires generated api and web references');
  }
  return Object.entries(imageReferences).map(([kind, reference]) => [
    kind,
    [
      'image',
      'ls',
      '--no-trunc',
      '--quiet',
      '--filter',
      `reference=${reference}`,
      '--format',
      '{{.ID}}|{{.Repository}}:{{.Tag}}',
    ],
  ]);
}

export function buildCentralResourceCensusCommands() {
  return buildProjectCensusCommands(CENTRAL_PROJECT_NAME);
}

export function censusFromOutputs(outputs) {
  return {
    containers: nonEmptyLines(outputs.containers).sort(),
    volumes: nonEmptyLines(outputs.volumes).sort(),
    networks: nonEmptyLines(outputs.networks).sort(),
  };
}

export function censusIsEmpty(census) {
  return ['containers', 'volumes', 'networks'].every((kind) => census[kind].length === 0);
}

function parseLaneImageRows(output) {
  return nonEmptyLines(output).map((line) => {
    const separator = line.indexOf('|');
    if (separator <= 0 || separator === line.length - 1) {
      throw new Error('local-auth live image census returned an invalid image row');
    }
    return {
      id: line.slice(0, separator),
      tag: line.slice(separator + 1),
    };
  });
}

export function laneImageCensusFromOutputs(outputs, imageReferences) {
  const commands = buildLaneImageCensusCommands(imageReferences);
  return Object.fromEntries(
    commands.map(([kind]) => [
      kind,
      {
        reference: imageReferences[kind],
        matches: parseLaneImageRows(outputs?.[kind]),
      },
    ]),
  );
}

export function laneImageCensusIsEmpty(census) {
  return Object.values(census ?? {}).every((entry) => entry.matches.length === 0);
}

export function assertLaneImagesAbsent(census, phase = 'teardown') {
  if (!laneImageCensusIsEmpty(census)) {
    const remaining = Object.values(census).flatMap((entry) =>
      entry.matches.map((match) => `${entry.reference}=${match.id}`),
    );
    throw new Error(
      `local-auth live ${phase} left lane-owned image references: ${remaining.join(', ')}`,
    );
  }
  return census;
}

export function assertLaneImagesPresent(census) {
  const missing = Object.values(census)
    .filter((entry) => entry.matches.length === 0)
    .map((entry) => entry.reference);
  if (missing.length > 0) {
    throw new Error(
      `local-auth live build did not materialize lane image references: ${missing.join(', ')}`,
    );
  }
  return census;
}

export function assertCensusUnchanged(before, after, label = 'scoped Docker resource') {
  const same = ['containers', 'volumes', 'networks'].every(
    (kind) => JSON.stringify(before?.[kind] ?? []) === JSON.stringify(after?.[kind] ?? []),
  );
  if (!same) {
    throw new Error(`${label} identity changed during the local-auth live lane`);
  }
  return after;
}

async function projectCensus(projectName, env, redactions = []) {
  const entries = buildProjectCensusCommands(projectName);
  const outputs = {};
  for (const [kind, args] of entries) {
    const result = await runCommand('docker', args, {
      cwd: REPO_ROOT,
      env,
      redactions,
    });
    outputs[kind] = result.stdout;
  }
  return censusFromOutputs(outputs);
}

async function laneImageCensus(imageReferences, env, redactions = []) {
  const outputs = {};
  for (const [kind, args] of buildLaneImageCensusCommands(imageReferences)) {
    const result = await runCommand('docker', args, {
      cwd: REPO_ROOT,
      env,
      redactions,
    });
    outputs[kind] = result.stdout;
  }
  return laneImageCensusFromOutputs(outputs, imageReferences);
}

async function removeLaneImages(imageReferences, env, redactions = []) {
  const before = await laneImageCensus(imageReferences, env, redactions);
  const references = Object.values(before)
    .filter((entry) => entry.matches.length > 0)
    .map((entry) => entry.reference);
  if (references.length === 0) return before;

  const result = await runCommand('docker', ['image', 'rm', '--force', ...references], {
    cwd: REPO_ROOT,
    env,
    allowFailure: true,
    redactions,
  });
  if (result.code !== 0) {
    throw new Error(
      `scoped local-auth lane image cleanup failed: ${redactSecrets(result.stderr, redactions).trim()}`,
    );
  }
  const after = await laneImageCensus(imageReferences, env, redactions);
  return assertLaneImagesAbsent(after);
}

async function assertCentralProjectUnchanged(before, env, redactions) {
  const after = await projectCensus(CENTRAL_PROJECT_NAME, env, redactions);
  return assertCensusUnchanged(before, after, 'pre-existing FCC central resource');
}

export async function assertProjectAbsent(projectName, env = process.env) {
  const census = await projectCensus(projectName, env);
  if (!censusIsEmpty(census)) {
    throw new Error('generated local-auth live Compose project already has resources');
  }
  return census;
}

export async function assertProjectGone(projectName, env, redactions = []) {
  const census = await projectCensus(projectName, env, redactions);
  if (!censusIsEmpty(census)) {
    throw new Error(
      `local-auth live teardown left project-scoped resources: ${JSON.stringify({
        containers: census.containers.length,
        volumes: census.volumes.length,
        networks: census.networks.length,
      })}`,
    );
  }
  return census;
}

const BOOTSTRAP_USER_AUDIT_SQL = [
  'SELECT "id"::text, "email", "enabled"::text,',
  '("password_hash" IS NOT NULL)::text,',
  'COALESCE("force_password_change", FALSE)::text,',
  'COALESCE("session_version", 0)::text',
  'FROM "users"',
  `WHERE "issuer" = '${LOCAL_AUTH_IDENTITY_ISSUER}'`,
  'ORDER BY "id"',
].join(' ');

const BOOTSTRAP_GRANT_AUDIT_SQL = [
  'SELECT COUNT(*)::text',
  'FROM "user_roles" ur',
  'JOIN "roles" r ON r."id" = ur."role_id"',
  'JOIN "users" u ON u."id" = ur."user_id"',
  `WHERE u."issuer" = '${LOCAL_AUTH_IDENTITY_ISSUER}'`,
  'AND r."role_key" = \'project_admin\'',
].join(' ');

async function runPostgresQuery(projectName, env, sql, redactions) {
  const result = await runCommand(
    'docker',
    buildComposeExecArgs(projectName, 'postgres', [
      'sh',
      '-c',
      POSTGRES_QUERY_SCRIPT,
      'local-auth-live-query',
      sql,
    ]),
    {
      cwd: REPO_ROOT,
      env,
      redactions,
      timeoutMs: 30_000,
    },
  );
  return result.stdout;
}

async function auditBootstrapState(projectName, env, credentials, phase, redactions) {
  const userOutput = await runPostgresQuery(projectName, env, BOOTSTRAP_USER_AUDIT_SQL, redactions);
  const grantOutput = await runPostgresQuery(
    projectName,
    env,
    BOOTSTRAP_GRANT_AUDIT_SQL,
    redactions,
  );
  return parseBootstrapAudit(userOutput, grantOutput, credentials.email, phase);
}

function ipv4Number(address) {
  const octets = String(address)
    .split('.')
    .map((part) => Number(part));
  if (
    octets.length !== 4 ||
    octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)
  ) {
    throw new Error(`invalid IPv4 address from Docker: ${address}`);
  }
  return ((octets[0] * 256 + octets[1]) * 256 + octets[2]) * 256 + octets[3];
}

function parseCidr(cidr) {
  const [address, prefixText] = String(cidr).split('/');
  const prefix = Number(prefixText);
  if (!Number.isInteger(prefix) || prefix < 0 || prefix > 32)
    throw new Error(`invalid Docker subnet: ${cidr}`);
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  const start = ipv4Number(address) & mask;
  const end = (start | (~mask >>> 0)) >>> 0;
  return { start: start >>> 0, end };
}

function cidrOverlaps(left, right) {
  return left.start <= right.end && right.start <= left.end;
}

export function networkProfileFor(octet) {
  if (!Number.isInteger(octet) || octet < 1 || octet > 254) {
    throw new Error('local-auth live network octet is outside the private range');
  }
  return {
    subnet: `10.240.${octet}.0/24`,
    dynamicRange: `10.240.${octet}.128/25`,
    proxyIp: `10.240.${octet}.10`,
  };
}

export function chooseNetworkProfile(existingSubnets = [], candidateOctets = []) {
  const occupied = existingSubnets.map(parseCidr);
  const candidates =
    candidateOctets.length > 0
      ? candidateOctets
      : Array.from({ length: 64 }, () => randomInt(1, 255));
  for (const octet of candidates) {
    const profile = networkProfileFor(octet);
    if (!occupied.some((subnet) => cidrOverlaps(parseCidr(profile.subnet), subnet))) return profile;
  }
  throw new Error('could not allocate a non-overlapping local-auth live Docker subnet');
}

async function dockerNetworkSubnets(env) {
  const listed = await runCommand('docker', ['network', 'ls', '--quiet'], { cwd: REPO_ROOT, env });
  const ids = nonEmptyLines(listed.stdout);
  if (ids.length === 0) return [];
  const inspected = await runCommand(
    'docker',
    ['network', 'inspect', ...ids, '--format', '{{json .IPAM.Config}}'],
    { cwd: REPO_ROOT, env },
  );
  const subnets = [];
  for (const line of nonEmptyLines(inspected.stdout)) {
    let configs;
    try {
      configs = JSON.parse(line);
    } catch {
      continue;
    }
    for (const config of configs ?? []) {
      if (typeof config?.Subnet === 'string') subnets.push(config.Subnet);
    }
  }
  return subnets;
}

export async function allocateNetworkProfile(env = process.env) {
  return chooseNetworkProfile(await dockerNetworkSubnets(env));
}

export async function findFreePort(host = LOOPBACK_HOST) {
  return new Promise((resolvePromise, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, host, () => {
      const address = server.address();
      const port = typeof address === 'object' && address !== null ? address.port : null;
      server.close((error) => {
        if (error) reject(error);
        else if (port === null) reject(new Error('could not discover a free loopback port'));
        else resolvePromise(port);
      });
    });
  });
}

export async function allocatePorts() {
  const ports = {};
  for (const key of ['web', 'postgres', 'headless', 'platform', 'keycloak']) {
    let port = await findFreePort();
    while (Object.values(ports).includes(port)) port = await findFreePort();
    ports[key] = port;
  }
  return ports;
}

export function assertLoopbackBrowserOrigin(origin) {
  const url = new URL(origin);
  if (url.protocol !== 'http:' || url.hostname !== LOOPBACK_HOST || url.username || url.password) {
    throw new Error('local-auth live browser origin must be an HTTP 127.0.0.1 origin');
  }
  return url.origin;
}

export function assertNoCallerSuppliedInputs(env = process.env) {
  for (const key of CALLER_CONTROLLED_STACK_KEYS) {
    if (Object.prototype.hasOwnProperty.call(env, key)) {
      throw new Error(`local-auth live lane refuses caller-supplied deployment input ${key}`);
    }
  }
  for (const [key, value] of Object.entries(env)) {
    const lowerKey = key.toLowerCase();
    const lowerValue = String(value ?? '').toLowerCase();
    if (/(password|passwd|secret|token|jwt|dsn|database_url|db_url)/u.test(lowerKey)) {
      throw new Error(`local-auth live lane refuses caller-supplied sensitive input ${key}`);
    }
    if (
      lowerKey.includes('evidence') ||
      lowerKey.includes('cutover') ||
      lowerKey.includes('central.env') ||
      lowerKey.includes('central_env') ||
      lowerKey.includes('central-env') ||
      lowerValue.includes('.claude/evidence') ||
      lowerValue.includes('docs/platform/evidence') ||
      lowerValue.includes('central.env')
    ) {
      throw new Error(`local-auth live lane refuses production/evidence input ${key}`);
    }
  }
}

function randomSecret(bytes = 32) {
  return randomBytes(bytes).toString('base64url');
}

export function generateCredentials() {
  const suffix = randomBytes(8).toString('hex');
  const initialPassword = `Initial-${randomSecret(18)}`;
  const newPassword = `Changed-${randomSecret(18)}`;
  return {
    suffix,
    email: `fcc-local-auth-${suffix}@example.invalid`,
    initialPassword,
    newPassword,
    jwtSecret: randomSecret(48),
    postgresPassword: randomSecret(32),
    keycloakAdminPassword: randomSecret(32),
    chamberClientSecret: randomSecret(32),
    stagingCliSecret: randomSecret(32),
  };
}

export function buildStackEnvironment({ credentials, ports, network, projectName }) {
  const prefix = `fcc-local-auth-live-${credentials.suffix}`;
  const imageReferences = buildLaneImageReferences(projectName);
  return {
    PUBLIC_HOST: LOOPBACK_HOST,
    WEB_PORT: String(ports.web),
    POSTGRES_PORT: String(ports.postgres),
    HEADLESS_API_PORT: String(ports.headless),
    PLATFORM_API_PORT: String(ports.platform),
    KEYCLOAK_PORT: String(ports.keycloak),
    POSTGRES_USER: `fcc_live_${credentials.suffix.slice(0, 12)}`,
    POSTGRES_PASSWORD: credentials.postgresPassword,
    POSTGRES_DB: `fcc_live_${credentials.suffix.slice(0, 12)}`,
    KEYCLOAK_ADMIN: 'fcc-live-admin',
    KEYCLOAK_ADMIN_PASSWORD: credentials.keycloakAdminPassword,
    FCC_CHAMBER_CLIENT_SECRET: credentials.chamberClientSecret,
    FCC_STAGING_CLI_SECRET: credentials.stagingCliSecret,
    FCC_CENTRAL_PROVIDER_ID: 'unlicensed',
    FCC_HEADLESS_DB_PATH: '/data/headless/local-auth-live.fcc.db',
    FCC_PLATFORM_AUTH_MODE: 'local_jwt',
    FCC_HEADLESS_AUTH_MODE: 'local_jwt',
    WEB_AUTH_MODE: 'local',
    ALLOW_INSECURE_TRANSPORT: 'true',
    FCC_PLATFORM_LOCAL_JWT_SECRET: credentials.jwtSecret,
    FCC_HEADLESS_LOCAL_JWT_SECRET: credentials.jwtSecret,
    FCC_PLATFORM_LOCAL_JWT_ISSUER: 'https://fcc-local-auth-live.invalid/auth',
    FCC_HEADLESS_LOCAL_JWT_ISSUER: 'https://fcc-local-auth-live.invalid/auth',
    FCC_PLATFORM_LOCAL_JWT_AUDIENCE: 'fcc-local-auth-live',
    FCC_HEADLESS_LOCAL_JWT_AUDIENCE: 'fcc-local-auth-live',
    FCC_PLATFORM_LOCAL_JWT_TTL_SECONDS: '900',
    FCC_HEADLESS_LOCAL_JWT_TTL_SECONDS: '900',
    FCC_PLATFORM_LOCAL_JWT_REFRESH_TTL_SECONDS: '604800',
    FCC_HEADLESS_LOCAL_JWT_REFRESH_TTL_SECONDS: '604800',
    FCC_PLATFORM_BOOTSTRAP_ADMIN_EMAIL: credentials.email,
    FCC_PLATFORM_BOOTSTRAP_ADMIN_PASSWORD: credentials.initialPassword,
    OIDC_REALM: 'fcc-dev',
    OIDC_CLIENT_ID: 'fcc-platform-frontend',
    CENTRAL_PROXY_IP: network.proxyIp,
    CENTRAL_APP_SUBNET: network.subnet,
    CENTRAL_APP_DYNAMIC_RANGE: network.dynamicRange,
    FCC_LOCAL_AUTH_LIVE_CONTAINER_PREFIX: prefix,
    FCC_LOCAL_AUTH_LIVE_PROJECT: projectName,
    FCC_LOCAL_AUTH_LIVE_API_IMAGE: imageReferences.api,
    FCC_LOCAL_AUTH_LIVE_WEB_IMAGE: imageReferences.web,
  };
}

export function buildBrowserEnvironment(baseEnv, credentials, origin) {
  return {
    ...baseEnv,
    FCC_LIVE_STACK_E2E: '1',
    FCC_LOCAL_AUTH_LIVE_CAPTURE_OFF: '1',
    E2E_BASE_URL: assertLoopbackBrowserOrigin(origin),
    FCC_LOCAL_AUTH_LIVE_EMAIL: credentials.email,
    FCC_LOCAL_AUTH_LIVE_INITIAL_PASSWORD: credentials.initialPassword,
    FCC_LOCAL_AUTH_LIVE_NEW_PASSWORD: credentials.newPassword,
  };
}

function composePortMapping(serviceName, rawMapping) {
  if (rawMapping === null || typeof rawMapping !== 'object') {
    throw new Error(`merged Compose model has an invalid ${serviceName} host-port mapping`);
  }
  const published = Number(rawMapping.published);
  const target = Number(rawMapping.target);
  const hostIp = String(rawMapping.host_ip ?? rawMapping.hostIp ?? '').trim();
  const protocol = String(rawMapping.protocol ?? 'tcp').toLowerCase();
  if (!Number.isInteger(published) || !Number.isInteger(target) || hostIp === '') {
    throw new Error(`merged Compose model has an incomplete ${serviceName} host-port mapping`);
  }
  return { hostIp, published, target, protocol };
}

/**
 * Validate the effective (post-override) Compose model before any container starts.
 * The source YAML is not enough: Compose merges port sequences, and an additive
 * override can leave a base publication in place while looking loopback-only.
 */
export function validateMergedComposeModel(model, imageReferences = null) {
  const services = model?.services;
  if (services === null || typeof services !== 'object' || Array.isArray(services)) {
    throw new Error('merged Compose model has no services object');
  }

  const mappings = [];
  for (const [serviceName, service] of Object.entries(services)) {
    const rawPorts = service?.ports ?? [];
    if (!Array.isArray(rawPorts)) {
      throw new Error(`merged Compose model has invalid ports for ${serviceName}`);
    }
    for (const rawMapping of rawPorts) {
      mappings.push({
        serviceName,
        ...composePortMapping(serviceName, rawMapping),
      });
    }
  }

  const internalServices = ['postgres', 'keycloak', 'headless-api', 'platform-api'];
  for (const serviceName of internalServices) {
    const exposed = mappings.filter((mapping) => mapping.serviceName === serviceName);
    if (exposed.length > 0) {
      throw new Error(`merged Compose model exposes internal service ${serviceName}`);
    }
  }

  const webMappings = mappings.filter((mapping) => mapping.serviceName === 'web');
  if (mappings.some((mapping) => mapping.serviceName !== 'web')) {
    throw new Error('merged Compose model exposes a non-gateway service');
  }
  if (webMappings.length !== 1) {
    throw new Error(
      `merged Compose model must publish exactly one web gateway port, found ${webMappings.length}`,
    );
  }
  const [web] = webMappings;
  if (web.hostIp !== LOOPBACK_HOST || web.target !== 80) {
    throw new Error('merged Compose model web publication is not loopback port 80');
  }

  if (imageReferences !== null) {
    const expectedApi = imageReferences.api;
    const expectedWeb = imageReferences.web;
    for (const serviceName of ['central-migrate', 'headless-api', 'platform-api']) {
      if (services[serviceName]?.image !== expectedApi) {
        throw new Error(
          `merged Compose model ${serviceName} does not use the generated lane API image`,
        );
      }
    }
    if (services.web?.image !== expectedWeb) {
      throw new Error('merged Compose model web does not use the generated lane web image');
    }
  }

  const seen = new Set();
  for (const mapping of mappings) {
    const key = `${mapping.hostIp}:${mapping.published}/${mapping.protocol}`;
    if (seen.has(key)) throw new Error(`merged Compose model duplicates host-port mapping ${key}`);
    seen.add(key);
  }
  return { webPort: web.published };
}

function parseAuditRows(output) {
  return nonEmptyLines(output).map((line) => line.split('|').map((field) => field.trim()));
}

export function parseBootstrapAudit(userOutput, grantOutput, expectedEmail, phase) {
  const userRows = parseAuditRows(userOutput);
  const grantRows = parseAuditRows(grantOutput);
  const grantCount = Number(grantRows[0]?.[0]);
  const [userId, email, enabled, hashPresent, forcePasswordChange, sessionVersion] =
    userRows[0] ?? [];
  const audit = {
    phase,
    localUserCount: userRows.length,
    expectedIdentity: userRows.length === 1 && email === expectedEmail,
    enabled: enabled === 't' || enabled === 'true',
    hashPresent: hashPresent === 't' || hashPresent === 'true',
    forcePasswordChange: forcePasswordChange === 't' || forcePasswordChange === 'true',
    projectAdminGrants: Number.isInteger(grantCount) ? grantCount : -1,
    sessionVersion: Number(sessionVersion),
    userIdPresent: typeof userId === 'string' && userId.length > 0,
    userId,
  };
  if (!audit.userIdPresent || !Number.isInteger(audit.sessionVersion)) {
    throw new Error(`local-auth ${phase} bootstrap audit returned an invalid safe row shape`);
  }
  return audit;
}

export function assertBootstrapAudit(
  audit,
  { expectedForcePasswordChange, previousSessionVersion = null, previousAudit = null },
) {
  const sessionAdvanced =
    previousSessionVersion === null ? true : audit.sessionVersion > previousSessionVersion;
  if (
    audit.localUserCount !== 1 ||
    !audit.expectedIdentity ||
    !audit.enabled ||
    !audit.hashPresent ||
    audit.forcePasswordChange !== expectedForcePasswordChange ||
    audit.projectAdminGrants !== 1 ||
    !sessionAdvanced ||
    (previousAudit !== null && audit.userId !== previousAudit.userId)
  ) {
    throw new Error(
      `local-auth ${audit.phase} bootstrap audit failed: ${JSON.stringify({
        local_user_count: audit.localUserCount,
        expected_identity: audit.expectedIdentity,
        enabled: audit.enabled,
        hash_present: audit.hashPresent,
        force_password_change: audit.forcePasswordChange,
        project_admin_grants: audit.projectAdminGrants,
        session_version_advanced: sessionAdvanced,
        same_user: previousAudit === null || audit.userId === previousAudit.userId,
      })}`,
    );
  }
  return audit;
}

function emitBootstrapAudit(audit) {
  const safe = {
    phase: audit.phase,
    local_user_count: audit.localUserCount,
    expected_identity: audit.expectedIdentity,
    enabled: audit.enabled,
    hash_present: audit.hashPresent,
    force_password_change: audit.forcePasswordChange,
    project_admin_grants: audit.projectAdminGrants,
    session_version: audit.sessionVersion,
  };
  console.log(`[local-auth-live] bootstrap audit: ${JSON.stringify(safe)}`);
}

function parseComposeRows(output) {
  const source = String(output ?? '').trim();
  if (source === '') return [];
  try {
    const parsed = JSON.parse(source);
    return Array.isArray(parsed) ? parsed : [parsed];
  } catch {
    return nonEmptyLines(source).flatMap((line) => {
      try {
        return [JSON.parse(line)];
      } catch {
        return [];
      }
    });
  }
}

export function migrationExitCode(rows) {
  const migration = rows.find((row) => String(row.Service ?? row.service) === 'central-migrate');
  if (!migration) return null;
  const raw = migration.ExitCode ?? migration.exitCode;
  const code = Number(raw);
  return Number.isInteger(code) ? code : null;
}

export async function assertMigrationSucceeded(projectName, env, redactions = []) {
  const result = await runCommand('docker', buildComposeArgs(projectName, 'ps'), {
    cwd: REPO_ROOT,
    env,
    redactions,
  });
  const code = migrationExitCode(parseComposeRows(result.stdout));
  if (code !== 0) {
    throw new Error(
      code === null
        ? 'central-migrate did not appear as a completed Compose service'
        : 'central-migrate did not complete successfully',
    );
  }
}

export function validateServedRuntimeConfig(source) {
  const text = String(source ?? '');
  if (!/authMode\s*:\s*['"]local['"]/u.test(text)) {
    throw new Error('served runtime-config.js did not declare authMode=local');
  }
  if (!/insecureTransportAllowed\s*:\s*true\b/u.test(text)) {
    throw new Error('served runtime-config.js did not allow the loopback HTTP lane');
  }
  return true;
}

export async function readServedRuntimeConfig(origin, fetchImpl = fetch) {
  const response = await fetchImpl(new URL('/runtime-config.js', origin));
  if (!response.ok) throw new Error('local-auth live web gateway did not serve runtime-config.js');
  const source = await response.text();
  validateServedRuntimeConfig(source);
  return source;
}

async function waitForRuntimeConfig(origin, fetchImpl = fetch, timeoutMs = 120_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    if (requestedSignal !== null)
      throw new Error(`local-auth live lane interrupted by ${requestedSignal}`);
    try {
      return await readServedRuntimeConfig(origin, fetchImpl);
    } catch (error) {
      lastError = asError(error);
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 1000));
    }
  }
  throw new Error(
    `local-auth live gateway readiness timed out: ${lastError?.message ?? 'no response'}`,
  );
}

export function pairingEnvText(stackEnv) {
  const keys = [
    'FCC_PLATFORM_AUTH_MODE',
    'FCC_HEADLESS_AUTH_MODE',
    'WEB_AUTH_MODE',
    'ALLOW_INSECURE_TRANSPORT',
    'PUBLIC_HOST',
    'FCC_PLATFORM_LOCAL_JWT_SECRET',
    'FCC_HEADLESS_LOCAL_JWT_SECRET',
  ];
  return `${keys.map((key) => `${key}=${stackEnv[key]}`).join('\n')}\n`;
}

function browserRunnerArgs(outputDirectory) {
  return [
    resolve(REPO_ROOT, SHARED_LIVE_RUNNER),
    '--lane=local-auth',
    '--reporter=list',
    '--trace=off',
    `--output=${outputDirectory}`,
  ];
}

async function emitComposeDiagnostics(projectName, env, redactions) {
  try {
    const status = await runCommand('docker', buildComposeArgs(projectName, 'ps'), {
      cwd: REPO_ROOT,
      env,
      allowFailure: true,
      redactions,
    });
    const logs = await runCommand('docker', buildComposeArgs(projectName, 'logs'), {
      cwd: REPO_ROOT,
      env,
      allowFailure: true,
      timeoutMs: 30_000,
      redactions,
    });
    const statusText = redactSecrets(status.stdout || status.stderr, redactions).trim();
    const logText = redactSecrets(logs.stdout || logs.stderr, redactions).trim();
    if (statusText !== '') console.error(`[local-auth-live] service status:\n${statusText}`);
    if (logText !== '')
      console.error(`[local-auth-live] service diagnostics:\n${logText.slice(-12_000)}`);
  } catch (error) {
    console.error(
      `[local-auth-live] diagnostics unavailable: ${redactSecrets(asError(error).message, redactions)}`,
    );
  }
}

async function teardown(projectName, env, redactions, imageReferences) {
  const down = await runCommand('docker', buildComposeArgs(projectName, 'down'), {
    cwd: REPO_ROOT,
    env,
    allowFailure: true,
    timeoutMs: 120_000,
    redactions,
  });
  const cleanupErrors = [];
  try {
    await assertProjectGone(projectName, env, redactions);
  } catch (error) {
    cleanupErrors.push(asError(error));
  }
  try {
    await removeLaneImages(imageReferences, env, redactions);
  } catch (error) {
    cleanupErrors.push(asError(error));
  }
  if (down.code !== 0) {
    cleanupErrors.push(
      new Error(
        `scoped local-auth Compose teardown failed: ${redactSecrets(down.stderr, redactions).trim()}`,
      ),
    );
  }
  if (cleanupErrors.length > 0) {
    throw new Error(cleanupErrors.map((error) => error.message).join('; '));
  }
}

export async function withTeardown(work, cleanup, { onCleanupError = () => undefined } = {}) {
  let workError = null;
  try {
    return await work();
  } catch (error) {
    workError = asError(error);
    throw workError;
  } finally {
    try {
      await cleanup();
    } catch (error) {
      const cleanupError = asError(error);
      if (workError !== null) onCleanupError(cleanupError);
      else {
        // Cleanup failure is the primary outcome when work did not fail.
        // eslint-disable-next-line no-unsafe-finally
        throw cleanupError;
      }
    }
  }
}

function installSignalHandlers() {
  const handler = (signal) => {
    requestedSignal = signal;
    activeChildTerminate?.();
  };
  process.on('SIGINT', handler);
  process.on('SIGTERM', handler);
  return () => {
    process.off('SIGINT', handler);
    process.off('SIGTERM', handler);
  };
}

function abortIfRequested() {
  if (requestedSignal !== null)
    throw new Error(`local-auth live lane interrupted by ${requestedSignal}`);
}

export async function runLocalAuthLiveLane({ env = process.env, fetchImpl = fetch } = {}) {
  requestedSignal = null;
  assertNoCallerSuppliedInputs(env);
  const credentials = generateCredentials();
  const projectName = validateProjectName(
    `fcc-local-auth-live-${process.pid}-${credentials.suffix.slice(0, 10)}`,
  );
  const ports = await allocatePorts();
  const network = await allocateNetworkProfile(env);
  const laneImageReferences = buildLaneImageReferences(projectName);
  const stackEnv = buildStackEnvironment({ credentials, ports, network, projectName });
  const childEnv = { ...env, ...stackEnv };
  const redactions = [
    ...Object.values(credentials),
    ...GENERATED_CREDENTIAL_ENV_KEYS.map((key) => stackEnv[key]).filter(Boolean),
  ];
  const origin = assertLoopbackBrowserOrigin(`http://${LOOPBACK_HOST}:${ports.web}`);
  let composeAttempted = false;
  let primaryError = null;
  let centralCensusBefore = null;
  let laneImageCensusBefore = null;
  let outputDirectory = null;
  let preBrowserAudit = null;

  try {
    centralCensusBefore = await projectCensus(CENTRAL_PROJECT_NAME, env, redactions);
    await assertProjectAbsent(projectName, env);
    laneImageCensusBefore = await laneImageCensus(laneImageReferences, env, redactions);
    assertLaneImagesAbsent(laneImageCensusBefore, 'preflight');
    abortIfRequested();

    // The checker reads this tuple directly from stdin; it never needs a
    // filesystem representation or a secret-bearing command-line argument.
    await runCommand('python3', [AUTH_PAIRING_SCRIPT, PAIRING_ENV_STREAM], {
      cwd: REPO_ROOT,
      env,
      input: pairingEnvText(stackEnv),
      redactions,
    });

    composeAttempted = true;
    const mergedConfig = await runCommand('docker', buildComposeArgs(projectName, 'config'), {
      cwd: REPO_ROOT,
      env: childEnv,
      redactions,
    });
    let model;
    try {
      model = JSON.parse(mergedConfig.stdout);
    } catch {
      throw new Error('docker compose config did not return a JSON model for the local-auth lane');
    }
    validateMergedComposeModel(model, laneImageReferences);

    // Always rebuild the current checkout. Docker may reuse valid layers, but
    // the `--build` gate prevents a stale `:latest` image from being reused.
    await runCommand('docker', buildComposeArgs(projectName, 'up', { build: true }), {
      cwd: REPO_ROOT,
      env: childEnv,
      timeoutMs: (COMPOSE_WAIT_TIMEOUT_SECONDS + 60) * 1000,
      redactions,
    });
    abortIfRequested();
    assertLaneImagesPresent(await laneImageCensus(laneImageReferences, childEnv, redactions));
    await assertMigrationSucceeded(projectName, childEnv, redactions);

    preBrowserAudit = await auditBootstrapState(
      projectName,
      childEnv,
      credentials,
      'pre-browser',
      redactions,
    );
    assertBootstrapAudit(preBrowserAudit, { expectedForcePasswordChange: true });
    emitBootstrapAudit(preBrowserAudit);

    await waitForRuntimeConfig(origin, fetchImpl);
    outputDirectory = await mkdtemp(join(tmpdir(), 'fcc-local-auth-live-results-'));
    // The browser child needs only its generated form values and gateway URL;
    // do not forward the Compose JWT/bootstrap environment into Playwright.
    const browserEnv = buildBrowserEnvironment(env, credentials, origin);
    const browserResult = await runCommand(process.execPath, browserRunnerArgs(outputDirectory), {
      cwd: APP_ROOT,
      env: browserEnv,
      timeoutMs: 15 * 60_000,
      redactions,
    });
    // The live lane owns the child process so it can redact generated secrets,
    // but Playwright's normal reporter is still normative evidence. Preserve
    // that evidence in the parent log after redaction instead of treating a
    // successful child with invisible output as an unverifiable PASS.
    const browserOutput = redactSecrets(
      `${browserResult.stdout ?? ''}${browserResult.stderr ?? ''}`,
      redactions,
    );
    if (browserOutput !== '')
      process.stdout.write(browserOutput.endsWith('\n') ? browserOutput : `${browserOutput}\n`);

    const postBrowserAudit = await auditBootstrapState(
      projectName,
      childEnv,
      credentials,
      'post-browser',
      redactions,
    );
    assertBootstrapAudit(postBrowserAudit, {
      expectedForcePasswordChange: false,
      previousSessionVersion: preBrowserAudit.sessionVersion,
      previousAudit: preBrowserAudit,
    });
    emitBootstrapAudit(postBrowserAudit);
    return 0;
  } catch (error) {
    primaryError = asError(error);
    if (composeAttempted) {
      try {
        await emitComposeDiagnostics(projectName, childEnv, redactions);
      } catch (diagnosticsError) {
        console.error(
          `[local-auth-live] diagnostics failed: ${redactSecrets(asError(diagnosticsError).message, redactions)}`,
        );
      }
    }
    throw primaryError;
  } finally {
    const cleanupErrors = [];
    if (composeAttempted) {
      try {
        await teardown(projectName, childEnv, redactions, laneImageReferences);
      } catch (error) {
        cleanupErrors.push(asError(error));
      }
    }
    if (centralCensusBefore !== null) {
      try {
        await assertCentralProjectUnchanged(centralCensusBefore, env, redactions);
      } catch (error) {
        cleanupErrors.push(asError(error));
      }
    }
    if (outputDirectory !== null) {
      try {
        await rm(outputDirectory, { recursive: true, force: true });
      } catch (error) {
        cleanupErrors.push(asError(error));
      }
    }
    if (cleanupErrors.length > 0) {
      const message = cleanupErrors
        .map((error) => redactSecrets(error.message, redactions))
        .join('; ');
      if (primaryError !== null) {
        console.error(`[local-auth-live] cleanup/audit failed after lane failure: ${message}`);
      } else {
        // Cleanup is the only authoritative outcome when the lane itself
        // succeeded; preserve that failure instead of silently returning 0.
        // eslint-disable-next-line no-unsafe-finally
        throw new Error(`local-auth live cleanup/audit failed: ${message}`);
      }
    }
  }
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  const removeSignalHandlers = installSignalHandlers();
  runLocalAuthLiveLane()
    .then((code) => {
      process.exitCode = code;
    })
    .catch((error) => {
      console.error(`[local-auth-live] ${redactSecrets(asError(error).message)}`);
      process.exitCode = requestedSignal === null ? 1 : 128;
    })
    .finally(removeSignalHandlers);
}
