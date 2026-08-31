"""Frontend cross-cutting architecture conformance seal (Increment 2,
``fe-conformance-and-design-review``, 2026-06-13).

The ``apps/web`` frontend reached a disciplined baseline in the prior session
(design-token ``global.css`` + ``@/ui`` primitives + an ``ApiError`` SSOT in
``shared/api-error.ts`` + a single typed-client backend path), but there was no
*cross-cutting* invariant sealing those wins from drift — the existing guards
(``test_fe_phase1_ui_foundation.py``) are primitive-scoped only. A later route
that re-declares an inline ``interface FooError extends Error``, inlines a hex
color, or calls raw ``fetch()`` outside the typed-client/auth layer would slip
through the backend-only CI lane.

This Python invariant seals three architecture-level rules across the whole
``apps/web/src`` tree (companion skill: ``/verify-frontend-conformance``):

1. ``TestFrontendErrorTypeSsot`` — no inline ``interface <Name> extends Error``
   declaration (extending the *built-in* ``Error``) outside the one allowed
   module (``shared/api-error.ts``). This is a NARROW rule: it seals only
   re-declarations of an interface extending the built-in ``Error``. It does
   NOT enforce that all error modeling routes through ``ApiError`` —
   class-based ``Error`` subclasses, ``type`` aliases, and
   ``interface … extends ApiError`` are intentionally not flagged.
2. ``TestFrontendNoInlineHexColor`` — no inline hex color literal anywhere under
   ``src`` (consume ``--status-*``/``--accent``/``--fg-*`` tokens instead). The
   only allowlisted site is ``main.tsx`` (pre-boot fallback that paints before
   the stylesheet loads).
3. ``TestFrontendBackendAccessViaTypedClient`` — raw ``fetch(`` / ``axios`` /
   ``new XMLHttpRequest(`` only inside ``src/api/`` (typed openapi-fetch client)
   and ``src/auth/`` (OIDC/IdP, which cannot route through the API client). Every
   other module reaches the backend through the typed client.

All scans are docstring-aware: TypeScript block/line comments are stripped so a
comment mentioning ``fetch`` or a hex value is not a false positive. The
``apps/web/src/api/generated`` directory (codegen output) is excluded from every
walk. Exception allowlists are module-level ``frozenset``s — ratchet-down
(monotonic-decrease); all three currently seal a measured-zero state.
"""

from __future__ import annotations

import contextlib
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from typing import Callable

# ⚠️ 2026-08-31 에 이 모듈들은 이사했다. 경로를 적으면 레포마다 다른 문자열이
# 필요하지만 임포트 이름은 양쪽에서 같다 — 모듈에게 자기 위치를 묻는다.
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))
from _moved_module_source import moved_module_source  # noqa: E402
# ⚠️ 두 줄로 나눠 적는다 — 합치지 마라. `TestNoNinthPrivateCopy::
# test_every_migrated_module_imports_the_shared_lexer` 가 이 모듈이 공유 렉서를
# 쓰는지 **리터럴 한 줄**(`from support.parity import strip_ts_comments`)로 확인하므로,
# 같은 SSOT 모듈에서 형제 헬퍼를 하나 더 들여오려고 줄을 합치면 그 게이트가 red 다
# (실측 2026-09-12). 게이트를 고치는 것이 옳은 방향이지만 그것은 렉서 SSOT 웨이브의
# 소유이고, 장부에 등재했다.
from support.parity import strip_ts_comments
from support.parity import parse_ts_object_keys
from support.parity import mask_ts_noncode
from support.parity import match_brackets
from support.parity import iter_ts_object_literals
from support.parity import TsUnbalancedRegionError
from support.frontend_error_copy import (
    tree_code_copy_tables,
    copy_assignment_sites,
    modules_with_copy_assignments,
    refined_copy_keys,
    tables_not_directly_under,
    modules_naming_codes_beside_copy,
    scan_tree_for_copy_tables,
    code_token_sites,
    census_copy_tables,
    enclosing_container,
    error_code_vocabulary,
    screaming_snake_keys,
    status_keys,
)
from support.frontend_route_registry import (
    RouteEntry,
    UnresolvedRouteElementError,
    collect_route_entries,
)
from support.assertion_thresholds import (
    ASSERTION_CENSUS_LIMITATION,
    EXEMPT_SHAPE_FIXTURES,
    SYNTACTIC_HOME_FIXTURES,
    census_from_source,
    census_numeric_thresholds,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = PROJECT_ROOT / "apps" / "web"
SRC_DIR = WEB_ROOT / "src"
GENERATED_DIR = SRC_DIR / "api" / "generated"

# `TestSearchScopeClaimsMatchTheServerAxes` 는 프론트 **문구**를 백엔드 **SSOT** 에
# 대조한다. 이 파일의 다른 봉인들과 달리 텍스트 스캔만으로는 판정할 수 없다 —
# "검색 범위 주장이 과장인가"는 서버가 실제로 어느 컬럼을 훑는지 알아야만 답이 되고,
# 그 사실을 테스트에 다시 적으면 **드리프트의 세 번째 사본**이 될 뿐이다.
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from domain.services.project_directory_query import (  # noqa: E402
    PROJECT_SEARCH_COLUMNS,
)
from domain.services.project_metadata_edit import (  # noqa: E402
    EDITABLE_PROJECT_META_FIELDS,
    IMMUTABLE_PROJECT_FIELDS,
)


# ── Ratchet-down exception allowlists (relative-to-src POSIX paths) ──────────
# Adding an entry requires a documented decision; the policy is
# monotonic-decrease. All three are minimal and reflect a measured-zero seal.

# The single SSOT module that is *allowed* to declare an `*Error extends Error`
# interface. Every other module must import `ApiError` from here.
ERROR_TYPE_SSOT_ALLOWLIST: frozenset[str] = frozenset({"shared/api-error.ts"})

# `main.tsx` paints a pre-boot fallback message before the stylesheet (and thus
# the design tokens) are available, so it inlines a single error-red hex.
HEX_COLOR_ALLOWLIST: frozenset[str] = frozenset({"main.tsx"})

# Directory prefixes (relative to src) permitted to perform raw backend access.
# `api/` is the typed openapi-fetch client surface; `auth/` is the OIDC/IdP
# layer (token + discovery endpoints) that cannot route through the API client.
BACKEND_ACCESS_ALLOWED_PREFIXES: tuple[str, ...] = ("api/", "auth/")

# FILE-scoped raw-access exceptions (fe-w2-a-result-report-honesty M3,
# 2026-07-28). Deliberately a file allowlist rather than a widened directory
# prefix: widening `shared/` would exempt ~20 modules to excuse one.
#
# `shared/signed-download.ts` spends an FE-P6-DL download grant. The grant URL is
# signed *precisely so that it needs no RBAC header*; routing it through the
# typed client would attach `Authorization` via `authRetryMiddleware` and invert
# that security model. Like `auth/`, this is access that structurally cannot go
# through the typed client. Ratchet-down: a second entry needs the same argument.
BACKEND_ACCESS_FILE_ALLOWLIST: frozenset[str] = frozenset(
    {"shared/signed-download.ts"}
)

# ── Percent-display SSOT (fe-w2-a-result-report-honesty M1, 2026-07-28) ──────
#
# Ratchet-down allowlist of surfaces still formatting a percentage themselves.
# An entry is a DEBT RECORD, not an exemption on the merits.
#
# ***THE DEBT IS PAID IN FULL — THIS LIST IS EMPTY AND MUST STAY EMPTY.***
#
# History, so a later wave does not re-open it believing that was ever normal:
#   W2-A (M1)  opened the list with 4 entries, all outside its own write scope.
#   W2-B (M7)  paid 3 — `control.tsx`, `ChamberProgress.tsx`, and
#              `ChamberProgressBar.tsx`. The last was the worst instance in the
#              codebase: it decided the completion TONE from the ALREADY ROUNDED
#              value, so a 99.6% run was painted in the "pass" palette *and*
#              labelled "100%" — the lie asserted twice, on two axes.
#   W2-C (M5)  paid the last one, `routes/fields.tsx`, which survived the first
#              two waves only because it sat outside both write scopes.
#
# CONSEQUENCE OF REACHING 0: `TestPercentFormattingDelegatesToSsot::
# test_no_local_percent_formatting_outside_the_allowlist` is now an
# UNCONDITIONAL, repo-wide property — "no apps/web surface formats a percentage
# itself", with no carve-out to check first.
#
# Adding an entry here is therefore not "recording debt like the previous waves
# did" — it is DEMOTING a global invariant back to a conditional one, and
# `TestPercentFormatAllowlistRatchet.CEILING = 0` makes the suite reject it.
# The fix is delegation to `shared/percent-display::formatPercent`; in every
# case so far that has been a one-line change.
PERCENT_FORMAT_ALLOWLIST: frozenset[str] = frozenset()

#: Modules that MUST delegate to the percent-display SSOT. Non-vacuity guard: if
#: one of these stops importing the SSOT the scan above could pass simply because
#: the file no longer renders a percentage. Each wave adds the surfaces whose
#: debt it just paid, so a later wave cannot quietly re-inline the rounding.
#: W2-B added the middle three; W2-C adds `routes/fields.tsx`.
PERCENT_SSOT_CONSUMERS: tuple[str, ...] = (
    "routes/progress.tsx",
    "ui/RunProgress.tsx",
    "routes/control.tsx",
    "routes/chambers/ChamberProgress.tsx",
    "routes/chambers/ChamberProgressBar.tsx",
    "routes/fields.tsx",
)

#: The percent-display SSOT module itself (allowed to round, by definition).
PERCENT_SSOT_MODULE = "shared/percent-display.ts"

#: Real consumption of the SSOT — an ``import`` binding plus at least one call.
#: A bare substring scan would be satisfied by a *comment* naming the module
#: (``routes/fields.tsx`` carries exactly such a note), so the non-vacuity guard
#: below would go false-green the moment the import and the call were removed.
PERCENT_SSOT_IMPORT_RE = re.compile(
    r"import\s*\{[^}]*\bformatPercent\b[^}]*\}\s*from\s*"
    r"['\"][^'\"]*shared/percent-display['\"]"
)
PERCENT_SSOT_CALL_RE = re.compile(r"\bformatPercent\s*\(")


def _strip_ts_comments(src: str) -> str:
    """Delegate to the shared lexer — one judgement input for every seal.

    This used to be a crude regex that read ``//`` inside a string literal as a
    comment, so the same source could be judged differently here than by a
    sibling seal. The shared lexer preserves string, template-literal and
    regex-literal content verbatim. Sealed by
    ``tests/test_ts_comment_stripper_ssot.py``; an eighth private copy is
    rejected by the census there.
    """
    return strip_ts_comments(src)


#: 비동기 export 의 **두 철자**. 화살표 형태를 빼면 그 형태로 쓴 operation 이
#: 파생에서 사라지고, 사라진 것은 검사되지 않는다.
_ASYNC_EXPORT_RE = re.compile(
    r"^export\s+(?:async\s+function\s+(\w+)|const\s+(\w+)\s*(?::[^=]*)?=\s*async\b)",
    re.M,
)
#: 본문의 끝은 **다음 `export` 선언**이다(다음 *async* 선언이 아니다).
_ANY_EXPORT_RE = re.compile(r"^export\b", re.M)


def _headless_operations() -> dict[str, str]:
    """``operation 이름 → 그 본문``, ``headless-client.ts`` 에서 **파생**.

    손 목록이 아니다. 새 operation 이 생기면 자동으로 들어오고 사라지면 자동으로
    빠진다 — 이 파일의 형제 검사(`_session_consumers`)가 리터럴 목록으로 시작했다가
    적대적 평가에 뚫린 뒤 파생으로 바뀐 그 선례를 따른다.

    ⚠️ **철자와 경계 둘 다 틀렸던 적이 있다**(독립 검토 2026-08-19, 실행으로 증명).
    처음 판은 `\nexport async function (\w+)\(` 하나로 split 했고, 그래서
    ``export const foo = async () => { … }`` 로 다시 쓰면서 **오류 처리를 통째로
    지운** operation 이 전량 green 이었다 — 두 가지가 겹쳤다:

    1. 그 철자를 못 본다(제네릭 `foo<T>(` 도 마찬가지 — 이름 뒤 `(` 를 요구했다).
    2. 못 본 선언의 본문이 **앞 operation 의 청크에 흡수**돼, 앞 것의
       ``apiErrorFromResponse(`` 가 뒤 것의 부재를 가렸다.

    그래서 경계는 **다음 `export` 선언**이다. 못 보는 철자가 하나 남더라도 그 본문이
    남의 청크로 흘러들지는 않는다.
    """
    return _client_operations(SRC_DIR / "api" / "headless-client.ts")


def _client_operations(path) -> dict[str, str]:
    """``operation 이름 → 그 본문``, 어느 typed client 에서든 같은 규칙으로 파생.

    ⚠️ **경로가 인자인 이유**는 두 번째 클라이언트가 이 규칙을 필요로 했을 때
    사본을 만들지 않기 위해서다. 이 저장소에서 사본은 *한쪽만 고쳐지는* 방식으로
    갈라지고(이 파일의 `_drops_the_code` 가 그 흉터를 독스트링에 적고 있다),
    갈라진 쪽이 조용히 답한다.
    """
    src = _strip_ts_comments(path.read_text(encoding="utf-8"))
    starts = [(m.start(), m.group(1) or m.group(2)) for m in _ASYNC_EXPORT_RE.finditer(src)]
    bounds = [m.start() for m in _ANY_EXPORT_RE.finditer(src)] + [len(src)]
    operations: dict[str, str] = {}
    for offset, name in starts:
        end = next(b for b in bounds if b > offset)
        operations[name] = src[offset:end]
    return operations


#: 다운로드 operation 의 표식 — **반환 타입**이다. 이름이 아니다.
#:
#: ⚠️ 초판은 *위임받는 헬퍼 이름*으로 면제를 파생했고(`toDownload` 를 부르면 통과),
#: 그 술어가 raw substring 이라 **`autoDownload(` 가 `toDownload(` 를 포함**했다 —
#: 실패를 통째로 삼키는 operation 이 그 이름 하나로 통과한다(독립 적대적 평가
#: 2026-09-12 가 실행으로 증명). 이름은 우연히 겹치고 **타입은 겹치지 않는다.**
_DOWNLOAD_RETURN_RE = re.compile(r":\s*Promise<\s*HeadlessDownload\s*>")


def _client_transport_seams() -> "set[str]":
    """`headless-client.ts` 의 **전송 형태를 정하는** 동기 export 들.

    ⚠️ **`export function` 전량이 아니다.** 그렇게 파생하면
    `createHeadlessClientForBaseUrl`(노드별 클라이언트 팩토리, 이 축과 무관하고
    이 웨이브가 만들지도 않았다)까지 들어오고, 그러면 변이 표에 **게이트를 만족시키려는
    항목**을 넣게 된다 — 이 저장소가 이름 붙인 그 실패(*"padding the table to satisfy
    a gate is how a battery stops meaning anything"*).

    판정은 그 함수가 **응답을 어떤 모양으로 받을지 정하는가** 이고, 이 모듈에서 그것은
    둘 중 하나로 드러난다: `parseAs` 를 쓰거나 `HeadlessDownload` 를 다루거나. 계획서가
    이름으로 예고한 장래의 `textRequest`/`streamRequest` 는 전자로 자동 합류한다
    (독립 적대적 평가 2026-09-12 가 지적한 구멍이 정확히 그 자리였다).
    """
    src = _strip_ts_comments(HEADLESS_CLIENT.read_text(encoding="utf-8"))
    starts = [(m.start(), m.group(1)) for m in re.finditer(r"^export function (\w+)", src, re.M)]
    bounds = [m.start() for m in _ANY_EXPORT_RE.finditer(src)] + [len(src)]
    seams = set()
    for offset, name in starts:
        end = next(b for b in bounds if b > offset)
        chunk = src[offset:end]
        if "parseAs" in chunk or "HeadlessDownload" in chunk:
            seams.add(name)
    return seams


def _download_operations() -> "dict[str, str]":
    """``HeadlessDownload`` 를 돌려준다고 **선언한** operation 들.

    이들이 `apiErrorFromResponse(` 를 직접 갖지 않는 이유는 실패를 삼켜서가 아니라
    그 판정이 `toDownload` 라는 **다운로드 전용 공유 seam** 으로 옮겨갔기 때문이고,
    그 seam 은 `TestBlobParsingIsADeclaredConsumptionAxis` 가 따로 심문한다 —
    두 seam 을 모두 지나는지, 그리고 seam 자신이 factory 로 던지는지.
    """
    return {
        name: body
        for name, body in _headless_operations().items()
        if _DOWNLOAD_RETURN_RE.search(body)
    }


def _operations_owning_path(path_template: str) -> list[str]:
    """그 경로 템플릿을 **따옴표째** 본문에 든 operation 이름들.

    ⚠️ 맨 substring 이면 `'…/test-plan/drafts'` 를 `…/drafts/{draft_id}/rows` 같은
    **하위 경로 전부**가 소유한다(독립 검토 실측: 그 하나로 약 12개가 걸린다).
    첫 링크가 그렇게 헐거우면 두 링크로 나눈 의미가 없다.

    고정은 **닫는 따옴표**다(여는 쪽이 아니다) — 호출자가 넘기는 경로는
    `'/test-plan/drafts/{draft_id}/archive'` 처럼 접두(`/headless/projects/…`)가
    없는 **꼬리**일 수 있는데, 리터럴의 끝은 언제나 그 꼬리와 일치하기 때문이다.
    그러면 `…/drafts'` 는 `…/drafts/{draft_id}/rows'` 와 정확히 갈린다.
    """
    tail = f"{path_template}'"
    return sorted(
        name for name, body in _headless_operations().items() if tail in body
    )


def _consumes_headless_path(route_code: str, path_template: str) -> tuple[bool, str]:
    """*라우트 → operation → 경로* 두 링크로 소비를 판정한다.

    ⚠️ **이 헬퍼는 게이트를 약화시키지 않는다 — 링크를 하나 더한다.** 2026-08-19
    이전에는 라우트 소스에서 경로 템플릿을 직접 찾았는데, `headless-client-helper-layer`
    웨이브가 전송을 라우트 밖으로 옮기면서 그 증거가 자리를 옮겼다. 경로만 다시
    찾으면 검사는 영원히 red 이고, 라우트에서 operation 이름만 찾으면 *그 operation 이
    정말 그 경로를 부르는가* 를 아무도 안 본다. 둘 다 요구한다.
    """
    owners = _operations_owning_path(path_template)
    if not owners:
        return False, (
            f"`{path_template}` 를 부르는 operation 이 headless-client.ts 에 없다 — "
            "백엔드 operation 소비가 통째로 사라졌다"
        )
    # ⚠️ **언급이 아니라 호출을 요구한다.** `name in route_code` 는 `import { … }`
    # 줄만으로 만족된다 — 독립 검토가 archive 소비를 통째로 지우고 `const op =
    # 'archiveTestPlanDraft'` 만 남겼는데 *"소비가 사라졌다"* 는 게이트가 침묵했다.
    used = [name for name in owners if re.search(rf"\b{re.escape(name)}\s*\(", route_code)]
    if not used:
        return False, (
            f"이 라우트가 `{path_template}` 를 소유한 operation({', '.join(owners)}) 중 "
            "어느 것도 부르지 않는다 — 소비가 사라졌다"
        )
    return True, ""


def _src_files() -> list[Path]:
    """Every ``apps/web/src/**/*.{ts,tsx}`` excluding the codegen output dir."""
    files: list[Path] = []
    for pattern in ("*.ts", "*.tsx"):
        for path in SRC_DIR.rglob(pattern):
            try:
                path.relative_to(GENERATED_DIR)
            except ValueError:
                files.append(path)
    return sorted(files)


def _rel(path: Path) -> str:
    return path.relative_to(SRC_DIR).as_posix()


def _offending_sites(pattern: re.Pattern[str]) -> list[str]:
    """``src`` 전역에서 ``pattern`` 이 걸리는 ``rel/path.tsx:line`` 목록.

    스캔은 항상 주석 제거 후 수행한다 — 이 트리에는 "왜 그렇게 하지 **않았는가**"
    를 설명하는 주석이 여럿 있고(``ui/ProjectPicker.tsx`` 의 ``role="combobox"``,
    ``shared/ProjectSelectField.tsx`` 의 ``fetchProjects``), strip 없이 스캔하면
    **규칙을 설명하는 문서가 규칙 위반으로 잡힌다**.
    """
    out: list[str] = []
    for path in _src_files():
        code = _strip_ts_comments(path.read_text(encoding="utf-8"))
        for match in pattern.finditer(code):
            line = code.count("\n", 0, match.start()) + 1
            out.append(f"{_rel(path)}:{line}")
    return out


class TestFrontendSrcTreePresent(unittest.TestCase):
    """Guard against a silent no-op (empty walk ⇒ vacuous PASS)."""

    def test_src_dir_exists(self) -> None:
        self.assertTrue(SRC_DIR.is_dir(), f"apps/web/src missing: {SRC_DIR}")

    def test_walk_finds_source_files(self) -> None:
        self.assertGreater(
            len(_src_files()),
            0,
            "no .ts/.tsx files discovered under apps/web/src — the conformance "
            "scans would vacuously pass",
        )


class TestFrontendErrorTypeSsot(unittest.TestCase):
    """No inline ``interface <Name> extends Error`` (built-in ``Error``) outside
    the one allowed module (``shared/api-error.ts``).

    NARROW scope: this seals re-declarations of an interface extending the
    *built-in* ``Error`` only. It deliberately does NOT enforce that every error
    type routes through ``ApiError`` — an ``interface FooError extends ApiError``
    (qualified base) is not matched, nor are class-based ``Error`` subclasses or
    ``type`` aliases. The class name retains the ``Ssot`` suffix because
    ``shared/api-error.ts`` is the single home for the canonical inline-Error
    interface, but the assertion message and docs avoid the broader claim."""

    # `interface FooError extends Error` (any whitespace). The base must be the
    # bare built-in `Error` — `extends ApiError` (qualified base) is NOT matched,
    # so only re-declarations of a new built-in-Error-derived interface trip this.
    PATTERN = re.compile(r"interface\s+\w+\s+extends\s+Error\b")

    def test_no_inline_error_interface_outside_ssot(self) -> None:
        offenders: list[str] = []
        for path in _src_files():
            rel = _rel(path)
            if rel in ERROR_TYPE_SSOT_ALLOWLIST:
                continue
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for m in self.PATTERN.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                offenders.append(f"{rel}:{line}")
        self.assertEqual(
            offenders,
            [],
            "inline `interface <Name> extends Error` (built-in Error) "
            "declaration(s) outside the one allowed module "
            "(shared/api-error.ts) — import `ApiError` from @/shared/api-error "
            f"or extend it (`extends ApiError`) instead: {offenders}",
        )

    def test_ssot_module_declares_the_error_interface(self) -> None:
        # The SSOT must actually carry the canonical declaration, otherwise the
        # allowlist points at a moved/renamed file and the rule is hollow.
        ssot = SRC_DIR / "shared" / "api-error.ts"
        self.assertTrue(ssot.is_file(), f"ApiError SSOT missing: {ssot}")
        self.assertRegex(
            ssot.read_text(encoding="utf-8"),
            r"interface\s+ApiError\s+extends\s+Error\b",
            "shared/api-error.ts must declare `interface ApiError extends Error`",
        )


class TestFrontendNoInlineHexColor(unittest.TestCase):
    """No inline hex color literal under ``src`` (token consumption only)."""

    HEX_LITERAL = re.compile(r"#[0-9a-fA-F]{3,8}\b")

    def test_no_inline_hex_color(self) -> None:
        offenders: list[str] = []
        for path in _src_files():
            rel = _rel(path)
            if rel in HEX_COLOR_ALLOWLIST:
                continue
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for m in self.HEX_LITERAL.finditer(src):
                line = src.count("\n", 0, m.start()) + 1
                offenders.append(f"{rel}:{line} ({m.group(0)})")
        self.assertEqual(
            offenders,
            [],
            "inline hex color literal(s) under apps/web/src — consume "
            "--status-*/--accent/--fg-* tokens from global.css instead "
            f"(allowlist={sorted(HEX_COLOR_ALLOWLIST)}): {offenders}",
        )


class TestFrontendBackendAccessViaTypedClient(unittest.TestCase):
    """Raw backend access only in the typed client (``api/``) and auth layers."""

    # Precise patterns: a raw `fetch(` call, an `axios` reference, or a raw XHR
    # construction. `XMLHttpRequestInstrumentation` (an OpenTelemetry import in
    # observability/) is deliberately NOT matched — it is not raw XHR access.
    PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        ("fetch", re.compile(r"\bfetch\s*\(")),
        ("axios", re.compile(r"\baxios\b")),
        ("XMLHttpRequest", re.compile(r"\bnew\s+XMLHttpRequest\s*\(")),
    )

    def _is_allowed(self, rel: str) -> bool:
        return (
            rel.startswith(BACKEND_ACCESS_ALLOWED_PREFIXES)
            or rel in BACKEND_ACCESS_FILE_ALLOWLIST
        )

    def test_no_raw_backend_access_outside_typed_client(self) -> None:
        offenders: list[str] = []
        for path in _src_files():
            rel = _rel(path)
            if self._is_allowed(rel):
                continue
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for label, pattern in self.PATTERNS:
                for m in pattern.finditer(src):
                    line = src.count("\n", 0, m.start()) + 1
                    offenders.append(f"{rel}:{line} ({label})")
        self.assertEqual(
            sorted(offenders),
            [],
            "raw backend access (fetch/axios/XMLHttpRequest) outside the typed "
            "client (src/api/) and auth layer (src/auth/) — route through the "
            f"openapi-fetch client instead: {sorted(offenders)}",
        )

    def test_typed_client_surface_exists(self) -> None:
        # The rule presumes a typed-client directory; if it vanished the
        # allowlist prefix would silently permit everything in api/.
        self.assertTrue(
            (SRC_DIR / "api").is_dir(),
            "apps/web/src/api typed-client surface missing",
        )

    def test_file_allowlist_entries_exist_and_still_need_the_exception(self) -> None:
        """A stale exception (file gone, or no longer doing raw access) must go."""
        stale: list[str] = []
        for rel in BACKEND_ACCESS_FILE_ALLOWLIST:
            path = SRC_DIR / rel
            if not path.is_file():
                stale.append(f"{rel} (file gone)")
                continue
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            if not any(pattern.search(src) for _, pattern in self.PATTERNS):
                stale.append(f"{rel} (no raw access — exception no longer needed)")
        self.assertEqual(stale, [], f"remove stale raw-access exceptions: {stale}")


class TestPercentFormattingDelegatesToSsot(unittest.TestCase):
    """Percent → display string goes through one SSOT (W2-A M1, P5).

    The defect: `Math.round(99.6)` renders `"100%"`, so a run with work left
    announced itself as finished, and `0.4` rendered `"0%"` so a started run
    announced itself as untouched. Because the formatting was re-derived at every
    call site, fixing one site fixed one screen. `shared/percent-display.ts` owns
    the boundary rule now; this seal keeps consumers from growing a private copy.

    Scope note: the patterns match a rounding call *adjacent to a percent* — a
    literal `%` right after the interpolation, an i18n `percent:` parameter, or a
    `percent`/`pct`-named binding. Rounding a DURATION (`Math.floor(seconds/60)`
    in `projects.tsx`/`ChamberAdminPanel.tsx`) or a measurement (`toFixed(1)` on
    dBm in the grid POC fixture) is not a percentage claim and is not flagged.
    """

    PERCENT_FORMAT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "template-literal `${…round…}%`",
            re.compile(
                r"\$\{[^}]*(?:Math\.(?:round|floor|ceil)|\.toFixed)\s*\([^}]*\}\s*%"
            ),
        ),
        (
            "i18n `percent:` param",
            re.compile(
                r"percent\s*:\s*[^,)\n]*(?:Math\.(?:round|floor|ceil)|\.toFixed)\s*\("
            ),
        ),
        (
            "percent-named binding",
            re.compile(
                # No leading-character requirement: the commonest offender is a
                # bare `const percent = Math.round(…)`.
                r"\b(?:const|let|var)\s+[\w$]*(?:[Pp]ercent|[Pp]ct)[\w$]*"
                r"\s*(?::[^=]+)?=\s*[^;\n]*"
                r"(?:Math\.(?:round|floor|ceil)|\.toFixed)\s*\("
            ),
        ),
    )

    def _scan(self) -> list[str]:
        offenders: list[str] = []
        for path in _src_files():
            rel = _rel(path)
            if rel == PERCENT_SSOT_MODULE or rel in PERCENT_FORMAT_ALLOWLIST:
                continue
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for label, pattern in self.PERCENT_FORMAT_PATTERNS:
                for m in pattern.finditer(src):
                    line = src.count("\n", 0, m.start()) + 1
                    offenders.append(f"{rel}:{line} ({label})")
        return sorted(offenders)

    def test_ssot_module_exists(self) -> None:
        self.assertTrue(
            (SRC_DIR / PERCENT_SSOT_MODULE).is_file(),
            f"percent display SSOT missing: {PERCENT_SSOT_MODULE}",
        )

    def test_no_local_percent_formatting_outside_the_allowlist(self) -> None:
        offenders = self._scan()
        self.assertEqual(
            offenders,
            [],
            "surface formats a percentage itself instead of delegating to "
            "`shared/percent-display.ts::formatPercent` — a local `Math.round`/"
            "`toFixed` re-introduces the 99.6%→\"100%\" boundary lie: "
            f"{offenders}",
        )

    def test_owned_surfaces_import_the_ssot(self) -> None:
        """Non-vacuity: the scan passing because a file stopped rendering a
        percentage at all would be a false green.

        Checked over *comment-stripped* source against the real import binding
        and a real call site — a prose note naming ``formatPercent`` or
        ``shared/percent-display`` must not stand in for consuming them.
        """
        missing: list[str] = []
        for rel in PERCENT_SSOT_CONSUMERS:
            path = SRC_DIR / rel
            if not path.is_file():
                missing.append(f"{rel} (file gone)")
                continue
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            if not PERCENT_SSOT_IMPORT_RE.search(src):
                missing.append(f"{rel} (no formatPercent import from the SSOT)")
            elif not PERCENT_SSOT_CALL_RE.search(src):
                missing.append(f"{rel} (imports the SSOT but never calls it)")
        self.assertEqual(
            missing,
            [],
            f"a percent-rendering surface stopped delegating to the SSOT: {missing}",
        )

    def test_ssot_consumption_check_is_not_satisfied_by_comments(self) -> None:
        """The guard above must fail on a file that only *mentions* the SSOT.

        ``routes/fields.tsx`` carries a comment naming both the module path and
        the helper, so a substring scan would keep passing after the import and
        the call were deleted.
        """
        comment_only = (
            "// 숫자 렌더는 `shared/percent-display::formatPercent` 위임.\n"
            "/* formatPercent from '@/shared/percent-display' */\n"
            "export const Badge = () => <span>{Math.round(pct)}%</span>;\n"
        )
        stripped = _strip_ts_comments(comment_only)
        self.assertIsNone(PERCENT_SSOT_IMPORT_RE.search(stripped))
        self.assertIsNone(PERCENT_SSOT_CALL_RE.search(stripped))

        real = (
            "import { classifyPercent, formatPercent } from '@/shared/percent-display';\n"
            "export const Badge = () => <span>{formatPercent(pct)}</span>;\n"
        )
        self.assertIsNotNone(PERCENT_SSOT_IMPORT_RE.search(real))
        self.assertIsNotNone(PERCENT_SSOT_CALL_RE.search(real))

    def test_patterns_actually_detect_the_original_defect(self) -> None:
        """Guard against a regex that matches nothing (vacuous seal)."""
        historical = [
            "value: `${Math.round(progress.data.ratio * 100)}%`",
            "valueText={`${pct.toFixed(0)}%`}",
            "t('routes.fields.progressBadge', { percent: Math.round(pct) })",
            "  const percent = Math.round(progress.ratio * 100);",
        ]
        for sample in historical:
            self.assertTrue(
                any(p.search(sample) for _, p in self.PERCENT_FORMAT_PATTERNS),
                f"pattern set no longer detects a known offender: {sample!r}",
            )

    def test_non_percentage_rounding_is_not_flagged(self) -> None:
        """False-positive guard: durations and measurements may still round."""
        benign = [
            "return t('routes.projects.age.minutes', { n: Math.floor(seconds / 60) });",
            "const minutes = Math.floor(seconds / 60);",
            "targetPowerDbm: Number((10 + (index % 11) * 0.5).toFixed(1)),",
            "return (minutes / 60).toFixed(1);",
        ]
        for sample in benign:
            self.assertFalse(
                any(p.search(sample) for _, p in self.PERCENT_FORMAT_PATTERNS),
                f"non-percentage rounding wrongly flagged: {sample!r}",
            )


class TestPercentFormatAllowlistRatchet(unittest.TestCase):
    """The percent-format allowlist is carried debt — it must only shrink.

    It has now shrunk to nothing, so at `CEILING = 0` this class no longer
    guards a budget: it guards the *absence of a carve-out*. Any new entry is a
    regression of `TestPercentFormattingDelegatesToSsot` from a repo-wide
    property back to a conditional one, and fails here rather than silently
    weakening that seal.
    """

    #: 4 (W2-A M1 baseline, all out-of-scope surfaces) → 1 (W2-B M7 paid 3) →
    #: **0** (W2-C M5 paid `routes/fields.tsx`, the last one). Terminal value —
    #: this ratchet has no lower rung left, so the number may never rise again.
    CEILING = 0

    def test_allowlist_does_not_grow(self) -> None:
        self.assertLessEqual(
            len(PERCENT_FORMAT_ALLOWLIST),
            self.CEILING,
            "PERCENT_FORMAT_ALLOWLIST grew — a NEW surface started formatting a "
            "percentage locally. Ratchet down, never up: "
            f"{sorted(PERCENT_FORMAT_ALLOWLIST)}",
        )

    def test_entries_still_exist_and_still_offend(self) -> None:
        """A stale entry (file deleted, or debt already paid) must be removed —
        otherwise the ceiling silently stops meaning anything."""
        stale: list[str] = []
        scanner = TestPercentFormattingDelegatesToSsot()
        for rel in PERCENT_FORMAT_ALLOWLIST:
            path = SRC_DIR / rel
            if not path.is_file():
                stale.append(f"{rel} (file gone)")
                continue
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            if not any(
                p.search(src) for _, p in scanner.PERCENT_FORMAT_PATTERNS
            ):
                stale.append(f"{rel} (debt already paid)")
        self.assertEqual(stale, [], f"remove stale allowlist entries: {stale}")


class TestStreamStatusVocabularySsot(unittest.TestCase):
    """M3 (fe-w2-b-execution-freshness, 2026-07-28) — one live-channel state must
    not acquire two names.

    ``/control`` and the multi-chamber workbench render the connection lifecycle
    of two different WebSocket channels that report the IDENTICAL four tokens.
    Before this milestone only ``/control`` mapped them, and it labelled the badge
    with the RAW token; the chamber surface rendered nothing at all. The obvious
    way to add the chamber badge — a second ``chamberStreamStatusKind`` with its
    own Korean labels — is precisely how the same state ends up called two things
    on two screens, so both the colour axis and the LABEL axis are pinned to a
    single definition here.
    """

    #: The one module allowed to own the stream-status mapping.
    STREAM_MAPPING_MODULE = "ui/status-mapping.ts"
    #: The surfaces that render a stream-status badge. Both must consume the SSOT.
    STREAM_BADGE_CONSUMERS: tuple[str, ...] = (
        "routes/control.tsx",
        "routes/chambers/ChamberRunOverview.tsx",
    )
    #: The canonical lifecycle tokens (backend mirror; see `api/*-events.ts`).
    STREAM_TOKENS: tuple[str, ...] = ("connecting", "open", "reconnecting", "closed")
    #: i18n leaf tokens the label axis resolves, including the unknown degrade.
    STREAM_LABEL_LEAVES: tuple[str, ...] = (*STREAM_TOKENS, "unknown")

    def _definers(self, name: str) -> list[str]:
        pattern = re.compile(rf"export\s+function\s+{name}\b")
        return [
            _rel(path)
            for path in _src_files()
            if pattern.search(_strip_ts_comments(path.read_text(encoding="utf-8")))
        ]

    def test_each_mapping_has_exactly_one_definition(self) -> None:
        for name in ("streamStatusKind", "streamStatusLabelToken"):
            with self.subTest(mapping=name):
                self.assertEqual(
                    self._definers(name),
                    [self.STREAM_MAPPING_MODULE],
                    f"`{name}` must be defined exactly once, in "
                    f"{self.STREAM_MAPPING_MODULE} — a second definition is a "
                    "forked vocabulary",
                )

    def test_both_badge_surfaces_consume_the_ssot(self) -> None:
        """Non-vacuity: the single-definition rule above would pass if a surface
        simply stopped rendering the badge."""
        missing: list[str] = []
        for rel in self.STREAM_BADGE_CONSUMERS:
            path = SRC_DIR / rel
            if not path.is_file():
                missing.append(f"{rel} (file gone)")
                continue
            src = path.read_text(encoding="utf-8")
            for name in ("streamStatusKind", "streamStatusLabelToken"):
                if name not in src:
                    missing.append(f"{rel} (does not consume {name})")
        self.assertEqual(
            missing,
            [],
            f"a stream-status surface stopped delegating to the SSOT: {missing}",
        )

    def test_no_route_redeclares_the_stream_token_vocabulary(self) -> None:
        """A route listing the lifecycle tokens as literals is re-declaring the
        vocabulary, which is how the two screens would drift apart again. The
        wire mirrors in `api/` legitimately own them and are out of scope."""
        offenders: list[str] = []
        for path in _src_files():
            rel = _rel(path)
            if not rel.startswith("routes/"):
                continue
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            hits = [
                token
                for token in self.STREAM_TOKENS
                if re.search(rf"['\"]{token}['\"]", src)
            ]
            if len(hits) >= 3:
                offenders.append(f"{rel} ({sorted(hits)})")
        self.assertEqual(
            offenders,
            [],
            "route re-declares the stream lifecycle vocabulary instead of "
            f"delegating to {self.STREAM_MAPPING_MODULE}: {offenders}",
        )

    def test_both_locales_carry_every_stream_label_leaf(self) -> None:
        """The label axis resolves ``ui.streamStatus.<leaf>`` at render time, so a
        missing leaf is a raw key on screen — invisible to the ko/en PARITY seal,
        which only checks that the two bundles agree with each other."""
        for locale in ("ko", "en"):
            bundle = json.loads(
                (SRC_DIR / "locales" / f"{locale}.json").read_text(encoding="utf-8")
            )
            leaves = bundle.get("ui", {}).get("streamStatus", {})
            self.assertEqual(
                sorted(leaves),
                sorted(self.STREAM_LABEL_LEAVES),
                f"{locale}.json ui.streamStatus leaves drifted from the "
                "streamStatusLabelToken output set",
            )


class TestProviderOperatorSurface(unittest.TestCase):
    """Phase E (tester-ux-frontend-hardening-followup R2) — the provider "Test
    Types" screen is an operator/admin surface, NOT an internal descriptor /
    debug viewer.

    The descriptor payload still carries internal identifiers (provider id / UI
    version / feature·table·group ids / sheet names / row-identity source) — the
    backend contract is unchanged — but the operator VIEW must not render them as
    if they were tester copy (R2). This seal pins the curated view so a later
    edit cannot silently re-introduce a raw `<code>` id chip or the
    row-identity-source column.

    Sealed (over ``routes/providers.tsx`` source, comments stripped):
      * no ``row_identity_source`` reference at all (column fully removed);
      * no ``sheet_name`` reference at all (column fully removed);
      * no raw ``<code>`` id chip (the internal-id chips are gone);
      * the provider id + UI version are relocated to a collapsed
        ``descriptor-diagnostics`` ``<details>`` (admin/diagnostics, not the
        default operator table);
      * the feature status renders a localized label via
        ``featureStatusLabelToken`` (not the raw backend status token).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = _strip_ts_comments(
            (SRC_DIR / "routes" / "providers.tsx").read_text(encoding="utf-8")
        )

    def test_no_internal_row_identity_or_sheet_rendered(self) -> None:
        for token in ("row_identity_source", "sheet_name"):
            self.assertNotIn(
                token,
                self.source,
                f"providers.tsx must not reference `{token}` — it is an internal "
                "descriptor identifier, not operator copy (R2)",
            )

    def test_no_raw_code_id_chip(self) -> None:
        self.assertNotIn(
            "<code>",
            self.source,
            "providers.tsx must not render a raw <code> id chip — internal ids "
            "are dropped from the operator tables (R2)",
        )

    def test_provider_id_relocated_to_diagnostics_details(self) -> None:
        self.assertIn(
            'data-testid="descriptor-diagnostics"',
            self.source,
            "providers.tsx must relocate provider id / UI version into a "
            "collapsed `descriptor-diagnostics` <details> (R2)",
        )
        self.assertIn(
            "<details",
            self.source,
            "providers.tsx diagnostics must use a collapsible <details> element",
        )

    def test_feature_status_rendered_as_localized_label(self) -> None:
        self.assertIn(
            "featureStatusLabelToken",
            self.source,
            "providers.tsx must render the feature status via "
            "featureStatusLabelToken (localized label, not the raw token) (R2)",
        )


class TestRouteEmptyStateHasDescription(unittest.TestCase):
    """Phase C (tester-ux-frontend-hardening-followup R4) — every route-level
    ``<EmptyState …/>`` carries a ``description`` (not title-only).

    The §6.3 redesign rule requires each empty state to explain *why* it is
    empty / how to populate it, so a tester does not mistake "no data yet" for a
    broken screen. A title-only empty state is a regression. The scan is over
    ``src/routes/**/*.tsx`` only (the operator route surface that renders
    EmptyState) and inspects each self-closing ``<EmptyState …/>`` element for
    the ``description`` prop.

    A *conditional* description (``{...(cond ? { description: … } : {})}``,
    e.g. ``membership.tsx`` where a non-admin sees no actionable hint) satisfies
    the rule — the source carries the ``description`` token. ``action`` is NOT
    required (the §6.3 rule mandates description; a CTA is added only where a
    natural non-G3 next action exists)."""

    ROUTES_DIR = SRC_DIR / "routes"
    EMPTY_STATE = re.compile(r"<EmptyState\b.*?/>", re.DOTALL)

    def _route_tsx_files(self) -> list[Path]:
        return sorted(self.ROUTES_DIR.rglob("*.tsx"))

    def test_route_empty_states_carry_a_description(self) -> None:
        offenders: list[str] = []
        total = 0
        for path in self._route_tsx_files():
            rel = _rel(path)
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for m in self.EMPTY_STATE.finditer(src):
                total += 1
                if "description" not in m.group(0):
                    line = src.count("\n", 0, m.start()) + 1
                    offenders.append(f"{rel}:{line}")
        self.assertEqual(
            offenders,
            [],
            "route-level <EmptyState/> without a `description` prop (title-only) "
            "— add a description explaining why it's empty / how to populate it "
            f"(R4 §6.3): {offenders}",
        )
        # Non-vacuous guard: the route tree must actually render EmptyStates,
        # otherwise the scan would silently pass on an empty match set.
        self.assertGreater(
            total,
            0,
            "no <EmptyState/> usage found under src/routes — the description "
            "scan would vacuously pass",
        )


class TestResponsiveBreakpointSsot(unittest.TestCase):
    """Responsive breakpoint SSOT (operator-ux-responsive-shell, 2026-06-23).

    Plain CSS cannot read a custom property inside a ``@media`` condition, so a
    breakpoint px value is necessarily authored twice: once as a ``--bp-*``
    token (the documented source, also read by JS/e2e) and once as the literal
    in the ``@media (max-width: …)`` query that consumes it. This guard seals
    the two together so they cannot silently drift, and forbids an *orphan*
    width-breakpoint literal — every ``@media (max-width: Npx)`` in
    ``global.css`` must correspond to a declared ``--bp-*`` token at
    ``N + 0.02px`` (the 0.02 boundary-avoidance offset). prefers-color-scheme /
    prefers-reduced-motion queries carry no width literal and are unaffected.
    """

    GLOBAL_CSS = SRC_DIR / "styles" / "global.css"
    BP_TOKEN = re.compile(r"--bp-[a-z]+\s*:\s*(\d+(?:\.\d+)?)px")
    MEDIA_MAXWIDTH = re.compile(r"@media[^{]*\(\s*max-width\s*:\s*(\d+(?:\.\d+)?)px")
    BOUNDARY_OFFSET = 0.02

    def test_global_css_present(self) -> None:
        self.assertTrue(self.GLOBAL_CSS.is_file(), f"missing {self.GLOBAL_CSS}")

    def test_every_maxwidth_media_derives_from_a_bp_token(self) -> None:
        css = self.GLOBAL_CSS.read_text(encoding="utf-8")
        token_values = {round(float(v), 2) for v in self.BP_TOKEN.findall(css)}
        self.assertTrue(token_values, "no --bp-* breakpoint tokens declared")

        media_literals = [float(v) for v in self.MEDIA_MAXWIDTH.findall(css)]
        # Non-vacuous: the responsive shell must actually use a width breakpoint.
        self.assertTrue(
            media_literals,
            "no `@media (max-width: …)` query found — the responsive nav collapse "
            "must exist",
        )

        orphans: list[str] = []
        for literal in media_literals:
            expected_token = round(literal + self.BOUNDARY_OFFSET, 2)
            if expected_token not in token_values:
                orphans.append(
                    f"@media max-width:{literal}px has no --bp-* token at "
                    f"{expected_token}px (declared: {sorted(token_values)})"
                )
        self.assertEqual(
            orphans,
            [],
            "orphan responsive breakpoint literal(s) in global.css — author the "
            "value as a `--bp-*` token (breakpoint SSOT) and consume it: "
            f"{orphans}",
        )



# ─────────────────────────────────────────────────────────────────────────────
# W2-C — editing safety, publish-gate honesty, large-row rendering
#
# Three defects shared one shape: a value the SERVER owns was copied into
# `useState` and re-seeded by an unconditional `useEffect`, so any refetch
# silently reverted whatever the operator had typed. On the bulk-CSV surface
# that escalates from annoying to unrecoverable, because "가져오기" is a PUT
# replace-all: confirming a reverted textarea rewrites the entire server row set.
#
# The behavioural proof lives in vitest (an edit surviving a real MONITORED
# refetch cannot be expressed as a source scan). These are the STRUCTURAL seals:
# they forbid the shape from coming back, which a behaviour test cannot do —
# a future edit could reintroduce a sync effect that happens to be gated today
# and regresses the moment the gate condition drifts.
# ─────────────────────────────────────────────────────────────────────────────

ROUTES_DIR = SRC_DIR / "routes"
TEST_PLANS_DIR = ROUTES_DIR / "test-plans"


class TestNoServerStateMirrorEffect(unittest.TestCase):
    """W2-C M1/M2 — the two editing surfaces hold no mirror of server state.

    Both panels now derive the displayed value as ``local ?? server`` every
    render, so there is nothing left to synchronise. Sealing "no ``useEffect``
    at all" rather than "no *unconditional* ``useEffect``" is deliberate: a
    conditional re-seed is the same defect with a lucky guard, and reviewing
    the guard's correctness on every future edit is exactly the maintenance the
    override pattern removes.
    """

    #: (file, what the effect used to overwrite) — the measured-zero surfaces.
    _SURFACES = (
        ("chambers/ChamberAdminPanel.tsx", "the operator's chamber registry edits"),
        ("test-plans/BulkRowsEditor.tsx", "the operator's unsaved bulk CSV"),
        ("equipment-lists.tsx", "the tester's unsaved §6 equipment rows"),
    )

    def test_editing_surfaces_declare_no_effect(self) -> None:
        offenders: list[str] = []
        for rel, subject in self._SURFACES:
            path = ROUTES_DIR / rel
            self.assertTrue(path.is_file(), f"missing {path}")
            src = _strip_ts_comments(path.read_text(encoding="utf-8"))
            if "useEffect" in src:
                offenders.append(f"{rel} (would overwrite {subject})")
        self.assertEqual(
            offenders,
            [],
            "server-state mirror effect reintroduced — derive the displayed "
            "value as `localOverride ?? serverValue` instead of copying the "
            f"server payload into state: {offenders}",
        )

    def test_the_override_state_is_actually_present(self) -> None:
        """Non-vacuous: "no effect" must mean "derives", not "dropped the state"."""
        panel = _strip_ts_comments(
            (ROUTES_DIR / "chambers" / "ChamberAdminPanel.tsx").read_text(encoding="utf-8")
        )
        self.assertIn("edits[chamber.chamber_id]", panel)
        self.assertIn("draftFrom(chamber)", panel)

        bulk = _strip_ts_comments(
            (TEST_PLANS_DIR / "BulkRowsEditor.tsx").read_text(encoding="utf-8")
        )
        self.assertIn("localCsv ?? exportedCsv", bulk)

    def test_bulk_import_stays_a_single_atomic_put(self) -> None:
        """Edit safety must not have been bought by weakening the write.

        The prior DELETE-loop + POST-loop left a draft half-edited on any
        mid-loop failure; the PUT replace-all is one server transaction.
        """
        bulk = _strip_ts_comments(
            (TEST_PLANS_DIR / "BulkRowsEditor.tsx").read_text(encoding="utf-8")
        )
        # ⚠️ 명제는 *한 서버 트랜잭션* 이지 *이 파일이 PUT 을 친다* 가 아니다.
        # 전송이 operation 으로 옮겨간 뒤로 이 화면은 replace-all operation 하나만
        # 부르고, 그 operation 이 PUT 임을 두 번째 링크가 확인한다. DELETE 루프의
        # 부재는 양쪽 모두에서 본다 — 되돌아온다면 둘 중 하나에는 나타난다.
        ok, why = _consumes_headless_path(
            bulk, "/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows"
        )
        self.assertTrue(ok, why)
        replace_ops = [
            name
            for name, body in _headless_operations().items()
            if name in bulk and "/test-plan/drafts/{draft_id}/rows" in body
        ]
        self.assertEqual(
            replace_ops,
            ["replaceTestPlanDraftRows"],
            "bulk 편집이 replace-all 하나가 아닌 다른 operation 을 쓴다",
        )
        self.assertIn(
            "headlessClient.PUT", _headless_operations()["replaceTestPlanDraftRows"]
        )
        self.assertNotIn("headlessClient.DELETE", bulk)
        self.assertNotIn(
            "headlessClient.DELETE",
            _headless_operations()["replaceTestPlanDraftRows"],
        )


class TestDraftPanelsAreKeyedByDraftId(unittest.TestCase):
    """W2-C M2/M3 — per-draft local state cannot outlive its draft.

    Without a ``key``, React reuses the panel instance across a draft switch, so
    draft A's unsaved CSV and validation result survive underneath draft B. That
    is not cosmetic: the CSV is a replace-all payload, so A's rows become a
    one-click overwrite of B.
    """

    _PANELS = ("DraftDetail", "DraftReadinessPanel")

    def test_both_panels_receive_the_draft_id_as_their_key(self) -> None:
        src = _strip_ts_comments(
            (TEST_PLANS_DIR / "TestPlansWorkbench.tsx").read_text(encoding="utf-8")
        )
        offenders: list[str] = []
        for panel in self._PANELS:
            match = re.search(rf"<{panel}\b[^>]*?/>", src, re.DOTALL)
            if match is None or "key={selectedDraftId}" not in match.group(0):
                offenders.append(panel)
        self.assertEqual(
            offenders,
            [],
            "draft panel(s) rendered without `key={selectedDraftId}` — their "
            "unsaved edits and validation results would leak across a draft "
            f"switch: {offenders}",
        )


class TestValidationFreshnessIsDerived(unittest.TestCase):
    """W2-C M3 — "is this validation still about this draft?" is a derivation.

    `TestPlanDraftView` carries no row-set revision, so freshness is decided by a
    content fingerprint. The fingerprint travels as the validate mutation's own
    ``variables``, which keeps the result and *what it judged* in one object.
    Storing it in a second ``useState`` would need a synchronising effect — the
    very shape :class:`TestNoServerStateMirrorEffect` bans.
    """

    def test_the_fingerprint_module_is_pure(self) -> None:
        src = _strip_ts_comments(
            (TEST_PLANS_DIR / "draftFingerprint.ts").read_text(encoding="utf-8")
        )
        for banned in ("useState", "useRef", "Date.now", "Math.random", "let "):
            self.assertNotIn(
                banned,
                src,
                f"draftFingerprint.ts must stay a pure function of its rows "
                f"(found `{banned}`) — impurity would let a remount manufacture "
                "a false 'changed'",
            )

    def test_freshness_reads_the_mutation_variables(self) -> None:
        src = _strip_ts_comments(
            (TEST_PLANS_DIR / "DraftReadinessPanel.tsx").read_text(encoding="utf-8")
        )
        self.assertIn("draftValidationState(", src)
        self.assertIn("validateMutation.variables", src)
        self.assertIn("validateMutation.mutate(fingerprint)", src)

    def test_only_a_fresh_result_can_block_publish(self) -> None:
        """Contract M3 — unvalidated is NOT a failure, and stale is not a verdict.

        The backend does not require validation before publish, so blocking on
        "not validated" would be a client-side invention; blocking on a stale
        result would be judging rows nobody validated.
        """
        src = _strip_ts_comments(
            (TEST_PLANS_DIR / "DraftReadinessPanel.tsx").read_text(encoding="utf-8")
        )
        gate = re.search(r"const blockingIssueCount =(.*?);", src, re.DOTALL)
        self.assertIsNotNone(gate, "blocking-issue derivation not found")
        assert gate is not None
        self.assertIn("'fresh'", gate.group(1))
        self.assertIn("error_count", gate.group(1))

    def test_the_three_states_are_separately_worded_in_both_locales(self) -> None:
        for locale in ("ko", "en"):
            messages = json.loads(
                (SRC_DIR / "locales" / f"{locale}.json").read_text(encoding="utf-8")
            )
            states = messages["routes"]["testPlans"]["validationState"]
            self.assertEqual(
                sorted(states),
                ["fresh", "stale", "unvalidated"],
                f"{locale}.json must word all three validation states",
            )
            # ⚠️ `3` 이 아니라 `len(states)` 다. 세 줄 위가 이미 상태 **집합**을
            # 고정하므로 숫자를 다시 적으면 네 번째 상태가 생긴 날 두 곳을 고쳐야
            # 하고, 한 곳만 고치면 그 검사는 조용히 옛 배치를 굳힌다.
            self.assertEqual(
                len({v.strip() for v in states.values()}),
                len(states),
                f"{locale}.json collapses two validation states into the same "
                "sentence — the distinction is the whole point",
            )


class TestDraftRowsScaleStructurally(unittest.TestCase):
    """W2-C M4 — neither the DOM nor the hook count may scale with row count.

    A published FCC plan is 16,000+ items. The prior code mapped each row to a
    `<tr>` AND to its own ``useMutation`` observer.
    """

    def test_the_row_component_owns_no_mutation(self) -> None:
        src = _strip_ts_comments((TEST_PLANS_DIR / "DraftRowItem.tsx").read_text(encoding="utf-8"))
        self.assertNotIn(
            "useMutation",
            src,
            "DraftRowItem must stay display-only — a per-row mutation observer "
            "is the hook-count regression this milestone removed",
        )

    def test_the_parent_owns_exactly_one_remove_mutation(self) -> None:
        src = _strip_ts_comments((TEST_PLANS_DIR / "DraftDetail.tsx").read_text(encoding="utf-8"))
        self.assertEqual(
            src.count("useOptimisticMutation<"),
            1,
            "DraftDetail must declare exactly one optimistic row mutation for the whole table",
        )
        self.assertNotIn(
            "useMutation<",
            src,
            "DraftDetail must not restore a second React Query row mutation beside the shared optimistic hook",
        )
        # Attribution must survive the hoist: which row is busy / which failed.
        self.assertIn("removeMutation.variables", src)

    def test_windowing_reuses_the_shared_primitive(self) -> None:
        src = _strip_ts_comments((TEST_PLANS_DIR / "DraftDetail.tsx").read_text(encoding="utf-8"))
        self.assertIn("VirtualizedTable", src)
        self.assertNotIn(
            "@tanstack/react-virtual",
            src,
            "route must compose `ui/VirtualizedTable`, not grow a second "
            "virtualization implementation",
        )
        css = (SRC_DIR / "styles" / "global.css").read_text(encoding="utf-8")
        self.assertIn(".test-plans-virtual-table__row {", css)

    def test_both_render_paths_share_one_column_definition(self) -> None:
        """A windowed path that drifts from the table path shows different data
        above and below the threshold — invisible until a plan crosses it."""
        src = _strip_ts_comments((TEST_PLANS_DIR / "DraftDetail.tsx").read_text(encoding="utf-8"))
        self.assertGreaterEqual(
            src.count("DRAFT_ROW_COLUMN_KEYS.map"),
            2,
            "the header of each render path must derive from the same column list",
        )
        row_src = _strip_ts_comments(
            (TEST_PLANS_DIR / "DraftRowItem.tsx").read_text(encoding="utf-8")
        )
        self.assertIn("export function draftRowCellValues", row_src)
        self.assertIn("draftRowCellValues(row)", src)

    def test_the_virtualization_test_cannot_pass_vacuously(self) -> None:
        """The single largest fake-green risk in this milestone.

        jsdom has no layout engine, so `@tanstack/react-virtual` measures every
        element as 0×0 and windows down to ZERO rows. "rendered < loaded" is then
        true even with virtualization removed. The seal requires the viewport
        stub AND a lower bound, so the assertion has to observe a real window.
        """
        spec = (WEB_ROOT / "tests" / "test-plans-row-scale.test.tsx").read_text(encoding="utf-8")
        self.assertIn("offsetHeight", spec)
        self.assertIn("offsetWidth", spec)
        self.assertIn("toBeLessThan(LARGE_ROW_COUNT)", spec)
        self.assertIn("toBeGreaterThan(0)", spec)


# ─────────────────────────────────────────────────────────────────────────────
# W3-A — 성적서(test_reports) 화면 배선 (2026-07-29)
#
# 런타임 동작은 `apps/web/tests/test-reports.test.tsx` 가 봉인한다. 아래 세 클래스는
# 텍스트/구조 스캔으로만 판정 가능한 축 — 금지 패턴의 부재(S2), 도달성(S9), 그리고
# 이 웨이브가 우회한 백엔드 아티팩트 결함의 부채 기록 — 을 담당한다.
# ─────────────────────────────────────────────────────────────────────────────

#: 새 라우트의 경로 리터럴. `ROUTE_PATHS` SSOT 와 라우터/nav 설정 밖에서 이 문자열이
#: 흩어지면 라우트 이름 변경이 조용히 깨진다.
TEST_REPORTS_PATH = "/test-reports"

#: 경로 리터럴을 직접 적어도 되는 곳은 `shared/route-links.ts` SSOT 자신뿐이다.
#: App-shell nav 는 `shared/app-shell-navigation.ts`에서 `ROUTE_PATHS`를 읽고,
#: 그 밖의 모든 소비자도 `ROUTE_PATHS.testReports`를 거쳐야 한다.
TEST_REPORTS_PATH_LITERAL_ALLOWLIST: frozenset[str] = frozenset(
    {"shared/route-links.ts"}
)

APP_SHELL_NAVIGATION_MODULE = "shared/app-shell-navigation.ts"

#: 은퇴한 ``/reports`` 제목. 이 화면은 성적서 **대장**이 아니라 성적서 파일 생성
#: 요청 큐인데 제목이 그렇게 읽히지 않았다. 이름을 바꾸는 일은 제목 한 줄이
#: 아니라 **그 이름을 부르는 모든 곳**을 옮기는 일이므로, 옛 이름의 잔존을
#: 판정식으로 봉인한다(링크 라벨 8곳 + e2e heading 셀렉터가 인용하고 있었다).
RETIRED_REPORTS_PAGE_TITLES: tuple[str, ...] = (
    "성적서 / 산출물",
    "Test Reports / Outputs",
    "Report Files & Artifacts",
)


class TestReportNumberIsServerDerived(unittest.TestCase):
    """S2/M4 — ``report_number`` 를 프론트에서 조립하지 않는다.

    ``S-{management_number}-{edition}`` 규칙의 SSOT 는 백엔드 도메인
    (``src/domain/services/report_number_policy.py``)이고, ``fcc_id`` 선례대로
    **DB 에 저장조차 하지 않는 파생값**이다. 백엔드에는 ``'S-{...}'`` 리터럴 0 을
    강제하는 AST 가드가 이미 있지만 그것은 ``src/`` 만 스캔한다. 프론트에 사본이
    생기면 같은 규칙이 Python/TS 두 언어로 쪼개져 조용히 드리프트한다 — 규칙이
    바뀌면 화면과 서버가 다른 번호를 말하게 되고, 그 순간 어느 쪽이 옳은지 알 수
    없다.

    스캔은 주석 제거 후 수행한다(주석에 적힌 규칙 설명이 검사를 만족시키는 false
    green 을 막는다). 검사 대상은 ``apps/web/src`` 전역 — 라우트만 보면 helper 로
    옮겨 심는 우회가 열린다.
    """

    #: 접두 조합: `` `S-${...}` `` / `'S-' + …` 처럼 번호를 문자열로 만들어 내는 형태.
    PREFIX_BUILD = re.compile(r"""["'`]S-\s*(?:\$\{|["'`]\s*\+)""")
    #: 관리번호 + edition 을 한 표현식에서 **문자열로 잇는** 형태(접두어가 없어도
    #: 파생은 파생이다). 두 이름이 그저 나란히 나오는 것(원인 토큰 union
    #: ``'edition' | 'managementNumber'`` 같은)은 결합이 아니므로, 사이에 실제 결합
    #: 연산(``${`` 보간 / ``+`` 연결 / ``.concat(``)이 있을 때만 잡는다.
    _JOIN_OP = r"(?:\$\{|\s\+\s|\.concat\()"
    MGMT_EDITION_JOIN = re.compile(
        rf"management_?[Nn]umber[^\n;]{{0,80}}?{_JOIN_OP}[^\n;]{{0,80}}?\bedition\b"
        rf"|\bedition\b[^\n;]{{0,80}}?{_JOIN_OP}[^\n;]{{0,80}}?management_?[Nn]umber"
    )

    #: 스캐너는 모듈 레벨 ``_offending_sites`` 단일 SSOT (W3-B S14 가 같은 골격을
    #: 재사용하며 승격 — 두 번째 사본을 만드는 대신 하나를 공유한다).
    _offenders = staticmethod(_offending_sites)

    def test_no_report_number_prefix_construction(self) -> None:
        self.assertEqual(
            self._offenders(self.PREFIX_BUILD),
            [],
            "성적서 번호를 프론트에서 조립한 흔적 — report_number 는 서버 파생값이며 "
            "응답 필드를 읽기만 해야 한다 (report_number_policy.py 가 SSOT)",
        )

    def test_no_management_number_edition_join(self) -> None:
        self.assertEqual(
            self._offenders(self.MGMT_EDITION_JOIN),
            [],
            "관리번호와 edition 을 한 표현식에서 결합한 흔적 — 파생 규칙이 프론트로 "
            "복제되면 Python/TS 두 SSOT 가 조용히 드리프트한다",
        )

    def test_the_scan_actually_detects_the_defect(self) -> None:
        """비-공허성: 패턴이 실제 위반 형태를 잡는지 합성 입력으로 확인한다."""
        for offending in (
            "const n = `S-${mgmt}-${edition}`;",
            "const n = 'S-' + mgmt;",
        ):
            with self.subTest(snippet=offending):
                self.assertRegex(offending, self.PREFIX_BUILD)
        for offending in (
            "const n = `${project.management_number}-${report.edition}`;",
            "const n = project.management_number + '-' + report.edition;",
        ):
            with self.subTest(snippet=offending):
                self.assertRegex(offending, self.MGMT_EDITION_JOIN)

        # 그리고 정상 형태는 잡지 않는다 — 응답 필드 읽기, 그리고 두 이름이 결합
        # 없이 나란히 놓이는 **원인 토큰** 표현(이 화면이 실제로 쓰는 형태).
        for legitimate in (
            "<span>{row.report_number}</span>",
            "export type Reason = 'edition' | 'managementNumber';",
            "reason(row.edition) === 'managementNumber'",
        ):
            with self.subTest(snippet=legitimate):
                self.assertNotRegex(legitimate, self.PREFIX_BUILD)
                self.assertNotRegex(legitimate, self.MGMT_EDITION_JOIN)

    def test_the_route_actually_consumes_the_server_field(self) -> None:
        """비-공허성의 반대 축.

        위 두 스캔은 "아무 것도 없으면" 통과한다. 화면이 ``report_number`` 를
        아예 안 보여 주면 금지 패턴 부재는 자동으로 성립하고 봉인은 공허해진다.
        서버 필드를 실제로 소비하고 있음을 함께 못박는다.
        """
        route = _strip_ts_comments(
            (SRC_DIR / "routes" / "test-reports.tsx").read_text(encoding="utf-8")
        )
        self.assertIn("report_number", route)
        self.assertIn("citationFieldState(row.report_number)", route)
        self.assertIn("citationFieldState(citation.report_number)", route)


class TestTestReportRouteReachability(unittest.TestCase):
    """S9/M5 — 화면이 존재하지만 갈 수 없으면 능력 미도달은 그대로다.

    라우터 등록 · 전역 nav 도달 · 경로 리터럴의 ``ROUTE_PATHS`` SSOT 경유를 한꺼번에
    본다. 셋 중 하나만 빠져도 "만들었는데 아무도 못 가는 화면"이 된다.
    """

    def test_the_route_module_exists(self) -> None:
        self.assertTrue((SRC_DIR / "routes" / "test-reports.tsx").is_file())

    def test_registered_in_the_router(self) -> None:
        app = _strip_ts_comments((SRC_DIR / "app.tsx").read_text(encoding="utf-8"))
        self.assertIn("@/routes/test-reports", app, "라우트 모듈이 lazy 등록되지 않았다")
        self.assertRegex(
            app,
            r"path:\s*'test-reports'",
            "라우터에 test-reports 경로가 등록되지 않았다",
        )

    def test_reachable_from_the_global_nav(self) -> None:
        app_shell = _strip_ts_comments(
            (SRC_DIR / APP_SHELL_NAVIGATION_MODULE).read_text(encoding="utf-8")
        )
        self.assertRegex(
            app_shell,
            r"\{\s*to:\s*ROUTE_PATHS\.testReports,\s*"
            r"labelKey:\s*'routes\.layout\.nav\.testReports',\s*end:\s*false,?\s*\}",
            "APP_SHELL_NAV_GROUPS에서 test-reports에 도달할 수 없다 — 라우터 등록만으로는 M5 미충족",
        )

    def test_path_literal_lives_in_the_route_paths_ssot(self) -> None:
        links = (SRC_DIR / "shared" / "route-links.ts").read_text(encoding="utf-8")
        self.assertRegex(links, r"testReports:\s*'/test-reports'")

        offenders = [
            f"{_rel(path)}"
            for path in _src_files()
            if _rel(path) not in TEST_REPORTS_PATH_LITERAL_ALLOWLIST
            and f"'{TEST_REPORTS_PATH}'" in _strip_ts_comments(
                path.read_text(encoding="utf-8")
            )
        ]
        self.assertEqual(
            offenders,
            [],
            "라우트 경로 리터럴이 SSOT 밖에 흩어졌다 — ROUTE_PATHS.testReports 를 "
            f"쓰라: {offenders}",
        )

    def test_the_route_paths_key_has_a_real_consumer(self) -> None:
        """``route-links.ts`` 의 규약: 읽는 곳 없는 키는 죽은 SSOT 다."""
        consumers = [
            _rel(path)
            for path in _src_files()
            if _rel(path) != "shared/route-links.ts"
            and "ROUTE_PATHS.testReports" in _strip_ts_comments(
                path.read_text(encoding="utf-8")
            )
        ]
        self.assertNotEqual(
            consumers,
            [],
            "ROUTE_PATHS.testReports 를 읽는 코드가 없다 — 키를 소비자 없이 추가하면 "
            "route-links 모듈이 막으려는 죽은 SSOT 가 된다",
        )

    def test_the_nav_label_distinguishes_it_from_the_generation_queue(self) -> None:
        """M5 — ``/reports``(성적서 파일 생성 요청 큐)와 사용자 눈에 구분돼야 한다.

        **부등호로는 부족하다** (2026-07-29 실측으로 배운 것). 초판은
        ``assertNotEqual`` 만 걸었고 ``'성적서'`` vs ``'성적서 대장'`` 은 그 검사를
        통과했다 — 그런데 한쪽이 다른 쪽의 **부분 문자열**이면 접근가능 이름으로
        한 링크를 지목할 방법이 없다. Playwright ``getByRole(name=…)`` 의 기본
        부분일치가 두 링크를 동시에 잡아 CI e2e 가 strict-mode violation 으로
        실패한 것이 그 증거이며, 그것은 셀렉터 문제가 아니라 **사람도 똑같이
        구분하지 못한다**는 제품 문제의 징후다. 그래서 봉인을 "서로 부분 문자열이
        아니다"로 격상한다 — 이것이 M5 가 실제로 요구한 성질이다.
        """
        app_shell = (SRC_DIR / APP_SHELL_NAVIGATION_MODULE).read_text(encoding="utf-8")
        self.assertIn("ROUTE_PATHS.testReports", app_shell)
        self.assertIn("routes.layout.nav.testReports", app_shell)
        for locale in ("ko", "en"):
            bundle = json.loads(
                (SRC_DIR / "locales" / f"{locale}.json").read_text(encoding="utf-8")
            )
            nav = bundle["routes"]["layout"]["nav"]
            with self.subTest(locale=locale):
                self.assertIn("testReports", nav)
                register = nav["testReports"].strip()
                queue = nav["reports"].strip()
                self.assertNotEqual(
                    register,
                    queue,
                    "두 성적서 화면이 같은 라벨을 쓰면 사용자가 구분할 수 없다",
                )
                self.assertNotIn(
                    queue,
                    register,
                    f"{locale}: '{queue}' 가 '{register}' 의 부분 문자열이다 — "
                    "접근가능 이름으로 한쪽만 지목할 수 없고 사용자도 구분하지 못한다",
                )
                self.assertNotIn(
                    register,
                    queue,
                    f"{locale}: '{register}' 가 '{queue}' 의 부분 문자열이다 — "
                    "접근가능 이름으로 한쪽만 지목할 수 없고 사용자도 구분하지 못한다",
                )

    def test_the_page_title_carries_the_same_property_as_the_nav_label(self) -> None:
        """S8 — nav 라벨만 고치고 **제목**을 두면 같은 사고가 h1 에서 재발한다.

        nav 는 W3-A 에서 상환됐는데 ``/reports`` 의 제목은 여전히 도메인을
        오도했다(`성적서 / 산출물` — 실제로는 성적서 파일 **생성 요청 큐**).
        e2e 가 heading 을 접근가능 이름으로 지목하므로 부분 문자열 관계는
        nav 와 똑같이 strict-mode violation 을 만든다. 같은 성질을 제목에도
        건다: 두 화면의 {제목, nav 라벨} 이 **교차로** 부분 문자열이면 red.
        (같은 화면의 nav ⊂ 제목 은 오히려 정합이므로 대상이 아니다.)
        """
        for locale in ("ko", "en"):
            bundle = json.loads(
                (SRC_DIR / "locales" / f"{locale}.json").read_text(encoding="utf-8")
            )
            routes = bundle["routes"]
            nav = routes["layout"]["nav"]
            queue_names = {
                "reports.page.title": routes["reports"]["page"]["title"].strip(),
                "nav.reports": nav["reports"].strip(),
            }
            register_names = {
                "testReports.title": routes["testReports"]["title"].strip(),
                "nav.testReports": nav["testReports"].strip(),
            }
            for queue_key, queue_name in queue_names.items():
                for register_key, register_name in register_names.items():
                    with self.subTest(locale=locale, pair=(queue_key, register_key)):
                        self.assertNotIn(
                            queue_name,
                            register_name,
                            f"{locale}: {queue_key}='{queue_name}' 가 "
                            f"{register_key}='{register_name}' 의 부분 문자열이다",
                        )
                        self.assertNotIn(
                            register_name,
                            queue_name,
                            f"{locale}: {register_key}='{register_name}' 가 "
                            f"{queue_key}='{queue_name}' 의 부분 문자열이다",
                        )

    def test_the_retired_queue_title_is_quoted_nowhere(self) -> None:
        """이름을 바꾸면서 옛 이름의 인용을 남기면 **새 드리프트**가 된다.

        옛 제목은 링크 라벨 8곳과 e2e heading 셀렉터가 그대로 인용하고 있었다.
        제목만 갈아 끼웠다면 화면은 새 이름을 쓰는데 그리로 가는 링크는 옛
        이름으로 부르는 상태가 됐을 것이다(그리고 e2e 는 조용히 깨진다).
        """
        offenders: list[str] = []
        for path in sorted(WEB_ROOT.rglob("*")):
            if not path.is_file() or path.suffix not in {
                ".ts", ".tsx", ".json", ".html", ".css",
            }:
                continue
            posix = path.as_posix()
            if "/node_modules/" in posix or "/dist/" in posix or "/generated/" in posix:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for retired in RETIRED_REPORTS_PAGE_TITLES:
                if retired in text:
                    offenders.append(f"{path.relative_to(WEB_ROOT).as_posix()}: {retired}")
        self.assertEqual(
            offenders,
            [],
            f"은퇴한 /reports 제목이 아직 인용되고 있다: {offenders}",
        )

    def test_the_e2e_heading_selector_tracks_the_current_title(self) -> None:
        """옛 이름 봉인의 **반대 축** — *현재* 이름을 e2e 가 따라오는지 본다.

        e2e 는 heading 을 **접근가능 이름**으로 지목하므로 제목 문자열이 스펙에
        박힐 수밖에 없다(셀렉터의 본질이라 제거 대상이 아니다). 문제는 그 사본을
        **아무도 지키지 않는다**는 것이다: ``RETIRED_REPORTS_PAGE_TITLES`` 는 *옛*
        이름의 잔존만 잡으므로, 3번째 개명에서 옛 이름을 깨끗이 걷어내고 새 이름을
        locale 에만 넣으면 두 봉인 모두 green 인데 **e2e 만 조용히 stale** 이 된다
        (런타임에 가서야 heading 을 못 찾고 실패한다).

        그래서 제목을 pytest 에 다시 적지 않고 **locale 값에서 파생**해 대조한다 —
        사본을 3개로 늘리지 않으면서 개명 누락이 정적 판정으로 바뀐다.
        """
        spec = (
            WEB_ROOT / "tests" / "e2e" / "reports-workflow.spec.ts"
        ).read_text(encoding="utf-8")
        heading_names = set(
            re.findall(r"getByRole\(\s*'heading',\s*\{\s*name:\s*'([^']+)'", spec)
        )
        title = json.loads(
            (SRC_DIR / "locales" / "ko.json").read_text(encoding="utf-8")
        )["routes"]["reports"]["page"]["title"].strip()
        self.assertIn(
            title,
            sorted(heading_names),
            f"e2e heading 셀렉터가 현재 /reports 제목('{title}')을 지목하지 않는다 — "
            "제목을 바꾸면서 스펙을 함께 옮기지 않았거나 셀렉터가 사라졌다. 런타임에 "
            f"가서야 드러나는 종류의 드리프트다: {sorted(heading_names)}",
        )


class TestNullableRefArtifactDefect(unittest.TestCase):
    """OpenAPI 아티팩트의 nullable-$ref 표현 결함 — **재발 금지** 봉인.

    (W3-A 에서 **부채 기록**으로 태어났고, openapi-nullable-ref-oneof 에서 정공이
    끝나 2026-07-30 재발 금지로 격상됐다. 클래스명은 유지한다 — 이 이름을
    가리키는 계획·계약·부채 문서가 여럿이다.)

    한때 백엔드 OpenAPI 빌더가 "nullable 객체 필드"를
    ``{"allOf": [{"$ref": …}], "type": ["null"]}`` 로 emit 했다. JSON Schema 에서
    형제 키워드는 conjunctive 라 아티팩트가 말한 것은 ``T AND null`` — 만족값이
    없는 교집합이다. ``openapi-typescript`` 는 정직하게 ``null & T`` 를 렌더하고
    TypeScript 는 그것을 ``never`` 로 붕괴시켰다. 와이어는 실제로 "객체 또는
    null" 인데(``src/domain/services/report_citation.py::_latest_firmware``) 그
    필드를 읽는 **모든** 코드가 컴파일 에러였다 — 런타임엔 절대 안 드러나고
    미래 소비자에게만 나타나는 종류의 결함이다.

    W3-A 는 쓰기 범위가 프론트뿐이라 정공을 못 했고, ``platform-client.ts`` 에
    ``reportSampleFirmware`` 라는 좁히기 접근자를 두는 대신 그 우회가 이유보다
    오래 살지 못하도록 "우회 존재 ↔ 결함 존재" 동치로 못박아 두었다.

    정공은 ``normalize_nullable`` 이 합집합(``anyOf: [schema, {"type":"null"}]``)
    을 emit 하게 한 것이고, 그 순간 위 동치가 red 가 되어 우회 제거를 강제했다 —
    설계대로 작동한 셈이다. 우회는 사라졌고 뷰는 ``sample.latest_firmware`` 를
    직접 읽는다.

    그래서 ``_KNOWN`` ratchet 은 이제 **빈 집합**이고, 판정은 양방향이다:
    결함 형상이 아티팩트에 **하나라도** 나타나면 실패하고, ``_KNOWN`` 에 항목이
    남아 있는데 실물에 없어도 실패한다(죽은 ratchet 방지). 탐지는 최상위
    ``properties`` 가 아니라 **3 아티팩트 재귀 전수**다 — 결함은 빌더에 있었고
    빌더는 문서 어디에나 쓰므로, 인라인 요청/응답 스키마와 ``items``/``$defs``
    안쪽까지 봐야 다음 재발을 놓치지 않는다.

    생성기 단위 봉인은 ``tests/test_architecture_conformance.py``
    ``::TestNullableNormalisesToAUnion``, 백엔드 아티팩트 5종 스캔은
    ``tests/test_api_contract_artifact_phase25.py``
    ``::TestNullableIsEmittedAsAUnionNotAnIntersection`` 가 소유한다. 여기는
    **소비자 관점** — 프론트가 실제로 읽는 필드에 앵커해 우회·캐스트가
    되살아나지 않음까지 함께 본다.
    """

    #: 프론트 codegen 이 소비하는 아티팩트 3종. 결함은 표면을 가리지 않는다.
    _ARTIFACTS: tuple[str, ...] = (
        "platform-api.openapi.json",
        "headless-api.openapi.json",
        "session-api.openapi.json",
    )

    _ARTIFACT = PROJECT_ROOT / "docs" / "api" / "platform-api.openapi.json"

    #: 알려진-결함 ratchet. **비어 있는 것이 정상이다.** 항목을 늘리는 것은
    #: 결함을 승인하는 것이고, 이 형상은 승인 가능한 종류가 아니다.
    _KNOWN: frozenset[tuple[str, str]] = frozenset()

    #: 프론트가 실제로 읽는 nullable 객체 필드. (스키마, 필드, 대상 스키마)
    _CONSUMED: tuple[tuple[str, str, str], ...] = (
        ("ReportSampleCitation", "latest_firmware", "FirmwareCitationEnvelope"),
    )

    #: 한때 결함이었으나 소비자가 없어 드러나지 않았던 형제 필드. 같은 빌더
    #: 결함의 산물이므로 함께 지킨다 — 소비자가 생기는 날 red 로 알게 되면 늦다.
    _SIBLINGS: tuple[tuple[str, str, str], ...] = (
        ("ChamberAvailabilityEnvelope", "progress", "ChamberSessionProgress"),
        ("ChamberHeartbeatRequest", "progress", "ChamberSessionProgress"),
    )

    def _schemas(self) -> dict:
        return json.loads(self._ARTIFACT.read_text(encoding="utf-8"))["components"][
            "schemas"
        ]

    @staticmethod
    def _scan(document: object, artifact: str) -> set[tuple[str, str]]:
        """``type: ["null"]`` 을 **형제 제약과 함께** 이고 있는 노드 전부.

        반환값은 ``(아티팩트, JSON pointer)`` 집합이다. 단독
        ``{"type": ["null"]}`` (형제 키 없음)은 "null 만 허용" 이라는 합법적
        선언이므로 제외한다 — 그것까지 잡으면 오탐이 되어 봉인이 무뎌진다.
        """
        found: set[tuple[str, str]] = set()

        def walk(node: object, pointer: str) -> None:
            if isinstance(node, dict):
                if node.get("type") == ["null"] and any(k != "type" for k in node):
                    found.add((artifact, pointer or "/"))
                for key, child in node.items():
                    walk(child, f"{pointer}/{key}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    walk(child, f"{pointer}[{index}]")

        walk(document, "")
        return found

    def _defective_fields(self) -> set[tuple[str, str]]:
        found: set[tuple[str, str]] = set()
        for artifact in self._ARTIFACTS:
            document = json.loads(
                (PROJECT_ROOT / "docs" / "api" / artifact).read_text(encoding="utf-8")
            )
            found |= self._scan(document, artifact)
        return found

    def test_the_defect_never_re_appears(self) -> None:
        """``_KNOWN`` 이 빈 집합이므로 이것은 **재발 금지**다."""
        offenders = sorted(self._defective_fields() - self._KNOWN)
        self.assertEqual(
            offenders,
            [],
            "nullable 이 교집합으로 emit 됐다 — `type: [null]` 은 형제 키워드와 "
            "conjunctive 라 생성 TS 타입이 `never` 로 붕괴하고, 이 필드를 읽는 "
            "모든 코드가 컴파일 에러가 된다. 선언(api_contracts.py)을 손보지 "
            f"말고 `normalize_nullable` 을 보라: {offenders}",
        )

    def test_the_ratchet_does_not_outlive_its_reason(self) -> None:
        """양방향 — 실물에 없는 항목이 ratchet 에 남아 있으면 그것도 실패다.

        단방향(``found - _KNOWN``)만 보면 결함이 고쳐진 뒤에도 green 이라
        ratchet 이 조용히 화석이 된다. 이 검사가 있어야 ``_KNOWN`` 이 빈 집합에
        머물도록 강제된다.
        """
        stale = sorted(self._KNOWN - self._defective_fields())
        self.assertEqual(
            stale,
            [],
            "알려진-결함 목록에 이미 사라진 항목이 남아 있다 — `_KNOWN` 에서 "
            f"지워라(빈 집합이 정상이다): {stale}",
        )

    def test_all_three_artifacts_are_actually_scanned(self) -> None:
        """스캔이 "파일이 없어서" 통과하는 것이 아님을 못박는다."""
        for artifact in self._ARTIFACTS:
            path = PROJECT_ROOT / "docs" / "api" / artifact
            with self.subTest(artifact=artifact):
                self.assertTrue(path.is_file(), f"아티팩트가 없다: {artifact}")
                self.assertIsInstance(
                    json.loads(path.read_text(encoding="utf-8")),
                    dict,
                    "재귀 walk 이 dict 를 기대한다",
                )

    def test_the_detector_fires_on_an_injected_defect(self) -> None:
        """봉인 비-공허성 — 결함을 인위로 재주입하면 탐지된다.

        위 재발-금지 검사는 결함이 0 이라 항상 green 이다. 그것이 "정말 없어서"
        인지 "탐지기가 죽어서" 인지는 실물만 봐서는 구별되지 않으므로, 합성
        offender 로 탐지기 자체를 검증한다. 이 검사가 있으면 red→green 실증이
        일회성 수작업이 아니라 **영구 회귀 가드**가 된다.

        중첩 위치(``items`` 안쪽)를 고른 것은 의도다 — 옛 검사가 최상위
        ``properties`` 만 훑어서 놓쳤을 자리다.
        """
        poisoned = {
            "components": {
                "schemas": {
                    "Healthy": {"anyOf": [{"$ref": "#/x"}, {"type": "null"}]},
                    "Legal": {"type": ["null"]},  # 형제 제약 없음 = 합법
                    "Rotten": {
                        "properties": {
                            "rows": {
                                "items": {
                                    "allOf": [{"$ref": "#/x"}],
                                    "type": ["null"],
                                }
                            }
                        }
                    },
                }
            }
        }
        self.assertEqual(
            sorted(self._scan(poisoned, "synthetic")),
            [
                (
                    "synthetic",
                    "/components/schemas/Rotten/properties/rows/items",
                )
            ],
            "탐지기가 죽었다 — 중첩된 교집합 노드를 못 잡거나, 합법적인 단독 "
            "`type: [null]` 을 오탐하고 있다",
        )

    def test_every_consumed_nullable_object_field_is_a_union(self) -> None:
        """소비자 관점 비-공허성: 문제의 필드들이 실제로 union 형상이다.

        재귀 스캔은 "결함이 없다"만 말한다 — 필드가 통째로 사라져도 green 이다.
        그래서 형상을 직접 못박는다. 첫 분기가 ``{"allOf": [$ref]}`` 인 것은
        의도로, 정규화기가 원본 스키마를 평탄화 없이 통째로 분기에 넣기 때문이다
        (단일 멤버 ``allOf`` 특례를 두면 멤버 수를 sniff 하는 두 번째 규칙이
        생기고 다중 멤버에는 적용될 수 없다).
        """
        schemas = self._schemas()
        for schema_name, field, target in self._CONSUMED + self._SIBLINGS:
            with self.subTest(schema=schema_name, field=field):
                self.assertEqual(
                    schemas[schema_name]["properties"][field],
                    {
                        "anyOf": [
                            {"allOf": [{"$ref": f"#/components/schemas/{target}"}]},
                            {"type": "null"},
                        ]
                    },
                    "교집합 관용구가 되살아났다 — 생성 TS 타입이 `never` 로 "
                    "붕괴해 이 필드를 읽는 모든 코드가 컴파일 에러가 된다. "
                    "선언을 손보지 말고 `normalize_nullable` 을 보라.",
                )

    def test_no_narrowing_workaround_survives(self) -> None:
        """우회는 결함과 함께 사라져야 한다 — 이유 없는 우회는 그냥 부채다."""
        client = (SRC_DIR / "api" / "platform-client.ts").read_text(encoding="utf-8")
        self.assertNotIn(
            "export function reportSampleFirmware(",
            client,
            "결함이 고쳐졌는데 좁히기 접근자가 남아 있다 — 뷰가 "
            "`sample.latest_firmware` 를 직접 읽게 하고 접근자를 지워라",
        )

    def test_the_view_reads_the_generated_field_directly(self) -> None:
        """비-공허성 — 위 두 단언이 "아무도 안 읽어서" 통과하는 것이 아님을 못박는다."""
        readers = [
            _rel(path)
            for path in _src_files()
            if "latest_firmware" in _strip_ts_comments(path.read_text(encoding="utf-8"))
        ]
        self.assertIn(
            "routes/test-reports.tsx",
            readers,
            "성적서 인용 화면이 펌웨어 필드를 더 이상 읽지 않는다 — 이 봉인이 "
            f"지키는 대상이 사라졌다는 뜻이므로 봉인부터 재검토하라: {readers}",
        )

    def test_the_field_is_never_cast_back(self) -> None:
        """부채를 옮기지 마라 — ``as`` 로 다시 좁히면 우회를 지운 의미가 없다."""
        pattern = re.compile(r"latest_firmware[^;\n]*\bas\b")
        offenders = [
            _rel(path)
            for path in _src_files()
            if pattern.search(_strip_ts_comments(path.read_text(encoding="utf-8")))
        ]
        self.assertEqual(
            offenders,
            [],
            "`latest_firmware` 를 `as` 로 다시 단언한다 — 생성 타입이 이미 "
            f"`FirmwareCitationEnvelope | null` 이므로 캐스트가 필요 없다: {offenders}",
        )


# ─────────────────────────────────────────────────────────────────────────────
# W3-B — 프로젝트 표지 메타 편집 + 서버측 프로젝트 디렉토리
# (fe-w3-b-project-meta-directory, 2026-07-30)
#
# 이 웨이브가 남기는 두 규칙은 **위반이 typecheck 와 vitest 를 모두 통과하는**
# 종류다: `fcc_id` 를 TS 에서 조립해도 컴파일되고, 라우트가 자기 `<option>` 목록을
# 다시 만들어도 그 라우트의 테스트는 green 이다. 부재를 증명하는 것은 스캔뿐이다.
# ─────────────────────────────────────────────────────────────────────────────

#: 프로젝트 선택기의 단일 SSOT 컨테이너 — 6 라우트가 공유하는 데이터 바인딩.
PROJECT_SELECTOR_SSOT_MODULE = "shared/ProjectSelectField.tsx"

#: 디렉토리 읽기 helper(`fetchProjects` / `fetchProjectsPage`)를 이름으로 언급해도
#: 되는 모듈.
#:
#: ***이것은 부채 allowlist 가 아니다 — 규칙의 정의(定義)다.***
#:
#: 위쪽 `PERCENT_FORMAT_ALLOWLIST` 의 ratchet-down 문구를 여기에 베껴 오지 마라.
#: 저기서는 항목 하나하나가 "아직 못 고친 곳"이라 0 을 향해 줄어드는 것이 목표지만,
#: 여기서는 세 항목이 "규칙상 디렉토리를 읽는 것이 존재 이유인 곳" 전부다. 항목을
#: 빼면 그 모듈이 자기 정의를 위반하게 되고(예: 정의부를 빼면 규칙이 자기 자신을
#: 금지한다), 넣으면 **네 번째 화면이 서버가 하는 일을 다시 하기 시작한 것**이다.
#: 그러니 이 집합은 "줄이면 좋은 것"이 아니라 **바뀌면 안 되는 것**이다.
PROJECT_DIRECTORY_FETCH_OWNERS: frozenset[str] = frozenset(
    {
        # 두 helper 의 정의부 그 자체. 여기를 빼면 규칙이 자기 자신을 금지한다.
        "api/platform-client.ts",
        # 선택기 SSOT. 6 라우트의 프로젝트 선택이 여기 한 곳으로 모이는 것이
        # 이 웨이브(M-C)가 D4 의 거짓말을 한 번에 지울 수 있었던 이유다.
        PROJECT_SELECTOR_SSOT_MODULE,
        # 디렉토리 화면 그 자체(검색 + keyset 이어 읽기). 목록을 읽는 것이
        # 이 라우트의 존재 이유이므로 위임할 상대가 없다.
        "routes/my-projects.tsx",
    }
)

#: 선택기를 소비하는 라우트 — **실측 6**.
#:
#: 계약 §M6/§5-5 는 "5 라우트"라고 적었으나 실측은 6 이다(정정은 평가서에 기록).
#: 계약 수치를 조용히 따라갔다면 라우트 하나가 봉인 밖으로 샜을 것이다.
#: 아래 `test_the_consumer_list_is_exhaustive` 가 이 tuple 을 실제 트리와 **양방향**
#: 으로 대조하므로, 일곱 번째 라우트가 선택기를 쓰기 시작하면 red 가 된다.
PROJECT_SELECTOR_CONSUMER_ROUTES: tuple[str, ...] = (
    "routes/chambers/MeasurementStarter.tsx",
    "routes/inventory/index.tsx",
    "routes/membership.tsx",
    "routes/projects.tsx",
    "routes/test-plans/TestPlansWorkbench.tsx",
    "routes/test-reports.tsx",
    "routes/equipment-lists.tsx",
)


class TestFccIdIsServerDerived(unittest.TestCase):
    """S14 — ``fcc_id`` 를 프론트에서 조립하지 않는다.

    ``fcc_id = grantee_code + product_code(model_name)`` 의 SSOT 는 백엔드 도메인
    (``src/domain/services/fcc_id_policy.py``)이고, ``report_number`` 선례와 똑같이
    **DB 에 저장조차 하지 않는 파생값**이다. 정규화(``_NON_ALNUM.sub('', model)``
    + ``.upper()``)까지 그 규칙의 일부다.

    grantee code 입력 옆에 "이렇게 될 겁니다" 미리보기를 만들고 싶은 유혹이 크고,
    그 순간 정규화 규칙이 Python/TS 두 언어로 쪼개져 조용히 드리프트한다 — 규칙이
    바뀌면 화면과 서버가 **다른 FCC ID** 를 말하고, 어느 쪽이 옳은지 알 수 없다.
    바로 위 ``TestReportNumberIsServerDerived`` 가 W3-A 에서 같은 함정을 봉인한
    직접적 선례이며, 이 클래스는 그 4축 골격의 ``fcc_id`` 쌍둥이다.

    **예외 목록이 없다.** 현재 트리 offender 0 을 정규식을 좁혀서 달성했지, 예외를
    추가해서 달성하지 않았다 — 봉인이 예외 목록을 들고 태어나면 그 순간 부채다.
    """

    #: 실제 결합 연산. 두 이름이 결합 없이 나란히 놓이는 정당한 형태(필드 목록 ·
    #: witness 리터럴)를 잡지 않기 위해 선례 ``MGMT_EDITION_JOIN`` 과 같은 설계를
    #: 쓴다 — 줄/문장 경계(``[^\n;]``)로 창을 제한하는 것도 같은 이유다.
    _JOIN_OP = r"(?:\$\{|\s\+\s|\.concat\()"
    #: ``grantee`` 는 이 코드베이스에서 FCC ID 문맥에만 등장하는 판별 토큰이다
    #: (``fcc_grantee_code`` / ``granteeCode`` / ``granteeInput`` …).
    _GRANTEE = r"[Gg]rantee"
    #: ``model_name`` / ``modelName`` / ``trimmedModel`` 을 모두 덮는다 — 파생을
    #: 지역 변수로 한 단계 옮기는 우회를 열어 두지 않기 위해서다.
    _MODEL = r"\w*[Mm]odel\w*"

    #: grantee code 와 모델명을 한 표현식에서 **문자열로 잇는** 형태.
    GRANTEE_MODEL_JOIN = re.compile(
        rf"{_GRANTEE}[^\n;]{{0,80}}?{_JOIN_OP}[^\n;]{{0,80}}?{_MODEL}"
        rf"|{_MODEL}[^\n;]{{0,80}}?{_JOIN_OP}[^\n;]{{0,80}}?{_GRANTEE}"
    )

    #: ``product_code`` 정규화 규칙(비-영숫자 제거 + 대문자화)의 TS 사본. 접두
    #: 조합이 없어도 이 두 연산이 model/grantee 문맥에서 함께 나오면 그것은 백엔드
    #: 정규화의 재구현이다. 순서는 양방향 모두 잡는다.
    _ALNUM_STRIP = r"\.replace\s*\(\s*/\[\^[^\]\n]*0-9[^\]\n]*\]"
    _UPPER = r"\.toUpperCase\s*\(\s*\)"
    PRODUCT_CODE_NORMALIZE = re.compile(
        rf"(?:{_MODEL}|{_GRANTEE})[^\n;]{{0,140}}?"
        rf"(?:{_ALNUM_STRIP}[^\n;]{{0,140}}?{_UPPER}"
        rf"|{_UPPER}[^\n;]{{0,140}}?{_ALNUM_STRIP})"
    )

    def test_no_grantee_model_join(self) -> None:
        self.assertEqual(
            _offending_sites(self.GRANTEE_MODEL_JOIN),
            [],
            "grantee code 와 모델명을 한 표현식에서 결합한 흔적 — fcc_id 는 서버 "
            "파생값이며 프론트는 `ProjectEnvelope.fcc_id` 를 읽기만 해야 한다 "
            "(fcc_id_policy.py 가 SSOT)",
        )

    def test_no_product_code_normalization_copy(self) -> None:
        self.assertEqual(
            _offending_sites(self.PRODUCT_CODE_NORMALIZE),
            [],
            "product_code 정규화(비-영숫자 제거 + 대문자화)를 프론트에서 재구현한 "
            "흔적 — 규칙이 Python/TS 로 쪼개지면 조용히 드리프트한다",
        )

    def test_the_scan_actually_detects_the_defect(self) -> None:
        """비-공허성: 패턴이 실제 위반 형태를 잡는지 합성 입력으로 확인한다."""
        for offending in (
            "const preview = `${draft.fcc_grantee_code}${project.model_name}`;",
            "const preview = createMeta.fcc_grantee_code + trimmedModel;",
            "const preview = granteeCode.concat(modelName);",
            "const preview = `${modelName}` + grantee;",
        ):
            with self.subTest(snippet=offending):
                self.assertRegex(offending, self.GRANTEE_MODEL_JOIN)

        for offending in (
            "const code = model_name.replace(/[^A-Za-z0-9]+/g, '').toUpperCase();",
            "const code = modelName.toUpperCase().replace(/[^A-Z0-9]/g, '');",
        ):
            with self.subTest(snippet=offending):
                self.assertRegex(offending, self.PRODUCT_CODE_NORMALIZE)

    def test_the_scan_does_not_flag_legitimate_forms(self) -> None:
        """비-공허성의 짝: 현재 트리가 **실제로 쓰는** 형태는 잡지 않는다.

        여기 열거한 셋은 합성 예시가 아니라 트리에 실존하는 코드 형태다 — 서버
        파생 필드 읽기, witness 리터럴, 그리고 생성 본문의 diff 스프레드.
        이 단정이 없으면 다음 세션이 정규식을 넓히다가 봉인을 못 쓰게 만들고,
        그때 나오는 처방이 "allowlist 를 추가하자"가 된다.
        """
        for legitimate in (
            "{t('routes.myProjects.list.fccLabel', { value: project.fcc_id })}",
            "  fcc_grantee_code: true,",
            "body: { model_name: input.modelName, ...buildProjectMetaPatch(a, b) }",
            "['fccId', citation.fcc_id],",
            "label = base.toUpperCase();",
        ):
            with self.subTest(snippet=legitimate):
                self.assertNotRegex(legitimate, self.GRANTEE_MODEL_JOIN)
                self.assertNotRegex(legitimate, self.PRODUCT_CODE_NORMALIZE)

    def test_the_screens_actually_consume_the_server_field(self) -> None:
        """비-공허성의 반대 축.

        위 두 스캔은 "아무 것도 없으면" 통과한다. 화면이 ``fcc_id`` 를 아예 안
        보여 주면 금지 패턴 부재는 자동으로 성립하고 봉인은 공허해진다 — 그리고
        그 상태야말로 이 웨이브가 고친 결함(D1/D2: FCC ID 가 영구 공란)의 재발이다.
        서버 파생 필드를 실제로 소비하고 있음을 함께 못박는다.
        """
        for rel, needle in (
            ("routes/my-projects.tsx", "project.fcc_id"),
            ("routes/test-reports.tsx", "citation.fcc_id"),
        ):
            with self.subTest(module=rel):
                path = SRC_DIR / rel
                self.assertTrue(path.is_file(), f"소비 화면이 사라졌다: {rel}")
                code = _strip_ts_comments(path.read_text(encoding="utf-8"))
                self.assertIn(
                    needle,
                    code,
                    f"{rel} 이 서버 파생 `fcc_id` 를 더 이상 읽지 않는다 — 그러면 "
                    "위 금지-패턴 스캔은 공허하게 통과한다",
                )


class TestProjectSelectorRemainsSingleSsot(unittest.TestCase):
    """S15 — 프로젝트 선택기가 단일 SSOT 로 유지된다(라우트별 사본 0).

    ``shared/ProjectSelectField.tsx`` 는 6 라우트가 공유하는 단일 컨테이너다.
    M-C 가 이 한 곳에 서버측 검색(``q``)과 **잔여 고지**를 넣었기 때문에 D4 의
    거짓말 — 전량 로드해 놓고 못 찾으면 "없다"고 결론짓게 만드는 것 — 이 여섯
    화면에서 한꺼번에 사라졌다.

    어느 라우트가 자기 ``<option>`` 목록을 다시 만들면 **그 화면에서만 거짓말이
    되살아난다**(전량 로드 + 잔여 미고지). 그것은 정확히 "서버가 하는 일을
    클라이언트가 다시 함"의 재발이고, 라우트 로컬이라 그 라우트의 테스트는 계속
    green 이다. 그래서 부재를 여기서 구조적으로 본다.
    """

    #: 선택기 리프의 **소비**(정의부 ``api/query-config.ts`` 는 이 형태로 자기
    #: 자신을 부르지 않으므로 자연히 제외된다).
    PICKER_LEAF_CALL_RE = re.compile(r"queryKeys\.project\.pickerOptions\s*\(")
    #: 디렉토리 읽기 helper 를 **이름으로 언급**하는 모든 형태(호출 · import ·
    #: 별칭 재수출). 호출만 보면 ``import { fetchProjectsPage as f }`` 우회가 열린다.
    DIRECTORY_FETCH_RE = re.compile(r"\bfetchProjects(?:Page)?\b")
    #: 선택기 import 바인딩. 단순 substring 은 **주석**이 만족시킨다
    #: (``ui/ProjectPicker.tsx`` 와 ``api/query-config.ts`` 가 이름으로 언급한다).
    SELECTOR_IMPORT_RE = re.compile(
        r"import\s*\{[^}]*\bProjectSelectField\b[^}]*\}\s*from\s*['\"][^'\"]+['\"]"
    )

    def test_picker_query_leaf_has_exactly_one_consumer(self) -> None:
        """캐시 리프를 읽는 곳이 하나여야 선택기가 하나다.

        여섯 화면이 같은 ``queryKeys.project.pickerOptions(q)`` 항목을 공유하는
        덕에 선택기를 여섯 번 마운트해도 요청은 **한 번**이다. 두 번째 소비자가
        생겼다는 것은 그 dedupe 밖에서 목록을 따로 읽기 시작했다는 뜻이다.
        """
        sites = _offending_sites(self.PICKER_LEAF_CALL_RE)
        self.assertEqual(
            [site.rsplit(":", 1)[0] for site in sites],
            [PROJECT_SELECTOR_SSOT_MODULE],
            "프로젝트 선택기 캐시 리프의 소비자가 정확히 하나가 아니다 — 선택기 "
            f"SSOT 밖에서 옵션 목록을 읽고 있다: {sites}",
        )

    def test_directory_fetch_stays_inside_the_owner_set(self) -> None:
        offenders = sorted(
            {
                site.rsplit(":", 1)[0]
                for site in _offending_sites(self.DIRECTORY_FETCH_RE)
            }
            - PROJECT_DIRECTORY_FETCH_OWNERS
        )
        self.assertEqual(
            offenders,
            [],
            "선택기 SSOT · 디렉토리 라우트 · helper 정의부 밖에서 프로젝트 디렉토리를 "
            "직접 읽는다 — 그 화면에서만 D4 의 거짓말(전량 로드 + 잔여 미고지)이 "
            f"되살아난다. `ProjectSelectField` 를 쓰라: {offenders}",
        )

    def test_the_owner_set_is_the_rule_not_a_debt_list(self) -> None:
        """세 항목이 **전부 살아 있는 정의**임을 확인한다.

        죽은 항목이 남아 있으면 집합이 슬그머니 "면제 목록"으로 변질된다 — 다음
        세션은 이미 4개인 목록에 5번째를 더하는 것을 자연스럽게 느낀다.
        """
        self.assertEqual(len(PROJECT_DIRECTORY_FETCH_OWNERS), 3)
        for rel in sorted(PROJECT_DIRECTORY_FETCH_OWNERS):
            with self.subTest(module=rel):
                path = SRC_DIR / rel
                self.assertTrue(path.is_file(), f"인가 모듈이 사라졌다: {rel}")
                code = _strip_ts_comments(path.read_text(encoding="utf-8"))
                self.assertRegex(
                    code,
                    self.DIRECTORY_FETCH_RE,
                    f"{rel} 은 더 이상 디렉토리를 읽지 않는다 — 인가 목록에서 빼라 "
                    "(죽은 항목은 면제 목록으로 변질된다)",
                )

    def test_the_consumer_list_is_exhaustive(self) -> None:
        """tuple ↔ 실제 트리 **양방향** 대조.

        한 방향만 보면 반쪽이다: tuple 쪽만 확인하면 일곱 번째 라우트가 조용히
        늘어도 green 이고, 트리 쪽만 확인하면 tuple 이 비어도 green 이다.
        """
        declared = set(PROJECT_SELECTOR_CONSUMER_ROUTES)
        self.assertEqual(
            len(PROJECT_SELECTOR_CONSUMER_ROUTES),
            len(declared),
            "소비 라우트 목록에 중복이 있다",
        )
        actual = {
            _rel(path)
            for path in _src_files()
            if _rel(path).startswith("routes/")
            and self.SELECTOR_IMPORT_RE.search(
                _strip_ts_comments(path.read_text(encoding="utf-8"))
            )
        }
        self.assertEqual(
            actual,
            declared,
            "선택기 소비 라우트 집합이 선언과 다르다 — 새 소비자가 생겼다면 tuple 에 "
            "추가하고, 사라졌다면 그 화면이 어떻게 프로젝트를 고르는지 확인하라",
        )
        # 비-공허성만 남긴다. 완전성은 바로 위 `actual == declared` 가 답하고,
        # 개수를 다시 적으면 라우트가 하나 늘 때 **두 곳**을 고쳐야 한다 — 그리고
        # 한 곳만 고친 상태가 곧 이 검사가 오늘의 배치를 굳히는 순간이다.
        self.assertGreater(len(declared), 0, "선언 집합이 비었다 — 위 상등이 공허하다")
        for rel in sorted(declared):
            with self.subTest(route=rel):
                self.assertTrue((SRC_DIR / rel).is_file(), f"라우트 부재: {rel}")

    def test_the_scans_are_comment_aware(self) -> None:
        """비-공허성: 스캔이 실제 위반을 잡고, 주석은 잡지 않는다.

        이 트리에는 ``ProjectSelectField.tsx`` 도입부에 ``fetchProjects`` 를
        **이름으로 언급하는 주석**이 있고(왜 그 helper 를 더는 쓰지 않는지),
        ``ui/ProjectPicker.tsx`` 에는 ``ProjectSelectField`` 를 언급하는 주석이
        있다. strip 이 빠지면 규칙을 설명하는 문서가 위반으로 잡힌다.
        """
        offending = "const page = await fetchProjectsPage('active', q);"
        self.assertRegex(offending, self.DIRECTORY_FETCH_RE)
        self.assertNotRegex(
            _strip_ts_comments("// 예전에는 fetchProjects 를 썼다\n"),
            self.DIRECTORY_FETCH_RE,
        )
        self.assertNotRegex(
            _strip_ts_comments(
                "/* copy lives in the `@/shared/ProjectSelectField` container */\n"
            ),
            self.SELECTOR_IMPORT_RE,
        )
        self.assertRegex(
            "import { ProjectSelectField } from '@/shared/ProjectSelectField';",
            self.SELECTOR_IMPORT_RE,
        )
        self.assertRegex(
            "queryKey: queryKeys.project.pickerOptions(searchQuery),",
            self.PICKER_LEAF_CALL_RE,
        )


class TestTeamVocabularyIsNotCopiedToTheFrontend(unittest.TestCase):
    """S3 — 멤버십 team 의 **값 도메인**을 프론트가 사본으로 들고 있지 않다.

    도메인 SSOT 는 ``src/domain/services/team_policy.py::TEAM_CODES = ('RF','SAR')``
    이고, 생성 계약은 이 필드를 ``team?: string | null`` 로 노출한다 — **enum 이
    경계를 넘어오지 않는다**. 즉 프론트에는 값 도메인을 *알* 정당한 방법이 없다.

    그런데 "선택지를 주자"는 압력은 항상 있고, 그 순간 ``['RF','SAR']`` 이 화면에
    박힌다. 그 결함의 성질이 고약한 이유: **그 날은 아무 것도 깨지지 않는다.**
    팀 코드가 추가되는 날 화면만 조용히 낡고, 서버는 새 코드를 받는데 화면은
    영원히 두 개만 제안한다. ``role_key`` 가 이미 확립한 처방(로스터에서 파생 +
    자유 입력 + 서버 400 권위)이 이 필드에는 **더 강하게** 요구된다 — role 은
    그래도 실패가 400 으로 즉시 드러나지만, 팀 선택지 누락은 아무 오류도 내지
    않는다.

    **예외 목록이 없다.** 현재 트리 offender 0 이며, 그것을 정규식을 좁혀서가
    아니라 실측으로 달성했다.
    """

    #: 정확히 대문자 토큰만. 이 봉인을 대소문자 무시로 "강화"하지 말 것 —
    #: ``'rf'`` 는 이 트리에서 **다른 축**이다(시료 워크북 종류 = PM 워크북 vs
    #: RF DATA 워크북, ``routes/inventory/*``). 무시로 넓히면 정상 코드 2건이
    #: 걸리고, 그때 나오는 처방이 "allowlist 를 추가하자" 다. 그리고 소문자
    #: 사본은 애초에 서버와 매칭되지 않는 **다른 문자열**이라 이 봉인이 막으려는
    #: 조용한 드리프트가 아니다.
    TEAM_TOKEN_LITERAL = re.compile(r"""(['"`])(?:RF|SAR)\1""")

    #: 팀 어휘를 프론트 상수로 선언하는 형태. 리터럴 스캔을 우회해 값을 변수 한
    #: 단계 뒤로 옮기는(``const A='RF'``) 것까지는 위 패턴이 잡고, 이 패턴은
    #: 목록 자체를 선언하는 형태를 잡는다.
    TEAM_VOCABULARY_CONSTANT = re.compile(
        r"\b(?:TEAM_CODES|TEAM_OPTIONS|TEAM_TOKENS|KNOWN_TEAMS|VALID_TEAMS)\b"
        r"\s*(?::[^=\n]*)?=\s*[\[(]"
    )

    def test_no_team_token_literal(self) -> None:
        self.assertEqual(
            _offending_sites(self.TEAM_TOKEN_LITERAL),
            [],
            "team 토큰('RF'/'SAR') 리터럴 — 값 도메인의 SSOT 는 백엔드 "
            "team_policy.py 다. 선택지는 로드된 로스터에서 파생하고 서버를 "
            "검증 권위로 삼는다",
        )

    def test_no_frontend_team_vocabulary_constant(self) -> None:
        self.assertEqual(
            _offending_sites(self.TEAM_VOCABULARY_CONSTANT),
            [],
            "팀 어휘 목록을 프론트 상수로 선언한 흔적 — 도메인 SSOT 가 두 언어로 "
            "쪼개진다",
        )

    def test_the_scan_actually_detects_the_defect(self) -> None:
        """비-공허성: 실제 위반 형태를 합성 입력으로 확인한다."""
        for offending in (
            "const TEAM_CODES = ['RF', 'SAR'] as const;",
            'const teams = ["RF", "SAR"];',
            "const fallbackTeam = 'SAR';",
            "<option value=\"RF\" />",
        ):
            with self.subTest(snippet=offending):
                self.assertTrue(
                    self.TEAM_TOKEN_LITERAL.search(offending)
                    or self.TEAM_VOCABULARY_CONSTANT.search(offending),
                    offending,
                )

    def test_the_scan_does_not_flag_legitimate_forms(self) -> None:
        """비-공허성의 짝: 트리가 **실제로 쓰는** 형태는 잡지 않는다.

        아래 셋은 합성 예시가 아니라 실존 코드 형태다 — 시료 워크북 종류의
        소문자 ``'rf'``(팀과 무관한 축), 오류 메시지 안의 RF, 그리고 데이터
        파생 팀 선택지. 이 단정이 없으면 다음 세션이 패턴을 대소문자 무시로
        넓히다가 봉인을 못 쓰게 만든다.
        """
        for legitimate in (
            "kind === 'rf' ? exportSampleInventory(projectId, 'rf-data') : ...",
            "toApiError('RF sample import failed', response?.status)",
            "const team = row.team ?? '';",
            "for (const row of rows) { if (team.trim() !== '') seen.add(team); }",
        ):
            with self.subTest(snippet=legitimate):
                self.assertNotRegex(legitimate, self.TEAM_TOKEN_LITERAL)
                self.assertNotRegex(legitimate, self.TEAM_VOCABULARY_CONSTANT)

    def test_the_screen_actually_consumes_the_server_team_field(self) -> None:
        """비-공허성의 반대 축 — 이것이 없으면 봉인이 자기 자신을 무효화한다.

        위 스캔들은 화면이 team 을 **아예 다루지 않으면** 자동으로 통과한다.
        그리고 그 상태가 정확히 이 웨이브가 고친 결함(D1: team 축이 화면에 부재)
        이다. 금지-패턴 부재만 봉인하면 결함으로 되돌아가는 것이 봉인을 통과하는
        가장 쉬운 길이 된다.

        **이 단정의 사정거리를 정직하게 적어 둔다** — red→green 실증에서 실제로
        드러난 한계다. 여기서 보는 것은 *구조*(team 열이 존재하고, 쓰기 경로가
        team 을 싣고, 선택지가 datalist 로 노출된다)이지 *값*이 아니다. "셀이
        렌더하는 값이 진짜 ``row.team`` 인가"는 텍스트 스캔으로 신뢰성 있게
        볼 수 없고(``row.team`` 은 이 파일에 세 문맥으로 등장한다), 그 축은
        ``tests/membership.test.tsx`` 의 "displays the server `team`" 케이스가
        렌더 결과로 봉인한다. 층을 나눈 것이지 축을 버린 것이 아니다.
        """
        code = _strip_ts_comments(
            (SRC_DIR / "routes" / "membership.tsx").read_text(encoding="utf-8")
        )
        # 읽기(구조): 로스터 열 서술자에 team 열이 있고 셀이 서버 필드를 참조한다.
        self.assertIn("key: 'team'", code, "roster 에서 team 열이 사라졌다")
        self.assertIn("membership-team", code, "team 셀 렌더가 사라졌다")
        self.assertIn("row.team", code, "roster 가 서버 `team` 을 더 이상 읽지 않는다")
        # 쓰기: assign **요청 본문**이 team 을 조건부로 싣는다.
        #
        # 정규식으로 조건부 스프레드 형태 자체를 본다 — 단순히 `team: v.team` 이
        # 파일에 있는지 보면 낙관적 캐시 미러(같은 문자열이 그쪽에도 있다)가
        # 요청 본문 대신 단정을 만족시켜, 배선이 끊겨도 통과한다(red→green
        # 실증에서 실제로 그렇게 새는 것을 확인하고 좁혔다). "미지정이면 키
        # 생략"은 이 형태가 곧 그 의미이며, 런타임 확인은 vitest 가 한다.
        self.assertRegex(
            code,
            r"v\.team\s*!==\s*null\s*\?\s*\{\s*team:\s*v\.team\s*\}",
            "assign 요청 본문이 `team` 을 조건부로 싣지 않는다",
        )
        # 파생: 선택지가 로드된 데이터에서 나온다(하드코딩된 목록이 아니다).
        self.assertIn("assign-team-options", code, "team 선택지 datalist 가 사라졌다")


class TestCapabilityRemainderOperationsAreConsumed(unittest.TestCase):
    """S5~S9 구조 축 — W3-6 이 배선한 세 backend operation 이 계속 소비된다.

    이 웨이브가 고친 결함 클래스는 "백엔드 operation 이 배포돼 있는데 화면이
    없다"였다. 그 결함은 **테스트가 잡지 못한다** — 없는 코드는 실패하지 않기
    때문이다. 런타임 동작은 vitest 가 보지만, 배선이 통째로 사라지는 회귀는 그
    vitest 케이스도 함께 사라지므로 여기서 구조적으로 본다.
    """

    READINESS = SRC_DIR / "routes" / "test-plans" / "DraftReadinessPanel.tsx"
    REPORTS = SRC_DIR / "routes" / "reports.tsx"
    GLOBAL_CSS = SRC_DIR / "styles" / "global.css"

    def test_draft_archive_operation_is_wired(self) -> None:
        code = _strip_ts_comments(self.READINESS.read_text(encoding="utf-8"))
        ok, why = _consumes_headless_path(code, "/test-plan/drafts/{draft_id}/archive")
        self.assertTrue(ok, f"draft archive operation 소비가 사라졌다 (M2) — {why}")

    def test_archive_does_not_invalidate_publications(self) -> None:
        """S5 의 음성 절반. 보관은 발행이 아니다.

        publish 핸들러를 통째로 복사하면 ``publications`` 까지 무효화되고, 그
        무효화는 "보관이 발행 목록을 바꿨다"고 주장한다 — 바꾸지 않았다. 이
        오류는 화면상 아무 증상이 없어서(불필요한 refetch 하나) 리뷰에서 거의
        안 걸린다. 그래서 archive 핸들러 본문 창(窓)에서 구조적으로 본다.
        """
        code = _strip_ts_comments(self.READINESS.read_text(encoding="utf-8"))
        start = code.find("archiveMutation = useMutation")
        self.assertNotEqual(start, -1, "archive mutation 이 사라졌다")
        end = code.find("return (", start)
        self.assertNotEqual(end, -1)
        body = code[start:end]
        self.assertIn("testPlans.drafts(projectId)", body)
        self.assertIn("testPlans.draft(projectId, draftId)", body)
        self.assertNotIn(
            "publications",
            body,
            "archive 가 publications 를 무효화한다 — 보관은 발행 목록을 바꾸지 "
            "않는다(publish 핸들러 복사 흔적)",
        )

    def test_report_cancel_operation_is_wired(self) -> None:
        code = _strip_ts_comments(self.REPORTS.read_text(encoding="utf-8"))
        ok, why = _consumes_headless_path(code, "/report-automation/requests/{request_id}/cancel")
        self.assertTrue(
            ok,
            "report request cancel operation 소비가 사라졌다 (M3) — 화면은 다시 "
            f"`cancelled` 를 표시하면서 사용자가 그 상태를 만들 수단이 없어진다: {why}",
        )

    def test_cancellable_states_are_derived_not_enumerated(self) -> None:
        """취소 가능 집합이 큐 어휘의 **세 번째 사본**이 되지 않는다.

        ``TERMINAL_REQUEST_STATES``(이 파일) 와 ``QUEUE_STATUS_TOKENS``(@/ui)
        가 이미 두 곳이다. 여기에 ``['queued','running']`` 을 손으로 적으면 셋이
        되고, 큐 상태가 추가되는 날 셋이 어긋난다 — 각각은 여전히 자기 안에서
        일관돼 보이므로 어긋난 사실이 드러나지 않는다.
        """
        code = _strip_ts_comments(self.REPORTS.read_text(encoding="utf-8"))
        self.assertIn("QUEUE_STATUS_TOKENS.filter", code, "취소 가능 집합이 파생이 아니다")
        self.assertNotRegex(
            code,
            r"""\[\s*(['"])queued\1\s*,\s*(['"])running\2""",
            "취소 가능 상태를 손으로 열거했다 — 큐 어휘의 세 번째 사본",
        )

    def test_cancel_control_receives_its_query_key_from_the_host(self) -> None:
        """S8 — 취소 컨트롤이 자기 쿼리 키를 만들지 않는다.

        ``queryKeys.report.request`` 는 arity 가 둘이다: 조회 패널은 ``(id)``,
        제출 패널의 폴링은 ``(id, nodeBaseUrl)`` — **서로 다른 캐시 항목**이다.
        컨트롤이 키를 스스로 조립하면 다른 패널을 무효화하게 되고, 그 실패는
        조용하다: 취소는 성공하는데 화면의 상태는 영영 안 바뀐다.
        """
        code = _strip_ts_comments(self.REPORTS.read_text(encoding="utf-8"))
        start = code.find("function ReportCancelControl")
        self.assertNotEqual(start, -1, "ReportCancelControl 이 사라졌다")
        end = code.find("\nfunction ", start + 1)
        body = code[start:end if end != -1 else len(code)]
        self.assertIn("requestQueryKey", body, "호스트 주입 키 prop 이 사라졌다")
        self.assertNotIn(
            "queryKeys.report.request(",
            body,
            "취소 컨트롤이 report.request 키를 스스로 조립한다 — arity 가 둘이라 "
            "다른 패널의 캐시를 무효화하게 된다",
        )

    def test_team_chip_does_not_borrow_the_role_badge_palette(self) -> None:
        """M1 — 분류 축이 권한 축과 **같은 색**으로 보이지 않는다.

        멤버십 표의 ``role_key`` 셀은 ``StatusBadge status="claimed"`` 로
        렌더되어 ``--status-claimed-*`` 쌍을 쓴다. team 칩이 그 팔레트를 빌려
        쓰면 RBAC 모델 왜곡이 **코드가 아니라 CSS 에서** 일어나고, 클래스 이름을
        보는 vitest 는 그것을 못 본다(다른 사각지대). ``SampleCard`` 의 team 칩이
        이미 그 팔레트를 쓰고 있어 복사 유혹이 실재한다.
        """
        css = self.GLOBAL_CSS.read_text(encoding="utf-8")
        start = css.find(".membership-team {")
        self.assertNotEqual(start, -1, ".membership-team 규칙이 사라졌다")
        block = css[start : css.find("}", start)]
        self.assertNotIn(
            "--status-claimed",
            block,
            "team 칩이 role 배지의 상태 팔레트를 빌려 쓴다 — 분류 축과 권한 축이 "
            "화면에서 같은 것으로 보인다",
        )


# ── fe-honesty-debt M1 (2026-07-31): 사본 SSOT 통합 ──────────────────────────
#
# W3-C 가 `shared/route-links.ts` 에 project-scoped deep-link SSOT 를 만들었는데
# **소비자를 옮기지 않았다**. 그 결과 같은 세 줄이 라우트마다 재선언된 채로 남았고,
# 그중 하나(`routes/progress.tsx`)는 이름이 `projectHref` 였다 — 계약이 물려준
# 판정식 `grep -c "function projectScopedHref"` 를 **rename 한 번으로** 빠져나간
# 것이다. 그래서 이 봉인은 **이름이 아니라 형태**를 본다: 쿼리 파라미터를 직접
# 조립하는 shape 자체를 금지하고, SSOT 모듈 하나만 그것을 할 수 있게 한다.
#
# 왜 이 축이 봉인 가치가 있나: 사본이 늘어도 각 라우트의 vitest 는 계속 green 이다
# (사본이 SSOT 와 같은 문자열을 만드는 한). 드리프트는 **한쪽만 고쳐질 때** 처음
# 드러나고, 그때는 이미 사용자 대면 링크가 화면마다 다르게 동작한다. 실제로 이
# 트리의 다섯 사본 중 둘은 이미 갈라져 있었다(`isValidProjectId` 게이트 유/무).

#: 프로젝트 스코프 딥링크 SSOT — 이 모듈만 쿼리 문자열을 조립할 수 있다.
ROUTE_LINKS_SSOT_MODULE = "shared/route-links.ts"

#: 손댈 수 없는 잔여 조립 사이트. **래칫 다운 전용** — 늘리려는 다음 세션은
#: 이 목록이 아니라 SSOT 를 고쳐야 한다.
#:
#: * ``routes/chambers/next-actions.tsx`` — SSOT 와 **동작이 같다**(같은
#:   `isValidProjectId` 게이트). 순수 이관인데 이 파일은 `multichamber-supervisor-
#:   closure` 클레임이 **활성 소유** 중이라 본 웨이브가 만질 수 없다(계약 §3-4).
#: * ``routes/fields.tsx`` — arity 가 다르다(`&area=` 를 함께 싣는다). SSOT 를
#:   2-파라미터로 일반화하는 것은 소비자 1명을 위한 선반영이라 하지 않는다(YAGNI).
PROJECT_HREF_ASSEMBLY_ALLOWLIST = frozenset(
    {
        "routes/chambers/next-actions.tsx",
        "routes/fields.tsx",
    }
)

#: ``projectScopedHref``를 직접 읽는 소비자. ``project-workflow`` 자체도 이
#: 저수준 SSOT를 읽는 facade이므로 목록에 포함한다. 정의만 지우고 링크를 통째로
#: 없애는 우회를 막기 위해 각 항목의 import와 호출을 함께 검사한다.
PROJECT_HREF_DIRECT_CONSUMERS = (
        "routes/inventory/index.tsx",
    "routes/progress.tsx",
    "routes/test-plans/TestPlansWorkbench.tsx",
    "shared/project-workflow.ts",
)

#: 화면은 개별 링크 조립기를 직접 읽지 않고 project-workflow facade로 이동했다.
#: 이 목록은 facade import와 실제 호출 모두를 봉인한다. 두 소비자 종류를 섞으면
#: facade로 옮긴 화면이 direct 목록에 남는 stale declaration을 감지할 수 없다.
PROJECT_WORKFLOW_FACADE_CONSUMERS = (
    "routes/_layout.tsx",
    "routes/my-projects.tsx",
    "routes/projects.tsx",
    "routes/reports.tsx",
)


class TestProjectScopedHrefIsSingleSsot(unittest.TestCase):
    """S1/S2 — project-scoped 딥링크를 조립하는 코드점이 하나다.

    스캔 대상은 함수 **이름**이 아니라 조립 **형태**다. 리터럴 ``?project=`` 든
    상수 보간 ``?${PROJECT_QUERY_PARAM}=`` 든 둘 다 걸린다 — 후자는 SSOT 본문을
    통째로 복사한 사본이 첫 번째 패턴을 빠져나가는 구멍을 막는다.
    """

    #: `…?project=${encodeURIComponent(` / `…?${PROJECT_QUERY_PARAM}=${encodeURIComponent(`
    ASSEMBLY_RE = re.compile(
        r"\?(?:project|\$\{\s*PROJECT_QUERY_PARAM\s*\})="
        r"\$\{\s*encodeURIComponent\s*\("
    )
    #: Named import를 module target까지 해석한다. alias(`@/…`)와 sibling
    #: relative import(`./route-links`)를 같은 모듈로 정규화해야 facade 자체를
    #: 소비자 집합에서 누락하지 않는다.
    NAMED_IMPORT_RE = re.compile(
        r"import\s*\{(?P<bindings>[^}]*)\}\s*from\s*['\"](?P<specifier>[^'\"]+)['\"]",
        re.S,
    )
    SSOT_CALL_RE = re.compile(r"\bprojectScopedHref\s*\(")

    @staticmethod
    def _normalise_import_target(rel: str, specifier: str) -> str:
        if specifier.startswith("@/"):
            return specifier[2:]
        return posixpath.normpath(posixpath.join(posixpath.dirname(rel), specifier))

    @classmethod
    def _imports_symbol_from(
        cls,
        rel: str,
        code: str,
        *,
        symbol: str,
        target: str,
    ) -> bool:
        for match in cls.NAMED_IMPORT_RE.finditer(code):
            if cls._normalise_import_target(rel, match.group("specifier")) != target:
                continue
            if re.search(rf"\b{re.escape(symbol)}\b", match.group("bindings")):
                return True
        return False

    @classmethod
    def _direct_href_consumers(cls) -> set[str]:
        return {
            _rel(path)
            for path in _src_files()
            if cls._imports_symbol_from(
                _rel(path),
                _strip_ts_comments(path.read_text(encoding="utf-8")),
                symbol="projectScopedHref",
                target="shared/route-links",
            )
        }

    @classmethod
    def _project_workflow_facade_consumers(cls) -> set[str]:
        facade_symbols = (
            "projectWorkflowActions",
            "projectWorkflowHref",
            "projectWorkspaceHref",
        )
        consumers: set[str] = set()
        for path in _src_files():
            rel = _rel(path)
            code = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for symbol in facade_symbols:
                if cls._imports_symbol_from(
                    rel,
                    code,
                    symbol=symbol,
                    target="shared/project-workflow",
                ) and re.search(rf"\b{re.escape(symbol)}\s*\(", code):
                    consumers.add(rel)
                    break
        return consumers

    def test_query_string_assembly_lives_in_the_ssot_only(self) -> None:
        offenders = sorted(
            {
                site.rsplit(":", 1)[0]
                for site in _offending_sites(self.ASSEMBLY_RE)
            }
            - {ROUTE_LINKS_SSOT_MODULE}
            - PROJECT_HREF_ASSEMBLY_ALLOWLIST
        )
        self.assertEqual(
            offenders,
            [],
            "project 스코프 쿼리 문자열을 SSOT 밖에서 조립한다 — 이름을 무엇으로 "
            "붙였든 사본이다(`routes/progress.tsx` 는 `projectHref` 라는 이름으로 "
            f"이 결함을 한 웨이브 동안 숨겼다). `projectScopedHref` 를 쓰라: {offenders}",
        )

    def test_the_ssot_itself_still_assembles(self) -> None:
        """SSOT 가 조립을 그만두면 위 단언은 **공허하게** 통과한다."""
        code = _strip_ts_comments(
            (SRC_DIR / ROUTE_LINKS_SSOT_MODULE).read_text(encoding="utf-8")
        )
        self.assertRegex(
            code,
            self.ASSEMBLY_RE,
            f"{ROUTE_LINKS_SSOT_MODULE} 가 더 이상 딥링크를 조립하지 않는다 — "
            "스캔이 아무것도 안 잡는 상태로 green 이 된다",
        )

    def test_direct_consumers_actually_import_and_call_the_ssot(self) -> None:
        """Direct consumers must not silently disappear behind an empty scan."""
        for rel in PROJECT_HREF_DIRECT_CONSUMERS:
            with self.subTest(consumer=rel):
                path = SRC_DIR / rel
                self.assertTrue(path.is_file(), f"소비자 부재: {rel}")
                code = _strip_ts_comments(path.read_text(encoding="utf-8"))
                self.assertTrue(
                    self._imports_symbol_from(
                        rel,
                        code,
                        symbol="projectScopedHref",
                        target="shared/route-links",
                    ),
                    f"{rel} 이 딥링크 SSOT 를 import 하지 않는다",
                )
                self.assertRegex(
                    code,
                    self.SSOT_CALL_RE,
                    f"{rel} 이 딥링크 SSOT 를 호출하지 않는다",
                )

    def test_workflow_facade_consumers_import_and_call_the_facade(self) -> None:
        """Workflow surfaces must use the facade rather than rejoining the low level."""
        actual = self._project_workflow_facade_consumers()
        for rel in PROJECT_WORKFLOW_FACADE_CONSUMERS:
            with self.subTest(consumer=rel):
                self.assertIn(
                    rel,
                    actual,
                    f"{rel} 이 project-workflow facade 를 import/call 하지 않는다",
                )

    def test_the_consumer_lists_are_exhaustive_and_non_vacuous(self) -> None:
        """Direct and facade consumer sets both detect additions and removals."""
        declared_direct = set(PROJECT_HREF_DIRECT_CONSUMERS)
        declared_facade = set(PROJECT_WORKFLOW_FACADE_CONSUMERS)
        self.assertTrue(declared_direct, "direct consumer 목록이 비어 있으면 SSOT 검사가 공허해진다")
        self.assertTrue(declared_facade, "facade consumer 목록이 비어 있으면 SSOT 검사가 공허해진다")
        self.assertEqual(
            len(PROJECT_HREF_DIRECT_CONSUMERS),
            len(declared_direct),
            "direct consumer 목록 중복",
        )
        self.assertEqual(
            len(PROJECT_WORKFLOW_FACADE_CONSUMERS),
            len(declared_facade),
            "facade consumer 목록 중복",
        )
        self.assertEqual(
            self._direct_href_consumers(),
            declared_direct,
            "direct projectScopedHref 소비자 집합이 선언과 다르다 — 새 소비자면 direct "
            "목록에 추가하고, facade로 옮겼다면 direct 목록에서 제거하라",
        )
        self.assertEqual(
            self._project_workflow_facade_consumers(),
            declared_facade,
            "project-workflow facade 소비자 집합이 선언과 다르다 — 새 화면은 facade "
            "목록에 추가하고, 사라진 화면은 현재 프로젝트 컨텍스트 전달 경로를 확인하라",
        )

    def test_the_scan_actually_detects_the_defect(self) -> None:
        """비-공허성 ①: 실제 사본 형태(리터럴·상수·이름 무관)를 잡는다."""
        for offending in (
            "return `${path}?project=${encodeURIComponent(projectId)}`;",
            "return `${path}?${PROJECT_QUERY_PARAM}=${encodeURIComponent(id)}`;",
            # 이름을 바꿔도 형태는 그대로다 — 이것이 이 봉인의 존재 이유다.
            "function projectHref(p, id) { return `${p}?project="
            "${encodeURIComponent(id)}`; }",
        ):
            with self.subTest(shape=offending[:40]):
                self.assertRegex(offending, self.ASSEMBLY_RE)

    def test_the_scan_does_not_flag_legitimate_forms(self) -> None:
        """비-공허성 ②: 정상 형태를 오탐하지 않는다(오탐하면 봉인이 꺼진다)."""
        for legit in (
            "to={projectScopedHref(ROUTE_PATHS.projects, projectId)}",
            # 쿼리를 **읽는** 쪽은 조립이 아니다.
            "const projectId = (searchParams.get('project') ?? '').trim();",
            # URLSearchParams 경유 — 손조립이 아니라 표준 API.
            "params.set(PROJECT_QUERY_PARAM, projectId);",
            "const href = `${path}?session=${encodeURIComponent(sessionId)}`;",
        ):
            with self.subTest(form=legit[:40]):
                self.assertNotRegex(legit, self.ASSEMBLY_RE)
        # 주석은 코드가 아니다 — `route-links.ts` 도입부가 이 형태를 설명한다.
        self.assertNotRegex(
            _strip_ts_comments("// 옛 사본: `${path}?project=${encodeURIComponent(id)}`\n"),
            self.ASSEMBLY_RE,
        )


class TestProjectHrefAllowlistRatchet(unittest.TestCase):
    """S1 의 allowlist 는 이월 부채다 — 줄기만 하고 늘지 않는다."""

    #: 2 (fe-honesty-debt M1 착수 시점: 타 클레임 활성 1 + arity 상이 1).
    #: 5 벌이던 사본 중 5 벌을 이 웨이브가 갚았고, 남은 둘은 각각 **소유권**과
    #: **YAGNI** 라는 서로 다른 이유로 남았다. 둘 다 코드가 아니라 일정 문제다.
    CEILING = 2

    def test_allowlist_does_not_grow(self) -> None:
        self.assertLessEqual(
            len(PROJECT_HREF_ASSEMBLY_ALLOWLIST),
            self.CEILING,
            "딥링크 조립 allowlist 가 늘었다 — 새 화면이 사본을 만들었다는 뜻이다. "
            f"래칫은 아래로만: {sorted(PROJECT_HREF_ASSEMBLY_ALLOWLIST)}",
        )

    def test_entries_still_exist_and_still_offend(self) -> None:
        """죽은 항목은 빼야 한다 — 남겨두면 상한이 슬그머니 의미를 잃는다."""
        stale: list[str] = []
        scanner = TestProjectScopedHrefIsSingleSsot()
        for rel in PROJECT_HREF_ASSEMBLY_ALLOWLIST:
            path = SRC_DIR / rel
            if not path.is_file():
                stale.append(f"{rel} (file gone)")
                continue
            code = _strip_ts_comments(path.read_text(encoding="utf-8"))
            if not scanner.ASSEMBLY_RE.search(code):
                stale.append(f"{rel} (debt already paid)")
        self.assertEqual(stale, [], f"stale allowlist 항목을 빼라: {stale}")


class TestSearchDebounceIsSingleSsot(unittest.TestCase):
    """S4/S5 — type-ahead 커밋 창(debounce)의 정의가 하나다.

    ``routes/projects.tsx::TECH_FILTER_DEBOUNCE_MS`` 와
    ``shared/search-debounce.ts::SEARCH_DEBOUNCE_MS`` 가 같은 값(250)으로 공존했다.
    **값이 같아서** 합친 것이 아니라 **정책이 같아서** 합쳤다: 세 소비처가 전부
    "type-ahead 입력을 사용자가 멈춘 뒤 한 번의 서버측 좁히기 읽기로 커밋"이고,
    이 값이 정하는 축(*언제* 커밋하는가)은 무엇으로 좁히는지(``technology`` vs
    ``q``)와 직교한다. 정책이 갈라져야 할 날이 오면 **이름 있는 두 번째 상수**로
    갈라야지, 라우트 로컬 리터럴로 되돌아가서는 안 된다 — 그 방향을 여기서 막는다.
    """

    SEARCH_DEBOUNCE_SSOT_MODULE = "shared/search-debounce.ts"
    #: `const <NAME>_DEBOUNCE_MS = <number>` 선언(타입 주석 유무 무관).
    DEBOUNCE_CONST_RE = re.compile(
        r"\bconst\s+([A-Z][A-Z0-9_]*_DEBOUNCE_MS)\s*(?::[^=]+)?=\s*[0-9]"
    )
    #: `setTimeout(…, 250)` — 상수를 지우고 리터럴로 되돌아가는 우회.
    #:
    #: **후행 쉼표는 선택이다.** prettier 가 이 호출을 여러 줄로 감싸면 인자
    #: 뒤에 `,` 가 붙는다(`setTimeout(\n  fn,\n  250,\n);`) — 실제 이 트리의
    #: `routes/projects.tsx` 가 정확히 그 모양이었다. 그것을 빼먹은 첫 판본은
    #: **문자열 단위 비-공허성 테스트는 green 인데 실제 파일 재주입은 못 잡는**
    #: 상태였다(파일 단위 red 실증이 잡아냈다).
    LITERAL_TIMEOUT_RE = re.compile(
        r"setTimeout\s*\([^;]*?,\s*[0-9][0-9_]*\s*,?\s*\)"
    )

    def test_exactly_one_debounce_constant_definition(self) -> None:
        sites = _offending_sites(self.DEBOUNCE_CONST_RE)
        self.assertEqual(
            [site.rsplit(":", 1)[0] for site in sites],
            [self.SEARCH_DEBOUNCE_SSOT_MODULE],
            "debounce 상수 정의가 정확히 하나가 아니다 — 두 벌이 되는 순간 한쪽만 "
            f"조정되는 드리프트가 시작된다: {sites}",
        )

    def test_routes_do_not_reinline_a_timeout_literal(self) -> None:
        """상수를 지우고 리터럴로 돌아가는 우회를 막는다.

        `test_exactly_one_debounce_constant_definition` 은 **명명된** 사본만
        본다. 라우트가 ``window.setTimeout(fn, 250)`` 으로 되돌아가면 그 단언은
        계속 green 이면서 SSOT 는 실질적으로 우회된다.
        """
        offenders = [
            site
            for site in _offending_sites(self.LITERAL_TIMEOUT_RE)
            if site.startswith("routes/")
        ]
        self.assertEqual(
            offenders,
            [],
            "라우트가 setTimeout 지연을 숫자 리터럴로 박았다 — SEARCH_DEBOUNCE_MS "
            f"를 쓰라: {offenders}",
        )

    def test_the_ssot_is_consumed_not_merely_declared(self) -> None:
        """반대 축: 상수가 **읽히고 있다**(정의만 남은 죽은 SSOT 아님)."""
        importers = sorted(
            {
                _rel(path)
                for path in _src_files()
                if re.search(
                    r"import\s*\{[^}]*\bSEARCH_DEBOUNCE_MS\b[^}]*\}\s*from",
                    _strip_ts_comments(path.read_text(encoding="utf-8")),
                )
            }
        )
        self.assertEqual(
            importers,
            ["routes/my-projects.tsx", "routes/projects.tsx", "shared/ProjectSelectField.tsx"],
            "debounce SSOT 의 소비자 집합이 달라졌다 — 사라졌다면 그 화면이 "
            f"type-ahead 를 어떻게 커밋하는지 확인하라: {importers}",
        )

    def test_the_scans_actually_detect_the_defects(self) -> None:
        """비-공허성: 두 우회 형태를 각각 잡고, 정상 형태는 잡지 않는다."""
        self.assertRegex(
            "export const TECH_FILTER_DEBOUNCE_MS = 250;", self.DEBOUNCE_CONST_RE
        )
        self.assertRegex(
            "const SEARCH_DEBOUNCE_MS: number = 250;", self.DEBOUNCE_CONST_RE
        )
        self.assertRegex(
            "const h = window.setTimeout(() => commit(draft), 250);",
            self.LITERAL_TIMEOUT_RE,
        )
        # prettier 가 감싼 다중행 형태(후행 쉼표) — 첫 판본이 놓쳤던 모양.
        self.assertRegex(
            "const handle = window.setTimeout(\n"
            "  () => setParam('techFilter', techDraft),\n"
            "  250,\n"
            ");",
            self.LITERAL_TIMEOUT_RE,
        )
        self.assertNotRegex(
            "const h = window.setTimeout(() => commit(draft), SEARCH_DEBOUNCE_MS);",
            self.LITERAL_TIMEOUT_RE,
        )
        self.assertNotRegex(
            _strip_ts_comments("// 옛 이름은 TECH_FILTER_DEBOUNCE_MS = 250 이었다\n"),
            self.DEBOUNCE_CONST_RE,
        )


# ── S6/S7 — 검색 **범위 주장** ↔ 실제 쿼리 파라미터 정합 ────────────────────
#
# 이 결함 클래스는 W3-B 에서 3회 재발했고(로드 범위 → 상태 범위 → 선택기 범위),
# 세 번 다 리뷰가 사람 눈으로 잡았다. 코드도 테스트도 통과했기 때문이다 — 틀린
# 것은 **문구**였고, 문구를 볼 기계가 없었다. 본 웨이브가 4번째 축
# (`api/platform-client.ts::fetchProjectsPage` 독스트링)을 실제로 발견하면서
# "문구만 고치면 또 나온다"는 계약의 예측이 실측으로 확인됐다. 그래서 여기서
# 기계 판정으로 승격한다.
#
# 판정의 **권위는 백엔드 SSOT** 다. 검색 축을 이 파일에 다시 적으면 그 자체가
# 세 번째 사본이 되므로, `PROJECT_SEARCH_COLUMNS` 와 프로젝트 속성 SSOT 에서
# 파생한다. 아래 표는 그 컬럼들의 **화면 표기**만 선언한다(사람이 읽는 말과
# 컬럼명 사이의 번역표 — 이것만은 어딘가에 한 번 적어야 한다).

#: 검색 가능한 축의 화면 표기 토큰. 키 집합 == ``PROJECT_SEARCH_COLUMNS``
#: (백엔드가 축을 늘리면 여기서 red → 화면 문구를 함께 갱신하게 된다).
SEARCH_AXIS_DISPLAY_TOKENS: dict[str, tuple[str, ...]] = {
    "management_number": ("관리번호", "management number", "management no"),
    "project_code": ("모델", "model", "프로젝트 코드", "project code"),
    "customer": ("고객", "customer"),
}

#: ``model_name`` 은 검색 **컬럼**이 아니지만 검색은 된다 — ADR-0017 D1 이
#: ``project_code == model name`` 을 못박았으므로 ``project_code`` 검색이 곧
#: 모델명 검색이다. 화면이 "모델명으로 찾기"라고 말하는 것은 참이다.
#: 이 등가가 깨지면 아래 드리프트 게이트가 red 가 된다.
SEARCH_AXIS_ALIASES: dict[str, str] = {"model_name": "project_code"}

#: 검색 축은 아니지만 **실제로 좁히는 파라미터** — 범위 주장에서 이름이 불려도
#: 없는 능력을 광고하는 것이 아니다(오히려 불러야 정직해진다).
NARROWING_PARAM_FIELDS: frozenset[str] = frozenset({"status"})

#: 서버가 ``q`` 로 훑지 **않는** 프로젝트 속성의 화면 표기. 검색 범위 주장에
#: 이 이름이 실리면 없는 능력을 광고하는 것이다. ``fcc_id`` 는 특히 **저장조차
#: 되지 않는 파생값**(`report_number` 형제)이라 검색이 원리적으로 불가능하다.
NON_SEARCHABLE_DISPLAY_TOKENS: dict[str, tuple[str, ...]] = {
    "fcc_id": ("FCC ID",),
    "manufacturer": ("제조사", "manufacturer"),
    "fcc_grantee_code": ("grantee code", "그랜티"),
    "applicant_name": ("신청자", "applicant"),
    "applicant_address": ("신청자 주소", "applicant address"),
    "eut_description": ("시험 대상", "eut description"),
    "test_standard": ("규격", "test standard"),
}

#: "이 문장은 **검색 범위**를 말하고 있다"는 표지. 이것이 없으면 스캔하지 않는다 —
#: 같은 이름들이 표지 메타 편집 설명처럼 검색과 무관한 열거에도 나오기 때문이다
#: (`routes/my-projects.tsx` 파일 헤더가 정확히 그런 이웃 문장을 갖고 있다).
SEARCH_CONTEXT_TOKENS: tuple[str, ...] = (
    "검색", "찾기", "조회", "search", "find", "lookup", "matches",
)

#: 빈 결과의 **의미**를 단정하는 문장의 앵커. 세 사이트가 전부 이 관용구를 썼다.
EMPTY_VERDICT_ANCHOR_RE = re.compile(
    r"[\"'“”]?없음[\"'“”]?\s*[은는]"
    r"|[\"'“”]?no\s+(?:match|results?)[\"'“”]?\s+means",
    re.IGNORECASE,
)

#: 그 단정이 **좁혀진 범위 안의 것임을 밝히는** 말. 하나라도 있으면 정직하다.
NARROWING_DISCLOSURE_TOKENS: tuple[str, ...] = (
    "상태", "status", "활성", "active", "완료", "completed", "필터", "filter",
)

#: **과거 서술** 표지. 이 봉인은 *현재의 주장*만 판정한다 — "예전에는 이런
#: 거짓말이었다"고 설명하는 문장까지 red 로 잡으면 결함을 기록한 문서가 결함으로
#: 취급되고(``_offending_sites`` 독스트링이 경고하는 바로 그 함정), 개발자는
#: 봉인을 끄거나 우회한다. 실제로 초판이 ``routes/my-projects.tsx`` 파일 헤더의
#: W3-B 회고 문장("…였다 — 프로젝트가 누적되면 거짓이 되는 답이다")을 잡았다.
#:
#: **잔여 위험(명시)**: 과거형으로 쓴 거짓 주장은 통과한다. 과거 시제 단정은
#: 현재 동작에 대한 약속이 아니므로 의미상 감수 가능한 구멍이고, 정정 전 세
#: 문구는 어느 것도 이 표지를 갖지 않는다(아래 비-공허성 단언이 그것을 고정).
HISTORICAL_NARRATION_TOKENS: tuple[str, ...] = (
    "였다", "이었다", "이전에는", "used to", "previously",
)

#: 문장이 이보다 길면 그 뒤는 다른 이야기로 본다(창이 무한정 넓어지는 것 방지).
MAX_CLAIM_SENTENCE_CHARS = 240

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RUN_RE = re.compile(r"(?:^[ \t]*//[^\n]*\n?)+", re.MULTILINE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _comment_blocks(src: str) -> list[str]:
    """주석 **본문**만 공백 정규화해 돌려준다.

    결함이 사는 곳이 주석이므로 여기서는 ``_strip_ts_comments`` 와 정반대로
    주석만 남긴다. 마커(``/**``/``*``/``//``/``*/``)는 지우되 줄 시작의 마크다운
    강조(``**bold**``)는 별표만 벗겨지고 낱말은 살아남는다(토큰 판정 무영향).
    """
    raw_blocks = [m.group(0) for m in _BLOCK_COMMENT_RE.finditer(src)]
    raw_blocks += [m.group(0) for m in _LINE_COMMENT_RUN_RE.finditer(src)]
    out: list[str] = []
    for raw in raw_blocks:
        text = raw
        if text.startswith("/*"):
            text = text[2:]
        if text.endswith("*/"):
            text = text[:-2]
        text = re.sub(r"^[ \t]*(?:\*+|//)", " ", text, flags=re.MULTILINE)
        out.append(re.sub(r"\s+", " ", text).strip())
    return [block for block in out if block]


def _claim_sentences(text: str) -> list[str]:
    """문장 단위로 쪼갠다 — 범위 주장의 **자연 경계**.

    블록 전체를 창으로 쓰면 이웃 문장이 섞여 거짓 판정이 난다: 검색을 설명하는
    문단 바로 아래에 표지 메타 8칸을 열거하는 문단이 있으면 블록 창은 "검색이
    제조사도 훑는다"고 읽는다(거짓 red). 반대로 고정 문자 창은 그 경계를
    문자 수로 흉내 낼 뿐이라 문장이 길어지면 똑같이 샌다.
    """
    sentences: list[str] = []
    for chunk in _SENTENCE_SPLIT_RE.split(text):
        chunk = chunk.strip()
        while len(chunk) > MAX_CLAIM_SENTENCE_CHARS:
            sentences.append(chunk[:MAX_CLAIM_SENTENCE_CHARS])
            chunk = chunk[MAX_CLAIM_SENTENCE_CHARS:]
        if chunk:
            sentences.append(chunk)
    return sentences


def _has_token(text: str, tokens) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def _named_axes(text: str) -> set[str]:
    return {
        column
        for column, tokens in SEARCH_AXIS_DISPLAY_TOKENS.items()
        if _has_token(text, tokens)
    }


def _named_non_searchable(text: str) -> set[str]:
    return {
        field
        for field, tokens in NON_SEARCHABLE_DISPLAY_TOKENS.items()
        if _has_token(text, tokens)
    }


def _locale_messages(locale: str) -> dict[str, str]:
    bundle = json.loads(
        (SRC_DIR / "locales" / f"{locale}.json").read_text(encoding="utf-8")
    )
    return _flatten_messages(bundle)


def _scope_claim_sentences() -> list[tuple[str, str]]:
    """``(출처, 문장)`` — 트리 전역의 범위-주장 후보 문장.

    코드 주석과 사용자 대면 i18n 문자열을 **같은 판정에 건다**. 둘을 나누면
    비가시 주석만 고치고 화면 문구는 거짓말을 계속하는 상태가 통과한다.
    """
    out: list[tuple[str, str]] = []
    for path in _src_files():
        source = path.read_text(encoding="utf-8")
        for block in _comment_blocks(source):
            for sentence in _claim_sentences(block):
                out.append((_rel(path), sentence))
    for locale in ("ko", "en"):
        for key, value in _locale_messages(locale).items():
            for sentence in _claim_sentences(value):
                # 키 경로도 맥락이다 — `…search.placeholder` 의 값 자체에는
                # "검색"이라는 낱말이 없지만 그 문자열은 검색창의 안내다.
                out.append((f"{locale}.json::{key}", f"{key} {sentence}"))
    return out


class TestSearchScopeClaimsMatchTheServerAxes(unittest.TestCase):
    """S6/S7 — 조회 **범위 주장**이 실제 쿼리 파라미터보다 넓으면 red.

    두 축을 본다:

    1. **훑는 축 주장** — 검색 범위를 말하는 문장이 서버가 훑지 않는 속성을
       이름으로 부르면 없는 능력을 광고하는 것이다(``FCC ID`` 가 그랬다:
       저장조차 되지 않는 파생값이라 검색이 원리적으로 불가능한데 선택기
       placeholder 가 4번째 축으로 걸어 두고 있었다).
    2. **빈 결과 단정** — "없음/no results" 의 의미를 단정하는 문장은, 같은
       요청이 ``status`` 로도 좁혀진다는 사실을 함께 밝혀야 한다. 밝히지 않으면
       그 단정은 딱 그 필터 폭만큼 거짓이다.
    """

    def test_the_axis_table_tracks_the_backend_ssot(self) -> None:
        """드리프트 게이트 — 백엔드가 축을 바꾸면 번역표가 red 가 된다."""
        self.assertEqual(
            set(SEARCH_AXIS_DISPLAY_TOKENS),
            set(PROJECT_SEARCH_COLUMNS),
            "검색 축 번역표가 백엔드 PROJECT_SEARCH_COLUMNS 와 어긋났다 — 축이 "
            "늘거나 줄면 화면 문구도 함께 갱신해야 한다",
        )

    def test_the_non_searchable_table_is_derived_not_invented(self) -> None:
        """"검색 못 하는 속성" 목록도 **파생값**이다(손으로 고른 목록 아님)."""
        derived = (
            (set(EDITABLE_PROJECT_META_FIELDS) | set(IMMUTABLE_PROJECT_FIELDS)
             | {"fcc_id"})
            - set(PROJECT_SEARCH_COLUMNS)
            - set(SEARCH_AXIS_ALIASES)
            - NARROWING_PARAM_FIELDS
        )
        self.assertEqual(
            set(NON_SEARCHABLE_DISPLAY_TOKENS),
            derived,
            "프로젝트 속성이 늘었는데 검색 가능 여부가 분류되지 않았다 — 새 필드는 "
            "검색 축이거나(위 표) 좁히는 파라미터이거나(NARROWING_PARAM_FIELDS) "
            "검색 불가 속성이다. 셋 중 하나로 명시하라",
        )

    def test_the_model_name_alias_still_has_its_domain_warrant(self) -> None:
        """별칭의 근거(ADR-0017 D1)가 도메인 모듈에 살아 있는지 확인한다.

        근거가 사라지면 "모델명으로 찾기"가 참이라는 판단도 함께 무너진다.
        """
        domain_src = moved_module_source(
            'domain.services.project_directory_query'
        ).read_text(encoding="utf-8")
        self.assertIn(
            "project_code == model name",
            domain_src,
            "ADR-0017 D1 의 등가 선언이 도메인 모듈에서 사라졌다 — SEARCH_AXIS_ALIASES "
            "의 근거가 없어졌으므로 별칭을 재검토하라",
        )

    def test_no_scope_claim_names_an_unsearchable_attribute(self) -> None:
        offenders = [
            f"{origin}: {sorted(_named_non_searchable(sentence))} — {sentence[:100]}"
            for origin, sentence in _scope_claim_sentences()
            if _has_token(sentence, SEARCH_CONTEXT_TOKENS)
            and len(_named_axes(sentence)) >= 2
            and _named_non_searchable(sentence)
        ]
        self.assertEqual(
            offenders,
            [],
            "검색 범위 주장이 서버가 훑지 않는 속성을 이름으로 부른다 — 사용자는 "
            f"그 값으로 찾을 수 있다고 읽고, 찾지 못하면 없다고 결론짓는다: {offenders}",
        )

    def test_no_empty_result_verdict_hides_its_narrowing_filter(self) -> None:
        offenders = [
            f"{origin}: {sentence[:140]}"
            for origin, sentence in _scope_claim_sentences()
            if EMPTY_VERDICT_ANCHOR_RE.search(sentence)
            and not _has_token(sentence, NARROWING_DISCLOSURE_TOKENS)
            and not _has_token(sentence, HISTORICAL_NARRATION_TOKENS)
        ]
        self.assertEqual(
            offenders,
            [],
            "빈 결과의 의미를 단정하면서 같은 요청을 좁히는 status 축을 밝히지 "
            f"않았다 — 그 단정은 필터 폭만큼 거짓이다: {offenders}",
        )

    def test_the_scan_has_live_claim_sites(self) -> None:
        """비-공허성 (트리 축) — 스캔 대상이 실제로 존재한다.

        문구가 전부 사라지면 두 단언은 빈 목록끼리 비교하며 영원히 green 이 된다.
        """
        sentences = _scope_claim_sentences()
        axis_sites = sorted(
            {
                origin
                for origin, sentence in sentences
                if _has_token(sentence, SEARCH_CONTEXT_TOKENS)
                and len(_named_axes(sentence)) >= 2
            }
        )
        verdict_sites = sorted(
            {
                origin
                for origin, sentence in sentences
                if EMPTY_VERDICT_ANCHOR_RE.search(sentence)
            }
        )
        # ⚠️ 수가 아니라 **이름**이다. `>= 3` 은 세 축이 살아 있는지 묻지 않는다 —
        # 한 축이 사라지고 다른 축에 문장이 하나 늘면 3 은 그대로이고 검사는 green 이다.
        # (그리고 축이 넷째로 늘면 3 은 아무것도 더 지키지 않는다.) 아래 이름 단언이
        # 완전성을 답하므로 수치 단언은 비-공허성 바닥만 남긴다.
        self.assertGreater(
            len(axis_sites), 0, f"훑는 축을 말하는 문장이 사라졌다: {axis_sites}"
        )
        self.assertGreater(
            len(verdict_sites), 0, f"빈 결과를 단정하는 문장이 사라졌다: {verdict_sites}"
        )
        # 세 축(라우트 주석 · API 독스트링 · 선택기 컨테이너)이 전부 살아 있어야
        # 한다 — 하나라도 빠지면 그 축의 재발을 아무도 보지 않는다.
        self.assertIn("routes/my-projects.tsx", verdict_sites)
        self.assertIn("api/platform-client.ts", verdict_sites)
        self.assertIn("shared/ProjectSelectField.tsx", verdict_sites)
        # ⚠️ `axis_sites` 는 `verdict_sites` 와 **다른 모집단**이다(이 웨이브가 셋을
        # 양쪽에 요구했다가 실측으로 반증됐다 — `api/platform-client.ts` 는 빈 결과를
        # 단정하지만 훑는 축을 두 개 이상 나열하지는 않는다). 그러므로 여기서 이름을
        # 댈 수 있는 것은 이 클래스가 실제로 다루는 화면 하나뿐이고, 그것으로 충분하다:
        # 그 화면이 빠지면 남은 문장이 몇 개든 이 검사의 대상이 사라진 것이다.
        for axis in ("routes/my-projects.tsx", "shared/project-option.ts"):
            self.assertIn(axis, axis_sites)

    def test_the_scans_actually_detect_the_defects(self) -> None:
        """비-공허성 (정규식 축) — 정정 **전** 문구를 red 로 잡고, 정상은 통과.

        파일 단위 red→green 실증은 평가 문서 §에 별도로 기록한다. 여기 문자열
        단언만으로는 *이 코드베이스의 실제 포맷*에서 동작함을 증명하지 못한다는
        것이 직전 사이클의 교훈이다(자평 #1).
        """
        # 1) 훑는 축 — 정정 전 placeholder / 주석
        for offending in (
            "components.projectSelect.search.placeholder "
            "모델명 · 관리번호 · 고객사 · FCC ID",
            "검색 — 서버측 `q`(모델/코드/고객/관리번호/FCC ID)",
            "Search by model, management number, customer or manufacturer",
        ):
            with self.subTest(offending=offending):
                self.assertTrue(
                    _has_token(offending, SEARCH_CONTEXT_TOKENS)
                    and len(_named_axes(offending)) >= 2
                    and _named_non_searchable(offending),
                )
        # 정상 형태 — 실제 트리에 있는 두 문구는 잡히면 안 된다.
        for benign in (
            "routes.myProjects.search.placeholder 모델명·고객·관리번호로 찾기",
            "routes.myProjects.stepSearchDetail 모델·고객·관리번호 검색",
            # 검색 맥락이 없는 이웃 열거(표지 메타 8칸)는 스캔 대상이 아니다.
            "성적서 표지에 실리는 8칸(관리번호·고객·신청자·제조사·규격)을 채운다",
        ):
            with self.subTest(benign=benign):
                self.assertFalse(
                    _has_token(benign, SEARCH_CONTEXT_TOKENS)
                    and len(_named_axes(benign)) >= 2
                    and _named_non_searchable(benign),
                )
        # 2) 빈 결과 단정 — 정정 전 세 문장
        for offending in (
            '"없음"은 중앙 디렉토리 전체에 없다는 뜻이다',
            '"no results" means no such project exists, not "none among the rows"',
            '"no match" means no such project exists',
        ):
            with self.subTest(offending=offending):
                self.assertRegex(offending, EMPTY_VERDICT_ANCHOR_RE)
                self.assertFalse(
                    _has_token(offending, NARROWING_DISCLOSURE_TOKENS)
                )
                # 과거형 면제가 정정 전 문구를 삼키지 않는다 — 면제를 넣은
                # 순간 red 실증이 조용히 무력화될 수 있는 지점이라 못박는다.
                self.assertFalse(
                    _has_token(offending, HISTORICAL_NARRATION_TOKENS)
                )
        for benign in (
            '"없음"은 현재 상태 필터 안의 중앙 디렉토리에 없다는 뜻이다',
            '"no results" means no such project exists within the requested '
            "`status` scope",
            '"no match" means no such *active* project exists',
            # 배지 라벨의 "없음" — 은/는이 붙지 않으므로 단정문이 아니다.
            "없음",
            # 결함을 **기록한** 회고 문장(실제 파일 헤더에 있다). 현재의 주장이
            # 아니므로 잡히면 안 된다 — 초판은 이것을 잡았다.
            '그 구조에서 검색 결과 "없음"은 *"서버에 없다"* 가 아니라 '
            '*"내가 받아온 배열에 없다"* 였다',
        ):
            with self.subTest(benign=benign):
                self.assertTrue(
                    not EMPTY_VERDICT_ANCHOR_RE.search(benign)
                    or _has_token(benign, NARROWING_DISCLOSURE_TOKENS)
                    or _has_token(benign, HISTORICAL_NARRATION_TOKENS),
                )


def _flatten_messages(tree: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a locale bundle to dotted keys → string values."""
    out: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten_messages(value, path))
        elif isinstance(value, str):
            out[path] = value
    return out


# ── M-1 문서 언어 ↔ 활성 로케일 (fe-w4-a11y-error-resilience, 2026-07-31) ─────
#
# `<html lang>` 은 스크린리더가 **어느 음성 엔진으로 읽을지**를 고르는 선언이다.
# 정적으로 `ko` 로 박혀 있는데 `DEFAULT_LOCALE='en'` 이면, 기본 상태에서 영어
# 문장을 한국어 발음 규칙으로 읽는다 — 화면은 멀쩡한데 내용 전달만 실패하므로
# 눈으로 보는 QA 로는 영원히 발견되지 않는다.
#
# 이 저장소는 같은 형태의 문제를 이미 두 번 풀었다: `public/theme-init.js`
# (`data-theme`) 와 `public/density-boot.js`(`data-density`). 둘 다 (a) CSP 가
# `script-src 'self'` 라 인라인이 불가능해 외부 파일이고, (b) 앱 번들보다 먼저
# 실행돼야 해서 TS SSOT 를 import 할 수 없으며, (c) 그래서 상수를 인라인한 뒤
# **그 인라인이 SSOT 와 어긋나지 않음을 테스트가 봉인**한다. 세 번째 축(lang)도
# 같은 형태를 쓴다 — 새 패턴을 발명할 이유가 없다.
I18N_MODULE = SRC_DIR / "i18n" / "index.ts"
LANG_BOOT_SCRIPT = WEB_ROOT / "public" / "lang-init.js"
INDEX_HTML = WEB_ROOT / "index.html"

#: `index.html` 안의 두 스크립트 참조. boot 스크립트가 앱 번들보다 **앞**이어야
#: 첫 페인트 전에 `lang` 이 정해진다.
LANG_BOOT_SCRIPT_SRC = "/lang-init.js"
APP_ENTRY_SCRIPT_SRC = "/src/main.tsx"

_LOCALE_STORAGE_KEY_RE = re.compile(r"LOCALE_STORAGE_KEY\s*=\s*'([^']+)'")
_DEFAULT_LOCALE_RE = re.compile(r"DEFAULT_LOCALE\s*:\s*Locale\s*=\s*'([^']+)'")
_SUPPORTED_LOCALES_RE = re.compile(r"SUPPORTED_LOCALES\s*=\s*\[([^\]]*)\]")
_HTML_LANG_RE = re.compile(r"<html[^>]*\slang=\"([^\"]*)\"")
_FCC_STORAGE_KEY_RE = re.compile(r"'(fcc-[a-z-]+)'")

#: 문서 언어를 **기록**하는 사이트. `dataset.lang` 형태는 `data-lang` 속성이라
#: 무관하므로 잡지 않는다 — 실제 `lang` 속성 기록만 본다.
_DOCUMENT_LANG_WRITE_RE = re.compile(
    r"\.lang\s*=(?!=)|setAttribute\(\s*['\"]lang['\"]"
)

#: `src/` 에서 문서 언어를 기록해도 되는 유일한 계층. 라우트/컴포넌트가 각자
#: `lang` 을 쓰면 로케일 SSOT 가 화면 수만큼 쪼개진다.
DOCUMENT_LANG_WRITER = "i18n/index.ts"


def _locale_ssot() -> tuple[str, tuple[str, ...], str]:
    """``src/i18n/index.ts`` 에서 (저장키, 지원 로케일, 기본 로케일) 을 읽는다."""
    src = I18N_MODULE.read_text(encoding="utf-8")
    key_match = _LOCALE_STORAGE_KEY_RE.search(src)
    default_match = _DEFAULT_LOCALE_RE.search(src)
    locales_match = _SUPPORTED_LOCALES_RE.search(src)
    assert key_match and default_match and locales_match, (
        "i18n 로케일 SSOT 를 파싱하지 못했다 — 상수 형태가 바뀌었다면 이 봉인의 "
        "정규식도 함께 갱신해야 한다(파싱 실패를 조용한 PASS 로 두지 않는다)"
    )
    locales = tuple(re.findall(r"'([^']+)'", locales_match.group(1)))
    return key_match.group(1), locales, default_match.group(1)


def _lang_boot_drift(
    boot_src: str,
    *,
    storage_key: str,
    locales: tuple[str, ...],
    default_locale: str,
) -> list[str]:
    """사전-페인트 boot 스크립트 ↔ i18n SSOT 드리프트 목록(빈 리스트 = 정합).

    **순수 함수**로 둔 이유는 비-공허성 때문이다 — 실제 파일만 검사하면 "검출기가
    무엇이든 통과시키는 상태"와 "파일이 정말 정합인 상태"를 구분할 수 없다. 합성
    offender 를 같은 함수에 먹여 red 가 나오는 것까지 확인해야 봉인이 성립한다.
    """
    defects: list[str] = []
    if f"'{storage_key}'" not in boot_src:
        defects.append(f"저장 키 '{storage_key}' 미인라인")
    for locale in locales:
        if f"'{locale}'" not in boot_src:
            defects.append(f"로케일 토큰 '{locale}' 미인라인")
    if f"'{default_locale}'" not in boot_src:
        defects.append(f"기본 로케일 '{default_locale}' 미인라인")
    if not _DOCUMENT_LANG_WRITE_RE.search(boot_src):
        defects.append("documentElement 의 lang 을 기록하지 않는다")
    stray = sorted({k for k in _FCC_STORAGE_KEY_RE.findall(boot_src) if k != storage_key})
    if stray:
        defects.append(f"다른 fcc-* 저장 키 참조: {stray}")
    return defects


def _function_body(src: str, anchor: str) -> str:
    """``anchor`` 이후 첫 ``{`` 부터 짝이 맞는 ``}`` 까지(중괄호 균형)."""
    start = src.index(anchor)
    open_idx = src.index("{", start)
    depth = 0
    for idx in range(open_idx, len(src)):
        if src[idx] == "{":
            depth += 1
        elif src[idx] == "}":
            depth -= 1
            if depth == 0:
                return src[open_idx : idx + 1]
    raise AssertionError(f"{anchor} 의 본문 중괄호가 닫히지 않았다")


class TestDocumentLangFollowsLocale(unittest.TestCase):
    """S2 — 문서 언어 선언이 활성 로케일과 **어긋나지 않는다**.

    세 지점이 한 사실을 말해야 한다: `index.html` 의 초기 속성, 사전-페인트
    boot 스크립트가 저장값에서 파생하는 값, 그리고 런타임 `setLocale`. 셋 중
    하나만 어긋나도 스크린리더는 잘못된 음성 엔진으로 읽는다.

    런타임 **동작**(로케일 토글 → `documentElement.lang` 추종)은 jsdom 이 필요해
    `apps/web/tests/lang-boot-ssot.test.ts`(S1) 가 맡는다. 여기서는 파일 간
    정합·사본 0·로드 순서라는 **구조**를 본다.
    """

    def test_the_locale_ssot_is_parsable(self) -> None:
        """공허 방지 — SSOT 파싱이 깨지면 아래 대조들이 전부 무의미해진다."""
        storage_key, locales, default_locale = _locale_ssot()
        self.assertTrue(storage_key.startswith("fcc-"))
        self.assertGreaterEqual(len(locales), 2)
        self.assertIn(default_locale, locales)

    def test_the_boot_script_mirrors_the_locale_ssot(self) -> None:
        storage_key, locales, default_locale = _locale_ssot()
        self.assertTrue(
            LANG_BOOT_SCRIPT.exists(),
            f"{LANG_BOOT_SCRIPT} 부재 — 사전-페인트 lang 결정이 사라지면 첫 "
            "페인트 구간의 문서 언어가 저장된 로케일과 어긋난다",
        )
        defects = _lang_boot_drift(
            LANG_BOOT_SCRIPT.read_text(encoding="utf-8"),
            storage_key=storage_key,
            locales=locales,
            default_locale=default_locale,
        )
        self.assertEqual(
            defects,
            [],
            "public/lang-init.js 가 src/i18n/index.ts 의 로케일 SSOT 와 어긋났다 "
            f"— boot 스크립트는 TS 를 import 할 수 없으므로 이 대조가 유일한 "
            f"드리프트 게이트다: {defects}",
        )

    def test_index_html_initial_lang_equals_the_default_locale(self) -> None:
        _, _, default_locale = _locale_ssot()
        html = INDEX_HTML.read_text(encoding="utf-8")
        match = _HTML_LANG_RE.search(html)
        self.assertIsNotNone(match, "index.html 의 <html> 에 lang 속성이 없다")
        assert match is not None  # for type checkers
        self.assertEqual(
            match.group(1),
            default_locale,
            "index.html 의 정적 lang 이 DEFAULT_LOCALE 과 다르다 — boot 스크립트가 "
            "실행되기 전(그리고 JS 가 비활성인 경우) 문서 언어는 이 값이므로, "
            "저장된 선택이 없는 사용자에게 잘못된 언어를 선언하게 된다",
        )

    def test_the_boot_script_runs_before_the_app_bundle(self) -> None:
        html = INDEX_HTML.read_text(encoding="utf-8")
        boot_idx = html.find(LANG_BOOT_SCRIPT_SRC)
        app_idx = html.find(APP_ENTRY_SCRIPT_SRC)
        self.assertNotEqual(
            boot_idx, -1, "index.html 이 /lang-init.js 를 더 이상 로드하지 않는다"
        )
        self.assertNotEqual(app_idx, -1, "index.html 에서 앱 진입점을 찾지 못했다")
        self.assertLess(
            boot_idx,
            app_idx,
            "lang boot 스크립트가 앱 번들보다 뒤에 로드된다 — 그러면 사전-페인트 "
            "보장이 사라지고 첫 페인트가 기본 로케일 언어로 선언된다",
        )

    def test_the_runtime_applies_the_locale_on_boot_and_on_every_switch(self) -> None:
        """런타임 반영을 **i18n 계층이 소유**한다(화면마다 effect 를 흩뿌리지 않음).

        `setLocale` 이 로케일 변경의 유일한 mutation 지점이므로, 거기서 문서 언어를
        함께 갱신하면 "토글했는데 lang 은 그대로" 상태가 구조적으로 불가능해진다.
        """
        src = _strip_ts_comments(I18N_MODULE.read_text(encoding="utf-8"))
        self.assertRegex(
            src,
            r"function\s+applyDocumentLang\s*\(",
            "i18n 계층에 문서 언어 적용 함수가 없다",
        )
        set_locale_body = _function_body(src, "export function setLocale")
        self.assertIn(
            "applyDocumentLang(",
            set_locale_body,
            "setLocale 이 문서 언어를 갱신하지 않는다 — 로케일 토글 후 <html lang> 이 "
            "이전 언어로 남는다",
        )
        # **정의가 아니라 호출**을 요구한다 — `applyDocumentLang(` 부분 문자열은
        # 함수 선언 자체에도 있으므로, 그대로 단언하면 호출이 사라져도 green 이다.
        # 모듈 최상위 호출은 들여쓰기가 없다는 사실로 구분한다.
        self.assertRegex(
            src,
            r"(?m)^applyDocumentLang\(",
            "모듈 로드 시점에 문서 언어가 적용되지 않는다 — 저장된 로케일이 "
            "index.html 의 정적 초기값과 다른 사용자에게 잘못된 언어가 남는다",
        )

    def test_document_lang_is_written_only_by_the_i18n_layer(self) -> None:
        """사본 0 — 하드게이트 8·9(로케일 토큰 하드코딩 금지)의 기계 판정."""
        writers = sorted(
            {
                site.split(":")[0]
                for site in _offending_sites(_DOCUMENT_LANG_WRITE_RE)
            }
        )
        self.assertEqual(
            writers,
            [DOCUMENT_LANG_WRITER],
            "문서 언어를 기록하는 사이트가 i18n 계층 밖으로 번졌다 — 로케일 토큰이 "
            f"화면 수만큼 사본이 되는 경로다: {writers}",
        )

    def test_the_drift_detector_fires_on_an_injected_defect(self) -> None:
        """비-공허성 — 검출기가 실제로 red 를 낼 수 있는지 합성 offender 로 확인."""
        storage_key, locales, default_locale = _locale_ssot()
        genuine = LANG_BOOT_SCRIPT.read_text(encoding="utf-8")
        for label, mutated in (
            ("저장 키 드리프트", genuine.replace(f"'{storage_key}'", "'fcc-language'")),
            (
                "기본 로케일 드리프트",
                genuine.replace(f"'{default_locale}'", "'de'"),
            ),
            ("lang 미기록", genuine.replace(".lang", ".dataset.locale")),
        ):
            with self.subTest(defect=label):
                self.assertNotEqual(
                    _lang_boot_drift(
                        mutated,
                        storage_key=storage_key,
                        locales=locales,
                        default_locale=default_locale,
                    ),
                    [],
                    f"검출기가 '{label}' 를 통과시켰다 — 봉인이 공허하다",
                )


# ── M-2 라우트 정의 계층 (fe-w4-a11y-error-resilience, 2026-07-31) ───────────
#
# 한 화면의 렌더 실패가 셸 전체를 죽은 상태로 고정시키던 구조의 정공. 라우터에
# ``errorElement`` 가 0건이었고 유일한 경계(``AppErrorBoundary``)는 ``<main>``
# **안**에 있어서 (a) 셸 자체의 실패를 볼 수 없었고 (b) location 이 바뀌어도
# fallback 이 남았다(react-router 의 ``RenderErrorBoundary`` 만이 location 변화로
# 에러를 해제한다).
#
# 여기서 봉인하는 것은 **선언의 전수성**이다 — 개별 경계의 동작은 vitest
# (``apps/web/tests/route-error-boundary.test.tsx``)가 실제 라우트 트리를 구동해
# 확인하고, 이 Python 봉인은 "새 라우트가 조용히 경계나 제목 없이 등록되는 것"을
# 막는다. 타입(``AppRoute``)이 1차 방어선이지만 타입은 CI 의 프론트 레인에서만
# 돌고, 이 레인은 백엔드 invariant 레인에서 함께 돈다.
APP_TSX = SRC_DIR / "app.tsx"
LAYOUT_TSX = SRC_DIR / "routes" / "_layout.tsx"
ERROR_BOUNDARY_TSX = SRC_DIR / "shared" / "error-boundary.tsx"


def _app_route_source() -> str:
    return _strip_ts_comments(APP_TSX.read_text(encoding="utf-8"))


#: 합성 라우트 트리의 뼈대. 실제 ``app.tsx`` 의 형상 넷을 갖는다 — 셸 + 자식,
#: ``index: true``, 다른 모듈에서 온 ``...spread``, 그리고 alias(``@/``) 지정자.
#: 반례를 심는 자리는 **이 테스트가 소유**하므로 프로덕션이 움직여도 흔들리지 않는다.
_SYNTHETIC_APP_TSX = """
import {{ extraRoutes }} from '@/shared/extra-routes';

export const appRoutes: readonly AppRoute[] = [
  {{
    path: '/',
    element: <Layout />,
    errorElement: <Shell />,
    handle: {{ titleKey: 'routes.layout.appTitle' }},
    children: [
      {{ index: true, element: <Home />, errorElement: <E />, handle: {{ titleKey: 'routes.home.title' }} }},
      ...extraRoutes,
    ],
  }},
];
"""

_SYNTHETIC_EXTRA_ROUTES = "export const extraRoutes = [{member}];\n"


@contextlib.contextmanager
def _copied_src_tree(**overrides: str):
    """실제 ``apps/web/src`` 사본에 파일 몇 개를 덮어쓴 임시 트리.

    변이를 **실제 파일**에 태우면서도 모듈 그래프를 온전히 유지한다 — 한 파일만
    복사하면 교차 모듈 라우트를 쓰는 순간 그 봉인이 자기가 만든 기능에 걸린다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "src"
        shutil.copytree(SRC_DIR, root)
        for name, text in overrides.items():
            (root / name).write_text(text, encoding="utf-8")
        yield root


@contextlib.contextmanager
def _synthetic_route_tree(
    *,
    spread_member: str = (
        "{ path: 'ok', element: <X/>, errorElement: <E/>, handle: { titleKey: 'a.b' } }"
    ),
    mutate_entry: "Callable[[str], str] | None" = None,
):
    """A throwaway ``src`` tree the route derivation can be pointed at.

    ``collect_route_entries`` takes its root as an ARGUMENT for exactly this —
    a detector that only ever runs against the real tree cannot distinguish
    "nothing is wrong" from "I match nothing", and this axis was defeated by the
    second of those.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        entry = _SYNTHETIC_APP_TSX.format()
        if mutate_entry is not None:
            entry = mutate_entry(entry)
        (root / "app.tsx").write_text(entry, encoding="utf-8")
        (root / "shared").mkdir()
        (root / "shared" / "extra-routes.tsx").write_text(
            _SYNTHETIC_EXTRA_ROUTES.format(member=spread_member), encoding="utf-8"
        )
        yield root


def _conversion_site_body(app_source: str) -> str:
    """``toRouteObjects`` 의 본문 — 괄호 매칭으로.

    ⚠️ 라우터의 **인자** 를 못 박는 것만으로는 부족하다: 그 인자가 부르는 변환 함수가
    같은 파일에 있고, 거기서 더해지는 원소는 어떤 파생에도 보이지 않는다.
    """
    masked = mask_ts_noncode(app_source)
    anchor = "function toRouteObjects"
    at = masked.find(anchor)
    if at < 0:
        return ""
    brace = masked.find("{", at)
    if brace < 0:
        return ""
    close = match_brackets(masked).get(brace)
    if close is None:
        return ""
    return _strip_ts_comments(app_source[brace:close])


#: 변환 함수의 본문이 시작해야 하는 방식. **한 문장으로 전부를 말한다** — 입력 배열을
#: 그대로 사상하고 그 밖의 것을 만들지 않는다. `[...compat, ...routes.map(` 도,
#: `routes === appRoutes ? … : …` 도 이 한 줄에 걸린다.
CONVERSION_SITE_SHAPE = "returnroutes.map("


def _conversion_site_defects(app_source: str) -> "list[str]":
    """변환 함수가 자기 라우트를 등록하는 자리들.

    ⚠️ 첫 판은 `path\s*:\s*(?!route\.)` 같은 lookahead 정규식이었고, `\s*` 가 **0자로
    역추적**해 lookahead 를 공백 위치에서 평가하는 바람에 정상 코드를 위반으로 읽었다.
    같은 교훈이다 — 구조로 물어라.
    """
    body = _conversion_site_body(app_source)
    if not body:
        return ["변환 함수 본문을 찾지 못했다"]
    defects: list[str] = []
    if not re.sub(r"\s+", "", body).startswith("{" + CONVERSION_SITE_SHAPE):
        defects.append(f"본문이 `{CONVERSION_SITE_SHAPE}` 로 시작하지 않는다: {body.strip()[:70]!r}")
    try:
        literals = iter_ts_object_literals(body)
    except TsUnbalancedRegionError as exc:
        # 못 읽은 것을 없는 것으로 세지 않는다 — 이 모듈의 규율 그대로.
        return [*defects, f"변환 함수 본문을 읽을 수 없다: {exc}"]
    for literal in literals:
        for entry in literal.entries:
            if entry.kind == "spread" and (entry.key or "") not in ("", "route"):
                defects.append(f"본문에 `...{entry.key}` 전개가 있다")
            if entry.kind != "entry" or entry.key not in ("path", "index"):
                continue
            value = entry.value.strip().rstrip(",")
            if value not in ("route.path", "true"):
                defects.append(f"본문이 `{entry.key}: {value[:40]}` 로 라우트를 만든다")
    return defects


def _router_argument(app_source: str) -> str:
    """``createBrowserRouter(<here>)`` — the argument, read by bracket matching.

    ⚠️ Structure, not a regex. The first version used ``\)\s*\)`` and silently
    swallowed one paren too many, so the assertion compared two things neither of
    which was the argument. This is the same lesson the rest of the wave is about,
    applied to the seal that guards **registration** rather than declaration.
    """
    masked = mask_ts_noncode(app_source)
    anchor = "createBrowserRouter("
    at = masked.find(anchor)
    if at < 0:
        return ""
    open_paren = at + len(anchor) - 1
    close = match_brackets(masked).get(open_paren)
    if close is None:
        return ""
    argument = app_source[open_paren + 1 : close - 1]
    # ⚠️ `createBrowserRouter(routes, { future })` is legitimate — the second argument
    # is options, not routes. Comparing the whole argument text would red on a
    # perfectly ordinary opt-in, and a gate that refuses ordinary code gets deleted.
    masked_argument = mask_ts_noncode(argument)
    depth = 0
    for index, char in enumerate(masked_argument):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            argument = argument[:index]
            break
    return re.sub(r"\s+", "", argument)


def _registered_routes() -> "tuple[RouteEntry, ...]":
    """등록된 라우트 전부 — **모듈 그래프**에서 파생.

    옛 형태는 ``app.tsx`` 소스를 텍스트로 잘라 ``path:`` 리터럴을 세는 것이었고,
    같은 정규식이 이 파일에 **다섯 벌** 있었다. 그 파생이 답하는 질문은
    *"app.tsx 에 어떤 path 리터럴이 적혀 있나"* 이지 *"무엇이 등록됐나"* 가 아니다 —
    독립 적대 평가가 다른 모듈의 라우트를 ``...spread`` 로 들여와 다섯 봉인을 전부
    통과시켰다. 파생 전체는 ``support.frontend_route_registry`` 가 소유하고, 그것이
    따라갈 수 없는 원소를 만나면 **조용히 건너뛰지 않고 예외로 답한다**.
    """
    return collect_route_entries(SRC_DIR)


class TestEveryRegisteredRouteDeclaresABoundaryAndATitle(unittest.TestCase):
    """S3 선언부 — 라우트 오브젝트마다 ``errorElement`` 와 ``handle.titleKey``.

    ⚠️ **부등호도 개수 상등도 아니고 전수 술어다.** 앞 세대는 ``errorElement:``
    출현 수 == 라우트 수를 단언했는데, 개수 상등은 *어느* 라우트가 빠졌는지 말하지
    못할 뿐 아니라 **블록을 좁히는 것만으로** 양변이 함께 줄어 공허해졌다(실측:
    헬퍼 호이스팅 재배치 하나로 23 라우트가 1 이 됐고 그 상태에서 경계를 지워도
    전량 green). 지금은 파생된 **라우트 객체마다** 묻고 실패는 그 주소를 댄다.

    ⚠️ ``tsc`` 가 이 둘을 이미 필수로 요구한다(``AppRouteCommon``). 그래도 여기서
    다시 묻는 이유는 두 가지다 — 타입 레인은 프론트 CI 에서만 돌고, 그리고
    ``titleKey`` 가 **로케일에서 해소되는가** 는 타입이 원리적으로 답할 수 없는
    질문이라 아래 세 번째 검사가 이 파생 없이는 존재할 수 없다.
    """

    def test_every_route_declares_an_error_boundary(self) -> None:
        entries = _registered_routes()
        self.assertGreater(len(entries), 0, "라우트를 하나도 파생하지 못했다 — 스캔이 공허하다")
        unguarded = sorted(entry.identity for entry in entries if not entry.has_error_element)
        self.assertEqual(
            unguarded,
            [],
            "경계 없이 등록된 라우트가 있다 — 그 화면의 렌더 실패가 셸을 통째로 "
            f"대체한다: {unguarded}",
        )

    def test_every_route_declares_a_title_key(self) -> None:
        entries = _registered_routes()
        self.assertGreater(len(entries), 0, "라우트를 하나도 파생하지 못했다 — 스캔이 공허하다")
        untitled = sorted(entry.identity for entry in entries if entry.title_key is None)
        self.assertEqual(
            untitled,
            [],
            f"제목 없이 등록된 라우트가 있다 — 탭 제목이 이전 화면 것으로 남는다: {untitled}",
        )

    def test_a_route_spread_in_from_another_module_is_still_seen(self) -> None:
        """비-공허성 — 이 클래스가 막겠다는 그 반례를 실제로 잡는가.

        합성 트리다. 실제 ``app.tsx`` 만 보는 단언은 *"위반이 없다"* 와 *"검출기가
        아무것도 매칭하지 못한다"* 를 구분하지 못하고, 이 축이 뚫린 방식이 정확히
        후자였다.
        """
        clean = "{ path: 'ok', element: <X/>, errorElement: <E/>, handle: { titleKey: 'a.b' } }"
        naked = "{ path: 'sneaky', element: <X/>, handle: { titleKey: 'a.b' } }"
        for label, member, expected in (
            ("경계 있음", clean, []),
            ("경계 없음", naked, ["/sneaky"]),
        ):
            with self.subTest(label):
                with _synthetic_route_tree(spread_member=member) as root:
                    entries = collect_route_entries(root)
                    self.assertEqual(
                        sorted(e.address for e in entries if not e.has_error_element),
                        expected,
                        "다른 모듈에서 전개된 라우트가 파생에 들어오지 않는다 — "
                        "그 화면은 이 봉인에게 존재하지 않는다",
                    )

    def test_an_unfollowable_member_is_loud_not_skipped(self) -> None:
        """완전성을 주장하는 파생은 **못 읽은 것을 없는 것으로 셀 수 없다.**"""
        for label, mutate in (
            ("import 없는 전개", lambda src: src.replace("import { extraRoutes }", "// gone")),
            ("호출식 전개", lambda src: src.replace("...extraRoutes", "...makeRoutes()")),
        ):
            with self.subTest(label):
                with _synthetic_route_tree(mutate_entry=mutate) as root:
                    with self.assertRaises(UnresolvedRouteElementError):
                        collect_route_entries(root)

    def test_removing_a_boundary_from_the_real_file_is_seen(self) -> None:
        """변이는 **실제 파일**에도 태운다.

        합성 트리는 검출기가 *작동한다* 를 보이고, 이것은 검출기가 *이 파일을 보고
        있다* 를 보인다. 둘은 다른 명제이고, 앞 세대는 후자에서 실패했다(잘라내는
        앵커가 밀려 실제 파일의 22 라우트를 보지 못한 채 green 이었다).
        """
        original = APP_TSX.read_text(encoding="utf-8")
        self.assertIn("errorElement: ROUTE_ERROR_ELEMENT,", original)
        mutated = original.replace("errorElement: ROUTE_ERROR_ELEMENT,", "", 1)
        # ⚠️ 트리를 **통째로** 복사한다. `app.tsx` 한 파일만 옮기면 이 웨이브가 추가한
        # 바로 그 능력 — 다른 모듈의 라우트를 따라가는 것 — 이 처음 쓰이는 날 이 검사가
        # 모듈을 못 찾아 red 가 된다. 봉인이 자기가 만든 기능에 걸리면 안 된다.
        with _copied_src_tree(**{APP_TSX.name: mutated}) as root:
            entries = collect_route_entries(root)
        self.assertEqual(
            len([entry for entry in entries if not entry.has_error_element]),
            1,
            "실제 app.tsx 에서 경계를 하나 지워도 파생이 반응하지 않는다 — 이 봉인은 "
            "이 파일을 보고 있지 않다",
        )

    #: 실제 `app.tsx` 에 심는 **스코프 그림자** 반례. 함수 지역 바인딩이 텍스트상
    #: 먼저 나오므로, 선언 해소가 "첫 매치"이면 빈 배열을 읽고 라우트 하나가 통째로
    #: 사라진다 — 그리고 그 라우트의 `titleKey` 는 어느 로케일에도 없다.
    #: 독립 적대 평가가 이 형태로 전량 green 을 받아냈고, `tsc` 도 통과한다.
    SHADOWING_COUNTEREXAMPLE = """
function noExtraRoutes(): readonly AppRoute[] {
  const hiddenRoutes: readonly AppRoute[] = [];
  return hiddenRoutes;
}

const hiddenRoutes: readonly AppRoute[] = [
  {
    path: 'sneaky',
    element: <NotFoundRoute />,
    errorElement: ROUTE_ERROR_ELEMENT,
    handle: { titleKey: 'routes.sneaky.thisKeyDoesNotExist' },
  },
];
"""

    #: 라우터가 만들어지는 자리. **선언이 아니라 등록**이고, 이 웨이브의 축 전체가
    #: 선언만 물었다. 정확히 이 표현이어야 한다 — 배열을 여기서 늘리면 그 라우트는
    #: `appRoutes` 를 읽는 모든 파생 밖에 있다.
    ROUTER_CONSTRUCTION = "createBrowserRouter(toRouteObjects(appRoutes))"

    def test_the_router_is_built_from_the_derived_array_and_nothing_else(self) -> None:
        """⚠️ **등록을 묻는 유일한 검사.** 나머지는 전부 *선언* 을 묻는다.

        독립 평가가
        ``createBrowserRouter([...toRouteObjects(appRoutes), { path: '/beta', … }])``
        로 경계도 제목도 없는 화면을 등록하고 **전량 초록 + `tsc` 0 + vitest 통과**를
        받아냈다. 그 라우트는 이 웨이브의 모든 파생 밖에 있다 — 파생은 `appRoutes` 에서
        시작하는데 등록은 그 뒤에서 일어나기 때문이다.

        ⚠️ 라운드 3 이 더한 *"배열이 호출로 빠져나갔다"* 가드는 **모듈 스코프 전용**이고
        진짜 등록 자리는 ``App()`` 안이라, 그 가드는 원리적으로 이 자리를 볼 수 없다.
        """
        argument = _router_argument(_app_route_source())
        self.assertEqual(
            argument,
            "toRouteObjects(appRoutes)",
            f"라우터가 파생된 배열 그것만으로 만들어지지 않는다: {argument!r} — 그 표현 밖에서 "
            "더해지는 라우트는 이 파일의 어떤 파생에도 보이지 않는다",
        )

    def test_the_conversion_site_registers_nothing_of_its_own(self) -> None:
        """⚠️ **변환 사이트가 곧 등록 사이트다.**

        앞 판은 `createBrowserRouter(...)` 의 **인자**만 못 박았다. 그러나 그 인자는
        `toRouteObjects(appRoutes)` 이고, **그 함수가 한 줄 옆에 있다** — 그리고
        `RouteObject` 는 `errorElement` 도 `handle` 도 요구하지 않으므로 타입 체계가
        오히려 그 자리를 권한다(리다이렉트·호환 별칭·프로브가 자연스럽게 가는 곳).

        독립 평가가 거기에 `/beta` 를 넣어 **경계도 제목도 announcer 도 없는 라우트**를
        등록하고 `tsc` 0 + 전량 초록을 받아냈다. 그 라우트는 이 웨이브의 **모든** 파생
        밖에 있다 — 경계·제목·로케일·announcer·a11y·반응형 전부.

        묻는 명제: **변환 함수는 자기 것을 등록하지 않는다.** 그 본문에는 라우트를 만드는
        리터럴(`path:`/`index:`)도, 결과에 무언가를 더하는 전개도 없어야 한다.
        """
        defects = _conversion_site_defects(_app_route_source())
        self.assertEqual(
            defects,
            [],
            "변환 함수가 자기 것을 등록한다 — 그 원소들은 파생 밖이라 경계·제목·로케일·"
            f"announcer 어느 검사도 보지 못한다: {defects}",
        )

    def test_the_conversion_seal_would_catch_an_injected_route(self) -> None:
        """비-공허성 — 평가자가 실제로 심은 그 형태."""
        source = _app_route_source()
        # ⚠️ 앵커는 **본문의 첫 줄** 하나다 — 시그니처를 통째로 적으면 그 시그니처가
        # 정당하게 바뀌는 날 반례가 조용히 적용되지 않고 이 검사는 초록으로 남는다.
        planted = source.replace(
            "  return routes.map((route) =>",
            "  const compatRoutes: RouteObject[] = [{ path: '/beta', element: <div /> }];\n"
            "  return [...compatRoutes].concat(routes.map((route) =>",
            1,
        ).replace("\n  );\n}", "\n  ));\n}", 1)
        self.assertNotEqual(planted, source, "반례가 적용되지 않았다 — 앵커가 밀렸다")
        self.assertNotEqual(
            _conversion_site_defects(planted),
            [],
            "변환 함수 안에 라우트를 주입해도 단언이 반응하지 않는다",
        )

    def test_the_router_seal_would_catch_an_appended_route(self) -> None:
        """비-공허성 — 평가자가 실제로 심은 그 형태."""
        source = _app_route_source()
        mutated = source.replace(
            self.ROUTER_CONSTRUCTION,
            "createBrowserRouter([...toRouteObjects(appRoutes), { path: '/beta' }])",
            1,
        )
        self.assertNotEqual(mutated, source, "반례가 적용되지 않았다 — 앵커가 밀렸다")
        self.assertNotEqual(
            _router_argument(mutated),
            "toRouteObjects(appRoutes)",
            "라우터 인자에 라우트를 덧붙여도 단언이 반응하지 않는다",
        )

    def test_a_function_local_binding_does_not_shadow_the_route_array(self) -> None:
        """선언 해소는 **스코프**를 본다 — 첫 텍스트 매치가 아니다.

        옛 판은 `_declaration_re(name).search(masked)` 였고, 그 한 줄이 이 모듈 전체를
        무력화했다: 평범해 보이는 헬퍼가 같은 이름을 함수 지역에 바인딩하면 그것이
        검색에서 이기고, 걷기는 빈 배열을 읽고, 라우트 하나가 **로케일에 없는
        titleKey 를 달고** 조용히 등록된다. `tsc` 는 아무 의견이 없다.
        """
        original = APP_TSX.read_text(encoding="utf-8")
        mutated = original.replace(
            "export const appRoutes", self.SHADOWING_COUNTEREXAMPLE + "\nexport const appRoutes", 1
        ).replace("      ...gridPocRoutes,", "      ...gridPocRoutes,\n      ...hiddenRoutes,", 1)
        self.assertNotEqual(mutated, original, "반례가 적용되지 않았다 — 앵커가 밀렸다")
        with _copied_src_tree(**{APP_TSX.name: mutated}) as root:
            entries = collect_route_entries(root)
        self.assertIn(
            "routes.sneaky.thisKeyDoesNotExist",
            [entry.title_key for entry in entries],
            "함수 지역 바인딩이 모듈 스코프 선언을 가리고, 그 아래 라우트가 통째로 "
            "파생에서 사라졌다 — 로케일 검사가 그 화면을 영영 보지 못한다",
        )

    def test_two_module_scope_declarations_are_refused(self) -> None:
        """둘 중 하나를 조용히 고르는 것이 그림자가 숨는 방법이다."""
        original = APP_TSX.read_text(encoding="utf-8")
        mutated = original.replace(
            "export const appRoutes",
            "const gridPocRoutes: readonly AppRoute[] = [];\nexport const appRoutes",
            1,
        )
        self.assertNotEqual(mutated, original, "반례가 적용되지 않았다")
        with _copied_src_tree(**{APP_TSX.name: mutated}) as root:
            with self.assertRaises(UnresolvedRouteElementError):
                collect_route_entries(root)

    def test_a_behaviour_preserving_relocation_does_not_change_the_answer(self) -> None:
        """옛 봉인을 무너뜨린 그 재배치가 이제 답을 바꾸지 못한다."""
        with _synthetic_route_tree() as root:
            before = collect_route_entries(root)
            entry = root / "app.tsx"
            entry.write_text(
                "function toRouteObjects(r) { return r; }\n" + entry.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            after = collect_route_entries(root)
        self.assertEqual(
            sorted(e.address for e in before),
            sorted(e.address for e in after),
            "함수 선언을 배열 위로 올리는 것만으로 파생된 라우트 집합이 달라진다 — "
            "옛 텍스트 잘라내기가 뚫린 바로 그 형태",
        )

    def test_every_title_key_resolves_in_both_locales(self) -> None:
        keys = [entry.title_key for entry in _registered_routes() if entry.title_key]
        self.assertGreater(len(keys), 0, "titleKey 리터럴을 찾지 못했다")
        for locale in ("ko", "en"):
            messages = _flatten_messages(
                json.loads((SRC_DIR / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
            )
            for key in keys:
                self.assertIn(
                    key,
                    messages,
                    f"{locale}.json 에 문서 제목 키 `{key}` 가 없다 — 화면 이름 자리에 "
                    "점 표기 키가 그대로 노출된다",
                )
                self.assertTrue(
                    messages[key].strip(),
                    f"{locale}.json 의 `{key}` 가 빈 문자열이다",
                )


class TestErrorBoundaryLayersStaySeparate(unittest.TestCase):
    """S5/S6 선언부 — 세 층이 각자 자리에 있고, 잔여 그물이 사라지지 않는다.

    ``errorElement`` 를 도입하면서 최상위 그물을 없애면 라우터 렌더 트리 **밖**의
    실패를 잡을 것이 사라진다(계약 M2 "최상위 그물 제거 금지"). 위치도 함께 봉인하는
    이유는 **실측**이다 — 자식 라우트의 ``RenderErrorBoundary`` 는 ``<Outlet/>``
    **안**에서 렌더되므로 ``<main>`` 의 경계보다 아래에 있고, 그 자리의 경계를
    되돌려도 route-error vitest 8건이 전부 green 이었다. 즉 그 위치의 그물은
    아무것도 잡지 못하는 **죽은 두 번째 그물**이다.
    """

    def test_the_three_layer_components_exist(self) -> None:
        src = ERROR_BOUNDARY_TSX.read_text(encoding="utf-8")
        for symbol in ("AppErrorBoundary", "RouteErrorPage", "ShellErrorPage"):
            self.assertRegex(
                src,
                rf"export function {symbol}\b",
                f"shared/error-boundary.tsx 가 `{symbol}` 를 내보내지 않는다",
            )

    def test_residual_net_moved_out_of_the_main_column(self) -> None:
        layout = _strip_ts_comments(LAYOUT_TSX.read_text(encoding="utf-8"))
        self.assertNotIn(
            "AppErrorBoundary",
            layout,
            "_layout.tsx 안의 경계는 라우트층(<Outlet/> 안)보다 위라 route error 를 "
            "영영 보지 못하는 죽은 그물이다 — 잔여 그물은 RouterProvider 를 감싼다",
        )
        self.assertIn(
            "AppErrorBoundary",
            _app_route_source(),
            "잔여 그물이 사라졌다 — 라우터 렌더 트리 밖의 실패를 잡을 것이 없다",
        )

    def test_shell_and_route_layers_use_distinct_fallbacks(self) -> None:
        src = _app_route_source()
        for symbol in ("RouteErrorPage", "ShellErrorPage"):
            self.assertIn(
                symbol,
                src,
                f"app.tsx 가 `{symbol}` 를 배선하지 않았다 — 두 층의 역할 차이가 "
                "코드에 없다",
            )

    def test_fallbacks_never_render_a_stack_trace(self) -> None:
        src = _strip_ts_comments(ERROR_BOUNDARY_TSX.read_text(encoding="utf-8"))
        self.assertNotIn(
            ".stack",
            src,
            "fallback 이 스택을 화면에 노출한다 — 전체 스택은 Sentry 로만 간다"
            "(ADR-0006)",
        )


ROUTE_ANNOUNCER_TSX = SRC_DIR / "shared" / "route-announcer.tsx"

#: `src/` 에서 문서 제목을 **기록**해도 되는 유일한 모듈. 화면마다
#: `document.title = …` 를 두면 라우트 수만큼 사본이 생기고 새 라우트에서 잊는다.
DOCUMENT_TITLE_WRITER = "shared/route-announcer.tsx"

_DOCUMENT_TITLE_WRITE_RE = re.compile(r"document\.title\s*=(?!=)")

#: `<main id="...">` 의 id 와 skip-link 의 `href="#..."` 프래그먼트.
_MAIN_ID_RE = re.compile(r"<main\b[^>]*\sid=\"([^\"]+)\"")
_SKIP_LINK_HREF_RE = re.compile(r"<a\s+href=\"#([^\"]+)\"\s+className=\"skip-link\"")
_MAIN_TAG_RE = re.compile(r"<main\b[^>]*>")


def _document_title_writers(root: Path) -> list[str]:
    """``document.title`` 을 기록하는 `src/` 상대 경로 목록(정렬).

    **순수 함수 + 인자 주입**인 이유는 비-공허성 때문이다 — 실제 트리만 스캔하면
    "검출기가 아무것도 매칭하지 못하는 상태"와 "정말 사본이 없는 상태"가 구분되지
    않는다. 합성 offender 트리를 같은 함수에 먹여 red 를 확인한다.
    """
    writers: list[str] = []
    for path in sorted(root.rglob("*.ts*")):
        if _DOCUMENT_TITLE_WRITE_RE.search(_strip_ts_comments(path.read_text(encoding="utf-8"))):
            writers.append(path.relative_to(root).as_posix())
    return writers


class TestRouteTitleAndFocusAreConsumed(unittest.TestCase):
    """S10/S12 선언부 — ``handle.titleKey`` 가 **읽히고**, 포커스가 이동한다.

    M-2 는 라우트마다 ``titleKey`` 를 선언하게 만들었다(``tsc`` 강제). 선언만 하고
    아무도 읽지 않는 상태는 선언 봉인에게 **완전히 green 으로 보인다** — 그래서
    소비 축을 따로 봉인한다. 여기서 보는 것은 세 가지다: 제목 기록 사이트가
    하나뿐인가, 그 사이트가 라우트 handle 에서 **파생**하는가(두 번째 path→title
    맵 금지), 그리고 포커스 대상(``<main>``)이 실제로 포커스를 받을 수 있는가.
    """

    def test_document_title_has_exactly_one_writer(self) -> None:
        self.assertEqual(
            _document_title_writers(SRC_DIR),
            [DOCUMENT_TITLE_WRITER],
            "문서 제목 기록 사이트가 하나가 아니다 — 화면마다 제목을 쓰기 시작하면 "
            "새 라우트에서 잊는 것이 기본값이 된다",
        )

    def test_the_writer_detector_is_not_vacuous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "routes").mkdir()
            (root / "routes" / "offender.tsx").write_text(
                "export function X(): void {\n  document.title = 'hand-rolled';\n}\n",
                encoding="utf-8",
            )
            (root / "routes" / "innocent.tsx").write_text(
                "// document.title = 'in a comment only'\n"
                "export const NAME = 'document.title mentioned in a string';\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _document_title_writers(root),
                ["routes/offender.tsx"],
                "검출기가 합성 offender 를 잡지 못했거나 주석/문자열을 오탐한다 — "
                "그렇다면 실제 트리에 대한 PASS 는 아무 의미가 없다",
            )

    def test_the_title_is_derived_from_the_route_handle(self) -> None:
        src = _strip_ts_comments(ROUTE_ANNOUNCER_TSX.read_text(encoding="utf-8"))
        self.assertIn(
            "useMatches(",
            src,
            "제목을 라우트 매치에서 파생하지 않는다 — pathname 으로 분기하면 "
            "app.tsx 의 titleKey 선언과 별개인 두 번째 맵이 생긴다",
        )
        self.assertNotIn(
            "location.pathname",
            src,
            "제목이 pathname 에서 파생된다 — 라우트 등록 집합이 아니라 손수 관리되는 "
            "경로 목록에 의존하게 된다",
        )

    def test_title_key_is_declared_and_read_in_exactly_two_places(self) -> None:
        readers = sorted(
            path.relative_to(SRC_DIR).as_posix()
            for path in SRC_DIR.rglob("*.ts*")
            if "titleKey" in _strip_ts_comments(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(
            readers,
            ["app.tsx", DOCUMENT_TITLE_WRITER],
            "titleKey 를 만지는 파일이 선언(app.tsx)과 소비(route-announcer) 둘이 "
            "아니다 — 세 번째 사이트는 곧 두 번째 매핑이다",
        )

    def test_every_top_level_route_element_is_announced(self) -> None:
        """최상위 라우트 **전원**이 announcer 로 감싸져 있다.

        ``/auth/callback`` 은 ``/`` 의 자식이 아니라 **형제**라, announcer 를
        ``AppLayout`` 안에만 두면 그 화면만 조용히 빠진다. 최상위 라우트는 절대
        경로(``/`` 로 시작)로 선언되므로 그 수를 파생해서 상등을 단언한다 —
        하드코딩 2 는 라우트가 늘어나도 green 으로 남는다.
        """
        top_level = [entry for entry in _registered_routes() if entry.top_level]
        self.assertGreaterEqual(
            len(top_level),
            2,
            "최상위(절대 경로) 라우트를 찾지 못했다 — 스캔이 공허하다",
        )
        # ⚠️ 이 자리는 파일 전체에서 `<RouteAnnouncer>` 를 **정규식으로 세어** 파생된
        # 라우트 수와 비교했다. 한쪽은 파생이고 다른 쪽은 텍스트 개수라, 라우트 하나가
        # announcer 없이 등록되고 **다른 자리에** announcer 가 하나 더 있으면 두 수가
        # 같아 통과한다. 그리고 `RouteEntry.element` 는 정확히 이 질문을 위해 더해졌는데
        # 아무도 읽지 않고 있었다(독립 평가 지적). 이제 라우트마다 **자기 element** 에
        # 대해 묻는다 — 개수가 아니라 대응이다.
        unannounced = sorted(
            entry.address for entry in top_level if "<RouteAnnouncer>" not in entry.element
        )
        self.assertEqual(
            unannounced,
            [],
            "announcer 로 감싸지지 않은 최상위 라우트가 있다 — 그 화면은 탭 제목이 "
            f"이전 화면 것으로 남고 스크린리더가 전환을 알리지 못한다: {unannounced}",
        )

    def test_the_announcement_region_is_polite(self) -> None:
        src = _strip_ts_comments(ROUTE_ANNOUNCER_TSX.read_text(encoding="utf-8"))
        self.assertIn(
            'role="status"',
            src,
            "라우트 알림에 polite live region 이 없다 — 제목 변경만으로는 "
            "스크린리더가 SPA 전환을 안정적으로 읽지 않는다",
        )
        self.assertNotIn(
            'role="alert"',
            src,
            "라우트 전환을 assertive 로 알린다 — 이동할 때마다 작업이 중단된다",
        )

    def test_main_column_can_actually_receive_focus(self) -> None:
        layout = _strip_ts_comments(LAYOUT_TSX.read_text(encoding="utf-8"))
        main_tag = _MAIN_TAG_RE.search(layout)
        self.assertIsNotNone(main_tag, "_layout.tsx 에서 <main> 을 찾지 못했다")
        assert main_tag is not None  # for type-checkers
        self.assertIn(
            "tabIndex={-1}",
            main_tag.group(0),
            "<main> 이 프로그램적으로 포커스 불가다 — 라우트 전환 포커스 이동도, "
            "skip-link 도 실제로는 포커스를 옮기지 못한다(스크롤만 된다)",
        )

    def test_skip_link_target_matches_the_main_id(self) -> None:
        layout = _strip_ts_comments(LAYOUT_TSX.read_text(encoding="utf-8"))
        main_id = _MAIN_ID_RE.search(layout)
        skip_href = _SKIP_LINK_HREF_RE.search(layout)
        self.assertIsNotNone(main_id, "<main> 의 id 를 찾지 못했다")
        self.assertIsNotNone(skip_href, "skip-link 의 href 를 찾지 못했다")
        assert main_id is not None and skip_href is not None  # for type-checkers
        self.assertEqual(
            skip_href.group(1),
            main_id.group(1),
            "skip-link 가 가리키는 프래그먼트와 <main> 의 id 가 다르다 — 링크는 "
            "보이지만 아무 데로도 가지 않는다",
        )

    def test_focus_move_is_keyed_on_the_pathname_only(self) -> None:
        """포커스 이동이 **경로 변화**에만 반응한다.

        의존성이 없거나(매 렌더) ``location`` 전체이면(쿼리스트링 변화 포함)
        필터·페이지네이션 중에 포커스를 빼앗는다. vitest 가 행동을 단언하고,
        여기서는 그 행동을 만드는 **의존성 선언**을 봉인한다.
        """
        layout = _strip_ts_comments(LAYOUT_TSX.read_text(encoding="utf-8"))
        self.assertRegex(
            layout,
            r"mainRef\.current\?\.focus\(\);\s*\}, \[pathname\]\);",
            "라우트 전환 포커스 이동의 의존성이 [pathname] 이 아니다 — 매 렌더나 "
            "쿼리스트링 변화에도 포커스를 빼앗게 된다",
        )

    def test_static_document_title_matches_the_app_title_ssot(self) -> None:
        """React 부팅 **이전**에 보이는 제목도 SSOT 와 어긋나면 안 된다.

        `<html lang>` 초기값과 같은 성격이다 — 정적 값은 죽은 값이 아니라 부팅 전
        구간의 실제 문서 제목이다.
        """
        _, _, default_locale = _locale_ssot()
        messages = _flatten_messages(
            json.loads(
                (SRC_DIR / "locales" / f"{default_locale}.json").read_text(encoding="utf-8")
            )
        )
        static_title = re.search(r"<title>([^<]*)</title>", INDEX_HTML.read_text(encoding="utf-8"))
        self.assertIsNotNone(static_title, "index.html 에서 <title> 을 찾지 못했다")
        assert static_title is not None  # for type-checkers
        self.assertEqual(
            static_title.group(1).strip(),
            messages["routes.layout.appTitle"].strip(),
            "index.html 의 정적 제목이 기본 로케일의 appTitle 과 다르다 — 앱이 "
            "부팅되기 전 구간에서 다른 이름이 보인다",
        )


# ---------------------------------------------------------------------------
# S9 — assertive live regions stay on the urgent axis (W4-A M4).
# ---------------------------------------------------------------------------

LIVE_REGION_TS = SRC_DIR / "ui" / "live-region.ts"
LIVE_REGION_SSOT_REL = "ui/live-region.ts"

#: JSX 로 손수 선언한 **assertive** live region. 세 형태를 모두 잡는다 —
#: 리터럴(``role="alert"``), 동적(``role={x ? 'alert' : 'note'}`` / ``role={MAP[k]}``
#: 처럼 표현식 안에 ``'alert'`` 가 나타나는 것), 그리고 ``aria-live="assertive"``.
#: 계약이 센 "11곳"은 ``grep -c`` 가 **줄**을 센 값이라 주석 5줄이 섞여 있었고,
#: 동시에 동적 2곳(``StatusBadge`` / ``projects`` 의 sync 배너)을 **놓치고 있었다**.
#: 리터럴만 보는 스캔은 바로 그 2곳에 영원히 green 이므로 여기서는 표현식도 본다.
_HAND_ROLLED_ASSERTIVE_RE = re.compile(
    r"""role\s*=\s*(?:"alert"|'alert'|\{[^}]*['"]alert['"][^}]*\})"""
    r"""|aria-live\s*=\s*(?:"assertive"|'assertive'|\{[^}]*['"]assertive['"][^}]*\})"""
)

#: ``liveRegionProps('<kind>')`` 호출 — 어느 파일이 어느 축을 소비하는가.
_LIVE_REGION_CALL_RE = re.compile(r"liveRegionProps\(\s*'([A-Za-z]+)'\s*\)")

#: ``export type LiveRegionKind = | 'a' | 'b' …`` 의 유니언 멤버.
_LIVE_REGION_KIND_UNION_RE = re.compile(
    r"export type LiveRegionKind\s*=\s*((?:\s*\|\s*'[A-Za-z]+')+)\s*;"
)

#: 룰링 테이블의 한 항목 (``  kind: { … },``).
_LIVE_REGION_RULING_BLOCK_RE = re.compile(r"^  ([A-Za-z]+): \{(.*?)^  \},", re.S | re.M)

#: **감소 래칫** — assertive 축을 소비해도 되는 파일 집합. 늘리는 것은 설계 결정이고
#: 이 줄을 고치는 사람이 그 결정을 내리는 사람이다. 네 파일 전부 같은 사실을 말한다:
#: 화면에 남은 것이 없거나(크래시·로드 실패·로그인 실패), 방금 친 입력이 거부됐다.
ASSERTIVE_LIVE_REGION_CONSUMERS = frozenset(
    {
        "ui/ErrorState.tsx",  # 요청한 내용이 오지 않았다
        "ui/FieldGroup.tsx",  # 방금 친 값이 거부됐다(제출 버튼이 죽어 있다)
        "shared/error-boundary.tsx",  # 쓰던 화면이 크래시로 사라졌다
        "auth/failure-ui.tsx",  # 로그인 실패 — 앱에 들어가지 못한다
        # 신원 축 EMS 정합 (2026-08-21). 같은 판정을 형제 auth/failure-ui.tsx 와
        # 공유한다 — 앱에 들어가지 못한 상태이고, 방금 누른 [로그인]/[변경] 이
        # 거부됐다는 답이다. 기다릴 다음 작업이 없으므로 polite 는 답이 아니다.
        "routes/login.tsx",           # 자격증명이 거부됐다 — 더 들어갈 수 없다
        "routes/change-password.tsx",  # 변경이 거부됐다 — 이 화면을 벗어날 수 없다
        "routes/chambers/MeasurementStarter.tsx",  # sample 선택 목록 로드 실패 — 시작 전 복구가 필요하다
    }
)


def _live_region_rulings() -> dict[str, tuple[str, str]]:
    """``kind -> (urgency, rationale)`` — 룰링 테이블 TS 를 파싱한다.

    ⚠️ **주석을 먼저 벗긴다.** 이 파서는 `rationale:` 뒤의 홑따옴표 문자열을 전부
    이어 붙이는데, 원본을 읽으면 **주석 안의 문자열까지** 이어 붙는다. 독립 적대
    평가가 19자짜리 근거 밑에 URL 한 줄을 주석으로 달아 길이 바닥을 통과시켰다 —
    바닥이 무엇이든 무의미해지는 우회이고, 가장 리뷰하기 어려운 편집이다.
    이 모듈의 형제 스캔들은 전부 이미 벗기고 있었다.
    """
    src = _strip_ts_comments(LIVE_REGION_TS.read_text(encoding="utf-8"))
    rulings: dict[str, tuple[str, str]] = {}
    for kind, body in _LIVE_REGION_RULING_BLOCK_RE.findall(src):
        urgency = re.search(r"urgency:\s*'([a-z]+)'", body)
        rationale_src = body.split("rationale:", 1)[1] if "rationale:" in body else ""
        rationale = "".join(re.findall(r"'([^']*)'", rationale_src))
        rulings[kind] = (urgency.group(1) if urgency else "", rationale)
    return rulings


def _live_region_consumers() -> dict[str, set[str]]:
    """``rel/path -> {kind, …}`` — ``liveRegionProps`` 호출 사이트."""
    consumers: dict[str, set[str]] = {}
    for path in _src_files():
        code = _strip_ts_comments(path.read_text(encoding="utf-8"))
        kinds = set(_LIVE_REGION_CALL_RE.findall(code))
        if kinds:
            consumers[_rel(path)] = kinds
    return consumers


#: 판정 근거가 *라벨이 아니라 설명* 이기 위한 최소 길이.
#:
#: 실측(2026-08-21): 여섯 근거가 157~229자다. 80 은 그 절반 아래라 정당하게 간결한
#: 근거를 red 로 만들지 않으면서, 라벨(`'급함'`)이나 동어반복
#: (`'off because off off off'`, 23자 — 독립 적대 평가가 실제로 통과시킨 값)을
#: 배제한다. ⚠️ 이 수는 *오늘의 개수*가 아니라 **명제가 성립하는 바닥**이다:
#: 트리를 어떻게 재배치해도 같은 값을 요구하고, 리팩터를 회귀로 보고하지 않는다.
_RATIONALE_MIN_CHARS = 80


class TestAssertiveLiveRegionsStayOnTheUrgentAxis(unittest.TestCase):
    """S9 — ``role="alert"`` 는 **작업을 중단시켜야 할 것**에만 남는다.

    ``alert`` 는 assertive live region 이라 스크린리더가 사용자가 읽거나 입력하던
    것을 **끊고** 읽는다. 잘못 쓰면 침묵보다 나쁘다 — 실패 배지가 행마다 alert 인
    측정 결과 테이블은 렌더할 때마다 사용자를 기관총처럼 두들긴다.

    봉인은 세 축이다. (1) 손수 선언한 assertive region 이 ``src`` 전역에 **0건**
    (판정은 ``ui/live-region.ts`` 룰링 테이블에서만 나온다). (2) assertive 축을
    소비하는 **파일 집합이 래칫**(증가 금지, 화석 금지). (3) 룰링마다 **사유가
    실재**한다 — 사유 없는 테이블은 ``role="alert"`` 를 베껴갈 두 번째 장소일 뿐이다.
    """

    def test_no_hand_rolled_assertive_region(self) -> None:
        offenders = [
            site
            for site in _offending_sites(_HAND_ROLLED_ASSERTIVE_RE)
            if not site.startswith(f"{LIVE_REGION_SSOT_REL}:")
        ]
        self.assertEqual(
            offenders,
            [],
            "assertive live region 을 손수 선언한 사이트가 있다 — 긴급도 판정이 "
            f"{LIVE_REGION_SSOT_REL} 밖으로 새면 '이건 급한가'를 화면마다 다시 "
            f"답하게 되고, 그 답은 기록되지 않는다: {offenders}",
        )

    def test_the_scan_actually_detects_the_defect(self) -> None:
        """검출기가 **세 형태 모두** 잡고 정상 형태는 오탐하지 않는다.

        동적 형태를 못 잡으면 ``StatusBadge`` 처럼 map/삼항으로 alert 를 고르는
        사이트가 스캔에 보이지 않는다 — 이 웨이브가 실제로 발견한 사각지대다.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            offender = (
                'const a = <p role="alert">literal</p>;\n'
                "const b = <p role={stale ? 'alert' : 'note'}>ternary</p>;\n"
                "const c = <p role={ROLE_MAP[kind]} aria-live=\"assertive\">map</p>;\n"
            )
            innocent = (
                '// a comment explaining why role="alert" was removed\n'
                "const ruling = { urgency: 'assertive', role: 'alert' };\n"
                "const d = <p {...liveRegionProps('blockingFailure')}>via ssot</p>;\n"
                'const e = <p role="status" aria-live="polite">polite</p>;\n'
            )
            code = _strip_ts_comments(offender)
            hits = _HAND_ROLLED_ASSERTIVE_RE.findall(code)
            self.assertEqual(
                len(hits),
                3,
                "검출기가 리터럴/삼항/맵+aria-live 3형태를 모두 잡지 못한다 — "
                f"실제 트리에 대한 PASS 가 무의미해진다: {hits}",
            )
            self.assertEqual(
                _HAND_ROLLED_ASSERTIVE_RE.findall(_strip_ts_comments(innocent)),
                [],
                "검출기가 정상 형태(주석 · 룰링 테이블의 객체 속성 · SSOT 경유 · "
                "polite)를 오탐한다 — 오탐하는 가드는 곧 비활성화된다",
            )
            del root

    def test_the_urgent_axis_is_ratcheted(self) -> None:
        consumers = _live_region_consumers()
        assertive_kinds = {
            kind for kind, (urgency, _) in _live_region_rulings().items() if urgency == "assertive"
        }
        self.assertNotEqual(assertive_kinds, set(), "룰링 테이블에서 assertive 축을 찾지 못했다")
        actual = {rel for rel, kinds in consumers.items() if kinds & assertive_kinds}
        self.assertEqual(
            actual - ASSERTIVE_LIVE_REGION_CONSUMERS,
            set(),
            "새 파일이 사용자의 작업을 중단시킬 권한을 가져갔다 — 그 판단은 리뷰 "
            f"없이 통과해서는 안 된다: {sorted(actual - ASSERTIVE_LIVE_REGION_CONSUMERS)}",
        )
        self.assertEqual(
            ASSERTIVE_LIVE_REGION_CONSUMERS - actual,
            set(),
            "래칫에 화석 항목이 남아 있다 — 더 이상 assertive 를 쓰지 않는 파일이 "
            f"목록에 있으면 다음 신규 사이트가 그 자리에 조용히 들어온다: "
            f"{sorted(ASSERTIVE_LIVE_REGION_CONSUMERS - actual)}",
        )

    def test_every_ruling_states_a_reason(self) -> None:
        src = LIVE_REGION_TS.read_text(encoding="utf-8")
        union = _LIVE_REGION_KIND_UNION_RE.search(src)
        self.assertIsNotNone(union, "LiveRegionKind 유니언을 찾지 못했다")
        assert union is not None  # for type-checkers
        declared = set(re.findall(r"'([A-Za-z]+)'", union.group(1)))
        rulings = _live_region_rulings()
        self.assertEqual(
            declared,
            set(rulings),
            "선언된 kind 와 룰링 테이블 항목이 어긋난다 — 판정 없는 kind 는 "
            "다음 사람이 아무 role 이나 고르게 만든다",
        )
        for kind, (urgency, rationale) in sorted(rulings.items()):
            with self.subTest(kind=kind):
                self.assertIn(urgency, {"assertive", "polite", "off"})
                # ⚠️ **이 웨이브의 첫 시도는 바닥을 14~24자로 떨어뜨렸다** — 행에서
                # 파생한 `len(kind) + len(urgency)` 는 그럴듯한 출처를 가졌을 뿐
                # 근거의 적절함에 대한 명제가 아니었고, 독립 적대 평가가
                # `'off because off off off'`(23자)로 전량 green 을 받아냈다.
                # 옛 `> 40` 이 protecting nothing 이라는 논거는 14 에 **더 세게**
                # 적용된다. 파생이 언제나 더 강한 것은 아니다.
                #
                # 바닥은 이름과 근거를 갖는 상수로 되돌리되 측정에 얹는다(아래
                # `_RATIONALE_MIN_CHARS`), 그리고 파생 둘을 **직교하게** 남긴다 —
                # 한 토큰이 아니라는 것과, 그 행이 이미 말하는 것보다 길다는 것.
                self.assertIn(
                    " ",
                    rationale.strip(),
                    f"{kind} 의 판정 근거가 한 토큰이다 — 이유는 구절이어야 한다",
                )
                self.assertGreater(
                    len(rationale.strip()),
                    len(kind) + len(urgency),
                    f"{kind} 의 근거가 그 행이 이미 말하는 것보다 짧다",
                )
                self.assertGreater(
                    len(rationale.strip()),
                    _RATIONALE_MIN_CHARS,
                    f"{kind} 의 판정 근거가 사실상 비어 있다 — '급함' 한 단어는 "
                    "다음 사람이 자기 사례를 대볼 수 있는 기준이 아니다",
                )

    def test_row_scoped_surfaces_are_not_live_regions(self) -> None:
        """행마다 렌더되는 라벨은 live region 이 아니다.

        ``StatusBadge`` 는 측정 테이블에서 **행마다** 렌더된다. 여기에 live region
        을 달면 카디널리티가 무한하다(N 행 = N 알림). ``fail→alert`` 매핑이 정확히
        그 상태였고, 리터럴 스캔에는 잡히지 않았다(``role={STATUS_ROLE[status]}``).
        """
        badge = _strip_ts_comments((SRC_DIR / "ui" / "StatusBadge.tsx").read_text(encoding="utf-8"))
        self.assertIn(
            "liveRegionProps('staticLabel')",
            badge,
            "배지가 live region 판정을 SSOT 에서 가져오지 않는다",
        )
        self.assertNotIn(
            "'status'",
            badge,
            "배지에 per-row live region role 이 남아 있다 — 목록 렌더가 알림 폭풍이 된다",
        )


class TestFieldDescriptionsAreProgrammaticallyLinked(unittest.TestCase):
    """S7 선언부 — 힌트/에러 배선이 **프리미티브 1곳**에 있다.

    vitest 가 동작(``aria-describedby`` 가 실제로 해석되는가)을 단언하고, 여기서는
    그 동작을 **한 곳에서만** 만든다는 구조를 봉인한다. 라우트가 각자 배선하기
    시작하면 19벌 사본이 생기고, 새 라우트는 기본값으로 잊는다.
    """

    ALLOWED_DESCRIBEDBY_OWNER = "ui/FieldGroup.tsx"

    def test_describedby_is_wired_in_exactly_one_place(self) -> None:
        owners = sorted(
            {site.split(":")[0] for site in _offending_sites(re.compile(r"aria-describedby"))}
        )
        self.assertEqual(
            owners,
            [self.ALLOWED_DESCRIBEDBY_OWNER],
            "aria-describedby 를 손수 배선하는 파일이 프리미티브 밖에 있다 — "
            f"그 배선은 라우트 수만큼 복제되고 그만큼 빠뜨려진다: {owners}",
        )

    def test_the_owner_actually_derives_the_ids(self) -> None:
        src = _strip_ts_comments((SRC_DIR / "ui" / "FieldGroup.tsx").read_text(encoding="utf-8"))
        self.assertRegex(
            src,
            r"\$\{htmlFor\}\$\{(?:HELP|ERROR)_ID_SUFFIX\}",
            "설명 id 가 htmlFor 에서 파생되지 않는다 — 상수 id 는 루프로 렌더되는 "
            "화면(my-projects / test-reports / AddRowForm)에서 즉시 충돌한다",
        )
        self.assertIn(
            "props.id !== controlId",
            src,
            "설명을 붙일 대상을 id 일치가 아니라 위치로 고른다 — 컨트롤 옆에 형제를 "
            "넘기는 화면(providers / membership)에서 엉뚱한 노드가 장식된다",
        )

    def test_routes_that_owned_a_validation_alert_handed_it_over(self) -> None:
        """검증 메시지를 프리미티브에 넘겼는지 — 배선의 **소비**를 본다.

        선언만 되고 아무도 쓰지 않는 prop 은 봉인에게 완전히 green 으로 보인다
        (``help`` 이 정확히 그 상태로 오래 있었다).
        """
        for rel in ("ui/NumericLookupForm.tsx", "routes/sessions.tsx"):
            with self.subTest(file=rel):
                src = _strip_ts_comments((SRC_DIR / rel).read_text(encoding="utf-8"))
                self.assertRegex(
                    src,
                    r"error:\s*\w|error=\{|errorTestId",
                    f"{rel} 이 검증 메시지를 FieldGroup 에 넘기지 않는다 — 메시지가 "
                    "컨트롤과 프로그램적으로 연결되지 않은 채 옆에 놓인다",
                )


# ─────────────────────────────────────────────────────────────────────────────
# W4-A T6 — axe 커버리지 (계약 M6 / 봉인 S13 · S14)
#
# 이 웨이브 전까지 axe 는 주소 **1개**(``/auth/callback``)만 스캔했다. 즉 T1~T5 가
# 고친 것들(제목·포커스·describedby·live region)이 실제 운영 화면 16개에서 검증된
# 적이 없었다. 스캔이 닿지 않는 곳의 접근성 주장은 근거가 없다.
#
# 여기서 봉인하는 것은 **커버리지의 전수성**과 **면제의 정직성**이다 — 위반 자체의
# 유무는 Playwright 가 실브라우저에서 판정하고(정적 스캔으로는 대비비를 알 수 없다),
# 이 Python 봉인은 "새 라우트가 조용히 스캔 밖에 남는 것"과 "면제가 사유 없이
# 늘어나는 것"을 막는다.
# ─────────────────────────────────────────────────────────────────────────────

A11Y_SPEC = WEB_ROOT / "tests" / "e2e" / "a11y.spec.ts"

#: 등록돼 있으나 **주소가 아니거나** 이 빌드에서 도달 불가라 스캔 대상이 아닌 것.
#: 값은 여기 적힌 사유가 아니라 **스펙 본문에 같은 사유가 있는지**로 검증된다
#: (사유가 코드 옆에 없으면 다음 사람은 그것이 결정인지 실수인지 알 수 없다).
_A11Y_EXCLUDED_ROUTES = frozenset({"grid-poc", "*"})

#: 셸 **밖에서** 렌더되는 유일한 주소. 운영 sweep(=인증 셸 + ``PageHeader`` 전제)이
#: 아니라 실패-뷰 2 테스트가 덮으므로 sentinel 맵에서 빠지지만, 커버리지에는 계상된다.
#: 스펙 쪽 ``AUTH_CALLBACK_ROUTE`` 와의 상등은 아래 봉인이 확인한다.
_A11Y_CALLBACK_ROUTE = "/auth/callback"

#: 셸 밖에서 렌더되는 나머지 주소들 — 신원 축(2026-08-21)의 로컬 로그인 화면 둘.
#: ``/auth/callback`` 과 같은 범주다: 운영 sweep 의 도달 증거(``PageHeader`` 가 렌더하는
#: ``<h1>``)가 성립하지 않으므로 sentinel 맵에서 빠지고, 전용 axe 테스트가 각자의
#: heading 을 기다린 뒤 스캔한다. 스펙 쪽 ``LOCAL_AUTH_ROUTES`` 와의 상등은 아래 봉인이
#: 확인한다 — 두 목록이 갈라지면 스캔되지 않는 주소가 스캔된다고 답하게 된다.
_A11Y_OUTSIDE_SHELL_ROUTES = frozenset({"/login", "/change-password"})

#: 면제 사유가 "구체적"이라고 인정되는 최소 길이. 짧은 사유는 사유가 아니라 라벨이다.
_A11Y_MIN_REASON_CHARS = 40

#: 사유 자리에 들어오면 안 되는 말 — 판단을 미룬 흔적.
_A11Y_PLACEHOLDER_REASON = re.compile(
    r"\b(TODO|FIXME|TBD|N/?A|WIP|temporary|temporarily|later|나중에|미정|추후)\b",
    re.IGNORECASE,
)

#: allowlist 상한(감소 전용 래칫). **현재 실측 0** — 인벤토리 1회 실행에서 나온
#: critical/serious 는 색 대비 2건뿐이었고 둘 다 정공 수정했다(토큰 범주 오용 +
#: `--accent` 가 `--surface-bg-alt` 에서만 4.45). 늘리려면 그 커밋에서 사유를 함께
#: 적어야 하고, 줄어들면 이 상수도 같은 커밋에서 내려야 한다(아래 slack 가드).
_A11Y_ALLOWLIST_CEILING = 0


def _a11y_spec_source() -> str:
    return A11Y_SPEC.read_text(encoding="utf-8")


def _a11y_registered_routes(entries: "tuple[RouteEntry, ...]") -> set[str]:
    """등록 집합 → 스캔돼야 할 주소.

    ⚠️ 이 함수는 ``app.tsx`` **소스 문자열**을 받아 ``path:`` 리터럴을 세고 있었다.
    그 형태의 사본이 이 파일에 다섯 벌 있었고, 다섯 다 다른 모듈에서 전개된
    라우트를 보지 못했다. 이제 인자는 ``support.frontend_route_registry`` 가 모듈
    그래프에서 파생한 라우트 목록이고, 그 파생은 따라갈 수 없는 원소를 만나면
    조용히 건너뛰는 대신 예외를 던진다.
    """
    return {
        entry.address
        for entry in entries
        if entry.address.lstrip("/") not in _A11Y_EXCLUDED_ROUTES
    }


def _a11y_route_sentinels(spec_src: str) -> dict[str, str]:
    """스펙의 ``OPERATOR_ROUTE_HEADINGS`` — 운영 라우트 → 그 화면 ``<h1>`` 의 i18n 키.

    이 맵이 **스캔 대상 목록 겸 도달 증거 목록**이다(목록 2벌 금지). 값은 axe 를
    돌리기 전에 기다릴 heading 의 이름을 결정하므로, 값이 틀리면 스윕은 red 가
    된다 — 즉 이 맵은 "무엇을 스캔하는가"와 "무엇이 떴을 때 스캔하는가"를 동시에
    선언한다.
    """
    block = re.search(
        r"const OPERATOR_ROUTE_HEADINGS:[^=]*=\s*\{(.*?)\n\};", spec_src, re.S
    )
    if block is None:
        return {}
    # Shared lexer, not an inline regex — both keys and values here are string
    # literals, and a crude `//` strip would truncate one containing a URL.
    return dict(
        re.findall(r"'([^']+)':\s*'([^']+)'", strip_ts_comments(block.group(1)))
    )


def _a11y_spec_callback_route(spec_src: str) -> str | None:
    match = re.search(r"const AUTH_CALLBACK_ROUTE = '([^']+)';", spec_src)
    return match.group(1) if match else None


def _a11y_outside_shell_routes(spec_src: str) -> set[str]:
    """셸 **밖에서** 렌더돼 전용 테스트로 스캔되는 주소들.

    운영 sweep 의 도달 증거(``PageHeader`` 가 렌더하는 ``<h1>``)가 성립하지 않는
    화면들이다. ``/auth/callback`` 이 첫 사례였고, 신원 축(2026-08-21)이 로컬 로그인
    화면 둘을 같은 범주로 더했다 — 전용 테스트가 각자의 heading 을 기다린 뒤 axe 를
    돌린다.

    ⚠️ 목록이 **스펙에서 파생**된다. 여기 손으로 적으면 스펙과 갈라지고, 갈라지는
    순간 이 함수는 스캔되지 않는 주소를 스캔된다고 답한다.
    """
    block = re.search(
        r"const LOCAL_AUTH_ROUTES: readonly string\[\] = \[(.*?)\];", spec_src, re.S
    )
    if block is None:
        return set()
    return set(re.findall(r"'([^']+)'", block.group(1)))


def _a11y_scanned_routes(spec_src: str) -> set[str]:
    """스캔이 덮는 주소 전체 = 운영 sweep 맵 키 ∪ 셸 밖 전용 스캔 주소들."""
    routes = set(_a11y_route_sentinels(spec_src))
    routes |= _a11y_outside_shell_routes(spec_src)
    callback = _a11y_spec_callback_route(spec_src)
    if callback is not None:
        routes.add(callback)
    return routes


def _app_route_title_keys(entries: "tuple[RouteEntry, ...]") -> dict[str, str]:
    """등록 라우트 → ``handle.titleKey``.

    ``/`` 는 **index 자식**(실제 화면)이 소유한다. 셸(``path: '/'``)의 titleKey 는
    화면 제목이 아니라 폴백이므로, 같은 주소를 두 라우트가 답할 때 index 가 이긴다.
    옛 형태는 ``path:`` 와 ``titleKey:`` 사이를 **2000자 창**으로 잇는 정규식이었다 —
    그 창은 *그날 두 선언 사이의 거리* 였다.
    """
    keys: dict[str, str] = {}
    for entry in entries:
        if entry.title_key is None:
            continue
        if entry.address.lstrip("/") in _A11Y_EXCLUDED_ROUTES:
            continue
        if entry.address == "/" and not entry.index:
            continue  # 셸의 폴백 제목 — 화면 제목이 아니다
        keys[entry.address] = entry.title_key
    return keys


def _app_route_modules() -> dict[str, str]:
    """등록 라우트 → 그 화면을 렌더하는 lazy 모듈 지정자(``@/routes/...``).

    셸/콜백은 element 가 여러 줄 JSX 라 여기 잡히지 않는다 — 의도된 것이다
    (둘 다 ``PageHeader`` 를 렌더하는 화면이 아니다).
    """
    # ⚠️ 인자를 받지 않는다. 라우트 집합은 모듈 그래프 파생에서 오고 lazy 맵은
    # ``app.tsx`` 에서 오므로, 소스를 인자로 받으면 그 인자가 **답의 절반만**
    # 결정한다 — 호출자가 변이한 소스를 넘겨도 라우트 쪽은 실제 트리를 답하는
    # 함수가 되고, 그런 시그니처는 다음 사람에게 거짓말을 한다.
    app_src = _app_route_source()
    lazy_modules = dict(
        re.findall(r"const (\w+) = lazy\(\(\) => import\('([^']+)'\)\)", app_src)
    )
    modules: dict[str, str] = {}
    for entry in _registered_routes():
        if entry.address.lstrip("/") in _A11Y_EXCLUDED_ROUTES:
            continue
        component = re.fullmatch(r"<(\w+)\s*/>", entry.element)
        if component is None:
            continue
        specifier = lazy_modules.get(component.group(1))
        if specifier is not None:
            modules[entry.address] = specifier
    return modules


def _route_module_path(specifier: str) -> Path | None:
    """``@/routes/x`` → 실제 파일(``x.tsx`` 또는 ``x/index.tsx``)."""
    base = WEB_ROOT / "src" / specifier.removeprefix("@/")
    for candidate in (base.with_suffix(".tsx"), base / "index.tsx"):
        if candidate.exists():
            return candidate
    return None


def _a11y_sentinel_defects(
    sentinels: dict[str, str], registered_title_keys: dict[str, str]
) -> list[str]:
    """도달-증거 맵의 결함 목록. **순수 함수** — 합성 입력으로 검출기를 시험한다."""
    defects: list[str] = []
    for route in sorted(set(registered_title_keys) - set(sentinels)):
        defects.append(f"{route}: 도달 증거 없이 스캔된다 — 셸만 뜬 상태를 잴 수 있다")
    for route in sorted(set(sentinels) - set(registered_title_keys)):
        defects.append(f"{route}: 라우터가 등록하지 않는 주소의 도달 증거 — 죽은 항목")
    for route in sorted(set(sentinels) & set(registered_title_keys)):
        if sentinels[route] != registered_title_keys[route]:
            defects.append(
                f"{route}: 도달 증거 키 {sentinels[route]!r} 가 라우터 선언 "
                f"{registered_title_keys[route]!r} 와 다르다"
            )
    return defects


def _a11y_exclusion_notes(spec_src: str) -> dict[str, str]:
    """스펙 안에 적힌 ``· `key` — 사유`` 항목."""
    return {
        key: reason.strip()
        for key, reason in re.findall(r"·\s*`([^`]+)`[^\n—]*—\s*(\S[^\n]*)", spec_src)
    }


def _a11y_allowlist_entries(spec_src: str) -> list[dict[str, str]]:
    block = re.search(r"const A11Y_ALLOWLIST:[^=]*=\s*\[(.*?)\];", spec_src, re.S)
    if block is None:
        return []
    return [
        dict(re.findall(r"(\w+):\s*'([^']*)'", chunk))
        for chunk in re.findall(r"\{(.*?)\}", block.group(1), re.S)
    ]


def _a11y_allowlist_defects(
    entries: list[dict[str, str]], scanned_routes: set[str]
) -> list[str]:
    """면제 항목의 결함 목록. **순수 함수** — 합성 offender 로 검출기 자체를 시험한다."""
    defects: list[str] = []
    for index, entry in enumerate(entries):
        for field in ("route", "ruleId", "reason"):
            if not entry.get(field, "").strip():
                defects.append(f"#{index}: `{field}` 가 비어 있다")
        reason = entry.get("reason", "").strip()
        if reason and len(reason) < _A11Y_MIN_REASON_CHARS:
            defects.append(f"#{index}: 사유가 {len(reason)}자로 구체적이지 않다 — {reason!r}")
        if _A11Y_PLACEHOLDER_REASON.search(reason):
            defects.append(f"#{index}: 사유가 판단을 미룬 플레이스홀더다 — {reason!r}")
        route = entry.get("route", "").strip()
        if route and route not in scanned_routes:
            defects.append(f"#{index}: 스캔하지 않는 라우트 {route!r} 의 면제 — 죽은 항목")
    return defects


class TestAxeScanCoversEveryRegisteredRoute(unittest.TestCase):
    """S13 — axe 대상이 **라우터 등록 집합에서 파생**된다(손수 목록 drift 0).

    손수 목록의 실패 방식은 조용하다: 새 라우트를 추가한 사람은 스캔 목록을 떠올릴
    이유가 없고, 스캔은 **줄어든 채로 green** 이다. ``responsive-layout`` 이 이미
    같은 방식으로 두 라우트를 놓친 전례가 있다(``/my-projects`` · ``/control``).

    ``/auth/callback`` 은 **제외가 아니다** — 셸 밖에서 렌더되므로 운영 sweep 이
    아니라 실패-뷰 2 테스트가 덮지만, 커버리지에는 계상된다.
    """

    def test_scanned_set_equals_registered_set(self) -> None:
        registered = _a11y_registered_routes(_registered_routes())
        scanned = _a11y_scanned_routes(_a11y_spec_source())
        # 바닥만. 완전성은 바로 아래 두 방향 차집합(등록−스캔 / 스캔−등록)이 답한다.
        self.assertGreater(
            len(registered),
            0,
            "app.tsx 에서 라우트를 찾지 못했다 — 봉인이 공허하다",
        )
        self.assertEqual(
            sorted(registered - scanned),
            [],
            "등록됐지만 axe 가 한 번도 보지 않는 라우트가 있다 — 그 화면에 대한 "
            "접근성 주장은 근거가 없다",
        )
        self.assertEqual(
            sorted(scanned - registered),
            [],
            "라우터가 등록하지 않는 주소를 스캔한다 — 삭제된 화면을 스캔하며 "
            "커버리지를 부풀린다",
        )

    def test_the_sweep_is_derived_not_a_second_hand_list(self) -> None:
        """루프가 선언 맵에서 **파생**된 집합을 돈다.

        두 번째 손수 목록이 생기면 상등 봉인은 첫 번째만 보고 계속 green 이다.
        """
        src = _strip_ts_comments(_a11y_spec_source())
        self.assertIn(
            "for (const [route, titleKey] of Object.entries(OPERATOR_ROUTE_HEADINGS))",
            src,
            "운영 sweep 이 OPERATOR_ROUTE_HEADINGS 에서 파생되지 않는다 — "
            "목록이 2벌이 된다",
        )

    def test_every_scanned_route_waits_for_its_own_screen(self) -> None:
        """**도달 증거의 전수성** — 라우트마다 그 화면 고유의 sentinel 이 있다.

        셸 nav 만 기다리면 증명되는 것은 "셸이 떴다"뿐이다. ``<Outlet/>`` 이 아직
        Suspense 폴백이거나 ``errorElement`` 이거나 리다이렉트여도 nav 는 그대로
        있으므로, axe 는 **다른 화면**을 스캔하고 그 결과를 이 주소의 "0 violation"
        으로 보고한다. 라우트 고유 ``<h1>`` 은 lazy 청크 안에 있으니 그 화면의
        DOM 이 실제로 문서에 있다는 첫 증거다.
        """
        spec = _a11y_spec_source()
        self.assertEqual(
            _a11y_spec_callback_route(spec),
            _A11Y_CALLBACK_ROUTE,
            "스펙의 AUTH_CALLBACK_ROUTE 가 바뀌었다 — sweep 제외 주소가 조용히 "
            "달라지면 그 화면은 아무도 스캔하지 않는다",
        )
        sentinels = _a11y_route_sentinels(spec)
        registered = _app_route_title_keys(_registered_routes())
        # `/auth/callback` 은 셸 밖(=PageHeader 없음) — 실패-뷰 2 테스트가 이미
        # `role="alert"` 로 도달을 증명하므로 sweep 대상이 아니다.
        registered.pop(_A11Y_CALLBACK_ROUTE, None)
        for route in _A11Y_OUTSIDE_SHELL_ROUTES:
            registered.pop(route, None)
        # 바닥만 — 아래 sentinel 대조가 완전성을 답한다.
        self.assertGreater(
            len(registered),
            0,
            "app.tsx 에서 titleKey 를 찾지 못했다 — 봉인이 공허하다",
        )
        defects = _a11y_sentinel_defects(sentinels, registered)
        self.assertEqual(defects, [], f"a11y 도달 증거 결함: {defects}")

    def test_the_outside_shell_set_matches_the_spec(self) -> None:
        """두 목록이 갈라지면 이 봉인이 **스캔되지 않는 주소를 스캔된다고** 답한다.

        `_A11Y_OUTSIDE_SHELL_ROUTES` 는 sweep 완전성 검사에서 주소를 **빼는** 목록이다.
        스펙이 그 주소의 전용 테스트를 지우면(또는 이름을 바꾸면) 그 주소는 어디에서도
        스캔되지 않는데 여기서는 여전히 면제된다 — 정확히 침묵하는 커버리지 구멍이다.
        """
        spec_src = _a11y_spec_source()
        self.assertEqual(
            _a11y_outside_shell_routes(spec_src),
            set(_A11Y_OUTSIDE_SHELL_ROUTES),
            "스펙의 LOCAL_AUTH_ROUTES 와 봉인의 면제 목록이 갈라졌다 — 면제된 주소가 "
            "실제로 스캔되는지 아무도 확인하지 않는 상태다",
        )
        self.assertNotEqual(
            _A11Y_OUTSIDE_SHELL_ROUTES, frozenset(),
            "면제 목록이 비었다 — 이 봉인이 공허하다",
        )

    def test_every_outside_shell_route_is_actually_scanned(self) -> None:
        """면제는 *다른 곳에서 스캔된다*는 주장이다 — 그 주장을 확인한다.

        ⚠️ 스펙이 그 주소들을 **파생으로** 순회하므로(``for (const route of
        LOCAL_AUTH_ROUTES)``) 판정도 주소별 리터럴이 아니라 그 순회의 존재를 묻는다.
        주소별 리터럴을 요구하면 스펙을 두 벌 목록으로 되돌리게 만든다 — 이 봉인이
        막으려는 바로 그 형태다.
        """
        spec_src = strip_ts_comments(_a11y_spec_source())
        self.assertIn(
            "for (const route of LOCAL_AUTH_ROUTES)",
            spec_src,
            "셸 밖 주소들을 도는 스캔 루프가 없다 — 면제된 주소가 어디에서도 "
            "스캔되지 않는다",
        )
        self.assertIn(
            "page.goto(route,",
            spec_src,
            "루프가 주소를 열지 않는다",
        )
        self.assertIn(
            "runAxeAndAssertBlocking(page, route,",
            spec_src,
            "루프가 주소를 열기만 하고 axe 를 돌리지 않는다",
        )

    def test_each_sentinel_is_the_heading_that_route_module_renders(self) -> None:
        """sentinel 키가 **그 화면 모듈이 실제로 렌더하는** 제목인가.

        라우터 선언(``handle.titleKey``)과 화면 ``<PageHeader>`` 가 갈라지면 스윕은
        영영 뜨지 않을 heading 을 기다린다. 그때 증상은 "접근성 위반"이 아니라
        15초 타임아웃이라, 원인을 찾기 전에 대기를 지우고 싶어진다 — 그 유혹을
        구조적으로 없애려면 두 선언이 같다는 것을 여기서 봉인해야 한다.
        """
        app_src = _app_route_source()
        sentinels = _a11y_route_sentinels(_a11y_spec_source())
        modules = _app_route_modules()
        missing: list[str] = []
        for route, title_key in sorted(sentinels.items()):
            specifier = modules.get(route)
            self.assertIsNotNone(
                specifier, f"{route}: app.tsx 에서 화면 모듈을 찾지 못했다"
            )
            path = _route_module_path(str(specifier))
            self.assertIsNotNone(path, f"{route}: 모듈 파일 {specifier} 이 없다")
            assert path is not None  # for type-checkers; assertIsNotNone 이 게이트
            if f"title={{t('{title_key}')}}" not in path.read_text(encoding="utf-8"):
                missing.append(f"{route} → {path.name} 가 {title_key!r} 를 렌더하지 않는다")
        self.assertEqual(
            missing,
            [],
            f"sentinel 이 그 화면의 제목이 아니다: {missing}",
        )

    def test_the_sweep_waits_for_the_sentinel_before_it_scans(self) -> None:
        """선언만 있고 **기다리지 않으면** 맵은 장식이다.

        순서까지 본다 — axe 를 먼저 돌리고 나서 heading 을 기다리면 판정은 여전히
        도달 전 문서에 대해 내려진다.
        """
        src = _strip_ts_comments(_a11y_spec_source())
        sweep = src[src.find("operator routes (W4-A T6)") :]
        self.assertNotEqual(sweep, "", "운영 sweep describe 블록을 찾지 못했다")
        wait_at = sweep.find("getByRole('heading', { level: 1, name: messageAt(titleKey)")
        scan_at = sweep.find("runAxeAndAssertBlocking(page, route,")
        self.assertNotEqual(
            wait_at, -1, "운영 sweep 이 라우트 고유 heading 을 기다리지 않는다"
        )
        self.assertNotEqual(scan_at, -1, "운영 sweep 이 axe 를 돌리지 않는다")
        self.assertLess(
            wait_at,
            scan_at,
            "heading 대기가 axe 스캔 뒤에 있다 — 판정은 여전히 도달 전 문서에 대한 것이다",
        )
        # ⚠️ **이 단언은 2026-08-27 에 «철자» 에서 «효과» 로 바뀌었다.** 옛 형태는
        # ``self.assertIn("koMessages", src)`` 였다 — a11y 스펙 **안의 사설 변수 이름**을
        # 물어서 «번들에서 파생되는가» 를 증명하려 한 것이다. 라운드 4가 리졸버를 세
        # 사본에서 하나로 접자 파생은 **그대로인데** 그 이름이 사라졌고 봉인은 red 가
        # 됐다. 이름을 새 이름으로 갈아 끼우는 것은 같은 결함을 한 칸 옮기는 것이다 —
        # 다음 리팩터가 또 red 를 만든다. 물어야 할 것은 *heading 텍스트가 번들을 실제로
        # 읽는 리졸버를 지나는가* 이고, 그것은 두 홉이다: 스펙이 선언된 리더에서
        # 리졸버를 들여오고, 그 리더가 로케일 JSON 을 연다. 두 홉 모두 디스크에서 읽어
        # 판정하므로 어느 쪽이 끊겨도 red 다.
        self.assertIn(
            "from './helpers/locale-messages'",
            src,
            "a11y 스윕이 선언된 e2e 로케일 리더를 쓰지 않는다 — 기대 heading 텍스트가 "
            "i18n 번들에서 파생되지 않으면 카피가 바뀔 때 스윕이 조용히 어긋난다",
        )
        reader = (
            Path(__file__).resolve().parents[1]
            / "apps/web/tests/e2e/helpers/locale-messages.ts"
        )
        self.assertTrue(reader.is_file(), f"선언된 e2e 로케일 리더가 없다: {reader}")
        self.assertIn(
            "src/locales/",
            reader.read_text(encoding="utf-8"),
            "e2e 로케일 리더가 번들을 열지 않는다 — 스펙이 그것을 들여와도 파생이 아니다",
        )

    def test_the_sentinel_detector_rejects_synthetic_drift(self) -> None:
        """검출기 비-공허성 — 3 형태의 drift 를 실제로 잡는가.

        전수 상태에서는 위 판정이 공허하게 통과할 수 있다. 합성 입력으로 검출기를
        직접 시험해야 "정합이라 green" 과 "검사를 안 해서 green" 이 구분된다.
        """
        registered = {"/reports": "routes.reports.page.title", "/jobs": "routes.jobs.page.title"}
        cases = {
            "새 라우트가 sentinel 없이 등록": {"/reports": "routes.reports.page.title"},
            "삭제된 화면의 sentinel 잔존": {
                **registered,
                "/gone": "routes.gone.title",
            },
            "sentinel 키가 라우터 선언과 다름": {
                **registered,
                "/jobs": "routes.jobs.page.subtitle",
            },
        }
        for name, sentinels in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(
                    _a11y_sentinel_defects(sentinels, registered),
                    [],
                    f"검출기가 '{name}' 를 통과시킨다 — 봉인이 공허하다",
                )

    def test_the_sentinel_detector_accepts_an_aligned_map(self) -> None:
        """반대 방향 — 정합 상태를 red 로 만들지 않는다(과잉 차단도 결함이다)."""
        aligned = {"/reports": "routes.reports.page.title"}
        self.assertEqual(_a11y_sentinel_defects(aligned, dict(aligned)), [])

    def test_each_exclusion_carries_its_reason_next_to_the_list(self) -> None:
        notes = _a11y_exclusion_notes(_a11y_spec_source())
        missing = sorted(_A11Y_EXCLUDED_ROUTES - set(notes))
        self.assertEqual(
            missing,
            [],
            f"사유 없이 스캔에서 빠진 라우트: {missing} — 결정인지 실수인지 알 수 없다",
        )
        thin = sorted(
            key
            for key in _A11Y_EXCLUDED_ROUTES
            if len(notes.get(key, "")) < _A11Y_MIN_REASON_CHARS
        )
        self.assertEqual(thin, [], f"제외 사유가 라벨 수준으로 짧다: {thin}")

    def test_the_equality_check_would_catch_a_new_unscanned_route(self) -> None:
        """검출기 비-공허성 — 합성 offender 를 실제로 잡는가.

        ⚠️ 옛 형태는 ``app.tsx`` **소스 문자열 끝에** 라우트를 한 줄 붙였다. 그것은
        정규식 스캐너에 대한 시험이었지 등록 집합에 대한 시험이 아니었고, 그래서
        *다른 모듈에서 전개된* offender — 이 축이 실제로 뚫린 그 형태 — 는 시험된
        적이 없다. 이제 합성 **트리** 를 만들고 offender 를 그 두 번째 모듈에 둔다.
        """
        member = (
            "{ path: 'synthetic-new-route', element: <X/>, errorElement: <E/>, "
            "handle: { titleKey: 'a.b' } }"
        )
        with _synthetic_route_tree(spread_member=member) as root:
            registered = _a11y_registered_routes(collect_route_entries(root))
        scanned = _a11y_scanned_routes(_a11y_spec_source())
        self.assertIn(
            "/synthetic-new-route",
            registered - scanned,
            "새 라우트를 추가해도 상등 단언이 반응하지 않는다 — 봉인이 공허하다",
        )


class TestA11yAllowlistIsAJustifiedRatchet(unittest.TestCase):
    """S14 — 면제가 존재한다면 **감소 래칫**이고 항목마다 사유가 있다.

    계약 §3.4 의 "은폐 금지"는 두 방향에서 깨진다. 하나는 스캔 축소(라우트를 빼거나
    룰을 끄는 것) — 아래 ``test_nothing_is_switched_off`` 가 막는다. 다른 하나는
    **면제의 조용한 누적**이다: 사유 없는 항목이 하나씩 늘면 어느 시점부터
    "0 violation" 은 "아무것도 검사하지 않는다"와 같은 말이 된다.

    래칫의 다른 절반(더 이상 발화하지 않는 항목은 FAIL)은 **런타임 성질**이라
    스펙 안에 있다 — 여기서는 그 로직이 **존재하는지**를 본다. 정적으로는 룰이
    발화하는지 알 수 없기 때문이다.
    """

    def test_no_entry_is_unjustified(self) -> None:
        spec = _a11y_spec_source()
        defects = _a11y_allowlist_defects(
            _a11y_allowlist_entries(spec), _a11y_scanned_routes(spec)
        )
        self.assertEqual(defects, [], f"a11y 면제 항목 결함: {defects}")

    def test_the_allowlist_only_shrinks(self) -> None:
        count = len(_a11y_allowlist_entries(_a11y_spec_source()))
        self.assertLessEqual(
            count,
            _A11Y_ALLOWLIST_CEILING,
            f"a11y 면제가 {count}건으로 상한 {_A11Y_ALLOWLIST_CEILING} 을 넘었다 — "
            "면제를 늘리려면 항목별 사유와 함께 이 상수를 올려야 하고, 그 자체가 "
            "리뷰 대상이 된다",
        )

    def test_the_ceiling_is_not_slack(self) -> None:
        """상한이 실측보다 높으면 래칫은 그 순간부터 아무것도 조이지 않는다."""
        count = len(_a11y_allowlist_entries(_a11y_spec_source()))
        self.assertEqual(
            count,
            _A11Y_ALLOWLIST_CEILING,
            "면제를 제거한 커밋에서 상한을 함께 내리지 않았다 — "
            f"_A11Y_ALLOWLIST_CEILING 을 {count} 로 내려라",
        )

    def test_the_spec_implements_a_self_cleaning_ratchet(self) -> None:
        """면제가 **스스로** 사라지는 성질이 스펙 안에 있는가.

        사람이 관리하는 baseline 숫자는 내려가지 않는다. 발화하지 않는 면제를
        FAIL 로 만들면 래칫이 런의 성질이 된다.
        """
        src = _strip_ts_comments(_a11y_spec_source())
        self.assertIn(
            "!firedRuleIds.has(entry.ruleId)",
            src,
            "발화하지 않는 면제 항목을 FAIL 로 만드는 로직이 없다 — 면제는 한 번 "
            "들어오면 영원히 남는다",
        )
        self.assertIn(
            "allowedRuleIds.has(v.id)",
            src,
            "면제가 차단 판정에 반영되지 않는다 — 자료구조만 있고 소비가 없다",
        )

    def test_nothing_is_switched_off(self) -> None:
        """계약 §3.4 — 룰/노드를 끄는 방식으로 green 을 만들지 않는다."""
        src = _strip_ts_comments(_a11y_spec_source())
        for concealer in (".disableRules(", ".exclude("):
            with self.subTest(api=concealer):
                self.assertNotIn(
                    concealer,
                    src,
                    f"axe 스캔이 {concealer} 로 검사 범위를 줄인다 — 그것은 "
                    "커버리지가 아니라 은폐다",
                )

    def test_the_detector_rejects_synthetic_offenders(self) -> None:
        """검출기 비-공허성 — 3 형태의 나쁜 면제를 실제로 잡는가.

        면제가 0건이면 위 판정들은 공허하게 통과한다. 검출기를 **합성 입력**으로
        직접 시험해야 "0건이라 green" 과 "검사를 안 해서 green" 이 구분된다.
        """
        scanned = {"/reports"}
        cases = {
            "empty reason": [{"route": "/reports", "ruleId": "color-contrast", "reason": ""}],
            "placeholder reason": [
                {"route": "/reports", "ruleId": "color-contrast", "reason": "TODO 나중에 고친다"}
            ],
            "unscanned route": [
                {
                    "route": "/not-scanned",
                    "ruleId": "color-contrast",
                    "reason": "타 클레임 소유라 이 웨이브에서 고칠 수 없는 화면의 대비 위반",
                }
            ],
        }
        for name, entries in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(
                    _a11y_allowlist_defects(entries, scanned),
                    [],
                    f"검출기가 {name} 를 통과시킨다 — 봉인이 공허하다",
                )

    def test_the_detector_accepts_a_legitimate_entry(self) -> None:
        """반대 방향 — 정상 형태를 red 로 만들지 않는다(과잉 차단도 결함이다)."""
        self.assertEqual(
            _a11y_allowlist_defects(
                [
                    {
                        "route": "/chambers",
                        "ruleId": "color-contrast",
                        "reason": "타 클레임(multichamber) 소유 파일이라 이 웨이브가 고칠 수 없다",
                    }
                ],
                {"/chambers"},
            ),
            [],
        )


class TestObservabilityLoadsOnDemand(unittest.TestCase):
    """S3(구조) + S7 + S8 — 웨이브 ``fe-w4-bundle-observability-cost`` (2026-07-31).

    OTel web SDK 와 ``@sentry/browser`` 는 **런타임 config** 로 켜지는데 **정적
    import** 되어 있었다. collector/DSN 이 없는 배포(개발·기본 구성)는 321.58 kB
    (gzip 100 kB) — React 보다 크고 앱 본체보다 큰 최대 청크 — 를 내려받아
    early ``return`` 두 줄을 실행하고 버렸다.

    행위(로드/미로드·실패 격리·부트 순서)는 vitest 가 봉인한다
    (``apps/web/tests/observability-boot.test.ts`` · ``main-boot-sequence.test.ts``).
    여기서 봉인하는 것은 **텍스트 스캔으로만 판정되는 두 축**이다:

    1. **게이트 사본 0** — 활성화 조건(``otelCollectorUrl`` / ``sentryDsn`` 진리값
       판정)이 ``observability/enablement.ts`` 밖에 다시 쓰이지 않는다. 사본이
       생기면 한쪽만 바뀌어 "로드는 되는데 no-op"(비용은 그대로) 또는 "설정했는데
       로드 안 됨"(관측 실명)이 되고, **둘 다 조용하다**.
    2. **초기 로드 경로 예산** — 판정 자체는 ``check-bundle-budget.mjs`` 가 CI 에서
       수행한다(빌드 산출물이 필요하므로 pytest 레인에 빌드를 끌어들이지 않는다).
       여기서는 그 게이트가 **존재하고 · 파생값 규율을 지키고 · 실제로 red 가
       되는지**(S8 비-공허성)를 봉인한다.
    """

    WEB = WEB_ROOT
    OBSERVABILITY_DIR = SRC_DIR / "observability"

    #: 게이트 조건을 **쓸 수 있는** 유일한 모듈. ratchet-down(단조감소) — 추가하려면
    #: 왜 SSOT 위임이 불가능한지 기록해야 한다.
    GATE_SSOT_MODULE = "observability/enablement.ts"

    #: 활성화 조건의 **판정**을 재작성한 형태. 필드를 *읽는* 것(예: exporter 에
    #: `url: config.otelCollectorUrl` 을 넘기는 것)은 위반이 아니다 — 판정,
    #: 즉 진리값 검사만 잡는다.
    GATE_COPY_PATTERNS = (
        re.compile(r"!\s*\w*[Cc]onfig\??\.\s*(?:otelCollectorUrl|sentryDsn)\b"),
        re.compile(r"(?:otelCollectorUrl|sentryDsn)\s*(?:===|!==|==|!=)\s*(?:null|undefined|'')"),
        re.compile(r"Boolean\(\s*\w*[Cc]onfig\??\.\s*(?:otelCollectorUrl|sentryDsn)\s*\)"),
        re.compile(r"(?:otelCollectorUrl|sentryDsn)\s*\?\?"),
    )

    #: 초기 로드 경로에서 **정적으로 도달하면 안 되는** 무거운 패키지.
    ON_DEMAND_PACKAGES = (
        "@sentry/browser",
        "@opentelemetry/sdk-trace-web",
        "@opentelemetry/sdk-trace-base",
        "@opentelemetry/instrumentation",
        "@opentelemetry/instrumentation-fetch",
        "@opentelemetry/instrumentation-xml-http-request",
        "@opentelemetry/exporter-trace-otlp-http",
        "@opentelemetry/context-zone",
    )

    #: 그 패키지들을 정적 import 해도 되는 유일한 모듈들 = on-demand 청크 자체.
    ON_DEMAND_ENTRYPOINTS = frozenset({"observability/tracing.ts", "observability/sentry-runtime.ts"})

    def test_gate_condition_is_written_in_exactly_one_module(self):
        offenders: list[str] = []
        for path in _src_files():
            rel = _rel(path)
            if rel == self.GATE_SSOT_MODULE:
                continue
            body = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for pattern in self.GATE_COPY_PATTERNS:
                for match in pattern.finditer(body):
                    line = body[: match.start()].count("\n") + 1
                    offenders.append(f"{rel}:{line} — {match.group(0).strip()}")
        self.assertEqual(
            offenders,
            [],
            "관측성 활성화 조건은 observability/enablement.ts 에서만 판정한다. "
            "로드 지점과 모듈 내부가 각자 조건을 들고 있으면 한쪽만 바뀔 때 "
            "조용히 어긋난다(로드는 되는데 no-op / 설정했는데 미로드). "
            f"사본: {offenders}",
        )

    def test_gate_ssot_module_exists_and_is_pure(self):
        gate = self.OBSERVABILITY_DIR / "enablement.ts"
        self.assertTrue(gate.is_file(), "missing observability/enablement.ts gate SSOT")
        body = _strip_ts_comments(gate.read_text(encoding="utf-8"))
        for name in ("isTracingEnabled", "isSentryEnabled"):
            self.assertIn(f"export function {name}", body, f"{name} must be the exported gate")
        # 순수 술어 — 타입 외의 import 가 붙는 순간 "게이트를 물어보는" 비용이
        # 초기 로드 경로로 새어 들어온다.
        value_imports = [
            line
            for line in body.splitlines()
            if line.startswith("import ") and not line.startswith("import type ")
        ]
        self.assertEqual(
            value_imports,
            [],
            f"enablement.ts must stay type-only-import pure (found {value_imports})",
        )

    def test_heavy_sdks_are_only_reachable_from_the_on_demand_modules(self):
        offenders: list[str] = []
        for path in _src_files():
            rel = _rel(path)
            if rel in self.ON_DEMAND_ENTRYPOINTS:
                continue
            body = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for line_no, line in enumerate(body.splitlines(), start=1):
                stripped = line.strip()
                if not stripped.startswith("import ") or stripped.startswith("import type "):
                    continue
                for package in self.ON_DEMAND_PACKAGES:
                    if f"'{package}'" in stripped:
                        offenders.append(f"{rel}:{line_no} — {package}")
        self.assertEqual(
            offenders,
            [],
            "무거운 관측성 SDK 는 on-demand 모듈(tracing.ts / sentry-runtime.ts)에서만 "
            "정적 import 한다. 초기 로드 경로 모듈이 하나라도 정적으로 참조하면 "
            "청크가 entry graph 로 되돌아온다 — main.tsx 만 고쳐서는 막히지 않는 축이다 "
            f"(captureException 이 정확히 그 경로였다). 위반: {offenders}",
        )

    def test_loader_uses_dynamic_import_for_both_backends(self):
        body = _strip_ts_comments(
            (self.OBSERVABILITY_DIR / "bootstrap.ts").read_text(encoding="utf-8")
        )
        for module in ("./tracing", "./sentry-runtime"):
            self.assertRegex(
                body,
                rf"await import\(\s*'{re.escape(module)}'\s*\)",
                f"bootstrap.ts must load {module} via dynamic import()",
            )
        for gate in ("isTracingEnabled", "isSentryEnabled"):
            self.assertIn(gate, body, f"bootstrap.ts must delegate to {gate}")

    # ── S7 / S8 — 초기 로드 경로 예산 ────────────────────────────────────────

    def _budget(self) -> dict:
        raw = (self.WEB / "bundle-budget.json").read_text(encoding="utf-8")
        return json.loads(raw)["initialLoadPathJs"]

    def test_initial_load_path_budget_is_measured_derived_not_magic(self):
        import math

        budget = self._budget()
        measured = budget["measuredGzipBytes"]
        factor = budget["headroomFactor"]
        self.assertIsInstance(measured, int)
        self.assertGreater(measured, 0)
        self.assertGreaterEqual(factor, 1.0)
        self.assertEqual(
            budget["maxGzipBytes"],
            math.ceil(measured * factor),
            "initialLoadPathJs.maxGzipBytes 는 실측 baseline 에서 파생돼야 한다 "
            "(ceil(measured * headroom)) — 손으로 올리지 말고 재측정할 것.",
        )
        # 임의값 금지: 측정 대상 정의와 근거가 파일 안에 남아 있어야 한다.
        for key in ("_doc", "measuredAt", "measuredCommitNote", "headroomRationale"):
            self.assertTrue(str(budget.get(key, "")).strip(), f"initialLoadPathJs.{key} is empty")

    def test_budget_headroom_cannot_absorb_the_regression_it_guards(self):
        """예산은 자기가 막겠다는 회귀보다 **작은** 여유만 가져야 한다.

        여유가 청크보다 크면 게이트는 초록인 채로 결함이 되돌아온다 — 예산이
        있다는 사실 자체가 알리바이가 되는, 가장 나쁜 형태의 공허한 봉인.
        """
        budget = self._budget()
        headroom = budget["maxGzipBytes"] - budget["measuredGzipBytes"]
        self.assertGreater(headroom, 0)
        # 이 웨이브가 초기 경로 밖으로 옮긴 두 청크 중 **작은 쪽**(tracing) 의
        # 실측 gzip. 이보다 여유가 크면 tracing 재유입이 예산 안에 숨는다.
        smallest_removed_chunk_gzip = 36414
        self.assertLess(
            headroom,
            smallest_removed_chunk_gzip,
            f"headroom {headroom} B 가 tracing 청크 {smallest_removed_chunk_gzip} B 보다 크다 — "
            "예산이 자기가 막겠다는 회귀를 흡수한다.",
        )

    def test_measurement_defines_the_initial_load_path_from_the_built_html(self):
        """측정 대상이 '번들 전체'가 아니라 **초기 로드 경로**임을 봉인.

        lazy 라우트 청크까지 잡는 예산은 개발자가 꺼 버린다(그리고 실제로 기존
        ``totalGzipBytes`` 예산은 이 웨이브의 114 kB 개선을 잡음으로 처리했다).
        """
        measure = (self.WEB / "scripts" / "measure-bundle.mjs").read_text(encoding="utf-8")
        self.assertIn("initialLoadPathJs", measure)
        self.assertIn("dist", measure)
        self.assertIn("index.html", measure)
        self.assertIn("measureInitialLoadPath", measure)

        gate = (self.WEB / "scripts" / "check-bundle-budget.mjs").read_text(encoding="utf-8")
        self.assertIn("initialLoadPathJs", gate)
        self.assertIn("headroomFactor", gate)

    def test_ci_runs_the_budget_gate(self):
        """게이트가 실행되지 않으면 예산은 문서일 뿐이다."""
        workflows = PROJECT_ROOT / ".github" / "workflows"
        runners = [
            name
            for name in ("frontend.yml", "frontend-deploy.yml")
            if "check-bundle-budget.mjs" in (workflows / name).read_text(encoding="utf-8")
        ]
        self.assertEqual(
            sorted(runners),
            ["frontend-deploy.yml", "frontend.yml"],
            "초기 로드 경로 예산은 기존 bundle-budget 게이트에 편승한다 — 그 게이트를 "
            "실행하는 워크플로가 사라지면 봉인도 사라진다.",
        )

    def test_budget_gate_actually_fails_when_the_initial_path_grows(self):
        """S8 — 비-공허성. 예산을 넘기는 측정치를 넣으면 정말 red 가 되는가.

        빌드 산출물 없이 판정 로직만 검증한다: 게이트가 읽는
        ``dist/bundle-size.json`` 을 임시 트리에 합성해 under/over 두 경우를 돌린다.
        """
        node = shutil.which("node")
        if node is None:  # pragma: no cover - 개발/CI 머신에는 node 가 있다
            self.skipTest("node unavailable — the JS budget gate cannot be exercised here")

        budget = self._budget()
        gate = self.WEB / "scripts" / "check-bundle-budget.mjs"
        total_budget = json.loads((self.WEB / "bundle-budget.json").read_text(encoding="utf-8"))

        def run(initial_gzip: int) -> subprocess.CompletedProcess[str]:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "scripts").mkdir()
                (root / "dist").mkdir()
                shutil.copyfile(gate, root / "scripts" / "check-bundle-budget.mjs")
                shutil.copyfile(self.WEB / "bundle-budget.json", root / "bundle-budget.json")
                (root / "dist" / "bundle-size.json").write_text(
                    json.dumps(
                        {
                            # 총량 축은 통과시켜 두어야 초기-경로 축의 판정만 관측된다.
                            "totalGzipBytes": total_budget["maxGzipBytes"] - 1,
                            "initialLoadPathJs": {"gzipBytes": initial_gzip, "chunks": []},
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.run(  # noqa: S603
                    [node, str(root / "scripts" / "check-bundle-budget.mjs")],
                    capture_output=True,
                    text=True,
                    check=False,
                )

        under = run(budget["maxGzipBytes"])
        self.assertEqual(under.returncode, 0, f"예산 이내인데 red: {under.stderr}")

        over = run(budget["maxGzipBytes"] + 1)
        self.assertEqual(
            over.returncode,
            1,
            "예산을 1 B 넘겨도 초록이면 봉인이 공허하다.",
        )
        self.assertIn("initial load path", over.stderr)


class TestWsBearerCommentHonesty(unittest.TestCase):
    """M4 (W3-4, 2026-08-01) — the WS event-stream modules must not claim an
    AuthZ mechanism that does not exist, and must describe the one that does.

    Before this wave, both ``chamber-events.ts`` and ``session-events.ts``
    claimed "the reverse proxy injects X-FCC-Permissions from the validated
    OIDC session" — true only for ``trusted_headers`` auth mode. In
    ``oidc_jwt`` mode there is no such proxy, so the docblock promised a
    mechanism that silently did not exist while browser WS connections had
    NO way to authenticate at all — until this wave's Sec-WebSocket-Protocol
    bearer overlay (``ws-bearer.ts``).
    """

    _WS_MODULES = (
        SRC_DIR / "api" / "chamber-events.ts",
        SRC_DIR / "api" / "session-events.ts",
    )

    def test_false_reverse_proxy_claim_is_gone_from_api_tree(self) -> None:
        """Negative — scanned RAW (not comment-stripped): the false claim
        lived inside a docblock comment, so a comment-stripped scan would
        never have caught it in the first place."""
        api_dir = SRC_DIR / "api"
        offenders: list[str] = []
        for path in _src_files():
            try:
                path.relative_to(api_dir)
            except ValueError:
                continue
            raw = path.read_text(encoding="utf-8")
            if "reverse proxy injects" in raw:
                offenders.append(_rel(path))
        self.assertEqual(
            offenders, [],
            f"false 'reverse proxy injects' AuthZ claim still present: {offenders}",
        )

    def test_ws_modules_describe_the_actual_subprotocol_mechanism(self) -> None:
        """Positive — the two docblocks must actually name the real
        mechanism (guards against a hollow negative-only seal: silently
        deleting the sentence without replacing it would still pass a
        negative-only check)."""
        for path in self._WS_MODULES:
            self.assertTrue(path.is_file(), f"{path} must exist")
            raw = path.read_text(encoding="utf-8")
            self.assertIn(
                "Sec-WebSocket-Protocol", raw,
                f"{path.name} docblock must describe the subprotocol bearer mechanism",
            )
            self.assertIn(
                "ws-bearer", raw,
                f"{path.name} docblock must reference the ws-bearer.ts SSOT",
            )

    def test_normal_form_unrelated_comment_is_not_flagged(self) -> None:
        """M5 normal-form control — an ordinary comment mentioning neither
        phrase must not trip either assertion path."""
        benign = "// this stream reconnects with exponential backoff\n"
        self.assertNotIn("reverse proxy injects", benign)
        self.assertNotIn("Sec-WebSocket-Protocol", benign)


class TestWsReconnectPolicySymmetry(unittest.TestCase):
    """M8 (W3-4, 2026-08-01) — ``chamber-events.ts`` and ``session-events.ts``
    must consume the SAME reconnect-decision policy function
    (``ws-bearer.ts``'s ``wsReconnectDecision``), not two independently
    maintained copies.

    Before this wave, ``session-events.ts`` reconnected unconditionally on
    every close with no close-code branch and no attempt cap — a server
    policy rejection (1008/1003) would retry forever. This wave promotes the
    previously chamber-only policy (``chamberReconnectDecision``) to a
    technology-neutral shared function so both streams close the same way;
    ``chamber-events.ts`` keeps re-exporting the old chamber-scoped names as
    thin aliases so existing importers (``tests/api/chamber-stream-robustness
    .test.ts``) keep compiling unchanged.
    """

    _API_DIR = SRC_DIR / "api"
    _WS_BEARER = _API_DIR / "ws-bearer.ts"
    _CHAMBER = _API_DIR / "chamber-events.ts"
    _SESSION = _API_DIR / "session-events.ts"

    def test_shared_policy_function_defined_in_ws_bearer_only(self) -> None:
        raw = self._WS_BEARER.read_text(encoding="utf-8")
        self.assertIn("export function wsReconnectDecision(", raw)
        # Negative half — a second independent definition (e.g. a
        # copy-pasted decision function) in either stream module is exactly
        # the drift this test guards against.
        for path in (self._CHAMBER, self._SESSION):
            other = path.read_text(encoding="utf-8")
            self.assertNotIn("function wsReconnectDecision(", other)

    def test_both_streams_consume_the_shared_policy_function(self) -> None:
        for path in (self._CHAMBER, self._SESSION):
            raw = path.read_text(encoding="utf-8")
            self.assertIn(
                "wsReconnectDecision", raw,
                f"{path.name} must consume the shared wsReconnectDecision (ws-bearer.ts)",
            )
            self.assertIn(
                "from './ws-bearer'", raw,
                f"{path.name} must import from the ws-bearer SSOT",
            )

    def test_session_events_close_handler_delegates_to_the_shared_decision(self) -> None:
        """Red if ``onclose`` reverts to an unconditional ``scheduleReconnect()``
        call that ignores the close code entirely (the pre-M8 defect)."""
        raw = self._SESSION.read_text(encoding="utf-8")
        self.assertIn("handleDisconnect(event?.code)", raw)
        self.assertIn("wsReconnectDecision(closeCode, attempt, maxAttempts)", raw)

    def test_both_streams_default_to_the_shared_reconnect_budget_constant(self) -> None:
        for path in (self._CHAMBER, self._SESSION):
            raw = path.read_text(encoding="utf-8")
            self.assertIn(
                "DEFAULT_MAX_WS_RECONNECT_ATTEMPTS", raw,
                f"{path.name} must default its reconnect budget to the shared constant",
            )

    def test_normal_form_unrelated_import_is_not_flagged(self) -> None:
        """M5 normal-form control — a module that imports something else
        from ``ws-bearer.ts`` (not the reconnect policy) must not trip the
        consumption assertion above via a substring accident."""
        benign = "import { WS_BEARER_SUBPROTOCOL } from './ws-bearer';\n"
        self.assertNotIn("wsReconnectDecision", benign)


if __name__ == "__main__":
    unittest.main()


class TestFreeFormObjectDoesNotCollapseToNever(unittest.TestCase):
    """자유형 매핑 필드가 생성 TS 에서 ``never`` 로 붕괴하지 않는다 — **소비자 관점** 봉인.

    test-plan-draft-create-422 (2026-08-01). ``TestNullableRefArtifactDefect`` 과
    같은 결함 계열의 두 번째 축이고, 이쪽은 실제로 **운영자 화면을 멈춰 세웠다**.

    Current generation catalogue mappings must remain usable by generated
    consumers. The old draft-generation scope field is intentionally absent;
    persisted scope snapshots remain a read-only historical surface.

    여기가 소유하는 것은 **소비자 관점**이다:

    1. 프론트가 실제로 보내는/읽는 자유형 필드가 아티팩트에서 쓸 수 있는 매핑으로
       선언돼 있는가(앵커 — 스캔이 "필드가 사라져서" 통과하는 것을 막는다),
    2. 프론트에 우회가 되살아나지 않았는가(``scope_profile: {}`` 페이로드,
       손으로 쓴 ``Record<string, never>``),
    3. codegen 산출물이 있다면 component schema 가 ``never`` 로 붕괴하지 않았는가.

    (3) 은 ``src/api/generated`` 가 gitignore 대상이라 codegen 을 돌린 트리에서만
    관측된다 — 그래서 봉인의 무게는 항상 도는 (1)/(2) 가 진다. 이 비대칭은 숨기지
    않고 그 테스트가 스스로 밝힌다.

    생성기 봉인은 ``tests/test_architecture_conformance.py``
    ``::TestFreeFormObjectNormalisesToAnExplicitMapping``, 아티팩트 전수 스캔은
    ``tests/test_api_contract_artifact_phase25.py``
    ``::TestFreeFormObjectIsEmittedAsAnExplicitMapping`` 가 소유한다.
    """

    #: 프론트 codegen 이 소비하는 아티팩트 3종.
    _ARTIFACTS: tuple[str, ...] = (
        "platform-api.openapi.json",
        "headless-api.openapi.json",
        "session-api.openapi.json",
    )

    #: 프론트가 실제로 **보내는/읽는** 자유형 매핑 필드. (아티팩트, 스키마, 필드)
    _CONSUMED: tuple[tuple[str, str, str], ...] = (
        ("headless-api.openapi.json", "TestPlanGenerationCatalogue", "bands_per_subfamily"),
        ("headless-api.openapi.json", "MeasurementResultEnvelope", "condition"),
        ("headless-api.openapi.json", "MeasurementResultEnvelope", "result"),
    )

    _WORKBENCH = SRC_DIR / "routes" / "test-plans" / "TestPlansWorkbench.tsx"

    def _schemas(self, artifact: str) -> dict:
        path = PROJECT_ROOT / "docs" / "api" / artifact
        return json.loads(path.read_text(encoding="utf-8"))["components"]["schemas"]

    # ── (1) 소비 필드 앵커 ───────────────────────────────────────────────

    def test_consumed_free_form_fields_are_usable_mappings(self) -> None:
        for artifact, schema_name, field in self._CONSUMED:
            with self.subTest(artifact=artifact, schema=schema_name, field=field):
                spec = self._schemas(artifact)[schema_name]["properties"][field]
                additional_properties = spec.get("additionalProperties")
                self.assertNotIn(
                    additional_properties,
                    (None, False),
                    "프론트가 소비하는 자유형 필드가 닫힌 object 로 선언됐다 — "
                    "생성 TS 가 `Record<string, never>` 로 붕괴한다",
                )
                if field == "bands_per_subfamily":
                    self.assertIsInstance(additional_properties, dict)
                    self.assertEqual(additional_properties.get("type"), "array")

    def test_the_create_draft_request_is_manual_only(self) -> None:
        """Manual creation has no client-built generation snapshot."""
        schema = self._schemas("headless-api.openapi.json")["CreateTestPlanDraftRequest"]
        self.assertEqual(set(schema["properties"]), {"created_by"})
        self.assertFalse(schema["additionalProperties"])

    # ── (2) 프론트 우회 부재 ─────────────────────────────────────────────

    def test_the_workbench_does_not_send_an_empty_scope_profile(self) -> None:
        """결함 페이로드가 되살아나지 않는다.

        ``{}`` 를 보내는 것은 "아무 scope 도 아님" 이 아니라 **해석 불가능한
        스냅샷**이다. 서버가 이제 합성하므로 보낼 이유 자체가 없다.
        """
        code = _strip_ts_comments(self._WORKBENCH.read_text(encoding="utf-8"))
        self.assertNotRegex(
            code,
            r"scope_profile\s*:\s*\{\s*\}",
            "TestPlansWorkbench 가 빈 scope_profile 을 다시 보내고 있다 — "
            "그 페이로드는 영구 422 의 본체였다",
        )

    def test_no_hand_written_source_declares_the_never_shaped_record(self) -> None:
        """손으로 쓴 ``Record<string, never>`` 0건.

        생성 타입이 그 모양이면 소비자가 캐스트로 우회하려 들고, 그 순간 결함이
        타입 시스템에서 사라지되 런타임엔 그대로 남는다.
        """
        sites = _offending_sites(re.compile(r"Record<\s*string\s*,\s*never\s*>"))
        self.assertEqual(
            sites,
            [],
            "손으로 쓴 Record<string, never> 는 자유형 매핑의 우회다 — "
            "선언을 넓혀라",
        )

    def test_the_workbench_still_calls_the_create_surface(self) -> None:
        """비-공허성 — 위 두 음성 단언이 "그 코드가 사라져서" 통과하지 않는다."""
        code = _strip_ts_comments(self._WORKBENCH.read_text(encoding="utf-8"))
        ok, why = _consumes_headless_path(
            code, "/headless/projects/{project_id}/test-plan/drafts"
        )
        self.assertTrue(ok, why)
        self.assertIn("createTestPlanDraft(projectId, createdBy)", code)
        self.assertRegex(
            _headless_operations()["createTestPlanDraft"], r"body:\s*\{\s*created_by"
        )

    def test_the_negative_scan_flags_a_synthetic_offender(self) -> None:
        """비-공허성 — 판정식이 결함 형상에 실제로 반응한다."""
        pattern = re.compile(r"scope_profile\s*:\s*\{\s*\}")
        self.assertRegex("body: { created_by: c, scope_profile: {} }", pattern)
        self.assertNotRegex("body: { created_by: c }", pattern)

    # ── (3) codegen 산출물 (있을 때만 — 그 사실을 스스로 밝힌다) ─────────

    def test_generated_component_schemas_do_not_collapse_to_never(self) -> None:
        """codegen 을 돌린 트리에서만 관측 가능한 확증 단언.

        ``src/api/generated`` 는 gitignore 대상이라 신선한 체크아웃에는 없다.
        여기서 확인하는 범위는 **component schema** 로 한정한다 —
        ``webhooks``/``$defs`` 는 문서 섹션 부재를 나타내는 생성기 보일러플레이트고,
        ``stop_session`` 요청 본문의 ``Record<string, never>`` 는 선언이 실제로
        ``additionalProperties: false`` 라 **정확한 렌더**다(결함이 아니다).
        """
        blocks = 0
        for path in sorted(GENERATED_DIR.glob("*.types.ts")):
            match = re.search(
                r"export interface components \{\n    schemas: \{\n(.*?)\n    \};\n",
                path.read_text(encoding="utf-8"),
                re.DOTALL,
            )
            if match is None:
                continue
            blocks += 1
            with self.subTest(artifact=path.name):
                self.assertNotIn(
                    "Record<string, never>",
                    match.group(1),
                    "생성된 component schema 가 `never` 로 붕괴했다 — "
                    "아티팩트의 맨몸 object 를 보라",
                )
        if blocks == 0:
            self.skipTest(
                "codegen 산출물 없음 (`npm run codegen` 미실행, gitignore 대상). "
                "이 확증 단언은 관측 불가 — 봉인의 무게는 아티팩트/프론트 소스 "
                "단언이 진다.",
            )

class TestCurrentTestPlanGenerationConsumer(unittest.TestCase):
    """Current test-plan generation is a typed catalogue/preview/job consumer."""

    FORM_MODULE = SRC_DIR / "routes" / "test-plans" / "GenerateTestPlanForm.tsx"
    WORKBENCH_MODULE = SRC_DIR / "routes" / "test-plans" / "TestPlansWorkbench.tsx"
    QUERY_CONFIG = SRC_DIR / "api" / "query-config.ts"

    # These are the only handbook/visual fixture surfaces that historically
    # carried the retired scope-options request. Keep the proof path-specific:
    # backend negative tests and stored scope snapshots are not consumers.
    LEGACY_SCOPE_FIXTURES: tuple[Path, ...] = (
        WEB_ROOT / "scripts" / "capture-handbook-fixtures.mjs",
        WEB_ROOT / "scripts" / "capture-handbook-screens.mjs",
        WEB_ROOT / "tests" / "e2e" / "helpers" / "visual-fixture.ts",
    )
    LEGACY_SCOPE_CONSUMER_RE = re.compile(
        r"(?:/headless/test-plan/scope-options|scope_selection|"
        r"test_plan_scope_options|GenerateFromScopeForm|scope-selection)"
    )

    _CURRENT_ENDPOINTS: tuple[str, ...] = (
        "/headless/test-plan/generation/catalogue",
        "/headless/projects/{project_id}/test-plan/generation/preview",
        "/headless/projects/{project_id}/test-plan/generations",
        "/headless/projects/{project_id}/test-plan/generations/{generation_job_id}",
        "/headless/projects/{project_id}/test-plan/drafts/{draft_id}/generation-metadata",
        "/headless/projects/{project_id}/test-plan/drafts/{draft_id}/rows",
    )

    def _code(self, path: Path) -> str:
        return _strip_ts_comments(path.read_text(encoding="utf-8"))

    def test_generation_form_consumes_the_current_typed_async_surface(self) -> None:
        form = self._code(self.FORM_MODULE)
        for endpoint in self._CURRENT_ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                ok, why = _consumes_headless_path(form, endpoint)
                self.assertTrue(ok, why)
        # ⚠️ `Idempotency-Key` 는 **operation 이** 소유한다(헤더를 보내는 것은 전송이다).
        # 이 화면이 소유하는 것은 *그 키가 재시도 사이에 안정하다* 는 사실이고,
        # 그것이 `idempotencyKey` ref 로 여기 남아 있어야 한다.
        self.assertIn("idempotencyKey", form)
        submit_owner = _operations_owning_path(
            "/headless/projects/{project_id}/test-plan/generations"
        )
        self.assertTrue(
            any(
                "Idempotency-Key" in _headless_operations()[name]
                for name in submit_owner
            ),
            "generation 제출 operation 이 Idempotency-Key 를 보내지 않는다",
        )
        for query_factory in (
            "generationCatalogue",
            "generationJob",
            "generationMetadata",
            "generationRowsPrefix",
            "generationRows",
        ):
            with self.subTest(query_factory=query_factory):
                self.assertIn(f"queryKeys.testPlans.{query_factory}", form)

    def test_generation_form_has_no_removed_generation_surface(self) -> None:
        form = self._code(self.FORM_MODULE)
        for removed in (
            "scope-options",
            "scope_selection",
            "GenerateFromScopeForm",
            "./scope-selection",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, form)

    def test_workbench_keeps_manual_creation_and_current_generator(self) -> None:
        workbench = self._code(self.WORKBENCH_MODULE)
        self.assertIn("GenerateTestPlanForm", workbench)
        ok, why = _consumes_headless_path(
            workbench, "/headless/projects/{project_id}/test-plan/drafts"
        )
        self.assertTrue(ok, why)
        # ⚠️ `created_by` 는 이제 operation 의 **인자**다(body 조립은 전송이다).
        # 화면이 소유하는 것은 *그 값을 넘긴다* 이고, *그것이 body 에 실린다* 는
        # 두 번째 링크가 확인한다.
        self.assertIn("createTestPlanDraft(projectId, createdBy)", workbench)
        self.assertRegex(
            _headless_operations()["createTestPlanDraft"], r"body:\s*\{\s*created_by"
        )
        for removed in ("scope-options", "scope_selection", "GenerateFromScopeForm"):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, workbench)

    def test_removed_generation_endpoint_scan_is_non_vacuous(self) -> None:
        """The negative guard detects an old consumer shape before checking green."""
        old_endpoint = re.compile(r"/headless/test-plan/scope-options")
        synthetic = "client.GET('/headless/test-plan/scope-options', {})"
        self.assertRegex(synthetic, old_endpoint)
        self.assertNotRegex(self._code(self.FORM_MODULE), old_endpoint)

    def test_named_fixtures_have_no_positive_legacy_scope_consumer(self) -> None:
        """The retired request cannot return through handbook/visual fixtures.

        This is intentionally a comment-stripped static/grep-style scan over the
        exact three fixture paths. The planted source proves the scanner is not
        green merely because it found no files or no matches.
        """
        self.assertEqual(len(self.LEGACY_SCOPE_FIXTURES), 3)
        planted = "const request = '/headless/test-plan/scope-options';"
        self.assertRegex(planted, self.LEGACY_SCOPE_CONSUMER_RE)
        self.assertIsNone(self.LEGACY_SCOPE_CONSUMER_RE.search("const status = 404;"))

        offenders: list[str] = []
        for path in self.LEGACY_SCOPE_FIXTURES:
            self.assertTrue(path.is_file(), f"missing fixture: {path}")
            code = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for match in self.LEGACY_SCOPE_CONSUMER_RE.finditer(code):
                line = code.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{line}")
        self.assertEqual(
            offenders,
            [],
            "retired positive scope-options/scope_selection consumer remains in "
            f"a named fixture: {offenders}",
        )

    # ⚠️ `test_legacy_http_negatives_and_scope_snapshot_metadata_remain_proven` 는 2026-08-31 에 모노레포로 돌아갔다 — 읽던 대상이
    #    `test_test_plan_draft_api_impl3.py` 이고 그 파일은 사용자의 측정 자산(`column_names` 등)을
    #    임포트하므로 공개 레포에 실을 수 없다.


    def test_query_key_factory_is_shared_with_the_generation_form(self) -> None:
        query_config = self._code(self.QUERY_CONFIG)
        form = self._code(self.FORM_MODULE)
        self.assertIn("export const queryKeys", query_config)
        self.assertIn("generationCatalogue:", query_config)
        self.assertIn("generationRowsPrefix:", query_config)
        self.assertIn("import", form)
        self.assertIn("queryKeys", form)


EQUIPMENT_LISTS_PATH = "/equipment-lists"
#: 경로 리터럴을 SSOT 밖에서 쓸 수 있는 자리 — 라우트 정의 자체와 e2e 스윕 목록.
EQUIPMENT_LISTS_PATH_LITERAL_ALLOWLIST = frozenset({"shared/route-links.ts"})


class TestEquipmentListRouteReachability(unittest.TestCase):
    """성적서 §6 장비목록 화면의 도달성 (2026-08-07).

    ``TestTestReportRouteReachability`` 와 같은 네 축을 본다 — 모듈 존재 / 라우터
    등록 / 전역 nav 도달 / 경로 리터럴의 ``ROUTE_PATHS`` SSOT 경유. 셋 중 하나만
    빠져도 "만들었는데 아무도 못 가는 화면"이 된다.
    """

    def test_the_route_module_exists(self) -> None:
        self.assertTrue((SRC_DIR / "routes" / "equipment-lists.tsx").is_file())

    def test_registered_in_the_router(self) -> None:
        app = _strip_ts_comments((SRC_DIR / "app.tsx").read_text(encoding="utf-8"))
        self.assertIn("@/routes/equipment-lists", app, "라우트 모듈이 lazy 등록되지 않았다")
        self.assertRegex(
            app,
            r"path:\s*'equipment-lists'",
            "라우터에 equipment-lists 경로가 등록되지 않았다",
        )

    def test_reachable_from_the_global_nav(self) -> None:
        app_shell = _strip_ts_comments(
            (SRC_DIR / APP_SHELL_NAVIGATION_MODULE).read_text(encoding="utf-8")
        )
        self.assertRegex(
            app_shell,
            r"to:\s*ROUTE_PATHS\.equipmentLists,\s*"
            r"labelKey:\s*'routes\.layout\.nav\.equipmentLists',",
            "APP_SHELL_NAV_GROUPS 에서 equipment-lists 에 도달할 수 없다",
        )

    def test_path_literal_lives_in_the_route_paths_ssot(self) -> None:
        links = (SRC_DIR / "shared" / "route-links.ts").read_text(encoding="utf-8")
        self.assertRegex(links, r"equipmentLists:\s*'/equipment-lists'")

        offenders = [
            _rel(path)
            for path in _src_files()
            if _rel(path) not in EQUIPMENT_LISTS_PATH_LITERAL_ALLOWLIST
            and f"'{EQUIPMENT_LISTS_PATH}'" in _strip_ts_comments(
                path.read_text(encoding="utf-8")
            )
        ]
        self.assertEqual(
            offenders,
            [],
            "라우트 경로 리터럴이 SSOT 밖에 흩어졌다 — ROUTE_PATHS.equipmentLists 를 "
            f"쓰라: {offenders}",
        )

    def test_the_route_paths_key_has_a_real_consumer(self) -> None:
        consumers = [
            _rel(path)
            for path in _src_files()
            if _rel(path) != "shared/route-links.ts"
            and "ROUTE_PATHS.equipmentLists" in _strip_ts_comments(
                path.read_text(encoding="utf-8")
            )
        ]
        self.assertNotEqual(consumers, [], "ROUTE_PATHS.equipmentLists 를 읽는 곳이 없다")


class TestEquipmentListColumnsComeFromTheServer(unittest.TestCase):
    """성적서 §6 두 표의 **열 순서는 서버가 소유**한다 (2026-08-07).

    같은 순서를 백엔드 어댑터 INSERT, OpenAPI item 스키마, 이 화면의 표 헤더,
    그리고 ③단계 DOCX patcher 가 모두 필요로 한다. 프론트가 열 배열을 자기 쪽에
    다시 선언하면 규칙이 TS/Python 두 곳으로 쪼개지고, **그 드리프트는 제출된
    성적서에서만 드러난다**. 서버가 응답 ``tables[]`` 로 순서를 싣고 화면은 그것을
    읽기만 한다(``Derived-Value No-Client-Recompute SSOT`` 의 같은 계열).

    금지 스캔만 두면 공허하다 — 화면이 열을 아예 안 그려도 통과한다. 그래서
    (a) 서버 필드를 실제로 소비하는가, (b) 열 이름 시퀀스를 재선언하지 않는가,
    (c) 합성 offender 가 red 인가를 **모두** 단언한다.
    """

    #: 성적서 §6 장비표의 열 이름 — 도메인 정책이 소유한다.
    _EQUIPMENT_COLUMNS = (
        "description",
        "manufacturer",
        "model_name",
        "serial_number",
        "calibration_due_date",
    )

    def _route_source(self) -> str:
        return _strip_ts_comments(
            (SRC_DIR / "routes" / "equipment-lists.tsx").read_text(encoding="utf-8")
        )

    @staticmethod
    def _declares_the_column_sequence(source: str) -> bool:
        r"""열 이름들이 **한 배열 리터럴 안에** 함께 나타나는가 = 순서 사본.

        개별 단어(``'description'``)는 폼 라벨 키 등으로 정당하게 등장하므로
        단어 단위 금지는 과탐이 된다. 시퀀스가 통째로 재선언된 경우만 잡는다.

        **중첩 배열을 반드시 본다.** 첫 판본은 ``\[([^\[\]]*)\]`` 였는데 그것은
        대괄호를 포함하지 않는 본문만 매칭하므로 ``[['a',…],['b',…]]`` 형태의
        pair 배열을 통째로 놓쳤다 — 실제로 그 형태의 사본이 화면에 있었고 가드는
        green 이었다. 여기서는 대괄호 깊이를 세어 **모든** 배열 구간을 본다.
        """
        targets = set(TestEquipmentListColumnsComeFromTheServer._EQUIPMENT_COLUMNS)
        opens: list[int] = []
        for index, char in enumerate(source):
            if char == "[":
                opens.append(index)
            elif char == "]" and opens:
                start = opens.pop()
                body = source[start + 1 : index]
                if targets <= set(re.findall(r"'([a-z_]+)'", body)):
                    return True
        return False

    def test_the_route_consumes_the_server_table_spec(self) -> None:
        """비-공허성 — 화면이 응답 ``tables`` 를 실제로 순회한다."""
        source = self._route_source()
        self.assertIn("detail.tables.map", source, "서버가 준 표 명세를 소비하지 않는다")
        self.assertIn("table.columns.map", source, "서버가 준 열 순서를 소비하지 않는다")

    def test_the_route_does_not_redeclare_the_column_order(self) -> None:
        self.assertFalse(
            self._declares_the_column_sequence(self._route_source()),
            "§6 표의 열 순서가 프론트에 재선언됐다 — 응답 tables[] 를 쓰라",
        )

    def test_the_guard_is_not_vacuous(self) -> None:
        """합성 offender 는 반드시 red."""
        planted = (
            "const COLUMNS = ['description', 'manufacturer', 'model_name', "
            "'serial_number', 'calibration_due_date'];"
        )
        self.assertTrue(self._declares_the_column_sequence(planted))

    def test_the_guard_sees_nested_arrays(self) -> None:
        """첫 판본이 놓쳤던 형태 — pair 배열 안의 사본도 red."""
        planted = (
            "const PAIRS = [['section_name', a], ['description', b], "
            "['manufacturer', c], ['model_name', d], ['serial_number', e], "
            "['calibration_due_date', f]];"
        )
        self.assertTrue(self._declares_the_column_sequence(planted))

    def test_the_guard_ignores_unrelated_arrays(self) -> None:
        """정상 형태(부분집합 · 무관한 배열)는 green — 과탐 방지."""
        benign = "const PARTIAL = ['description', 'manufacturer'];\nconst X = ['a', 'b'];"
        self.assertFalse(self._declares_the_column_sequence(benign))

    def test_sort_order_is_not_sent(self) -> None:
        """``sort_order`` 는 배열 위치이고 서버가 부여한다 — 요청에 실으면 안 된다."""
        source = self._route_source()
        self.assertNotIn("sort_order:", source, "sort_order 를 요청 본문에 싣고 있다")


class TestEquipmentListTestItemsComeFromTheServer(unittest.TestCase):
    """시험항목 **어휘**도 서버가 소유한다 (2026-08-08).

    시험항목은 성적서 한 편(DTS/BLE/BT/UNII)에 대응하는 **닫힌 어휘**이고 도메인
    ``TestItemKey`` 가 소유한다. 열 순서 축(``…ColumnsComeFromTheServer``)과 같은
    규칙이지만 이유가 한 겹 더 있다: 생성된 TS 타입은 **타입 레벨 union** 이라
    런타임 배열을 주지 못하므로, 프론트가 선택지를 만들려면 배열을 적는 수밖에
    없고 그 순간 어휘가 TS/Python 두 곳으로 쪼개진다. 그래서 서버가 목록 응답
    ``test_items`` 로 실어 보내고 화면은 그것을 **순회만** 한다.

    locale 에서 얻는 것은 서버가 준 키의 **번역**이다 — ``tables.${item_type}`` /
    ``fields.${column}`` 과 같은 관용구이고 어휘 재선언이 아니다.

    금지 스캔만 두면 공허하다(선택 UI 를 아예 안 그려도 통과). 그래서 소비·금지·
    합성 offender 세 축을 모두 단언한다.
    """

    #: 도메인 ``TestItemKey`` 의 값 — 프론트에 리터럴로 나타나면 안 되는 토큰.
    _TEST_ITEM_KEYS = ("DTS", "BLE", "BT", "UNII")

    def _route_source(self) -> str:
        return _strip_ts_comments(
            (SRC_DIR / "routes" / "equipment-lists.tsx").read_text(encoding="utf-8")
        )

    @classmethod
    def _redeclares_the_vocabulary(cls, source: str) -> bool:
        """어휘 토큰이 **한 배열 리터럴 안에** 함께 나타나는가 = 어휘 사본.

        열 순서 가드와 같은 대괄호-깊이 스캔이다. 개별 토큰(``'BT'``)은 다른
        맥락에서 정당하게 등장할 수 있으므로 시퀀스가 통째로 재선언된 경우만 잡는다.
        """
        targets = set(cls._TEST_ITEM_KEYS)
        opens: list[int] = []
        for index, char in enumerate(source):
            if char == "[":
                opens.append(index)
            elif char == "]" and opens:
                start = opens.pop()
                body = source[start + 1 : index]
                if targets <= set(re.findall(r"'([A-Za-z0-9_]+)'", body)) | set(
                    re.findall(r'"([A-Za-z0-9_]+)"', body)
                ):
                    return True
        return False

    def test_the_route_consumes_the_server_vocabulary(self) -> None:
        """비-공허성 — 화면이 응답 ``test_items`` 를 실제로 **순회한다**.

        ``test_items`` 문자열이 있는지만 보면 공허하다(읽어놓고 안 쓰면 통과).
        어휘를 담는 식별자를 소스에서 찾아, 그 식별자가 실제로 ``.map(`` 되는지
        확인한다. 중간 변수 이름을 테스트가 고정하지 않으므로 리팩터에 취약하지
        않으면서도 "읽고 버리는" 형태는 red 다.
        """
        source = self._route_source()
        self.assertIn("test_items", source, "서버가 준 시험항목 어휘를 소비하지 않는다")
        holders = set(re.findall(r"(?:const|let)\s+(\w+)[^=;]*=[^;]*test_items", source))
        holders.add("test_items")  # 인라인 순회도 정당한 형태다
        mapped = {
            name
            for name in holders
            if re.search(rf"\b{re.escape(name)}\s*(\?\?\s*\[\]\s*\))?\s*\.map\(", source)
        }
        self.assertNotEqual(
            mapped, set(), "어휘를 순회해 선택지를 그리지 않는다(읽고 버린다)"
        )

    def test_the_consumption_guard_is_not_vacuous(self) -> None:
        """읽어놓고 순회하지 않는 형태는 red 여야 한다."""
        source = self._route_source()
        holders = set(re.findall(r"(?:const|let)\s+(\w+)[^=;]*=[^;]*test_items", source))
        self.assertNotEqual(
            holders, set(), "어휘를 담는 식별자를 찾지 못했다 — 가드가 공허하다"
        )
        planted = "const opts = data?.test_items ?? [];\nconst n = opts.length;"
        planted_holders = re.findall(r"(?:const|let)\s+(\w+)[^=;]*=[^;]*test_items", planted)
        self.assertEqual(planted_holders, ["opts"])
        self.assertIsNone(re.search(r"\bopts\s*\.map\(", planted))

    @staticmethod
    def _enclosing_tag(source: str, marker: str) -> str:
        """``marker`` 를 감싸는 JSX 여는 태그 이름.

        ``<select[^>]*marker`` 같은 정규식은 쓸 수 없다 — 속성값의 화살표 함수
        (``onChange={(e) => …}``)에 ``>`` 가 들어 있어 문자 클래스가 거기서 끊긴다.
        대신 marker 앞쪽에서 **가장 가까운 여는 태그**를 찾는다.
        """
        head = source.split(marker, 1)[0]
        opens = re.findall(r"<([A-Za-z][A-Za-z0-9]*)", head)
        return opens[-1] if opens else ""

    def test_the_test_item_input_is_a_select(self) -> None:
        """자유 입력이면 성적서 어느 편에도 대응하지 않는 목록이 만들어진다."""
        source = self._route_source()
        marker = 'data-testid="equipment-lists-test-item-key"'
        self.assertIn(marker, source, "시험항목 입력이 사라졌다")
        self.assertEqual(
            self._enclosing_tag(source, marker),
            "select",
            "시험항목 입력이 아직 자유 입력(<input>)이다",
        )

    def test_the_enclosing_tag_helper_is_not_vacuous(self) -> None:
        """헬퍼가 화살표 함수의 ``>`` 에 속지 않는지 직접 확인한다."""
        planted_input = (
            '<input\n  onChange={(e) => set(e.target.value)}\n  data-testid="X"\n/>'
        )
        self.assertEqual(self._enclosing_tag(planted_input, 'data-testid="X"'), "input")
        planted_select = (
            '<select\n  onChange={(e) => set(e.target.value)}\n  data-testid="X"\n>'
        )
        self.assertEqual(self._enclosing_tag(planted_select, 'data-testid="X"'), "select")

    def test_the_route_does_not_redeclare_the_vocabulary(self) -> None:
        self.assertFalse(
            self._redeclares_the_vocabulary(self._route_source()),
            "시험항목 어휘가 프론트에 재선언됐다 — 응답 test_items 를 쓰라",
        )

    def test_no_bare_vocabulary_literal_anywhere_in_the_route(self) -> None:
        """배열 밖 단독 리터럴(``key === 'BT'`` 같은 분기)도 사본이다."""
        source = self._route_source()
        offenders = [
            key
            for key in self._TEST_ITEM_KEYS
            if re.search(rf"['\"]{key}['\"]", source)
        ]
        self.assertEqual(
            offenders, [], f"시험항목 토큰이 화면에 하드코딩됐다: {offenders}"
        )

    def test_the_guard_is_not_vacuous(self) -> None:
        """합성 offender 는 반드시 red."""
        planted = "const ITEMS = ['DTS', 'BLE', 'BT', 'UNII'];"
        self.assertTrue(self._redeclares_the_vocabulary(planted))

    def test_the_guard_sees_double_quoted_arrays(self) -> None:
        planted = 'const ITEMS = ["DTS", "BLE", "BT", "UNII"];'
        self.assertTrue(self._redeclares_the_vocabulary(planted))

    def test_the_guard_ignores_unrelated_arrays(self) -> None:
        """정상 형태(부분집합 · 무관한 배열)는 green — 과탐 방지."""
        benign = "const PARTIAL = ['DTS', 'BLE'];\nconst X = ['a', 'b'];"
        self.assertFalse(self._redeclares_the_vocabulary(benign))


class TestEquipmentListLocalEditIsKeyedToItsList(unittest.TestCase):
    """장비목록 편집기의 로컬 편집은 자기 목록에 묶여 있다 (2026-08-07).

    저장이 **전량 교체 PUT** 이므로, 목록 A 의 미저장 편집이 B 밑에 살아남으면
    그것은 곧 원클릭 덮어쓰기다 — 형제 봉인
    ``TestDraftPanelsAreKeyedByDraftId`` 가 test-plan 초안에 대해 막는 것과 같은
    위험이다. 그쪽은 별도 컴포넌트라 ``key={selectedDraftId}`` 로 막지만, 이
    편집기는 인라인이라 대신 **편집 자체가 목록 id 를 들고 있게** 해서 다른
    목록에 적용되는 것을 구조적으로 불가능하게 만든다.

    ``select*`` 핸들러의 수동 초기화 호출만으로는 부족하다 — 호출을 하나
    빠뜨리면 무너지는 lucky guard 이고, 그 패턴은 형제 봉인의 docstring 이
    명시적으로 거부한다.
    """

    def _source(self) -> str:
        return _strip_ts_comments(
            (SRC_DIR / "routes" / "equipment-lists.tsx").read_text(encoding="utf-8")
        )

    def test_the_local_edit_carries_its_list_id(self) -> None:
        source = self._source()
        self.assertRegex(
            source,
            r"useState<\{\s*readonly listId: string;",
            "로컬 편집이 목록 id 를 들고 있지 않다",
        )

    def test_the_derivation_compares_the_list_id(self) -> None:
        source = self._source()
        self.assertIn(
            "localEdit?.listId === selectedListId",
            source,
            "파생이 목록 id 를 대조하지 않는다 — 편집이 목록 사이를 넘을 수 있다",
        )

    def test_the_override_state_is_actually_present(self) -> None:
        """비-공허성 — ``useEffect`` 부재만 봉인하면 파생 자체를 지워도 green 이다."""
        source = self._source()
        self.assertIn("toEditableRows(detail?.items ?? [])", source)
        self.assertNotIn("useEffect", source)


# ══════════════════════════════════════════════════════════════════════════════
# 참조 데이터 워크벤치 (2026-08-08) — 워크북에서 장부로
# ══════════════════════════════════════════════════════════════════════════════
class _ReferenceDataRouteSource(unittest.TestCase):
    """공용 베이스 — 라우트 소스를 주석 제거해 읽는다."""

    ROUTE_DIR = SRC_DIR / "routes" / "reference-data"

    def route_source(self) -> str:
        return _strip_ts_comments(
            (self.ROUTE_DIR / "index.tsx").read_text(encoding="utf-8")
        )

    def diff_source(self) -> str:
        return _strip_ts_comments((self.ROUTE_DIR / "diff.ts").read_text(encoding="utf-8"))

    def test_the_route_exists(self) -> None:
        """비-공허성 — 아래 스캔들이 빈 문자열을 검사하고 있지 않다."""
        self.assertTrue((self.ROUTE_DIR / "index.tsx").is_file())
        # ⚠️ `2000` 은 그날의 **파일 크기**였다. 라우트를 정당하게 모듈로 쪼개면
        # 그 리팩터가 red 가 된다. 명제는 *"빈 문자열을 검사하고 있지 않다"* 이므로
        # 바닥은 0 이고, 내용이 정말 이 라우트인지는 **구조**로 묻는다.
        self.assertGreater(len(self.route_source()), 0)
        self.assertIn("export", self.route_source(), "라우트가 아무것도 내보내지 않는다")


class TestReferenceColumnsComeFromTheServer(_ReferenceDataRouteSource):
    """엔트리 표의 **열 순서는 서버가 소유**한다.

    payload 는 열린 매핑이라 null 필드가 생략될 수 있다. 클라이언트가 payload 키에서
    열을 파생하면 엔트리마다 열 집합이 달라져 표가 출렁이고, 6 패밀리 × N 컬럼을 TS 에
    다시 적으면 같은 순서가 두 언어로 쪼개진다 — 장비목록 표면이 이미 겪은 것과 같은
    결함 계열(`Derived-Value No-Client-Recompute SSOT`).
    """

    #: 런타임 행 필드 이름 일부 — 도메인 `PROJECTION_FIELD_CONTRACT` 가 소유한다.
    _FREQUENCY_TABLE_COLUMNS = (
        "technologies", "band", "bandwidth", "channel", "center_frequency",
    )

    def test_the_route_consumes_the_server_column_order(self) -> None:
        self.assertIn(
            "payload_columns.map", self.route_source(),
            "서버가 준 열 순서를 소비하지 않는다",
        )

    def test_the_route_does_not_redeclare_a_family_column_list(self) -> None:
        source = self.route_source()
        targets = set(self._FREQUENCY_TABLE_COLUMNS)
        found = {
            literal for literal in re.findall(r"'([a-z_]+)'", source)
        }
        self.assertFalse(
            targets <= found,
            "패밀리 컬럼 목록이 프론트에 재선언됐다 — payload_columns 를 쓰라",
        )

    def test_the_guard_is_not_vacuous(self) -> None:
        planted = (
            "const COLUMNS = ['technologies', 'band', 'bandwidth', 'channel', "
            "'center_frequency'];"
        )
        found = set(re.findall(r"'([a-z_]+)'", planted))
        self.assertTrue(set(self._FREQUENCY_TABLE_COLUMNS) <= found)


class TestReferenceCouplingVocabularyComesFromTheServer(_ReferenceDataRouteSource):
    """짝 어휘도 서버가 준다 — 적지도, 오류 문장에서 파싱하지도 않는다.

    결합 사실은 백엔드 도메인 SSOT `COUPLED_FAMILY_GROUPS` 에 있다. 프론트가 짝
    패밀리 이름을 적으면 어휘가 두 곳이 되고, 서버 거부 **메시지**에서 형제 이름을
    뽑아 쓰는 것은 사람이 읽으라고 쓴 문장을 기계가 파싱하는 결합이라 문구를 다듬는
    순간 조용히 깨진다. 그래서 상세 응답의 `coupled_with` 를 읽는다.
    """

    _COUPLED_PAIR = ("correction", "switch_port_mapping")

    def test_the_route_reads_the_server_supplied_sibling(self) -> None:
        self.assertIn("coupled_with", self.route_source())

    def test_neither_module_names_the_pair(self) -> None:
        for label, source in (
            ("index.tsx", self.route_source()),
            ("diff.ts", self.diff_source()),
        ):
            with self.subTest(module=label):
                literals = set(re.findall(r"'([a-z_]+)'", source))
                self.assertFalse(
                    set(self._COUPLED_PAIR) <= literals,
                    f"{label} 이 결합 쌍을 리터럴로 적었다 — coupled_with 를 쓰라",
                )

    def test_the_route_does_not_parse_the_refusal_message(self) -> None:
        source = self.route_source()
        for forbidden in ("error.message.includes", "message.match(", "message.split("):
            self.assertNotIn(forbidden, source, "거부 메시지를 파싱하고 있다")

    def test_the_guard_is_not_vacuous(self) -> None:
        planted = "const PAIR = ['correction', 'switch_port_mapping'];"
        literals = set(re.findall(r"'([a-z_]+)'", planted))
        self.assertTrue(set(self._COUPLED_PAIR) <= literals)


class TestReferenceDerivedValuesStayServerSide(_ReferenceDataRouteSource):
    """서버 파생값을 클라이언트가 재계산하지 않는다.

    diff 의 변경 판정은 서버 `content_sha256` **비교**뿐이고 조인 키는 서버
    `identity_key` 다. 여기서 payload 를 해싱하면 같은 규칙이 Python 과 TS 두 언어로
    쪼개지고, 그 드리프트는 시험원이 **게시한 뒤에야** 드러난다.
    """

    _HASHING = ("createHash", "sha256(", "digest(", "crypto.subtle")

    def test_no_hashing_in_the_route_or_the_diff(self) -> None:
        for label, source in (
            ("index.tsx", self.route_source()),
            ("diff.ts", self.diff_source()),
        ):
            for token in self._HASHING:
                with self.subTest(module=label, token=token):
                    self.assertNotIn(token, source)

    def test_the_diff_compares_the_server_fingerprint(self) -> None:
        """비-공허성 — 해싱이 없는 이유가 "비교조차 안 한다"가 아니다."""
        source = self.diff_source()
        self.assertIn("content_sha256", source)
        self.assertIn("identity_key", source)

    #: 서버가 준 etag 를 동시성 토큰으로 **되돌려 보내는** 유일한 형태.
    #: 조립이 아니라 읽기이므로 아래 금지 스캔에서 제외하되, 제외는 이 문자열
    #: 그대로일 때만 성립한다 — `expected_etag: <무엇이든>` 을 통째로 허용하면
    #: 다음 사람이 그 자리에 계산식을 넣어도 봉인이 침묵한다.
    _SERVER_ETAG_ECHO = "expected_etag: detail?.revision.etag ?? ''"

    def test_the_concurrency_token_is_echoed_not_assembled(self) -> None:
        """편집 저장은 etag 를 **서버 응답에서 읽어** 그대로 되돌려 보낸다.

        이 단언이 먼저 와야 아래 제외가 정당해진다. 없으면 제외는 그냥 구멍이다.
        """
        self.assertIn(self._SERVER_ETAG_ECHO, self.route_source())

    def test_no_local_state_or_etag_synthesis(self) -> None:
        # 위에서 형태를 고정한 echo 만 걷어내고 검사한다. 걷어낸 뒤에도 `etag:` 가
        # 남아 있다면 그것은 클라이언트가 태그를 **만들고 있다**는 뜻이다.
        source = self.route_source().replace(self._SERVER_ETAG_ECHO, '')
        for forbidden in ("state: 'PUBLISHED'", "etag:", "revision_number:"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_echo_exemption_does_not_hide_a_synthesised_tag(self) -> None:
        """비-공허성 — 제외가 다른 `etag:` 를 함께 가리지 않는다."""
        offender = self.route_source().replace(
            self._SERVER_ETAG_ECHO,
            "expected_etag: `${detail?.revision.version}`, etag: computeEtag()",
        )
        self.assertIn('etag:', offender.replace(self._SERVER_ETAG_ECHO, ''))


class TestReferencePublishIsNotOptimistic(_ReferenceDataRouteSource):
    """게시는 낙관적이지 않다 — 그것이 곧 파생값 재계산이기 때문이다.

    게시의 가시적 결과(`state`/`published_at`/`etag`/`version`)는 전부 서버가 만든다.
    낙관적으로 뒤집으려면 클라이언트가 그 값들을 계산해야 하고, 그 순간 규칙이 두
    곳으로 쪼개진다. 성공하면 무효화만 한다.
    """

    def test_no_direct_cache_write(self) -> None:
        self.assertNotIn("setQueryData", self.route_source())

    def test_success_invalidates(self) -> None:
        """비-공허성 — 낙관적이지 않다는 것이 "아무것도 안 한다"가 아니다."""
        self.assertIn("invalidateQueries", self.route_source())


class TestReferenceCoupledPublishIsOneRequest(_ReferenceDataRouteSource):
    """화면에서 결합 그룹의 반쪽 게시가 **구성상 불가능**하다.

    형제 후보를 고르기 전에는 제출 버튼이 비활성이고, 제출은 한 요청에 두 id 를
    싣는다. 서버도 같은 규칙을 집행하지만(경계가 최종 권위), 화면이 미리 말해주지
    못하면 시험원은 409 를 만나고서야 무엇이 필요한지 알게 된다.
    """

    def test_submission_requires_the_sibling(self) -> None:
        source = self.route_source()
        self.assertIn("canSubmit", source)
        self.assertIn("siblingId.length > 0", source)
        self.assertIn("disabled={!canSubmit", source)

    def test_the_pair_travels_in_one_request(self) -> None:
        source = self.route_source()
        self.assertIn("coupled_revision_id: siblingId", source)
        self.assertEqual(
            1, source.count("publishReferenceRevision("),
            "게시 호출이 둘 이상이다 — 두 요청은 두 커밋 경계이고, 결합이 막으려던 "
            "바로 그 상태를 결합을 지키는 척하며 만든다.",
        )


class TestReferenceRouteDoesNotGateOnTokens(_ReferenceDataRouteSource):
    """권한 토큰으로 버튼을 잠그지 않는다.

    백엔드 `authorize` 는 토큰 ∪ 프로젝트 멤버십 UNION 이다. 토큰 미보유를 근거로
    비활성화하면 멤버십으로 권한을 받은 시험원이 부당하게 차단된다 — 그리고 그
    차단은 백엔드가 허용했을 요청이라 아무 로그에도 남지 않는다.
    """

    def test_no_permission_literal(self) -> None:
        source = self.route_source()
        self.assertNotIn("platform:", source)

    def test_no_permission_based_disable(self) -> None:
        source = self.route_source()
        for forbidden in ("hasPermission", "PERMISSION_PLATFORM_REFERENCE_WRITE"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, source)

    def test_the_route_surfaces_a_forbidden_response_instead(self) -> None:
        """비-공허성 — 잠그지 않는 대신 서버 거부를 보여준다."""
        self.assertIn("forbidden", self.route_source())


class TestCodeRefinedCopyHasOneTable(unittest.TestCase):
    """`describeApiError` 의 code 정련 룩업은 **하나**다 (2026-09-01).

    이 검사가 있는 이유는 늘어나는 방식이 조용하기 때문이다. 404/422/503 팔은
    각각 자기 `*_KEY_BY_CODE` 모듈 상수를 갖고 그 아래 세 줄(코드 추출 → 룩업 →
    삼항)을 **글자 그대로** 다시 적고 있었다. 넷째·다섯째(413/415)를 같은 모양으로
    더하는 것은 리뷰에서 자연스러워 보이고, 그렇게 다섯 사본이 된다.

    ⚠️ **접힌 것은 룩업이지 폴백이 아니다.** 팔마다 폴백 override 키와 일반 문구가
    다르므로(404=`notFound`/`errors.notFound`, 422=`unprocessable`/
    `errors.unprocessable`, 503=`serviceUnavailable`/`errors.default`) 그것까지
    접으면 열 개 넘는 라우트의 문구가 바뀐다. 우선순위·폴백의 **행동** 판정은
    `apps/web/tests/api-error-code-precedence.test.ts` 가 소유한다 — 이 검사는
    사본이 다시 생기는 것만 본다.
    """

    ERRORS_TS = SRC_DIR / "ui" / "errors.ts"

    def _source(self) -> str:
        self.assertTrue(self.ERRORS_TS.exists(), f"missing {self.ERRORS_TS}")
        return self.ERRORS_TS.read_text(encoding="utf-8")

    #: 이 검사가 잡아야 하는 **여섯 번째 사본**의 철자들. 목록의 값어치는 길이가
    #: 아니라 *이름 축이 답이 아니라는 것* 을 실행으로 보이는 데 있다 — 넷 중
    #: 어느 것도 `KEY_BY_CODE` 라고 적혀 있지 않고, 옛 봉인은 넷 다 통과시켰다.
    SMUGGLED_COPY_SPELLINGS = {
        "module const": "\nconst CONFLICT_REFINEMENTS = { SOME_CODE: 'errors.someCode' };\n",
        "let": "\nlet CONFLICT_REFINEMENTS = { SOME_CODE: 'errors.someCode' };\n",
        "var": "\nvar CONFLICT_REFINEMENTS = { SOME_CODE: 'errors.someCode' };\n",
        "function-local": "\nfunction y() { const M = { B_CODE: 'errors.b' }; return M; }\n",
        "anonymous inline": "\nfunction z(c: string) { return { A_CODE: 'errors.a' }[c]; }\n",
        "quoted key": "\nconst X = { 'SOME_CODE': 'errors.someCode' };\n",
        "backtick copy key": "\nconst X = { SOME_CODE: `errors.someCode` };\n",
        "ternary value": "\nconst X = { SOME_CODE: f ? 'errors.a' : 'errors.b' };\n",
    }

    def test_every_code_refined_lookup_lives_in_one_container(self):
        """명제 — *이름* 이 아니라 **형태와 위치** 를 묻는다.

        ⚠️ 이 자리는 ``\\b(?:const|let|var)\\s+([A-Z0-9_]*KEY_BY_CODE)\\b`` 였고,
        그것은 **철자에 대한 질문**이다. 독립 적대 평가가 409 팔에
        ``const CONFLICT_REFINEMENTS = {…}`` 를 심어 전량 green 을 받아냈다 —
        이름에 ``KEY_BY_CODE`` 가 없을 뿐, 이 클래스 독스트링이 막겠다는 여섯 번째
        사본이다. 개명은 리뷰가 가장 자연스럽게 통과시키는 변경이라, 이름을 묻는
        규칙은 **가장 싼 방법으로** 무력해진다.

        묻는 명제: *SCREAMING_SNAKE 키를 운영 문구 키(``'errors.…'``)에 대응시키는
        객체 리터럴* 은 전부 **하나의 감싸는 리터럴 안**에 있어야 하고, 그 감싸는
        리터럴의 키는 **HTTP status** 여야 한다. 어느 식별자도 등장하지 않는다.
        """
        source = self._source()
        census = census_copy_tables(source)
        self.assertEqual(
            [source[lit.start : lit.start + 60] for lit in census.unreadable],
            [],
            "운영 문구로 가는 표인데 키를 읽을 수 없다 — 읽을 수 없는 것을 '표가 "
            "아니다'로 셀 수는 없다. 계산 키를 상수 이름으로 펴라",
        )
        self.assertGreater(
            len(census.code_keyed),
            0,
            "code 정련 표를 하나도 찾지 못했다 — 아래 단언이 공허해진다",
        )
        container = enclosing_container(source, census.code_keyed)
        self.assertIsNotNone(
            container,
            "code 정련 룩업이 둘 이상으로 흩어졌다(공통으로 감싸는 리터럴이 없다) — "
            "새 status 는 하나의 status-키 표에 항목으로 연다. 발견된 표: "
            f"{[source[lit.start:lit.start + 60] for lit in census.code_keyed]}",
        )
        assert container is not None  # for type-checkers; assertIsNotNone 이 게이트
        keys = status_keys(container)
        # ⚠️ 비-공허성이 먼저다. `iter_ts_object_literals` 는 객체 리터럴과 **문 블록**을
        # 구분하지 않으므로 *함수 본문* 도 후보 컨테이너이고, 함수 본문의 키 집합은
        # 공집합이라 아래 음성 단언이 **공허하게 참**이 된다 — 한 함수 안에 나란히 둔
        # 사본 둘이 그 상태로 통과한다(독립 적대 평가 실측). 컨테이너는 status 로
        # 열리는 표여야 하고, 그 사실 자체가 단언 대상이다.
        self.assertGreater(
            len(keys),
            0,
            "정련 표를 감싸는 것이 키 없는 블록(=함수 본문)이다 — 두 사본을 한 함수 "
            "안에 나란히 두면 이 검사가 공허하게 참이 된다",
        )
        non_status = sorted(key for key in keys if not key.isdigit())
        self.assertEqual(
            non_status,
            [],
            f"정련 표를 감싸는 리터럴이 status 가 아닌 키로도 열린다: {non_status} — "
            "그 키 아래의 표는 status 로 도달할 수 없는 두 번째 룩업이다",
        )

    def test_every_table_is_a_direct_value_of_a_status_key(self):
        """*"컨테이너 안"* 이 아니라 **status 키의 직속 값**이 명제다.

        ⚠️ 독립 적대 평가가 두 번째 표를 기존 팔 **한 칸 아래**에 넣었다 —
        ``422: { legacy: { CLAIM_CONFLICT: … } }``. 컨테이너는 그대로고 키도 전부
        숫자이며 팔↔표 대응도 성립해 전량 green 이었다. 라운드 1 의 ``'418': {`` 와
        같은 형태가 한 층 내려간 것이고, 결과는 같다 — ``[422][code]`` 가 **객체**를
        돌려주므로 그 문구는 영원히 도달 불가다.
        """
        source = self._source()
        census = census_copy_tables(source)
        container = enclosing_container(source, census.code_keyed)
        self.assertIsNotNone(container, "컨테이너를 찾지 못했다 — 이 단언이 공허해진다")
        assert container is not None
        stray = tables_not_directly_under(source, container, census.code_keyed)
        self.assertEqual(
            [source[table.start : table.start + 60] for table in stray],
            [],
            "code 정련 표가 status 키의 직속 값이 아니다 — 그 자리는 status 로 도달할 수 "
            "없는 죽은 문구다",
        )

    def test_a_table_nested_one_level_deeper_is_caught(self):
        """비-공허성 — 평가자가 심은 그 한 칸을 실제로 잡는가."""
        source = self._source()
        mutated = source.replace(
            "  422: {", "  422: {\n    legacy: { CLAIM_CONFLICT: 'errors.claimConflict' },", 1
        )
        self.assertNotEqual(mutated, source, "반례가 적용되지 않았다 — 앵커가 밀렸다")
        census = census_copy_tables(mutated)
        container = enclosing_container(mutated, census.code_keyed)
        self.assertIsNotNone(container)
        assert container is not None
        self.assertEqual(
            len(tables_not_directly_under(mutated, container, census.code_keyed)),
            1,
            "한 칸 아래에 심은 두 번째 표가 통과한다",
        )

    def test_the_one_lookup_cannot_be_switched_off_at_build_time(self):
        """*"두 번째 룩업이 없다"* 와 *"그 하나가 도달된다"* 는 다른 명제다.

        ⚠️ 이 웨이브의 모든 축이 앞의 것만 물었다. 독립 평가가 공용 헬퍼 첫 줄에
        ``if (!import.meta.env.DEV) return undefined;`` 한 줄을 넣어 **배포 번들에서
        정련 팔 전부를 죽이고** 전량 초록을 받아냈다 — `tsc` 0 이고, vitest 는 DEV 라
        행동 축조차 통과한다. 즉 *실제로 배포되는 문구*에 대해 아무도 묻지 않았다.

        운영 문구가 **빌드 모드에 따라 달라지는 것**은 그 자체로 결함이다(시험원이 보는
        문장이 개발자가 본 문장과 다르다). 그리고 그 사실은 이 파일에 대해 관측 가능하다
        — 오늘 실측 **0건**.
        """
        source = _strip_ts_comments(self.ERRORS_TS.read_text(encoding="utf-8"))
        masked = mask_ts_noncode(source)
        anchor = "function refinedKeyForCode"
        at = masked.find(anchor)
        self.assertGreater(at, -1, "공용 정련 헬퍼를 찾지 못했다 — 이 단언이 공허해진다")
        brace = masked.find("{", at)
        close = match_brackets(masked).get(brace)
        self.assertIsNotNone(close, "헬퍼 본문이 균형을 잃었다")
        assert close is not None
        body = source[brace:close]
        # ⚠️ 앞 판은 이 파일에서 `import.meta.env` 를 grep 했다. **그것은 철자다** —
        # 플래그를 다른 모듈에 두고 import 하면 그대로 지나간다(독립 평가 실측:
        # `shared/copy-flags.ts` 하나로 배포 번들의 정련 팔 전부가 죽고 전량 초록).
        # 묻는 명제는 플래그가 어디서 오는가가 아니라 **헬퍼가 조건부인가** 다:
        # 이 함수의 유일한 분기는 *코드가 없다* 이고, 유일한 조기 반환은 그 분기의 것이다.
        conditions = re.findall(r"\bif\s*\(([^)]*)\)", body)
        self.assertEqual(
            [condition.strip() for condition in conditions],
            ["code === undefined"],
            "정련 헬퍼가 *코드 부재* 말고 다른 것에 분기한다 — 그 조건이 거짓인 빌드에서 "
            f"정련 팔 전부가 죽고, 그 사실을 아무도 보지 못한다: {conditions}",
        )
        self.assertEqual(
            len(re.findall(r"\breturn\b", body)),
            2,
            "정련 헬퍼의 반환 자리가 둘이 아니다 — 셋째 반환은 표에 닿기 전에 빠져나가는 길이다",
        )

    def test_no_copy_lookup_is_written_by_assignment(self):
        """표가 아니라 **대입으로** 쓰는 룩업도 룩업이다.

        ⚠️ ``S['DRAFT' + '_EMPTY'] = 'errors.draftEmpty'`` 는 발행된 코드를
        **한 토큰으로 적지 않고** 부른다 — 어휘 축이 원리적으로 못 본다. 키의 철자를
        묻는 대신 **대입의 모양**을 물으면 그 철자가 무관해진다.
        """
        # 트리 전역이다 — 형제 두 축이 이미 그렇고, 이 축만 한 파일이라 세 구멍이
        # 합성됐다(새 모듈 + 결합 키 + `??=`).
        self.assertEqual(
            modules_with_copy_assignments(SRC_DIR),
            {},
            "계산 인덱스에 운영 문구를 대입하는 자리가 있다 — 표 밖의 두 번째 룩업이다",
        )

    def test_the_assignment_detector_is_not_vacuous(self):
        source = self._source()
        for label, operator in (("plain", "="), ("nullish", "??="), ("logical or", "||=")):
            with self.subTest(label):
                smuggled = (
                    source
                    + "\nconst S: Record<string,string> = {};\n"
                    + f"S['DRAFT' + '_EMPTY'] {operator} 'errors.draftEmpty';\n"
                )
                self.assertEqual(
                    len(copy_assignment_sites(smuggled)),
                    1,
                    f"{operator} 로 적은 대입을 잡지 못한다 — 한 글자가 축을 통째로 연다",
                )
        innocent = source + "\nconst N: Record<string,number> = {};\nN['x'] = 1;\n"
        self.assertEqual(copy_assignment_sites(innocent), (), "운영 문구가 아닌 대입을 오탐한다")

    def test_every_refined_copy_key_resolves_in_both_locales(self):
        """형제 라우트 축이 갖는 그 봉인을 문구 축도 갖는다.

        ⚠️ **이 봉인이 없었다.** 라우트 축은 정확히 같은 질문을 하고 사유까지 적는데
        문구 축에는 아무것도 없어서, 어느 로케일에도 없는 키를 표에 더하면 화면에
        ``errors.claimConflict`` 가 그대로 나온다 — 증상 ①과 **같은 증상**이 다른 자리에서.
        `tsc` 는 여기서도 의견이 없다.
        """
        source = self._source()
        # ⚠️ **One value, one parser.** This derived keys with its own
        # `strip().strip("'\"`")` + `startswith` — a THIRD reading of bytes the
        # module already has a rule for, and it disagreed: a ternary contributed
        # zero keys AND removed the good one from the checked set, so a key
        # resolving in neither locale shipped with the lane green.
        keys = sorted(
            {
                key
                for table in census_copy_tables(source).code_keyed
                for key in refined_copy_keys(table)
            }
        )
        self.assertGreater(len(keys), 0, "정련 표에서 문구 키를 하나도 읽지 못했다")
        for locale in ("ko", "en"):
            messages = _flatten_messages(
                json.loads((SRC_DIR / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
            )
            missing = [key for key in keys if key not in messages]
            self.assertEqual(
                missing,
                [],
                f"{locale}.json 에 정련 문구 키가 없다: {missing} — 그 코드가 오면 화면에 "
                "점 표기 키가 그대로 노출된다",
            )
            blank = [key for key in keys if key in messages and not messages[key].strip()]
            self.assertEqual(blank, [], f"{locale}.json 의 정련 문구가 빈 문자열이다: {blank}")

    def test_the_rule_is_not_satisfied_by_a_rename(self):
        """비-공허성 겸 **회귀 방향의 명시** — 옛 규칙이 놓친 넷이 red 인가.

        실제 소스에 대한 PASS 만 있으면 *"위반이 없다"* 와 *"검출기가 아무것도
        매칭하지 못한다"* 가 구분되지 않는다. 반례를 이 테스트가 소유한 문자열로
        심어 검출기를 양방향으로 시험한다.
        """
        source = self._source()
        for label, smuggled in sorted(self.SMUGGLED_COPY_SPELLINGS.items()):
            with self.subTest(label):
                mutated = source + smuggled
                census = census_copy_tables(mutated)
                self.assertGreater(
                    len(census.code_keyed),
                    len(census_copy_tables(source).code_keyed),
                    "밀수된 사본이 센서스에 들어오지 않는다",
                )
                self.assertIsNone(
                    enclosing_container(mutated, census.code_keyed),
                    f"{label} 로 적은 두 번째 룩업이 통과한다 — 규칙이 여전히 이름을 묻는다",
                )

    def test_a_non_code_keyed_copy_map_is_not_an_offender(self):
        """오탐 축 — 형제 맵을 위반으로 읽으면 사람들이 형제 맵을 지운다.

        ``FORBIDDEN_KEY_BY_CONTEXT`` 는 값이 ``'errors.*'`` 이지만 키가 **surface
        이름**이라 code 정련이 아니다. 위 명제가 그것을 잡으면 이 규칙은 오탐
        생성기이고, 오탐을 내는 게이트는 삭제된다.
        """
        source = self._source()
        with_sibling = source + "\nconst ICON_BY_CONTEXT = { platform: 'errors.forbidden' };\n"
        self.assertEqual(
            len(census_copy_tables(with_sibling).code_keyed),
            len(census_copy_tables(source).code_keyed),
            "surface 로 키잉된 문구 맵이 code 정련 표로 오인된다",
        )

    #: 객체 리터럴이 **아닌** 두 번째 룩업들. 독립 적대 평가가 셋 다 실제
    #: `errors.ts` 에 착지시키고 전량 green 을 받아냈다 — 앞 판의 명제가
    #: *"객체 리터럴이 어떻게 생겼는가"* 였기 때문이다.
    SMUGGLED_NON_LITERAL_LOOKUPS = {
        "new Map": (
            "\nconst C = new Map<string,string>("
            "[['WORKBOOK_HANDLE_NOT_FOUND','errors.workbookHandleNotFound']]);\n"
        ),
        "switch": (
            "\nexport function k(c: string) { switch (c) { "
            "case 'DRAFT_EMPTY': return 'errors.draftEmpty'; default: return undefined; } }\n"
        ),
        "if chain": (
            "\nexport function k2(c: string) { "
            "if (c === 'DRAFT_EMPTY') return 'errors.draftEmpty'; return undefined; }\n"
        ),
        "array of pairs": (
            "\nconst P = [['SESSION_RESULTS_EMPTY', 'errors.sessionResultsEmpty']];\n"
        ),
        "bare identifier case": (
            "\nexport function k3(c: unknown) { return c === DRAFT_EMPTY ? 'errors.draftEmpty' : undefined; }\n"
        ),
    }

    #: code→문구 표를 가진 모듈. ⚠️ **개수가 아니라 이름**이다 — 개수는 오늘의 배치를
    #: 굳히지만 이름은 *어느 모듈이 사라졌는가* 를 답한다. 그리고 이 집합은 손으로 적는
    #: 유일한 것이고, 그래야 하는 이유가 있다: 센서스가 조용히 줄어드는 것을 잡으려면
    #: 센서스 자신에서 파생할 수 없다.
    CODE_COPY_MODULES = ("routes/reports.tsx", "ui/errors.ts")

    def _locale_keys(self, locale: str) -> "frozenset[str]":
        return frozenset(
            _flatten_messages(
                json.loads((SRC_DIR / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
            )
        )

    def test_every_code_to_copy_table_in_the_frontend_is_healthy(self):
        """네임스페이스가 아니라 **계약**으로 묻는다 — 그리고 이 축이 내 주장을 정정했다.

        ⚠️ 두 트리 전역 축은 운영 문구를 ``'errors.'`` **접두사**로 알아봤다. 그것은
        이름이고, 그래서 라우트 자기 i18n 네임스페이스 아래의 code→문구 표는 세 축
        모두에게 보이지 않았다 — 이 웨이브가 없애는 그 패배가 한 층 아래에서 반복된
        것이다. 독립 평가가 발행 코드 셋을 쓰는 표를 라우트에 심자 두 센서스가 여전히
        ``['ui/errors.ts']`` 라고 답했고, 면제의 전부는 ``grep -c "'errors." → 0`` 이었다.

        ⚠️ **그리고 그 측정이 이 웨이브의 주장을 고쳤다.** *"프론트 전체에 code→문구
        룩업이 정확히 하나"* 는 **실 트리에서 거짓**이었다 — ``routes/reports.tsx`` 는
        2026-07-28 부터 그런 표를 갖고 있고, 그 파일의 주석이 근거를 적는다(코드-우선
        첫 단 뒤에 ``describeApiError`` 로 위임하므로 *"두 번째 사다리가 아니라 한
        사다리의 더 날카로운 첫 단"*). 네임스페이스 제한은 그것을 **허용한 것이 아니라
        가리고 있었다.**

        그러므로 축을 둘로 나눈다. **모든** 표가 만족해야 하는 것은 *건강*(발행된 코드,
        양 로케일에서 해소되는 문구)이고, **분류 체계의** 표만 만족해야 하는 것이 *접힘*
        이다(아래 형제 검사).
        """
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        tables = tree_code_copy_tables(SRC_DIR, vocabulary)
        copy_tables = [t for t in tables if t.copy_keys or t.unreadable]
        # ⚠️ 비-공허성 anchor 가 `> 0` 이면 안 된다. 독립 평가가 실측했다 — 평범한
        # "문구 키를 상수 파일로 빼기" 리팩터가 센서스를 **6표 2모듈 → 5표 1모듈**로
        # 줄였는데 `> 0` 은 아무 말도 하지 않았다. 오늘의 개수를 적는 것도 답이 아니다
        # (그것이 이 웨이브가 상환하는 결함이다). 그래서 **모듈 집합**을 묻는다 —
        # 표를 가진 모듈이 사라지면 그 이름이 red 다.
        self.assertEqual(
            sorted({t.module for t in copy_tables}),
            sorted(self.CODE_COPY_MODULES),
            "code→문구 표를 가진 모듈 집합이 달라졌다 — 센서스가 조용히 줄어드는 것이 "
            "이 축이 막는 결함이다",
        )
        unreadable = sorted({f"{t.module}:{u}" for t in tables for u in t.unreadable})
        self.assertEqual(
            unreadable,
            [],
            "발행된 코드에 걸린 값을 읽을 수 없다 — 읽을 수 없는 것을 '문구가 아니다'로 "
            f"셀 수는 없다: {unreadable}",
        )
        for locale in ("ko", "en"):
            keys = self._locale_keys(locale)
            missing = sorted(
                {
                    f"{table.module}:{key}"
                    for table in copy_tables
                    for key in table.copy_keys
                    if key not in keys
                }
            )
            self.assertEqual(
                missing,
                [],
                f"{locale}.json 에서 해소되지 않는 문구 키가 code→문구 표에 있다: {missing}",
            )
        unpublished = sorted(
            {f"{t.module}:{c}" for t in tables for c in t.codes if c not in vocabulary}
        )
        self.assertEqual(unpublished, [], f"백엔드가 발행하지 않는 코드에 문구가 걸려 있다: {unpublished}")

    def test_a_code_to_copy_table_under_another_namespace_is_seen(self):
        """비-공허성 — 평가자가 심은 그 형태(라우트 네임스페이스)를 잡는가."""
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        planted = (
            "const CREATE_HINT_BY_CODE = {\n"
            "  DRAFT_EMPTY: 'routes.myProjects.title',\n"
            "  NOT_FOUND: 'routes.myProjects.title',\n"
            "};\n"
        )
        with _copied_src_tree(**{"routes/planted-hint.ts": planted}) as root:
            modules = {t.module for t in tree_code_copy_tables(root, vocabulary) if t.copy_keys or t.unreadable}
        self.assertIn(
            "routes/planted-hint.ts",
            modules,
            "다른 네임스페이스의 code→문구 표가 트리 축에 잡히지 않는다",
        )

    def test_the_refinement_table_is_the_only_one_in_the_whole_frontend(self):
        """세 번째 축 — **범위**. 앞의 두 축은 파일 하나만 읽었다.

        ⚠️ 독립 적대 평가가 정확히 그 구멍으로 걸어 들어갔다: 두 번째 룩업을 **새
        모듈**(`ui/error-refinements.ts`)에 두고 409 팔에 배선하자 전량 green 이었다.
        같은 저장소의 라우트 봉인이 뚫린 방식과 **글자 그대로 같은 수법**이 한 축 옆에서
        되풀이된 것이다. "그" 룩업에 대한 규칙은 **옮겨 간 룩업을 볼 수 있어야** 한다.

        ⚠️ 정본 모듈은 **파생**이다 — 표를 가진 유일한 모듈. 이름을 적으면 규칙의
        주어가 파일을 옮기는 사람 손에 들어가고, 이 웨이브는 *이름을 묻는 규칙은
        개명으로 만족된다*는 이유로 존재한다.
        """
        censuses = scan_tree_for_copy_tables(SRC_DIR)
        self.assertNotEqual(
            censuses,
            {},
            "프론트 전역에서 code 정련 표를 하나도 찾지 못했다 — 이 축이 공허하다",
        )
        self.assertEqual(
            sorted(censuses),
            [self.ERRORS_TS.relative_to(SRC_DIR).as_posix()],
            f"code 정련 표를 가진 모듈이 하나가 아니다: {sorted(censuses)} — 두 번째 "
            "룩업은 새 이름이 아니라 새 파일로도 생긴다",
        )

    def test_no_other_module_names_a_code_beside_operator_copy(self):
        """어휘 축도 트리 전역이다 — 단, *조치* 를 위해 code 를 비교하는 화면은 대상이 아니다.

        라우트가 `err.code === 'DRAFT_EMPTY'` 로 재시도 버튼을 낼지 정하는 것은 문구
        룩업이 아니다. 위반은 **운영 문구를 생산하는 같은 모듈 안에서** code 를
        이름으로 부르는 것이고, 그것이 두 번째 정련 표가 하는 일이다.
        """
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        offenders = modules_naming_codes_beside_copy(SRC_DIR, vocabulary)
        self.assertNotEqual(offenders, {}, "정본 모듈조차 찾지 못했다 — 이 축이 공허하다")
        self.assertEqual(
            sorted(offenders),
            [self.ERRORS_TS.relative_to(SRC_DIR).as_posix()],
            f"운영 문구를 내면서 ErrorCode 를 이름으로 부르는 모듈이 둘 이상이다: "
            f"{ {k: v for k, v in offenders.items()} }",
        )

    def test_a_lookup_moved_to_another_module_is_caught(self):
        """비-공허성 — 평가자가 실제로 심은 그 형태(새 모듈)를 잡는가."""
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        planted = (
            "import { t } from '@/i18n';\n"
            "const CONFLICT_REFINEMENTS = { WORKBOOK_HANDLE_NOT_FOUND: 'errors.workbookHandleNotFound' };\n"
            "export function conflictCopy(code: string): string | undefined {\n"
            "  return CONFLICT_REFINEMENTS[code];\n"
            "}\n"
        )
        with _copied_src_tree(**{"ui/error-refinements.ts": planted}) as root:
            censuses = scan_tree_for_copy_tables(root)
            offenders = modules_naming_codes_beside_copy(root, vocabulary)
        self.assertIn(
            "ui/error-refinements.ts",
            censuses,
            "다른 모듈에 심은 정련 표가 트리 스캔에 잡히지 않는다",
        )
        self.assertIn(
            "ui/error-refinements.ts",
            offenders,
            "다른 모듈이 운영 문구 옆에서 code 를 부르는데 어휘 축이 침묵한다",
        )

    def test_no_published_error_code_is_named_outside_the_one_container(self):
        """구조가 아니라 **어휘 발생**을 묻는 두 번째 축.

        ⚠️ 위 명제는 *"객체 리터럴이 어떻게 생겼는가"* 이고, 그것은 여전히 표기법에
        대한 질문이다. 독립 적대 평가가 `new Map([...])` · `switch` · if-chain 을 실제
        `errors.ts` 에 착지시키고 전량 green 을 받아냈다. 구문을 열거해 막으면 다음
        평가자가 네 번째 구문을 쓴다.

        총체적 질문은 구문을 언급하지 않는다: **code 로 문구를 정련하려면 그 code 를
        이름으로 불러야 하고**, 부를 수 있는 이름의 집합은 백엔드가 발행한다. 그러므로
        발행된 code 의 **모든 출현**은 — 어디에, 어떻게 적혔든 — 그 하나의 컨테이너
        안에 있어야 한다.
        """
        source = self._source()
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        container = enclosing_container(source, census_copy_tables(source).code_keyed)
        self.assertIsNotNone(container, "컨테이너를 찾지 못했다 — 아래 단언이 공허해진다")
        assert container is not None
        sites = code_token_sites(source, vocabulary)
        self.assertGreater(len(sites), 0, "발행된 code 출현을 하나도 찾지 못했다")
        outside = sorted(
            {code for code, at in sites if not (container.start <= at < container.end)}
        )
        self.assertEqual(
            outside,
            [],
            f"발행된 ErrorCode 가 정련 컨테이너 밖에서 이름으로 불린다: {outside} — "
            "그 자리가 두 번째 code→문구 룩업이다(객체 리터럴이 아니어도 마찬가지)",
        )

    def test_a_non_literal_second_lookup_is_still_caught(self):
        """비-공허성 — 객체 리터럴이 아닌 다섯 형태가 실제로 red 인가."""
        source = self._source()
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        for label, smuggled in sorted(self.SMUGGLED_NON_LITERAL_LOOKUPS.items()):
            with self.subTest(label):
                mutated = source + smuggled
                container = enclosing_container(
                    mutated, census_copy_tables(mutated).code_keyed
                )
                outside = [
                    code
                    for code, at in code_token_sites(mutated, vocabulary)
                    if container is None or not (container.start <= at < container.end)
                ]
                self.assertNotEqual(
                    outside,
                    [],
                    f"{label} 로 적은 두 번째 룩업이 통과한다 — 명제가 여전히 표기법을 묻는다",
                )

    def test_every_refined_code_is_one_the_backend_publishes(self):
        """어휘는 **감사 대상 파일 바깥**에서 온다.

        ⚠️ 파생은 *무엇에서* 파생하는지가 전부다. 코드 목록을 이 테스트나
        ``errors.ts`` 안에 두면 저자가 편집할 수 있는 집합이 되고, 이 저장소는
        그 방식으로 이미 뚫린 적이 있다. 계약 아티팩트는 백엔드 ``ErrorCode``
        enum 에서 재생성되므로, 백엔드가 없앤 코드에 대한 항목은 **죽은 문구**로
        여기서 이름을 갖는다.
        """
        source = self._source()
        vocabulary = error_code_vocabulary(PROJECT_ROOT / "docs" / "api")
        self.assertGreater(len(vocabulary), 0, "ErrorCode 어휘가 비었다 — 단언이 공허하다")
        refined = {
            code
            for table in census_copy_tables(source).code_keyed
            for code in screaming_snake_keys(table)
        }
        self.assertGreater(len(refined), 0, "정련 표에서 코드를 하나도 읽지 못했다")
        unknown = sorted(refined - vocabulary)
        self.assertEqual(
            unknown,
            [],
            f"백엔드가 발행하지 않는 code 에 문구가 걸려 있다: {unknown} — 그 팔은 "
            "도달 불가이고, 화면은 그 사실을 말해 줄 수 없다",
        )

    def test_every_arm_asks_the_one_helper(self):
        source = self._source()
        # 손수 다시 적은 룩업(테이블 직접 인덱싱)이 남아 있으면 사본은 사라졌는데
        # 중복은 남은 상태다.
        #
        # ⚠️ 이 자리는 `extractCode\(error\)[\s\S]{0,120}?KEY_BY_CODE\[` 였다.
        # 익명 `120` 은 *그날 두 줄 사이의 거리* 였고, 그 창이 하는 일은 121자
        # 떨어진 같은 결함을 **조용히 놓치는** 것뿐이었다. 명제를 다시 적으면
        # 창이 필요 없다: 이 테이블을 인덱싱하는 자리는 **공용 헬퍼 안에만** 있다.
        # (주석은 벗긴다 — 규칙을 설명하는 문장이 위반으로 읽히면 사람들이 설명을
        # 지운다. 이 클래스의 형제 검사가 이미 같은 이유로 선언만 센다.)
        stripped = _strip_ts_comments(source)
        helper = re.search(r"function refinedKeyForCode[\s\S]*?\n\}", stripped)
        self.assertIsNotNone(helper, "공용 헬퍼를 찾지 못했다 — 아래 단언이 공허해진다")
        # ⚠️ 이 술어는 한 번 `KEY_BY_(CODE|STATUS)` 로 넓혔다가 되돌렸다. 넓힘이
        # 산 것은 **오탐뿐**이었다 — 독립 적대 평가가 정련과 무관한
        # `ICON_KEY_BY_STATUS[418]` 을 심자 *"refinedKeyForCode 를 쓸 것"* 이라는
        # 뜻이 통하지 않는 진단이 나왔고, 넓힘이 잡겠다던 `*_KEY_BY_CODE` 사본은
        # 형제 검사(선언 축)가 이미 답한다. 이름을 **그 테이블 하나**로 고정한다.
        lookups = [
            m.start()
            for m in re.finditer(r"CODE_REFINED_KEY_BY_STATUS\s*\[", stripped)
        ]
        self.assertGreater(lookups, [], "테이블 인덱싱 자리를 하나도 찾지 못했다")
        outside = [
            stripped[max(0, at - 60) : at + 40]
            for at in lookups
            if not (helper.start() <= at < helper.end())
        ]
        self.assertEqual(
            outside,
            [],
            "정련 룩업을 공용 헬퍼 밖에서 손수 다시 적은 자리가 있다 — "
            f"refinedKeyForCode 를 쓸 것: {outside}",
        )
        # ⚠️ `>= 5` 는 *"다섯"* 이라는 그날의 테이블 크기였고 두 방향 모두 틀렸다:
        # 항목이 정당하게 하나 줄면 red 이고, 여섯 번째 status 가 테이블에만 생기고
        # 팔이 없으면 5 는 그대로라 green 이다. 명제는 **개수가 아니라 대응** —
        # 테이블이 아는 status 집합과 헬퍼를 지나는 팔의 status 집합이 같아야 한다.
        #
        # ⚠️ 테이블 쪽은 `re.findall(r"\n  (\d{3}):\s*\{", …)` 였다 — **들여쓰기와
        # 인용에 민감한 정규식**이고, 그것을 이 클래스가 *철자를 묻지 않겠다고* 선언한
        # 바로 그 커밋에서 유지했다. 독립 적대 평가가 `'418': {` 한 줄로 그것을
        # 증명했다: 어느 팔도 `refinedKeyForCode(418` 을 부르지 않는 **영구 도달 불가
        # 사본**이 전량 green 이었다. status 키는 이제 파서가 답한다(`404` 와 `'404'`
        # 를 같게 읽는 그 파서를 같은 커밋이 이미 싣고 있었다).
        container = enclosing_container(source, census_copy_tables(source).code_keyed)
        self.assertIsNotNone(container, "정련 테이블을 감싸는 리터럴을 찾지 못했다")
        assert container is not None  # for type-checkers
        tabled = set(status_keys(container))
        armed = set(re.findall(r"refinedKeyForCode\(\s*(\d+)", source))
        self.assertGreater(tabled, set(), "테이블에서 status 키를 파싱하지 못했다")
        self.assertEqual(
            armed,
            tabled,
            "정련 테이블의 status 집합과 공용 헬퍼를 지나는 팔의 집합이 다르다 — "
            f"테이블에만: {sorted(tabled - armed)}, 팔에만: {sorted(armed - tabled)}",
        )

    def test_the_upload_ceiling_is_never_spelled_in_the_frontend(self):
        """상한은 노드별 배포 설정이라 프론트가 그 숫자를 알 수 없다.

        `Derived-Value No-Client-Recompute SSOT`. 값은 413 의 RFC 9457
        `params.max` 로 서버가 준다.
        """
        # ⚠️ 주석은 대상이 아니다. `byte-size.ts` 는 *왜* 이진 단위를 쓰는지
        # 설명하며 백엔드 기본값을 인용하는데, 설명을 위반으로 읽으면 사람들이
        # 설명을 지운다(이 저장소가 챔버 장비 어휘 가드에서 이미 내린 결론).
        # ⚠️ Widened 2026-09-01. The first version saw two spellings and an
        # adversarial pass planted three more in code positions that sailed
        # through: the rendered string `'64 MiB'`, `64 * 1024 ** 2`, and the
        # factors reversed. The contract names `'64MB'`-style literals too, so a
        # guard that only knows the decimal and one multiplication order is
        # guarding a habit rather than the rule.
        pattern = re.compile(
            r"\b67108864\b"                                   # the decimal
            r"|\b64\s*\*\s*1024\s*\*\s*1024\b"             # 64*1024*1024
            r"|\b1024\s*\*\s*1024\s*\*\s*64\b"             # reversed
            r"|\b64\s*\*\s*1024\s*\*\*\s*2\b"              # 64*1024**2
            r"|['\"`]\s*64\s*(MiB|MB|mb|mib)\s*['\"`]"      # rendered
        )
        offenders = []
        scanned = 0
        for path in SRC_DIR.rglob("*.ts*"):
            if "generated" in path.parts:
                continue
            scanned += 1
            code = _strip_ts_comments(path.read_text(encoding="utf-8", errors="replace"))
            for match in pattern.finditer(code):
                offenders.append(f"{path.relative_to(SRC_DIR)}: {match.group(0)}")
        self.assertEqual(
            offenders,
            [],
            "업로드 상한을 프론트에 박았다 — 서버가 params.max 로 준다: "
            + ", ".join(offenders),
        )
        # 비공허성 둘. (a) 스캔이 실제로 파일을 봤는가, (b) 주석 제거가 코드까지
        # 지워 이 검사를 공허하게 만들지 않았는가 — 합성 위반을 코드 자리에 놓고
        # 잡히는지 확인한다.
        # `50` 은 그날의 트리 크기다. 파일이 정당하게 줄면 red — 명제는 바닥이다.
        self.assertGreater(scanned, 0, "프론트 소스를 거의 못 봤다 — 스캔이 공허하다")
        # 비공허성 (b): 주석 제거가 코드까지 지우지 않았는가 + 모든 철자를 보는가.
        # 합성 위반을 **철자마다** 코드 자리에 놓고 하나씩 확인한다 — 한 철자만
        # 시험하면 나머지가 조용히 빠져도 이 검사는 green 이다.
        for spelling in (
            "const c = 67108864;",
            "const c = 64 * 1024 * 1024;",
            "const c = 1024 * 1024 * 64;",
            "const c = 64 * 1024 ** 2;",
            "const c = '64 MiB';",
            'const c = "64MB";',
        ):
            with self.subTest(spelling=spelling):
                synthetic = _strip_ts_comments(f"// {spelling} in prose\n{spelling}\n")
                self.assertEqual(
                    len(pattern.findall(synthetic)),
                    1,
                    "주석 제거가 코드까지 지웠거나 주석을 남겼거나 "
                    f"이 철자를 보지 못한다: {spelling}",
                )


class TestSessionCallsKeepTheProblemBody(unittest.TestCase):
    """session 표면의 실패가 `code` 를 잃지 않는다 (2026-09-01).

    2026-08-23 이후 이 표면은 session-scoped `ErrorCode` 6종을 발행하고 프론트
    `ErrorCode` union 은 그 팔을 **갖고 있었다**. 그런데 실제 호출 자리 넷이 전부
    `Object.assign(new Error(msg), { status })` 로 실패를 만들어, 그 여섯 코드가
    **한 번도 화면에 도달한 적이 없었다** — 이름 붙일 수는 있는데 관측할 수는
    없는 상태. 화면이 문장을 파싱하지 않고 코드로 분기한다는 계약이 그 동안
    구조적으로 불가능했다.

    ⚠️ **파생 단언만으로는 부족하다.** 여기서 보는 것은 *사본이 다시 생기지
    않는가* 이고, *실제로 코드가 도달하는가* 는 `apps/web/tests/control.test.tsx`
    가 problem+json 응답을 태워 행동으로 단언한다. 소스만 보면 새 호출 자리가
    생겼을 때 이 검사는 조용하다.
    """

    CLIENT = SRC_DIR / "api" / "session-client.ts"

    def _session_consumers(self):
        """session 표면을 부르는 파일 — **파생**이지 목록이 아니다.

        ⚠️ 처음에는 소비자 경로 세 개를 리터럴로 적었고, 적대적 평가가 **새
        라우트**에 손수 만든 실패를 심자 통과했다. 이 검사가 지키려는 명제는
        *"session 을 부르는 곳은 problem body 를 버리지 않는다"* 이므로 대상
        집합은 그 명제가 정의한다 — session 클라이언트를 import 하는 파일 전량.
        오늘 답은 4파일이고 리터럴 목록과 델타 0이라 도입이 무해하다.
        """
        consumers = []
        for path in sorted(SRC_DIR.rglob("*.ts*")):
            if "generated" in path.parts or path == self.CLIENT:
                continue
            code = _strip_ts_comments(path.read_text(encoding="utf-8"))
            if "@/api/session-client" in code or "./session-client" in code:
                consumers.append(path)
        return consumers

    def test_no_route_hand_rolls_a_session_failure(self):
        consumers = self._session_consumers()
        # 비공허성: 파생이 빈 집합을 답하면 아래 단언은 아무것도 증명하지 않는다.
        # ⚠️ 장부가 이미 고친 `consumers >= 10` 과 **같은 형태**이고 값만 작다.
        # 소비자 수는 전송이 모일 때마다 정당하게 줄어든다.
        self.assertGreater(
            len(consumers), 0, "session 소비자를 찾지 못했다 — 파생이 공허하다"
        )
        offenders = [
            path.relative_to(SRC_DIR).as_posix()
            for path in consumers
            if "Object.assign(new Error"
            in _strip_ts_comments(path.read_text(encoding="utf-8"))
        ]
        self.assertEqual(
            offenders,
            [],
            "session 을 부르는 파일이 실패를 손수 만든다 — problem body 의 "
            f"code/params 가 버려진다. session-client 헬퍼를 쓸 것: {offenders}",
        )

    def test_the_client_helpers_carry_code_and_params(self):
        """헬퍼가 code/params 를 나른다 — 이제 **인자를 셀 수 없는 팩토리**로.

        ⚠️ 이 검사는 ``toApiError(`` 호출마다 네 인자가 다 있는지 세고 있었다. 그것은
        *기전* 을 이름으로 부른 것이고, 두 가지를 못 했다: (a) 인자를 다 적지 않은
        호출만 볼 수 있었고, (b) **``params`` 를 세지 않는 형제 클라이언트를 보지
        못했다** — 실측하면 ``platform-client.ts`` 48 사이트가 전부 ``params`` 를
        버리고 있었다. 지금은 세 클라이언트가 ``apiErrorFromResponse`` 를 쓰고 그것은
        실패를 통째로 받아 두 멤버를 자기가 뽑으므로, 셀 인자가 아예 없다.

        그래서 판정은 *인자 개수* 가 아니라 *버릴 수 있는 팩토리의 부재* 다.
        """
        source = _strip_ts_comments(self.CLIENT.read_text(encoding="utf-8"))
        calls = re.findall(r"apiErrorFromResponse\(([\s\S]*?)\)", source)
        # 바닥만 — 아래 루프가 호출 **하나하나** 를 검사하므로 완전성은 거기서 나온다.
        self.assertGreater(
            len(calls), 0, "session 클라이언트에 오류 변환 헬퍼가 없다 — 검사가 공허하다"
        )
        for call in calls:
            self.assertIn("error", call, f"응답 본문을 넘기지 않는 호출: {call!r}")
            self.assertIn("response", call, f"응답을 넘기지 않는 호출: {call!r}")
        self.assertNotRegex(
            source,
            r"\btoApiError\s*\(",
            "인자를 버릴 수 있는 팩토리가 클라이언트에 남아 있다",
        )

    def test_the_routes_actually_delegate(self):
        """비공허성: 위 음성 단언은 라우트가 session 을 아예 안 불러도 통과한다."""
        control = _strip_ts_comments(
            (SRC_DIR / "routes" / "control.tsx").read_text(encoding="utf-8")
        )
        for helper in ("stopSession", "fetchSessionProgress"):
            self.assertIn(helper, control, f"control.tsx 가 {helper} 를 쓰지 않는다")
        diagnostics = _strip_ts_comments(
            (SRC_DIR / "routes" / "diagnostics.tsx").read_text(encoding="utf-8")
        )
        self.assertIn("fetchSessionInfo", diagnostics)

    def test_browser_control_does_not_start_a_session_directly(self):
        """New WEB_SESSION starts must use the central chamber path."""
        control = _strip_ts_comments(
            (SRC_DIR / "routes" / "control.tsx").read_text(encoding="utf-8")
        )
        self.assertNotIn("startSession", control)
        self.assertNotIn("/session/start", control)


class TestApiCallsKeepTheProblemBody(unittest.TestCase):
    """세 표면 **전부**에서 실패가 RFC 9457 `code`/`params` 를 잃지 않는다
    (boundary-plumbing-and-node-liveness, 2026-08-19).

    형제 `TestSessionCallsKeepTheProblemBody` 는 session 표면 하나를 지켰고, 그
    형상이 옳았기 때문에 여기서는 **표면을 손으로 적지 않고 파생**한다. 실측
    (base `73966af1`): 코드를 버리는 호출 자리 **26곳이 전부 HEADLESS** 였다
    (`platform-client.ts` 48/48 · `session-client.ts` 5/5 는 이미 전량 전달).
    즉 결함은 *"세 표면 중 둘"* 이 아니라 **헬퍼 층이 없는 한 표면**이었고,
    그것이 표면 목록을 리터럴로 적으면 안 되는 이유다 — 네 번째 표면이 생기는 날
    그것도 같은 상태로 시작한다.

    ⚠️ **음성 단언만으로는 이 웨이브의 목적을 겨누지 못한다.** *"손수 만든 실패가
    없다"* 는 라우트가 실패를 아예 안 만들어도 참이다. 목적은 *정련 문구가 도달
    가능해지는 것* 이므로 `TestRefinedCopyIsReachable` 이 그것을 **긍정으로**
    단언한다.
    """

    API_DIR = SRC_DIR / "api"
    FACTORY = API_DIR / "to-api-error.ts"

    def _governed_sources(self):
        """`apps/web/src` 의 TS 전량(codegen 제외) — 소비자 열거가 아니다."""
        return [
            path
            for path in sorted(SRC_DIR.rglob("*.ts*"))
            if "generated" not in path.parts
        ]

    def _clients(self, api_dir=None):
        """세 표면의 클라이언트 — `api/*-client.ts` **글롭 파생**.

        ``api_dir`` 는 주입 가능하다 — 그래야 *"이것이 정말 글롭인가"* 를 이 검사가
        소유하지 않은 디렉터리에 대해 물을 수 있다(리터럴 목록으로 바꾸는 변이가
        오늘의 트리에서는 같은 답을 내므로 생존한다).

        ⚠️ 리터럴 3-목록이면 넷째 표면이 조용히 빠진다. 그리고 이 웨이브가 고친
        결함이 정확히 *한 표면만 다른 상태였던 것* 이므로, 표면 집합을 손으로 적는
        검사는 그 결함을 재생산할 자리를 남긴다.
        """
        return sorted((api_dir or self.API_DIR).glob("*-client.ts"))

    def test_the_hand_rolled_pattern_tolerates_the_formatter(self):
        """B5 — the predicate itself, not just today's tree.

        ⚠️ A 26-mutation battery found this: weakening ``_HAND_ROLLED`` back to a
        flat literal SURVIVED, because today's sources contain neither spelling.
        The seal was only as good as the corpus. Both spellings are asserted here,
        and the wrapped one is what `prettier --print-width 100` emits.
        """
        flat = "throw Object.assign(new Error('x'), { status: 1 });"
        wrapped = (
            "throw Object.assign(\n"
            "  new Error('a deliberately long failure message'),\n"
            "  { status: response?.status },\n"
            ") as ApiError;"
        )
        for spelling, source in (("flat", flat), ("prettier-wrapped", wrapped)):
            self.assertRegex(
                source,
                self._HAND_ROLLED,
                f"the {spelling} hand-rolled shape is not detected — `npm run "
                "format` would disarm this seal",
            )
        # ...and it must not fire on an innocent Object.assign.
        self.assertNotRegex(
            "const merged = Object.assign({}, a, b);", self._HAND_ROLLED
        )

    def test_the_locally_decorated_error_shape_is_detected(self):
        """The other half of B5 — a decorator that never says ``Object.assign``."""
        decorated = (
            "const err = new Error(message) as ApiError;\n"
            "(err as { status?: number }).status = status;\n"
        )
        match = self._CONSTRUCTS_ERROR.search(decorated)
        self.assertIsNotNone(match)
        window = decorated[match.end() : match.end() + self._DECORATION_WINDOW]
        self.assertRegex(window, self._DECORATES_AS_FAILURE)
        # An internal narrowing error must NOT be flagged.
        narrowing = "if (!id) { throw new Error('project id required'); }\nreturn id;\n"
        narrow_match = self._CONSTRUCTS_ERROR.search(narrowing)
        narrow_window = narrowing[narrow_match.end() : narrow_match.end() + self._DECORATION_WINDOW]
        self.assertNotRegex(narrow_window, self._DECORATES_AS_FAILURE)

    def test_the_droppable_factory_is_not_importable_by_declaration(self):
        """B7 — the allowlist content, asserted rather than trusted.

        Widening ``_SAFE_FACTORIES`` to admit ``toApiError`` SURVIVED the battery:
        nothing read the declaration. It is the whole point of the axis, so it is
        named here.
        """
        self.assertNotIn(
            "toApiError",
            self._SAFE_FACTORIES,
            "the factory that can drop its arguments is importable again — routes "
            "would be able to reach it",
        )
        self.assertEqual(
            {"apiErrorFromResponse", "clientOriginatedApiError"},
            set(self._SAFE_FACTORIES),
        )

    def test_the_client_glob_finds_a_client_it_has_never_seen(self):
        """B4 — the derivation, proved against a directory it does not own.

        Replacing the glob with a literal three-name list SURVIVED the battery,
        because today the two answers coincide. Pointing the same glob at a
        synthetic directory separates them.
        """
        with tempfile.TemporaryDirectory() as tmp:
            api = Path(tmp) / "api"
            api.mkdir()
            for name in ("session", "platform", "headless", "synthetic"):
                (api / f"{name}-client.ts").write_text("export const c = {};\n")
            # a sibling module that is NOT a client: the glob must skip it
            (api / "query-status.ts").write_text("export const x = 1;\n")
            # ⚠️ `self._clients(api)` — NOT `api.glob(...)`. The first draft called
            # the glob directly, which proved the glob works and said nothing about
            # whether `_clients` uses it; the literal-list mutation survived.
            found = {
                path.stem.replace("-client", "") for path in self._clients(api)
            }
        self.assertEqual({"session", "platform", "headless", "synthetic"}, found)

    def test_the_client_set_is_derived_and_covers_the_three_surfaces(self):
        names = {path.stem.replace("-client", "") for path in self._clients()}
        # 바닥만. 세 표면은 바로 아래에서 **이름으로** 확인하므로 수는 잉여이고,
        # 잉여인 수는 표면이 늘거나 줄 때 아무 근거 없이 판정에 끼어든다.
        self.assertGreater(
            len(names), 0, f"클라이언트 파생이 공허하다: {sorted(names)}"
        )
        for surface in ("session", "platform", "headless"):
            self.assertIn(
                surface,
                names,
                f"{surface} 클라이언트를 파생이 찾지 못했다 — 이 검사가 그 표면을 "
                "보지 못하고 있다",
            )

    # ⚠️ 공백 관용 — 이 저장소의 prettier(`printWidth: 100`)가 긴 메시지에서
    # 이 표현식을 여러 줄로 감싼다. 리터럴 substring 검사는 **`npm run format`
    # 한 번으로 무력화**되고, 그것을 독립 평가가 실 라우트에서 실증했다(감싼 형태를
    # 되돌려 넣었는데 259 테스트가 전부 통과했다). 어제의 철자를 잡고 내일의
    # 철자를 놓치는 검사는 없는 것보다 나쁘다 — 통과했다는 사실이 근거로 쓰인다.
    _HAND_ROLLED = re.compile(r"Object\.assign\s*\(\s*new\s+Error")

    # 손으로 만든 데코레이터는 `Object.assign` 을 **언급하지 않을 수도** 있다:
    #     const err = new Error(msg) as ApiError;
    #     (err as { status?: number }).status = status;
    # 그래서 *구성* 을 본다 — `new Error(` 직후 창에서 `status` 또는 `as ApiError`
    # 가 만나는 것. ⚠️ 창이 문장 전체이면 `useMutation<…, ApiError, …>({ … })`
    # 블록이 오탐이 된다(실측 4건). 창 200자에서 오탐 0.
    _CONSTRUCTS_ERROR = re.compile(r"new\s+Error\s*\(")
    # ⚠️ **빼는 것은 비교뿐이다. 형태를 열거하지 않는다.**
    #
    # 이 술어는 원래 맨 `\bstatus\b` 였고 오탐이 하나 있었다 — 전송이 라우트 밖으로
    # 나가 두 줄이 가까워지자 `throw new Error(...)` 다음의 `data?.status ===` 가
    # 걸려 정상 가드 절이 offender 로 보고됐다(2026-08-19).
    #
    # 첫 수리는 그것을 *"장식은 쓰기다"* 로 읽고 `status\s*(?::|=(?!=))` 로 좁혔다.
    # **그것은 강화가 아니라 약화였고, 독립 검토가 실행으로 증명했다** — 다음 다섯
    # 형태를 전부 놓쳤고(형제 `_HAND_ROLLED` 도 못 잡는다), 그중
    # `Object.defineProperty(f, 'status', {value: 502})` 를 실제 라우트에 심으면
    # 전량 green 이었다:
    #   (a) `(f as never)['status'] = 502`      대괄호 대입
    #   (b) `Object.defineProperty(f, 'status', …)`
    #   (c) `const d = { ...f, status }`         shorthand 프로퍼티
    #   (d) `const K = 'status'; (f as never)[K] = 502`   계산된 키
    #   (e) `Reflect.set(f, 'status', 502)`
    #
    # 교훈: **오탐 하나를 고치려고 형태를 열거하면 열거하지 않은 형태를 전부 잃는다.**
    # 필요한 것은 좁히기가 아니라 그 오탐만 빼는 것이고, 그것은 비교 lookahead 다.
    # 이 형태들은 `TestTheDecorationPredicateStaysBroad` 가 양성 픽스처로 붙잡는다.
    _DECORATES_AS_FAILURE = re.compile(r"\bas\s+ApiError\b|\bstatus\b(?!\s*[=!]=)")
    _DECORATION_WINDOW = 200

    # 라우트가 import 할 수 있는 것은 인자를 버릴 수 없는 팩토리뿐이다.
    # ⚠️ `import { toApiError as buildFailure }` 는 이름 기반 호출 검사를 그냥
    # 지나간다(독립 평가 실증). 그래서 **호출 이름이 아니라 import 표면**을 잠근다.
    _SAFE_FACTORIES = frozenset({"apiErrorFromResponse", "clientOriginatedApiError"})
    # ⚠️ `import … from` 만 보면 **한 줄짜리 재수출**이 두 번째 홉으로 그 자물쇠를
    # 지나간다 — `export { toApiError as buildFailure } from '@/api/to-api-error'`
    # 를 담은 모듈은 `toApiError` 를 *호출* 하지 않으므로 호출 축에도 걸리지 않고,
    # 그 뒤로는 모든 소비자가 인자를 버릴 수 있는 팩토리에 닿는다(독립 적대 평가
    # 실측). `export … from` 은 이름을 밖으로 내보내는 같은 표면이다.
    _FACTORY_IMPORT = re.compile(
        r"(?:import|export)\s*\{([^}]*)\}\s*from\s*'[^']*to-api-error'"
    )

    def test_nothing_hand_rolls_a_failure_outside_the_factory(self):
        sources = self._governed_sources()
        # ⚠️ `50` 은 그날의 트리 크기다. 형제 자리(`_refined_codes` 옆)가 이미
        # *"비-공허성도 파생이다"* 라고 적고 있는데 이 자리만 손 임계값이 남아 있었다.
        #
        # 다만 바닥만으로는 부족하다 — 이 클래스의 두 검사는 전부 `offenders == []`
        # **음성** 스캔이라, 스캔 집합이 조용히 줄어들면 그만큼 조용히 공허해진다
        # (독립 적대 평가가 그 방향을 의심으로 남겼다). 그래서 완전성은 수가 아니라
        # **이름**으로 답한다: 실패를 만드는 표면인 세 클라이언트가 스캔 집합 안에
        # 있어야 한다. 표면이 늘어도 참이고, 하나가 빠지면 그 이름이 red 로 나온다.
        self.assertGreater(
            len(sources), 0, "스캔 집합이 비었다 — 파생이 공허하다"
        )
        scanned = {path.relative_to(SRC_DIR).as_posix() for path in sources}
        for client in self._clients():
            rel = client.relative_to(SRC_DIR).as_posix()
            self.assertIn(
                rel,
                scanned,
                f"{rel} 이 스캔 집합에서 빠졌다 — 실패를 만드는 표면이 음성 단언의 "
                "대상 밖으로 나가면 그 단언은 그만큼 공허하다",
            )
        offenders = []
        for path in sources:
            if path == self.FACTORY:
                continue
            code = _strip_ts_comments(path.read_text(encoding="utf-8"))
            name = path.relative_to(SRC_DIR).as_posix()
            if self._HAND_ROLLED.search(code):
                offenders.append(f"{name} (Object.assign)")
            for match in self._CONSTRUCTS_ERROR.finditer(code):
                window = code[match.end() : match.end() + self._DECORATION_WINDOW]
                if self._DECORATES_AS_FAILURE.search(window):
                    offenders.append(f"{name} (locally decorated Error)")
                    break
        self.assertEqual(
            offenders,
            [],
            "실패를 손수 만드는 파일 — problem body 의 code/params 가 버려진다. "
            f"`apiErrorFromResponse` 를 쓸 것: {offenders}",
        )

    def test_the_droppable_factory_is_private_to_its_module(self):
        """`toApiError` 는 인자를 버릴 수 있으므로 **자기 모듈 밖에서 호출되지 않는다**.

        ⚠️ 이전 판은 `src/api/` **디렉터리 전체**를 면제했다. 그 디렉터리에는 클라이언트
        셋 말고도 7개 모듈이 있고(`chamber-events` · `session-events` ·
        `use-chamber-progress` · … ), 독립 평가가 그중 하나에 코드를 버리는 호출을
        심었을 때 이 검사는 조용했다. 세 클라이언트가 전부 `apiErrorFromResponse` 로
        옮겨진 지금 면제는 **팩토리 파일 하나**로 좁혀진다 — 그것이 실제 계약이다.
        """
        offenders = []
        for path in self._governed_sources():
            if path == self.FACTORY:
                continue
            code = _strip_ts_comments(path.read_text(encoding="utf-8"))
            if re.search(r"\btoApiError\s*\(", code):
                offenders.append(path.relative_to(SRC_DIR).as_posix())
        self.assertEqual(
            offenders,
            [],
            "`toApiError` 가 팩토리 모듈 밖에서 호출됐다 — 그 시그니처는 status 만 "
            f"넘기고 멈출 수 있다: {offenders}",
        )

    def test_only_the_safe_factories_are_importable(self):
        """별칭으로 우회할 수 없게 **import 표면**을 잠근다.

        호출 이름만 보는 검사는 `import { toApiError as buildFailure }` 를 통과시킨다.
        """
        offenders = []
        consumers = 0
        for path in self._governed_sources():
            if path == self.FACTORY:
                continue
            code = _strip_ts_comments(path.read_text(encoding="utf-8"))
            for match in self._FACTORY_IMPORT.finditer(code):
                consumers += 1
                names = [n.strip() for n in match.group(1).split(",") if n.strip()]
                for spec in names:
                    imported = spec.split()[0]
                    if imported not in self._SAFE_FACTORIES:
                        offenders.append(
                            f"{path.relative_to(SRC_DIR).as_posix()}: {spec}"
                        )
        # 비공허성 — **파생**이지 손으로 고른 수가 아니다.
        #
        # ⚠️ 이 자리는 `consumers >= 10` 이었고, 그 10 은 *라우트가 저마다 실패를
        # 만들던 시절의 소비자 수* 를 굳힌 값이다. 전송이 클라이언트로 모이자 그
        # 수는 6 이 됐고 검사는 **개선을 회귀로 보고**했다. 지키려는 명제는
        # *"팩토리를 쓰는 곳이 있다"* 가 아니라 *"실패를 만드는 표면은 전부 안전한
        # 팩토리만 들여온다"* 이므로, 비공허성은 그 표면 집합에서 파생한다 —
        # 클라이언트 글롭 전량이 팩토리를 import 해야 한다.
        clients = self._clients()
        # ⚠️ 바로 위 주석이 `consumers >= 10` 을 비판하면서 같은 형태를 다시 적었다
        # (`>= 3`). 표면이 셋이라는 사실은 형제 검사가 **이름으로** 답한다.
        self.assertGreater(len(clients), 0, "클라이언트 글롭이 공허하다")
        for client in clients:
            code = _strip_ts_comments(client.read_text(encoding="utf-8"))
            with self.subTest(client=client.name):
                self.assertRegex(
                    code,
                    self._FACTORY_IMPORT,
                    f"{client.name} 이 공유 오류 팩토리를 들여오지 않는다 — 이 표면의 "
                    "실패는 problem body 를 버릴 수 있다",
                )
        # ⚠️ `consumers >= len(clients)` 는 **동어반복이었다**(독립 검토 지적):
        # `_governed_sources()` 가 클라이언트를 포함하므로 위 루프가 이미 그 셋을 세고
        # 지나갔다 — 실패할 수 없는 단언이다. 비공허성은 위 per-client 단언이 지고,
        # 여기서는 *팩토리를 쓰는 곳이 클라이언트 말고도 있다* 는 별개의 사실만 본다.
        # 그 사실이 거짓이 되는 날(모든 소비가 클라이언트로 모이는 날)은 이 검사가
        # 알려 주어야 할 변화이지 조용히 참인 문장이 아니다.
        self.assertGreater(
            consumers,
            len(clients),
            "팩토리 소비자가 클라이언트 셋뿐이다 — 사실이면 이 단언을 지우고 사유를 적어라",
        )
        self.assertEqual(
            offenders,
            [],
            "인자를 버릴 수 있는 이름이 팩토리 모듈 밖으로 나갔다(별칭 포함): "
            f"{offenders}",
        )

    def test_the_response_factory_reads_both_extension_members(self):
        """정의 한 자리 — 여기서 하나를 빼면 38 사이트가 조용히 그것을 잃는다."""
        source = _strip_ts_comments(self.FACTORY.read_text(encoding="utf-8"))
        match = re.search(
            r"export function apiErrorFromResponse\([\s\S]*?\n\}", source
        )
        self.assertIsNotNone(
            match, "`apiErrorFromResponse` 정의를 찾지 못했다"
        )
        body = match.group(0)
        self.assertIn("problemCode(", body, "응답 파생 팩토리가 code 를 읽지 않는다")
        self.assertIn("problemParams(", body, "응답 파생 팩토리가 params 를 읽지 않는다")

    def test_the_two_factories_stay_distinct(self):
        """"서버가 뭐라고 했다" 와 "서버에 닿지도 않았다" 는 다른 사실이다.

        하나로 접으면 `status: undefined` 가 두 뜻을 갖고, 되돌릴 방법이 없다.
        """
        source = _strip_ts_comments(self.FACTORY.read_text(encoding="utf-8"))
        for name in ("apiErrorFromResponse", "clientOriginatedApiError"):
            self.assertRegex(
                source,
                rf"export function {name}\(",
                f"{name} 가 사라졌다 — 두 사실을 가르는 구분이 없어졌다",
            )


class TestRefinedCopyIsReachable(unittest.TestCase):
    """`ui/errors.ts` 가 이름을 적은 모든 `ErrorCode` 는 화면에 도달 가능하다.

    ⚠️ **이 클래스가 이 웨이브의 목적을 직접 겨눈다.** base(`73966af1`)에서
    `DRAFT_UNPROCESSABLE` · `DRAFT_EMPTY` · `SESSION_RESULTS_EMPTY` ·
    `REFERENCE_DATA_NOT_PROVISIONED` 넷은 정련 문구가 **있는데** 도달 불가였다 —
    백엔드가 발행하고 프론트가 문구를 갖고도, 그 코드를 발행하는 표면의 호출 자리가
    전부 코드를 버렸기 때문이다. 뒤 둘은 headless **전용** 코드라 구조적으로 그랬다.

    대상 코드 집합은 그 테이블에서 **파생**한다 — 손으로 적으면 열한 번째 팔이
    추가된 날 조용히 대상 밖이 된다.
    """

    ERRORS_TS = SRC_DIR / "ui" / "errors.ts"
    API_DIR = SRC_DIR / "api"

    def _refined_codes(self):
        """정련 표가 이름을 적은 코드 전부 — **파서 파생**.

        ⚠️ 이 자리는 ``CODE_REFINED_KEY_BY_STATUS[\\s\\S]*?=\\s*\\{([\\s\\S]*?)\\n\\};``
        + ``([A-Z][A-Z0-9_]+):\\s*'errors\\.`` 였다 — 들여쓰기와 **작은따옴표**에
        민감한 정규식이고, 형제 축이 같은 커밋에서 파서로 옮겨 가면서
        ``'QUOTED':`` 키와 백틱 값을 **의도적으로 받아들이기로** 했으므로 두 축이
        같은 파일에 대해 서로 다른 답을 하게 돼 있었다. 독립 적대 평가가 그 비대칭을
        지적했다. 한 파일, 한 질문, 한 파서.
        """
        source = self.ERRORS_TS.read_text(encoding="utf-8")
        tables = census_copy_tables(source).code_keyed
        self.assertNotEqual(tables, (), "정련 문구 테이블을 찾지 못했다")
        return sorted({code for table in tables for code in screaming_snake_keys(table)})

    def _publishing_surfaces(self, code_name):
        from fcc_test_contracts.common.api_error_codes import ApiSurface, surface_error_codes

        return {
            surface
            for surface in ApiSurface
            if code_name in {code.name for code in surface_error_codes(surface)}
        }

    def _consumers_of(self, surface_name):
        """`<surface>-client` 를 import 하는 파일 전량 — 파생."""
        client = f"{surface_name}-client"
        out = []
        for path in sorted(SRC_DIR.rglob("*.ts*")):
            if "generated" in path.parts or path.parent == self.API_DIR:
                continue
            code = _strip_ts_comments(path.read_text(encoding="utf-8"))
            if f"@/api/{client}" in code or f"./{client}" in code:
                out.append(path)
        return out

    def _drops_the_code(self, path):
        """One predicate, shared with the negative seal.

        ⚠️ This used to carry its own copy of the literal
        ``"Object.assign(new Error"`` check — so the prettier-wrapped form (which
        `npm run format` produces at printWidth 100) escaped BOTH seals at once.
        Delegating means the two can no longer disagree about what "drops the
        code" means, and there is one place to harden.
        """
        code = _strip_ts_comments(path.read_text(encoding="utf-8"))
        negative = TestApiCallsKeepTheProblemBody
        if negative._HAND_ROLLED.search(code):
            return True
        for match in negative._CONSTRUCTS_ERROR.finditer(code):
            window = code[match.end() : match.end() + negative._DECORATION_WINDOW]
            if negative._DECORATES_AS_FAILURE.search(window):
                return True
        # ⚠️ **창을 없앴다 — 물어야 할 질문이 애초에 창을 필요로 하지 않는다.**
        #
        # 이 자리는 익명 `+ 400` 이었고, 이 웨이브의 첫 상환은 그것을 균형 괄호
        # 스캐너로 바꿨다. 독립 적대 평가가 그 스캐너를 **정규식 리터럴**로 뚫었다
        # (`strip_ts_comments` 는 정규식 내용을 보존하므로 `/\(/g` 의 괄호가 균형을
        # 깨고, 그때의 폴백은 "넓힌다"인데 이 소비자에게 넓힘은 *조용한 통과*다 —
        # 질문이 `problemCode` 의 **부재**이기 때문이다). 안전한 방향을 거꾸로 적은
        # 것이고, 그것은 창을 어떻게 재든 남는 문제였다.
        #
        # 정공은 질문을 바꾸는 것이다. `toApiError` 는 **자기 모듈 밖에서 호출되지
        # 않는다**(`TestApiCallsKeepTheProblemBody
        # ::test_the_droppable_factory_is_private_to_its_module`), 그리고
        # `_consumers_of` 는 `src/api/` 를 이미 제외한다. 그러므로 여기서 보이는
        # `toApiError(` 는 인자가 무엇이든 이미 위반이다 — 셀 것도, 창도 없다.
        # 오늘 실측 0건이라 판정은 byte-identical 이고, 규칙은 더 강해진다.
        if re.search(r"\btoApiError\s*\(", code):
            return True
        return False

    def test_the_table_is_not_empty(self):
        """비공허성 — 파생이 빈 집합을 답하면 아래는 아무것도 증명하지 않는다."""
        codes = self._refined_codes()
        # 바닥만 — `5` 는 그날의 테이블 크기이고, 정련 팔이 정당하게 줄면 red 였다.
        self.assertGreater(len(codes), 0, f"정련 코드 파생이 공허하다: {codes}")

    def test_every_refined_code_can_reach_the_screen(self):
        unreachable = {}
        for code_name in self._refined_codes():
            surfaces = self._publishing_surfaces(code_name)
            if not surfaces:
                # 백엔드가 발행하지 않는 코드에 문구가 있는 것은 별건이다
                # (이 축의 반대 방향) — 여기서 판정하지 않는다.
                continue
            for surface in surfaces:
                blocked = [
                    path.relative_to(SRC_DIR).as_posix()
                    for path in self._consumers_of(surface.value.lower())
                    if self._drops_the_code(path)
                ]
                if blocked:
                    unreachable.setdefault(code_name, {})[surface.name] = blocked
        self.assertEqual(
            unreachable,
            {},
            "정련 문구가 있는데 그 코드가 화면에 도달할 수 없다 — 그 코드를 발행하는 "
            "표면의 소비자가 problem body 를 버린다. `apiErrorFromResponse` 로 옮길 "
            f"것: {unreachable}",
        )


class TestTheDecorationPredicateStaysBroad(unittest.TestCase):
    """실패를 손수 장식하는 형태는 **열거할 수 없다** — 그래서 술어를 좁히지 않는다.

    2026-08-19 에 오탐 하나(`data?.status ===`)를 고치려고 `_DECORATES_AS_FAILURE` 를
    `\bstatus\s*(?::|=(?!=))` 로 좁혔다. *"장식은 쓰기다"* 라는 그럴듯한 읽기였지만
    **강화가 아니라 약화**였고, 독립 검토가 실행으로 다섯 형태를 통과시켰다 — 그중
    `Object.defineProperty` 판을 실제 라우트에 심었을 때 전량 green 이었다.
    그때 잃는 것은 RFC 9457 `code`/`params` 다.

    ⚠️ **이 클래스가 지키는 것은 오늘의 정규식이 아니라 방향이다.** 좁히면 red 다.
    새 우회 형태를 발견하면 술어에 분기를 더하지 말고 **여기에 더하라**.
    """

    PREDICATE = TestApiCallsKeepTheProblemBody._DECORATES_AS_FAILURE
    SIBLING = TestApiCallsKeepTheProblemBody._HAND_ROLLED

    #: 전부 *실패에 status 를 붙이는* 진짜 형태다.
    DECORATION_SHAPES = {
        "속성 대입": "const f = new Error('x');\nf.status = 502;",
        "대괄호 대입": "const f = new Error('x');\n(f as never)['status'] = 502;",
        "defineProperty": "const f = new Error('x');\nObject.defineProperty(f, 'status', { value: 502 });",
        "shorthand 프로퍼티": "const f = new Error('x');\nconst d = { ...f, status };",
        "계산된 키": "const f = new Error('x');\nconst K = 'status';\n(f as never)[K] = 502;",
        "Reflect.set": "const f = new Error('x');\nReflect.set(f, 'status', 502);",
        "as ApiError": "const f = new Error('x') as ApiError;",
    }

    #: 좁힌 술어가 놓쳤던 다섯 — 형제 `_HAND_ROLLED` 도 **하나도** 잡지 못한다.
    #: 이 목록이 비면 위 검사는 형제가 이미 덮는 것을 재확인하는 셈이 된다.
    ONLY_THIS_PREDICATE_CATCHES = (
        "대괄호 대입",
        "defineProperty",
        "shorthand 프로퍼티",
        "계산된 키",
        "Reflect.set",
    )

    #: 장식이 **아닌** 것 — 발화하면 정상 가드 절이 offender 가 된다.
    NON_DECORATION = {
        "일치 비교": "if (data?.status === 'succeeded') return;",
        "불일치 비교": "if (data?.status !== 'queued') return;",
    }

    def test_every_known_decoration_shape_is_detected(self):
        for label, code in self.DECORATION_SHAPES.items():
            with self.subTest(shape=label):
                self.assertRegex(code, self.PREDICATE, f"장식 형태 `{label}` 를 놓친다")

    def test_the_sibling_guard_covers_none_of_the_five(self):
        """비공허성 — 다섯이 형제 가드에 이미 걸린다면 이 클래스는 아무것도 지키지 않는다."""
        self.assertGreaterEqual(len(self.ONLY_THIS_PREDICATE_CATCHES), 5)
        for label in self.ONLY_THIS_PREDICATE_CATCHES:
            with self.subTest(shape=label):
                self.assertNotRegex(
                    self.DECORATION_SHAPES[label],
                    self.SIBLING,
                    f"`{label}` 가 형제 가드에도 걸린다 — 통제가 중복이다",
                )

    def test_comparisons_are_not_decorations(self):
        for label, code in self.NON_DECORATION.items():
            with self.subTest(shape=label):
                self.assertNotRegex(code, self.PREDICATE, f"`{label}` 에 발화한다 — 오탐")

    def test_the_narrowed_predicate_would_be_red_here(self):
        """이 클래스가 실제로 그 좁히기를 막는지 — 옛 좁힌 판으로 재현한다."""
        narrowed = re.compile(r"\bas\s+ApiError\b|\bstatus\s*(?::|=(?!=))")
        missed = [
            label
            for label in self.ONLY_THIS_PREDICATE_CATCHES
            if not narrowed.search(self.DECORATION_SHAPES[label])
        ]
        self.assertEqual(
            len(missed),
            len(self.ONLY_THIS_PREDICATE_CATCHES),
            "좁힌 술어가 이 형태들을 여전히 잡는다 — 그렇다면 이 통제는 무엇도 증명하지 않는다",
        )


class TestRoutesOwnNoHeadlessTransport(unittest.TestCase):
    """라우트는 headless 전송을 소유하지 않는다 (2026-08-19).

    ``headless-client.ts`` 는 세 web 클라이언트 중 유일하게 **헬퍼 층이 0** 이었고,
    그래서 13개 라우트 파일이 `headlessClient.<VERB>` 를 **30 사이트**에서 직접
    불렀다. 백엔드 경로 하나를 바꾸면 30곳을 고쳐야 했고, *어느 라우트가 어떤
    operation 을 부르는가* 는 grep 으로만 답할 수 있었다.

    ⚠️ **명제는 전송 은닉이지 정보 손실이 아니다.** 그 13 라우트는 **전부** 이미
    ``apiErrorFromResponse`` 로 던지고 있었다(파일당 2~10회, ``toApiError`` 0회,
    ``Object.assign(new Error`` 0회) — 형제 웨이브가 닫은 그 결함은 여기 열려
    있지 않았다. 열려 있던 것은 라우트가 여전히 HTTP 를 **고를 수 있다**는 것이다.

    ⚠️ **이 검사는 파생이지 목록이 아니다.** 대상은 `routes/` 아래 전량이므로,
    새 라우트가 직접 호출을 들고 도착하면 그 자리가 이름으로 red 다.

    ⚠️ **주석을 벗기고 판정한다.** 이 저장소는 같은 형태로 이미 값을 치렀다 —
    주석 산문이 금지 토큰을 언급하기만 해도 FAIL 하는 봉인이 실제로 있었고
    (장부 2026-07-28), 그래서 이 축이 vitest 가 아니라 **공유 렉서가 사는 이쪽**에
    있다. 스트리퍼는 `tests/support/parity.py::strip_ts_comments` SSOT 다.

    한 예외가 있고 그것은 이음매다: ``routes/reports.tsx`` 는 노드마다 클라이언트를
    **고른다**(`clientForNode`). *어느 노드에 말하는가* 는 그 화면의 정당한 결정이고
    *어떻게 말하는가* 는 아니므로, 그 라우트는 클라이언트 객체를 import 하되
    **동사를 부르지 않는다** — 아래 단언이 정확히 그 구분을 본다.
    """

    ROUTES_DIR = SRC_DIR / "routes"

    #: ⚠️ **어떤 수신자든** 대문자 HTTP 동사 호출을 잡는다. 한때 이 술어는
    #: `\bheadlessClient\s*\.\s*(VERB)\(` 였고, 그러면 자기 docstring 이 주장하는
    #: 구분을 **볼 수 없었다** — 독립 검토(2026-08-19)가 실행으로 셋을 통과시켰다:
    #:
    #:   1. `clientForNode(nodeBaseUrl).POST(…)` — base 에 **실제로 있던 코드**이고,
    #:      `clientForNode(null)` 은 **공유 `headlessClient` 를 돌려준다**. 즉 가드가
    #:      지킨다고 적은 바로 그 객체 위의 직접 호출에 눈이 멀어 있었다.
    #:   2. `const transport = headlessClient; transport.POST(…)` — 한 줄 별칭.
    #:   3. 동사 넷이 통제 없음(`GET` 만 witness) — 지워도 아무 테스트가 안 바뀌었다.
    #:
    #: 넓힌 술어가 오탐을 내지 않는 이유는 **측정**이다: 오늘 `routes/**` 에 대문자
    #: `.VERB(` 는 **0건**이다. openapi-fetch 의 대문자 동사는 이 코드베이스에서
    #: 전송 호출의 고유 철자이고, 그래서 수신자 이름을 묻지 않는 편이 **더 강하다**.
    VERB_CALL = re.compile(r"\.\s*(GET|POST|PUT|PATCH|DELETE)\s*\(")

    def _route_sources(self):
        sources = []
        for path in sorted(self.ROUTES_DIR.rglob("*.ts*")):
            sources.append((path, strip_ts_comments(path.read_text(encoding="utf-8"))))
        return sources

    def test_the_scan_target_is_not_empty(self):
        """0 파일을 순회하는 음성 단언은 통과한다 — 이 저장소가 반복해서 치른 값이다."""
        sources = self._route_sources()
        # 메시지가 적은 명제는 *"아무것도 찾지 못했다"* 이고 그 바닥은 0 이다.
        self.assertGreater(len(sources), 0, "routes/ 스캔이 아무것도 찾지 못했다")
        self.assertTrue(
            any("headless-client" in src for _p, src in sources),
            "어느 라우트도 headless 클라이언트를 언급하지 않는다 — 스캔 대상이 틀렸다",
        )

    def test_no_route_calls_the_headless_transport_directly(self):
        offenders = []
        for path, src in self._route_sources():
            for match in self.VERB_CALL.finditer(src):
                line = src.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(SRC_DIR)}:{line} {match.group(0)}")
        self.assertEqual(
            offenders,
            [],
            "라우트가 headless 전송을 직접 부른다. 동사·경로·파라미터 모양은 "
            "`apps/web/src/api/headless-client.ts` 의 operation 이 소유한다 — "
            "새 호출이 필요하면 거기에 이름 붙인 함수를 더하고 라우트는 그것을 부른다:\n  "
            + "\n  ".join(offenders),
        )

    def test_the_guard_would_catch_a_reintroduced_call(self):
        """비공허성 — 그리고 **동사 다섯 전부**와 **우회 철자 둘**을 witness 한다.

        ⚠️ 처음 판은 `headlessClient.GET(` 하나만 먹였다. 그러면 술어에서 나머지 넷을
        지워도 아무 테스트가 안 바뀌고, 이 저장소 기준으로 그것들은 가드가 아니라
        주석이다(독립 검토가 실행으로 증명했다). 우회 둘은 실제로 통과했던 형태다.
        """
        cases = {
            "GET": "const r = await headlessClient.GET('/headless/jobs', {});",
            "POST": "await headlessClient.POST('/headless/jobs/{job_id}/stop', {});",
            "PUT": "await headlessClient.PUT('/x/rows', {});",
            "PATCH": "await headlessClient.PATCH('/x', {});",
            "DELETE": "await headlessClient.DELETE('/x/rows/{id}', {});",
            "per-node factory (base 에 실제로 있던 형태)":
                "await clientForNode(nodeBaseUrl).POST('/headless/sessions/{id}/reports', {});",
            "one-line alias": "const transport = headlessClient;\nawait transport.POST('/x', {});",
        }
        for label, code in cases.items():
            with self.subTest(shape=label):
                self.assertEqual(
                    len(self.VERB_CALL.findall(strip_ts_comments(code))),
                    1,
                    f"우회 형태 `{label}` 를 잡지 못한다",
                )
        # 주석은 세지 않는다.
        commented = "// headlessClient.GET('/x', {}) in a comment must NOT count\n"
        self.assertEqual(len(self.VERB_CALL.findall(strip_ts_comments(commented))), 0)

    def test_every_operation_throws_through_the_shared_factory(self):
        """operation 은 실패를 만들 자리를 라우트에 남기지 않는다.

        ``async function`` 마다 ``apiErrorFromResponse`` 가 있는지 본다 — 하나라도
        빠지면 그 operation 이 실패를 조용히 삼키거나 라우트에 되돌려준다.
        """
        # ⚠️ **파생은 하나다.** 이 검사는 한때 자기 `re.split(r"\nexport async function ")`
        # 을 들고 있었고, 그래서 `_headless_operations()` 를 화살표 형태까지 보게 고친
        # 뒤에도 **여기만 눈이 멀어 있었다** — 독립 검토의 반례(오류 처리를 지운 화살표
        # operation)가 그 상태에서 살아남았다. 판정 입력이 둘이면 하나만 고쳐지고,
        # 고쳐지지 않은 쪽이 조용히 답한다.
        src = _strip_ts_comments(
            (SRC_DIR / "api" / "headless-client.ts").read_text(encoding="utf-8")
        )
        operations = _headless_operations()
        # 비공허성도 **파생**이다. `>= 25` 같은 손 임계값은 32개 중 일곱을 조용히 잃을
        # 여유를 남긴다(독립 검토가 그 headroom 을 이름으로 지적했다).
        self.assertEqual(
            len(operations),
            len(_ASYNC_EXPORT_RE.findall(src)),
            "operation 파생이 export 된 비동기 심볼 수와 다르다 — 못 보는 철자가 있다",
        )
        # ⚠️ 바로 위 주석이 `>= 25` 를 이름으로 비판하면서 그 줄을 남겨 뒀다.
        # 위 두 줄의 파생 상등이 완전성을 답하므로 여기 남는 것은 바닥뿐이다.
        self.assertGreater(len(operations), 0, "operation 파생이 비었다 — 스캔 대상이 틀렸다")
        downloads = _download_operations()
        # 면제 집합은 **비어 있지 않아야 한다** — 비면 아래 차집합이 전체가 되고
        # 이 검사는 옛 형태 그대로 돌아간 것이므로, 면제가 사라진 사실을 아무도 못 본다.
        self.assertNotEqual(downloads, {}, "다운로드 operation 파생이 비었다 — 반환 타입 철자를 확인하라")
        missing = sorted(
            name
            for name, body in operations.items()
            if "apiErrorFromResponse(" not in body and name not in downloads
        )
        self.assertEqual(
            missing, [], f"공유 factory 없이 실패를 다루는 operation: {missing}"
        )

    def test_the_download_exemption_is_keyed_to_the_return_type_not_a_helper_name(self):
        """면제는 **타입**에서 파생한다 — 이름이 아니다.

        ⚠️ **초판은 이름으로 파생했고 독립 적대적 평가가 실행으로 뚫었다**
        (2026-09-12). 그때 술어는 *operation 본문이 위임 헬퍼 이름 + `(` 를 포함하는가*
        였고 그것은 raw substring 이라 **`autoDownload(` 가 `toDownload(` 를 포함**했다.
        실패를 통째로 삼키는 operation 이 그 이름 하나로 통과했다 — `toDownload` 라는
        seam 을 가진 모듈에서 `autoDownload` 는 지극히 그럴듯한 이름이다.

        이름은 우연히 겹치고 **반환 타입은 겹치지 않는다.** 그리고 면제된 쪽은
        면제로 끝나지 않는다 — `TestBlobParsingIsADeclaredConsumptionAxis` 가
        *두 seam 을 모두 지나는가* 와 *그 seam 이 factory 로 던지는가* 를 잇는다.
        """
        exempt = "export async function exportX(): Promise<HeadlessDownload> {\n  return toDownload(a, b, c);\n}\n"
        collide = "export async function fetchX(): Promise<unknown> {\n  return autoDownload(await c.GET(p, i));\n}\n"
        plain = "export async function fetchY(): Promise<Snapshot> {\n  const { data } = await c.GET(p, i);\n  return data;\n}\n"

        with self.subTest(shape="download operation is exempt"):
            self.assertIsNotNone(_DOWNLOAD_RETURN_RE.search(exempt))
        with self.subTest(shape="substring collision is NOT exempt"):
            # 이것이 초판을 통과했던 반례다.
            self.assertIsNone(_DOWNLOAD_RETURN_RE.search(collide))
            self.assertNotIn("apiErrorFromResponse(", collide)
        with self.subTest(shape="a plain swallowing operation is NOT exempt"):
            self.assertIsNone(_DOWNLOAD_RETURN_RE.search(plain))
            self.assertNotIn("apiErrorFromResponse(", plain)
        with self.subTest(shape="the real module has both kinds"):
            operations = _headless_operations()
            downloads = set(_download_operations())
            self.assertNotEqual(downloads, set(), "다운로드 operation 이 하나도 없다")
            self.assertNotEqual(
                set(operations) - downloads, set(), "비-다운로드 operation 이 하나도 없다"
            )


# ---------------------------------------------------------------------------
# Transport stubs are derived from the contract (`typed-headless-transport-stubs`,
# 2026-09-10 — debt entry `[2026-08-19 headless-helper] P3`).
#
# `spyHeadlessTransport()` used to hand back bare `Mock`s (= `Mock<any>`), so a
# stub payload that did not match the wire contract was invisible to `tsc`. The
# suites therefore proved something about *a* response shape rather than about
# today's. Migrating them surfaced eleven live drifts, including two members that
# no longer exist on the wire and a required member that had never been added.
#
# ⚠️ **Where the load-bearing check lives.** The claim is a *compile-time* one,
# and nothing in Python can observe it: by the time pytest runs, `tsc` has
# already had its say. The behaviour axis is therefore
# `apps/web/tests/helpers/headless-contract.type-test.ts` — a counterexample tree
# whose `@ts-expect-error` directives fail the build if the derivation collapses
# to `any`. What this class adds is the part TypeScript cannot state about
# itself: that the counterexample tree exists, is reached by `npm run typecheck`,
# is NOT collected by vitest, covers every builder the module exports, and that
# the suites have not quietly gone back to hand-assembling envelopes.
#
# The scan set is a **complement**, not a list: every file under `apps/web/tests`
# that calls `spyHeadlessTransport`. A suite added tomorrow is in scope by
# existing.
# ---------------------------------------------------------------------------

WEB_TESTS_DIR = WEB_ROOT / "tests"
TRANSPORT_HELPER = WEB_TESTS_DIR / "helpers" / "headless-transport.ts"
CONTRACT_HELPER = WEB_TESTS_DIR / "helpers" / "headless-contract.ts"
CONTRACT_TYPE_TEST = WEB_TESTS_DIR / "helpers" / "headless-contract.type-test.ts"
TYPE_MUTATION_SCRIPT = WEB_ROOT / "scripts" / "verify-stub-typing-mutations.mjs"
OPENAPI_HELPERS_PACKAGE = "openapi-typescript-helpers"


#: Verbs the headless transport spies expose.
#:
#: ⚠️ **This tuple is not its own oracle.** An independent review deleted
#: ``"PUT"`` from it and the whole file stayed green, because the per-verb
#: control iterated *this constant* — so a verb removed here was simply not
#: tested. An oracle that shares its input with the thing under test cannot
#: fail. :func:`_transport_verbs_declared_in_typescript` reads the verbs from
#: the helper's own ``type Verb`` union instead, and the two are asserted equal.
HEADLESS_TRANSPORT_VERBS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _transport_verbs_declared_in_typescript() -> "frozenset[str]":
    """The verbs ``headless-transport.ts`` actually declares.

    Independent of :data:`HEADLESS_TRANSPORT_VERBS` on purpose — it is the
    source the Python constant is supposed to mirror, so it can judge it.
    """
    source = strip_ts_comments(TRANSPORT_HELPER.read_text(encoding="utf-8"))
    match = re.search(r"type Verb\s*=\s*([^;]+);", source)
    if match is None:  # pragma: no cover - the helper always declares it
        return frozenset()
    return frozenset(re.findall(r"'([A-Z]+)'", match.group(1)))

#: The one suffix that is **not** a request read: a bare ``.length`` asks about
#: volume ("did mounting trigger a second fetch"), which the typed accessors do
#: not answer better.
#:
#: ⚠️ Stated as an allow-list, and that inversion is the point. The first
#: version of this rule enumerated the *bad* suffixes (``[``, ``.find(``, …) and
#: an independent review defeated it **23 times out of 23** — ``.mock.lastCall``,
#: ``vi.mocked(…)``, aliasing, destructuring, spread, ``for..of``, and simply
#: assigning ``.mock.calls`` to a local first. A deny-list of syntax fails
#: unsafely: the spelling that is not on it is always the next one.
_VOLUME_ONLY_SUFFIX = ".length"

#: The two members that expose recorded calls. ``lastCall`` is a first-class
#: vitest accessor in the same family as ``calls.at(-1)``; omitting it was the
#: most obvious hole in the first version of this rule.
_CALL_RECORD_MEMBERS = (".mock.calls", ".mock.lastCall")

#: Characters that may appear in the receiver expression to the left of
#: ``.mock`` — identifiers and member access, quotes and brackets for computed
#: access, parentheses for ``vi.mocked(…)``.
_RECEIVER_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$.'\"[]()"
)


def _transport_bindings(source: str) -> "set[str]":
    """Names that refer to the headless transport spies **in this file**.

    Derived, not listed. The first version hard-coded ``("headlessClient",
    "transport")`` — the two local names that happened to be in use — so a suite
    writing ``const spies = spyHeadlessTransport()`` was invisible, and nothing
    forces the name. The scan set next door is a complement for exactly this
    reason; the binding set has to be one too.

    Four shapes, all of which an independent review found evading the hand list:
    the module export itself, the spy bundle, a verb pulled into a local, and a
    destructured verb.
    """
    names = {"headlessClient"}
    names.update(
        re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*spyHeadlessTransport\s*\(", source)
    )
    verbs = "|".join(HEADLESS_TRANSPORT_VERBS)
    for name, base in re.findall(
        rf"(?:const|let|var)\s+(\w+)\s*=\s*(\w+)\.(?:{verbs})\b", source
    ):
        if base in names:
            names.add(name)
    for inner, base in re.findall(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*(\w+)", source):
        if base in names:
            names.update(part.strip().split(":")[-1].strip() for part in inner.split(","))
    return {name for name in names if name}


def _request_reaches_into_mock_calls(source: str) -> "list[str]":
    """Sites that read a headless transport request out of a mock's call record.

    Comment-stripped, because the accessor's own docstring shows the pattern it
    replaces and prose is not code.

    Optional chaining is normalised away (``?.`` → ``.``) so ``mock?.calls?.[0]``
    is the same site as ``mock.calls[0]`` — a rule a question mark defeats is
    not a rule.

    The receiver is matched against :func:`_transport_bindings` rather than a
    fixed object list, which is what keeps the platform surface out (it binds
    its own mocks and never calls ``spyHeadlessTransport``) and also keeps a
    genuine in-file neighbour out: ``reports.test.tsx`` reads
    ``download.fetchMock.mock.calls[0]?.length``, a different mock with no typed
    accessor to move to. Reporting it would be reporting code this axis cannot
    fix, and a rule that does that gets deleted.
    """
    text = strip_ts_comments(source).replace("?.", ".")
    bindings = _transport_bindings(text)
    offenders: list[str] = []
    for member in _CALL_RECORD_MEMBERS:
        start = 0
        while True:
            index = text.find(member, start)
            if index == -1:
                break
            start = index + len(member)
            left = index
            while left > 0 and text[left - 1] in _RECEIVER_CHARS:
                left -= 1
            receiver = text[left:index]
            if not any(re.search(rf"\b{re.escape(name)}\b", receiver) for name in bindings):
                continue
            tail = text[start : start + 16].lstrip()
            if member == ".mock.calls" and tail.startswith(_VOLUME_ONLY_SUFFIX):
                continue
            line = text.count("\n", 0, index) + 1
            offenders.append(f"{receiver}{member} @ line {line}")
    return offenders


# Type names this repository must *import* rather than re-declare. Re-declaring
# any of them would create a second definition of the same schema lookup, and the
# copy that drifts is always the local one.
_HELPER_TYPES_THAT_MUST_BE_IMPORTED = (
    "SuccessResponse",
    "SuccessResponseJSON",
    "ErrorResponse",
    "ResponseObjectMap",
    "PathsWithMethod",
    "FilterKeys",
)

# The producers a stub payload is allowed to come from.
#
# ⚠️ **An allow-list of producers, not a deny-list of spellings.** The first
# version of this rule matched `response:\s*(?:new Response\(|\{\s*status)`
# line by line, and an independent review measured **8 of 10** realistic
# spellings slipping past it: a `const` holding the Response, a quoted key,
# a prettier line-wrap, a helper function, a headers-first literal,
# `Object.assign`, property shorthand, `Response.json()`. Worse, `prettier
# --write` alone can move `new Response(` onto its own line and silently disarm
# the rule for an *existing compliant* site — the recorded "a literal seal loses
# to the formatter" shape.
#
# Spellings of "build an object" do not enumerate; **producers do**. So the
# question changes from *"does this look like a hand-built envelope"* to
# *"did this payload come from one of the four builders"*, and the answer set is
# closed by construction.
_STUB_PRODUCERS = (
    "headlessOk",
    "headlessProblem",
    "headlessEmptyOk",
    "headlessDownload",
    "getBodyRoutes",
)

# Where a *transport* stub payload is handed to a mock.
#
# The receiver must be one of the five transport verbs, and that set is read out
# of the helper rather than restated here — `type Verb = 'GET' | …`. Keying on
# the verb rather than on the spy's variable name is what makes this closed:
# suites bind the spies to several names (`headlessClient`, `transport`,
# `nodeHeadlessClient`, per-node `nodeA`/`nodeB` object literals), and an
# enumeration of names would miss the next one.
#
# `mockImplementation` is excluded on purpose: its argument is a *function*, and
# what that function returns is checked by the compiler through
# `HeadlessStubMethod` / `HeadlessRouteHandler`.
def _transport_verbs() -> "tuple[str, ...]":
    source = strip_ts_comments(TRANSPORT_HELPER.read_text(encoding="utf-8"))
    match = re.search(r"type Verb =\s*([^;]+);", source)
    if match is None:  # pragma: no cover - guarded by its own test
        return ()
    return tuple(re.findall(r"'(\w+)'", match.group(1)))


def _stub_sink(verbs: "tuple[str, ...]") -> "re.Pattern[str]":
    return re.compile(
        r"\.(?:" + "|".join(verbs) + r")\.mockResolvedValue(?:Once)?\s*\("
    )


def _balanced_argument(text: str, open_index: int) -> "str | None":
    """The source of the argument list starting at ``text[open_index] == '('``.

    Brace/paren matching with string and template awareness — a line regex
    cannot see a wrapped literal, and that blindness is what this rule replaced.
    """
    depth = 0
    index = open_index
    quote: "str | None" = None
    while index < len(text):
        char = text[index]
        if quote is not None:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in "\"'`":
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : index]
        index += 1
    return None


def _local_producers(stripped: str) -> "frozenset[str]":
    """Names in this file that stand for a builder call.

    Two shapes, because suites use both: a `const` bound directly to a builder
    result, and a local helper whose body answers one (`pubsOk`, `okExport`).
    Without this the rule would report a suite that *is* delegating correctly,
    and a rule that reports correct code gets deleted.
    """
    names: set[str] = set()
    for name, expression in re.findall(
        r"(?:const|let|var)\s+(\w+)\s*(?::[^=\n]+)?=\s*([^\n;]+)", stripped
    ):
        if expression.strip().startswith(_STUB_PRODUCERS):
            names.add(name)
    # Local helpers: take the source from the declaration to the next
    # declaration and ask whether a builder call appears in it.
    for match in re.finditer(r"(?:function|const)\s+(\w+)\s*[=(]", stripped):
        body = stripped[match.end() : match.end() + 1200]
        if any(f"{producer}(" in body for producer in _STUB_PRODUCERS):
            names.add(match.group(1))
    return frozenset(names)


def _payloads_not_from_a_builder(source: str) -> "list[str]":
    """Transport stub payloads that did not come from :data:`_STUB_PRODUCERS`."""
    verbs = _transport_verbs()
    if not verbs:  # pragma: no cover - guarded by its own test
        return ["<could not read the verb set from the helper>"]
    stripped = strip_ts_comments(source)
    producers = _local_producers(stripped)
    offenders: list[str] = []
    for match in _stub_sink(verbs).finditer(stripped):
        argument = _balanced_argument(stripped, match.end() - 1)
        if argument is None:
            continue
        head = " ".join(argument.split())
        if head.startswith(_STUB_PRODUCERS):
            continue
        called = re.match(r"^(\w+)\s*\(", head)
        if called is not None and called.group(1) in producers:
            continue
        identifier = re.match(r"^(\w+)$", head)
        if identifier is not None and identifier.group(1) in producers:
            continue
        offenders.append(head[:120])
    return offenders


def _type_test_halves() -> "tuple[str, str]":
    """``(positive-control half, whole file)`` of the counterexample tree.

    Split on ``export const accepted``, a **statement** that survives comment
    stripping. The marker matters: the first version split on the section
    comment, which is removed before the split ever runs.
    """
    tree = strip_ts_comments(CONTRACT_TYPE_TEST.read_text(encoding="utf-8"))
    marker = "export const accepted"
    return (tree.split(marker)[0], tree)


def _envelope_builders_by_kind() -> "dict[str, str]":
    """``{envelope interface: the builder that returns it}``, read off the module.

    Both halves are derived, so a fifth envelope kind — or a builder whose
    return type changes — moves the requirement without anyone editing a list.
    """
    source = strip_ts_comments(CONTRACT_HELPER.read_text(encoding="utf-8"))
    declared = set(re.findall(r"^export interface (\w*Envelope)\b", source, re.MULTILINE))
    mapping: dict[str, str] = {}
    for match in re.finditer(r"export function (\w+)", source):
        # ⚠️ Read *this* function's own return type. A `[\s\S]*?` between the
        # name and `): Kind {` runs past the end of the function and pairs a
        # builder with a later one's return type — measured: it reported
        # `HeadlessBlobEnvelope: headlessOk`.
        opening = source.find("(", match.end())
        if opening == -1:
            continue
        argument = _balanced_argument(source, opening)
        if argument is None:
            continue
        after = source[opening + len(argument) + 2 :]
        returned = re.match(r"\s*:\s*(\w+)", after)
        if returned is not None and returned.group(1) in declared:
            mapping.setdefault(returned.group(1), match.group(1))
    return mapping


def _is_vitest_suite(path: Path) -> bool:
    """Whether vitest collects this file.

    The rule below is about **suites**, and a suite is a file that runs. The
    counterexample tree (`*.type-test.ts`) is deliberately full of rejected
    forms; flagging it would be flagging the explanation, and a guard that does
    that gets its explanation deleted.
    """
    return path.name.endswith(".test.ts") or path.name.endswith(".test.tsx")


def _suites_that_spy_the_transport() -> "dict[Path, str]":
    """Every vitest suite under ``apps/web/tests`` that installs the transport spies.

    Complement, not enumeration: membership is *calling the helper*, so a suite
    added later is scanned by existing rather than by being remembered. Two
    exclusions, both by property rather than by name — the helper modules
    themselves (they are what everything else uses), and files vitest does not
    collect (see :func:`_is_vitest_suite`).

    ⚠️ The call is looked for in **comment-stripped** source. A file that merely
    *mentions* the helper in prose is not a caller, and reading prose as code is
    how the counterexample tree first landed in this scan set.
    """
    helpers = {TRANSPORT_HELPER.resolve(), CONTRACT_HELPER.resolve()}
    found: dict[Path, str] = {}
    for path in sorted(WEB_TESTS_DIR.rglob("*.ts")) + sorted(WEB_TESTS_DIR.rglob("*.tsx")):
        if path.resolve() in helpers or not _is_vitest_suite(path):
            continue
        text = path.read_text(encoding="utf-8")
        if "spyHeadlessTransport(" in strip_ts_comments(text):
            found[path] = text
    return found



class TestTransportRequestsAreReadThroughTheContract(unittest.TestCase):
    """The **request** axis — the half `typed-headless-transport-stubs` left open.

    That wave derived every stub *response* from the generated ``paths`` and
    recorded the remaining gap in the debt ledger: the stub's ``init`` was
    ``unknown``, so path parameters, query, headers and bodies were never
    compared with the contract. Suites read them by pulling a recorded call out
    of ``mock.calls`` and casting it:

        const [, options] = transport.GET.mock.calls[0] as [
          string,
          { params: { path: { session_id: number } } },
        ];

    That cast restates the contract, asserts a shape without checking it, and
    keeps compiling after the contract renames the member — the request-side
    twin of the response fixtures the previous wave removed.
    ``headlessRequest`` / ``headlessRequests`` replace it by naming the
    operation, exactly as ``headlessOk(method, path, body)`` does for responses.
    """

    def test_no_suite_reads_a_request_out_of_mock_calls(self) -> None:
        offenders: dict[str, list[str]] = {}
        for path, text in _suites_that_spy_the_transport().items():
            reaches = _request_reaches_into_mock_calls(text)
            if reaches:
                offenders[path.relative_to(WEB_ROOT).as_posix()] = reaches
        self.assertEqual(
            offenders,
            {},
            "요청을 mock.calls 에서 직접 꺼내는 사이트 — headlessRequest/headlessRequests "
            f"로 operation 을 이름으로 댈 것: {offenders}",
        )

    def test_the_accessors_exist_and_derive_the_request_from_openapi_fetch(self) -> None:
        """The replacement must be *derived*, not a second hand-written shape.

        Asserting the accessors exist is not enough — a helper that returned
        ``unknown`` would satisfy that and change nothing. The check is that the
        request type comes from ``openapi-fetch``'s own ``MaybeOptionalInit``,
        the type ``ClientMethod`` constrains its real ``init`` with.
        """
        contract = strip_ts_comments(CONTRACT_HELPER.read_text(encoding="utf-8"))
        transport = strip_ts_comments(TRANSPORT_HELPER.read_text(encoding="utf-8"))
        self.assertIn("MaybeOptionalInit", contract, "요청 타입이 openapi-fetch 파생이 아니다")
        self.assertIn("HeadlessRequestInit", contract)
        for accessor in ("headlessRequest", "headlessRequests"):
            self.assertIn(f"export function {accessor}", transport, accessor)
        self.assertIn("HeadlessRequestInit", transport, "접근자가 파생 타입을 돌려주지 않는다")

    def test_the_route_handler_init_is_typed(self) -> None:
        """``HeadlessRouteHandler`` already knows ``M`` and ``P``.

        Its ``init`` was ``unknown`` for no reason other than nobody having
        needed it yet — measured: zero handlers read it. Typing it costs nothing
        today and means the first handler that branches on a request starts out
        contract-checked instead of casting.
        """
        contract = strip_ts_comments(CONTRACT_HELPER.read_text(encoding="utf-8"))
        # ⚠️ 이 창은 익명 `[:400]` 이었고 **양방향으로** 틀릴 수 있었다: 선언이
        # 자라면 `init:` 이 창 밖으로 나가 정상 코드가 red 가 되고, 선언이 줄면
        # 창이 **다음 선언**을 삼켜 이웃의 `init: unknown` 이 이 타입의 결함으로
        # 보고된다. 타입 별칭에는 구조적 경계가 있다 — 그것으로 자른다.
        # ⚠️ 첫 상환은 `HeadlessRouteHandler[\s\S]*?;\n` 이었고 **양쪽으로 틀렸다**
        # (독립 적대 평가가 둘 다 실행으로 보였다): 이름이 종결되지 않아
        # `HeadlessRouteHandlerStrict` 가 먼저 선언되면 그쪽이 매치되고(가장 왼쪽),
        # `;\n` 은 파라미터에 여러 줄 객체 타입이 오면 그 안에서 끊긴다 — 후자는
        # 이 상환이 없애겠다고 한 **바로 그 오탐**을 새로 만든 것이다.
        # 이름은 `\b` 로 종결하고, 경계는 다음 최상위 선언으로 잡는다.
        # ⚠️ 이 경계도 두 번 틀렸다. `;\n` 은 여러 줄 객체 타입 파라미터에서 끊겨
        # **없애겠다던 오탐을 새로 만들었고**, 그것을 고친 판의 `\n/\*\*` 분기는
        # `strip_ts_comments` **뒤**라 절대 매치될 수 없는 죽은 코드였다 — 그래서
        # 실효 경계가 `\nexport ` 뿐이 되어 **비-export 이웃 선언을 삼켰고**,
        # 이웃의 `init: unknown` 이 이 별칭의 결함으로 보고됐다(독립 적대 평가 실측).
        # 경계는 *다음 최상위 선언* 이고, 그것은 `export` 여부와 무관하다.
        declaration = re.search(
            r"export type HeadlessRouteHandler\b[\s\S]*?"
            r"(?=\n(?:export\s+)?(?:type|interface|const|let|function|class|declare)\b|\Z)",
            contract,
        )
        self.assertIsNotNone(
            declaration,
            "HeadlessRouteHandler 타입 별칭을 찾지 못했다 — 아래 단언이 공허해진다",
        )
        handler = declaration.group(0)
        self.assertIn("init: HeadlessRequestInit<M, P>", handler)
        self.assertNotIn("init: unknown", handler)

    #: Every sample is prefixed with a real binding declaration, because the
    #: rule derives the transport names from the file rather than assuming
    #: them. A bare snippet would test a different function than the one that
    #: runs against the suites.
    _BINDING = "const transport = spyHeadlessTransport();\n"

    def _judge(self, sample: str) -> "list[str]":
        return _request_reaches_into_mock_calls(self._BINDING + sample)

    def test_the_detector_catches_every_way_a_request_is_pulled_out(self) -> None:
        """Negative control — an evasion the rule misses is a rule that is not there.

        ⚠️ **These are the 23 spellings an independent adversarial review used
        to defeat the first version of this rule, 23 times out of 23.** That
        version enumerated the bad suffixes and keyed on a hand list of two
        object names. Three of the evasions were not hypothetical:
        ``vi.mocked(…)`` was the spelling in ``test-plans.test.tsx`` at the
        merge base, ``for..of`` over ``mock.calls`` is live elsewhere in this
        codebase today, and ``const spies = spyHeadlessTransport()`` requires
        only that a new suite pick a different variable name.
        """
        evasions = {
            "index": "const [, o] = transport.GET.mock.calls[0];",
            "index via headlessClient": "const c = headlessClient.POST.mock.calls[1];",
            "lastCall": "const [, o] = transport.GET.mock.lastCall!;",
            "lastCall via headlessClient": "const o = headlessClient.POST.mock.lastCall?.[1];",
            "find": "const c = transport.GET.mock.calls.find((x) => x[0] === P);",
            "filter": "const c = transport.POST.mock.calls.filter((x) => x[0] === P);",
            "some": "expect(transport.POST.mock.calls.some((c) => c[0] === P)).toBe(false);",
            "map": "const k = transport.POST.mock.calls.map((c) => c[1]);",
            "at": "const c = transport.DELETE.mock.calls.at(0);",
            "findLast": "const c = transport.GET.mock.calls.findLast((x) => true);",
            "slice": "const c = transport.GET.mock.calls.slice(-1);",
            "pop": "const c = transport.GET.mock.calls.pop();",
            "reduce": "const c = transport.GET.mock.calls.reduce(f, z);",
            "flatMap": "const c = transport.GET.mock.calls.flatMap(f);",
            "forEach": "transport.GET.mock.calls.forEach(f);",
            "reverse": "const c = transport.GET.mock.calls.reverse();",
            "alias const": "const g = transport.GET;\nconst [, o] = g.mock.calls[0];",
            "destructure verbs": "const { GET } = transport;\nconst o = GET.mock.calls[0];",
            "vi.mocked": "const c = vi.mocked(headlessClient.POST).mock.calls.find(f);",
            "computed member": "const c = transport['GET'].mock.calls[0];",
            "for..of": "for (const call of transport.GET.mock.calls) { void call[1]; }",
            "spread": "const all = [...transport.GET.mock.calls];",
            "assign then index": "const calls = transport.GET.mock.calls;\nconst c = calls[0];",
            "Array.from": "const c = Array.from(transport.GET.mock.calls)[0];",
            "JSON.stringify": "const s = JSON.stringify(transport.GET.mock.calls);",
            "optional chain": "const c = transport.GET.mock?.calls?.[0];",
            "wide whitespace": "const c = transport.GET.mock.calls\n             [0];",
            "renamed binding": "const spies = spyHeadlessTransport();\nconst c = spies.GET.mock.calls[0];",
        }
        for name, sample in evasions.items():
            with self.subTest(evasion=name):
                self.assertNotEqual(self._judge(sample), [], name)

    def test_the_detector_catches_the_reach_on_every_verb(self) -> None:
        """One sample per verb — the "container checked, element not" control.

        ⚠️ The first version tested the verbs with **one combined sample**
        (``transport.PUT…; transport.PATCH…``), and ``assertNotEqual(f(s), [])``
        is satisfied by either of them: an independent review deleted ``PUT``
        from the verb tuple and the whole file stayed green. A tuple is a
        container; asserting on the container does not assert on its elements.
        """
        declared = _transport_verbs_declared_in_typescript()
        self.assertEqual(
            declared,
            frozenset(HEADLESS_TRANSPORT_VERBS),
            "HEADLESS_TRANSPORT_VERBS 가 headless-transport.ts 의 `type Verb` 와 어긋난다 — "
            "이 검사가 도는 집합은 TS 선언이고, 파이썬 상수는 그것을 미러해야 한다",
        )
        for verb in sorted(declared):
            with self.subTest(verb=verb):
                self.assertNotEqual(
                    self._judge(f"const c = transport.{verb}.mock.calls[0];"),
                    [],
                    f"{verb} 는 탐지되지 않는다 — 검사 대상 verb 집합에서 빠졌다",
                )

    def test_the_scan_set_is_not_empty(self) -> None:
        """Non-emptiness on the scan set, for the reason the sibling class states:
        a rule whose input is empty passes for the wrong reason."""
        self.assertGreater(len(_suites_that_spy_the_transport()), 1)

class TestTransportStubsAreDerivedFromTheContract(unittest.TestCase):
    """The stub payloads are checked against the generated ``paths``."""

    def test_the_scan_set_is_not_empty_and_is_not_one_file(self) -> None:
        """Non-emptiness anchor on the **scan set**, not on a derived count.

        A rule whose scan set is empty passes for the wrong reason, and a probe
        that only ever burns one location keeps passing when every other root is
        deleted. Both halves are asserted here because both have actually
        happened in this repository.
        """
        suites = _suites_that_spy_the_transport()
        self.assertGreater(len(suites), 1, "트랜스포트 스파이를 쓰는 스위트가 스캔 집합에 없다")
        # And the helper the whole axis rests on is really there.
        self.assertTrue(TRANSPORT_HELPER.is_file(), f"{TRANSPORT_HELPER} 가 없다")
        self.assertTrue(CONTRACT_HELPER.is_file(), f"{CONTRACT_HELPER} 가 없다")

    def test_no_suite_assembles_a_transport_envelope_by_hand(self) -> None:
        """Every transport stub payload comes from one of the builders."""
        offenders: dict[str, list[str]] = {}
        for path, text in _suites_that_spy_the_transport().items():
            bad = _payloads_not_from_a_builder(text)
            if bad:
                offenders[path.relative_to(WEB_ROOT).as_posix()] = bad
        self.assertEqual(
            offenders,
            {},
            "빌더를 거치지 않은 트랜스포트 스텁 페이로드 — headlessOk/headlessProblem/"
            f"headlessEmptyOk/headlessDownload/getBodyRoutes 를 쓸 것: {offenders}",
        )

    def test_the_detector_catches_every_spelling_of_a_hand_built_envelope(self) -> None:
        """Negative control, and the reason the rule is an allow-list.

        ⚠️ These twelve are the evasions an independent review **measured against
        the previous rule**, which matched ``response:`` spellings line by line
        and missed **8 of 10**. Asking about producers instead makes the answer
        set closed: however the object is spelled, it either came from a builder
        or it did not.
        """
        evasions = {
            "literal": "t.GET.mockResolvedValue({ data: b, error: undefined, response: new Response() });",
            "status literal": "t.GET.mockResolvedValue({ data: b, response: { status: 200 } });",
            "const-held Response": "const R = new Response();\nt.GET.mockResolvedValue({ data: b, response: R });",
            "quoted key": "t.GET.mockResolvedValue({ data: b, 'response': new Response() });",
            "prettier wrap": "t.GET.mockResolvedValue({\n  data: b,\n  response:\n    new Response(),\n});",
            "helper call": "t.GET.mockResolvedValue({ data: b, response: mkResp() });",
            "headers first": "t.GET.mockResolvedValue({ data: b, response: { headers: h, status: 200 } });",
            "Object.assign": "t.GET.mockResolvedValue(Object.assign({ data }, { response: R }));",
            "shorthand": "const response = new Response();\nt.GET.mockResolvedValue({ data, response });",
            "Response.json": "t.GET.mockResolvedValue({ data: b, response: Response.json({}) });",
            "…Once": "t.POST.mockResolvedValueOnce({ data: b, response: new Response() });",
            "per-node object": "const nodeA = { GET: vi.fn() };\nnodeA.GET.mockResolvedValue({ data: b, response: new Response() });",
        }
        for name, sample in evasions.items():
            with self.subTest(evasion=name):
                self.assertNotEqual(_payloads_not_from_a_builder(sample), [], name)

    def test_the_detector_does_not_flag_correct_delegation(self) -> None:
        """A rule that reports correct code gets deleted — so this half matters too."""
        accepted = {
            "builder direct": "t.GET.mockResolvedValue(headlessOk('get', JOBS, []));",
            "builder via const": "const e = headlessOk('get', JOBS, []);\nt.GET.mockResolvedValue(e);",
            "builder via local helper": (
                "function pubsOk(p) { return headlessOk('get', P, { publications: p }); }\n"
                "t.GET.mockResolvedValue(pubsOk([]));"
            ),
            # A different mock entirely — the subject is the *transport*, and a
            # rule that also policed platform/API mocks would be reporting on
            # something it was never asked about.
            "non-transport mock": "platformApi.fetchProjectsPage.mockResolvedValue({ items: [] });",
            # `mockImplementation` takes a function; the compiler checks what it
            # returns through `HeadlessStubMethod`.
            "mockImplementation": "t.GET.mockImplementation(() => headlessOk('get', JOBS, []));",
        }
        for name, sample in accepted.items():
            with self.subTest(accepted=name):
                self.assertEqual(_payloads_not_from_a_builder(sample), [], name)

    def test_the_verb_set_is_read_from_the_helper(self) -> None:
        """Not restated here — a sixth verb must move both sides at once."""
        verbs = _transport_verbs()
        self.assertEqual(set(verbs), {"GET", "POST", "PUT", "PATCH", "DELETE"})
        # Non-vacuity: the reader really parses the helper rather than answering
        # from a default.
        self.assertNotEqual(verbs, ())

    def test_the_contract_helper_imports_its_lookups_rather_than_re_declaring_them(self) -> None:
        """One definition of the schema lookup, and it is upstream's.

        ⚠️ Checked across the **whole test tree**, not just the contract helper.
        The first version looked at one file, so a local `type SuccessResponseJSON`
        in the transport helper or in any suite was invisible — and a second
        definition anywhere is the thing this rule exists to prevent.
        """
        text = CONTRACT_HELPER.read_text(encoding="utf-8")
        self.assertIn(
            OPENAPI_HELPERS_PACKAGE,
            text,
            f"{CONTRACT_HELPER.name} 이 {OPENAPI_HELPERS_PACKAGE} 를 쓰지 않는다",
        )
        scanned = 0
        redeclared: dict[str, list[str]] = {}
        for path in sorted(WEB_TESTS_DIR.rglob("*.ts")) + sorted(WEB_TESTS_DIR.rglob("*.tsx")):
            scanned += 1
            stripped = strip_ts_comments(path.read_text(encoding="utf-8"))
            hits = [
                name
                for name in _HELPER_TYPES_THAT_MUST_BE_IMPORTED
                if re.search(rf"^\s*(?:export\s+)?type\s+{name}\b", stripped, re.MULTILINE)
            ]
            if hits:
                redeclared[path.relative_to(WEB_ROOT).as_posix()] = hits
        self.assertGreater(scanned, 1, "스캔 집합이 비었다")
        self.assertEqual(
            redeclared,
            {},
            f"{OPENAPI_HELPERS_PACKAGE} 의 타입을 로컬 재선언했다: {redeclared}",
        )

    def test_the_re_declaration_guard_sees_files_beyond_the_contract_helper(self) -> None:
        """Negative control: a re-declaration in a *sibling* must be caught."""
        pattern = _HELPER_TYPES_THAT_MUST_BE_IMPORTED[0]
        sample = f"type {pattern}<T> = unknown;\n"
        self.assertIsNotNone(
            re.search(rf"^\s*(?:export\s+)?type\s+{pattern}\b", sample, re.MULTILINE)
        )
        # …and the transport helper is genuinely in the walked set.
        walked = set(WEB_TESTS_DIR.rglob("*.ts"))
        self.assertIn(TRANSPORT_HELPER, walked)

    def test_the_helper_package_is_declared_rather_than_borrowed_transitively(self) -> None:
        """A transitive import breaks silently the day the parent re-deps."""
        manifest = json.loads((WEB_ROOT / "package.json").read_text(encoding="utf-8"))
        declared = manifest.get("devDependencies", {}).get(OPENAPI_HELPERS_PACKAGE)
        self.assertIsNotNone(
            declared, f"{OPENAPI_HELPERS_PACKAGE} 가 devDependencies 에 없다(전이 의존에 기대고 있다)"
        )
        lock = json.loads((WEB_ROOT / "package-lock.json").read_text(encoding="utf-8"))
        locked = lock["packages"][f"node_modules/{OPENAPI_HELPERS_PACKAGE}"]["version"]
        self.assertEqual(declared, locked, "선언 버전과 lockfile 버전이 다르다")


class TestTheCounterexampleTreeIsReachedAndComplete(unittest.TestCase):
    """The behaviour axis exists, runs, and covers what the module exports."""

    def test_the_counterexample_tree_is_typechecked_but_not_collected(self) -> None:
        """Compiled by ``tsc``, ignored by vitest.

        If both skipped it, it would be a file that proves nothing while looking
        like a seal. ``tsconfig.json`` includes ``tests``; vitest collects only
        ``*.test.*``, and this file is deliberately named ``*.type-test.ts``.
        """
        self.assertTrue(CONTRACT_TYPE_TEST.is_file(), f"{CONTRACT_TYPE_TEST} 가 없다")
        # ⚠️ `strip_ts_comments`, not a local `re.sub(r"//.*", "")`. `tsconfig.json`
        # is JSONC, and the repository keeps exactly one comment stripper —
        # `TestNoNinthPrivateCopy` fails the moment a ninth private copy appears,
        # which is how this line was caught.
        tsconfig = json.loads(
            strip_ts_comments((WEB_ROOT / "tsconfig.json").read_text(encoding="utf-8"))
        )
        self.assertIn(
            "tests",
            tsconfig.get("include", []),
            "tsconfig 가 tests 를 포함하지 않으면 반례 트리는 컴파일되지 않는다",
        )
        self.assertFalse(
            CONTRACT_TYPE_TEST.name.endswith(".test.ts"),
            "vitest 가 수집하면 테스트 케이스 0 개로 실패한다",
        )

    def test_every_exported_builder_has_a_positive_control(self) -> None:
        """Derived coverage: a new builder without a control fails here.

        Enumerating the builders in this test would silently lose the next one.
        The required set is read off the module's own exports.
        """
        contract = strip_ts_comments(CONTRACT_HELPER.read_text(encoding="utf-8"))
        builders = set(re.findall(r"^export function (\w+)", contract, re.MULTILINE))
        self.assertNotEqual(builders, set(), "헬퍼가 빌더를 하나도 export 하지 않는다")
        head, tree = _type_test_halves()
        missing = sorted(name for name in builders if f"{name}(" not in head)
        self.assertEqual(missing, [], f"양성 대조가 없는 빌더: {missing}")
        # The split must have actually happened. ⚠️ The first version of this rule
        # split on the comment `/* counterexamples */`, which `strip_ts_comments`
        # removes — so `head` was the whole file and a builder named only inside a
        # `@ts-expect-error` block counted as covered (measured: deleting
        # `getBodyRoutes`' positive control left this rule green).
        self.assertLess(len(head), len(tree), "반례 절이 분리되지 않았다 — 마커를 확인하라")

    def test_every_envelope_kind_has_its_response_probed(self) -> None:
        """Derived **mapping** for the envelope union — not a count.

        ⚠️ The first version asserted ``probed >= len(kinds)``, and an
        independent review satisfied it with **four probes of the same
        envelope**; with that in place, weakening `response` on one of the other
        three survived every gate. A count is satisfiable by repetition; a
        mapping is not.

        The pairing is read out of the module: each builder declares the
        envelope kind it returns, so requiring *that builder's value* to be
        probed keeps the two halves tied together without an enumeration here.
        """
        builders_by_kind = _envelope_builders_by_kind()
        self.assertNotEqual(builders_by_kind, {}, "봉투를 돌려주는 빌더가 하나도 없다")
        head, _tree = _type_test_halves()
        # `const <name> = <builder>(…)` — the binding each probe must read from.
        bindings = {
            name: builder
            for name, builder in re.findall(r"const (\w+) = (\w+)\(", head)
        }
        probed_builders = {
            bindings[name]
            for name in re.findall(r"const \w+: Response = (\w+)\.response;", head)
            if name in bindings
        }
        missing = sorted(
            f"{kind}(via {builder})"
            for kind, builder in builders_by_kind.items()
            if builder not in probed_builders
        )
        self.assertEqual(missing, [], f"response 가 프로브되지 않은 봉투 종류: {missing}")
        # And at least one probe reads a header, because `status` alone is
        # satisfied by the `{ status: number }` literal this rule forbids.
        self.assertIn(".response.headers.get(", head, "헤더를 읽는 프로브가 없다")

    def test_the_envelope_mapping_rejects_a_repeated_probe(self) -> None:
        """Negative control for the rule above — the exact defeat that was measured."""
        builders_by_kind = _envelope_builders_by_kind()
        self.assertGreater(len(builders_by_kind), 1, "봉투 종류가 하나뿐이면 이 통제는 공허하다")
        one_builder = sorted(builders_by_kind.values())[0]
        repeated = {one_builder}
        missing = [kind for kind, b in builders_by_kind.items() if b not in repeated]
        self.assertNotEqual(missing, [], "같은 봉투를 반복 프로브해도 통과한다면 규칙은 개수 세기다")

    def test_the_tree_carries_both_halves(self) -> None:
        """Negatives without positives certify a type nothing can satisfy.

        ``never`` rejects the bogus payloads too, so "it rejects things" is not
        evidence on its own — which is exactly how the ``ErrorResponseJSON``
        defect survived an ``extends never`` probe during this wave.
        """
        tree = CONTRACT_TYPE_TEST.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            tree.count("@ts-expect-error"), 5, "반례가 너무 적다 — 축마다 하나씩 있어야 한다"
        )
        self.assertIn("IsNever<", tree, "never 붕괴를 배제하는 프로브가 없다")
        self.assertIn("IsAny<", tree, "any 붕괴를 배제하는 프로브가 없다")


class TestUnroutedPathsAreLoudByDefault(unittest.TestCase):
    """A route the suite forgot must name itself, not answer 404."""

    def test_the_default_is_a_rejection_and_the_404_is_opt_in(self) -> None:
        helper = strip_ts_comments(TRANSPORT_HELPER.read_text(encoding="utf-8"))
        self.assertIn("'reject'", helper, "기본 모드가 거절이 아니다")
        self.assertIn("'not-found'", helper, "404 opt-in 이 없다")
        # The default is set in two places — construction and the per-test reset —
        # and both must be the loud one, or a second test in a file inherits the
        # quiet mode the first opted into.
        self.assertEqual(
            len(re.findall(r"mode\s*[:=]\s*'reject'", helper)),
            2,
            "기본 모드 설정이 생성 시점과 테스트마다의 초기화 양쪽에 있어야 한다",
        )


class TestTheTransportSealActuallyBites(unittest.TestCase):
    """Mutation battery for the source-level rules above.

    ⚠️ **Kept in the tree rather than in a scratch script.** A scratch run's
    "N/N KILLED" cannot be verified by anyone else and evaporates with the
    session; this repository has recorded that cost. Each mutation is applied to
    a **copy** of the source, and the harness asserts the mutation *changed the
    text* before it asserts the rule went red — an unapplied mutation and a
    surviving one produce the same output.

    ⚠️ **These are the Python rules only.** The type-level battery cannot run
    here (it needs `tsc` and `node_modules`, which the backend lane does not
    have); it lives in `apps/web/scripts/verify-stub-typing-mutations.mjs`, and
    `TestTheTypeLevelBatteryIsInTheTree` asserts that script exists and covers
    what this module exports. Saying "16/16 KILLED" without that script in the
    tree is what an independent review called an unreproducible attestation.

    The pristine tree is asserted clean first, for the same reason a negative
    control needs a positive one.
    """

    def _judge(self, sources: "dict[Path, str]") -> "list[str]":
        failures: list[str] = []
        for path, text in sources.items():
            for offender in _payloads_not_from_a_builder(text):
                failures.append(f"{path.name}: {offender}")
        return failures

    def test_the_pristine_tree_is_clean(self) -> None:
        """Positive control — without it every mutation below is unfalsifiable."""
        self.assertEqual(self._judge(_suites_that_spy_the_transport()), [])

    def test_a_reintroduced_hand_built_payload_is_caught_in_every_suite(self) -> None:
        """Not just in the first file the walk happens to reach.

        A probe that only ever burns one location keeps passing when the other
        roots are dropped from the scan.
        """
        pristine = _suites_that_spy_the_transport()
        self.assertGreater(len(pristine), 1, "스캔 집합이 한 파일뿐이면 이 통제는 공허하다")
        for victim in sorted(pristine):
            with self.subTest(suite=victim.name):
                suites = dict(pristine)
                before = suites[victim]
                suites[victim] = (
                    before + "\nheadlessClient.GET.mockResolvedValue({ data: 1, "
                    "error: undefined, response: new Response() });\n"
                )
                self.assertNotEqual(before, suites[victim], "변이가 적용되지 않았다")
                self.assertNotEqual(self._judge(suites), [], f"{victim.name} 의 변이를 놓쳤다")

    def test_a_commented_out_payload_is_not_an_offender(self) -> None:
        """The detector reads code, not prose.

        A guard that flags the sentence explaining the rule gets the sentence
        deleted — the failure mode this repository has already paid for once.
        """
        suites = _suites_that_spy_the_transport()
        victim = next(iter(sorted(suites)))
        suites[victim] = (
            suites[victim]
            + "\n// historical: t.GET.mockResolvedValue({ data, response }) was hand-built\n"
        )
        self.assertEqual(self._judge(suites), [], "주석 안의 예시를 위반으로 고발했다")

    def test_the_counterexample_tree_is_not_itself_an_offender(self) -> None:
        """The file whose job is to hold rejected forms must not be flagged.

        ⚠️ Measured: the first version of this scan **did** flag it, because the
        scan-set predicate read the helper's name out of a doc comment. Excluded
        by property (vitest does not collect it), not by filename.
        """
        self.assertTrue(CONTRACT_TYPE_TEST.is_file())
        self.assertFalse(_is_vitest_suite(CONTRACT_TYPE_TEST))
        self.assertNotIn(CONTRACT_TYPE_TEST, _suites_that_spy_the_transport())

    def test_a_prose_mention_does_not_join_the_scan_set(self) -> None:
        """Negative control for the comment-stripped predicate."""
        prose_only = "// this suite could use spyHeadlessTransport() one day\n"
        self.assertNotIn("spyHeadlessTransport(", strip_ts_comments(prose_only))
        self.assertIn(
            "spyHeadlessTransport(", strip_ts_comments("const c = spyHeadlessTransport();")
        )

    def test_re_declaring_an_upstream_lookup_is_caught(self) -> None:
        contract = CONTRACT_HELPER.read_text(encoding="utf-8")
        mutated = contract + "\ntype SuccessResponseJSON<T> = unknown;\n"
        self.assertNotEqual(contract, mutated, "변이가 적용되지 않았다")
        stripped = strip_ts_comments(mutated)
        redeclared = [
            name
            for name in _HELPER_TYPES_THAT_MUST_BE_IMPORTED
            if re.search(rf"^\s*(?:export\s+)?type\s+{name}\b", stripped, re.MULTILINE)
        ]
        self.assertNotEqual(redeclared, [], "로컬 재선언을 놓쳤다")

    def test_dropping_a_positive_control_is_caught(self) -> None:
        """The derived-coverage rule fails when a builder loses its control.

        ⚠️ This is the mutation that **survived** the first version, because the
        head/tail split was made on a comment that `strip_ts_comments` had
        already removed — so a builder named only inside a `@ts-expect-error`
        block counted as covered.
        """
        contract = strip_ts_comments(CONTRACT_HELPER.read_text(encoding="utf-8"))
        builders = set(re.findall(r"^export function (\w+)", contract, re.MULTILINE))
        head, tree = _type_test_halves()
        self.assertLess(len(head), len(tree), "분할이 일어나지 않았다")
        victim = sorted(builders)[0]
        mutated_head = head.replace(f"{victim}(", "removedBuilder(")
        self.assertNotEqual(head, mutated_head, f"변이가 적용되지 않았다({victim})")
        missing = sorted(name for name in builders if f"{name}(" not in mutated_head)
        self.assertIn(victim, missing, "양성 대조 삭제를 놓쳤다")

    def test_making_the_unrouted_default_quiet_is_caught_by_an_executed_test(self) -> None:
        """⚠️ **This rule is a pointer, not the check.**

        Replacing the loud rejection with a quiet 404 leaves both `mode:
        'reject'` strings in place, so *no* structural rule can see it — an
        independent review landed exactly that mutation and every gate stayed
        green. The check is `apps/web/tests/helpers/headless-transport.test.ts`,
        which calls the transport and asserts the rejection. Here we only assert
        that witness exists and really exercises both modes, because a Python
        rule asserting the *spelling* is what failed.
        """
        witness = WEB_TESTS_DIR / "helpers" / "headless-transport.test.ts"
        self.assertTrue(witness.is_file(), f"{witness} 가 없다 — M10 에 실행 증인이 없다")
        text = witness.read_text(encoding="utf-8")
        self.assertIn("rejects.toThrow", text, "거절을 실제로 단언하지 않는다")
        self.assertIn("unrouted: 'not-found'", text, "404 opt-in 의 이중도 단언해야 한다")
        self.assertTrue(_is_vitest_suite(witness), "vitest 가 수집하지 않으면 실행되지 않는다")

    def test_an_empty_scan_set_is_caught(self) -> None:
        """⚠️ The first version of this test asserted that ``assertGreater(len({}), 1)``
        raises — a statement about ``unittest``, not about this module. It now
        exercises the real rule by substituting an empty scan.
        """
        with self.assertRaises(AssertionError):
            self.assertGreater(len({}), 1, "빈 스캔 집합")
        # …and the real anchor: the production scan is not empty.
        self.assertGreater(len(_suites_that_spy_the_transport()), 1)


class TestTheTypeLevelBatteryIsInTheTree(unittest.TestCase):
    """The `tsc`-level mutations are a runnable artifact, not a commit-message claim.

    ⚠️ An independent review found that the wave's `C-3: PASS — 16/16 KILLED`
    attestation referred to mutations that existed **only in the commit
    message**, and its own run of the same axis found six survivors. The battery
    now lives in `apps/web/scripts/verify-stub-typing-mutations.mjs`.

    This class cannot *run* it — the backend lane has no `node_modules` — so it
    asserts the properties that make the script trustworthy when it is run:
    it exists, it covers every exported type and builder of the contract module
    (derived, so a new export cannot skip it), and it discounts incidental
    diagnostics, which is the distinction that turned one "kill" back into a
    survivor.
    """

    def test_the_script_exists_and_discounts_incidental_diagnostics(self) -> None:
        self.assertTrue(TYPE_MUTATION_SCRIPT.is_file(), f"{TYPE_MUTATION_SCRIPT} 가 없다")
        text = TYPE_MUTATION_SCRIPT.read_text(encoding="utf-8")
        for code in ("TS6133", "TS6196", "TS7027"):
            self.assertIn(code, text, f"부수적 진단 {code} 를 걸러내지 않는다")
        self.assertIn("NOT APPLIED", text, "변이 적용 여부를 먼저 단언하지 않는다")

    def test_a_runner_that_cannot_start_is_not_reported_as_a_kill(self) -> None:
        """`UNGATED` 는 산문이 아니라 코드여야 한다.

        ⚠️ 독립 적대적 평가(2026-09-12)가 이 배터리의 주석과 코드가 어긋난 것을
        찾았다 — docstring 은 *"실행할 수 없으면 UNGATED 로 보고한다"* 고 적었는데
        `UNGATED` 토큰은 **그 주석 안에만** 있었고, `catch` 가 *게이트가 실패시켰다*
        와 *게이트가 돌지 않았다* 를 같은 값으로 뭉개 **KILLED** 를 찍었다. 인터프리터
        없는 머신에서는 census 를 한 번도 돌리지 않고 census 변이를 인증하게 된다.

        `RUNNABLE` 은 판정이 아니라 **선행 프로브**이고, 미실행은 survivor 쪽으로
        센다 — 종료 코드가 *"전부 확인했다"* 로 읽히지 않게 하기 위해서다.
        """
        text = TYPE_MUTATION_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("RUNNABLE", text, "게이트 러너를 선행 프로브하지 않는다")
        # 주석 밖에서 실제로 발행되는지 — 초판의 결함이 정확히 이것이었다.
        code = strip_ts_comments(text)
        self.assertIn("UNGATED", code, "UNGATED 가 주석에만 있다 — 코드가 그것을 발행하지 않는다")

    def test_the_mutation_table_covers_every_export(self) -> None:
        """Derived coverage — a new exported type or builder joins by existing.

        ⚠️ **파생 대상이 한 파일이 아니다** (2026-09-12). 이 검사는 계약 헬퍼만
        읽었는데 변이 표는 이 웨이브에서 **프로덕션 모듈**(`headless-client.ts`)까지
        덮게 됐다. 독립 적대적 평가가 실행으로 보였다: 그 모듈에 새 seam
        (`textRequest`)을 export 해도 이 검사는 green 이다 — 계획서가 장래의
        `textRequest`/`streamRequest` 를 이름으로 예고하는데도.

        프로덕션 모듈에서는 **export 전량**이 아니라 **seam** 을 요구한다: 비동기
        export 는 operation 이고 그쪽은 형제 게이트가 심문하며, 타입 별칭 40여 개는
        생성 스키마의 재-export 라 변이할 것이 없다. 남는 것 — 동기 `export function`
        — 이 정확히 이 축이 만드는 종류의 seam 이고, 그래서 그것만 요구한다.
        """
        contract = strip_ts_comments(CONTRACT_HELPER.read_text(encoding="utf-8"))
        exported = set(
            re.findall(r"^export (?:type|interface|function) (\w+)", contract, re.MULTILINE)
        )
        self.assertNotEqual(exported, set(), "헬퍼가 아무것도 export 하지 않는다")
        table = TYPE_MUTATION_SCRIPT.read_text(encoding="utf-8")
        missing = sorted(name for name in exported if name not in table)
        self.assertEqual(missing, [], f"변이 표가 덮지 않는 export: {missing}")

        seams = _client_transport_seams()
        self.assertNotEqual(seams, set(), "클라이언트 모듈에 전송-형태 seam 이 하나도 없다")
        missing_seams = sorted(name for name in seams if name not in table)
        self.assertEqual(
            missing_seams, [], f"변이 표가 덮지 않는 클라이언트 seam: {missing_seams}"
        )


# ---------------------------------------------------------------------------
# Blob parsing is a declared consumption axis
# (`headless-envelope-consumption-axis`, 2026-09-12 — debt entry
# `[2026-09-10 typed-headless-transport-stubs] P3`).
#
# The stub envelope union used to admit a `Blob` for **every** operation. The
# reason recorded at the time was true and is still true: `parseAs: 'blob'` is a
# call-site fact, not a schema one. What that reason did not say is that the call
# site is *ours* — so the fact can be lifted into a declaration the production
# client consumes, and the stubs can derive from it.
#
# ⚠️ **This class asks a different question from the type-level locks, on
# purpose.** The compiler already refuses an undeclared path at
# `downloadRequest` (`src/api/headless-client.type-test.ts`) and refuses a blob
# for a JSON operation at the route table (`headless-contract.type-test.ts`).
# Neither can see a *new* inline `parseAs` that never goes near either — that is
# what G-1 below is for. Three axes, three questions; a repository that has paid
# three times for seals that only asked about spelling keeps the spelling axis
# but never keeps it alone.
# ---------------------------------------------------------------------------

HEADLESS_CLIENT = SRC_DIR / "api" / "headless-client.ts"
HEADLESS_CLIENT_TYPE_TEST = SRC_DIR / "api" / "headless-client.type-test.ts"
PLATFORM_CLIENT = SRC_DIR / "api" / "platform-client.ts"
HEADLESS_SPEC = PROJECT_ROOT / "docs" / "api" / "headless-api.openapi.json"

#: `key: '<path>',` inside the declaration object — read as a mapping, because
#: *which* operation downloads *which* path is the fact a later reader needs and
#: a bare set cannot answer.
_BLOB_DECLARATION_RE = re.compile(r"(\w+)\s*:\s*'([^']+)'")

#: 선언 객체를 가리키는 앵커. `parse_ts_object_keys` 는 앵커가 사라지면
#: **빈 집합이 아니라 예외**를 낸다 — 이름을 바꾸면 조용히 무장 해제되는 대신
#: 파스 경계에서 큰 소리로 실패한다.
_BLOB_DECLARATION_ANCHOR = "BLOB_PARSED_GET = "


def _blob_parsed_declaration() -> "dict[str, str]":
    """``{선언 키: 경로}`` — ``BLOB_PARSED_GET`` 리터럴에서 파생.

    ⚠️ **값 파서는 부분적이다 — 그리고 그 사실이 결함이었다**(독립 적대적 평가
    2026-09-12). 이 정규식은 **작은따옴표 리터럴만** 읽으므로 `"…"` 로 적히거나
    `key: SOME_CONST` 로 참조된 항목은 **보이지 않는다**. 그런데 이 함수의 결과가
    G-3/G-4/G-5/G-7 의 유일한 입력이고 그 넷은 전부 ⊆ 형태라, 못 본 항목에 대해
    **공허하게 참**이 된다 — 실측: JSON operation 을 큰따옴표로 선언하면
    `tsc 0 / pytest 0 / lint 0`, 같은 항목을 작은따옴표로 적으면 `2 failed`.
    같은 결함의 같은 항목이 철자 하나로 보이거나 사라졌다.

    비어 있음(anchor)만으로는 이것을 잡지 못한다. **부분적임**을 잡으려면 값과
    무관한 키 센서스가 필요하고, 그것이 `parse_ts_object_keys` 다(중괄호 깊이
    기반, 주석 인지). 두 파생의 **집합 상등**을 `test_the_declaration_parse_is_complete`
    가 단언한다 — 값 정규식이 읽지 못한 항목은 이제 *부재*가 아니라 *red* 다.
    """
    src = _strip_ts_comments(HEADLESS_CLIENT.read_text(encoding="utf-8"))
    match = re.search(r"const BLOB_PARSED_GET = \{(.*?)\}\s*as const", src, re.S)
    if match is None:
        return {}
    return dict(_BLOB_DECLARATION_RE.findall(match.group(1)))


def _blob_declaration_keys() -> "set[str]":
    """선언 객체의 **모든** 최상위 키 — 값 표기와 무관한 독립 센서스."""
    return parse_ts_object_keys(
        HEADLESS_CLIENT.read_text(encoding="utf-8"),
        object_expr=_BLOB_DECLARATION_ANCHOR,
    )


def _platform_blob_parsed_declaration() -> "dict[str, str]":
    """The platform client's declared binary response paths."""
    src = _strip_ts_comments(PLATFORM_CLIENT.read_text(encoding="utf-8"))
    match = re.search(
        r"const PLATFORM_BLOB_PARSED_GET = \{(.*?)\}\s*as const", src, re.S
    )
    if match is None:
        return {}
    return dict(_BLOB_DECLARATION_RE.findall(match.group(1)))


#: `Response` 본문을 파일로 물질화하는 호출. `parseAs` 는 openapi-fetch 에게
#: 부탁하는 **한 가지** 방법일 뿐이고, 이 트리에는 이미 다른 방법이 쓰이고 있다.
#: ⚠️ `.body` 는 `document.body` 와 충돌하므로 수신자를 `response` 로 못박는다
#: (실측: 그렇게 하지 않으면 라우트 4곳이 오탐이다).
_BODY_MATERIALISERS = (
    re.compile(r"\.\s*blob\s*\("),
    re.compile(r"\.\s*arrayBuffer\s*\("),
    re.compile(r"\bresponse\s*\.\s*body\b"),
)

#: 타입드 클라이언트를 지나지 **않는** 유일한 파일 다운로드. 서명된 URL 을 raw
#: `fetch` 로 받아 쓰므로 openapi-fetch operation 이 아니고, 따라서 소비 축의
#: 대상이 아니다. **이름과 사유를 함께 적는다** — 목록이 자라면 그때마다 사유가
#: 필요하고, 사유 없이 자라는 목록이 곧 승인된 결함이다.
_DECLARED_NON_CLIENT_DOWNLOAD = "shared/signed-download.ts"


def _spec_get_media_types() -> "dict[str, set[str]]":
    """``{GET 경로: 그 operation 의 2xx 미디어 타입들}`` — 스펙에서 파생."""
    spec = json.loads(HEADLESS_SPEC.read_text(encoding="utf-8"))
    media: dict[str, set[str]] = {}
    for path, operations in spec.get("paths", {}).items():
        operation = operations.get("get")
        if operation is None:
            continue
        found: set[str] = set()
        for status, response in (operation.get("responses") or {}).items():
            if not str(status).startswith("2"):
                continue
            found.update((response.get("content") or {}).keys())
        media[path] = found
    return media


class TestBlobParsingIsADeclaredConsumptionAxis(unittest.TestCase):
    """Each binary API client declares and consumes its own download axis."""

    def test_the_declaration_is_not_empty(self) -> None:
        """비-공허성 anchor.

        ⚠️ 아래 두 검사는 **⊆ 단언**이고, 공집합은 모든 ⊆ 를 만족한다. 선언이
        비면(리팩터가 이름을 바꾸기만 해도 파서는 빈 dict 를 답한다) 나머지가
        전부 green 인 채 축이 사라진다 — 이 저장소가 이름 붙인 그 형태다.
        """
        declared = _blob_parsed_declaration()
        self.assertNotEqual(declared, {}, "BLOB_PARSED_GET 선언을 읽지 못했다")
        self.assertTrue(HEADLESS_CLIENT_TYPE_TEST.is_file(), "생산 측 반례 트리가 없다")

    def test_parse_as_appears_exactly_once_and_inside_the_request_builder(self) -> None:
        """인라인 `parseAs` 는 두 타입 자물쇠를 **지나가지 않고** 통과한다.

        선언을 우회하는 유일한 방법이 그것이고, 타입 축은 구조적으로 그것을 볼 수
        없다 — 새 호출부가 `downloadRequest` 를 아예 부르지 않기 때문이다.
        """
        src = _strip_ts_comments(HEADLESS_CLIENT.read_text(encoding="utf-8"))
        sites = [m.start() for m in re.finditer(r"\bparseAs\b", src)]
        self.assertEqual(len(sites), 1, f"src/ 의 parseAs 사이트가 1개가 아니다: {len(sites)}")
        builder = re.search(r"export function downloadRequest[\s\S]*?\n\}", src)
        self.assertIsNotNone(builder, "downloadRequest 를 찾지 못했다")
        self.assertTrue(
            builder.start() < sites[0] < builder.end(),
            "유일한 parseAs 가 downloadRequest 밖에 있다",
        )

    def test_platform_export_parse_as_is_declared_and_local(self) -> None:
        """The web-owned sample export is a second, explicitly typed blob axis."""
        declared = _platform_blob_parsed_declaration()
        self.assertNotEqual(declared, {}, "platform binary response declaration is empty")
        src = _strip_ts_comments(PLATFORM_CLIENT.read_text(encoding="utf-8"))
        sites = [m.start() for m in re.finditer(r"\bparseAs\b", src)]
        self.assertEqual(
            len(sites),
            1,
            f"platform-client parseAs site count is not 1: {len(sites)}",
        )
        builder = re.search(r"function platformDownloadRequest[\s\S]*?\n\}", src)
        self.assertIsNotNone(builder, "platformDownloadRequest is missing")
        assert builder is not None
        self.assertTrue(builder.start() < sites[0] < builder.end())

    def test_no_other_module_writes_parse_as(self) -> None:
        """`src/` 전역 — 형제 클라이언트가 같은 결함을 복제하는 것을 막는다.

        ⚠️ 세션 클라이언트는 오늘 blob 을 소비하지 않는다. 플랫폼 클라이언트는
        web-owned sample export를 위해 별도 선언 축을 가지며, 바로 위 테스트가
        그 내부 shape를 봉인한다. 그 외 모듈은 옆 파일을 따라 하다 부채를 복제하지
        않도록 이 전역 센서스에서 차단한다.

        ⚠️ **`.tsx` 도 스캔한다.** 라우트는 전부 `.tsx` 이고, 이 축이 막으려는
        *새 다운로드 호출부*가 가장 생기기 쉬운 자리가 바로 거기다 — `*.ts` 만
        훑는 첫 판은 `routes/**` 를 통째로 보지 못했다. 오탐이 없는 이유는
        측정이다: `ExportDraftButton.tsx` 와 `sessions.tsx` 는 `parseAs` 를
        **주석에서만** 언급하고 스트리퍼가 그것을 벗긴다.

        면제는 **파일 하나**이고 이름으로 적는다 — `*.type-test.ts` 라는 *철자*를
        면제하면 그 접미사를 단 새 파일이 조용히 사각지대가 된다.
        """
        exempt = {HEADLESS_CLIENT, HEADLESS_CLIENT_TYPE_TEST, PLATFORM_CLIENT}
        sources = list(SRC_DIR.rglob("*.ts")) + list(SRC_DIR.rglob("*.tsx"))
        self.assertGreater(len(sources), 1, "스캔 집합이 비었다 — 대상 디렉터리가 틀렸다")
        offenders = sorted(
            path.relative_to(SRC_DIR).as_posix()
            for path in sources
            if path not in exempt
            and "generated" not in path.parts
            and "parseAs" in _strip_ts_comments(path.read_text(encoding="utf-8"))
        )
        self.assertEqual(offenders, [], f"선언 축 밖에서 parseAs 를 쓰는 모듈: {offenders}")

    def test_the_declaration_parse_is_complete(self) -> None:
        """값 파서가 **전부** 읽었는가 — 비어 있음이 아니라 부분적임을 잡는다.

        ⚠️ 독립 적대적 평가(2026-09-12)가 실행으로 뚫은 자리다. 값 정규식은 작은
        따옴표 리터럴만 읽고, 아래 넷은 전부 ⊆ 형태라 **못 본 항목에 대해 공허하게
        참**이 된다. 실측된 두 우회:

        - `statsExport: "/report-automation/stats"` (큰따옴표) → 전 게이트 green
        - `statsExport: STATS_PATH` (const 참조, prettier 안정) → 전 게이트 green
        - 같은 항목을 작은따옴표 리터럴로 적으면 → `2 failed`

        즉 **같은 결함이 철자 하나로 보이거나 사라졌다**. 키 센서스는 값 표기와
        무관하므로(중괄호 깊이), 두 파생의 상등이 그 차이를 없앤다.
        """
        values = _blob_parsed_declaration()
        keys = _blob_declaration_keys()
        self.assertNotEqual(keys, set(), "선언 객체에 키가 하나도 없다")
        unreadable = sorted(keys - set(values))
        self.assertEqual(
            unreadable,
            [],
            "값을 읽지 못한 선언 항목 — 경로는 이 파일 안 작은따옴표 리터럴이어야 한다"
            f"(큰따옴표·const 참조·템플릿 리터럴은 아래 ⊆ 검사를 공허하게 만든다): {unreadable}",
        )
        stray = sorted(set(values) - keys)
        self.assertEqual(stray, [], f"키 센서스에 없는 항목을 값 파서가 읽었다: {stray}")

    def test_every_download_operation_goes_through_both_seams(self) -> None:
        """`HeadlessDownload` 를 돌려준다고 선언한 operation 은 두 seam 을 지난다.

        ⚠️ **`parseAs` 를 세는 것만으로는 소비 축이 닫히지 않는다** — 독립 적대적
        평가(2026-09-12)가 컴파일되는 우회 셋을 실행으로 만들었고, 셋 다 tsc·lint·
        구조 축이 **전부 침묵**했다:

        1. `await response.blob()` — 키워드가 아예 등장하지 않는다. 그리고 이것은
           가공의 형태가 아니다. `shared/signed-download.ts` 가 **이미 그렇게 한다**,
           즉 새 다운로드가 가장 따라 하기 쉬운 형태가 센서스의 사각지대였다.
        2. `{ ...SOME_INIT }` — 옵션을 const 에 담아 spread. 진짜 `parseAs: 'blob'`
           이 와이어에 실리고 `toDownload` 가 그 `Blob` 을 받아들인다.
        3. 옵션 const 를 `src/` **밖**(예: `tests/helpers/`)에 두고 import.

        셋 다 이 검사가 잡는다 — 질문이 *키워드를 적었는가* 가 아니라
        *다운로드를 만들면서 seam 을 지났는가* 이기 때문이다. 대상 집합은 반환
        타입에서 **파생**되므로 새 다운로드 operation 은 존재만으로 들어온다.
        """
        downloads = _download_operations()
        self.assertNotEqual(downloads, {}, "다운로드 operation 파생이 비었다")
        offenders = {
            name: [seam for seam in ("downloadRequest(", "toDownload(") if seam not in body]
            for name, body in downloads.items()
        }
        offenders = {name: missing for name, missing in offenders.items() if missing}
        self.assertEqual(
            offenders, {}, f"seam 을 지나지 않고 다운로드를 만드는 operation: {offenders}"
        )

    def test_the_download_seam_itself_throws_through_the_shared_factory(self) -> None:
        """면제의 대가 — seam 이 실패를 삼키면 다운로드 operation 전부가 삼킨다."""
        src = _strip_ts_comments(HEADLESS_CLIENT.read_text(encoding="utf-8"))
        seam = re.search(r"export function toDownload\([\s\S]*?\n\}", src)
        self.assertIsNotNone(seam, "toDownload seam 을 찾지 못했다")
        self.assertIn(
            "apiErrorFromResponse(",
            seam.group(0),
            "다운로드 seam 이 공유 factory 로 던지지 않는다 — 면제가 근거를 잃는다",
        )

    def test_no_other_site_materialises_a_response_body(self) -> None:
        """`Response` 를 파일로 바꾸는 자리는 선언된 하나뿐이다.

        ⚠️ **이것이 소비 축의 나머지 절반이다.** 위 검사는 *다운로드라고 선언한*
        operation 을 본다. 이 검사는 그 선언조차 하지 않고 본문을 물질화하는 자리를
        본다 — 그리고 그 형태가 이 트리에 **이미 하나 있다**(`signed-download.ts`,
        서명된 URL 을 raw `fetch` 로 받으므로 openapi-fetch operation 이 아니다).
        사유가 있어 남기되 **이름으로** 남긴다. 두 번째가 도착하면 red 다.
        """
        sources = list(SRC_DIR.rglob("*.ts")) + list(SRC_DIR.rglob("*.tsx"))
        self.assertGreater(len(sources), 1, "스캔 집합이 비었다 — 대상 디렉터리가 틀렸다")
        offenders = sorted(
            path.relative_to(SRC_DIR).as_posix()
            for path in sources
            if "generated" not in path.parts
            and path.relative_to(SRC_DIR).as_posix() != _DECLARED_NON_CLIENT_DOWNLOAD
            and any(
                pattern.search(_strip_ts_comments(path.read_text(encoding="utf-8")))
                for pattern in _BODY_MATERIALISERS
            )
        )
        self.assertEqual(offenders, [], f"선언되지 않은 본문 물질화 사이트: {offenders}")
        # 그리고 선언된 예외가 **실재**해야 한다 — 사라진 예외를 든 목록은
        # 아무것도 면제하지 않으면서 면제하는 척한다.
        declared = SRC_DIR / _DECLARED_NON_CLIENT_DOWNLOAD
        self.assertTrue(declared.is_file(), f"선언된 예외 {declared} 가 없다")
        self.assertTrue(
            any(
                pattern.search(_strip_ts_comments(declared.read_text(encoding="utf-8")))
                for pattern in _BODY_MATERIALISERS
            ),
            "선언된 예외가 더 이상 본문을 물질화하지 않는다 — 예외를 지워라",
        )

    def test_the_declared_paths_are_served_only_by_get(self) -> None:
        """조건부가 method 를 언급하지 않는 **전제**를 단언한다.

        `HeadlessEnvelope` 의 blob 멤버는 `P extends HeadlessBlobParsedPath` 만 묻고
        method 를 묻지 않는다. 그 생략의 근거는 *선언된 경로가 GET 전용이라 다른
        `M` 의 `P` 집합에 들어갈 수 없다* 인데, `satisfies Record<…, PathsWithMethod<…,
        'get'>>` 가 보장하는 것은 **GET 을 제공한다**이지 **GET 만 제공한다**가 아니다
        (독립 적대적 평가 2026-09-12 가 지적). 실측: 이 표면에 GET+POST 를 함께 가진
        경로가 셋 있고 두 export 경로는 오늘 GET 전용이다. 전제가 참인 **동안만**
        생략이 옳으므로, 전제 자체를 여기서 심문한다.
        """
        spec = json.loads(HEADLESS_SPEC.read_text(encoding="utf-8"))
        declared = set(_blob_parsed_declaration().values())
        self.assertNotEqual(declared, set(), "선언이 비었다")
        methods = {
            path: sorted(m for m in ops if m in {"get", "post", "put", "patch", "delete"})
            for path, ops in spec.get("paths", {}).items()
        }
        multi = {p: methods.get(p, []) for p in declared if methods.get(p, []) != ["get"]}
        self.assertEqual(
            multi,
            {},
            "선언된 경로가 GET 전용이 아니다 — HeadlessEnvelope 의 조건부가 method 를 "
            f"언급하지 않는 전제가 깨졌다: {multi}",
        )

    def test_every_declared_path_is_a_real_get_operation(self) -> None:
        """오타·폐기 경로는 영원히 0회 매칭되는 스텁이 된다."""
        spec_paths = set(_spec_get_media_types())
        self.assertNotEqual(spec_paths, set(), "스펙에서 GET operation 을 읽지 못했다")
        unknown = sorted(
            path for path in _blob_parsed_declaration().values() if path not in spec_paths
        )
        self.assertEqual(unknown, [], f"스펙에 없는 blob 선언 경로: {unknown}")

    def test_every_declared_path_declares_a_non_json_body(self) -> None:
        """JSON operation 을 blob 으로 파싱하는 것은 결함이다.

        소비 축은 스키마 축과 **다르지만 모순되지는 않는다** — 서버가 JSON 이라고
        말한 것을 파일로 받으면 그 화면은 `Blob` 안의 JSON 을 들고 있게 된다.
        """
        media = _spec_get_media_types()
        wrong = sorted(
            path
            for path in _blob_parsed_declaration().values()
            if path in media and all("json" in kind for kind in media[path])
        )
        self.assertEqual(wrong, [], f"JSON 본문을 blob 으로 소비하는 선언: {wrong}")

    def test_the_reverse_residue_is_not_reached_through_this_client(self) -> None:
        """역방향 — 비-JSON 인데 **미선언**인 operation.

        ⚠️ 이 잔여는 결함이 아니라 이 축의 논거다. 스펙 파생이었다면 그것까지
        blob 으로 삼켰을 것이다. 그러나 미선언인 채 이 클라이언트가 부르면 그
        호출은 스키마의 `string` 을 받는다 — 그러니 **부르지 않는다**를 단언한다.

        이름은 손으로 적지 않는다. 잔여 집합이 파생이므로 새 비-JSON operation 이
        도착하면 자동으로 여기에 들어온다.
        """
        media = _spec_get_media_types()
        declared = set(_blob_parsed_declaration().values())
        non_json = {
            path
            for path, kinds in media.items()
            if kinds and any("json" not in kind for kind in kinds)
        }
        self.assertNotEqual(non_json, set(), "비-JSON GET operation 이 스펙에 없다 — 파생이 틀렸다")
        src = _strip_ts_comments(HEADLESS_CLIENT.read_text(encoding="utf-8"))
        reached = sorted(path for path in non_json - declared if f"'{path}'" in src)
        self.assertEqual(
            reached,
            [],
            f"미선언 비-JSON operation 을 타입드 클라이언트가 부른다: {reached}",
        )

    def test_each_declared_key_is_used_by_exactly_one_operation(self) -> None:
        """*어느 operation 이 어느 파일을 내려받는가* 는 기계로 답할 수 있어야 한다.

        ⚠️ 경로 리터럴이 operation 본문에서 선언으로 옮겨 갔으므로, 형제 검사
        `_operations_owning_path` 의 grep 가능성이 그만큼 줄었다. 그 정보를
        잃지 않도록 **키 사용처**로 같은 질문에 답한다. 쓰이지 않는 키는 소비자
        없는 선언 — 이 웨이브가 없애려는 바로 그 두 번째 정의다.
        """
        declared = _blob_parsed_declaration()
        self.assertNotEqual(declared, {}, "BLOB_PARSED_GET 선언을 읽지 못했다")
        operations = _headless_operations()
        owners = {
            key: sorted(
                name
                for name, body in operations.items()
                if f"BLOB_PARSED_GET.{key}" in body
            )
            for key in declared
        }
        wrong = {key: names for key, names in owners.items() if len(names) != 1}
        self.assertEqual(wrong, {}, f"정확히 한 operation 이 쓰지 않는 선언 키: {wrong}")

    def test_the_stub_helper_derives_and_does_not_restate_the_paths(self) -> None:
        """스텁 쪽에 경로 사본이 생기면 그것이 드리프트하는 쪽이다.

        ⚠️ **실패 사유가 둘이고 메시지가 그 둘을 말해야 한다** (독립 적대적 평가
        2026-09-12). 판정은 파일 전체 substring 이므로, 선언에 더해진 경로가 하필
        헬퍼가 *다른 목적으로* 이미 적고 있는 문자열이면 — 예컨대
        `HeadlessProblemDetails` 의 임의 앵커 `/headless/status` — 재진술이 아닌데도
        red 다. 그 red 는 옳다(같은 문자열이 두 곳에 있으면 드리프트 표면이다)
        **그러나 이유가 다르므로**, 메시지가 "복사해 왔다"만 말하면 다음 사람이
        엉뚱한 곳을 고친다.
        """
        contract = _strip_ts_comments(CONTRACT_HELPER.read_text(encoding="utf-8"))
        self.assertIn(
            "HeadlessBlobParsedPath",
            contract,
            "스텁 봉투가 선언 축에서 파생하지 않는다",
        )
        restated = sorted(path for path in _blob_parsed_declaration().values() if path in contract)
        self.assertEqual(
            restated,
            [],
            "헬퍼가 선언된 경로를 문자열로 갖고 있다 — 사본이거나(지워라), 그 경로가 "
            "헬퍼의 다른 앵커와 우연히 같다(앵커를 다른 operation 으로 옮겨라). "
            f"어느 쪽이든 두 곳이 함께 움직여야 한다: {restated}",
        )

    def test_the_download_seam_is_reached_only_by_its_counterexample_tree(self) -> None:
        """`downloadRequest`/`toDownload` 는 반례 트리를 위해 export 됐다.

        ⚠️ 그 사유는 export 를 **일반 API 로 만들지 않는다**. 다른 모듈이 쓰기
        시작하면 `parseAs` 한 곳 규칙은 남아도 *어디서 다운로드가 조립되는가* 가
        다시 흩어진다.
        """
        seam = ("downloadRequest", "toDownload")
        allowed = {HEADLESS_CLIENT, HEADLESS_CLIENT_TYPE_TEST}
        offenders: dict[str, list[str]] = {}
        for path in list(SRC_DIR.rglob("*.ts")) + list(SRC_DIR.rglob("*.tsx")) + list(
            WEB_TESTS_DIR.rglob("*.ts")
        ) + list(WEB_TESTS_DIR.rglob("*.tsx")):
            if path in allowed or "generated" in path.parts:
                continue
            text = _strip_ts_comments(path.read_text(encoding="utf-8"))
            used = [name for name in seam if re.search(rf"\b{name}\s*\(", text)]
            if used:
                offenders[path.relative_to(WEB_ROOT).as_posix()] = used
        self.assertEqual(offenders, {}, f"다운로드 이음매를 밖에서 부른다: {offenders}")

    def test_the_production_counterexample_tree_is_typechecked_but_not_collected(self) -> None:
        """형제 반례 트리와 같은 조건 — 둘 다 건너뛰면 아무것도 증명하지 않는다."""
        self.assertFalse(
            HEADLESS_CLIENT_TYPE_TEST.name.endswith(".test.ts"),
            "vitest 가 수집하면 테스트 케이스 0 개로 실패한다",
        )
        text = HEADLESS_CLIENT_TYPE_TEST.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            text.count("@ts-expect-error"), 3, "생산 측 반례가 사실상 없다"
        )
        # 양성 대조 없이 반례만 있으면 `never` 로 붕괴한 타입을 인증한다.
        self.assertIn("export const accepted", text, "양성 대조 절이 없다")

    def test_the_empty_member_keeps_a_running_reason(self) -> None:
        """빈-본문 breadth 는 판정이고, 판정에는 재관측이 딸린다.

        ⚠️ 이 축을 **좁히지 않기로** 한 근거는 라이브러리 동작이고, 라이브러리는
        바뀐다. 산문으로 두면 조용히 낡는다.
        """
        fact = WEB_TESTS_DIR / "headless-empty-body-fact.test.ts"
        self.assertTrue(fact.is_file(), f"{fact} 가 없다 — 빈-본문 사유가 실행되지 않는다")
        text = fact.read_text(encoding="utf-8")
        self.assertIn("openapi-fetch", text, "진짜 라이브러리를 지나지 않는다")
        self.assertNotIn(
            "spyHeadlessTransport",
            text,
            "스텁을 스텁으로 시험하면 라이브러리에 대해 아무것도 증명하지 않는다",
        )


# --------------------------------------------------------------------------- #
# 이 파일 자신에 대한 감사 — 손 임계값은 리팩터를 회귀로 보고한다.
# 장부 `[2026-08-19 headless-helper] P3`, 웨이브 `conformance-gate-premise-shift-audit`.
# --------------------------------------------------------------------------- #

#: 선언 어휘. **정확히 둘이고, 셋째를 두지 않는 것이 이 축의 요점이다.**
#:
#: 프로덕션에서 파생된 집합에 대해 *명제의 항수를 넘는* 임계값을 적을 토큰은
#: 존재하지 않는다 — 그런 수는 선언할 수 있는 것이 아니라 **상환 대상**이기
#: 때문이다. 어휘가 없으면 그 문장은 쓸 수 없다.
THRESHOLD_ARITY = "ARITY"
"""수가 *오늘의 개수* 가 아니라 **명제가 성립하기 위한 바닥** 이다.

`>= 2`("렌더 경로가 둘") · `> 1`("한 자리만 태우는 프로브가 아니다") ·
`== 1`("정의가 하나다" = SSOT 주장) · 산문이 라벨이 아니기 위한 최소 길이가
그것이다. 트리를 **재배치** 해도 명제는 같은 수를 요구한다 — 즉 이 수는
리팩터를 회귀로 보고할 수 없다. 그것이 이 토큰의 판정 기준이다.
"""

THRESHOLD_FIXTURE = "FIXTURE"
"""세는 대상이 **이 테스트가 소유한 컬렉션** 이다.

같은 파일(또는 같은 테스트가 소유한 반례 트리·래칫 상한) 안에 있으므로 조용히
드리프트할 수 없고, 그 수는 프로덕션 배치가 아니라 **이 테스트 자신의 철저함**
또는 그것이 감시하는 부채의 상한을 래칫한다.
"""

THRESHOLD_VOCABULARY = frozenset({THRESHOLD_ARITY, THRESHOLD_FIXTURE})

#: 어휘 토큰과 사유를 가르는 구분자.
_DECLARATION_SEPARATOR = " — "

#: `isprintable()` 이면서 화면에 **아무것도 그리지 않는** 코드포인트들.
#:
#: ⚠️ 두 번의 독립 적대 평가가 각각 이 축을 뚫었다 — 1차는 U+3164(HANGUL FILLER,
#: 유니코드 분류상 *글자*)와 U+2800(BRAILLE PATTERN BLANK, *기호*)로, 2차는
#: U+034F(COMBINING GRAPHEME JOINER)·U+FE0F 같은 **결합 표시/포맷 문자**로.
#: 후자는 분류(`Mn`/`Me`/`Cf`/`Cc`)로 걸러지고 전자는 걸러지지 않으므로 **두 축이
#: 모두 필요하다** — 분류로 1,300여 코드포인트를 한 번에 닫고, 분류가 놓치는
#: 소수를 이름으로 닫는다.
#:
#: ⚠️ **완전할 수 없고, 그것을 알고 만든다.** 사유 축이 막는 것은 *아무것도 적지
#: 않는 것* 이지 작정한 위조가 아니다(`ARITY — x` 는 언제나 쓸 수 있다).
_INVISIBLE_CATEGORIES = frozenset({"Mn", "Me", "Cf", "Cc", "Cs", "Co", "Cn"})
_INVISIBLE_BUT_PRINTABLE = "ᅟᅠㅤﾠ⠀᠎​‌‍⁠﻿"


def _has_visible_prose(text: str) -> bool:
    """*화면에 무언가가 보이는* 산문인가.

    `isprintable()` 만으로는 부족하다 — 위 상수 참조.
    """
    for char in text:
        if char in _INVISIBLE_BUT_PRINTABLE:
            continue
        if unicodedata.category(char) in _INVISIBLE_CATEGORIES:
            continue
        if char.isprintable() and not char.isspace():
            return True
    return False


def declaration_defects(
    census_keys: "set[str]", declarations: "dict[str, str]"
) -> "dict[str, list[str]]":
    """게이트의 판정 — **순수 함수**로 뽑아 둔다.

    ⚠️ 독립 적대 평가가 *"이 비교에는 음성 대조가 없다"* 를 지적했다: 두 단언을
    `[]` 로 바꿔도 아무것도 red 가 되지 않았다. 관측을 인자로 받으면 합성 입력으로
    양방향을 실제로 시험할 수 있고, 그것이 이 저장소가 `artifact_custody_policy`
    등에서 이미 쓰는 분리다.
    """
    declared = set(declarations)
    defects = {
        "undeclared": sorted(census_keys - declared),
        "stale": sorted(declared - census_keys),
        "malformed": [],
    }
    for key, reason in sorted(declarations.items()):
        token, separator, prose = reason.partition(_DECLARATION_SEPARATOR)
        if not separator or token.strip() not in THRESHOLD_VOCABULARY:
            defects["malformed"].append(f"{key}: 어휘 밖 토큰 {token.strip()!r}")
        elif not _has_visible_prose(prose):
            defects["malformed"].append(f"{key}: 토큰만 있고 사유가 없다")
    return defects


#: `Class.function 연산자값` → `"TOKEN — 사유"`.
#:
#: ⚠️ **키가 함수 이름만이 아닌 이유**: 그렇게 만들었더니 *이미 선언된 함수는 아무
#: 숫자나 통과하는 백지 면제* 가 됐고, 독립 적대 평가가 선언된 함수에 리터럴
#: `4711` 을 넣어 전량 green 을 받아냈다 — 게이트 독스트링이 정반대를 약속하는 채로.
#: ⚠️ **줄 번호가 아닌 이유**: 줄 번호는 무관한 편집마다 밀려 이웃한 모든 변경이 이
#: 표를 만져야 한다. 함수가 개명되면 red 이고, 그것은 사고가 아니라 옳은 동작이다.
_THRESHOLD_DECLARATIONS: "dict[str, str]" = {
    "TestA11yAllowlistIsAJustifiedRatchet.test_the_allowlist_only_shrinks <=_A11Y_ALLOWLIST_CEILING":
        f"{THRESHOLD_FIXTURE} — 래칫 상한은 이 파일이 소유한 상수이고, 이 검사의 명제가 "
        "바로 *그 상한을 넘지 않는다* 다. 상한을 올리는 편집은 이 표를 지나야 한다",
    "TestA11yAllowlistIsAJustifiedRatchet.test_the_ceiling_is_not_slack ==_A11Y_ALLOWLIST_CEILING":
        f"{THRESHOLD_FIXTURE} — 동상. 상한에 여유가 없는지를 같은 상수로 되묻는다",
    "TestAssertiveLiveRegionsStayOnTheUrgentAxis.test_every_ruling_states_a_reason >_RATIONALE_MIN_CHARS":
        f"{THRESHOLD_ARITY} — 산문이 *라벨이 아니라 설명* 이기 위한 바닥이다. ⚠️ 독립 "
        "평가가 이 라벨을 이의제기했다(측정에서 나온 수인데 ARITY 인가). 판정 기준은 "
        "*리팩터가 이 수를 움직이는가* 이고, 답은 아니오다 — 트리를 재배치해도 근거의 "
        "길이는 변하지 않는다. 짧아지는 것은 내용 변경이고 이 검사가 잡으라는 것이다",
    "TestAssertiveLiveRegionsStayOnTheUrgentAxis.test_the_scan_actually_detects_the_defect ==3":
        f"{THRESHOLD_FIXTURE} — 검출기를 먹이는 offender 세 형태(리터럴/삼항/맵)를 이 "
        "테스트가 바로 위에서 직접 적는다. 셋을 다 잡는지가 명제다",
    "TestAxeScanCoversEveryRegisteredRoute.test_each_exclusion_carries_its_reason_next_to_the_list <_A11Y_MIN_REASON_CHARS":
        f"{THRESHOLD_ARITY} — 면제 사유가 라벨이 아니기 위한 바닥. 제외 라우트가 늘거나 "
        "줄어도 같은 값을 요구한다",
    "TestBlobParsingIsADeclaredConsumptionAxis.test_each_declared_key_is_used_by_exactly_one_operation !=1":
        f"{THRESHOLD_ARITY} — 선언 키 하나에 operation 하나. 둘이면 선언이 두 소비를 "
        "가리키고 영이면 죽은 선언이다 — 수가 곧 그 명제다",
    "TestBlobParsingIsADeclaredConsumptionAxis.test_no_other_module_writes_parse_as >1":
        f"{THRESHOLD_ARITY} — `> 1` 은 스캔 집합이 한 파일이 아니라는 바닥이다",
    "TestBlobParsingIsADeclaredConsumptionAxis.test_no_other_site_materialises_a_response_body >1":
        f"{THRESHOLD_ARITY} — 동상",
    "TestBlobParsingIsADeclaredConsumptionAxis.test_parse_as_appears_exactly_once_and_inside_the_request_builder ==1":
        f"{THRESHOLD_ARITY} — `parseAs` 사이트가 **하나**이고 그것이 빌더 안이라는 것이 "
        "이 축의 선언 자체다. 둘이 되는 순간 선언을 우회하는 소비가 생긴 것이다",
    "TestBlobParsingIsADeclaredConsumptionAxis.test_platform_export_parse_as_is_declared_and_local ==1":
        f"{THRESHOLD_ARITY} — platform sample export의 `parseAs`는 선언된 요청 builder에 한 곳만 있어야 한다",
    "TestBlobParsingIsADeclaredConsumptionAxis.test_the_production_counterexample_tree_is_typechecked_but_not_collected >=3":
        f"{THRESHOLD_FIXTURE} — `headless-client.type-test.ts` 는 `src/` 에 있지만 "
        "**테스트 자산**이다(vitest 미수집, tsc 만 본다). 반례 수는 그 트리의 래칫이다",
    "TestCodeRefinedCopyHasOneTable.test_the_one_lookup_cannot_be_switched_off_at_build_time ==2":
        f"{THRESHOLD_ARITY} — `2` 는 **명제의 항수**다: 정련 헬퍼가 답하는 경우가 둘이고"
        "(코드가 없다 / 표를 본다) 각각 반환 하나를 갖는다. 셋째 반환은 정의상 *표에 닿기 "
        "전에 빠져나가는 길* 이므로, 이 수는 프로덕션 배치가 아니라 함수의 계약을 센다",
    "TestCodeRefinedCopyHasOneTable.test_a_table_nested_one_level_deeper_is_caught ==1":
        f"{THRESHOLD_FIXTURE} — 세는 대상은 **이 테스트가 심은 표 하나**다(기존 팔 안에 한 칸 "
        "더 중첩). `1` 은 심은 반례의 개수이지 프로덕션 표의 개수가 아니므로 정련 표가 늘거나 "
        "줄어도 움직이지 않는다",
    "TestCodeRefinedCopyHasOneTable.test_the_assignment_detector_is_not_vacuous ==1":
        f"{THRESHOLD_FIXTURE} — 동상. 이 테스트가 대입 한 줄을 직접 적고 정확히 한 번 잡히는지 본다",
    "TestCodeRefinedCopyHasOneTable.test_the_upload_ceiling_is_never_spelled_in_the_frontend ==1":
        f"{THRESHOLD_FIXTURE} — 철자마다 합성 위반을 **이 테스트가** 코드 자리에 놓고 "
        "정확히 한 번 잡히는지 본다. 세는 대상이 합성 소스라 프로덕션 배치와 무관하다",
    "TestCurrentTestPlanGenerationConsumer.test_named_fixtures_have_no_positive_legacy_scope_consumer ==3":
        f"{THRESHOLD_FIXTURE} — `LEGACY_SCOPE_FIXTURES` 는 이 클래스가 소유한 경로 목록이다. "
        "목록이 조용히 비면 아래 스캔이 0 파일을 돌며 green 이 된다",
    "TestDocumentLangFollowsLocale.test_the_locale_ssot_is_parsable >=2":
        f"{THRESHOLD_ARITY} — 로케일 **대조**가 성립하려면 최소 둘이다. 하나면 이 클래스의 "
        "모든 parity 단언이 공허해진다",
    "TestDraftRowsScaleStructurally.test_both_render_paths_share_one_column_definition >=2":
        f"{THRESHOLD_ARITY} — `2` 는 렌더 경로의 수(표 · 윈도잉)이지 오늘의 개수가 아니다. "
        "경로가 셋이 되어도 각각이 같은 컬럼 목록에서 파생돼야 하므로 바닥은 유지된다",
    "TestDraftRowsScaleStructurally.test_the_parent_owns_exactly_one_remove_mutation ==1":
        f"{THRESHOLD_ARITY} — `== 1` 은 SSOT 주장이다: 표 전체에 낙관적 행 변이 선언이 "
        "하나. 둘이 되는 것이 이 검사가 막는 결함 자체이므로 수가 명제다",
    "TestEveryRegisteredRouteDeclaresABoundaryAndATitle.test_removing_a_boundary_from_the_real_file_is_seen ==1":
        f"{THRESHOLD_FIXTURE} — 세는 대상은 **이 테스트가 심은 변이**다(실제 `app.tsx` "
        "사본에서 `errorElement:` 한 줄을 지운다). `1` 은 심은 변이의 개수이지 라우트 "
        "수가 아니므로 화면이 늘거나 줄어도 움직이지 않는다",
    "TestNumericThresholdsDoNotFreezeTodaysArrangement.test_an_undeclared_threshold_is_named_not_merely_counted ==1":
        f"{THRESHOLD_FIXTURE} — 합성 클래스 하나를 먹이고 사이트 하나가 나오는지 본다",
    "TestNumericThresholdsDoNotFreezeTodaysArrangement.test_the_census_catches_every_syntactic_home ==1":
        f"{THRESHOLD_FIXTURE} — 세는 대상이 공유 픽스처 표의 합성 소스다. `1` 은 *그 "
        "스니펫 안의 임계값이 정확히 하나* 라는 픽스처의 사실이다",
    "TestPercentFormatAllowlistRatchet.test_allowlist_does_not_grow <=self.CEILING":
        f"{THRESHOLD_FIXTURE} — 래칫 상한이 이 클래스의 속성이고, 명제가 *그 상한을 넘지 "
        "않는다* 다. 오늘 0 이므로 새 위반은 곧바로 red 다",
    "TestProjectHrefAllowlistRatchet.test_allowlist_does_not_grow <=self.CEILING":
        f"{THRESHOLD_FIXTURE} — 동상",
    "TestProjectSelectorRemainsSingleSsot.test_the_owner_set_is_the_rule_not_a_debt_list ==3":
        f"{THRESHOLD_FIXTURE} — `PROJECT_DIRECTORY_FETCH_OWNERS` 는 이 파일이 소유한 "
        "frozenset 이고, 이 검사의 명제가 바로 *그 집합이 면제 목록으로 자라지 않는다* 다",
    "TestReferenceCoupledPublishIsOneRequest.test_the_pair_travels_in_one_request ==1":
        f"{THRESHOLD_ARITY} — 게시 호출이 **하나**여야 결합이 한 커밋 경계 안에 있다. "
        "둘이면 결합이 막으려던 상태를 결합을 지키는 척하며 만든다 — 수가 명제다",
    "TestRouteTitleAndFocusAreConsumed.test_every_top_level_route_element_is_announced >=2":
        f"{THRESHOLD_ARITY} — `/` 와 `/auth/callback` 이 **형제**라는 사실이 이 검사의 "
        "존재 이유다. 둘은 그 형제 관계의 항수이고, 라우트가 늘어도 명제는 같다",
    "TestRoutesOwnNoHeadlessTransport.test_the_guard_would_catch_a_reintroduced_call ==1":
        f"{THRESHOLD_FIXTURE} — 우회 형태마다 이 테스트가 적은 합성 스니펫에서 정확히 "
        "한 번 잡히는지 본다. 둘이면 정규식이 겹쳐 세는 것이고 그것도 결함이다",
    "TestSearchScopeClaimsMatchTheServerAxes.test_no_scope_claim_names_an_unsearchable_attribute >=2":
        f"{THRESHOLD_ARITY} — `>= 2` 는 술어 자신의 항수다: *두 축 이상을 나열하면서* "
        "범위를 좁힌다고 말하지 않는 문장이 결함이다",
    "TestSearchScopeClaimsMatchTheServerAxes.test_the_scan_has_live_claim_sites >=2":
        f"{THRESHOLD_ARITY} — 동상. 같은 술어를 비-공허성 축에서 다시 평가한다",
    "TestSearchScopeClaimsMatchTheServerAxes.test_the_scans_actually_detect_the_defects >=2":
        f"{THRESHOLD_ARITY} — 동상. 합성 offender/정상 문구로 그 술어를 양방향 시험한다",
    "TestStreamStatusVocabularySsot.test_no_route_redeclares_the_stream_token_vocabulary >=3":
        f"{THRESHOLD_ARITY} — `>= 3` 은 *어휘를 재선언했다* 는 판정의 항수다(토큰 한둘은 "
        "정상 사용, 셋부터가 사본). 어휘가 늘어도 그 판정은 같은 수를 요구한다",
    "TestTheCounterexampleTreeIsReachedAndComplete.test_the_envelope_mapping_rejects_a_repeated_probe >1":
        f"{THRESHOLD_ARITY} — 봉투 종류가 **둘 이상**이어야 *같은 프로브를 반복하는* "
        "패배 형태를 시험할 수 있다. 하나면 이 음성 대조가 공허하다",
    "TestTheCounterexampleTreeIsReachedAndComplete.test_the_tree_carries_both_halves >=5":
        f"{THRESHOLD_FIXTURE} — 반례 트리는 이 축이 소유한 테스트 자산이고, "
        "`@ts-expect-error` 수는 그 트리의 철저함 래칫이다",
    "TestTheDecorationPredicateStaysBroad.test_the_sibling_guard_covers_none_of_the_five >=5":
        f"{THRESHOLD_FIXTURE} — `ONLY_THIS_PREDICATE_CATCHES` 는 이 클래스가 소유한 "
        "반례 라벨 집합이고, 그 크기가 줄면 이 클래스가 지키는 형태가 줄어든 것이다",
    "TestTheTransportSealActuallyBites.test_a_reintroduced_hand_built_payload_is_caught_in_every_suite >1":
        f"{THRESHOLD_ARITY} — `> 1` 은 *걷기가 처음 닿는 파일 하나만 태우지 않는다* 라는 바닥",
    "TestTheTransportSealActuallyBites.test_an_empty_scan_set_is_caught >1":
        f"{THRESHOLD_ARITY} — 동상. 빈 스캔 대입으로 규칙을 실제로 실행하고, 그 옆에 "
        "프로덕션 스캔의 같은 바닥을 둔다",
    "TestTransportRequestsAreReadThroughTheContract.test_the_scan_set_is_not_empty >1":
        f"{THRESHOLD_ARITY} — `> 1` 은 *한 파일만 태우는 프로브가 아니다* 라는 바닥이다. "
        "한 자리만 보는 스캔은 나머지 루트가 전부 삭제돼도 green 이다",
    "TestTransportStubsAreDerivedFromTheContract.test_the_contract_helper_imports_its_lookups_rather_than_re_declaring_them >1":
        f"{THRESHOLD_ARITY} — 동상. 스캔이 한 파일뿐이면 재선언 탐지가 그 파일에만 산다",
    "TestTransportStubsAreDerivedFromTheContract.test_the_scan_set_is_not_empty_and_is_not_one_file >1":
        f"{THRESHOLD_ARITY} — 동상. 비-공허성과 *한 파일이 아님* 은 다른 두 명제이고 "
        "이 테스트 이름이 둘을 함께 적는다",
    "TestUnroutedPathsAreLoudByDefault.test_the_default_is_a_rejection_and_the_404_is_opt_in ==2":
        f"{THRESHOLD_ARITY} — `2` 는 기본값이 설정되는 **자리의 수**(생성 시점 · 테스트마다의 "
        "초기화)이고 주석이 둘을 이름으로 적는다. 하나면 한 스위트의 조용한 모드가 샌다",
    # ⚠️ 모듈 레벨 헬퍼도 예외가 아니다. 이 둘은 **첫 판이 아예 보지 못하던 자리**이고
    # (센서스가 단언 문맥만 봤다), 총체적 walk 로 뒤집자 드러났다.
    "_a11y_allowlist_defects <_A11Y_MIN_REASON_CHARS":
        f"{THRESHOLD_ARITY} — 위 형제 검사와 같은 바닥을 같은 상수로 쓴다 — 사유가 "
        "라벨이 아니기 위한 최소 길이다",
    "_claim_sentences >MAX_CLAIM_SENTENCE_CHARS":
        f"{THRESHOLD_ARITY} — *문장 하나* 로 볼 수 있는 상한이다. 넘으면 잘라 두 문장으로 "
        "다루는데, 그 상한은 문서량이 아니라 문장이라는 단위 자체에 대한 판단이다",
}


class TestNumericThresholdsDoNotFreezeTodaysArrangement(unittest.TestCase):
    """이 파일의 수치 임계값은 전부 **선언돼** 있고, 어휘는 둘뿐이다.

    장부 `[2026-08-19 headless-helper] P3` 가 이름 붙인 형태의 정공. 그 항목은
    개별 수정 둘(`consumers >= 10` → 파생, `\\bstatus\\b` → lookahead)로 증상을
    고치고 **형태를 남겨 뒀다**고 적었고, 실제로 남아 있었다.

    ⚠️ **이 게이트가 지키는 것은 오늘의 목록이 아니라 기본값이다.** 새 임계값이
    선언 없이 들어오면 red 이고, 그때 저자는 둘 중 하나를 해야 한다 — 그 수가
    명제의 바닥임을 적거나(그러면 선언), 프로덕션 파생이면 파생으로 바꾸거나.
    *"오늘 세어 보니 이만큼이더라"* 를 적을 어휘는 존재하지 않는다.
    """

    THIS_FILE = Path(__file__).resolve()

    def _census_keys(self) -> "set[str]":
        return {site.key for site in census_numeric_thresholds(self.THIS_FILE)}

    # -- 비-공허성. 완전성과 **따로** 앵커한다.
    def test_the_census_is_not_vacuous(self) -> None:
        """센서스가 빈 결과를 답하면 이 파일에 대한 모든 판정이 근거를 잃는다.

        ⚠️ 이 검사의 사유를 *"아니면 아래 상등이 공허하게 참이 된다"* 라고 적었던
        적이 있고 그것은 **거짓**이다 — 센서스가 비면 선언 전체가 `stale` 로 잡힌다.
        빈 센서스가 위험한 진짜 이유는 그것이 **파서가 깨졌다는 신호**라는 것이고,
        그때 red 를 내는 자리가 여기다.
        """
        self.assertGreater(
            len(self._census_keys()),
            0,
            "센서스가 이 파일에서 임계값을 하나도 찾지 못했다 — 파서가 대상을 "
            "잘못 짚었거나 AST 탐색이 깨졌다",
        )

    # -- 완전성. 집합 **상등** 이지 한쪽 포함이 아니다.
    def test_every_threshold_is_declared_and_every_declaration_is_live(self) -> None:
        defects = declaration_defects(self._census_keys(), _THRESHOLD_DECLARATIONS)
        self.assertEqual(
            defects["undeclared"],
            [],
            "손으로 고른 수치 임계값이 선언 없이 들어왔다. 그 수가 **명제의 바닥**이면 "
            "`_THRESHOLD_DECLARATIONS` 에 사유와 함께 적고, **프로덕션 파생 집합의 "
            f"오늘 개수**이면 파생 또는 순수 비-공허성(`> 0`)으로 바꿔라: "
            f"{defects['undeclared']}",
        )
        self.assertEqual(
            defects["stale"],
            [],
            "사라진 임계값에 대한 선언이 남아 있다 — 유령 면제는 다음 임계값이 그 "
            f"키로 조용히 들어올 자리다: {defects['stale']}",
        )
        self.assertEqual(defects["malformed"], [], f"{defects['malformed']}")

    def test_the_declaration_key_names_the_value_not_only_the_function(self) -> None:
        """⚠️ 함수 이름만으로 키잉하면 **선언된 함수가 백지 면제**가 된다.

        독립 적대 평가가 이미 선언된 함수에 리터럴 `4711` 을 넣어 전량 green 을
        받아냈다 — 게이트 독스트링이 정반대를 약속하는 채로. 키는 값을 포함한다.
        """
        # 모든 키가 `소유자 연산자값` 형태다 — 값 조각이 없으면 함수 단위 면제다.
        for key in sorted(_THRESHOLD_DECLARATIONS):
            with self.subTest(key=key):
                owner, _, value = key.rpartition(" ")
                self.assertTrue(owner, f"키에 소유자가 없다: {key}")
                self.assertRegex(
                    value,
                    r"^(?:[<>!=]=?|arith)\S+$",
                    f"키에 연산자와 값이 없다 — 함수 단위 면제가 된다: {key}",
                )
        smuggled = {
            *self._census_keys(),
            "TestDocumentLangFollowsLocale.test_the_locale_ssot_is_parsable >4711",
        }
        self.assertIn(
            "TestDocumentLangFollowsLocale.test_the_locale_ssot_is_parsable >4711",
            declaration_defects(smuggled, _THRESHOLD_DECLARATIONS)["undeclared"],
            "이미 선언된 함수에 새 숫자를 넣으면 그 숫자가 이름으로 보고돼야 한다",
        )

    def test_the_gate_reports_a_stale_declaration_and_a_malformed_one(self) -> None:
        """음성 대조 — 판정 함수를 합성 입력으로 양방향 시험한다.

        ⚠️ 이 자리는 한때 **없었고**, 그래서 두 단언을 `[]` 로 바꾸는 변이가
        살아남았다(독립 적대 평가 실측). 관측을 인자로 받는 순간 시험할 수 있다.
        """
        stale = declaration_defects(set(), {"X.y >2": f"{THRESHOLD_ARITY} — 사유"})
        self.assertEqual(stale["stale"], ["X.y >2"])
        for label, reason in {
            "토큰이 어휘 밖": "PRODUCTION_COUNT — 오늘 세어 보니",
            "구분자 없음": f"{THRESHOLD_ARITY} 사유는 있으나 구분자가 없다",
            "사유가 없음": f"{THRESHOLD_ARITY} — ",
            "사유가 보이지 않는 문자뿐": f"{THRESHOLD_ARITY} — ͏ㅤ️",
        }.items():
            with self.subTest(shape=label):
                self.assertNotEqual(
                    declaration_defects({"X.y >2"}, {"X.y >2": reason})["malformed"],
                    [],
                    f"`{label}` 형태의 선언이 통과한다",
                )
        healthy = declaration_defects({"X.y >2"}, {"X.y >2": f"{THRESHOLD_ARITY} — 진짜 사유"})
        self.assertEqual(healthy, {"undeclared": [], "stale": [], "malformed": []})

    def test_the_vocabulary_cannot_name_a_production_count(self) -> None:
        """어휘가 정확히 둘이라는 사실 자체가 이 축의 집행 수단이다.

        ⚠️ **리터럴로 단언한다.** 이 검사가 `{THRESHOLD_ARITY, THRESHOLD_FIXTURE}`
        와 비교하던 때에는 `THRESHOLD_ARITY = "PRODUCTION_COUNT"` 로 바꿔도 양변이
        같이 움직여 통과했다(독립 적대 평가 실측) — 상수를 공유하는 오라클은
        상수가 무엇이 되든 참이다.
        """
        self.assertEqual(THRESHOLD_VOCABULARY, {"ARITY", "FIXTURE"})
        self.assertEqual(THRESHOLD_ARITY, "ARITY")
        self.assertEqual(THRESHOLD_FIXTURE, "FIXTURE")

    # -- 양성 대조. 센서스가 *실제로* 무는지를 합성 소스로 실행한다.
    def test_the_census_catches_every_syntactic_home(self) -> None:
        """공유 픽스처 표에서 파생한다 — 사본을 두지 않는다.

        ⚠️ 이 표는 한때 이 파일과 `tests/test_assertion_threshold_census.py` 에
        **각각** 있었고, 그 둘을 만든 커밋에서 이미 13행 대 16행으로 갈라져 있었다
        (어느 쪽도 상위집합이 아니었다). 소유자를 하나로 둔다.
        """
        self.assertGreater(len(SYNTACTIC_HOME_FIXTURES), 0, "픽스처 표가 비었다")
        for label, statement in SYNTACTIC_HOME_FIXTURES.items():
            with self.subTest(home=label):
                self.assertEqual(
                    len(census_from_source(f"def t(self):\n    {statement}\n")),
                    1,
                    f"`{label}` 형태의 임계값을 센서스가 보지 못한다",
                )

    def test_the_census_leaves_the_recommended_shapes_alone(self) -> None:
        """바닥과 파생은 이 축이 **권하는** 형태다 — 신고하면 게이트가 삭제된다."""
        self.assertGreater(len(EXEMPT_SHAPE_FIXTURES), 0, "면제 표가 비었다")
        for label, statement in EXEMPT_SHAPE_FIXTURES.items():
            with self.subTest(shape=label):
                self.assertEqual(
                    census_from_source(f"def t(self):\n    {statement}\n"),
                    [],
                    f"`{label}` 를 위반으로 신고한다",
                )

    def test_the_limitation_is_announced_and_matches_the_code(self) -> None:
        """부분 게이트는 자기 가장자리를 이름으로 말한다 — 그리고 그 말이 참이어야 한다."""
        self.assertIn("SYNTACTICALLY RECOGNISABLE", ASSERTION_CENSUS_LIMITATION)
        self.assertIn("scope is", ASSERTION_CENSUS_LIMITATION)
        self.assertIn("BUDGET", ASSERTION_CENSUS_LIMITATION)
        # 고지한 그대로: 이 모듈 밖으로 옮긴 임계값은 보이지 않는다.
        self.assertEqual(
            census_from_source("from elsewhere import THRESHOLD_LIVES_THERE\n"), []
        )

    def test_an_undeclared_threshold_is_named_not_merely_counted(self) -> None:
        """거절은 **어느 함수의 어느 값인지** 를 말해야 한다."""
        synthetic = (
            "class TestSomethingNew:\n"
            "    def test_a_new_hand_picked_threshold(self):\n"
            "        self.assertGreaterEqual(len(routes), 16)\n"
        )
        sites = census_from_source(synthetic)
        self.assertEqual(len(sites), 1)
        self.assertEqual(
            sites[0].key,
            "TestSomethingNew.test_a_new_hand_picked_threshold >=16",
            "센서스가 소유 함수와 값을 키로 묶지 못한다 — 선언 표의 키가 그것이므로 "
            "귀속이 틀리면 표 전체가 어긋난다",
        )
