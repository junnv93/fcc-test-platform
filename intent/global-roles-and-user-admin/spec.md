# Spec: 권한을 프로젝트별 배정에서 전역 직무 역할로 되돌리고, 사용자 관리 진입점을 만든다

Intent: ./intent.md
Author: Claude (draft) / kmjkds
Date: 2026-09-07
Status: accepted
Slug: global-roles-and-user-admin

## 1. 요구사항

### 권한 어휘

- **R1.** `platform:admin` 은 **사람·권한 관리만** 게이트한다. 시험 관련 오퍼레이션을
  하나도 게이트하지 않는다.
- **R2.** 신설 토큰 `platform:project-operate` 가 프로젝트 라이프사이클과 성적서 발행을
  게이트한다: `update_project` · `complete_project` · `reopen_project` · `create_report`.
- **R3.** `register_chamber` · `update_chamber_web_session_approval` 은 **기존** 토큰
  `platform:chamber-config-write` 로 옮긴다. **신설하지 않는다.**
- **R4.** 권한 토큰 우주는 백엔드 세 카탈로그(platform·headless·session)와 프론트
  `permissions.ts` 사이에서 **집합 등호**를 유지한다.

### 역할

- **R5.** 역할은 **전역 셋**이다 — `pm` · `engineer` · `system_admin`. 프로젝트 스코프
  역할은 존재하지 않는다.
- **R6.** `engineer` 는 시험에 관한 모든 오퍼레이션을 수행할 수 있다. §6 의 파생 판정이
  「시험원이 못 하는 시험 관련 오퍼레이션 = 0」을 관측한다.
- **R7.** `pm` 은 `platform:read` + `platform:sample-write` 를 갖는다.
- **R8.** `system_admin` 은 `platform:admin` + `platform:sample-hard-delete` 를 갖는다.
  **시험 데이터를 만지는 토큰을 하나도 갖지 않는다.**
- **R9.** 한 사람이 `engineer` 와 `system_admin` 을 동시에 가질 수 있다.
- **R10.** 역할 부여 그래프는 **하나**다. `role_permissions`(프로젝트용)와
  `global_role_grants`(전역용)의 이원 구조가 사라진다.

### 인가

- **R11.** `PlatformApiAdapter.authorize` 의 인가 경로는 **토큰 하나**다. 인가 1회당
  중앙 DB SELECT 는 0회다.
- **R12.** 프론트 `hasPermission()` 이 백엔드 판정과 **같은 답**을 낸다. 화면은 못 하는
  일의 컨트롤을 제공하지 않는다.

### 사용자 관리

- **R13.** 다음 오퍼레이션이 존재하고 전부 `platform:admin` 으로 게이트된다:
  `list_users` · `create_local_user` · `disable_user` · `enable_user` ·
  `reset_user_password` · `assign_global_role` · `revoke_global_role`.
- **R14.** 계정 발급은 `force_password_change=true` 로 태어난다. 초기 비밀번호는
  `test12345` 이며 **응답 본문·로그·감사 어디에도 실리지 않는다**(값이 상수이므로
  운영자가 이미 알고 있다).
- **R15.** `reset_user_password` 는 새 비밀번호를 받지 않는다. 비밀번호를 초기 상수로
  되돌리고 `force_password_change` 를 세우며 `session_version` 을 올린다.
- **R16.** 사용자 관리 쓰기는 전부 `audit_events` 에 **같은 트랜잭션**으로 남는다.
- **R17.** 계정 비활성화는 **두 표면 모두**에서 30초 이내에 효력을 갖는다(§3 D4).

### 이관

- **R18.** 이관으로 권한을 잃는 기존 사용자가 0명이다. 부트스트랩 계정은 `engineer` +
  `system_admin` 을 갖는다.

## 2. 범위 밖 (Non-goals)

* **진행률 보고 산출물.** 진행률 도메인은 운영자 판정으로 재설계 대기 중이다
  (ADR-0002 §범위 밖). 이 스펙은 `pm` 이 진행률을 **읽을 수 있게** 하는 데까지만 간다.
* **프로젝트 담당자 배정.** `project_membership` 을 「담당 표」로 남기는 안은 intent 에서
  기각됐다. 담당자를 사전에 정하는 기능을 새로 만들지 않는다.
