#!/usr/bin/env python3
"""배포 전 점검 — 백엔드 auth mode 와 SPA 로그인 전략이 짝인가.

⚠️ **이 둘은 반드시 함께 바뀐다.** 백엔드만 바꾸면 화면이 여전히 IdP 로 튕기고, 프론트만
바꾸면 로그인 요청이 401 로 돌아온다. 2026-08-22 까지 그 규칙은 세 곳에 주석으로만 있었다.

봉인(``tests/test_auth_mode_pairing.py``)은 **저장소의 기본값**이 짝인지 본다. 이 스크립트는
**운영자가 실제로 쓰는 값**을 본다 — 그 둘은 다른 질문이고, 사고는 후자에서 난다:
``central.env`` 는 gitignore 대상이라 어떤 봉인도 그 파일을 볼 수 없다.

판정은 :func:`application.common.auth_config.auth_mode_pairing_defect` **하나**를 쓴다 —
봉인과 같은 함수다. 사전점검이 자기 술어를 재조립하면 게이트가 통과시키는 배포를 막거나
그 반대가 되고, 그러면 사람들이 사전점검을 끈다(이 저장소가 이미 적은 실패 형태다).

사용::

    # 운영자 env 파일을 그대로 판정 (가장 흔한 사용)
    python3 scripts/check_auth_mode_pairing.py --env-file infra/central/central.env

    # 값 두 개를 직접
    python3 scripts/check_auth_mode_pairing.py --auth-mode local_jwt --web-auth-mode local

    # 실제로 떠 있는 배포에 물어본다 (SPA 가 받는 runtime-config 를 읽는다)
    python3 scripts/check_auth_mode_pairing.py --env-file infra/central/central.env \\
        --runtime-config-url http://10.206.34.233:8080/runtime-config.js

종료 코드: 짝이면 ``0``, 어긋나면 ``1``, 판정할 값이 없으면 ``2``.
⚠️ **판정 불가와 통과를 같은 코드로 만들지 않는다** — 값을 못 읽었는데 0 을 돌려주면
이 점검은 아무것도 하지 않으면서 초록으로 보인다.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / 'src') not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / 'src'))

# ⚠️ **여기서 죽으면 「검사가 죽었다」가 「설정이 어긋났다」로 보고된다** (2026-09-03).
# 계약 패키지가 없는 인터프리터에서 이 import 가 ``ModuleNotFoundError`` 를 던지면
# 스크립트가 traceback 으로 죽고 **exit 1** 을 냈다. 그런데 1 은 이 스크립트 자신의
# 계약에서 「불일치」이고, ``check_deployment_drift.py::judge_auth_pairing`` 이 그것을
# 그대로 ``DRIFT — auth mode 와 로그인 전략이 어긋난다`` 로 옮긴다.
#
# 실측 2026-09-03: 시스템 python3 로 돌린 드리프트 게이트가 auth-pair 를 DRIFT 로
# 보고했지만, 계약 패키지가 있는 인터프리터로 돌리면 같은 env 가 ``coherent`` 로
# exit 0 이었다. **어긋난 설정이 없는데 어긋났다고 말하고 있었고**, 그 상태에서는
# 진짜 불일치가 생겨도 구분되지 않는다.
#
# 형제 ``check_central_provider_id_pairing.py`` 와 같은 수리다 — 로드 실패는
# **판정 불가(2)** 이고, 게이트는 2 를 UNKNOWN 으로 옮긴다.
_CONTRACT_IMPORT_ERROR: 'Exception | None' = None
try:
    from fcc_test_contracts.common.auth_config import (  # noqa: E402
        WEB_AUTH_STRATEGIES,
        deployment_auth_defects,
        web_auth_strategy_for,
    )
except Exception as _exc:  # noqa: BLE001 — 원인을 실어 2 로 내린다
    _CONTRACT_IMPORT_ERROR = _exc
    WEB_AUTH_STRATEGIES = {}

    def deployment_auth_defects(*_a, **_k):  # type: ignore[misc]
        raise RuntimeError('contract package unavailable')

    def web_auth_strategy_for(*_a, **_k):  # type: ignore[misc]
        raise RuntimeError('contract package unavailable')

#: 한 배포가 **함께** 정해야 하는 값들.
#:
#: ⚠️ 첫 판은 앞의 둘만 봤고, 그래서 런북 §S0-L 이 지시하는 바로 그 설정에 ``exit 0`` 을
#: 냈다(적대 평가 실측). 나머지 셋이 빠지면 각각: headless 화면 전부 401 · SPA 가 부팅
#: 거부 · 그 둘의 조합. 하나만 고치고 다시 막히지 않도록 **함께** 묻는다.
ENV_PLATFORM_AUTH_MODE = 'FCC_PLATFORM_AUTH_MODE'
ENV_WEB_AUTH_MODE = 'WEB_AUTH_MODE'
ENV_HEADLESS_AUTH_MODE = 'FCC_HEADLESS_AUTH_MODE'
ENV_ALLOW_INSECURE = 'ALLOW_INSECURE_TRANSPORT'
ENV_PUBLIC_HOST = 'PUBLIC_HOST'
ENV_PLATFORM_SECRET = 'FCC_PLATFORM_LOCAL_JWT_SECRET'
ENV_HEADLESS_SECRET = 'FCC_HEADLESS_LOCAL_JWT_SECRET'

#: 브라우저가 실제로 받는 파일 안의 키. 템플릿이 ``authMode: '${WEB_AUTH_MODE}'`` 로
#: 만들므로, 이 값이 곧 *"SPA 가 무엇을 하기로 했는가"* 의 최종 답이다.
_RUNTIME_AUTH_MODE = re.compile(r"authMode\s*:\s*['\"]([^'\"]*)['\"]")

#: ``runtime-config.js`` 는 1KB 남짓이다. 그보다 훨씬 큰 응답은 그 파일이 아니다.
_MAX_RUNTIME_CONFIG_BYTES = 1_000_000

_EXIT_OK = 0
_EXIT_MISMATCH = 1
_EXIT_UNDETERMINED = 2


def read_env_text(text: str) -> dict:
    """``KEY=value`` 를 읽는다 — **docker compose 와 같은 답을 내도록**.

    ⚠️ dotenv 라이브러리를 쓰지 않는다: 운영자가 의존성 없이 돌릴 수 있어야 한다. 대신
    compose 와 갈라지는 네 가지를 명시적으로 맞춘다(적대 평가 실측 — 넷 다 이 스크립트가
    다른 답을 냈고, 그중 둘은 정상 파일에 **거짓 경보**를 냈다):

    1. **인라인 주석** — ``KEY=local # 메모`` 에서 compose 는 ``local`` 을 본다. 옛 판은
       ``'local # 메모'`` 를 계정 모드로 읽고 *"선언되지 않은 모드"* 라며 운영자에게
       파이썬 소스를 고치라고 했다. 따옴표 안의 ``#`` 는 값의 일부다.
    2. **BOM** — 중앙 PC 의 메모장이 붙인다. 첫 키가 통째로 안 보였다.
    3. **``KEY=``** — compose 는 *미설정*으로 보고 기본값을 쓴다. 빈 문자열로 읽으면
       "설정됐는데 빈 값" 이 되어 판정이 갈린다.
    4. **중복 키** — 마지막이 이긴다(compose 와 동일).
    """
    values: dict = {}
    text = text.lstrip('\ufeff')  # (2) BOM
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        if not sep:
            continue
        value = _unquote_env_value(value)
        if value == '':
            # (3) compose treats `KEY=` as unset; a later duplicate may still set it.
            values.pop(key.strip(), None)
            continue
        values[key.strip()] = value  # (4) last wins
    return values


def read_env_file(path: Path) -> dict:
    """Read a path-backed env file using the same parser as stdin input."""
    return read_env_text(path.read_text(encoding='utf-8-sig'))


def _unquote_env_value(raw: str) -> str:
    """따옴표를 벗기고, 따옴표 **밖**의 인라인 주석을 버린다.

    ⚠️ 순서가 중요하다. 주석을 먼저 자르면 ``"lo#cal"`` 이 잘리고, 따옴표를 먼저 벗기면
    ``"local" # note`` 가 따옴표를 못 벗는다(끝 문자가 ``e`` 라서). 그래서 **여는 따옴표가
    있으면 닫는 따옴표까지가 값**이고 그 뒤는 통째로 버린다.
    """
    value = raw.strip()
    if value[:1] in ('"', "'"):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value[1:]
    # unquoted: ` #` onward is a comment, matching compose's own rule.
    cut = value.find(' #')
    if cut != -1:
        value = value[:cut]
    return value.strip()


def fetch_runtime_auth_mode(url: str, *, timeout: float = 5.0) -> 'str | None':
    """떠 있는 배포의 ``runtime-config.js`` 에서 ``authMode`` 를 읽는다.

    ⚠️ 이것이 **관측**이고 위의 env 는 **기대**다. 둘이 다르면 그것은 컨테이너를 다시
    빌드/기동하지 않았다는 뜻이다 — ``runtime-config.js`` 는 컨테이너 기동 시 env 에서
    생성되므로 ``central.env`` 만 고치고 재기동하지 않으면 화면은 옛 전략으로 남는다.
    """
    from urllib.parse import urlparse
    from urllib.request import urlopen  # 지연 import — 오프라인 사용을 막지 않는다

    # ⚠️ 스킴을 제한한다. `file://` 을 허용하면 이 점검이 로컬 파일을 읽고 **배포를
    # 확인했다고 답한다** — 관측이 아니라 자기 자신을 읽는 것이다(적대 평가 실측).
    scheme = urlparse(url).scheme.lower()
    if scheme not in {'http', 'https'}:
        raise ValueError(
            f'--runtime-config-url must be http or https, got {scheme!r}. '
            'This flag exists to observe a RUNNING deployment.'
        )
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 — 스킴 제한됨
        # 상한을 둔다: 이 파일은 1KB 남짓이고, 잘못된 URL 이 이 프로세스를 메모리로
        # 채우게 둘 이유가 없다.
        body = response.read(_MAX_RUNTIME_CONFIG_BYTES).decode('utf-8', 'replace')
    match = _RUNTIME_AUTH_MODE.search(body)
    return match.group(1) if match else None


def main(argv=None) -> int:
    if _CONTRACT_IMPORT_ERROR is not None:
        print(
            'auth pairing: 판정 불가 — 계약 패키지를 불러오지 못했다 '
            f'({type(_CONTRACT_IMPORT_ERROR).__name__}: {_CONTRACT_IMPORT_ERROR})\n'
            '  이것은 「어긋났다」가 아니다. 짝이 맞는지 **확인하지 못했다**는 뜻이다.\n'
            '  계약 패키지가 있는 인터프리터로 다시 돌려라 (예: 이 저장소의 venv).',
            file=sys.stderr,
        )
        return _EXIT_UNDETERMINED
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument(
        '--env-stdin', action='store_true',
        help='read the ephemeral pairing env from stdin without creating a file',
    )
    parser.add_argument('--auth-mode')
    parser.add_argument('--web-auth-mode')
    parser.add_argument('--runtime-config-url')
    args = parser.parse_args(argv)

    auth_mode = args.auth_mode
    web_auth_mode = args.web_auth_mode
    headless_auth_mode = None
    allow_insecure = None
    public_host = None
    local_jwt_secrets = None
    if args.env_file is not None or args.env_stdin:
        if args.env_file is not None and args.env_stdin:
            parser.error('--env-file and --env-stdin are mutually exclusive')
        if args.env_stdin:
            env = read_env_text(sys.stdin.read())
        else:
            assert args.env_file is not None
            if not args.env_file.exists():
                print(f'FAIL: {args.env_file} does not exist', file=sys.stderr)
                return _EXIT_UNDETERMINED
            env = read_env_file(args.env_file)
        auth_mode = auth_mode or env.get(ENV_PLATFORM_AUTH_MODE)
        web_auth_mode = web_auth_mode or env.get(ENV_WEB_AUTH_MODE)
        headless_auth_mode = env.get(ENV_HEADLESS_AUTH_MODE)
        allow_insecure = env.get(ENV_ALLOW_INSECURE)
        public_host = env.get(ENV_PUBLIC_HOST)
        platform_secret = env.get(ENV_PLATFORM_SECRET)
        headless_secret = env.get(ENV_HEADLESS_SECRET)
        if platform_secret is not None or headless_secret is not None:
            local_jwt_secrets = (platform_secret or '', headless_secret or '')

    if not auth_mode:
        print(
            f'UNDETERMINED: {ENV_PLATFORM_AUTH_MODE} is not set. Pass --auth-mode or an '
            '--env-file that declares it.\n'
            '⚠️ Not answering is not the same as passing — this exits 2, not 0.',
            file=sys.stderr,
        )
        return _EXIT_UNDETERMINED
    if not web_auth_mode:
        # ⚠️ compose 와 엔트로피포인트 모두 이 값을 defaulting 하므로 "미설정" 은 곧
        # "기본값으로 돈다" 이다. 그 사실을 말하고, 기본값으로 판정하지 않는다 —
        # 어느 기본값이 이길지는 compose 가 그 변수를 넘기는지에 달렸고, 그 구분을
        # 놓쳐서 ALLOW_INSECURE_TRANSPORT 사고가 났다.
        print(
            f'UNDETERMINED: {ENV_WEB_AUTH_MODE} is not set, so the SPA runs whatever '
            'default wins (compose or the container entrypoint). Set it explicitly — '
            'the whole point of this pair is that neither side may be implicit.',
            file=sys.stderr,
        )
        return _EXIT_UNDETERMINED

    defects = deployment_auth_defects(
        platform_auth_mode=auth_mode,
        web_auth_mode=web_auth_mode,
        headless_auth_mode=headless_auth_mode,
        local_jwt_secrets=local_jwt_secrets,
        insecure_transport_allowed=allow_insecure,
        public_host=public_host,
    )
    if defects:
        for defect in defects:
            print(f'FAIL: {defect}', file=sys.stderr)
        expected = web_auth_strategy_for(auth_mode)
        # ⚠️ `n/a` 는 SPA 의 enum 에 없는 값이다. 첫 판은 그것을 "고치는 방법" 으로
        # 출력했고, 따르면 런타임 설정 검증이 던져 **화면이 통째로 안 뜬다**.
        if expected in WEB_AUTH_STRATEGIES:
            print(f'  fix: {ENV_WEB_AUTH_MODE}={expected} · '
                  f'{ENV_HEADLESS_AUTH_MODE}={auth_mode}', file=sys.stderr)
        return _EXIT_MISMATCH

    unasked = [
        name for name, value in (
            (ENV_HEADLESS_AUTH_MODE, headless_auth_mode),
            (ENV_ALLOW_INSECURE, allow_insecure),
            # ⚠️ 첫 판은 이 축을 빠뜨려, 값이 없으면 전송 판정을 **건너뛰고 통과**시켰다 —
            # 바로 위 주석이 하지 말라고 적은 그것이다(적대 평가 2라운드 M-4).
            (ENV_PUBLIC_HOST, public_host),
        ) if value is None
    ]
    if unasked:
        # ⚠️ 묻지 못한 축을 통과로 접지 않는다. 이 스크립트가 처음 거절당한 이유가
        # 정확히 그것이었다 — 두 축만 보고 나머지 셋에 대해 초록을 냈다.
        print(
            'UNDETERMINED: the pair is fine, but these were not declared so they '
            f'could not be judged: {", ".join(unasked)}. On a plaintext deployment '
            'both matter — see runbook §S0-L.',
            file=sys.stderr,
        )
        return _EXIT_UNDETERMINED

    print(f'OK: {ENV_PLATFORM_AUTH_MODE}={auth_mode} · {ENV_WEB_AUTH_MODE}='
          f'{web_auth_mode} · {ENV_HEADLESS_AUTH_MODE}={headless_auth_mode} · '
          f'{ENV_ALLOW_INSECURE}={allow_insecure} · {ENV_PUBLIC_HOST}={public_host} '
          'are coherent')
    if web_auth_strategy_for(auth_mode) not in WEB_AUTH_STRATEGIES:
        print(
            f'  note: {auth_mode!r} has no SPA login screen, so {ENV_WEB_AUTH_MODE} '
            'places no constraint here. That is not an endorsement of the mode — '
            'trusted_headers in particular needs a gateway that sets X-FCC-Subject, '
            'and this stack\'s nginx does not.',
            file=sys.stderr,
        )

    if args.runtime_config_url:
        try:
            served = fetch_runtime_auth_mode(args.runtime_config_url)
        except Exception as exc:  # noqa: BLE001 — 운영자에게 사유를 그대로 보여준다
            print(
                f'UNDETERMINED: could not read {args.runtime_config_url} ({exc}). '
                'The configured pair is fine; what the deployment SERVES is unverified.',
                file=sys.stderr,
            )
            return _EXIT_UNDETERMINED
        if served is None:
            print(
                f'UNDETERMINED: {args.runtime_config_url} carries no authMode. '
                'Is that really the SPA runtime config?',
                file=sys.stderr,
            )
            return _EXIT_UNDETERMINED
        if served.strip().lower() != str(web_auth_mode).strip().lower():
            print(
                f'FAIL: the deployment SERVES authMode={served!r} but the env says '
                f'{web_auth_mode!r}.\n'
                '  ⚠️ runtime-config.js is generated when the web container starts, so '
                'editing central.env is not enough — rebuild/restart the web service.',
                file=sys.stderr,
            )
            return _EXIT_MISMATCH
        print(f'OK: the running deployment serves authMode={served}')

    return _EXIT_OK


if __name__ == '__main__':
    raise SystemExit(main())
