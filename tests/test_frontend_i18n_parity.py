"""Frontend i18n en/ko parity seal (fe-i18n-en-ko-parity, Increment 4,
2026-06-13).

The ``apps/web`` UI strings were all Korean inline literals (no localisation).
The equipment reference platform routes copy through an i18n layer with a ko/en
key-parity pre-commit gate (``scripts/check-i18n-keys.mjs``). FCC adopts the same
shape with a self-contained ``t()`` resolver (no new npm dependency — Vite SPA,
no SSR) over two JSON bundles (``src/locales/{ko,en}.json``) and a
``SUPPORTED_LOCALES`` token SSOT. The default locale is ``en`` (English-first);
testers switch to Korean via the header locale toggle and the choice persists in
localStorage. The ko/en key-parity gate means the default-locale fallback never
renders a bare key for either locale.

This Python invariant seals the migration from the backend-only CI lane so a
refactor that re-introduces an inline Korean UI literal, drops a translation, or
adds a third locale token without updating the SSOT fails CI even when ``npm run
typecheck``/``test``/``lint`` is skipped. It is *complementary* to the ``apps/web``
vitest suite (``tests/i18n-parity.test.ts``). Companion skill:
``/verify-frontend-i18n-parity``.

The sealed rules:

1. ``TestI18nSsotExists`` — ``src/i18n/index.ts`` exists and exports the locale
   SSOT (``SUPPORTED_LOCALES`` = ``['ko', 'en']``, ``DEFAULT_LOCALE`` = ``en``,
   ``t``, ``useT``); both locale bundles exist and parse.
2. ``TestLocaleKeyParity`` — the flattened ko key-set equals the en key-set
   (missing translation = 0), and every ``{token}`` placeholder set matches per
   key (so an interpolation that exists in one locale exists in the other).
3. ``TestNoInlineHangulLiteral`` — no inline Hangul (가-힣) literal anywhere
   under ``src`` outside ``locales/`` and the codegen ``generated/`` dir, after
   stripping TS comments (JSDoc Korean is allowed — it is documentation, not a
   rendered string). Every Korean UI string must route through ``t()``. The
   allowlist is an empty module-level ``frozenset`` — ratchet-down, sealing a
   measured-zero state.
4. ``TestNoInlineRenderedEnglishLiteral`` — no inline **English** UI literal in a
   *rendered sink* (see the heuristic below). Catches the literals a Hangul-only
   scan misses (``'Online'`` / ``'Offline'``, ``<dt>Provider ID</dt>``,
   ``textContent = `Boot error …` ``) without flagging identifiers, testids,
   className strings, enum/status tokens, or API field names.
5. ``TestNoExtraLocaleBundles`` — ``src/locales`` contains exactly one JSON
   bundle per ``SUPPORTED_LOCALES`` token (no orphan / no missing bundle).
6. ``TestNoModuleScopeTranslatedString`` — no ``t(...)`` call is *evaluated at
   module-load* time, which would snapshot the active-locale string into a
   top-level ``const``/record/object and freeze it — a later ``setLocale('en')``
   would leave that copy stale. Render-time helper ``t()`` calls (inside a
   *deferred* function/arrow body, e.g. the ``useT()`` hook's ``t``,
   ``ui/errors.ts::describeApiError``, the ``main.tsx`` boot fallback) are
   explicitly ALLOWED — they read the live locale at call time. This seals the
   iter-02 bug where ``control.tsx::OUTCOME_COPY`` stored ``t(...)`` results at
   module scope.

   Module-scope rule — the allowed/forbidden boundary is *module-load
   evaluation*, NOT merely "outside a function body":

     FORBIDDEN (module-load evaluated → frozen locale):
       - bare / object / record initializer:  ``const X = t('k')`` ·
         ``const M = { a: t('k') }``
       - variable-key call:                    ``const k='k'; const X = t(k)``
       - IIFE:                                 ``const X = (() => t('k'))()``
       - eager iteration callback:             ``const X = KEYS.map(() => t('k'))``
       - module-scope template interpolation:  ``const X = `${t('k')}` ``
     ALLOWED (deferred → render/call-time, live locale):
       - function declaration / stored arrow:  ``function f(){ return t('k') }`` ·
         ``const f = () => t('k')``
       - ``useT()`` hook ``t`` inside a component body, and any ``t()`` inside a
         deferred body (incl. an iteration callback *inside* a function, or a
         template ``${t()}`` inside a function).

   Heuristic (documented & precise). A string-aware mini lexer
   (string/comment interiors skipped; template literals scanned only for
   ``${...}`` interpolations) classifies each function body as *deferred*
   (declaration / ``const f = () =>`` / property / JSX handler / hook — bound and
   invoked later) or *eager* (an IIFE ``(() => …)()`` or a callback passed to a
   synchronous ``.map``/``.forEach``/``.filter``/``.reduce``/… iteration method —
   runs at module load). A ``t()`` call is an offence iff NO *deferred* function
   body encloses it (eager bodies and module root do not defer). The detector
   covers **literal AND variable first-arg** calls — at module load both freeze
   (the iter-02 lexer only caught literal-first-arg and treated *every* function
   body, eager included, as render-time, so the four shapes above slipped
   through; see ``TestModuleScopeLexerFixtures`` for the per-shape seal). Known
   limitations (acceptable, monotonic-decrease allowlist available): a callback
   to a *non-iteration* deferred call at module scope (``setTimeout(() => t())``)
   is treated as deferred, and object method-shorthand (``{ foo() { … } }``) at
   module scope is a non-function brace; neither pattern carries a ``t()`` today.
   Allowlist ``MODULE_SCOPE_T_ALLOWLIST`` is an empty ratchet-down ``frozenset``.
7. ``TestTranslationKeyReferences`` — every static ``t('...')`` key referenced
   by application source resolves in the locale SSOT. Bundle parity alone cannot
   catch a consumer typo when the misspelled key is absent from both bundles.

Rendered-English heuristic (rule 4) — documented & precise, NOT a blanket
"no English string literal" ban (which is impossible: English tokens are
indistinguishable from identifiers/testids/className/enum/API-field names by a
static scan). Rendered UI copy reaches the operator through exactly three sinks,
and ONLY these are scanned (``.tsx`` only — JSX lives in ``.tsx``; the dev-gated
``grid-poc`` PoC is excluded, mirroring ``verify-grid-poc-exclusion``):

  (a) **JSX display attributes** — ``aria-label`` / ``label`` / ``title`` /
      ``placeholder`` / ``caption`` / ``description`` / ``alt``.
  (b) **JSX text children** — prose between ``>`` and ``<`` (no braces/operators).
  (c) **DOM text sinks** — ``.textContent =`` in the non-React bootstrap.

For each sink the value has ``t(...)`` calls and ``${...}`` interpolations
stripped first; a remaining quoted/backtick literal (sinks a/c) or prose run
(sink b) holding a run of ≥2 ASCII letters is an offence. So ``label={t('…')}``,
``label={isOnline ? t('…') : t('…')}``, ``label={feature.status}``, and
symbol-only text (``·`` / ``—`` / ``⚠``) all pass; ``label={x ? 'Online' : 'Off'}``
and ``<dt>Provider ID</dt>`` fail. Because every English UI label is now routed
through ``t()`` for genuine ko/en parity, the allowlist is an empty ratchet-down
``frozenset`` — a regression that re-introduces an inline English UI literal
fails CI even with ``npm`` skipped. (This rule was added 2026-06-13 after a Codex
review correctly flagged that the original Hangul-only seal let inline English
labels through — the prior "blanket English ban impossible → not enforced" stance
was an over-broad excuse; the sink heuristic enforces exactly the rendered
positions without the false-positive surface.)
"""
from __future__ import annotations

import json
import re
import unittest
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple
from support.parity import strip_ts_comments

# ``tests/conftest.py`` puts ``scripts/`` on ``sys.path``; this is the same
# import shape ``test_exec_plan_buckets.py`` uses. The blocker census below asks
# the claim-status SSOT rather than re-deriving "does this claim still own its
# scope?" from the registry, which is the drift this module exists to prevent.
from work_claim_status import claim_owns_scope, parse_claim_status


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "apps" / "web"
SRC_DIR = WEB_ROOT / "src"
I18N_MODULE = SRC_DIR / "i18n" / "index.ts"
LOCALES_DIR = SRC_DIR / "locales"
GENERATED_DIR = SRC_DIR / "api" / "generated"

# The locale-token SSOT mirrored on the test side. MUST stay in sync with
# `src/i18n/index.ts::SUPPORTED_LOCALES`; `TestI18nSsotExists` cross-checks the
# source so a drift here or there is caught.
SUPPORTED_LOCALES: tuple[str, ...] = ("ko", "en")
DEFAULT_LOCALE = "en"

# Exact ko/en leaf equality is permitted only when the rendered token is a
# standard identifier, a language name, a format/symbol, or another operator
# term whose meaning is intentionally language-neutral. Every equality is
# named separately; this is not a regex/count allowlist. BLOCKER entries are
# retained as explicit evidence because their owners/consumers cannot be
# changed in this wave without editing the active session's locale bundles.
#
# ⚠️ A `BLOCKER:` reason on its own asserts nothing about meaning — it is prose,
# and prose cannot be falsified by a gate. That is why
# `TestALeafMayNotStayUntranslatedWhenTheBundleTranslatesItElsewhere` below
# exists: it derives the "is this token really language-neutral?" question from
# the bundle instead of from this table. Keep both. This table names *why* a
# human accepted each equality; that class proves the bundle does not already
# contradict the acceptance.
IDENTICAL_LOCALE_LEAF_DISPOSITIONS: dict[str, str] = {
    "routes.equipmentLists.fields.serial_number": "standard serial-number abbreviation",
    "routes.equipmentLists.testItems.BLE": "standard Bluetooth technology acronym and expansion",
    "routes.equipmentLists.testItems.BT": "standard Bluetooth technology acronym and expansion",
    "routes.equipmentLists.testItems.DTS": "standard WLAN technology acronym and band notation",
    "routes.equipmentLists.testItems.UNII": "standard WLAN technology acronym and band notation",
    "routes.fields.areas.mmwave.label": "standard millimetre-wave technology notation",
    "routes.jobs.list.colId": "standard table identifier",
    "routes.layout.appTitle": "FCC product name",
    "routes.layout.documentTitle": "format template with translated placeholders",
    "routes.layout.localeToggle.english": "language name",
    "routes.layout.localeToggle.korean": "language name",
    "routes.membership.assignUserPlaceholder": "operator email example",
    "routes.myProjects.list.fccLabel": "regulatory identifier format",
    "routes.sampleInventory.fieldAp": "standard firmware-part acronym",
    "routes.sampleInventory.fieldBl": "standard firmware-part acronym",
    "routes.sampleInventory.fieldCp": "standard firmware-part acronym",
    "routes.sampleInventory.fieldCsc": "standard firmware-part acronym",
    "routes.sampleInventory.fieldHwRev": "standard hardware-revision abbreviation",
    "routes.sampleInventory.fieldRfCal": "standard calibration abbreviation",
    "routes.sampleInventory.fieldSmsn": "standard serial-number acronym",
    # The editor renders the same operator tokens as the card, through a dynamic
    # key (`SampleEditor.tsx` builds `routes.sampleInventory.editor.fields.${k}`)
    # while `SampleCard.tsx` uses the static `field*` keys above. The two sets are
    # byte-identical, so accepting one and blocking the other was an arbitrary
    # split, not a semantic one. Each is named separately here for the same reason
    # its sibling carries — no wildcard, prefix, or count stands in for them.
    "routes.sampleInventory.editor.fields.ap": "standard firmware-part acronym (editor label)",
    "routes.sampleInventory.editor.fields.bl": "standard firmware-part acronym (editor label)",
    "routes.sampleInventory.editor.fields.cp": "standard firmware-part acronym (editor label)",
    "routes.sampleInventory.editor.fields.csc": "standard firmware-part acronym (editor label)",
    "routes.sampleInventory.editor.fields.rfCal": (
        "standard calibration abbreviation (editor label)"
    ),
    "routes.sampleInventory.editor.fields.hwRev": (
        "standard hardware-revision abbreviation (editor label)"
    ),
    "routes.sampleInventory.editor.fields.smsn": "standard serial-number acronym (editor label)",
    "routes.testPlans.addRowPathPlaceholder": "standard protocol/path notation",
    "routes.testPlans.generator.axis.phys": (
        "existing precise generator label; standard physical-layer acronym"
    ),
    "routes.testPlans.generator.technology.unii": (
        "existing precise generator label; standard spectrum technology notation"
    ),
    "routes.testReports.citation.fccId": "regulatory identifier",
    "routes.testReports.citation.firmware.ap": "standard firmware-part acronym",
    "routes.testReports.citation.firmware.bl": "standard firmware-part acronym",
    "routes.testReports.citation.firmware.cp": "standard firmware-part acronym",
    "routes.testReports.citation.firmware.csc": "standard firmware-part acronym",
    "routes.testReports.citation.firmware.hwRev": "standard hardware-revision abbreviation",
    "routes.testReports.citation.firmware.rfCal": "standard calibration abbreviation",
    "routes.testReports.citation.serialNumber": "standard serial-number abbreviation",
    "ui.percent.aboveHighBand": "symbolic percentage threshold",
    "ui.percent.belowLowBand": "symbolic percentage threshold",
    "ui.percent.complete": "symbolic percentage value",
    "ui.percent.exact": "format template with percentage placeholder",
    "ui.percent.unknown": "symbolic unknown-value marker",
    "ui.percent.unstarted": "symbolic percentage value",
}

#: Identical ko/en leaves that are **not** accepted — an unresolved defect kept
#: visible with a named, still-live owner rather than laundered into the table
#: above. **Empty as of 2026-08-26**: `routes.layout.navPrimaryAria` was deleted
#: as an orphan, the seven sample-inventory editor acronyms were accepted with
#: individual reasons, and `routes.projectResults.provider` /
#: `routes.chambers.startPlanOptionLabel` were resolved in `ko.json` under the
#: operator's in-session-closure determination.
#:
#: ⚠️ Empty is the goal state, not a licence to widen. The rule that governs
#: entries here is asserted on synthetic claims by
#: `test_the_owner_rule_rejects_an_unowned_and_a_closed_blocker`, so it keeps its
#: teeth while this set is empty and starts binding real data the moment an
#: entry returns.
IDENTICAL_LOCALE_BLOCKER_KEYS: frozenset[str] = frozenset()

#: Korean leaves that render **no Hangul at all** while differing from their
#: English counterpart. Sibling of ``IDENTICAL_LOCALE_LEAF_DISPOSITIONS`` and
#: **disjoint from it by construction**: that table owns ``ko == en``, this one
#: owns ``ko != en``. The disjointness is asserted below so no leaf can be moved
#: between the two tables to escape both.
#:
#: ⚠️ **Why this table has to exist.** An independent reviewer measured that the
#: translatability law keys on byte equality of the *English* value, so it can
#: only see repeated English — never untranslated Korean. Of 44 identical ko/en
#: leaves, 24 had an English value that is unique in the bundle and were
#: structurally invisible to it. A leaf like ``routes.sampleInventory.history
#: .revision`` — ko ``revision {revision}`` against en ``Revision {revision}`` —
#: escaped **every** gate on a capital letter, while twelve sibling leaves render
#: the same word as ``리비전``. Neither existing gate could reach it: the
#: disposition table cannot (``ko != en``) and the translatability law cannot
#: (not byte-equal). This axis is that gap, and nothing else.
#:
#: Per-key named reasons only — no wildcard, no prefix, no count. Set equality
#: with the measured candidate set runs in **both** directions, so a repaired
#: leaf must be deleted here (the table can only shrink) and a new one cannot be
#: absorbed by silence.
NO_HANGUL_KO_LEAF_DISPOSITIONS: dict[str, str] = {
    "routes.myProjects.metaField.fcc_grantee_code": (
        "proper noun — an FCC-assigned grantee identifier; both sides render the "
        "same token and differ only in title-casing"
    ),
    "routes.sampleInventory.editor.fields.serialNumber": (
        "device-identifier acronyms (S/N, IMEI); differs from en only by the "
        "interpunct spacing convention"
    ),
    "routes.sampleInventory.fieldSerial": (
        "device-identifier acronyms (S/N, IMEI); static label sibling of the "
        "editor row above, same token, same spacing difference"
    ),
}

#: Latin runs that may stay latin **inside** a Korean sentence, one named reason
#: each. Third sibling of ``IDENTICAL_LOCALE_LEAF_DISPOSITIONS`` and
#: ``NO_HANGUL_KO_LEAF_DISPOSITIONS``, and the only one of the three whose unit is
#: a **term** rather than a leaf.
#:
#: ⚠️ **Why this table has to exist.** All three preceding laws share one blind
#: spot, and a third independent reviewer measured it: an English word sitting
#: *inside* a Korean sentence satisfies none of their preconditions. The
#: identical-leaf table needs ``ko == en``; the translatability law needs byte
#: equality of the English value; the no-Hangul axis needs the ko side to contain
#: *no* Hangul at all. ``템플릿 export`` is none of those, so every gate this
#: repository had built was structurally unable to see it — while the same bundle
#: renders ``Export CSV`` as ``CSV 내보내기`` twelve keys away.
#:
#: ⚠️ **Why the unit is the run and not the leaf.** "Do we leave this English word
#: in Korean?" is a vocabulary decision made once per term. Keying on leaves would
#: demand ~100 entries that all repeat the same sentence, and the tech-debt ledger
#: already prescribes the opposite for this exact surface: *반례 그룹을 도메인 용어
#: 축으로 승격 — 선언된 용어 하나당 ko 표기 하나*.
#:
#: Per-run named reasons only — no wildcard, no prefix, no count. Set equality
#: with the measured offender set runs in **both** directions, so a repaired run
#: must be deleted here (the table can only shrink).
#:
#: ⚠️ **The reason is per run; the sites are enumerated.** A fourth independent
#: reviewer measured that a run-keyed table alone leaves every declared run a
#: permanently open channel: they injected six genuinely untranslated leaves, each
#: reusing an already-declared run, and the seal stayed green 6/6. The vocabulary
#: decision really is one-per-term — that part of the design stands — but *where*
#: a term is allowed to appear is a different question, and leaving it unasked
#: made "a new offender cannot be absorbed silently" false as written. Each entry
#: therefore carries the exact ko leaves the run may occur in, and that set is
#: asserted equal to the measured one in both directions too. 14 runs, 35 sites.
class RunDisposition(NamedTuple):
    """Why a latin run may stay latin, and the exact leaves it may stay in."""

    reason: str
    keys: tuple[str, ...]


