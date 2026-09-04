# -*- coding: utf-8 -*-
"""Central hub containerization invariants (B1/P13 — central-docker-compose).

Seals the architectural decision that ONLY the central hub is containerized
(Platform API + Headless API + web nginx + PostgreSQL + Keycloak), while chamber
nodes stay native because of their GPIB/USB/Nuitka-Windows-exe dependencies.

The load-bearing guard: the central container import path MUST be win32-free.
Three complementary seals (so a regression cannot slip through a single blind
spot):

1. ``TestCentralImportClosureWin32Free`` — RUNTIME proof. Builds both central
   ASGI factories (``headless_api_app:create_app`` / ``platform_api_app:create_app``)
   in a fresh subprocess and asserts no forbidden win32 module landed in
   ``sys.modules`` (winsound / thread_sampler GetThreadTimes / appium
   subprocess / GUI entrypoints). This is exactly what ``uvicorn --factory``
   executes at container start.
2. ``TestCentralStaticImportClosure`` — STATIC proof (no deps, cross-platform).
   Walks the first-party transitive module-level import closure of the central
   entrypoints and asserts it is disjoint from the win32-dependent modules.
3. ``TestCentralRequirementsPurity`` / ``TestCentralComposeContract`` — the
   image dependency set and the compose/env wiring stay consistent with the
   Python runtime-config SSOT (no hardcoded ports, no desktop/device packages).
"""
from __future__ import annotations

# ⚠️ 2026-09-04 — 여기 있던 네 개의 지역 `import yaml` 중 셋은 `except: skipTest` 가드를
# 달고 있었다. 그 가드는 **컨테이너 보안 불변식(네트워크 격리 · 권한 드롭 · 신뢰 홉 ·
# 빌드 대상 파생)을 스스로 꺼버리는** 장치였다 — PyYAML 이 없으면 검사가 실패하는 대신
# 조용히 통과한다. 이 저장소의 `lane_check` 이 같은 형태를 이미 이름 붙여 거부한다:
# *"기준선을 관측값으로 덮어써서 초록을 만들지 마라 — 그것은 검사를 끄는 것이다."*
#
# 나머지 하나(`_load_compose`)는 가드가 없어서 **중앙 저장소 CI 를 24건 빨갛게** 만들고
# 있었다(run 33858657216). 같은 파일이 같은 의존성을 두 가지 방식으로 동시에 잘못 다루고
# 있었던 셈이다 — 한쪽은 검사를 끄고, 다른 쪽은 러너를 세운다.
#
# 답은 가드를 늘리는 것이 아니라 **선언을 채우는 것**이다. PyYAML 은 이제
# `[project.optional-dependencies].test` 에 있고 `tests/test_supply_closure_axis.py` 가
# 그 선언과 이 import 를 파생 대조한다. PyYAML 이 없으면 이 파일은 **실패한다** —
# 그것이 옳다. 검사할 수 없는 상태는 통과가 아니다.
import yaml

import ast
import json
import re
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

