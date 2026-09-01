# Prometheus Alert Rules — FCC API Observability

Status: Active operations contract (B4 — backend architecture roadmap, 2026-06-13)

Last updated: 2026-06-13

## Purpose

The FCC web surfaces (Session / Headless / Platform APIs) already export
Prometheus metrics through `ApiMetricsRegistry`
(`src/application/common/metrics_registry.py`) and the
`/{session,headless,platform}/metrics` endpoints. The metrics are exposed but,
until this document, there were **no alert thresholds and no response
procedures** — so the signal was operationally inert.

This file is the alert-rule SSOT. Every alert here:

1. references only metric names produced by `ApiMetricsRegistry.render()` —
   the registry is the authority, never a hand-typed metric string;
2. carries `severity` / `for` / a threshold;
3. ships a **baseline measurement PromQL** so the threshold is set from
   observed data, not an arbitrary number;
4. has a matching escalation entry in
   [`runbook-api-observability.md`](runbook-api-observability.md) (alert↔runbook
   parity is machine-checked).

The metric/label vocabulary is owned by the registry, not by this document:

| Concept | Source of truth (`metrics_registry.py`) |
| --- | --- |
| Namespace prefix | `METRICS_NAMESPACE_SESSION` (`fcc_session`), `METRICS_NAMESPACE_HEADLESS` (`fcc_headless`), `METRICS_NAMESPACE_PLATFORM` (`fcc_platform`) |
| Request histogram | `{namespace}_request_total` → `_bucket` / `_count` / `_sum` (labels `operation`, `status`) |
| WS gauge | `{namespace}_ws_connections` (label `state`) — Session and Platform APIs (`enable_websocket=True`) |
| WS close counter | `{namespace}_ws_connections_closed_total` (label `reason`) — Session and Platform APIs |
| Chamber availability gauge | `fcc_platform_chamber_count` (label `availability` = `idle`/`in_use`/`offline`) — Platform API, refreshed at scrape from the chamber registry SSOT (`application/platform/chamber_metrics.py`) |
| Chamber heartbeat age gauge | `fcc_platform_chamber_heartbeat_age_max_seconds` (no labels) — Platform API max heartbeat age across the fleet |
| `status` label values | `STATUS_OK` (`ok`) / `STATUS_DENIED` (`denied`) / `STATUS_ERROR` (`error`) |
| `reason` label values | `WS_CLOSE_REASON_NORMAL` / `_ERROR` / `_TIMEOUT` / `_DENIED` |

Because the Headless registry is constructed with `enable_websocket=False`, the
WebSocket metrics that exist are the enabled Session and Platform namespaces:
`fcc_session_ws_connections*` and `fcc_platform_ws_connections*`. Alerting on a
non-existent `fcc_headless_ws_*` series would be a hardcoding bug; the parity
invariant rejects any metric token outside the registry-rendered set.

## Measurement methodology (set thresholds from data, not guesses)

Thresholds below are **initial placeholders** chosen conservatively. Before an
alert is promoted to paging, replace its threshold with a value derived from
production data, following the equipment-grade `max_over_time × N` rule:

- **Baseline** = the alert's own ratio/quantile expression, sampled over a
  representative window: `max_over_time( (<expr>)[7d:1m] )`. The subquery
  selector `[7d:1m]` **must wrap the whole `<expr>` in parentheses** — in PromQL
  the `[range:resolution]` operator binds to the immediately preceding
  expression, so `max_over_time( numerator / clamp_min(denom)[7d:1m] )` would
  apply the subquery to the denominator alone (a range-vector / instant-vector
  type error). Parenthesise the entire ratio: `max_over_time( ((num)/(denom))[7d:1m] )`.
  This is the worst steady-state value the system reaches when healthy.
- **Warning threshold** = `baseline × 3`. Three times the worst healthy value
  is anomalous but not yet customer-visible-severe.
- **Critical threshold** = `warning × 10`. A critical alert fires only when the
  signal is an order of magnitude past the warning band.

Each alert section embeds the exact baseline PromQL to run. Record the measured
baseline in the runbook's change log when you tighten a threshold.

## Alert rules

The blocks below are valid Prometheus rule-group entries (`groups[].rules[]`).

### FccApiRequestErrorRateWarning

Fraction of API requests completing with `status="error"` across all three
surfaces is elevated.

```yaml
- alert: FccApiRequestErrorRateWarning
  expr: |
    (
        sum(rate(fcc_session_request_total_count{status="error"}[5m]))
      + sum(rate(fcc_headless_request_total_count{status="error"}[5m]))
      + sum(rate(fcc_platform_request_total_count{status="error"}[5m]))
    )
    /
    clamp_min(
        sum(rate(fcc_session_request_total_count[5m]))
      + sum(rate(fcc_headless_request_total_count[5m]))
      + sum(rate(fcc_platform_request_total_count[5m]))
    , 1e-9)
    > 0.03
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "API error ratio above warning band"
    runbook: "docs/operations/runbook-api-observability.md#alert-fccapirequesterrorratewarning"
```