KO_LATIN_RUN_DISPOSITIONS: dict[str, RunDisposition] = {
    # ── Korean-first parenthetical gloss: 한국어(English). The Korean IS present;
    # the latin disambiguates a term the operator also meets in English elsewhere
    # (an API field, a status token, a report column). Removing the gloss would
    # lose information, which is the opposite of what this axis is for.
    "edition": RunDisposition(
        "parenthetical gloss — every occurrence is 판(edition); the Korean term leads and the "
        "latin names the API/report field the operator also sees",
        (
            "routes.testReports.citationEditionHint",
            "routes.testReports.citationEditionLabel",
            "routes.testReports.column.edition",
            "routes.testReports.create.editionConflict",
            "routes.testReports.create.invalid",
            "routes.testReports.create.optionalHint",
            "routes.testReports.description",
            "routes.testReports.domainNote",
            "routes.testReports.emptyDescription",
            "routes.testReports.field.edition",
            "routes.testReports.reportNumberAbsent.edition",
        ),
    ),
    "idle": RunDisposition(
        "parenthetical gloss — 대기(idle); the latin names the chamber status token the "
        "availability list and the node heartbeat both report",
        (
            "routes.chambers.noneStartableDescription",
            "routes.chambers.recoveryConflict",
            "routes.chambers.recoveryConflictOffline",
        ),
    ),
    "admin": RunDisposition(
        "parenthetical gloss — 관리(admin) 권한; the latin names the RBAC scope token itself, "
        "which is what the operator must ask to be granted",
        ("routes.membership.adminTokenHint",),
    ),
    "claim": RunDisposition(
        "parenthetical gloss — 점유(claim); the latin names the measurement-claim operation the "
        "API and the audit log both call a claim",
        ("routes.projects.offlineNote",),
    ),
    "Conducted": RunDisposition(
        "parenthetical gloss — 전도(Conducted); the latin names the FCC test condition as the "
        "regulation and the report both spell it",
        (
            "routes.fields.areas.licensed_conducted.label",
            "routes.fields.areas.unlicensed_conducted.label",
        ),
    ),
    "Radiated": RunDisposition(
        "parenthetical gloss — 방사(Radiated); sibling of the row above, same regulation, same "
        "report vocabulary",
        ("routes.fields.areas.unlicensed_radiated.label",),
    ),
    # ── Workbench-area proper nouns. Siblings of `routes.fields.areas.mmwave.label`,
    # which `IDENTICAL_LOCALE_LEAF_DISPOSITIONS` already accepts by name for the
    # same reason: these are the provider/area identities of this system, not
    # descriptions of them.
    "Licensed": RunDisposition(
        "workbench-area proper noun — the licensed-band provider identity, sibling of the "
        "already-declared routes.fields.areas.mmwave.label",
        ("routes.fields.areas.licensed_conducted.label",),
    ),
    "Unlicensed": RunDisposition(
        "workbench-area proper noun — the unlicensed-radio provider identity, sibling of the "
        "already-declared routes.fields.areas.mmwave.label",
        (
            "routes.fields.areas.unlicensed_conducted.label",
            "routes.fields.areas.unlicensed_radiated.label",
        ),
    ),
    # ── Machine identifiers the operator types or reads verbatim. Translating
    # these would produce a string that does not work when pasted.
    "project": RunDisposition(
        "URL query-parameter name — every occurrence is the literal ?project= the hint tells the "
        "operator is preserved across screens",
        (
            "routes.fields.contextHint",
            "routes.myProjects.createHint",
            "routes.progress.nextHint",
            "routes.projects.nextHint",
            "routes.sampleInventory.nextHint",
            "routes.testPlans.nextHint",
        ),
    ),
    "area": RunDisposition(
        "URL query-parameter name — the literal ?area= carried beside ?project=",
        ("routes.fields.contextHint",),
    ),
    "plan": RunDisposition(
        "identifier prefix and machine echo — the literal plan-… id shape and the published id "
        "echoed back after publishing",
        (
            "routes.chambers.startPlanPlaceholder",
            "routes.testPlans.publishSuccess",
        ),
    ),
    "origin": RunDisposition(
        "database column name — one of origin·derived_kind·generation_key·scope_revision, the "
        "columns a CSV round trip does not preserve",
        ("routes.testPlans.bulkRoundTripNote",),
    ),
    "xlsx": RunDisposition(
        "file-format extension — the literal .xlsx the operator selects and uploads",
        (
            "errors.workbookUploadUnsupportedType",
            "routes.testPlans.importFileLabel",
        ),
    ),
    # ── ⚠️ The one remaining false positive of this law, declared rather than
    # filtered away. Narrowing the predicate to drop single-letter runs would be
    # the "narrowing a predicate loses the unenumerated" failure this repository
    # has already paid for. Writing the bogus proof down lets a reader disagree.
    #
    # ⚠️ Its sibling `S` used to sit here with an argument that it was
    # *structurally* unclosable — that the boundary silencing the bogus proof was
    # the same one blinding the axis to the real `HTTP(S)`. A fourth independent
    # reviewer refuted that: the fix is not a boundary change but a **tokenizer**
    # change, and `_LATIN_RUN_RE` now keeps `HTTP(S) Session Node` as one run, so
    # `S` never enters the census and is *more* visible than before, not less. An
    # exemption argued from a false impossibility is an unnecessary exemption.
    "A": RunDisposition(
        "single-letter example value (예: A) — the contradicting 'proof' is the English article "
        "in 'Select a project…', not a translation of anything",
        ("routes.chambers.startSamplePlaceholder",),
    ),
}

#: ⚠️ **The third partition, and the reason it exists.** ``latin_run_offenders``
#: asks whether the bundle *contradicts* a run. When no other leaf carries the run
#: in English there is nothing to contradict it with, and the run used to pass as
#: "fine" — a verdict of "no evidence" wearing the clothes of a verdict of "no
#: defect". An independent reviewer measured that reach at 946 of 977 distinct
#: English words and named four ordinary ones in the shadow: ``bucket``,
#: ``minutes``, ``capability``, ``index``.
#:
#: The ledger prescribed a **declared domain glossary** — one Korean rendering per
#: English term. That is a bigger obligation than the hole: it must have an
#: opinion about all 977 words including the 946 the bundle already judges, and it
#: creates a second authority that can disagree with the bundle. The hole is only
#: where latin actually lands *inside Korean copy*. At this SHA that is 28 runs
#: across 33 leaves, and each one gets a reason a reader can disagree with plus
#: the exact sites, under the same bidirectional set equality the two sibling
#: tables use.
#:
#: ⚠️ **This does not make the axis omniscient, and the claim is not that.** It
#: makes its silence *declared*: every latin run in a Korean value is now
#: contradicted (and declared), corroborated (and derived), or unjudgeable (and
#: declared). The three are asserted pairwise disjoint and exhaustive over the
#: census, so "the law had no opinion and nothing said so" stops being reachable.
KO_LATIN_RUN_BLIND_DISPOSITIONS: dict[str, RunDisposition] = {
    # ── Initialisms this industry writes in latin in both languages. None of
    # them is an English *word* an operator could be asked to read in Korean;
    # each names a protocol, a bus, a standard, or a system by its registered
    # short form.
    "API": RunDisposition(
        "protocol initialism — the diagnostics page names the HTTP surface an operator asks the "
        "infrastructure team about; there is no Korean short form of it",
        (
            "routes.diagnostics.apiVersion",
            "routes.diagnostics.nextSessionEnabled",
            "routes.diagnostics.sessionUnavailable",
            "routes.diagnostics.stepSession",
        ),
    ),
    "CSV": RunDisposition(
        "file-format initialism — the operator picks, edits and re-uploads a .csv, and the format "
        "name is what the file manager and the spreadsheet both show",
        (
            "routes.testPlans.bulkCsvLabel",
            "routes.testPlans.bulkRoundTripNote",
            "routes.testPlans.bulkStaleNotice",
            "routes.testPlans.bulkUnsaved",
            "routes.testPlans.bulkWipeNotice",
            "routes.testPlans.exportCsvButton",
        ),
    ),
    "DCCF": RunDisposition(
        "measurement-domain initialism — Duty Cycle Correction Factor, the name the FCC procedure "
        "and this repository's own measurement modules both use",
        ("ui.errorState.variants.dccfMissing.hint",),
    ),
    "EMS": RunDisposition(
        "system proper noun — the equipment management system is a separate product with its own "
        "name; the Korean expansion is already beside it in the same sentence",
        ("routes.equipmentLists.domainNote",),
    ),
    "EUT": RunDisposition(
        "regulatory initialism — Equipment Under Test, as the FCC procedure and the report both "
        "spell it; the Korean 시험 대상 leads and this names the term of art",
        ("routes.myProjects.metaField.eut_description",),
    ),
    "GPIB": RunDisposition(
        "instrument-bus initialism — the physical connector an operator looks for on the analyser; "
        "translating it would name no cable that exists",
        ("ui.errorState.variants.instrumentOffline.hint",),
    ),
    "LAN": RunDisposition(
        "network initialism — the sibling of GPIB in the same sentence, and the word the chamber "
        "configuration screen uses for the same connection",
        ("ui.errorState.variants.instrumentOffline.hint",),
    ),
    "HTTPS": RunDisposition(
        "URL scheme — the literal an operator types into the chamber address field; a translated "
        "scheme is not a scheme",
        ("routes.chambers.bootstrapValidationUrl",),
    ),
    "IdP": RunDisposition(
        "identity-provider initialism — the term the infrastructure team and the OIDC vocabulary "
        "both use, and the word the operator has to say when asking for onboarding",
        (
            "auth.failure.idpUnreachable.description",
            "routes.membership.errorNotFound",
        ),
    ),
    "OIDC": RunDisposition(
        "protocol initialism — the login standard by name; the administrator this message asks the "
        "operator to contact configures it under exactly this word",
        ("auth.failure.idpConfigMissing.description",),
    ),
    "URI": RunDisposition(
        "OIDC configuration field name — redirect URI is the key in the identity-provider console, "
        "not a description of one",
        ("auth.failure.idpConfigMissing.description",),
    ),
    "UTF-8": RunDisposition(
        "character-encoding standard name — the byte limit is defined in terms of it, and the "
        "Hangul-character equivalent is already given beside it",
        ("auth.local.passwordRule",),
    ),
    "ISO 8601": RunDisposition(
        "date-format standard name — the operator types a value in that exact format, so the name "
        "is the instruction",
        ("routes.membership.assignExpiresLabel",),
    ),
    "PC": RunDisposition(
        "hardware initialism — the chamber's measurement computer; every occurrence is preceded by "
        "its Korean description (측정 PC · 노드 PC · 챔버 PC)",
        (
            "routes.chambers.emptyDescription",
            "routes.chambers.equipmentTakesEffect",
            "routes.chambers.pageDescription",
            "routes.chambers.recoveryUnavailable",
            "routes.control.stopUnavailableUnknown",
        ),
    ),
    # ── Radio-technology and vendor enumerations. These are the names of
    # standards and products, and a list of them is a list of proper nouns.
    "BT/BLE/DTS/UNII": RunDisposition(
        "test-category enumeration — Bluetooth, Bluetooth LE, digital transmission systems and the "
        "UNII bands, each named as the FCC rule part names it",
        ("routes.progress.description",),
    ),
    "BT/BLE/WLAN": RunDisposition(
        "radio-standard enumeration — the sibling of the row above on the workbench-area blurb, "
        "same standards, same spelling",
        ("routes.fields.areas.unlicensed_conducted.blurb",),
    ),
    "Keycloak/Azure AD/Okta": RunDisposition(
        "identity-product enumeration — three vendor product names given as examples of what an "
        "IdP is; a translated product name identifies no product",
        ("auth.failure.idpUnreachable.description",),
    ),
    "HTTP(S) Session Node": RunDisposition(
        "component proper noun with its scheme — the Session Node is this system's own named "
        "component and the operator enters its address; kept as one token deliberately, see "
        "_LATIN_RUN_RE",
        ("routes.chambers.bootstrapValidation",),
    ),
    # ── Names of things outside this screen: a workbook sheet, a device model,
    # two export forms. A form's name does not change with the reader's language.
    "Frequency Table": RunDisposition(
        "workbook sheet proper noun — the lookup sheet an operator opens by that name; the Korean "
        "조회 표 leads and this names which sheet",
        ("errors.referenceDataNotProvisioned",),
    ),
    "SM-S921U": RunDisposition(
        "device model number example — the placeholder shows the shape of a value the operator "
        "copies off the device label",
        ("routes.myProjects.create.modelPlaceholder",),
    ),
    "PM Status": RunDisposition(
        "export-form proper noun fixed by operator determination 6 (2026-08-27) — the repository's "
        "own 시험원 material calls this form PM 상태표 / PM Status, and both bundles now agree",
        ("routes.sampleInventory.export.pm",),
    ),
    "Sample Data": RunDisposition(
        "export-form proper noun fixed by operator determination 6 (2026-08-27) — the sibling of "
        "PM Status, named Sample DATA in two 시험원 education artifacts",
        ("routes.sampleInventory.export.rf",),
    ),
    "Duty": RunDisposition(
        "measurement-type proper noun — the duty measurement is a named step in the FCC procedure "
        "and a named module in this repository; DCCF in the same sentence is derived from it",
        ("ui.errorState.variants.dccfMissing.hint",),
    ),
    # ── Machine identifiers. Translating one produces a string that does not
    # work when it is pasted, which is the opposite of helping.
    "derived_kind": RunDisposition(
        "database column name — one of the four provenance columns a CSV round trip drops, listed "
        "verbatim so the operator can find them in the exported file",
        ("routes.testPlans.bulkRoundTripNote",),
    ),
    "generation_key": RunDisposition(
        "database column name — sibling of derived_kind in the same enumeration",
        ("routes.testPlans.bulkRoundTripNote",),
    ),
    "scope_revision": RunDisposition(
        "database column name — sibling of derived_kind in the same enumeration",
        ("routes.testPlans.bulkRoundTripNote",),
    ),
    "issuer": RunDisposition(
        "OIDC configuration key name — the administrator reads this key in the identity-provider "
        "console; the Korean 발급자 leads and this names the setting",
        ("auth.failure.idpConfigMissing.description",),
    ),
    # ── ⚠️ The one ordinary English term in the shadow today, and the reviewer
    # named it: `capability`. It survives as a Korean-first parenthetical gloss,
    # the shape KO_LATIN_RUN_DISPOSITIONS already accepts for `edition` and
    # `idle` — the Korean 서버 leads and the latin names the server contract the
    # operator meets in the API. `bucket`, `minutes` and `index`, the other three
    # the reviewer named, occur in **no** Korean value at this SHA; if one ever
    # does it lands here and is red until somebody writes down why.
    "capability matrix": RunDisposition(
        "parenthetical gloss — 서버(capability matrix); the Korean leads and the latin names the "
        "server-published contract the generator screen renders verbatim",
        ("routes.testPlans.generator.description",),
    ),
}

#: Hangul syllables plus both jamo blocks. A leaf is "Korean" for this axis when
#: any of these appears; the axis is about *script*, not about vocabulary, which
#: is what keeps it free of a term list.
_HANGUL_RE = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")
_LATIN_RE = re.compile(r"[A-Za-z]")
#: Placeholders carry a runtime value, not copy. They are removed before judging
#: so a leaf that is *only* a value slot (``{provider}``) is not an offender,
#: while ``revision {revision}`` still is — its latin run survives the strip.
_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")


def no_hangul_latin_candidates(flat_ko: dict[str, str]) -> set[str]:
    """ko leaves that render latin letters and no Hangul, placeholders removed.

    Deliberately computed **before** any comparison with ``en``: this is the
    census anchor that survives resolution. "No offenders" and "the detector
    never ran" print the same result, so the non-vacuity assertion has to hold on
    to something the repair cannot delete — and the candidate set is that,
    because the ~36 leaves the identical-leaf table already owns stay in it.
    """
    candidates: set[str] = set()
    for key, korean in flat_ko.items():
        if _HANGUL_RE.search(korean):
            continue
        if not _LATIN_RE.search(_PLACEHOLDER_RE.sub("", korean)):
            continue
        candidates.add(key)
    return candidates


#: Placeholders become a **digit**, not a space. ``{value}m`` must stay one token:
#: substituting a space leaves a bare ``m`` that the English minute suffix then
#: "contradicts", and ``1M`` (a data rate) yields a bare ``M`` the same way. Both
#: were measured false runs in the first draft of this axis, not hypotheticals.
_RUN_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}")
#: A maximal latin run: letters, then any digits/inner separators that keep a
#: multi-word English phrase together. ``Frequency Table`` is therefore ONE run and
#: not the word ``table`` — which matters, because the bundle translates ``table``
#: and does not translate the name of that lookup sheet.
#: Characters that are part of a word for boundary purposes.
#:
#: ⚠️ **Both apostrophes.** The first draft listed only ASCII ``'`` — and this
#: corpus does not use it. `en.json` writes ``the report’s §6 table`` with U+2019,
#: so ``screen’s`` still yielded a phantom run ``s`` and two of the twelve live
#: proofs for the old ``S`` entry were exactly that artifact. The regression test
#: below now pins **both** spellings, because pinning one is the
#: "seal that asks about spelling" failure inside the commit that names it.
_WORD_CHAR = r"[A-Za-z0-9'’]"
#: ⚠️ The lookbehind is the same boundary ``_run_pattern`` uses, and it has to be
#: here too. Without it the extractor and the oracle disagree: ``{value}m``
#: normalises to ``0m`` and the extractor would report a run ``m`` that the oracle
#: can never match, putting a token nobody wrote into the census.
#:
#: ⚠️ **A parenthesised suffix is an inner separator, not a break.** ``HTTP(S)`` is
#: one token an operator types, and splitting it produced a bare ``S`` that the
#: English plural marker in ``field(s) changed`` and the unit in
#: ``Signal timeout (s)`` then "contradicted". The first version of this axis
#: declared ``S`` and argued the narrowing was *structurally* impossible — that the
#: boundary silencing the bogus proof would also blind the axis to the real
#: ``HTTP(S)``. That argument was refuted by an independent reviewer and it was a
#: boundary-shaped answer to a tokenizer-shaped question: keeping ``(S)`` inside
#: the run makes ``HTTP(S) Session Node`` **more** visible, not less, and the bare
#: ``S`` never enters the census at all.
#: ⚠️ The parenthesised segment is a **matched pair or nothing** — an earlier
#: draft allowed a trailing ``)`` after any segment and produced the run
#: ``Frequency Table)`` out of ``조회 표(예: Frequency Table)``. A closing bracket
#: that no opening bracket introduced is punctuation, not part of the token.
_LATIN_RUN_RE = re.compile(
    r"(?<!" + _WORD_CHAR + r")[A-Za-z][A-Za-z0-9]*"
    r"(?:[ \-_.&/][A-Za-z0-9]+|\([A-Za-z0-9]+\))*"
)


