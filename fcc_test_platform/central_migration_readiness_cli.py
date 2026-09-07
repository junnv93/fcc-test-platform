#!/usr/bin/env python3
"""중앙 마이그레이션 **사전점검** — 창을 열기 전에 「준비됐나 · 무슨 일이 일어나나」.

``scripts/check_deployment_drift.py`` 의 형제다. 그쪽은 배포 **뒤**에 *"지금 도는 것이
방금 배포하려던 그것인가"* 를 묻고, 이쪽은 배포 **앞**에 *"창을 열어도 되는가"* 를 묻는다.

## 왜 이 도구가 생겼나 — 실측된 값

2026-09-06 에 한 세션이 런북 §4-a 를 그대로 따라 개발 PC 의 중앙 스택에 031~035 를
적용했다. 절차는 옳았지만 **문서가 몰랐던 것 넷**에 차례로 걸렸다:

1. `central-migrate` 가 `exit 2` 로 죽었다 — 이미지가 상자 표식을 잃어 러너가
   마이그레이션 디렉터리를 못 찾았다. **창을 연 뒤에 알았다.**
2. `032` 거부 가드가 **실제로 걸렸다**(프로젝트 1건). 창 안에서 만났으면 창이 길어진다.
3. §4-a ②의 정지 목록이 `platform-api-node` 를 빠뜨린다 — 그 서비스도 같은 이미지로
   옛 코드를 서빙한다.
4. `001` 드리프트는 예고돼 있었지만, `001` **이 아닌** 이름이 섞였는지는 사람이
   눈으로 봐야 했다.

넷 다 **읽기 전용으로 미리 답할 수 있는 질문**이다. 창 안에서 처음 만날 이유가 없다.

## 축

===================  =========================================================
축                   무엇을 묻는가
===================  =========================================================
``machine``          내가 **중앙 PC** 에 있는가 (개발 PC 가 같은 컨테이너를 돌린다)
``checkout``         이 체크아웃이 컨테이너에서 **죽지 않는** 코드를 담고 있는가
``deploy-class``     이번에 적용될 것이 **정지 창**을 요구하는가 (원장에서 파생)
``refusal-guard``    ``032`` 가 **거부할** 데이터가 있는가 (읽기 전용 질의)
``ledger``           미적용/드리프트가 무엇인가 · ``001`` 아닌 드리프트가 있는가
``stop-list``        창 안에서 **무엇을 멈춰야** 하는가 (compose 에서 파생)
===================  =========================================================

사용::

    fcc-platform-central-migration-readiness
    fcc-platform-central-migration-readiness --json

설치 전(중앙 PC 에서 ``git pull`` 직후)에는 저장소의 얇은 진입점으로도 부를 수 있다 —
``python3 scripts/check_central_migration_readiness.py``. ⚠️ 그것은 **저장소 안에서만**
성립하는 경로다. 이 모듈이 다른 곳에 실행을 지시할 때는 언제나 명령 이름을 쓴다.

종료 코드: 전 축 READY 면 ``0``, 한 축이라도 BLOCKED 면 ``1``, BLOCKED 는 없는데
판정하지 못한 축이 있으면 ``2``.

⚠️ **UNKNOWN 을 통과와 같은 코드로 만들지 않는다.** 묻지 못한 축은 통과가 아니라
미확인이다. 형제 도구들이 이미 같은 규칙을 적고 있다.

⚠️ **이 도구는 아무것도 바꾸지 않는다.** 전 축이 읽기 전용이다 — 사전점검이 상태를
바꾸면 그 뒤의 판정이 자기가 만든 상태를 재게 된다.

판정은 **순수 함수**(``judge_*``)이고 수집은 **주입된 runner** 를 거친다. 그 분리가
docker 없이 시험할 수 있게 하고, 봉인이 실제 판정 코드를 부르게 한다.
"""
from __future__ import annotations

import argparse
import ast
import base64
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]

VERDICT_READY = 'READY'
VERDICT_BLOCKED = 'BLOCKED'
VERDICT_UNKNOWN = 'UNKNOWN'

EXIT_READY = 0
EXIT_BLOCKED = 1
EXIT_UNKNOWN = 2

