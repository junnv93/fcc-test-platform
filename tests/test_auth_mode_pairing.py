"""백엔드 auth mode ↔ SPA 로그인 전략의 짝이 **강제**된다 (2026-08-22).

⚠️ **이 규칙은 2026-08-22 까지 세 곳에 주석으로만 있었고 강제하는 것이 없었다.**

* ``infra/docker-compose.central.yml`` — *"MUST agree with FCC_PLATFORM_AUTH_MODE"*
* ``infra/central/runtime-config.central.js.template`` — *"⚠ Must match …"*
* 런북 S0-L — *"⚠️ 양쪽이 일치해야 한다"*

실측(2026-08-22): ``WEB_AUTH_MODE`` 는 ``tests/`` · ``scripts/`` · ``src/`` 어디에도
나오지 않았다. 어긋났을 때 운영자가 보는 것은 설정 오류가 아니라 *"로그인 화면이 자꾸
Keycloak 으로 튄다"* 또는 *"비밀번호가 맞는데 401"* 이다.

⚠️ **같은 파일이 같은 계열의 사고를 이미 기록하고 있다**: `ALLOW_INSECURE_TRANSPORT` 를
compose 가 컨테이너로 넘기지 않아 운영자가 `central.env` 에 적은 값이 아무 효과도 없었고,
그 진단이 *"live deployment 에서 실제 시간을 잃었다"*. 그것을 적어 둔 파일에서 바로 다음
칸이 같은 형태로 남아 있었다.

이 파일은 그 규칙을 **네 자리에서** 확인한다 — 정책 자체, compose 기본값, env 예시,
컨테이너 엔트리포인트 기본값 — 그리고 프론트 어휘와의 cross-language 등가성을 본다.
"""
from __future__ import annotations

import re
import sys
import unittest

# ⚠️ 2026-09-05 — 여기 있던 지역 `import yaml` + `except ImportError: skipTest` 를
#    최상단 import 로 올렸다. PyYAML 은 `[test]` 에 **선언된** 의존성이므로(PR #64)
#    그 부재는 「이 shard 에서는 검사하지 않는다」가 아니라 **환경 결함**이다.
#    ⚠️ 그리고 가드는 `test_supply_closure_axis.py` 의
#    `TestEveryUnguardedImportIsDeclared` 가 **원리적으로 볼 수 없는** 자리였다 —
#    그 축은 이름 그대로 *unguarded* import 만 대조한다. 가드를 걷어내는 것이
#    이 import 를 그 게이트의 관할로 넣는 유일한 방법이다.
#    (문구 "not installed in this shard" 도 낡았다: CI 는 `pip install -e '.[test]'`
#     단일 shard 이고 PyYAML 은 이제 거기 있다.)
import yaml
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_REPO_ROOT / 'src'), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fcc_test_contracts.common.auth_config import (  # noqa: E402
    LOCAL_DEV_HOSTNAMES,
    WEB_AUTH_STRATEGIES,
    WEB_AUTH_STRATEGY_NOT_APPLICABLE,
    HttpAuthConfig,
    deployment_auth_defects,
    AUTH_MODE_DISABLED,
    AUTH_MODE_LOCAL_JWT,
    AUTH_MODE_NONE,
    AUTH_MODE_OIDC_JWT,
    AUTH_MODE_TRUSTED_HEADERS,
    WEB_AUTH_STRATEGY_LOCAL,
    WEB_AUTH_STRATEGY_OIDC,
    auth_mode_pairing_defect,
    web_auth_strategy_for,
)
from fcc_test_contracts.common.tree_artifacts import resolve_repo_artifact  # noqa: E402

_COMPOSE = _REPO_ROOT / 'infra' / 'docker-compose.central.yml'
_ENV_EXAMPLE = _REPO_ROOT / 'infra' / 'central' / 'central.env.example'
_ENTRYPOINT = (
    _REPO_ROOT / 'infra' / 'central' / 'docker-entrypoint.d' / '30-runtime-config.sh'
)
_TEMPLATE = _REPO_ROOT / 'infra' / 'central' / 'runtime-config.central.js.template'
_RUNTIME_TS = _REPO_ROOT / 'apps' / 'web' / 'src' / 'config' / 'runtime.ts'

#: ``${VAR:-default}`` — compose interpolation and shell parameter expansion share it.
_DEFAULTED = r'^\$\{%s:-(.*)\}$'


def _compose_environment(case: unittest.TestCase, service: str) -> dict:
    """A service's ``environment`` as a mapping, whichever syntax compose used.

    ⚠️ **Not a regex over the file.** Adversarial review defeated the first version
    two ways: a ``${WEB_AUTH_MODE:-oidc}`` written inside a *prose comment* shadowed
    the real line (first match wins), and the map-vs-list form of ``environment:``
    made a legitimate refactor fail with a message naming the wrong cause. YAML is
    the thing compose reads; read that.
    """
    document = yaml.safe_load(_require(case, _COMPOSE))
    raw = document['services'][service].get('environment') or {}
    if isinstance(raw, dict):
        return {str(k): '' if v is None else str(v) for k, v in raw.items()}
    # list form: ``- KEY=value`` / ``- KEY`` (pass through from the host)
    mapping: dict = {}
    for item in raw:
        key, _sep, value = str(item).partition('=')
        mapping[key] = value
    return mapping


def _interpolated_default(case: unittest.TestCase, service: str, var: str) -> 'str | None':
    """The default compose would use for ``var`` when the host does not set it."""
    value = _compose_environment(case, service).get(var)
    if value is None:
        return None
    match = re.match(_DEFAULTED % re.escape(var), value.strip())
    return match.group(1) if match else value.strip()