def _run_text(value: str) -> str:
    return _RUN_PLACEHOLDER_RE.sub("0", value)


def latin_runs(value: str) -> list[str]:
    """Every maximal latin run in ``value``, placeholders neutralised."""
    return [match.group(0) for match in _LATIN_RUN_RE.finditer(_run_text(value))]


@lru_cache(maxsize=None)
def _run_pattern(run: str) -> re.Pattern[str]:
    #: Both apostrophes count as word characters on either side — see
    #: ``_WORD_CHAR``. The extractor and the oracle share the class deliberately:
    #: when they disagreed, the census held tokens the oracle could never match.
    return re.compile(
        rf"(?<!{_WORD_CHAR}){re.escape(run)}(?!{_WORD_CHAR})", re.IGNORECASE
    )


def _run_occurs(run: str, value: str) -> bool:
    return _run_pattern(run).search(_run_text(value)) is not None


def mixed_script_latin_candidates(flat_ko: dict[str, str]) -> dict[str, set[str]]:
    """``run -> keys`` for every latin run inside a ko value that also has Hangul.

    Computed **before** any comparison with ``en``: this is the census anchor that
    survives resolution. "No offenders" and "the detector never ran" print the
    same result, so the non-vacuity assertion has to hold on to something the
    repair cannot delete — and glossed terms, area names and URL parameters keep
    this populated no matter how many untranslated words get fixed.
    """
    census: dict[str, set[str]] = {}
    for key, korean in flat_ko.items():
        if not _HANGUL_RE.search(korean):
            continue
        for run in latin_runs(korean):
            census.setdefault(run, set()).add(key)
    return census


def latin_run_offenders(
    flat_ko: dict[str, str], flat_en: dict[str, str]
) -> dict[str, list[str]]:
    """Runs the bundle itself contradicts, mapped to the leaves that prove it.

    The predicate is ``TestALeafMayNotStayUntranslatedWhenTheBundleTranslatesItElsewhere``
    moved one level down — from the whole value to the run inside it:

        a latin run in a Korean value is untranslated when some *other* leaf
        carries that same run in its English value and renders that leaf's Korean
        without it.

    The bundle proves the term translatable by its own content, so this needs no
    vocabulary list, no regex of "bad words", and no count — exactly the three
    shapes M-7 forbids.
    """
    offenders: dict[str, list[str]] = {}
    for run, keys in mixed_script_latin_candidates(flat_ko).items():
        proof = sorted(
            other
            for other in run_carriers(run, keys, flat_ko, flat_en)
            if not _run_occurs(run, flat_ko[other])
        )
        if proof:
            offenders[run] = proof
    return offenders


def run_carriers(
    run: str, keys: set[str] | frozenset[str], flat_ko: dict[str, str], flat_en: dict[str, str]
) -> list[str]:
    """Leaves **other than** ``keys`` whose English value carries ``run``.

    ⚠️ **One definition, because the partition's exhaustiveness rests on it.**
    "Contradicted" and "unjudgeable" are the two halves of the same question —
    *what does the rest of the bundle say about this term?* — and if each
    computed its own carrier set they could disagree, leaving a run that belongs
    to neither and is therefore judged by nothing. That is precisely the state
    this axis exists to make impossible, so it must not be reachable by drift
    between two copies of one predicate.
    """
    return sorted(
        other
        for other, english in flat_en.items()
        if other not in keys
        and flat_ko.get(other) is not None
        and _run_occurs(run, english)
    )


def latin_run_unjudgeable(
    flat_ko: dict[str, str], flat_en: dict[str, str]
) -> dict[str, list[str]]:
    """Runs the bundle **cannot** judge, mapped to the leaves they occur in.

    ⚠️ **This is the 3% the offender law was silent about, and silence is what
    made it a defect rather than a limit.** ``latin_run_offenders`` asks whether
    some *other* leaf carries the run in English and renders its Korean without
    it. When no other leaf carries the run at all — because the English term is
    unique in the bundle — that question has no evidence either way, and the run
    fell through as "fine". An independent reviewer measured the reach at 946 of
    977 distinct English words, and named four ordinary ones in the shadow:
    ``bucket``, ``minutes``, ``capability``, ``index``.

    The repayment the ledger prescribed was a declared domain glossary — one
    Korean rendering per English term. That is sound and it is larger than the
    hole: a glossary must have an opinion about all 977 words, including the 946
    the bundle already judges correctly, and it introduces a second authority
    that can disagree with the bundle. The hole is only where latin actually
    lands *inside Korean copy*, and that set is small enough to enumerate with a
    reason apiece.
    """
    unjudgeable: dict[str, list[str]] = {}
    for run, keys in mixed_script_latin_candidates(flat_ko).items():
        if not run_carriers(run, keys, flat_ko, flat_en):
            unjudgeable[run] = sorted(keys)
    return unjudgeable


def latin_run_corroborated(flat_ko: dict[str, str], flat_en: dict[str, str]) -> dict[str, list[str]]:
    """Runs other leaves carry in English **and** keep in their Korean.

    The bundle has been asked and has answered "this term stays latin here too",
    so no declaration is owed — the evidence is derived and re-derived on every
    run. Named as its own partition so the three together can be asserted
    exhaustive: a run that belonged to none of them would be exactly the silent
    case this axis exists to abolish.
    """
    offenders = latin_run_offenders(flat_ko, flat_en)
    unjudgeable = latin_run_unjudgeable(flat_ko, flat_en)
    return {
        run: sorted(keys)
        for run, keys in mixed_script_latin_candidates(flat_ko).items()
        if run not in offenders and run not in unjudgeable
    }


def _reason_names_slug(reason: str, slug: str) -> bool:
    """Whether ``reason`` names the work-claim ``slug`` as a whole token.

    ⚠️ **Not a substring test.** Slugs in `.claude/work-claims/` genuinely nest —
    measured 2026-08-26, seven pairs do (`reference-web-authoring` inside
    `reference-web-authoring-closure`, `session-workbook-upload` inside
    `session-workbook-upload-ui`, and five more). Under `slug in reason` a
    blocker that names the longer slug would also resolve against the shorter
    claim, so the gate's verdict would depend on an unrelated claim's status —
    the exact "names overlap, declared identities do not" failure this
    repository has paid for before. Slugs are kebab-case, so the boundary must
    exclude ``-`` as well as word characters.
    """
    return re.search(rf"(?<![\w-]){re.escape(slug)}(?![\w-])", reason) is not None


def _blocker_owner_problems(
    blockers: frozenset[str] | set[str],
    dispositions: dict[str, str],
    claim_statuses: dict[str, str | None],
) -> list[str]:
    """Which blockers fail to name a work-claim that still owns its scope.

    ⚠️ **This is a function, not an inline loop, because the goal state has zero
    blockers.** When every equality is resolved the rule is vacuously true over
    real data, and a rule that can only be exercised while it is being violated
    is a rule nobody can check. Taking ``claim_statuses`` as an argument lets the
    seal feed it a synthetic registry — an unowned reason, a reason naming a
    closed claim — so the behaviour is asserted whether or not a blocker happens
    to exist today.

    ``claim_statuses`` maps claim slug → raw ``status`` value, exactly as read
    from the registry. Resolution goes through the claim-status SSOT so this does
    not become a second parse of that vocabulary.
    """
    problems: list[str] = []
    for key in sorted(blockers):
        reason = dispositions[key]
        named = [slug for slug in sorted(claim_statuses) if _reason_names_slug(reason, slug)]
        if not named:
            problems.append(
                f"blocker {key} names no work-claim slug; reason was: {reason!r}"
            )
            continue
        for slug in named:
            status = parse_claim_status(claim_statuses[slug], source=slug)
            if not claim_owns_scope(status):
                problems.append(
                    f"blocker {key} names {slug}, which no longer owns its scope "
                    f"(status {status.value}) — resolve the leaf or re-own the blocker"
                )
    return problems

# Ratchet-down exception allowlist (relative-to-src POSIX paths permitted to
# retain an inline Hangul literal). Currently EMPTY — every Korean UI string is
# routed through `t()`. Adding an entry requires a documented decision; the
# policy is monotonic-decrease.
INLINE_HANGUL_ALLOWLIST: frozenset[str] = frozenset()

# Ratchet-down exception allowlist for rule 4 (rendered English literal). Entries
# are ``relpath:literal`` (e.g. ``routes/x.tsx:Foo Bar``). Currently EMPTY — every
# English UI label is routed through `t()`. Adding an entry requires a documented
# decision; the policy is monotonic-decrease.
RENDERED_ENGLISH_ALLOWLIST: frozenset[str] = frozenset()

# Ratchet-down exception allowlist for rule 6 (module-scope translated string).
# Entries are ``relpath:line`` (e.g. ``routes/x.tsx:42``). Currently EMPTY — no
# literal-key `t()` is evaluated at module-load time. Adding an entry requires a
# documented decision; the policy is monotonic-decrease.
MODULE_SCOPE_T_ALLOWLIST: frozenset[str] = frozenset()

# JSX display attributes whose value is rendered to the operator (visible label
# or assistive-tech name). `aria-labelledby`/`aria-describedby` are NOT here —
# they carry element-id references, not copy.
SINK_ATTRS: tuple[str, ...] = (
    "aria-label",
    "label",
    "title",
    "placeholder",
    "caption",
    "description",
    "alt",
)

# `.tsx` files excluded from the rendered-English scan (dev-gated PoC — never in
# a production build; mirrors `tests/test_grid_poc_exclusion.py`).
RENDERED_ENGLISH_EXCLUDED = ("routes/grid-poc.tsx", "routes/grid-poc.fixture.ts")

_HANGUL = re.compile(r"[가-힣]")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
# A `t('key', …)` / `t("key")` call — stripped before scanning a sink value so the
# (English-letter) translation KEY is never mistaken for a rendered literal.
_T_CALL = re.compile(r"\bt\(\s*[`'\"][^`'\"]*[`'\"][^)]*\)")
# A `${ … }` template interpolation — stripped so interpolated data/`t()` is not
# scanned as a literal.
_TEMPLATE_INTERP = re.compile(r"\$\{[^}]*\}")
# A quoted/backtick string literal still holding a run of ≥2 ASCII letters
# (after the two strips above) — the offence signature for sinks (a) and (c).
_ENGLISH_IN_LITERAL = re.compile(r"[`'\"][^`'\"]*[A-Za-z]{2,}[^`'\"]*[`'\"]")
# A JSX display attribute followed by its value (string literal or single-level
# `{ … }` brace expression). Used for sink (a).
_SINK_ATTR = re.compile(
    r"(?:" + "|".join(re.escape(a) for a in SINK_ATTRS) + r")\s*=\s*"
    r"(\"[^\"]*\"|'[^']*'|\{(?:[^{}]|\{[^{}]*\})*\})"
)
# JSX text between `>` and `<` with no braces/operators (prose only). Sink (b).
_JSX_TEXT = re.compile(r">([^<>{}\n]+)<")
# Pure prose: starts + ends with a letter, only letters/space/simple punctuation
# in between (rejects comparison fragments like ` 0 && y `, identifiers, symbols).
_PROSE = re.compile(r"^[A-Za-z][A-Za-z '’.,!?\-]*[A-Za-z]$")
# `.textContent = <value>` up to the statement end. Sink (c).
_TEXTCONTENT = re.compile(r"\.textContent\s*=\s*([^;\n]+)")
# A `t(` call — the bare identifier ``t`` immediately followed by ``(``. The
# first argument may be a string literal OR a variable key: at module-load
# context BOTH freeze the copy to the import-time locale, so rule 6 flags either
# (the iter-02 hardening — a prior version only caught literal-first-arg calls,
# letting ``const k = 'errors.x'; const X = t(k)`` through). The lookbehind
# rejects ``.test(`` / ``format(`` / any identifier ending in ``t``; a
# ``function t(`` *declaration* is skipped by the lexer before this can match.
_T_CALL_AT = re.compile(r"(?<![\w$.])t\(")
_LITERAL_T_KEY = re.compile(r"(?<![\w$.])t\(\s*(['\"`])([^'\"`$]+)\1")

# Array iteration methods whose callback runs synchronously at call time. A
# function literal passed to one of these AT MODULE SCOPE is therefore evaluated
# at module load (eager) — a `t()` inside such a callback is NOT deferred.
_EAGER_ITER_METHODS: frozenset[str] = frozenset(
    {
        "map",
        "forEach",
        "filter",
        "reduce",
        "reduceRight",
        "flatMap",
        "find",
        "findIndex",
        "some",
        "every",
        "sort",
    }
)


def _strip_ts_comments(src: str) -> str:
    """Delegate to the shared lexer — see
    ``tests/test_ts_comment_stripper_ssot.py``. The private regex this replaces
    mistook ``//`` inside string literals for comments, so prose and code were
    judged by a different input here than in sibling seals."""
    return strip_ts_comments(src)


def _src_files() -> list[Path]:
    """Every ``.ts``/``.tsx`` under ``src`` excluding codegen output, the locale
    bundles, and vitest test files."""
    out: list[Path] = []
    for p in sorted(SRC_DIR.rglob("*.ts")) + sorted(SRC_DIR.rglob("*.tsx")):
        rel = p.relative_to(SRC_DIR).as_posix()
        if rel.startswith("api/generated/"):
            continue
        if rel.startswith("locales/"):
            continue
        if p.name.endswith((".test.ts", ".test.tsx")):
            continue
        out.append(p)
    return out


def _flatten(tree: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, path))
        else:
            out[path] = value
    return out


def _load_locale(locale: str) -> dict:
    return json.loads((LOCALES_DIR / f"{locale}.json").read_text(encoding="utf-8"))


class TestTranslationKeyReferences(unittest.TestCase):
    """Static translation consumers must resolve against the locale SSOT."""

    def test_detector_covers_literal_quote_styles_and_rejects_interpolation(self) -> None:
        source = "t('a.single'); t(\"a.double\"); t(`a.template`); t(`a.${dynamic}`)"
        self.assertEqual(
            [match.group(2) for match in _LITERAL_T_KEY.finditer(source)],
            ["a.single", "a.double", "a.template"],
        )

    def test_every_literal_translation_key_exists(self) -> None:
        known_keys = set(_flatten(_load_locale(DEFAULT_LOCALE)))
        missing: list[str] = []
        for path in _src_files():
            source = _strip_ts_comments(path.read_text(encoding="utf-8"))
            relative = path.relative_to(SRC_DIR).as_posix()
            for match in _LITERAL_T_KEY.finditer(source):
                key = match.group(2)
                if key not in known_keys:
                    line = source.count("\n", 0, match.start()) + 1
                    missing.append(f"{relative}:{line}: {key}")
        self.assertEqual(
            missing,
            [],
            "literal t() key(s) missing from locale SSOT:\n" + "\n".join(missing),
        )


class TestI18nSsotExists(unittest.TestCase):
    """The i18n SSOT module + locale bundles exist and expose the SSOT."""

    def test_module_present(self) -> None:
        self.assertTrue(I18N_MODULE.is_file(), f"missing i18n SSOT module: {I18N_MODULE}")

    def test_exports_ssot_surfaces(self) -> None:
        src = I18N_MODULE.read_text(encoding="utf-8")
        for export in ("SUPPORTED_LOCALES", "DEFAULT_LOCALE"):
            self.assertRegex(src, rf"export const {export}\b", f"i18n must export `{export}`")
        for fn in ("export function t", "export function useT"):
            self.assertIn(fn, src, f"i18n must export `{fn.split()[-1]}`")

    def test_supported_locales_token_set(self) -> None:
        src = I18N_MODULE.read_text(encoding="utf-8")
        m = re.search(r"SUPPORTED_LOCALES\s*=\s*\[([^\]]*)\]", src)
        self.assertIsNotNone(m, "could not locate SUPPORTED_LOCALES literal")
        tokens = tuple(t.strip().strip("'\"") for t in m.group(1).split(",") if t.strip())
        self.assertEqual(
            tokens,
            SUPPORTED_LOCALES,
            f"SUPPORTED_LOCALES must be {SUPPORTED_LOCALES} (got {tokens})",
        )

    def test_default_locale_is_en(self) -> None:
        src = I18N_MODULE.read_text(encoding="utf-8")
        self.assertRegex(
            src,
            rf"DEFAULT_LOCALE\s*:\s*Locale\s*=\s*'{DEFAULT_LOCALE}'",
            f"DEFAULT_LOCALE must be '{DEFAULT_LOCALE}' (English-first default)",
        )

    def test_locale_bundles_parse(self) -> None:
        for locale in SUPPORTED_LOCALES:
            path = LOCALES_DIR / f"{locale}.json"
            self.assertTrue(path.is_file(), f"missing locale bundle: {path}")
            self.assertIsInstance(_load_locale(locale), dict)


class TestLocaleKeyParity(unittest.TestCase):
    """ko key-set == en key-set, and placeholder sets match per key."""

    def test_key_sets_equal(self) -> None:
        ko_keys = set(_flatten(_load_locale("ko")))
        en_keys = set(_flatten(_load_locale("en")))
        ko_only = sorted(ko_keys - en_keys)
        en_only = sorted(en_keys - ko_keys)
        self.assertEqual(ko_only, [], f"keys present in ko.json but missing in en.json: {ko_only}")
        self.assertEqual(en_only, [], f"keys present in en.json but missing in ko.json: {en_only}")

    def test_placeholder_sets_match(self) -> None:
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        mismatches: list[str] = []
        for key, ko_value in flat_ko.items():
            en_value = flat_en.get(key, "")
            ko_ph = sorted(_PLACEHOLDER.findall(ko_value))
            en_ph = sorted(_PLACEHOLDER.findall(en_value))
            if ko_ph != en_ph:
                mismatches.append(f"{key}: ko={ko_ph} en={en_ph}")
        self.assertEqual(mismatches, [], f"interpolation placeholder drift: {mismatches}")


