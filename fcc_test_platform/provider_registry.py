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

from fcc_test_platform.domain.ports.output.project_result_reference_provider_port import (
    ProjectResultReferenceProviderPort,
)
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


class ProviderReferenceResolverRegistry(MappingABC[str, ProjectResultReferenceProviderPort]):
    """Immutable natural-provider-id registry for reference export adapters.

    The platform service consumes a mapping-shaped resolver, while the
    composition root owns the provider implementations. Keeping this registry
    dependency-free lets the platform accept provider adapters without importing
    provider taxonomy into the service or route layer.

    ⚠️ **값 타입은 한때 ``object`` 였다.** 그 이유는 위의 「의존 없음」이라고 읽혔지만,
    실제로 그것이 막아 준 import 는 없다 — 여기 붙는
    :class:`ProjectResultReferenceProviderPort` 는 provider 분류학이 아니라 **이 레인
    자신의 domain 포트**이고, 어차피 이 레지스트리를 소비하는
    ``CentralProjectReferenceService`` 가 그 포트로 선언돼 있다. 즉 ``object`` 는
    아무것도 지키지 않으면서 「이 사전에 무엇이든 들어갈 수 있다」는 거짓만 말했고,
    그 대가로 ``api_composition`` 이 이 레지스트리를 서비스에 넘기는 자리가 타입상
    틀린 채였다(``Mapping[str, object]`` 는 ``Mapping[str, Port]`` 가 **아니다** —
    Mapping 은 값에 대해 공변이므로 ``object`` 쪽이 오히려 «상위» 타입이다).

    ⚠️ 런타임 검사는 이미 이 포트를 요구하고 있었다: 아래 ``__init__`` 이
    ``getattr(adapter, 'provider_id', '')`` 로 신원을 대조한다. 즉 선언이 검사보다
    느슨했던 자리다.
    """

    def __init__(
        self, adapters: MappingABC[str, ProjectResultReferenceProviderPort],
    ) -> None:
        normalized: dict[str, ProjectResultReferenceProviderPort] = {}
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

    def __getitem__(self, provider_id: str) -> ProjectResultReferenceProviderPort:
        return self._adapters[provider_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self._adapters)

    def __len__(self) -> int:
        return len(self._adapters)

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)