def _require(case: unittest.TestCase, path: Path) -> str:
    """저장소 산출물을 요구하거나 **사유와 함께** skip 한다.

    ⚠️ 레인 추출 상자는 ``infra/`` 와 ``apps/`` 를 싣지 않는다. 상자 안에서 이 질문들은
    *답할 수 없는 것*이지 실패한 것이 아니다 — 조용히 통과시키지 않고 무엇을 확인하지
    못했는지 말한다.
    """
    if not path.exists():
        case.skipTest(
            f'NOT VERIFIED here: {path.name} is absent. This assertion is about the '
            'monorepo deployment artifacts, and a delivered lane box ships source '
            'without infra/ or apps/. It runs in the repository lane.'
        )
    return path.read_text(encoding='utf-8')


def _default_of(text: str, var: str) -> 'str | None':
    match = re.search(_DEFAULTED % re.escape(var), text)
    return match.group(1) if match else None


class TestThePolicyIsTotalAndExplicit(unittest.TestCase):

    _ALL_MODES = (
        AUTH_MODE_DISABLED, AUTH_MODE_NONE, AUTH_MODE_TRUSTED_HEADERS,
        AUTH_MODE_OIDC_JWT, AUTH_MODE_LOCAL_JWT,
    )

    #: ⚠️ **행마다 값을 고정한다.** 첫 판은 *"어떤 값이든 있으면 통과"* 였고, 그래서
    #: 세 행의 답을 바꿔도 봉인이 초록이었다(적대 평가 실측: T1/T2/T3 SURVIVED).
    #: 존재를 묻는 것과 답을 묻는 것은 다르다.
    _EXPECTED = {
        AUTH_MODE_LOCAL_JWT: WEB_AUTH_STRATEGY_LOCAL,
        AUTH_MODE_OIDC_JWT: WEB_AUTH_STRATEGY_OIDC,
        AUTH_MODE_TRUSTED_HEADERS: WEB_AUTH_STRATEGY_NOT_APPLICABLE,
        AUTH_MODE_DISABLED: WEB_AUTH_STRATEGY_NOT_APPLICABLE,
        AUTH_MODE_NONE: WEB_AUTH_STRATEGY_NOT_APPLICABLE,
    }

    def test_every_declared_auth_mode_has_the_pairing_it_should(self):
        """⚠️ 새 모드가 조용히 ``oidc`` 로 접히지 않고, 기존 행의 답도 고정된다."""
        self.assertEqual(set(self._EXPECTED), set(self._ALL_MODES))
        for mode, expected in self._EXPECTED.items():
            with self.subTest(mode=mode):
                self.assertEqual(web_auth_strategy_for(mode), expected)

    def test_the_modes_with_no_spa_login_place_no_constraint(self):
        """⚠️ 이 단언은 2026-08-22 에 **두 번** 바뀌었고, 두 번 다 적대 평가가 밀었다.

        (1) 첫 판은 셋을 ``oidc`` 로 적었고, 결함 메시지가 *"API 가 IdP 토큰을 기대하므로
        401"* 이라고 **거짓**을 말했다 — ``disabled``/``none`` 은 resolver 를 만들지 않아
        토큰을 기대하지 않고 ``trusted_headers`` 는 이 스택이 설정하지 않는 헤더를 본다.
        (2) 둘째 판은 ``n/a`` 로 고치면서 그것을 **결함**으로 만들었고, 그러자 그 모드로는
        게이트를 통과할 방법이 없어졌으며 고치는 방법으로 SPA enum 에 없는 값을 제시했다.

        옳은 답은 셋째다: **제약이 없다.** 어느 ``WEB_AUTH_MODE`` 도 틀리지 않는다.
        """
        for mode in (AUTH_MODE_DISABLED, AUTH_MODE_NONE, AUTH_MODE_TRUSTED_HEADERS):
            for strategy in (WEB_AUTH_STRATEGY_OIDC, WEB_AUTH_STRATEGY_LOCAL):
                with self.subTest(mode=mode, strategy=strategy):
                    self.assertIsNone(auth_mode_pairing_defect(mode, strategy))

    def test_the_not_applicable_token_is_never_a_configurable_value(self):
        """⚠️ SPA 의 enum 에 없는 값이다 — 설정으로 나가면 화면이 통째로 안 뜬다."""
        self.assertNotIn(WEB_AUTH_STRATEGY_NOT_APPLICABLE, WEB_AUTH_STRATEGIES)

    def test_an_unknown_mode_is_a_defect_not_a_default(self):
        self.assertIsNone(web_auth_strategy_for('mode_that_does_not_exist'))
        defect = auth_mode_pairing_defect('mode_that_does_not_exist', 'oidc')
        self.assertIsNotNone(defect)
        self.assertIn('no declared SPA pairing', defect)

    def test_the_two_modes_that_matter_pair_the_way_the_runbook_says(self):
        self.assertEqual(
            web_auth_strategy_for(AUTH_MODE_LOCAL_JWT), WEB_AUTH_STRATEGY_LOCAL,
        )
        self.assertEqual(
            web_auth_strategy_for(AUTH_MODE_OIDC_JWT), WEB_AUTH_STRATEGY_OIDC,
        )

    def test_a_mismatch_names_the_symptom_the_operator_will_actually_see(self):
        """⚠️ 증상에서 원인으로 거슬러 올라가는 데 드는 시간이 이 결함의 실제 비용이다."""
        self.assertIn(
            'redirect to the IdP',
            auth_mode_pairing_defect(AUTH_MODE_LOCAL_JWT, WEB_AUTH_STRATEGY_OIDC),
        )
        self.assertIn(
            '401',
            auth_mode_pairing_defect(AUTH_MODE_OIDC_JWT, WEB_AUTH_STRATEGY_LOCAL),
        )

    def test_the_predicate_is_total(self):
        for mode in (None, '', 0, [], object(), 'LOCAL_JWT', '  local_jwt  '):
            with self.subTest(mode=repr(mode)[:30]):
                self.assertIsInstance(auth_mode_pairing_defect(mode, 'oidc'), (str, type(None)))

    def test_case_and_whitespace_do_not_break_the_pairing(self):
        """env 파일의 값은 손으로 적히고 대소문자·공백이 섞인다."""
        for raw in ('local_jwt', 'LOCAL_JWT', ' Local_Jwt '):
            with self.subTest(raw=raw):
                self.assertIsNone(auth_mode_pairing_defect(raw, ' LOCAL '))