class TestIdenticalLocaleLeafDispositions(unittest.TestCase):
    """Every ko/en-equal leaf has an explicit, minimal disposition."""

    def test_exact_equal_leaf_set_is_classified(self) -> None:
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        identical = {key for key in flat_ko if flat_ko[key] == flat_en.get(key)}

        self.assertEqual(
            set(IDENTICAL_LOCALE_LEAF_DISPOSITIONS),
            identical,
            "every and only every ko/en-equal leaf must have an explicit disposition",
        )
        self.assertEqual(
            {
                key
                for key, reason in IDENTICAL_LOCALE_LEAF_DISPOSITIONS.items()
                if reason.startswith("BLOCKER:")
            },
            IDENTICAL_LOCALE_BLOCKER_KEYS,
        )

    def test_allowed_dispositions_are_exact_reasoned_entries(self) -> None:
        """Each accepted equality is an exact key carrying its own live reason.

        ⚠️ This assertion used to end with ``len(allowed) == 37``. A literal count
        is exactly the count-only shape M-7 forbids: it says nothing about any
        leaf, it freezes today's arrangement, and the only thing it does when the
        table legitimately changes is send the next session to edit a magic
        number. What it was *trying* to buy — "the allowlist cannot grow
        silently" — is bought properly below by requiring every accepted key to
        still be a live, byte-equal leaf in both bundles: an entry that stops
        being an equality is a stale entry and turns this red, and a new equality
        cannot be accepted without being named.
        """
        allowed = {
            key
            for key, reason in IDENTICAL_LOCALE_LEAF_DISPOSITIONS.items()
            if not reason.startswith("BLOCKER:")
        }
        self.assertTrue(allowed, "the accepted-equality set must not be empty")
        self.assertTrue(all("*" not in key and "." in key for key in allowed))
        self.assertTrue(
            all(IDENTICAL_LOCALE_LEAF_DISPOSITIONS[key].strip() for key in allowed)
        )

        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        stale = sorted(
            key for key in allowed if flat_ko.get(key) != flat_en.get(key, object())
        )
        self.assertEqual(
            stale,
            [],
            "accepted equalities that are no longer ko/en-equal must be removed, "
            f"not left behind: {stale}",
        )
        # A substring assertion on one blocker's wording used to live here. It
        # asked about spelling, which is true even of a reason that names nobody
        # — and it is subsumed by
        # `test_every_blocker_names_a_live_owning_work_claim`, which resolves the
        # named slug against the claim registry instead of matching text.

    # ⚠️ `test_every_blocker_names_a_live_owning_work_claim` 은 2026-08-31 에
    #    모노레포로 돌아갔다(`tests/test_frontend_sprint_governance_records.py`).
    #    그 메서드에서 살아 있던 유일한 내용은 `.claude/work-claims` 레지스트리
    #    건강 검사였고 — 아래 주석이 적듯 `IDENTICAL_LOCALE_BLOCKER_KEYS` 가
    #    비어 실데이터 단언은 이미 공허했다 — 그 레지스트리는 이 레포에 없다.
    #    행동 자체는 합성 픽스처를 쓰는 아래 형제가 그대로 붙들고 있다.


    def test_the_owner_rule_rejects_an_unowned_and_a_closed_blocker(self) -> None:
        """The rule is asserted on synthetic claims, not on today's registry.

        ⚠️ ``IDENTICAL_LOCALE_BLOCKER_KEYS`` is **empty** as of 2026-08-26 — every
        identical leaf is either accepted with a named reason or resolved in the
        bundle. That is the goal state, not a hole, but it means the real-data
        assertion above cannot fail no matter how the rule is broken. So the
        behaviour is pinned here instead: a reason that names nobody, and a
        reason that names a claim which has stopped owning its scope, must both
        be reported. If this class ever regains a blocker, the assertion above
        starts carrying weight again without any edit.
        """
        dispositions = {
            "a.unowned": "BLOCKER: the locale owner keeps this as-is",
            "b.closed": "BLOCKER: owner web-domain-i18n-qa will retire the leaf",
            "c.live": "BLOCKER: owner web-domain-i18n-qa will retire the leaf",
        }
        self.assertEqual(
            _blocker_owner_problems({"a.unowned"}, dispositions, {"web-domain-i18n-qa": "active"}),
            ["blocker a.unowned names no work-claim slug; reason was: "
             f"{dispositions['a.unowned']!r}"],
        )
        closed = _blocker_owner_problems(
            {"b.closed"}, dispositions, {"web-domain-i18n-qa": "merged"}
        )
        self.assertEqual(len(closed), 1)
        self.assertIn("no longer owns its scope", closed[0])
        self.assertEqual(
            _blocker_owner_problems({"c.live"}, dispositions, {"web-domain-i18n-qa": "active"}),
            [],
        )
        self.assertEqual(_blocker_owner_problems(frozenset(), dispositions, {}), [])

    def test_a_nested_slug_is_not_resolved_by_its_shorter_prefix(self) -> None:
        """Test the effect on synthetic slugs, not today's registry.

        The nesting hazard is latent right now — no blocker names a slug that
        another claim's slug is a prefix of — so today's data cannot tell a
        whole-token matcher from a substring one. Feed the matcher slugs that do
        nest, so the assertion means something before the hazard becomes real.
        """
        reason = "BLOCKER: owner reference-web-authoring-closure must retire the leaf"
        self.assertTrue(_reason_names_slug(reason, "reference-web-authoring-closure"))
        self.assertFalse(_reason_names_slug(reason, "reference-web-authoring"))
        self.assertFalse(_reason_names_slug(reason, "web-authoring-closure"))
        # A real nesting pair from the registry, both directions.
        self.assertTrue(
            _reason_names_slug(
                "BLOCKER: owner session-workbook-upload holds it", "session-workbook-upload"
            )
        )
        self.assertFalse(
            _reason_names_slug(
                "BLOCKER: owner session-workbook-upload holds it",
                "session-workbook-upload-ui",
            )
        )
        # Ordinary punctuation must still delimit a slug.
        self.assertTrue(
            _reason_names_slug("BLOCKER: owner is `web-domain-i18n-qa`.", "web-domain-i18n-qa")
        )


class TestALeafMayNotStayUntranslatedWhenTheBundleTranslatesItElsewhere(unittest.TestCase):
    """The bundle is its own oracle for "is this token language-neutral?".

    ``IDENTICAL_LOCALE_LEAF_DISPOSITIONS`` records why a human accepted each
    ko/en equality, and prose cannot be falsified. This class supplies the
    predicate the gate was missing, derived entirely from the bundle and needing
    no vocabulary list, regex, or count:

        an identical ko/en leaf is not language-neutral when some *other* key
        carries the same English value and does translate it into Korean.

    When that happens the bundle has already proved the term is translatable, so
    the equality is an untranslated leaf rather than a neutral token. Measured
    across the whole bundle this has exactly one offender today, and it is the
    one an independent reviewer found by hand — which is the point: the census
    should not depend on someone reading 1,600 leaves.
    """

    # Named baseline, one entry per unresolved offender, with its owner. Not a
    # regex and not a count: an entry that gets fixed must be deleted or
    # `test_the_baseline_holds_no_stale_entry` turns red, so this can only shrink.
    #
    # **Empty as of 2026-08-26.** The single offender this law found —
    # `routes.projectResults.provider`, contradicted by
    # `routes.referenceData.fields.provider` (ko `제공자`) — was resolved in the
    # bundle together with its whole ko `provider*` family, so the shrink
    # completed rather than stalling at a permanent exemption.
    UNRESOLVED: dict[str, str] = {}

    @staticmethod
    def _offenders(flat_ko: dict[str, str], flat_en: dict[str, str]) -> dict[str, list[str]]:
        by_english: dict[str, list[str]] = {}
        for key, value in flat_en.items():
            by_english.setdefault(value, []).append(key)
        offenders: dict[str, list[str]] = {}
        for key, english in flat_en.items():
            if flat_ko.get(key) != english:
                continue
            contradicting = sorted(
                other
                for other in by_english[english]
                if other != key and flat_ko.get(other) != english
            )
            if contradicting:
                offenders[key] = contradicting
        return offenders

    def test_no_unbaselined_leaf_is_contradicted_by_its_own_bundle(self) -> None:
        offenders = self._offenders(_flatten(_load_locale("ko")), _flatten(_load_locale("en")))
        new = {key: proof for key, proof in offenders.items() if key not in self.UNRESOLVED}
        self.assertEqual(
            new,
            {},
            "ko/en-equal leaf whose English value the bundle translates elsewhere — "
            "translate it or record it with its owner: "
            + "; ".join(f"{k} contradicted by {v}" for k, v in sorted(new.items())),
        )

    def test_the_baseline_holds_no_stale_entry(self) -> None:
        offenders = self._offenders(_flatten(_load_locale("ko")), _flatten(_load_locale("en")))
        stale = sorted(set(self.UNRESOLVED) - set(offenders))
        self.assertEqual(
            stale,
            [],
            f"baselined leaves that are no longer offenders must be deleted: {stale}",
        )

    def test_the_census_is_not_vacuous(self) -> None:
        """The detector must actually reach real leaves with something to judge.

        ⚠️ This test used to anchor on the real offender
        (`routes.projectResults.provider`). That anchor **disappeared when the
        offender was fixed** — which is the correct outcome for the bundle and
        the wrong outcome for a seal, because "no offenders" and "the detector
        never ran" print the same result. The anchor therefore moves to the two
        properties that survive resolution: the bundle is large, and it still
        contains ko/en equalities for the law to have an opinion about. The proof
        that the detector *fires* lives in
        `test_a_synthetic_offender_is_detected_and_a_neutral_token_is_not`,
        which does not depend on today's data at all.
        """
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        self.assertGreater(len(flat_en), 1000, "the bundle census is unexpectedly small")
        equal_leaves = {key for key in flat_en if flat_ko.get(key) == flat_en[key]}
        self.assertGreater(
            len(equal_leaves),
            20,
            "no ko/en equalities left to judge — this law would be vacuous",
        )
        self.assertEqual(
            self._offenders(flat_ko, flat_en),
            {},
            "the real bundle must have no leaf its own translations contradict",
        )

    def test_a_synthetic_offender_is_detected_and_a_neutral_token_is_not(self) -> None:
        """Test the effect, not the spelling.

        The detector is fed a tree that does not exist in the repository so the
        assertion cannot pass by accident on today's data: one genuinely
        untranslated leaf contradicted by a sibling, and one acronym that every
        key spells the same way.
        """
        flat_en = {
            "a.provider": "Provider",
            "b.provider": "Provider",
            "c.acronym": "SMSN",
            "d.acronym": "SMSN",
        }
        flat_ko = {
            "a.provider": "Provider",
            "b.provider": "제공자",
            "c.acronym": "SMSN",
            "d.acronym": "SMSN",
        }
        offenders = self._offenders(flat_ko, flat_en)
        self.assertEqual(offenders, {"a.provider": ["b.provider"]})


class TestAKoreanLeafRenderingNoHangulIsDeclaredNotDiscovered(unittest.TestCase):
    """The half of the translatability problem the sibling law cannot reach.

    ``TestALeafMayNotStayUntranslatedWhenTheBundleTranslatesItElsewhere`` asks
    whether the *bundle* contradicts a ko/en equality. That question is only
    askable when the two sides are byte-equal, which means the law sees repeated
    English and never untranslated Korean. Measured by an independent reviewer:
    24 of 44 identical leaves had a unique English value and were invisible to
    it, and four ko leaves rendering no Hangul at all sat outside both gates.

    This class owns that outside. Its predicate is about **script**, so it needs
    no vocabulary list either:

        a ko leaf that renders latin letters and no Hangul, while differing from
        its English counterpart, is invisible to every other gate and must
        therefore be **declared** with a per-key reason.

    Three of today's four are genuinely language-neutral (a proper noun and two
    device-identifier acronyms differing only in casing or interpunct spacing)
    and are declared. The fourth — ``routes.sampleInventory.history.revision``,
    ko ``revision {revision}`` — was the defect this axis was built to expose,
    and it is repaired in the bundle rather than declared. That is the discipline
    the table encodes: declaring is for tokens that have no Korean, not for
    Korean somebody did not write.
    """

    @staticmethod
    def _offenders(flat_ko: dict[str, str], flat_en: dict[str, str]) -> dict[str, str]:
        return {
            key: flat_ko[key]
            for key in no_hangul_latin_candidates(flat_ko)
            if flat_en.get(key) is not None and flat_en[key] != flat_ko[key]
        }

    def test_no_undeclared_no_hangul_ko_leaf_differs_from_english(self) -> None:
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        undeclared = {
            key: value
            for key, value in self._offenders(flat_ko, flat_en).items()
            if key not in NO_HANGUL_KO_LEAF_DISPOSITIONS
        }
        self.assertEqual(
            undeclared,
            {},
            "ko leaf renders no Hangul and is not byte-equal to en, so no other gate can "
            "see it — translate it, or name it in NO_HANGUL_KO_LEAF_DISPOSITIONS with its "
            "reason: "
            + "; ".join(f"{k} = {v!r} (en {flat_en.get(k)!r})" for k, v in sorted(undeclared.items())),
        )

    def test_the_table_holds_no_stale_declaration(self) -> None:
        """A repaired leaf must leave the table, so the table can only shrink."""
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        stale = sorted(set(NO_HANGUL_KO_LEAF_DISPOSITIONS) - set(self._offenders(flat_ko, flat_en)))
        self.assertEqual(
            stale,
            [],
            "declared leaves that are no longer candidates must be deleted — a table that "
            f"keeps its fossils stops being a census: {stale}",
        )

    def test_the_two_locale_laws_are_disjoint(self) -> None:
        """No leaf may sit in both tables, and neither may borrow the other's half.

        Without this, the cheapest way to escape a red gate is to move a key from
        one table to the other: each law then assumes the *other* one is holding
        it. The assertion is structural, not a spelling check — it asks what the
        bundle actually says about each declared key.
        """
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))

        overlap = sorted(set(NO_HANGUL_KO_LEAF_DISPOSITIONS) & set(IDENTICAL_LOCALE_LEAF_DISPOSITIONS))
        self.assertEqual(overlap, [], f"a leaf declared by both locale laws: {overlap}")

        wrong_half = sorted(
            key
            for key in NO_HANGUL_KO_LEAF_DISPOSITIONS
            if flat_en.get(key) == flat_ko.get(key)
        )
        self.assertEqual(
            wrong_half,
            [],
            "these are byte-equal ko/en leaves and belong to "
            f"IDENTICAL_LOCALE_LEAF_DISPOSITIONS, not to this table: {wrong_half}",
        )

        borrowed = sorted(
            key
            for key in IDENTICAL_LOCALE_LEAF_DISPOSITIONS
            if flat_ko.get(key) is not None and flat_en.get(key) != flat_ko.get(key)
        )
        self.assertEqual(
            borrowed,
            [],
            "these leaves are no longer ko/en-equal, so the identical-leaf table no longer "
            f"describes them: {borrowed}",
        )

    def test_every_declaration_is_named_individually(self) -> None:
        """Same discipline as the sibling table: no wildcard, prefix, or count."""
        for key, reason in NO_HANGUL_KO_LEAF_DISPOSITIONS.items():
            self.assertNotIn("*", key, f"wildcard key is not a declaration: {key}")
            self.assertGreater(
                len(reason.strip()),
                20,
                f"{key} carries no reason a reader could disagree with: {reason!r}",
            )

    def test_the_census_is_not_vacuous(self) -> None:
        """Anchored on what survives the repair, not on the repaired leaf.

        The obvious anchor — "``routes.sampleInventory.history.revision`` is a
        candidate" — **disappears the moment it is fixed**, which is precisely
        when a seal most needs to still be running. So the anchors are the bundle
        size and the size of the candidate set *before* the ``ko != en`` filter:
        the ~36 leaves that the identical-leaf table owns keep that set populated
        no matter how many offenders get translated.
        """
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        self.assertGreater(len(flat_en), 1000, "the bundle census is unexpectedly small")
        candidates = no_hangul_latin_candidates(flat_ko)
        self.assertGreater(
            len(candidates),
            20,
            "no latin-rendering ko leaves left to judge — this axis would be vacuous",
        )
        self.assertEqual(
            set(self._offenders(flat_ko, flat_en)),
            set(NO_HANGUL_KO_LEAF_DISPOSITIONS),
            "the declared table must equal the measured offender set in both directions",
        )

    def test_a_synthetic_offender_is_detected_and_neutral_shapes_are_not(self) -> None:
        """Test the effect on a tree that does not exist in the repository.

        Four shapes, each isolating one branch of the predicate, so the assertion
        cannot pass by accident on today's data.
        """
        flat_ko = {
            "a.untranslated": "revision {revision}",  # latin survives the strip → offender
            "b.slot_only": "{provider}",  # nothing but a value slot → not an offender
            "c.korean": "리비전 {revision}",  # has Hangul → not this axis
            "d.equal": "SMSN",  # ko == en → the identical-leaf table's half
            "e.digits": "1440 × 900",  # no latin letters at all → not an offender
        }
        flat_en = {
            "a.untranslated": "Revision {revision}",
            "b.slot_only": "{provider}",
            "c.korean": "Revision {revision}",
            "d.equal": "SMSN",
            "e.digits": "1440 x 900",
        }
        self.assertEqual(
            no_hangul_latin_candidates(flat_ko),
            {"a.untranslated", "d.equal"},
        )
        self.assertEqual(
            self._offenders(flat_ko, flat_en),
            {"a.untranslated": "revision {revision}"},
        )


