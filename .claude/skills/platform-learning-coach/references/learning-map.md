# Learning Map — fcc-test-platform (중앙 플랫폼 레인)

⚠️ 이 커리큘럼은 **이 저장소의 도메인**이다. 원본(FCC) 의 학습 지도는 Excel 입력 ·
측정 워크플로 · 성적서 생성 · 안테나/채널 정규화를 가르치는데, 그 코드는 여기 없다.
챔버 축을 배우려면 그 저장소의 코치를 쓴다.

각 레벨의 목표는 **사용자가 코드를 쓰지 않고 그 층을 설명할 수 있는 것**이다.

---

## Level 1 — 이 상자가 무엇을 소유하는가

목표: 최상위 디렉터리가 각각 무엇인지, 그리고 **무엇이 여기 없는지**를 말할 수 있다.

주제:
- `domain/`(91) · `application/`(24) · `fcc_test_platform/`(107) · `tests/`(147) 의 역할 차이
- `migrations/`(30) 와 `docs/platform/central_db_schema.v1.json` 의 관계 —
  하나는 만드는 절차, 하나는 결과의 선언
- `infra/` 가 배치를 갖고 `apps/web` 이 화면을 갖는다
- **경계**: 측정 코드 · 계측기 제어 · Excel 성적서 생성은 **이 저장소에 없다**.
  `EXTRACTED_FROM.md` 가 그 분리를 적는다.
- `pyproject.toml` 의 패키지 폐포가 **배포에 실리는 것의 SSOT** 다.
  여기 없는 이름은 컨테이너 안에서 import 되지 않는다.

확인 질문: 「이 저장소를 clone 하면 측정을 돌릴 수 있나?」

---

## Level 2 — 값이 어디서 와서 어느 칸에 앉는가

목표: 챔버가 보낸 값이 중앙 DB 의 어느 칸에 어떤 형식으로 앉는지 설명할 수 있다.

주제:
- 봉투(노드 → 중앙)에 실리는 것과 DB 칸의 **형식이 다르다**
- provider 이름표 두 가지: 자연키(`fcc-unlicensed-conducted`)와
  uuid(`providers.id`). 어디서 어느 것을 쓰나
- **등록부 해소** — 자연키를 uuid 로 바꿔주는 자리.
  `application/central_contract/central_sync_readiness.py` 의 `PROVIDER_READINESS_SQL` 이 SSOT
- 해소가 일어나는 세 가지 형태:
  ① 서비스가 미리 바꿔 넘긴다 ② 어댑터가 스스로 조회한다 ③ INSERT 문이 조인한다
- 실제 코드 경로 하나:
  `platform_routes.push_artifact_custody_report` → `CentralArtifactCustodyService.store_report`
  → `PostgresCentralArtifactCustodyWriteAdapter.store_report` → 중앙 PostgreSQL

실패 모드: **자연키를 uuid 칸에 그대로 넣기.** 2026-09-02~03 에 실재했고
보관 보고가 전량 거절됐다. 봉인 → `tests/test_provider_id_uuid_slot_seal.py`

---

## Level 3 — 판정 규칙

목표: 이 저장소가 「성공」이라고 말할 때 그것이 무엇을 뜻하는지 설명할 수 있다.

주제:
- **latest-wins** — 늦게 도착한 옛 관측이 새 판정을 덮지 않는다.
  조건이 `ON CONFLICT … DO UPDATE` 의 `WHERE` 절에 있고,
  `RETURNING` 이 비면 그것이 곧 "밀려났다"는 답이다
- 그래서 **응답 200 + 행 0** 이 정상 동작으로 실재한다
- 밀려남을 조용한 성공으로 접으면 안 되는 이유 — 영수증에 `superseded` 가 실린다
- 어휘 검증이 경계에 있는 이유 — 클라이언트 잘못이 중앙 장애(503)로 둔갑하지 않게
- 오류 분류표(`_PLATFORM_ERROR_CODE_TABLE`)가 most-specific-first 여야 하는 이유

실패 모드: 응답 코드로 인입을 판정하기 · 밀려남과 거절을 같은 답으로 접기

---

## Level 4 — 경계와 신분

목표: 요청이 어디서 인증되고 어디서 권한이 갈리는지 설명할 수 있다.

주제:
- 챔버 토큰 바인딩 — 경로의 챔버와 봉투의 챔버를 **둘 다** 묶는 이유
  (경로만 묶으면 한 챔버 토큰으로 다른 챔버 세션에 판정을 심을 수 있다)
- 기계 신분증 두 방향: 노드 → 중앙, 중앙 → 노드
- `providers` 등록부는 **운영자가 등록하는 참조 데이터**이지 인입되는 것이 아니다.
  그래서 없는 provider 는 404(클라이언트 잘못)이지 503 이 아니다
- 인증 서버의 자격 중 일부는 **런타임에 발급**되어 설정 파일에 없다.
  실측: realm 파일 4개 · 실제 11개

실패 모드: 증상이 `invalid_client` 하나뿐이라 「삭제됨」과 「회전됨」이 구분되지 않는다

---

## Level 5 — 배포와 게이트

목표: 「도는 것이 저장소에 적힌 것과 같은가」를 어떻게 판정하는지 설명할 수 있다.

주제:
- 6축 판정 — `scripts/check_deployment_drift.py`
  (revision · image-id · migration · env-keys · auth-pair · public-host)
- **PASS / DRIFT / 판정 불가** 세 값의 차이와, 셋째를 접으면 안 되는 이유
- 이미지가 어느 커밋에서 만들어졌는지를 라벨로 남기는 이유
  (가장 흔한 배포 결함은 **일어나지 않은 재빌드**다)
- 마이그레이션 경로 해소 — 저장소에서는 `migrations/`, 이미지 안에서는
  `docs/platform/migrations`. 상자 표식(`.extraction-layout.json`)이 그 차이를 흡수한다

실패 모드: 파일이 0개인데 「적용할 것 없음 · 성공」 · 죽은 검사가 결함으로 보고됨

---

## Level 6 — 검사를 어디까지 믿을 것인가

목표: 초록이 근거인지 아닌지 스스로 판단할 수 있다.

주제:
- `tests/support/central_pg_sqlite_shim.py` 의 타입 변환표 —
  **검사 DB 가 현장보다 느슨한 자리**
- 비-공허성 팔: 대상이 존재하는가 · 실제로 관측했는가 · 새 경로가 생기면 빨간불인가
- 봉인은 **고치기 전에 빨간불인지 먼저** 확인한다
- 레인 기준선(`delivered_test_run_baseline.json`) —
  선언을 **줄이는** 것과 **늘리는** 것의 차이. 늘리는 것이 검사를 끄는 것이다

실증: 2026-09-02 결함이 자동 검사 2,700여 건을 전부 통과했다.
두 겹이었다 — 검사 DB 에 형식 제한이 없었고, 검사 입력이 이미 맞는 형식이었다.