#: 중앙 PC 의 Windows 컴퓨터 이름. 인계 문서들이 세 기계를 이 이름으로 적는다.
#: ⚠️ 값이 아니라 **축**이 요점이다 — 다른 현장이면 `--central-name` 으로 바꾼다.
DEFAULT_CENTRAL_NAME = 'SUW0521PC1WNBRE'

DEFAULT_COMPOSE_FILE = 'infra/docker-compose.central.yml'
DEFAULT_ENV_FILE = 'infra/central/central.env'

#: `001` 만이 드리프트의 **선언된 예외**다(재렌더되는 생성물). 다른 이름이 나오면
#: 이미 적용된 마이그레이션이 편집됐다는 뜻이고 답은 `reconcile` 이 아니라 새 파일이다.
RECONCILABLE = '001_initial_central_db'

#: `032` 의 `RAISE EXCEPTION` 가드와 **같은 술어**. 값을 파괴할지 말지를 가른다.
#: ⚠️ 술어를 여기 다시 적는 것은 두 SSOT 다. 그래서 그 사실을 봉인이 대조한다
#: (`tests/test_central_migration_readiness_axes.py`).
REFUSAL_GUARD_SQL = (
    "SELECT count(*), coalesce(string_agg(format('%s (customer=%L, applicant_name=%L)', "
    "project_code, customer, applicant_name), '; ' ORDER BY project_code), '') "
    'FROM projects '
    "WHERE customer IS NOT NULL AND btrim(customer) <> '' "
    "AND applicant_name IS NOT NULL AND btrim(applicant_name) <> '' "
    'AND lower(btrim(customer)) <> lower(btrim(applicant_name))'
)


@dataclass(frozen=True)
class AxisResult:
    axis: str
    verdict: str
    detail: str

    def __post_init__(self) -> None:
        if self.verdict not in (VERDICT_READY, VERDICT_BLOCKED, VERDICT_UNKNOWN):
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
    try:
        completed = subprocess.run(
            list(command), cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
        )
    except (OSError, ValueError) as exc:
        return CommandResult(returncode=127, stdout='', stderr=str(exc))
    return CommandResult(completed.returncode, completed.stdout or '', completed.stderr or '')


# ── 판정 (순수 함수) ─────────────────────────────────────────────────────────

def judge_machine(computer_name: str | None, central_name: str) -> AxisResult:
    """내가 중앙 PC 에 있는가.

    ⚠️ **이 축이 첫 번째인 이유.** 개발 PC 가 `fcc-central-*` 컨테이너를 **같은
    이름으로** 돌린다. `docker ps` 도, 이 도구의 나머지 축도 두 기계에서 같은 모양이다.
    여기서 틀리면 그 아래 전부가 «다른 기계를 재고 옳게 보인다».
    """
    if computer_name is None:
        return AxisResult(
            'machine', VERDICT_UNKNOWN,
            'COMPUTERNAME 을 읽지 못했다 (powershell.exe interop 부재?) — '
            '기계를 확인하지 못한 채로 창을 열지 마라',
        )
    if computer_name == central_name:
        return AxisResult('machine', VERDICT_READY, f'중앙 PC ({computer_name})')
    return AxisResult(
        'machine', VERDICT_BLOCKED,
        f'여기는 {computer_name} 이고 중앙은 {central_name} 이다. '
        '개발 PC 도 같은 이름의 fcc-central-* 컨테이너를 돌리므로 '
        '여기서 적용하면 중앙이 아니라 이 기계의 DB 가 바뀐다.',
    )