* **OIDC 배포 지원.** 중앙은 `local_jwt` 다(§5). OIDC 배포에서 전역 역할이 IdP 클레임과
  어떻게 정합하는지는 이 스펙이 다루지 않는다. `identity_policy.py` 의 `role_map` 검증은
  **손대지 않는다** — 그것은 headless 표면의 별도 역할 어휘(`viewer`/`operator`/
  `report_manager`/`admin`)를 보며, 이 스펙이 바꾸는 프로젝트 역할과 이름 공간이 다르다.
* **비밀번호 정책 강화.** `password_defects` 를 바꾸지 않는다. `test12345` 가 통과하는
  현재 정책 그대로다.
* **메일 발송.** 초기 비밀번호는 운영자가 구두·별도 경로로 전달한다.
* **`platform:chamber` 기계 토큰.** 챔버 PC 는 Keycloak 토큰을 계속 쓴다. 건드리지 않는다.

## 3. 설계 결정과 근거

| # | 결정 | 버린 대안 | 근거 |
|---|---|---|---|
| **D1** | `platform:admin` 을 쪼개되 **신설 토큰은 하나** (`platform:project-operate`). 챔버 둘은 기존 `platform:chamber-config-write` 로 이동 | ① 안 쪼갠다 ② 신설 토큰 둘(프로젝트용·챔버용) | ①은 시험원에게 성적서 생성을 주면 권한 배정이 따라온다(intent §Problem ①). ②는 챔버 등록·웹세션승인·계측기주소·저장위치가 **행위자도 스코프도 같은데** 토큰만 둘이 된다. 스키마가 이미 그 논거를 적어 뒀다: *"늘 함께 부여되는 토큰 쌍은 drift 표면이 하나 더 있는 한 토큰일 뿐"* |
| **D2** | `project_membership` 표·뷰·서비스를 **삭제**한다 | 표를 「담당 배정」으로 남긴다 | intent 확정: *"프로젝트에 따로 담당을 부여할 필요를 느끼지 못한다."* 그리고 `role_key` 를 가진 채 인가하지 않는 표는 **「이름이 보장처럼 생긴」** 형태다 — 다음 사람이 그것을 권한으로 읽는다 |
| **D3** | 전역 역할 부여를 `rbac_role_grants.global_role_grants` **한 절**로 통합. `role_permissions` 표와 프로젝트 부여 절은 삭제 | 두 표를 유지하고 프로젝트 절만 비운다 | 빈 절은 「나중에 채울 자리」로 읽힌다. 그리고 두 표가 남아 있는 한 *"project 역할이 전역 능력으로 승격되면 안 된다"* 는 규율을 계속 지켜야 한다 — 표가 하나면 그 함정 자체가 없다 |
| **D4** | 계정 비활성화의 headless 반영: **좁은 내부 유효성 라우트 + 30초 TTL 캐시**를 headless 에 둔다 | ① 매 요청 platform 호출 ② access token TTL 단축(900→120) ③ 아무것도 안 함(15분 창) | ①은 측정 표면에 요청당 HTTP 왕복을 얹는다. ②는 코드 0줄이지만 **갱신 트래픽 7.5배**이고 bcrypt 없는 `/auth/refresh` 를 그만큼 더 두드린다. ③은 intent 가 「실질 즉시」를 약속했다. ⚠️ 이 결정의 근거가 되는 실측: `TokenRevocationList` 는 **프로세스 로컬**이라 platform 의 로그아웃·폐기가 headless 에 **전혀 전달되지 않는다** — 즉 오늘 headless 는 비활성화뿐 아니라 **로그아웃·비밀번호변경·권한회수 전부에 최대 15분 눈이 멀어 있다.** D4 는 그 넷을 한꺼번에 닫는다 |
| **D5** | 백엔드↔프론트 패리티 봉인을 **이 레인에 새로 세운다** | 모노레포 사본을 고친다 | 실측: 이 레인 `tests/` 에 그 봉인이 **0건**이고, 모노레포 원본은 `apps/web/src/api/permissions.ts` 를 찾는데 **그 파일이 모노레포에 없다**(웹 앱이 이 레인으로 떠났다). 대상이 여기 있으므로 봉인도 여기 있어야 한다 |
| **D6** | 이관은 **표 DROP + 전역 역할 시드 + 부트스트랩 재부여**. 데이터 이관 로직 없음 | 멤버십 행을 전역 역할로 승격하는 이관 스크립트 | intent §Open questions 3 실측: 아직 실사용 전이고 `project_membership` 에 지킬 사람이 없다. 이관 로직은 **돌 일이 없는 코드**가 된다 |
| **D7** | `create_project` 는 `authenticated` 로 **유지** | `platform:project-operate` 로 올린다 | intent 확정: pm 과 engineer 둘 다 접수할 수 있어야 한다. 그리고 그 토큰은 프로젝트가 **생긴 뒤에** 필요한 것들을 게이트한다 — 생성은 아직 대상이 없다(ADR-0017 D3 의 원래 논거) |
| **D8** | ADR-0017 D3(생성자 자동 admin)은 `superseded` | 생성자에게 자동으로 무언가 부여 | 부여할 멤버십이 없다. 그리고 역할이 전역이면 생성자는 **이미** 자기 역할을 갖고 있다 — 자동 부여가 답하던 질문이 사라진다 |

