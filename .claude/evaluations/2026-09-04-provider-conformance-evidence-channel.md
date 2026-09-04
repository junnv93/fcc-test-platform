# 적합성 증거 채널 — 두 상자가 동시에 초록이던 자리를 red 로 (2026-09-04)

## Why

운영자 판정 2026-08-31 「나」안은 *provider 가 자기 레포에서 검사하고 중앙은 결과만
받는다* 까지만 정했다. **결과가 무엇인지**는 정하지 않았고, `OPEN-QUESTIONS.md` §1 이
남긴 셋 중 셋째 — *「결과가 없거나 낡았을 때 무엇이 red 가 되는가」* — 가 핵심이었다.

그것이 없으면 **결과가 오지 않은 상태와 결과가 통과한 상태가 같은 초록**이고, 그 축은
문서 표현대로 *"아무도 내지 않는 숙제"* 가 된다.

⚠️ 그리고 그 침묵은 가설이 아니었다. 실측 2026-09-04: 이 트리의 레지스트리가 어느
트리에도 없는 아티팩트를 가리키는데 **계약 레인 체커를 손으로 물려야만** 보였다
(`compatible:false` · `providers:[]` · exit 2). 두 레인의 pytest 는 전부 초록이었다.
`check_headless_provider_registry.py` 자신의 docstring 이 이미 *"this file spans two
repositories … 아무도 그것을 red 로 바꾸지 않았다"* 고 적어 둔 그 자리다.

## What

운영자 지시로 §6.5 의 설계 셋을 판정했다(판정문 §6.6). 업계 형태는
**conformance attestation** 이다 — Pact 의 `can-i-deploy`(검증 결과를 계약 버전에
묶는다), in-toto/SLSA 의 `{subject, predicate}`, OCI·CNCF 적합성 프로그램의
「스펙이 개정되면 적합성이 만료된다」.

1. **무엇을 받는가** — 적합성 증거 문서(계약 레인 신규 스키마). 아티팩트도(안 「다」)
   불리언도(낡음을 잴 축이 없다) 아니다.
2. **누가 언제** — provider 는 자기 CI 에서(이미 `provider_onboarding.md` O-7 로
   배정돼 있었다), 중앙은 등재 시 + **게이트가 돌 때마다**.
3. **없거나 낡으면** — **fail-closed.** 증거 없는 등재 provider 는 「미지」가 아니라
   **부적합**이다.

이 커밋(platform)이 담은 것은 그 중앙 쪽 얼굴이다.

## How

- `tests/test_provider_registry_artifacts_resolve_cross_lane.py` — 등재된
  `contract_artifact` 가 **발행 레인에 실재하는가**. `phase38` 의 유보를 넘지 않는다:
  그 문장은 `resolve_repo_artifact`(내 트리)에 대해 참이고, 이쪽은
  `resolve_dependency_artifact` 로 **묻는 트리를 바꾼다**.
- `tests/test_provider_conformance_evidence.py` — 세 실패를 **이름으로** 가른다
  (`evidence missing` · `stale` · `non-conformant`) + 비-공허성 팔 둘 + grandfather
  래칫. 검증은 **파생이지 일정이 아니다** — 게이트가 SSOT digest 를 매번 다시 계산하므로
  계약이 바뀌면 기존 증거가 다음 실행에서 자동으로 낡는다. 만료일을 두면 그것은 digest
  옆의 두 번째 의견이고, 계약이 일정 밖에서 바뀌는 날 둘이 어긋난다.
- 계약 핀 `v0.1.12` → **`v0.1.13`** — `contract_identity_digest` 가 거기 있다.
  ⚠️ 정규형을 이 레인에서 다시 구현하지 않는다. 계약 레인이 소유하고, 체커가 비교 전
  양쪽을 줄이는 것과 **같은 함수**를 쓴다.

## Verification

교차 레인 검사 — 다섯 조합:

| 작업트리 | 계약 레인 | 결과 |
|---|---|---|
| 깨진 | 트리 | 1 failed, 2 passed |
| 깨진 | 휠 | 1 failed, 2 passed |
| 깨진 | 없음 | 1 error (**수집 에러 — skip 아님**) |
| 깨끗 | 트리 · 휠 | 3 passed |

증거 게이트 — 합성 증거로 **세 팔을 전부 발화**시켰다:

```
(a) 올바른 증거              → 7 passed
(b) contract_identity 낡음   → evidence stale
(c) subject 불일치           → evidence non-conformant
(d) result.compatible=false  → evidence non-conformant
(e) 증거 없음                → evidence missing
```

⚠️ **초판이 틀렸고 재서 고쳤다.** *「휠로 설치되면 트리가 없어 축이 못 돈다」* 고
docstring 에 적어 놓고 전제를 확인하지 않았다. 계약 휠은 `artifacts/` 를 패키지 안에
실어 보내므로 **축은 두 형상 모두에서 돈다**. 진짜 형태는 *「없는 아티팩트가 형상에
따라 다른 모양으로 나타난다」* 였다. `check-axis-blindness.md` §*「그 차이는 X 때문이다」*
그대로 — 설명이 요구하는 전제를 적고 확인해서 나온 정정이다.

**독립 확증**(KC 레인): byte 로 다른 두 파일이 정규형 digest 동일(`82129f64…`).
저자 자신의 검증보다 강한 근거다.

## 후속

1. 🔴 **위조는 안 닫힌다.** 서명 없는 증거는 올바른 digest 를 손으로 적어 만들 수 있다.
   닫은 것은 **부재**와 **낡음**뿐이고 스키마 `notes` 가 그렇게 적는다. 서명은 키 보관
   주체가 정해져야 한다.
2. 🔴 **`provider_onboarding.md` 가 두 벌이고 갈렸다.** 실행을 강제하는
   `test_provider_onboarding_package.py` 는 **모노레포에만** 있고 모노레포 사본을 읽는다.
   §6.3 이 체커에 대해 적은 *"spans two repositories"* 와 같은 형태이고, 그때 수리는
   **체커를 contracts 로 옮기는 것**이었다. 대칭으로 이 테스트도 옮겨야 한다.
3. 🔴 계약 레인 `pyproject.toml` 주석의 *「npm 배포물과 상등」* 이 이미 거짓이고
   (python `0.1.13` vs npm `0.1.4`) **검사가 0건**이다.
4. grandfather 목록 셋(`fcc-unlicensed-conducted`·`fcc-mmwave-headless`·
   `fcc-licensed-headless`)은 **줄어들기만** 해야 한다. 래칫이 그것을 지킨다.
