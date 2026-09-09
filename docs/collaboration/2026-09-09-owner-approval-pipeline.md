# 레포 소유자 승인 요청 — 파이프라인과 순서

> 작성 2026-09-09. 작성자 세션: `younhs21-dexter` (collaborator, admin 아님).
> 대상: `junnv93` (두 레포 소유자).
> 상태: **아직 아무것도 push 되지 않았다.** 이 문서는 «무엇을 어떤 순서로
> 물을 것인가»를 정하기 위한 것이고, 그 자체가 승인 요청은 아니다.

---

## 0. 한 문단 요약

실무자·PM 콘솔 UI를 다시 만들면서 **세 종류의 경계**를 넘었다.

1. **UI** — 소유자가 「마음대로 해 보라」고 허락한 범위. 승인 대상이 아니다.
2. **봉인(seal) 검사 5건** — 값이 낡아 «고친 쪽»을 위반이라 부르던 것들. 규칙을
   고쳤다. 소유자에게 **알려야** 하지만 방아쇠는 아니다.
3. **읽기 계약 1건** — 새 엔드포인트. `fcc-test-contracts`(커널)를 만졌고,
   **태그 릴리스가 필요하다.** 이것이 유일하게 «따로 물어야 하는» 항목이다.

⚠️ 지금 이 기계는 **설치본과 핀이 갈라져 있다** — `kernel-v0.5.4` 로 핀돼 있는데
설치된 커널에는 새 surface 가 들어 있다. 로컬 실험이라 그렇게 뒀고, 3번이
정리되기 전까지 이 상태를 다른 기계로 옮기면 안 된다.

---

## 1. 지금 상태 (실측)

| 레포 | 브랜치 | HEAD | 미커밋 |
|---|---|---|---|
| `fcc-test-platform` | `feature/flowdeck-console-ui` | `aa97ab1` | 17 M + 3 신규 (+1,770 / −66) |
| `fcc-test-contracts` | `main` | `bdc5e9b` | 1 M (+52) |

`aa97ab1` 은 **어제 로컬 커밋**이고 아직 push 되지 않았다(사용자 지시:
「당분간 푸시 금지」). 즉 소유자는 이 작업을 **아직 하나도 보지 못했다.**

---

## 2. 승인 축 셋 — 무엇이 어떤 종류인가

### 축 A. UI (승인 불요)

소유자가 UI 실험을 허락했다. 이 축은 **보고**만 하면 된다.

- 실무자/PM/총괄 세 홈, Nord 테마, 누적 추이 그래프, 진행률 링 재설계
- 내비 아이콘 20개(의존성 0, 직접 그림), 브랜드 마크 교체
- 시험항목 화면(`/test-items`), 시스템 대시보드 목업(`/system`)
- 배정·이슈 표시(면 + 레일), 요약 띠

### 축 B. 봉인 검사 5건 (알림 — 방아쇠 아님)

전부 **재려던 것과 다른 것을 재고 있던** 검사다. 값을 고친 것이 아니라
**축을 고쳤다**. 이 축은 CODEOWNERS §① 「게이트 자신」에 걸리지만,
`require_code_owner_reviews: false` 라 오늘 막지는 않는다 — 그래도 게이트를
만진 것은 소유자가 알아야 하는 종류다.

| # | 검사 | 박아 둔 값 | 재려던 것 | 바꾼 축 | 커밋 |
|---|---|---|---|---|---|
| 1 | `test_light_default_with_dark_override` | `--p-light-surface-bg: #ffffff` | 라이트가 기본인가 | 면의 **방향** (위가 아래보다 어둡지 않다) | `aa97ab1` |
| 2 | `TestNativeControlFoundation` | `[light, dark, dark]` 정확히 셋 | 컨트롤 테두리가 흩어지지 않는가 | **모든** 선언이 per-theme primitive 를 거치는가 | `aa97ab1` |
| 3 | `TestPrimitivesNoInlineHexColors` | 파일에 hex «문자열»이 있는가 | **적용되는** 색이 리터럴인가 | 주석 제거 후 스캔 + 이빨 확인 케이스 | `aa97ab1` |
| 4 | (신설) `test_text_tiers_are_three_and_stay_apart` | — | — | 3단 글자가 세 테마에 있고 서로 벌어져 있고 4.5:1 위인가 | `aa97ab1` |
| 5 | (신설) `test_the_elevation_ladder_only_goes_up` | — | — | 위 면이 아래 면보다 어둡지 않은가 | `aa97ab1` |

⚠️ 1·2·3 은 **없애지 않았다.** 축을 좁혀 다시 세웠고, 3 에는 CLAUDE.md §P0-4 가
요구하는 「일부러 깨진 입력」 케이스를 함께 넣었다.

### 축 C. 읽기 계약 1건 (**따로 물어야 함**)

`GET /platform/projects/{project_id}/plan-conditions` 신설.

**왜 필요했나.** 계획(`published_plan_expectation`)은 조건마다 행을 갖는데,
읽기 표면이 그것을 `progress_area` 로 **묶어서** 개수만 내줬다. 커버리지는
«측정된» 조건만 담는다. 그래서 **아직 안 한 조건은 읽기 어디에도 이름이 없었고**,
실무자 화면이 「3건 남음」까지만 말하고 「그중 무엇을」은 답하지 못했다.
이 읽기는 새 저장소를 만들지 않는다 — 이미 있던 행을 그대로 내보낸다.

**무엇을 만졌나.**

