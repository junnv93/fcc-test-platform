"""Validate chamber real-measurement staging evidence (pure, offline, no-fake).

The M1 measurement staging gate (closure plan P2) must *stop calling M1 done*
until the SCPI-analyzer + Appium measurement path is proven on real hardware,
WITHOUT ever pretending hardware ran. This module is the offline, deterministic
half of that gate: it validates the *structure* of an operator-attested evidence
bundle and reports which M1 pass criteria are still unmet — but it never asserts
that hardware ran. Truth is supplied by a human pasting real, cross-referenced
API/WS/log identifiers; the gate only checks shape and self-consistency.

It is the measurement-path counterpart of ``chamber_token_evidence`` (machine
token lifecycle): that one covers credentials, this one covers a real
measurement run (start -> in_use -> progress -> completion) across >= 2 PCs.

The recorded bundle additionally attests two operator-supplied facts that the
gate can only check the *shape* of, never the truth of (no-fake rule):

* **SCPI/Appium proof-of-life** — the analyzer ``*IDN?`` string, the Appium
  session id, and the DUT model actually driven. These prove the measurement
  path exercised real instruments, not that a sweep produced a given number.
* **Fleet partition** — a 3+ node fleet split (split-brain) scenario, which is
  either ``observed`` or honestly declared ``not_run`` (blocked-by-equipment).
  ``not_run`` remains a passing-with-honesty state so a partition that could not
  be exercised is never faked into ``observed``.

Two evidence modes:

* ``template`` — a blank skeleton produced by the gate's ``--template`` builder.
  Every recorded field carries the ``<FILL-ME>`` placeholder / zero / false
  default. It is *structurally* valid (so the builder's own output passes the
  shape check) but it MUST NOT pass the M1 criteria — an unfilled template can
  never masquerade as a real run.
* ``recorded`` — an operator-filled bundle from a real staging attempt. The
  placeholder sentinel is forbidden anywhere in a recorded bundle, so a
  half-filled template can never be relabelled ``recorded`` to fake a pass.

dependency-free (stdlib only) so it stays frozen-exe safe and unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


__all__ = [
    'SCHEMA_VERSION',
    'EVIDENCE_MODES',
    'TOKEN_BINDING_STATES',
    'MEASUREMENT_OUTCOMES',
    'SCENARIO_STATES',
    'TIMELINE_PHASES',
    'TEMPLATE_PLACEHOLDER',
    'MIN_PHYSICAL_MACHINES',
    'MIN_EXPECTED_FLEET_SIZE',
    'TOP_LEVEL_REQUIRED_FIELDS',
    'MACHINE_REQUIRED_FIELDS',
    'EQUIPMENT_REQUIRED_FIELDS',
    'StagingEvidenceIssue',
    'PassCriterion',
    'is_measurement_staging_evidence_valid',
    'chamber_measurement_staging_errors',
    'measurement_staging_unmet_pass_criteria',
    'measurement_staging_pass_summary',
    'measurement_staging_passes_m1',
]

#: Bumped 1 -> 2 when the recorded bundle gained the SCPI/Appium proof-of-life
#: equipment fields and the ``fleet_partition`` scenario block (both required).
#: A stale v1 bundle without them fails loudly rather than silently passing with
#: missing evidence — there is no persisted v1 bundle to migrate (the bundle is
#: operator-generated on demand by the ``--template`` builder).
SCHEMA_VERSION = 2

#: How the bundle was produced. ``template`` = blank skeleton (never a pass);
#: ``recorded`` = filled from a real staging attempt.
EVIDENCE_MODES = ('template', 'recorded')

#: Chamber machine-token binding state observed during the run (ADR-0016).
TOKEN_BINDING_STATES = ('bound', 'claimless', 'unknown')

#: A real run must end in one of these — a clean completion or a *controlled*
#: failure (an honest failure is acceptable evidence; a hang/unknown is not).
MEASUREMENT_OUTCOMES = ('completed', 'controlled_failure')

#: Restart / partition scenarios are either actually exercised or explicitly
#: declared not-run (silent omission is rejected by the criteria).
SCENARIO_STATES = ('observed', 'not_run')

#: Measurement lifecycle phases the timeline must cover for a passing run.
TIMELINE_PHASES = ('start', 'progress', 'completion')

#: The ONLY accepted unfilled marker. Forbidden in ``recorded`` bundles.
TEMPLATE_PLACEHOLDER = '<FILL-ME>'

#: M1 minimum: real distributed fleet, not a single-box loopback.
MIN_PHYSICAL_MACHINES = 2

#: The operator-declared target fleet size for a fleet-partition scenario. A
#: partition needs at least the 2-machine minimum to describe two sides; the 3+
#: split-brain target is captured in ``notes`` (the honest 2-PC minimum is NOT
#: raised — a partition attempt with only 2 PCs is legitimately ``not_run``).
MIN_EXPECTED_FLEET_SIZE = MIN_PHYSICAL_MACHINES

#: 2xx is the only "POST succeeded" range for the start probe.
_HTTP_SUCCESS_MIN = 200
_HTTP_SUCCESS_MAX = 299

TOP_LEVEL_REQUIRED_FIELDS = (
    'schema_version',
    'evidence_id',
    'collected_at',
    'mode',
    'central_stack_version',
    'machines',
    'chamber',
    'equipment',
    'published_plan_id',
    'measurement',
    'timeline',
    'correlation',
    'restart_partition',
    'fleet_partition',
)

MACHINE_REQUIRED_FIELDS = ('role', 'identity', 'address', 'os')

#: Equipment block required fields. ``analyzer_resource`` / ``appium_device_id``
#: identify the instruments; the proof-of-life trio + ``dut_driven`` flag attest
#: the measurement path actually exercised them (operator-pasted, shape-checked
#: only — never a claim that a sweep produced a value).
EQUIPMENT_REQUIRED_FIELDS = (
    'analyzer_resource',
    'appium_device_id',
    'analyzer_idn',
    'appium_session_id',
    'dut_model',
)


@dataclass(frozen=True)
class StagingEvidenceIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict:
        return {'code': self.code, 'path': self.path, 'message': self.message}


@dataclass(frozen=True)
class PassCriterion:
    key: str
    met: bool
    detail: str

    def to_dict(self) -> dict:
        return {'key': self.key, 'met': self.met, 'detail': self.detail}


# --------------------------------------------------------------------------- #
# Structural validation (shape / types / enums / no-fake placeholder guard)    #
# --------------------------------------------------------------------------- #

def is_measurement_staging_evidence_valid(manifest: Mapping) -> bool:
    return not chamber_measurement_staging_errors(manifest)


def chamber_measurement_staging_errors(manifest: Mapping) -> list[StagingEvidenceIssue]:
    issues: list[StagingEvidenceIssue] = []
    if not isinstance(manifest, Mapping):
        return [_issue('invalid_manifest', '', 'manifest must be an object')]

    if manifest.get('schema_version') != SCHEMA_VERSION:
        issues.append(_issue('invalid_schema_version', 'schema_version',
                             f'schema_version must be {SCHEMA_VERSION}'))
    for key in ('evidence_id', 'collected_at', 'mode', 'central_stack_version',
                'published_plan_id'):
        _require_text(manifest, key, key, issues)

    mode = _text(manifest.get('mode'))
    if mode and mode not in EVIDENCE_MODES:
        issues.append(_issue('invalid_mode', 'mode',
                             f"mode must be one of {', '.join(EVIDENCE_MODES)}"))

    _validate_machines(manifest.get('machines'), issues)
    _validate_chamber(manifest.get('chamber'), issues)
    _validate_equipment(manifest.get('equipment'), issues)
    _validate_measurement(manifest.get('measurement'), issues)
    _validate_timeline(manifest.get('timeline'), issues)
    _validate_correlation(manifest.get('correlation'), issues)
    _validate_restart_partition(manifest.get('restart_partition'), issues)
    _validate_fleet_partition(manifest.get('fleet_partition'), issues)

    # No-fake guard: a recorded (real-run) bundle must not carry the unfilled
    # template placeholder anywhere — a half-filled template can never be
    # relabelled ``recorded`` to fake a pass.
    if mode == 'recorded':
        for path in _placeholder_paths(manifest, ''):
            issues.append(_issue('placeholder_in_recorded', path,
                                 'recorded evidence must not contain the unfilled '
                                 f'placeholder {TEMPLATE_PLACEHOLDER!r}'))
    return issues


def _validate_machines(value, issues: list[StagingEvidenceIssue]) -> None:
    if not _is_list(value) or not value:
        issues.append(_issue('invalid_machines', 'machines',
                             f'machines must list at least {MIN_PHYSICAL_MACHINES} entries'))
        return
    if len(value) < MIN_PHYSICAL_MACHINES:
        issues.append(_issue('invalid_machines', 'machines',
                             f'machines must list at least {MIN_PHYSICAL_MACHINES} entries'))
    for index, raw in enumerate(value):
        path = f'machines[{index}]'
        machine = raw if isinstance(raw, Mapping) else {}
        if not machine:
            issues.append(_issue('invalid_machine', path, 'machine must be an object'))
            continue
        for field in MACHINE_REQUIRED_FIELDS:
            if not _text(machine.get(field)):
                issues.append(_issue('missing_required_field', f'{path}.{field}',
                                     f'{field} is required'))


def _validate_chamber(value, issues: list[StagingEvidenceIssue]) -> None:
    chamber = _require_object(value, 'chamber', issues)
    if chamber is None:
        return
    _require_text(chamber, 'chamber_id', 'chamber.chamber_id', issues)
    _require_text(chamber, 'node_pc_identity', 'chamber.node_pc_identity', issues)
    _validate_enum(chamber.get('token_binding_state'), TOKEN_BINDING_STATES,
                   'chamber.token_binding_state', 'invalid_token_binding_state', issues)


def _validate_equipment(value, issues: list[StagingEvidenceIssue]) -> None:
    equipment = _require_object(value, 'equipment', issues)
    if equipment is None:
        return
    for field in EQUIPMENT_REQUIRED_FIELDS:
        _require_text(equipment, field, f'equipment.{field}', issues)
    # dut_driven attests the DUT was actually driven (keystring automation) — a
    # boolean the operator sets, checked for TYPE only (never that a sweep ran).
    if not isinstance(equipment.get('dut_driven'), bool):
        issues.append(_issue('invalid_dut_driven', 'equipment.dut_driven',
                             'dut_driven must be a boolean'))


def _validate_measurement(value, issues: list[StagingEvidenceIssue]) -> None:
    measurement = _require_object(value, 'measurement', issues)
    if measurement is None:
        return
    _require_text(measurement, 'job_id', 'measurement.job_id', issues)
    _require_text(measurement, 'session_id', 'measurement.session_id', issues)

    start = measurement.get('start')
    if not isinstance(start, Mapping):
        issues.append(_issue('missing_required_field', 'measurement.start',
                             'start must be an object'))
    else:
        _require_text(start, 'endpoint', 'measurement.start.endpoint', issues)
        if not isinstance(start.get('http_status'), int) or isinstance(start.get('http_status'), bool):
            issues.append(_issue('invalid_http_status', 'measurement.start.http_status',
                                 'http_status must be an integer'))

    if not isinstance(measurement.get('chamber_in_use_observed'), bool):
        issues.append(_issue('invalid_in_use_flag', 'measurement.chamber_in_use_observed',
                             'chamber_in_use_observed must be a boolean'))

    progress = measurement.get('progress')
    if not isinstance(progress, Mapping):
        issues.append(_issue('missing_required_field', 'measurement.progress',
                             'progress must be an object'))
    else:
        for flag in ('via_api', 'via_ws'):
            if not isinstance(progress.get(flag), bool):
                issues.append(_issue('invalid_progress_flag', f'measurement.progress.{flag}',
                                     f'{flag} must be a boolean'))

    # outcome may be empty in a template; non-empty must be a known outcome.
    _validate_enum(measurement.get('outcome'), MEASUREMENT_OUTCOMES,
                   'measurement.outcome', 'invalid_outcome', issues, allow_empty=True)


def _validate_timeline(value, issues: list[StagingEvidenceIssue]) -> None:
    if not _is_list(value) or len(value) < len(TIMELINE_PHASES):
        issues.append(_issue('invalid_timeline', 'timeline',
                             f'timeline must list at least {len(TIMELINE_PHASES)} '
                             f'entries (one per phase: {", ".join(TIMELINE_PHASES)})'))
        return
    for index, raw in enumerate(value):
        path = f'timeline[{index}]'
        entry = raw if isinstance(raw, Mapping) else {}
        if not entry:
            issues.append(_issue('invalid_timeline_entry', path, 'entry must be an object'))
            continue
        _validate_enum(entry.get('phase'), TIMELINE_PHASES, f'{path}.phase',
                       'invalid_timeline_phase', issues)
        if not _text(entry.get('at')):
            issues.append(_issue('missing_required_field', f'{path}.at', 'at is required'))


def _validate_correlation(value, issues: list[StagingEvidenceIssue]) -> None:
    correlation = _require_object(value, 'correlation', issues)
    if correlation is None:
        return
    for key in ('api', 'ws', 'log'):
        _require_text(correlation, key, f'correlation.{key}', issues)


def _validate_restart_partition(value, issues: list[StagingEvidenceIssue]) -> None:
    block = _require_object(value, 'restart_partition', issues)
    if block is None:
        return
    _validate_enum(block.get('node_restart'), SCENARIO_STATES,
                   'restart_partition.node_restart', 'invalid_scenario_state', issues)
    _validate_enum(block.get('network_partition'), SCENARIO_STATES,
                   'restart_partition.network_partition', 'invalid_scenario_state', issues)


def _validate_fleet_partition(value, issues: list[StagingEvidenceIssue]) -> None:
    """A 3+ node fleet split (split-brain) scenario.

    ``state`` is ``observed`` (actually exercised) or ``not_run`` (honestly
    declared blocked-by-equipment — never faked into ``observed``). Silent
    omission is rejected by requiring the block + a non-empty ``notes`` reason.
    ``expected_fleet_size`` records the operator-declared target size (>= the
    2-machine minimum; the 3+ target lives in the value + notes).
    """
    block = _require_object(value, 'fleet_partition', issues)
    if block is None:
        return
    _validate_enum(block.get('state'), SCENARIO_STATES,
                   'fleet_partition.state', 'invalid_scenario_state', issues)
    _require_text(block, 'notes', 'fleet_partition.notes', issues)
    size = block.get('expected_fleet_size')
    if not isinstance(size, int) or isinstance(size, bool):
        issues.append(_issue('invalid_fleet_size', 'fleet_partition.expected_fleet_size',
                             'expected_fleet_size must be an integer'))
    elif size < MIN_EXPECTED_FLEET_SIZE:
        issues.append(_issue('invalid_fleet_size', 'fleet_partition.expected_fleet_size',
                             f'expected_fleet_size must be >= {MIN_EXPECTED_FLEET_SIZE}'))


# --------------------------------------------------------------------------- #
# M1 pass criteria (real-run gate — independent of structural validity)        #
# --------------------------------------------------------------------------- #

def measurement_staging_unmet_pass_criteria(manifest: Mapping) -> list[PassCriterion]:
    """Return the M1 pass criteria that are NOT met by ``manifest``.

    A real staging run passes when this list is empty AND
    ``chamber_measurement_staging_errors`` is empty. A template skeleton always
    returns several unmet criteria — by construction it can never pass.
    """
    return [c for c in measurement_staging_pass_summary(manifest) if not c.met]


def measurement_staging_passes_m1(manifest: Mapping) -> bool:
    """The full M1 gate: True only for a structurally-valid *recorded* bundle
    with no unmet criteria.

    Three conditions, all required:

    * ``mode == 'recorded'`` — the recorded-mode gate. A blank template, or a
      template whose fields were filled in with real values but left in
      ``mode='template'``, is not an attested real run and must never clear the
      gate even though it can meet every measurement pass criterion. This is the
      single source of truth for ``--require-pass`` so the CLI carries no gate
      logic of its own.
    * structural validity (``chamber_measurement_staging_errors`` is empty) — a
      recorded bundle that is structurally broken cannot pass. Critically this
      includes the no-fake guard: a recorded bundle that still carries the
      ``<FILL-ME>`` placeholder in ANY field — even a non-pass-criterion field
      such as ``central_stack_version`` — is a ``placeholder_in_recorded``
      structural error and must never clear the gate, even when every
      measurement pass criterion happens to be met. Without this check the gate
      would silently ignore structural errors and report a false pass for a
      half-filled bundle relabelled ``recorded``.
    * every M1 pass criterion is met (``measurement_staging_unmet_pass_criteria``
      is empty).
    """
    if not isinstance(manifest, Mapping):
        return False
    if _text(manifest.get('mode')) != 'recorded':
        return False
    if chamber_measurement_staging_errors(manifest):
        return False
    return not measurement_staging_unmet_pass_criteria(manifest)


def measurement_staging_pass_summary(manifest: Mapping) -> list[PassCriterion]:
    """Evaluate every M1 pass criterion (met + detail), in plan order."""
    if not isinstance(manifest, Mapping):
        manifest = {}
    machines = manifest.get('machines') if _is_list(manifest.get('machines')) else []
    equipment = manifest.get('equipment') if isinstance(manifest.get('equipment'), Mapping) else {}
    measurement = manifest.get('measurement') if isinstance(manifest.get('measurement'), Mapping) else {}
    progress = measurement.get('progress') if isinstance(measurement.get('progress'), Mapping) else {}
    start = measurement.get('start') if isinstance(measurement.get('start'), Mapping) else {}
    correlation = manifest.get('correlation') if isinstance(manifest.get('correlation'), Mapping) else {}
    restart = manifest.get('restart_partition') if isinstance(manifest.get('restart_partition'), Mapping) else {}
    fleet = manifest.get('fleet_partition') if isinstance(manifest.get('fleet_partition'), Mapping) else {}
    timeline = manifest.get('timeline') if _is_list(manifest.get('timeline')) else []

    machine_count = _distinct_physical_machine_count(machines)
    analyzer = _real_value(equipment.get('analyzer_resource'))
    appium = _real_value(equipment.get('appium_device_id'))
    analyzer_idn = _real_value(equipment.get('analyzer_idn'))
    appium_session_id = _real_value(equipment.get('appium_session_id'))
    dut_model = _real_value(equipment.get('dut_model'))
    dut_driven = equipment.get('dut_driven') is True
    fleet_partition_state = _text(fleet.get('state'))
    http_status = start.get('http_status')
    in_use = measurement.get('chamber_in_use_observed') is True
    via_api = progress.get('via_api') is True
    via_ws = progress.get('via_ws') is True
    fallback = _real_value(progress.get('fallback'))
    outcome = _text(measurement.get('outcome'))
    job_id = _real_value(measurement.get('job_id'))
    session_id = _real_value(measurement.get('session_id'))
    corr_api = _real_value(correlation.get('api'))
    corr_ws = _real_value(correlation.get('ws'))
    corr_log = _real_value(correlation.get('log'))
    node_restart = _text(restart.get('node_restart'))
    network_partition = _text(restart.get('network_partition'))
    timeline_phases = _real_timeline_phases(timeline)
    timeline_complete = set(TIMELINE_PHASES) <= timeline_phases

    summary: list[PassCriterion] = []

    summary.append(PassCriterion(
        'two_physical_machines',
        machine_count >= MIN_PHYSICAL_MACHINES,
        f'{machine_count} distinct physical machine(s) by identity<->address '
        f'connected component (need >= {MIN_PHYSICAL_MACHINES}; rows sharing an '
        f'identity or an address — including cross-linked rows — collapse to one '
        f'machine)'))

    summary.append(PassCriterion(
        'real_analyzer_and_appium',
        bool(analyzer and appium),
        f'analyzer_resource={"set" if analyzer else "missing"}, '
        f'appium_device_id={"set" if appium else "missing"}'))

    summary.append(PassCriterion(
        'measurement_path_proof',
        bool(analyzer_idn and appium_session_id and dut_model and dut_driven),
        f'analyzer_idn={"set" if analyzer_idn else "missing"}, '
        f'appium_session_id={"set" if appium_session_id else "missing"}, '
        f'dut_model={"set" if dut_model else "missing"}, dut_driven={dut_driven} '
        f'(operator-attested SCPI/Appium proof-of-life; shape only, not a sweep claim)'))

    summary.append(PassCriterion(
        'measurement_post_succeeds',
        _is_http_success(http_status),
        f'POST /platform/chambers/{{id}}/measurements http_status={http_status!r}'))

    summary.append(PassCriterion(
        'chamber_in_use_transition',
        in_use,
        f'chamber_in_use_observed={in_use}'))

    summary.append(PassCriterion(
        'progress_api_ws_or_fallback',
        (via_api and via_ws) or bool(fallback),
        f'via_api={via_api}, via_ws={via_ws}, '
        f'fallback={"documented" if fallback else "none"} '
        f'(need via_api AND via_ws, or a documented fallback)'))

    summary.append(PassCriterion(
        'timeline_phases_complete',
        timeline_complete,
        f'timeline phases with real timestamps={sorted(timeline_phases) or "none"} '
        f'(need all of {list(TIMELINE_PHASES)})'))

    summary.append(PassCriterion(
        'completion_with_correlated_ids',
        outcome in MEASUREMENT_OUTCOMES and bool(job_id) and bool(session_id)
        and bool(corr_api or corr_ws or corr_log),
        f'outcome={outcome or "missing"}, job_id={"set" if job_id else "missing"}, '
        f'session_id={"set" if session_id else "missing"}, '
        f'correlation={"set" if (corr_api or corr_ws or corr_log) else "missing"}'))

    summary.append(PassCriterion(
        'restart_and_partition_captured_or_not_run',
        node_restart in SCENARIO_STATES and network_partition in SCENARIO_STATES,
        f'node_restart={node_restart or "unset"}, network_partition={network_partition or "unset"}'))

    summary.append(PassCriterion(
        'fleet_partition_captured_or_not_run',
        fleet_partition_state in SCENARIO_STATES,
        f'fleet_partition.state={fleet_partition_state or "unset"} '
        f'(3+ node split-brain; not_run is an honest blocked-by-equipment pass, '
        f'never faked into observed)'))

    return summary


# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #

def _require_object(value, path: str, issues: list[StagingEvidenceIssue]) -> Mapping | None:
    if not isinstance(value, Mapping) or not value:
        issues.append(_issue('missing_required_field', path, f'{path} must be an object'))
        return None
    return value


def _require_text(mapping: Mapping, key: str, path: str,
                  issues: list[StagingEvidenceIssue]) -> None:
    if not _text(mapping.get(key)):
        issues.append(_issue('missing_required_field', path, f'{key} is required'))


def _validate_enum(value, allowed: Sequence[str], path: str, code: str,
                   issues: list[StagingEvidenceIssue], *, allow_empty: bool = False) -> None:
    text = _text(value)
    if not text:
        if not allow_empty:
            issues.append(_issue('missing_required_field', path, f'{path} is required'))
        return
    if text not in allowed:
        issues.append(_issue(code, path, f"{path} must be one of {', '.join(allowed)}"))


def _placeholder_paths(value, path: str):
    """Yield every dotted path whose string value is the template placeholder."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f'{path}.{key}' if path else str(key)
            yield from _placeholder_paths(child, child_path)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _placeholder_paths(child, f'{path}[{index}]')
    elif isinstance(value, str) and value.strip() == TEMPLATE_PLACEHOLDER:
        yield path


