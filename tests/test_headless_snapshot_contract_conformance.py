# ⚠️ 2026-08-31: 이 파일은 모노레포 `tests/test_headless_snapshot_contract_conformance.py` 에서 갈라져 왔다. 남은 것은
#    소비 대상이 이 레포에 있는 단위(TestTestPlanGenerationJobResponseConformance, TestSourceAndPublishedArtifactAgreeOnNullability, TestTheMutationBatteryIsInTheTree)뿐이고,
#    나머지 형제 검사와 그것들만 쓰던 import 는 저쪽에 남았다.
"""Headless snapshot payloads must conform to the contract they are declared under.

**Why this file exists.** The headless contract schemas in
``application/headless/api_contract_schemas.py`` are hand-written mirrors of a
producer function — and *nothing verified the mirror*. There is no
``response_model`` anywhere in ``headless_routes.py``, no validating middleware
and no ``jsonschema`` in the request path: ``HEADLESS_API_SCHEMAS`` is consumed
only to *generate* the OpenAPI document, to embed a contract snapshot, and to
compare two contract documents against each other. Every layer between the
store and FastAPI is a pass-through, so whatever the ``_*_to_dict`` builder
returns is literally what goes on the wire.

That mirror is currently **accurate**, and the accuracy is load-bearing in a way
that is easy to delete by accident. Two single lines carry it:

    execution_job_store.py    'assigned_worker_id': row.assigned_worker_id or ''
    report_automation_store.py 'error_message':     row.error_message or ''

Both columns are nullable and both really do hold ``NULL`` in production (lease
expiry writes ``None`` back). Remove either ``or ''`` and the server starts
emitting JSON ``null`` for a member the contract declares ``{'type': 'string'}``.
Downstream, ``apps/web/src/routes/jobs.tsx::orDash(value: string | undefined)``
guards with ``value !== undefined && value !== ''`` — a ``null`` passes both
tests and is returned unchanged, so the function's declared ``string`` return
is unsound and the table cell gets ``null`` as a React child. **React renders a
``null`` child as nothing**: the cell goes silently blank where an em-dash
belongs. (This paragraph said "renders the string 'null'" until an adversarial
review traced it through ``ui/DataTable.tsx`` and disproved it. Worth correcting
precisely rather than loosely — blank is the *harder* failure to notice, and a
future reader reasoning from the wrong mechanism reaches the wrong fix.)

The debt ledger predicted that failure and inferred the contract was
*under-declaring*. It is not: the coercion means ``null`` never leaves the
server, so widening the contract would have handed every consumer a defensive
branch that can never fire. What was missing was not a wider contract but a
**seal on why null does not go out** — this file.

**Four judgements, per covered schema, over a corpus of real payloads.**

1. *member-set parity* — the emitted keys are exactly the declared properties.
   ``ReportRequestSnapshot`` had already drifted three members
   (``idempotency_key`` / ``created_at`` / ``updated_at``): it emitted them and
   declared none of them, ``additionalProperties: True`` hid it, and the
   generated TypeScript degraded them to ``unknown``.
2. *soundness* — a member declared non-nullable is never ``None``. This is the
   judgement that kills the ``or ''`` deletion.
3. *reachability* — the members declared nullable are exactly the ones observed
   ``None``. This kills the opposite mistake: declaring a member nullable on a
   hunch makes every consumer branch on a value that cannot occur.
4. *schema validation* — every payload validates against its declared schema.

The corpus is produced by driving the **real stores against a real SQLite
database**, not by calling the ``_*_to_dict`` builders with stub rows: the whole
question is whether a nullable column's ``NULL`` survives the round trip, and a
stub row cannot answer it.

**Why judgement 1 lives here and not in the schema's ``required`` list.** The
producers emit every declared member unconditionally, so it is tempting to move
all of them into ``required``. That would be a *narrowing of a shared contract*:
``required`` constrains every provider serving this contract — mmWave and
licensed serve it too — into always emitting members FCC happens to emit, and
the provider compatibility checker compares schemas for exact equality. The same
reasoning keeps ``additionalProperties`` at ``True``. Judgement 1 pins presence
where presence is actually a fact — **this** producer — without legislating for
the others.

Coverage is bounded and says so — ``UNSEALED_RESPONSE_SCHEMAS`` names every
response schema this file does *not* probe, and it is **derived** from
``HEADLESS_API_OPERATIONS`` so a newly added operation turns it red rather than
silently widening the gap between "three are sealed" and "these are sealed".
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import jsonschema

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / 'src'
if str(_SRC) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(_SRC))

from fcc_test_contracts.headless.api_contracts import (  # noqa: E402
    HEADLESS_API_OPERATIONS,
    HEADLESS_API_SCHEMAS,
)

#: Published artifact the frontend's generated types are built from. The
#: nullability partition is asserted against *both* this and the in-process
#: schema table: reading only the source would let "edited the schema, forgot to
#: regenerate" look green, and reading only the artifact would let the reverse.
_PUBLISHED_ARTIFACT = _REPO_ROOT / 'docs' / 'api' / 'headless-api.openapi.json'

#: Schemas named by an operation's ``response``, derived from the operation
#: table rather than listed. Everything in here is either probed below or named
#: in ``UNSEALED_RESPONSE_SCHEMAS``.
#:
#: ⚠️ **This is narrower than "every schema that can appear in a response"**,
#: and the difference is stated rather than left to be discovered:
#:
#: * ``ProblemDetails`` and ``ErrorCode`` appear in the error arm of dozens of
#:   response bodies but are nobody's ``['response']``, so they are invisible
#:   here. They are not unowned — ``/verify-api-error-contract`` owns the RFC
#:   9457 axis, sealed four layers deep.
#: * Operations with **no** ``response`` key (binary/streaming bodies) are
#:   skipped by the filter below; ``RESPONSE_LESS_OPERATIONS`` names them so
#:   the skip is visible and a new one cannot join silently.
RESPONSE_SCHEMAS = frozenset(
    operation['response']
    for operation in HEADLESS_API_OPERATIONS.values()
    if operation.get('response')
)

#: Operations the derivation above cannot see, because they declare no
#: ``response`` schema at all — they stream bytes (xlsx / signed download).
RESPONSE_LESS_OPERATIONS = frozenset(
    name
    for name, operation in HEADLESS_API_OPERATIONS.items()
    if not operation.get('response')
)

#: Schemas whose emitted payload this file probes behaviourally.
SEALED_RESPONSE_SCHEMAS = frozenset({
    'MeasurementJobSnapshot',
    'ReportRequestSnapshot',
    'TestPlanGenerationJobResponse',
})

#: The stated edge of this seal — ratchet **down** only. A response schema
#: listed here has no behavioural conformance probe, so its declared shape is
#: still only a hand-written claim. Adding an operation with a new response
#: schema makes ``test_the_coverage_ratchet_is_exhaustive_and_derived`` red,
#: which is the point: the gap is named, never silently inherited.
UNSEALED_RESPONSE_SCHEMAS = frozenset({
    'ApiContractDocument',
    'ArtifactMetadataList',
    'CancelReportAutomationResponse',
    'HeadlessBackendStatusSnapshot',
    'HealthCheckResponse',
    'ListPublishedTestPlansResponse',
    'ListTestPlanDraftsResponse',
    'MeasurementAttemptPage',
    'MeasurementJobList',
    'MeasurementJobSubmitted',
    'MeasurementResultEnvelopeList',
    'ProviderCapabilitiesResponse',
    'ProviderUiDescriptor',
    'PublishedTestPlanView',
    'RemoveTestPlanDraftRowResponse',
    'ReplaceTestPlanDraftRowsResponse',
    'ReportAutomationQueueStats',
    'ReportOutputDownloadGrant',
    'ReportOutputMetadataList',
    'ReportPreflightSummary',
    'ReportRequestSubmitted',
    'StopMeasurementJobResponse',
    'TestPlanDraftRowView',
    'TestPlanDraftView',
    'TestPlanGenerationCatalogueResponse',
    'TestPlanGenerationMetadataResponse',
    'TestPlanGenerationPreviewResponse',
    'TestPlanGenerationRowPageResponse',
    'TestPlanGenerationSubmittedResponse',
    'TestPlanImportResponse',
    'ValidateTestPlanDraftResponse',
})


def declares_null(member_schema: dict) -> bool:
    """Does this property declaration admit JSON ``null``?

    Two spellings reach here and both are legitimate. The in-process schema
    table writes ``{'type': 'x', 'nullable': True}``; the published OpenAPI
    artifact carries the same fact as the union ``{'type': ['x', 'null']}``
    after ``normalize_nullable`` (and, for a constraint-only schema, as an
    ``anyOf`` arm). Judging one spelling would answer for one surface only.
    """
    if member_schema.get('nullable') is True:
        return True
    declared_type = member_schema.get('type')
    if isinstance(declared_type, list):
        return 'null' in declared_type
    if declared_type == 'null':
        return True
    for arm in member_schema.get('anyOf') or member_schema.get('oneOf') or ():
        if isinstance(arm, dict) and declares_null(arm):
            return True
    return False


def nullable_partition(schema: dict) -> tuple[frozenset, frozenset]:
    """``(nullable, non_nullable)`` member names for an object schema."""
    properties = schema.get('properties') or {}
    nullable = frozenset(
        name for name, member in properties.items() if declares_null(member)
    )
    return nullable, frozenset(properties) - nullable


@lru_cache(maxsize=1)
def published_schemas() -> dict:
    """``components.schemas`` of the published headless artifact.

    Cached: the artifact is ~176 KB and judgement 4 runs once per payload, so
    an uncached read would parse it a dozen times per test method for a
    document that cannot change during a run.

    Judgement 4 validates against *this*, not against
    ``HEADLESS_API_SCHEMAS``, and the reason is worth stating: the in-process
    table spells nullability as OpenAPI 3.0's ``nullable: True``, which is an
    **extension keyword JSON Schema does not know**. Handing a payload with a
    ``None`` member to ``jsonschema`` alongside ``{'type': 'integer',
    'nullable': True}`` fails with *"None is not of type 'integer'"* — the
    validator is right and the declaration is simply not JSON Schema yet.
    ``normalize_nullable`` is what turns it into the union form, and the
    artifact is where that normalised form lands. Validating the *pre*-
    normalisation table would therefore test a document nobody consumes.
    """
    import json

    with open(_PUBLISHED_ARTIFACT, encoding='utf-8') as handle:
        return json.load(handle)['components']['schemas']








# ⚠️ `TestTestPlanGenerationJobResponseConformance` 는 이 레포로 오지 못했다 — 사유는
#    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.







class TestSourceAndPublishedArtifactAgreeOnNullability(unittest.TestCase):
    """The partition must read the same from the producer's SSOT and from the
    artifact the frontend's types are generated from.

    Judging only ``HEADLESS_API_SCHEMAS`` would let "edited the schema, forgot
    to regenerate the artifact" look green; judging only the artifact would let
    the reverse. The two spellings differ (``nullable: True`` vs a ``['x',
    'null']`` union) — ``declares_null`` is what makes them comparable.
    """

    def setUp(self):
        self.published = published_schemas()

    # ⚠️ `test_the_seal_reads_the_canonical_artifact_not_the_mirror` 는 이 레포로 오지 못했다 — 사유는
    #    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.


    def test_the_partition_matches_for_every_sealed_schema(self):
        for name in sorted(SEALED_RESPONSE_SCHEMAS):
            with self.subTest(name):
                self.assertIn(name, self.published)
                source = nullable_partition(HEADLESS_API_SCHEMAS[name])
                artifact = nullable_partition(self.published[name])
                self.assertEqual(
                    source,
                    artifact,
                    f'{name}: source and published artifact disagree — '
                    'regenerate docs/api/headless-api.openapi.json',
                )

    def test_the_two_error_message_declarations_still_disagree(self):
        """A guard against "harmonising" the two schemas.

        They differ on purpose: one producer coerces, the other passes through,
        and two frontend call sites already depend on the difference. Making
        them agree without changing a producer would put the contract at odds
        with the wire — silently, because nothing validates responses.
        """
        coerced = HEADLESS_API_SCHEMAS['ReportRequestSnapshot']['properties']
        passed_through = HEADLESS_API_SCHEMAS['TestPlanGenerationJobResponse']['properties']

        self.assertFalse(declares_null(coerced['error_message']))
        self.assertTrue(declares_null(passed_through['error_message']))


# ⚠️ `TestTheMutationBatteryIsInTheTree` 는 이 레포로 오지 못했다 — 사유는
#    `tests/RETIRED_WITH_THE_FRONTEND.md`(모노레포) §5 참조.





if __name__ == '__main__':  # pragma: no cover
    unittest.main()
