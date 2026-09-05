#!/usr/bin/env python3
"""`FCC_CENTRAL_PROVIDER_ID` 짝 검사 — 중앙과 노드가 같은 값을 쓰는가 (F-1, 2026-09-02).

`scripts/check_auth_mode_pairing.py` 의 형제다. 그 스크립트가 인증 모드 튜플을 맞추듯,
이 스크립트는 **provider 정체성 값 하나**를 세 자리에서 맞춘다:

    계약 SSOT   fcc_test_contracts.headless.api_contracts.DEFAULT_PROVIDER_METADATA
    중앙        infra/central/central.env  (컨테이너가 읽는 값)
    노드        측정 PC 런처의 FCC_CENTRAL_PROVIDER_ID

**왜 필요한가.** 이 값은 두 프로세스가 **각자** 설정하고, 둘이 다르면
``ChamberResultIngestionService.ingest`` 가 ``provider_id does not match central
configuration`` 으로 거절한다. 그 거절은 **인입 시점**에 오고 앞선 두 층(챔버 토큰
바인딩 · 기계 신분증)을 이미 통과한 뒤라 **인증 문제처럼 읽힌다**. 실측 2026-09-02 —
중앙 셋은 자연키로 일치하는데 노드만 ``providers.id`` UUID 였고, 그것을 검사하는 것이
**0건**이었다.

**어쩌다 UUID 가 들어갔나.** 형제 봉인 ``TestProviderIdentityValue`` 는
`central.env.example` 과 compose 기본값의 값을 계약 SSOT 로 묶었지만 **런북은 그 집합에
없었다**. 그 빈자리에서 런북이 ``providers.id`` UUID 를 지시하게 됐고, 운영자는 런북을
따랐다. 런북의 근거 문장(*"동기화 어댑터는 그 값을 그대로 레코드에 넣는다"*)은 낡았다 —
``CentralBackendSyncAdapter.sync_result_events`` 는 platform readiness 가 해소한
``provider_uuid`` 를 쓰고 config 값은 폴백이며, 그 위 주석이 *"the configured provider
code … Once platform readiness resolves the central row, every FK-bearing ingestion
record uses providers.id"* 라고 명시한다.

⚠️ **UUID 를 거절하는 근거는 모양이 아니라 계약이다.** 계약 SSOT 가 언젠가 UUID 형태를
고르면 그때는 UUID 가 맞는 값이다. 판정은 언제나 *"계약 SSOT 와 같은가"* 이고, UUID
모양은 **진단 문구를 고르는 데만** 쓴다 — 운영자가 런북을 따라 넣은 값이라는 것을
알려주기 위해서다.

⚠️ **값을 바꿀 수 있는 창은 좁다.** 이 값은 중앙 세션 uuid 파생
``uuid5(ns, "{provider_id}:{로컬 세션번호}")`` 에도 들어간다. 측정이 이미 인입된 뒤에
바꾸면 같은 측정이 다른 세션으로 중복 유입된다. 그러므로 **첫 측정 전에** 맞춰야 한다 —
이 검사기가 존재하는 이유가 그것이다.

사용법::

    python3 scripts/check_central_provider_id_pairing.py \\
        --central-env infra/central/central.env \\
        --node-env /home/kmjkds/fcc-node-runtime/run-node.sh

종료 코드: 0 일치 · 1 불일치 · 2 판정 불가(읽을 수 없음).
**2 를 0 으로도 1 로도 접지 않는다** — 「읽을 수 없다」와 「틀렸다」는 다른 사실이고,
접으면 판정이 거짓말이 된다.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
#: ⚠️ **경로가 아니라 모듈이다 (2026-09-05).** 형제의 알맹이는 이제
#: `fcc_test_platform.check_auth_mode_pairing_cli` 에 산다 — `scripts/` 는 패키지가
#: 아니라 휠이 나르지 못하므로, 이 레인을 핀으로 받는 소비자에게 그 파일은 오지
#: 않는다. `scripts/check_auth_mode_pairing.py` 는 22줄 진입점만 남았고, 그것을
#: **파일로** 읽으면 파서가 없다.
_SIBLING_MODULE = 'fcc_test_platform.check_auth_mode_pairing_cli'
#: ⚠️ 이 저장소에는 ``src/`` 가 없다 (레인 분리 이후). 계약 패키지는 **설치된 배포판**
#: 으로 오므로 경로를 더할 일이 없고, 없는 경로를 sys.path 에 넣는 것은 「무엇을 읽어
#: 판정했나」를 흐린다. 레포 루트만 둔다.
_SRC_ROOT = _REPO_ROOT

ENV_PROVIDER_ID = 'FCC_CENTRAL_PROVIDER_ID'

EXIT_OK = 0
EXIT_MISMATCH = 1
EXIT_UNDETERMINED = 2

_UUID_RE = re.compile(
    r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
)


class Undetermined(Exception):
    """판정할 수 없다 (→ exit 2). **불일치(1)로도 일치(0)로도 접지 않는다.**"""


def _load_sibling():
    """형제 스크립트를 그대로 불러 env 파서를 **재사용**한다.

    사본을 뜨지 않는 이유: 그 파서는 compose 와 갈라지는 네 가지(인라인 주석 · BOM ·
    ``KEY=`` · 중복 키)를 적대 평가 실측으로 맞춰 놓았다. 사본이 둘이면 그중 하나가
    먼저 낡고, 낡은 쪽이 **정상 파일에 거짓 경보**를 낸다.

    ⚠️ **여기서 죽으면 안 된다 (2026-09-03 이관 시 수정).** 형제는 모듈 레벨에서
    ``fcc_test_contracts`` 를 import 하므로, 계약 패키지가 없는 인터프리터에서는 이
    로드가 ``ModuleNotFoundError`` 를 던진다. 그것이 **모듈 레벨**에서 일어나던 동안
    이 스크립트는 traceback 으로 죽었고 **exit 1** 을 냈다 — 이 스크립트 자신의 계약이
    1 을 「불일치」로 정의하므로, 운영자에게 «검사가 죽었다» 가 «값이 틀렸다» 와 **같은
    답**으로 보였다(실측 2026-09-03, 시스템 python3). 런북은 `python3 scripts/…` 로
    적는데 exit 0 은 venv 인터프리터에서만 났다.

    그래서 이제 로드 실패는 **판정 불가(2)** 로 내려간다.

    ⚠️ **파일 경로로 읽던 것을 모듈 import 로 바꿨다 (2026-09-05).** 형제의 알맹이가
    패키지로 갔고 `scripts/` 쪽에는 진입점만 남았다. 경로로 읽으면 그 진입점을 읽어
    ``AttributeError: module 'check_auth_mode_pairing' has no attribute
    'read_env_text'`` 가 난다 — **실측으로 이 셋을 빨갛게 만들었다**(2026-09-05):
    ``test_it_reads_an_export_prefixed_shell_launcher`` ·
    ``test_a_disagreeing_pair_exits_one_as_a_subprocess`` ·
    ``test_running_it_as_a_subprocess_produces_a_verdict``.

    모듈에게 물으면 그 파서가 어디로 옮겨가든 따라온다. 그리고 이제 **휠이 그것을
    나르므로** 이 레인을 설치해 쓰는 소비자에게도 같은 파서가 간다.
    """
    try:
        return importlib.import_module(_SIBLING_MODULE)
    except Exception as exc:  # noqa: BLE001 — 원인을 그대로 실어 2 로 내린다
        raise Undetermined(
            f'형제 파서({_SIBLING_MODULE})를 불러오지 못했다 — '
            f'{type(exc).__name__}: {exc}'
        ) from exc


def read_env_text(text: str) -> dict:
    """형제 파서로 env 텍스트를 읽는다. **지연 로드**다.

    모듈 레벨에서 형제를 불러오면 그 실패가 스크립트 자체를 죽인다(위 참조).
    """
    return _load_sibling().read_env_text(text)


def read_env_file(path: Path) -> dict:
    """경로 기반 env/셸 런처를 형제 파서로 읽는다 (``export`` 접두 포함)."""
    return read_env_text(path.read_text(encoding='utf-8-sig'))


def contract_provider_id() -> 'str | None':
    """계약 SSOT 값. 계약 패키지를 못 읽으면 ``None`` — 판정 불가다."""
    sys.path.insert(0, str(_SRC_ROOT))
    try:
        from fcc_test_contracts.headless.api_contracts import (
            DEFAULT_PROVIDER_METADATA,
        )
    except Exception:
        return None
    finally:
        sys.path.remove(str(_SRC_ROOT))
    value = DEFAULT_PROVIDER_METADATA.get('provider_id')
    return str(value) if value else None


@dataclass(frozen=True)
class Verdict:
    exit_code: int
    message: str


def judge(
    *,
    central: 'str | None',
    node: 'str | None',
    contract: 'str | None',
) -> Verdict:
    """세 값을 비교한다. 읽지 못한 값이 하나라도 있으면 **판정 불가**다."""
    missing = [
        name for name, value in (
            ('contract SSOT', contract), ('central', central), ('node', node),
        ) if not value
    ]
    if missing:
        return Verdict(
            EXIT_UNDETERMINED,
            f'{ENV_PROVIDER_ID}: cannot judge — unread: {", ".join(missing)}. '
            'This is not a pass. Supply the missing source and re-run.',
        )

    if central != contract:
        return Verdict(
            EXIT_MISMATCH,
            f'{ENV_PROVIDER_ID}: central={central!r} differs from the '
            f'contract SSOT {contract!r} (DEFAULT_PROVIDER_METADATA). Central '
            'and the provider registry must name the same provider code.',
        )

    if node != contract:
        hint = (
            ' The node value looks like a providers.id UUID — the runbook used '
            'to prescribe that, and its rationale is stale: the sync adapter now '
            'takes provider_uuid from platform readiness and only falls back to '
            'this value. Use the provider code.'
            if _UUID_RE.match(node) else ''
        )
        return Verdict(
            EXIT_MISMATCH,
            f'{ENV_PROVIDER_ID}: node={node!r} differs from central/contract '
            f'{contract!r}. Chamber result ingestion will reject every batch '
            'with "provider_id does not match central configuration" — a '
            'rejection that surfaces late and reads like an auth failure.'
            + hint,
        )

    return Verdict(
        EXIT_OK,
        f'{ENV_PROVIDER_ID}: contract, central and node all agree on '
        f'{contract!r}.',
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--central-env', type=Path,
        default=_REPO_ROOT / 'infra' / 'central' / 'central.env',
        help='중앙 컨테이너가 읽는 env 파일',
    )
    parser.add_argument(
        '--node-env', type=Path, default=None,
        help='측정 PC 런처(셸 스크립트도 된다 — export 접두를 읽는다)',
    )
    parser.add_argument(
        '--node-value', default=None,
        help='노드 값을 파일 대신 직접 준다(원격 PC 를 확인할 때)',
    )
    args = parser.parse_args(argv)

    def _read(path: 'Path | None') -> 'str | None':
        if path is None:
            return None
        try:
            return read_env_file(path).get(ENV_PROVIDER_ID)
        except OSError as exc:
            print(f'warning: cannot read {path}: {exc}', file=sys.stderr)
            return None

    central = _read(args.central_env)
    node = args.node_value or _read(args.node_env)

    verdict = judge(
        central=central, node=node, contract=contract_provider_id(),
    )
    stream = sys.stdout if verdict.exit_code == EXIT_OK else sys.stderr
    print(verdict.message, file=stream)
    return verdict.exit_code


def _undetermined(reason: str) -> int:
    print(
        f'{ENV_PROVIDER_ID}: 판정 불가 — {reason}\n'
        '  이것은 「불일치」가 아니다. 값이 맞는지 **확인하지 못했다**는 뜻이다.\n'
        '  계약 패키지가 있는 인터프리터로 다시 돌려라 (예: 이 저장소의 venv).',
        file=sys.stderr,
    )
    return EXIT_UNDETERMINED


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Undetermined as exc:
        raise SystemExit(_undetermined(str(exc))) from exc
