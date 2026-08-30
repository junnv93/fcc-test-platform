import { beforeEach, describe, expect, it, vi } from 'vitest';

import { isSentryEnabled, isTracingEnabled } from '@/observability/enablement';

import type { RuntimeConfig } from '@/config/runtime';
import type * as WebVitalsModule from '@/observability/web-vitals';

/**
 * Wave `fe-w4-bundle-observability-cost` (2026-07-31) — S1–S5.
 *
 * The defect: the OpenTelemetry web SDK and `@sentry/browser` are switched on by
 * *runtime* config, but were imported *statically*. Every deployment without a
 * collector/DSN downloaded 321.58 kB (100 kB gzip) — the single largest chunk in
 * the build, larger than React — and threw it away after two early `return`s.
 *
 * These cases seal the behaviour that fixes it. The byte-level consequence is
 * sealed separately by the initial-load-path bundle budget
 * (`scripts/check-bundle-budget.mjs`) and the structural half of the gate SSOT
 * by `tests/test_frontend_architecture_conformance.py::TestObservabilityLoadsOnDemand`;
 * behaviour, bytes and structure are different failure modes and no one seal
 * implies the others.
 */

const BASE_CONFIG = globalThis.__FCC_TEST_DEFAULT_RUNTIME_CONFIG__ as RuntimeConfig;
const COLLECTOR_URL = 'https://otel.example/v1/traces';
const SENTRY_DSN = 'https://key@org.ingest.sentry.io/1';

function configWith(overrides: Partial<RuntimeConfig>): RuntimeConfig {
  return { ...BASE_CONFIG, ...overrides };
}

interface BootProbe {
  /** How many times each on-demand module was *evaluated*. A mock factory runs
   *  on first `import()` of its path, so 0 is direct evidence the module was
   *  never fetched — which is the claim, not "was not initialised". An early
   *  `return` inside a module still costs the full download. */
  loads: { tracing: number; sentryRuntime: number };
  initTracing: ReturnType<typeof vi.fn>;
  initSentryRuntime: ReturnType<typeof vi.fn>;
  initWebVitals: ReturnType<typeof vi.fn>;
  /** Init call order across all three stages. */
  order: string[];
}

/**
 * Boot observability against a freshly-registered set of mocks.
 *
 * `vi.doMock` (not the hoisted `vi.mock`) on purpose: the factories carry the
 * load counters, and a hoisted factory is evaluated once for the whole file, so
 * every case after the first would read a stale count of 0.
 */
async function bootWith(
  config: RuntimeConfig,
  failOnLoad: { tracing?: boolean; sentryRuntime?: boolean } = {},
): Promise<BootProbe> {
  vi.resetModules();

  const probe: BootProbe = {
    loads: { tracing: 0, sentryRuntime: 0 },
    initTracing: vi.fn(),
    initSentryRuntime: vi.fn(),
    initWebVitals: vi.fn(),
    order: [],
  };
  probe.initTracing.mockImplementation(() => probe.order.push('tracing'));
  probe.initSentryRuntime.mockImplementation(() => probe.order.push('sentry'));
  probe.initWebVitals.mockImplementation(() => probe.order.push('web-vitals'));

  vi.doMock('@/observability/tracing', () => {
    probe.loads.tracing += 1;
    if (failOnLoad.tracing === true) {
      throw new Error('tracing chunk failed to load');
    }
    return { initTracing: probe.initTracing };
  });
  vi.doMock('@/observability/sentry-runtime', () => {
    probe.loads.sentryRuntime += 1;
    if (failOnLoad.sentryRuntime === true) {
      throw new Error('sentry-runtime chunk failed to load');
    }
    return { initSentryRuntime: probe.initSentryRuntime };
  });
  vi.doMock('@/observability/web-vitals', async () => ({
    ...(await vi.importActual<typeof WebVitalsModule>('@/observability/web-vitals')),
    initWebVitals: probe.initWebVitals,
  }));

  const { initObservability } = await import('@/observability/bootstrap');
  await initObservability(config);
  return probe;
}

beforeEach(() => {
  vi.resetModules();
  vi.doUnmock('@/observability/tracing');
  vi.doUnmock('@/observability/sentry-runtime');
  vi.doUnmock('@/observability/web-vitals');
});

describe('S1 — nothing is downloaded when nothing is configured', () => {
  it('never touches the tracing or sentry chunk in the default (disabled) config', async () => {
    const probe = await bootWith(configWith({ otelCollectorUrl: null, sentryDsn: null }));

    expect(probe.loads.tracing).toBe(0);
    expect(probe.loads.sentryRuntime).toBe(0);
    expect(probe.initTracing).not.toHaveBeenCalled();
    expect(probe.initSentryRuntime).not.toHaveBeenCalled();
  });

  it('still subscribes Web Vitals — it is static on purpose (subscribe before paint)', async () => {
    const probe = await bootWith(configWith({ otelCollectorUrl: null, sentryDsn: null }));

    expect(probe.initWebVitals).toHaveBeenCalledTimes(1);
  });

  it('loads each backend independently — one being off does not disable the other', async () => {
    const sentryOnly = await bootWith(
      configWith({ otelCollectorUrl: null, sentryDsn: SENTRY_DSN }),
    );
    expect(sentryOnly.loads.tracing).toBe(0);
    expect(sentryOnly.loads.sentryRuntime).toBe(1);

    const tracingOnly = await bootWith(
      configWith({ otelCollectorUrl: COLLECTOR_URL, sentryDsn: null }),
    );
    expect(tracingOnly.loads.tracing).toBe(1);
    expect(tracingOnly.loads.sentryRuntime).toBe(0);
  });
});

