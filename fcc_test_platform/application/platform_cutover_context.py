"""Stable, exception-opaque diagnostics for cutover validation contexts."""

from __future__ import annotations


_SAFE_CONTEXT_FIELDS = frozenset({"central_db_schema", "extraction_manifest"})

CONTEXT_READ_ERROR_CODE = "context_read_error"
CONTEXT_READ_ERROR_CATEGORY = "validation_context"
CONTEXT_READ_ERROR_MESSAGE = "validation context could not be read"


def build_context_read_issue(context_field: str) -> dict[str, str]:
    """Build the only persisted issue shape for a context read exception.

    The caller supplies a field name from the closed set above.  The exception
    and configured path are deliberately absent from this boundary, so no
    exception-controlled text can reach a cutover summary.
    """
    if context_field not in _SAFE_CONTEXT_FIELDS:
        raise ValueError("unsupported validation context field")
    return {
        "code": CONTEXT_READ_ERROR_CODE,
        "category": CONTEXT_READ_ERROR_CATEGORY,
        "path": context_field,
        "message": CONTEXT_READ_ERROR_MESSAGE,
        "evidence_key": context_field,
    }
