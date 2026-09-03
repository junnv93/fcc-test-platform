"""JIT central user provisioning from verified API principals."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, MutableSet, Optional

from fcc_test_contracts.common.access_policy import ApiPrincipal
from fcc_test_contracts.common.identity import canonical_issuer
from fcc_test_platform.domain.ports.output.central_user_write_port import CentralUserWritePort


__all__ = ['UserProvisioningService']


class UserProvisioningService:
    """Ensure a central ``users`` row for a verified non-anonymous principal."""

    def __init__(
        self,
        user_write_port: CentralUserWritePort,
        *,
        id_factory: Optional[Callable[[], str]] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> None:
        if user_write_port is None:
            raise ValueError('user_write_port is required')
        self._users = user_write_port
        self._id_factory = id_factory or _uuid4_str
        self._clock = clock or _utcnow_iso

    def ensure_provisioned(
        self,
        principal: ApiPrincipal,
        *,
        dedup: MutableSet[tuple[str, str]] | None = None,
    ) -> dict:
        subject = str(getattr(principal, 'subject', '') or '').strip()
        if not subject or subject == 'anonymous':
            return {}
        issuer = canonical_issuer(getattr(principal, 'issuer', ''))
        key = (issuer, subject)
        if dedup is not None and key in dedup:
            return {}

        now = self._clock()
        user = self._users.ensure_user({
            'id': self._id_factory(),
            'issuer': issuer,
            'subject': subject,
            'display_name': str(getattr(principal, 'display_name', '') or '').strip(),
            'email': str(getattr(principal, 'email', '') or '').strip(),
            'enabled': True,
            'created_at': now,
            'updated_at': now,
        })
        if dedup is not None:
            dedup.add(key)
        if not _is_enabled(user.get('enabled')):
            raise PermissionError('principal user is disabled')
        return user


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid4_str() -> str:
    return str(uuid.uuid4())


def _is_enabled(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'f', 'no', 'off'}
