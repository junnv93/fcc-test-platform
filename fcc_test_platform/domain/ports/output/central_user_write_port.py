"""Output port — central users JIT provisioning writer.

Central users are keyed by the OIDC-standard identity tuple ``(issuer, subject)``.
``subject`` alone is only locally unique within one issuer and must not be used
as a global natural key.
"""
from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable


__all__ = ['CentralUserWritePort', 'UserWriteError']


class UserWriteError(RuntimeError):
    """Raised when a central users write fails at infrastructure level (→ 503)."""


@runtime_checkable
class CentralUserWritePort(Protocol):
    """Idempotently ensure a central ``users`` row for a verified principal."""

    def ensure_user(self, user_record: Mapping) -> dict:
        """UPSERT a ``users`` row on ``(issuer, subject)``.

        On conflict:
        * ``enabled`` is preserved,
        * display_name / email update only from non-empty incoming values,
        * updated_at advances.
        """
        ...