class TestTheShippedDefaultsArePaired(unittest.TestCase):
    """세 배포 산출물의 **기본값**이 서로 짝이다."""

    def test_compose_defaults_are_a_coherent_deployment(self):
        platform = _interpolated_default(self, 'platform-api', 'FCC_PLATFORM_AUTH_MODE')
        web = _interpolated_default(self, 'web', 'WEB_AUTH_MODE')
        headless = _interpolated_default(self, 'headless-api', 'FCC_HEADLESS_AUTH_MODE')
        insecure = _interpolated_default(self, 'web', 'ALLOW_INSECURE_TRANSPORT')
        for name, value in (('FCC_PLATFORM_AUTH_MODE', platform),
                            ('WEB_AUTH_MODE', web),
                            ('FCC_HEADLESS_AUTH_MODE', headless)):
            self.assertIsNotNone(value, f'compose must pass {name}')
        defects = deployment_auth_defects(
            platform_auth_mode=platform, web_auth_mode=web,
            headless_auth_mode=headless, insecure_transport_allowed=insecure,
        )
        self.assertEqual(defects, (), '; '.join(defects))

    def test_every_auth_field_reaches_the_container_that_reads_it(self):
        """⚠️ **선언과 전달은 다르다** — 이 웨이브의 가장 큰 발견이 여기다.

        런북 §S0-L 은 운영자에게 ``local_jwt`` 다섯 값과 부트스트랩 둘을 ``central.env`` 에
        적으라고 한다. compose 는 그 일곱 중 **하나도** 컨테이너로 넘기지 않았고,
        platform-api 는 ``local_jwt auth requires local_jwt_issuer`` 로 부팅을 거부했다
        (적대 평가 실측 2026-08-22). `--env-file` 은 compose **보간**을 먹이지 컨테이너
        환경이 아니다 — 같은 파일이 `ALLOW_INSECURE_TRANSPORT` 에 대해 이미 적어 둔 구분이다.

        ⚠️ 명단을 손으로 적지 않는다. ``HttpAuthConfig.env_keys`` 가 이미 그 집합을 알고
        있고, 손 명단은 여섯 번째 필드가 생기는 날 조용히 빠진다.
        """
        for service, prefix in (('platform-api', 'FCC_PLATFORM_'),
                                ('headless-api', 'FCC_HEADLESS_')):
            with self.subTest(service=service):
                passed = set(_compose_environment(self, service))
                # ⚠️ 명단은 **기본값이 없는 필드**다 — 파생이지 손 목록이 아니다.
                # 데이터클래스 기본값이 ``''`` 인 필드는 어떤 모드에서 필수이고 그 모드는
                # 값이 없으면 부팅을 거부한다. 반면 ``oidc_subject_claim='sub'`` 처럼
                # 동작하는 기본값이 있는 필드는 compose 가 넘길 이유가 없다.
                # ⚠️ **전량이다.** 첫 판은 *"기본값이 없는 필드"* 로 좁혔고 그 근거는
                # 양방향으로 거짓이었다(적대 평가 2라운드): TTL 은 빈 값이어도 부팅을
                # 거부하지 않고, 반대로 `oidc_subject_claim` 은 기본값이 있는데도 Azure
                # Entra 에서 **필수**라 `'oid'` 를 넣을 방법이 없으면 부팅이 거부된다 —
                # CLAUDE.md 가 Entra 를 이 축의 목적지로 적고 있는데 말이다.
                # "어느 필드가 어느 모드에서 필수인가" 는 여기서 답할 질문이 아니다.
                # 배포가 **설정할 수 있어야** 하는 것은 그 표면이 읽는 필드 전부다.
                required = set(HttpAuthConfig.env_keys(prefix).values())
                self.assertTrue(required, 'the derivation must not be empty')
                missing = sorted(required - passed)
                self.assertEqual(
                    missing, [],
                    f'{service} reads these via HttpAuthConfig but compose never '
                    f'passes them, so the operator setting them has no effect: {missing}',
                )

    def test_the_bootstrap_admin_variables_reach_platform_api(self):
        """⚠️ 이 둘이 없으면 users 테이블이 비어 **아무도 로그인할 수 없다**.

        ⚠️ 이 둘만은 **손 목록**이다 — ``HttpAuthConfig`` 의 필드가 아니라 합성 루트가
        직접 읽는 env 이기 때문이다(``platform_api_composition.ENV_BOOTSTRAP_ADMIN_*``).
        그 사실을 숨기지 않으려고 이름을 그 모듈에서 가져온다: 상수가 바뀌면 여기가 따라온다.
        """
        try:
            import fcc_test_platform.api_composition as composition
        except Exception as exc:  # noqa: BLE001
            self.skipTest(
                f'NOT VERIFIED here: platform_api_composition is not importable '
                f'({exc}). It wires across lanes; it runs in the repository lane.'
            )
        passed = set(_compose_environment(self, 'platform-api'))
        for var in (composition.ENV_BOOTSTRAP_ADMIN_EMAIL,
                    composition.ENV_BOOTSTRAP_ADMIN_PASSWORD):
            with self.subTest(var=var):
                self.assertIn(var, passed)

    def test_the_web_service_passes_every_variable_its_template_substitutes(self):
        """⚠️ **H-1**: 이 웨이브가 근거로 든 사고의 변수 자체가 봉인 밖에 있었다.

        ``ALLOW_INSECURE_TRANSPORT`` 를 web 서비스에서 지워도 봉인이 통과했다(적대 평가
        2라운드 실측) — 즉 2026-08-21 사고를 인용하면서 그 사고의 재발을 막지 못했다.

        명단은 **템플릿에서 파생**한다. envsubst 가 치환하는 placeholder 집합이 곧 web
        컨테이너가 받아야 하는 값의 집합이고, 그것은 기계가 읽을 수 있다 — 손 목록 둘이
        각자 낡아가는 것이 이 결함의 형태였다.
        """
        template = _require(self, _TEMPLATE)
        placeholders = set(re.findall(r'\$\{([A-Z0-9_]+)\}', template))
        self.assertTrue(placeholders, 'the template must substitute something')
        passed = set(_compose_environment(self, 'web'))
        missing = sorted(placeholders - passed)
        self.assertEqual(
            missing, [],
            'the runtime-config template substitutes these, but compose never passes '
            f'them into the web container, so the entrypoint default silently wins: '
            f'{missing}',
        )

    def test_the_entrypoint_substitutes_exactly_what_the_template_needs(self):
        """⚠️ envsubst 의 allowlist 가 좁으면 placeholder 가 **리터럴로** 브라우저에 간다."""
        template = _require(self, _TEMPLATE)
        entry = _require(self, _ENTRYPOINT)
        placeholders = set(re.findall(r'\$\{([A-Z0-9_]+)\}', template))
        match = re.search(r"envsubst\s+'([^']*)'", entry)
        self.assertIsNotNone(match, 'the entrypoint must restrict envsubst')
        allowed = set(re.findall(r'\$\{([A-Z0-9_]+)\}', match.group(1)))
        self.assertEqual(
            placeholders, allowed,
            'the template placeholders and the envsubst allowlist must be the same '
            'set — a narrower allowlist ships "${VAR}" verbatim to the browser, a '
            'wider one substitutes things the template never asked for',
        )

    def test_the_env_example_defaults_are_the_same_pair(self):
        """⚠️ 운영자가 실제로 복사하는 파일이다 — compose 기본값과 갈라지면 안 된다."""
        env = _require(self, _ENV_EXAMPLE)
        for service, var in (('platform-api', 'FCC_PLATFORM_AUTH_MODE'),
                             ('web', 'WEB_AUTH_MODE'),
                             ('headless-api', 'FCC_HEADLESS_AUTH_MODE')):
            with self.subTest(var=var):
                matches = re.findall(rf'^{var}=(.*)$', env, re.MULTILINE)
                self.assertTrue(matches, f'{var} must be present in central.env.example')
                self.assertEqual(
                    matches[-1].strip(),  # compose is last-wins; so are we
                    _interpolated_default(self, service, var),
                    f'{var} differs between central.env.example and the compose default',
                )

    def test_the_entrypoint_default_matches_the_compose_default(self):
        """⚠️ 이 기본값은 compose 가 값을 넘기지 **않을 때** 발효한다.

        `ALLOW_INSECURE_TRANSPORT` 사고가 정확히 그 자리였다 — compose 가 안 넘기면
        엔트리포인트의 자기 기본값이 조용히 이긴다.
        """
        entry = _require(self, _ENTRYPOINT)
        match = re.search(r':\s*"\$\{WEB_AUTH_MODE:=([^}]*)\}"', entry)
        self.assertIsNotNone(match, 'the entrypoint must default WEB_AUTH_MODE')
        self.assertEqual(
            match.group(1), _interpolated_default(self, 'web', 'WEB_AUTH_MODE'),
        )

    def test_compose_actually_passes_web_auth_mode_into_the_container(self):
        self.assertIn(
            'WEB_AUTH_MODE', _compose_environment(self, 'web'),
            'the web service must pass WEB_AUTH_MODE into the container, not merely '
            'let the entrypoint fall back to its own default',
        )

    def test_the_template_consumes_the_same_variable(self):
        template = _require(self, _TEMPLATE)
        entry = _require(self, _ENTRYPOINT)
        self.assertIn('${WEB_AUTH_MODE}', template)
        self.assertIn(
            'WEB_AUTH_MODE', entry,
            'envsubst must be allowed to substitute it, or the template ships the '
            'literal placeholder to the browser',
        )


