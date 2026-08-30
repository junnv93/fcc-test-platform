"""Dependency-free provider registry loading for shared headless contracts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from collections.abc import Mapping as MappingABC
from pathlib import Path
from typing import Iterable, Iterator


__all__ = [
    'ProviderRegistry',
    'ProviderRegistryEntry',
    'ProviderReferenceResolverRegistry',
    'ProviderRegistryError',
    'load_provider_registry',
    'validate_registry_contract_identities',
]


FORBIDDEN_REGISTRY_KEYS = frozenset({'routes', 'schemas', 'operations'})
REQUIRED_PROVIDER_KEYS = (
    'provider_id',
    'product_line',
    'contract_family',
    'contract_artifact',
)


class ProviderRegistryError(ValueError):
    """Raised when a provider registry document is invalid."""


class ProviderReferenceResolverRegistry(MappingABC[str, object]):
    """Immutable natural-provider-id registry for reference export adapters.

    The platform service consumes a mapping-shaped resolver, while the
    composition root owns the provider implementations. Keeping this registry
    dependency-free lets the platform accept provider adapters without importing
    provider taxonomy into the service or route layer.
    """

    def __init__(self, adapters: MappingABC[str, object]) -> None:
        normalized: dict[str, object] = {}
        for provider_id, adapter in adapters.items():
            key = str(provider_id).strip()
            if not key:
                raise ProviderRegistryError('reference resolver provider_id is required')
            adapter_id = str(getattr(adapter, 'provider_id', '')).strip()
            if adapter_id != key:
                raise ProviderRegistryError(
                    f'reference resolver identity mismatch for {key!r}'
                )
            if key in normalized:
                raise ProviderRegistryError(f'duplicate reference resolver: {key}')
            normalized[key] = adapter
        self._adapters = dict(sorted(normalized.items()))

    def __getitem__(self, provider_id: str) -> object:
        return self._adapters[provider_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)


@dataclass(frozen=True)
class ProviderRegistryEntry:
    provider_id: str
    product_line: str
    contract_family: str
    contract_artifact: str
    resolved_contract_artifact: Path

    def to_dict(self) -> dict:
        return {
            'provider_id': self.provider_id,
            'product_line': self.product_line,
            'contract_family': self.contract_family,
            'contract_artifact': self.contract_artifact,
            'resolved_contract_artifact': str(self.resolved_contract_artifact),
        }

    def validate_contract_identity(self) -> None:
        contract = json.loads(self.resolved_contract_artifact.read_text(encoding='utf-8'))
        if not isinstance(contract, dict):
            raise ProviderRegistryError(
                f'{self.provider_id} contract artifact must be a JSON object'
            )
        provider = contract.get('provider')
        if not isinstance(provider, dict):
            raise ProviderRegistryError(
                f'{self.provider_id} contract artifact is missing provider metadata'
            )
        _require_matching_provider_field(self, provider, 'provider_id')
        _require_matching_provider_field(self, provider, 'product_line')
        _require_matching_provider_field(self, provider, 'contract_family')


@dataclass(frozen=True)
class ProviderRegistry:
    registry_version: int
    providers: tuple[ProviderRegistryEntry, ...]

    @property
    def artifact_paths(self) -> list[str]:
        return [str(provider.resolved_contract_artifact) for provider in self.providers]

    def to_dict(self) -> dict:
        return {
            'registry_version': self.registry_version,
            'providers': [provider.to_dict() for provider in self.providers],
        }


def load_provider_registry(registry_path: Path, project_root: Path) -> ProviderRegistry:
    """Load and validate a provider registry JSON file."""
    registry_path = Path(registry_path)
    if not registry_path.is_absolute():
        registry_path = Path(project_root) / registry_path

    document = json.loads(registry_path.read_text(encoding='utf-8'))
    return _parse_registry(document, registry_path, Path(project_root))


def validate_registry_contract_identities(registry: ProviderRegistry) -> None:
    """Ensure registry identities match each referenced contract artifact."""
    for provider in registry.providers:
        provider.validate_contract_identity()


def _parse_registry(
    document: object,
    registry_path: Path,
    project_root: Path,
) -> ProviderRegistry:
    if not isinstance(document, dict):
        raise ProviderRegistryError('provider registry must be a JSON object')

    _reject_forbidden_keys(document.keys(), 'registry')

    providers = document.get('providers')
    if not isinstance(providers, list) or not providers:
        raise ProviderRegistryError('provider registry is empty')

    entries = tuple(
        _parse_provider(provider, index, registry_path, project_root)
        for index, provider in enumerate(providers)
    )
    _reject_duplicates(entries)

    return ProviderRegistry(
        registry_version=int(document.get('registry_version', 1)),
        providers=entries,
    )


def _parse_provider(
    provider: object,
    index: int,
    registry_path: Path,
    project_root: Path,
) -> ProviderRegistryEntry:
    if not isinstance(provider, dict):
        raise ProviderRegistryError(f'providers[{index}] must be an object')

    _reject_forbidden_keys(provider.keys(), f'providers[{index}]')
    for key in REQUIRED_PROVIDER_KEYS:
        if not _text(provider.get(key)):
            raise ProviderRegistryError(f'providers[{index}].{key} is required')

    contract_artifact = _text(provider['contract_artifact'])
    resolved = _resolve_artifact_path(registry_path, project_root, contract_artifact)
    if not resolved.exists():
        raise ProviderRegistryError(
            f'providers[{index}].contract_artifact does not exist: {resolved}'
        )

    return ProviderRegistryEntry(
        provider_id=_text(provider['provider_id']),
        product_line=_text(provider['product_line']),
        contract_family=_text(provider['contract_family']),
        contract_artifact=contract_artifact,
        resolved_contract_artifact=resolved,
    )


def _reject_forbidden_keys(keys: Iterable[str], label: str) -> None:
    forbidden = sorted(FORBIDDEN_REGISTRY_KEYS.intersection(keys))
    if forbidden:
        raise ProviderRegistryError(
            f"{label} must not duplicate contract details: {', '.join(forbidden)}"
        )


def _reject_duplicates(entries: tuple[ProviderRegistryEntry, ...]) -> None:
    _reject_duplicate_value(entries, 'provider_id')
    _reject_duplicate_value(entries, 'product_line')


def _reject_duplicate_value(
    entries: tuple[ProviderRegistryEntry, ...],
    field_name: str,
) -> None:
    seen: set[str] = set()
    for entry in entries:
        value = getattr(entry, field_name)
        if value in seen:
            raise ProviderRegistryError(f'duplicate provider registry {field_name}: {value}')
        seen.add(value)


def _require_matching_provider_field(
    entry: ProviderRegistryEntry,
    provider: dict,
    field_name: str,
) -> None:
    expected = getattr(entry, field_name)
    actual = _text(provider.get(field_name))
    if actual != expected:
        raise ProviderRegistryError(
            f'{entry.provider_id}.{field_name} mismatch: '
            f'expected {expected!r}, got {actual!r}'
        )


def _resolve_artifact_path(
    registry_path: Path,
    project_root: Path,
    artifact: str,
) -> Path:
    path = Path(artifact)
    if path.is_absolute():
        return path
    candidate = project_root / path
    if candidate.exists():
        return candidate
    return registry_path.parent / path


def _text(value: object) -> str:
    if value is None:
        return ''
    return str(value).strip()
