# 이 사본은 무엇인가 — `fcc-test-platform`

> 이 파일은 **손으로 작성되지 않았습니다.** 배송할 때마다 모노레포의
> `scripts/stamp_delivery_provenance.py` 가 새로 생성합니다.
> 여기 고친 값은 다음 배송에서 덮어써집니다.

## 출처

| | |
|---|---|
| 원본 레포 | `https://github.com/junnv93/FCC_mobile_test_automation.git` |
| 원본 커밋 (SHA) | `4ced019a6c8ec82333a85c9201e8a1d1b7d13a52` |
| 추출 시각 (UTC) | `2026-08-30T23:02:55Z` |
| 추출 매니페스트 판번호 | `manifest_version = 2` |
| 레인 키 | `fcc-test-platform` |
| 레인 소유자 (매니페스트 선언) | Shared web/platform maintainers |

⚠️ **매니페스트의 판번호와 파일명의 `v1` 은 다른 것입니다.** 파일명
(`headless_contract_extraction_manifest.v1.json`)의 `v1` 은 **스키마 계열**이고,
위 `manifest_version = 2` 은 **그 계열 안의 판번호**입니다. 이 표가
판번호를 적는 이유는 상자의 내용을 결정한 것이 스키마 계열이 아니라 판번호이기
때문입니다.

⚠️ **원본 커밋은 이 레포에 없습니다.** git history 를 이전하지 않는다는 운영자
판정에 따라, 이 레포의 `git log` 는 배송 커밋만 갖습니다. 위 SHA 로 조회할 곳은
`https://github.com/junnv93/FCC_mobile_test_automation.git` 입니다.

## 이 상자에 실린 것 — 실측

| | |
|---:|---|
| **920** 파일 | 총 19.6 MiB (이 문서 제외 — 자기 크기는 자기 숫자에 의존하므로 셀 수 없다. 파일 수에는 이 문서가 포함된다) |
| 파일 수의 출처 | `.extraction-layout.json` **선언** — 디스크를 훑은 값이 아니다. 따라서 이 수는 **push 된 트리**와 일치하며, 검증 산출물이 흘러든 스테이징 디렉터리와는 일치하지 않을 수 있다 |
| **128** 매니페스트 entry | 이 파일들을 예약한 선언의 개수 |
| 최상위 항목 | `.extraction-layout.json` · `.github` · `.gitignore` · `CODEOWNERS` · `EXTRACTED_FROM.md` · `README.md` · `application` · `apps` · `config` · `delivered_test_run_baseline.json` · `docs` · `domain` · `fcc_test_platform` · `githooks` · `infra` · `migrations` · `pyproject.toml` · `scripts` · `tests` · `web` |

## 알려진 상태 — **당신이 깨뜨린 것이 아닙니다**

이 상자가 자기 테스트 suite 를 돌리면 **47개 노드가
실패합니다.** 그 실패들은 매니페스트
`governance.delivered_test_run_baseline` 에 **node id 이름 집합**으로 등재돼
있고 한 방향으로만 줄어듭니다.

⚠️ 개수가 아니라 이름 집합으로 판정하는 이유: 개수는 *고쳐진 실패*와
*새로 깨진 실패*를 맞바꾼 것을 구분하지 못합니다.

그리고 이 상자는 **import 경계 위반 5건**과
**미해소 의존 0건**을 안고 배송됩니다
(`governance.staged_import_violation_baseline` ·
`governance.staged_unresolved_dependency_baseline`). 둘 다 등재된 부채이고
한 방향으로만 줄어듭니다.

## 이 상자가 혼자 도는가

**아니요.** 이 레인은 아래 형제 레인이 `sys.path` 에 함께 있어야 import 가
해소됩니다:

- `fcc-test-contracts`

실행 방법은 `README.md` §테스트 를 보세요.

---

이 배송의 완료명은 **「첫 배송 + 리허설」** 이지 「레포 분리 완료」가 아닙니다 —
CI · lockfile · 설치 검증이 남아 있습니다. `README.md` §무엇이 아직 없나 참조.
