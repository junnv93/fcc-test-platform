import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import {
  buildProjectMetaPatch,
  EDITABLE_PROJECT_FIELDS,
  isProjectMetaField,
  projectMetaDraftFrom,
  type ProjectMetaDraft,
  type ProjectMetaField,
} from '@/shared/project-meta-patch';

import type { ProjectEnvelope } from '@/api/platform-client';

/**
 * W3-B M2 (2026-07-30) — 표지 메타 부분 갱신 diff 봉인 (S1 · S2).
 *
 * `PATCH /platform/projects/{id}` 은 **키의 존재 여부가 명령**이다: 키가 없으면
 * 불변, `null` 이면 삭제. 두 명령을 뭉개는 순간 lost update(다른 사람이 바꾼 칸을
 * 내 낡은 값으로 덮어쓰기)와 "비운 칸이 지움인지 안 건드림인지 모름"이 동시에
 * 생긴다. 그래서 이 파일의 단정은 **전부 2겹**이다:
 *
 *   `toStrictEqual(...)` + `Object.keys(...)`
 *
 * `toEqual`/`toHaveBeenCalledWith` 는 **값이 `undefined` 인 실존 키를 통과시킨다**
 * (실측 기록: `tests/platform-client.test.ts` W3-B M1 블록). `{applicant_address: undefined}`
 * 는 `toEqual({})` 를 통과하지만 와이어에는 키가 실존한다 — 그게 정확히 봉인해야
 * 하는 결함이므로 느슨한 매처로는 공허해진다.
 */

const APPS_WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const PLATFORM_TYPES_PATH = resolve(
  APPS_WEB_ROOT,
  'src',
  'api',
  'generated',
  'platform-api.types.ts',
);

/** 편집 8칸이 전부 채워진 서버 행. */
function project(over: Partial<ProjectEnvelope> = {}): ProjectEnvelope {
  return {
    project_id: '11111111-1111-4111-8111-111111111111',
    project_code: 'SM-S921U',
    model_name: 'SM-S921U',
    sample_count: 0,
    status: 'active',
    fcc_id: 'A3LSMS921U',
    management_number: '4792232056',
    applicant_name: 'ACME Corp.',
    applicant_address: '1 Main St',
    manufacturer: 'ACME Mfg.',
    fcc_grantee_code: 'A3L',
    eut_description: 'Smartphone',
    test_standard: 'FCC Part 15',
    ...over,
  };
}

/**
 * 특정 칸의 **키 자체가 없는** 서버 행.
 *
 * OpenAPI 가 이 칸들을 optional 로 내보내므로 "미기재"의 실제 와이어 표현은
 * `field: undefined` 가 아니라 **키 부재**다(`exactOptionalPropertyTypes: true` 가
 * 명시적 `undefined` 전달을 컴파일 단계에서 거절하는 이유이기도 하다).
 */
function projectMissing(...fields: readonly (keyof ProjectEnvelope)[]): ProjectEnvelope {
  const row: Record<string, unknown> = { ...project() };
  for (const field of fields) delete row[field];
  return row as ProjectEnvelope;
}

/** 모든 칸이 빈 draft — 개별 칸만 바꿔 넣어 diff 를 좁게 만드는 데 쓴다. */
function blankDraft(): Record<ProjectMetaField, string> {
  const draft = {} as Record<ProjectMetaField, string>;
  for (const field of EDITABLE_PROJECT_FIELDS) draft[field] = '';
  return draft;
}

function withField(
  base: ProjectMetaDraft,
  field: ProjectMetaField,
  value: string,
): ProjectMetaDraft {
  return { ...base, [field]: value };
}

