# Runbook — FCC API Observability Alerts

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

This runbook gives on-call a deterministic, three-step response for each alert
defined in [`prometheus-alert-rules.md`](prometheus-alert-rules.md). Every alert
in that file has exactly one `## Alert: <Name>` section here, and vice versa —
the parity is machine-checked by
`tests/test_observability_alerting_parity.py`.

Each section follows the same shape:

1. **즉시 행동 (Immediate)** — confirm the alert with a PromQL/log query; decide
   blast radius in under a minute.
2. **조사 (Investigate)** — narrow to the surface/operation/cause.
3. **완화 (Mitigate)** — the action that stops the bleeding, plus the durable
   follow-up.

Metric and label names below come from `ApiMetricsRegistry`
(`src/application/common/metrics_registry.py`); the namespaces are `fcc_session`,
`fcc_headless`, `fcc_platform`. Scrape any surface directly with:

```bash
curl -s http://<host>:<port>/session/metrics
curl -s http://<host>:<port>/headless/metrics
curl -s http://<host>:<port>/platform/metrics
```

## Alert: FccApiRequestErrorRateWarning

The error (`status="error"`) ratio across the three APIs is above the warning
band for 10m.

**즉시 행동 (Immediate)** — confirm and find which surface dominates:

```promql
sum by (job) (rate(fcc_session_request_total_count{status="error"}[5m]))
sum by (job) (rate(fcc_headless_request_total_count{status="error"}[5m]))
sum by (job) (rate(fcc_platform_request_total_count{status="error"}[5m]))
```

Pull the structured JSON logs for the loudest surface and filter on
`level=ERROR`. The `traceparent` correlation id links a failing request to its
downstream spans.

**조사 (Investigate)** — break the error rate down by `operation` to find the
failing route, then read its handler/log path:

```promql
topk(5, sum by (operation) (rate(fcc_session_request_total_count{status="error"}[5m])))
```

Distinguish a code regression (recent deploy → check the release that preceded
the `for:` window) from a dependency failure (DB lock, file-server mount,
instrument I/O) surfaced in the logs.

**완화 (Mitigate)** — if a deploy caused it, roll back that surface; if a
dependency caused it, restore the dependency (e.g. re-mount artifact roots,
clear a stuck SQLite WAL). Durable follow-up: add/adjust the alert baseline once
the incident is closed, and file the root cause.

## Alert: FccApiRequestErrorRateCritical

Same error ratio at the critical band (≈10× warning), paging on-call. Treat as
an active outage.

**즉시 행동 (Immediate)** — confirm the critical ratio and declare an incident:

```promql
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
```

**조사 (Investigate)** — same `operation` breakdown as the warning runbook, but
in parallel check whether `FccApiLatencyP95Warning` is also firing (a saturated
backend produces both). Check host CPU/memory and the DB engine state.

**완화 (Mitigate)** — fastest safe rollback or traffic shed for the affected
surface; if it is dependency saturation, scale/restore the dependency. Do not
silence the alert — resolve the signal. Post-incident: confirm the threshold
band still matches the measured baseline.

## Alert: FccApiRequestDeniedRateWarning

The denied (`status="denied"`, i.e. 401/403) ratio is elevated for 10m. Most
often a misconfiguration, not an attack.

**즉시 행동 (Immediate)** — confirm and locate:

```promql
topk(5, sum by (operation) (rate(fcc_session_request_total_count{status="denied"}[5m])))
```

Check whether the denials cluster on one operation (a single mis-scoped route)
or spread across all (a broken auth mode / expired key).

**조사 (Investigate)** — correlate with the most recent auth/RBAC config change
(`FCC_HEADLESS_AUTH_MODE`, OIDC issuer/audience/JWKS, trusted-header names, or a
platform membership change). Read the structured logs for the denied requests —
the principal-resolution path logs why authorization failed.

**완화 (Mitigate)** — revert the offending auth/RBAC config or rotate the
correct credential/JWKS. If the spike is genuine abuse, apply the gateway rate
limit. Durable follow-up: add a config-change checklist entry so the same
misconfiguration cannot recur silently.

## Alert: FccApiLatencyP95Warning

p95 request latency on at least one surface is above the 500ms SLO target for
10m.

**즉시 행동 (Immediate)** — confirm which surface is slow:

```promql
histogram_quantile(0.95, sum by (le) (rate(fcc_session_request_total_bucket[5m])))
histogram_quantile(0.95, sum by (le) (rate(fcc_headless_request_total_bucket[5m])))
histogram_quantile(0.95, sum by (le) (rate(fcc_platform_request_total_bucket[5m])))
```

