# 접수 화면이 시료를 만들 때마다 실패했다 — 구현이 자기 선언을 어기고 있었다

측정 2026-09-06 · 기준 `main` = `8dfeb33` · 세션 fcc-delivery-final (a241f0f7)

## Why — 시험원이 실제로 본 것

개발 PC 의 중앙 스택을 035 까지 올린 뒤 접수 화면을 브라우저로 직접 몰았다.
시료를 만들면 **매번** 「The request failed.」가 떴다 — 그런데 시료는 **생성됐다.**

    200 POST  /platform/projects/{id}/samples
    503 GET   /platform/projects/{id}/samples/undefined      ← 문자열 "undefined"
    503 GET   /platform/projects/{id}/samples/undefined

⚠️ **3/3 재현.** 새 브라우저 맥락 셋에서 각각 한 번씩 만들었고 세 번 다 났다.
간헐 결함이 아니라 **결정적**이다.

## What — 원인

`POST …/samples` 의 실제 응답 키는 **`id`** 인데, 그 엔드포인트의 OpenAPI 는
`SampleInventoryItem` 으로 선언하고 그 스키마의 required 는 **`sample_id`** 다.

    응답 키: [… "id", "project_id", "status", "row_version", …]
    sample_id 값: undefined

프런트엔드는 **선언을 믿는 것이 옳다** — 타입 클라이언트가 그 선언에서 생성되므로
`updated.sample_id` 는 타입상 `string` 이다. 그 값이 `undefined` 라
`updateSearch({ sample: updated.sample_id })` 가 URL 에 `?sample=undefined` 를 넣고,
뒤따르는 상세 조회가 503 을 낸다.

⚠️ **타입 검사가 이것을 잡을 수 없다.** 프런트는 선언과 일치하고 서버도 파이썬
타입상 문제가 없다. 갈라진 것은 «선언과 런타임 값»이고 **그 축을 보는 검사가
없었다.**

### 왜 읽기는 맞고 쓰기만 틀렸나

읽기 경로는 SQL 에서 별칭을 붙인다 — `SELECT s."id" AS "sample_id"`.
쓰기 경로는 손으로 dict 를 짓는다 — `{'id': sample_id, 'project_id': …}`.
**같은 자원이 읽기와 쓰기에서 다른 이름을 답하고 있었다.**

### 같은 계급이 하나 더 있었다

custody 사건 추가 응답도 `id` 를 돌려주는데 `SampleCustodyEventEnvelope` 는
`custody_event_id` 를 선언하고 `additionalProperties: false` 다 — 이름이 틀렸을
뿐 아니라 **선언에 없는 속성**이다. 같은 파일의 «삭제» 응답은 이미
`custody_event_id` 를 쓰고 있었다. 한 파일 안에서 갈라져 있었다.

## How — 수리

쓰기 어댑터의 출력 키를 선언에 맞췄다(네 자리):

    create      'id' → 'sample_id'
    patch       'id' → 'sample_id'
    _row_projection (status·delete 가 쓴다)  'id' → 'sample_id'
    custody 추가 'id' → 'custody_event_id'

⚠️ **`patch` 에는 한 겹이 더 있었다.** 커널의 값 모양이 자원 키를 `id` 로 부르고
(`fcc_test_kernel.domain.services.sample_inventory_policy` — `apply_patch` 가 그
이름으로 돌려준다), 그 dict 가 그대로 응답이 되어 `id` 와 `sample_id` 가 **둘 다**
실렸다. 커널을 고치면 2-레포 웨이브가 되고 커널 태그가 앞서야 하므로, **어댑터
경계에서 번역**했다 — 커널 값은 내부 모양이고 이 어댑터가 API 경계다.

시험 50줄이 옛 키를 적고 있었다(`sample['id']` 등). 전부 순수 `KeyError` 였고
의미는 바뀌지 않았다.

## 봉인 — 「전부 통과」가 아니라 「선언과 일치」

