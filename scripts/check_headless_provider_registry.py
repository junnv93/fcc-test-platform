"""Run batch contract compatibility checks from a provider registry JSON.

Bootstrapping goes through :mod:`contract_cli` rather than hardcoding
``<root>/src``. That path is the monorepo layout; in an extracted package there
is no ``src/`` and the package sits at the tree root, so the hardcoded form made
the delivered checker unable to import itself — the same defect SPLIT-4 repaid
across the other contract CLIs, left standing here because nothing ran this one
from a staged tree.

The registry it validates is platform-owned while the batch checker it calls is
contracts-owned, so after extraction this file spans two repositories. Closing
the *shape* axis first is deliberate: which lane ends up owning the check is an
ownership decision, and it is recorded in the ledger rather than guessed at here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import contract_cli as _contract_cli  # noqa: E402


PROJECT_ROOT = _contract_cli.ensure_importable(__file__)
DEFAULT_REGISTRY = PROJECT_ROOT / 'docs' / 'api' / 'headless_provider_registry.json'


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    registry_path = Path(args[0]) if args else DEFAULT_REGISTRY
    if not registry_path.is_absolute():
        registry_path = PROJECT_ROOT / registry_path

    try:
        from fcc_test_platform.provider_registry import (
            load_provider_registry,
            validate_registry_contract_identities,
        )
        # Sibling entry point. Resolved through contract_cli because the two
        # trees disagree on whether ``scripts/`` is a package, and importing the
        # same file under both names would give it two module identities.
        batch_main = _contract_cli.sibling_module(
            __file__, 'check_headless_api_contracts_batch',
        ).main

        registry = load_provider_registry(registry_path, PROJECT_ROOT)
        validate_registry_contract_identities(registry)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({
            'compatible': False,
            'error': {
                'code': 'registry_usage_error',
                'path': str(registry_path),
                'message': str(exc),
            },
            'providers': [],
        }, sort_keys=True, indent=2, ensure_ascii=True))
        return 2

    return batch_main(registry.artifact_paths)


if __name__ == '__main__':
    raise SystemExit(main())
