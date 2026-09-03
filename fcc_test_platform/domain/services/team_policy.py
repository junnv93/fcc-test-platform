"""Tester sub-team (RF / SAR) classification SSOT (Phase D, 2026-06-23).

The team axis is **orthogonal to permission**: a 시험원 is a member of a sub-team
(RF or SAR), used for attribution (누가=어느 팀), default filtering (SAR 팀 시험원은
SAR 배정 시료 위주 뷰), and to mesh with ``samples.assigned_team``. It NEVER gates
access — any 시험원 (regardless of team) holds the same write permission.

This module is the single source of truth for the valid team tokens so neither
the membership write boundary nor any sample-inventory consumer hard-codes the
``'RF'`` / ``'SAR'`` literals. Pure domain — stdlib only.
"""
from __future__ import annotations

from typing import Optional


__all__ = ['TEAM_CODES', 'is_known_team', 'normalize_team']

# The canonical tester sub-team tokens. RF (radiated/conducted RF performance) and
# SAR (specific absorption rate) are the two FCC testing sub-teams that share the
# same 시험원 write permission.
TEAM_CODES: tuple[str, ...] = ('RF', 'SAR')


def normalize_team(value: object) -> Optional[str]:
    """Canonicalise a team token (case/whitespace-insensitive) → 'RF'/'SAR'.

    Returns ``None`` for an empty/blank value (team is optional). A non-empty
    value that is not a known team is returned upper-cased & stripped so the
    caller's ``is_known_team`` gate rejects it (no silent coercion to a valid
    token).
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.upper()


def is_known_team(value: object) -> bool:
    """True iff ``value`` normalises to a known team token (or is empty/None).

    Empty/None is allowed (team is an optional attribute); a non-empty unknown
    token is rejected so the membership write boundary can 400 on a typo.
    """
    normalized = normalize_team(value)
    if normalized is None:
        return True
    return normalized in TEAM_CODES