**조사 (Investigate)** — break p95 down by `operation` to find the slow route;
correlate with `fcc_*_request_total_count` to tell a real slowdown from a
low-traffic quantile artifact:

```promql
histogram_quantile(0.95, sum by (le, operation) (rate(fcc_session_request_total_bucket[5m])))
```

Common causes: SQLite WAL contention, large artifact/report I/O, or a slow
downstream (instrument/file-server). The `traceparent` span shows where the
time goes.

**완화 (Mitigate)** — relieve the bottleneck (checkpoint the WAL, move heavy I/O
off the request path, cache the hot read). If a deploy regressed latency, roll
it back. Durable follow-up: confirm the p95 budget in the SLO doc still holds
and add a latency budget test if a code path regressed.

## Alert: FccWebSocketCloseErrorRateWarning

The Session-API WebSocket error-close ratio (`reason="error"`) is elevated for
10m — streaming connections are dying on exceptions rather than closing
cleanly.

**즉시 행동 (Immediate)** — confirm the error-close ratio and the absolute rate:

```promql
sum(rate(fcc_session_ws_connections_closed_total{reason="error"}[5m]))
/
clamp_min(sum(rate(fcc_session_ws_connections_closed_total[5m])), 1e-9)
```

Compare against the other close reasons (`normal`, `timeout`, `denied`) to see
whether errors replaced clean closes or rode on top of normal churn:

```promql
sum by (reason) (rate(fcc_session_ws_connections_closed_total[5m]))
```

**조사 (Investigate)** — read the Session-API logs for the streaming handler
exceptions; check the live gauge `fcc_session_ws_connections` by `state` for a
connecting/closing pile-up. Correlate with `FccApiLatencyP95Warning` and the
session event bus — a backend stall mid-stream surfaces as an error close.

**완화 (Mitigate)** — fix or roll back the streaming handler regression; if a
downstream stall causes it, restore that dependency. Durable follow-up: ensure
the exception path closes the socket with the correct `reason` and add a
regression test for the failing stream.

## Alert: FccChamberOfflineWarning

One or more chambers have been derived `offline` (no heartbeat within TTL) for
10m. The distributed fleet has lost measurement capacity on those nodes.

**즉시 행동 (Immediate)** — identify which chambers are offline and how many:

```promql
fcc_platform_chamber_count{availability="offline"}
```

Cross-check the registry directly: `GET /platform/chambers` lists each
`chamber_id` with its derived `status` and `last_heartbeat_at`. An offline
chamber either powered down, lost its network path to the central hub, or its
node Session API process / heartbeat sender stopped.

**조사 (Investigate)** — distinguish the cause per chamber:

- `last_heartbeat_at` recent but status offline → clock skew / TTL too tight.
- `last_heartbeat_at` stale → node process down or network partition. Check the
  node host (Session API up? `FCC_CENTRAL_*` env set? token valid — a node using
  a static `FCC_CENTRAL_AUTH_TOKEN` whose access token expired stops heartbeating;
  prefer client_credentials auto-refresh).
- Many chambers offline at once → central hub / network incident, not per-node.

**완화 (Mitigate)** — restart the node Session API process (re-registers
idempotently and resumes heartbeat within two intervals), restore the network
path, or correct the node credential. Confirm recovery:
`fcc_platform_chamber_count{availability="offline"}` returns to baseline and the
chamber shows `idle`/`in_use` in `GET /platform/chambers`.

## Alert: FccChamberHeartbeatStaleWarning

The oldest heartbeat across the fleet
(`fcc_platform_chamber_heartbeat_age_max_seconds`) exceeds the heartbeat TTL
(90s) for 5m — a chamber is about to be, or has been, derived offline.

**즉시 행동 (Immediate)** — find the stalest chamber:

```promql
fcc_platform_chamber_heartbeat_age_max_seconds
```

Then read `GET /platform/chambers` and sort by `last_heartbeat_at` to find the
node whose heartbeat is lagging.

**조사 (Investigate)** — a rising max age before any chamber flips offline is an
early-warning of a degrading node→central path: intermittent network, an
overloaded node thread, or a node token nearing expiry (static-token nodes).
Correlate with the node logs (the heartbeat sender logs push failures) and the
platform request metrics for the heartbeat operation.

**완화 (Mitigate)** — restore the lagging node's heartbeat cadence (network,
process, or credential). If the TTL is structurally too tight for the network,
raise the chamber's `heartbeat_ttl_seconds` via the admin registration path.
Confirm the max age returns below the TTL.

## Change log

Record each threshold change with the measured baseline and date:

| Date | Alert | Measured baseline | New threshold | Operator |
| --- | --- | --- | --- | --- |
| 2026-06-13 | (all) | — | initial placeholders | B4 increment |