def judge_checkout(box_markers: Sequence[str] | None, how: str = 'installed') -> AxisResult:
    """이 체크아웃이 **컨테이너 안에서 죽지 않는** 코드를 담고 있는가.

    실측 2026-09-06: 이미지는 설치 뒤 `pyproject.toml` 을 지운다(휠을 가리지 않으려고).
    해소기가 그것만 상자 표식으로 인정하던 판에서 `central-migrate` 가
    `마이그레이션 디렉터리가 없다: /docs/platform/migrations` 로 죽었다.

    ⚠️ 커밋 SHA 로 묻지 않는다 — 그러면 리베이스·체리픽에 거짓말한다. **성질**로 묻는다:
    이 트리의 해소기가 배송 기록을 상자 표식으로 «세는가».
    """
    if box_markers is None:
        return AxisResult(
            'checkout', VERDICT_UNKNOWN,
            'fcc_test_platform/repository_anchor.py 를 설치본으로도 소스로도 읽지 못했다 — '
            '이 저장소 트리에서 돌리고 있는지 확인하라',
        )
    # 설치본이면 «값»이, 소스 모드면 «이름»이 온다. 둘 다 같은 성질을 답한다.
    knows_layout = any(
        marker.endswith('.extraction-layout.json') or marker == _LAYOUT_SYMBOL
        for marker in box_markers
    )
    seen = f'{", ".join(box_markers)} ({how})'
    if knows_layout:
        return AxisResult(
            'checkout', VERDICT_READY, f'해소기가 배송 기록을 상자 표식으로 센다 — {seen}',
        )
    return AxisResult(
        'checkout', VERDICT_BLOCKED,
        f'상자 표식이 {seen} 뿐이다. 이미지는 pip 설치 뒤 pyproject.toml 을 '
        '지우므로 컨테이너 안에서 해소가 실패하고 central-migrate 가 exit 2 로 죽는다. '
        '이 수리를 담은 main 을 받아라.',
    )


def judge_deploy_class(stdout: str | None) -> AxisResult:
    """정지 창이 필요한가 — **원장에서 파생**한다(표를 외우지 않는다)."""
    if stdout is None:
        return AxisResult(
            'deploy-class', VERDICT_UNKNOWN,
            'scripts/platform_migration_deploy_class.py 를 돌리지 못했다',
        )
    names = re.findall(r'^(\d{3}_[A-Za-z0-9_]+\.sql)\s+.*STOP-WINDOW', stdout, re.MULTILINE)
    refusing = re.findall(r'^(\d{3}_[A-Za-z0-9_]+\.sql)\s+.*CAN-REFUSE', stdout, re.MULTILINE)
    if not names:
        return AxisResult(
            'deploy-class', VERDICT_READY,
            '정지 창이 필요한 파일 없음 — §4-a 를 건너뛰고 §5 로 간다',
        )
    tail = f' · 거부 가드 있음: {", ".join(refusing)}' if refusing else ''
    return AxisResult(
        'deploy-class', VERDICT_READY,
        f'정지 창 필요 {len(names)}건: {", ".join(names)}{tail}',
    )


def judge_refusal_guard(count: int | None, detail: str, column_present: bool | None) -> AxisResult:
    """`032` 가 거부할 데이터가 있는가 — **읽기 전용**.

    ⚠️ `customer` 칸이 이미 없으면 그 관문은 지났다. 그것은 실패가 아니다.
    """
    if column_present is False:
        return AxisResult(
            'refusal-guard', VERDICT_READY,
            'projects.customer 가 이미 없다 — 032 는 지난 관문이다',
        )
    if count is None:
        return AxisResult(
            'refusal-guard', VERDICT_UNKNOWN,
            '중앙 DB 에 질의하지 못했다 — 거부 여부를 «모르는 채로» 창을 열지 마라',
        )
    if count == 0:
        return AxisResult('refusal-guard', VERDICT_READY, '충돌 0건 — 032 는 거부하지 않는다')
    return AxisResult(
        'refusal-guard', VERDICT_BLOCKED,
        f'{count}건이 두 칸에 서로 다른 주체를 든다: {detail or "(이름 없음)"}. '
        '창을 열기 «전»에 화면에서 정리하고 이 점검을 다시 돌려라 — '
        '창 안에서 거부가 나면 창이 길어진다.',
    )


def _ledger_entries(value: object) -> list[str]:
    """원장 페이로드의 배열 필드 하나 — 「JSON 은 무엇이든 줄 수 있다」를 여기서 한 번 접는다.

    ⚠️ `collect_ledger` 는 최상위가 dict 인지 **만** 본다(:408 `isinstance(payload, dict)`).
    그 안의 값 타입은 컨테이너 안 `fcc-platform-db-migrate status` 의 출력이 정하고,
    이 모듈에는 그것을 강제할 자리가 없다 — 그러니 「배열일 것」은 «앎»이 아니라
    «기대»다. 배열이 아니면 「없다」로 읽는다: 문자열이 오면 `for item in value` 가
    한 «글자»씩 돌아서, 원장이 망가졌을 때 판정문에 글자 목록이 실린다.

    ⚠️ 가드를 두 호출부에 각각 흩뿌리지 않고 여기 한 번만 두는 이유가 그것이다 —
    흩뿌리면 「이 값은 정말 무엇이든 될 수 있다」는 사실이 두 자리에 적히고,
    셋째 필드가 생기는 날 그중 하나가 빠진다.
    """
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value]