## 4. 영향받는 경계

* **레인**: **셋 다.** 순서가 있다.

  ```
  ① fcc-test-contracts / packages/fcc-test-kernel
     surface_projects.py · surface_reports.py · surface_chambers.py 의 permission 선언
     → kernel-v0.5.5 태그가 «먼저» 나가야 한다
                ↓
  ② fcc-test-platform  (핀 올림 → 스키마 → 마이그레이션 → 서비스 → 라우트 → 프론트 → 시험)
                ↓
  ③ FCC_mobile_test_automation  (headless 이미지를 만드는 레포)
     platform_rbac_http.py 를 「권한 조회」에서 「유효성 확인」으로 좁힌다 (D4)
  ```

  ⚠️ ①을 건너뛰면 ②가 존재하지 않는 권한을 참조한다.

* **DB**: **마이그레이션 필요.** `036_global_roles_and_user_admin.sql`
  - `DROP VIEW project_member_permissions`
  - `DROP TABLE project_membership`
  - `DROP TABLE role_permissions`
  - `audit_events.event_type` CHECK 제약에 사용자 관리 이벤트 추가
    (`user.created` · `user.disabled` · `user.enabled` · `user.password_reset` ·
     `role.assigned` · `role.revoked`). ⚠️ 기존 `membership.assigned`/`membership.revoked`
    는 **제거하지 않는다** — append-only 표에 그 이름의 과거 행이 이미 있다.
  - `roles`/`global_role_grants` 재시드 (`pm` · `engineer` · `system_admin`)
  - 중앙 DB 사전점검 대상이다.

* **API 계약**: **바뀐다.** 오퍼레이션 7개 신설 + 9개 permission 재배치 →
  OpenAPI 아티팩트 3사본 **재생성**이 계획에 들어간다(손으로 고치지 않는다).

* **프론트엔드**: `permissions.ts`(토큰 신설), `membership.tsx`(삭제),
  사용자 관리 화면(신설), 권고적 게이팅 우회 2곳 제거(`projects.tsx` · `membership.tsx`),
  네비게이션. **시각 골든이 바뀐다** — 화면이 추가·삭제되므로 재촬영이 계획에 들어간다.

* **provider 비공개 레포**: **닿지 않는다.** 실측: `INTERNAL_RBAC_ROUTES` 소비자는
  provider 레포가 아니라 **모노레포**
  (`src/infrastructure/adapters/driven/platform_rbac_http.py`)다. headless 이미지는
  모노레포가 만든다. ⚠️ intent 초안이 「provider 레포」라고 적었던 것은 **틀렸고**
  intent §Open questions 5 가 그것을 정정해 두었다.

**Debt-Accepted-By**: (없음)

기준선에 실패 이름을 추가할 계획이 없다. 이 작업이 만드는 red 는 전부 **고쳐서** 닫는다.
D5 가 새 봉인을 세우는데, 새 봉인은 오늘의 갈라짐도 함께 드러낼 수 있다 — 그때 기준선에
넣지 않고 갈라짐을 고친다. 고칠 수 없는 것이 나오면 그 시점에 이 줄을 채우고 사람이
결정한다.

