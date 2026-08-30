/**
 * project-meta-patch — 성적서 표지 메타 부분 갱신 diff SSOT (W3-B M2, 2026-07-30).
 *
 * `PATCH /platform/projects/{id}` 의 의미론은 **키의 존재 여부가 곧 명령**이다:
 *
 *   | 와이어                       | 백엔드 동작            |
 *   |------------------------------|------------------------|
 *   | 키 자체가 없음               | 그 칸 **불변**         |
 *   | `"customer": null`           | 그 칸 **삭제**         |
 *   | `"customer": "ACME"`         | 그 칸 **설정**         |
 *
 * 즉 `undefined` 와 `null` 이 **다른 명령**이다. 폼이 "현재 값을 전부 담아 보내는"
 * 흔한 구현을 하면 두 가지가 동시에 깨진다: (1) 그 사이 다른 사람이 바꾼 칸을 내
 * 화면의 낡은 값으로 덮어쓴다(lost update — 백엔드
 * `central_project_service.update_project_metadata` 가 방어하지 않는다고 명시),
 * (2) 사용자가 비운 칸이 "지움"인지 "안 건드림"인지 서버가 구분할 수 없다.
 *
 * 그래서 이 모듈이 **원본 스냅샷 대비 dirty diff** 를 단일 순수 지점에서 만든다.
 * 계산이 라우트 컴포넌트 안에 인라인되면 렌더 상태와 얽혀 단정이 어려워지고, 세
 * 분기 중 하나가 조용히 무너져도 알 수 없다(그 붕괴가 곧 데이터 손실이다).
 *
 * 선례: `routes/chambers/ChamberAdminPanel.tsx` 의 baseline-대비-override 패턴이
 * "서버 상태를 미러링하지 않는다" 는 절반을 확립했다. 다만 그쪽은 전체 payload 를
 * 재전송하는 PUT 이라 **per-key diff 가 없다** — 코드베이스 전역에 선례가 없으므로
 * 여기가 그 새 SSOT 다.
 */
import type { ProjectEnvelope, UpdateProjectRequest } from '@/api/platform-client';

/**
 * 편집 가능한 표지 메타 필드 이름. 생성 타입 `UpdateProjectRequest` 의 `keyof` 라
 * 손수 유지되는 union 이 아니다 — 백엔드 스키마 → OpenAPI 아티팩트 →
 * `npm run codegen` → 여기로 흐르는 단일 사슬.
 */
export type ProjectMetaField = keyof UpdateProjectRequest;

/**
 * 한 프로젝트의 편집 스냅샷. 폼 입력이 원천이므로 값은 **항상 문자열**이고 `''` 이
 * "빈 칸"을 뜻한다(서버의 `null` 과 사용자가 비운 칸을 같은 표현으로 모아, 세
 * 분기 판정을 {@link buildProjectMetaPatch} 한 곳에만 남긴다).
 */
export type ProjectMetaDraft = Readonly<Record<ProjectMetaField, string>>;

/**
 * 편집 필드 exhaustiveness witness — **화면 표시 순서의 SSOT 이기도 하다.**
 *
 * TS 타입은 런타임에 사라지므로 `keyof UpdateProjectRequest` 를 배열로 만들려면
 * 이 다리가 필요하다. `Record<ProjectMetaField, true>` 로 못 박아 두면 컴파일러가
 * 양방향으로 검사한다: 백엔드가 필드를 **추가**하면 키 누락으로 컴파일 실패,
 * **제거**하면 초과 프로퍼티로 컴파일 실패. 그래서 `['customer', …]` 를 손으로
 * 적은 두 번째 진실 원천과 달리 조용히 드리프트할 수 없다.
 *
 * 리터럴의 나열 순서가 그대로 `EDITABLE_PROJECT_FIELDS` 순서이고 폼 렌더 순서다
 * (성적서 표지 읽는 순서: 식별 → 의뢰/신청 주체 → 제조·인증 → 시험 대상/규격).
 * 순서를 바꾸려면 이 리터럴만 고친다.
 */
const EDITABLE_FIELD_WITNESS: Readonly<Record<ProjectMetaField, true>> = {
  management_number: true,
  customer: true,
  applicant_name: true,
  applicant_address: true,
  manufacturer: true,
  fcc_grantee_code: true,
  eut_description: true,
  test_standard: true,
};

/** 편집 가능한 표지 메타 필드 — 폼 렌더 순서이자 diff 순회 순서. */
export const EDITABLE_PROJECT_FIELDS: readonly ProjectMetaField[] = Object.keys(
  EDITABLE_FIELD_WITNESS,
) as readonly ProjectMetaField[];