class TestALatinRunInsideKoreanIsDeclaredNotDiscovered(unittest.TestCase):
    """The blind spot the three preceding laws share.

    Each of them needs a precondition a mixed-script leaf does not meet:

    ===================================== ==============================
    law                                   precondition
    ===================================== ==============================
    ``IDENTICAL_LOCALE_LEAF_DISPOSITIONS`` ``ko == en``
    the translatability law                byte equality of the English value
    ``NO_HANGUL_KO_LEAF_DISPOSITIONS``     the ko side has **no** Hangul
    ===================================== ==============================

    ``템플릿 export`` is none of those. A third independent reviewer measured the
    gap and this class is it: **an English word left inside a Korean sentence**,
    judged by the same oracle round 2 used, moved from the whole value down to the
    maximal latin run inside it.

    Measured with the tokenizer this file ships: **114** mixed-script ko leaves,
    **62** distinct latin runs, **20** contradicted by the bundle. Six run-classes
    were genuine defects and were repaired in ``ko.json``; the remaining
    **14** are declared above with named reasons and **35** named sites.

    ⚠️ **The first published figures were 64 and 21, and they were not
    reproducible.** They were taken with the draft extractor that the same commit
    removed for "putting a token nobody wrote into the census" — an independent
    reviewer re-derived 63 under the shipped one. The numbers here are re-measured
    against ``d8438320^`` with the tokenizer actually in the tree, which is what
    "measured before authorising" has to mean if it means anything.

    One of the remaining 14 is a **false positive of this very law** — ``A`` is
    "contradicted" by the English article in ``Select a project…`` — and it is
    declared with that written down rather than filtered out of the predicate. Its
    former sibling ``S`` was declared on the same footing and should not have
    been: see ``_LATIN_RUN_RE``.
    """

    def test_no_undeclared_latin_run_is_contradicted_by_its_own_bundle(self) -> None:
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        offenders = latin_run_offenders(flat_ko, flat_en)
        undeclared = {
            run: proof for run, proof in offenders.items() if run not in KO_LATIN_RUN_DISPOSITIONS
        }
        census = mixed_script_latin_candidates(flat_ko)
        self.assertEqual(
            undeclared,
            {},
            "English left inside a Korean value while the bundle translates the same run "
            "elsewhere — translate it, or name the run in KO_LATIN_RUN_DISPOSITIONS with its "
            "reason: "
            + "; ".join(
                f"{run!r} in {sorted(census[run])} contradicted by {proof[:2]}"
                for run, proof in sorted(undeclared.items())
            ),
        )

    def test_the_table_holds_no_stale_declaration(self) -> None:
        """A repaired run must leave the table, so the table can only shrink."""
        offenders = latin_run_offenders(_flatten(_load_locale("ko")), _flatten(_load_locale("en")))
        stale = sorted(set(KO_LATIN_RUN_DISPOSITIONS) - set(offenders))
        self.assertEqual(
            stale,
            [],
            "declared runs that are no longer contradicted must be deleted — a table that "
            f"keeps its fossils stops being a census: {stale}",
        )

    def test_the_three_locale_laws_are_disjoint(self) -> None:
        """The three axes partition their candidates, and no key is held by two.

        ⚠️ **This docstring used to claim more than the body tested**, and an
        independent reviewer was right to say so. It said *"no leaf may be moved
        between the three axes to escape all of them"* — but a leaf that moves is
        then judged by the **receiving** axis's criterion, which is a different
        question, and its old declaration correctly stops applying. The reviewer's
        demonstration (take a declared no-Hangul leaf, add one Hangul character,
        delete its declaration) does not escape *judgement*: the moved leaf is
        judged here, and here it is an offender only if the bundle contradicts one
        of its runs. What it escapes is *declaration*, and no tree-local assertion
        can require a deletion to be accounted for across commits.

        What is guaranteed, and is asserted below: the candidate sets are
        disjoint, this axis never borrows the identical-leaf table's half, and no
        key is declared by two tables at once. The residual — a declaration
        deleted during a move — is in the ledger with its shape named.
        """
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))

        mine = {key for keys in mixed_script_latin_candidates(flat_ko).values() for key in keys}
        theirs = no_hangul_latin_candidates(flat_ko)
        self.assertEqual(
            sorted(mine & theirs),
            [],
            "a leaf cannot be a candidate of both latin axes — one requires Hangul to be "
            f"present and the other requires it to be absent: {sorted(mine & theirs)}",
        )

        # A leaf this axis governs may not also be declared by a sibling table:
        # two tables holding one key is how each ends up assuming the other is
        # carrying it.
        governed = {key for d in KO_LATIN_RUN_DISPOSITIONS.values() for key in d.keys}
        double = sorted(
            governed & (set(NO_HANGUL_KO_LEAF_DISPOSITIONS) | set(IDENTICAL_LOCALE_LEAF_DISPOSITIONS))
        )
        self.assertEqual(double, [], f"a leaf declared by two locale tables at once: {double}")

        equal_here = sorted(key for key in mine if flat_en.get(key) == flat_ko.get(key))
        self.assertEqual(
            equal_here,
            [],
            "these mixed-script leaves are byte-equal to en, so the identical-leaf law owns "
            f"them and this axis must not: {equal_here}",
        )

    def test_every_declaration_is_named_individually(self) -> None:
        """Same discipline as both sibling tables: no wildcard, prefix, or count."""
        for run, disposition in KO_LATIN_RUN_DISPOSITIONS.items():
            self.assertNotIn("*", run, f"wildcard run is not a declaration: {run}")
            self.assertGreater(
                len(disposition.reason.strip()),
                20,
                f"{run} carries no reason a reader could disagree with: {disposition.reason!r}",
            )
            self.assertTrue(disposition.keys, f"{run} declares no site, so it exempts everywhere")
            for key in disposition.keys:
                self.assertNotIn("*", key, f"wildcard site is not a declaration: {run} -> {key}")

    def test_each_declared_run_names_the_exact_leaves_it_occurs_in(self) -> None:
        """The half a run-keyed table cannot supply on its own.

        ⚠️ **Measured by an independent reviewer, not imagined.** With reasons
        keyed only by run, every declared run is a permanently open channel: they
        injected six genuinely untranslated leaves, each reusing an
        already-declared run, and the seal stayed green **6 of 6**. The contract
        and the manifest both said "a new offender cannot be absorbed silently",
        and as written that was false — only a new *run* could not be.

        The vocabulary decision stays per-run, because "do we leave this English
        word in Korean?" is asked once per term. *Where* it may appear is a
        different question, and this is it: the declared site set must equal the
        measured one in both directions, so a new leaf reusing a declared run is
        red until someone declares that leaf too.
        """
        flat_ko = _flatten(_load_locale("ko"))
        census = mixed_script_latin_candidates(flat_ko)
        mismatched = {
            run: {
                "declared": sorted(disposition.keys),
                "measured": sorted(census.get(run, set())),
            }
            for run, disposition in KO_LATIN_RUN_DISPOSITIONS.items()
            if set(disposition.keys) != census.get(run, set())
        }
        self.assertEqual(
            mismatched,
            {},
            "a declared run occurs in leaves the table does not name (or names leaves it no "
            "longer occurs in) — declare the new site with the run's reason, or delete the "
            f"stale one: {mismatched}",
        )

    def test_a_new_leaf_reusing_a_declared_run_is_not_absorbed_silently(self) -> None:
        """The reviewer's exact attack, pinned so it cannot come back.

        A synthetic leaf reusing the most-declared run must be flagged by the site
        assertion even though the *run* is declared. Run on a copy of the real
        census so the assertion is about the predicate, not about today's data.
        """
        flat_ko = dict(_flatten(_load_locale("ko")))
        flat_ko["zz.injected"] = "이 project 를 먼저 고르세요"
        census = mixed_script_latin_candidates(flat_ko)
        declared = set(KO_LATIN_RUN_DISPOSITIONS["project"].keys)
        self.assertIn("zz.injected", census["project"])
        self.assertNotEqual(
            declared,
            census["project"],
            "injecting a new leaf that reuses a declared run must make the declared site set "
            "differ from the measured one — otherwise the run is an open channel",
        )

    def test_the_census_is_not_vacuous(self) -> None:
        """Anchored on what survives the repair, not on the repaired runs.

        Anchoring on ``'export' is contradicted`` would delete the anchor the
        moment that repair lands — precisely when a seal most needs to still be
        running. The anchors are therefore the bundle size and the size of the
        mixed-script candidate set *before* any oracle runs: glossed terms, area
        proper nouns and URL parameters keep it populated however many words get
        translated. The proof that the detector *fires* lives in the synthetic
        test below, which does not touch today's data at all.
        """
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        self.assertGreater(len(flat_en), 1000, "the bundle census is unexpectedly small")
        census = mixed_script_latin_candidates(flat_ko)
        self.assertGreater(
            len(census),
            20,
            "no latin runs left inside Korean values — this axis would be vacuous",
        )
        self.assertEqual(
            set(latin_run_offenders(flat_ko, flat_en)),
            set(KO_LATIN_RUN_DISPOSITIONS),
            "the declared table must equal the measured offender set in both directions",
        )

    def test_a_synthetic_offender_is_detected_and_neutral_shapes_are_not(self) -> None:
        """Test the effect on a tree that does not exist in the repository.

        Six shapes, each isolating one branch, so the assertion cannot pass by
        accident on today's data.
        """
        flat_ko = {
            "a.untranslated": "템플릿 export",  # bundle renders this word in Korean → offender
            "b.proof": "CSV 내보내기",  # the proof leaf itself is never its own offender
            "c.unique": "Sample Data 내보내기",  # no leaf renders this run → not an offender
            "d.no_hangul": "S/N · IMEI",  # no Hangul → the sibling axis owns it
            "e.slot_only": "{provider} 선택",  # only a value slot → nothing latin survives
            "f.phrase": "조회 표(예: Frequency Table)",  # one run, not the word "table"
        }
        flat_en = {
            "a.untranslated": "Template export",
            "b.proof": "Export CSV",
            "c.unique": "Download Sample Data",
            "d.no_hangul": "S/N · IMEI",
            "e.slot_only": "Select {provider}",
            "f.phrase": "the lookup table (for example the frequency table)",
        }
        census = mixed_script_latin_candidates(flat_ko)
        self.assertNotIn("d.no_hangul", {k for keys in census.values() for k in keys})
        self.assertNotIn("e.slot_only", {k for keys in census.values() for k in keys})
        self.assertIn("Frequency Table", census)
        self.assertNotIn("table", census)

        offenders = latin_run_offenders(flat_ko, flat_en)
        self.assertEqual(sorted(offenders), ["export"])
        self.assertEqual(offenders["export"], ["b.proof"])

    def test_the_two_measured_tokenizer_defects_stay_closed(self) -> None:
        """Both false runs the first draft produced, pinned as regressions.

        Neither is hypothetical: both appeared in the real bundle and both would
        have demanded a declaration for a word nobody wrote. Reverting either
        normalisation turns this red.
        """
        # A placeholder must not leave its suffix standing alone: `{value}m` is one
        # token, so the English minute suffix cannot "contradict" a bare `m`.
        self.assertEqual(latin_runs("{value}분"), [])
        self.assertEqual(latin_runs("{value}m"), [])
        self.assertEqual(latin_runs("1M 속도"), [])
        # ⚠️ **Both apostrophes, because this corpus uses the one the first draft
        # did not list.** `en.json` writes `the report’s §6 table` with U+2019, so
        # asserting only the ASCII spelling left the real defect open — two of the
        # twelve proofs the old `S` entry stood on were exactly that artifact.
        for apostrophe in ("'", "’"):
            self.assertFalse(
                _run_occurs("S", f"Focus this screen{apostrophe}s search/filter"),
                f"a bare S must not be found inside screen{apostrophe}s",
            )
            self.assertEqual(latin_runs(f"screen{apostrophe}s 확인"), ["screen"])

        # ⚠️ **A parenthesised suffix stays inside its run.** This replaced an
        # exemption that was argued to be *structurally* forced — that silencing
        # the bogus `S` proof would also blind the axis to the real `HTTP(S)`.
        # It does the opposite: `HTTP(S) Session Node` is now one run the axis can
        # reason about, and the bare `S` never enters the census, so nothing
        # contradicts it and nothing has to be declared.
        self.assertEqual(
            latin_runs("챔버 ID, 이름, HTTP(S) Session Node 주소"),
            ["ID", "HTTP(S) Session Node"],
        )
        self.assertNotIn("S", mixed_script_latin_candidates(_flatten(_load_locale("ko"))))
        self.assertNotIn("S", KO_LATIN_RUN_DISPOSITIONS)
        # …while a genuine standalone latin run in Korean copy is still extracted.
        self.assertEqual(latin_runs("판(edition)"), ["edition"])
        # ⚠️ A closing bracket nothing opened is punctuation, not part of the run.
        # An earlier draft of the paren rule produced `Frequency Table)` here.
        self.assertEqual(latin_runs("조회 표(예: Frequency Table)"), ["Frequency Table"])


class TestTheOraclesSilenceIsDeclaredNotAssumed(unittest.TestCase):
    """The 3% the offender law could not see, and never said so.

    ``latin_run_offenders`` judges a run by asking the rest of the bundle. When
    the English term is **unique** in the bundle there is no other leaf to ask,
    so the run produced no proof and passed — "no evidence" rendered as "no
    defect". An independent reviewer measured the reach at 946 of 977 distinct
    English words and named four ordinary ones in the shadow: ``bucket``,
    ``minutes``, ``capability``, ``index``.

    ⚠️ **The ledger's own prescription was a declared domain glossary, and this
    is deliberately not that.** A glossary needs a Korean rendering for every
    English word in the bundle — including the 946 the bundle already judges —
    and it introduces a second authority that can disagree with the bundle. The
    hole is only where latin actually lands inside Korean copy. Enumerating that
    is a strictly smaller obligation and closes the same thing.

    ⚠️ **What is claimed, exactly.** Not that the axis now sees everything —
    that its silence is *declared*. Every latin run in a Korean value falls in
    exactly one of three partitions, and the third one now costs a written
    reason:

    ==================== ============================================= ============
    partition            membership                                    obligation
    ==================== ============================================= ============
    contradicted         another leaf carries it in en, without it in ko  declare
    corroborated         every other carrier keeps it in ko too           none
    **unjudgeable**      **no other leaf carries it in en at all**        **declare**
    ==================== ============================================= ============

    Measured at this SHA: 57 runs = 14 contradicted + 15 corroborated + 28
    unjudgeable, across 33 leaves.
    """

    def _tables(self) -> tuple[dict[str, str], dict[str, str]]:
        return _flatten(_load_locale("ko")), _flatten(_load_locale("en"))

    def test_the_three_partitions_are_disjoint_and_exhaustive(self) -> None:
        """The property the whole axis rests on.

        A run belonging to none of the three is the silent case this class
        exists to abolish; a run belonging to two means two rules each assume
        the other is carrying it.
        """
        flat_ko, flat_en = self._tables()
        census = set(mixed_script_latin_candidates(flat_ko))
        contradicted = set(latin_run_offenders(flat_ko, flat_en))
        corroborated = set(latin_run_corroborated(flat_ko, flat_en))
        unjudgeable = set(latin_run_unjudgeable(flat_ko, flat_en))

        self.assertEqual(
            contradicted | corroborated | unjudgeable,
            census,
            "a latin run in Korean copy belongs to no partition, so nothing judges it: "
            f"{sorted(census - (contradicted | corroborated | unjudgeable))}",
        )
        for left, right, names in (
            (contradicted, corroborated, "contradicted/corroborated"),
            (contradicted, unjudgeable, "contradicted/unjudgeable"),
            (corroborated, unjudgeable, "corroborated/unjudgeable"),
        ):
            self.assertEqual(
                sorted(left & right), [], f"{names} overlap: {sorted(left & right)}"
            )

    def test_every_unjudgeable_run_is_declared(self) -> None:
        flat_ko, flat_en = self._tables()
        unjudgeable = latin_run_unjudgeable(flat_ko, flat_en)
        undeclared = {
            run: sites
            for run, sites in unjudgeable.items()
            if run not in KO_LATIN_RUN_BLIND_DISPOSITIONS
        }
        self.assertEqual(
            undeclared,
            {},
            "this latin run sits inside Korean copy and the bundle carries its English nowhere "
            "else, so no gate in this repository can judge it. Translate it, or name it in "
            f"KO_LATIN_RUN_BLIND_DISPOSITIONS with the reason it stays latin: {undeclared}",
        )

    def test_the_blind_table_holds_no_stale_declaration(self) -> None:
        """Once the bundle can judge a run, the declaration must go.

        A run leaves this table in two different ways and both must clean it up:
        the Korean is translated (the run leaves the census entirely), or some
        other leaf starts carrying the English (the run moves to one of the two
        derived partitions). Either way keeping the entry turns a census into a
        fossil record.
        """
        flat_ko, flat_en = self._tables()
        unjudgeable = latin_run_unjudgeable(flat_ko, flat_en)
        stale = sorted(set(KO_LATIN_RUN_BLIND_DISPOSITIONS) - set(unjudgeable))
        self.assertEqual(
            stale,
            [],
            "these runs are no longer unjudgeable — the bundle can now speak about them, so the "
            f"blind declaration must be deleted: {stale}",
        )

    def test_each_declared_run_names_the_exact_leaves_it_occurs_in(self) -> None:
        """The half a run-keyed table cannot supply on its own.

        The same reviewer finding that reshaped ``KO_LATIN_RUN_DISPOSITIONS``
        applies here for the same reason: a run-keyed reason with no site list
        is a permanently open channel, and a new leaf reusing a declared term
        would be absorbed in silence.
        """
        flat_ko, flat_en = self._tables()
        unjudgeable = latin_run_unjudgeable(flat_ko, flat_en)
        mismatched = {
            run: {"declared": sorted(d.keys), "measured": unjudgeable.get(run, [])}
            for run, d in KO_LATIN_RUN_BLIND_DISPOSITIONS.items()
            if sorted(d.keys) != unjudgeable.get(run, [])
        }
        self.assertEqual(
            mismatched,
            {},
            "a declared unjudgeable run occurs in leaves the table does not name (or names leaves "
            f"it no longer occurs in): {mismatched}",
        )

    def test_every_declaration_is_named_individually(self) -> None:
        """Same discipline as both sibling tables: no wildcard, prefix, or count."""
        for run, disposition in KO_LATIN_RUN_BLIND_DISPOSITIONS.items():
            self.assertNotIn("*", run, f"wildcard run is not a declaration: {run}")
            self.assertGreater(
                len(disposition.reason.strip()),
                20,
                f"{run} carries no reason a reader could disagree with: {disposition.reason!r}",
            )
            self.assertTrue(disposition.keys, f"{run} declares no site, so it exempts everywhere")
            for key in disposition.keys:
                self.assertNotIn("*", key, f"wildcard site is not a declaration: {run} -> {key}")

    def test_no_run_is_declared_by_both_latin_tables(self) -> None:
        """The two run-keyed tables answer different questions about disjoint sets.

        Disjointness is already implied by the partition assertion above, but
        only while both tables are honest about which partition they describe.
        Asserting it directly means a run copied from one table to the other is
        red rather than merely redundant.
        """
        both = sorted(set(KO_LATIN_RUN_DISPOSITIONS) & set(KO_LATIN_RUN_BLIND_DISPOSITIONS))
        self.assertEqual(both, [], f"a run declared as both contradicted and unjudgeable: {both}")

    def test_the_declared_table_equals_the_measured_shadow(self) -> None:
        """Bidirectional set equality, plus anchors that survive the repair.

        Anchoring on "``capability matrix`` is unjudgeable" would delete the
        anchor the moment somebody translates it. The anchors are therefore the
        bundle size and the pre-oracle census, which acronyms and machine
        identifiers keep populated however much English gets translated.
        """
        flat_ko, flat_en = self._tables()
        self.assertGreater(len(flat_en), 1000, "the bundle census is unexpectedly small")
        self.assertGreater(
            len(mixed_script_latin_candidates(flat_ko)),
            20,
            "no latin runs left inside Korean values — this axis would be vacuous",
        )
        self.assertEqual(
            set(latin_run_unjudgeable(flat_ko, flat_en)),
            set(KO_LATIN_RUN_BLIND_DISPOSITIONS),
            "the blind table must equal the measured unjudgeable set in both directions",
        )

    def test_a_synthetic_shadow_run_is_detected_and_the_partitions_hold(self) -> None:
        """The effect, on a tree that does not exist in the repository.

        Five leaves isolating each branch: a run the bundle contradicts, the leaf
        that proves it, a run nothing else carries, a run some other leaf keeps
        in Korean too, and the leaf that corroborates it. The `index` case is
        deliberately one of the four ordinary English words the reviewer measured
        as unreachable — under the old law it produced no proof and passed
        silently, exactly like a correctly translated leaf.

        ⚠️ **The corroborator has no Hangul on purpose.** A carrier is any leaf
        *other than the ones the run already occurs in*, so a run that lives only
        inside mixed-script leaves has no carrier and is unjudgeable by
        construction. Putting the corroborating leaf inside the census would
        therefore test the opposite of what this branch is for — measured while
        writing this fixture, not assumed.
        """
        flat_ko = {
            "a.contradicted": "템플릿 export",
            "b.proof": "CSV 내보내기",
            "c.shadow": "정렬 index 를 확인하세요",
            "d.corroborated": "OIDC 설정을 확인하세요",
            "e.corroborator": "OIDC / SSO",
        }
        flat_en = {
            "a.contradicted": "Template export",
            "b.proof": "Export CSV",
            "c.shadow": "Check the sort index",
            "d.corroborated": "Check the OIDC settings",
            "e.corroborator": "OIDC / SSO",
        }
        contradicted = latin_run_offenders(flat_ko, flat_en)
        corroborated = latin_run_corroborated(flat_ko, flat_en)
        unjudgeable = latin_run_unjudgeable(flat_ko, flat_en)

        self.assertEqual(sorted(contradicted), ["export"])
        self.assertEqual(sorted(corroborated), ["OIDC"])
        # ⚠️ `CSV` lands in the shadow here and that is correct, not a fixture
        # bug: in a five-leaf bundle nothing else carries it, so the law has no
        # evidence about it either. It is kept in the expectation rather than
        # engineered away, because the branch being tested is exactly "the
        # bundle is too small to speak", and a fixture that hides an instance of
        # the thing under test is not a fixture.
        self.assertEqual(unjudgeable, {"CSV": ["b.proof"], "index": ["c.shadow"]})
        # …and the same exhaustiveness property holds on data nobody curated for it.
        census = set(mixed_script_latin_candidates(flat_ko))
        self.assertEqual(
            set(contradicted) | set(corroborated) | set(unjudgeable), census
        )
        # ⚠️ Non-vacuity of the *shadow* branch specifically: with the old law
        # `index` produced no proof and was therefore indistinguishable from a
        # correctly-translated leaf. Assert it is not an offender, so this test
        # cannot pass by the two rules being accidentally identical.
        self.assertNotIn("index", contradicted)


