import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import net from 'node:net';

export const DEFAULT_PLAYWRIGHT_DOCKER_BASE = 'noble';
export const DEFAULT_PLAYWRIGHT_DOCKER_HOST_ALIAS = 'hostmachine';
export const DEFAULT_PLAYWRIGHT_DOCKER_SERVER_PORT = 3000;
export const DEFAULT_PLAYWRIGHT_RUNTIME = 'host';

function dockerCommand() {
  const explicit = process.env.FCC_DOCKER_EXECUTABLE?.trim();
  if (explicit) return explicit;
  if (process.platform === 'win32') return 'docker.exe';
  // Snap-packaged shells may resolve `docker` to `/snap/bin/docker` even when
  // the host CLI is installed at `/usr/bin/docker`.
  if (existsSync('/usr/bin/docker')) return '/usr/bin/docker';
  return 'docker';
}

export function readPlaywrightPackageVersion(packageJsonSource) {
  const packageJson =
    packageJsonSource ??
    JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'));
  const version =
    packageJson.devDependencies?.['@playwright/test'] ??
    packageJson.dependencies?.['@playwright/test'] ??
    packageJson.devDependencies?.playwright ??
    packageJson.dependencies?.playwright;
  if (typeof version !== 'string' || version.trim() === '') {
    throw new Error('Unable to resolve Playwright package version from package.json');
  }
  return version.trim().replace(/^[^0-9]*/, '');
}

export function resolvePlaywrightRuntime(argv = process.argv.slice(2), env = process.env) {
  const runtimeArg = argv.find((arg) => arg.startsWith('--runtime='))?.split('=', 2)[1];
  return (
    runtimeArg ||
    env.FCC_CAPTURE_RUNTIME ||
    env.FCC_PLAYWRIGHT_RUNTIME ||
    DEFAULT_PLAYWRIGHT_RUNTIME
  );
}

export function resolvePlaywrightDockerImage(env = process.env, packageJsonSource) {
  const explicitImage = env.FCC_PLAYWRIGHT_DOCKER_IMAGE?.trim();
  if (explicitImage) return explicitImage;
  const version = readPlaywrightPackageVersion(packageJsonSource);
  const base = env.FCC_PLAYWRIGHT_DOCKER_BASE?.trim() || DEFAULT_PLAYWRIGHT_DOCKER_BASE;
  return `mcr.microsoft.com/playwright:v${version}-${base}`;
}

export function rewriteBaseUrlForDocker(baseUrl, env = process.env) {
  const url = new URL(baseUrl);
  const hostAlias =
    env.FCC_PLAYWRIGHT_DOCKER_HOST_ALIAS?.trim() || DEFAULT_PLAYWRIGHT_DOCKER_HOST_ALIAS;
  const isLocalHost =
    url.hostname === 'localhost' ||
    url.hostname === '127.0.0.1' ||
    url.hostname === '[::1]' ||
    url.hostname === '::1';
  if (isLocalHost) url.hostname = hostAlias;
  return url.toString();
}

export function buildPlaywrightDockerServerSpec(options = {}) {
  const env = options.env ?? process.env;
  const port = Number(
    env.FCC_PLAYWRIGHT_DOCKER_SERVER_PORT || DEFAULT_PLAYWRIGHT_DOCKER_SERVER_PORT,
  );
  const image = resolvePlaywrightDockerImage(env, options.packageJsonSource);
  const alias =
    env.FCC_PLAYWRIGHT_DOCKER_HOST_ALIAS?.trim() || DEFAULT_PLAYWRIGHT_DOCKER_HOST_ALIAS;
  const version = readPlaywrightPackageVersion(options.packageJsonSource);
  const args = [
    'run',
    '--rm',
    '--init',
    '--ipc=host',
    `--add-host=${alias}:host-gateway`,
    '-p',
    `${port}:${port}`,
    '--workdir',
    '/home/pwuser',
    '--user',
    'pwuser',
    image,
    '/bin/sh',
    '-c',
    `npx -y playwright@${version} run-server --port ${port} --host 0.0.0.0`,
  ];
  return {
    command: dockerCommand(),
    args,
    image,
    port,
    alias,
    endpoint: `ws://127.0.0.1:${port}/`,
  };
}

function requestAvailablePort(port) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen({ host: '127.0.0.1', port }, () => {
      const address = server.address();
      server.close((error) => {
        if (error) reject(error);
        else resolve(address.port);
      });
    });
  });
}

export async function resolveAvailablePlaywrightDockerPort(preferredPort) {
  try {
    return await requestAvailablePort(preferredPort);
  } catch (error) {
    if (error.code !== 'EADDRINUSE') throw error;
    return requestAvailablePort(0);
  }
}

export async function startPlaywrightDockerServer(options = {}) {
  const sourceEnv = options.env ?? process.env;
  const explicitPort = sourceEnv.FCC_PLAYWRIGHT_DOCKER_SERVER_PORT?.trim();
  const port = explicitPort
    ? Number(explicitPort)
    : await resolveAvailablePlaywrightDockerPort(DEFAULT_PLAYWRIGHT_DOCKER_SERVER_PORT);
  const spec = buildPlaywrightDockerServerSpec({
    ...options,
    env: { ...sourceEnv, FCC_PLAYWRIGHT_DOCKER_SERVER_PORT: String(port) },
  });
  const child = spawn(spec.command, spec.args, {
    cwd: new URL('..', import.meta.url),
    stdio: ['ignore', 'pipe', 'pipe'],
    env: options.env ?? process.env,
  });

  let stdout = '';
  let stderr = '';
  let markReady;
  let readinessTimer;
  const readyPromise = new Promise((resolve, reject) => {
    markReady = resolve;
    readinessTimer = setTimeout(
      () =>
        reject(new Error(`Timed out waiting for Playwright Docker server on port ${spec.port}`)),
      30_000,
    );
    readinessTimer.unref();
  });
  child.stdout?.on('data', (chunk) => {
    stdout += chunk.toString();
    process.stdout.write(chunk);
    if (/Listening on ws:\/\//.test(stdout)) markReady();
  });
  child.stderr?.on('data', (chunk) => {
    stderr += chunk.toString();
    process.stderr.write(chunk);
  });

  const exitPromise = new Promise((resolve) => {
    child.once('exit', (code, signal) => resolve({ code, signal }));
  });
  const errorPromise = new Promise((_, reject) => {
    child.once('error', (error) => {
      reject(new Error(`Failed to start Docker command "${spec.command}": ${error.message}`));
    });
  });

  let exitResult;
  try {
    exitResult = await Promise.race([errorPromise, exitPromise, readyPromise.then(() => null)]);
  } catch (error) {
    child.kill('SIGTERM');
    throw error;
  } finally {
    clearTimeout(readinessTimer);
  }
  if (exitResult) {
    throw new Error(
      [
        `Playwright Docker server exited before becoming ready (${JSON.stringify(exitResult)}).`,
        stdout.trim(),
        stderr.trim(),
      ]
        .filter(Boolean)
        .join('\n'),
    );
  }

  let stopped = false;
  return {
    ...spec,
    child,
    stop: async () => {
      if (stopped) return;
      stopped = true;
      child.kill('SIGTERM');
      await exitPromise;
    },
  };
}