def judge_ledger(status: Mapping[str, object] | None) -> AxisResult:
    """미적용·드리프트 — 그리고 드리프트가 `001` «뿐인가»."""
    if status is None:
        return AxisResult('ledger', VERDICT_UNKNOWN, '마이그레이션 원장을 읽지 못했다')
    drift = _ledger_entries(status.get('drift'))
    pending = _ledger_entries(status.get('pending'))
    unexpected = [d for d in drift if not d.startswith(RECONCILABLE)]
    if unexpected:
        return AxisResult(
            'ledger', VERDICT_BLOCKED,
            f'{RECONCILABLE} 이 «아닌» 드리프트가 있다: {unexpected}. '
            'reconcile 하지 마라 — 이미 적용된 마이그레이션이 편집됐다는 뜻이고 '
            '답은 새 NNN_*.sql 이다.',
        )
    parts = [f'미적용 {len(pending)}건' + (f': {", ".join(pending)}' if pending else '')]
    if drift:
        parts.append(f'{RECONCILABLE} 드리프트 — 예고된 것이다(§4-a ①-c 의 reconcile)')
    return AxisResult('ledger', VERDICT_READY, ' · '.join(parts))


def judge_stop_list(services: Mapping[str, str] | None) -> AxisResult:
    """창 안에서 무엇을 멈춰야 하는가 — **compose 에서 파생**한다.

    ⚠️ 런북 §4-a ②는 `platform-api web headless-api` 셋을 적는데, 2026-09-04 이후
    `platform-api-node` 가 **같은 이미지**를 쓴다. 목록을 손으로 적으면 그런 서비스가
    조용히 빠지고, 그것은 「옛 코드가 창 안에서 계속 서빙한다」가 된다.
    그래서 여기서는 **이미지를 공유하는 서비스 전부**를 파생한다.
    """
    if not services:
        return AxisResult('stop-list', VERDICT_UNKNOWN, 'compose 설정을 읽지 못했다')
    postgres = {name for name in services if 'postgres' in name}
    migrate = {name for name, image in services.items() if 'migrate' in name}
    # postgres 는 멈추지 않는다 — 마이그레이션이 그것을 쓴다. migrate 는 원샷 잡이다.
    keycloak = {name for name in services if 'keycloak' in name}
    stop = sorted(set(services) - postgres - migrate - keycloak)
    if not stop:
        return AxisResult('stop-list', VERDICT_UNKNOWN, '멈출 서비스를 하나도 파생하지 못했다')
    return AxisResult(
        'stop-list', VERDICT_READY,
        'stop ' + ' '.join(stop) + '  (postgres 는 멈추지 않는다 — 마이그레이션이 쓴다)',
    )


# ── 수집 ─────────────────────────────────────────────────────────────────────

def collect_computer_name(runner: Runner) -> str | None:
    result = runner(['powershell.exe', '-NoProfile', '-Command', '$env:COMPUTERNAME'])
    if not result.ok:
        return None
    name = result.stdout.strip().replace('\r', '')
    return name or None


#: `repository_anchor` 가 배송 기록을 가리킬 때 쓰는 이름. **값이 아니라 이름**이다 —
#: 값(`.extraction-layout.json`)을 여기 적으면 그 순간 두 SSOT 가 된다.
_LAYOUT_SYMBOL = 'LAYOUT_RECORD_NAME'


