"""Safe, atomic provenance receipts for verified FCC cutover output reuse."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from fcc_test_platform.application.platform_cutover_catalog import catalog_entry
from fcc_test_platform.evidence_primitives import sha256_bytes as _sha256_bytes


RECEIPT_SCHEMA_VERSION = 1
RECEIPT_SUFFIX = ".receipt.json"

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_PLACEHOLDER_RE = re.compile(r"^<[^<>]+>$")
_URL_OR_DSN_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*://|\b(?:DSN|JDBC|CONNECTION_STRING)\s*=)", re.IGNORECASE)
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.+$")
_SECRET_NAME_RE = re.compile(
    r"(?:SECRET|TOKEN|PASSWORD|CREDENTIAL|PRIVATE|API[_-]?KEY|CONNECTION[_-]?STRING|DSN)",
    re.IGNORECASE,
)
_APPROVED_CONTENT_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$", re.IGNORECASE)

# This is the only command/env classification vocabulary used by receipt and
# workflow-summary persistence.  Unknown values remain redacted by default.
SENSITIVE_OPTIONS = frozenset(
    {
        "--api-key",
        "--access-token",
        "--bearer-token",
        "--client-secret",
        "--connection-string",
        "--dsn",
        "--id-token",
        "--password",
        "--refresh-token",
        "--secret",
        "--token",
    }
)

_FLAG_OPTIONS = frozenset(
    {
        "--access-token-audience-verified",
        "--auth-flow-verified",
        "--browser-login-verified",
        "--copy-missing",
        "--logout-clears-session",
        "--pkce-required",
        "--public-client",
        "--redirect-state-verified",
        "--require-valid",
        "--role-claim-verified",
        "--token-exchange-verified",
    }
)

# Only values for these options are approved as non-secret metadata.  The
# value classifier still rejects URLs, DSNs, assignments, and secret-shaped
# option names before this allowlist is consulted.
_NON_SECRET_OPTIONS = frozenset(
    {
        "--alert-channel",
        "--alert-interval-seconds",
        "--alert-target",
        "--applied-by",
        "--artifact",
        "--artifact-root",
        "--backup-root",
        "--benchmark-id",
        "--browser",
        "--build-root",
        "--build-version",
        "--client-id",
        "--database-name",
        "--db-backup-file",
        "--db-path",
        "--deployed-at",
        "--destination-root",
        "--destination-root-id",
        "--evidence-id",
        "--environment-name",
        "--frontend-build-version",
        "--host-id",
        "--hosting-provider",
        "--idp-provider-key",
        "--ingestion-batch-id",
        "--iterations",
        "--job-id",
        "--log-collector",
        "--log-retention-days",
        "--max-p95-ms",
        "--migration-evidence-id",
        "--output",
        "--output-dir",
        "--plan",
        "--provider-id",
        "--provider-key",
        "--report-output",
        "--report-run-id",
        "--restore-point-id",
        "--restore-root",
        "--reviewed-by",
        "--scope",
        "--screenshot-dir",
        "--service-manager",
        "--session-id",
        "--source-root",
        "--source-root-id",
        "--start-mode",
        "--sync-job-id",
        "--target-ref",
        "--target-root",
        "--target-table",
        "--timeout-seconds",
        "--workers",
    }
)

# Raw filesystem/configuration values are never safe fingerprint inputs.  The
# option name remains part of command structure, while its value is replaced by
# a path-independent identity (or an explicitly supplied content digest).
_PATH_OPTIONS = frozenset(
    {
        "--artifact",
        "--artifact-root",
        "--backup-root",
        "--build-root",
        "--bundle-output",
        "--central-db-schema",
        "--config",
        "--db-backup-file",
        "--db-path",
        "--destination-root",
        "--evidence-root",
        "--extraction-manifest",
        "--manifest",
        "--output",
        "--output-dir",
        "--plan",
        "--records-json",
        "--report-output",
        "--restore-root",
        "--screenshot-dir",
        "--source-root",
        "--target-root",
        "--values",
        "--workflow-config",
    }
)

_NON_SECRET_ENV_NAMES = frozenset(
    {
        "FCC_CENTRAL_PROVIDER_ID",
        "FCC_HEADLESS_PROVIDER_ID",
    }
)

_COMMAND_SUBCOMMANDS = frozenset({"collect", "execute", "publish"})

VALUE_REDACTED = "<redacted>"


@dataclass(frozen=True)
class FileObservation:
    exists: bool
    size_bytes: int | None
    sha256: str | None
    mtime_ns: int | None


@dataclass(frozen=True)
class ReceiptBinding:
    provider_id: str
    cutover_candidate_id: str
    workflow_fingerprint: str
    central_db_schema_sha256: str
    extraction_manifest_sha256: str
    evidence_key: str
    canonical_filename: str
    collector_identity: str


def observe_file(path: Path) -> FileObservation:
    try:
        stat = path.stat()
    except (FileNotFoundError, OSError):
        return FileObservation(False, None, None, None)
    if not path.is_file():
        return FileObservation(False, None, None, stat.st_mtime_ns)
    try:
        content = path.read_bytes()
    except (OSError, PermissionError):
        return FileObservation(False, None, None, stat.st_mtime_ns)
    return FileObservation(
        True,
        len(content),
        _sha256_bytes(content),
        stat.st_mtime_ns,
    )


def file_sha256(path: Path) -> str:
    try:
        return _sha256_bytes(path.read_bytes())
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return ""


def is_sha256_digest(value: Any) -> bool:
    """Return whether *value* is a non-empty canonical SHA-256 hex digest."""
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def receipt_path_for(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}{RECEIPT_SUFFIX}")


def collector_identity(evidence_key: str) -> str:
    catalog_entry(evidence_key)
    return f"fcc-cutover-catalog:{evidence_key}"


def safe_workflow_fingerprint(config: Mapping[str, Any]) -> str:
    """Hash only path-independent workflow structure and approved digests.

    The fingerprint is intentionally a safe identity, not a replay credential.
    Catalog identity, command option shape, and approved non-path metadata are
    retained; arbitrary cwd values are omitted and output/path values are
    represented by stable catalog/option markers.  A low-entropy secret
    therefore cannot influence the persisted fingerprint through a
    filesystem/configuration string.
    """

    safe_steps: list[dict[str, Any]] = []
    for raw_step in config.get("steps", ()):
        if not isinstance(raw_step, Mapping):
            safe_steps.append({"invalid_step": True})
            continue
        key = str(raw_step.get("evidence_key", ""))
        try:
            canonical_filename = catalog_entry(key).canonical_filename
        except KeyError:
            canonical_filename = "<unknown>"
        safe_key = key if canonical_filename != "<unknown>" else "<unknown>"
        safe_steps.append(
            {
                "evidence_key": safe_key,
                "canonical_filename": canonical_filename,
                "timeout_seconds": raw_step.get("timeout_seconds"),
                "retries": raw_step.get("retries"),
                "retry_backoff_seconds": raw_step.get("retry_backoff_seconds"),
                "reuse_verified_output": bool(raw_step.get("reuse_verified_output", False)),
                "catalog_output": canonical_filename,
                "command_shape": _safe_command_shape(
                    raw_step.get("command", ()),
                    canonical_filename=canonical_filename,
                ),
                "environment": _safe_environment(raw_step.get("env") or raw_step.get("environment")),
            }
        )
    payload = {
        "schema_version": config.get("schema_version"),
        "provider_id": config.get("provider_id"),
        "cutover_candidate_id": config.get("cutover_candidate_id"),
        "stop_on_failure": bool(config.get("stop_on_failure", True)),
        "steps": safe_steps,
    }
    return _sha256_bytes(_canonical_json(payload).encode("utf-8"))


def build_receipt(
    *,
    binding: ReceiptBinding,
    observation: FileObservation,
    collected_at: str | None = None,
) -> dict[str, Any]:
    entry = catalog_entry(binding.evidence_key)
    if binding.canonical_filename != entry.canonical_filename:
        raise ValueError(
            f"receipt canonical filename does not match catalog key: {binding.evidence_key}"
        )
    if binding.collector_identity != collector_identity(binding.evidence_key):
        raise ValueError(
            f"receipt collector identity does not match catalog key: {binding.evidence_key}"
        )
    if not is_sha256_digest(binding.central_db_schema_sha256):
        raise ValueError("receipt requires a non-empty 64-hex central schema digest")
    if not is_sha256_digest(binding.extraction_manifest_sha256):
        raise ValueError("receipt requires a non-empty 64-hex extraction manifest digest")
    if not observation.exists or observation.size_bytes is None or not observation.sha256:
        raise ValueError("cannot create an FCC cutover receipt for missing output")
    return {
        "receipt_schema_version": RECEIPT_SCHEMA_VERSION,
        "provider_id": binding.provider_id,
        "cutover_candidate_id": binding.cutover_candidate_id,
        "workflow_fingerprint": binding.workflow_fingerprint,
        "central_db_schema_sha256": binding.central_db_schema_sha256,
        "extraction_manifest_sha256": binding.extraction_manifest_sha256,
        "evidence_key": binding.evidence_key,
        "canonical_filename": binding.canonical_filename,
        "output_size_bytes": observation.size_bytes,
        "output_sha256": observation.sha256,
        "collected_at": collected_at or _utc_now(),
        "collector_identity": binding.collector_identity,
    }


def receipt_mismatches(
    receipt: Mapping[str, Any],
    *,
    binding: ReceiptBinding,
    observation: FileObservation,
) -> tuple[dict[str, str], ...]:
    if not observation.exists:
        return (
            {
                "code": "receipt_output_missing",
                "message": "the expected output file is missing",
            },
        )
    expected = {
        "provider_id": binding.provider_id,
        "cutover_candidate_id": binding.cutover_candidate_id,
        "workflow_fingerprint": binding.workflow_fingerprint,
        "central_db_schema_sha256": binding.central_db_schema_sha256,
        "extraction_manifest_sha256": binding.extraction_manifest_sha256,
        "evidence_key": binding.evidence_key,
        "canonical_filename": binding.canonical_filename,
        "collector_identity": binding.collector_identity,
        "output_size_bytes": observation.size_bytes,
        "output_sha256": observation.sha256,
    }
    mismatches: list[dict[str, str]] = []
    for field, label in (
        ("central_db_schema_sha256", "central schema"),
        ("extraction_manifest_sha256", "extraction manifest"),
    ):
        if not is_sha256_digest(binding.__dict__[field]):
            mismatches.append(
                {
                    "code": f"receipt_{field}_invalid",
                    "message": f"current {label} digest is missing or not a 64-hex SHA-256 value",
                }
            )
    if receipt.get("receipt_schema_version") != RECEIPT_SCHEMA_VERSION:
        mismatches.append(
            {
                "code": "receipt_schema_version_mismatch",
                "message": "receipt schema version is not supported",
            }
        )
    for field, expected_value in expected.items():
        if receipt.get(field) != expected_value:
            mismatches.append(
                {
                    "code": f"receipt_{field}_mismatch",
                    "message": f"receipt {field} does not match the current run",
                }
            )
    if not isinstance(receipt.get("collected_at"), str) or not receipt.get("collected_at"):
        mismatches.append(
            {
                "code": "receipt_collected_at_missing",
                "message": "receipt collected_at is missing or invalid",
            }
        )
    return tuple(mismatches)


def atomic_write_receipt(path: Path, receipt: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (_canonical_json(dict(receipt)) + "\n").encode("utf-8")
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _safe_command_shape(command: Any, *, canonical_filename: str = "<unknown>") -> list[str]:
    if not isinstance(command, Sequence) or isinstance(command, (str, bytes)):
        return ["<invalid>"]
    shape: list[str] = []
    expects_value_for: str | None = None
    command_identity_seen = False
    for index, raw_token in enumerate(command):
        token = str(raw_token)
        if index == 0:
            shape.append("<executable>")
            continue
        if index == 1 and _is_command_identity_token(token):
            shape.append("<catalog-collector>")
            command_identity_seen = True
            continue
        if command_identity_seen and _is_command_subcommand_token(token):
            shape.append(token)
            continue
        if expects_value_for is not None:
            shape.append(_safe_command_value_marker(
                token,
                option=expects_value_for,
                canonical_filename=canonical_filename,
            ))
            expects_value_for = None
            continue
        if token.startswith("-"):
            option = token.split("=", 1)[0]
            if "=" in token:
                option, value = token.split("=", 1)
                shape.append(option)
                shape.append(_safe_command_value_marker(
                    value,
                    option=option,
                    canonical_filename=canonical_filename,
                ))
            else:
                shape.append(option)
                expects_value_for = None if option in _FLAG_OPTIONS else option
            continue
        shape.append(_safe_value_marker(token, option=None))
    return shape


def _safe_command_value_marker(
    value: str,
    *,
    option: str | None,
    canonical_filename: str,
) -> str:
    if option in _PATH_OPTIONS:
        return _path_option_identity(
            option,
            value,
            canonical_filename=canonical_filename,
        )
    return _safe_value_marker(value, option=option)


def _path_option_identity(
    option: str,
    value: str,
    *,
    canonical_filename: str,
) -> str:
    """Return a path-independent identity for an approved path option."""
    digest = _approved_content_digest(value)
    if digest:
        return f"content-digest:{digest}"
    if option == "--output" and canonical_filename != "<unknown>":
        return f"catalog-output:{canonical_filename}"
    return f"path-option:{option}"


def _safe_environment(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(name): _safe_value_marker(str(raw), option=str(name), source="environment")
        for name, raw in sorted(value.items(), key=lambda item: str(item[0]))
    }


def classify_value(value: Any, *, option: str | None = None, source: str = "command") -> str:
    """Classify a value before any receipt/summary persistence.

    The default is ``unclassified`` and its value is never hashed or copied.
    This function is the single classification seam shared by fingerprints,
    command summaries, environment summaries, and process-output handling.
    """
    text = str(value)
    if _PLACEHOLDER_RE.fullmatch(text):
        return "placeholder"
    if source == "command_identity":
        return "approved_non_secret" if _is_command_identity_token(text) else "unclassified"
    normalized_option = (option or "").split("=", 1)[0]
    if normalized_option in SENSITIVE_OPTIONS or _is_secret_name(normalized_option):
        return "redacted"
    if _URL_OR_DSN_RE.search(text) or _ASSIGNMENT_RE.fullmatch(text):
        return "redacted"
    if source == "environment":
        return (
            "approved_non_secret"
            if (option or "") in _NON_SECRET_ENV_NAMES
            else "redacted"
        )
    if source in {"stdout", "stderr"}:
        return "redacted"
    if normalized_option in _NON_SECRET_OPTIONS:
        return "approved_non_secret"
    return "unclassified"


def _safe_value_marker(
    value: str,
    *,
    option: str | None,
    source: str = "command",
) -> str:
    classification = classify_value(value, option=option, source=source)
    if classification == "placeholder":
        return value
    if classification == "approved_content_digest":
        return _approved_content_digest(value) or VALUE_REDACTED
    if classification == "approved_non_secret":
        return f"sha256:{_sha256_bytes(value.encode('utf-8'))}"
    return VALUE_REDACTED


def redact_command(command: Any) -> list[str]:
    """Return a persistence-safe command shape with default-deny values."""
    if not isinstance(command, Sequence) or isinstance(command, (str, bytes)):
        return [VALUE_REDACTED]
    redacted: list[str] = []
    expects_value_for: str | None = None
    command_identity_seen = False
    for index, raw_token in enumerate(command):
        token = str(raw_token)
        if index == 0:
            redacted.append(Path(token).name or VALUE_REDACTED)
            continue
        if index == 1 and _is_command_identity_token(token):
            redacted.append(token)
            command_identity_seen = True
            continue
        if command_identity_seen and _is_command_subcommand_token(token):
            redacted.append(token)
            continue
        if expects_value_for is not None:
            redacted.append(_redacted_value(token, option=expects_value_for))
            expects_value_for = None
            continue
        if token.startswith("-"):
            option = token.split("=", 1)[0]
            if "=" in token:
                option, value = token.split("=", 1)
                redacted.append(f"{option}={_redacted_value(value, option=option)}")
            else:
                redacted.append(option)
                expects_value_for = None if option in _FLAG_OPTIONS else option
            continue
        redacted.append(_redacted_value(token, option=None))
    return redacted


def _redacted_value(
    value: str,
    *,
    option: str | None,
    source: str = "command",
) -> str:
    classification = classify_value(value, option=option, source=source)
    if classification == "approved_content_digest":
        return _approved_content_digest(value) or VALUE_REDACTED
    if classification in {"placeholder", "approved_non_secret"}:
        return value
    if classification == "redacted" and _ASSIGNMENT_RE.fullmatch(value):
        return f"{value.split('=', 1)[0]}={VALUE_REDACTED}"
    return VALUE_REDACTED


def redact_text(text: Any) -> str:
    """Redact arbitrary text; raw collector output is never persisted."""
    return "" if text in (None, "") else VALUE_REDACTED


def redact_env(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(name): _redacted_value(str(raw), option=str(name), source="environment")
        for name, raw in sorted(value.items(), key=lambda item: str(item[0]))
    }


def redact_process_output(value: Any, *, source: str) -> str:
    if value in (None, ""):
        return ""
    classify_value(value, source=source)
    return VALUE_REDACTED


def _is_secret_name(name: str) -> bool:
    return bool(_SECRET_NAME_RE.search(name))


def _approved_content_digest(value: Any) -> str | None:
    text = str(value)
    if not _APPROVED_CONTENT_DIGEST_RE.fullmatch(text):
        return None
    return text.lower()


def _is_command_identity_token(value: str) -> bool:
    normalized = value.replace('\\', '/')
    return normalized.endswith('.py') and not normalized.startswith('-')


def _is_command_subcommand_token(value: str) -> bool:
    return value in _COMMAND_SUBCOMMANDS


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