**Baseline (measure, then set threshold = baseline × 3):**

```promql
max_over_time(
  (
    (
        sum(rate(fcc_session_request_total_count{status="error"}[5m]))
      + sum(rate(fcc_headless_request_total_count{status="error"}[5m]))
      + sum(rate(fcc_platform_request_total_count{status="error"}[5m]))
    )
    /
    clamp_min(
        sum(rate(fcc_session_request_total_count[5m]))
      + sum(rate(fcc_headless_request_total_count[5m]))
      + sum(rate(fcc_platform_request_total_count[5m]))
    , 1e-9)
  )[7d:1m]
) * 3
```

### FccApiRequestErrorRateCritical

Same error ratio at the critical band (`warning × 10`). Pages on-call.

```yaml
- alert: FccApiRequestErrorRateCritical
  expr: |
    (
        sum(rate(fcc_session_request_total_count{status="error"}[5m]))
      + sum(rate(fcc_headless_request_total_count{status="error"}[5m]))
      + sum(rate(fcc_platform_request_total_count{status="error"}[5m]))
    )
    /
    clamp_min(
        sum(rate(fcc_session_request_total_count[5m]))
      + sum(rate(fcc_headless_request_total_count[5m]))
      + sum(rate(fcc_platform_request_total_count[5m]))
    , 1e-9)
    > 0.3
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "API error ratio at critical band (≈10× warning)"
    runbook: "docs/operations/runbook-api-observability.md#alert-fccapirequesterrorratecritical"
```

**Baseline (critical = warning × 10; measure warning baseline, then × 30):**

```promql
max_over_time(
  (
    (
        sum(rate(fcc_session_request_total_count{status="error"}[5m]))
      + sum(rate(fcc_headless_request_total_count{status="error"}[5m]))
      + sum(rate(fcc_platform_request_total_count{status="error"}[5m]))
    )
    /
    clamp_min(
        sum(rate(fcc_session_request_total_count[5m]))
      + sum(rate(fcc_headless_request_total_count[5m]))
      + sum(rate(fcc_platform_request_total_count[5m]))
    , 1e-9)
  )[7d:1m]
) * 30
```

### FccApiRequestDeniedRateWarning

Fraction of requests rejected with `status="denied"` (401/403) is elevated — a
spike usually means an auth/RBAC misconfiguration or a credential rotation gone
wrong, not an attack-by-default.

```yaml
- alert: FccApiRequestDeniedRateWarning
  expr: |
    (
        sum(rate(fcc_session_request_total_count{status="denied"}[5m]))
      + sum(rate(fcc_headless_request_total_count{status="denied"}[5m]))
      + sum(rate(fcc_platform_request_total_count{status="denied"}[5m]))
    )
    /
    clamp_min(
        sum(rate(fcc_session_request_total_count[5m]))
      + sum(rate(fcc_headless_request_total_count[5m]))
      + sum(rate(fcc_platform_request_total_count[5m]))
    , 1e-9)
    > 0.2
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "API denied (401/403) ratio above warning band"
    runbook: "docs/operations/runbook-api-observability.md#alert-fccapirequestdeniedratewarning"
```

**Baseline (measure, then set threshold = baseline × 3):**

```promql
max_over_time(
  (
    (
        sum(rate(fcc_session_request_total_count{status="denied"}[5m]))
      + sum(rate(fcc_headless_request_total_count{status="denied"}[5m]))
      + sum(rate(fcc_platform_request_total_count{status="denied"}[5m]))
    )
    /
    clamp_min(
        sum(rate(fcc_session_request_total_count[5m]))
      + sum(rate(fcc_headless_request_total_count[5m]))
      + sum(rate(fcc_platform_request_total_count[5m]))
    , 1e-9)
  )[7d:1m]
) * 3
```

### FccApiLatencyP95Warning

p95 request latency (from the histogram buckets, in milliseconds) on any
surface exceeds the SLO target. Threshold `500` matches the
`scripts/headless_api_benchmark.py --max-p95-ms 500` smoke gate and the
[SLO document](slo.md).

```yaml
- alert: FccApiLatencyP95Warning
  expr: |
    histogram_quantile(0.95, sum by (le) (rate(fcc_session_request_total_bucket[5m])))  > 500
    or
    histogram_quantile(0.95, sum by (le) (rate(fcc_headless_request_total_bucket[5m]))) > 500
    or
    histogram_quantile(0.95, sum by (le) (rate(fcc_platform_request_total_bucket[5m]))) > 500
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "API p95 latency above SLO target (500ms)"
    runbook: "docs/operations/runbook-api-observability.md#alert-fccapilatencyp95warning"
```

**Baseline (track p95 trend; SLO target is the hard ceiling):**

