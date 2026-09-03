"""Project role catalog SSOT loader (FE-P8, 2026-05-28).

Reads the ``rbac_role_grants`` section of ``docs/platform/central_db_schema.v1.json``
verbatim at module load time so the **only** declaration of "what platform
role X grants what permissions" is the schema JSON. No role/permission grant
lives in application code — the loader is a pure projection of the SSOT.

Why load at import:

- the role catalog is small, immutable, and required for every authz check;
- loading lazily would add an import-time-vs-first-request race;
- the schema file ships with the repo (always present in any deployment that
  composes the platform API).

Frozen-exe / packaging note: the platform API is a server-side ASGI surface
(``platform_api_app.py``), not the desktop frozen exe — the desktop build does
not import this module. The schema JSON path is resolved relative to this
file by searching upward for ``docs/platform/central_db_schema.v1.json`` — the
nearest ancestor that holds it wins — so a repo clone, a server deployment, or
an extracted package tree each finds its own copy. A fixed depth was correct in
exactly one of those: the extracted package puts this module one level
shallower and the old ``parents[3]`` pointed above the tree, failing at import.
An override env-var ``FCC_PLATFORM_SCHEMA_PATH`` lets non-standard layouts
redirect the loader.

dependency-free: stdlib ``json`` / ``pathlib`` / ``os`` only.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import FrozenSet


__all__ = [
    'GLOBAL_ROLE_KEYS',
    'PROJECT_ADMIN_ROLE_KEY',
    'PROJECT_ROLE_KEYS',
    'global_permissions_for',
    'is_known_role',
    'permissions_for',
    'role_catalog',
]


#: The permission token that distinguishes the project-admin role (the highest
#: project-scoped grant). Derived from the schema, never hardcoded — the admin
#: ROLE key itself is then resolved as "the role that grants this permission".
_PLATFORM_ADMIN_PERMISSION = 'platform:admin'


_SCHEMA_PATH_ENV = 'FCC_PLATFORM_SCHEMA_PATH'

#: Location of the schema SSOT relative to whichever tree contains this module.
_SCHEMA_RELATIVE_PATH = Path('docs') / 'platform' / 'central_db_schema.v1.json'


def _discover_schema_path() -> Path:
    """First ancestor of this module that actually holds the schema SSOT.

    A fixed ``parents[3]`` was correct in exactly one tree. This module lives at
    ``src/application/platform/rbac_role_catalog.py`` in the monorepo and at
    ``fcc_test_platform/application/rbac_role_catalog.py`` in the delivered
    package — one level shallower — so the same arithmetic pointed *above* the
    delivered tree and this module raised ``FileNotFoundError`` at import.
    Measured 2026-08-12: it took ten test files down with it, and every gate was
    green, because a data path is not an import and neither the boundary axis
    nor the dependency-resolution axis can see one.

    Searching upward asks the question that is actually being asked — *where is
    the tree I am part of* — instead of encoding one tree's depth. It is the
    same conclusion this repository reached for migration discovery and for the
    measurement-result template loader: resolving packaged artifacts by
    ``Path(__file__)`` arithmetic is the defect, not the spelling of it.
    Deterministic (nearest ancestor wins) and unchanged in the monorepo, where
    the nearest ancestor holding ``docs/platform/`` is the repository root.

    Falls back to the module's own tree root so the failure stays loud and
    names a path, rather than degrading to an empty catalog — which would 403
    every membership-based authorization check in silence.
    """
    for root in _candidate_roots():
        candidate = root / _SCHEMA_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    here = Path(__file__).resolve()
    return here.parents[min(3, len(here.parents) - 1)] / _SCHEMA_RELATIVE_PATH


def _candidate_roots() -> 'list[Path]':
    """이 스키마가 있을 수 있는 트리들 — **가까운 것부터.**

    ⚠️ **조상 탐색만으로는 배포 이미지에서 성립하지 않는다** (실측 2026-09-03).
    위 docstring 이 *"where is the tree I am part of"* 를 묻는다고 적는데,
    설치된 배포판에서는 **그 물음의 답이 없다**:

        모듈   /usr/local/lib/python3.11/site-packages/fcc_test_platform/...
        자원   /app/docs/platform/central_db_schema.v1.json

    둘은 **조상 관계가 아니다.** 휠은 site-packages 로 가고 `docs/` 는 `COPY` 로
    `/app` 에 놓이기 때문이다. 그래서 조상 탐색이 전부 헛돌고 fallback 이
    `/usr/local/lib/python3.11/docs/...` 를 가리켜 **import 시점에 죽는다.**

    ⚠️ **그 사실이 오늘까지 가려져 있었다** — compose 가
    ``FCC_PLATFORM_SCHEMA_PATH`` 를 주기 때문이다. 즉 이 코드는
    **환경변수 없이는 이미지에서 못 돈다**는 것을 아무 데도 적지 않은 채
    그 변수에 전적으로 의존하고 있었다. 그것을 지우거나 오타 내면 배포가
    import 에서 죽고, 원인은 이 파일 어디에도 안 적혀 있다.

    ⚠️ **사본을 만들지 않는다.** 이 JSON 은 SSOT 이고(`docs/platform/`),
    패키지 안으로 복사하면 두 벌이 되어 갈라진다 — 이 저장소가 오늘만
    `benchmark_harness` 에서 같은 형태를 다뤘다. 자원은 이미 이미지에 있으므로
    필요한 것은 **찾는 축을 하나 더 갖는 것**뿐이다.

    작업 디렉터리를 후보에 넣는 이유: 배포 이미지의 `WORKDIR` 이 `/app` 이고,
    그것이 `docs/` 가 실제로 놓인 트리다. 저장소 체크아웃에서도 참이다
    (거기서는 조상 탐색이 이미 답을 준다 — 이 축은 그때 쓰이지 않는다).
    """
    here = Path(__file__).resolve()
    roots: list[Path] = [*here.parents]
    try:
        cwd = Path.cwd().resolve()
    except OSError:  # pragma: no cover — 삭제된 cwd
        return roots
    # ⚠️ cwd 는 **뒤에** 둔다. 앞에 두면 운영자가 우연히 다른 트리에서 실행할 때
    # 그 트리의 스키마가 이긴다 — 「내가 속한 트리」가 먼저다.
    return roots + [cwd, *cwd.parents]


def _resolve_schema_path() -> Path:
    """Return the schema JSON path — env override or tree-relative discovery."""
    override = os.environ.get(_SCHEMA_PATH_ENV)
    if override:
        return Path(override)
    return _discover_schema_path()


def _load_role_catalog() -> dict[str, frozenset[str]]:
    """Parse ``rbac_role_grants.grants`` into ``{role_key: frozenset(permission_keys)}``.

    Raises ``FileNotFoundError`` / ``KeyError`` loudly when the schema is
    missing or malformed so a misconfigured deployment fails at import rather
    than serving an empty catalog (which would silently 403 every membership-
    based authz check).
    """
    schema_path = _resolve_schema_path()
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    grants = schema['rbac_role_grants']['grants']
    return {role_key: frozenset(permissions) for role_key, permissions in grants.items()}


def _load_global_role_catalog() -> dict[str, frozenset[str]]:
    schema_path = _resolve_schema_path()
    schema = json.loads(schema_path.read_text(encoding='utf-8'))
    grants = schema['rbac_role_grants'].get('global_role_grants', {}).get('grants', {})
    return {role_key: frozenset(permissions) for role_key, permissions in grants.items()}


# Module-level cache — loaded once at import. The catalog is small (3 roles ×
# 1-3 permissions each) so the read is microseconds and the cache eliminates
# repeated JSON parse on every authz check.
_ROLE_CATALOG: dict[str, frozenset[str]] = _load_role_catalog()
_GLOBAL_ROLE_CATALOG: dict[str, frozenset[str]] = _load_global_role_catalog()

#: Public read-only view — role_key → frozenset(permission_key). Other modules
#: import this when they need to iterate the catalog (e.g., contract tests).
def role_catalog() -> dict[str, frozenset[str]]:
    """Return a defensive copy of the role → permissions catalog."""
    return {role_key: permissions for role_key, permissions in _ROLE_CATALOG.items()}


#: Frozenset of all project-scoped role keys. Authoritative whitelist for
#: membership write requests — an unknown role_key raises ``MembershipRoleUnknownError``
#: at the service boundary (400).
PROJECT_ROLE_KEYS: FrozenSet[str] = frozenset(_ROLE_CATALOG.keys())

# Global roles are deliberately not accepted by project membership writes.
GLOBAL_ROLE_KEYS: FrozenSet[str] = frozenset(_GLOBAL_ROLE_CATALOG.keys())


def _resolve_admin_role_key() -> str:
    """Return the single role_key that grants ``platform:admin`` (ADR-0017 D3
    creator auto-admin SSOT). Derived from the schema grant graph — loud if the
    schema has zero or multiple admin-granting roles (an ambiguous catalog must
    not silently pick one)."""
    admins = sorted(
        role_key for role_key, perms in _ROLE_CATALOG.items()
        if _PLATFORM_ADMIN_PERMISSION in perms
    )
    if len(admins) != 1:
        raise ValueError(
            f'expected exactly one role granting {_PLATFORM_ADMIN_PERMISSION!r} '
            f'in rbac_role_grants, found {admins!r} — ADR-0017 D3 auto-admin '
            'cannot resolve an unambiguous admin role'
        )
    return admins[0]


#: The project-admin role key (ADR-0017 D3 — granted to a project's creator).
#: Schema-derived (the role that grants ``platform:admin``) so a schema rename
#: of the role key flows through without a code literal edit.
PROJECT_ADMIN_ROLE_KEY: str = _resolve_admin_role_key()


def is_known_role(role_key: object) -> bool:
    """True when ``role_key`` is a project-scoped role declared in the schema."""
    if role_key is None:
        return False
    return str(role_key) in _ROLE_CATALOG


def permissions_for(role_key: str) -> frozenset[str]:
    """Return the permissions granted by ``role_key`` (empty for unknown).

    Used by the offline expansion path (e.g., test fixtures); production
    authorization reads through the central ``project_member_permissions``
    view so role grants resolve in one indexed SELECT.
    """
    return _ROLE_CATALOG.get(role_key, frozenset())


def global_permissions_for(role_key: str) -> frozenset[str]:
    """Return permissions declared for a global role, never a project role."""
    return _GLOBAL_ROLE_CATALOG.get(role_key, frozenset())
