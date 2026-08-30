import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import {
  fetchChambers,
  fetchReferenceRevision,
  fetchReferenceRevisions,
  fetchProviderList,
  createAuthoredReferenceRevision,
  fetchReferenceFamilies,
  forkReferenceRevision,
  publishReferenceRevision,
  updateReferenceRevisionEntries,
  updateReferenceRevisionRows,
  type ReferenceFamily,
  type ReferenceRevisionState,
  type ReferenceRevisionSummary,
} from '@/api/platform-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { useT } from '@/i18n';
import {
  BlockSkeleton,
  Button,
  Card,
  DataTable,
  type DataTableColumn,
  DataTableSkeleton,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  PageHeader,
  SectionBand,
  StatusBadge,
  StatusMessage,
  Toolbar,
  WorkbenchLayout,
} from '@/ui';

import { diffEntries, siblingCandidates } from './diff';

/**
 * 참조 데이터 워크벤치 — 워크북에서 장부로 (2026-08-08).
 *
 * 측정에 쓰이는 참조값(주파수표 · 분석기 설정 · 안테나 게인 · 케이블 손실 ·
 * 스위치 포트맵 · Test Info)의 **권위 있는 원본은 중앙 플랫폼**이고 챔버 PC 는
 * 복제본이다. 이 화면이 없으면 게시를 누를 자리가 없고, 게시가 0이면 모든 패밀리가
 * 워크북 소유로 남는다 — 즉 소유권 이전이 배관만 깔린 채 멈춘다.
 *
 * ## 이 화면이 하는 일 셋, 그리고 하지 않는 일들
 *
 * **한다**: 지금 무엇이 게시돼 있는지 본다 → 후보의 내용을 직전 게시본과 대조해
 * 검토한다 → 게시한다.
 *
 * **저작도 한다 (2026-08-09)** — 게시본을 복사해 새 판을 만들고(fork), 그 판의 칸을
 * 고치고, 검토해서 게시한다. 이전에는 후보를 만드는 방법이 운영자 CLI 하나뿐이라
 * 재배선 후 손실을 다시 잰 시험원이 자기 숫자를 넣지 못하고 기다려야 했다.
 *
 * **처음부터 만드는 것도 한다 (2026-08-11)** — 운영자 판정으로 참조값은 처음부터
 * 웹에서 운영되고 워크북은 초기 seed 로만 남으므로, 워크북 없이 태어난 리비전이
 * 정상 경로가 됐다. 그것은 **다른 operation** 이다(`.../reference-revisions/authored`):
 * 어느 operation 이 돌았는지가 곧 provenance 이므로(`WEB_AUTHORED`), 워크북 임포터의
 * 경로에 플래그를 더하면 감사 사실이 요청 모양이 정하는 값이 된다. 워크북 임포트
 * 자체는 여전히 운영자 CLI 의 일이다.
 *
 * **행 추가·삭제도 한다 (2026-08-11)** — 워크북에서 늘 하던 작업이고, 웹에서 안 되면
 * 그 워크북은 사라질 수 없다. 값 편집과 **다른 요청**인 이유는 값 편집 정책이 식별
 * 필드 이동을 거부하는 사유 그 자체다: 그것은 추가+삭제이지 편집이 아니다.
 *
 * **RETIRE 는 없다.** 중앙에 해당 operation 이 없고, 필요하지도 않다 — 게시가 직전
 * 게시본을 **같은 트랜잭션에서** 승계한다. 없는 기능의 버튼은 거짓말이고, 누른
 * 사람은 자기가 한 일이 일어났다고 믿는다.
 *
 * ## 이 화면이 지키는 정직성 규칙
 *
 * 1. **열 순서도 식별 열도 짝 어휘도 서버가 준다** — 상세 응답의 `payload_columns`,
 *    `identity_columns`, `coupled_with`. payload 는 열린 매핑이라 null 필드가 생략될
 *    수 있어 엔트리마다 열 집합이 달라지고, 짝 패밀리나 식별 열을 여기 적으면 그
 *    어휘가 백엔드 도메인과 TS 두 곳으로 쪼개진다. 식별 열 쪽 드리프트는 특히
 *    늦게 드러난다 — 시험원이 식별 칸을 고칠 수 있게 된 뒤, 400 을 받고서야 안다.
 *    거부 메시지에서 형제 이름을 파싱하는 것도 같은 결함의 다른 얼굴이다.
 * 2. **서버 파생값을 다시 계산하지 않는다** — diff 의 변경 판정은 서버
 *    `content_sha256` **비교**뿐이고 조인 키는 서버 `identity_key` 다. 여기서 payload
 *    를 해싱하면 같은 규칙이 두 언어로 쪼개지고, 그 드리프트는 시험원이 **게시한
 *    뒤에야** 드러난다. `state`/`etag`/`revision_number` 도 읽기만 한다.
 * 3. **낙관적 갱신을 하지 않는다** — 게시의 가시적 결과가 전부 서버 파생값이라
 *    낙관적으로 뒤집는 것은 곧 규칙 2 위반이다. 성공하면 무효화만 한다.
 * 4. **토큰으로 잠그지 않는다** — 백엔드 `authorize` 는 토큰 ∪ 프로젝트 멤버십
 *    UNION 이다. 토큰 미보유를 근거로 버튼을 비활성화하면 멤버십으로 권한을 받은
 *    시험원이 부당하게 차단된다. 백엔드를 최종 권위로 두고 403 을 표면화한다.
 * 5. **결합 그룹의 반쪽 게시가 구성상 불가능하다** — 형제 후보를 고르기 전에는
 *    제출 자체가 되지 않고, 제출은 **한 요청**에 두 id 를 싣는다. 서버도 같은
 *    규칙을 집행하지만(경계가 최종 권위), 화면이 그것을 미리 알려주지 못하면
 *    시험원은 409 를 만나고서야 무엇이 필요한지 알게 된다.
 * 6. **게시는 다음 세션부터 발효한다** — 측정 중 correction 곡선 교체는 금지이고
 *    물질화는 세션 부팅 1회다. 그 사실을 문구로 말한다.
 */

