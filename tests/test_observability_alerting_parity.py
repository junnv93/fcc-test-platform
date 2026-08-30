"""B4 — observability alert-rules ↔ runbook ↔ SLO parity invariant (2026-06-13).

The FCC web surfaces export Prometheus metrics through ``ApiMetricsRegistry``
(``src/application/common/metrics_registry.py``) but, before increment B4, had
no alert thresholds and no response procedures. B4 added three operations docs:

- ``docs/operations/prometheus-alert-rules.md`` — the alert SSOT
- ``docs/operations/runbook-api-observability.md`` — per-alert escalation
- ``docs/operations/slo.md`` — service level objectives

This invariant seals their structural integrity so the docs cannot silently
drift apart or from the metric registry:

1. **alert ↔ runbook set-equality** — every ``- alert: <Name>`` in the rules
   file has exactly one ``## Alert: <Name>`` section in the runbook, and vice
   versa (orphan alert = 0, orphan runbook = 0).
2. **metric-name SSOT** — every ``fcc_*`` metric token referenced in the alert
   rules and the SLO doc is a name that ``ApiMetricsRegistry.render()`` actually
   emits. A hand-typed metric string (typo, or a WebSocket metric on an
   HTTP-only namespace) fails. The registry is the authority.
3. **label-value SSOT** — every ``status="..."`` / ``reason="..."`` label value
   used in the rules is a member of ``VALID_STATUSES`` / ``VALID_WS_CLOSE_REASONS``.
4. **baseline procedure** — every alert section embeds a ``max_over_time``
   baseline PromQL, so the threshold is measured, not guessed.
5. **3-step escalation** — every runbook section has the immediate/investigate/
   mitigate steps.

The parsing logic lives in module-level functions so the completion-audit
negative tests can feed synthetic text (extra alert with no runbook; bogus
metric token) through the *same* parsers and prove the invariant has teeth.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / 'src'
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from fcc_test_contracts.common.metrics_registry import (  # noqa: E402
    ApiMetricsRegistry,
    METRICS_NAMESPACE_HEADLESS,
    METRICS_NAMESPACE_PLATFORM,
    METRICS_NAMESPACE_SESSION,
    STATUS_ERROR,
    VALID_STATUSES,
    VALID_WS_CLOSE_REASONS,
    WS_CLOSE_REASON_ERROR,
    WS_STATE_OPEN,
)
from fcc_test_platform.application.chamber_metrics import (  # noqa: E402
    CHAMBER_GAUGE_FAMILIES,
)


ALERT_RULES_DOC = ROOT / 'docs' / 'operations' / 'prometheus-alert-rules.md'
RUNBOOK_DOC = ROOT / 'docs' / 'operations' / 'runbook-api-observability.md'
SLO_DOC = ROOT / 'docs' / 'operations' / 'slo.md'

# Prometheus metric token: namespace prefix + body. The registry only ever
# produces ``fcc_<namespace>_...`` names; the SSOT set below is derived from
# the registry's own render(), never hand-listed.
_METRIC_TOKEN_RE = re.compile(r'\bfcc_[a-z0-9_]+\b')


# ── Registry-derived metric-name SSOT (the authority) ───────────────────────

def registry_metric_names() -> set[str]:
    """Every metric base name ``ApiMetricsRegistry.render()`` can emit.

    Derived by instantiating one registry per production namespace (Session
    with WebSocket enabled, Headless/Platform HTTP-only), recording a sample,
    and parsing the leading token of each render() data line. This is the SSOT
    an alert/SLO metric token must be a subset of — no hand-typed list.
    """
    names: set[str] = set()
    # Platform declares the chamber availability gauge families (same SSOT the
    # composition root uses) so derived gauge names are part of the authority set.
    surfaces = [
        (METRICS_NAMESPACE_SESSION, True, ()),
        (METRICS_NAMESPACE_HEADLESS, False, ()),
        (METRICS_NAMESPACE_PLATFORM, False, CHAMBER_GAUGE_FAMILIES),
    ]
    for namespace, ws, gauge_families in surfaces:
        reg = ApiMetricsRegistry(
            namespace=namespace, enable_websocket=ws, gauge_families=gauge_families,
        )
        reg.record_request('probe', STATUS_ERROR, 1.0)
        if ws:
            reg.inc_ws_connection(WS_STATE_OPEN)
            reg.inc_ws_closed_total(WS_CLOSE_REASON_ERROR)
        for line in reg.render().splitlines():
            if not line or line.startswith('#'):
                continue
            m = re.match(r'([a-zA-Z_][a-zA-Z0-9_]*)', line)
            if m:
                names.add(m.group(1))
    return names


# ── Document parsers (shared with negative tests) ───────────────────────────

def parse_alert_names(rules_text: str) -> list[str]:
    """Alert names from ``- alert: <Name>`` lines (Prometheus rule syntax)."""
    return re.findall(r'^\s*-\s*alert:\s*([A-Za-z][A-Za-z0-9]*)\s*$', rules_text, re.M)


def parse_alert_sections(rules_text: str) -> dict[str, str]:
    """Map alert name → its ``### <Name>`` section body in the rules doc."""
    sections: dict[str, str] = {}
    # Split on level-3 headings; a section runs until the next ### or ##.
    parts = re.split(r'^###\s+(.+?)\s*$', rules_text, flags=re.M)
    # parts = [preamble, head1, body1, head2, body2, ...]
    for i in range(1, len(parts), 2):
        heading = parts[i].strip()
        body = parts[i + 1]
        # Stop the body at the first level-2 heading inside the captured chunk.
        body = re.split(r'^##\s+', body, flags=re.M)[0]
        sections[heading] = body
    return sections


