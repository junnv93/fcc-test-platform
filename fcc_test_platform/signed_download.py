"""Signed URL planning for object-store backed platform downloads."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from typing import Mapping
from urllib.parse import urlencode, urlparse
from urllib.parse import parse_qs

from fcc_test_platform.artifact_storage import normalize_relative_path
from fcc_test_platform.evidence_primitives import is_sha256_hex


__all__ = [
    'SignedDownloadUrl',
    'VerifiedSignedDownload',
    'build_signed_download_url',
    'verify_signed_download_request',
]


SIGNED_URL_STORAGE_BACKENDS = frozenset({'object_store', 's3', 'azure_blob'})


@dataclass(frozen=True)
class SignedDownloadUrl:
    url: str
    relative_path: str
    storage_backend: str
    expires_at: str
    headers: dict[str, str]

    def to_dict(self) -> dict:
        return {
            'url': self.url,
            'relative_path': self.relative_path,
            'storage_backend': self.storage_backend,
            'expires_at': self.expires_at,
            'headers': dict(self.headers),
        }


@dataclass(frozen=True)
class VerifiedSignedDownload:
    relative_path: str
    storage_backend: str
    expires_at: str
    disposition: str
    sha256: str

    def to_dict(self) -> dict:
        return {
            'relative_path': self.relative_path,
            'storage_backend': self.storage_backend,
            'expires_at': self.expires_at,
            'disposition': self.disposition,
            'sha256': self.sha256,
        }


def build_signed_download_url(
    *,
    grant: Mapping,
    base_url: str,
    signing_secret: str,
    now: datetime,
) -> SignedDownloadUrl:
    relative_path = normalize_relative_path(str(grant.get('relative_path') or ''))
    storage_backend = _required_text(grant, 'storage_backend')
    if storage_backend not in SIGNED_URL_STORAGE_BACKENDS:
        raise ValueError('signed URLs require object-store storage_backend')
    if len(str(signing_secret or '')) < 32:
        raise ValueError('signing_secret must be at least 32 characters')
    _require_https_base_url(base_url)
    expires_at = _require_not_expired(str(grant.get('expires_at') or ''), now)
    disposition = _disposition(str(grant.get('disposition') or 'attachment'))
    metadata = grant.get('metadata') if isinstance(grant.get('metadata'), Mapping) else {}
    sha256 = str(metadata.get('sha256') or '').strip()
    if sha256 and not is_sha256_hex(sha256):
        raise ValueError('grant metadata sha256 must be a lowercase SHA-256 digest')
    payload = {
        'path': relative_path,
        'backend': storage_backend,
        'expires': expires_at,
        'disposition': disposition,
        'sha256': sha256,
    }
    payload['signature'] = _signature(payload, signing_secret)
    url = f'{base_url.rstrip("/")}/download?{urlencode(payload)}'
    return SignedDownloadUrl(
        url=url,
        relative_path=relative_path,
        storage_backend=storage_backend,
        expires_at=expires_at,
        headers={
            'Cache-Control': 'private, max-age=0, no-store',
            'X-Content-Type-Options': 'nosniff',
        },
    )


def verify_signed_download_request(
    *,
    query_string: str,
    signing_secret: str,
    now: datetime,
) -> VerifiedSignedDownload:
    if len(str(signing_secret or '')) < 32:
        raise ValueError('signing_secret must be at least 32 characters')
    params = _single_value_params(query_string)
    signature = params.pop('signature', '')
    if not signature:
        raise PermissionError('signature is required')
    required = {'path', 'backend', 'expires', 'disposition', 'sha256'}
    missing = sorted(required - set(params))
    if missing:
        raise ValueError(f'missing signed URL parameters: {missing}')
    relative_path = normalize_relative_path(params['path'])
    storage_backend = params['backend']
    if storage_backend not in SIGNED_URL_STORAGE_BACKENDS:
        raise ValueError('signed URLs require object-store storage_backend')
    expires_at = _require_not_expired(params['expires'], now)
    disposition = _disposition(params['disposition'])
    sha256 = params['sha256']
    if sha256 and not is_sha256_hex(sha256):
        raise ValueError('sha256 must be a lowercase SHA-256 digest')
    payload = {
        'path': relative_path,
        'backend': storage_backend,
        'expires': expires_at,
        'disposition': disposition,
        'sha256': sha256,
    }
    expected = _signature(payload, signing_secret)
    if not hmac.compare_digest(signature, expected):
        raise PermissionError('invalid signed URL signature')
    return VerifiedSignedDownload(
        relative_path=relative_path,
        storage_backend=storage_backend,
        expires_at=expires_at,
        disposition=disposition,
        sha256=sha256,
    )


def _signature(payload: Mapping[str, str], secret: str) -> str:
    canonical = '\n'.join(f'{key}={payload[key]}' for key in sorted(payload))
    return hmac.new(secret.encode('utf-8'), canonical.encode('utf-8'), hashlib.sha256).hexdigest()


def _single_value_params(query_string: str) -> dict[str, str]:
    parsed = parse_qs(str(query_string or ''), keep_blank_values=True)
    values: dict[str, str] = {}
    for key, raw_values in parsed.items():
        if len(raw_values) != 1:
            raise ValueError(f'duplicate signed URL parameter: {key}')
        values[key] = raw_values[0]
    return values


def _require_https_base_url(value: str) -> None:
    parsed = urlparse(str(value or '').strip())
    if parsed.scheme != 'https' or not parsed.netloc:
        raise ValueError('base_url must be an HTTPS URL')
    host = (parsed.hostname or '').lower()
    if host in {'localhost', '127.0.0.1', '::1'} or host.endswith('.localhost'):
        raise ValueError('base_url must not use localhost')


def _require_not_expired(value: str, now: datetime) -> str:
    if not value:
        raise ValueError('expires_at is required')
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    expiry = datetime.fromisoformat(text)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if current.astimezone(timezone.utc) >= expiry.astimezone(timezone.utc):
        raise PermissionError('download grant has expired')
    return expiry.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _disposition(value: str) -> str:
    text = value.strip().lower()
    if text not in {'inline', 'attachment'}:
        raise ValueError('disposition must be inline or attachment')
    return text


def _required_text(mapping: Mapping, key: str) -> str:
    text = str(mapping.get(key) or '').strip()
    if not text:
        raise ValueError(f'{key} is required')
    return text
