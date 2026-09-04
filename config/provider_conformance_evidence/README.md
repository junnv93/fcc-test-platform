# 적합성 증거 — provider 가 자기 레포에서 검사한 결과가 도착하는 곳

형식: `fcc-test-contracts/fcc_test_contracts/artifacts/provider_contract_conformance_evidence.schema.v1.json`
판정: FCC 모노레포 `.claude/exec-plans/active/2026-08-31-kc-provider-identity-결정문.md` §6.6

파일 이름은 `<provider_id>.json` 이다. 게이트는
`tests/test_provider_conformance_evidence.py` 다.

⚠️ **여기에 있는 것은 provider 의 계약 아티팩트가 아니라 provider 가 검사했다는 기록이다.**
아티팩트를 여기 두지 마라 — 운영자가 2026-08-31 에 기각한 안 「다」다.
