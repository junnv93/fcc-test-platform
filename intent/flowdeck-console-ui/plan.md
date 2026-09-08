# Plan: 시험 현황 화면을 «운영 콘솔» 형태로 바꾼다

Intent: ./intent.md
Spec: ./spec.md
Engineer: younhs21-dexter
Date: 2026-09-08
Status: draft
Slug: flowdeck-console-ui
Branch: feature/flowdeck-console-ui

## 1. 바뀌는 파일 — 순서대로

| # | 파일 | 무엇을 | 왜 이 순서 | 대응 요구사항 |
|---|---|---|---|---|
| 1 | `apps/web/src/styles/global.css` | primitive 팔레트 교체 + `--font-sans`/`--font-mono` 스택 | **모든 것의 바닥.** 여기가 먼저 서야 아래 단계의 화면을 눈으로 판정할 수 있다 | R1, R2, R3 |
| 2 | `apps/web/package.json` | `@fontsource/schibsted-grotesk` · `@fontsource/dm-mono` 추가 | 1의 `--font-sans` 가 가리키는 실체. 없으면 폴백으로 조용히 렌더돼 «적용됐는지» 구별이 안 된다 | R2 |
| 3 | `apps/web/src/main.tsx` | 폰트 CSS import | 2를 실제로 싣는 자리 | R2 |
| 4 | `apps/web/src/routes/_layout.tsx` | 사이드바 카운트 뱃지 · 현황 카드 · 상단바(브레드크럼/검색/테마) | 셸이 서야 개별 화면의 여백과 폭이 정해진다 | R4, R5, R6 |
| 5 | `apps/web/src/ui/CommandPalette.tsx` | **신규** — `⌘K` 탐색 팔레트 | 4가 부르는 대상 | R6 |
| 6 | `apps/web/src/ui/PageHeader.tsx` | eyebrow 슬롯 + 우측 동작 슬롯 | 4의 셸 안에서 각 화면의 머리 모양 | R7 |
| 7 | `apps/web/src/ui/StatTile.tsx` | **신규** — 지표 + 전일 대비(화살표·색·문구) | 8이 쓰는 부품 | R8 |
| 8 | `apps/web/src/ui/TrendChart.tsx` | **신규** — 인라인 SVG 면적 추이 | 〃 | R9 |
| 9 | `apps/web/src/ui/HealthBars.tsx` | **신규** — 성공률 막대(나쁜 것 위로) | 〃 | R10 |
| 10 | `apps/web/src/ui/index.ts` | 7~9 export | 배럴이 갱신돼야 10이 import 한다 | R7~R10 |
| 11 | `apps/web/src/routes/overview.tsx` | 홈을 콘솔로 재구성 + 선택 행 → 우측 상세 패널 | 부품(7~9)이 있어야 조립된다 | R8~R12 |
| 12 | `apps/web/src/locales/ko.json` · `en.json` | 새 문구 **양쪽 동시** | 11이 참조하는 키. 한쪽만 넣으면 parity red | R4~R12 |
| 13 | `apps/web/tests/e2e/console-home.spec.ts` | **신규** — §6 판정 스펙 | 대상이 완성된 뒤에 잰다 | R8~R12 |
| 14 | `apps/web/bundle-budget.json` | `measuredGzipBytes` 재측정 값 갱신 | 폰트가 실린 **뒤에** 재야 의미가 있다 | 제약(번들) |
| 15 | `apps/web/tests/e2e/ui-visual-regression.spec.ts-snapshots/*.png` | 골든 재생성 | **맨 마지막.** 화면이 확정되기 전에 찍으면 두 번 찍는다 | 제약(골든) |

⚠️ **15는 사람이 눈으로 대조한 뒤에만 커밋한다**(spec §4). 골든 교체는 검사를
끄는 것과 형태가 같다.

## 2. 무엇으로 검증하나

| 요구사항 | 검사 | 지금 이 검사는 |
|---|---|---|
| R1 | `tests/design-token-conformance.test.ts` (대비 32건) | **이미 있다** — 값 교체 후 초록 유지가 판정 |
| R1 | `tests/test_fe_phase1_ui_foundation.py` (토큰 «이름» 봉인) | **이미 있다** — 이름을 늘리지 않았음이 판정 |
| R2 | `tests/e2e/web-font-loading.spec.ts` | **이미 있다** — 확장 필요(새 서체 2종이 실제로 로드되는가) |
| R2 | `tests/test_apps_web_scaffold.py::TestDevCspSeparation` | **이미 있다** — CDN 을 열지 않았음이 판정 |
| R3 | `console-home.spec.ts` — 지표 셀의 `font-variant-numeric` 단언 | **새로 만든다** |
| R4, R5 | `console-home.spec.ts` — 뱃지 없음/있음 두 상태 | **새로 만든다** |
| R6 | `console-home.spec.ts` — `Ctrl+K` 로 열리고 **Esc 로 닫힌다** | **새로 만든다** |
| R7 | `tests/layout.test.tsx` | **이미 있다** — 확장 |
| R8 | `console-home.spec.ts` — 증감 3개 각각 화살표 + 문구 존재 | **새로 만든다** |
| R9, R10 | `console-home.spec.ts` — 차트 시리즈 2개 / 막대 정렬이 오름차순 성공률 | **새로 만든다** |
| R11 | `console-home.spec.ts` — 행 선택(마우스 **및 키보드**) → 상세 패널 갱신 | **새로 만든다** |
| R12 | `console-home.spec.ts` — API 를 `route.abort()` 로 죽이고 **위젯별** 빈 상태 확인 | **새로 만든다** |
| 전체 | `tests/e2e/a11y.spec.ts` | **이미 있다** |
| 전체 | `tests/i18n-parity.test.ts` | **이미 있다** |