/**
 * payload 한 칸을 문자열로 만든다.
 *
 * payload 는 열린 매핑이라 값의 타입이 계약으로 좁혀져 있지 않다. `String()` 을
 * 그대로 쓰면 객체가 `[object Object]` 로 렌더되어 **값이 있는데 없는 것처럼**
 * 보인다 — 참조 데이터 화면에서 그것은 시험원이 잘못된 판단을 내리는 경로다.
 * 비-원시 값은 JSON 으로 보여 최소한 무엇이 들어 있는지 읽히게 한다.
 */
function renderPayloadCell(value: unknown): string {
  if (value === null || value === undefined) {
    return '';
  }
  if (typeof value === 'object') {
    return JSON.stringify(value);
  }
  // eslint-disable-next-line @typescript-eslint/no-base-to-string -- 원시값만 도달한다
  return String(value);
}

/**
 * 편집한 칸을 원래 값과 **같은 종류**로 되돌린다.
 *
 * `<input>` 은 언제나 문자열을 준다. 그것을 그대로 보내면 숫자였던 칸이 문자열이
 * 되어, 시험원이 만지지도 않은 열의 **표현**이 편집 때문에 바뀐다.
 *
 * 읽지 못한 입력은 **원문 그대로 보내지 않는다**(2026-08-09 적대적 리뷰). 그렇게
 * 하던 초판은 숫자 칸을 조용히 문자열로 만들었다 — `'1,5'`(쉼표 소수 구분자, 흔한
 * 입력)가 `Number()` 에서 `NaN` 이라 원문으로 통과했고, 서버에도 투영에도 타입
 * 검사가 없어 correction dB 가 문자열로 저장됐다(실측). 이제 서버가 400 으로
 * 거부하고, 화면은 그 입력을 애초에 보내지 않아 시험원이 저장 전에 안다.
 *
 * 빈 값으로 대신 보내는 안은 기각했다 — 그것은 칸을 조용히 **지우는** 새 침묵
 * 경로이고, 시험원은 여전히 성공을 본다.
 *
 * 파싱은 **10진 표기만** 받는다. `Number()` 는 `'0x10'` 을 16, `'0b101'` 을 5 로
 * 읽는데, dB 칸에 그것을 친 사람이 16 을 의도했을 리 없다 — 언어의 관용이 곧 이
 * 도메인의 관용은 아니다.
 */
const DECIMAL_NUMBER = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

/** 읽었으면 값을, 못 읽었으면 `unreadable` — **조용히 비우지 않는다**. */
type CoercedCell = { readonly value: unknown } | { readonly unreadable: true };

function coerceLikeOriginal(original: unknown, edited: string): CoercedCell {
  const text = edited.trim();
  if (typeof original === 'number') {
    if (!DECIMAL_NUMBER.test(text)) return { unreadable: true };
    const parsed = Number(text);
    return Number.isFinite(parsed) ? { value: parsed } : { unreadable: true };
  }
  if (typeof original === 'boolean') {
    if (text === 'true') return { value: true };
    if (text === 'false') return { value: false };
    return { unreadable: true };
  }
  return { value: edited };
}

/**
 * 새 행의 한 칸 → payload 값.
 *
 * 기존 칸 편집은 **원래 값의 종류**로 되돌리면 됐지만(`coerceLikeOriginal`), 새 행에는
 * 되돌릴 원래 값이 없다. 그래서 규칙은 하나뿐이다 — 10진수로 읽히면 숫자, 아니면
 * 문자열. `Number()` 의 관용(`'0x10'`→16)을 쓰지 않는 이유는 위와 같다: 언어의
 * 관용이 이 도메인의 관용은 아니다.
 *
 * 이것은 파생값의 재계산이 아니라 **사용자 입력의 파싱**이다. 어느 UI든 해야 하는
 * 일이고, 서버는 여전히 모양을 검증하며 정체성·해시는 서버가 민팅한다.
 */
function coerceNewCell(text: string): unknown {
  const trimmed = text.trim();
  if (DECIMAL_NUMBER.test(trimmed)) {
    const parsed = Number(trimmed);
    if (Number.isFinite(parsed)) return parsed;
  }
  return text;
}

/** 빈 새 행 — 서버가 준 열 순서 그대로. */
function blankRow(columns: readonly string[]): Record<string, string> {
  return Object.fromEntries(columns.map((column) => [column, '']));
}

function rowPayload(
  columns: readonly string[],
  draft: Record<string, string>,
): Record<string, unknown> {
  return Object.fromEntries(columns.map((column) => [column, coerceNewCell(draft[column] ?? '')]));
}

/** URL 파라미터 — 편집 중인 화면 상태를 그대로 공유할 수 있게 한다. */
const PROVIDER_PARAM = 'provider';
const SCOPE_PARAM = 'scope';
const FAMILY_PARAM = 'family';
const STATE_PARAM = 'state';
const REVISION_PARAM = 'revision';

