# SLO — FCC API Observability

Status: Active operations contract (B4 — backend architecture roadmap, 2026-06-13)

Last updated: 2026-06-13

> ⚠️ **2026-09-05 정합 확인 — 이 문서는 `platform-api-node` 를 모릅니다.**
> 2026-09-04 에 평문 HTTP 대응으로 `platform-api` 가 **두 인스턴스**로 나뉘었습니다
> (`platform-api` = `local_jwt` 브라우저용 · `platform-api-node` = `oidc_jwt` 챔버
> 노드용). 이 문서의 규칙·SLO 는 그 이전에 쓰였습니다.
> **미확인 사항**: 새 인스턴스가 스크레이프 대상에 등록됐는지, 알림이 그것의 장애를
> 잡는지 — 이 저장소에는 Prometheus 배포가 없어 여기서 판정할 수 없습니다.
> 중앙 PC 실사가 필요하며 설계서 §9 에 등재돼 있습니다.
> 근거: [`.claude/evaluations/2026-09-04-http-dual-auth-node-lane.md`](../../.claude/evaluations/2026-09-04-http-dual-auth-node-lane.md)


## Purpose

This document is the single source of truth for the service-level objectives of
the FCC web surfaces (Session / Headless / Platform APIs). The
[alert rules](prometheus-alert-rules.md) and their
[runbook](runbook-api-observability.md) are derived from these objectives — an
alert threshold must never contradict an SLO target here.

All objectives are computed from `ApiMetricsRegistry`
(`src/application/common/metrics_registry.py`) series; namespaces are
`fcc_session`, `fcc_headless`, `fcc_platform`. SLO indicators reference the same
registry-rendered metric names as the alert rules (no hand-typed metric
strings — enforced by `tests/test_observability_alerting_parity.py`).

## Service level indicators and objectives

| SLI | Definition | Objective (28-day window) | Alert |
| --- | --- | --- | --- |
| Availability | `1 − (error requests / total requests)` | ≥ 99.5% success | `FccApiRequestErrorRateWarning` / `FccApiRequestErrorRateCritical` |
| Latency (p95) | p95 of `{namespace}_request_total` histogram | ≤ 500 ms | `FccApiLatencyP95Warning` |
| Authorization health | `denied requests / total requests` | ≤ baseline × 3 (no sustained spike) | `FccApiRequestDeniedRateWarning` |
| WebSocket stream health | Session WS error-closes / total closes | ≤ 10% of closes | `FccWebSocketCloseErrorRateWarning` |

### Availability SLI

Success ratio across all three surfaces. The error budget is `1 − 0.995 =
0.5%` of requests over the 28-day window.

```promql
1 - (
  (
      sum(rate(fcc_session_request_total_count{status="error"}[28d]))
    + sum(rate(fcc_headless_request_total_count{status="error"}[28d]))
    + sum(rate(fcc_platform_request_total_count{status="error"}[28d]))
  )
  /
  clamp_min(
      sum(rate(fcc_session_request_total_count[28d]))
    + sum(rate(fcc_headless_request_total_count[28d]))
    + sum(rate(fcc_platform_request_total_count[28d]))
  , 1e-9)
)
```

### Latency SLI (p95 ≤ 500 ms)

Histogram-derived p95 per surface; the budget is breached when any surface's
p95 stays above 500 ms. The 500 ms target matches the
`scripts/headless_api_benchmark.py --max-p95-ms 500` smoke gate.

```promql
histogram_quantile(0.95, sum by (le) (rate(fcc_session_request_total_bucket[28d])))
histogram_quantile(0.95, sum by (le) (rate(fcc_headless_request_total_bucket[28d])))
histogram_quantile(0.95, sum by (le) (rate(fcc_platform_request_total_bucket[28d])))
```

### Authorization health SLI

Denied (`status="denied"`) ratio must not spike beyond `baseline × 3`. The
denied series is a health/misconfiguration signal, not a hard availability
budget.

```promql
(
    sum(rate(fcc_session_request_total_count{status="denied"}[28d]))
  + sum(rate(fcc_headless_request_total_count{status="denied"}[28d]))
  + sum(rate(fcc_platform_request_total_count{status="denied"}[28d]))
)
/
clamp_min(
    sum(rate(fcc_session_request_total_count[28d]))
  + sum(rate(fcc_headless_request_total_count[28d]))
  + sum(rate(fcc_platform_request_total_count[28d]))
, 1e-9)
```

### WebSocket stream health SLI

Session-API WebSocket connections should close cleanly. Error closes
(`reason="error"`) should be ≤ 10% of all closes. WebSocket metrics exist only
for the Session API (`enable_websocket=True`).

```promql
sum(rate(fcc_session_ws_connections_closed_total{reason="error"}[28d]))
/
clamp_min(sum(rate(fcc_session_ws_connections_closed_total[28d])), 1e-9)
```

## Error budget policy

- The availability error budget is `0.5%` of requests over 28 days.
- When `FccApiRequestErrorRateCritical` fires, the budget is being spent at a
  paging rate — treat as an outage (see the runbook).
- A burned error budget freezes risky deploys for the affected surface until
  the budget recovers. Track budget burn with the availability SLI above.

## Maintaining this file

- An SLO target change must be reflected in the alert thresholds it governs
  (e.g. the 500 ms latency target ↔ `FccApiLatencyP95Warning`).
- Every metric token used here must be a name `ApiMetricsRegistry.render()`
  emits; the parity invariant rejects a hand-typed metric string.
