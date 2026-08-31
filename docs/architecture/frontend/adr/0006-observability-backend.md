# ADR-0006: Observability backend selection

**Status**: Proposed
**Date**: 2026-05-23
**Deciders**: shared web/platform maintainers + user
**Depends on**: ADR-0004 (distributed tracing SDK)

## Context

backend 가 OBS-0 ~ OBS-3 + P1-1 ~ P1-5 sprint 로 structured logging + W3C TraceContext + tracestate + Prometheus metrics + RUM-ready 형식 표준화. frontend 측 traces / errors / RUM / Core Web Vitals 의 destination backend 를 결정한다.

이전 contract-only sprint 의 결함:
- frontend observability `Sentry / OpenTelemetry / RUM` 완전 부재 — distributed tracing 단절
- 본 ADR 이 production-grade observability stack 의 SSOT 결정

### Constraints

- backend 의 `structured.jsonl` (OBS-0) 가 Loki / Datadog / ELK / OTel collector 호환 — frontend 도 같은 collector 가 받을 수 있어야 함
- Core Web Vitals (LCP / FID / INP / CLS / TTFB) 측정 + 전송
- error / exception → grouping + alerting
- session replay (선택사항, privacy 검토 후)
- bundle size 영향 minimal
- vendor lock-in 최소화 (OTel-compatible 우선)

## Decision

**3-layer observability stack 채택**:

| Layer | 도구 | 근거 |
|-------|-----|------|
| **Traces** | `@opentelemetry/sdk-trace-web` (ADR-0004) → **OTel collector** (vendor-neutral) | W3C TraceContext + tracestate + ParentBased sampler chain |
| **Errors** | `@sentry/browser` SDK → Sentry SaaS or self-hosted Sentry | error grouping + alerting + release tracking 표준 |
| **RUM / Core Web Vitals** | `web-vitals` lib → OTel collector (또는 Sentry Performance) | LCP/FID/INP/CLS/TTFB 5 metric 표준 |

### OTel collector backend (별도 deployment)

- `otel-collector` 가 frontend trace + backend trace 통합 수신
- export 대상: Jaeger / Tempo / Honeycomb / Datadog 중 운영팀이 선택 (vendor-neutral)
- 본 ADR 은 collector deployment 만 결정 — 최종 destination 은 운영 단계 결정

### Sentry 사용 패턴

```ts
// src/observability/sentry.ts
import * as Sentry from '@sentry/browser';
import { getRuntimeConfig } from '../config/runtime';

export function initSentry(): void {
  const config = getRuntimeConfig();
  if (!config.sentryDsn) return;  // optional, dev 환경 disable
  Sentry.init({
    dsn: config.sentryDsn,
    environment: config.environmentName,
    release: config.buildVersion,
    tracesSampleRate: config.traceSampleRatio ?? 0.1,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.replayIntegration({
        maskAllText: true,        // privacy
        blockAllMedia: true,
      }),
    ],
    // W3C TraceContext propagation (Sentry 가 traceparent 자동 inject)
    tracePropagationTargets: [config.apiBaseUrl, /.*/],
  });
}
```

### Core Web Vitals 패턴

```ts
// src/observability/web-vitals.ts
import { onCLS, onFID, onINP, onLCP, onTTFB } from 'web-vitals';
import { trace } from '@opentelemetry/api';

function reportMetric(metric: any): void {
  const span = trace.getActiveSpan();
  span?.setAttributes({
    [`web_vitals.${metric.name}`]: metric.value,
    [`web_vitals.${metric.name}.rating`]: metric.rating,
  });
  // 또는 Sentry custom transaction
}

export function initWebVitals(): void {
  onCLS(reportMetric);
  onFID(reportMetric);
  onINP(reportMetric);
  onLCP(reportMetric);
  onTTFB(reportMetric);
}
```

## Consequences

### Positive
- distributed tracing end-to-end (frontend → platform → provider) — Sentry / OTel collector 둘 다 수신
- Core Web Vitals 5 metric 정량 (Lighthouse CI gate 와 정합 — Sprint S8)
- error grouping + release tracking (Sentry source map upload)
- vendor lock-in 최소 (OTel collector 가 vendor-neutral)
- session replay 옵션 (privacy 검토 후 활성화)

### Negative
- Sentry SaaS 비용 (free tier 또는 self-host)
- bundle size: `@sentry/browser` ~ 30-40 KB gzipped + `@opentelemetry/sdk-trace-web` ~ 30-40 KB gzipped = total 60-80 KB → Core Web Vitals LCP 영향 측정 필요
- privacy 검토: session replay / breadcrumb 가 PII 노출 가능 — `maskAllText` / `blockAllMedia` 기본 활성화
- OTel collector deployment 운영 비용 (별도 Kubernetes pod 또는 sidecar)

## Alternatives Considered

### Datadog Browser RUM
- **rejected because**: vendor lock-in. OTel-collector 가 vendor-neutral.

### New Relic Browser
- **rejected because**: vendor lock-in + Sentry 보다 error grouping 약함

### LogRocket / FullStory (session replay 중심)
- **rejected because**: privacy + 비용 + observability stack 의 한 측면만 cover

### Only OTel collector (no Sentry)
- **rejected because**: error grouping / alerting / release tracking 직접 구축 비용 큼. Sentry 는 그 specialized 도구로 표준.

### Only Sentry (no OTel)
- **rejected because**: backend 가 OTel-native 인데 frontend 만 Sentry-only 면 vendor split. OTel collector 가 vendor-neutral pipe.

## Revisit Conditions

1. **Sentry SaaS 비용 > budget** → self-host Sentry 또는 GlitchTip
2. **frontend bundle size > 200 KB gzipped** → SDK lazy load 또는 축소
3. **session replay privacy 정책 변경** → 비활성화
4. **OTel collector ecosystem maturity** → Sentry 도 OTel collector 로 통합 검토 (Sentry가 OTel-native 로 전환 중)

## References

- [OpenTelemetry browser](https://github.com/open-telemetry/opentelemetry-js)
- [Sentry Browser SDK](https://docs.sentry.io/platforms/javascript/)
- [web-vitals](https://github.com/GoogleChrome/web-vitals)
- backend OBS-0 structured logging SSOT
- backend OBS-2 phase 3 ApiMetricsRegistry SSOT
- backend OBS-3 W3C tracestate SSOT
