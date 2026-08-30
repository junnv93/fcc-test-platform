"""Workbook upload policy — the node decides where bytes land, and says so.

WHY THIS IS A HANDLE AND NOT A PATH
-----------------------------------
``SessionUseCaseSupervisor.acquire`` already wrote down the constraint this
module exists to satisfy: a session may ask for a workbook other than the one
the node booted with, but only *"until an operation exists that can produce a
workbook path the node trusts"* was that axis deliberately closed, because
accepting a path over HTTP first *"would be an arbitrary-file-read parameter
with no legitimate producer"*.

So the producer is this: bytes are uploaded, the node picks the destination,
and the caller gets back an opaque **handle**. ``/session/start`` takes that
handle. No path string crosses the HTTP boundary in either direction, which is
why this axis needs no allow-list of permitted roots the way ``storage_root``
does — the dangerous parameter is not defended against, it does not exist.

The handle is derived from the content digest, which buys idempotence (the same
workbook uploaded twice is the same handle and is not rewritten — *"멱등은 존재가
아니라 내용"*, the rule the artifact-replication axis settled). That derivation
is an implementation fact and is deliberately **not** promised in the wire
contract: a client that could compute handles locally could name a workbook it
never uploaded, and then the handle would be a path parameter again wearing a
different hat.

WHAT IS PURE HERE AND WHAT IS NOT
---------------------------------
This module judges; it never opens a file. Observation belongs to the adapter,
expectation to the caller, judgement to these functions — the same three-way
split the plot-custody axis uses so an oracle cannot quietly share an
implementation with the thing it is checking. Concretely: the adapter reads a
ZIP's member names, :func:`judge_workbook_members` decides whether those names
describe a workbook.
"""
from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable, Optional


__all__ = [
    'DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES',
    'DEFAULT_WORKBOOK_RETENTION_SECONDS',
    'ENV_MAX_WORKBOOK_UPLOAD_BYTES',
    'ENV_WORKBOOK_RETENTION_SECONDS',
    'REQUIRED_OOXML_MEMBERS',
    'UPLOAD_CHUNK_BYTES',
    'WORKBOOK_HANDLE_PATTERN',
    'WORKBOOK_UPLOAD_DIR_PARTS',
    'WORKBOOK_UPLOAD_SUFFIX',
    'WORKBOOK_ZIP_MAGIC',
    'WorkbookHandleNotFound',
    'WorkbookUploadError',
    'WorkbookUploadNotAWorkbook',
    'WorkbookUploadTooLarge',
    'is_valid_workbook_handle',
    'judge_workbook_members',
    'looks_like_workbook_header',
    'mint_workbook_handle',
    'resolve_max_upload_bytes',
    'resolve_workbook_retention_seconds',
    'sanitize_upload_filename',
    'workbook_object_name',
    'workbook_upload_root_parts',
]


class WorkbookUploadError(ValueError):
    """Base for every typed refusal on the upload axis."""


class WorkbookUploadTooLarge(WorkbookUploadError):
    """The upload exceeded the node's configured ceiling (→ 413).

    The refusal **names the ceiling** through the RFC 9457 ``params`` extension
    rather than only in its prose. A tester who is told "too large" and not
    "too large for 64 MiB" has to guess, and the only way a screen could answer
    that question without this channel is by re-declaring the number — which is
    exactly the client-side re-derivation this repository forbids.

    ⚠️ The attribute is named ``max`` because that is the mechanism, not a style
    choice: :func:`problem_params_for` reads attributes whose names are
    :data:`PROBLEM_PARAM_ALLOWLIST` members, and ``'max'`` is the member that
    means *"an upper bound that was exceeded"*. Declaring the field under any
    other name publishes nothing.

    ``max_bytes`` defaults to ``None`` so a raise that does not know the ceiling
    keeps today's body byte-identical — ``problem_params_for`` drops ``None``
    rather than publishing a null bound. That is what makes this additive.
    """

    #: Opt-in declaration read by ``application.common.api_error_codes``. Kept
    #: as a plain literal (no import) — this module is domain-pure and the
    #: allow-list lives in the application layer.
    PROBLEM_PARAM_FIELDS = ('max',)

    def __init__(self, message: str, *, max_bytes: Optional[int] = None) -> None:
        super().__init__(message)
        self.max = max_bytes


class WorkbookUploadNotAWorkbook(WorkbookUploadError):
    """The bytes are not an OOXML workbook (→ 415)."""