# The prod gateway's prefix coverage is derived from the SAME helper the dev
# gateway seal uses (tests/test_apps_web_scaffold.py). Two gateways, one
# derivation — when each had its own, fixing /report-automation in dev taught
# the prod seal nothing and the identical hole stayed open here.
from support.api_prefixes import (
    BACKEND_ROUTE_TABLES,
    all_backend_prefixes,
    prefixes_by_surface,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = PROJECT_ROOT / 'src'
INFRA = PROJECT_ROOT / 'infra'
COMPOSE_PATH = INFRA / 'docker-compose.central.yml'
ENV_EXAMPLE_PATH = INFRA / 'central' / 'central.env.example'
KEYCLOAK_REALM = INFRA / 'keycloak' / 'fcc-dev-realm.json'
DOCKERFILE_API = INFRA / 'central' / 'Dockerfile.api'
#: Headless-only image (2026-09-03). The central PC no longer holds this
#: repository, so `fcc-test-platform`'s compose consumes the headless image BY
#: TAG with no `build:` stanza — it has to be built here and handed over.
DOCKERFILE_HEADLESS = INFRA / 'central' / 'Dockerfile.headless'
#: ⚠️ ``Dockerfile.web`` 는 여기 없다. ``apps/web`` 이 2026-08-31 레인 분리로
#: ``fcc-test-platform`` 으로 이사했으므로 web 이미지는 **거기서** 빌드된다.
#: 이 저장소가 빌드하는 중앙 이미지는 API 하나뿐이다.
REQUIREMENTS_CENTRAL = PROJECT_ROOT / 'requirements-central.txt'
#: Frontend runtime-config Zod schema SSOT. The central template's rendered
#: payload must satisfy this schema (field set + allowed values) or the SPA
#: fails its boot-time fail-fast parse.
FRONTEND_RUNTIME_TS = PROJECT_ROOT / 'apps' / 'web' / 'src' / 'config' / 'runtime.ts'

#: Frontend source the session-surface boundary is sealed against. The central
#: hub serves only /headless + /platform; the Session API (`/session/*`) is a
#: single-chamber-node surface the hub does not proxy, so the SPA must disable
#: it (runtime-config `sessionApiEnabled: false`) and the default landing /
#: Control route must not call it. These paths anchor that coherence.
WEB_SRC = PROJECT_ROOT / 'apps' / 'web' / 'src'
OVERVIEW_TSX = WEB_SRC / 'routes' / 'overview.tsx'
# tester-ux Phase S-diag: the gated /session/info call moved out of the landing
# into the [설정]>진단 route. Phase H then replaced the landing with the "오늘 할 일"
# home, so diagnostics is no longer delegated from '/' — it is a standalone
# gated route registered in app.tsx + linked from the [설정] nav group.
DIAGNOSTICS_TSX = WEB_SRC / 'routes' / 'diagnostics.tsx'
APP_TSX = WEB_SRC / 'app.tsx'
CONTROL_TSX = WEB_SRC / 'routes' / 'control.tsx'
LAYOUT_TSX = WEB_SRC / 'routes' / '_layout.tsx'
SESSION_CLIENT_TS = WEB_SRC / 'api' / 'session-client.ts'
SESSION_EVENTS_TS = WEB_SRC / 'api' / 'session-events.ts'
HEADLESS_CLIENT_TS = WEB_SRC / 'api' / 'headless-client.ts'
PLATFORM_CLIENT_TS = WEB_SRC / 'api' / 'platform-client.ts'
DEV_STACK_JSON = PROJECT_ROOT / 'apps' / 'web' / 'dev-stack.config.json'

#: Surface key → the compose service that serves it in the central hub.
#:
#: This is the ONLY prod-specific fact in the gateway topology. Which prefixes a
#: surface exposes comes from the backend route tables, and which port it
#: listens on comes from the dev SSOT — so :func:`gateway_proxy_routes` below is
#: fully derived. It replaces a hand-written ``GATEWAY_PROXY_ROUTES`` 2-tuple
#: (gate-and-deploy-path-parity, 2026-08-01) whose whole failure mode was that a
#: prefix nobody typed into it was a prefix nothing checked: ``/report-automation``
#: existed on the headless surface, was called by the SPA, and was invisible here.
#:
#: ⚠️ 게이트웨이 topology 를 재던 검사들은 2026-09-01 에 이 파일을 떠났다 —
#: ``nginx.conf`` 가 ``fcc-test-platform`` 소유가 됐기 때문이다(운영자 판정 b).
#: 아래 매핑은 compose 의 upstream 서비스명을 쓰는 곳에만 남아 있다.
CENTRAL_UPSTREAM_SERVICE = {
    'headless': 'headless-api',
    'platform': 'platform-api',
}


def gateway_proxy_routes() -> tuple[tuple[str, str], ...]:
    """Derived (prefix, ``service:port``) pairs the central gateway must route."""
    surfaces = json.loads(DEV_STACK_JSON.read_text(encoding='utf-8'))['surfaces']
    port_by_surface = {s['key']: s['port'] for s in surfaces}
    routes: list[tuple[str, str]] = []
    for surface, prefixes in sorted(prefixes_by_surface().items()):
        service = CENTRAL_UPSTREAM_SERVICE.get(surface)
        if service is None:
            continue
        for prefix in sorted(prefixes):
            routes.append((prefix, f'{service}:{port_by_surface[surface]}'))
    return tuple(routes)

#: The two ASGI factory modules a central container serves with uvicorn.
CENTRAL_ENTRYPOINTS = ('headless_api_app', 'platform_api_app')

#: Module name substrings that pull in win32-only behaviour. If any appears in
#: the central import path the container would either crash on Linux or carry a
#: silent platform dependency:
#:   * winsound / sound_player          — Windows-only audio (ui beep)
#:   * thread_sampler                   — ctypes.windll GetThreadTimes probe
#:   * appium / device_session_manager  — appium subprocess (chamber-node only)
#:   * main_entry                       — GUI process entrypoint (splash/winsound)
FORBIDDEN_WIN32_SUBSTRINGS = (
    'winsound',
    'sound_player',
    'thread_sampler',
    'appium',
    'device_session_manager',
    'main_entry',
)

#: Desktop / chamber-node packages that must NEVER be in the lean central image.
DESKTOP_ONLY_PACKAGES = (
    'PySide6',
    'pyvisa',
    'pyvisa-py',
    'selenium',
    'Appium-Python-Client',
    'ntplib',
    'pythonping',
    'pyinstaller',
)


# --------------------------------------------------------------------------- #
# Static import-closure machinery (dependency-free, cross-platform)
# --------------------------------------------------------------------------- #
def _resolve_first_party(dotted: str) -> Path | None:
    rel = dotted.replace('.', '/')
    module = SRC_ROOT / (rel + '.py')
    if module.is_file():
        return module
    pkg = SRC_ROOT / rel / '__init__.py'
    if pkg.is_file():
        return pkg
    return None


def _all_import_targets(file: Path) -> set[str]:
    """Every absolute import target in the file (module + function level)."""
    targets: set[str] = set()
    tree = ast.parse(file.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.add(node.module)
            for alias in node.names:
                targets.add(f'{node.module}.{alias.name}')
    return targets


def _module_level_import_targets(file: Path) -> set[str]:
    """Top-level (unconditionally executed) absolute import targets only."""
    targets: set[str] = set()
    tree = ast.parse(file.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            targets.add(node.module)
            for alias in node.names:
                targets.add(f'{node.module}.{alias.name}')
    return targets


def _central_static_closure() -> dict[str, str]:
    """First-party transitive module-level closure of the central path.

    Seeds = the entry modules + everything they import anywhere (this captures
    the composition roots that ``create_app`` imports at function level —
    ``headless_api_composition`` / ``platform_api_composition`` / ``bootstrap`` /
    ``application.platform.runtime_config``). From those seeds we follow
    module-level imports only (those execute unconditionally when the module is
    imported, i.e. at container start). Returns ``{module: importer}``.
    """
    seeds: set[str] = set(CENTRAL_ENTRYPOINTS)
    for entry in CENTRAL_ENTRYPOINTS:
        resolved = _resolve_first_party(entry)
        assert resolved is not None, f'{entry} 가 src/ 에 없다 — seed 갱신 필요.'
        seeds |= _all_import_targets(resolved)

    closure: dict[str, str] = {}
    stack = [(s, '<seed>') for s in seeds]
    while stack:
        name, importer = stack.pop()
        if name in closure:
            continue
        resolved = _resolve_first_party(name)
        if resolved is None:
            continue  # third-party — closure tracks first-party only
        closure[name] = importer
        for target in _module_level_import_targets(resolved):
            if target not in closure:
                stack.append((target, name))
    return closure


def _violations(modules) -> list[str]:
    return [
        m for m in modules
        if any(sub in m for sub in FORBIDDEN_WIN32_SUBSTRINGS)
    ]


# --------------------------------------------------------------------------- #
# 1. Runtime proof — build both factories in a fresh subprocess
# --------------------------------------------------------------------------- #
# Executed inside the subprocess. Builds the two central apps with a minimal env
# (SQLite headless DB + dummy postgres DSN — platform connects lazily) and
# prints any forbidden win32 module that ended up imported. A non-empty result
# (or a build error) fails the parent assertion.
_SUBPROCESS_PROBE = r'''
import sys, os, tempfile, json
forbidden_subs = {forbidden!r}
env = dict(os.environ)
td = tempfile.mkdtemp()
env["FCC_HEADLESS_DB_PATH"] = os.path.join(td, "h.fcc.db")
env["FCC_HEADLESS_AUTH_MODE"] = "disabled"
env["FCC_HEADLESS_ALLOW_INSECURE"] = "1"
env["FCC_CENTRAL_DB_URL"] = "postgresql://u:p@localhost:5432/fcc"
# Fail-closed in production and therefore required here. `env` starts as
# `dict(os.environ)`, so before this line the probe passed only on a machine
# whose shell happened to export it — the defect this file's sibling
# `tests/test_ambient_env_hermeticity.py` now makes impossible.
env["FCC_CENTRAL_PROVIDER_ID"] = "probe-provider"
env["FCC_PLATFORM_AUTH_MODE"] = "disabled"
env["FCC_PLATFORM_ALLOW_INSECURE"] = "1"
os.environ.update(env)
import headless_api_app, platform_api_app
headless_api_app.create_app(env)
platform_api_app.create_app(env)
hits = sorted(
    m for m in sys.modules
    if any(sub in m for sub in forbidden_subs)
)
print("RESULT:" + json.dumps(hits))
'''




# --------------------------------------------------------------------------- #
# 2. Static proof — module-level closure disjoint from win32 modules
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 3. Image dependency purity
# --------------------------------------------------------------------------- #
class TestCentralRequirementsPurity(unittest.TestCase):
    """The lean central image excludes every desktop/chamber-node package."""

    def _requirement_names(self) -> list[str]:
        names = []
        for raw in REQUIREMENTS_CENTRAL.read_text(encoding='utf-8').splitlines():
            line = raw.split('#', 1)[0].strip()
            if not line or line.startswith('-'):
                continue
            # strip version/extras: "uvicorn[standard]>=0.46.0" -> "uvicorn"
            name = line.split('>=')[0].split('==')[0].split('[')[0].strip()
            names.append(name)
        return names

    def test_requirements_file_exists(self):
        self.assertTrue(
            REQUIREMENTS_CENTRAL.is_file(),
            'requirements-central.txt (central image dependency SSOT) 부재.',
        )

    def test_no_desktop_only_packages(self):
        names = {n.lower() for n in self._requirement_names()}
        leaked = sorted(
            pkg for pkg in DESKTOP_ONLY_PACKAGES if pkg.lower() in names
        )
        self.assertEqual(
            leaked, [],
            f'requirements-central.txt 가 데스크톱/챔버-노드 전용 패키지를 포함: '
            f'{leaked}. 중앙 컨테이너는 win32/instrument/device 의존을 가지면 안 된다.',
        )

    def test_does_not_chain_full_desktop_requirements(self):
        # A "-r requirements.txt" / "-r requirements-web.txt" line would silently
        # pull PySide6/pyvisa/appium back in, defeating the lean boundary.
        text = REQUIREMENTS_CENTRAL.read_text(encoding='utf-8')
        for chained in ('-r requirements.txt', '-r requirements-web.txt'):
            self.assertNotIn(
                chained, text,
                f'requirements-central.txt must not chain {chained!r} — it '
                'would re-introduce the desktop/device packages.',
            )

    def test_core_web_surface_packages_present(self):
        names = {n.lower() for n in self._requirement_names()}
        for required in ('fastapi', 'uvicorn', 'psycopg', 'sqlalchemy'):
            self.assertIn(
                required, names,
                f'central image is missing required web-surface dep {required!r}.',
            )


# --------------------------------------------------------------------------- #
# 4. Compose / env wiring ↔ Python runtime-config SSOT
# --------------------------------------------------------------------------- #
class TestCentralComposeContract(unittest.TestCase):
    """The compose + env file stay consistent with the env-name SSOT and carry
    no hardcoded ports."""

    # ⚠️ `platform-api-node` 는 장치 서비스가 아니라 **같은 이미지의 두 번째 인스턴스**다
    # (2026-09-04, 평문 HTTP 잠정 형상). 인증 모드가 프로세스당 하나라서 브라우저용
    # `local_jwt` 와 노드용 `oidc_jwt` 를 한 프로세스에 담을 수 없다. 인증서가
    # 발급되면 이 항목과 그 서비스를 함께 지운다 — 「챔버 노드는 네이티브로 남는다」는
    # 원칙은 그대로다.
    EXPECTED_SERVICES = {
        'postgres', 'keycloak', 'central-migrate', 'headless-api', 'platform-api',
        'platform-api-node', 'web',
    }

    def _compose(self) -> dict:
        return yaml.safe_load(COMPOSE_PATH.read_text(encoding='utf-8'))

    def test_compose_file_exists(self):
        self.assertTrue(COMPOSE_PATH.is_file(), 'docker-compose.central.yml 부재.')
        self.assertTrue(ENV_EXAMPLE_PATH.is_file(), 'central.env.example 부재.')

    def test_compose_defines_exactly_the_central_services(self):
        compose = self._compose()
        services = set(compose.get('services', {}))
        self.assertEqual(
            services, self.EXPECTED_SERVICES,
            f'central stack services drifted: {services} != {self.EXPECTED_SERVICES}. '
            'Chamber nodes stay native — do not add device services here.',
        )

    def test_central_migrate_is_a_one_shot_init_job_gating_platform_api(self):
        """central-migrate applies the schema before platform-api serves.

        It must be a one-shot (restart: no — NOT unless-stopped, it runs once and
        exits), still privilege-hardened, and platform-api must wait for it to
        COMPLETE so the API never serves against an un-migrated DB
        (central-db-migration-runner, 2026-06-26)."""
        compose = self._compose()
        migrate = compose['services']['central-migrate']
        self.assertEqual(
            migrate.get('restart'), 'no',
            'central-migrate is a one-shot job — restart must be "no", not unless-stopped.',
        )
        self.assertIn(
            'no-new-privileges:true', migrate.get('security_opt', []) or [],
            'central-migrate must keep the no-new-privileges hardening.',
        )
        command = ' '.join(str(part) for part in migrate.get('command', []) or [])
        self.assertIn('platform_db_migrate.py', command)
        self.assertIn('migrate', command)
        gate = compose['services']['platform-api'].get('depends_on', {})
        self.assertEqual(
            gate.get('central-migrate', {}).get('condition'),
            'service_completed_successfully',
            'platform-api must wait for central-migrate to COMPLETE before serving.',
        )

    def test_no_initdb_001_mount_runner_owns_schema(self):
        """The postgres docker-entrypoint-initdb 001 mount is removed — the
        central-migrate runner is the single schema-application path (initdb only
        runs on a fresh volume and never applies 002+)."""
        volumes = self._compose()['services']['postgres'].get('volumes', []) or []
        offenders = [v for v in volumes if 'docker-entrypoint-initdb.d' in str(v)]
        self.assertEqual(
            offenders, [],
            f'postgres must not mount initdb SQL ({offenders}); the central-migrate '
            'job applies all migrations in order instead.',
        )

    def _python_env_ssot(self) -> dict:
        sys.path.insert(0, str(SRC_ROOT))
        try:
            from fcc_test_platform.central_db_config import CENTRAL_DB_ENV
            from fcc_test_platform.application.headless.runtime_config import HEADLESS_API_ENV
            from fcc_test_platform.application.runtime_config import (
                PLATFORM_AUTH_ENV_PREFIX,
            )
        finally:
            sys.path.remove(str(SRC_ROOT))
        return {
            'database_url': CENTRAL_DB_ENV['database_url'],   # FCC_CENTRAL_DB_URL
            'provider_id': CENTRAL_DB_ENV['provider_id'],     # FCC_CENTRAL_PROVIDER_ID
            'db_path': HEADLESS_API_ENV['db_path'],           # FCC_HEADLESS_DB_PATH
            'platform_auth': f'{PLATFORM_AUTH_ENV_PREFIX}AUTH_MODE',
        }

    def test_env_file_uses_python_env_name_ssot(self):
        """Env names in central.env.example must match the Python SSOT constants
        (no ad-hoc env-name drift between compose and runtime-config).

        ``FCC_CENTRAL_DB_URL`` is intentionally excluded here — it is *assembled*
        in compose from the POSTGRES_* credential SSOT, not declared in the env
        file (sealed by ``test_central_db_url_assembled_from_postgres_parts``)."""
        ssot = self._python_env_ssot()
        env_text = ENV_EXAMPLE_PATH.read_text(encoding='utf-8')
        required_names = {
            ssot['provider_id'],     # FCC_CENTRAL_PROVIDER_ID
            ssot['db_path'],         # FCC_HEADLESS_DB_PATH
            ssot['platform_auth'],   # FCC_PLATFORM_AUTH_MODE
        }
        missing = sorted(n for n in required_names if n not in env_text)
        self.assertEqual(
            missing, [],
            f'central.env.example is missing Python-SSOT env name(s): {missing}.',
        )

    def test_ports_are_env_parameterized_not_hardcoded(self):
        """Every published host port must come from an env var (${VAR:-default})
        so the env file is the single port SSOT."""
        compose = self._compose()
        offenders = []
        for name, svc in compose.get('services', {}).items():
            for mapping in svc.get('ports', []) or []:
                host_side = str(mapping).split(':', 1)[0]
                if '${' not in host_side:
                    offenders.append((name, mapping))
        self.assertEqual(
            offenders, [],
            f'hardcoded host port(s) in compose: {offenders}. Reference the '
            'central.env.example port vars instead.',
        )

    def test_headless_is_consumed_and_platform_builds_its_own_image(self):
        """⚠️ **2026-09-03 뒤집힘 — 한 이미지가 셋을 겸하던 시절의 단언이었다.**

        옛 판은 ``headless-api`` 와 ``platform-api`` 가 **같은 이미지**여야 한다고
        단언했다. 그것이 참이던 동안 그 이미지의 빌드 컨텍스트가 provider 저장소
        루트였고, **그것 하나 때문에 중앙 PC 가 provider 저장소를 요구했다.**

        지금 형상은 그 반대다 — provider 저장소가 headless 이미지를 빌드·태그하고
        이 compose 는 `build:` 없이 소비만 한다(`web` 이 2026-08-31 에 간 길의
        거울상). 그러므로 **두 이미지가 달라야 하고**, 같아지면 그것이 회귀다.
        """
        compose = self._compose()
        services = compose.get('services', {})
        headless = services.get('headless-api', {})
        platform = services.get('platform-api', {})
        self.assertNotEqual(
            headless.get('image'), platform.get('image'),
            'headless-api 와 platform-api 가 같은 이미지를 쓴다 — 한 이미지가 두 '
            '레인을 겸하던 옛 형상으로 되돌아갔다는 뜻이다.',
        )
        self.assertNotIn(
            'build', headless,
            'headless-api 에 build 키가 생겼다 — 그 이미지의 소스는 provider '
            f'저장소에 있고 이 저장소는 빌드할 수 없다. 발견: {sorted(headless)!r}',
        )
        self.assertIn('build', platform, 'platform-api 가 자기 이미지를 빌드하지 않는다')
        # The commands must select the two distinct ASGI factories.
        self.assertIn('headless_api_app:create_app', ' '.join(headless.get('command', [])))
        # ⚠️ ``fcc_test_platform.api_app``, 옛 top-level ``platform_api_app`` 이 아니다.
        # 2026-08-31 레인 분리로 그 표면이 ``src/`` 를 떠났고, compose 는 이미
        # 새 이름을 쓰는데 이 단언만 옛 이름에 남아 선재 red 였다.
        self.assertIn(
            'fcc_test_platform.api_app:create_app',
            ' '.join(platform.get('command', [])),
        )

    # --- DB DSN single-source guard (Codex finding #2) ---------------------- #
    @staticmethod
    def _env_defaults() -> dict:
        """Parse ``KEY=value`` defaults from central.env.example."""
        out = {}
        for raw in ENV_EXAMPLE_PATH.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, val = line.partition('=')
            out[key.strip()] = val.strip()
        return out

    def _platform_db_url_value(self) -> str:
        """Raw (uninterpolated) FCC_CENTRAL_DB_URL value from platform-api."""
        compose = self._compose()
        env = compose['services']['platform-api'].get('environment', {})
        self.assertIn(
            'FCC_CENTRAL_DB_URL', env,
            'platform-api must receive FCC_CENTRAL_DB_URL.',
        )
        return str(env['FCC_CENTRAL_DB_URL'])

    def test_env_file_has_single_db_credential_source(self):
        """The DB credentials live ONLY in POSTGRES_* — the env file must not
        also declare a literal FCC_CENTRAL_DB_URL (that would be a second source
        that goes stale on credential rotation)."""
        defaults = self._env_defaults()
        for required in ('POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB'):
            self.assertIn(
                required, defaults,
                f'central.env.example must declare {required} (credential SSOT).',
            )
        self.assertNotIn(
            'FCC_CENTRAL_DB_URL', defaults,
            'central.env.example must NOT set FCC_CENTRAL_DB_URL — it is '
            'assembled in compose from POSTGRES_*. A literal DSN here duplicates '
            'the credentials and goes stale when POSTGRES_PASSWORD rotates.',
        )

    def test_central_db_url_assembled_from_postgres_parts(self):
        """platform-api's DSN must be ASSEMBLED from the POSTGRES_* SSOT via
        interpolation — never a hardcoded credential literal."""
        dsn = self._platform_db_url_value()
        for part in ('${POSTGRES_USER', '${POSTGRES_PASSWORD', '${POSTGRES_DB'):
            self.assertIn(
                part, dsn,
                f'FCC_CENTRAL_DB_URL must reference {part} so the DSN derives '
                f'from the POSTGRES_* credential SSOT. Got: {dsn!r}',
            )

    def test_db_url_fallback_defaults_match_postgres_defaults(self):
        """The ``${POSTGRES_*:-default}`` fallbacks embedded in the assembled DSN
        must equal the POSTGRES_* defaults in the env file, so the two default
        sources cannot silently drift."""
        import re
        dsn = self._platform_db_url_value()
        defaults = self._env_defaults()
        fallbacks = dict(re.findall(r'\$\{(POSTGRES_[A-Z]+):-([^}]*)\}', dsn))
        for key in ('POSTGRES_USER', 'POSTGRES_PASSWORD', 'POSTGRES_DB'):
            self.assertIn(key, fallbacks, f'{key} fallback missing in DSN.')
            self.assertEqual(
                fallbacks[key], defaults.get(key),
                f'{key} default drift: DSN fallback {fallbacks[key]!r} != '
                f'env default {defaults.get(key)!r}.',
            )


# --------------------------------------------------------------------------- #
# 5. Web SPA runtime-config derives from the env port SSOT (Codex finding #1)
# --------------------------------------------------------------------------- #




# --------------------------------------------------------------------------- #
# 6. Same-origin API gateway (browser → API reachability without CORS)
# --------------------------------------------------------------------------- #
def _env_example_defaults() -> dict:
    """Parse ``KEY=value`` defaults from central.env.example (module helper)."""
    out = {}
    for raw in ENV_EXAMPLE_PATH.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        out[key.strip()] = val.strip()
    return out


def _central_web_origin() -> str:
    """The single browser origin the central stack serves, derived from the env
    SSOT (PUBLIC_HOST + WEB_PORT). This is what the realm must allow and what the
    SPA's same-origin API calls target."""
    d = _env_example_defaults()
    return f"http://{d['PUBLIC_HOST']}:{d['WEB_PORT']}"


def _load_compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding='utf-8'))




# --------------------------------------------------------------------------- #
# 7. Keycloak redirect / webOrigins + OIDC issuer split-horizon
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# 8. Web SPA runtime-config ↔ frontend Zod schema parity (Codex finding #1/#2)
# --------------------------------------------------------------------------- #








def _frontend_schema_region() -> str:
    """The ``runtimeConfigObjectSchema = z.object({ ... })`` region of runtime.ts.

    The exported ``runtimeConfigSchema`` is a ZodEffects wrapper (superRefine
    enforces the transport policy, which depends on a sibling field), so the
    object literal — the thing this parser needs — lives on the base schema.
    """
    text = FRONTEND_RUNTIME_TS.read_text(encoding='utf-8')
    start = text.index('runtimeConfigObjectSchema = z')
    end = text.index('export type RuntimeConfig', start)
    return text[start:end]


def _frontend_schema_keys() -> set[str]:
    """Top-level object keys (4-space indent) declared in runtimeConfigSchema."""
    import re
    return set(re.findall(r'^    (\w+):', _frontend_schema_region(), re.MULTILINE))


def _frontend_environment_name_enum() -> list[str]:
    """The ``environmentName: z.enum([...])`` allowed values from runtime.ts."""
    import re
    m = re.search(
        r"environmentName:\s*z\.enum\(\[([^\]]+)\]\)", _frontend_schema_region()
    )
    assert m is not None, (
        'could not extract environmentName z.enum([...]) from runtime.ts — '
        'the parser drifted from the frontend schema shape.'
    )
    return re.findall(r"'([^']+)'", m.group(1))




# --------------------------------------------------------------------------- #
# 9. Session-surface boundary — /session is not served centrally, and the SPA
#    is coherent with that (the rejected P0: the default route called /session
#    while nginx fell it through to the SPA index.html / HTML 404).
# --------------------------------------------------------------------------- #


#: ``location`` blocks that reverse-proxy, as (prefix, exact_match) pairs.
#:
#: Two things this pattern gets right that the previous one did not
#: (gate-and-deploy-path-parity, 2026-08-01):
#:
#: * ``[\w-]`` instead of ``\w`` — ``\w`` is ``[A-Za-z0-9_]`` and has no hyphen,
#:   so ``location /report-automation/`` was **unparseable**. Adding the block to
#:   nginx.conf would not have moved this set, and the frozen equality assertion
#:   below would have kept passing: the seal both required the hole and could not
#:   see it closed. Measured before the fix — the old pattern returned
#:   ``{'/headless'}`` for a two-block sample, the new one returns both.
#: * ``(=\s+)?`` and the optional trailing slash — an exact-match location
#:   (``location = /health``) is the correct shape for a bare single-segment
#:   endpoint, and the old pattern skipped those entirely.














# --------------------------------------------------------------------------- #
# 10. Real-WSL-Docker-smoke regressions (B1/P13 iteration 1) — three P0 startup
#     failures the first real `docker compose up` surfaced, each sealed so it
#     cannot silently regress:
#       (1) Keycloak 25 realm import rejects unknown `_comment*` JSON fields.
#       (2) oidc_jwt APIs need an audience (derived from OIDC_CLIENT_ID SSOT).
#       (3) headless SQLite data volume must be writable by the non-root appuser.
# --------------------------------------------------------------------------- #
def _walk_underscore_keys(obj, path='') -> list[str]:
    """Every dict key starting with ``_`` anywhere in the JSON tree (these are
    documentation/comment fields Keycloak's RealmRepresentation does not define
    — they are exactly the unknown properties KC25 import rejects)."""
    bad: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.startswith('_'):
                bad.append(f'{path}/{key}')
            bad += _walk_underscore_keys(value, f'{path}/{key}')
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            bad += _walk_underscore_keys(value, f'{path}[{i}]')
    return bad


class TestCentralKeycloakRealmImportable(unittest.TestCase):
    """The realm import JSON must carry NO comment-style fields. Keycloak 25's
    ``--import-realm`` deserializes into ``RealmRepresentation`` and rejects
    unknown properties, so an injected ``_comment_lifespans`` / ``_comment_origins``
    / protocol-mapper ``_comment`` makes the keycloak container exit(1) — which is
    exactly what the first real WSL docker smoke hit. Documentation lives in
    infra/README.md + these docstrings, never in the import payload."""

    def test_realm_is_valid_json(self):
        import json
        json.loads(KEYCLOAK_REALM.read_text(encoding='utf-8'))  # raises on drift

    def test_realm_has_no_unknown_comment_fields(self):
        import json
        realm = json.loads(KEYCLOAK_REALM.read_text(encoding='utf-8'))
        offenders = _walk_underscore_keys(realm)
        self.assertEqual(
            offenders, [],
            'Keycloak 25 realm import rejects unknown properties — remove the '
            f'underscore-prefixed comment field(s) {offenders} from the import '
            'JSON (document them in infra/README.md instead, or the keycloak '
            'container exits(1) on import).',
        )

    def test_keycloak_healthcheck_targets_management_port(self):
        """Keycloak 25 serves /health/ready on the MANAGEMENT port (9000), not the
        http port (8080) — probing :8080 returns 404 so the container would never
        report healthy (the real WSL smoke caught this). The healthcheck must
        target :9000."""
        compose = _load_compose()
        test = compose['services']['keycloak']['healthcheck']['test']
        cmd = ' '.join(test) if isinstance(test, list) else str(test)
        self.assertIn('/health/ready', cmd, 'keycloak healthcheck must probe /health/ready.')
        self.assertIn(
            '127.0.0.1/9000', cmd,
            'keycloak healthcheck must hit the management port 9000 (KC25 health '
            f'endpoint) — got: {cmd!r}. Port 8080 returns 404 for /health/ready.',
        )
        self.assertNotIn(
            '127.0.0.1/8080', cmd,
            'keycloak healthcheck must NOT probe :8080 for health (404 in KC25).',
        )


class TestCentralOidcAudienceDerivation(unittest.TestCase):
    """``oidc_jwt`` principal-resolver construction requires issuer AND audience
    AND jwks_uri (application/common/principal_resolver.py). compose set issuer +
    JWKS but not the audience, so both API factories crashed at startup with
    "oidc_jwt auth requires issuer, audience, and jwks_uri". The audience is the
    OIDC client id and must derive from the single OIDC_CLIENT_ID SSOT — never a
    duplicated literal that can drift from the browser runtime's client id."""

    _AUDIENCE_KEYS = (
        ('headless-api', 'FCC_HEADLESS_OIDC_AUDIENCE'),
        ('platform-api', 'FCC_PLATFORM_OIDC_AUDIENCE'),
    )

    def test_both_api_services_set_oidc_audience(self):
        compose = _load_compose()
        for svc, key in self._AUDIENCE_KEYS:
            env = compose['services'][svc].get('environment', {})
            self.assertIn(
                key, env,
                f'{svc} must set {key} — oidc_jwt auth fails fast without an '
                'audience (issuer + jwks_uri alone are insufficient).',
            )

    def test_audience_derives_from_oidc_client_id_ssot(self):
        compose = _load_compose()
        for svc, key in self._AUDIENCE_KEYS:
            value = str(compose['services'][svc]['environment'][key])
            self.assertIn(
                '${OIDC_CLIENT_ID', value,
                f'{svc} {key} must derive from the OIDC_CLIENT_ID SSOT (got '
                f'{value!r}) — a hardcoded audience literal would drift from the '
                'browser runtime client id.',
            )

    def test_audience_fallback_default_matches_env_client_id_default(self):
        """The ``${OIDC_CLIENT_ID:-default}`` fallback embedded in the audience
        must equal the OIDC_CLIENT_ID default in the env file, so the two default
        sources cannot silently drift."""
        import re
        defaults = _env_example_defaults()
        env_default = defaults.get('OIDC_CLIENT_ID')
        self.assertIsNotNone(
            env_default, 'central.env.example must declare OIDC_CLIENT_ID.'
        )
        compose = _load_compose()
        for svc, key in self._AUDIENCE_KEYS:
            value = str(compose['services'][svc]['environment'][key])
            m = re.search(r'\$\{OIDC_CLIENT_ID:-([^}]*)\}', value)
            self.assertIsNotNone(
                m, f'{svc} {key} must use ${{OIDC_CLIENT_ID:-default}} form.'
            )
            self.assertEqual(
                m.group(1), env_default,
                f'{svc} {key} fallback {m.group(1)!r} != env OIDC_CLIENT_ID '
                f'default {env_default!r} — default drift.',
            )


class TestCentralHeadlessDataVolumeWritable(unittest.TestCase):
    """The headless API writes its SQLite DB under a named volume as the non-root
    appuser (uid 10001). A fresh named volume mountpoint is root-owned, so the
    image must pre-create the data dir owned by appuser BEFORE the mount (Docker
    seeds an empty named volume from the image dir, ownership included) — else
    headless-api crashes with "sqlite3.OperationalError: unable to open database
    file". Seals the Dockerfile ownership boundary against that regression."""

    def _headless_data_dir(self) -> str:
        """The parent dir of FCC_HEADLESS_DB_PATH (default) — the dir that must be
        writable, and the path the named volume mounts at."""
        import posixpath
        defaults = _env_example_defaults()
        db_path = defaults.get(
            'FCC_HEADLESS_DB_PATH', '/data/headless/headless.fcc.db'
        )
        return posixpath.dirname(db_path)

    def test_dockerfile_creates_and_chowns_headless_data_dir(self):
        import re
        data_dir = self._headless_data_dir()
        text = DOCKERFILE_API.read_text(encoding='utf-8')
        self.assertIn(
            f'mkdir -p {data_dir}', text,
            f'Dockerfile.api must pre-create the headless data dir {data_dir} '
            '(so the empty named volume inherits it).',
        )
        self.assertIn(
            'chown', text,
            'Dockerfile.api must chown the data dir to appuser.',
        )
        # The chown must cover the data dir, and the chown+mkdir must precede the
        # USER switch (so they run as root while the image is being built).
        self.assertIn(
            data_dir, text.split('USER appuser')[0],
            f'{data_dir} chown/mkdir must come BEFORE `USER appuser` (root-time).',
        )
        self.assertRegex(
            text,
            rf'chown[^\n]*appuser[^\n]*{re.escape(data_dir)}',
            f'Dockerfile.api chown must target {data_dir} for appuser.',
        )

    def test_compose_mounts_named_volume_at_the_data_dir(self):
        """The headless-api volume must mount the named volume at exactly the dir
        the Dockerfile pre-owns (else the ownership seeding does not apply)."""
        data_dir = self._headless_data_dir()
        compose = _load_compose()
        mounts = compose['services']['headless-api'].get('volumes', []) or []
        target_dirs = [str(m).split(':')[1] for m in mounts if ':' in str(m)]
        self.assertIn(
            data_dir, target_dirs,
            f'headless-api must mount a volume at {data_dir} (the dir the image '
            f'pre-owns for appuser); got mounts {mounts}.',
        )




class TestRealmPlatformPermissionParity(unittest.TestCase):
    """Cross-language SSOT: every ``platform:*`` permission the Keycloak realm
    emits (group attributes + machine-client hardcoded claim) must be exactly the
    set the backend authorizes (``PLATFORM_API_PERMISSIONS`` in
    ``application/central_contract/api_contracts.py``). Without this gate the realm's
    permission strings are an unchecked hand-copy of the Python SSOT — a rename
    on either side silently breaks chamber/operator auth (the realm grants a
    token a permission the API never checks, or the API checks one the realm
    never mints). Mirrors the B3 cross-language parity discipline
    (``tests/support/parity.py``)."""

    @staticmethod
    def _realm_platform_tokens() -> set:
        realm = json.loads(KEYCLOAK_REALM.read_text(encoding='utf-8'))
        tokens: set = set()
        for group in realm.get('groups', []):
            for perm in group.get('attributes', {}).get('permissions', []):
                if str(perm).startswith('platform:'):
                    tokens.add(str(perm))
        for client in realm.get('clients', []):
            for mapper in client.get('protocolMappers', []):
                config = mapper.get('config', {})
                if config.get('claim.name') == 'permissions':
                    for tok in str(config.get('claim.value', '')).split():
                        if tok.startswith('platform:'):
                            tokens.add(tok)
        return tokens

    @staticmethod
    def _backend_platform_tokens() -> set:
        if str(SRC_ROOT) not in sys.path:
            sys.path.insert(0, str(SRC_ROOT))
        from fcc_test_kernel.application.central_contract.api_contracts import PLATFORM_API_PERMISSIONS
        return {v for v in PLATFORM_API_PERMISSIONS.values() if v.startswith('platform:')}

    def test_realm_platform_permissions_match_backend_ssot(self):
        from tests.support.parity import assert_set_equality
        assert_set_equality(
            self,
            name='platform RBAC permissions',
            left_label='Keycloak realm (groups + machine client)',
            left=self._realm_platform_tokens(),
            right_label='backend PLATFORM_API_PERMISSIONS',
            right=self._backend_platform_tokens(),
        )

    def test_client_descriptions_within_keycloak_column_limit(self):
        """Keycloak's CLIENT.DESCRIPTION column is VARCHAR(255); a longer
        description aborts the whole realm import (the server fails to start).
        Keep client descriptions short — detail lives in the runbook/exec-plan."""
        realm = json.loads(KEYCLOAK_REALM.read_text(encoding='utf-8'))
        offenders = [
            (c.get('clientId'), len(c.get('description', '')))
            for c in realm.get('clients', [])
            if len(c.get('description', '')) > 255
        ]
        self.assertEqual(
            offenders, [],
            f'client description(s) exceed Keycloak 255-char limit: {offenders}',
        )

    def test_client_secrets_are_env_placeholders_not_raw(self):
        """Non-public realm clients must carry their secret as a ``${ENV}``
        placeholder (substituted at import) — never a raw literal in the
        committed realm file (chamber-token-rbac runbook: secrets are never
        committed)."""
        realm = json.loads(KEYCLOAK_REALM.read_text(encoding='utf-8'))
        offenders = []
        for client in realm.get('clients', []):
            if client.get('publicClient'):
                continue
            secret = client.get('secret')
            if secret is not None and not (
                secret.startswith('${') and secret.endswith('}')
            ):
                offenders.append(client.get('clientId'))
        self.assertEqual(
            offenders, [],
            f'realm client(s) carry a raw secret literal (must be ${{ENV}}): {offenders}',
        )


# --------------------------------------------------------------------------- #
# 11. Operational hardening (central-ops-hardening, 2026-06-25) — the central
#     stack runs as the ALWAYS-ON hub on the 중앙 PC (WSL), so it carries the
#     on-premises hardening EMS (equipment_management_system) proved out, with
#     its INTENT ported (not its Node/Next compose structure):
#       * restart: unless-stopped (survive 중앙 PC reboot/crash)
#       * security_opt no-new-privileges (block in-container priv-escalation)
#       * postgres cap_drop ALL + minimal cap_add (least privilege)
#       * two-tier network (data-network internal:true holds postgres; the
#         browser/node-facing services live on app-network; platform-api bridges
#         both). The membership must keep the inter-service DNS the other seals
#         depend on reachable (platform-api↔postgres, API↔keycloak, web↔API).
# --------------------------------------------------------------------------- #
class TestCentralOperationalHardening(unittest.TestCase):
    """Always-on central-PC operational hardening of the compose stack."""

    ALL_SERVICES = ('postgres', 'keycloak', 'headless-api', 'platform-api', 'web')

    #: EMS base.yml restores exactly these for the official postgres image's
    #: entrypoint (chown/chmod of PGDATA + gosu user switch). Nothing more.
    POSTGRES_REQUIRED_CAPS = {'CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETGID', 'SETUID'}

    def _svc(self, name: str) -> dict:
        return _load_compose()['services'][name]

    def _service_networks(self, name: str) -> set:
        """The set of networks a service is attached to (list or mapping form)."""
        nets = self._svc(name).get('networks', []) or []
        if isinstance(nets, dict):
            return set(nets.keys())
        return set(nets)

    # --- restart policy (always-on) ----------------------------------------- #
    def test_all_services_restart_unless_stopped(self):
        for name in self.ALL_SERVICES:
            self.assertEqual(
                self._svc(name).get('restart'), 'unless-stopped',
                f'{name} must set restart: unless-stopped so the always-on '
                'central hub recovers after a 중앙 PC reboot or container crash.',
            )

    # --- privilege-escalation block ----------------------------------------- #
    def test_all_services_disable_new_privileges(self):
        for name in self.ALL_SERVICES:
            opts = self._svc(name).get('security_opt', []) or []
            self.assertIn(
                'no-new-privileges:true', opts,
                f'{name} must set security_opt no-new-privileges:true.',
            )

    # --- postgres least-privilege caps -------------------------------------- #
    def test_postgres_drops_all_caps(self):
        cap_drop = set(self._svc('postgres').get('cap_drop', []) or [])
        self.assertIn(
            'ALL', cap_drop,
            'postgres must cap_drop ALL (least privilege) — only the minimal '
            'entrypoint caps are restored via cap_add.',
        )

    def test_postgres_restores_only_minimal_caps(self):
        cap_add = set(self._svc('postgres').get('cap_add', []) or [])
        self.assertEqual(
            cap_add, self.POSTGRES_REQUIRED_CAPS,
            f'postgres cap_add must be exactly {sorted(self.POSTGRES_REQUIRED_CAPS)} '
            '(the official image entrypoint needs chown/chmod + gosu setuid/gid); '
            f'got {sorted(cap_add)}. Adding more widens the attack surface.',
        )

    # --- two-tier network topology ------------------------------------------ #
    def test_two_tier_networks_are_defined(self):
        nets = _load_compose().get('networks', {}) or {}
        self.assertIn('data-network', nets, 'data-network (DB tier) must be defined.')
        self.assertIn('app-network', nets, 'app-network (service tier) must be defined.')

    def test_data_network_is_internal(self):
        nets = _load_compose().get('networks', {}) or {}
        self.assertTrue(
            (nets.get('data-network') or {}).get('internal') is True,
            'data-network must be internal: true so the central DB has no '
            'internet egress and is unreachable from the LAN except via the API.',
        )

    def test_single_central_bridge_is_removed(self):
        """The old undifferentiated single `central` bridge must be gone — the
        two-tier split replaces it."""
        nets = _load_compose().get('networks', {}) or {}
        self.assertNotIn(
            'central', nets,
            'the single `central` network must be replaced by the data/app tiers.',
        )
        for name in self.ALL_SERVICES:
            self.assertNotIn(
                'central', self._service_networks(name),
                f'{name} must not attach to the removed `central` network.',
            )

    def test_service_network_membership(self):
        expected = {
            'postgres': {'data-network'},
            'keycloak': {'app-network'},
            'headless-api': {'app-network'},
            'platform-api': {'data-network', 'app-network'},
            'web': {'app-network'},
        }
        for name, want in expected.items():
            self.assertEqual(
                self._service_networks(name), want,
                f'{name} network membership drifted: {self._service_networks(name)} '
                f'!= {want}.',
            )

    # --- membership ↔ inter-service DNS reachability coherence --------------- #
    # docker compose resolves a service name by DNS only between containers that
    # SHARE at least one network. These assert the membership keeps the DNS the
    # other seals rely on reachable (a wrong split would 401/503 at runtime, not
    # in YAML parsing — so seal it here).
    def _share_a_network(self, a: str, b: str) -> bool:
        return bool(self._service_networks(a) & self._service_networks(b))

    def test_platform_api_can_reach_postgres(self):
        self.assertTrue(
            self._share_a_network('platform-api', 'postgres'),
            'platform-api must share a network with postgres (the FCC_CENTRAL_DB_URL '
            'DSN resolves the `postgres` service name) — else the DSN 503s.',
        )

    def test_apis_can_reach_keycloak_for_jwks(self):
        for api in ('headless-api', 'platform-api'):
            self.assertTrue(
                self._share_a_network(api, 'keycloak'),
                f'{api} must share a network with keycloak (the JWKS URL resolves '
                'the internal `keycloak:8080` name) — else every token 401s.',
            )

    def test_web_gateway_can_reach_both_apis(self):
        for api in ('headless-api', 'platform-api'):
            self.assertTrue(
                self._share_a_network('web', api),
                f'web must share a network with {api} (nginx reverse-proxies it by '
                'service name) — else the gateway 502s.',
            )

    def test_headless_is_isolated_from_db_tier(self):
        """headless uses SQLite, not postgres — it must NOT be on the DB tier
        (least exposure: only platform-api bridges into data-network)."""
        self.assertNotIn(
            'data-network', self._service_networks('headless-api'),
            'headless-api has no postgres dependency — keep it off data-network.',
        )


# --------------------------------------------------------------------------- #
# 12. Production gateway prefix coverage, derived (gate-and-deploy-path-parity,
#     2026-08-01). The dev gateway got this seal in PR #79; prod did not, and
#     the identical hole (/report-automation unproxied) survived here.
# --------------------------------------------------------------------------- #

#: Closed vocabulary of reasons a backend prefix may be left unproxied. Each
#: reason MUST have a witness below — an exclusion whose truth nothing checks is
#: just a mute button, and the mute button is how the seal dies.
_EXCLUSION_REASONS = frozenset({'runtime-flag-disabled'})






#: ``${VAR:-default}`` 에서 default 를 뽑는 유일한 자리. compose 보간 전체를 재구현하지
#: 않는다 — 이 파일이 묻는 것은 *기본값들 사이의 산술*뿐이고, 두 소비자가 같은 표현을
#: 쓰는지는 아래에서 **문자열 그대로** 비교해 해소 없이 답한다.
_COMPOSE_DEFAULT = re.compile(r'^\$\{[A-Z0-9_]+:-(?P<default>[^}]*)\}$')


def _compose_default(expression: str) -> str:
    match = _COMPOSE_DEFAULT.match(str(expression).strip())
    assert match, f'{expression!r} 이 ${{VAR:-default}} 형태가 아니다'
    return match.group('default')


class TestTheTrustedHopIsOneDeclaredAddress(unittest.TestCase):
    """peer 축 신뢰 hop (`peer-axis-trusted-hop`, 2026-08-22).

    ``FORWARDED_ALLOW_IPS`` 가 없으면 uvicorn 은 X-Forwarded-For 를 읽지 않고, 이
    게이트웨이 뒤의 **모든 호출자가 rate-limit 버킷 하나**를 공유한다. 그 상태는
    오류를 내지 않으므로 봉인이 아니면 아무도 모른다.

    ⚠️ 신뢰 대상은 **대역이 아니라 한 주소**다. 대역을 신뢰하면 브리지 게이트웨이
    주소까지 신뢰되고, 게시 포트 연결이 그 주소로 보이는 배포에서는 체인 전체가
    신뢰 대상이 되어 uvicorn 이 **최좌측(공격자) 값**으로 폴백한다
    (``tests/test_proxy_trust_policy.py`` 가 그 폴백을 실행으로 단언한다).
    """

    TRUST_VAR = 'FORWARDED_ALLOW_IPS'

    def _compose(self) -> dict:
        return _load_compose()

    def _api_services(self) -> list:
        """uvicorn 을 띄우는 서비스 — **compose 에서 파생**한다.

        ⚠️ **초판은 ``('headless-api', 'platform-api')`` 손 목록이었다.** 코드 축은
        같은 이유로 이미 glob 파생이었는데(진입점이 하나 늘어도 봉인이 따라가도록),
        배포 축이 그 손 목록을 되살렸다. 적대 평가가 실행으로 보였다 — 게시 포트를 갖고
        ``FORWARDED_ALLOW_IPS`` 가 **없는** 세 번째 API 서비스를 넣어도 이 클래스는
        전부 초록이었다.
        """
        services = self._compose()['services']
        found = [
            name for name, spec in sorted(services.items())
            if any('uvicorn' in str(token) for token in (spec.get('command') or []))
        ]
        self.assertGreaterEqual(
            len(found), 2,
            f'compose 에서 uvicorn 서비스를 못 찾았다: {sorted(services)}',
        )
        return found

    def test_the_service_census_is_derived_and_not_a_hand_list(self):
        """비-공허성 — 파생이 실제로 알려진 두 표면을 집는다."""
        self.assertEqual(
            ['headless-api', 'platform-api', 'platform-api-node'], self._api_services(),
        )

    def _proxy_expression(self) -> str:
        networks = self._compose()['services']['web']['networks']
        self.assertIsInstance(
            networks, dict,
            'web 는 app-network 에 정적 주소를 고정해야 하므로 map 형이어야 한다',
        )
        return networks['app-network']['ipv4_address']

    def test_every_api_surface_names_the_trusted_hop(self):
        compose = self._compose()
        for service in self._api_services():
            with self.subTest(service=service):
                environment = compose['services'][service]['environment']
                self.assertIn(
                    self.TRUST_VAR, environment,
                    f'{service} 가 신뢰 hop 을 명시하지 않으면 uvicorn 기본값(127.0.0.1) 이 '
                    f'살고 peer 예산이 배포 단위로 접힌다.',
                )

    def test_the_trusted_hop_is_verbatim_the_proxy_address_expression(self):
        """해소하지 않고 **표현 그대로** 비교한다 — 같은 표현은 드리프트할 수 없다."""
        proxy = self._proxy_expression()
        compose = self._compose()
        for service in self._api_services():
            with self.subTest(service=service):
                self.assertEqual(
                    proxy,
                    compose['services'][service]['environment'][self.TRUST_VAR],
                    f'{service} 가 신뢰하는 주소와 nginx 가 실제로 쓰는 주소가 갈라졌다.',
                )

    def test_the_app_network_declares_its_subnet(self):
        """선언되지 않은 대역은 이름 댈 수 없다 — 도커가 생성 순서로 배정한다."""
        ipam = self._compose()['networks']['app-network'].get('ipam') or {}
        config = ipam.get('config') or []
        self.assertEqual(1, len(config), 'app-network 는 subnet 을 정확히 하나 선언한다')
        self.assertIn('subnet', config[0])

    def test_the_proxy_address_is_inside_the_declared_subnet_and_is_not_a_reserved_one(self):
        """⚠️ 리터럴 둘을 나란히 두고 눈으로 맞추지 않는다 — **산술**로 판정한다."""
        import ipaddress

        subnet_expr = self._compose()['networks']['app-network']['ipam']['config'][0]['subnet']
        subnet = ipaddress.ip_network(_compose_default(subnet_expr))
        proxy = ipaddress.ip_address(_compose_default(self._proxy_expression()))

        self.assertIn(proxy, subnet, f'{proxy} 가 선언 대역 {subnet} 밖이다')
        self.assertNotEqual(proxy, subnet.network_address, '네트워크 주소는 호스트가 아니다')
        self.assertNotEqual(
            proxy, subnet.broadcast_address, '브로드캐스트 주소는 호스트가 아니다',
        )
        self.assertNotEqual(
            proxy, next(subnet.hosts()),
            '브리지 게이트웨이 주소(.1)를 프록시에 주면 게이트웨이와 프록시가 구분되지 '
            '않는다 — 게시 포트로 들어온 트래픽이 신뢰받게 된다.',
        )

    def test_the_dynamic_pool_excludes_the_proxy_address(self):
        """⚠️ 동적 할당이 이 주소를 **먼저 가져가면** `web` 의 정적 요청이 깨진다.

        실측(2026-08-22): /24 에 컨테이너를 아홉 개 붙이자 아홉 번째가 `.10` 을 받았고
        그 뒤 `web` 이 `Address already in use` 로 실패했다.

        ⚠️ **그때의 처방(`aux_addresses`)이 같은 오류를 영구화했다** (적대 평가 3R).
        aux 예약은 *"동적 할당만 피해라"* 가 아니라 *"그 주소는 사용 중이다"* 라서
        같은 주소를 **명시적으로 요청하는** 컨테이너도 거부된다 — 즉 `web` 이 한
        번도 뜨지 못한다. 옛 봉인은 컴포즈 **소스 텍스트**만 보고 그 키의 존재를
        요구했으므로, 컨테이너를 한 번도 띄우지 않은 채로 **결함을 요구**했다.

        판정을 텍스트에서 **산술**로 바꾼다: 동적 풀은 선언돼 있고, 프록시 정적
        주소는 그 풀 **밖**이어야 한다. 그 명제는 어느 키로 쓰든 참이어야 하는 것이고,
        철자를 묻지 않는다.
        """
        import ipaddress

        config = self._compose()['networks']['app-network']['ipam']['config'][0]
        self.assertNotIn(
            'aux_addresses', config,
            'aux_addresses 는 명시적 정적 요청까지 거부해 web 이 뜨지 못하게 한다 '
            '— 동적 풀은 ip_range 로 좁힌다',
        )
        self.assertIn('ip_range', config, '동적 할당 풀이 좁혀져 있지 않다')
        dynamic = ipaddress.ip_network(_compose_default(config['ip_range']))
        proxy = ipaddress.ip_address(
            _compose_default(self._proxy_expression()))
        self.assertNotIn(
            proxy, dynamic,
            f'프록시 정적 주소 {proxy} 가 동적 풀 {dynamic} 안에 있다 — '
            '기동 순서에 따라 그 주소를 빼앗긴다',
        )
        subnet = ipaddress.ip_network(_compose_default(config['subnet']))
        self.assertTrue(
            dynamic.subnet_of(subnet),
            f'동적 풀 {dynamic} 이 서브넷 {subnet} 밖이다',
        )
        # ⚠️ **«프록시 주소를 안 물고 있다» 만으로는 부족하다** (적대 평가 4R 실측).
        # `ip_range: …/32` 는 이 산술을 전부 만족하면서 **첫 컨테이너부터** 기동을
        # 실패시킨다(`no available IPv4 addresses on this network's address
        # pools`). 즉 옛 봉인을 고친 봉인이 같은 계열의 다음 결함을 그대로 허용했다.
        # 필요한 것은 «충분히 크다» 이고, 그 수치는 **compose 자신에서 파생**한다.
        services = self._compose().get('services') or {}
        attached = [
            name for name, spec in services.items()
            if 'app-network' in ((spec.get('networks') or {}) or {})
            or 'app-network' in (spec.get('networks') or [])
        ]
        self.assertGreater(len(attached), 0, '이 네트워크에 붙은 서비스가 0개다')
        # +2: 브리지 게이트웨이와 네트워크 주소는 배정될 수 없다.
        needed = len(attached) + 2
        self.assertGreaterEqual(
            dynamic.num_addresses, needed,
            f'동적 풀 {dynamic} 에 주소가 {dynamic.num_addresses}개뿐인데 이 '
            f'네트워크에 붙는 서비스는 {len(attached)}개다 — 기동이 실패한다',
        )

    def test_the_size_check_would_reject_a_single_address_pool(self):
        """⚠️ 반례 — 4R 이 실제로 통과시킨 그 값이 이제 거부되는지 보인다."""
        import ipaddress

        starved = ipaddress.ip_network('172.31.240.11/32')
        self.assertEqual(1, starved.num_addresses)
        self.assertNotIn(ipaddress.ip_address('172.31.240.10'), starved)

    def test_the_arithmetic_would_reject_a_pool_that_swallows_the_proxy(self):
        """⚠️ 반례가 없으면 위 산술이 무엇을 거부하는지 아무도 모른다."""
        import ipaddress

        swallowing = ipaddress.ip_network('172.31.240.0/24')
        self.assertIn(ipaddress.ip_address('172.31.240.10'), swallowing)

    def test_the_declared_subnet_is_private(self):
        import ipaddress

        subnet_expr = self._compose()['networks']['app-network']['ipam']['config'][0]['subnet']
        self.assertTrue(ipaddress.ip_network(_compose_default(subnet_expr)).is_private)

    def test_the_env_example_documents_both_knobs(self):
        text = ENV_EXAMPLE_PATH.read_text(encoding='utf-8')
        for name in ('CENTRAL_APP_SUBNET', 'CENTRAL_PROXY_IP', 'FCC_CENTRAL_CLIENT_RANGES'):
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_the_env_example_prescribes_values_the_policy_accepts(self):
        """⚠️ **이름의 등장은 값의 안전이 아니다.**

        초판은 세 변수 *이름*이 파일에 있는지만 물었고, 적대 평가가 그 파일을
        ``CENTRAL_APP_SUBNET=10.206.0.0/16`` / 프록시 주소를 그 안에 두도록 — 즉
        운영자용 템플릿이 도커 브리지를 시험원 LAN 에 겹치게 처방하도록 — 고쳐도
        전부 초록임을 보였다. 템플릿이 처방하는 값을 **실제 판정기에 태운다**.
        """
        import ipaddress
        import re
        sys.path.insert(0, str(SRC_ROOT))
        from fcc_test_contracts.common.proxy_trust_policy import peer_axis_mode, trust_defects

        text = ENV_EXAMPLE_PATH.read_text(encoding='utf-8')
        prescribed = dict(re.findall(
            r'^#?(CENTRAL_APP_SUBNET|CENTRAL_PROXY_IP|FCC_CENTRAL_CLIENT_RANGES)=(.*)$',
            text, re.MULTILINE))
        # ⚠️ **주석 처리된 값은 배포에 도달하지 않는다.** `FCC_CENTRAL_CLIENT_RANGES` 는
        # "신뢰 대상이 실 클라이언트를 삼켰다" 를 잡는 **유일한** 검사이고, 초판 템플릿은
        # 그것을 주석으로 배포했다 — 즉 기본 배포에서 그 검사는 꺼져 있었다(적대 평가
        # 2인 독립 지적). 두 대역 변수는 호스트마다 다르므로 주석이 정당하지만, 이것은
        # 다르다: 틀린 값이어도 검사가 덜 유용해질 뿐 배포를 막지 않는다.
        armed = re.findall(
            r'^FCC_CENTRAL_CLIENT_RANGES=(.+)$', text, re.MULTILINE)
        self.assertEqual(
            1, len(armed),
            'central.env.example 이 FCC_CENTRAL_CLIENT_RANGES 를 주석 없이 한 번 '
            f'처방해야 한다 (겹침 검사가 기본 배포에서 켜지도록). 실측: {armed}',
        )
        self.assertIn('CENTRAL_APP_SUBNET', prescribed)
        self.assertIn('CENTRAL_PROXY_IP', prescribed)

        proxy = prescribed['CENTRAL_PROXY_IP'].strip()
        subnet = ipaddress.ip_network(prescribed['CENTRAL_APP_SUBNET'].strip())
        clients = prescribed.get('FCC_CENTRAL_CLIENT_RANGES', '').strip()

        self.assertEqual((), trust_defects(proxy, client_ranges=clients))
        self.assertEqual('per-source', peer_axis_mode(proxy))
        self.assertIn(ipaddress.ip_address(proxy), subnet)
        self.assertTrue(subnet.is_private)
        if clients:
            self.assertFalse(
                subnet.overlaps(ipaddress.ip_network(clients)),
                '템플릿이 도커 브리지를 시험원 대역과 겹치게 처방한다',
            )




class _DriftGateMixin:
    """``scripts/check_deployment_drift.py`` 를 import 하는 공통 준비.

    이 게이트의 봉인이 **이 파일**에 사는 이유: 게이트가 지키는 대상이 이 compose 스택의
    배포 상태이고, 리비전 라벨 배선 자체가 compose/Dockerfile 계약이다. 별도 파일로 쪼개면
    invariant↔skill 매핑 SSOT 세 개(impact-map · skills-invariant-map · impact-tests)를
    함께 갱신해야 하는데, 그것들은 다른 축의 소유물이다. 한 파일 한 소유가 drift 표면을
    줄인다.
    """

    @staticmethod
    def _gate_module():
        sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))
        import check_deployment_drift  # type: ignore

        return check_deployment_drift


