# ADR-0005 re-key dry-run — operator preflight smoke evidence (2026-05-31)

> read-only preflight (`scripts/rekey_dry_run_cli.py`) end-to-end 산출/검증.
> **DB write / schema / backfill / cutover 0** (mode=ro 연결). backfill 착수 전
> precondition (`hash_mismatches == 0 AND parse_errors == 0`) 증명 단계.

## ⚠️ 정직한 제약 — repo 에 production measurement DB 없음

`measurement_conditions` 테이블은 **측정이 실제 실행될 때만** 채워진다. repo 의 모든
`*.fcc.db` (`my_plan.fcc.db` / `samples/.../*.fcc.db` 등) 는 `measurement_conditions`
**0 rows** (seed 된 test plan 만, 측정 기록 없음). 따라서 본 evidence 는:

1. **실제 DB 파일** end-to-end (CLI 가 production 파일을 read-only 로 열고 valid
   artifact 산출) — 단 0 records (vacuous clean).
2. **대표(representative) populated DB** — clean conditions + 중복 condition (merge
   group) 5건으로 reader→dry-run 전 경로 행사 + artifact 포맷 입증.

**운영자는 본인의 실제 production DB 에 대해 CLI 를 실행**해 precondition 을 확인해야
한다. 본 artifact 는 도구 정확성 + 포맷 + precondition 의미의 증거이지 production 데이터
검증이 아니다.

## 1. 실제 DB 파일 smoke (read-only)

```
$ python scripts/rekey_dry_run_cli.py --db my_plan.fcc.db --output <out>
[summary] records=0 mismatches=0 parse_errors=0 merge_groups=0 backfill_precondition_met=True
exit=0
```

CLI 가 production `*.fcc.db` 파일을 **mode=ro** 로 열어 exit 0 + valid artifact 산출
(0 records → vacuous clean).

**read-only 보장 범위 (정직)**: main DB **content 불변** (write SQLITE_READONLY 거부 —
`test_readonly_engine_rejects_write_and_no_journal_mode`). 단 WAL-mode DB 는 SQLite 가
`-shm`/`-wal` companion (shared-memory wal-index, ephemeral) 을 생성할 수 있음 — DB
데이터 미변경. `immutable=1` 이면 companion 0 이나 uncheckpointed `-wal` 무시 → stale
read 위험이라, preflight 최신-데이터 정확성 우선으로 `mode=ro` 채택 (companion 은
운영자가 사후 정리 가능).

## 2. 대표 populated DB (5 conditions, merge group 포함)

`tests/test_row_identity_store_adr_web_ui_05.py::TestRekeyDryRunRepresentativeSmoke`
가 재현·봉인 (5 conditions: ch36 @ row 0/3/4 = 같은 condition values 다른 position →
merge group, ch40/ch44 distinct). artifact:
`docs/platform/evidence/2026-05-31-rekey-dry-run-representative-artifact.json`.

```
[summary] records=5 mismatches=0 parse_errors=0 merge_groups=1 backfill_precondition_met=True
exit=0
```

검증 (Codex 요구):
- `backfill_precondition_met == true` ✅
- `hash_mismatches == []` ✅ (drift 0 — stored == live 재계산)
- `parse_errors == []` ✅ (파싱 실패 0)
- `merge_groups` 1건 (ch36 stored 3개 → new 1개) — **운영자 검토 대상** (exact-duplicate
  condition 후보; precondition 차단 아님, REJECT 는 migration orchestration 단계)
- `old_hash_checksum != new_hash_checksum` (re-key 가 hash 변경 입증),
  `old_hash_checksum == recomputed_old_hash_checksum` (clean — drift 없음)
- CLI exit code **0**

## 재현

```
python scripts/rekey_dry_run_cli.py --db <local.fcc.db> --output report.json
# exit 0 = clean (backfill 가능) / 1 = hash_mismatches 또는 parse_errors / 2 = DB·인자 오류
python -m pytest tests/test_row_identity_store_adr_web_ui_05.py::TestRekeyDryRunRepresentativeSmoke -q
```

## backfill 착수 게이트 (다음 sprint)

operator 가 **production DB** 에 CLI 실행 → `backfill_precondition_met == true`
(`hash_mismatches == []` AND `parse_errors == []`) 확인 후에만 idempotent backfill
설계 sprint 착수. `merge_groups` 는 별도 운영자 검토(중복 condition 데이터 오류 여부).
SSOT: `domain/services/rekey_dry_run.is_backfill_precondition_met`.
