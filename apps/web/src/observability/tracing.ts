import { ZoneContextManager } from '@opentelemetry/context-zone';
import { W3CTraceContextPropagator } from '@opentelemetry/core';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { registerInstrumentations } from '@opentelemetry/instrumentation';
import { FetchInstrumentation } from '@opentelemetry/instrumentation-fetch';
import { XMLHttpRequestInstrumentation } from '@opentelemetry/instrumentation-xml-http-request';
import {
  AlwaysOnSampler,
  BatchSpanProcessor,
  ParentBasedSampler,
  TraceIdRatioBasedSampler,
} from '@opentelemetry/sdk-trace-base';
import { WebTracerProvider } from '@opentelemetry/sdk-trace-web';

import { isTracingEnabled } from './enablement';

import type { RuntimeConfig } from '@/config/runtime';

/**
 * OpenTelemetry browser SDK bootstrap — ADR-0004 SSOT.
 *
 * Mirrors backend P1-B (ParentBased(AlwaysOn) sampler) + P1-C
 * (TraceIdRatioBased env sampler) decision so frontend ↔ backend tracing
 * shares the same sampling semantics — distributed trace continuity.
 *
 * Outbound HTTP propagation uses W3CTraceContextPropagator which injects
 * `traceparent` (and `tracestate` when present), interop with backend
 * `application/common/outbound_http.py::build_outbound_traceparent_headers`
 * SSOT.
 *
 * No-op when `otelCollectorUrl` is null (dev or self-hosted deployment
 * without an OTel collector).
 *
 * **Loaded on demand** (wave `fe-w4-bundle-observability-cost`, 2026-07-31):
 * this module is the only static importer of the OpenTelemetry web SDK, and
 * `observability/bootstrap.ts` `import()`s it only when
 * `isTracingEnabled(config)` holds. The guard below is *the same predicate*,
 * not a copy of it — a direct caller that bypasses the loader still no-ops
 * without a collector, and there is exactly one place where the condition is
 * written down.
 */
export function initTracing(config: RuntimeConfig): void {
  if (!isTracingEnabled(config)) {
    // Dev mode or no collector configured — instrumentation off.
    return;
  }

  const provider = new WebTracerProvider({
    sampler: new ParentBasedSampler({
      root:
        config.traceSampleRatio >= 1
          ? new AlwaysOnSampler()
          : new TraceIdRatioBasedSampler(config.traceSampleRatio),
    }),
  });

  provider.addSpanProcessor(
    new BatchSpanProcessor(new OTLPTraceExporter({ url: config.otelCollectorUrl }), {
      maxQueueSize: 1024,
      maxExportBatchSize: 64,
      scheduledDelayMillis: 5000,
      exportTimeoutMillis: 30000,
    }),
  );

  provider.register({
    contextManager: new ZoneContextManager(),
    propagator: new W3CTraceContextPropagator(),
  });

  registerInstrumentations({
    instrumentations: [
      new FetchInstrumentation({
        // Propagate traceparent to all backend hosts. CORS preflight headers
        // must include `traceparent`/`tracestate` server-side (Sprint S4 backend wiring).
        propagateTraceHeaderCorsUrls: [new RegExp(escapeRegExp(new URL(config.apiBaseUrl).origin))],
        clearTimingResources: true,
      }),
      new XMLHttpRequestInstrumentation({
        propagateTraceHeaderCorsUrls: [new RegExp(escapeRegExp(new URL(config.apiBaseUrl).origin))],
      }),
    ],
  });

  // Flush spans on page unload so short sessions still report.
  if (typeof window !== 'undefined') {
    window.addEventListener('pagehide', () => {
      void provider.forceFlush().catch(() => {
        /* best-effort, never block unload */
      });
    });
  }
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
