# 시료 도메인 — 엑셀 셀을 사건으로 (2026-09-04)

## Why

시료 도메인에는 축이 둘인데 **한쪽만 옳았다.**

시험 실무자 축은 처음부터 옳았다. `sample_intakes` 가 `samples` 에 1:N append-only 이고
`intake_date/bl/ap/cp/csc/rf_cal/hw_rev/note/tech_group` 를 갖는다 — 요구가 그대로
스키마에 있다.

PM 축은 그렇지 않았다. `intake_cert` · `received_date` · `released_date` · `note` 가
전부 **단일 TEXT** 인데, 실제 데이터는 그 한 칸에 값을 줄바꿈으로 쌓는다. 운영자가 준
시료 한 건:

```
반입증: 20251104-… / 20251027-… / 20251017-… / 20250930-…
수령한 날짜: 2025-10-28 / 2025-09-30
반출한 날짜: 2025-10-23 김용태 프로님 / 2025-10-17 김용태프로님
Note: 11/4일 재 반입 / 10/28일 재 반입 / 10/23일 NR n41/48 CEM 디버깅건으로 임시 반출 / …
```

**엑셀 셀을 그대로 옮겨놓은 형태**다. 그래서 「이 시료가 몇 번 나갔다 들어왔나」를 셀 수
없고, 반출과 반입을 짝지어 현재 보유 상태를 계산할 수 없고, 반출 사유가 자유 텍스트에
묻힌다.

⚠️ **그리고 칸끼리 개수가 맞지 않는다** — 반입증 4 · 수령 2 · 반출 2 · Note 가 가리키는
반입 3. 줄 순서로 짝지으면 **틀린 짝**이 생긴다. 이것이 「자동 변환하지 않는다」의 근거다.

## What

`ADR-0002` 가 운영자 결정 9건을 기록하고, 두 레인이 그것을 구현한다.

**계약 레인(`fcc-test-contracts`)** — 도메인이 계약을 낳는다.
`SAMPLE_EDITABLE_FIELDS` 에 `sample_kind`/`sample_description` 을 넣은 것만으로
OpenAPI 요청·응답 스키마가 따라 움직인다(`_SAMPLE_TEXT_PROPERTIES` 가 그 튜플에서
파생된다). custody 축은 `SampleCustodyEvent` · `CUSTODY_EVENT_FIELDS` ·
`SampleCustodyEventType` 과 `custody_state()` 한 함수로 들어간다.

**플랫폼 레인** — 마이그레이션 `034`, custody 읽기/쓰기 어댑터, 4개 엔드포인트, 화면.

핵심 설계 판단 셋:

1. **반입증은 사건의 속성이다.** 운영자 확인: *"고객사에서 우리 시험소로 샘플이 전달될
   때 고객사 측에서 전달해 주는 문서이고, 한번 반입 샘플 댓수가 12대 이런 식으로
   들어온다."* 즉 한 납품에 한 장이고 시료 여럿이 공유한다 — 개수 불일치가 이것으로
   설명된다. **별도 표를 만들지 않는다**: 배치는 `(project_id, intake_cert_number)` 로
   복원된다.
2. **정정은 수정이 아니라 삭제다.** PATCH 를 두지 않았다. 수정은 흔적 없이 과거를 바꾸고,
   삭제는 보인다. 행이 스스로 `actor_subject`/`created_at` 을 갖는다 —
   `sample_intakes` 는 행위자를 아예 갖지 않으므로 custody 축이 더 많이 기억한다.
3. **`sample_inventory_revisions` 에 쓰지 않는다.** 그 원장의 스냅샷 모양
   (`fcc.sample.inventory.snapshot.v1`)을 측정 세션이 함께 쓴다. custody 를 그 안에
   넣으면 이 축의 변경마다 측정 계약이 흔들린다. `row_version` 도 올리지 않아 편집
   화면이 열려 있어도 헛된 409 가 나지 않는다.

## How

`test_category` 에는 CHECK 를 걸지 않고 `event_type` 에는 걸었다. 대칭이 아닌 것이
의도다:

| | `test_category` | `event_type` |
|---|---|---|
| 어휘가 닫혔나 | 아니다 (mmWave 등 확장 가능) | 닫힌 이분법이다 |
| 틀리면 | 값이 이상해 보인다 | **보유 상태 계산이 조용히 틀린다** |
| 판정 | 앱 경계 + 드롭다운 | DB CHECK |

전자는 `test_equipment_lists.test_item_key` 가 먼저 내린 판단을 따랐다.

기존 TEXT 칸은 **그대로 둔다.** 화면이 둘 다 보여준다 — 위에 새 사건 목록, 아래 「기존
기록 (엑셀 원문)」. 사람이 보고 옮길 수 있고, 옮기지 않아도 한 줄도 잃지 않는다.

