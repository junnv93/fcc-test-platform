# ADR-0004: Distributed tracing frontend SDK

**Status**: Accepted — 구현됨

> ⚠️ **상태 갱신 2026-09-05** — 원문은 `Proposed` 였으나 **이 결정은 이미 구현돼 있다.**
> `@opentelemetry/sdk-trace-web` · `instrumentation-fetch` · `instrumentation-xml-http-request` · `exporter-trace-otlp-http` 실재. 서버 측은 `fcc_test_contracts/common/correlation.py` 가 W3C Trace Context 를 구현하므로 **양단이 이어진다**.
> 재구현하지 말 것. 현황은 `docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md` 참조.

**Date**: 2026-05-23
**Deciders**: shared web/platform maintainers + user
**Depends on**: ADR-0002 (stack)

## Context

backend 가 W3C TraceContext (Sprint P1-1 inbound + P0-3 outbound) + tracestate vendor extension (Sprint OBS-3, P1-A 2026-05-22) + OpenTelemetry ParentBased(AlwaysOn) sampler (P1-B 2026-05-25) + TraceIdRatioBased env sampler (P1-C 2026-05-25) 의 강한 SSOT 보유.

frontend 가 traceparent 헤더를 어떻게 발생 / 전파할지 결정 안 되면:
- `frontend → platform backend → provider backend` 의 trace chain 단절
- Sentry / OTel collector backend 에서 frontend session 의 root span 미연결
- RUM (Real User Monitoring) 가 distributed trace 와 별개의 silo

### Constraints

- backend `application/common/correlation.py::PARENT_SAMPLED_CTX` + `application/common/outbound_http.py::build_outbound_traceparent_headers` SSOT 와 정합
- W3C TraceContext spec (https://www.w3.org/TR/trace-context/) 엄격 준수
- bundle size 영향 최소화 (Core Web Vitals LCP 페널티 회피)
- TanStack Query + fetch 와 통합 (HTTP request interceptor)
- WebSocket connection 도 trace context 전파 (backend HIGH-3 WS traceparent header + query 2-transport 정합)

## Decision

**`@opentelemetry/sdk-trace-web` + `@opentelemetry/instrumentation-fetch` + `@opentelemetry/instrumentation-xml-http-request` + W3CTraceContextPropagator 채택**

### 구현 패턴

```ts
// src/observability/tracing.ts
import { WebTracerProvider } from '@opentelemetry/sdk-trace-web';
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { W3CTraceContextPropagator } from '@opentelemetry/core';
import { ParentBasedSampler, TraceIdRatioBasedSampler, AlwaysOnSampler } from '@opentelemetry/sdk-trace-base';
import { registerInstrumentations } from '@opentelemetry/instrumentation';
import { FetchInstrumentation } from '@opentelemetry/instrumentation-fetch';
import { getRuntimeConfig } from '../config/runtime';

export function initTracing(): void {
  const config = getRuntimeConfig();
  const provider = new WebTracerProvider({
    sampler: new ParentBasedSampler({
      root: new TraceIdRatioBasedSampler(config.traceSampleRatio ?? 0.1),
    }),
  });
  provider.addSpanProcessor(new BatchSpanProcessor(new OTLPTraceExporter({
    url: config.otelCollectorUrl,
  })));
  provider.register({ propagator: new W3CTraceContextPropagator() });
  registerInstrumentations({
    instrumentations: [
      new FetchInstrumentation({
        propagateTraceHeaderCorsUrls: [/.*/],  // platform backend 모두 propagate
      }),
    ],
  });
}
```

### backend 와의 정합

- `Sampler` 산업 표준 동일: `ParentBased(AlwaysOn)` default → `TraceIdRatioBased(ratio)` env override (P1-B / P1-C 패턴)
- `W3CTraceContextPropagator` 가 traceparent + tracestate (OBS-3) 자동 inject
- `BatchSpanProcessor` 가 traffic 폭주 시 backpressure

### WebSocket trace propagation

`HIGH-3` (2026-05-25) backend SSOT: `traceparent` header + query string 2-transport. frontend WS connection 시 `?traceparent=<header>` query 또는 `Sec-WebSocket-Protocol` subprotocol — backend가 지원하는 transport 확인 후 정합.

```ts
const ws = new WebSocket(`${wsBaseUrl}/session/events?traceparent=${currentTraceparent()}`);
```

## Consequences

### Positive
- backend → frontend → backend → provider chain trace 연결
- Sentry / OTel collector 가 root span (browser pageload) ↔ backend root span 연결
- P1-B / P1-C sampler 패턴 frontend 측 동일 (ParentBased default)
- `web-vitals` lib (Sprint S8) 와 same span 으로 export 가능

### Negative
- bundle size 영향: `@opentelemetry/sdk-trace-web` + instrumentation = ~30-40 KB gzipped — Core Web Vitals (LCP) 측정에 포함되어야 함
- BatchSpanProcessor flush latency — page unload 시 sendBeacon 추가 필요
- Instrumentation 가 sensitive header 노출 가능 — `clearTimingResources` 정책 검토

## Alternatives Considered

### Sentry Performance Monitoring
- **considered**: Sentry SDK 가 distributed tracing 내장 + RUM 강력
- **rejected because**: Sentry-proprietary protocol (deprecated in favor of OTel) — vendor lock-in. OTel + Sentry-collector 패턴이 vendor-neutral.

### Custom traceparent middleware (no SDK)
- **rejected because**: tracestate / sampler / BatchSpanProcessor 등 W3C spec full coverage 직접 구현 = bundle 대비 maintenance cost. 산업 표준 SDK 가 깔끔.

### Datadog Browser RUM
- **rejected because**: vendor lock-in. OTel-collector backend 가 vendor-neutral (Sentry / Datadog / Honeycomb / Jaeger 모두 collector 통해 수용).

## Revisit Conditions

1. **OpenTelemetry browser SDK v2** stable → API 변경 확인
2. **bundle size > 50 KB gzipped** → instrumentation 축소 또는 lazy load
3. **Core Web Vitals LCP > 2.5s** + tracing 이 원인 → BatchSpanProcessor 튜닝 또는 sampling 강화
4. **backend OTel collector deprecation** → vendor SDK 로 switch 검토

## References

- [W3C TraceContext spec](https://www.w3.org/TR/trace-context/)
- [@opentelemetry/sdk-trace-web](https://github.com/open-telemetry/opentelemetry-js)
- backend `application/common/correlation.py` — W3C TraceContext SSOT (P1-1)
- backend `application/common/outbound_http.py::build_outbound_traceparent_headers` — outbound SSOT (P0-3)
- backend `application/common/trace_sampler.py` — ParentBased + TraceIdRatioBased + env sampler (P1-B + P1-C)
- backend HIGH-3 WS traceparent (header + query 2-transport)