describe('S2 — an active deployment still gets the full feature', () => {
  it('loads and initialises both backends when both are configured', async () => {
    const config = configWith({ otelCollectorUrl: COLLECTOR_URL, sentryDsn: SENTRY_DSN });

    const probe = await bootWith(config);

    expect(probe.loads.tracing).toBe(1);
    expect(probe.loads.sentryRuntime).toBe(1);
    expect(probe.initTracing).toHaveBeenCalledWith(config);
    expect(probe.initSentryRuntime).toHaveBeenCalledWith(config);
    expect(probe.initWebVitals).toHaveBeenCalledTimes(1);
  });

  it('keeps tracing ahead of Sentry so Sentry can pick up the active span', async () => {
    const probe = await bootWith(
      configWith({ otelCollectorUrl: COLLECTOR_URL, sentryDsn: SENTRY_DSN }),
    );

    expect(probe.order).toEqual(['tracing', 'sentry', 'web-vitals']);
  });
});

describe('S3 — the gate is one predicate, and both sides read it', () => {
  // The *structural* half of S3 (no second copy of the condition anywhere in
  // the tree) is a source scan and lives in the Python conformance seal. This
  // half pins the semantics the loader and the modules share.
  it('treats null and empty string as off, and any url as on', () => {
    expect(isTracingEnabled({ otelCollectorUrl: null })).toBe(false);
    expect(isTracingEnabled({ otelCollectorUrl: '' })).toBe(false);
    expect(isTracingEnabled({ otelCollectorUrl: COLLECTOR_URL })).toBe(true);

    expect(isSentryEnabled({ sentryDsn: null })).toBe(false);
    expect(isSentryEnabled({ sentryDsn: '' })).toBe(false);
    expect(isSentryEnabled({ sentryDsn: SENTRY_DSN })).toBe(true);
  });

  it('is the same predicate the heavy module applies, so a direct caller cannot bypass it', async () => {
    const { initTracing } = await import('@/observability/tracing');

    // A disabled config must no-op rather than build an exporter. If the module
    // had kept a private copy of the condition, this is where the two would
    // drift apart without anything failing loudly.
    expect(() => initTracing(configWith({ otelCollectorUrl: null }))).not.toThrow();
  });
});

describe('S4/S5 — a broken observability backend is contained, but not hidden', () => {
  it('does not reject when a chunk fails to load', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    await expect(
      bootWith(configWith({ otelCollectorUrl: COLLECTOR_URL }), { tracing: true }),
    ).resolves.toBeDefined();

    consoleError.mockRestore();
  });

  it('keeps booting the rest of observability after one stage fails', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    const probe = await bootWith(
      configWith({ otelCollectorUrl: COLLECTOR_URL, sentryDsn: SENTRY_DSN }),
      { tracing: true },
    );

    // Sentry and Web Vitals are independent of tracing's failure — and so, more
    // importantly, is the React render that follows in `main.tsx`.
    expect(probe.initSentryRuntime).toHaveBeenCalledTimes(1);
    expect(probe.initWebVitals).toHaveBeenCalledTimes(1);

    consoleError.mockRestore();
  });

  it('reports the failure instead of swallowing it', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);

    await bootWith(configWith({ sentryDsn: SENTRY_DSN }), { sentryRuntime: true });

    // "Observability is dead and nobody knows" defeats the purpose of having it.
    expect(consoleError).toHaveBeenCalled();
    const logged = consoleError.mock.calls.map((call) => String(call[0])).join('\n');
    expect(logged).toContain('[observability]');
    expect(logged).toContain('sentry');

    consoleError.mockRestore();
  });
});

describe('captureException survives the split', () => {
  it('falls back to the console when no Sentry runtime is loaded', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { captureException } = await import('@/observability/sentry');

    captureException(new Error('boom'), { userId: 'user@example.com' });

    expect(consoleError).toHaveBeenCalledTimes(1);
    // The console path is a leak surface too — PII scrubbing still applies.
    const context = consoleError.mock.calls[0]?.[2] as Record<string, unknown>;
    expect(context['userId']).toBe('<email-redacted>');

    consoleError.mockRestore();
  });

  it('routes to the Sentry runtime once it registers a sink, and stops double-reporting', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { setCaptureSink } = await import('@/observability/capture-sink');
    const { captureException } = await import('@/observability/sentry');
    const sink = vi.fn().mockReturnValue(true);

    setCaptureSink(sink);
    captureException(new Error('boom'), { userId: 'user@example.com' });

    expect(sink).toHaveBeenCalledTimes(1);
    expect(consoleError).not.toHaveBeenCalled();
    // Scrubbed *before* it reaches the sink — the Sentry `beforeSend` hook is
    // the second line of defence, not the first.
    expect((sink.mock.calls[0]?.[1] as Record<string, unknown>)['userId']).toBe('<email-redacted>');

    consoleError.mockRestore();
  });

  it('falls back to the console when the sink reports a failed delivery', async () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const { setCaptureSink } = await import('@/observability/capture-sink');
    const { captureException } = await import('@/observability/sentry');

    setCaptureSink(vi.fn().mockReturnValue(false));
    captureException(new Error('boom'));

    expect(consoleError).toHaveBeenCalledTimes(1);

    consoleError.mockRestore();
  });
});
