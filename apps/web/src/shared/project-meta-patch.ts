/**
 * project-meta-patch — 성적서 표지 메타 부분 갱신 diff + **폼 스테이지** SSOT.
 *
 * `PATCH /platform/projects/{id}` 의 의미론은 **키의 존재 여부가 곧 명령**이다:
 *
 *   | 와이어                       | 백엔드 동작            |
 *   |------------------------------|------------------------|
 *   | 키 자체가 없음               | 그 칸 **불변**         |
 *   | `"applicant_name": null`     | 그 칸 **삭제**         |
 *   | `"applicant_name": "ACME"`   | 그 칸 **설정**         |
 *
 * 즉 `undefined` 와 `null` 이 **다른 명령**이다. 폼이 "현재 값을 전부 담아 보내는"
 * 흔한 구현을 하면 두 가지가 동시에 깨진다: (1) 그 사이 다른 사람이 바꾼 칸을 내
 * 화면의 낡은 값으로 덮어쓴다(lost update — 백엔드
 * `central_project_service.update_project_metadata` 가 방어하지 않는다고 명시),
 * (2) 사용자가 비운 칸이 "지움"인지 "안 건드림"인지 서버가 구분할 수 없다.
 *
 * 그래서 이 모듈이 **원본 스냅샷 대비 dirty diff** 를 단일 순수 지점에서 만든다.
 *
 * ## 폼 스테이지 (2026-09-04)
 *
 * 표지 메타는 **확정되는 시점이 다르다**. 관리번호·신청자는 접수 때 이미 있고,
 * grantee code·EUT 설명·시험 규격은 성적서를 쓸 때가 되어야 정해진다. 그전까지 생성
 * 폼이 그 전부를 한 번에 물었고, 사용자는 "지금 알 수 없는 값" 앞에서 멈췄다 —
 * 그 칸들은 결국 영구 공란으로 남았다.
 *
 * 그래서 필드 집합에 **스테이지 축**을 세운다(백엔드 커널
 * `project_metadata_edit.PROJECT_META_FIELD_STAGES` 와 같은 축):
 *
 * - {@link INTAKE_META_FIELDS} — 프로젝트 생성 폼이 묻는다.
 * - {@link REPORT_META_FIELDS} — 성적서 화면이 묻는다.
 *
 * 스테이지는 **화면 배치이지 권한이 아니다.** 어느 칸이든 두 화면 모두에서 편집
 * 가능하고, PATCH 계약은 조금도 좁아지지 않는다.
 *
 * ## 무엇이 손으로 적혀 있고, 무엇이 파생인가
 *
 * 필드 **집합**은 전부 생성 타입에서 파생한다(백엔드 스키마 → OpenAPI →
 * `npm run codegen` → 여기). 필수 여부조차 파생이다 — OpenAPI 의 `required` 는
 * 생성 타입에서 optional 마커(`?`)의 부재로 드러나므로 {@link RequiredCreateField}
 * 가 그것을 타입 수준에서 읽어낸다. 손으로 적는 것은 **스테이지 분류 하나뿐**이고,
 * 그마저 witness 객체라 컴파일러가 누락·초과를 양방향으로 잡는다.
 */
import type {
  ApplicantSuggestionEnvelope,
  CreateProjectRequest,
  ProjectEnvelope,
  UpdateProjectRequest,
} from '@/api/platform-client';

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
 * 폼 스테이지 — 그 칸을 **어느 화면이 묻는가**.
 *
 * `'intake'` 는 프로젝트를 개설하는 시점에 이미 답이 있는 칸, `'report'` 는 성적서를
 * 쓸 때가 되어야 답이 생기는 칸이다.
 */
export type ProjectMetaStage = 'intake' | 'report';

/**
 * 편집 필드 exhaustiveness witness — **스테이지 분류이자 화면 표시 순서의 SSOT.**
 *
 * TS 타입은 런타임에 사라지므로 `keyof UpdateProjectRequest` 를 배열로 만들려면 이
 * 다리가 필요하다. `Record<ProjectMetaField, ProjectMetaStage>` 로 못 박아 두면
 * 컴파일러가 양방향으로 검사한다: 백엔드가 필드를 **추가**하면 키 누락으로 컴파일
 * 실패, **제거**하면 초과 프로퍼티로 컴파일 실패. 그래서 손으로 적은 두 번째 진실
 * 원천과 달리 조용히 드리프트할 수 없다 — 새 필드는 반드시 **어느 스테이지에
 * 속하는지 답해야** 트리에 들어온다.
 *
 * 리터럴의 나열 순서가 그대로 폼 렌더 순서다(접수증을 읽는 순서: 번호 → 신청 주체
 * → 그 주소 → 제조사, 그다음 성적서 표지 순서: 인증 → 대상 → 규격).
 */
const EDITABLE_FIELD_STAGES: Readonly<Record<ProjectMetaField, ProjectMetaStage>> = {
  management_number: 'intake',
  applicant_name: 'intake',
  applicant_address: 'intake',
  manufacturer: 'intake',
  fcc_grantee_code: 'report',
  eut_description: 'report',
  test_standard: 'report',
};

