# 새 판이 나온 것을 알려 주는 장치가 없었다 (2026-09-06)

## Why

실측 2026-09-06 — 같은 규약을 쓰기로 한 세 곳의 판번호가 전부 달랐다:

    규약 레인 최신    v0.1.24
    계측장비 저장소   v0.1.23   (1판 뒤)
    이 상자           v0.1.22   (2판 뒤)

**아무도 틀리지 않았고 아무 검사도 실패하지 않았는데 벌어졌다.** 새 판이 나온 사실을
알려 주는 장치가 없기 때문이다. 사람이 기억해야 하고, 기억은 조용히 실패한다.

## ⚠️ 기본값으로는 안 됐다 — 문서가 아니라 실행으로 판정했다

`renovate 42.99.0` 을 `--platform=local --dry-run=lookup` 으로 이 트리에 돌렸다.
기본 관리자는 의존 **54개**를 추출하면서 **우리가 필요한 둘만** 건너뛴다:

    Dependency fcc-test-contracts has unsupported/unversioned value
      @ git+https://github.com/junnv93/fcc-test-contracts@v0.1.22 (versioning=pep440)
    Skipping fcc-test-contracts because no currentDigest or pinDigests

PEP 508 직접 URL 참조(`name @ git+…@tag`)는 pip/poetry 관리자의 versioning 축에
들어오지 않는다. **「봇을 켜면 된다」가 틀렸다는 것을 실행이 말했다.**

사용자 정의(정규식) 관리자를 두자 실제 갱신이 만들어졌다:

| 핀 | 현재 | 봇 제안 |
|---|---|---|
| `fcc-test-contracts` | `v0.1.22` | **`v0.1.24`** ← 위에서 잰 바로 그 드리프트 |
| `fcc-test-kernel` | `kernel-v0.5.0` | 갱신 없음 (이미 최신) |
| `fcc-test-platform` | `v0.1.8` | **`v0.1.10`** |

### 규칙이 셋인 이유

한 저장소에 **태그 계열이 둘**이다 — `v0.1.24`(계약)와 `kernel-v0.5.0`(커널)이 같은
저장소에 산다. 하나의 규칙으로 두 계열을 집으면 `extractVersion` 이 한쪽에서 틀린
판번호를 뽑는다. 세 번째는 이 상자 자신의 중앙 배포 핀이다(아래).

## ⚠️ 내가 저지른 사고 — 유효하지 않은 설정은 「없는 것」보다 나쁘다

초판이 주석을 담으려고 `_doc` 이라는 임의 키를 최상위에 두었다. Renovate 는 그것을
**알 수 없는 옵션**으로 보고 **설정 전체를 거부**했고, 실행이
`result: "config-validation"` 으로 끝났다 — **핀은 하나도 조회되지 않았다.**

> 🔴 그런데 내가 **그 직전에 쓴 검사들은 전부 초록이었다.** 정규식은 멀쩡히 핀을
> 집었기 때문이다. **「정규식이 맞다」와 「봇이 이 설정을 받아들인다」는 다른 명제**인데
> 나는 앞의 것만 재고 있었다.

`description`(Renovate 가 아는 키)으로 옮기고, 진짜 검증기로 확인했다 —
`renovate-config-validator` → *"Config validated successfully"*.
그리고 **그 사고를 잡는 축을 검사에 더했다.**

## What — 검사의 네 축

`tests/test_renovate_covers_every_lane_pin.py`

| 축 | 명제 |
|---|---|
| **설정 유효성** | 최상위에 모르는 키가 없는가 · `_` 로 시작하는 임의 키가 없는가 |
| 비공허성 | customManagers 가 있는가 · 이 트리에 git 핀이 있는가 |
| **모든 핀이 보이는가** | 모든 git 핀이 적어도 하나의 규칙에 잡히는가 |
| **낡은 규칙이 없는가** | 아무 핀에도 안 걸리는 규칙이 있는가 (반대 방향) |

⚠️ **정직한 한계 두 개를 검사 안에 적었다.**
① Renovate 의 정규식 엔진은 JavaScript 이고 이 검사는 Python `re` 로 평가한다. 이름
있는 그룹만 옮기면 이 패턴 부류에서 두 엔진의 판정은 같지만, 룩비하인드 같은 갈라지는
문법이 들어오는 날 거짓 초록이 된다 — `test_no_pattern_uses_engine_specific_syntax` 가
그 날을 잡는다.
② 설정 유효성의 권위 있는 판정은 `renovate-config-validator` 다. 그것은 renovate 패키지
전체를 요구하므로 시험 의존에 두지 않았고, 대신 **좁은 대리**를 두었다 — 새 키를 더하면
정당하더라도 빨개진다. **틀리는 방향이 안전하다**: 사람을 진짜 검증기로 보낸다.

## ⭐ 검사가 첫 실행에서 진짜 공백을 찾았다

규칙 둘로 돌리자 곧바로 빨개졌다 — `requirements-central.txt` 의
`fcc-test-platform @ …@v0.1.8` 이 어느 규칙에도 안 잡혔다.

이력을 재 보니 그 핀은 `989c04e` 에서 `v0.1.8` 로 박힌 뒤 **한 번도 움직이지 않았고**
그 사이 `v0.1.9` · `v0.1.10` 이 발행됐다. 「중앙은 마지막으로 검증된 판을 쓴다」는
의도적 지연인지, 그냥 잊힌 것인지 **기록이 없다.**

⚠️ 그래서 **판정하지 않고 규칙을 넣었다.** 봇 PR 은 변경이 아니라 **제안**이므로 그
질문을 사람 앞에 놓는다. 의도적 지연이라면 규칙을 지우고 그 이유를 `description` 에
적으면 되고, 그러면 다음 사람이 같은 자리에서 다시 묻지 않는다.

## Verification

* `tests/test_renovate_covers_every_lane_pin.py` **10 passed**
* 네 축 전부 반증: 임의 키 되넣기 → 빨강 2건 / 규칙 하나 삭제 → 빨강 2건 /
  새 레인 핀 추가 → 빨강 / 핀 삭제로 규칙만 남김 → 빨강. 되돌리면 전부 초록
* `renovate-config-validator` **Config validated successfully**
* Renovate dry-run 재실행: 설정 오류 0 · 정규식 관리자 **fileCount 5 · depCount 5**
  (계약×2파일 + 커널×2파일 + 상자×1파일) · 위 표대로 갱신 제안
* `lane_check` **EXIT=0**

## 후속

* ⚠️ **봇을 «켜는» 것은 이 커밋에 없다.** GitHub App 설치는 저장소 설정이라 **승인
  지점**이다. 이 설정은 켜기 전까지 아무 일도 하지 않고, 켜는 순간 위 세 핀을 본다.
* 켜는 웨이브에서 **진짜 검증기를 CI 에 넣어라**:
  `npx --yes --package renovate renovate-config-validator renovate.json`
* ⚠️ **전량 실행 flake 를 이 세션에서 둘 봤다** —
  `test_a_rotation_flood_does_not_resurrect_a_logged_out_refresh_token`(2일간 3회)과
  `test_double_release_is_pairing_error`(1회). 둘 다 단독 실행에서는 매번 통과한다.
  **flaky 게이트는 사람에게 빨강을 무시하도록 훈련시킨다** — 별건이지만 이름을 남긴다.
