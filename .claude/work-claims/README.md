# Work Claims — 이 레인의 작업 장부

여러 세션이 **한 체크아웃을 공유**하면서 각자 워크트리를 파고 일한다. 이 디렉터리는
그 세션들이 서로의 브랜치와 파일을 밟지 않게 하는 장부다. 자명하지 않은 작업을
시작하기 전에 **활성 작업 하나당 JSON 파일 하나**를 만든다.

## 왜 이 파일이 2026-09-06 에야 생겼나

⚠️ **판정기는 배송으로 왔는데 그것이 가리키는 선언이 안 왔다.**

`scripts/work_claim_status.py` 의 docstring 은 다섯 토큰이
*"exactly the ones already declared in `.claude/work-claims/README.md` — that
declaration existed before this module did"* 라고 적는다. 그 말은 **모노레포에서는
참**이다(`FCC_mobile_test_automation/.claude/work-claims/README.md`, 56줄). 그러나
이 레인에는 **스크립트만 배송되고 그 선언은 오지 않았다** — 실측: 이 저장소 이력
어디에도 `.claude/work-claims/` 가 없었다.

그래서 `githooks/pre-commit` 이 요구하는 장부가 **디렉터리째 부재**했고, 세션들이
`--force`(의도적 부재) 경로로 우회해 왔다. 이 파일은 그 선언을 **회수**한 것이지
새로 발명한 것이 아니다.

⚠️ 같은 형태가 이 저장소에 하나 더 있었다 — 설계서 §8 이 `OPERATIONS` 라는 dict 를
논하는데 그 이름은 이 레포에 없고 실체는 커널(`fcc_test_kernel`)에 산다. **코드나
문서가 이 레인에 없는 것을 가리키는지**는 볼 때마다 물어야 한다.

## 파일 하나의 형태

```json
{
  "task": "api-operation-spec-typeddict-20260906",
  "branch": "feat/api-operation-spec-typeddict-20260906",
  "owner": "fcc-delivery-final-bf",
  "status": "active",
  "base": "origin/main@39f3f0c",
  "worktree": "/tmp/claude-1000/.../scratchpad/wt-op-spec",
  "scope": [
    "fcc_test_platform/api/platform_routes.py",
    "docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md"
  ],
  "shared_outputs": ["mypy.ini"],
  "conflicts_with": ["mypy-read-adapters-20260906"],
  "sequencing": "커널 태그 → 계약 태그 → 이 레인 핀 판올림. 앞의 둘은 승인 지점."
}
```

`task` · `branch` · `status` 셋은 필수다. 나머지는 있으면 다음 세션이 덜 묻는다.

⚠️ **`branch` 는 판정기가 실제로 읽는다.** `scripts/work_claim_branch_guard.py` 가
HEAD 의 브랜치와 대조해 다르면 커밋을 거부한다 — 남의 브랜치에 커밋이 얹히는
사고를 막는 유일한 장치다. 그래서 이 필드는 장식이 아니다.

## status — 다섯 토큰이 전부다

`scripts/work_claim_status.py` 의 `ClaimStatus` 와 **상등**이다. 여섯 번째 철자를
쓰면 게이트가 빨개진다(그 모듈이 strict 로 해소한다).

| 토큰 | 뜻 | 범위를 소유하나 |
|---|---|:---:|
| `active` | 진행 중 | ✅ |
| `blocked` | 멈췄지만 범위는 아직 이 작업의 것 | ✅ |
| `review` | PR 이 열려 있고 범위는 아직 이 작업의 것 | ✅ |
| `merged` | 착지함. 정리 때 보관하거나 지워도 된다 | — |
| `abandoned` | 의도적으로 버림 | — |

「범위를 소유하나」는 `OWNS_SCOPE` 에서 **파생**된다. 호출자가 그 집합을 자기 자리에
다시 적으면 안 된다 — 그 중복이 바로 저 모듈이 고친 결함이다.

## 이 레인에서 장부를 읽는 것들

| 읽는 곳 | 무엇을 위해 |
|---|---|
| `githooks/pre-commit` | 브랜치 가드 — 남의 브랜치에 커밋이 얹히는 것을 막는다 |
| `scripts/work_claim_branch_guard.py` | 위 가드의 본체. `bind` / `unbind` 로 세션과 claim 을 묶는다 |
| `scripts/merge_readiness_guard.py` | 머지 준비 판정 |
| `scripts/hook_bypass_guard.py` | 훅 우회 감사 |
| `scripts/work_claim_status.py` | status 어휘의 SSOT |

바인딩은 `.git/fcc-work-claim` 에 있고 **커밋되지 않는다** — 워크트리마다 다르기
때문이다. 그래서 새 워크트리에서 일할 때마다 다시 `bind` 해야 한다.

```
python scripts/work_claim_branch_guard.py bind <claim 파일명에서 .json 뺀 것>
python scripts/work_claim_branch_guard.py unbind      # 작업이 끝나면
```

## 모노레포 원본과 다른 점 — 일부러 뺐다

원본에는 `plan_slugs` 절이 있다. **이 레인에서는 뺐다**: 그 필드를 읽는
`scripts/exec_plan_buckets.py` 도, 그것이 배치하는 `.claude/exec-plans/` 도 이
저장소에 **없다**(실측). 없는 것을 가리키는 문장을 새로 만드는 것은, 이 파일이
고치려는 결함을 다시 저지르는 일이다.

같은 이유로 `work_claim_status.py` 의 docstring 이 런타임 호출 지점으로 드는
`supervisor_status.py` · `supervisor_preflight.py` 도 **이 레인에 없다.** 그 문단은
모노레포를 설명하는 것이지 여기를 설명하지 않는다. 위의 「읽는 것들」 표가 이
레인의 실제 소비자다.

## 작업이 끝나면

`status` 를 `merged` 나 `abandoned` 로 바꾸고 `unbind` 한다. 파일을 지우는 것은
선택이다 — 남겨 두면 「그 파일을 누가 왜 만졌나」의 기록이 되고, 지우면 장부가
짧아진다. 어느 쪽이든 **소유가 끝났다는 사실이 파일에 적혀 있어야** 다음 세션이
그 범위를 집을 수 있다.

⚠️ 남의 claim 을 대신 닫지 마라. 상태를 모르면 그 세션에 물어라.