/**
 * 모든 칸이 빈 draft — **생성 폼의 baseline** 이다.
 *
 * 생성(`POST`)의 규약은 "빈 칸은 키를 생략한다"인데, 그것은 빈 baseline 대비
 * {@link buildProjectMetaPatch} 와 정확히 같은 계산이다: 빈 칸은 baseline 과 같아
 * 키가 붙지 않고, 채운 칸만 trimmed 문자열로 실린다. 빈 baseline 에서는 "값이
 * 있었는데 비웠다" 가 성립할 수 없으므로 `null` 도 나오지 않는다.
 *
 * 그래서 생성과 편집이 **같은 diff 한 곳**을 공유한다 — 생성 폼이 "빈 칸 생략"을
 * 따로 구현하면 그게 두 번째 진실 원천이 되고, 한쪽만 고쳐지는 순간 빈 칸이
 * `''` 로 저장되기 시작한다(백엔드가 `null` 과 `''` 를 구분하므로 실제 손상).
 */
export const EMPTY_PROJECT_META_DRAFT: ProjectMetaDraft = Object.freeze(
  Object.fromEntries(EDITABLE_PROJECT_FIELDS.map((field) => [field, ''])) as Record<
    ProjectMetaField,
    string
  >,
);

/**
 * 임의의 문자열이 편집 가능한 표지 메타 필드인지 판정한다.
 *
 * 용도는 RFC 9457 `params.field` 귀속이다. 409 `PROJECT_IDENTIFIER_CONFLICT` 는
 * 충돌한 **백엔드 입력 필드명**을 실어 보내는데(`management_number` /
 * `project_code`), 그 이름이 이 폼에 없는 칸일 수도 있다. 모르는 값을 억지로
 * 어딘가에 붙이면 사용자는 엉뚱한 칸을 고치려 하므로, 폼에 실제로 존재하는
 * 필드일 때만 귀속하고 나머지는 일반 충돌 문구로 폴백해야 한다. 이 술어가 그
 * allowlist 이며 — 별도 매핑 테이블을 신설하지 않는다. 편집 필드 집합이 곧 매핑이다.
 */
export function isProjectMetaField(value: unknown): value is ProjectMetaField {
  return typeof value === 'string' && Object.hasOwn(EDITABLE_FIELD_WITNESS, value);
}

/**
 * 목록 행에서 편집 스냅샷을 뜬다.
 *
 * 원천이 **목록 행**(`ProjectEnvelope`)인 것이 중요하다: 이 envelope 이 편집 8필드
 * 전부와 파생값 `fcc_id` 를 이미 나르므로 카드마다 상세를 조회하는 N+1 이 필요 없다.
 *
 * `null`/`undefined`(미기재) → `''`. 서버의 "값 없음"과 사용자가 비운 칸이 같은
 * 표현으로 모이므로, 왕복 후 diff 가 저절로 빈다(멱등).
 */
export function projectMetaDraftFrom(project: ProjectEnvelope): ProjectMetaDraft {
  const draft: Record<ProjectMetaField, string> = {} as Record<ProjectMetaField, string>;
  for (const field of EDITABLE_PROJECT_FIELDS) {
    draft[field] = project[field] ?? '';
  }
  return draft;
}

/**
 * baseline(편집 시작 시점의 서버 값) 대비 draft(사용자가 타이핑한 값)의 diff 를
 * PATCH body 로 만든다. 순수 함수 — 필드마다 정확히 세 갈래다:
 *
 *   1. **미변경** → 키를 **붙이지 않는다**(그 칸은 서버가 손대지 않는다).
 *   2. **값이 있었는데 비웠다** → `null`(그 칸을 지운다).
 *   3. **그 외** → `trim()` 한 문자열.
 *
 * 전부 미변경이면 `{}`. 호출자는 이때 **요청을 아예 보내지 말아야 한다** — 백엔드는
 * 편집 가능한 키가 하나도 없는 body 를 no-op 이 아니라 400 으로 거절한다.
 *
 * 비교는 양쪽 모두 `trim()` 후에 한다. 그래서 서버 값 뒤에 공백을 붙였다 지운
 * 편집은 의미 변화가 없으므로 키가 붙지 않는다(진짜 변경만 전송).
 */
export function buildProjectMetaPatch(
  baseline: ProjectMetaDraft,
  draft: ProjectMetaDraft,
): UpdateProjectRequest {
  const patch: UpdateProjectRequest = {};
  for (const field of EDITABLE_PROJECT_FIELDS) {
    const next = draft[field].trim();
    if (next === baseline[field].trim()) continue;
    patch[field] = next === '' ? null : next;
  }
  return patch;
}
