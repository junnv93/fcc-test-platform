# OpenAPI 발행 소유권 — 사본 다섯이 다 같이 낡았다 (2026-09-04)

## Why

`headless-api.openapi.json` **다섯 사본**(모노레포 1 · 계약 2 · 이 상자 2)이 실측
2026-09-04 에 **byte 동일하게**(`3eab137f03`) 낡아 있었다 — 계약은 `v0.1.17` 인데 내용은
`v0.1.12` 시절이었다.

⚠️ **사본이 서로 어긋난 것이 아니다. 다섯이 완전히 같았다.** 그러므로 *사본 사이의 일치*를
보는 검사였다면 **끝까지 초록**이었을 것이다. 어긋난 것은 **생산자와 SSOT** 다.

원인은 소유권이었다. 조립기가 필요로 하는 것은 전부 계약 레인 소유인데(변환기 · 표 아홉 ·
에러 코드; 모노레포 의존은 `TYPE_CHECKING` 아래 타입 힌트 하나, 런타임 의존 0) **발행
진입점만 모노레포에 있었고** 그 레포는 계약 `v0.1.12` 를 핀한다. 계약이 다섯 번
릴리스되는 동안 아무도 그 문서를 다시 내지 않았다.

그리고 이 상자에서도 같은 형태가 하나 더 있었다: `platform-api.openapi.json` 이 드리프트
했는데 게이트가 안내하는 `scripts/export_session_api_schemas.py` 가 **이 상자에 없다**.
red 는 정확한데 **운영자가 따라갈 수 있는 지시가 아니었다.**

## What

**소유권을 따라 진입점을 놓았다.** 계약이 저작하는 문서는 계약이 발행하고(`v0.1.18`,
조립기 이사), 이 상자가 저작하는 문서는 이 상자가 발행한다. 이 상자가 *나르기만* 하는
문서는 **복사**한다 — 저작과 운반을 섞지 않는다.

| 문서 | 저작 | 이 상자의 역할 |
|---|---|---|
| `headless-api.openapi.json` | 계약 레인 | **나른다** → `scripts/sync_published_openapi.py` |
| `platform-api.openapi.json` | **이 상자** | **발행한다** → `scripts/export_platform_openapi.py` |

## How

- `scripts/sync_published_openapi.py` — 의존 레인의 발행본을 가져온다. ⚠️ **여기서 다시
  만들지 않는다** — 그러면 생산자가 둘이 되고, 갈라지는 날 어느 쪽이 옳은지 말할 것이 없다.
- `scripts/export_platform_openapi.py` — 이 상자의 조립기로 발행한다. 직렬화 규약을
  모노레포 생산자와 **같게** 고정했다(다르면 서식 차이가 드리프트로 보인다).
- `tests/test_published_openapi_is_carried_not_authored.py` — 나르는 사본이 **의존 레인의
  발행본**과 같은가. ⚠️ 사본끼리 비교하지 않는다.
- 계약 핀 `v0.1.16` → **`v0.1.18`**

## Verification

```
lane_check     선언 0 / 관측 0 ✅   (2,994 passed)
봉인 발화      사본을 흔들자 red · 동기화하니 green
드리프트 검사   두 스크립트 모두 --check 가 낡음을 정확히 지목하고 fix 를 이름으로 댄다
```

## 🔴 이 웨이브가 드러낸 것 — 그리고 고치지 않은 것

**`resolve_dependency_artifact` 를 이 상자가 저작한 문서에 쓰는 자리가 있었다.** 그 함수는
*「나를 배송한 **의존** 레인이 이걸 어디 뒀나」* 를 묻는다. `platform-api.openapi.json` 은
이 상자가 저작하므로 그 물음은 **답하는 트리가 틀렸다** — 계약 패키지가 사본을 나르므로
답은 얻지만, 자기가 방금 만든 문서를 남의 사본과 비교하게 된다.

⚠️ **실패하던 둘만 고쳤다**(`test_platform_chamber_api_p2` · `test_platform_claim_write_fe_p3`).
같은 형태가 **아직 셋 더 있다**:

```
tests/test_platform_equipment_list_api.py:58              platform-api.openapi.json
tests/test_platform_project_directory_invariants.py:157   platform-api.openapi.json
tests/test_platform_asyncapi_schema.py:35                 platform-api.asyncapi.json
```

**오늘은 초록이다** — 계약 패키지의 사본이 우연히 맞기 때문이다. 그것이 낡는 날 red 가
나고, 원인은 이 상자의 아티팩트가 아니다. 초록인 것을 고치는 것은 이 웨이브의 요구가
아니었고, 한 번 시도했다가 문법을 깨뜨려 되돌렸다. **이름으로 남긴다.**

## 후속

1. 🔴 위 셋의 해소기 교정 (오늘 초록, 낡는 날 오진을 부른다)
2. 🔴 `platform-api.asyncapi.json` 에는 이 상자에 발행 진입점이 **없다** — 그 게이트는
   여전히 모노레포 스크립트를 가리킨다
3. 모노레포의 `src/application/headless/api_schema.py` 는 **삭제 대기 중복** — 그 레포가
   계약 핀을 `v0.1.12` 에서 올리는 날 계약 모듈을 import 하고 자기 사본을 지운다
