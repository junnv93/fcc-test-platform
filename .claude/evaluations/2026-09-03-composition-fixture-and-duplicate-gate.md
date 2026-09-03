# 선언된 부채 11 → 6 — 배선과 배포는 다른 축이다 (2026-09-03)

## 1. `reference_composition` 4건 — **내가 만든 부채였다**

같은 날 앞선 웨이브가 provider UI descriptor 를 *provider 빌더 import* 에서
**런타임 JSON 등록**으로 바꿨다. 그전에는 「어떤 provider 를 내놓는가」가
**코드 사실**이라 체크아웃에서 답이 있었다. 바뀐 뒤에는 **배포 사실**이고,
`config/provider-ui/*.json` 은 provider 소유 내용이라 **의도적으로 gitignore** 다.

그래서 배선을 재는 검사가 **배포 내용**에 걸려 red 였다 —
검사 이름(`…ReachesTheReferenceService`)이 말하는 것과 실제로 재던 것이 달랐다.

### 처방 — fixture, 그리고 **재지 않는 것을 적기**

임시 디렉터리에 최소 descriptor(`provider_id` + area 하나)를 심고
`FCC_PLATFORM_PROVIDER_UI_DIR` 로 가리킨다.

⚠️ **이 fixture 는 「배포가 descriptor 를 싣는가」를 재지 않는다.** 그것은 다른
축이고 로더가 이미 그 자리를 갖는다(없으면 경고 · 깨졌거나 중복이면 **기동 거부**).
두 축을 한 검사에 묶으면 **배선이 멀쩡한데 배포가 비었다는 이유로 red** 가 되고,
그런 red 는 무시된다.

⚠️ 그리고 디렉터리를 `setUp` 하나로 올렸다 — `_offered_provider_id()` 와
`_runtime()` 이 각자 세우면 두 합성이 서로 다른 레지스트리를 보게 되고,
그러면 이 클래스의 명제(*「화면이 제공하는 집합과 중앙 등록부가 같은 프로세스에서
만난다」*)가 무너진다.

## 2. ⚠️ 그러다 진짜 결함이 드러났다 — **출처가 둘이고 서로 다르다**

fixture 를 심자 이 단언이 깨졌다:

    self.assertEqual(resolver.provider_ids(), (self.offered_provider_id,))
    → ('fcc-unlicensed-conducted',) != ('fcc-wiring-probe',)

합성된 런타임 안에 「어떤 provider 가 있나」의 출처가 **둘**이다:

| | 무엇인가 | 출처 |
|---|---|---|
| `ProviderReferenceResolverRegistry` | 이 빌드가 싣는 **구현** 집합 | 코드 (`UNLICENSED_PROVIDER_ID`) |
| `ProviderUiDescriptorRegistry` | **배포**된 집합 | 디스크 JSON |

**둘은 정당하게 다르다.** 같은 출처였을 때는 하나의 검사로 둘 다 잡혔지만,
출처가 갈린 지금 상등을 단언하면 **정상 배포에서 red** 가 난다.

두 축을 갈라 각각 단언하고, `assertNotEqual` 로 **둘이 구분된다는 사실 자체**를
붙잡았다(fixture id 가 실수로 빌드의 것과 같아지면 이 검사가 구분하려는 것이 사라진다).

### 그리고 그 간극에 이름을 붙였다

descriptor 를 놓으면 화면이 그 provider 를 제공하는데, 그 provider 의 참조
어댑터가 이 빌드에 없으면? 실측: `__getitem__` 이 **`KeyError`** 를 낸다 —
조용하지 않다. 그것을 봉인했다(`test_a_deployed_provider_without_an_adapter_fails_loudly`).

⚠️ **이 봉인은 그 거동이 옳다고 말하지 않는다.** 요청 시점 `KeyError` 는 기동
시점 거부보다 늦고, 더 나은 자리는 합성이 「descriptor 는 있는데 어댑터가 없다」를
**기동에서** 말하는 것이다. 그 판정은 아직 안 내렸다 — 지금 무엇이 일어나는지만
붙잡는다. 조용해지는 변경(빈 결과 · `None`)이 들어오면 red 다.

## 3. `TestRunbookExists` — 이관이 아니라 **중복 제거**

provider 세션이 판정했고 **독립 확인했다**: 그쪽
`tests/test_chamber_measurement_staging_evidence.py:637` 에 같은 클래스·같은
이름·같은 세 단언이 그대로 있고, 대상 런북도 그쪽에만 있다.

추출(2026-08-30)이 파일을 두 레인에 복사했고 **이쪽 사본만 대상 런북을 잃었다.**

⚠️ 그리고 **그쪽 사본이 더 강하다.** 우리가 지적한 공허성(런북이 게이트 파일
이름을 «글자»로만 적으면 그 파일이 사라져도 초록)을 그쪽이 별도 검사로 받았다.
즉 이쪽 것을 가져갈 이유가 없다.

⚠️ 그쪽이 그 새 검사를 **「비-공허성 확인했다」고 적지 않았다** — 실측하니
파일을 치우면 그 검사가 붉는 게 아니라 형제들의 모듈 레벨 import 때문에
**수집이 먼저 깨진다.** 확인할 수 없었으므로 확인한 척하지 않았다는 것이
그 자체로 이 계열의 규율이다.

이 파일의 나머지 12개 클래스(73 passed)는 이 레인에서 의미가 있으므로 남았다.

## 결과

    선언된 부채 11 → 6

오늘 하루 누계: **17 → 6.**

남은 6 중 하나(`test_project_result_selection_performance`)는 `benchmark_harness`
가 배포되는 패키지 안에 없어서 막혀 있다. provider 세션과 판정이 섰다 —
`fcc_test_contracts.common` 으로 올린다. 순서는 **올리고 태그가 나온 뒤 그쪽이
소비로 바꾼다**(먼저 지우면 그 사이 그쪽 벤치가 전부 죽는다).