class TestTheFrontendVocabularyMatches(unittest.TestCase):
    """cross-language SSOT — 프론트 Zod enum 과 정책의 전략 토큰 집합이 같다."""

    def test_the_zod_enum_is_exactly_the_policy_vocabulary(self):
        text = _require(self, _RUNTIME_TS)
        match = re.search(r"authMode:\s*z\.enum\(\[([^\]]*)\]\)", text)
        self.assertIsNotNone(match, 'runtime.ts must declare authMode as a z.enum')
        declared = set(re.findall(r"'([^']+)'", match.group(1)))
        self.assertEqual(
            declared, {WEB_AUTH_STRATEGY_OIDC, WEB_AUTH_STRATEGY_LOCAL},
            'the SPA strategy vocabulary and the backend pairing table disagree — '
            'one of them will silently accept a value the other rejects',
        )

    def test_the_local_dev_hostnames_are_exactly_the_spas(self):
        """⚠️ **M-5**: 재판관은 SPA 다 — 그 집합을 재표현하면 예측이 뒤집힌다.

        첫 판은 WHATWG *potentially trustworthy* 로 적었고, 그 집합은 SPA 의
        ``isLocalDevHostname`` 과 **양방향으로** 다르다: ``::1``·``127.0.0.5`` 는 WHATWG 가
        신뢰하지만 SPA 는 거부하고, ``hostmachine`` 은 그 반대다. 그래서 사전점검이 화면이
        뜨지 않을 설정에 초록을, 뜰 설정에 빨강을 냈다(적대 평가 2라운드 실측).

        ⚠️ 그리고 값을 고쳤을 뿐 **집합을 고정하지는 않았다** — ``::1`` 을 다시 넣어도
        봉인이 통과했다. 그것이 이 테스트가 존재하는 이유다.
        """
        text = _require(self, _RUNTIME_TS)
        body = re.search(
            r'function isLocalDevHostname\([^)]*\)[^{]*\{(.*?)\n\}', text, re.S,
        )
        self.assertIsNotNone(body, 'runtime.ts must declare isLocalDevHostname')
        declared = set(re.findall(r"hostname === '([^']+)'", body.group(1)))
        self.assertTrue(declared, 'the extraction must find something')
        self.assertEqual(
            declared, set(LOCAL_DEV_HOSTNAMES),
            'the preflight predicts what the SPA will do, so its hostname set must '
            'be the SPA\'s — not a standard that merely resembles it',
        )

    def test_the_zod_default_is_the_pairing_of_the_compose_default(self):
        text = _require(self, _RUNTIME_TS)
        match = re.search(r"authMode:\s*z\.enum\([^)]*\)\.default\('([^']+)'\)", text)
        self.assertIsNotNone(match, 'runtime.ts must default authMode')
        platform = _interpolated_default(self, 'platform-api', 'FCC_PLATFORM_AUTH_MODE')
        self.assertIsNone(
            auth_mode_pairing_defect(platform, match.group(1)),
            "the SPA's own default must pair with the deployment's default backend mode",
        )