class TestNoInlineHangulLiteral(unittest.TestCase):
    """No inline Hangul literal in src code — every Korean string via t()."""

    def test_no_inline_hangul(self) -> None:
        offenders: list[str] = []
        for path in _src_files():
            rel = path.relative_to(SRC_DIR).as_posix()
            if rel in INLINE_HANGUL_ALLOWLIST:
                continue
            stripped = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for m in _HANGUL.finditer(stripped):
                line = stripped.count("\n", 0, m.start()) + 1
                offenders.append(f"{rel}:{line}")
        self.assertEqual(
            offenders,
            [],
            "inline Hangul UI literal(s) found in src code — route through "
            f"t() (@/i18n) + src/locales/*.json: {offenders[:20]}",
        )


def _strip_translatable(value: str) -> str:
    """Remove `t(...)` calls and `${...}` interpolations from a sink value so
    only bare (un-routed) literals remain for scanning."""
    value = _T_CALL.sub("", value)
    value = _TEMPLATE_INTERP.sub("", value)
    return value


class TestNoInlineRenderedEnglishLiteral(unittest.TestCase):
    """No inline English UI literal in a rendered sink — every English label via
    t(). Sink-based heuristic (see module docstring rule 4)."""

    def _tsx_files(self) -> list[Path]:
        out: list[Path] = []
        for p in _src_files():
            rel = p.relative_to(SRC_DIR).as_posix()
            if p.suffix != ".tsx":
                continue
            if rel in RENDERED_ENGLISH_EXCLUDED:
                continue
            out.append(p)
        return out

    def test_no_inline_english_rendered_literal(self) -> None:
        offenders: list[str] = []
        for path in self._tsx_files():
            rel = path.relative_to(SRC_DIR).as_posix()
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))

            def record(line_idx: int, snippet: str) -> None:
                if f"{rel}:{snippet}" in RENDERED_ENGLISH_ALLOWLIST:
                    return
                offenders.append(f"{rel}:{line_idx}: {snippet}")

            # Sink (a) — JSX display attributes.
            for m in _SINK_ATTR.finditer(src):
                residual = _strip_translatable(m.group(1))
                if _ENGLISH_IN_LITERAL.search(residual):
                    record(src.count("\n", 0, m.start()) + 1, m.group(0)[:80])

            # Sink (b) — JSX prose text children.
            for m in _JSX_TEXT.finditer(src):
                text = m.group(1).strip()
                if _PROSE.match(text) and sum(c.isalpha() for c in text) >= 2:
                    record(src.count("\n", 0, m.start()) + 1, f">{text}<")

            # Sink (c) — DOM textContent assignment (non-React bootstrap).
            for m in _TEXTCONTENT.finditer(src):
                residual = _strip_translatable(m.group(1))
                if _ENGLISH_IN_LITERAL.search(residual):
                    record(src.count("\n", 0, m.start()) + 1, m.group(0)[:80])

        self.assertEqual(
            offenders,
            [],
            "inline English UI literal(s) found in a rendered sink — route "
            f"through t() (@/i18n) + src/locales/*.json: {offenders[:20]}",
        )


def _scan_template_for_t(src: str, start: int) -> tuple[int, list[int]]:
    """Given ``src[start] == '`'`` (a template literal), return
    ``(index_just_past_closing_backtick, [1-based line numbers of ``t(`` calls
    found inside ``${ … }`` interpolations])``.

    A module-scope template literal is *constructed* at module load, so any
    ``t(`` reached inside one of its interpolations is module-load evaluated
    (the iter-02 hardening — ``const X = `${t('errors.x')}` `` previously slipped
    through because template interiors were skipped wholesale). Interpolation
    expressions are brace-balanced; nested strings / templates inside an
    interpolation are handled so an inner ``}`` in a string does not close it.
    """
    n = len(src)
    i = start + 1
    lines: list[int] = []
    while i < n:
        c = src[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1, lines
        if c == "$" and i + 1 < n and src[i + 1] == "{":
            j = i + 2
            seg_start = j
            depth = 1
            while j < n and depth > 0:
                cj = src[j]
                if cj == "\\":
                    j += 2
                    continue
                if cj in "'\"":
                    quote = cj
                    j += 1
                    while j < n and src[j] != quote:
                        j += 2 if src[j] == "\\" else 1
                    j += 1
                    continue
                if cj == "`":
                    end2, inner = _scan_template_for_t(src, j)
                    lines.extend(inner)
                    j = end2
                    continue
                if cj == "{":
                    depth += 1
                elif cj == "}":
                    depth -= 1
                j += 1
            segment = src[seg_start : j - 1]
            for m in _T_CALL_AT.finditer(segment):
                lines.append(src.count("\n", 0, seg_start + m.start()) + 1)
            i = j
            continue
        i += 1
    return n, lines


def _module_scope_t_calls(src: str) -> list[int]:
    """Return 1-based line numbers of ``t(...)`` calls that are *evaluated at
    module load* — frozen to the import-time locale (the iter-02 bug class).

    A ``t()`` call is module-load evaluated when there is **no deferred function
    body** between the call and module root. A function body is *deferred* when
    the function is bound/stored (declaration, ``const f = () => …``, object
    property, JSX event handler, hook) and only invoked later; it is *eager*
    when it runs at module load — an IIFE (``(() => …)()``) or a callback passed
    to a synchronous array-iteration method (``KEYS.map(() => …)``). A ``t()``
    inside an eager function at module scope is still module-load evaluated, so
    it is flagged; a ``t()`` inside a deferred function (anywhere) passes.

    Hardening over the iter-02 lexer (which treated *any* function/arrow body as
    render-time and only caught literal-first-arg calls), now caught:
      * IIFE                       ``const X = (() => t('k'))();``
      * eager iteration callback   ``const X = KEYS.map(() => t('k'));``
      * module-scope template      ``const X = `${t('k')}`;``
      * variable-key call          ``const k='…'; const X = t(k);``
      * the original object literal ``const X = { a: t('k') };``

    String-aware mini lexer. ``brace_kind`` tags each ``{``: ``'fn_def'``
    (deferred function body) / ``'fn_eager'`` (IIFE or iteration callback body)
    / ``'plain'``. ``arrow_deferred_depths`` tracks brace-less concise *deferred*
    arrows (an eager concise arrow pushes nothing — it is module-load). The next
    function literal is marked eager via ``eager_hint``, set when a grouping
    ``(`` is immediately followed by ``(`` / ``function`` (IIFE wrapper) or when a
    call paren follows an ``.<iterMethod>`` name. ``t()`` is an offence when no
    ``'fn_def'`` brace and no deferred concise arrow are active. Known limits
    (documented, monotonic-decrease ``MODULE_SCOPE_T_ALLOWLIST``): a callback to a
    *non-iteration* deferred call at module scope (``setTimeout(() => t())``) is
    treated as deferred (eager_hint not set), and object method-shorthand at
    module scope is a non-fn brace; neither pattern carries a ``t()`` in the
    current codebase.
    """
    src = _strip_ts_comments(src)
    n = len(src)
    i = 0
    depth = 0  # total bracket nesting () [] {}
    brace_kind: list[str] = []  # 'fn_def' | 'fn_eager' | 'plain' per open '{'
    arrow_deferred_depths: list[int] = []  # depths of active deferred concise arrows
    pending_fn = False  # a function body '{' is expected (saw => / function)
    pending_fn_depth = 0  # the depth at which that body '{' appears (skips params)
    pending_eager = False  # ...and that pending function body is eager
    eager_hint = False  # the next function literal is eager (IIFE / iteration cb)
    offenders: list[int] = []

    def in_deferred() -> bool:
        return ("fn_def" in brace_kind) or bool(arrow_deferred_depths)

    while i < n:
        c = src[i]
        # Skip string literals (interiors are not scanned).
        if c in "'\"":
            quote = c
            i += 1
            while i < n and src[i] != quote:
                i += 2 if src[i] == "\\" else 1
            i += 1
            continue
        # Template literal: skip the literal text but scan ${...} interpolations
        # when at module-load context (a deferred template is render-time → safe).
        if c == "`":
            end, t_lines = _scan_template_for_t(src, i)
            if not in_deferred():
                offenders.extend(t_lines)
            i = end
            continue
        # Arrow token `=>`.
        if c == "=" and i + 1 < n and src[i + 1] == ">":
            j = i + 2
            while j < n and src[j] in " \t\r\n":
                j += 1
            if j < n and src[j] == "{":
                pending_fn = True  # braced body — the '{' at this depth is the body
                pending_fn_depth = depth
                pending_eager = eager_hint
            elif not eager_hint:
                arrow_deferred_depths.append(depth)  # deferred concise body
            # (eager concise body pushes nothing — it is module-load evaluated)
            eager_hint = False
            i += 2
            continue
        # `function` keyword (word-boundary checked). The body '{' appears at the
        # keyword's depth — AFTER the parameter list `(...)`. Skip the optional
        # `*` (generator) and the function NAME so e.g. `function t(` is not read
        # as a `t(` call.
        if (
            src.startswith("function", i)
            and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] in "_$"))
            and (i + 8 >= n or not (src[i + 8].isalnum() or src[i + 8] in "_$"))
        ):
            pending_fn = True
            pending_fn_depth = depth
            pending_eager = eager_hint
            eager_hint = False
            i += 8
            while i < n and src[i] in " \t\r\n*":
                i += 1
            while i < n and (src[i].isalnum() or src[i] in "_$"):
                i += 1
            continue
        # `t(` call (literal OR variable first arg).
        if _T_CALL_AT.match(src, i):
            if not in_deferred():
                offenders.append(src.count("\n", 0, i) + 1)
        # Bracket bookkeeping.
        if c == "(":
            # Classify this paren to set eager_hint for the next function literal.
            k = i - 1
            while k >= 0 and src[k] in " \t\r\n":
                k -= 1
            if k >= 0 and (src[k].isalnum() or src[k] in "_$"):
                # Call paren — is the callee an iteration method (`.map(` …)?
                e = k
                while k >= 0 and (src[k].isalnum() or src[k] in "_$"):
                    k -= 1
                word = src[k + 1 : e + 1]
                before = src[k] if k >= 0 else ""
                if before == "." and word in _EAGER_ITER_METHODS:
                    eager_hint = True
            else:
                # Grouping paren — an IIFE wrapper if it directly wraps a function.
                m = i + 1
                while m < n and src[m] in " \t\r\n":
                    m += 1
                if m < n and (
                    src[m] == "("
                    or (
                        src.startswith("function", m)
                        and (m + 8 >= n or not (src[m + 8].isalnum() or src[m + 8] in "_$"))
                    )
                ):
                    eager_hint = True
            depth += 1
        elif c == "[":
            depth += 1
        elif c in ")]":
            depth -= 1
            while arrow_deferred_depths and arrow_deferred_depths[-1] > depth:
                arrow_deferred_depths.pop()
        elif c == "{":
            if pending_fn and depth == pending_fn_depth:
                brace_kind.append("fn_eager" if pending_eager else "fn_def")
                pending_fn = False
                pending_eager = False
            else:
                brace_kind.append("plain")
                eager_hint = False  # a plain block clears a stale hint
            depth += 1
        elif c == "}":
            if brace_kind:
                brace_kind.pop()
            depth -= 1
            while arrow_deferred_depths and arrow_deferred_depths[-1] > depth:
                arrow_deferred_depths.pop()
        elif c in ",;":
            eager_hint = False  # statement/arg separator clears a stale hint
            while arrow_deferred_depths and arrow_deferred_depths[-1] >= depth:
                arrow_deferred_depths.pop()
        i += 1
    return offenders


class TestNoModuleScopeTranslatedString(unittest.TestCase):
    """No literal-key ``t('...')`` evaluated at module-load time — every
    translated string is resolved at render/call time so a locale switch is
    live (see module docstring rule 6)."""

    def test_no_module_scope_translated_string(self) -> None:
        offenders: list[str] = []
        for path in _src_files():
            rel = path.relative_to(SRC_DIR).as_posix()
            for line in _module_scope_t_calls(path.read_text(encoding="utf-8")):
                if f"{rel}:{line}" in MODULE_SCOPE_T_ALLOWLIST:
                    continue
                offenders.append(f"{rel}:{line}")
        self.assertEqual(
            offenders,
            [],
            "module-scope translated string(s) — a literal-key t('...') is "
            "evaluated at module load, freezing the copy to the import-time "
            "locale. Store the translation KEY at module scope and resolve it "
            f"with the render-time t() (useT()) instead: {offenders[:20]}",
        )


class TestModuleScopeLexerFixtures(unittest.TestCase):
    """Self-test the ``_module_scope_t_calls`` lexer against the offending shapes
    (must flag) and legitimate render-time shapes (must pass). This guards the
    *invariant itself* — a regression that re-broke the lexer into the iter-02
    false-negative state (any function body treated as render-time, literal-only
    detection) would be caught here, not just by the live-source scan. The
    iter-02 Codex review listed four false-negative shapes; all four are below."""

    # Each fixture must yield ≥1 module-load offence.
    OFFENDING = {
        "object-literal (the original OUTCOME_COPY bug)": "const M = { a: t('errors.x') };",
        "bare const": "const X = t('errors.x');",
        "IIFE concise": "const X = (() => t('errors.x'))();",
        "IIFE braced": "const X = (function () { return t('errors.x'); })();",
        "eager .map callback": "const X = KEYS.map(() => t('errors.x'));",
        "eager .forEach callback": "const X = []; KEYS.forEach((k) => { X.push(t(k)); });",
        "module-scope template": "const X = `prefix ${t('errors.x')} suffix`;",
        "variable-key call": "const k = 'errors.x';\nconst X = t(k);",
        "ternary at module scope": "const X = cond ? t('errors.a') : t('errors.b');",
    }

    # Each fixture must yield ZERO offences (legitimate render-time / forwarding).
    CLEAN = {
        "function declaration": "function f() { return t('errors.x'); }",
        "stored arrow helper": "const f = () => t('errors.x');",
        "stored arrow with params": "const f = (key) => t(key);",
        "hook component": (
            "function C() { const { t } = useT(); return t('errors.x'); }"
        ),
        "key map (strings only)": (
            "const KEYS = { a: 'errors.a', b: 'errors.b' };\n"
            "function f() { return t(KEYS.a); }"
        ),
        "map inside a function (render-time)": (
            "function f() { return KEYS.map((k) => t(k)); }"
        ),
        "template inside a function": "function f() { return `${t('errors.x')}`; }",
        "function t() declaration is not a call": (
            "export function t(key) { return MESSAGES[key]; }"
        ),
        "object method shorthand value access": (
            "const KEY = 'errors.x';\n"
            "function C() { const { t } = useT(); return t(KEY); }"
        ),
        "nested deferred helper inside an IIFE": (
            "const X = (() => { const h = () => t('errors.x'); return h; })();"
        ),
    }

    def test_offending_shapes_flagged(self) -> None:
        for label, snippet in self.OFFENDING.items():
            with self.subTest(shape=label):
                self.assertTrue(
                    _module_scope_t_calls(snippet),
                    f"module-load t() shape NOT flagged (false negative): {label}",
                )

    def test_legitimate_shapes_pass(self) -> None:
        for label, snippet in self.CLEAN.items():
            with self.subTest(shape=label):
                self.assertEqual(
                    _module_scope_t_calls(snippet),
                    [],
                    f"render-time t() shape wrongly flagged (false positive): {label}",
                )


