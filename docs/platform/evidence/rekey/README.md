# ADR-0005 Re-keying E1~E5 Evidence 번들 — 운영자 Handoff

> publish/materialization 의 hard gate. 이 디렉토리에 E1~E5 JSON artifact 5개가
> 모두 모이고 검증을 통과해야 gate 가 열린다. **현재 비어 있음 = gate CLOSED 가
> repo 의 공식 상태** (`tests/test_rekey_publish_gate.py::TestCurrentRepoEvidenceBundle`
> 가 봉인 — evidence 도착으로 gate 가 열리면 그 테스트가 의도적으로 FAIL 하며
> publish sprint 착수 신호가 된다).

## 판정 SSOT / 검증 도구

- 판정 술어: `src/domain/services/rekey_evidence_gate.py::evaluate_rekey_publish_gate`
- 운영 CLI: `python scripts/rekey_publish_gate_cli.py` (exit 0=OPEN / 1=CLOSED / 2=IO 오류)
- invariant: `python -m pytest tests/test_rekey_publish_gate.py -q`

## 단계별: 무엇을 실행해 어떤 파일을 두는가

| 단계 | 실행 (production, 운영자/B-1 owner) | 산출 파일명 (이 디렉토리) | 필수 필드 (검증 기준) |
|------|-------------------------------------|---------------------------|------------------------|
| **E1** local dry-run clean | `python scripts/rekey_dry_run_cli.py --db logs/<model>.fcc.db --output E1-dry-run.json` (exit 0 확인) | `E1-dry-run.json` | CLI 산출 그대로 — `total_records>0` / checksum 3종 64-hex / `hash_mismatches=[]` / `parse_errors=[]` / `backfill_precondition_met=true` |
| **E2** migration 018 apply | production local SQLite 에 `018_condition_hash_v2_parallel_column` 적용(앱 1회 실행 = `run_migrations`) 후 `python scripts/rekey_migration_apply_evidence_cli.py --db logs/<model>.fcc.db --output E2-migration-018-apply.json` (read-only, exit 0 확인 — 수기 JSON 금지) | `E2-migration-018-apply.json` | `migration_id`(정확히 `018_condition_hash_v2_parallel_column`) / `condition_hash_v2_column_present: true` / `db_identity` / `applied_at` |
| **E3** local backfill | `python scripts/rekey_backfill_cli.py --db logs/<model>.fcc.db --apply --output E3-local-backfill.json` (write 승인 `--apply` 명시, exit 0 확인 — `RekeyBackfillAudit` 를 도구가 직렬화, 수기 JSON 금지) | `E3-local-backfill.json` | `total_eligible>0` / `updated_count+skipped_count==total_eligible` / `conflict_count==0` / `old/new/mapping_checksum` 64-hex |
| **E4** central expand+ingest | **(0·provider envelope 산출, DB 미접속)** clean E1 dry-run JSON 에서 provider mapping envelope 를 도구로 파생: `python scripts/rekey_mapping_envelope_cli.py --dry-run E1-dry-run.json --output E4-mapping-envelope.json` (도메인 SSOT `build_mapping_envelope` precondition gate + `verify_mapping_envelope_integrity` 송신 전 무결성 검증 — 수기 전사 금지, dirty dry-run=exit 1/손상=exit 2 시 파일 미산출). **(1·live ingest)** central PG expand migration 적용 후 provider mapping envelope 를 `python scripts/rekey_central_ingest_evidence_cli.py ingest --dsn "$FCC_CENTRAL_DB_URL" --envelope E4-mapping-envelope.json --output E4-ingest-audit.json` 로 **실제 실행**(`PostgresCentralRekeyIngestAdapter.ingest_mapping_envelope` verbatim re-key). `envelope_mapping_checksum`/`central_conflict_count` 는 `RekeyIngestAudit` 에서 파생(운영자 선언 0). conflict 시 write 0 + truthful failure artifact + exit 1. **(2·live coverage)** `python scripts/rekey_central_ingest_evidence_cli.py collect --dsn "$FCC_CENTRAL_DB_URL" --output E4-coverage.json` (read-only SELECT, 수동 SQL 금지). **(3·조립, DB 미접속)** `python scripts/rekey_central_ingest_evidence_cli.py assemble --ingest E4-ingest-audit.json --coverage E4-coverage.json --output E4-central-ingest.json` (exit 0 확인 — 수기 JSON·숫자 전사·운영자 선언 scalar 금지) | `E4-central-ingest.json` | `envelope_mapping_checksum` 64-hex / `central_total_attempts>0` / `central_v2_null_count==0` / `central_conflict_count==0` |
| **E5** cutover evidence | `python scripts/rekey_cutover_evidence_cli.py --evidence-dir docs/platform/evidence/rekey --output E5-cutover-evidence.json` (E1/E3/E4 artifact 에서 자동 파생 — DB 미접속, exit 0 확인) | `E5-cutover-evidence.json` | 기존 6-gate (`rekey_cutover.evaluate_cutover_preconditions`) 통과 |

