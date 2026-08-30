import { describe, expect, it } from 'vitest';

import { diffEntries, siblingCandidates } from '@/routes/reference-data/diff';

import type { ReferenceEntryRecord, ReferenceRevisionSummary } from '@/api/platform-client';

/**
 * 참조 데이터 diff — **서버가 준 값만으로** 판정한다 (2026-08-08).
 *
 * 시험원이 "무엇이 달라지나"를 모른 채 게시를 누르면 그것은 검토가 아니라 도박이다.
 * 그래서 화면은 diff 를 보여주지만, 그 판정을 **다시 계산하지 않는다**: 조인 키는
 * 서버 `identity_key`, 변경 판정은 서버 `content_sha256` 비교뿐이다.
 *
 * 여기서 payload 를 해싱하면 같은 규칙이 Python 과 TypeScript 두 언어로 쪼개지고,
 * 그 드리프트는 시험원이 **게시한 뒤에야** 드러난다. 그래서 아래 테스트는 payload 를
 * 일부러 **서로 다르게** 두고 지문만 같게 만든다 — 함수가 지문을 믿는지, 아니면
 * 몰래 내용을 비교하는지를 가르는 유일한 입력이다.
 */

function entry(
  identityKey: string,
  fingerprint: string,
  payload: Record<string, unknown> = {},
): ReferenceEntryRecord {
  return {
    reference_id: `${identityKey}#${fingerprint}`,
    identity_key: identityKey,
    entry_order: 0,
    payload,
    content_sha256: fingerprint,
  };
}

describe('diffEntries', () => {
  it('joins on the server identity key, not on array position', () => {
    const candidate = [entry('b', 'x'), entry('a', 'y')];
    const published = [entry('a', 'y'), entry('b', 'x')];

    const result = diffEntries(candidate, published);

    expect(result.unchanged).toBe(2);
    expect(result.added).toBe(0);
    expect(result.removed).toBe(0);
    expect(result.changed).toBe(0);
  });

  it('trusts the server fingerprint rather than re-comparing the payload', () => {
    // 같은 지문, 다른 payload — 내용을 몰래 비교하는 구현이면 'changed' 가 된다.
    const candidate = [entry('a', 'same', { correction_db: -1.5 })];
    const published = [entry('a', 'same', { correction_db: -9.9 })];

    expect(diffEntries(candidate, published).unchanged).toBe(1);
  });

  it('reports a differing fingerprint as changed even when the payload matches', () => {
    const shared = { correction_db: -1.5 };
    const candidate = [entry('a', 'new', shared)];
    const published = [entry('a', 'old', shared)];

    const result = diffEntries(candidate, published);
    expect(result.changed).toBe(1);
    expect(result.unchanged).toBe(0);
  });

  it('classifies additions and removals by identity presence', () => {
    const candidate = [entry('a', 'x'), entry('new', 'z')];
    const published = [entry('a', 'x'), entry('gone', 'w')];

    const result = diffEntries(candidate, published);

    expect(result.added).toBe(1);
    expect(result.removed).toBe(1);
    expect(result.unchanged).toBe(1);
    expect(result.rows.find((row) => row.identityKey === 'new')?.published).toBeNull();
    expect(result.rows.find((row) => row.identityKey === 'gone')?.candidate).toBeNull();
  });

  it('an empty baseline is every entry added — not "no change"', () => {
    // 화면은 baseline 부재를 별도 문구로 처리하지만, 함수 자체가 그 둘을 뭉개면
    // 최초 게시가 "변경 없음"으로 읽힐 여지가 남는다.
    expect(diffEntries([entry('a', 'x')], []).added).toBe(1);
    expect(diffEntries([entry('a', 'x')], []).unchanged).toBe(0);
  });
});

function revision(
  revisionId: string,
  family: string,
  scopeId: string,
  state: string,
): ReferenceRevisionSummary {
  return {
    revision_id: revisionId,
    family,
    scope_id: scopeId,
    state,
    revision_number: 1,
  } as ReferenceRevisionSummary;
}

describe('siblingCandidates', () => {
  /**
   * 짝 패밀리 이름은 **인자로 받는다** — 이 함수는 짝이 무엇인지 모른다. 결합 사실은
   * 백엔드 도메인 SSOT 에 있고 서버가 `coupled_with` 로 알려준다. 그래서 아래 입력의
   * 패밀리 이름은 실제 도메인 어휘가 아니어도 무방하며, 그것이 이 설계의 증거다.
   */
  it('keeps only same-scope candidates of the named sibling family', () => {
    const rows = [
      revision('r1', 'sibling', 'room-1', 'CANDIDATE'),
      revision('r2', 'sibling', 'room-2', 'CANDIDATE'),
      revision('r3', 'other', 'room-1', 'CANDIDATE'),
      revision('r4', 'sibling', 'room-1', 'PUBLISHED'),
    ];

    expect(siblingCandidates(rows, 'sibling', 'room-1').map((row) => row.revision_id)).toEqual([
      'r1',
    ]);
  });

  it('rejects a different room — that pairing is the defect the coupling prevents', () => {
    const rows = [revision('r2', 'sibling', 'room-2', 'CANDIDATE')];
    expect(siblingCandidates(rows, 'sibling', 'room-1')).toEqual([]);
  });

  it('does not invent a sibling when none is named', () => {
    const rows = [revision('r1', 'sibling', 'room-1', 'CANDIDATE')];
    expect(siblingCandidates(rows, '', 'room-1')).toEqual([]);
  });
});
