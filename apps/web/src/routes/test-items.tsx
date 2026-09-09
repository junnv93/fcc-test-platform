import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import {
  fetchClaimsPage,
  fetchCoveragePage,
  fetchPlanConditionsPage,
  fetchProjects,
  type ActiveClaimEnvelope,
  type PlanConditionEnvelope,
} from '@/api/platform-client';
import { useT } from '@/i18n';
import {
  BlockSkeleton,
  Card,
  describeApiError,
  EmptyState,
  ErrorState,
  ItemTable,
} from '@/ui';

import type { ItemBundle, ItemFilter, ItemRow } from '@/ui';

/**
 * `/test-items?model=<projectId>&family=<label>` — one family's test items.
 *
 * ┌─ 목적 ────────────────────────────────────────────────────────────────┐
 * │ 누가   시험할 사람                                                     │
 * │ 언제   「이 계열에서 뭐가 남았나」를 물을 때                            │
 * │ 무엇에 답하나 — 조건 하나하나가 됐는지 · 누가 했는지 · 누가 잡고 있는지 │
 * │ 답하지 않는 것 — 「그중 무엇을 먼저」(순서는 사람이 정한다) ·          │
 * │                  「미착수 조건의 이름」(§ItemTable 의 선언된 구멍)      │
 * └──────────────────────────────────────────────────────────────────────┘
 *
 * ⚠️ 홈에서 펼치지 않고 «여기로 온다». 한 계열이 50행을 넘으면 홈의 3열 중
 * 한 열만 길어져 대분류를 나란히 비교하려던 배치가 무너지고, 페이지가 스크롤
 * 몇 번 길이가 된다. 목록을 보는 것은 그 자체로 하나의 일이라 화면을 준다.
 *
 * ⚠️ 질의 키를 홈과 «똑같이» 쓴다(`console-progress` / `console-coverage` /
 * `console-claims`). 그래서 홈에서 넘어오면 네트워크가 한 번도 돌지 않는다 —
 * 캐시에 이미 있는 것을 다시 읽을 뿐이다. 키를 새로 지으면 같은 데이터를 두 번
 * 받고, 그 둘이 서로 다른 시각의 값이 되어 화면 사이에서 숫자가 어긋난다.
 *
 * ⚠️ 읽기 전용. 잡기·놓기는 작업 화면의 일이다.
 */
/** 부적합으로 읽는 판정. `ItemTable` 이 필터에 쓰는 집합과 같은 어휘다 —
 *  두 곳이 다른 목록을 들면 「부적합 3건」과 목록의 줄 수가 어긋난다. */
const FAILED_VERDICTS = new Set(['fail', 'failed', 'nonconforming', 'ng']);