class TestDeploymentRevisionLabelWiring(unittest.TestCase, _DriftGateMixin):
    """배포 리비전 라벨 배선 — 도는 이미지가 «어느 커밋» 인지 기록되는가.

    이미지 태그가 ``:latest`` 로 고정이라, 이 라벨이 없으면 *실행 중인 코드가 현재 HEAD 인가*
    라는 질문에 **원리적으로 답할 수 없다.** 그리고 그 질문에 답하지 못하는 것이 갱신 배포의
    최빈 결함(``--build`` 누락)을 조용하게 만든다.
    """

    def _compose(self) -> dict:
        return yaml.safe_load(COMPOSE_PATH.read_text(encoding='utf-8'))

    def _building_services(self) -> dict:
        services = self._compose().get('services', {})
        return {n: s for n, s in services.items() if isinstance(s, dict) and s.get('build')}

    def test_every_building_service_forwards_the_revision_arg(self):
        """대상을 손으로 열거하지 않는다 — compose 의 build 서비스 전량에서 파생한다.

        새 빌드 서비스가 인자를 빠뜨리면 그 이미지만 영원히 UNKNOWN 이 되는데, 그것은
        빨간불이 아니라 «한 축이 조용히 빠진» 상태다.
        """
        building = self._building_services()
        # 비-공허성 하한. ⚠️ 2026-09-01 에 4 → 3 으로 내렸다: web 이 이 저장소의
        # 빌드 대상에서 빠졌기 때문이고(운영자 판정 — `apps/web` 이 이사했다),
        # **그것이 유일한 사유**다. 이 숫자를 「초록으로 만들려고」 다시 내리지 말 것 —
        # 하한의 일은 검사가 공허해지는 것을 막는 것이지 현재 상태를 따라가는 것이 아니다.
        self.assertGreaterEqual(
            len(building), 3,
            '비-공허성 — build 블록을 하나도 못 찾았다면 이 검사는 아무것도 묻지 않았다',
        )
        missing = [
            name for name, spec in building.items()
            if (spec['build'].get('args') or {}).get('GIT_REVISION') is None
        ]
        self.assertEqual(
            [], sorted(missing),
            'build: 를 가진 서비스는 전부 args.GIT_REVISION 을 넘겨야 한다. 누락: '
            f'{sorted(missing)}',
        )

    def test_the_revision_arg_is_an_interpolated_variable_not_a_literal(self):
        """상수를 박으면 «항상 같은 값» 이 기록돼 드리프트가 영원히 안 보인다."""
        for name, spec in self._building_services().items():
            with self.subTest(service=name):
                value = str((spec['build'].get('args') or {}).get('GIT_REVISION'))
                self.assertIn(
                    'GIT_REVISION', value,
                    f'{name}: args.GIT_REVISION 은 ${{GIT_REVISION...}} 보간이어야 한다 (실측 {value!r})',
                )

    def test_both_dockerfiles_declare_the_arg_and_emit_the_oci_label(self):
        for path in (DOCKERFILE_API,):
            with self.subTest(dockerfile=path.name):
                text = path.read_text(encoding='utf-8')
                self.assertRegex(
                    text, r'(?m)^ARG\s+GIT_REVISION',
                    f'{path.name} 이 ARG GIT_REVISION 을 선언하지 않는다',
                )
                self.assertRegex(
                    text,
                    r'(?m)^LABEL\s+org\.opencontainers\.image\.revision="\$\{GIT_REVISION\}"',
                    f'{path.name} 의 라벨이 ARG 를 소비하지 않는다(리터럴이면 드리프트가 안 보인다)',
                )

    def test_the_arg_sits_after_the_dependency_layers(self):
        """캐시 보존 — ARG 를 위에 두면 커밋마다 의존성 레이어가 통째로 무효화된다."""
        anchors = {
            DOCKERFILE_API: 'requirements-central.txt',
        }
        for path, anchor in anchors.items():
            with self.subTest(dockerfile=path.name):
                text = path.read_text(encoding='utf-8')
                self.assertIn(anchor, text, f'{path.name} 에서 앵커 {anchor!r} 를 찾지 못했다')
                self.assertGreater(
                    text.index('ARG GIT_REVISION'), text.rindex(anchor),
                    f'{path.name}: ARG GIT_REVISION 은 의존성 레이어 뒤에 와야 한다',
                )

    def test_the_label_key_agrees_between_dockerfiles_and_the_gate(self):
        """양쪽이 어긋나면 게이트가 **영원히 UNKNOWN** 을 답한다 — 조용한 실패다."""
        key = self._gate_module().REVISION_LABEL
        for path in (DOCKERFILE_API,):
            with self.subTest(dockerfile=path.name):
                self.assertIn(
                    f'LABEL {key}=', path.read_text(encoding='utf-8'),
                    f'{path.name} 의 라벨 키가 게이트 상수 {key!r} 와 다르다',
                )


