"""Filesystem download proxy resolution for platform artifact/report grants."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import mimetypes
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping
from urllib.parse import quote

from fcc_test_platform.artifact_storage import normalize_relative_path
from fcc_test_platform.evidence_primitives import is_sha256_hex
from fcc_test_contracts.common.report_output_download import (
    DownloadExpiredError,
    DownloadIntegrityError,
)


__all__ = [
    'DownloadResolution',
    'content_disposition_header',
    'open_verified_stream',
    'resolve_download_grant',
    'stream_file_handle',
]

# Stream chunk size — also used by the verification hash pass so both reads of
# the same fd use the same granularity.
_STREAM_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class DownloadResolution:
    path: Path
    relative_path: str
    storage_backend: str
    byte_size: int
    sha256: str
    headers: dict[str, str]
    # FE-P6 (2026-05-29): the grant's *signed* expected sha256 (empty when the
    # grant carried none). Verification happens ONCE at stream time over the same
    # open fd (single-pass) — resolve no longer hashes, so there is no redundant
    # read and no resolve↔stream TOCTOU window.
    expected_sha: str = ''
    # Content-Type for the stream. Derived from the filename extension so an
    # ``inline`` disposition actually previews (e.g. application/pdf) instead of
    # being forced to download by a blanket octet-stream. ``attachment`` still
    # downloads (the Content-Disposition governs that) regardless of media type.
    media_type: str = 'application/octet-stream'

    def to_dict(self) -> dict:
        return {
            'path': str(self.path),
            'relative_path': self.relative_path,
            'storage_backend': self.storage_backend,
            'byte_size': self.byte_size,
            'sha256': self.sha256,
            'expected_sha': self.expected_sha,
            'media_type': self.media_type,
            'headers': dict(self.headers),
        }


def resolve_download_grant(
    *,
    grant: Mapping,
    storage_roots: Mapping[str, Path | str],
    now: datetime,
) -> DownloadResolution:
    """Resolve a download grant to a file path under an injected storage root."""

    relative_path = normalize_relative_path(str(grant.get('relative_path') or ''))
    storage_backend = _required_text(grant, 'storage_backend')
    if storage_backend != 'filesystem':
        raise ValueError('only filesystem storage_backend is supported by this resolver')
    _require_not_expired(str(grant.get('expires_at') or ''), now)
    root = _storage_root(storage_roots, storage_backend)
    path = (root / relative_path).resolve()
    _require_within_root(path, root)
    if not path.is_file():
        raise FileNotFoundError(relative_path)
    # Single-pass integrity: resolve does NOT hash the file. The grant's signed
    # sha256 is carried as ``expected_sha`` and verified exactly once at stream
    # time over the streamed fd (``open_verified_stream``) — eliminating the
    # redundant resolve-time full read AND the resolve↔stream TOCTOU window.
    expected_sha = _expected_sha(grant)
    disposition = _disposition(str(grant.get('disposition') or 'attachment'))
    return DownloadResolution(
        path=path,
        relative_path=relative_path,
        storage_backend=storage_backend,
        byte_size=path.stat().st_size,
        # sha256 mirrors the grant's signed digest (the value the stream verifies
        # the bytes against); '' when the grant carried none.
        sha256=expected_sha,
        expected_sha=expected_sha,
        media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
        headers={
            'Content-Disposition': content_disposition_header(disposition, path.name),
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'private, max-age=0, no-store',
        },
    )


def _require_not_expired(expires_at: str, now: datetime) -> None:
    if not expires_at:
        raise ValueError('expires_at is required')
    text = expires_at.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    expiry = datetime.fromisoformat(text)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if current.astimezone(timezone.utc) >= expiry.astimezone(timezone.utc):
        raise DownloadExpiredError('download grant has expired')


def _storage_root(storage_roots: Mapping[str, Path | str], storage_backend: str) -> Path:
    if storage_backend not in storage_roots:
        raise ValueError(f'missing storage root for {storage_backend}')
    return Path(storage_roots[storage_backend]).resolve()


def _require_within_root(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError('resolved path escapes storage root') from exc


def _expected_sha(grant: Mapping) -> str:
    metadata = grant.get('metadata')
    if not isinstance(metadata, Mapping):
        return ''
    value = str(metadata.get('sha256') or '').strip()
    if value and not is_sha256_hex(value):
        raise ValueError('grant metadata sha256 must be a lowercase SHA-256 digest')
    return value


def _ascii_fallback_filename(filename: str) -> str:
    """RFC 6266 ASCII ``filename`` fallback — no quote/backslash/control/8-bit.

    Characters that would break a quoted-string token (``"`` ``\\``), control
    bytes, and any non-ASCII (e.g. Korean report names) collapse to ``_`` so the
    legacy ``filename="..."`` parameter stays well-formed for old clients; modern
    clients prefer the RFC 5987 ``filename*`` parameter built alongside it.
    """
    out = []
    for ch in filename:
        codepoint = ord(ch)
        if codepoint < 0x20 or codepoint == 0x7F or ch in '"\\' or codepoint > 0x7F:
            out.append('_')
        else:
            out.append(ch)
    return ''.join(out) or 'download'


def content_disposition_header(disposition: str, filename: str) -> str:
    """Build an RFC 6266 Content-Disposition with an RFC 5987 ``filename*``.

    Always emits the ASCII ``filename="..."`` fallback; appends the
    percent-encoded UTF-8 ``filename*`` only when the original name differs from
    the ASCII fallback (i.e. it had non-ASCII or special characters), so plain
    ASCII names keep a clean single-parameter header.
    """
    ascii_name = _ascii_fallback_filename(filename)
    header = f'{disposition}; filename="{ascii_name}"'
    if filename != ascii_name:
        header += f"; filename*=UTF-8''{quote(filename, safe='')}"
    return header


def open_verified_stream(path: str | Path, *, expected_sha: str) -> BinaryIO:
    """Open ``path`` once, verify its sha256, and return the fd positioned at 0.

    TOCTOU-safe streaming: the bytes hashed here and the bytes the caller streams
    come from the **same open file description**, so a swap/truncate between a
    prior resolve-time hash and the actual stream cannot serve unverified bytes.

    Raises ``FileNotFoundError`` if the file vanished (TOCTOU between grant and
    stream → HTTP 404) and ``DownloadIntegrityError`` if the bytes do not match
    the signed ``expected_sha`` → HTTP 409. When ``expected_sha`` is empty the
    grant carried no digest and only the open (existence) is enforced. The fd is
    closed on any failure; on success ownership transfers to the caller (use
    :func:`stream_file_handle`).
    """
    handle = Path(path).open('rb')  # FileNotFoundError if the file vanished
    try:
        if expected_sha:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(_STREAM_CHUNK_BYTES), b''):
                digest.update(chunk)
            if digest.hexdigest() != expected_sha:
                raise DownloadIntegrityError(
                    'download stream sha256 does not match the granted bytes'
                )
            handle.seek(0)
        return handle
    except BaseException:
        handle.close()
        raise


def stream_file_handle(
    handle: BinaryIO, *, chunk_size: int = _STREAM_CHUNK_BYTES,
) -> Iterator[bytes]:
    """Yield chunks from an open fd (e.g. from :func:`open_verified_stream`).

    Closes the handle when iteration completes or the consumer aborts.
    """
    try:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        handle.close()


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