## 5. 정책 확인

**인증/RBAC** — 이 스펙의 본체다.

* 중앙 인증 형상을 실측했다(2026-09-07, 중앙 PC `infra/central/central.env`):
  `FCC_PLATFORM_AUTH_MODE=local_jwt` · `FCC_HEADLESS_AUTH_MODE=local_jwt` ·
  `WEB_AUTH_MODE=local`, 서명 열쇠 셋(SECRET·ISSUER·AUDIENCE) platform↔headless **일치**.
  → **이 시스템이 계정을 소유한다.** 계정 발급·비밀번호 초기화가 이 도메인 안에 있는 근거다.
  → 그리고 SPA 토큰이 `/headless/*` 에 도달하므로 R6 이 화면에서 실현된다.
* `platform-api-node` 는 `oidc_jwt` 하드코딩으로 남는다 — 챔버 기계 토큰 전용이고 사람
  역할과 무관하다. **건드리지 않는다.**
* 신설 토큰은 **하나**다(`platform:project-operate`). 이 레포는 「신규 grantable 토큰 0」을
  반복해 선호했고(스키마·커널 주석 다수), D1 이 그 규율 안에서 최소로 늘린다.
* `public` / `authenticated` 는 인가 **모드**이지 부여 가능한 토큰이 아니다. 패리티 우주에서
  계속 제외한다.

**개인정보** — 새 PII 를 만들지 않는다. 사용자 관리가 다루는 칸은 이미 `users` 에 있는
`subject` · `issuer` · `display_name` · `email` · `enabled` 뿐이다.
비밀번호는 응답·로그·감사에 싣지 않는다(R14). `audit_events` 는 `actor_subject` 와
`target_user_subject` 를 남기는데 둘 다 기존 칸이다.
⚠️ `list_users` 가 **내부 직원 명부**를 노출한다 — `platform:admin` 게이트이고, 로그인
표면이 열거를 막는 규율(*"네 경우를 구별할 수 없게 접는다"*)과 모순되지 않는다: 저쪽은
미인증 표면이고 이쪽은 관리자 전용이다.

**오프라인 동작** — 변화 없다. 이 스펙은 중앙 웹 표면만 만진다. 챔버 PC 의 오프라인
측정 루프는 `platform:chamber` 기계 토큰을 쓰며 이 변경의 밖이다.

## 6. 성공 판정 — 무엇이 그 값을 내는가

| intent | 재는 것 | 관측 방법 |
|---|---|---|
| S1 | `platform:admin` 이 게이트하는 «시험 관련» 오퍼레이션 6 → **0** | 신규 `tests/test_role_model_is_derived.py` 가 `PLATFORM_API_OPERATIONS` 에서 파생 판정 |
| S2 | 역할 이름 집합 → **`{pm, engineer, system_admin}`** | 같은 시험이 `rbac_role_grants` 에서 파생 |
| S3 | 프로젝트 스코프 부여 절 → **삭제** | 스키마 JSON 에 `grants` 키 부재 단언 |
| S4 | 인가 경로 2 → **1** | `inspect.getsource(PlatformApiAdapter.authorize)` 에 멤버십 분기 부재 — ⚠️ 문자열 존재 검사가 아니라 **AST** 로 묻는다 |
| S5 | 인가 1회당 DB SELECT → **0** | 연결 팩토리를 세는 가짜로 `authorize()` 100회 호출, 호출수 0 단언 |
| S6 | 프론트 권고적 게이팅 우회 2 → **0** | `tests/test_frontend_architecture_conformance.py` 에 축 추가 |
| S7 | API 로 불가능한 사용자 관리 동작 5 → **0** | 오퍼레이션 카탈로그에서 파생 (R13 의 일곱 이름) |
| S8 | 시험원이 못 하는 «시험 관련» 오퍼레이션 42 → **0** | **파생 판정**: 세 카탈로그 전량 ∖ (engineer 토큰 ∪ 인가모드 ∪ 기계토큰 ∪ system_admin 전용) = ∅ |
| S9 | 권한을 잃는 기존 사용자 → **0** | 이관 대상 0명(D6). 마이그레이션이 `project_membership` 행수를 세어 0이 아니면 **거부**한다 |
| S10 | 패리티 봉인 0건 → **1건** | 신규 `tests/test_rbac_parity.py` — 이 레인 판. 주입으로 이빨 확인 |
| S11 | 계정 비활성화 반영: headless 최대 15분 → **30초 이내** | D4 캐시 TTL 상수를 시험이 읽고, 주입한 시계로 만료 경계 단언 |