class TestTheReportNamesTheMachineItMeasured(unittest.TestCase, _DriftGateMixin):
    """⚠️ **오늘 이 자리에 네 번 걸렸다** (실측 2026-09-03).

    이 계열은 개발 PC 와 중앙 PC 에서 **같은 이름의 컨테이너**를 돌린다
    (`fcc-central-platform-api` · `fcc-central-keycloak` · …). 그래서
    `docker compose ps` · `docker inspect` · 이 게이트의 출력이 **두 기계에서
    완전히 같은 모양**이고, 그 출력을 복사해 옮기면 **기계 축이 사라진다.**

    실측된 네 건: 한 세션이 개발 PC 관측으로 「중앙이 서비스 가능하다」를 두 번
    보고했고, 다른 세션이 개발 PC 의 Keycloak 을 중앙으로 읽었으며, 세 번째가 그
    보고를 근거로 운영자에게 전달했다. **매번 사람이 알려줘서** 정정됐다.

    가른 것은 결국 **도달성**이었다 — `curl <중앙IP>:8081` 이 닿느냐.
    이름·포트·컨테이너 목록은 어느 것도 그 축을 갖지 않았다.

    처방은 「더 주의하자」가 아니라 **관측이 스스로 기계를 말하게 하는 것**이다.
    """

    class _R:
        def __init__(self, ok, stdout=''):
            self.ok, self.stdout = ok, stdout

    def _runner(self, *, host='central-pc', addrs='10.0.0.5 172.17.0.1'):
        def run(cmd):
            if cmd == ['hostname']:
                return self._R(True, host + '\n')
            if cmd == ['hostname', '-I']:
                return self._R(True, addrs)
            return self._R(False)
        return run

    def test_the_header_names_the_host_and_its_addresses(self):
        gate = self._gate_module()
        line = gate.describe_measuring_host(self._runner())
        self.assertIn('central-pc', line)
        self.assertIn('10.0.0.5', line)

    def test_an_unreadable_value_is_a_question_mark_not_a_blank(self):
        """⚠️ 빈 자리는 「없다」와 「못 읽었다」를 같은 값으로 만든다."""
        gate = self._gate_module()

        def broken(cmd):
            return self._R(False)

        line = gate.describe_measuring_host(broken)
        self.assertIn('?', line)

    def test_the_header_is_the_first_line_not_the_last(self):
        """⚠️ 꼬리에 두면 `| tail` 로 잘려 나간다 — 출력을 잘라 붙이는 것이
        정확히 이 결함이 퍼지는 경로다."""
        gate = self._gate_module()
        results = [gate.AxisResult('x', gate.VERDICT_PASS, 'ok')]
        report = gate.format_report(results, header='측정 기계: central-pc')
        self.assertTrue(report.startswith('측정 기계: central-pc'), report)

    def test_the_report_without_a_header_is_unchanged(self):
        """머리말은 선택이다 — 기존 호출부를 깨지 않는다."""
        gate = self._gate_module()
        results = [gate.AxisResult('x', gate.VERDICT_PASS, 'ok')]
        self.assertNotIn('측정 기계', gate.format_report(results))

    def test_wsl_is_named_because_its_lan_address_lives_elsewhere(self):
        gate = self._gate_module()
        with mock.patch.object(gate, 'is_wsl', lambda: True):
            line = gate.describe_measuring_host(self._runner())
        self.assertIn('WSL', line)
        with mock.patch.object(gate, 'is_wsl', lambda: False):
            line = gate.describe_measuring_host(self._runner())
        self.assertNotIn('WSL', line)


