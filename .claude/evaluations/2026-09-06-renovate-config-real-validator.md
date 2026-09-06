# 봇 설정을 «봇 자신의 검증기»로 읽힌다 — 그리고 그 검증기를 부르는 법이 둘이다

측정 2026-09-06 · 기준 `main` = `4ebd580` · 세션 fcc-delivery-final (a241f0f7)

## Why — 왜 눈이 하나 더 필요한가

PR #99 가 `renovate.json` 과 그 봉인
(`tests/test_renovate_covers_every_lane_pin.py`)을 들여왔다. 그 봉인은 「이 트리의
git 핀이 customManager 정규식에 잡히는가」를 본다 — 그리고 그것은 Renovate 의 정규식
의미를 **Python `re` 로 재구현**해서 본다. 그 재구현은 **설정이 renovate 자신에게
유효한가**는 묻지 못한다.

⚠️ 그 축이 왜 중요한가: **Renovate 는 모르는 키가 하나라도 있으면 설정 전체를
거부한다.** 그러면 봇은 붙어 있는데 아무 PR 도 만들지 않고, 화면에서 그것은
「갱신할 것이 없다」와 **구별되지 않는다.** 이 저장소가 여러 번 이름 붙인
«공허함은 초록과 같은 모양이다» 형태다.

## What — 그리고 이 작업이 실제로 잡은 함정

목표 지시문이 준 명령은 이것이었다:

    npx --yes --package renovate renovate-config-validator renovate.json

돌려 보니 이렇게 답했다:

     INFO: Validating renovate.json as global config     ← ⚠️ 전역 설정 축

**파일명을 인자로 주면 검증기가 그 파일을 «전역(global) 설정»으로 읽는다.** 전역
설정과 저장소 설정은 허용 키가 다르다. 인자 없이 부르면 검증기가 저장소 설정 파일을
스스로 찾는다:

    $ npx --yes --package renovate@42.99.0 renovate-config-validator
     INFO: Validating renovate.json                      ← 저장소 설정 축
     INFO: Config validated successfully

⚠️ **오늘의 `renovate.json` 은 두 축 모두에서 통과한다.** 즉 오늘은 증상이 없다.
그래서 더더욱 봉인이 필요하다 — 증상 없는 틀린 축은 스스로 드러나지 않고, 다음
사람이 「초록이었다」를 근거로 그 형태를 굳힌다.

## How — 두 자리로 나눠 배선했다

| 자리 | 무엇을 묻는가 | 성질 |
|---|---|---|
| `.github/workflows/renovate-config.yml` | 그 축으로 **실제로 통과하는가** | 네트워크 · 권위 |
| `tests/test_renovate_config_is_validated_as_a_repository_config.py` | 게이트가 **올바른 축으로 배선돼 있는가** | 오프라인 · 결정적 |

레인 게이트(`lane_check`) 안에서 `npx` 를 부르지 않은 이유가 그 분담이다. 그러면
pre-push 가 네트워크에 의존하게 되고, 바깥 사정으로 팀원이 막힌다.

봉인이 보는 축 넷:

1. **대상이 있는가** — `renovate.json` 부재 시 나머지가 공허해진다
2. **검증기를 부르는 워크플로가 있는가**
3. **인자를 주지 않는가** — 위 함정
4. **판이 고정돼 있는가** — `renovate@latest` 는 스키마가 바뀌는 날 우리 커밋과
   무관하게 빨개진다. 그런 게이트는 사람이 끈다
5. **설정이 바뀔 때 실제로 켜지는가** — `paths` 필터에 `renovate.json` 이 있는지

대상 워크플로는 손으로 나열하지 않고 **선언에서 파생한다**(`run:` 본문에
`renovate-config-validator` 가 나오는 잡 전부). 잡이 다른 파일로 옮겨 가도 따라간다.

## Verification — 실측

* 검증기 두 형태를 **실제로 돌려** 축 차이를 관측했다(위 인용은 실제 출력이다).
* 새 검사 8건 통과.
* **주입으로 이빨을 확인했다.** 실제 워크플로에:
  - 파일명 인자를 넣자 → `test_no_invocation_passes_a_config_filename` 이 인자
    이름(`['renovate.json']`)을 말하며 빨개졌다
  - `renovate@latest` 로 바꾸자 → `test_no_invocation_floats_the_renovate_version`
    도 함께 빨개졌다(2 failed)
  - 복원하니 8/8 초록
* 워크플로 YAML 파싱 확인. 새 워크플로의 `concurrency` 는 처음부터 커밋별 그룹
  형태로 썼다(근거: `2026-09-06-ci-verification-record-per-commit.md`).
* 레인 전량: `lane_check` EXIT=0.

## 후속

1. ⚠️ **이 게이트는 「설정이 유효한가」만 답한다.** 「봇이 실제로 붙어 있는가」는
   답하지 못한다 — GitHub App 설치는 저장소 바깥 상태이고 **사용자 승인 지점**이다.
   설치 전까지 `renovate.json` 은 아무 일도 하지 않는다.
2. 봇을 켜면 첫 PR 로 나올 것: `fcc-test-contracts` v0.1.22 → v0.1.24.
   `fcc-test-kernel` 은 `kernel-v0.5.0` 이 최신이라 나올 것이 없다(실측).
3. self-pin(`fcc-test-platform@v0.1.8`)은 판정이 필요하다 — 아래 별항.

### self-pin 의 상태 (2026-09-06 실측)

    선언        requirements-central.txt:  fcc-test-platform@v0.1.8
    발행된 태그  v0.1.9 · v0.1.10
    그런데       PR #90 이 v0.1.10 을 **「판올림 커밋 없이 태그만 붙어 못 쓴다」**고
                판정하고 0.1.11 을 준비했다

즉 「자기 핀이 안 움직인다」는 관측은 맞지만, **지금 올릴 곳이 마땅치 않다**는 것이
그 이유의 일부다. 봇이 켜지면 이 질문을 PR 로 사람 앞에 놓는다 — 그것이 이 규칙을
남겨 두는 값이다. 의도적 지연이라면 그때 규칙을 지우고 이유를 적으면 된다.
⚠️ 이 문단은 **관측이지 판정이 아니다.** v0.1.8 에 머문 것이 의도인지 방치인지를
말하는 기록은 이 레인 어디에도 없다(찾지 못했다).