class TestTheGuardWouldNoticeAMismatch(unittest.TestCase):
    """⚠️ 비-공허성 — 위 검사들이 실제로 어긋남을 잡는지 합성 사례로 확인한다.

    이 저장소가 값을 치른 형태: 봉인이 자기가 막는다고 적은 것을 실제로는 막지 못하는데
    출력이 통과와 같다.
    """

    def test_a_planted_compose_mismatch_is_detected(self):
        text = _require(self, _COMPOSE)
        planted = text.replace(
            '${FCC_PLATFORM_AUTH_MODE:-oidc_jwt}',
            '${FCC_PLATFORM_AUTH_MODE:-local_jwt}',
        )
        self.assertNotEqual(planted, text, 'the substitution must actually apply')
        self.assertIsNotNone(
            auth_mode_pairing_defect(
                _default_of(planted, 'FCC_PLATFORM_AUTH_MODE'),
                _default_of(planted, 'WEB_AUTH_MODE'),
            ),
            'switching only the backend default must be reported as a defect',
        )

    def test_a_planted_frontend_only_switch_is_detected(self):
        text = _require(self, _COMPOSE)
        planted = text.replace('${WEB_AUTH_MODE:-oidc}', '${WEB_AUTH_MODE:-local}')
        self.assertNotEqual(planted, text)
        self.assertIsNotNone(
            auth_mode_pairing_defect(
                _default_of(planted, 'FCC_PLATFORM_AUTH_MODE'),
                _default_of(planted, 'WEB_AUTH_MODE'),
            ),
            'switching only the SPA default must be reported as a defect',
        )

    def test_switching_both_together_is_accepted(self):
        """⚠️ 그리고 정당한 전환은 막지 않는다 — 그것이 이 축의 목적이다."""
        self.assertIsNone(auth_mode_pairing_defect('local_jwt', 'local'))


#: 평문 LAN 로컬 로그인 배포가 실제로 정합인 설정 — 런북 §S0-L 이 지시해야 하는 것.
#: ⚠️ **첫 판은 모드 다섯만 담았고, 그것은 「짝은 맞지만 부팅은 못 하는」 env 였다.**
#:
#: `local_jwt` 는 `issuer`·`audience`·`secret`(HS256 최소 길이) 없이 부팅을 거부한다
#: (`LocalJwtConfig.validate`). 그런데 이 픽스처를 「통과」라고 부르는 검사들이 있었고,
#: 그래서 사전점검 도구가 그 상태에 `OK` 를 내도 아무도 몰랐다 — 실측 2026-09-04
#: (중앙 PC 최초 구축): 운영자가 `OK` 를 받은 뒤에 `LOCAL_JWT_ISSUER` 가 양쪽 다 빈 것을
#: 따로 발견했고, 그대로 재기동했으면 부팅 거부였다.
#:
#: 즉 **「정합」의 정의가 좁았다.** 모드가 맞는 것과 그 배포가 뜨는 것은 다른 축이고,
#: 운영자에게는 후자가 질문이다. 값을 여기 담는 것이 그 정정이다.
_COHERENT_LOCAL = (
    'FCC_PLATFORM_AUTH_MODE=local_jwt\n'
    'WEB_AUTH_MODE=local\n'
    'FCC_HEADLESS_AUTH_MODE=local_jwt\n'
    'ALLOW_INSECURE_TRANSPORT=true\n'
    'PUBLIC_HOST=10.206.34.233\n'
    # 두 표면은 **같은 토큰**을 검증하므로 값이 같아야 한다.
    f'FCC_PLATFORM_LOCAL_JWT_SECRET={"s" * 40}\n'
    f'FCC_HEADLESS_LOCAL_JWT_SECRET={"s" * 40}\n'
    'FCC_PLATFORM_LOCAL_JWT_ISSUER=http://10.206.34.233:8080/local\n'
    'FCC_HEADLESS_LOCAL_JWT_ISSUER=http://10.206.34.233:8080/local\n'
    'FCC_PLATFORM_LOCAL_JWT_AUDIENCE=fcc-platform\n'
    'FCC_HEADLESS_LOCAL_JWT_AUDIENCE=fcc-platform\n'
)

