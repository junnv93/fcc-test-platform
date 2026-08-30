"""The single source of truth for FCC platform cutover evidence."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

from fcc_test_platform.artifact_sync_evidence import artifact_sync_evidence_errors
from fcc_test_platform.db_migration_evidence import (
    central_db_migration_evidence_errors,
)
from fcc_test_platform.extraction_evidence import extraction_package_errors
from fcc_test_platform.frontend_deployment_evidence import frontend_deployment_errors
from fcc_test_platform.frontend_qa_evidence import frontend_qa_errors
from fcc_test_platform.hardware_smoke_evidence import hardware_smoke_errors
from fcc_test_platform.identity_policy import identity_policy_errors
from fcc_test_platform.idp_deployment_evidence import idp_deployment_errors
from fcc_test_platform.ingestion_execution_evidence import ingestion_execution_errors
from fcc_test_platform.performance_smoke import performance_smoke_errors
from fcc_test_platform.rbac_assignment_evidence import rbac_assignment_evidence_errors
from fcc_test_platform.report_reconstruction_evidence import (
    db_only_report_reconstruction_errors,
)
from fcc_test_platform.backup_restore_drill import backup_restore_drill_errors
from fcc_test_contracts.common.provider_service_evidence import provider_service_deployment_errors


Validator = Callable[..., list]


@dataclass(frozen=True)
class EvidenceCatalogEntry:
    """Immutable metadata and validator binding for one evidence key."""

    key: str
    canonical_filename: str
    cli_argument: str
    validator: Validator
    required_contexts: tuple[str, ...]
    completion_group: str

    @property
    def validator_name(self) -> str:
        return self.validator.__name__


EVIDENCE_CATALOG: tuple[EvidenceCatalogEntry, ...] = (
    EvidenceCatalogEntry(
        "service_deployment",
        "provider_service_deployment.json",
        "--service-deployment",
        provider_service_deployment_errors,
        (),
        "deployment_idp_frontend_browser_qa",
    ),
    EvidenceCatalogEntry(
        "hardware_smoke",
        "hardware_smoke.json",
        "--hardware-smoke",
        hardware_smoke_errors,
        (),
        "live_lab_and_platform_operations",
    ),
    EvidenceCatalogEntry(
        "db_migration",
        "db_migration.json",
        "--db-migration",
        central_db_migration_evidence_errors,
        ("central_db_schema",),
        "live_lab_and_platform_operations",
    ),
    EvidenceCatalogEntry(
        "ingestion_execution",
        "ingestion_execution.json",
        "--ingestion-execution",
        ingestion_execution_errors,
        (),
        "live_postgresql_ingestion_execution",
    ),
    EvidenceCatalogEntry(
        "artifact_sync",
        "artifact_sync.json",
        "--artifact-sync",
        artifact_sync_evidence_errors,
        (),
        "live_lab_and_platform_operations",
    ),
    EvidenceCatalogEntry(
        "db_only_report_reconstruction",
        "db_only_report_reconstruction.json",
        "--db-only-report-reconstruction",
        db_only_report_reconstruction_errors,
        (),
        "db_only_report_reconstruction",
    ),
    EvidenceCatalogEntry(
        "backup_restore_drill",
        "backup_restore_drill.json",
        "--backup-restore-drill",
        backup_restore_drill_errors,
        (),
        "live_lab_and_platform_operations",
    ),
    EvidenceCatalogEntry(
        "identity_policy",
        "identity_policy.json",
        "--identity-policy",
        identity_policy_errors,
        (),
        "deployment_idp_frontend_browser_qa",
    ),
    EvidenceCatalogEntry(
        "idp_deployment",
        "idp_deployment.json",
        "--idp-deployment",
        idp_deployment_errors,
        (),
        "deployment_idp_frontend_browser_qa",
    ),
    EvidenceCatalogEntry(
        "rbac_assignment",
        "rbac_assignment.json",
        "--rbac-assignment",
        rbac_assignment_evidence_errors,
        (),
        "live_lab_and_platform_operations",
    ),
    EvidenceCatalogEntry(
        "extraction_package",
        "extraction_package.json",
        "--extraction-package",
        extraction_package_errors,
        ("extraction_manifest",),
        "live_lab_and_platform_operations",
    ),
    EvidenceCatalogEntry(
        "performance_smoke",
        "performance_smoke.json",
        "--performance-smoke",
        performance_smoke_errors,
        (),
        "live_lab_and_platform_operations",
    ),
    EvidenceCatalogEntry(
        "frontend_deployment",
        "frontend_deployment.json",
        "--frontend-deployment",
        frontend_deployment_errors,
        (),
        "deployment_idp_frontend_browser_qa",
    ),
    EvidenceCatalogEntry(
        "frontend_browser_qa",
        "frontend_browser_qa.json",
        "--frontend-browser-qa",
        frontend_qa_errors,
        (),
        "deployment_idp_frontend_browser_qa",
    ),
)

_ALLOWED_CONTEXTS = frozenset({"central_db_schema", "extraction_manifest"})


def _validate_catalog() -> None:
    keys = [entry.key for entry in EVIDENCE_CATALOG]
    filenames = [entry.canonical_filename for entry in EVIDENCE_CATALOG]
    arguments = [entry.cli_argument for entry in EVIDENCE_CATALOG]
    if len(keys) != len(set(keys)):
        raise RuntimeError("duplicate FCC cutover evidence catalog key")
    if len(filenames) != len(set(filenames)):
        raise RuntimeError("duplicate FCC cutover evidence catalog filename")
    if len(arguments) != len(set(arguments)):
        raise RuntimeError("duplicate FCC cutover evidence catalog CLI argument")
    if any(not entry.key or not entry.canonical_filename for entry in EVIDENCE_CATALOG):
        raise RuntimeError("FCC cutover evidence catalog contains an empty identity")
    for entry in EVIDENCE_CATALOG:
        if not callable(entry.validator):
            raise RuntimeError(f"evidence catalog validator is not callable: {entry.key}")
        unknown = set(entry.required_contexts) - _ALLOWED_CONTEXTS
        if unknown:
            raise RuntimeError(
                f"evidence catalog has unknown contexts for {entry.key}: {sorted(unknown)}"
            )
        if not entry.completion_group:
            raise RuntimeError(f"evidence catalog has no completion group: {entry.key}")


_validate_catalog()

_BY_KEY: Mapping[str, EvidenceCatalogEntry] = MappingProxyType(
    {entry.key: entry for entry in EVIDENCE_CATALOG}
)
_FILENAMES: Mapping[str, str] = MappingProxyType(
    {entry.key: entry.canonical_filename for entry in EVIDENCE_CATALOG}
)
_CLI_ARGUMENTS: Mapping[str, str] = MappingProxyType(
    {entry.key: entry.cli_argument for entry in EVIDENCE_CATALOG}
)
_COMPLETION_GROUPS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        group: tuple(entry.key for entry in EVIDENCE_CATALOG if entry.completion_group == group)
        for group in dict.fromkeys(entry.completion_group for entry in EVIDENCE_CATALOG)
    }
)


def catalog_entries() -> tuple[EvidenceCatalogEntry, ...]:
    return EVIDENCE_CATALOG


def catalog_entry(key: str) -> EvidenceCatalogEntry:
    try:
        return _BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"unknown FCC cutover evidence key: {key}") from exc


def catalog_keys() -> tuple[str, ...]:
    return tuple(entry.key for entry in EVIDENCE_CATALOG)


def catalog_filenames() -> Mapping[str, str]:
    return _FILENAMES


def catalog_cli_arguments() -> Mapping[str, str]:
    return _CLI_ARGUMENTS


def catalog_completion_groups() -> Mapping[str, tuple[str, ...]]:
    return _COMPLETION_GROUPS


def catalog_validator_bindings() -> Mapping[str, Validator]:
    return MappingProxyType({entry.key: entry.validator for entry in EVIDENCE_CATALOG})