class TestNoExtraLocaleBundles(unittest.TestCase):
    """src/locales holds exactly one JSON bundle per supported locale."""

    def test_bundle_set_matches_supported_locales(self) -> None:
        bundles = {p.stem for p in LOCALES_DIR.glob("*.json")}
        self.assertEqual(
            bundles,
            set(SUPPORTED_LOCALES),
            f"src/locales bundles {sorted(bundles)} must equal SUPPORTED_LOCALES "
            f"{sorted(SUPPORTED_LOCALES)} (no orphan / no missing bundle)",
        )


class TestNoDevTokenInLocaleValues(unittest.TestCase):
    """Phase L (tester-ux-frontend-redesign §4) — no developer/permission token
    literal leaks into rendered UI text.

    The locale bundle values are the SSOT for every rendered string (sealed by
    ``TestNoInlineHangulLiteral`` / ``TestNoInlineRenderedEnglishLiteral``), so a
    scan over the flattened values is a complete scan of user-facing copy — it
    cannot false-positive on code identifiers, permission-token CONSTANTS
    (``api/permissions.ts`` ``PERMISSION_PLATFORM_READ='platform:read'``), test
    ``authenticateAs([...])`` setup, or API field names, because those never live
    in a locale value. Security-sensitive: a screen that prints ``platform:claim``
    leaks the internal permission structure (§4).
    """

    # Hard tokens that must never appear in rendered text. Permission tokens
    # (the vocabulary SSOT) stay in code; the UI says "권한이 없어요" instead.
    DEV_TOKEN_RX = re.compile(
        r"platform:(?:read|claim|admin)"
        r"|headless:read"
        r"|session:control"
        r"|test_plan:(?:read|author)"
        r"|condition_hash"
        r"|capability_path"
        r"|project_member_permissions"
        r"|\bRBAC\b"
    )

    def test_no_dev_token_in_rendered_copy(self) -> None:
        offenders: list[str] = []
        for locale in SUPPORTED_LOCALES:
            for key, value in _flatten(_load_locale(locale)).items():
                if isinstance(value, str) and self.DEV_TOKEN_RX.search(value):
                    offenders.append(f"{locale}:{key} -> {value!r}")
        self.assertEqual(
            offenders,
            [],
            "rendered UI text must not leak developer/permission tokens "
            "(replace with tester-domain language; permission tokens stay in "
            f"api/permissions.ts only): {offenders[:20]}",
        )


class TestNoOperatorDevTermInKoLocale(unittest.TestCase):
    """Phase A (tester-ux-frontend-hardening-followup R1) — no operator-facing
    developer / English-operator term leaks into the **Korean** rendered copy.

    A non-developer tester who switches the UI to Korean reads the ``ko``
    bundle, so a leftover English operator word (``Online`` / ``Margin`` /
    ``Plan``) or a
    developer-jargon phrase (``Boot error`` / ``feature matrix`` / ``Grid PoC`` /
    ``read-only``) in a ``ko`` value is unreadable jargon. This scan is over the
    flattened ``ko`` locale *values* — the SSOT for every rendered Korean string
    (sealed complete by ``TestNoInlineHangulLiteral`` /
    ``TestNoInlineRenderedEnglishLiteral``) — so it cannot false-positive on code
    identifiers, permission-token CONSTANTS, test setup tokens, or API field
    names, none of which live in a locale value.

    Scope is ``ko`` ONLY: the ``en`` bundle is the English locale, where these
    words are the legitimate translation (complementary to
    ``TestNoDevTokenInLocaleValues``, which scans BOTH locales for hard
    permission tokens that must never render in any language).

    Allowed Latin runs in ``ko`` (brand / standards / format tokens such as
    ``FCC Test Platform`` / ``UUID`` / ``ISO 8601`` / ``CSV`` / ``API`` /
    ``Admin`` / ``BLE / DTM / 1M`` / ``plan-...``) are NOT a blanket ban — the
    denylist enumerates only the specific operator terms with a tester-domain
    Korean replacement, so legitimate identifiers stay.
    """

    # Specific operator/developer terms that must NOT appear in a `ko` value.
    # `Plan` is capital-only (so the lowercase `plan-...` ID placeholder and
    # `Platform` do not trip); the rest are case-insensitive. Each maps to a
    # tester-domain Korean term in the bundle (R1 권장 치환).
    KO_DEV_TERM_RX = re.compile(
        r"\bOnline\b"
        r"|\bOffline\b"
        r"|\bBoot error\b"
        r"|\bGrid PoC\b"
        r"|\bread-only\b"
        r"|\bfeature matrix\b"
        r"|\btest plan tables\b"
        r"|\bequipment\b"
        r"|\breference tables\b"
        r"|\bjob\b"
        r"|\bworker\b"
        r"|\bMargin\b",
        re.IGNORECASE,
    )
    # `Plan` is case-sensitive (capital P): avoids the lowercase `plan-...`
    # placeholder and substrings like `Platform`.
    KO_DEV_TERM_CASE_RX = re.compile(r"\bPlan\b")

    def test_no_operator_dev_term_in_ko_values(self) -> None:
        offenders: list[str] = []
        for key, value in _flatten(_load_locale("ko")).items():
            if not isinstance(value, str):
                continue
            if self.KO_DEV_TERM_RX.search(value) or self.KO_DEV_TERM_CASE_RX.search(value):
                offenders.append(f"ko:{key} -> {value!r}")
        self.assertEqual(
            offenders,
            [],
            "Korean (ko.json) rendered copy must not contain operator-facing "
            "developer / English-operator terms — replace with tester-domain "
            f"Korean (R1 권장 치환): {offenders[:20]}",
        )


class TestEmptyFilteredTitleStatesSearchScope(unittest.TestCase):
    """M2 (debt-ledger-reconciliation, 2026-08-01) — a filtered empty-state title
    that says "nothing matches" without naming the *scope* of the search misleads
    the reader into "this doesn't exist anywhere" when the truth is narrower
    ("nothing in this filter/screen"). ``routes.myProjects.list`` is the entry
    screen's project search; a hit for an existing management number that simply
    falls outside the selected status filter must not read as "no such project".

    Sealed as a PROPERTY, not a pinned literal (the contract explicitly warns
    against re-pinning an exact string — see the sibling nav seal below for the
    same lesson learned earlier in this codebase): the value must (a) contain a
    scope token, (b) preserve the ``{query}`` placeholder, (c) keep the same
    placeholder set across locales. ``routes.projects.coverage.emptyFilteredTitle``
    was evaluated for the same defect and is NOT included here — see
    ``tech-debt-tracker.md`` [debt-ledger-reconciliation] for why it differs
    (that screen is already inside a single project's context, established by
    its own ``emptyTitle`` sibling and section heading, so the "no such project
    anywhere" misreading does not apply the same way).
    """

    KEY = "routes.myProjects.list.emptyFilteredTitle"
    # Tokens that count as "this states a search scope" per locale (case-insensitive
    # substring match — Korean has no separate case, so this is just lower()).
    SCOPE_TOKENS: dict[str, tuple[str, ...]] = {
        "ko": ("필터",),
        "en": ("filter",),
    }

    def test_key_exists_in_both_locales(self) -> None:
        for locale in SUPPORTED_LOCALES:
            flat = _flatten(_load_locale(locale))
            self.assertIn(self.KEY, flat, f"{locale}: missing key {self.KEY}")

    def test_states_the_search_scope(self) -> None:
        for locale in SUPPORTED_LOCALES:
            value = _flatten(_load_locale(locale))[self.KEY]
            tokens = self.SCOPE_TOKENS[locale]
            self.assertTrue(
                any(tok.lower() in value.lower() for tok in tokens),
                f"{locale}: emptyFilteredTitle does not state a search scope "
                f"(expected one of {tokens} in {value!r}) — a bare "
                '"nothing matches \'{query}\'" reads as global non-existence',
            )

    def test_query_placeholder_preserved(self) -> None:
        for locale in SUPPORTED_LOCALES:
            value = _flatten(_load_locale(locale))[self.KEY]
            self.assertIn("{query}", value, f"{locale}: {{query}} placeholder dropped")

    def test_placeholder_sets_match_between_locales(self) -> None:
        flat_ko = _flatten(_load_locale("ko"))
        flat_en = _flatten(_load_locale("en"))
        ko_ph = sorted(_PLACEHOLDER.findall(flat_ko[self.KEY]))
        en_ph = sorted(_PLACEHOLDER.findall(flat_en[self.KEY]))
        self.assertEqual(ko_ph, en_ph, f"placeholder drift on {self.KEY}: ko={ko_ph} en={en_ph}")


class TestNavLabelsAreNotMutualSubstrings(unittest.TestCase):
    """M3 (debt-ledger-reconciliation, 2026-08-01) — re-evaluation of a prior
    session's deferred "nav labels are substrings of other UI copy" debt.

    A prior evaluation counted 14/16 ``routes.layout.nav.*`` labels as substrings
    of *some other message in the bundle* (e.g. the short nav label "진행률"
    inside the unrelated screen string "진행률 작업 흐름") and filed that as debt.
    That framing is wrong: a short nav label legitimately appearing inside an
    unrelated longer phrase on a *different* screen is not a user-facing lie, and
    a global "no nav label may be any other message's substring" rule would force
    unnatural copy just to satisfy a scan.

    The one real risk is narrower: a NON-exact test selector could accidentally
    match a *different* nav item if one nav label is a substring of *another nav
    label* (both rendered in the same primary-nav list). That risk is measured at
    0/16 today (this is a regression-only seal, not a fix), scoped to
    ``routes.layout.nav.*`` (16 keys) for both locales — narrower than the prior
    session's broad-corpus framing.
    """

    MIN_NAV_LABELS = 16

    @staticmethod
    def _nav_labels(locale: str) -> dict[str, str]:
        return _load_locale(locale)["routes"]["layout"]["nav"]

    @staticmethod
    def _mutual_substring_violations(labels: dict[str, str]) -> list[tuple[str, str]]:
        """Pairwise: label A (non-empty) is contained in label B, for A != B."""
        violations: list[tuple[str, str]] = []
        items = list(labels.items())
        for k1, v1 in items:
            for k2, v2 in items:
                if k1 == k2:
                    continue
                if v1 and v1 in v2:
                    violations.append((k1, k2))
        return violations

    def test_lower_bound_is_non_vacuous(self) -> None:
        # `all([])` is vacuously True — guard against the scan silently narrowing
        # to an empty (or near-empty) key set and reporting a false "0 violations".
        self.assertEqual(len(SUPPORTED_LOCALES), 2, "locale count must be 2")
        for locale in SUPPORTED_LOCALES:
            labels = self._nav_labels(locale)
            self.assertGreaterEqual(
                len(labels),
                self.MIN_NAV_LABELS,
                f"{locale}: nav label count fell below the measured floor "
                f"({len(labels)} < {self.MIN_NAV_LABELS}) — scope narrowed silently",
            )
            pairs_compared = len(labels) * (len(labels) - 1)
            self.assertGreater(
                pairs_compared, 0, f"{locale}: 0 label pairs compared — vacuous check"
            )

    def test_lower_bound_guard_rejects_an_emptied_scope(self) -> None:
        # M5 axis 4 — sealed, not just manually verified: if the target key set
        # were emptied (or a filter made always-false), the floor check above
        # must itself fail. Proven directly against the guard, not by inference.
        self.assertLess(0, self.MIN_NAV_LABELS)
        empty_labels: dict[str, str] = {}
        self.assertLess(len(empty_labels), self.MIN_NAV_LABELS)

    def test_real_nav_labels_are_not_mutual_substrings(self) -> None:
        for locale in SUPPORTED_LOCALES:
            labels = self._nav_labels(locale)
            violations = self._mutual_substring_violations(labels)
            self.assertEqual(
                violations,
                [],
                f"{locale}: nav label(s) are substrings of another nav label — "
                f"non-exact-selector ambiguity risk: {violations}",
            )

    def test_synthetic_offender_is_detected(self) -> None:
        # No real locale file touched — proves the check function actually
        # judges (a check that always passes would pass this too if not for the
        # explicit assertion that it must NOT).
        offending = {"a": "측정", "b": "측정 현황"}
        violations = self._mutual_substring_violations(offending)
        self.assertEqual(
            violations,
            [("a", "b")],
            "synthetic substring collision was NOT detected — the check is vacuous",
        )

    def test_scope_is_narrow_not_full_corpus(self) -> None:
        # Proves the narrowing (nav.* only, not "any message in the bundle") is
        # real and not accidental. `progress.workbenchNavAria` genuinely contains
        # the nav label "진행률" (nav.progress) — the exact shape of debt the
        # prior session flagged — but it is a DIFFERENT screen's aria-label, not
        # another nav item, so it is out of scope for THIS seal.
        ko = _load_locale("ko")
        unrelated_value = ko["routes"]["progress"]["workbenchNavAria"]
        nav_ko = self._nav_labels("ko")
        self.assertIn("progress", nav_ko)
        self.assertIn(
            nav_ko["progress"],
            unrelated_value,
            "fixture drift: 'progress.workbenchNavAria' no longer contains the nav "
            "label — pick another real unrelated-long-phrase fixture",
        )
        # The detector itself CAN flag this pairing when both are in its input
        # (sanity: it is not blind to real substrings) —
        probe = dict(nav_ko)
        probe["__unrelated_probe__"] = unrelated_value
        self.assertIn(("progress", "__unrelated_probe__"), self._mutual_substring_violations(probe))
        # — but the production scan's input is `nav_ko` itself, which never
        # contains this unrelated phrase, so it is not falsely flagged in practice.
        self.assertNotIn("__unrelated_probe__", nav_ko)
        self.assertEqual(self._mutual_substring_violations(nav_ko), [])


#: The one module an e2e spec may read the i18n bundle through.
E2E_LOCALE_READER = "apps/web/tests/e2e/helpers/locale-messages.ts"
#: Any spelling of "I am declaring my own bundle resolver".
#:
#: ⚠️ **The object-property form is here because a fifth independent reviewer
#: wrote it and it passed.** ``export const helpers = { messageAt(k){…} }``
#: declares the same resolver with none of ``function``/``const``/``let``/``var``
#: in front of the name. The second alternative catches a shorthand method
#: (params, then a body brace) and a property assignment — and deliberately does
#: **not** catch a plain call ``messageAt('a.b')``, which is what every legitimate
#: consumer writes. Keying on the bare identifier would flag every consumer and
#: the next author would delete the rule.
_MESSAGE_RESOLVER_DECL = re.compile(
    r"\b(?:function|const|let|var)\s+messageAt\b"
    r"|(?<![.\w$])messageAt\s*(?::|\([^)]*\)\s*\{)"
)
#: Any spelling of "I am opening the bundle myself". Deliberately the *path*
#: rather than `readFileSync`: the defect is reading the locale JSON, and a
#: future reader could arrive through `fs/promises`, an import attribute, or a
#: helper this test has never heard of.
#:
#: ⚠️ **The directory, not the full filename.** The same reviewer split the path
#: across a constant (``const L='../../src/locales/'; … L+'ko.json'``) and built
#: another with ``resolve(__dirname,'../../src/locales','ko.json')``; both walked
#: past a pattern that demanded ``src/locales/ko.json`` contiguously. The
#: directory cannot be split further without naming ``locales`` on its own, so
#: both spellings are keyed — and nothing legitimate under
#: ``apps/web/tests/e2e/`` names that directory except the declared reader.
#:
#: ⚠️ **Widened again, because the first shape that walked past it was this
#: repository's own house style.** A sixth independent reviewer fed the shipped
#: detector ``import ko from '@/locales/ko.json'`` on the real 43-file tree and
#: got ``{}``. `apps/web/vite.config.ts` maps ``@`` → ``src`` and
#: `tests/e2e/helpers/sample-inventory-fixture.ts` already imports that way, so
#: the evasion was not exotic — it was the spelling a next author would reach for
#: first. Five more followed: the path split inside a word
#: (``'../../src/loc' + 'ales/ko.json'``), a template segment
#: (``` `../../src/${dir}/ko.json` ```), a dynamic ``await import()``, a renamed
#: resolver over an alias import, and an object-property resolver over one.
#:
#: The repair is to stop asking *how the path is spelled* and ask **what file is
#: named**. Every evasion above still writes ``locales`` or a bundle basename
#: somewhere, because it has to name the file it opens. Both halves are derived
#: from the locale SSOT rather than typed, so adding a locale extends the
#: detector instead of silently leaving a hole.
#:
#: ⚠️ Measured on the real tree at this SHA: **0** false positives. Legitimate
#: consumers import ``helpers/locale-messages``, which contains no ``locales``
#: segment and no bundle basename — the widening costs nothing.
_LOCALE_BUNDLE_READ = re.compile(
    r"locales\b|\b(?:" + "|".join(re.escape(locale) for locale in SUPPORTED_LOCALES) + r")\.json\b"
)


