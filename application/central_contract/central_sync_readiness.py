"""Typed central result-sync readiness checks owned by the platform lane.

The configured provider code is the wire/config natural key.  The central
``providers.id`` value is the UUID foreign-key identity used by the ingestion
writer.  This boundary resolves the two explicitly from the central schema so
callers cannot accidentally use the natural key in a UUID FK column.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable
import uuid


class CentralSyncReadinessCode(str, Enum):
    READY = 'ready'
    PROVIDER_NOT_REGISTERED = 'provider_not_registered'
    CENTRAL_DB_UNAVAILABLE = 'central_db_unavailable'

    @classmethod
    def values(cls) -> tuple[str, ...]:
        """Return the bounded wire vocabulary owned by this enum."""
        return tuple(code.value for code in cls)


@dataclass(frozen=True)
class CentralSyncProviderIdentity:
    """The central row identity resolved from the configured provider code."""

    provider_code: str
    provider_uuid: str

    def __post_init__(self) -> None:
        code = str(self.provider_code or '').strip()
        raw_uuid = str(self.provider_uuid or '').strip()
        if not code:
            raise ValueError('provider_code is required')
        if not raw_uuid:
            raise ValueError('provider_uuid is required')
        # ``providers.id`` is UUID in the central schema.  Normalising through
        # uuid.UUID also prevents a malformed registry row from reaching the
        # writer as a value that would fail later at a FK boundary.
        object.__setattr__(self, 'provider_code', code)
        object.__setattr__(self, 'provider_uuid', str(uuid.UUID(raw_uuid)))


@dataclass(frozen=True)
class CentralSyncReadiness:
    code: CentralSyncReadinessCode
    reason: str = ''
    retryable: bool = False
    provider: CentralSyncProviderIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, CentralSyncReadinessCode):
            try:
                object.__setattr__(self, 'code', CentralSyncReadinessCode(self.code))
            except (TypeError, ValueError) as exc:
                raise ValueError('invalid central sync readiness code') from exc
        if not isinstance(self.reason, str):
            raise TypeError('readiness reason must be a string')
        if not isinstance(self.retryable, bool):
            raise TypeError('readiness retryable must be a bool')
        if self.code is CentralSyncReadinessCode.READY and self.retryable:
            raise ValueError('ready central sync readiness cannot be retryable')
        if self.code is not CentralSyncReadinessCode.READY and not self.retryable:
            raise ValueError('unavailable central sync readiness must be retryable')

    @property
    def enabled(self) -> bool:
        return self.code is CentralSyncReadinessCode.READY


# Derived from docs/platform/central_db_schema.v1.json and its official
# migration: providers.provider_id is the natural key and providers.id is the
# UUID identity referenced by test_sessions/measurement_results/attempts.
PROVIDER_READINESS_SQL = (
    'SELECT "id", "provider_id" FROM "providers" '
    'WHERE "provider_id" = %s LIMIT 1'
)


class PostgresCentralSyncReadinessProbe:
    """Probe the central provider registry through the injected DB boundary."""

    def __init__(self, connection_factory: Callable, *, provider_id: str) -> None:
        if not provider_id or not str(provider_id).strip():
            raise ValueError('provider_id is required for central sync readiness')
        self._connection_factory = connection_factory
        self._provider_id = str(provider_id).strip()

    def __call__(self) -> CentralSyncReadiness:
        connection = None
        try:
            connection = self._connection_factory()
            cursor = connection.cursor()
            try:
                cursor.execute(PROVIDER_READINESS_SQL, (self._provider_id,))
                row = cursor.fetchone()
            finally:
                cursor.close()
        except Exception:
            return CentralSyncReadiness(
                CentralSyncReadinessCode.CENTRAL_DB_UNAVAILABLE,
                'central database readiness check failed', True,
            )
        finally:
            close = getattr(connection, 'close', None)
            if callable(close):
                close()
        if not row:
            return CentralSyncReadiness(
                CentralSyncReadinessCode.PROVIDER_NOT_REGISTERED,
                'provider is not registered in the central providers registry', True,
            )
        try:
            provider_uuid, provider_code = row
            identity = CentralSyncProviderIdentity(
                provider_code=str(provider_code),
                provider_uuid=str(provider_uuid),
            )
        except (TypeError, ValueError):
            return CentralSyncReadiness(
                CentralSyncReadinessCode.CENTRAL_DB_UNAVAILABLE,
                'central database readiness check failed', True,
            )
        if identity.provider_code != self._provider_id:
            return CentralSyncReadiness(
                CentralSyncReadinessCode.CENTRAL_DB_UNAVAILABLE,
                'central provider registry returned an inconsistent identity', True,
            )
        return CentralSyncReadiness(
            CentralSyncReadinessCode.READY,
            provider=identity,
        )


__all__ = [
    'CentralSyncProviderIdentity',
    'CentralSyncReadiness',
    'CentralSyncReadinessCode',
    'PostgresCentralSyncReadinessProbe',
    'PROVIDER_READINESS_SQL',
]
