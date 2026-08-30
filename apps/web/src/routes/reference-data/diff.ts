import type { ReferenceEntryRecord, ReferenceRevisionSummary } from '@/api/platform-client';

/**
 * 후보 리비전을 직전 게시본과 대조한다 — **서버가 준 값만으로**.
 *
 * 시험원이 "무엇이 달라지나"를 모른 채 게시를 누르면 그것은 검토가 아니라 도박이다.
 * 그래서 이 화면은 diff 를 보여주지만, 그 판정을 **다시 계산하지 않는다**:
 *
 * - 조인 키는 서버의 `identity_key`(패밀리별 자연키에서 도메인이 파생).
 * - 변경 판정은 서버의 `content_sha256` **비교**뿐이다. 여기서 payload 를 해싱하면
 *   같은 규칙이 Python 과 TypeScript 두 언어로 쪼개지고, 그 드리프트는 시험원이
 *   **게시한 뒤에야** 드러난다(§Derived-Value No-Client-Recompute SSOT).
 *
 * 순수 함수로 분리한 이유는 렌더링 없이 단위 검증이 가능하기 때문이고, 그것이
 * "재계산 0"을 주장이 아니라 실행으로 확인할 수 있게 한다.
 */

/** 한 엔트리가 직전 게시본 대비 어떻게 되었는가. */
export type EntryChangeKind = 'added' | 'removed' | 'changed' | 'unchanged';

export interface EntryDiffRow {
  readonly identityKey: string;
  readonly kind: EntryChangeKind;
  /** 후보 쪽 엔트리. `removed` 이면 null. */
  readonly candidate: ReferenceEntryRecord | null;
  /** 직전 게시본 쪽 엔트리. `added` 이면 null. */
  readonly published: ReferenceEntryRecord | null;
}

export interface EntryDiffSummary {
  readonly rows: readonly EntryDiffRow[];
  readonly added: number;
  readonly removed: number;
  readonly changed: number;
  readonly unchanged: number;
}

/**
 * 직전 게시본이 **없을 때**와 "전부 그대로일 때"는 다르다.
 *
 * 전자는 비교할 대상이 없다는 뜻이고 후자는 비교했더니 같다는 뜻이다. 둘을 같은
 * 화면으로 접으면 시험원은 최초 게시를 "변경 없음"으로 읽는다. 그래서 baseline 이
 * 없으면 이 함수는 호출되지 않고, 화면이 다른 문구를 쓴다.
 */
export function diffEntries(
  candidate: readonly ReferenceEntryRecord[],
  published: readonly ReferenceEntryRecord[],
): EntryDiffSummary {
  const publishedByIdentity = new Map<string, ReferenceEntryRecord>();
  for (const entry of published) {
    publishedByIdentity.set(entry.identity_key, entry);
  }

  const rows: EntryDiffRow[] = [];
  const seen = new Set<string>();

  for (const entry of candidate) {
    seen.add(entry.identity_key);
    const previous = publishedByIdentity.get(entry.identity_key) ?? null;
    if (previous === null) {
      rows.push({
        identityKey: entry.identity_key,
        kind: 'added',
        candidate: entry,
        published: null,
      });
      continue;
    }
    rows.push({
      identityKey: entry.identity_key,
      kind: previous.content_sha256 === entry.content_sha256 ? 'unchanged' : 'changed',
      candidate: entry,
      published: previous,
    });
  }

  for (const entry of published) {
    if (seen.has(entry.identity_key)) {
      continue;
    }
    rows.push({
      identityKey: entry.identity_key,
      kind: 'removed',
      candidate: null,
      published: entry,
    });
  }

  const count = (kind: EntryChangeKind): number =>
    rows.reduce((total, row) => (row.kind === kind ? total + 1 : total), 0);

  return {
    rows,
    added: count('added'),
    removed: count('removed'),
    changed: count('changed'),
    unchanged: count('unchanged'),
  };
}

/**
 * 결합 그룹의 나머지 반쪽 후보를 고른다.
 *
 * 짝 어휘는 **서버가 준다** — 상세 응답의 `coupled_with`. 결합 사실은 백엔드 도메인
 * SSOT 에 있고, 여기서 패밀리 이름을 적으면 그 어휘가 두 곳이 된다. 거부 메시지에서
 * 형제 이름을 파싱하는 것도 같은 결함의 다른 얼굴이다(사람이 읽으라고 쓴 문장을
 * 기계가 파싱하는 결합). 이 함수는 짝이 무엇인지 **모르고**, 받은 이름으로 거른다.
 *
 * 같은 방(scope)의 CANDIDATE 만 후보다: 다른 방의 반쪽을 함께 게시하면 한 방의
 * 포트맵에 다른 방의 케이블 손실이 붙어, 결합이 막으려던 바로 그 결함이 결합을
 * 지키는 척하며 발생한다. 서버도 같은 이유로 그것을 거부한다.
 */
export function siblingCandidates(
  revisions: readonly ReferenceRevisionSummary[],
  siblingFamily: string,
  scopeId: string,
): readonly ReferenceRevisionSummary[] {
  return revisions.filter(
    (revision) =>
      revision.family === siblingFamily &&
      revision.scope_id === scopeId &&
      revision.state === 'CANDIDATE',
  );
}
