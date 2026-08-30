import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const RUNTIME_CONFIG_PATH = new URL('../public/runtime-config.dev.json', import.meta.url);
const DEV_STACK_SCRIPT = new URL('./dev-stack.mjs', import.meta.url);
const GENERATION_WORKER_SCRIPT = new URL('./run-test-plan-generation-worker.py', import.meta.url);

function readGatewayOrigin() {
  const runtimeConfig = JSON.parse(readFileSync(RUNTIME_CONFIG_PATH, 'utf8'));
  const configuredOrigin = runtimeConfig.apiBaseUrl;
  if (typeof configuredOrigin !== 'string' || configuredOrigin.trim() === '') {
    throw new Error(
      `runtime-config.dev.json must define a non-empty apiBaseUrl: ${RUNTIME_CONFIG_PATH.pathname}`,
    );
  }

  const url = new URL(configuredOrigin);
  if (url.protocol !== 'http:' || url.port === '') {
    throw new Error(
      `runtime-config.dev.json apiBaseUrl must be an http URL with an explicit port: ${configuredOrigin}`,
    );
  }
  return url.origin;
}

function assertOriginMatches(name, value, expectedOrigin) {
  if (typeof value !== 'string' || value.trim() === '') return;
  const configuredOrigin = new URL(value).origin;
  if (configuredOrigin !== expectedOrigin) {
    throw new Error(
      `${name} must match runtime-config.dev.json apiBaseUrl (${expectedOrigin}); got ${value}`,
    );
  }
}

function buildChildEnvironment(env = process.env) {
  const gatewayOrigin = readGatewayOrigin();
  assertOriginMatches('FCC_DEV_STACK_GATEWAY_URL', env.FCC_DEV_STACK_GATEWAY_URL, gatewayOrigin);
  assertOriginMatches('E2E_BASE_URL', env.E2E_BASE_URL, gatewayOrigin);

  return {
    ...env,
    FCC_DEV_STACK_SURFACES: env.FCC_DEV_STACK_SURFACES ?? 'headless,platform',
    FCC_DEV_STACK_GATEWAY_URL: gatewayOrigin,
    FCC_DEV_STACK_SKIP_INFRA: env.FCC_DEV_STACK_SKIP_INFRA ?? '1',
    FCC_HEADLESS_AUTH_MODE: env.FCC_HEADLESS_AUTH_MODE ?? 'disabled',
    FCC_PLATFORM_AUTH_MODE: env.FCC_PLATFORM_AUTH_MODE ?? 'disabled',
    FCC_PLATFORM_ALLOW_INSECURE: env.FCC_PLATFORM_ALLOW_INSECURE ?? '1',
    E2E_BASE_URL: gatewayOrigin,
  };
}

function spawnChild(label, command, args, env) {
  console.log(`[dev:stack:test-plans] start ${label}: ${command} ${args.join(' ')}`);
  return spawn(command, args, {
    cwd: APP_ROOT,
    env,
    stdio: 'inherit',
    shell: false,
  });
}

async function runStack(env) {
  const { resolveVenvPython } = await import('./dev-stack.mjs');
  const children = [
    {
      label: 'stack',
      child: spawnChild('stack', process.execPath, [fileURLToPath(DEV_STACK_SCRIPT)], env),
      stopSent: false,
      done: false,
    },
    {
      label: 'generation-worker',
      child: spawnChild(
        'generation-worker',
        resolveVenvPython({ env }),
        [fileURLToPath(GENERATION_WORKER_SCRIPT)],
        env,
      ),
      stopSent: false,
      done: false,
    },
  ];

  return new Promise((resolveRun) => {
    let shuttingDown = false;
    let exitCode = 0;

    const finishIfStopped = () => {
      if (shuttingDown && children.every(({ done }) => done)) resolveRun(exitCode);
    };

    const stopChildren = () => {
      for (const entry of children) {
        if (entry.done || entry.stopSent) continue;
        entry.stopSent = true;
        entry.child.kill('SIGINT');
      }
    };

    const shutdown = (code) => {
      if (!shuttingDown) {
        shuttingDown = true;
        exitCode = code;
      } else if (code !== 0) {
        exitCode = code;
      }
      stopChildren();
      finishIfStopped();
    };

    for (const entry of children) {
      entry.child.on('error', (error) => {
        console.error(`[dev:stack:test-plans] ${entry.label} failed to start: ${error.message}`);
        shutdown(1);
      });
      entry.child.on('close', (code, signal) => {
        entry.done = true;
        if (!shuttingDown) {
          const gracefulSignal = signal === 'SIGINT' || signal === 'SIGTERM';
          if (gracefulSignal) {
            console.log(`[dev:stack:test-plans] ${entry.label} stopped by ${signal}`);
            shutdown(0);
          } else {
            const failureCode = signal === null && code === 0 ? 1 : (code ?? 1);
            console.error(
              `[dev:stack:test-plans] ${entry.label} exited unexpectedly ` +
                `(code=${code ?? 'null'}, signal=${signal ?? 'none'})`,
            );
            shutdown(failureCode);
          }
        }
        finishIfStopped();
      });
    }

    process.once('SIGINT', () => shutdown(0));
    process.once('SIGTERM', () => shutdown(0));
  });
}

async function main() {
  const childEnvironment = buildChildEnvironment();
  const exitCode = await runStack(childEnvironment);
  process.exitCode = exitCode;
}

if (import.meta.url === pathToFileURL(process.argv[1] ?? '').href) {
  main().catch((error) => {
    console.error(error instanceof Error ? (error.stack ?? error.message) : String(error));
    process.exitCode = 1;
  });
}
