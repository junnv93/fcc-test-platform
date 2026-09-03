#!/usr/bin/env python3
"""배포 후 점검 — 지금 도는 배포가 이 저장소의 현재 상태와 같은가.

``docs/operations/central-pc-update-deploy-runbook.md`` §13 한계 1 의 상환이다. 그 문서가
적었듯 갱신 배포의 가장 흔한 결함은 **실패하지 않는다** — ``--build`` 를 빠뜨리면 compose 가
같은 ``:latest`` 태그의 캐시 이미지를 재사용하고, 스택은 옛 코드로 "정상 기동" 한다. 그때
``docker compose ps`` 는 전부 healthy 이고 스모크도 통과한다. 그래서 이 점검이 필요하다.

여섯 축을 본다. 각 축은 **PASS / DRIFT / UNKNOWN** 하나로 답한다:

===================  =========================================================
축                   무엇을 묻는가
===================  =========================================================
``revision``         도는 이미지가 현재 ``git HEAD`` 로 빌드됐는가
``image-id``         컨테이너가 그 태그의 **현재** 이미지를 쓰는가
``migration``        중앙 DB 에 미적용/드리프트 마이그레이션이 있는가
``env-keys``         ``central.env.example`` 이 요구하는 키가 운영 env 에 다 있는가
``auth-pair``        백엔드 auth mode 와 SPA 로그인 전략이 짝인가
``public-host``      ``PUBLIC_HOST`` 가 이 PC 가 실제로 가진 주소인가
===================  =========================================================

사용::

    python3 scripts/check_deployment_drift.py
    python3 scripts/check_deployment_drift.py --runtime-config-url http://10.206.34.233:8080/runtime-config.js
    python3 scripts/check_deployment_drift.py --json

종료 코드: 전 축 PASS 면 ``0``, 한 축이라도 DRIFT 면 ``1``, DRIFT 는 없는데 판정하지 못한
축이 있으면 ``2``.

⚠️ **UNKNOWN 을 통과와 같은 코드로 만들지 않는다.** 묻지 못한 축은 통과가 아니라 미확인이고,
둘을 같은 코드로 접으면 이 점검은 아무것도 하지 않으면서 초록으로 보인다(같은 규칙을
``scripts/check_auth_mode_pairing.py`` 가 이미 적고 있다).

⚠️ **DRIFT 가 UNKNOWN 을 이긴다.** 아는 결함이 모르는 축보다 먼저 조치 대상이기 때문이고,
그래야 UNKNOWN 하나가 DRIFT 를 가리지 않는다.

판정은 **순수 함수**(``judge_*``)이고 수집은 **주입된 runner** 를 거친다. 그 분리가 이
스크립트를 docker 없이 시험할 수 있게 하고, 봉인이 실제 판정 코드를 부르게 한다 — 관측만
하고 테스트로 착지시키지 않은 점검은 변이가 살아남는다.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── 어휘 ────────────────────────────────────────────────────────────────────
VERDICT_PASS = 'PASS'
VERDICT_DRIFT = 'DRIFT'
VERDICT_UNKNOWN = 'UNKNOWN'

EXIT_PASS = 0
EXIT_DRIFT = 1
EXIT_UNKNOWN = 2

#: OCI 표준 라벨 키. **Dockerfile 과 이 상수는 같은 철자여야 한다** — 어긋나면 게이트가
#: 영원히 UNKNOWN 을 답하고, 그것은 조용한 실패다. 양쪽 정합은
#: ``tests/test_central_docker_compose.py::TestDeploymentRevisionLabelWiring`` 가 봉인한다.
REVISION_LABEL = 'org.opencontainers.image.revision'

#: 리비전을 싣지 않은 빌드를 사람이 읽을 때의 처방. 라벨이 비어 있다는 사실만으로는
#: 무엇을 해야 하는지 알 수 없다.
_REBUILD_HINT = (
    '배포 명령이 리비전을 싣지 않았다 — '
    'GIT_REVISION="$(git rev-parse HEAD)" docker compose ... up -d --build'
)

DEFAULT_COMPOSE_FILE = 'infra/docker-compose.central.yml'
DEFAULT_ENV_FILE = 'infra/central/central.env'
DEFAULT_ENV_EXAMPLE = 'infra/central/central.env.example'

#: 마이그레이션 원장을 물어볼 서비스. 이 컨테이너가 중앙 DSN 과 러너를 모두 갖는다.
MIGRATION_PROBE_SERVICE = 'platform-api'


@dataclass(frozen=True)
class AxisResult:
    """한 축의 판정. ``verdict`` 는 세 어휘 중 하나다."""

    axis: str
    verdict: str
    detail: str

    def __post_init__(self) -> None:
        # 구성으로 막는다 — 판정 어휘 밖의 값을 든 결과는 애초에 만들어지지 않는다.
        if self.verdict not in (VERDICT_PASS, VERDICT_DRIFT, VERDICT_UNKNOWN):
            raise ValueError(f'unknown verdict: {self.verdict!r}')


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ''

    @property
    def ok(self) -> bool:
        return self.returncode == 0


Runner = Callable[[Sequence[str]], CommandResult]


def subprocess_runner(command: Sequence[str]) -> CommandResult:
    """기본 runner — 실제 프로세스를 부른다. 실행 자체가 불가능하면 UNKNOWN 재료를 만든다."""
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError) as exc:  # docker/git 부재, 실행 불가
        return CommandResult(returncode=127, stdout='', stderr=str(exc))
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or '',
        stderr=completed.stderr or '',
    )


# ── 판정 (순수 함수) ─────────────────────────────────────────────────────────

def judge_revision(head: str | None, image_revisions: Mapping[str, str | None]) -> AxisResult:
    """도는 이미지의 리비전 라벨이 현재 ``git HEAD`` 와 같은가.

    ``image_revisions`` 는 ``{이미지 태그: 라벨값 또는 None}``. ``None`` 은 이미지를 읽지
    못한 것이고 빈 문자열은 리비전 없이 빌드된 것이다 — 둘 다 UNKNOWN 이지 PASS 가 아니다.
    """
    if not head:
        return AxisResult('revision', VERDICT_UNKNOWN, 'git HEAD 를 읽지 못했다')
    if not image_revisions:
        return AxisResult('revision', VERDICT_UNKNOWN, '검사할 빌드 이미지를 찾지 못했다')

    drifted: list[str] = []
    unknown: list[str] = []
    for tag in sorted(image_revisions):
        recorded = (image_revisions[tag] or '').strip() if image_revisions[tag] is not None else None
        if recorded is None:
            unknown.append(f'{tag}: 이미지를 읽지 못했다')
        elif not recorded:
            unknown.append(f'{tag}: 리비전 라벨이 비어 있다')
        elif not looks_like_git_revision(recorded):
            # 리비전 자리에 리비전이 아닌 것이 있으면 그것은 «다른 커밋» 이 아니라
            # «판정 불가» 다. DRIFT 로 답하면 실재하지 않는 결함을 지목하게 된다.
            unknown.append(f'{tag}: 리비전 라벨이 커밋이 아니다({recorded!r})')
        elif recorded != head:
            drifted.append(f'{tag}: image={_short(recorded)} HEAD={_short(head)}')

    if drifted:
        return AxisResult('revision', VERDICT_DRIFT, '; '.join(drifted))
    if unknown:
        return AxisResult('revision', VERDICT_UNKNOWN, '; '.join(unknown) + f' ({_REBUILD_HINT})')
    return AxisResult('revision', VERDICT_PASS, f'전 이미지가 HEAD={_short(head)}')


def judge_image_ids(
    container_images: Mapping[str, str | None],
    tag_ids: Mapping[str, str | None],
    container_tags: Mapping[str, str],
) -> AxisResult:
    """컨테이너가 그 태그의 **현재** 이미지를 쓰는가.

    ``--build`` 는 돌았는데 컨테이너가 재생성되지 않은 형상을 잡는다. 리비전 축과 직교다 —
    저쪽은 *이미지가 낡았는가*, 이쪽은 *컨테이너가 낡은 이미지를 붙들고 있는가*를 묻는다.
    """
    if not container_tags:
        return AxisResult('image-id', VERDICT_UNKNOWN, '검사할 컨테이너를 찾지 못했다')

    drifted: list[str] = []
    unknown: list[str] = []
    for container in sorted(container_tags):
        tag = container_tags[container]
        running = container_images.get(container)
        current = tag_ids.get(tag)
        if running is None:
            unknown.append(f'{container}: 컨테이너를 읽지 못했다')
        elif current is None:
            unknown.append(f'{tag}: 이미지를 읽지 못했다')
        elif running != current:
            drifted.append(f'{container}: {_short(running)} != {tag} {_short(current)}')

    if drifted:
        return AxisResult('image-id', VERDICT_DRIFT, '; '.join(drifted))
    if unknown:
        return AxisResult('image-id', VERDICT_UNKNOWN, '; '.join(unknown))
    return AxisResult('image-id', VERDICT_PASS, f'{len(container_tags)}개 컨테이너가 현재 이미지')


def judge_migration_status(payload: Mapping[str, object] | None) -> AxisResult:
    """``platform_db_migrate.py status`` 의 원장을 읽는다.

    판정을 재조립하지 않는다 — 러너가 답한 ``pending``/``drift`` 를 그대로 읽는다.
    """
    if payload is None:
        return AxisResult('migration', VERDICT_UNKNOWN, '마이그레이션 원장을 읽지 못했다')

    pending = payload.get('pending')
    drift = payload.get('drift')
    if not isinstance(pending, list) or not isinstance(drift, list):
        return AxisResult('migration', VERDICT_UNKNOWN, 'status 응답에 pending/drift 가 없다')

    problems: list[str] = []
    if pending:
        problems.append(f'pending={pending}')
    if drift:
        problems.append(f'drift={drift}')
    if problems:
        return AxisResult('migration', VERDICT_DRIFT, ' '.join(problems))
    return AxisResult('migration', VERDICT_PASS, 'pending=[] drift=[]')


def judge_env_keys(example_text: str | None, env_text: str | None) -> AxisResult:
    """운영 env 가 example 이 선언한 키를 다 갖는가.

    compose 가 컨테이너로 넘기는 값은 선언된 것뿐이라, 새 릴리스가 요구하는 키가 없으면
    부팅 거부 또는 조용한 기본값이 된다.
    """
    if example_text is None:
        return AxisResult('env-keys', VERDICT_UNKNOWN, 'central.env.example 을 읽지 못했다')
    if env_text is None:
        return AxisResult('env-keys', VERDICT_UNKNOWN, 'central.env 를 읽지 못했다(운영자 소유 파일)')

    missing = sorted(env_declared_keys(example_text) - env_declared_keys(env_text))
    if missing:
        return AxisResult('env-keys', VERDICT_DRIFT, 'central.env 에 없는 키: ' + ', '.join(missing))
    return AxisResult('env-keys', VERDICT_PASS, 'example 이 선언한 키를 모두 갖는다')


def judge_auth_pairing(exit_code: int | None) -> AxisResult:
    """``check_auth_mode_pairing.py`` 의 판정을 그대로 옮긴다.

    ⚠️ 술어를 재조립하지 않는다. 사전점검이 자기 판정을 다시 짜면 게이트가 통과시키는 배포를
    막거나 그 반대가 되고, 그러면 사람들이 점검을 끈다.
    """
    if exit_code is None:
        return AxisResult('auth-pair', VERDICT_UNKNOWN, '짝 점검을 실행하지 못했다')
    if exit_code == 0:
        return AxisResult('auth-pair', VERDICT_PASS, 'auth mode ↔ 로그인 전략 짝')
    if exit_code == 1:
        return AxisResult('auth-pair', VERDICT_DRIFT, 'auth mode 와 로그인 전략이 어긋난다')
    return AxisResult('auth-pair', VERDICT_UNKNOWN, f'짝을 판정할 값이 없다(exit={exit_code})')


def judge_public_host(public_host: str | None, host_addresses: Sequence[str] | None) -> AxisResult:
    """``PUBLIC_HOST`` 가 이 PC 가 실제로 가진 주소인가.

    WSL IP 는 재부팅으로 바뀔 수 있고, 바뀌면 서비스는 정상인 채 LAN 에서만 죽는다.
    IP 가 아닌 이름(``localhost``·호스트명)은 여기서 판정하지 않는다 — 해소 규칙이 이
    스크립트 밖에 있으므로 아는 척하지 않는다.
    """
    if not public_host:
        return AxisResult('public-host', VERDICT_UNKNOWN, 'PUBLIC_HOST 를 읽지 못했다')
    if not _looks_like_ipv4(public_host):
        return AxisResult(
            'public-host',
            VERDICT_UNKNOWN,
            f'PUBLIC_HOST={public_host} 는 IP 가 아니라 이름이라 이 점검이 판정하지 않는다',
        )
    if not host_addresses:
        return AxisResult('public-host', VERDICT_UNKNOWN, '이 PC 의 주소 목록을 읽지 못했다')
    if public_host not in host_addresses:
        # ⚠️ WSL 인데 Windows 주소를 못 읽었으면 **DRIFT 가 아니라 판정 불가**다.
        # 그 경우 「설정이 틀렸다」와 「축이 볼 수 없다」가 같은 값이 되고,
        # 중앙 PC 가 WSL 이므로 그것은 영원한 오탐이 된다.
        if is_wsl() and not any(_is_windows_host_candidate(a) for a in host_addresses):
            return AxisResult(
                'public-host',
                VERDICT_UNKNOWN,
                f'PUBLIC_HOST={public_host} 가 WSL VM 주소 {list(host_addresses)} 에 '
                '없고, Windows 호스트 주소를 읽지 못했다 — 이 축이 판정할 수 없다. '
                '(LAN 이 보는 주소는 Windows 쪽이고 WSL2 가 포워딩한다.)',
            )
        return AxisResult(
            'public-host',
            VERDICT_DRIFT,
            f'PUBLIC_HOST={public_host} 가 이 PC 의 주소 {list(host_addresses)} 에 없다',
        )
    return AxisResult('public-host', VERDICT_PASS, f'PUBLIC_HOST={public_host}')


#: 축 이름 → 판정 함수.
#:
#: ⚠️ **이 목록은 봉인을 통해 load-bearing 이다.** ``run_all_axes`` 는 판정 함수를 직접
#: 부르므로 이 dict 를 읽지 않는다 — 그러니 이것만으로는 «저자가 통제하는 토큰» 이고,
#: 축을 추가하면서 등재하지 않으면 음성 단언(입력 부재 → PASS 금지)이 그 축을 놓친다.
#: 그래서 봉인이 **실행에서 나온 축 집합**과 이 목록의 **집합 상등**을 요구한다: 등재를
#: 빠뜨리면 red 다. 두 변이 서로 다른 출처(실제 실행 vs 선언)라 같은 파생을 지나지 않는다.
JUDGES: dict[str, Callable[..., AxisResult]] = {
    'revision': judge_revision,
    'image-id': judge_image_ids,
    'migration': judge_migration_status,
    'env-keys': judge_env_keys,
    'auth-pair': judge_auth_pairing,
    'public-host': judge_public_host,
}


def overall_exit_code(results: Sequence[AxisResult]) -> int:
    """DRIFT 우선, 그다음 UNKNOWN, 전부 PASS 일 때만 0.

    ⚠️ **빈 결과는 0 이 아니다.** 축을 하나도 돌리지 못한 실행이 통과로 보이면 그것이
    이 스크립트가 막으려는 바로 그 침묵이다.
    """
    if not results:
        return EXIT_UNKNOWN
    if any(r.verdict == VERDICT_DRIFT for r in results):
        return EXIT_DRIFT
    if any(r.verdict == VERDICT_UNKNOWN for r in results):
        return EXIT_UNKNOWN
    return EXIT_PASS


# ── 보조 (순수) ─────────────────────────────────────────────────────────────

def env_declared_keys(text: str) -> set[str]:
    """env 파일 본문에서 **선언된** 키 이름만. 주석 처리된 줄은 선언이 아니다."""
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        name, sep, _value = stripped.partition('=')
        if not sep:
            continue
        name = name.strip()
        if name and all(ch.isalnum() or ch == '_' for ch in name):
            keys.add(name)
    return keys


def env_value(text: str, key: str) -> str | None:
    """env 본문에서 한 키의 값. 마지막 선언이 이긴다(셸 env 파일 의미론)."""
    found: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        name, sep, value = stripped.partition('=')
        if sep and name.strip() == key:
            found = value.strip()
    return found


def build_targets(compose_doc: Mapping[str, object]) -> dict[str, dict[str, str]]:
    """``build:`` 를 가진 서비스만 ``{서비스: {image, container_name}}`` 로.

    대상 집합을 손으로 열거하지 않는다 — compose 가 SSOT 다. 서비스가 늘면 이 점검이
    자동으로 따라간다.
    """
    services = compose_doc.get('services')
    if not isinstance(services, dict):
        return {}
    targets: dict[str, dict[str, str]] = {}
    for name, spec in services.items():
        if not isinstance(spec, dict) or not spec.get('build'):
            continue
        image = spec.get('image')
        container = spec.get('container_name')
        if not isinstance(image, str) or not isinstance(container, str):
            continue
        targets[str(name)] = {'image': image, 'container_name': container}
    return targets


def _short(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith('sha256:'):
        cleaned = cleaned[len('sha256:'):]
    return cleaned[:12] if len(cleaned) > 12 else cleaned


#: git object id 로 읽을 수 있는 최소 길이. 사람이 손으로 적은 짧은 토큰(``dev``·``latest``)이
#: 리비전으로 통과하지 않게 한다.
_MIN_REVISION_LENGTH = 7


def looks_like_git_revision(value: str) -> bool:
    """이 문자열이 git object id 로 **읽힐 수 있는가** (allow-list).

    ⚠️ 특정 실패 철자를 금지하지 않는다. 실측(2026-08-23): docker 의 ``.Config.Labels`` 는
    항상 ``map[string]string`` 이라 없는 키는 ``<no value>`` 가 아니라 **빈 문자열**로
    렌더된다 — 즉 그 센티널을 막는 deny-list 는 오늘 이 인터프리터에서 이미 도달 불가였고,
    대신 ``unknown``·``$GIT_REVISION``(미전개)·``latest`` 같은 열거되지 않은 값들이 전부
    통과해 **DRIFT 로 오귀속**됐을 것이다. 그래서 «무엇이 리비전인가» 를 묻는다.
    """
    return len(value) >= _MIN_REVISION_LENGTH and all(c in '0123456789abcdefABCDEF' for c in value)


def _looks_like_ipv4(value: str) -> bool:
    parts = value.split('.')
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit() or not 0 <= int(part) <= 255:
            return False
    return True


# ── 수집 (runner 경유) ───────────────────────────────────────────────────────

def collect_head(runner: Runner) -> str | None:
    result = runner(['git', 'rev-parse', 'HEAD'])
    return result.stdout.strip() if result.ok and result.stdout.strip() else None


def collect_compose_doc(runner: Runner, compose_file: str, env_file: str) -> Mapping[str, object] | None:
    """compose 자신이 해소한 문서를 읽는다 — YAML 을 우리가 다시 파싱하지 않는다."""
    result = runner([
        'docker', 'compose', '-f', compose_file, '--env-file', env_file,
        'config', '--format', 'json',
    ])
    if not result.ok:
        return None
    try:
        parsed = json.loads(result.stdout)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def collect_image_revision(runner: Runner, tag: str) -> str | None:
    result = runner([
        'docker', 'image', 'inspect', tag,
        '--format', '{{index .Config.Labels "' + REVISION_LABEL + '"}}',
    ])
    if not result.ok:
        return None
    # 라벨 부재는 빈 문자열로 온다(실측 2026-08-23 — Labels 는 항상 map[string]string).
    # 값의 «리비전다움» 판정은 여기서 하지 않는다 — 수집과 판정을 섞지 않는다.
    return result.stdout.strip()


def collect_image_id(runner: Runner, tag: str) -> str | None:
    result = runner(['docker', 'image', 'inspect', tag, '--format', '{{.Id}}'])
    return result.stdout.strip() if result.ok and result.stdout.strip() else None


def collect_container_image(runner: Runner, container: str) -> str | None:
    result = runner(['docker', 'inspect', container, '--format', '{{.Image}}'])
    return result.stdout.strip() if result.ok and result.stdout.strip() else None


def collect_migration_status(
    runner: Runner, compose_file: str, env_file: str
) -> Mapping[str, object] | None:
    result = runner([
        'docker', 'compose', '-f', compose_file, '--env-file', env_file,
        'exec', '-T', MIGRATION_PROBE_SERVICE,
        'python', 'scripts/platform_db_migrate.py', 'status',
    ])
    if not result.ok:
        return None
    # status 는 JSON 을 찍지만 컨테이너가 다른 줄을 앞에 낼 수 있으므로 마지막 JSON 객체를 읽는다.
    text = result.stdout.strip()
    start = text.find('{')
    if start < 0:
        return None
    try:
        parsed = json.loads(text[start:])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def collect_auth_pairing_exit(
    runner: Runner, env_file: str, runtime_config_url: str | None
) -> int | None:
    command = [
        sys.executable, 'scripts/check_auth_mode_pairing.py', '--env-file', env_file,
    ]
    if runtime_config_url:
        command += ['--runtime-config-url', runtime_config_url]
    result = runner(command)
    # 127 은 우리 runner 가 «실행 자체를 못 했다» 에 쓰는 값이다.
    return None if result.returncode == 127 else result.returncode


#: WSL VM 이 자기에게 주는 사설 대역. **이 밖의 주소가 목록에 있으면**
#: Windows 쪽을 실제로 읽었다는 뜻이다 — 「읽었는데 없다」와 「못 읽었다」를 가른다.
_WSL_PRIVATE_PREFIXES = ('172.', '10.255.', '127.')


def _is_windows_host_candidate(address: str) -> bool:
    return not address.startswith(_WSL_PRIVATE_PREFIXES)


def is_wsl() -> bool:
    """이 프로세스가 WSL 안에서 도는가.

    ``/proc/version`` 에 ``microsoft`` 가 있으면 WSL 이다 — 커널 자신이 답하므로
    환경변수(``WSL_DISTRO_NAME``)보다 지우기 어렵다.
    """
    try:
        return 'microsoft' in Path('/proc/version').read_text(encoding='utf-8').lower()
    except OSError:
        return False


def collect_windows_host_addresses(runner: Runner) -> list[str] | None:
    """Windows 호스트의 IPv4 주소. **WSL 에서만 의미가 있다.**

    ⚠️ **이 함수가 없던 동안 이 축은 중앙 PC 에서 영원히 DRIFT 였다** (실측 2026-09-03).

    중앙 PC 는 WSL 이고, LAN 이 보는 주소(`PUBLIC_HOST`)는 **Windows 호스트**의
    것이다. WSL VM 은 `172.x` 만 갖고 그 주소를 **자기 인터페이스로 갖지 않는다** —
    WSL2 가 포워딩할 뿐이다. 그래서 `hostname -I` 만 보면
    *「PUBLIC_HOST 가 이 PC 주소에 없다」* 가 **항상** 참이 된다.

    실측: 중앙 PC 에서 `PUBLIC_HOST=10.206.34.233` 이 DRIFT 로 나왔고,
    `powershell.exe Get-NetIPAddress` 로 물으니 **그 주소가 거기 있었다.**
    설정은 옳았고 **축이 볼 수 없었다.**

    > 그리고 오탐을 내는 게이트는 삭제된다 — 이 저장소가 반복해서 낸 결론이다.

    ⚠️ 실패는 **조용히 None** 이다(예외 아님). powershell 이 없거나 느린 것은
    *「Windows 주소가 없다」* 가 아니라 *「물어보지 못했다」* 이고, 호출자가 그 둘을
    가른다.
    """
    result = runner([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        'Get-NetIPAddress -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress',
    ])
    if not result.ok:
        return None
    addresses = [
        line.strip() for line in result.stdout.splitlines()
        if _looks_like_ipv4(line.strip())
    ]
    return addresses or None


def collect_host_addresses(runner: Runner) -> list[str] | None:
    result = runner(['hostname', '-I'])
    if not result.ok:
        return None
    addresses = result.stdout.split()
    if is_wsl():
        # ⚠️ **합친다, 대체하지 않는다.** WSL VM 주소도 여전히 유효한 답이다
        # (컨테이너끼리는 그쪽으로 통신한다). 대체하면 그 경우를 잃는다.
        windows = collect_windows_host_addresses(runner)
        if windows is not None:
            addresses = addresses + [a for a in windows if a not in addresses]
    return addresses or None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return None


# ── 실행 ────────────────────────────────────────────────────────────────────

def run_all_axes(
    *,
    runner: Runner,
    compose_file: str = DEFAULT_COMPOSE_FILE,
    env_file: str = DEFAULT_ENV_FILE,
    env_example: str = DEFAULT_ENV_EXAMPLE,
    runtime_config_url: str | None = None,
    read_text: Callable[[Path], str | None] = _read_text,
) -> list[AxisResult]:
    """여섯 축을 전부 돌린다. 순서는 출력 순서이며 판정에 영향을 주지 않는다."""
    compose_doc = collect_compose_doc(runner, compose_file, env_file)
    targets = build_targets(compose_doc) if compose_doc else {}

    tags = sorted({spec['image'] for spec in targets.values()})
    container_tags = {spec['container_name']: spec['image'] for spec in targets.values()}

    example_text = read_text(REPO_ROOT / env_example)
    env_text = read_text(REPO_ROOT / env_file)

    results = [
        judge_revision(
            collect_head(runner),
            {tag: collect_image_revision(runner, tag) for tag in tags},
        ),
        judge_image_ids(
            {name: collect_container_image(runner, name) for name in container_tags},
            {tag: collect_image_id(runner, tag) for tag in tags},
            container_tags,
        ),
        judge_migration_status(collect_migration_status(runner, compose_file, env_file)),
        judge_env_keys(example_text, env_text),
        judge_auth_pairing(collect_auth_pairing_exit(runner, env_file, runtime_config_url)),
        judge_public_host(
            env_value(env_text, 'PUBLIC_HOST') if env_text else None,
            collect_host_addresses(runner),
        ),
    ]
    # 축 하나를 추가하고 위 목록에 넣지 않으면 봉인이 red 다(JUDGES 파생).
    return results


def describe_measuring_host(runner: Runner) -> str:
    """**어느 기계에서 쟀는지** 한 줄. 축이 아니라 머리말이다.

    ⚠️ **오늘 이 자리에 네 번 걸렸다** (실측 2026-09-03).

    이 계열은 개발 PC 와 중앙 PC 에서 **같은 이름의 컨테이너**를 돌린다
    (`fcc-central-platform-api` · `fcc-central-keycloak` · …). 그래서
    `docker compose ps` · `docker inspect` · 이 게이트의 출력이 **두 기계에서
    완전히 같은 모양**이고, 그 출력을 복사해 옮기면 **기계 축이 사라진다.**

    실측된 네 건: 한 세션이 개발 PC 관측으로 「중앙이 서비스 가능하다」를 두 번
    보고했고, 다른 세션이 개발 PC 의 Keycloak 을 중앙으로 읽었으며, 세 번째가
    그 보고를 근거로 운영자에게 전달했다. **매번 사람이 「여기는 개발 PC야」라고
    알려줘서** 정정됐다.

    가른 것은 결국 **도달성**이었다 — `curl 10.206.34.233:8081` 이 닿느냐.
    이름·포트·컨테이너 목록은 어느 것도 그 축을 갖지 않았다.

    > **어느 기계인지는 이름이 아니라 도달성으로 판정한다.**

    그러므로 처방은 「더 주의하자」가 아니라 **관측이 스스로 기계를 말하게 하는
    것**이다. 이 한 줄이 있었으면 네 건 중 셋은 복사하는 순간 드러났다.

    ⚠️ 못 읽는 값은 **비워 두지 않고 `?` 로 적는다** — 빈 자리는 「없다」와
    「못 읽었다」를 같은 값으로 만든다.
    """
    hostname = runner(['hostname'])
    addresses = runner(['hostname', '-I'])
    host = hostname.stdout.strip() if hostname.ok else '?'
    addrs = addresses.stdout.split() if addresses.ok else []
    label = f'측정 기계: {host}'
    if addrs:
        label += f'  [{" ".join(addrs[:4])}{" …" if len(addrs) > 4 else ""}]'
    else:
        label += '  [주소 ?]'
    if is_wsl():
        label += '  (WSL — LAN 주소는 Windows 호스트 쪽이다)'
    return label


def format_report(results: Sequence[AxisResult], *, header: str | None = None) -> str:
    width = max((len(r.axis) for r in results), default=0)
    lines = [f'{r.axis.ljust(width)}  {r.verdict:<7} {r.detail}' for r in results]
    if header:
        # ⚠️ 머리말을 **맨 위**에 둔다. 꼬리에 두면 `| tail` 로 잘려 나가고,
        # 출력을 잘라 붙이는 것이 정확히 이 결함이 퍼지는 경로다.
        lines = [header, ''] + lines
    return '\n'.join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='배포 드리프트 점검 — 도는 배포가 현재 저장소 상태와 같은가.',
    )
    parser.add_argument('--compose-file', default=DEFAULT_COMPOSE_FILE)
    parser.add_argument('--env-file', default=DEFAULT_ENV_FILE)
    parser.add_argument('--env-example', default=DEFAULT_ENV_EXAMPLE)
    parser.add_argument(
        '--runtime-config-url',
        default=None,
        help='실제로 서빙되는 runtime-config.js 주소(있으면 auth 짝을 배포 값으로 판정)',
    )
    parser.add_argument('--json', action='store_true', help='JSON 으로 출력')
    args = parser.parse_args(argv)

    results = run_all_axes(
        runner=subprocess_runner,
        compose_file=args.compose_file,
        env_file=args.env_file,
        env_example=args.env_example,
        runtime_config_url=args.runtime_config_url,
    )
    code = overall_exit_code(results)

    measuring_host = describe_measuring_host(subprocess_runner)

    if args.json:
        print(json.dumps(
            {
                'exit_code': code,
                # ⚠️ JSON 에도 넣는다 — 기계 축이 사람용 출력에만 있으면
                # 자동화가 그것을 잃는다.
                'measuring_host': measuring_host,
                'axes': [
                    {'axis': r.axis, 'verdict': r.verdict, 'detail': r.detail}
                    for r in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(format_report(results, header=measuring_host))
        if code == EXIT_DRIFT:
            print('\nDRIFT — 도는 배포가 이 저장소의 현재 상태와 다르다.')
        elif code == EXIT_UNKNOWN:
            print('\nUNKNOWN — 판정하지 못한 축이 있다. 미확인은 통과가 아니다.')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