export function TestItemsRoute(): JSX.Element {
  const { t } = useT();
  const [params] = useSearchParams();
  const modelId = params.get('model') ?? '';
  const family = params.get('family') ?? '';

  const projects = useQuery({
    queryKey: ['console-projects-active'],
    queryFn: () => fetchProjects('active'),
  });

  /** 계획 조건 전량. 커서로 이어 받는다 — 845N 이 393행이고 한 페이지는 200이다.
   *  ⚠️ 이 화면은 한 «계열»만 보는데도 프로젝트 전체를 받는다. 기술 패싯은
   *  모드(`technology`) 단위라 계열로는 못 거르고, 계열당 요청을 쪼개면 모드 수
   *  만큼 왕복이 늘어난다. 한 번 받아 캐시에 두고 화면에서 거르는 쪽이 싸다. */
  const planConditions = useQuery({
    queryKey: ['console-plan-conditions', modelId],
    queryFn: async () => {
      const items: PlanConditionEnvelope[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 25; page += 1) {
        const chunk = await fetchPlanConditionsPage(modelId, cursor);
        items.push(...chunk.items);
        if (chunk.nextCursor == null || chunk.nextCursor === '') break;
        cursor = chunk.nextCursor;
      }
      return items;
    },
    enabled: modelId !== '',
    placeholderData: keepPreviousData,
  });

  const coverage = useQuery({
    queryKey: ['console-coverage', modelId],
    queryFn: async () => {
      const items = [];
      let cursor: string | undefined;
      for (let page = 0; page < 25; page += 1) {
        const chunk = await fetchCoveragePage(modelId, cursor);
        items.push(...chunk.items);
        if (chunk.nextCursor == null || chunk.nextCursor === '') break;
        cursor = chunk.nextCursor;
      }
      return { items, nextCursor: undefined };
    },
    enabled: modelId !== '',
    placeholderData: keepPreviousData,
  });

  const claims = useQuery({
    queryKey: ['console-claims', modelId],
    queryFn: async () => {
      const items: ActiveClaimEnvelope[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 25; page += 1) {
        const chunk = await fetchClaimsPage(modelId, cursor);
        items.push(...chunk.items);
        if (chunk.nextCursor == null || chunk.nextCursor === '') break;
        cursor = chunk.nextCursor;
      }
      return items;
    },
    enabled: modelId !== '',
    placeholderData: keepPreviousData,
  });

  /** 계획이 대분류와 계열을 한 칸에 담는다(`"Unlicensed band / U-NII"`).
   *  홈과 같은 구분자를 쓴다 — 두 화면이 다른 규칙으로 쪼개면 같은 계열이
   *  서로 다른 이름이 된다. */
  const SEP = ' / ';

  const bundle = useMemo((): { items: ItemBundle; modes: number } | null => {
    const plan = (planConditions.data ?? []).filter((row) => {
      const bucketId = row.progress_bucket_id ?? '';
      const cut = bucketId.indexOf(SEP);
      const label = cut === -1 ? bucketId : bucketId.slice(cut + SEP.length);
      return label === family;
    });
    if (plan.length === 0) return null;

    /** 계획이 이 화면의 «분모»다. 여기 없는 조건은 이 계열의 일이 아니고,
     *  여기 있는데 커버리지·claim 어디에도 없는 것이 곧 「할 것」이다. */
    const heldBy = new Map<string, { operator: string; when: string }>();
    for (const claim of claims.data ?? []) {
      heldBy.set(claim.condition_hash ?? '', {
        operator: claim.operator ?? '\u2014',
        when: claim.occurred_at ?? '',
      });
    }
    const doneBy = new Map<
      string,
      { operator: string; when: string; verdict: string | null; attempt: number }
    >();
    for (const measured of coverage.data?.items ?? []) {
      doneBy.set(measured.condition_hash ?? '', {
        operator: measured.latest_operator ?? '\u2014',
        when: measured.latest_measured_at ?? '',
        verdict: measured.latest_verdict ?? null,
        attempt: measured.latest_attempt_number ?? 1,
      });
    }

    const rows: ItemRow[] = plan.map((row) => {
      const hash = row.condition_hash ?? '';
      const held = heldBy.get(hash);
      const done = doneBy.get(hash);
      const base = {
        id: hash,
        mode: row.progress_area ?? '\u2014',
        item: row.test_item ?? '\u2014',
        minutes: row.planned_minutes ?? null,
      };
      // ⚠️ 잡힌 것이 «완료»를 이긴다. 재측정 중이면 두 곳에 다 있는데, 그때
      // 알고 싶은 것은 「지금 누가 손대고 있나」이지 지난 판정이 아니다.
      if (held !== undefined) {
        return {
          ...base, state: 'held' as const, operator: held.operator,
          when: held.when, verdict: null, attempt: 1,
        };
      }
      if (done !== undefined) {
        return {
          ...base, state: 'done' as const, operator: done.operator,
          when: done.when, verdict: done.verdict, attempt: done.attempt,
        };
      }
      return {
        ...base, state: 'todo' as const, operator: '', when: '',
        verdict: null, attempt: 1,
      };
    });

    /* 모드 → 항목 순. 표가 모드로 묶이므로 이 순서가 곧 그룹 순서이고,
       그룹 안의 줄 순서다. 정렬을 여기 한 번만 두는 이유는 표에서 또 하면
       「무엇이 순서를 정하는가」가 두 군데가 되기 때문이다. */
    rows.sort(
      (a, b) => a.mode.localeCompare(b.mode) || a.item.localeCompare(b.item),
    );

    /** 같은 (모드, 시험항목)에 계획 조건이 둘 이상이면 그 행들은 이 읽기가
     *  주는 값만으로는 구별되지 않는다. 그때만 아래에 그 사실을 적는다 —
     *  항상 적으면 경고가 배경이 되고, 진짜일 때도 안 읽힌다. */
    const seen = new Set<string>();
    let hasAmbiguousRows = false;
    for (const row of rows) {
      const key = `${row.mode}\u0000${row.item}`;
      if (seen.has(key)) {
        hasAmbiguousRows = true;
        break;
      }
      seen.add(key);
    }

    const modes = new Set(rows.map((row) => row.mode));
    return { items: { rows, hasAmbiguousRows }, modes: modes.size };
  }, [planConditions.data, coverage.data, claims.data, family]);

  /** 필터는 «질문»이다: 전체 / 뭐가 남았나 / 뭘 다시 해야 하나.
   *  세 개뿐인 이유는 실무자가 이 화면에 들고 오는 질문이 그 셋이기 때문이고,
   *  더 늘리면 고르는 일이 찾는 일보다 비싸진다. */
  const [filter, setFilter] = useState<ItemFilter>('all');

  const counts = useMemo(() => {
    const rows = bundle?.items.rows ?? [];
    return {
      all: rows.length,
      todo: rows.filter((row) => row.state === 'todo').length,
      failed: rows.filter(
        (row) => row.verdict !== null && FAILED_VERDICTS.has(row.verdict),
      ).length,
    };
  }, [bundle]);

  const modelName = useMemo(() => {
    const found = (projects.data ?? []).find((project) => project.project_id === modelId);
    return found?.model_name ?? found?.project_code ?? modelId;
  }, [projects.data, modelId]);

  const pending = planConditions.isPending || coverage.isPending || claims.isPending;
  const failed = planConditions.isError
    ? planConditions.error
    : coverage.isError
      ? coverage.error
      : null;

  return (
    <section className="console" aria-labelledby="test-items-heading">
      <header className="console__head">
        {/* 돌아갈 길을 화면이 갖고 있어야 한다. 브라우저 뒤로가기는 있지만,
            「어디서 왔는지」를 이름으로 말해 주는 것과 다르다. */}
        <Link className="console__back" to="/">
          {t('routes.testItems.back')}
        </Link>
        <p className="eyebrow">{t('routes.testItems.eyebrow', { model: modelName })}</p>
        <h1 className="console__title" id="test-items-heading">
          {family === '' ? t('routes.testItems.noFamily') : family}
        </h1>
      </header>

      <Card as="section" className="console-panel">
        {pending && <BlockSkeleton lines={6} testId="test-items-loading" />}
        {failed !== null && (
          <ErrorState
            testId="test-items-error"
            message={describeApiError(failed, 'platform')}
          />
        )}
        {!pending && failed === null && bundle === null && (
          <EmptyState
            testId="test-items-empty"
            title={t('routes.testItems.emptyTitle')}
            description={t('routes.testItems.emptyBody')}
          />
        )}
        {!pending && failed === null && bundle !== null && (
          <>
            {/* 모델 토글과 «같은 모양»의 segmented control. 이 화면에서 처음
                보는 컨트롤을 만들지 않는다 — 같은 동작은 같은 모습이어야 한다.
                건수를 라벨에 넣는 이유: 누르기 «전»에 그 필터에 뭐가 있는지
                알면, 비어 있는 필터를 눌러 보는 왕복이 사라진다. */}
            <div className="item-filter" role="group" aria-label={t('routes.testItems.filterLabel')}>
              {(['all', 'todo', 'failed'] as const).map((key) => (
                <button
                  type="button"
                  className="item-filter__btn"
                  key={key}
                  aria-pressed={filter === key}
                  onClick={() => setFilter(key)}
                >
                  {t(`routes.testItems.filter.${key}`)}
                  <span className="item-filter__count">{counts[key]}</span>
                </button>
              ))}
            </div>
            <ItemTable
              items={bundle.items}
              filter={filter}
              t={t}
              testId="test-items-table"
            />
          </>
        )}
      </Card>
    </section>
  );
}

export default TestItemsRoute;