/** 편집 가능한 표지 메타 필드 — 폼 렌더 순서이자 diff 순회 순서. */
export const EDITABLE_PROJECT_FIELDS: readonly ProjectMetaField[] = Object.keys(
  EDITABLE_FIELD_STAGES,
) as readonly ProjectMetaField[];

/**
 * 한 스테이지의 필드를 선언 순서로 돌려준다 — 각 폼이 자기 칸 목록을 **파생**하는
 * 지점이다. 화면이 `['management_number', …]` 를 손으로 적으면 그것이 두 번째
 * SSOT 가 되고, 스테이지가 바뀌어도 조용히 옛 목록을 그린다.
 */
export function stageProjectFields(stage: ProjectMetaStage): readonly ProjectMetaField[] {
  return EDITABLE_PROJECT_FIELDS.filter((field) => EDITABLE_FIELD_STAGES[field] === stage);
}

/** 프로젝트 생성 폼이 묻는 칸 (접수 스테이지). */
export const INTAKE_META_FIELDS: readonly ProjectMetaField[] = stageProjectFields('intake');

/** 성적서 화면이 묻는 칸 (성적서 스테이지). */
export const REPORT_META_FIELDS: readonly ProjectMetaField[] = stageProjectFields('report');

/**
 * 생성 요청에서 **필수**인 키 — 계약에서 타입 수준으로 파생한다.
 *
 * OpenAPI 의 `required` 는 생성 타입에서 optional 마커(`?`)의 **부재**로 나타난다.
 * `Pick<T, K> extends Required<Pick<T, K>>` 는 그 마커의 유무를 그대로 묻는다:
 * 필수 키라면 두 타입이 같아 참이고, optional 키라면 값이 없을 수 있어 거짓이다.
 * 백엔드가 required 를 늘리거나 줄이면 이 union 이 즉시 따라 바뀌고, 아래 witness
 * 가 컴파일 오류로 알린다.
 *
 * (같은 판정을 `{}`/`Record<string, never>` 로 쓰는 관용구가 흔하지만, 이 저장소는
 * 그 형태를 금지한다 — 자유형 매핑을 never 로 좁히는 우회와 구문이 같아서, 봉인이
 * 둘을 구분할 수 없기 때문이다. `Required` 판정은 의도도 더 직접적으로 말한다.)
 */
export type RequiredCreateField = {
  [K in keyof CreateProjectRequest]-?: Pick<CreateProjectRequest, K> extends Required<
    Pick<CreateProjectRequest, K>
  >
    ? K
    : never;
}[keyof CreateProjectRequest];

/**
 * 필수 키의 런타임 witness. 값이 아니라 **키 집합**이 계약이고, 타입이 그것을
 * 강제한다 — 필수가 아닌 키를 여기 적으면 초과 프로퍼티로, 필수인 키를 빠뜨리면
 * 누락으로 컴파일이 깨진다.
 */
const REQUIRED_CREATE_WITNESS: Readonly<Record<RequiredCreateField, true>> = {
  model_name: true,
  management_number: true,
  applicant_name: true,
};

/** 생성 폼에서 별표(*)가 붙고 제출을 막는 칸. */
export const REQUIRED_CREATE_FIELDS: readonly RequiredCreateField[] = Object.keys(
  REQUIRED_CREATE_WITNESS,
) as readonly RequiredCreateField[];

/** 임의의 편집 필드가 생성 시 필수인지 — 폼이 `*` 를 붙일지 판정하는 술어. */
export function isRequiredCreateField(field: string): field is RequiredCreateField {
  return Object.hasOwn(REQUIRED_CREATE_WITNESS, field);
}

/**
 * 신청자를 고르면 **함께 채워지는** 칸 — 제안 envelope 과 편집 필드의 **교집합**이다.
 *
 * 목록을 손으로 고르지 않는다: 서버가 제안에 실어 보내는 칸이 곧 채울 수 있는 칸이고,
 * 거기에 없는 것(관리번호 — 프로젝트마다 UNIQUE 라 물려받으면 그 자리에서 409)은
 * 애초에 도달할 수 없다. 서버가 제안 필드를 늘리면 이 타입이 자동으로 넓어진다.
 */
export type ApplicantFillField = Extract<keyof ApplicantSuggestionEnvelope, ProjectMetaField>;

/**
 * 자동 채움 대상의 런타임 목록 — 편집 필드 순서를 그대로 따른다(폼에 나타나는
 * 순서대로 채워져야 사용자가 무엇이 바뀌었는지 눈으로 따라갈 수 있다).
 *
 * 술어가 타입 가드라, 제안 envelope 에서 사라진 필드를 여기 남겨 두면 컴파일이
 * 깨진다 — 목록이 아니라 **교집합의 런타임 투영**이라는 뜻이다.
 */