describe('EDITABLE_PROJECT_FIELDS — 생성 타입 파생 (비-공허 drift gate)', () => {
  /**
   * 손수 적은 union 이 아님을 **런타임에** 증명한다. `Record<keyof
   * UpdateProjectRequest, true>` witness 는 컴파일러가 양방향 검사하지만
   * (`npm run typecheck`), 그 사실 자체는 테스트가 보지 못한다. 그래서 생성
   * 아티팩트의 `UpdateProjectRequest` 블록을 직접 파싱해 키 집합이 **정확히 일치**
   * 하는지 본다 — 백엔드가 표지 칸을 추가/제거하면 여기서 loud fail.
   */
  function generatedUpdateFields(): string[] {
    const source = readFileSync(PLATFORM_TYPES_PATH, 'utf8');
    const start = source.indexOf('UpdateProjectRequest: {');
    expect(start).toBeGreaterThan(-1);
    const block = source.slice(start, source.indexOf('};', start));
    // 캡처 그룹을 구조분해 + 명시 guard 로 좁힌다. `m[1] as string` 은
    // `non-nullable-type-assertion-style` 이, `m[1]!` 은 `no-non-null-assertion`
    // 이 거절하므로 둘 다 피하는 형태.
    const fields: string[] = [];
    for (const match of block.matchAll(/^\s{2,}([a-z_]+)\?:/gmu)) {
      const [, name] = match;
      if (name !== undefined) fields.push(name);
    }
    return fields.sort();
  }

  it('covers exactly the generated UpdateProjectRequest keys', () => {
    expect([...EDITABLE_PROJECT_FIELDS].sort()).toStrictEqual(generatedUpdateFields());
  });

  it('is non-empty (a vacuous list would make every diff assertion trivially pass)', () => {
    expect(EDITABLE_PROJECT_FIELDS.length).toBeGreaterThan(0);
  });

  it('excludes project identity + lifecycle columns the backend rejects with 400', () => {
    // model_name/project_code 는 re-key(ADR-0005), status 는 complete/reopen
    // 하위자원 — PATCH 는 400 으로 거절한다. 폼에 실리면 저장이 통째로 실패한다.
    for (const forbidden of ['model_name', 'project_code', 'status', 'fcc_id']) {
      expect(EDITABLE_PROJECT_FIELDS).not.toContain(forbidden);
    }
  });
});

describe('isProjectMetaField — 409 params.field 귀속 allowlist', () => {
  it('accepts every editable field', () => {
    for (const field of EDITABLE_PROJECT_FIELDS) {
      expect(isProjectMetaField(field)).toBe(true);
    }
  });

  it('rejects backend field names that are NOT on this form', () => {
    // `PROJECT_IDENTIFIER_CONFLICT` 는 project_code 충돌로도 발생한다. 폼에 없는
    // 칸이므로 귀속 대상이 아니고, 호출자는 일반 충돌 문구로 폴백해야 한다.
    expect(isProjectMetaField('project_code')).toBe(false);
    expect(isProjectMetaField('model_name')).toBe(false);
    expect(isProjectMetaField('')).toBe(false);
  });

  it('rejects non-string params values without throwing', () => {
    // params 값은 백엔드에서 `Any` 라 타입 보증이 없다 — 방어적으로 좁힌다.
    expect(isProjectMetaField(undefined)).toBe(false);
    expect(isProjectMetaField(null)).toBe(false);
    expect(isProjectMetaField(42)).toBe(false);
    // 프로토타입 오염 경로: `hasOwn` 이라 상속 키는 통과하지 않는다.
    expect(isProjectMetaField('toString')).toBe(false);
    expect(isProjectMetaField('constructor')).toBe(false);
  });
});

describe('projectMetaDraftFrom — 목록 행에서 스냅샷 (N+1 없음)', () => {
  it('maps every editable column onto a string draft', () => {
    const draft = projectMetaDraftFrom(project());
    expect(Object.keys(draft).sort()).toStrictEqual([...EDITABLE_PROJECT_FIELDS].sort());
    expect(draft.applicant_address).toBe('1 Main St');
    expect(draft.fcc_grantee_code).toBe('A3L');
  });

  it('folds explicit null (미기재) to the empty string', () => {
    const draft = projectMetaDraftFrom(
      project({ applicant_address: null, management_number: null }),
    );
    expect(draft.applicant_address).toBe('');
    expect(draft.management_number).toBe('');
  });

  it('folds an ABSENT key to the empty string (optional wire field)', () => {
    const draft = projectMetaDraftFrom(projectMissing('applicant_name', 'test_standard'));
    expect(draft.applicant_name).toBe('');
    expect(draft.test_standard).toBe('');
    // 키 부재도 draft 에는 모든 칸이 존재해야 한다 — 없으면 렌더 시 controlled
    // input 이 `value={undefined}` 로 uncontrolled 로 떨어진다.
    expect(Object.keys(draft).sort()).toStrictEqual([...EDITABLE_PROJECT_FIELDS].sort());
  });

  it('an absent key produces no patch key when left alone (never a spurious null)', () => {
    // 미기재 칸을 건드리지 않았는데 `null` 을 보내면 무의미한 쓰기이고, 그 칸이
    // 다른 사람에 의해 채워졌다면 그것을 지워 버린다.
    const baseline = projectMetaDraftFrom(projectMissing('applicant_name'));
    expect(buildProjectMetaPatch(baseline, { ...baseline })).toStrictEqual({});
  });
});