def _real_value(value) -> str:
    """Text value with the unfilled placeholder treated as absent."""
    text = _text(value)
    return '' if text == TEMPLATE_PLACEHOLDER else text


def _count_real(values) -> int:
    return sum(1 for v in values if _real_value(v))


def _distinct_physical_machine_count(machines) -> int:
    """Count distinct *physical* machines from the machines array.

    A machine only counts when BOTH its ``identity`` and ``address`` are real
    (non-empty, not the template placeholder). Each row links its identity to
    its address; rows that share either field are transitively the *same*
    physical PC. The count is therefore the number of connected components in
    the bipartite identity<->address graph, NOT the smaller of the two distinct
    cardinalities — the latter over-counts a cross-linked cluster such as
    ``pc-a/.1``, ``pc-a/.2``, ``pc-b/.1`` (distinct ids=2, distinct addrs=2, but
    a single connected cluster = one machine, so M1 must fail).
    """
    parent: dict[str, str] = {}

    def find(node: str) -> str:
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        # path compression
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for raw in machines:
        if not isinstance(raw, Mapping):
            continue
        identity = _real_value(raw.get('identity'))
        address = _real_value(raw.get('address'))
        if not identity or not address:
            continue
        # namespace the two sides so an identity string equal to an address
        # string never collides into one node.
        union(f'identity::{identity}', f'address::{address}')

    return len({find(node) for node in parent})


def _real_timeline_phases(timeline) -> set:
    """Phases whose entry carries a real (non-placeholder) timestamp."""
    phases: set[str] = set()
    for raw in timeline:
        if not isinstance(raw, Mapping):
            continue
        phase = _text(raw.get('phase'))
        if phase in TIMELINE_PHASES and _real_value(raw.get('at')):
            phases.add(phase)
    return phases


def _is_http_success(value) -> bool:
    return (isinstance(value, int) and not isinstance(value, bool)
            and _HTTP_SUCCESS_MIN <= value <= _HTTP_SUCCESS_MAX)


def _is_list(value) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _issue(code: str, path: str, message: str) -> StagingEvidenceIssue:
    return StagingEvidenceIssue(code=code, path=path, message=message)
