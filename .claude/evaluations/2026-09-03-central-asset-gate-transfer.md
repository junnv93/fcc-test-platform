# 중앙 자산 게이트 이관 — 실측 (2026-09-03)

## 무엇을 쟀나

중앙 자산(런북 · `docker-compose.central.yml` · `fcc-dev-realm.json` ·
`central.env.example`)이 이 레인으로 온 뒤, **그것을 읽는 게이트가 provider
저장소에 남아 있었다.** 그 게이트를 이 저장소에 세우고 실제로 도는지 쟀다.

## 실측 1 — 가져온 게이트 열 건이 「분리 이전 세계」를 단언하고 있었다

(A) 3파일을 그대로 가져온 첫 실행: **86 passed / 10 failed.**
열 건 전부가 축이 뒤집힌 것이었지 결함이 아니었다.

| 단언했던 것 | 지금 참인 것 |
|---|---|
| `web` 에 `build` 스탠자가 **없다** | **있다** — 중앙이 자기 이미지를 빌드한다 |
| `platform-api` 와 `headless-api` 가 **같은 이미지** | **다르다** — headless 는 소비만 |
| 이 저장소는 **비추출 레인** | **추출된 레인** |
| 핀 개수가 정확히 N | 이관이 진행되며 움직인다 → **비-공허성으로 대체** |

⚠️ **개수 동등성을 비-공허성으로 바꾼 것이 게이트 낮추기가 아닌 이유**: 핀 개수는
커널 이관이 진행되는 동안 **의도적으로** 움직이는 축이다. 움직이는 값을 상수로
붙잡으면 그 게이트는 정상 진행마다 red 를 내고, 그런 게이트는 삭제된다.
붙잡아야 할 성질은 「핀이 존재한다」이고 그것은 계속 참이어야 한다.

## 실측 2 — 이름 축은 위임과 복제를 구분하지 못한다

`test_it_reuses_the_sibling_parser_rather_than_copying_it` 의 원래 술어는
`'def read_env_text' not in source` 였다. 이 축에서 **「형제를 호출한다」와
「아무것도 안 한다」가 같은 값**이다. 위임 축으로 바꿨다:
`'_load_sibling().read_env_text' in source`.

## 실측 3 — 게이트가 즉시 두 건을 잡았다

1. 런북 배치표에 `fcc-test-kernel` 행이 없었다 (같은 날 만든 배포판인데 문서가 안 따라왔다)
2. 내 잔여 파일 `infra/central/central.env.bak.20260903-070041` 이 낡은 값
   `'unlicensed'` 를 담고 있었다 → 삭제

## 실측 4 — 여섯 함수 중 셋만 이 레인에서 돌 수 있다

파일째 가져가면 provider 의 코드 테스트가 사라진다(`test_proxy_trust_policy.py`
78개 중 중앙 자산을 읽는 건 2개). 함수 단위로 가져오며 이식 가능성을 먼저 쟀다:

| 함수 | 필요한 것 | 이 레인에 있나 |
|---|---|---|
| compose 의 `FORWARDED_ALLOW_IPS` 값 판정 | `fcc_test_contracts.common.proxy_trust_policy` | **있다** (계약 레인) |
| 보관 축출 문구 대조 | `fcc_test_platform...local_auth_service` 상수 | **있다** (이 레인 소유) |
| realm 의 sample 권한 토큰 | `infra/keycloak/fcc-dev-realm.json` | **있다** |
| 런북 프록시 진단 명령 | 런북 + **provider 부팅** | **절반** |
| rbac 권한 우주 | headless 권한 집합 | **없다** |
| 백업 리허설 워크플로 | 그 워크플로 파일 | **없다** |

⚠️ **절반짜리를 전부인 척하지 않는 것이 이 실측의 핵심이다.** 안 도는 것을
가져오면 provider 에서는 지워지고 여기서는 skip 이라 **아무 데서도 안 돈다.**

## 실측 5 — 판별력 (트리 무변이)

메모리에서만 변이시켜 쟀고, 측정 후 `git status --porcelain` 으로 잔류 0 확인.

    compose 값이 * 로 드리프트          🔴 red
    코드 문구 개명, 런북 미갱신          🔴 red
    realm 에 미승인 sample 토큰 추가     🔴 red

## 실측 6 — 스위트의 red 는 **숨겨진 것이 아니라 선언된 것이다**

⚠️ **이 절은 한 번 틀리게 썼다가 고쳤다. 그 정정 자체가 실측이다.**

전체 스위트를 돌리고 처음 쓴 문장은 *"이 저장소의 스위트는 추출 이래 green 이었던
적이 없다"* 였다. **참이지만 결정적인 것을 빠뜨렸다** — 그 red 들은
`delivered_test_run_baseline.json` 에 **17개 node-id 로 선언되어 있고**,
`scripts/lane_check.py` 가 pre-push 에서 그 집합을 붙잡는다.

    관측된 실패 17 / 선언된 실패 17  ✅ 일치 — 이 상자는 선언한 그대로다

즉 **숨겨진 결함이 아니라 배송이 명시적으로 진 부채**이고, 이미 게이트가 있다.
`benchmark_harness` 수집 오류(`tests/test_project_result_selection_performance.py`)
까지 그 선언에 들어 있다.

**왜 22 와 17 이 다른가 — 술어가 다르다.** pytest 는 subtest 실패를 각각 세고
(`test_no_coverage_table_string_in_ingestion_modules` 하나가 SUBFAILED 4건),
lane_check 는 **부모 node-id** 로 센다. 두 수는 모두 정확하고 서로 다른 질문의
답이다 — `check-axis-blindness.md` §「N개다」 서식 그대로다.

**그래도 남는 사실 하나**: 그 17 중 지배 유형이 `fcc-test-platform/src/…` 를 찾는
것이라는 점은 그대로다. `src/` 는 이 레인에 없다. 즉 선언된 부채의 내용은
**분리 잔류**이고, 그것을 갚는 것이 커널 이관 2단계와 같은 방향이다.

### 그리고 이 실측이 축 맹점의 실례인 두 번째 이유

`pytest tests/` 를 `--continue-on-collection-errors` 없이 돌리면 수집 오류 한 건이
전체를 중단시켜 출력이 `1 error` 하나가 된다. `2881 passed` 는 그 플래그를 줘야
보인다 — **「실패 0건」과 「실행 0건」이 같은 모양**이다.

⚠️ 그리고 pre-push 훅이 `/usr/bin/python3`(pytest 없음)로 lane_check 를 돌렸을 때,
훅은 그것을 **통과로 읽지 않고** *"pytest 가 시작조차 못했다"* 로 이름 붙여 막았다.
같은 서식의 **올바른** 구현이다. 지정된 경로는 `FCC_LANE_CHECK_PYTHON` 이다.

## 근거

- `.claude/rules/check-axis-blindness.md` — 이 웨이브에서 세 번 발화했다 (실측 2 · 5 · 6)
- provider 저장소 `origin/main = 403a2fd7` 의 원본 함수들
