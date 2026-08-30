"""Shared validation primitives for platform evidence manifests."""
from __future__ import annotations

import hashlib
from typing import Mapping


__all__ = [
    'sha256_bytes',
    'is_sha256_hex',
    'is_placeholder_token',
    'placeholder_tokens',
    'placeholder_value_paths',
]


_SHA256_HEX_CHARS = frozenset('0123456789abcdef')


def sha256_bytes(value: bytes) -> str:
    """Return the canonical lowercase SHA-256 digest for *value*.

    This helper is intentionally byte-oriented and side-effect free so the
    extraction-owned evidence primitive remains the single digest boundary
    shared by platform evidence consumers.
    """
    return hashlib.sha256(value).hexdigest()


def is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(char in _SHA256_HEX_CHARS for char in value)


def is_placeholder_token(value: str) -> bool:
    """A value that is entirely an angle-bracket template sentinel (``<...>``).

    SSOT for the cutover workflow placeholder convention. Previously duplicated
    in ``scripts/platform_cutover_workflow_hints.py`` and
    ``scripts/platform_cutover_live_workflow.py``.
    """
    return value.startswith('<') and value.endswith('>') and len(value) > 2


def placeholder_tokens(text: str) -> list[str]:
    """Ordered angle-bracket tokens contained in ``text`` (duplicates preserved).

    Low-level scanner SSOT. Each token is the stripped inner content of a
    ``<...>`` span; empty spans are skipped. Callers that need unique/sorted
    output apply ``sorted(set(...))`` themselves.
    """
    tokens: list[str] = []
    start = 0
    while True:
        open_index = text.find('<', start)
        if open_index < 0:
            return tokens
        close_index = text.find('>', open_index + 1)
        if close_index < 0:
            return tokens
        token = text[open_index + 1:close_index].strip()
        if token:
            tokens.append(token)
        start = close_index + 1


def placeholder_value_paths(
    obj: object,
    *,
    _prefix: str = '',
) -> list[tuple[str, tuple[str, ...]]]:
    """Recursively locate evidence-manifest leaves that still carry ``<...>`` tokens.

    Returns ``(dotted_path, sorted_unique_tokens)`` pairs for every string leaf
    that contains an unresolved placeholder. A real evidence manifest collected
    from a live environment carries no template sentinels, so any hit means the
    bundle is a template/fixture rather than production evidence.
    """
    hits: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(obj, str):
        tokens = placeholder_tokens(obj)
        if tokens:
            hits.append((_prefix or '<root>', tuple(sorted(set(tokens)))))
        return hits
    if isinstance(obj, Mapping):
        for key in obj:
            child_prefix = f'{_prefix}.{key}' if _prefix else str(key)
            hits.extend(placeholder_value_paths(obj[key], _prefix=child_prefix))
        return hits
    if isinstance(obj, (list, tuple)) and not isinstance(obj, (str, bytes)):
        for index, item in enumerate(obj):
            child_prefix = f'{_prefix}[{index}]'
            hits.extend(placeholder_value_paths(item, _prefix=child_prefix))
        return hits
    return hits