class TestThePublicHostAxisCanSeeTheWslHost(unittest.TestCase, _DriftGateMixin):
    """⚠️ **이 축이 중앙 PC 에서 영원히 DRIFT 였다** (실측 2026-09-03).

    중앙 PC 는 WSL 이고 LAN 이 보는 주소(`PUBLIC_HOST`)는 **Windows 호스트**의
    것이다. WSL VM 은 `172.x` 만 갖고 그 주소를 자기 인터페이스로 **갖지 않는다** —
    WSL2 가 포워딩할 뿐이다. 그래서 `hostname -I` 만 보면
    *「PUBLIC_HOST 가 이 PC 주소에 없다」* 가 **항상** 참이 된다.

    실측: 중앙 PC 최초 구축에서 `PUBLIC_HOST=10.206.34.233` 이 DRIFT 로 나왔고,
    `powershell.exe Get-NetIPAddress` 로 물으니 **그 주소가 거기 있었다.**
    **설정은 옳았고 축이 볼 수 없었다.**

    > 그리고 **오탐을 내는 게이트는 삭제된다** — 이 저장소가 반복해서 낸 결론이다.

    ⚠️ 그래서 처방은 「DRIFT 를 끄는 것」이 아니라 **「구분되게 만드는 것」**이다:
    Windows 주소를 읽으면 판정하고, 못 읽으면 **판정 불가**다.
    「설정이 틀렸다」와 「축이 볼 수 없다」를 가른다.
    """

    _WSL_ONLY = ['172.31.240.128', '172.18.0.1']
    _WITH_WINDOWS = ['172.31.240.128', '172.18.0.1', '10.206.34.233']
    _LAN_HOST = '10.206.34.233'

    def _judge(self, *, wsl: bool, addresses, public_host: str):
        gate = self._gate_module()
        with mock.patch.object(gate, 'is_wsl', lambda: wsl):
            return gate.judge_public_host(public_host, addresses)

    def test_a_wsl_host_address_we_could_not_read_is_undetermined_not_drift(self):
        """⚠️ **이 팔이 이 클래스의 존재 이유다.**"""
        result = self._judge(
            wsl=True, addresses=self._WSL_ONLY, public_host=self._LAN_HOST)
        self.assertEqual('UNKNOWN', result.verdict, result.detail)
        self.assertIn('Windows 호스트 주소를 읽지 못했다', result.detail)

    def test_reading_the_windows_address_makes_it_pass(self):
        result = self._judge(
            wsl=True, addresses=self._WITH_WINDOWS, public_host=self._LAN_HOST)
        self.assertEqual('PASS', result.verdict, result.detail)

    def test_a_real_drift_outside_wsl_is_still_drift(self):
        """⚠️ **반대 방향** — 완화가 「WSL 이면 무조건 통과」로 퇴화하면 안 된다.

        이 팔이 없으면 위 정정이 그 축을 통째로 꺼 버린 것과 구분되지 않는다.
        """
        result = self._judge(
            wsl=False, addresses=self._WSL_ONLY, public_host=self._LAN_HOST)
        self.assertEqual('DRIFT', result.verdict, result.detail)

    def test_an_address_the_vm_really_owns_still_passes(self):
        result = self._judge(
            wsl=True, addresses=self._WITH_WINDOWS, public_host='172.31.240.128')
        self.assertEqual('PASS', result.verdict, result.detail)

    def test_the_collector_merges_rather_than_replaces(self):
        """WSL VM 주소도 여전히 유효한 답이다 — 대체하면 그 경우를 잃는다."""
        gate = self._gate_module()

        class _Result:
            def __init__(self, ok, stdout):
                self.ok, self.stdout = ok, stdout

        def runner(cmd):
            if cmd[0] == 'hostname':
                return _Result(True, '172.31.240.128 172.18.0.1')
            return _Result(True, '10.206.34.233\n192.168.0.5\n')

        with mock.patch.object(gate, 'is_wsl', lambda: True):
            merged = gate.collect_host_addresses(runner)
        self.assertIn('172.31.240.128', merged)
        self.assertIn('10.206.34.233', merged)

    def test_a_failed_powershell_call_does_not_erase_the_vm_addresses(self):
        """⚠️ 못 물어본 것이 「주소가 없다」가 되면 안 된다."""
        gate = self._gate_module()

        class _Result:
            def __init__(self, ok, stdout):
                self.ok, self.stdout = ok, stdout

        def runner(cmd):
            if cmd[0] == 'hostname':
                return _Result(True, '172.31.240.128')
            return _Result(False, '')

        with mock.patch.object(gate, 'is_wsl', lambda: True):
            merged = gate.collect_host_addresses(runner)
        self.assertEqual(['172.31.240.128'], merged)


