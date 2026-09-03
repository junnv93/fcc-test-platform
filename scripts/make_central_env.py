#!/usr/bin/env python3
"""`infra/central/central.env` 를 example 에서 만든다 — 시크릿은 생성하고, 나머지는 **대조한다.**

## 왜 이 스크립트가 있는가

최초 구축 문서(`infra/central/ONPREM_DEPLOYMENT.md` §2)가 *"example 을 복사하고 이
값들을 바꿔라"* 라고 지시한다. 손으로 하면 **바꾸지 않은 값이 조용히 남는다** — 그리고
데모 시크릿은 **동작한다.** 스택은 정상 기동하고, 운영 시크릿이 저장소에 공개된 값인
상태로 돈다. 그 실패는 **아무 게이트도 붉히지 않는다.**

## ⚠️ 값을 채우는 것보다 **대조하는 것**이 이 스크립트의 핵심이다

실측 2026-09-03 (형제 레인): 챔버 env 생성기가 provider id 를 **접두사 없는 짧은 값**
으로 갖고 있었고, **값이 있으면 옳은지 안 보고 그대로 날랐다.** 계약 SSOT 는
`fcc-unlicensed-conducted` 이고, 그대로 붙었으면 heartbeat 가 404 로 막히면서 증상은
「노드가 안 뜬다」였을 것이다. 같은 날 두 번째로 챔버 client id 가 같은 형태로 틀렸다.

⚠️ **틀린 값을 여기 대입 형태(`KEY=값`)로 적지 않는다** — 이 저장소의
`tests/test_central_provider_id_pairing.py` 가 `scripts/`·`infra/`·`docs/operations/`
아래의 그 형태를 전수로 세어 계약 SSOT 와 대조한다. 운영자가 복사하는 모양이기
때문이다. 실측: 이 파일의 초판이 그 게이트에 **실제로 걸렸다.**

> **생성기가 값을 나르기만 하면 그것은 두 번째 SSOT 다.**

그래서 이 스크립트는 세 가지를 **파일을 쓰기 전에** 한다:

    ① 계약 SSOT 대조     provider_id 가 fcc_test_contracts 의 값과 같은가
    ② 데모 값 잔류 검사   example 의 데모 시크릿이 하나라도 남았는가
    ③ 형식 검사          PUBLIC_HOST 가 IP 인가, CLIENT_RANGES 가 CIDR 인가

하나라도 틀리면 **아무것도 쓰지 않고 거부한다.** 절반만 쓴 env 는 「설정이 틀렸다」와
「생성이 중단됐다」를 같은 모양으로 만든다.

## ⚠️ 시크릿을 출력하지 않는다

생성된 시크릿은 파일에만 들어간다. 화면에는 **키 이름과 길이**만 나온다 — 운영 런북이
*"시크릿을 채팅·이슈·문서에 붙여 넣지 않는다"* 를 명시하고, 이 스크립트의 출력이 그
경로가 되면 안 된다.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import re
import secrets
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / 'infra' / 'central' / 'central.env.example'
TARGET = REPO_ROOT / 'infra' / 'central' / 'central.env'

#: 자동 생성할 시크릿과 그 데모 값. **데모 값을 여기 적는 이유**는 ②의 잔류 검사가
#: 그것을 알아야 하기 때문이다 — "바꿨는가" 는 "무엇에서 바꿨는가" 없이 물을 수 없다.
SECRETS: dict[str, str] = {
    'POSTGRES_PASSWORD': 'fcc-dev-password',
    'KEYCLOAK_ADMIN_PASSWORD': 'admin',
    'FCC_CHAMBER_CLIENT_SECRET': 'fcc-chamber-dev-secret',
    'FCC_CENTRAL_SESSION_CLIENT_SECRET': 'fcc-central-session-dev-secret',
    'FCC_STAGING_CLI_SECRET': 'fcc-staging-cli-dev-secret',
}

#: 운영자가 값을 대야 하는 것. 생성할 수 없다 — 이 기계의 사실이다.
OPERATOR_KEYS = ('PUBLIC_HOST',)

#: 계약 레인이 소유하는 값. **여기에 리터럴을 적지 않는다** — 아래에서 import 한다.
CONTRACT_KEYS = ('FCC_CENTRAL_PROVIDER_ID',)


def _contract_provider_id() -> str:
    """계약 SSOT 의 provider_id. ⚠️ 문자열로 적지 않고 **묻는다.**"""
    from fcc_test_contracts.headless.api_contracts import DEFAULT_PROVIDER_METADATA

    return str(DEFAULT_PROVIDER_METADATA['provider_id'])


def _read_value(text: str, key: str) -> str | None:
    match = re.search(rf'^{re.escape(key)}=(.*)$', text, re.MULTILINE)
    return match.group(1) if match else None


def _set_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf'^{re.escape(key)}=.*$', re.MULTILINE)
    if not pattern.search(text):
        raise KeyError(f'{key} 가 example 에 없다 — 키 이름이 바뀌었는지 확인하라')
    return pattern.sub(f'{key}={value}', text, count=1)


def _new_secret() -> str:
    """URL-safe 32바이트. compose 의 `${VAR}` 보간과 셸에 안전한 알파벳만 나온다."""
    return secrets.token_urlsafe(32)


def build(public_host: str, *, client_ranges: str | None) -> tuple[str, list[str]]:
    """새 env 본문과 **문제 목록**을 돌려준다. 문제가 있으면 호출자가 쓰지 않는다."""
    text = EXAMPLE.read_text(encoding='utf-8')
    problems: list[str] = []

    # ── ③ 형식 — 운영자 값
    try:
        ipaddress.ip_address(public_host)
    except ValueError:
        problems.append(
            f'PUBLIC_HOST 가 IP 가 아니다: {public_host!r} — 중앙 PC 의 고정 LAN IP 를 준다. '
            '호스트명을 쓰면 OIDC 토큰의 iss 가 기계마다 달라진다.'
        )
    text = _set_value(text, 'PUBLIC_HOST', public_host)

    if client_ranges is not None:
        for chunk in client_ranges.split(','):
            entry = chunk.strip()
            # ⚠️ **접두 길이를 명시적으로 요구한다** (실측 2026-09-03).
            # `ip_network('10.206.0.0', strict=False)` 는 **거부하지 않고 `/32`** 를 준다.
            # 즉 대역을 적으려다 `/16` 을 빠뜨리면 **주소 하나만 신뢰**하게 되는데,
            # 그 실패는 「대역을 적었다」와 출력에서 같은 모양이고 운영 중에는
            # 「어떤 챔버는 되고 어떤 챔버는 안 된다」로 나타난다.
            if '/' not in entry:
                problems.append(
                    f'FCC_CENTRAL_CLIENT_RANGES 항목에 접두 길이가 없다: {entry!r} — '
                    'CIDR 로 적어라(예: 10.206.0.0/16). 없으면 /32 로 읽혀 주소 하나만 신뢰한다.'
                )
                continue
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError as exc:
                problems.append(f'FCC_CENTRAL_CLIENT_RANGES 에 CIDR 이 아닌 값: {entry!r} ({exc})')
        text = _set_value(text, 'FCC_CENTRAL_CLIENT_RANGES', client_ranges)

    # ── 시크릿 생성
    for key in SECRETS:
        text = _set_value(text, key, _new_secret())

    # ── ① 계약 SSOT 대조. ⚠️ 값을 나르지 않고 **묻는다.**
    expected = _contract_provider_id()
    actual = _read_value(text, 'FCC_CENTRAL_PROVIDER_ID')
    if actual != expected:
        problems.append(
            f'FCC_CENTRAL_PROVIDER_ID 가 계약 SSOT 와 다르다: {actual!r} != {expected!r}. '
            '이 값이 틀리면 노드 heartbeat 가 404 로 막히고 증상은 「노드가 안 뜬다」로 보인다.'
        )

    # ── ② 데모 값 잔류 — 시크릿 **전부**를 다시 센다(생성한 것만이 아니다)
    for key, demo in SECRETS.items():
        if _read_value(text, key) == demo:
            problems.append(f'{key} 가 아직 데모 값이다 — 생성이 그 키에 닿지 않았다')

    for key in OPERATOR_KEYS + CONTRACT_KEYS:
        value = _read_value(text, key)
        if value is None or not value.strip():
            problems.append(f'{key} 가 비었다')

    return text, problems


def main(argv: 'list[str] | None' = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--public-host', required=True,
                        help='중앙 PC 의 고정 LAN IP (예: 10.206.34.233)')
    parser.add_argument('--client-ranges', default=None,
                        help='시험원·챔버 PC 사내망 CIDR (미지정이면 example 값 유지)')
    parser.add_argument('--force', action='store_true',
                        help='기존 central.env 를 덮어쓴다')
    args = parser.parse_args(argv)

    if not EXAMPLE.is_file():
        print(f'❌ example 이 없다: {EXAMPLE}', file=sys.stderr)
        return 2

    if TARGET.exists() and not args.force:
        # ⚠️ 덮어쓰면 **기존 시크릿이 사라진다.** 그 파일로 이미 볼륨이 만들어졌다면
        # 새 비밀번호는 그 볼륨과 맞지 않고, 그 실패는 「배포가 틀렸다」로 보인다.
        print(
            f'❌ 이미 있다: {TARGET}\n'
            '   덮어쓰면 기존 시크릿이 사라진다. 그 env 로 이미 볼륨이 만들어졌다면\n'
            '   새 비밀번호는 그 볼륨과 맞지 않고, 그 실패는 「배포가 틀렸다」로 보인다.\n'
            '   정말 새로 만들려면 --force, 그리고 옛 볼륨도 함께 버려야 한다.',
            file=sys.stderr,
        )
        return 2

    try:
        text, problems = build(args.public_host, client_ranges=args.client_ranges)
    except KeyError as exc:
        print(f'❌ {exc}', file=sys.stderr)
        return 2
    except ImportError as exc:
        print(
            f'❌ 계약 레인을 import 하지 못했다 ({exc}) — provider_id 를 대조할 수 없다.\n'
            '   대조 없이 쓰지 않는다. `pip install -e .` 후 다시 실행하라.',
            file=sys.stderr,
        )
        return 2

    if problems:
        print('❌ 거부 — 아무것도 쓰지 않았다:', file=sys.stderr)
        for problem in problems:
            print(f'   · {problem}', file=sys.stderr)
        return 1

    TARGET.write_text(text, encoding='utf-8')
    os.chmod(TARGET, 0o600)   # 시크릿 파일이다

    print(f'✅ 작성: {TARGET}  (권한 600)')
    print(f'   PUBLIC_HOST            {args.public_host}')
    print(f'   FCC_CENTRAL_PROVIDER_ID {_read_value(text, "FCC_CENTRAL_PROVIDER_ID")}  ← 계약 SSOT 와 일치')
    print(f'   FCC_CENTRAL_CLIENT_RANGES {_read_value(text, "FCC_CENTRAL_CLIENT_RANGES")}')
    print('   생성된 시크릿 (값은 출력하지 않는다):')
    for key in SECRETS:
        print(f'     · {key:38s} {len(_read_value(text, key) or "")}자')
    print()
    print('⚠️ 이 파일은 gitignore 대상이고 커밋되지 않는다. 백업은 운영자가 따로 보관한다.')
    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main())
