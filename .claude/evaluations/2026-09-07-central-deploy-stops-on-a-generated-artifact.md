# 평가 — 배포를 멈춘 것은 «생성물을 원장에 넣은 것» 이었다 (2026-09-07)

## Why — 왜 지금인가

운영자가 중앙 PC 에서 「이미지 재빌드부터 홈페이지까지」를 실제로 돌리는 중에 막혔다.

    ✘ Container fcc-central-migrate   Error ... exit 3
    → platform-api · platform-api-node · web  셋이 «안 뜸»

셋은 `condition: service_completed_successfully` 로 그 잡에 물려 있다. 즉 마이그레이션
하나가 거부하면 **웹이 안 뜬다.** 그것이 이 배선의 의도이고 옳다 — 문제는 그 거부가
**정상 상태에 대해** 났다는 것이다.

## What — 무엇을 찾았나

`exit 3` 은 `MigrationDriftError` 다. detail:

    001_initial_central_db: file checksum bbcd0f2861c9… != ledger a1ed7a06b17a…
    (already-applied migration was edited — refusing to re-apply)

⚠️ **아무도 안 고쳤다.** `001` 의 첫 줄이 스스로 말한다:

    -- Generated from docs/platform/central_db_schema.v1.json.

**`001` 은 생성물인데 append-only 원장에 들어가 있다.** 마이그레이션의 정의는 「한 번
적용되면 불변인 역사」이고, 생성물은 SSOT 가 바뀔 때마다 다시 만들어진다. 두 성질은
양립하지 않는다. 오류 메시지가 아무도 안 한 일을 *"was edited"* 라고 부르는 것이 그
범주 오류가 표면에 드러난 자리다.

### 그 비교에 «참 양성» 이 없었다

    · 001 은 스키마가 바뀔 때마다 체크섬이 바뀐다 (설계상 반복이 보장된 사건)
    · 기존 DB 에서 001 은 **다시 실행되지 않는다** — 체크섬만 비교된다
    · 그 체크섬은 「재생성됐다」와 「사람이 고쳤다」를 **원리적으로 못 가른다**

즉 낼 수 있는 것은 거짓 양성뿐이었고, 대가는 **스키마가 바뀔 때마다 배포가 멈추는
것**이었다. 그리고 그 처방(`reconcile`)은 자기 docstring 에서 스스로를
*"The single sanctioned exception to the append-only ledger"* 라고 부른다 —
**유일한 불변식에 «승인된 예외»가 있다는 것 자체가 냄새였다.**

## How — 검사를 «구별 가능한 자리»로 옮겼다

「사람이 `001` 을 고쳤나」를 답하는 검사는 **이미 있었고 체크섬보다 강했다**:

    tests/test_chamber_node_central_schema.py::TestChamberSchemaDdlDrift
      generated = exporter.render_ddl(exporter.load_schema(SCHEMA_PATH))
      committed = DDL_PATH.read_text(...)
      assertEqual(committed, generated)          ← 바이트 동등

그래서 러너는 **생성물을 드리프트 비교에서 뺀다.** 판정은 이름을 못박지 않고 **파일
머리(`Generated from`)에서 파생**한다 — 부트스트랩이 이름을 바꾸거나 두 번째 생성물이
생기는 날 손 목록은 조용히 낡는다.

⚠️ **완화의 «전제»를 봉인했다.** 대체 검사가 사라지면 이 완화가 조용한 구멍이 되므로,
`tests/test_platform_db_migration_runner.py` 가 그 게이트의 실재를 단언한다 — 전제를
지우는 사람이 그 줄에서 막힌다.

## Verification — 주입

    ① 완화를 증분까지 번지게 (모두 건너뜀)         1 failed
    ② 완화를 되돌림 (생성물도 다시 막음)            1 failed
    ③ 생성물 판정을 죽임 (헤더 파생 제거)           3 failed
    ④ `001` 을 «손으로» 고침                        1 failed  ← 잃은 것이 없다는 증명
    복원                                            7 passed

④가 이 평가의 요지다: 원장 체크섬이 지킨다던 바로 그 경우가 **여전히 잡힌다.**

## 곁가지 — 같은 뿌리의 문서 결함 넷

같은 조사에서 배포 문서가 **잘못된 스택을 만들게** 하고 있었다. 전부 「쓸 때는 참이었고
다시 파생되지 않은 산문」이다.

    ONPREM_DEPLOYMENT.md      네 자리가 provider 저장소로 cd (추출 배송 이후 무갱신)
    LOCAL_DEVELOPMENT.md      같은 cd 하나
    운영 문서 셋              기대 컨테이너 목록이 platform-api-node 이전 판
    ONPREM §검증              `python` — 같은 파일 §5 가 이미 python3 로 정정해 둔 것

두 저장소가 각각 `infra/docker-compose.central.yml` 을 갖고 **내용이 다르다**(provider
6서비스·`web` 에 `build:` 없음 / 이쪽 7서비스·있음). 옛 경로로 치면 인증 모드를 가르는
인스턴스가 안 뜨고 `web` 을 못 만든다.

봉인 둘을 `tests/test_central_docker_compose.py` 에 세웠다 — 블록이 **이 저장소의 중앙
자산**을 만지면 그 블록의 `cd` 는 이 저장소여야 하고, 기대 컨테이너 목록은 compose 의
`container_name` 에서 파생한다.

⚠️ **일회성을 상시 가동과 갈라 두었다**(형제 세션 지적). `fcc-central-migrate` 는
`restart: "no"` 라 정상 스택에서도 실행 목록에 없다 — 7개 전부를 「떠 있어야 한다」로
파생하면 **건강한 스택을 위반으로 읽는다.** 과발화하는 게이트는 꺼지고, 꺼진 게이트는
0층이다. 그 갈라짐 자체를 봉인이 단언한다.

⚠️ **술어를 두 번 넓혔고 둘 다 주입이 알려 줬다.** 처음에는 `docker-compose.central.yml`
하나만 봐서 `cd … && cp infra/central/central.env.example …` 블록을 못 잡았다. 그 자리도
똑같이 틀렸다 — 잘못된 저장소의 env 예시를 복사한다. 그리고 그 폭 자체는 좁히는 주입이
결함과 «함께» 들어오므로 아무것도 안 빨개진다 → 탐지기에 합성 입력을 먹이는 시험으로 샀다.

## 후속

- `reconcile` 서브커맨드는 **남긴다.** 이 수리 이전에 만들어진 DB 의 원장 행을 손으로
  정리하고 싶을 때 쓸 수 있고 멱등이다. 정상 배포 경로에는 더 이상 필요 없다.
  ⚠️ 지우는 것은 별개 판단이다 — CLI 표면 제거는 이 커밋의 범위가 아니다.
- ⚠️ **상자 `.venv` 에 `yaml` 이 없어** `tests/test_central_docker_compose.py` 가 그
  정본 환경에서 **수집조차 안 된다.** 이 봉인들을 거기서 돌리려면 그 표류를 먼저 풀어야
  한다(형제 세션이 같은 것을 보고했다: contracts 0.1.15 · kernel 0.3.0). 공유 venv
  재설치는 도는 세션들의 발밑을 바꾸므로 **승인 지점**이다.