⚠️ **R6 의 Esc 를 반드시 잰다.** 참고한 컨셉 파일(`flowdeck.html`)이 정확히
여기서 깨져 있었다 — `<div class="scrim" hidden>` 인데 `.scrim { display: grid }`
가 `[hidden]` 을 덮어써서 팔레트가 **열린 채로 고정**됐다. 실측으로 확인했다.
같은 결함을 옮겨오지 않으려면 「열린다」가 아니라 **「닫힌다」를 재야** 한다.

## 3. 게이트 통과 계획

* `scripts/lane_check.py` — 실패 이름 집합이 `delivered_test_run_baseline.json` 과
  같아야 한다. 이 레인의 선언은 **0건**이고, 그대로 **0건을 유지**하는 것이 계획이다.
* 새 실패를 기준선에 **추가하지 않는다.** §4 `Debt-Accepted-By` 는 (없음)이다.
* 커밋 메시지 17항목 자가점검(`githooks/commit-msg`)이 붙는다.
* `npm run codegen:check` 는 손대지 않았으므로 초록이어야 한다 — 빨개지면
  그것은 이 계획 밖의 무언가를 건드렸다는 신호다.

## 4. 승인 지점 (사람이 눌러야 하는 것)

* **없음.** 태그 push · 컨테이너 재기동 · DB 마이그레이션 · 배포 · GitHub 설정
  변경 어느 것도 이 계획에 없다.
* 다만 **사람의 «판정»이 필요한 자리가 둘** 있다(승인 버튼이 아니라 눈):
  1. 시각 골든 15의 전후 대조.
  2. spec §7 의 열린 질문 4개 — `→ 답( ):` 이 비어 있으면 **방아쇠 T3** 가 발화해
     검사가 초록이 되지 않는다. 코드보다 **먼저** 답이 적혀야 한다.

## 5. 되돌리는 법

* **전부 되돌릴 수 있다.** 이 계획은 프론트엔드 표시층만 만지고 DB·API·계약을
  건드리지 않는다.
* 되돌리기: 브랜치를 머지하지 않거나, 머지 후에는 revert 커밋 하나.
  `git revert -m 1 <머지 커밋>` 로 15개 파일이 함께 돌아온다.
* **부분 되돌리기도 된다.** 1(토큰)만 되돌리면 색만 원래대로 오고 레이아웃은
  남는다 — 토큰 그래프가 단방향이라 그렇다.
* 폰트만 빼려면 2·3을 되돌리고 `--font-sans` 스택에서 첫 항목을 지운다. 한글
  폴백이 그대로 받아준다.
* ⚠️ **되돌려도 남는 것 하나**: 15의 골든 PNG. revert 로 파일은 돌아오지만,
  그 사이에 다른 PR 이 골든을 만졌다면 충돌한다. 그래서 15가 맨 마지막이다.

## 6. 범위 밖으로 새는 것을 막는 선언

작업 중 발견되겠지만 **이 계획에서 하지 않을 것**:

* **나머지 24개 라우트의 재구성.** 토큰·셸은 자동으로 물려받지만, 개별 화면의
  정보 구조를 손대기 시작하면 이 PR 이 끝나지 않는다. 새 intent 로 올린다.
* **모션·트랜지션 체계.** `Radiated` 레포에 다듬어진 것이 있으므로 그것을 참고
  원본으로 삼아 반응성 작업 때 별도 intent 로 올린다(spec §2).
* **`⌘K` 팔레트의 명령 실행.**
* **`flowdeck.html` 의 `[hidden]` 결함 자체를 고치는 것.** 그 파일은 이 저장소
  밖의 참고 자료이지 납품물이 아니다. 우리는 **그 결함을 옮겨오지 않는 것**까지만
  책임진다(§2 R6 검사).
* **headless/session 레인의 데이터 붙이기.** 그 두 백엔드는 이 저장소에 없다
  (모노레포 소유). 홈의 해당 위젯은 R12 의 빈 상태로 남는다.
* **번들 예산의 `headroomFactor` 조정.** 측정값만 갱신한다. 계수를 손대는 것은
  예산 자체를 무르게 만드는 것이라 별도 판단이다.

**Scope-Extended-By**: (없음)