def collect_box_markers() -> tuple[Sequence[str] | None, str]:
    """상자 표식 목록과 **어떻게 알아냈는지**.

    ⚠️ 두 모드가 필요한 이유는 이 도구의 «대상»이다. 중앙 세션은 `git pull` 직후
    아직 아무것도 설치하지 않은 상태에서 이것을 돌린다. 그때
    `fcc_test_platform.repository_anchor` 는 import 되지 않는다 — 그 모듈이
    `fcc_test_contracts` 에서 상수를 가져오는데 그 배포판이 없기 때문이다.
    실측 2026-09-06: 맨 `python3` 로 돌리자 이 축이 UNKNOWN 이 됐다.

    ⚠️ **그리고 하필 그 축이 exit 2 를 막아 주는 축이다.** 도구가 가장 필요한 순간에
    가장 값진 축이 침묵하면 그 도구는 없는 것과 같다.

    그래서 설치본이 없으면 **소스를 읽는다**. 값을 다시 적지 않고 «이름»을 본다 —
    `BOX_MARKERS` 가 `LAYOUT_RECORD_NAME` 을 참조하는가. 어느 모드였는지는 판정이
    말한다(무엇을 쟀는지 감추지 않는다).
    """
    try:
        from fcc_test_platform.repository_anchor import BOX_MARKERS
    except Exception:  # noqa: BLE001 — 설치본이 없다. 소스로 내려간다.
        return _box_markers_from_source(), 'source'
    return [str(marker) for marker in BOX_MARKERS], 'installed'


def _box_markers_from_source() -> Sequence[str] | None:
    """설치 없이 — `BOX_MARKERS` 대입문을 AST 로 읽는다.

    문자열 리터럴은 그대로, 이름 참조는 **그 식별자**로 낸다. `LAYOUT_RECORD_NAME` 의
    값을 여기서 해소하지 않는 것이 요점이다 — 해소하려면 그 값을 적어야 하고, 그러면
    두 SSOT 다.
    """
    path = REPO_ROOT / 'fcc_test_platform' / 'repository_anchor.py'
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        # ⚠️ 좁힘을 «문»으로 한다. 이 자리는 한때
        #        targets = node.targets if isinstance(node, ast.Assign) else []
        #    이었는데, 조건식 안의 `isinstance` 는 그 식 «밖»으로 좁힘을 내보내지
        #    못한다 — 아래 `node.value` 가 여전히 `ast.stmt` 를 보고, 그 타입에는
        #    `value` 가 없다. 런타임에는 맞는 코드였다(빈 targets 는 continue 로
        #    걸린다). 즉 「맞게 도는데 그 이유가 안 적힌」 자리였다.
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == 'BOX_MARKERS' for t in node.targets):
            continue
        value = node.value
        if not isinstance(value, (ast.Tuple, ast.List)):
            return None
        out: list[str] = []
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                out.append(element.value)
            elif isinstance(element, ast.Name):
                out.append(element.id)
        return out
    return None


def collect_deploy_class(runner: Runner) -> str | None:
    result = runner([sys.executable, 'scripts/platform_migration_deploy_class.py'])
    return result.stdout if result.ok else None


def _compose(compose_file: str, env_file: str, *rest: str) -> list[str]:
    return ['docker', 'compose', '-f', compose_file, '--env-file', env_file, *rest]


def collect_refusal_guard(
    runner: Runner, compose_file: str, env_file: str,
) -> tuple[int | None, str, bool | None]:
    # ⚠️ SQL 을 셸 인자로 «따옴표로 감싸» 넘기지 않는다. 이 질의는 빈 문자열 리터럴
    #    (`btrim(customer) <> ''`)을 담는데, 그것이 `sh -c '…'` 안으로 들어가면
    #    따옴표가 **두 겹의 셸을 지나며 깨진다**(2026-09-06 실측: 이 축이 조용히
    #    UNKNOWN 이 됐다 — 그리고 UNKNOWN 은 「질의가 0을 답했다」와 다른 뜻이다).
    #    손으로 돌릴 때는 heredoc 이 그 문제를 피하지만 runner 는 stdin 을 쓰지 않는다.
    #    base64 는 **인용 규칙이 없는 전송**이라 그 계급 전체를 없앤다.
    encoded = base64.b64encode(REFUSAL_GUARD_SQL.encode('utf-8')).decode('ascii')
    result = runner(_compose(
        compose_file, env_file, 'exec', '-T', 'postgres', 'sh', '-c',
        f'echo {encoded} | base64 -d | psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F"|"',
    ))
    blob = (result.stdout or '') + (result.stderr or '')
    if 'does not exist' in blob and 'customer' in blob:
        return None, '', False
    if not result.ok:
        return None, '', None
    line = next((ln for ln in result.stdout.splitlines() if '|' in ln), '')
    head, _, tail = line.partition('|')
    try:
        return int(head.strip()), tail.strip(), True
    except ValueError:
        return None, '', None