## Cross-stage 무결성 (자동 검증)

`E1.mapping_checksum == E3.mapping_checksum == E4.envelope_mapping_checksum ==
E5.{local,envelope,central}_mapping_checksum` — 다섯 artifact 가 **같은 재키잉
실행의 같은 매핑**을 가리켜야 한다. 불일치 = evidence 짜깁기/다른 실행 의심 →
gate CLOSED.

## 규칙

- 숫자 sentinel 금지: 누락은 필드 자체를 빼지 말고 **파일을 만들지 않는 것**으로
  표현(부재 = explicit blocker). `-1` 등 음수 count 는 malformed 로 차단된다.
- checksum 은 어디서도 재계산하지 않는다(never-recompute) — 각 단계 도구가 declare
  한 문자열을 그대로 복사.
- 빈 production DB(레코드 0)는 vacuous clean 으로 gate 를 열 수 없다 — 별도
  operator waiver 결정 필요.
- gate OPEN 확인 후의 다음 단계: C1 live-path cutover + publish/materialization
  sprint (Codex 재제출 — `adr-0005-rekeying-d-pre.md` §GATE).
- **E4 ingest→collect→assemble 분리 (execution evidence)**: E4 evidence 4 필드는 **실제
  adapter 실행** 에서 파생한다 — `envelope_mapping_checksum`/`central_conflict_count` 는
  `ingest` 가 `RekeyIngestAudit`(conflict pre-scan 결과)에서, `central_total_attempts`/
  `central_v2_null_count` 는 `collect` 의 coverage SELECT 에서. 운영자가 conflict count 를
  손으로 적는 경로는 없다. central PG 접속(`ingest`/`collect`)은 composition root SSOT
  `platform_api_composition.build_central_connection_factory`(lazy psycopg) + ingest adapter +
  coverage SSOT 에만 위임하고 CLI 는 드라이버/raw SQL 을 embed 하지 않는다(central PG 접근을
  application/platform 경계로 격리). `assemble` 은 **DB 에 접속하지 않고** `ingest`/`collect`
  산출 JSON(`--ingest`/`--coverage`)을 합쳐 E4 artifact 를 조립·gate 자가검증만 한다(frozen-safe,
  재현 가능, 운영자 선언 scalar 0). provider mapping envelope 는 E1 dry-run 산출(`mapping` +
  checksum)에서 **verbatim** 파생한다(central 재계산 0) — `scripts/rekey_mapping_envelope_cli.py`
  가 provider-side 에서 도메인 SSOT `build_mapping_envelope`(precondition gate) +
  `verify_mapping_envelope_integrity`(송신 전 무결성)로 `E4-mapping-envelope.json` 을 산출하며,
  DB·central ingest·publish 미접근(JSON→SSOT→JSON 순수 변환)이다. conflict 시 `ingest` 는 write 0 +
  truthful failure artifact(`central_conflict_count>0`) + exit 1 로 gate 가 닫게 한다.
- **필드/파일명/migration_id 최종 권위 = `src/domain/services/rekey_evidence_gate.py`
  상수.** 본 README·CLI 는 그 상수를 인용·파생할 뿐 재정의하지 않는다. 두 문서가
  갈라지면 gate 모듈이 SSOT.