```
contracts   surface_project_results.py   ROUTES · PERMISSIONS · OPERATION_QUERY
                                          RESPONSE_HEADERS · schemas · OPERATIONS
platform    CentralReadPort              read_plan_conditions
            PostgresCentralReadAdapter   칼럼 9 · 커서 · SQL 6형태
            CentralReadService           project_plan_conditions + envelope
            platform_routes              핸들러 + 레지스트리
아티팩트    docs/api/platform-api.openapi.json · packages/api-artifacts/…
FE          generated types · fetchPlanConditionsPage · test-items.tsx
```

---

## 3. 요청 순서 — 이 순서가 아니면 조용히 갈라진다

CLAUDE.md 가 명시한다: **커널 변경은 태그가 먼저** 나가야 하고, 핀은 platform
쪽 **두 파일**에 있어 한쪽만 올리면 갈라진다.

```
① contracts PR                 surface_project_results.py (+52)
     └ 자기 PR 자기 머지 가능 (required_approving_review_count: 0)
     └ 필수 검사 `lane-check` 는 서버에서 돈다 — enforce_admins: true

② 태그 릴리스  ← ⚠️ 소유자에게 «따로» 묻는 유일한 항목
     kernel-v0.5.5        (subdirectory: packages/fcc-test-kernel)
     v0.1.27              (루트)  ← 루트 아티팩트도 바뀌면 함께

③ platform 핀 갱신             requirements-central.txt  두 줄
                               pyproject.toml            (해당 시)

④ platform PR                  백엔드 읽기 + UI + 봉인 5건
     └ `gh pr merge --merge`   ⚠️ --squash 금지 (17항목 자가점검이 사라진다)
```

⚠️ ②를 건너뛰고 ④만 머지하면 CI 의 platform 이 **아직 그 surface 가 없는**
커널로 빌드되어 라우트가 사라진다. 로컬에서는 설치본을 직접 갈아 끼워 돌고 있어
그 사실이 보이지 않는다 — 이 문서가 그것을 적어 두는 이유다.

---

## 4. 소유자에게 물을 것 — 세 문장

> **1. 태그를 내 주시겠습니까?**
> `fcc-test-contracts` 에 커널 읽기 표면 한 건(`list_project_plan_conditions`)을
> 추가했습니다. `kernel-v0.5.5` + (루트 아티팩트가 바뀌면) `v0.1.27` 이 필요하고,
> 그 뒤에 platform 핀 두 파일을 올리겠습니다.
>
> **2. 봉인 검사 5건을 고친 것을 봐 주시겠습니까?**
> 셋은 값이 낡아 «고친 쪽»을 위반이라 부르던 것이고(라이트 표면 · 테마 개수 ·
> 주석 속 hex), 둘은 새로 세운 것입니다(3단 글자 · elevation 방향).
> 없앤 것은 없고 축을 좁혔습니다.
>
> **3. UI 는 보고만 드립니다.**
> 허락하신 범위 안이고 되돌리기는 브랜치 하나입니다.

---

## 5. 지금 이 기계의 «갈라진» 상태 — 넘기기 전에 정리할 것

| 항목 | 상태 | 정리 방법 |
|---|---|---|
| 설치 커널 vs 핀 | `kernel-v0.5.4` 핀 · 설치본엔 새 surface | ②③ 이후 `pip install -r requirements-central.txt` |
| `runtime-config.{dev.json,js}` | 포트 5173→5174 (이 기계 사정) | **커밋하지 않는다** |
| Keycloak `fcc-dev` | unmanaged attribute 허용 + `employee_id` 클레임 매퍼 | dev 전용. 운영 IdP 는 별도 결정 |
| 시드 데이터 | 시험항목 9종 · 가상 사번 4명 | 확인용. 운영 데이터 아님 |

---

## 6. 아직 지불하지 않은 것 (숨기지 않는다)

- `scripts/lane_check.py` **미실행** — 이 기계에 platform venv 가 없다.
  즉 「이름 집합 판정을 통과했다」고 말할 수 없다.
- `tests/test_frontend_visual_language.py` 가 **9건 빨갛다.** 이 레인의 선언된
  실패는 **0** 인데 지금 9다. ⚠️ 그 9건은 **어제 커밋(`aa97ab1`)이 남긴 것**이고
  오늘 작업이 만든 것이 아니다(stash 대조로 확인). 고칠지 기준선에 올릴지는
  별건이며, 올린다면 **T2** 라 `Debt-Accepted-By:` 가 필요하다.
- 새 프리미티브(`NavIcon` · `StackedTrend` · `ItemTable` · `DonutProgress`)와
  새 라우트 둘의 **렌더 테스트가 없다.** `plan.md §2` 가 요구한다.
- 시각 골든 미갱신 · `bundle-budget.json` 미재측정.
- `intent/flowdeck-console-ui/spec.md` §7 의 열린 질문 4개가 여전히 `→ 답( ):`
  (T3 가 계속 red).
- **축 C 의 `intent/` 삼종 세트가 아직 없다.** 읽기 계약 신설은 그것을 요구한다
  (`intent/plan-conditions-read/`). ①을 올리기 전에 써야 한다.

---

## 7. 다음 행동 (이 문서 이후)

1. `intent/plan-conditions-read/{intent,spec,plan}.md` 초안 작성 — 사람은
   `Status: accepted` 만 적는다.
2. 오늘 작업을 **두 커밋**으로 나눠 로컬 커밋:
   `feat(platform): 계획 조건 읽기` / `feat(web): 실무자 콘솔`
   (각각 17항목 자가점검 + 명시 파일 add)
3. 소유자에게 §4 의 세 문장 전달. **푸시는 그 답을 받은 뒤.**
