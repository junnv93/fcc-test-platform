# 배송 원장이 마이그레이션 하나를 빠뜨렸고, 파생이 그것을 가렸다 (2026-09-05)

## Why

`.extraction-layout.json` 은 마이그레이션 **35개 중 34개**만 들고 있었다.
`034_sample_custody_events.sql` 하나가 없다(실측 2026-09-05).

규약은 있었고 한 번 어긋났다 — 형제 넷은 전부 **파일을 더한 그 커밋에서**
원장에도 들어갔다:

| 마이그레이션 | 파일과 원장을 함께 넣은 커밋 |
|---|---|
| `031` · `032` · `033` | `a592e66` (프로젝트 접수 개편) |
| `034` | `1854019` (시료 custody) — **원장 미갱신** |
| `035` | `7bda0bf` (samples.metadata_json 폐기) |

`1854019` 는 `migrations/034_…sql` 과 `migrations/001_…sql` 을 건드렸고
`.extraction-layout.json` 은 건드리지 않았다.

## ⚠️ 왜 5일간 아무도 못 봤나 — 파생이 우연히 답을 맞혔다

`fcc_test_contracts.common.tree_artifacts.resolve_repo_artifact` 는 **디렉터리**
이동을 개별 파일 기록들의 «꼬리 일치»에서 **파생**한다. 나머지 34개가
`docs/platform/migrations/X.sql → migrations/X.sql` 를 말하는 한, 디렉터리는
`migrations` 로 옳게 풀린다. 그래서:

```python
MIGRATIONS_DIR = resolve_repo_artifact(__file__, 'docs/platform/migrations')
MIGRATIONS_DIR / '034_sample_custody_events.sql'   # ← 멀쩡히 존재한다
```

**아무것도 빨갛게 되지 않았다.** 이 저장소가 이미 이름 붙인 결함 계열이다 —
*「기본 도구는 조용히 부분집합만 준다」*(설계서 교훈 ③). 여기서는 도구가 아니라
**파생이** 불완전한 입력 위에서 그럴듯한 답을 냈다.

### 그럼 무엇이 실제로 깨지나

| 축 | 결과 |
|---|---|
| 디렉터리 해소 (`'docs/platform/migrations'`) | 정상 — 다른 34개가 답한다 |
| **정확 경로 해소** (`'docs/platform/migrations/034_….sql'`) | **자기 자신으로 해소** — 그 경로는 이 상자에 없다 |
| **모노레포 전달표** (원장의 역매핑) | 이 마이그레이션이 **통째로 누락** |

원장은 **파생의 입력**이다. 입력이 불완전하면 파생은 조용히 부분집합을 준다 —
그래서 「파생이 우연히 맞힌다」가 검사를 면제해 주지 않는다.

## What

1. 항목 하나를 넣는다(033 과 035 사이, 정렬 유지):
   `"docs/platform/migrations/034_sample_custody_events.sql": "migrations/034_sample_custody_events.sql"`
2. 같은 누락이 다시 나지 않도록 봉인을 세운다 —
   `tests/test_central_db_migration_runner.py::TestMigrationsRecordedInDeliveryLedger`.
   양방향으로 본다: 디스크에 있는데 원장에 없는 것, 원장에 있는데 디스크에 없는 것.

### 원장에 손대도 되는가 — 예, 배송 기계는 퇴역했다

설계서에 `.extraction-layout.json` 은 **「배송이 생성한다 — 손대지 마라」**는
줄이 있다. 그 문장은 **배송 기계가 살아 있던 시절**의 것이다:

* 생성기 `scripts/stamp_delivery_provenance.py` 는 **모노레포**에 있고 이 상자에
  없다(실측: `grep -rl extraction-layout scripts/` → 0건).
* 원장의 마지막 «기계» 커밋은 `1c15065 chore: 모노레포 추출 재배송` 이고, 그
  뒤로 `a592e66` · `7bda0bf` 두 세션이 손으로 넣었다.
* 방향은 역전됐다 — 모노레포가 이 상자를 pip 로 소비한다.

원장은 이제 **예약 선언**이다. 설계서 자신이 그렇게 적는다: *「매니페스트는 예약
선언이므로 「값에 있다」만으로는 납품 판정이 되지 않는다」* — 예약됐으나 디스크에
없는 경로가 이미 55개다. 그러므로 이 항목은 「모노레포에 034 가 있다」는 주장이
**아니고**, 031·032·033·035 와 같은 **경로 예약**이다.

## Verification

* 봉인 반증 — 034 항목을 빼고 돌리면 빨갛고, **파일 이름을 정확히 지목**한다:
  `['034_sample_custody_events.sql'] != [] : migration(s) on disk with no delivery-ledger record`. 되넣으면 초록.
* `tests/test_central_db_migration_runner.py` **57 passed**.
* 원장 JSON 유효성 + 마이그레이션 기록 수 34 → **35** (디스크 35개와 일치).
* 전용 venv(contracts 0.1.21 · kernel 0.5.0), `PYTHONPATH` 우회 없음.

⚠️ 봉인은 「일했다는 증거」를 함께 본다(`assertGreater(len(on_disk), 30)`) —
`discover_migrations()` 가 0개를 답해도 「차집합 없음」으로 초록이 되는 자리를 막는다.

## 후속

* 이 상자에는 `.claude/work-claims/` 가 없다(레지스트리는 모노레포에 있다).
  034 를 넣은 시료 세션의 claim 도 그 쪽이다 — 소유 확인은 열린 PR(#70, 겹침 0)과
  활성 claim(전부 모노레포 문서 축, 무관)으로 했다.
* 설계서의 「손대지 마라」 줄은 배송 퇴역 이후 낡았다. 문서 축이라 별건으로 남긴다.