export const APPLICANT_FILL_FIELDS: readonly ApplicantFillField[] = EDITABLE_PROJECT_FIELDS.filter(
  (field): field is ApplicantFillField =>
    field === 'applicant_name' || field === 'applicant_address' || field === 'manufacturer',
);

/**
 * 제안 하나를 draft 에 적용한다 — 신청자를 고르면 따라오는 칸을 그 값으로 덮는다.
 *
 * 순수 함수인 것이 요점이다: "무엇이 채워지는가"는 {@link APPLICANT_FILL_FIELDS}
 * (= 제안 envelope ∩ 편집 필드)가 정하고, 이 함수는 그 목록을 순회할 뿐이라 화면이
 * 채움 규칙을 두 번째로 구현할 자리가 없다.
 *
 * 제안에 없는 값(`null`)은 `''` 로 들어간다 — 그래야 "신청자를 바꿨는데 옛 주소가
 * 남아 있는" 상태가 생기지 않는다. 그 상태는 사용자가 눈치채지 못한 채 남의 주소를
 * 성적서 표지에 싣게 만든다.
 */
export function applyApplicantSuggestion(
  draft: ProjectMetaDraft,
  suggestion: ApplicantSuggestionEnvelope,
): ProjectMetaDraft {
  const next: Record<ProjectMetaField, string> = { ...draft };
  for (const field of APPLICANT_FILL_FIELDS) {
    next[field] = suggestion[field] ?? '';
  }
  return next;
}

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
  return typeof value === 'string' && Object.hasOwn(EDITABLE_FIELD_STAGES, value);
}

/**
 * 서버 행에서 편집 스냅샷을 뜬다.
 *
 * 인자는 **편집 필드를 나르는 무엇이든**이다(`Pick`). 목록 행으로 좁혀 두면 같은
 * 스냅샷을 상세 행(`ProjectDetailEnvelope`)에서 뜰 수 없어, 성적서 화면이 두 번째
 * 변환을 손으로 쓰게 된다 — 이 함수가 막으려는 바로 그 사본이다.
 *
 * 목록 행으로도 충분하다는 사실은 여전히 중요하다: `ProjectEnvelope` 이 편집 필드
 * 전부와 파생값 `fcc_id` 를 이미 나르므로 카드마다 상세를 조회하는 N+1 이 없다.
 *
 * `null`/`undefined`(미기재) → `''`. 서버의 "값 없음"과 사용자가 비운 칸이 같은
 * 표현으로 모이므로, 왕복 후 diff 가 저절로 빈다(멱등).
 */
export function projectMetaDraftFrom(
  project: Pick<ProjectEnvelope, ProjectMetaField>,
): ProjectMetaDraft {
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
 *
 * `fields` 로 순회 범위를 좁힐 수 있다 — 한 화면이 **자기 스테이지 칸만** 보여줄 때,
 * 화면에 없는 칸을 diff 에 넣으면 사용자가 보지도 못한 값을 저장하게 된다.
 * 생략하면 편집 필드 전부를 본다.
 */
export function buildProjectMetaPatch(
  baseline: ProjectMetaDraft,
  draft: ProjectMetaDraft,
  fields: readonly ProjectMetaField[] = EDITABLE_PROJECT_FIELDS,
): UpdateProjectRequest {
  const patch: UpdateProjectRequest = {};
  for (const field of fields) {
    const next = draft[field].trim();
    if (next === baseline[field].trim()) continue;
    patch[field] = next === '' ? null : next;
  }
  return patch;
}

/**
 * 생성 요청 본문을 만든다 — 필수 칸은 문자열로, 나머지는 **채운 칸만** 싣는다.
 *
 * 필수 칸이 비어 있으면 `null` 을 돌려준다: 그 상태에서 요청을 보내면 400 이고,
 * 폼이 그 전에 막아야 한다(제출 버튼 비활성의 근거가 곧 이 판정이다). 판정과 조립을
 * 한 함수에 두는 이유는, 둘이 갈라지면 "버튼은 눌리는데 서버가 거절하는" 상태가
 * 생기기 때문이다.
 *
 * 선택 칸의 "빈 칸은 키 생략"은 편집 경로와 **같은 diff 한 곳**을 지난다.
 */
export function buildCreateProjectBody(
  modelName: string,
  meta: ProjectMetaDraft,
): CreateProjectRequest | null {
  const model = modelName.trim();
  if (model === '') return null;
  for (const field of REQUIRED_CREATE_FIELDS) {
    if (field === 'model_name') continue;
    if (meta[field].trim() === '') return null;
  }
  // 선택 칸은 빈 baseline 대비 diff 와 정확히 같은 계산이다(빈 칸 = 키 생략).
  // 필수 칸은 위에서 non-empty 가 확인되었으므로 그 diff 에 문자열로 실린다.
  const optional = buildProjectMetaPatch(EMPTY_PROJECT_META_DRAFT, meta);
  return { ...optional, model_name: model } as CreateProjectRequest;
}
