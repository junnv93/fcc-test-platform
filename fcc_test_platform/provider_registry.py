"""Provider registry: the platform's *content* axis over the contract *format* axis.

⚠️ **The format axis moved to ``fcc-test-contracts`` (2026-08-31) and this module
now imports it.** What a registry document must look like -- which keys are
required, which are forbidden, how a ``contract_artifact`` resolves, whether an
entry's identity matches the artifact it names, what a provider may be called --
is a contract question, and it was answered here in a copy that the contracts
lane also answered. Two answers to one question is one answer too many: the copy
here had already fallen behind (it never grew the naming rule the contracts lane
settled on the same day), and nothing was red.

The boundary that survives: **format is a contract question, content is a
platform one.** *Which* providers are registered stays here
(``config/headless_provider_registry.json``); *what a registration must look
like* comes from the dependency.

:class:`ProviderReferenceResolverRegistry` deliberately did **not** move. It is
not about registry documents at all -- it is the platform service's mapping of
provider ids to reference-export adapters, consumed by ``api_composition`` -- and
sending it to a lane whose ``depends_on`` is empty would push platform service
vocabulary into the shared kernel.
"""
from __future__ import annotations

from collections.abc import Mapping as MappingABC
from typing import Iterator

from fcc_test_contracts.headless.provider_registry import (
    FORBIDDEN_REGISTRY_KEYS,
    REQUIRED_PROVIDER_KEYS,
    ProviderRegistry,
    ProviderRegistryEntry,
    ProviderRegistryError,
    load_provider_registry,
    validate_registry_contract_identities,
)


__all__ = [
    'ProviderRegistry',
    'ProviderRegistryEntry',
    'ProviderReferenceResolverRegistry',
    'ProviderRegistryError',
    'load_provider_registry',
    'validate_registry_contract_identities',
]


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