class TestDeploymentDriftGateVerdicts(unittest.TestCase, _DriftGateMixin):
    """게이트의 판정 계약 — 특히 «모른다» 가 «통과» 로 접히지 않는다.

    소스 검사가 아니라 판정 함수를 **실제로 호출**한다. 관측만 하고 봉인하지 않으면 변이가
    살아남는다.
    """

    def test_an_empty_run_is_not_a_pass(self):
        m = self._gate_module()
        self.assertEqual(m.EXIT_UNKNOWN, m.overall_exit_code([]))

    def test_drift_outranks_unknown(self):
        m = self._gate_module()
        mixed = [
            m.AxisResult('a', m.VERDICT_UNKNOWN, ''),
            m.AxisResult('b', m.VERDICT_DRIFT, ''),
            m.AxisResult('c', m.VERDICT_PASS, ''),
        ]
        self.assertEqual(m.EXIT_DRIFT, m.overall_exit_code(mixed))

    def test_unknown_without_drift_is_its_own_code(self):
        m = self._gate_module()
        results = [
            m.AxisResult('a', m.VERDICT_PASS, ''),
            m.AxisResult('b', m.VERDICT_UNKNOWN, ''),
        ]
        self.assertEqual(m.EXIT_UNKNOWN, m.overall_exit_code(results))
        self.assertNotEqual(m.EXIT_PASS, m.overall_exit_code(results))

    def test_all_pass_is_the_only_zero(self):
        m = self._gate_module()
        self.assertEqual(
            m.EXIT_PASS,
            m.overall_exit_code([m.AxisResult('a', m.VERDICT_PASS, '')]),
        )

    def test_a_verdict_outside_the_vocabulary_cannot_be_constructed(self):
        """구성으로 막는다 — 어휘 밖 판정을 든 결과는 애초에 만들어지지 않는다."""
        m = self._gate_module()
        with self.assertRaises(ValueError):
            m.AxisResult('a', 'OK', '')

    def test_no_axis_returns_pass_when_its_input_is_absent(self):
        """음성 단언 — 판정 재료가 없을 때 PASS 를 답하는 축이 하나도 없다.

        이것이 이 게이트의 핵심 실패 모드다: 못 읽은 축을 통과로 답하면 게이트가 아무것도
        하지 않으면서 초록으로 보인다.

        ⚠️ **축마다 «부재» 가 여럿일 수 있고, 하나만 먹이면 첫 자물쇠만 증명된다.** 실측
        (변이 V-8, 2026-08-23): ``env-keys`` 에 ``(None, None)`` 만 먹였더니 example 부재
        분기에서 먼저 걸려, **운영 env 부재 분기를 PASS 로 바꾼 변이가 살아남았다.** 그래서
        값이 인자 튜플 하나가 아니라 **튜플들**이다.
        """
        m = self._gate_module()
        absent_inputs = {
            'revision': (
                (None, {}),                      # HEAD 를 못 읽음
                ('a' * 40, {}),                  # 검사할 이미지가 없음
            ),
            'image-id': (
                ({}, {}, {}),                    # 대상 컨테이너가 없음
                ({'c': None}, {'t': 'sha256:1'}, {'c': 't'}),   # 컨테이너를 못 읽음
                ({'c': 'sha256:1'}, {'t': None}, {'c': 't'}),   # 이미지를 못 읽음
            ),
            'migration': (
                (None,),                         # 원장을 못 읽음
                ({'applied': []},),              # pending/drift 가 없는 응답
            ),
            'env-keys': (
                (None, None),                    # example 을 못 읽음
                ('KEY=1\n', None),               # 운영 env 를 못 읽음 (두 번째 자물쇠)
            ),
            'auth-pair': (
                (None,),                         # 실행하지 못함
                (2,),                            # 판정할 값이 없음
            ),
            'public-host': (
                (None, None),                    # PUBLIC_HOST 를 못 읽음
                ('10.0.0.5', None),              # 이 PC 의 주소를 못 읽음 (두 번째 자물쇠)
                ('localhost', ['127.0.0.1']),    # 이름은 판정하지 않는다
            ),
        }
        self.assertEqual(
            set(m.JUDGES), set(absent_inputs),
            '축이 늘었는데 부재-입력 케이스가 없다 — 그 축은 이 음성 단언을 통과한 적이 없다',
        )
        for axis, cases in sorted(absent_inputs.items()):
            for index, args in enumerate(cases):
                with self.subTest(axis=axis, case=index):
                    verdict = m.JUDGES[axis](*args).verdict
                    self.assertNotEqual(
                        m.VERDICT_PASS, verdict,
                        f'{axis}[{index}]: 재료가 없는데 PASS 를 답한다',
                    )

    def test_the_registry_equals_what_a_run_actually_produces(self):
        """JUDGES 를 load-bearing 으로 만드는 집합 상등.

        ``run_all_axes`` 는 판정 함수를 직접 부르므로 이 dict 를 읽지 않는다. 두 변이 다른
        출처(실제 실행 vs 선언)라, 축을 추가하고 등재하지 않으면 여기서 red 다.
        """
        m = self._gate_module()

        def failing_runner(_command):
            return m.CommandResult(returncode=127, stdout='', stderr='no such tool')

        results = m.run_all_axes(runner=failing_runner, read_text=lambda _p: None)
        self.assertEqual(set(m.JUDGES), {r.axis for r in results})
        self.assertTrue(
            all(r.verdict == m.VERDICT_UNKNOWN for r in results),
            f'아무것도 실행되지 않은 run 은 전 축 UNKNOWN 이어야 한다: {results}',
        )
        self.assertEqual(m.EXIT_UNKNOWN, m.overall_exit_code(results))

    def test_a_revision_label_that_is_not_a_commit_is_unknown_not_drift(self):
        """실측 기반 — 리비전 자리의 비-커밋 값은 «다른 커밋» 이 아니라 «판정 불가» 다."""
        m = self._gate_module()
        head = 'a' * 40
        for bogus in ('', '<no value>', '${GIT_REVISION}', 'latest', 'unknown'):
            with self.subTest(label=bogus):
                result = m.judge_revision(head, {'img:latest': bogus})
                self.assertEqual(m.VERDICT_UNKNOWN, result.verdict)

    def test_a_real_mismatch_is_drift(self):
        m = self._gate_module()
        result = m.judge_revision('a' * 40, {'img:latest': 'b' * 40})
        self.assertEqual(m.VERDICT_DRIFT, result.verdict)
        self.assertEqual(
            m.VERDICT_PASS,
            m.judge_revision('a' * 40, {'img:latest': 'a' * 40}).verdict,
        )

    def test_env_key_axis_reads_declarations_not_comments(self):
        m = self._gate_module()
        example = '# NEW_KEY=commented-out\nOLD_KEY=1\n'
        self.assertEqual(
            m.VERDICT_PASS,
            m.judge_env_keys(example, 'OLD_KEY=2\n').verdict,
            '주석 처리된 줄은 선언이 아니다',
        )
        self.assertEqual(
            m.VERDICT_DRIFT,
            m.judge_env_keys('NEW_KEY=1\nOLD_KEY=1\n', 'OLD_KEY=2\n').verdict,
        )

    def test_env_key_axis_ignores_prose_that_merely_contains_an_equals_sign(self):
        """이름 필터를 **주석 가드와 분리해** 겨눈다.

        ⚠️ 두 가드가 같은 입력을 각자 막고 있으면 거절 검사는 «먼저 발화하는 것» 만
        증명한다. 실측(변이 V-11, 2026-08-23): 주석 줄은 주석 가드가 이미 걸러내므로
        **이름 필터를 통째로 제거한 변이가 살아남았다.** 주석이 아니면서 ``=`` 를 품은
        산문 — 문서형 env 파일에 흔하다 — 이 그 둘을 가른다.
        """
        m = self._gate_module()
        keys = m.env_declared_keys('PLAIN=1\nsee also: FOO=bar 를 참고\n')
        self.assertEqual({'PLAIN'}, keys)

    def test_the_env_example_axis_runs_against_the_real_file(self):
        """비-공허성 — 합성 입력만 먹인 축은 실제 파일 형식이 바뀌면 조용히 죽는다."""
        m = self._gate_module()
        keys = m.env_declared_keys(ENV_EXAMPLE_PATH.read_text(encoding='utf-8'))
        self.assertIn('PUBLIC_HOST', keys)
        self.assertGreaterEqual(len(keys), 10)

    def test_migration_axis_reads_the_runner_verdict(self):
        m = self._gate_module()
        self.assertEqual(
            m.VERDICT_PASS,
            m.judge_migration_status({'applied': ['001'], 'pending': [], 'drift': []}).verdict,
        )
        self.assertEqual(
            m.VERDICT_DRIFT,
            m.judge_migration_status({'pending': ['026'], 'drift': []}).verdict,
        )
        self.assertEqual(
            m.VERDICT_DRIFT,
            m.judge_migration_status({'pending': [], 'drift': ['001: checksum']}).verdict,
        )
        self.assertEqual(
            m.VERDICT_UNKNOWN,
            m.judge_migration_status({'applied': []}).verdict,
            'pending/drift 가 없는 응답은 통과가 아니라 미확인이다',
        )

    def test_public_host_axis_refuses_to_guess_about_names(self):
        m = self._gate_module()
        # ⚠️ **WSL 축을 명시적으로 고정한다** (2026-09-03).
        # 2026-09-03 에 이 판정에 「WSL 인가」 축이 생겼다(Windows 호스트 주소를
        # 못 읽으면 DRIFT 가 아니라 판정 불가). 그것을 고정하지 않으면 **같은
        # 입력이 기계에 따라 다른 답**을 내고, 이 팔은 그 사실을 잡아 red 가 됐다
        # — 올바른 반응이었다. 여기서 재는 것은 *이름 축*이지 WSL 축이 아니다.
        with mock.patch.object(m, 'is_wsl', lambda: False):
            self.assertEqual(
                m.VERDICT_PASS,
                m.judge_public_host('10.0.0.5', ['172.17.0.1', '10.0.0.5']).verdict,
            )
            self.assertEqual(
                m.VERDICT_DRIFT,
                m.judge_public_host('10.0.0.5', ['172.17.0.1']).verdict,
            )
        self.assertEqual(
            m.VERDICT_UNKNOWN,
            m.judge_public_host('localhost', ['127.0.0.1']).verdict,
            '이름 해소 규칙은 이 스크립트 밖에 있으므로 아는 척하지 않는다',
        )

    def test_auth_axis_maps_the_delegated_exit_codes(self):
        m = self._gate_module()
        self.assertEqual(m.VERDICT_PASS, m.judge_auth_pairing(0).verdict)
        self.assertEqual(m.VERDICT_DRIFT, m.judge_auth_pairing(1).verdict)
        self.assertEqual(
            m.VERDICT_UNKNOWN, m.judge_auth_pairing(2).verdict,
            '판정할 값이 없다는 응답을 통과로 접지 않는다',
        )

    def test_build_targets_derive_from_the_real_compose_file(self):
        """게이트가 검사할 대상 집합이 실제 compose 에서 나온다(손 목록 0)."""
        m = self._gate_module()
        doc = yaml.safe_load(COMPOSE_PATH.read_text(encoding='utf-8'))
        targets = m.build_targets(doc)
        # ⚠️ web 은 더 이상 여기서 빌드되지 않는다 (운영자 판정 2026-09-01) —
        # `apps/web` 이 이사했으므로 이미지는 `fcc-test-platform` 이 만들고 이
        # compose 는 그것을 `image:` 로 소비만 한다. 그러므로 빌드 대상은 이
        # 저장소가 실제로 빌드하는 API 계열뿐이고, 그 사실을 **여기서 단언한다**
        # — 그래야 web 의 `build:` 가 슬그머니 되돌아오면 red 가 된다.
        # ⚠️ **2026-09-03 정정.** 옛 판은 «이 저장소가 빌드하는 중앙 이미지는
        # `fcc-central-api` 하나» 라고 단언했다. 여기서는 둘이다 —
        # `fcc-central-platform-api`(platform-api · central-migrate 공유)와
        # `fcc-central-web`. 그리고 `headless-api` 는 **빌드 대상이 아니다**.
        #
        # 이미지 이름을 손으로 적지 않는다: 빌드 대상 집합에 headless 가 **없다**는
        # 것과, 모든 대상이 이 저장소의 컨테이너라는 것만 단언한다. 이름을 적으면
        # 태그가 바뀌는 날 결함이 아닌 것이 red 가 된다.
        self.assertGreaterEqual(len(targets), 2)
        self.assertNotIn(
            'headless-api', targets,
            'headless-api 가 빌드 대상에 들어왔다 — 그 이미지는 provider 저장소가 '
            '만든다. 여기서 빌드하면 중앙 PC 가 그 저장소를 다시 요구하게 된다.',
        )
        for spec in targets.values():
            self.assertTrue(spec['container_name'].startswith('fcc-central'))

    def test_the_runbook_names_the_gate(self):
        """게이트를 부르지 않는 런북은 게이트가 없는 것과 같다."""
        runbook = PROJECT_ROOT / 'docs' / 'operations' / 'central-pc-update-deploy-runbook.md'
        text = runbook.read_text(encoding='utf-8')
        self.assertIn('scripts/check_deployment_drift.py', text)
        self.assertIn('GIT_REVISION="$(git rev-parse HEAD)"', text)