class WorkbookHandleNotFound(WorkbookUploadError):
    """No workbook is stored under that handle on this node (→ 404)."""


# --------------------------------------------------------------------------
# Handle grammar
# --------------------------------------------------------------------------

#: A handle is exactly a lowercase SHA-256 hex digest. Anchored on both ends,
#: fixed length, and drawn from an alphabet that contains no path separator, no
#: ``.``, no drive letter and no NUL — so a handle cannot become a path segment
#: that escapes its directory even before the store re-checks containment.
WORKBOOK_HANDLE_PATTERN = re.compile(r'\A[0-9a-f]{64}\Z')

#: Suffix the stored object carries. Not cosmetic: ``pandas.ExcelFile`` and the
#: seeding readers pick their engine from the extension, so a workbook landing
#: without it would be read by a different code path than the workbook a tester
#: opens locally.
WORKBOOK_UPLOAD_SUFFIX = '.xlsx'

#: Where uploads live under the node's runtime directory, as path *parts* rather
#: than a joined literal — the caller joins them onto whatever root it resolved,
#: so no call site spells the layout itself.
WORKBOOK_UPLOAD_DIR_PARTS = ('uploads', 'workbooks')


def is_valid_workbook_handle(token: object) -> bool:
    """True when ``token`` is syntactically a handle this node could have minted."""
    return isinstance(token, str) and WORKBOOK_HANDLE_PATTERN.match(token) is not None


def mint_workbook_handle(digest_hex: str) -> str:
    """Turn a content digest into the handle the wire contract carries.

    Raises :class:`WorkbookUploadError` rather than returning something
    unusable: a caller that reached here with a non-digest has a bug, and a
    handle that does not match the grammar would be rejected by every consumer
    anyway — failing at the mint says so at the site that can be fixed.
    """
    token = str(digest_hex or '').strip().lower()
    if not is_valid_workbook_handle(token):
        raise WorkbookUploadError(
            f'not a workbook handle: {digest_hex!r} '
            '(expected a lowercase sha256 hex digest)'
        )
    return token


def workbook_object_name(handle: str) -> str:
    """File name for a stored workbook. Validates first — never interpolates raw."""
    return f'{mint_workbook_handle(handle)}{WORKBOOK_UPLOAD_SUFFIX}'


def workbook_upload_root_parts() -> tuple[str, ...]:
    """The upload directory's parts, relative to the node's runtime directory."""
    return WORKBOOK_UPLOAD_DIR_PARTS


# --------------------------------------------------------------------------
# The client's file name — echoed, never used as a name
# --------------------------------------------------------------------------

#: Substituted when the client sent nothing usable. A blank echo would make the
#: response look like the node lost the name rather than the client never sent
#: one.
_UNNAMED_UPLOAD = 'workbook.xlsx'

_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')


def sanitize_upload_filename(raw: object) -> str:
    """Normalize a client-supplied file name **for echoing back only**.

    Nothing on disk is ever named from this. The stored object's name comes from
    :func:`workbook_object_name`, i.e. from the digest, so a hostile file name
    has no destination to reach. This function exists so the response body and
    the logs carry something a human recognises without carrying control
    characters, traversal segments, or a UNC prefix into either.

    Unicode is NFC-normalized before the character filter so that a name built
    from decomposed code points cannot smuggle a separator past it.
    """
    text = raw if isinstance(raw, str) else ''
    text = unicodedata.normalize('NFC', text)
    # Both separators, unconditionally — a Windows client's backslash path must
    # be stripped even when this runs on POSIX, and vice versa.
    text = text.replace('\\', '/').rsplit('/', 1)[-1]
    text = _UNSAFE_FILENAME_CHARS.sub('', text).strip().strip('.')
    if not text:
        return _UNNAMED_UPLOAD
    return text[:255]


# --------------------------------------------------------------------------
# Is this actually a workbook?
# --------------------------------------------------------------------------

#: Local file header of a ZIP entry. Every OOXML file starts with one.
WORKBOOK_ZIP_MAGIC = b'PK\x03\x04'

#: The two members that make a ZIP an *Excel* workbook rather than some other
#: OOXML package (a .docx has ``[Content_Types].xml`` too, but no ``xl/``).
REQUIRED_OOXML_MEMBERS = frozenset({'[Content_Types].xml', 'xl/workbook.xml'})