⚠️ **S8 은 스칼라가 아니라 집합으로 둔다.** 「42 → 0」을 수로 적으면 오퍼레이션이 늘어난 날
그 수가 의미를 잃는다. 판정은 **차집합이 공집합인가**로 쓴다.

⚠️ **S10 의 봉인은 세운 뒤 이빨을 확인한다** — 백엔드에만 토큰을 하나 넣고 프론트를
안 고치면 red 가 나야 한다. 공집합형 봉인은 말뭉치가 줄면 조용해진다.

## 7. 열린 질문

1. **`enable_user` 를 R13 에 넣는 것이 맞는가** — intent 는 「비활성화」만 말했다. 끄는 기능만
   있고 켜는 기능이 없으면 실수로 끈 계정을 DB 로만 되살릴 수 있다.
   → 답(kmjkds 위임 · Claude): **넣는다.** 되돌릴 수 없는 관리 동작은 그 자체가 결함이고,
   `unlock_local_account` 가 이미 같은 형태(잠금의 반대)로 존재한다. 감사 이벤트도 짝으로 남긴다.

2. **`pm` 도 `list_users` 를 볼 수 있어야 하는가** — 담당자를 찾으려면 명부가 필요할 수 있다.
   → 답(kmjkds 위임 · Claude): **아니다.** intent 가 `system_admin` 을 「사람과 권한만 다루는
   역할」로 정의했고, 그 대칭으로 명부는 그쪽 것이다. 그리고 D2 가 담당자 배정을 범위 밖으로
   두었으므로 `pm` 이 명부를 찾을 이유가 이 스펙 안에 없다. 필요해지면 별도 의도로 연다.

3. **`role_permissions` 표를 정말 지우는가, 아니면 남기고 안 쓰는가** — 지우면 되돌리기 어렵다.
   → 답(kmjkds 위임 · Claude): **지운다.** D3 의 근거 그대로다. 그리고 이 레포는 「설치 잔재가
   봉인을 초록으로 만든다」를 기록했다 — 안 쓰는 표가 남아 있으면 그것을 읽는 시험이 조용히
   통과한다. 되돌릴 필요가 생기면 마이그레이션 하나로 다시 만든다(시드는 스키마 SSOT 에 있다).

4. **`kernel-v0.5.5` 를 이 작업이 직접 태그하는가** — 형제 세션이 같은 번호를 먼저 가져갈 수 있다.
   → 답(kmjkds 위임 · Claude): **태그 발행 시점에 사용자 승인을 받는다.** 번호는 그 시점에
   `git ls-remote --tags` 로 확인해 고른다 — 이 레포는 형제 세션이 버전 번호를 먼저 가져간 사례를
   기록했다. 태그를 옮기지 않는다.

5. **모노레포 변경(D4 ③)을 이 작업이 완주하는가** — 모노레포 CI 는 「그대로 둔다」가 운영자 판정이다.
   → 답(kmjkds 위임 · Claude): **소스는 고치되 CI 는 건드리지 않는다.** 그 판정(2026-09-07)은
   워크플로·러너에 관한 것이지 소스 변경 금지가 아니다. 다만 모노레포 쪽은 **마지막 웨이브**로
   두고, platform 이 먼저 자립적으로 초록이 되게 한다 — 그래야 어느 레인의 결함인지 갈린다.

6. **`disable_user` 가 자기 자신을 끌 수 있는가**
   → 답(kmjkds 위임 · Claude): **막는다.** 관리자가 자기 계정을 끄면 남은 관리자가 없을 때 시스템에
   아무도 들어갈 수 없다. `actor_subject == target` 이면 400 으로 거절한다. 같은 이유로
   `revoke_global_role` 이 **마지막 `system_admin`** 을 회수하는 것도 막는다.