def parse_runbook_alert_names(runbook_text: str) -> list[str]:
    """Runbook entries from ``## Alert: <Name>`` headings."""
    return re.findall(r'^##\s+Alert:\s*([A-Za-z][A-Za-z0-9]*)\s*$', runbook_text, re.M)


def parse_runbook_sections(runbook_text: str) -> dict[str, str]:
    """Map alert name → its ``## Alert: <Name>`` section body in the runbook."""
    sections: dict[str, str] = {}
    parts = re.split(r'^##\s+Alert:\s*([A-Za-z][A-Za-z0-9]*)\s*$', runbook_text, flags=re.M)
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        body = parts[i + 1]
        body = re.split(r'^##\s+', body, flags=re.M)[0]
        sections[name] = body
    return sections


def fenced_code(text: str) -> str:
    """Concatenated bodies of every fenced ``` code block.

    Metric/label SSOT applies to real queries (PromQL / shell), not to prose
    that *names* a metric to explain it does not exist (e.g. the doc says
    ``fcc_headless_ws_*`` would be a bug). Restricting extraction to fenced
    blocks keeps explanatory prose free.
    """
    return '\n'.join(re.findall(r'```[^\n]*\n(.*?)```', text, re.S))


def metric_tokens(text: str) -> set[str]:
    """Every ``fcc_*`` metric token inside fenced code blocks of a document."""
    return set(_METRIC_TOKEN_RE.findall(fenced_code(text)))


def label_values(text: str, label: str) -> set[str]:
    """Every value used for a given Prometheus label inside fenced code blocks."""
    return set(re.findall(label + r'="([^"]+)"', fenced_code(text)))


# ── Baseline PromQL structural parsers (subquery-binding correctness) ─────────

# Subquery selector: ``[<range>:<resolution>]`` (resolution may be empty).
_SUBQUERY_RE = re.compile(r'\[\d+[smhdwy]:\d*[smhdwy]?\]')


def promql_blocks(section_body: str) -> list[str]:
    """Every ```promql fenced block inside a rules-doc section body."""
    return re.findall(r'```promql\s*\n(.*?)```', section_body, re.S)


def yaml_blocks(section_body: str) -> list[str]:
    """Every ```yaml fenced block inside a rules-doc section body.

    The alert rule (with its `expr:`) lives in the section's ```yaml block, so
    this is where the surface/metric coverage the alert actually fires on is
    extracted from.
    """
    return re.findall(r'```yaml\s*\n(.*?)```', section_body, re.S)


