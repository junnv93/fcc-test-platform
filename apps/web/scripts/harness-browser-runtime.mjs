import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';

const SCRUBBED_ENV_KEYS = Object.freeze([
  'LD_LIBRARY_PATH',
  'SNAP',
  'SNAP_ARCH',
  'SNAP_COMMON',
  'SNAP_CONTEXT',
  'SNAP_COOKIE',
  'SNAP_DATA',
  'SNAP_EUID',
  'SNAP_INSTANCE_KEY',
  'SNAP_INSTANCE_NAME',
  'SNAP_LIBRARY_PATH',
  'SNAP_NAME',
  'SNAP_REAL_HOME',
  'SNAP_REEXEC',
  'SNAP_REVISION',
  'SNAP_UID',
  'SNAP_USER_COMMON',
  'SNAP_USER_DATA',
  'SNAP_VERSION',
]);

export const PLAYWRIGHT_LINUX_APT_PACKAGE_BY_LIBRARY = Object.freeze({
  'libnss3.so': 'libnss3',
  'libnssutil3.so': 'libnss3',
  'libsmime3.so': 'libnss3',
  'libnspr4.so': 'libnspr4',
  'libatk-1.0.so.0': 'libatk1.0-0',
  'libatk-bridge-2.0.so.0': 'libatk-bridge2.0-0',
  'libcups.so.2': 'libcups2',
  'libxcb.so.1': 'libxcb1',
  'libX11.so.6': 'libx11-6',
  'libXcomposite.so.1': 'libxcomposite1',
  'libXdamage.so.1': 'libxdamage1',
  'libXext.so.6': 'libxext6',
  'libXfixes.so.3': 'libxfixes3',
  'libXrandr.so.2': 'libxrandr2',
  'libgbm.so.1': 'libgbm1',
  'libpango-1.0.so.0': 'libpango-1.0-0',
  'libcairo.so.2': 'libcairo2',
  'libasound.so.2': 'libasound2',
  'libatspi.so.0': 'libatspi2.0-0',
});

export const PLAYWRIGHT_BROWSER_EXECUTABLE_ENV_KEYS = Object.freeze([
  'FCC_PLAYWRIGHT_BROWSER_EXECUTABLE',
  'FCC_CAPTURE_BROWSER_EXECUTABLE',
]);

export const PLAYWRIGHT_BROWSER_CHANNEL_ENV_KEYS = Object.freeze([
  'FCC_PLAYWRIGHT_BROWSER_CHANNEL',
  'FCC_CAPTURE_BROWSER_CHANNEL',
]);

export function buildHarnessBrowserEnv(baseEnv = process.env) {
  const env = { ...baseEnv };
  for (const key of SCRUBBED_ENV_KEYS) delete env[key];
  return env;
}

export function parseMissingLibraries(lddOutput) {
  return lddOutput
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.endsWith('=> not found'))
    .map((line) => line.split(' => ')[0])
    .filter(Boolean);
}

function readOsRelease() {
  try {
    return readFileSync('/etc/os-release', 'utf8')
      .split('\n')
      .filter((line) => line.includes('='))
      .slice(0, 8);
  } catch {
    return [];
  }
}

export function suggestedAptPackagesForLibraries(missingLibraries) {
  return [
    ...new Set(
      missingLibraries.map((name) => PLAYWRIGHT_LINUX_APT_PACKAGE_BY_LIBRARY[name]).filter(Boolean),
    ),
  ];
}

export function resolveHarnessBrowserExecutable(defaultExecutablePath, env = process.env) {
  for (const key of PLAYWRIGHT_BROWSER_EXECUTABLE_ENV_KEYS) {
    const value = env[key];
    if (typeof value === 'string' && value.trim() !== '') return value.trim();
  }
  return defaultExecutablePath;
}

export function resolveHarnessBrowserChannel(env = process.env) {
  for (const key of PLAYWRIGHT_BROWSER_CHANNEL_ENV_KEYS) {
    const value = env[key];
    if (typeof value === 'string' && value.trim() !== '') return value.trim();
  }
  return undefined;
}

export function resolveHarnessBrowserLaunchOptions(defaultExecutablePath, env = process.env) {
  const channel = resolveHarnessBrowserChannel(env);
  if (channel) return { channel };
  const executablePath = resolveHarnessBrowserExecutable(defaultExecutablePath, env);
  return executablePath ? { executablePath } : {};
}

function readLdconfigCatalog() {
  const result = spawnSync('ldconfig', ['-p'], { encoding: 'utf8' });
  if (result.status !== 0) return {};
  const catalog = {};
  for (const line of `${result.stdout ?? ''}`.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed.includes('=>')) continue;
    const [left, right] = trimmed.split('=>', 2);
    const library = left.split(' ', 1)[0]?.trim();
    const path = right.trim();
    if (library && path && catalog[library] === undefined) catalog[library] = path;
  }
  return catalog;
}

export function describeHarnessBrowserRuntime(executablePath, env = buildHarnessBrowserEnv()) {
  const executableExists =
    typeof executablePath === 'string' && executablePath !== ''
      ? existsSync(executablePath)
      : false;
  const ldd = spawnSync('ldd', [executablePath], { encoding: 'utf8', env });
  const stdout = `${ldd.stdout ?? ''}${ldd.stderr ?? ''}`.trim();
  return {
    executablePath,
    executableExists,
    lddExitCode: ldd.status ?? 0,
    scrubbedEnvKeys: SCRUBBED_ENV_KEYS,
    missingLibraries: parseMissingLibraries(stdout),
    lddOutput: stdout,
    osRelease: readOsRelease(),
    timestamp: new Date().toISOString(),
  };
}

export function summarizeHarnessBrowserRuntime(executablePath, env = buildHarnessBrowserEnv()) {
  const diagnostics = describeHarnessBrowserRuntime(executablePath, env);
  const missingLibraries = diagnostics.missingLibraries;
  const ldconfigCatalog = readLdconfigCatalog();
  const catalogEntries = Object.fromEntries(
    missingLibraries
      .filter((name) => ldconfigCatalog[name] !== undefined)
      .map((name) => [name, ldconfigCatalog[name]]),
  );
  const inaccessibleCatalogLibraries = Object.fromEntries(
    Object.entries(catalogEntries).filter(([, path]) => !existsSync(path)),
  );
  return {
    ...diagnostics,
    ok:
      diagnostics.executableExists &&
      diagnostics.lddExitCode === 0 &&
      missingLibraries.length === 0,
    suggestedAptPackages: suggestedAptPackagesForLibraries(missingLibraries),
    browserExecutableEnvKeys: PLAYWRIGHT_BROWSER_EXECUTABLE_ENV_KEYS,
    catalogEntries,
    inaccessibleCatalogLibraries,
  };
}

export async function launchHarnessBrowser(browserType, options = {}) {
  const env = buildHarnessBrowserEnv();
  const launchOptions = resolveHarnessBrowserLaunchOptions(undefined, process.env);
  try {
    return await browserType.launch({
      ...options,
      ...launchOptions,
      env,
    });
  } catch (error) {
    const resolvedExecutablePath =
      'executablePath' in launchOptions && typeof launchOptions.executablePath === 'string'
        ? launchOptions.executablePath
        : browserType.executablePath();
    const diagnostics = summarizeHarnessBrowserRuntime(resolvedExecutablePath, env);
    console.error('[harness-browser-runtime]', JSON.stringify(diagnostics, null, 2));
    error.message = `${error.message}\n[harness-browser-runtime] ${JSON.stringify(diagnostics, null, 2)}`;
    throw error;
  }
}