The alert fires when **any** of the three surfaces breaches the SLO target, so
the baseline must track the worst surface, not just Session. Each
`histogram_quantile(...)` consumes the `le` label and returns a label-less
vector, so a bare `A or B or C` would collide on the empty label set and keep
only the first surface — `label_replace` stamps a distinguishing `surface`
label before the union so `max(...)` aggregates the true worst-case p95 across
Session / Headless / Platform, matching the alert's per-surface OR.

```promql
max_over_time(
  (
    max(
        label_replace(histogram_quantile(0.95, sum by (le) (rate(fcc_session_request_total_bucket[5m]))),  "surface", "session",  "", "")
      or
        label_replace(histogram_quantile(0.95, sum by (le) (rate(fcc_headless_request_total_bucket[5m]))), "surface", "headless", "", "")
      or
        label_replace(histogram_quantile(0.95, sum by (le) (rate(fcc_platform_request_total_bucket[5m]))), "surface", "platform", "", "")
    )
  )[7d:1m]
)
```

### FccWebSocketCloseErrorRateWarning

Fraction of Session-API WebSocket disconnects closed with `reason="error"`
(exception during streaming) is elevated. WebSocket metrics also exist for the
Platform API (`enable_websocket=True`); Headless remains HTTP-only.

```yaml
- alert: FccWebSocketCloseErrorRateWarning
  expr: |
    sum(rate(fcc_session_ws_connections_closed_total{reason="error"}[5m]))
    /
    clamp_min(sum(rate(fcc_session_ws_connections_closed_total[5m])), 1e-9)
    > 0.1
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Session WebSocket error-close ratio above warning band"
    runbook: "docs/operations/runbook-api-observability.md#alert-fccwebsocketcloseerrorratewarning"
```

**Baseline (measure, then set threshold = baseline × 3):**

```promql
max_over_time(
  (
    sum(rate(fcc_session_ws_connections_closed_total{reason="error"}[5m]))
    /
    clamp_min(sum(rate(fcc_session_ws_connections_closed_total[5m])), 1e-9)
  )[7d:1m]
) * 3
```

### FccChamberOfflineWarning

One or more registered chambers are derived `offline` (no heartbeat within TTL).
A distributed-measurement fleet with offline nodes loses capacity; sustained
offline indicates a powered-down/partitioned chamber PC or a stalled node
process. Metric: `fcc_platform_chamber_count{availability="offline"}` (gauge,
refreshed at scrape time from the chamber registry availability SSOT).

Alert on the offline **fraction** (scales with fleet size, unlike an absolute
count): the share of registered chambers that are offline.

```yaml
- alert: FccChamberOfflineWarning
  expr: |
    sum(fcc_platform_chamber_count{availability="offline"})
    /
    clamp_min(sum(fcc_platform_chamber_count), 1e-9)
    > 0.5
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "More than half the chamber fleet offline for 10m"
    runbook: "docs/operations/runbook-api-observability.md#alert-fccchamberofflinewarning"
```

**Baseline (measure, then set threshold = baseline × 3):**

```promql
max_over_time(
  (
    sum(fcc_platform_chamber_count{availability="offline"})
    /
    clamp_min(sum(fcc_platform_chamber_count), 1e-9)
  )[7d:1m]
) * 3
```

### FccChamberHeartbeatStaleWarning

The oldest chamber heartbeat across the fleet is approaching/exceeding the
heartbeat TTL (`DEFAULT_HEARTBEAT_TTL_SECONDS` = 90s), i.e. a chamber is about
to be (or has been) derived `offline`. Metric:
`fcc_platform_chamber_heartbeat_age_max_seconds` (gauge, max seconds since last
heartbeat across registered chambers). The 90s placeholder ties to the domain
TTL; tighten from the measured baseline.

Alert on the max heartbeat age as a **ratio to the TTL** (the `/ 90` denominator
ties to `DEFAULT_HEARTBEAT_TTL_SECONDS`): a ratio > 1 means a chamber has
exceeded its heartbeat window and is being derived offline.

```yaml
- alert: FccChamberHeartbeatStaleWarning
  expr: |
    fcc_platform_chamber_heartbeat_age_max_seconds
    /
    clamp_min(90, 1e-9)
    > 1
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Max chamber heartbeat age exceeds TTL"
    runbook: "docs/operations/runbook-api-observability.md#alert-fccchamberheartbeatstalewarning"
```

**Baseline (measure, then set threshold = baseline × 3):**

```promql
max_over_time(
  (
    fcc_platform_chamber_heartbeat_age_max_seconds
    /
    clamp_min(90, 1e-9)
  )[7d:1m]
) * 3
```

## Maintaining this file

- Adding an alert here **requires** a matching `## Alert: <Name>` section in the
  runbook (`tests/test_observability_alerting_parity.py` fails on an orphan).
- Every metric token in an `expr`/baseline must be a name that
  `ApiMetricsRegistry.render()` emits — the same invariant rejects a typo or a
  WebSocket metric on an HTTP-only namespace.
- Thresholds are placeholders until a baseline is measured. Record the measured
  baseline and the date you tightened a threshold in the runbook change log.