## Verification

* `tests/test_platform_sample_custody_and_classification.py` (19) — 예시 2 의 여섯
  사건이 손실 없이 들어가고, 보유 상태가 계산되고, 사건 없는 시료가 「반출됨」이 아니라
  「기록 없음」이고, 반입증으로 배치가 복원되고, 원문 칸이 그대로인지.
* `tests/test_sample_inventory_exporter.py` (7) — **새 자리**. 아래 실측 참조.
* `apps/web/tests/sample-inventory-custody.test.tsx` (14) — 등록 버튼이 폼을 열고,
  사건이 보이고 추가·삭제되고, 입고 1:N 이 나오고, Accessory 가 시험 구분을 숨긴다.
* 계약 레인 `tests/test_sample_custody_contract.py` (19) — 어휘가 도메인에서 파생되는가,
  custody 경로에 PATCH 가 없는가, 보유 상태 규칙이 한 곳에만 사는가.
* 전체: 플랫폼 pytest · `apps/web` vitest 1492 · eslint 0 errors · `tsc --noEmit`.

### 지나가다 실측한 것 — 내보내기가 항상 죽고 있었다

`TEMPLATE_DIRECTORY` 가 `tests/fixtures/sample_inventory_templates/` 를 가리키는데 파일은
`tests/fixtures/sample_inventory/` 에 있었다. `_load_template` 은 조용히 넘어가지 않고
`FileNotFoundError` 를 던지는 설계라 증상은 분명했지만 — **이 경로를 지나가는 테스트가
한 건도 없었다.** 계약·CRUD·RBAC 테스트는 전부 초록이었다.

경로를 옛 값으로 되돌려 새 테스트 7건이 전부 빨간지 확인했다. 렌더러를 실제로 호출하는
시험이 없으면 파일 경로 오타는 어떤 단위 시험도 잡지 못한다.

⚠️ **그리고 그 수정은 절반이었다.** 동료 세션(`fcc-delivery-final-29`)이 짚었다 —
디렉터리 이름을 고쳐도 `parents[3] / 'tests' / 'fixtures'` 는 **컨테이너에서 여전히
못 찾는다**. 이미지는 `pip install --no-deps .` 뒤 소스를 지우고 site-packages 만으로
돌고, 휠은 `fcc_test_platform*` 만 실으며 `tests/` 는 이미지에 COPY 되지도 않는다.
실측(휠만 설치한 깨끗한 venv, cwd 가 저장소 밖):

```
옛 표현식 → …/site-packages/tests/fixtures/sample_inventory   존재: False
```

그래서 템플릿을 `fcc_test_platform/infrastructure/excel/templates/` 로 옮기고
`importlib.resources` 로 읽는다(`decision_catalogue.json` 이 이미 쓰는 형태).
`package-data` 에 선언을 더했고, 판정은 **실제로 휠을 빌드해 돌려본 것**이다:

```
휠 내용물   fcc_test_platform/infrastructure/excel/templates/{pm_sample_status,rf_sample_data}.xlsx
휠만 설치   PM 5,255 bytes · RF 5,733 bytes — 둘 다 유효 xlsx, 소스 트리 없음
```

「고쳤다」와 「돌고 있는 것이 고쳐졌다」가 다른 축이라는 것이 이 결함의 요지다.

## 후속

* **커널 태그 + pin 상향이 남아 있다.** 플랫폼이 `kernel-v0.3.0` 을 git 태그로 고정하므로
  이 두 PR 은 태그가 올라간 뒤에야 함께 선다. 태그 push 는 운영자 승인 지점이다.
* **낡은 미러 하나** — `packages/api-artifacts/artifacts/platform-api.openapi.json` 이
  **이 작업 전부터** 정본(`docs/api/`)과 갈라져 있었다(`IngestPublishedPlanRequest` 등
  결락). 미러 검사 절반이 모노레포에 남아 이 레포에서 돌지 않는다 —
  `tests/test_api_artifacts_web_half.py` 의 docstring 이 *"한쪽만 검사하면 나머지가
  갈라져도 조용하다"* 고 예고한 그 자리다. 재생성이 함께 고쳤다.
* **로케일 중복 키 10개** — `ko.json`/`en.json` 의 챔버 bootstrap 블록에
  `bootstrapDescription` 등이 두 번씩 있다(앞엣것은 죽은 문자열). 다른 도메인이라
  손대지 않았다.
* **배치 등록 화면** — 반입증 하나로 12대를 한 번에 등록하는 흐름은 결정 7 이 자연히
  가리키는 다음 단계다. 스키마는 그것을 막지 않는다.
* **진행률(progress) 도메인은 여전히 대기** — 이 작업은 손대지 않았다.
