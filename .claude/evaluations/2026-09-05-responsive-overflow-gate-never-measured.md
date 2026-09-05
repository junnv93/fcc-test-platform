# 오버플로 게이트는 「통과한 적 없음」이 아니라 «잰 적이 없다» (2026-09-05)

## Why — 무엇이 틀렸나

`apps/web/tests/e2e/responsive-layout.spec.ts` 의
「every route fits the document at N px」 6건이 빨갛다. 이름만 보면 **오버플로가
있다**는 뜻이지만, 실패 지점은 오버플로가 아니라 **전제**였다.

스윕은 라우트마다 셸 nav 를 「청크가 마운트됐다」의 표지로 기다린다:

```ts
await expect(page.getByRole('navigation', { name: '메뉴' })).toHaveCount(1, …);
```

`ROUTES` 의 16번째 `/login` 과 17번째 `/change-password` 는 **셸 밖**이다 —
`app.tsx` 에서 `/`(=`AppLayout`)의 자식이 아니라 **형제**로 등록된다. 그러니
그 랜드마크는 0개고, 스윕은 15초를 기다린 뒤 죽는다.

> ⚠️ **스펙이 그 사실을 자기 주석에 이미 적고 있었다** — 그 두 줄 바로 위에
> `"Outside the shell, but still addresses an operator sees at every width"`.
> **주석은 맞았고 단언이 틀렸다.** 같은 파일 안에서 서로를 반증하는 두 진술이
> 나란히 있었고, 게이트가 빨갛다는 사실이 그 모순을 가렸다.

### 무엇이 관측되지 않았나 — 실측 2026-09-05

| | |
|---|---|
| `documentOverflow()` 실행 횟수 | **0** (여섯 폭 전부) |
| 어느 폭에서도 측정된 적 없는 라우트 | `/inventory` · `/membership` · `/providers` · `/chambers` (목록에서 `/login` 뒤) |

**빨간 게이트는 꺼진 게이트다.** 「통과한 적이 없다」와 「잰 적이 없다」는 같은
색으로 보이지만 다른 사실이고, 후자는 *아무것도 지키지 않는다*.

## ⚠️ 첫 가설이 틀렸다 — 권한이 아니었다

같은 파일의 ready-structure 는 `injectAuthenticatedSession(page, { permissions:
RESPONSIVE_PERMISSIONS })` 를 부르는데 이 스윕은 인자 없이 부른다. 그럴듯한
진단이지만 **실측이 기각했다**: 기본 권한(`TEST_OPERATOR_PERMISSIONS`,
`platform:admin`·`test_plan:read` 없음)으로도 셸 안 라우트 **19개 전부**가
`nav메뉴=1` 을 낸다. `/test-plans` 도 `/membership` 도 그렇다.

셸 nav 는 권한과 무관하게 렌더된다. 인자 차이는 ready-structure 가 **내용**을
렌더하려고 필요한 것이지 이 스윕의 표지와 무관하다.

## What — 표지를 구조 사실로 가른다

**단일 표지는 없다** (실측, 폭 390px · 기본 권한):

| 라우트 | `<main>` | `<h1>` |
|---|---|---|
| 대부분 | 1 | 1 |
| `/equipment-lists` · `/reference-data` | **2** | 1 |
| `/test-plans` | **0** | **2** |

그래서 표지는 **라우트가 셸 안이냐**로 갈린다 — 셸 안이면 셸 nav, 셸 밖이면 그
라우트 자신의 `<main>`(`login.tsx` · `change-password.tsx` 둘 다 렌더한다).

### 분류를 손으로 유지하지 않는다

`SHELL_LESS_ROUTES` 를 사람이 관리하면 다음에 셸 밖 라우트가 하나 더 등록될 때
**같은 일이 조용히 되풀이된다**. 그래서 라우터에서 파생한 값과 대조하는 봉인을
더했다:

`tests/test_frontend_visual_language.py::TestResponsiveRouteCoverage::test_shell_less_routes_match_the_router`

셸 밖 = `RouteEntry.top_level`(셸의 자식이 아니라 형제) − 셸 자신(`path == '/'`)
− 기록된 제외. 어긋나면 e2e 가 「오버플로 게이트가 빨갛다」고 **오도하는 대신**
그 봉인이 「전제가 틀렸다」고 말한다.

## How — 전제를 고치니 그 아래가 드러났다

스윕이 `/login` 을 지나가자 이번엔 Playwright 의 **테스트당 30초 기본 예산**을
넘겼다. 이 테스트 **하나**가 라우트 21개를 직렬로 걷고, 라우트당 ~2.5초가
든다(≈53초).

> 전에는 16번째에서 예산 안에 죽었으므로 **예산 부족은 관측될 수 없었다.**
> 결함 둘이 겹쳐 있었고 위의 것이 아래를 가리고 있었다.

예산을 목록 길이에서 파생시킨다 — `test.setTimeout(ROUTES.length * 12_000)`.

⚠️ **이것은 게이트를 느슨하게 하는 것이 아니다.** 진짜 행(hang) 방어는 걸음마다
이미 걸려 있고(`navigationTimeout: 15s` + 표지 `toHaveCount` 15s), 바깥 예산은
그 걸음들의 **합**이어야 하므로 상수가 아니라 걸음 수에서 나와야 한다. 고친 뒤
통과 시간이 **26~28초**로 옛 30초에 아슬아슬했다 — **표지만 고쳤다면 곧바로
flaky 게이트가 됐을 것이다.**

## Verification — 그래서 오버플로는 실제로 있었나

**없다.** 게이트가 처음으로 실제 측정을 했고, 재 보니 깨끗했다.

| | 전 | 후 |
|---|---|---|
| `-g "every route fits the document"` | 6 failed | **6 passed** |
| `responsive-layout.spec.ts` 전체 | 55 passed / 6 failed | **61 passed** |
| `tests/test_frontend_visual_language.py` | — | 137 passed / 2 skipped |

라우트 21개 × 폭 6개(390/640/768/1024/1280/1440) 전부
`scrollWidth - clientWidth == 0`.

시험 환경: 전용 워크트리(`origin/main` = `498e3a2`), 전용 venv 에 핀 태그
설치(`fcc-test-contracts 0.1.21` · `fcc-test-kernel 0.5.0`) — `PYTHONPATH`
우회 없음. `npm run codegen` 선행(생성물 `src/api/generated/*.ts` 는 gitignore).

## 후속 — 이 작업이 드러낸 것 (여기서 고치지 않음)

**`/test-plans` 를 `test_plan:read` 없이 열면 `<main>` 랜드마크가 0개다.**

셸은 이 라우트가 자기 main 을 소유한다고 보고
(`_layout.ROUTES_WITH_OWN_MAIN_LANDMARK`) 만들어 주지 않는데, 권한 거절 화면은
그것을 렌더하지 않는다. 즉 **권한 없는 시험원에게 이 화면은 main 랜드마크가
없다.**

ready-structure 매트릭스는 `RESPONSIVE_PERMISSIONS`(= `test_plan:read` 포함)로
들어가므로 이 상태를 **볼 수 없다** — 권한 있는 경로만 재는 게이트는 권한 없는
경로의 접근성을 말해 주지 않는다. 접근성 축이고 이 스윕의 소유가 아니라 별건으로
남긴다.