#: Comment spans, stripped before the literal census so prose about Korean copy
#: is not counted as Korean copy. ⚠️ Order matters: block comments first, because
#: a ``//`` inside one is not a line comment.
_TS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_TS_LINE_COMMENT = re.compile(r"(?m)^\s*//.*$")
#: A string literal in any of TypeScript's three quotings. Escapes are consumed so
#: a quote inside a literal does not end it early.
_TS_STRING_LITERAL = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`", re.S)
_HANGUL_SYLLABLE = re.compile(r"[가-힣]")


def e2e_hangul_literal_census(sources: dict[str, str]) -> dict[str, int]:
    """Korean **string literals** per e2e file — the census, defined here.

    ⚠️ **The number is derived, because the published one was not reproducible.**
    Rounds 3 and 4 asserted "57 hardcoded Korean literals across 23 files"; a
    sixth reviewer swept eight readings and got 104/28, 73/28, 85/27, 56/27,
    101/26, 70/26, 84/26 and 55/26 — none of them 57/23 — and F5's own lesson,
    *name the reading*, had not been applied to F6's number. So the reading is
    the function: literals only (not lines), comments stripped, occurrences not
    distinct values, every ``.ts`` under the e2e tree.

    Prose is stripped first on purpose. A comment explaining *why* a Korean
    string was removed would otherwise count as the string, and the repository has
    already recorded that shape — "a guard must not flag the explanation".
    """
    census: dict[str, int] = {}
    for path, text in sources.items():
        stripped = _TS_LINE_COMMENT.sub("", _TS_BLOCK_COMMENT.sub("", text))
        count = sum(
            1 for match in _TS_STRING_LITERAL.finditer(stripped) if _HANGUL_SYLLABLE.search(match.group(0))
        )
        if count:
            census[path] = count
    return census


def e2e_private_bundle_readers(sources: dict[str, str]) -> dict[str, list[str]]:
    """Files under the e2e tree that resolve the bundle themselves.

    ``sources`` maps repo-relative POSIX path → file text, so the negative arm
    can feed a tree that does not exist in the repository. Returns path → the
    reasons it is a private reader, empty when only the declared helper is one.
    """
    offenders: dict[str, list[str]] = {}
    for path, text in sorted(sources.items()):
        if path == E2E_LOCALE_READER:
            continue
        reasons = []
        if _MESSAGE_RESOLVER_DECL.search(text):
            reasons.append("declares its own messageAt")
        if _LOCALE_BUNDLE_READ.search(text):
            reasons.append("opens src/locales/*.json directly")
        if reasons:
            offenders[path] = reasons
    return offenders


class TestTheE2eLaneHasOneBundleReader(unittest.TestCase):
    """Three copies of the same resolver, folded into one derived helper.

    ⚠️ **The count is why this is a seal and not a preference.** ``a11y.spec.ts``
    and ``ui-visual-regression.spec.ts`` each carried a private ``messageAt``
    with the same body, and ``test-plans-workflow.spec.ts`` skipped the resolver
    entirely and wrote the Korean out — so a round-3 locale repair
    (``Excel 파일로 가져오기`` → ``엑셀 …``) turned four of its assertions red.
    Writing a fourth copy was the obvious next move; this makes it red instead.

    ⚠️ **Deriving the text is not weaker than the literal, because the two state
    different propositions.** *These exact words render* is judged by the locale
    gates in this file, which read the bundle. *The accessible name equals the
    visible label the component renders from this key* is what an e2e spec is
    for, and it survives a copy change while a key that stops resolving fails at
    collection time rather than as an opaque locator timeout.
    """

    @staticmethod
    def _e2e_sources() -> dict[str, str]:
        root = WEB_ROOT / "tests" / "e2e"
        return {
            path.relative_to(PROJECT_ROOT).as_posix(): path.read_text(encoding="utf-8")
            for path in sorted(root.rglob("*.ts"))
        }

    def test_only_the_declared_helper_reads_the_bundle(self) -> None:
        offenders = e2e_private_bundle_readers(self._e2e_sources())
        self.assertEqual(
            offenders,
            {},
            "an e2e file resolves the i18n bundle itself — import messageAt from "
            f"{E2E_LOCALE_READER} instead: {offenders}",
        )

    def test_the_declared_helper_exists_and_is_a_reader(self) -> None:
        helper = PROJECT_ROOT / E2E_LOCALE_READER
        self.assertTrue(helper.is_file(), f"{E2E_LOCALE_READER} is declared but absent")
        text = helper.read_text(encoding="utf-8")
        self.assertRegex(text, _LOCALE_BUNDLE_READ, "the helper does not read the bundle")
        self.assertRegex(text, _MESSAGE_RESOLVER_DECL, "the helper exports no resolver")

    def test_the_scan_is_not_vacuous(self) -> None:
        """A derived SSOT with no consumers is a file, not an SSOT."""
        sources = self._e2e_sources()
        self.assertGreater(len(sources), 10, "the e2e tree scan found almost nothing")
        consumers = sorted(
            path
            for path, text in sources.items()
            if path != E2E_LOCALE_READER and "helpers/locale-messages" in text
        )
        self.assertGreaterEqual(
            len(consumers),
            3,
            "the helper was extracted because three files needed it; fewer than three "
            f"consumers means one was left behind: {consumers}",
        )

    def test_a_private_reader_is_detected_on_a_synthetic_tree(self) -> None:
        """The effect, on a tree that does not exist in the repository."""
        synthetic = {
            E2E_LOCALE_READER: "function messageAt() { return 'src/locales/ko.json'; }",
            "apps/web/tests/e2e/good.spec.ts": (
                "import { messageAt } from './helpers/locale-messages';\n"
                "expect(x).toHaveAccessibleName(messageAt('a.b'));\n"
            ),
            "apps/web/tests/e2e/copy.spec.ts": "function messageAt(k) { return k; }\n",
            "apps/web/tests/e2e/arrow.spec.ts": "const messageAt = (k) => k;\n",
            "apps/web/tests/e2e/opens.spec.ts": (
                "const ko = await readFile(new URL('../../src/locales/ko.json', x));\n"
            ),
            # ── The three shapes a fifth independent reviewer used to walk past
            # the first version of this detector. Each is pinned as a regression.
            "apps/web/tests/e2e/split.spec.ts": (
                "const L = '../../src/locales/';\n"
                "const ko = JSON.parse(readFileSync(new URL(L + 'ko.json', import.meta.url)));\n"
            ),
            "apps/web/tests/e2e/joined.spec.ts": (
                "const ko = readFileSync(resolve(__dirname, '../../src/locales', 'ko.json'));\n"
            ),
            "apps/web/tests/e2e/property.spec.ts": (
                "export const helpers = { messageAt(k) { return k; } };\n"
            ),
            # ── The six a SIXTH reviewer walked past the second version with. The
            # first is `apps/web/vite.config.ts`'s own alias, already used by
            # `tests/e2e/helpers/sample-inventory-fixture.ts` — house style, not
            # an exotic attack, which is why it is listed first.
            "apps/web/tests/e2e/alias.spec.ts": (
                "import ko from '@/locales/ko.json';\n"
                "export const at = (k) => k.split('.').reduce((o, s) => o?.[s], ko);\n"
            ),
            "apps/web/tests/e2e/word-split.spec.ts": (
                "const p = '../../src/loc' + 'ales/ko.json';\n"
                "const ko = JSON.parse(readFileSync(p));\n"
            ),
            "apps/web/tests/e2e/renamed.spec.ts": (
                "import bundle from '@/locales/ko.json';\n"
                "function lookup(k) { return k.split('.').reduce((o, s) => o?.[s], bundle); }\n"
            ),
            "apps/web/tests/e2e/template.spec.ts": (
                "const dir = 'loc' + 'ales';\n"
                "const ko = readFileSync(`../../src/${dir}/ko.json`);\n"
            ),
            "apps/web/tests/e2e/dynamic.spec.ts": (
                "const ko = await import('@/locales/ko.json');\n"
            ),
            "apps/web/tests/e2e/object-alias.spec.ts": (
                "import en from '@/locales/en.json';\n"
                "export const messages = { at(k) { return en[k]; } };\n"
            ),
        }
        offenders = e2e_private_bundle_readers(synthetic)
        self.assertEqual(
            sorted(offenders),
            [
                "apps/web/tests/e2e/alias.spec.ts",
                "apps/web/tests/e2e/arrow.spec.ts",
                "apps/web/tests/e2e/copy.spec.ts",
                "apps/web/tests/e2e/dynamic.spec.ts",
                "apps/web/tests/e2e/joined.spec.ts",
                "apps/web/tests/e2e/object-alias.spec.ts",
                "apps/web/tests/e2e/opens.spec.ts",
                "apps/web/tests/e2e/property.spec.ts",
                "apps/web/tests/e2e/renamed.spec.ts",
                "apps/web/tests/e2e/split.spec.ts",
                "apps/web/tests/e2e/template.spec.ts",
                "apps/web/tests/e2e/word-split.spec.ts",
            ],
            f"the detector must flag every private shape and neither the helper "
            f"nor the consumer: {offenders}",
        )
        # ⚠️ The renamed and object-alias shapes must be caught by the PATH axis,
        # not by the identifier — that is the whole point of widening it. A future
        # reader is under no obligation to call anything `messageAt`.
        for path in (
            "apps/web/tests/e2e/renamed.spec.ts",
            "apps/web/tests/e2e/alias.spec.ts",
            "apps/web/tests/e2e/dynamic.spec.ts",
        ):
            self.assertEqual(
                offenders[path],
                ["opens src/locales/*.json directly"],
                f"{path} must be caught without naming the resolver",
            )
        self.assertEqual(
            offenders["apps/web/tests/e2e/opens.spec.ts"],
            ["opens src/locales/*.json directly"],
            "the path axis must fire on its own — a future reader may never spell "
            "`messageAt`, and keying only on the identifier would miss it",
        )



#: Korean string literals per e2e file, measured by ``e2e_hangul_literal_census``
#: at 2026-08-27. **A ratchet, per file, and that is the mechanism — not a
#: bookkeeping detail.**
#:
#: ⚠️ **This exists because a ledger row with no owner produced the same finding
#: twice.** Two independent reviewers reported the axis open; both times it was
#: written down and nobody picked it up, because "23 files across other sessions'
#: domains" has no natural owner. The operator's determination on 2026-08-27 was
#: to stop looking for one: *"다음부터 다른 세션이 e2e 관련 코드 작업을 할 때 그 세션이
#: 맡아서 그것도 해결하도록"*. A per-file ratchet says exactly that in code — whoever
#: opens the file inherits its literals, everybody else is undisturbed, and no
#: wave has to be scheduled.
#:
#: **If this test fails on your file, you are the owner now.** Route the Korean
#: through ``messageAt()`` from ``helpers/locale-messages`` and lower the number
#: here. A file absent from this table must have **zero** — new specs start clean.
E2E_HANGUL_LITERAL_BASELINE: dict[str, int] = {
    "apps/web/tests/e2e/artifact-custody-workflow.spec.ts": 1,
    "apps/web/tests/e2e/auth-flow.spec.ts": 1,
    "apps/web/tests/e2e/chambers-workflow.spec.ts": 1,
    "apps/web/tests/e2e/control-workflow.spec.ts": 2,
    "apps/web/tests/e2e/diagnostics-workflow.spec.ts": 2,
    "apps/web/tests/e2e/equipment-lists-workflow.spec.ts": 1,
    "apps/web/tests/e2e/fields-workflow.spec.ts": 2,
    "apps/web/tests/e2e/grid-poc.spec.ts": 5,
    "apps/web/tests/e2e/helpers/visual-fixture.ts": 1,
    "apps/web/tests/e2e/jobs-workflow.spec.ts": 1,
    "apps/web/tests/e2e/membership-workflow.spec.ts": 1,
    "apps/web/tests/e2e/my-projects-workflow.spec.ts": 1,
    "apps/web/tests/e2e/progress-workflow.spec.ts": 1,
    "apps/web/tests/e2e/projects-workflow.spec.ts": 1,
    "apps/web/tests/e2e/provider-picker-list-states.spec.ts": 3,
    "apps/web/tests/e2e/providers-workflow.spec.ts": 1,
    "apps/web/tests/e2e/reference-data-workflow.spec.ts": 1,
    "apps/web/tests/e2e/reports-workflow.spec.ts": 1,
    "apps/web/tests/e2e/responsive-layout.spec.ts": 6,
    "apps/web/tests/e2e/route-resilience.spec.ts": 6,
    "apps/web/tests/e2e/sessions-workflow.spec.ts": 2,
    "apps/web/tests/e2e/smoke.spec.ts": 15,
    "apps/web/tests/e2e/test-plans-generation-browser.spec.ts": 16,
    "apps/web/tests/e2e/test-plans-live.spec.ts": 2,
    "apps/web/tests/e2e/test-reports-workflow.spec.ts": 3,
    "apps/web/tests/e2e/web-font-loading.spec.ts": 5,
}


#: Keys whose **English** value legitimately contains Hangul, with the reason.
#: Endonyms only: the name a language calls itself is the same word in every
#: bundle, and translating it would be the defect.
EN_HANGUL_ENDONYMS: dict[str, str] = {
    "routes.layout.localeToggle.korean": (
        "endonym — the Korean language names itself 한국어 in the English UI too, which is what "
        "every locale switcher on the web does; rendering it as 'Korean' would make the toggle "
        "unreadable to the operator it is for"
    ),
}


class TestTheEnglishBundleIsNotWrittenInKorean(unittest.TestCase):
    """The en-side of M-7, which had no axis at all until now.

    ⚠️ **Measured, not imagined.** A sixth independent reviewer built nine attacks
    against the three locale laws this wave shipped and one escaped every single
    one: *an `en` leaf written in Korean that differs from `ko` by one character*.
    The identical-leaf table needs `ko == en`; the translatability law keys on
    byte equality of the English value; the no-Hangul axis asks about **`ko`**;
    and the mixed-script law looks for latin **inside Korean**. Nothing asked the
    obvious question — *is this English?*

    Today exactly one leaf is affected and it is correct, so the hole is latent.
    That is precisely when it is cheap to close: M-7 says `ko/en 모두 고쳐진다`, and
    a law with no en-side arm cannot mean that.
    """

    def test_no_english_value_is_written_in_korean(self) -> None:
        bundle = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
        offenders: list[str] = []

        def walk(node: object, path: list[str]) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, [*path, key])
            elif isinstance(node, str) and _HANGUL_SYLLABLE.search(node):
                dotted = ".".join(path)
                if dotted not in EN_HANGUL_ENDONYMS:
                    offenders.append(f"{dotted} = {node!r}")

        walk(bundle, [])
        self.assertEqual(
            offenders,
            [],
            "these English values contain Korean. Translate them, or — if the word really is the "
            "same in English (an endonym) — declare the key in EN_HANGUL_ENDONYMS with the "
            f"reason:\n  " + "\n  ".join(sorted(offenders)),
        )

    def test_every_declared_endonym_is_still_one(self) -> None:
        """The allowlist may only shrink, and every row must still be reachable.

        ⚠️ An allowlist nobody re-checks becomes an amnesty. A declared key that
        no longer holds Hangul is a fossil, and a declared key that no longer
        exists is a licence for whatever takes its name next.
        """
        bundle = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))

        def at(dotted: str) -> object:
            node: object = bundle
            for part in dotted.split("."):
                if not isinstance(node, dict):
                    return None
                node = node.get(part)
            return node

        for dotted, reason in EN_HANGUL_ENDONYMS.items():
            value = at(dotted)
            self.assertIsInstance(value, str, f"declared endonym {dotted} is not a leaf any more")
            self.assertRegex(
                str(value),
                _HANGUL_SYLLABLE,
                f"{dotted} no longer contains Hangul, so its exemption is a fossil — delete it",
            )
            self.assertGreater(len(reason), 40, f"{dotted} is exempted without a reason")


class TestTheE2eKoreanLiteralsOnlyEverShrink(unittest.TestCase):
    """The literal axis, carried by a ratchet instead of by an owner.

    ⚠️ **`test-plans-workflow.spec.ts` is absent from the baseline, and that is
    the demonstration.** It carried three literals — a page heading, a chamber
    heading, and the word `아니오` — and a round-3 locale repair
    (`Excel 파일로 가져오기` → `엑셀 …`) had already turned four assertions in that
    file red once. All three now resolve through the derived reader, so the file
    left the table entirely rather than having its number lowered.
    """

    @staticmethod
    def _census() -> dict[str, int]:
        return e2e_hangul_literal_census(TestTheE2eLaneHasOneBundleReader._e2e_sources())

    def test_the_baseline_is_not_vacuous(self) -> None:
        """A ratchet over an empty census forbids nothing."""
        self.assertGreater(len(E2E_HANGUL_LITERAL_BASELINE), 10)
        self.assertGreater(sum(E2E_HANGUL_LITERAL_BASELINE.values()), 50)
        self.assertNotIn(
            "apps/web/tests/e2e/test-plans-workflow.spec.ts",
            E2E_HANGUL_LITERAL_BASELINE,
            "the one file this claim repaired must not be re-admitted to the baseline",
        )

    def test_the_census_reads_literals_and_not_prose(self) -> None:
        """Positive and negative arms, on a tree that is not the repository's.

        ⚠️ Without the negative arm this would pass on a census that counted
        everything, and without the positive one it would pass on a census that
        counted nothing.
        """
        synthetic = {
            "a.spec.ts": "await expect(x).toHaveText('시험 챔버');\n",
            "b.spec.ts": "// 이 문장은 한국어지만 주석이다\nconst a = 1;\n",
            "c.spec.ts": "/*\n * 블록 주석 안의 한국어\n */\nconst b = 2;\n",
            "d.spec.ts": "import { messageAt } from './helpers/locale-messages';\n",
            "e.spec.ts": "const two = ['첫째', `둘째`];\n",
        }
        self.assertEqual(
            e2e_hangul_literal_census(synthetic),
            {"a.spec.ts": 1, "e.spec.ts": 2},
            "only string literals count; comments are prose about the rule, not violations of it",
        )

    def test_no_e2e_file_grows_a_korean_literal(self) -> None:
        census = self._census()

        grown = sorted(
            f"{path}: {count} literal(s), baseline {E2E_HANGUL_LITERAL_BASELINE.get(path, 0)}"
            for path, count in census.items()
            if count > E2E_HANGUL_LITERAL_BASELINE.get(path, 0)
        )
        self.assertEqual(
            grown,
            [],
            "an e2e spec gained hardcoded Korean UI copy. Route it through messageAt() from "
            "apps/web/tests/e2e/helpers/locale-messages.ts — a literal breaks the moment a "
            f"locale wave renames one word, which has already happened here:\n  "
            + "\n  ".join(grown),
        )

        shrunk = sorted(
            f"{path}: {census.get(path, 0)} literal(s), baseline {baseline}"
            for path, baseline in E2E_HANGUL_LITERAL_BASELINE.items()
            if census.get(path, 0) < baseline
        )
        self.assertEqual(
            shrunk,
            [],
            "these files carry fewer Korean literals than the baseline records — lower the "
            "numbers in E2E_HANGUL_LITERAL_BASELINE in the same commit, or the next author "
            f"inherits a budget that was already spent:\n  " + "\n  ".join(shrunk),
        )

        vanished = sorted(
            path for path in E2E_HANGUL_LITERAL_BASELINE if path not in census
        )
        self.assertEqual(
            vanished,
            [],
            "these files no longer carry any Korean literal, so their rows are fossils and must "
            f"be deleted from the baseline: {vanished}",
        )


if __name__ == "__main__":
    unittest.main()