class TestProviderIdentityValue(unittest.TestCase):
    """`FCC_CENTRAL_PROVIDER_ID` 는 이름만이 아니라 **값**이 SSOT 를 따라야 한다.

    이 클래스가 생긴 이유 (실측 2026-09-01, 개발 PC) — 형제 시험
    ``test_env_file_uses_python_env_name_ssot`` 은 *키가 있는가* 만 묻는다. 그
    축에서는 ``FCC_CENTRAL_PROVIDER_ID=unlicensed`` 와
    ``FCC_CENTRAL_PROVIDER_ID=fcc-unlicensed-conducted`` 가 **같은 값**을 가지므로,
    출하되는 예시 env 가 **같은 저장소의 런북 S4(a) 가 등록하지 않는 provider** 를
    지목하고 있어도 모든 검사가 초록이었다. 검사 축 맹점의 교과서적 사례다
    (`.claude/rules/check-axis-blindness.md`).

    실해는 그 오답이 **늦게, 그리고 엉뚱한 이름으로** 나타난다는 것이다: 노드는
    기계 신분증을 받고 챔버 바인딩을 통과한 **뒤에야** 참조 번들에서
    ``404 unknown provider_id 'unlicensed'`` 를 만나므로, 증상이 인증 층에 있는
    것처럼 읽힌다. 실제로 그 앞의 두 층(챔버 토큰 바인딩 · 기계 신분증)을 먼저
    닫고 나서야 이것이 드러났다.
    """

    @staticmethod
    def _contract_provider_id() -> str:
        sys.path.insert(0, str(SRC_ROOT))
        try:
            from fcc_test_contracts.headless.api_contracts import (
                DEFAULT_PROVIDER_METADATA,
            )
        finally:
            sys.path.remove(str(SRC_ROOT))
        return str(DEFAULT_PROVIDER_METADATA['provider_id'])

    def test_env_example_declares_the_contract_provider_id(self):
        expected = self._contract_provider_id()
        text = ENV_EXAMPLE_PATH.read_text(encoding='utf-8')
        declared = re.findall(
            r'^FCC_CENTRAL_PROVIDER_ID=(.*)$', text, flags=re.MULTILINE,
        )
        self.assertEqual(
            declared, [expected],
            'central.env.example must declare exactly one '
            f'FCC_CENTRAL_PROVIDER_ID and it must be {expected!r}; got {declared!r}.',
        )

    def test_compose_default_is_the_contract_provider_id(self):
        """compose 의 ``:-`` 기본값도 같은 SSOT 여야 한다.

        env 파일을 고쳐도 compose 기본값이 다르면, env 를 주지 않은 배포는
        조용히 옛 값으로 돈다 — 고친 줄이 있는데도 고쳐지지 않는 형태다.
        """
        expected = self._contract_provider_id()
        text = COMPOSE_PATH.read_text(encoding='utf-8')
        defaults = re.findall(
            r'\$\{FCC_CENTRAL_PROVIDER_ID:-([^}]*)\}', text,
        )
        self.assertTrue(defaults, 'compose declares no FCC_CENTRAL_PROVIDER_ID default')
        self.assertEqual(
            sorted(set(defaults)), [expected],
            f'every compose default for FCC_CENTRAL_PROVIDER_ID must be {expected!r}; '
            f'got {sorted(set(defaults))!r}.',
        )

    def test_the_runbook_registers_the_same_provider_id(self):
        """런북이 INSERT 하는 provider 와 예시 env 가 지목하는 provider 는 같아야 한다.

        이 둘이 갈라진 것이 이 결함의 실체였다. 한쪽만 고치면 다음 배포가 같은
        자리에서 다시 넘어진다.
        """
        expected = self._contract_provider_id()
        runbook = (
            PROJECT_ROOT / 'docs' / 'operations'
            / 'central-pc-operational-validation-runbook.md'
        )
        self.assertIn(expected, runbook.read_text(encoding='utf-8'))




if __name__ == '__main__':
    unittest.main()