describe('S1 — 미변경 필드는 키 자체가 붙지 않는다', () => {
  it('sends ONLY the changed key, verbatim', () => {
    const baseline = projectMetaDraftFrom(project());
    const patch = buildProjectMetaPatch(
      baseline,
      withField(baseline, 'applicant_address', 'ACME2'),
    );

    // 2겹 1 — 값 동등.
    expect(patch).toStrictEqual({ applicant_address: 'ACME2' });
    // 2겹 2 — 키 집합. `{applicant_address:'ACME2', manufacturer: undefined}` 는 위 단정을
    // 통과할 수 있지만 와이어에 `manufacturer` 키가 실존한다(= 서버가 그 칸을
    // 건드릴 수 있는 명령).
    expect(Object.keys(patch)).toStrictEqual(['applicant_address']);
    expect(Object.keys(patch)).toHaveLength(1);
  });

  it('returns {} when nothing changed — the caller must skip the request (400)', () => {
    const baseline = projectMetaDraftFrom(project());
    const patch = buildProjectMetaPatch(baseline, { ...baseline });
    expect(patch).toStrictEqual({});
    expect(Object.keys(patch)).toStrictEqual([]);
  });

  it('treats a whitespace-only edit as no change (no key)', () => {
    const baseline = projectMetaDraftFrom(project());
    const patch = buildProjectMetaPatch(
      baseline,
      withField(baseline, 'applicant_address', '  1 Main St  '),
    );
    expect(Object.keys(patch)).toStrictEqual([]);
  });

  it('trims the transmitted value', () => {
    const baseline = projectMetaDraftFrom(project());
    const patch = buildProjectMetaPatch(
      baseline,
      withField(baseline, 'applicant_address', '  ACME2  '),
    );
    expect(patch).toStrictEqual({ applicant_address: 'ACME2' });
  });

  it('is a fixpoint: applying the patch then re-diffing yields {}', () => {
    // 멱등 — 저장 후 서버가 돌려준 값으로 baseline 을 다시 뜨면 diff 가 빈다.
    const baseline = projectMetaDraftFrom(project());
    const draft = withField(baseline, 'applicant_address', 'ACME2');
    const saved = projectMetaDraftFrom(project({ applicant_address: 'ACME2' }));
    expect(buildProjectMetaPatch(baseline, draft)).toStrictEqual({ applicant_address: 'ACME2' });
    expect(buildProjectMetaPatch(saved, saved)).toStrictEqual({});
  });

  it('sends every key when the operator really did edit every field', () => {
    // 비-공허 대조: "항상 키를 안 붙인다" 로 S1 을 통과시키는 구현을 배제한다.
    const baseline = blankDraft();
    const draft = {} as Record<ProjectMetaField, string>;
    for (const field of EDITABLE_PROJECT_FIELDS) draft[field] = `v-${field}`;
    const patch = buildProjectMetaPatch(baseline, draft);
    expect(Object.keys(patch).sort()).toStrictEqual([...EDITABLE_PROJECT_FIELDS].sort());
  });
});

describe('S2 — 값이 있었는데 비운 필드만 null, 나머지 키는 부재', () => {
  it('clears exactly one field and leaves the other seven untouched', () => {
    const baseline = projectMetaDraftFrom(project());
    const patch = buildProjectMetaPatch(baseline, withField(baseline, 'applicant_name', ''));

    // `null` 은 "이 칸을 지워라"는 명령이고 키 부재는 "손대지 말라"다 — 둘은
    // 절대 서로 대체될 수 없다.
    expect(patch).toStrictEqual({ applicant_name: null });
    expect(Object.keys(patch)).toStrictEqual(['applicant_name']);
    for (const field of EDITABLE_PROJECT_FIELDS) {
      if (field === 'applicant_name') continue;
      expect(Object.hasOwn(patch, field)).toBe(false);
    }
  });

  it('distinguishes null (clear) from an absent key (leave) in ONE patch', () => {
    const baseline = projectMetaDraftFrom(project());
    const patch = buildProjectMetaPatch(baseline, {
      ...baseline,
      applicant_name: '',
      applicant_address: 'ACME2',
    });
    expect(patch).toStrictEqual({ applicant_name: null, applicant_address: 'ACME2' });
    expect(Object.keys(patch).sort()).toStrictEqual(['applicant_address', 'applicant_name']);
  });

  it('does NOT send null for a field that was already empty on the server', () => {
    // 이미 비어 있던 칸을 비워 두는 것은 변경이 아니다 — `null` 을 보내면 무의미한
    // 쓰기이고, 최소 diff 원칙(그리고 lost-update 표면 최소화)에도 어긋난다.
    const baseline = projectMetaDraftFrom(project({ applicant_name: null }));
    const patch = buildProjectMetaPatch(baseline, { ...baseline });
    expect(patch).toStrictEqual({});
  });

  it('whitespace-only input clears a populated field (trim ⇒ empty ⇒ null)', () => {
    const baseline = projectMetaDraftFrom(project());
    const patch = buildProjectMetaPatch(baseline, withField(baseline, 'applicant_address', '   '));
    expect(patch).toStrictEqual({ applicant_address: null });
  });
});