def collect_ledger(runner: Runner, compose_file: str, env_file: str) -> Mapping[str, object] | None:
    # ⚠️ 컨테이너 안에서도 `scripts/…` 를 부르지 않는다. 이 모듈은 **배포판의 일부**이고,
    #    배포판은 자기가 싣지 않는 경로를 실행 지시로 내놓으면 안 된다
    #    (`tests/test_the_package_never_tells_anyone_to_run_scripts.py` 가 그 축을 막는다 —
    #    2026-09-06 에 이 줄이 실제로 걸렸다). 이미지는 `pip install` 로 콘솔 명령을
    #    갖는다(실측: `/usr/local/bin/fcc-platform-db-migrate`).
    result = runner(_compose(
        compose_file, env_file, 'exec', '-T', 'platform-api',
        'fcc-platform-db-migrate', 'status',
    ))
    if not result.ok:
        return None
    start = result.stdout.find('{')
    if start < 0:
        return None
    try:
        payload = json.loads(result.stdout[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def collect_services(runner: Runner, compose_file: str, env_file: str) -> Mapping[str, str] | None:
    result = runner(_compose(compose_file, env_file, 'config', '--format', 'json'))
    if not result.ok:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    services = payload.get('services') or {}
    return {name: str(spec.get('image') or '') for name, spec in services.items()}


# ── 조립 ─────────────────────────────────────────────────────────────────────

def run_all_axes(
    *, runner: Runner, compose_file: str, env_file: str, central_name: str,
) -> list[AxisResult]:
    count, detail, column_present = collect_refusal_guard(runner, compose_file, env_file)
    return [
        judge_machine(collect_computer_name(runner), central_name),
        judge_checkout(*collect_box_markers()),
        judge_deploy_class(collect_deploy_class(runner)),
        judge_refusal_guard(count, detail, column_present),
        judge_ledger(collect_ledger(runner, compose_file, env_file)),
        judge_stop_list(collect_services(runner, compose_file, env_file)),
    ]


def overall_exit_code(results: Sequence[AxisResult]) -> int:
    """⚠️ BLOCKED 가 UNKNOWN 을 이긴다 — 아는 결함이 먼저 조치 대상이다."""
    if any(r.verdict == VERDICT_BLOCKED for r in results):
        return EXIT_BLOCKED
    if any(r.verdict == VERDICT_UNKNOWN for r in results):
        return EXIT_UNKNOWN
    return EXIT_READY


def format_report(results: Sequence[AxisResult]) -> str:
    width = max(len(r.axis) for r in results)
    return '\n'.join(f'{r.axis:<{width}}  {r.verdict:<7} {r.detail}' for r in results)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='중앙 마이그레이션 사전점검 — 창을 열기 전에 읽기 전용으로 묻는다.',
    )
    parser.add_argument('--compose-file', default=DEFAULT_COMPOSE_FILE)
    parser.add_argument('--env-file', default=DEFAULT_ENV_FILE)
    parser.add_argument('--central-name', default=DEFAULT_CENTRAL_NAME)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    results = run_all_axes(
        runner=subprocess_runner,
        compose_file=args.compose_file,
        env_file=args.env_file,
        central_name=args.central_name,
    )
    code = overall_exit_code(results)
    if args.json:
        print(json.dumps(
            {'exit_code': code,
             'axes': [{'axis': r.axis, 'verdict': r.verdict, 'detail': r.detail} for r in results]},
            ensure_ascii=False, indent=2))
    else:
        print(format_report(results))
        if code == EXIT_BLOCKED:
            print('\nBLOCKED — 창을 열지 마라. 위 BLOCKED 축을 먼저 해소하라.')
        elif code == EXIT_UNKNOWN:
            print('\nUNKNOWN — 판정하지 못한 축이 있다. 미확인은 통과가 아니다.')
        else:
            print('\nREADY — 런북 §4-a ② 의 순서로 창을 열어도 된다.')
    return code


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