`tests/test_sample_write_responses_match_their_declaration.py`.
**소스를 읽지 않는다.** 실제 쓰기·읽기 경로를 돌려 **나온 값**을 선언된 스키마로
검증한다. 대상 연산 집합은 OpenAPI 에서 **파생한다**.

⚠️ 이 검사를 붙이자 **오늘 이미 있던 갈라짐 둘이 더 나왔다.** 그것까지 이
웨이브에서 고치면 범위가 조용히 넘치고, 무시하면 검사가 거짓 초록이 된다.
그래서 `scripts/lane_check.py` 와 같은 형태를 썼다 — **관측된 갈라짐 집합 ==
선언된 갈라짐 집합(`KNOWN_DRIFT`)**. 그러면 새 갈라짐도, 고쳐진 갈라짐도 red 다.

`KNOWN_DRIFT` 에 적은 둘(이 웨이브가 만든 것이 **아니다**):

| 연산 | 갈라짐 | 왜 지금 안 고치나 |
|---|---|---|
| `POST`·`PATCH`·`status`·`DELETE` | 선언된 required `intake_count` 가 없다 | 담으려면 쓰기마다 세는 질의가 붙는다 — **비용 판단**이 앞서야 한다 |
| `GET …/{sample_id}` | 선언에 없는 `model_id` 를 더 준다 | 그 칸의 소유(프로젝트 축 vs 시료 축) 판정이 앞서야 한다. OpenAPI 는 **생성물**이라 손으로 못 고친다 |

그리고 이 웨이브가 고친 축은 `KNOWN_DRIFT` **뒤에 숨지 않게** 따로 못박았다
(`test_the_identifier_axis_is_clean_everywhere`).

## Verification — 실측

* 전 연산 10건을 실제로 돌려 갈라짐을 전수로 쟀다. custody 넷은 수리 후 전부 ✅.
* 새 검사 7건 통과. **주입으로 이빨을 두 축에서 확인했다**:
  - 선언에 없는 키(`surprise`)를 create 응답에 넣자 → 「선언에 없는 새 갈라짐」 red
  - `create` 를 결함 판(`id`)으로 되돌리자 → **4건 red**(식별자 전용 축 포함)
  - 복원하니 7/7
* 레인 전량: `lane_check` EXIT=0 (3,221 passed · 실패 0).
* **브라우저에서 재확인했다 — 같은 스크립트로 전후를 쟀다.**

      수리 전   3/3 발생   undefined 요청 2건씩 · 「The request failed」 표시됨
      수리 후   0/3 발생   undefined 요청 0건   · 문구 없음

  그리고 성공 경로의 URL 이 실제 UUID 를 받는다:
  `?sample=12c88ff5-eb8b-47fd-8f7c-b0076a5af4c0` (전에는 `?sample=undefined`).

  ⚠️ **첫 사후 측정은 3/3 「발생」으로 나왔고 그것은 내 대조 방법의 결함이었다.**
  재현 스크립트가 시료번호를 `S-100`~`S-102` 로 고정해 두어, 수리 «전» 실행이
  만든 행과 충돌해 **정당한 중복 오류**를 냈다. `undefined` 요청은 그때 이미
  0건이었으므로 두 신호를 따로 세지 않았으면 「안 고쳐졌다」로 오독했을 것이다.
  번호를 고유하게 바꾸니 0/3. **대조군은 상태까지 고정해야 한다.**

## 후속

1. ✅ 재빌드 뒤 접수 화면에서 실증했다(위 Verification). 개발 PC 의 중앙 스택은
   이 수리를 담은 이미지로 돌고 있다.
2. ⚠️ **모노레포에 같은 시료 시험 사본이 있다**(`FCC_mobile_test_automation`).
   그쪽이 이 레인 핀을 올리는 날 같은 `KeyError` 를 만난다. 이 문단이 그 예고다.
3. `KNOWN_DRIFT` 의 둘은 **각자 판정이 필요한 별건**이다. 늘리지 말고 줄여라.