#: ⚠️ 위 픽스처에서 **부팅 필수값 하나만** 뺀 것. 이것이 「짝은 맞는데 안 뜬다」 상태이고,
#: 사전점검이 그것을 `OK` 로 읽던 자리다.
_COHERENT_PAIR_BUT_UNBOOTABLE = _COHERENT_LOCAL.replace(
    'FCC_PLATFORM_LOCAL_JWT_ISSUER=http://10.206.34.233:8080/local\n', '')


class TestTheOperatorPreflight(unittest.TestCase):
    """봉인은 **저장소 기본값**을, 사전점검은 **운영자가 실제로 쓰는 값**을 본다.

    ⚠️ 그 둘은 다른 질문이고 사고는 후자에서 난다 — ``central.env`` 는 gitignore 대상이라
    어떤 봉인도 그 파일을 볼 수 없다.
    """

    _SCRIPT = resolve_repo_artifact(__file__, 'scripts/check_auth_mode_pairing.py')

    def setUp(self):
        if not self._SCRIPT.is_file() and (_REPO_ROOT / '.extraction-layout.json').is_file():
            self.skipTest(
                'NOT VERIFIED here: provider-owned auth preflight is not shipped '
                'in this delivered lane; repository execution remains required.'
            )

    def _run(self, *args, input=None):
        import subprocess

        return subprocess.run(  # noqa: S603
            [sys.executable, str(self._SCRIPT), *args],
            capture_output=True, text=True, input=input, cwd=str(_REPO_ROOT),
        )

    def _env(self, body: str):
        import tempfile

        handle = tempfile.NamedTemporaryFile(
            'w', suffix='.env', delete=False, encoding='utf-8',
        )
        handle.write(body)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_a_paired_env_passes(self):
        result = self._run('--env-file', self._env(_COHERENT_LOCAL))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_paired_env_can_be_streamed_without_an_env_file(self):
        result = self._run('--env-stdin', input=_COHERENT_LOCAL)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_jwt_values_that_were_never_declared_are_not_a_pass(self):
        """⚠️ **이 결함의 재현.** 모드 짝은 맞는데 `local_jwt` 필수값이 없다.

        실측 2026-09-04(중앙 PC): 이 상태에서 도구가 **`OK`** 를 냈다. 운영자는 그 뒤에
        `LOCAL_JWT_ISSUER` 가 비어 있는 것을 따로 발견했고, 그대로 재기동했으면
        `ValueError: local_jwt auth requires local_jwt_issuer` 로 부팅 거부 —
        **도는 배포가 안 뜨는 배포로 바뀐다.**

        ⚠️ 기대는 **2(판정 불가)** 이지 1 이 아니다. 이 파서는 ``KEY=`` 를 compose 와
        같게 *미설정* 으로 읽으므로(`read_env_text` 주석 3) 「선언했는데 빈 값」이라는
        상태가 이 층에는 없다. 요점은 **통과가 아니라는 것**이고, 미선언 축을 통과로
        접지 않는 것이 이 스크립트의 규율이다.
        """
        result = self._run('--env-file', self._env(_COHERENT_PAIR_BUT_UNBOOTABLE))
        self.assertEqual(result.returncode, 2, result.stdout or result.stderr)
        # 무엇을 넣어야 하는지 이름으로 대야 한다.
        self.assertIn('FCC_PLATFORM_LOCAL_JWT_ISSUER', result.stderr)

    def test_declared_local_jwt_values_that_cannot_boot_fail(self):
        """⚠️ **부팅 검증이 실제로 도는가** — 위 검사만으로는 못 가른다.

        위는 「선언 안 됨」 경로라, 값을 읽고 `LocalJwtConfig.validate` 를 부르는 코드가
        통째로 죽어 있어도 통과한다. 여기서는 여섯 값을 **전부 선언**하되 시크릿을
        HS256 최소 길이 미만으로 둔다 — 그러면 미선언 경로는 지나가고 **검증만이**
        이것을 잡을 수 있다.

        ⚠️ 사유를 이 검사가 다시 쓰지 않는다. 부팅이 내는 문장을 그대로 확인한다.
        """
        result = self._run('--env-file', self._env(
            _COHERENT_LOCAL.replace('s' * 40, 'short')))
        self.assertEqual(result.returncode, 1, result.stdout or result.stderr)
        self.assertIn('is not bootable', result.stderr)
        self.assertIn('local_jwt_secret of at least', result.stderr)

    def test_a_mismatched_env_fails_and_names_the_fix(self):
        result = self._run('--env-file', self._env(
            _COHERENT_LOCAL.replace('WEB_AUTH_MODE=local', 'WEB_AUTH_MODE=oidc')))
        self.assertEqual(result.returncode, 1)
        self.assertIn('WEB_AUTH_MODE=local', result.stderr)

    def test_an_unanswerable_case_is_neither_pass_nor_fail(self):
        """⚠️ 판정 불가를 0 으로 돌려주면 이 점검은 아무것도 안 하면서 초록이 된다."""
        for body in ('POSTGRES_PASSWORD=x\n', 'FCC_PLATFORM_AUTH_MODE=local_jwt\n'):
            with self.subTest(body=body.strip()):
                result = self._run('--env-file', self._env(body))
                self.assertEqual(result.returncode, 2, result.stderr)

    def test_a_pair_that_is_fine_but_unjudgeable_elsewhere_is_not_a_pass(self):
        """⚠️ 이 스크립트의 첫 판이 정확히 여기서 틀렸다.

        `(platform, web)` 만 보고 headless 와 전송 축에 대해 **초록**을 냈고, 그래서
        런북 §S0-L 이 지시하는 부팅 불가 설정에 `exit 0` 을 줬다.
        """
        result = self._run('--env-file', self._env(
            'FCC_PLATFORM_AUTH_MODE=local_jwt\nWEB_AUTH_MODE=local\n'))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('FCC_HEADLESS_AUTH_MODE', result.stderr)

    def test_the_configuration_the_runbook_used_to_prescribe_is_refused(self):
        """⚠️ **회귀 봉인.** 그 설정은 platform 부팅은 되지만 headless 가 전부 401 이고

        SPA 는 부팅을 거부한다. 사전점검이 그것을 통과시키던 것이 이 축의 출발점이다.
        """
        result = self._run('--env-file', self._env(
            'FCC_PLATFORM_AUTH_MODE=local_jwt\nWEB_AUTH_MODE=local\n'
            'FCC_HEADLESS_AUTH_MODE=oidc_jwt\n'
            'ALLOW_INSECURE_TRANSPORT=false\nPUBLIC_HOST=10.206.34.233\n'))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('SAME bearer', result.stderr)
        self.assertIn('refuse to boot', result.stderr)

    def test_a_missing_file_is_undetermined_not_ok(self):
        self.assertEqual(
            self._run('--env-file', '/definitely/not/here.env').returncode, 2,
        )

    def test_the_parser_tolerates_the_shapes_an_operator_writes(self):
        result = self._run('--env-file', self._env(
            '# 주석\n\nexport FCC_PLATFORM_AUTH_MODE="oidc_jwt"\n'
            "WEB_AUTH_MODE='oidc'  # SPA\n"
            'FCC_HEADLESS_AUTH_MODE=oidc_jwt\n'
            'ALLOW_INSECURE_TRANSPORT=false\nPUBLIC_HOST=localhost\n'))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_preflight_agrees_with_the_predicate_on_every_mode(self):
        """⚠️ **철자가 아니라 효과를 묻는다.**

        첫 판은 import 문자열의 존재와 ``"== 'local_jwt'"`` 의 부재를 봤고, 적대 평가가
        그 술어를 **통째로 재구현**해도 통과함을 보였다(따옴표만 바꾸면 된다). 이 저장소가
        *"A seal that asks about spelling"* 으로 이름 붙인 형태다.

        대신 스크립트를 다섯 모드 × 두 전략으로 **실제로 돌려** 판정이 술어와 같은지 본다.
        """
        for mode in self._ALL_MODES_FOR_DELEGATION:
            for strategy in (WEB_AUTH_STRATEGY_OIDC, WEB_AUTH_STRATEGY_LOCAL):
                with self.subTest(mode=mode, strategy=strategy):
                    body = (
                        f'FCC_PLATFORM_AUTH_MODE={mode}\n'
                        f'WEB_AUTH_MODE={strategy}\n'
                        f'FCC_HEADLESS_AUTH_MODE={mode}\n'
                        'ALLOW_INSECURE_TRANSPORT=true\nPUBLIC_HOST=localhost\n'
                    )
                    expected_defects = deployment_auth_defects(
                        platform_auth_mode=mode, web_auth_mode=strategy,
                        headless_auth_mode=mode, insecure_transport_allowed='true',
                        public_host='localhost',
                    )
                    result = self._run('--env-file', self._env(body))
                    if mode == AUTH_MODE_LOCAL_JWT and expected_defects == ():
                        # ⚠️ **이 body 는 `local_jwt` 필수값을 선언하지 않는다.**
                        # 스크립트는 술어보다 축을 하나 더 묻는다(부팅 검증), 그리고
                        # 선언되지 않은 축은 이 스크립트의 규율상 **판정 불가(2)** 다 —
                        # 「통과」가 아니라는 것이 요점이고, 위임 등가성은 술어에게 준
                        # 축들에 대해서만 성립한다. 술어를 여기서 다시 부르지 않는 이유:
                        # 같은 술어로 기대값을 만들면 양변이 함께 움직여 무엇도 못 잡는다
                        # (이 클래스의 H-3 주석이 적은 형태).
                        #
                        # ⚠️ **술어가 이미 결함을 낸 경우는 여기 오지 않는다** —
                        # `local_jwt` + `oidc` 전략은 짝 불일치라 스크립트가 그것을
                        # 먼저 `FAIL` 로 낸다. 첫 판은 분기를 `local_jwt` 전체로 잡아
                        # 그 경우까지 2 로 기대했고, 그것은 짝 검사를 가리는 것이었다.
                        self.assertEqual(result.returncode, 2, result.stderr)
                        self.assertIn('LOCAL_JWT_ISSUER', result.stderr)
                        continue
                    self.assertEqual(
                        result.returncode == 0, expected_defects == (),
                        f'the script and the predicate disagree for '
                        f'({mode}, {strategy}): script exit={result.returncode}, '
                        f'predicate={expected_defects}\n{result.stderr}',
                    )

    _ALL_MODES_FOR_DELEGATION = (
        AUTH_MODE_LOCAL_JWT, AUTH_MODE_OIDC_JWT, AUTH_MODE_TRUSTED_HEADERS,
        AUTH_MODE_DISABLED, AUTH_MODE_NONE, 'a_mode_nobody_declared',
    )

    def test_an_oidc_login_over_plaintext_is_refused_by_a_literal_expectation(self):
        """⚠️ **H-3**: 이 규칙에는 회귀 가드가 없었다.

        위임 테스트는 기대값을 **같은 술어에서** 계산하므로, 술어를 지우면 양변이 함께
        움직여 통과한다(적대 평가 2라운드 실측: 분기를 ``if False:`` 로 바꿔도 초록).
        그래서 여기서는 술어를 부르지 않고 **결과를 글자로** 적는다.
        """
        result = self._run('--env-file', self._env(
            'FCC_PLATFORM_AUTH_MODE=oidc_jwt\nWEB_AUTH_MODE=oidc\n'
            'FCC_HEADLESS_AUTH_MODE=oidc_jwt\n'
            'ALLOW_INSECURE_TRANSPORT=true\nPUBLIC_HOST=10.206.34.233\n'))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('crypto.subtle', result.stderr)

    def test_a_plaintext_host_without_the_flag_is_refused_by_a_literal_expectation(self):
        result = self._run('--env-file', self._env(
            'FCC_PLATFORM_AUTH_MODE=local_jwt\nWEB_AUTH_MODE=local\n'
            'FCC_HEADLESS_AUTH_MODE=local_jwt\n'
            'ALLOW_INSECURE_TRANSPORT=false\nPUBLIC_HOST=10.206.34.233\n'))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('refuse to boot', result.stderr)

    def test_a_mode_without_an_spa_login_can_still_pass(self):
        """⚠️ **M-6**: 통과할 수 없는 게이트는 꺼진다.

        첫 판은 ``disabled``/``none``/``trusted_headers`` 에 대해 어떤 값으로도 초록이
        없었다 — 그리고 고치는 방법으로 SPA enum 에 없는 ``n/a`` 를 제시했다(**H-4**).
        """
        for mode in ('disabled', 'none', 'trusted_headers'):
            with self.subTest(mode=mode):
                result = self._run('--env-file', self._env(
                    f'FCC_PLATFORM_AUTH_MODE={mode}\nWEB_AUTH_MODE=oidc\n'
                    f'FCC_HEADLESS_AUTH_MODE={mode}\n'
                    'ALLOW_INSECURE_TRANSPORT=true\nPUBLIC_HOST=localhost\n'))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn(
                    f'{WEB_AUTH_STRATEGY_NOT_APPLICABLE}', result.stdout,
                    'a value the SPA enum does not accept must never be printed as '
                    'configuration',
                )

    def test_no_output_ever_offers_a_value_the_spa_would_reject(self):
        """⚠️ **H-4** — 진단이 제시한 값을 따르면 화면이 통째로 안 떴다."""
        for mode in ('disabled', 'none', 'trusted_headers', 'local_jwt', 'oidc_jwt'):
            for strategy in ('oidc', 'local'):
                with self.subTest(mode=mode, strategy=strategy):
                    result = self._run(
                        '--auth-mode', mode, '--web-auth-mode', strategy,
                        '--env-file', self._env(
                            f'FCC_HEADLESS_AUTH_MODE={mode}\n'
                            'ALLOW_INSECURE_TRANSPORT=true\nPUBLIC_HOST=localhost\n'),
                    )
                    combined = result.stdout + result.stderr
                    self.assertNotIn(
                        f'WEB_AUTH_MODE={WEB_AUTH_STRATEGY_NOT_APPLICABLE}', combined,
                    )

    def test_headless_secrets_that_differ_are_refused(self):
        """⚠️ **H-2**: 두 표면은 **같은 토큰**을 검증해야 한다."""
        result = self._run('--env-file', self._env(
            'FCC_PLATFORM_AUTH_MODE=local_jwt\nWEB_AUTH_MODE=local\n'
            'FCC_HEADLESS_AUTH_MODE=local_jwt\n'
            'FCC_PLATFORM_LOCAL_JWT_SECRET=aaa\nFCC_HEADLESS_LOCAL_JWT_SECRET=bbb\n'
            'ALLOW_INSECURE_TRANSPORT=true\nPUBLIC_HOST=10.0.0.1\n'))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('SAME token', result.stderr)

    def test_public_host_is_an_axis_not_an_exemption(self):
        """⚠️ **M-4**: 묻지 못한 축을 통과로 접지 않는다 — 주석이 그렇게 적고 있었다."""
        result = self._run('--env-file', self._env(
            'FCC_PLATFORM_AUTH_MODE=local_jwt\nWEB_AUTH_MODE=local\n'
            'FCC_HEADLESS_AUTH_MODE=local_jwt\nALLOW_INSECURE_TRANSPORT=true\n'))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('PUBLIC_HOST', result.stderr)

    def test_the_url_flag_only_observes_a_running_deployment(self):
        """⚠️ ``file://`` 을 허용하면 이 점검이 로컬 파일을 읽고 배포를 확인했다고 답한다."""
        import tempfile

        handle = tempfile.NamedTemporaryFile(
            'w', suffix='.js', delete=False, encoding='utf-8',
        )
        handle.write("authMode: 'local',")
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        result = self._run(
            '--env-file', self._env(_COHERENT_LOCAL),
            '--runtime-config-url', f'file://{handle.name}',
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('http or https', result.stderr)


if __name__ == '__main__':
    unittest.main()