export default function ReferenceDataRoute(): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();

  const providerId = params.get(PROVIDER_PARAM) ?? '';
  const scopeId = params.get(SCOPE_PARAM) ?? '';
  const familyFilter = params.get(FAMILY_PARAM) ?? '';
  const stateFilter = params.get(STATE_PARAM) ?? '';
  const revisionId = params.get(REVISION_PARAM) ?? '';

  /** 형제 후보 선택. 목록/스코프를 바꾸면 의미를 잃으므로 함께 지운다. */
  const [siblingId, setSiblingId] = useState('');
  const [publishError, setPublishError] = useState<unknown>(null);

  /**
   * 아직 저장하지 않은 칸 편집 — **`revision_id` → `reference_id`** → (열 → 문자열).
   *
   * 리비전 id 로 한 겹 더 키를 잡는 이유는 편의가 아니다(2026-08-09 적대적 리뷰).
   * `reference_id` 는 전역 유일하지 않다 — 그것은 `identity_key_for(family, row)`
   * 이고 `scope_id` 도 `revision_id` 도 담지 않으므로, **같은 설비 구성을 쓰는 두 방**
   * 은 글자까지 같은 `reference_id` 를 갖는다(표준 구성을 여러 챔버에 쓰는 것이 보통이다).
   * 초안을 그 키만으로 들고 있으면, `setParam` 을 거치지 않는 이동(뒤로/앞으로,
   * 북마크, 다른 화면에서 온 링크)이 A 방에서 친 숫자를 B 방의 같은 이름 행에
   * 적용한다 — 주소는 유효하므로 서버가 받아들이고, 시험원은 하지도 않은 편집이
   * 저장된 것을 본다.
   *
   * 문자열로 들고 있는 이유: `<input>` 이 주는 것이 문자열이고, 여기서 숫자로
   * 바꾸면 이 화면이 payload 값의 타입 규칙을 **두 번째로** 정하게 된다. 서버가
   * 무엇을 받아들이는지는 서버가 안다. 저장 시 원래 값이 숫자였던 칸만 숫자로
   * 되돌려 보내, 편집하지 않은 칸의 표현이 편집 때문에 바뀌지 않게 한다.
   */
  const [drafts, setDrafts] = useState<Record<string, Record<string, Record<string, string>>>>({});
  /** 지금 열린 판의 초안만 본다 — 다른 판의 초안은 존재해도 도달하지 않는다. */
  const openDrafts = drafts[revisionId] ?? {};
  const [writeError, setWriteError] = useState<unknown>(null);

  /**
   * 아직 보내지 않은 **행** 변경 — 삭제 표시와 새 행 초안. 칸 편집과 같은 이유로
   * `revision_id` 로 한 겹 키를 잡는다(`reference_id` 는 전역 유일하지 않다).
   */
  const [removalDrafts, setRemovalDrafts] = useState<Record<string, string[]>>({});
  const [additionDrafts, setAdditionDrafts] = useState<Record<string, Record<string, string>[]>>(
    {},
  );
  const pendingRemovals = removalDrafts[revisionId] ?? [];
  const pendingAdditions = additionDrafts[revisionId] ?? [];
  const hasRowChanges = pendingRemovals.length > 0 || pendingAdditions.length > 0;

  /** 처음부터 만들기 초안 — 어떤 패밀리를, 어느 스코프에, 어떤 첫 행으로. */
  const [createFamily, setCreateFamily] = useState('');
  const [createScopeId, setCreateScopeId] = useState('');
  const [createRow, setCreateRow] = useState<Record<string, string>>({});
  const [createError, setCreateError] = useState<unknown>(null);

  const setParam = (key: string, value: string): void => {
    const next = new URLSearchParams(params);
    if (value) {
      next.set(key, value);
    } else {
      next.delete(key);
    }
    // 상위 축을 바꾸면 하위 선택은 다른 집합을 가리키게 되므로 함께 비운다.
    if (key !== REVISION_PARAM) {
      next.delete(REVISION_PARAM);
    }
    setParams(next, { replace: true });
    setSiblingId('');
    setPublishError(null);
    // 다른 리비전으로 넘어가면 초안은 없는 행을 가리킨다. 남겨두면 다음 저장이
    // 시험원이 보고 있지도 않은 판에 값을 쓴다.
    setDrafts({});
    setWriteError(null);
  };

  const providersQuery = useQuery({
    queryKey: queryKeys.provider.list(),
    queryFn: fetchProviderList,
    ...REFETCH_STRATEGIES.STATIC,
  });

  const chambersQuery = useQuery({
    queryKey: queryKeys.chambers.list(),
    queryFn: fetchChambers,
    ...REFETCH_STRATEGIES.STATIC,
  });

  const listQuery = useQuery({
    queryKey: queryKeys.reference.list(providerId, {
      family: familyFilter,
      scopeId,
      state: stateFilter,
    }),
    queryFn: () =>
      fetchReferenceRevisions(providerId, {
        ...(familyFilter ? { family: familyFilter as ReferenceFamily } : {}),
        ...(scopeId ? { scopeId } : {}),
        ...(stateFilter ? { state: stateFilter as ReferenceRevisionState } : {}),
      }),
    enabled: providerId.length > 0,
    ...REFETCH_STRATEGIES.NORMAL,
  });

  const detailQuery = useQuery({
    queryKey: queryKeys.reference.detail(providerId, revisionId),
    queryFn: () => fetchReferenceRevision(providerId, revisionId),
    enabled: providerId.length > 0 && revisionId.length > 0,
    ...REFETCH_STRATEGIES.NORMAL,
  });

  const revisions = listQuery.data?.items ?? [];
  const detail = detailQuery.data ?? null;

  /**
   * 직전 게시본. 같은 (family, profile, scope) identity 에서 PUBLISHED 인 것 —
   * 서버가 그 조합에 최대 하나만 허용한다.
   */
  const publishedBaseline = useMemo((): ReferenceRevisionSummary | null => {
    if (detail === null) {
      return null;
    }
    const current = detail.revision;
    return (
      revisions.find(
        (row) =>
          row.state === 'PUBLISHED' &&
          row.family === current.family &&
          row.profile_id === current.profile_id &&
          row.scope_id === current.scope_id,
      ) ?? null
    );
  }, [detail, revisions]);

  const baselineQuery = useQuery({
    queryKey: queryKeys.reference.detail(providerId, publishedBaseline?.revision_id ?? ''),
    queryFn: () => fetchReferenceRevision(providerId, publishedBaseline?.revision_id ?? ''),
    enabled: providerId.length > 0 && publishedBaseline !== null,
    ...REFETCH_STRATEGIES.NORMAL,
  });

  const comparison = useMemo(() => {
    if (detail === null || !baselineQuery.isSuccess) {
      return null;
    }
    return diffEntries(detail.entries, baselineQuery.data.entries);
  }, [detail, baselineQuery.isSuccess, baselineQuery.data]);

  const siblings = useMemo(() => {
    if (detail?.coupled_with == null) {
      return [];
    }
    return siblingCandidates(revisions, detail.coupled_with, detail.revision.scope_id);
  }, [detail, revisions]);

  const isCoupled = detail !== null && detail.coupled_with !== null;
  const isCandidate = detail?.revision.state === 'CANDIDATE';
  const isPublished = detail?.revision.state === 'PUBLISHED';
  const canSubmit = isCandidate && (!isCoupled || siblingId.length > 0);

  /** 서버가 식별 열이라고 말한 집합. 여기서 다시 선언하지 않는다. */
  const identityColumns = useMemo(() => new Set(detail?.identity_columns ?? []), [detail]);

  /**
   * 저장할 편집 목록. **바뀐 행만** 싣는다 — correction 곡선은 수천 점이고, 전량
   * 재전송은 낭비이자 lost-update 통로다(재전송에 실린 안 건드린 점들이 그 사이
   * 다른 사람이 바꾼 값을 덮는다).
   *
   * 서버는 payload 의 키 집합이 그 패밀리의 런타임 행과 **정확히 같기를** 요구하므로
   * 원본 payload 위에 편집분만 얹는다. 편집하지 않은 칸은 원래 값 그대로 간다.
   */
  const { pendingEdits, unreadableCells } = useMemo(() => {
    if (detail === null) {
      return { pendingEdits: [], unreadableCells: [] as string[] };
    }
    const unreadable: string[] = [];
    const edits = detail.entries
      .filter((entry) => {
        const draft = openDrafts[entry.reference_id];
        if (draft === undefined) {
          return false;
        }
        const payload = entry.payload as Record<string, unknown>;
        return Object.entries(draft).some(
          ([column, value]) => renderPayloadCell(payload[column]) !== value,
        );
      })
      .map((entry) => {
        const payload = entry.payload as Record<string, unknown>;
        const draft = openDrafts[entry.reference_id] ?? {};
        const next: Record<string, unknown> = { ...payload };
        for (const [column, value] of Object.entries(draft)) {
          const cell = coerceLikeOriginal(payload[column], value);
          if ('unreadable' in cell) {
            unreadable.push(`${entry.reference_id} · ${column}`);
          } else {
            next[column] = cell.value;
          }
        }
        return { reference_id: entry.reference_id, payload: next };
      });
    return { pendingEdits: edits, unreadableCells: unreadable };
  }, [detail, openDrafts]);

  const hasUnsavedEdits = pendingEdits.length > 0;
  const hasUnreadableCells = unreadableCells.length > 0;

  /**
   * 저작 가능한 패밀리와 각각이 요구하는 칸. 리비전이 하나도 없는 패밀리에도
   * 답해야 하므로 상세 응답으로는 얻을 수 없고, 열 목록을 여기 적는 것은 이
   * 화면이 명시적으로 금지하는 일이다(규칙 1).
   */
  const familiesQuery = useQuery({
    queryKey: queryKeys.reference.families(providerId),
    queryFn: () => fetchReferenceFamilies(providerId),
    enabled: providerId !== '',
    ...REFETCH_STRATEGIES.STATIC,
  });
  const families = familiesQuery.data ?? [];
  const createDescriptor = families.find((descriptor) => descriptor.family === createFamily);

  const rowsMutation = useMutation({
    mutationFn: () =>
      updateReferenceRevisionRows(providerId, revisionId, {
        expected_etag: detail?.revision.etag ?? '',
        ...(pendingRemovals.length > 0 ? { removals: pendingRemovals } : {}),
        ...(pendingAdditions.length > 0
          ? {
              additions: pendingAdditions.map((draft) => ({
                payload: rowPayload(detail?.payload_columns ?? [], draft),
              })),
            }
          : {}),
      }),
    onSuccess: () => {
      // 낙관적 갱신 없음(규칙 3) — 행 집합도 etag 도 서버 파생값이다.
      setWriteError(null);
      setRemovalDrafts((current) => ({ ...current, [revisionId]: [] }));
      setAdditionDrafts((current) => ({ ...current, [revisionId]: [] }));
      void queryClient.invalidateQueries({ queryKey: queryKeys.reference.all });
    },
    onError: (error: unknown) => setWriteError(error),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      const descriptor = createDescriptor;
      if (descriptor === undefined) {
        throw new Error('no family selected');
      }
      return createAuthoredReferenceRevision(providerId, {
        family: descriptor.family,
        // 프로필도 스코프 축도 **서버가 준 값**이다 — 화면이 'default' 나
        // 'room'/'project' 를 적으면 그 어휘가 두 언어에 존재하게 된다.
        profile_id: descriptor.default_profile_id,
        scope_kind: descriptor.scope_kind,
        scope_id: createScopeId,
        entries: [{ payload: rowPayload(descriptor.payload_columns, createRow) }],
      });
    },
    onSuccess: (created) => {
      setCreateError(null);
      setCreateRow({});
      void queryClient.invalidateQueries({ queryKey: queryKeys.reference.all });
      setParam(REVISION_PARAM, created.revision.revision_id);
    },
    onError: (error: unknown) => setCreateError(error),
  });

  const forkMutation = useMutation({
    mutationFn: () => forkReferenceRevision(providerId, revisionId),
    onSuccess: (created) => {
      setWriteError(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.reference.all });
      // 새로 만든 판으로 곧바로 이동한다 — fork 의 다음 동작은 언제나 "이 판을
      // 연다"이고, 상세 응답에 열 순서·식별 열·짝까지 실려 오므로 두 번째 왕복이
      // 필요 없다.
      setParam(REVISION_PARAM, created.revision.revision_id);
    },
    onError: (error: unknown) => setWriteError(error),
  });

  const saveMutation = useMutation({
    mutationFn: () =>
      updateReferenceRevisionEntries(providerId, revisionId, {
        // 서버가 준 값을 그대로 되돌려 보낸다 — 조립하지 않는다.
        expected_etag: detail?.revision.etag ?? '',
        edits: pendingEdits,
      }),
    onSuccess: () => {
      // 낙관적 갱신 없음(규칙 3). 저장의 가시적 결과는 전부 서버 파생값이다.
      setWriteError(null);
      setDrafts({});
      void queryClient.invalidateQueries({ queryKey: queryKeys.reference.all });
      void queryClient.invalidateQueries({
        queryKey: queryKeys.reference.detail(providerId, revisionId),
      });
    },
    onError: (error: unknown) => setWriteError(error),
  });

  const publishMutation = useMutation({
    mutationFn: () =>
      publishReferenceRevision(providerId, revisionId, {
        ...(isCoupled ? { coupled_revision_id: siblingId } : {}),
      }),
    onSuccess: () => {
      // 낙관적 갱신 없음 — 게시의 가시적 결과(state/published_at/etag/version)는
      // 전부 서버 파생값이라 여기서 뒤집으면 그것을 클라이언트가 계산하는 셈이다.
      setPublishError(null);
      void queryClient.invalidateQueries({ queryKey: queryKeys.reference.all });
    },
    onError: (error: unknown) => setPublishError(error),
  });

  const columns: DataTableColumn<ReferenceRevisionSummary>[] = [
    {
      key: 'family',
      header: t('routes.referenceData.columns.family'),
      priority: 'primary',
      cell: (row) => row.family,
    },
    {
      key: 'scope',
      header: t('routes.referenceData.columns.scope'),
      priority: 'primary',
      cell: (row) => `${row.scope_kind} · ${row.scope_id}`,
    },
    {
      key: 'revision_number',
      header: t('routes.referenceData.columns.revision'),
      priority: 'detail',
      cell: (row) => String(row.revision_number),
    },
    {
      key: 'state',
      header: t('routes.referenceData.columns.state'),
      priority: 'primary',
      cell: (row) => (
        <StatusBadge status={row.state === 'PUBLISHED' ? 'published' : 'draft'} label={row.state} />
      ),
    },
    {
      key: 'entry_count',
      header: t('routes.referenceData.columns.entryCount'),
      priority: 'detail',
      cell: (row) => String(row.entry_count),
    },
    {
      key: 'created_by',
      header: t('routes.referenceData.columns.createdBy'),
      priority: 'detail',
      cell: (row) => row.created_by,
    },
    {
      key: 'open',
      header: t('routes.referenceData.columns.open'),
      priority: 'primary',
      cell: (row) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setParam(REVISION_PARAM, row.revision_id)}
          data-testid={`reference-open-${row.revision_id}`}
        >
          {t('routes.referenceData.actions.open')}
        </Button>
      ),
    },
  ];

  const errorCopy = {
    forbidden: t('routes.referenceData.error.forbidden'),
    notFound: t('routes.referenceData.error.notFound'),
    serviceUnavailable: t('routes.referenceData.error.unavailable'),
    network: t('routes.referenceData.error.network'),
    default: t('routes.referenceData.error.generic'),
  };

  return (
    <section className="reference-data" aria-labelledby="reference-data-heading">
      <PageHeader
        title={t('routes.referenceData.title')}
        titleId="reference-data-heading"
        description={t('routes.referenceData.description')}
      />

      <p className="section-hint" data-testid="reference-data-effect-note">
        {t('routes.referenceData.effectNote')}
      </p>

      <WorkbenchLayout
        className="reference-data-workbench"
        mainLabel={t('routes.referenceData.title')}
        testId="reference-data-workbench"
        main={
          <>
            <Card
              as="section"
              variant="summary"
              aria-labelledby="reference-data-scope-heading"
              testId="reference-data-scope-card"
            >
              <SectionBand
                title={t('routes.referenceData.scopeSection')}
                titleId="reference-data-scope-heading"
              />
              <Toolbar ariaLabel={t('routes.referenceData.scopeSection')}>
                <FieldGroup
                  label={t('routes.referenceData.fields.provider')}
                  htmlFor="reference-data-provider"
                >
                  <select
                    id="reference-data-provider"
                    data-testid="reference-data-provider-select"
                    value={providerId}
                    onChange={(event) => setParam(PROVIDER_PARAM, event.target.value)}
                  >
                    <option value="">{t('routes.referenceData.fields.anyProvider')}</option>
                    {(providersQuery.data ?? []).map((provider) => (
                      <option key={provider.provider_id} value={provider.provider_id}>
                        {provider.provider_id}
                      </option>
                    ))}
                  </select>
                </FieldGroup>

                <FieldGroup
                  label={t('routes.referenceData.fields.scope')}
                  htmlFor="reference-data-scope"
                >
                  <select
                    id="reference-data-scope"
                    data-testid="reference-data-scope-select"
                    value={scopeId}
                    onChange={(event) => setParam(SCOPE_PARAM, event.target.value)}
                  >
                    <option value="">{t('routes.referenceData.fields.anyScope')}</option>
                    {(chambersQuery.data?.items ?? []).map((chamber) => (
                      <option key={chamber.chamber_id} value={chamber.chamber_id}>
                        {chamber.chamber_id}
                      </option>
                    ))}
                  </select>
                </FieldGroup>

                <FieldGroup
                  label={t('routes.referenceData.fields.state')}
                  htmlFor="reference-data-state"
                >
                  <select
                    id="reference-data-state"
                    data-testid="reference-data-state-select"
                    value={stateFilter}
                    onChange={(event) => setParam(STATE_PARAM, event.target.value)}
                  >
                    <option value="">{t('routes.referenceData.fields.anyState')}</option>
                    {/*
                      상태 어휘는 서버 응답이 실제로 싣는 값에서 파생한다 — 여기에
                      배열을 적으면 그 어휘가 두 곳이 된다.
                    */}
                    {Array.from(new Set(revisions.map((row) => row.state))).map((state) => (
                      <option key={state} value={state}>
                        {state}
                      </option>
                    ))}
                  </select>
                </FieldGroup>

                <FieldGroup
                  label={t('routes.referenceData.fields.family')}
                  htmlFor="reference-data-family"
                >
                  <select
                    id="reference-data-family"
                    data-testid="reference-data-family-select"
                    value={familyFilter}
                    onChange={(event) => setParam(FAMILY_PARAM, event.target.value)}
                  >
                    <option value="">{t('routes.referenceData.fields.anyFamily')}</option>
                    {Array.from(new Set(revisions.map((row) => row.family))).map((family) => (
                      <option key={family} value={family}>
                        {family}
                      </option>
                    ))}
                  </select>
                </FieldGroup>
              </Toolbar>
            </Card>

            {providerId.length === 0 ? (
              <EmptyState
                title={t('routes.referenceData.empty.noProvider')}
                description={t('routes.referenceData.empty.noProviderHint')}
                testId="reference-data-no-provider"
              />
            ) : null}

            {/*
              처음부터 만들기. 2026-08-11 운영자 판정으로 참조값은 처음부터 웹에서
              운영되므로 이것이 예외가 아니라 정상 경로다.

              어떤 칸을 그릴지 **서버가 알려준다**(`payload_columns`). 여기에 패밀리별
              열 목록을 적으면 같은 순서가 두 언어로 쪼개지고, 그 드리프트는 시험원이
              만든 행이 거부될 때에야 드러난다. `profile_id` 와 `scope_kind` 도 같은
              이유로 서버 값을 그대로 쓴다.

              첫 행이 필요하다 — 빈 리비전은 게시되면 그 패밀리의 런타임 테이블을
              비우고, "아직 안 채웠다"와 "이 방에는 아무것도 없다"가 같은 값이 된다.
            */}
            {providerId.length > 0 && familiesQuery.isSuccess ? (
              <>
                <SectionBand
                  title={t('routes.referenceData.create.title')}
                  titleId="reference-data-create-heading"
                />
                <StatusMessage
                  tone="info"
                  testId="reference-data-create-note"
                  message={t('routes.referenceData.create.description')}
                />
                <Toolbar ariaLabel={t('routes.referenceData.create.title')}>
                  <FieldGroup
                    label={t('routes.referenceData.create.family')}
                    htmlFor="reference-data-create-family"
                  >
                    <select
                      id="reference-data-create-family"
                      data-testid="reference-data-create-family"
                      value={createFamily}
                      onChange={(event) => {
                        setCreateFamily(event.target.value);
                        setCreateRow({});
                      }}
                    >
                      <option value="">{t('routes.referenceData.create.familyPlaceholder')}</option>
                      {families.map((descriptor) => (
                        <option key={descriptor.family} value={descriptor.family}>
                          {descriptor.family}
                        </option>
                      ))}
                    </select>
                  </FieldGroup>
                  <FieldGroup
                    label={t('routes.referenceData.create.scopeId')}
                    htmlFor="reference-data-create-scope"
                  >
                    <input
                      id="reference-data-create-scope"
                      type="text"
                      data-testid="reference-data-create-scope"
                      value={createScopeId}
                      onChange={(event) => setCreateScopeId(event.target.value)}
                    />
                  </FieldGroup>
                </Toolbar>

                {createDescriptor !== undefined ? (
                  <>
                    {createDescriptor.payload_columns.map((column) => (
                      <FieldGroup
                        key={column}
                        label={
                          createDescriptor.identity_columns.includes(column)
                            ? `${column} · ${t('routes.referenceData.identityColumn')}`
                            : column
                        }
                        htmlFor={`reference-data-create-cell-${column}`}
                      >
                        <input
                          id={`reference-data-create-cell-${column}`}
                          type="text"
                          data-testid={`reference-data-create-cell-${column}`}
                          value={createRow[column] ?? ''}
                          onChange={(event) => {
                            const next = event.target.value;
                            setCreateRow((current) => ({
                              ...current,
                              [column]: next,
                            }));
                          }}
                        />
                      </FieldGroup>
                    ))}
                    <Toolbar ariaLabel={t('routes.referenceData.create.title')}>
                      <Button
                        variant="primary"
                        disabled={createScopeId.length === 0 || createMutation.isPending}
                        data-testid="reference-data-create-submit"
                        onClick={() => createMutation.mutate()}
                      >
                        {t('routes.referenceData.create.submit')}
                      </Button>
                    </Toolbar>
                  </>
                ) : null}

                {createError !== null ? (
                  <ErrorState
                    message={describeApiError(createError, 'platform', errorCopy)}
                    testId="reference-data-create-error"
                  />
                ) : null}
              </>
            ) : null}

            {providerId.length > 0 && listQuery.isPending ? (
              <DataTableSkeleton rows={4} columns={columns.length} />
            ) : null}

            {providerId.length > 0 && listQuery.isError ? (
              <ErrorState
                message={describeApiError(listQuery.error, 'platform', errorCopy)}
                testId="reference-data-list-error"
              />
            ) : null}

            {providerId.length > 0 && listQuery.isSuccess ? (
              revisions.length === 0 ? (
                <EmptyState
                  title={t('routes.referenceData.empty.noRevisions')}
                  description={t('routes.referenceData.empty.noRevisionsHint')}
                  testId="reference-data-empty"
                />
              ) : (
                <DataTable
                  caption={t('routes.referenceData.listCaption')}
                  columns={columns}
                  rows={revisions}
                  rowKey={(row) => row.revision_id}
                  testId="reference-data-table"
                />
              )
            ) : null}

            {/*
              상세의 열 수는 서버가 `payload_columns` 로 주므로 대기 중에는 아직
              모른다. 숫자를 지어내면 도착하는 순간 레이아웃이 튀고, 그 튐은 리뷰에서
              보이지 않는다. 그래서 표 스켈레톤이 아니라 블록으로 자리를 잡는다.
            */}
            {revisionId.length > 0 && detailQuery.isPending ? (
              <BlockSkeleton lines={4} testId="reference-data-detail-skeleton" />
            ) : null}

            {revisionId.length > 0 && detailQuery.isError ? (
              <ErrorState
                message={describeApiError(detailQuery.error, 'platform', errorCopy)}
                testId="reference-data-detail-error"
              />
            ) : null}

            {detail !== null ? (
              <Card
                as="section"
                variant="summary"
                aria-labelledby="reference-data-detail-heading"
                testId="reference-data-detail-card"
              >
                <SectionBand
                  title={t('routes.referenceData.detailSection')}
                  titleId="reference-data-detail-heading"
                />

                {/*
                  엔트리 표의 열은 서버가 준 payload_columns 순서 그대로다. 열 이름을
                  여기서 다시 적지 않는 것이 이 화면의 핵심 규칙이다.

                  후보에서만 값 칸이 입력이 된다. 식별 열은 후보에서도 읽기 전용이고
                  그 사실을 눈에 보이게 표시한다 — 서버가 400 으로 거부하기는 하지만,
                  고칠 수 있어 보이는 칸을 고친 뒤 거부당하는 것은 시험원이 무엇을
                  잘못했는지 모른 채 막히는 경험이다.
                */}
                <DataTable
                  caption={t('routes.referenceData.entriesCaption')}
                  columns={detail.payload_columns.map((column) => ({
                    key: column,
                    header: identityColumns.has(column)
                      ? `${column} · ${t('routes.referenceData.identityColumn')}`
                      : column,
                    priority: 'primary' as const,
                    cell: (entry: (typeof detail.entries)[number]) => {
                      const original = renderPayloadCell(
                        (entry.payload as Record<string, unknown>)[column],
                      );
                      if (!isCandidate || identityColumns.has(column)) {
                        return original;
                      }
                      return (
                        <input
                          type="text"
                          aria-label={`${entry.reference_id} · ${column}`}
                          data-testid={`reference-data-cell-${entry.reference_id}-${column}`}
                          value={openDrafts[entry.reference_id]?.[column] ?? original}
                          onChange={(event) => {
                            const next = event.target.value;
                            setDrafts((current) => ({
                              ...current,
                              [revisionId]: {
                                ...current[revisionId],
                                [entry.reference_id]: {
                                  ...current[revisionId]?.[entry.reference_id],
                                  [column]: next,
                                },
                              },
                            }));
                          }}
                        />
                      );
                    },
                  }))}
                  rows={detail.entries}
                  rowKey={(entry) => entry.reference_id}
                  testId="reference-data-entries-table"
                />

                {/*
                  행 추가·삭제. 값 편집과 **다른 요청**으로 나가는 이유는 서버 정책이
                  식별 필드 이동을 거부하는 사유 그 자체다 — 그것은 추가+삭제이지
                  편집이 아니다. 화면에서 둘을 한 버튼으로 접으면, 서버가 두 결함을
                  갈라 놓은 노력이 여기서 다시 합쳐진다.

                  삭제는 **표시만** 하고 저장 시 한 요청으로 나간다. 즉시 보내면 행
                  하나마다 왕복이 생기고, 같은 정체성의 행으로 교체하는 정당한 요청
                  (삭제+추가)이 두 요청으로 갈라져 그 사이에 반쪽 상태가 존재한다.
                */}
                {isCandidate ? (
                  <>
                    <SectionBand
                      title={t('routes.referenceData.rows.title')}
                      titleId="reference-data-rows-heading"
                    />
                    <StatusMessage
                      tone="info"
                      testId="reference-data-rows-note"
                      message={t('routes.referenceData.rows.description')}
                    />
                    <DataTable
                      caption={t('routes.referenceData.rows.removalCaption')}
                      columns={[
                        {
                          key: 'reference_id',
                          header: t('routes.referenceData.rows.rowColumn'),
                          priority: 'primary' as const,
                          cell: (entry: (typeof detail.entries)[number]) => entry.identity_key,
                        },
                        {
                          key: 'action',
                          header: t('routes.referenceData.rows.actionColumn'),
                          priority: 'primary' as const,
                          cell: (entry: (typeof detail.entries)[number]) => {
                            const marked = pendingRemovals.includes(entry.reference_id);
                            return (
                              <Button
                                variant="ghost"
                                data-testid={`reference-data-remove-${entry.reference_id}`}
                                onClick={() =>
                                  setRemovalDrafts((current) => {
                                    const existing = current[revisionId] ?? [];
                                    return {
                                      ...current,
                                      [revisionId]: marked
                                        ? existing.filter((id) => id !== entry.reference_id)
                                        : [...existing, entry.reference_id],
                                    };
                                  })
                                }
                              >
                                {t(
                                  marked
                                    ? 'routes.referenceData.rows.undoRemove'
                                    : 'routes.referenceData.rows.remove',
                                )}
                              </Button>
                            );
                          },
                        },
                      ]}
                      rows={detail.entries}
                      rowKey={(entry) => `remove-${entry.reference_id}`}
                      testId="reference-data-row-removals"
                    />

                    {pendingAdditions.map((draft, index) => (
                      <fieldset key={`addition-${String(index)}`}>
                        <legend>
                          {t('routes.referenceData.rows.newRow', {
                            index: String(index + 1),
                          })}
                        </legend>
                        {detail.payload_columns.map((column) => (
                          <FieldGroup
                            key={column}
                            label={column}
                            htmlFor={`reference-data-new-${String(index)}-${column}`}
                          >
                            <input
                              id={`reference-data-new-${String(index)}-${column}`}
                              type="text"
                              data-testid={`reference-data-new-${String(index)}-${column}`}
                              value={draft[column] ?? ''}
                              onChange={(event) => {
                                const next = event.target.value;
                                setAdditionDrafts((current) => {
                                  const rows = [...(current[revisionId] ?? [])];
                                  rows[index] = { ...rows[index], [column]: next };
                                  return { ...current, [revisionId]: rows };
                                });
                              }}
                            />
                          </FieldGroup>
                        ))}
                      </fieldset>
                    ))}

                    <Toolbar ariaLabel={t('routes.referenceData.rows.title')}>
                      <Button
                        variant="secondary"
                        data-testid="reference-data-add-row"
                        onClick={() =>
                          setAdditionDrafts((current) => ({
                            ...current,
                            [revisionId]: [
                              ...(current[revisionId] ?? []),
                              blankRow(detail.payload_columns),
                            ],
                          }))
                        }
                      >
                        {t('routes.referenceData.rows.add')}
                      </Button>
                      <Button
                        variant="primary"
                        disabled={!hasRowChanges || rowsMutation.isPending}
                        data-testid="reference-data-save-rows"
                        onClick={() => rowsMutation.mutate()}
                      >
                        {t('routes.referenceData.rows.save', {
                          added: String(pendingAdditions.length),
                          removed: String(pendingRemovals.length),
                        })}
                      </Button>
                    </Toolbar>
                  </>
                ) : null}

                <StatusMessage
                  tone="info"
                  testId="reference-data-provenance"
                  message={t(
                    detail.revision.provenance_kind === 'WEB_AUTHORED'
                      ? 'routes.referenceData.provenance.webAuthored'
                      : detail.revision.provenance_kind === 'FORK_EDIT'
                        ? 'routes.referenceData.provenance.forkEdit'
                        : 'routes.referenceData.provenance.workbook',
                  )}
                />

                {publishedBaseline === null ? (
                  <StatusMessage
                    tone="info"
                    testId="reference-data-no-baseline"
                    message={t('routes.referenceData.diff.noBaseline')}
                  />
                ) : comparison !== null ? (
                  <StatusMessage
                    tone="info"
                    testId="reference-data-diff-summary"
                    message={t('routes.referenceData.diff.summary', {
                      added: String(comparison.added),
                      changed: String(comparison.changed),
                      removed: String(comparison.removed),
                      unchanged: String(comparison.unchanged),
                    })}
                  />
                ) : null}

                {isCoupled ? (
                  <>
                    <StatusMessage
                      tone="info"
                      testId="reference-data-coupled-note"
                      message={t('routes.referenceData.coupled.note', {
                        sibling: detail.coupled_with ?? '',
                      })}
                    />
                    <FieldGroup
                      label={t('routes.referenceData.coupled.pick')}
                      htmlFor="reference-data-sibling"
                    >
                      <select
                        id="reference-data-sibling"
                        data-testid="reference-data-sibling-select"
                        value={siblingId}
                        onChange={(event) => setSiblingId(event.target.value)}
                      >
                        <option value="">{t('routes.referenceData.coupled.unselected')}</option>
                        {siblings.map((row) => (
                          <option key={row.revision_id} value={row.revision_id}>
                            {`${row.family} · #${row.revision_number} · ${row.created_by}`}
                          </option>
                        ))}
                      </select>
                    </FieldGroup>
                  </>
                ) : null}

                {publishError !== null ? (
                  <ErrorState
                    message={describeApiError(publishError, 'platform', {
                      ...errorCopy,
                      conflict: t('routes.referenceData.error.publishConflict'),
                    })}
                    testId="reference-data-publish-error"
                  />
                ) : null}

                {writeError !== null ? (
                  <ErrorState
                    message={describeApiError(writeError, 'platform', {
                      ...errorCopy,
                      conflict: t('routes.referenceData.error.staleEdit'),
                    })}
                    testId="reference-data-write-error"
                  />
                ) : null}

                <Toolbar ariaLabel={t('routes.referenceData.detailSection')}>
                  {/*
                    fork 는 게시본에서만 의미가 있다 — 후보는 이미 고칠 수 있으므로
                    두 번째 사본을 만들 이유가 없고, 서버도 409 로 그렇게 답한다.
                    권한으로 잠그지 않는 규칙(4)은 그대로다: 상태로만 가린다.
                  */}
                  {isPublished ? (
                    <Button
                      variant="primary"
                      disabled={forkMutation.isPending}
                      onClick={() => forkMutation.mutate()}
                      data-testid="reference-data-fork"
                    >
                      {t('routes.referenceData.actions.fork')}
                    </Button>
                  ) : null}
                  {isCandidate ? (
                    <Button
                      variant="secondary"
                      disabled={!hasUnsavedEdits || hasUnreadableCells || saveMutation.isPending}
                      onClick={() => saveMutation.mutate()}
                      data-testid="reference-data-save"
                    >
                      {t('routes.referenceData.actions.save', {
                        count: String(pendingEdits.length),
                      })}
                    </Button>
                  ) : null}
                  <Button
                    variant="primary"
                    disabled={!canSubmit || hasUnsavedEdits || publishMutation.isPending}
                    onClick={() => publishMutation.mutate()}
                    data-testid="reference-data-publish"
                  >
                    {t('routes.referenceData.actions.publish')}
                  </Button>
                </Toolbar>

                {/*
                  저장하지 않은 편집이 남아 있으면 게시를 막는다. 서버는 저장된
                  내용만 게시하므로, 막지 않으면 시험원이 방금 친 숫자가 아니라
                  그 이전 숫자가 장비 앞에 놓이고 화면은 성공이라고 말한다.
                */}
                {isCandidate && hasUnreadableCells ? (
                  <StatusMessage
                    tone="info"
                    testId="reference-data-unreadable"
                    message={t('routes.referenceData.unreadableNote', {
                      cells: unreadableCells.join(', '),
                    })}
                  />
                ) : null}

                {isCandidate && hasUnsavedEdits ? (
                  // tone 은 `info` 다. 강제는 색이 아니라 비활성화된 게시 버튼이
                  // 하고 있고, 이 한 화면 때문에 공유 프리미티브의 tone 집합을
                  // 넓히면 디자인 토큰 축이 라우트 요구로 흔들린다.
                  <StatusMessage
                    tone="info"
                    testId="reference-data-unsaved"
                    message={t('routes.referenceData.unsavedNote', {
                      count: String(pendingEdits.length),
                    })}
                  />
                ) : null}

                {!isCandidate ? (
                  <StatusMessage
                    tone="info"
                    testId="reference-data-not-candidate"
                    message={t('routes.referenceData.publishedNote')}
                  />
                ) : null}
              </Card>
            ) : null}
          </>
        }
      />
    </section>
  );
}