def looks_like_workbook_header(head: bytes) -> bool:
    """Cheap first-chunk verdict, so a wrong file type is refused early.

    This is a *necessary* condition, not a sufficient one — it says "keep
    reading", not "this is a workbook". :func:`judge_workbook_members` is the
    sufficient one. Both exist because the ZIP central directory lives at the
    end of the file: refusing a .pdf at byte 4 avoids spooling megabytes of it,
    but only the full package can answer whether the workbook parts are there.
    """
    return bytes(head[: len(WORKBOOK_ZIP_MAGIC)]) == WORKBOOK_ZIP_MAGIC


def judge_workbook_members(names: Iterable[str]) -> bool:
    """True when a ZIP's member names describe an Excel workbook package.

    Takes the observation as an argument and opens nothing, so the adapter that
    reads the archive and the rule that judges it cannot drift into agreeing
    with each other by construction.
    """
    present = {str(name).strip() for name in names}
    return REQUIRED_OOXML_MEMBERS.issubset(present)


# --------------------------------------------------------------------------
# Size ceiling
# --------------------------------------------------------------------------

#: 64 MiB. Real plans measure ~2 MB; the largest workbook this repository has
#: benchmarked against is 64 MB, so the ceiling admits every workbook anyone has
#: actually seen and refuses the rest.
DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES = 64 * 1024 * 1024

ENV_MAX_WORKBOOK_UPLOAD_BYTES = 'FCC_SESSION_MAX_WORKBOOK_UPLOAD_BYTES'

#: Read granularity. Bounded so a hostile body is refused after one chunk past
#: the ceiling rather than after the whole body has been spooled.
UPLOAD_CHUNK_BYTES = 1024 * 1024


def resolve_max_upload_bytes(raw: Optional[str]) -> int:
    """Parse the configured ceiling. Total — never raises, never returns 0.

    Unset, blank, non-numeric and non-positive all resolve to the default. There
    is deliberately **no** spelling for "unlimited": this endpoint runs on a
    chamber PC whose disk also holds submission evidence, and a configuration
    typo that silently removed the ceiling would only be discovered by the disk
    filling up during a measurement.

    Pure — the environment is read by the caller, not here.
    """
    text = str(raw or '').strip()
    if not text:
        return DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES
    try:
        value = int(text)
    except (TypeError, ValueError):
        return DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES
    if value <= 0:
        return DEFAULT_MAX_WORKBOOK_UPLOAD_BYTES
    return value


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------

#: Seven days. A measurement runs for hours, so this comfortably outlives any
#: single session, and it keeps the content-addressed dedup window alive across
#: a working week — re-uploading last Monday's workbook on Friday is still free.
DEFAULT_WORKBOOK_RETENTION_SECONDS = 7 * 24 * 60 * 60

ENV_WORKBOOK_RETENTION_SECONDS = 'FCC_SESSION_WORKBOOK_RETENTION_SECONDS'


def resolve_workbook_retention_seconds(raw: Optional[str]) -> float:
    """Parse how long an unreferenced upload is kept. Total — never raises.

    Deliberately the same shape as :func:`resolve_max_upload_bytes`, including
    the absence of an "off" spelling: two knobs on the same config object that
    disagree about what a blank value means are a place for the next defect to
    live. Unset, blank, non-numeric and non-positive all resolve to the default.

    A caller who wants uploads kept effectively forever configures a long
    retention, which is a number an operator can read back — unlike a magic
    ``0`` whose meaning has to be remembered.

    Pure — the environment is read by the caller, not here.
    """
    text = str(raw or '').strip()
    if not text:
        return float(DEFAULT_WORKBOOK_RETENTION_SECONDS)
    try:
        value = float(text)
    except (TypeError, ValueError):
        return float(DEFAULT_WORKBOOK_RETENTION_SECONDS)
    # ⚠️ Both non-finite forms are rejected, and each for its own reason.
    #
    # ``nan`` fails every comparison, so it would sail through a ``<= 0`` guard
    # and then make every age comparison false — a sweep that silently never
    # runs. ``not value > 0`` catches it.
    #
    # ``inf`` passes ``> 0`` and IS the off switch this parser is documented not
    # to have: ``age < inf`` is true for every file, forever. ``'inf'``,
    # ``'Infinity'`` and ``'1e999'`` all reach here through ``float()``, which is
    # exactly the asymmetry with the sibling ceiling — that one parses with
    # ``int()`` and rejects all three. This restores the promised symmetry.
    if not value > 0 or math.isinf(value):
        return float(DEFAULT_WORKBOOK_RETENTION_SECONDS)
    return value