def metric_tokens_raw(text: str) -> set[str]:
    """Every ``fcc_*`` metric token in a raw (already-code) string.

    Unlike :func:`metric_tokens` this does not restrict to fenced blocks — the
    caller passes a single already-extracted code block, so the extra fenced
    filtering would be a no-op (and would drop the block entirely if it lacked
    its own fences).
    """
    return set(_METRIC_TOKEN_RE.findall(text))


def baseline_promql_blocks(section_body: str) -> list[str]:
    """The promql blocks of a section that carry a ``max_over_time`` baseline."""
    return [b for b in promql_blocks(section_body) if 'max_over_time' in b]


def _matching_paren(text: str, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at ``open_idx`` (-1 if unbalanced)."""
    assert text[open_idx] == '('
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
    return -1


def subquery_wraps_full_expression(block: str) -> bool:
    """True iff the subquery selector binds the *whole* ``max_over_time`` arg.

    Correct shape (subquery wraps the entire ratio/quantile expression)::

        max_over_time( (<expr>)[7d:1m] ) [* N]

    Buggy shape rejected — the subquery binds only the trailing operand
    (e.g. the ``clamp_min(...)`` denominator), which in PromQL is a
    range-vector / instant-vector type error::

        max_over_time( (num) / clamp_min(denom)[7d:1m] )

    Detection is paren-balance based (not a fragile full-text regex): the first
    non-space token after ``max_over_time(`` must be a ``(`` whose matching
    ``)`` is immediately followed by the ``[range:res]`` selector, and the
    wrapped expression must be the full ratio/quantile (contains ``/`` or
    ``histogram_quantile``), not a bare operand.
    """
    m = re.search(r'max_over_time\s*\(', block)
    if not m:
        return False
    i = m.end()
    while i < len(block) and block[i].isspace():
        i += 1
    if i >= len(block) or block[i] != '(':
        # First token after max_over_time( is not a wrapping paren → the
        # subquery cannot bind the whole expression.
        return False
    close = _matching_paren(block, i)
    if close < 0:
        return False
    rest = block[close + 1:]
    sm = re.match(r'\s*' + _SUBQUERY_RE.pattern, rest)
    if not sm:
        # The wrapping paren is not immediately followed by the subquery
        # selector → selector binds something narrower (the bug).
        return False
    inner = block[i + 1:close]
    return ('/' in inner) or ('histogram_quantile' in inner)


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def rules_text() -> str:
    assert ALERT_RULES_DOC.is_file(), f'missing {ALERT_RULES_DOC}'
    return ALERT_RULES_DOC.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def runbook_text() -> str:
    assert RUNBOOK_DOC.is_file(), f'missing {RUNBOOK_DOC}'
    return RUNBOOK_DOC.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def slo_text() -> str:
    assert SLO_DOC.is_file(), f'missing {SLO_DOC}'
    return SLO_DOC.read_text(encoding='utf-8')


# ── MUST 1 — alert ↔ runbook set-equality ───────────────────────────────────

def test_alert_runbook_set_equality(rules_text: str, runbook_text: str) -> None:
    alerts = set(parse_alert_names(rules_text))
    runbook = set(parse_runbook_alert_names(runbook_text))
    assert alerts, 'no alerts parsed from rules doc'
    orphan_alerts = sorted(alerts - runbook)
    orphan_runbooks = sorted(runbook - alerts)
    assert not orphan_alerts, f'alerts with no runbook entry: {orphan_alerts}'
    assert not orphan_runbooks, f'runbook entries with no alert: {orphan_runbooks}'


def test_alert_names_unique(rules_text: str, runbook_text: str) -> None:
    alerts = parse_alert_names(rules_text)
    runbook = parse_runbook_alert_names(runbook_text)
    assert len(alerts) == len(set(alerts)), f'duplicate alert names: {alerts}'
    assert len(runbook) == len(set(runbook)), f'duplicate runbook entries: {runbook}'


def test_every_alert_has_a_rule_section(rules_text: str) -> None:
    # Each ``- alert: X`` must live under a ``### X`` section so its baseline is
    # locatable.
    alert_names = set(parse_alert_names(rules_text))
    sections = parse_alert_sections(rules_text)
    missing = sorted(n for n in alert_names if n not in sections)
    assert not missing, f'alerts without a ### section: {missing}'
    for name, body in sections.items():
        if name in alert_names:
            assert f'- alert: {name}' in body, (
                f'section ### {name} does not contain its `- alert: {name}` rule'
            )


# ── MUST 2 — metric-name SSOT (registry is the authority) ────────────────────

def test_alert_metric_tokens_subset_of_registry(rules_text: str) -> None:
    ssot = registry_metric_names()
    used = metric_tokens(rules_text)
    assert used, 'no fcc_* metric tokens found in alert rules doc'
    unknown = sorted(used - ssot)
    assert not unknown, (
        'alert rules reference metric name(s) not emitted by '
        f'ApiMetricsRegistry.render(): {unknown}\nregistry SSOT: {sorted(ssot)}'
    )


def test_slo_metric_tokens_subset_of_registry(slo_text: str) -> None:
    ssot = registry_metric_names()
    used = metric_tokens(slo_text)
    assert used, 'no fcc_* metric tokens found in SLO doc'
    unknown = sorted(used - ssot)
    assert not unknown, (
        f'SLO doc references metric name(s) not in registry SSOT: {unknown}'
    )


def test_runbook_metric_tokens_subset_of_registry(runbook_text: str) -> None:
    ssot = registry_metric_names()
    unknown = sorted(metric_tokens(runbook_text) - ssot)
    assert not unknown, (
        f'runbook references metric name(s) not in registry SSOT: {unknown}'
    )


def test_websocket_metrics_only_on_session_namespace(rules_text: str, slo_text: str) -> None:
    # Headless/Platform are HTTP-only (enable_websocket=False) — alerting on a
    # fcc_headless_ws_* / fcc_platform_ws_* series would reference a metric that
    # never exists. Guard explicitly (the subset check already covers it, but
    # this names the failure mode).
    for doc, text in (('rules', rules_text), ('slo', slo_text)):
        for token in metric_tokens(text):
            if '_ws_' in token:
                assert token.startswith(METRICS_NAMESPACE_SESSION), (
                    f'{doc} doc references WebSocket metric {token!r} on a '
                    'non-Session namespace (Headless/Platform are HTTP-only)'
                )


# ── MUST 3 — label-value SSOT ────────────────────────────────────────────────

def test_status_label_values_are_registry_constants(rules_text: str, slo_text: str) -> None:
    for doc, text in (('rules', rules_text), ('slo', slo_text)):
        used = label_values(text, 'status')
        unknown = sorted(used - VALID_STATUSES)
        assert not unknown, f'{doc} doc uses unknown status label value(s): {unknown}'


def test_reason_label_values_are_registry_constants(rules_text: str, slo_text: str) -> None:
    for doc, text in (('rules', rules_text), ('slo', slo_text)):
        used = label_values(text, 'reason')
        unknown = sorted(used - VALID_WS_CLOSE_REASONS)
        assert not unknown, f'{doc} doc uses unknown reason label value(s): {unknown}'


# ── MUST — baseline procedure per alert ──────────────────────────────────────

def test_every_alert_section_has_baseline_promql(rules_text: str) -> None:
    alert_names = set(parse_alert_names(rules_text))
    sections = parse_alert_sections(rules_text)
    for name in alert_names:
        body = sections.get(name, '')
        assert 'max_over_time' in body, (
            f'alert {name} has no `max_over_time` baseline measurement PromQL '
            '(thresholds must be measured, not guessed)'
        )


def test_every_baseline_subquery_wraps_full_expression(rules_text: str) -> None:
    """The ``[range:res]`` subquery must bind the whole ratio/quantile.

    A substring check (``'max_over_time' in body``) PASSes invalid PromQL such
    as ``max_over_time( num / clamp_min(denom)[7d:1m] )`` where the subquery
    binds only the denominator. This walks paren balance to prove the selector
    wraps the entire expression — so the documented baseline is a query an
    operator can actually run, not a type error.
    """
    alert_names = set(parse_alert_names(rules_text))
    sections = parse_alert_sections(rules_text)
    checked = 0
    for name in alert_names:
        blocks = baseline_promql_blocks(sections.get(name, ''))
        assert blocks, f'alert {name} has no ```promql baseline block'
        for block in blocks:
            # Every baseline block must carry exactly one subquery selector and
            # it must wrap the full expression.
            selectors = _SUBQUERY_RE.findall(block)
            assert len(selectors) == 1, (
                f'alert {name} baseline must contain exactly one subquery '
                f'selector, found {len(selectors)}: {selectors}'
            )
            assert subquery_wraps_full_expression(block), (
                f'alert {name} baseline subquery {selectors[0]} does not wrap '
                'the full ratio/quantile expression — in PromQL the selector '
                'binds the immediately preceding operand, so it must follow a '
                'parenthesised `(<full expr>)`. Got:\n' + block
            )
            checked += 1
    assert checked >= len(alert_names), 'fewer baseline blocks than alerts'


def _alert_expr_metrics(section_body: str) -> set[str]:
    """``fcc_*`` metric tokens the alert rule (``expr:``) actually fires on."""
    metrics: set[str] = set()
    for block in yaml_blocks(section_body):
        metrics |= metric_tokens_raw(block)
    return metrics


def _baseline_metrics(section_body: str) -> set[str]:
    """``fcc_*`` metric tokens the section's ``max_over_time`` baseline samples."""
    metrics: set[str] = set()
    for block in baseline_promql_blocks(section_body):
        metrics |= metric_tokens_raw(block)
    return metrics


def test_baseline_covers_every_alert_expr_metric(rules_text: str) -> None:
    """Each alert's baseline must measure every surface its ``expr`` watches.

    The threshold-setting workflow says ``Baseline = the alert's own
    ratio/quantile expression``. A baseline that samples a *narrower* set of
    metrics than the alert evaluates makes the operator derive a threshold from
    a signal the alert never actually fires on — e.g. the latency alert ORs
    Session / Headless / Platform p95 but its baseline only sampled the Session
    bucket, so Headless/Platform breaches were invisible to the baseline.

    Sealing ``alert-expr metric coverage ⊆ baseline metric coverage`` per alert
    catches that class of drift. (Extra baseline metrics are allowed — only a
    *missing* surface is a defect.)
    """
    alert_names = set(parse_alert_names(rules_text))
    sections = parse_alert_sections(rules_text)
    checked = 0
    for name in alert_names:
        body = sections.get(name, '')
        alert_metrics = _alert_expr_metrics(body)
        baseline_metrics = _baseline_metrics(body)
        assert alert_metrics, f'alert {name}: no fcc_* metric tokens in its `expr`'
        assert baseline_metrics, f'alert {name}: no fcc_* metric tokens in its baseline'
        missing = sorted(alert_metrics - baseline_metrics)
        assert not missing, (
            f'alert {name}: the baseline does not sample metric(s) the alert '
            f'expr fires on: {missing}. The baseline must cover every surface '
            'the alert watches (alert-expr metric coverage ⊆ baseline coverage) '
            'so the operator measures a threshold from the same signal the '
            'alert evaluates.'
        )
        checked += 1
    assert checked == len(alert_names), 'fewer sections audited than alerts'


def test_negative_baseline_missing_surface_metric_is_detected() -> None:
    """The bug shape (baseline omits a surface the alert ORs) must be caught.

    Feeds a synthetic latency-style section whose alert ORs the Session and
    Headless p95 but whose baseline samples only the Session bucket — exactly
    the defect this increment fixes — through the *same* coverage helpers and
    proves the gap is surfaced.
    """
    synthetic = (
        '### FccSyntheticLatency\n'
        '```yaml\n'
        '- alert: FccSyntheticLatency\n'
        '  expr: |\n'
        '    histogram_quantile(0.95, sum by (le) (rate(fcc_session_request_total_bucket[5m]))) > 500\n'
        '    or\n'
        '    histogram_quantile(0.95, sum by (le) (rate(fcc_headless_request_total_bucket[5m]))) > 500\n'
        '```\n'
        '```promql\n'
        'max_over_time(\n'
        '  ( histogram_quantile(0.95, sum by (le) (rate(fcc_session_request_total_bucket[5m]))) )[7d:1m]\n'
        ')\n'
        '```\n'
    )
    body = parse_alert_sections(synthetic)['FccSyntheticLatency']
    alert_metrics = _alert_expr_metrics(body)
    baseline_metrics = _baseline_metrics(body)
    # The alert fires on both surfaces; the (buggy) baseline samples only one.
    assert 'fcc_headless_request_total_bucket' in alert_metrics
    assert 'fcc_headless_request_total_bucket' not in baseline_metrics
    assert alert_metrics - baseline_metrics == {'fcc_headless_request_total_bucket'}, (
        'coverage-parity helper failed to flag the surface the baseline omits'
    )


def test_latency_alert_baseline_samples_all_three_surfaces(rules_text: str) -> None:
    """Regression guard for the specific B4 fix: the p95 latency baseline must
    sample Session *and* Headless *and* Platform buckets, mirroring its alert.
    """
    sections = parse_alert_sections(rules_text)
    body = sections.get('FccApiLatencyP95Warning', '')
    assert body, 'FccApiLatencyP95Warning section missing'
    baseline_metrics = _baseline_metrics(body)
    for surface in ('session', 'headless', 'platform'):
        token = f'fcc_{surface}_request_total_bucket'
        assert token in baseline_metrics, (
            f'p95 latency baseline does not sample {token} — it must track the '
            'worst surface across all three the alert ORs, not just Session'
        )


def test_negative_denominator_bound_subquery_is_rejected() -> None:
    """The buggy shape (subquery on the denominator only) must be rejected."""
    buggy = (
        'max_over_time(\n'
        '  (\n      sum(rate(fcc_session_request_total_count{status="error"}[5m]))\n  )\n'
        '  /\n'
        '  clamp_min(\n      sum(rate(fcc_session_request_total_count[5m]))\n  , 1e-9)\n'
        '  [7d:1m]\n'
        ') * 3\n'
    )
    fixed = (
        'max_over_time(\n'
        '  (\n'
        '    (\n      sum(rate(fcc_session_request_total_count{status="error"}[5m]))\n    )\n'
        '    /\n'
        '    clamp_min(\n      sum(rate(fcc_session_request_total_count[5m]))\n    , 1e-9)\n'
        '  )[7d:1m]\n'
        ') * 3\n'
    )
    assert not subquery_wraps_full_expression(buggy), (
        'denominator-bound subquery (the bug) was accepted'
    )
    assert subquery_wraps_full_expression(fixed), (
        'correctly-wrapped subquery was rejected'
    )


def test_promtool_parses_baseline_and_alert_exprs(rules_text: str) -> None:
    """If ``promtool`` is on PATH, every alert expr + baseline parses cleanly.

    promtool ``check rules`` validates PromQL syntax/typing offline (no server).
    The buggy denominator-bound subquery is a range/instant type error that
    promtool rejects. When promtool is unavailable (the usual CI/dev case on
    this Windows stack) the structural parser test above is the binding gate, so
    this is an additive defence-in-depth rather than the sole guard.
    """
    promtool = shutil.which('promtool')
    if not promtool:
        pytest.skip('promtool not on PATH — structural parser test is the gate')

    sections = parse_alert_sections(rules_text)
    alert_exprs = re.findall(r'^\s*expr:\s*\|\n(.*?)^\s*for:', rules_text, re.S | re.M)
    baselines: list[str] = []
    for body in sections.values():
        baselines.extend(baseline_promql_blocks(body))

    rules_lines = ['groups:', '  - name: fcc-parse-gate', '    rules:']

    def _indent(expr: str, spaces: int) -> str:
        pad = ' ' * spaces
        return '\n'.join(pad + ln if ln.strip() else ln for ln in expr.splitlines())

    idx = 0
    for expr in alert_exprs + baselines:
        rules_lines.append(f'      - record: parse:gate{idx}')
        rules_lines.append('        expr: |')
        rules_lines.append(_indent(expr.rstrip('\n'), 10))
        idx += 1

    with tempfile.NamedTemporaryFile(
        'w', suffix='.yml', delete=False, encoding='utf-8'
    ) as fh:
        fh.write('\n'.join(rules_lines) + '\n')
        rules_path = fh.name

    proc = subprocess.run(
        [promtool, 'check', 'rules', rules_path],
        capture_output=True, text=True,
    )
    Path(rules_path).unlink(missing_ok=True)
    assert proc.returncode == 0, (
        'promtool rejected an alert/baseline PromQL expression:\n'
        f'{proc.stdout}\n{proc.stderr}'
    )


def test_baseline_methodology_documented(rules_text: str) -> None:
    # The equipment-grade rule: warning = baseline × 3, critical = warning × 10.
    assert 'max_over_time' in rules_text
    assert re.search(r'baseline\s*[×x*]\s*3', rules_text), 'warning = baseline × 3 rule absent'
    assert re.search(r'warning\s*[×x*]\s*10', rules_text), 'critical = warning × 10 rule absent'


# ── SLO alignment ────────────────────────────────────────────────────────────

def test_slo_defines_core_objectives(slo_text: str) -> None:
    lowered = slo_text.lower()
    for term in ('availability', 'latency', 'error budget'):
        assert term in lowered, f'SLO doc missing objective: {term!r}'


def test_slo_alerts_reference_real_alerts(slo_text: str, rules_text: str) -> None:
    # Every alert name the SLO table cites must be a real alert.
    alerts = set(parse_alert_names(rules_text))
    cited = set(re.findall(r'`(Fcc[A-Za-z0-9]+)`', slo_text))
    unknown = sorted(cited - alerts)
    assert not unknown, f'SLO doc cites non-existent alert(s): {unknown}'


# ── SHOULD — 3-step escalation per runbook section ───────────────────────────

def test_runbook_sections_have_three_steps(runbook_text: str) -> None:
    sections = parse_runbook_sections(runbook_text)
    assert sections, 'no runbook alert sections parsed'
    for name, body in sections.items():
        for marker in ('즉시', '조사', '완화'):
            assert marker in body, (
                f'runbook section {name} missing escalation step marker {marker!r}'
            )


# ── completion-audit — the invariant has teeth (synthetic negatives) ─────────

def test_negative_orphan_alert_is_detected(runbook_text: str) -> None:
    # Adding an alert with no runbook entry must break set-equality.
    synthetic_rules = (
        '### FccSyntheticOrphanAlert\n'
        '```yaml\n- alert: FccSyntheticOrphanAlert\n```\n'
    )
    alerts = set(parse_alert_names(synthetic_rules))
    runbook = set(parse_runbook_alert_names(runbook_text))
    assert alerts - runbook == {'FccSyntheticOrphanAlert'}, (
        'parity parser failed to flag an orphan alert'
    )


def test_negative_bogus_metric_token_is_detected() -> None:
    # A hand-typed / typo'd metric must fall outside the registry SSOT.
    ssot = registry_metric_names()
    bogus = 'fcc_headless_ws_connections_closed_total'  # ws on HTTP-only ns
    typo = 'fcc_session_request_totl_count'
    assert bogus not in ssot, 'HTTP-only WS metric unexpectedly in registry SSOT'
    assert typo not in ssot, 'typo metric unexpectedly in registry SSOT'
    # And the extractor would surface them from a PromQL block.
    assert metric_tokens(f'```promql\n{bogus} / {typo}\n```') == {bogus, typo}


def test_negative_missing_baseline_is_detected() -> None:
    section_without_baseline = (
        '### FccNoBaseline\n```yaml\n- alert: FccNoBaseline\n  expr: x > 1\n```\n'
    )
    sections = parse_alert_sections(section_without_baseline)
    assert 'max_over_time' not in sections.get('FccNoBaseline', '')
