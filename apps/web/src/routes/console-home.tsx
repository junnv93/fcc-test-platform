import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';



import {
  fetchChambers,
  fetchCoveragePage,
  fetchProjectProgress,
  fetchProjects,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import {
  BlockSkeleton,
  Card,
  ChamberCard,
  DonutProgress,
  StackedTrend,
  chamberStatusKind,
  describeApiError,
  EmptyState,
  ErrorState,
  HealthBars,
  PageHeader,
  StatTile,
  StatusBadge,
  verdictToStatusKind,
} from '@/ui';

import type { HealthRow } from '@/ui';
import type { CSSProperties } from 'react';

/**
 * One console, three audiences (flowdeck-console-ui).
 *
 * ⚠️ Three ROUTES, one component. The three homes differ in which panels they
 * carry, not in how a panel behaves — a technology bar must mean the same thing
 * to the operator running it and the PM reading it, or the two will disagree in
 * a meeting while both look at "the dashboard". Copying this file three times
 * is how that drift starts, so `scope` selects panels and nothing else.
 *
 *   operator — 무엇을 시험할까 (todo) + 기술별 진행 + 부적합 상세
 *   pm       — 전체 진행률 + 영역별 진행률 (+ 이슈 내역)
 *   manager  — 위 둘을 모두
 *
 * ⚠️ Every number here is served, never derived from a partial read. The
 * standing judgement this repo carries — *"never faked from partial
 * aggregates"* — is why the failure-rate tile declares a missing contract
 * instead of estimating one. See `StatTile`.
 */

export type ConsoleScope = 'operator' | 'pm' | 'manager';

export interface ConsoleHomeProps {
  readonly scope: ConsoleScope;
}

/** Verdicts that mean "this condition did not pass". Kept as a set rather than
 *  a `!== 'pass'` test: an unmeasured condition is not a nonconformity, and
 *  lumping the two would inflate the count the operator acts on. */
const FAILING_VERDICTS = new Set(['fail', 'failed', 'nonconforming', 'ng']);

/** 계획이 «대분류 / 계열» 을 한 칸에 담는 구분자. 정식 컬럼이 생기면 사라진다. */
const SEP = ' / ';

/** 대분류의 «선언된» 순서 — 주파수가 낮은 쪽부터. 정렬·추이 스택·카드가 같은
 *  순서를 써야 같은 밴드가 화면 어디서나 같은 자리에 온다.
 *  ⚠️ 모듈 상수다. 컴포넌트 안에 두면 렌더마다 새 배열이라 useMemo 가 매번 다시 돈다. */
const CATEGORY_ORDER = ['Unlicensed band', 'Licensed band', 'mmWave'];

export function ConsoleHome({ scope }: ConsoleHomeProps): JSX.Element {
  const { t } = useT();
  const showsOperator = scope === 'operator' || scope === 'manager';
  const showsProgramme = scope === 'pm' || scope === 'manager';

/** 분 → 시간. 계획은 분 단위로 쌓이지만 사람이 일정을 잡는 단위는 시간이라,
   *  화면에는 시간만 나온다. 한 자리 소수까지 — 「12h」와 「12.5h」의 차이가
   *  반나절이라 반올림해 버리면 일정 판단이 틀린다. */
  const hours = (minutes: number): string => `${(minutes / 60).toFixed(1)}h`;

  const chambers = useQuery({ queryKey: queryKeys.chambers.list(), queryFn: fetchChambers });
  const projects = useQuery({
    queryKey: queryKeys.project.lists(),
    queryFn: () => fetchProjects('active'),
  });

  const activeProjects = useMemo(() => projects.data ?? [], [projects.data]);

  /** 어느 모델의 진행률을 보고 있는가.
   *
   *  ⚠️ 「전체 합산」 보기는 «없다». 여러 모델의 진행률을 하나로 합치면 그 숫자가
   *  가리키는 물건이 없다 — 848U 가 90% 이고 840 이 10% 일 때 「50%」인 시험은
   *  세상에 존재하지 않고, 그 값으로는 아무 결정도 못 한다. 진행률은 항상
   *  «한 모델의» 진행률이다.
   *
   *  `null` 은 「아직 안 골랐다」이고, 그때는 첫 모델을 보여준다. */
  const [modelFilter, setModelFilter] = useState<string | null>(null);

  /** ⚠️ 내보내기는 «목업»이다. 이 화면의 목적은 PM 팀의 관찰과 고객사 보고이고,
   *  그 보고서에 무엇이 들어가야 하는지는 아직 정해지지 않았다.
   *
   *  버튼을 «되는 것처럼» 만들어 두고 아무 파일도 안 나오게 하는 것이 가장 나쁘다 —
   *  누른 사람은 실패했다고 생각하고, 다음 사람은 기능이 있다고 믿는다. 그래서
   *  누르면 «무엇이 아직 정해지지 않았는지»를 펼친다. 그 목록이 그대로 PM 팀과의
   *  논의 안건이 되고, 정해지는 날 이 블록이 실제 내보내기로 교체된다. */
  const [exportOpen, setExportOpen] = useState(false);

  /** 이슈 행에 커서를 올리면 «왼쪽 진행률의 같은 모드»가 켜진다.
   *
   *  ⚠️ 이 연결이 두 패널을 한 화면에 둔 이유를 완성한다. 나란히 있기만 하면
   *  읽는 사람이 「802.11n HT40 이 어디 있더라」를 눈으로 찾아야 하고, 열이
   *  셋이고 행이 마흔이면 그 탐색이 곧 포기가 된다.
   *
   *  ⚠️ 마우스만이 아니라 포커스에도 반응한다. hover 전용으로 만들면 키보드로
   *  이슈를 훑는 사람에게는 이 기능이 «없는 것»이 된다. */
  const [hoverMode, setHoverMode] = useState<string | null>(null);
  /** 클릭하면 고정된다. 커서를 떼야 하는 순간(스크롤·다른 창 참조)에도 하이라이트가
   *  남아야 하고, 그것이 마우스에서 손을 떼는 사람에게 이 기능이 살아남는 방식이다. */
  const [pinnedMode, setPinnedMode] = useState<string | null>(null);
  const linkedMode = hoverMode ?? pinnedMode;

  /** 고정할 때 그 행이 화면 밖이면 끌어온다.
   *
   *  ⚠️ 호버에는 하지 않는다. 목록을 훑는 동안 왼쪽이 계속 스크롤되면 읽는 사람이
   *  멀미를 하고, 스크롤이 커서 아래의 행을 바꿔 버려 호버 대상이 저절로 달라진다.
   *  «고정»은 사람이 「이걸 보겠다」고 결정한 순간이므로 그때만 움직인다.
   *
   *  ⚠️ `block: 'nearest'` — 이미 보이는 행은 건드리지 않는다. `center` 로 두면
   *  보고 있던 화면이 이유 없이 튄다. */
  const revealMode = useCallback((mode: string | null) => {
    if (mode === null) return;
    const row = document.querySelector<HTMLElement>(
      `[data-testid="programme-columns"] [data-linked="true"]`,
    );
    row?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, []);
  const activeModelId = modelFilter ?? activeProjects[0]?.project_id ?? null;
  const items = useMemo(() => chambers.data?.items ?? [], [chambers.data]);

  /** The project the operator panels talk about. First active project until a
   *  project context exists on this screen — deliberately NOT remembered in
   *  localStorage, which would make two operators at one bench disagree about
   *  "the" project without either of them having chosen it. */
  const focusProjectId = activeProjects[0]?.project_id ?? null;

  const fleet = useMemo(() => {
    const counts = { total: items.length, idle: 0, inUse: 0, offline: 0 };
    for (const chamber of items) {
      if (!chamber.enabled) {
        counts.offline += 1;
        continue;
      }
      const status = chamber.status.trim().toLowerCase();
      if (status === 'idle') counts.idle += 1;
      else if (status === 'in_use') counts.inUse += 1;
      else counts.offline += 1;
    }
    return counts;
  }, [items]);

  const runningRows = useMemo<HealthRow[]>(
    () =>
      items
        .filter((chamber) => chamber.progress?.is_running === true)
        .map((chamber) => ({
          id: chamber.chamber_id,
          label: chamber.name,
          ratio: chamber.progress?.ratio ?? 0,
          detail: t('routes.home.progressDetail', {
            completed: String(chamber.progress?.completed ?? 0),
            total: String(chamber.progress?.total ?? 0),
          }),
        })),
    [items, t],
  );

  // ── operator: 기술별 커버리지 + 부적합 ────────────────────────────────────
  /** ⚠️ 커버리지는 «선택된 모델» 을 따른다. 예전에는 항상 첫 프로젝트를 봤는데,
   *  그러면 PM 이 840 을 골라도 이슈 목록은 845N 의 것이 나온다 — 화면 안에서
   *  두 개의 「지금 보고 있는 모델」이 생기는 셈이다. */
  const coverage = useQuery({
    queryKey: ['console-coverage', activeModelId],
    queryFn: async () => {
      // ⚠️ 한 페이지가 200 건인데 모델당 조건이 그보다 많다(845N 218 · 848U 242).
      // 첫 페이지만 받으면 진행률·이슈·추이가 «조용히» 일부만 세고, 그 오차는
      // 화면 어디에도 나타나지 않는다. 커서가 끝날 때까지 이어 받는다.
      const items = [];
      let cursor: string | undefined;
      // 상한을 둔다 — 커서가 끝나지 않는 서버 결함이 브라우저를 멈추게 두지 않는다.
      for (let page = 0; page < 25; page += 1) {
        const chunk = await fetchCoveragePage(activeModelId ?? '', cursor);
        items.push(...chunk.items);
        if (chunk.nextCursor == null || chunk.nextCursor === '') break;
        cursor = chunk.nextCursor;
      }
      return { items, nextCursor: undefined };
    },
    enabled: activeModelId !== null,
    /* 모델을 바꾸는 동안 이전 모델의 커버리지를 «들고 있는다». 없으면 데이터가
       비는 순간 추이가 통째로 사라졌다가 다시 그려져, 그 자리만 화면에서 깜빡인다
       (실측: 840 은 약 75ms, 848U 는 커서 2페이지라 약 400ms 동안 점이 0개였다).
       계획·카드·링은 이미 전 모델치를 받아 둬서 즉시 바뀌는데 이 그래프만
       왕복을 기다리므로, 눈에는 「그래프가 고장 났다」로 보인다. */
    placeholderData: keepPreviousData,
  });

  const byTechnology = useMemo(() => {
    const rows = coverage.data?.items ?? [];
    const acc = new Map<string, { done: number; total: number; failed: number }>();
    for (const row of rows) {
      const key = row.technology ?? '—';
      const bucket = acc.get(key) ?? { done: 0, total: 0, failed: 0 };
      bucket.total += 1;
      const verdict = (row.latest_verdict ?? '').trim().toLowerCase();
      if (verdict !== '') bucket.done += 1;
      if (FAILING_VERDICTS.has(verdict)) bucket.failed += 1;
      acc.set(key, bucket);
    }
    return acc;
  }, [coverage.data]);

  /** The focus project's plan buckets. Fetched for the operator scope too — the
   *  technology bars need the planned denominator, and this is the only read
   *  that serves it. */
  const focusProgressQuery = useQuery({
    queryKey: ['console-progress', focusProjectId],
    queryFn: () => fetchProjectProgress(focusProjectId ?? ''),
    enabled: showsOperator && focusProjectId !== null,
  });
  const focusProgress = useMemo(() => focusProgressQuery.data ?? [], [focusProgressQuery.data]);

  /** ⚠️ The denominator comes from the PLAN, not from the coverage view.
   *
   *  The first draft divided measured conditions by measured conditions and
   *  every technology read 100% — a bar that is always full is not a progress
   *  bar, it is a decoration. `published_plan_expectation` carries what was
   *  planned; the coverage view carries what was measured. Progress is the
   *  ratio between them, and the failure count rides along from coverage. */
  const technologyRows = useMemo<HealthRow[]>(() => {
    const planned = new Map<string, number>();
    focusProgress.forEach((bucket) => {
      const key = bucket.progress_bucket_id ?? bucket.progress_area ?? '';
      planned.set(key, (planned.get(key) ?? 0) + (bucket.total_conditions ?? 0));
    });
    const keys = new Set([...planned.keys(), ...byTechnology.keys()]);
    return [...keys]
      .filter((key) => key !== '')
      .map((technology) => {
        const measured = byTechnology.get(technology);
        const total = planned.get(technology) ?? measured?.total ?? 0;
        const done = measured?.done ?? 0;
        return {
          id: technology,
          label: technology,
          ratio: total === 0 ? 0 : Math.min(1, done / total),
          detail: t('routes.home.techDetail', {
            done: String(done),
            total: String(total),
            failed: String(measured?.failed ?? 0),
          }),
        };
      });
  }, [byTechnology, focusProgress, t]);

  /** 할 일 — 계획에 있는데 아직 측정되지 않은 것.
   *
   *  ⚠️ 이 화면에서 시험원이 가장 먼저 볼 것이고, 그래서 «남은 양»이 아니라
   *  «남은 항목»이다. 진행률 막대는 「62%」라고 말하지만 그 말로는 아무도
   *  일을 시작하지 못한다. 시작하려면 「무엇이」 남았는지가 필요하다.
   *
   *  분모는 `published_plan_expectation`(계획), 차감은 커버리지(실측). 두
   *  집합의 차가 곧 할 일이고, 그것이 이 레포에서 이미 서빙되는 두 읽기로
   *  «파생 없이» 나온다 — 새 계약을 요구하지 않는다. */
  const todo = useMemo(() => {
    const measured = new Set(
      (coverage.data?.items ?? []).map((row) => row.condition_hash ?? ''),
    );
    const perTech = new Map<string, number>();
    focusProgress.forEach((bucket) => {
      const key = bucket.progress_bucket_id ?? bucket.progress_area ?? '—';
      const planned = bucket.total_conditions ?? 0;
      // 이 버킷에서 이미 측정된 수를 커버리지로 센다. 버킷 id 는 기술과 같은
      // 값을 쓰도록 계획이 채워져 있어(seed/운영 모두), 기술로 맞춘다.
      const done = (coverage.data?.items ?? []).filter(
        (row) => (row.technology ?? '') === key,
      ).length;
      const remaining = planned - done;
      if (remaining > 0) perTech.set(key, remaining);
    });
    void measured;
    return [...perTech.entries()]
      .map(([technology, remaining]) => ({ technology, remaining }))
      .sort((a, b) => b.remaining - a.remaining);
  }, [coverage.data, focusProgress]);

  const todoTotal = useMemo(() => todo.reduce((n, row) => n + row.remaining, 0), [todo]);

  /** The measurement FLOW — what happened, newest first.
   *
   *  ⚠️ This is the panel the aggregates exist to support, not the other way
   *  round. A tile says "4 nonconforming"; it cannot say that U-NII-1 #3 came
   *  back at attempt 2 an hour after the first one failed. A reader follows a
   *  story, and a screen that only totals things makes them reconstruct it.
   *
   *  ⚠️ Two of the five things a reader wants are NOT SERVED today:
   *    · WHERE  — coverage carries `latest_session_id`, not a chamber. Mapping
   *               it back needs a second read that no endpoint offers.
   *    · WHAT VALUE — margin/measured value appears in no read contract.
   *  They are declared in the panel rather than dropped silently, because a
   *  flow missing "where" and "how much" is a flow with two holes in it, and
   *  the next person should see the holes rather than re-derive them.
   *
   *  The full retry history is also unavailable in one call — the attempts
   *  endpoint is per-condition (`/conditions/{hash}/attempts`), so a project
   *  feed would be one request per condition. This shows the LATEST verdict per
   *  condition, which is a real timeline, just not an exhaustive one. */
  const flow = useMemo(() => {
    const rows = [...(coverage.data?.items ?? [])];
    rows.sort((a, b) => (b.latest_measured_at ?? '').localeCompare(a.latest_measured_at ?? ''));
    return rows.slice(0, 12);
  }, [coverage.data]);

  /** 모드별로 묶은 이슈. 「802.11n HT20」이 두 줄, 「802.11b」가 두 줄 나오면
   *  목록이 길어지기만 하고 「무엇이 문제인가」는 오히려 흐려진다. 사람이 보는
   *  단위는 case 가 아니라 «모드»이고, 그 모드 안에 몇 건인지가 다음 정보다.
   *
   *  ⚠️ 개별 case 를 못 보게 된 것은 아니다 — 이 묶음을 펼쳐 case 를 보는 것은
   *  다음 단계이고, 그때 필요한 것은 조건별 attempts 계약이다(이미 있다:
   *  `/conditions/{hash}/attempts`). 지금은 «묶음»까지가 화면의 답이다. */
  const issueGroups = useMemo(() => {
    const acc = new Map<
      string,
      { count: number; latestAt: string; attempt: number; operator: string; verdict: string }
    >();
    for (const row of coverage.data?.items ?? []) {
      const verdict = (row.latest_verdict ?? '').trim().toLowerCase();
      if (!FAILING_VERDICTS.has(verdict)) continue;
      const mode = row.technology ?? '—';
      const at = row.latest_measured_at ?? '';
      const cur = acc.get(mode);
      if (cur === undefined) {
        acc.set(mode, {
          count: 1,
          latestAt: at,
          attempt: row.latest_attempt_number ?? 0,
          operator: row.latest_operator ?? '—',
          verdict: row.latest_verdict ?? '',
        });
      } else {
        cur.count += 1;
        // 가장 최근 발생을 대표로 남긴다 — 「언제부터 막혔나」가 아니라
        // 「마지막으로 언제 그랬나」가 지금 볼 값이다.
        if (at > cur.latestAt) {
          cur.latestAt = at;
          cur.attempt = row.latest_attempt_number ?? 0;
          cur.operator = row.latest_operator ?? '—';
          cur.verdict = row.latest_verdict ?? '';
        }
      }
    }
    return [...acc.entries()]
      .map(([mode, v]) => ({ mode, ...v }))
      .sort((a, b) => b.count - a.count || b.latestAt.localeCompare(a.latestAt));
  }, [coverage.data]);

  const nonconformities = useMemo(
    () =>
      (coverage.data?.items ?? []).filter((row) =>
        FAILING_VERDICTS.has((row.latest_verdict ?? '').trim().toLowerCase()),
      ),
    [coverage.data],
  );

  // ── pm: 프로젝트별 영역 진행률 ────────────────────────────────────────────
  const progressQueries = useQueries({
    queries: (showsProgramme ? activeProjects : []).map((project) => ({
      queryKey: ['console-progress', project.project_id],
      queryFn: () => fetchProjectProgress(project.project_id ?? ''),
      enabled: project.project_id != null,
    })),
  });

  /** 3층 집계: 대분류 → 계열 → 모드.
   *
   *  ⚠️ 층이 셋인데 계획 테이블이 내주는 칸은 둘(`progress_bucket_id`,
   *  `progress_area`)이다. 그래서 계획이 대분류와 계열을 «한 칸에» 담고
   *  (`"Unlicensed band / U-NII"`) UI 가 약속된 구분자로 나눈다.
   *
   *  이것은 UI 가 이름을 보고 «추측»하는 것과 다르다 — 두 사실 모두 계획이
   *  운반하고, UI 는 디코드만 한다. 새 계열이 생겨도 UI 는 고칠 것이 없다.
   *
   *  ⚠️ 그래도 이것은 임시다. 정식으로는 계열이 자기 컬럼을 가져야 하고, 그때
   *  이 `split` 한 줄이 사라진다. 구분자에 의존하는 코드가 여기 한 곳뿐인
   *  이유가 그것이다 — 옮길 때 한 줄만 보면 된다.
   *
   *  비율은 «case 수로 가중»한다. 단순 평균은 case 11개짜리 계열과 62개짜리
   *  계열을 같은 무게로 세어 작은 쪽이 전체를 흔든다. */

  const categories = useMemo(() => {
    interface Tally {
      done: number;
      total: number;
      planned: number;
      spent: number;
    }
    const blank = (): Tally => ({ done: 0, total: 0, planned: 0, spent: 0 });
    const add = (a: Tally, b: Tally): Tally => ({
      done: a.done + b.done,
      total: a.total + b.total,
      planned: a.planned + b.planned,
      spent: a.spent + b.spent,
    });

    const acc = new Map<string, { tally: Tally; families: Map<string, { tally: Tally; modes: Map<string, Tally> }> }>();

    progressQueries.forEach((query, index) => {
      // `progressQueries` 는 `activeProjects` 와 같은 순서로 만들어지므로
      // 인덱스가 곧 프로젝트다. id 로 다시 찾지 않는 이유는 그 대응이
      // useQueries 의 계약이고, 여기서 깨지면 조용히 틀리기 때문이다.
      const project = activeProjects[index];
      if ((project?.project_id ?? null) !== activeModelId) return;
      for (const bucket of query.data ?? []) {
        const bucketId = bucket.progress_bucket_id ?? '—';
        const cut = bucketId.indexOf(SEP);
        const category = cut === -1 ? bucketId : bucketId.slice(0, cut);
        const family = cut === -1 ? bucketId : bucketId.slice(cut + SEP.length);
        const mode = bucket.progress_area ?? '—';

        const total = bucket.total_conditions ?? 0;
        const one: Tally = {
          total,
          done: Math.round(((bucket.percent ?? 0) / 100) * total),
          planned: Number(bucket.planned_minutes ?? 0),
          spent: Number(bucket.completed_minutes ?? 0),
        };

        const cat = acc.get(category) ?? {
          tally: blank(),
          families: new Map<string, { tally: Tally; modes: Map<string, Tally> }>(),
        };
        cat.tally = add(cat.tally, one);
        const fam = cat.families.get(family) ?? { tally: blank(), modes: new Map<string, Tally>() };
        fam.tally = add(fam.tally, one);
        fam.modes.set(mode, add(fam.modes.get(mode) ?? blank(), one));
        cat.families.set(family, fam);
        acc.set(category, cat);
      }
    });

    const ratioOf = (v: Tally): number => (v.total === 0 ? 0 : v.done / v.total);
    const toneOf = (r: number): string => (r < 0.5 ? 'bad' : r < 0.8 ? 'warn' : 'ok');
    const dress = (label: string, v: Tally) => ({
      id: label,
      label,
      ratio: ratioOf(v),
      tone: toneOf(ratioOf(v)),
      done: v.done,
      total: v.total,
      // 원시 분(minute). 문자열만 넘기면 합계를 다시 셀 수 없다 — 링 범례가
      // 세 분류를 «더해» 전체 시간을 말해야 하므로 값 자체를 들고 다닌다.
      spent: v.spent,
      planned: v.planned,
      detail: t('routes.home.areaDetail', { done: String(v.done), total: String(v.total) }),
      time: t('routes.home.timeSpent', { spent: hours(v.spent), planned: hours(v.planned) }),
    });

    /** ⚠️ 순서는 «값»이 아니라 «이름»으로 정한다.
     *
     *  처음엔 「나쁜 것 위로」로 비율 정렬했다. 그 자체는 좋은 규칙이지만, 모델
     *  토글을 누를 때마다 비율이 바뀌고 따라서 행이 통째로 뛰었다 — 848U 에서
     *  보던 U-NII 가 840 에서는 세 칸 아래에 있으니 눈이 매번 다시 찾아야 했다.
     *  비교하려고 만든 토글이 비교를 방해한 셈이다.
     *
     *  그래서 위치는 «필터와 무관하게» 고정한다. 늦었다는 사실은 위치가 아니라
     *  막대 길이와 색이 말하고, 그 둘은 필터가 바뀌어도 같은 자리에서 변한다.
     *  대분류는 주파수가 낮은 쪽부터라는 도메인 순서를 선언으로 갖는다. */
    const byDeclared = (a: { label: string }, b: { label: string }): number => {
      const ia = CATEGORY_ORDER.indexOf(a.label);
      const ib = CATEGORY_ORDER.indexOf(b.label);
      if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
      return a.label.localeCompare(b.label);
    };

    return [...acc.entries()]
      .map(([label, cat]) => ({
        ...dress(label, cat.tally),
        families: [...cat.families.entries()]
          .map(([famLabel, fam]) => ({
            ...dress(famLabel, fam.tally),
            modes: [...fam.modes.entries()]
              .map(([modeLabel, m]) => dress(modeLabel, m))
              .sort((a, b) => a.label.localeCompare(b.label)),
          }))
          .sort((a, b) => a.label.localeCompare(b.label)),
      }))
      .sort(byDeclared);
  }, [progressQueries, activeProjects, activeModelId, t]);

  /** ⚠️ 대분류 카드와 «같은 방식»으로 센다(조건 수 가중). 도넛이 단순 평균이고
   *  카드가 가중 평균이면 둘이 어긋나고, 읽는 사람은 어느 쪽이 맞는지 알 수
   *  없다 — 한 화면에 두 개의 진실을 두지 않는다. */
  /** 전체 계획 case 수 — 링의 «원 하나»가 뜻하는 것. */
  const plannedTotal = useMemo(
    () => categories.reduce((n, c) => n + c.total, 0),
    [categories],
  );

  /** 끝난 case 수. 도넛 범례가 비율만이 아니라 «몇 건 중 몇 건»을 말하려면
   *  분자가 필요하다 — 48% 는 1,024건 중 489건일 때와 21건 중 10건일 때 같은
   *  글자지만 같은 뜻이 아니다. */
  const doneTotal = useMemo(
    () => categories.reduce((n, c) => n + c.done, 0),
    [categories],
  );

  /** 링 범례의 «시간» 축. 건수만으로는 남은 분량의 «무게»를 모른다 — 232건이
   *  40시간일 수도 200시간일 수도 있고, 일정을 잡는 사람이 세는 것은 후자다. */
  const spentTotal = useMemo(
    () => categories.reduce((n, c) => n + c.spent, 0),
    [categories],
  );
  const plannedMinutes = useMemo(
    () => categories.reduce((n, c) => n + c.planned, 0),
    [categories],
  );

  const programmeOverall = useMemo(() => {
    const total = categories.reduce((n, c) => n + c.total, 0);
    if (total === 0) return null;
    return categories.reduce((n, c) => n + c.done, 0) / total;
  }, [categories]);

  /** 일자별 누적 진행 추이 — 밴드별로 쌓아 올린다.
   *
   *  ⚠️ 새 읽기 계약을 만들지 않았다. 커버리지가 조건마다 `latest_measured_at`
   *  을 주므로 그것을 날짜로 묶어 누적하면 「그 날까지 몇 건이 측정됐나」가 나오고,
   *  분모는 계획(`progress`)의 총 case 수다. 두 읽기 모두 이미 화면이 쓰는 것이다.
   *
   *  ⚠️ 한계를 적어 둔다. 커버리지는 조건당 «최신» 시도만 담으므로, 재측정한
   *  조건은 «처음 측정한 날»이 아니라 «마지막으로 측정한 날»에 계산된다. 즉 이
   *  곡선은 「그 날 실제로 몇 건이 끝나 있었나」의 근사이고, 재측정이 많을수록
   *  과거가 실제보다 낮게 그려진다. 정확한 곡선은 시도 전량의 시계열을 주는
   *  읽기가 필요하고 그것은 아직 없다.
   *
   *  ⚠️ 마감일도 없다. 계획 발행일 + 창(window)으로 «일정선»을 그으려면 그 창이
   *  데이터에 있어야 하는데 `published_plan_expectation` 에 마감 칸이 없다.
   *  그래서 일정선은 그리지 않는다 — 없는 값을 그럴듯하게 그리는 것이 이 화면이
   *  가장 하지 말아야 할 일이다(§StatTile 의 판정과 같은 이유). */
  const timelineNow = useMemo(() => {
    const rows = coverage.data?.items ?? [];
    if (rows.length === 0 || plannedTotal === 0) return null;

    // 모드 → 대분류. 계획이 그 대응을 갖고 있으므로 UI 가 추측하지 않는다.
    const bandOfMode = new Map<string, string>();
    progressQueries.forEach((query, index) => {
      const project = activeProjects[index];
      if ((project?.project_id ?? null) !== activeModelId) return;
      for (const bucket of query.data ?? []) {
        const bucketId = bucket.progress_bucket_id ?? '';
        const cut = bucketId.indexOf(SEP);
        bandOfMode.set(
          bucket.progress_area ?? '',
          cut === -1 ? bucketId : bucketId.slice(0, cut),
        );
      }
    });

    const perDay = new Map<string, Map<string, number>>();
    for (const row of rows) {
      const day = (row.latest_measured_at ?? '').slice(0, 10);
      if (day === '') continue;
      const band = bandOfMode.get(row.technology ?? '') ?? '—';
      const bucket = perDay.get(day) ?? new Map<string, number>();
      bucket.set(band, (bucket.get(band) ?? 0) + 1);
      perDay.set(day, bucket);
    }
    if (perDay.size < 2) return null;

    /** 분모는 «전체 계획»이다. 밴드 값은 «전체의 몇 점(point)을 채웠나»이고,
     *  그래프는 그것을 쌓아 올린다 — 맨 위 선이 곧 전체 진행률이고, 그 선이
     *  가는 곳이 화면 위쪽의 100% 선이다.
     *
     *  ⚠️ 한 번 각 밴드의 «자기 계획 대비»로 바꿨다가 되돌렸다. 그 편이 선 하나만
     *  볼 때는 읽기 쉬웠지만, 이 그래프가 대답해야 하는 질문은 「이 계열이 얼마나
     *  끝났나」가 아니라 «전부 합쳐 100% 까지 얼마나 남았나» 다. 그건 쌓아야만
     *  보인다. 계열별 완료율은 바로 아래 카드 세 장이 이미 말하고 있다.
     *
     *  ⚠️ 그래서 범례의 단위는 % 가 아니라 «pt» 다. 「mmWave 4%」와 아래 카드의
     *  「mmWave 27%」가 한 화면에 같이 있으면 읽는 사람은 어느 쪽도 믿지 않는다.
     *  전체의 4점과 자기 계획의 27% 는 둘 다 참이고, 그 둘을 가르는 것은 단위뿐이다. */
    const bands = CATEGORY_ORDER.filter((band) =>
      [...perDay.values()].some((m) => m.has(band)),
    );
    const days = [...perDay.keys()].sort();
    const running = new Map<string, number>();
    const points = days.map((day) => {
      const add = perDay.get(day);
      for (const band of bands) {
        running.set(band, (running.get(band) ?? 0) + (add?.get(band) ?? 0));
      }
      return {
        day,
        // 전체 계획 대비 «기여분». 셋을 더하면 그 날의 전체 진행률이 된다.
        values: bands.map((band) => (running.get(band) ?? 0) / plannedTotal),
      };
    });

    return { bands, days, points };
  }, [coverage.data, progressQueries, activeProjects, activeModelId, plannedTotal]);

  /** ⚠️ 이전 커버리지를 «그대로 그리면 안 된다».
   *
   *  `keepPreviousData` 가 붙들고 있는 것은 분자(이전 모델의 측정 건수)인데,
   *  분모(`plannedTotal`)와 밴드 목록은 계획에서 오고 계획은 이미 캐시라
   *  «새 모델 것으로 즉시» 바뀐다. 그 둘을 곱하면 848U 의 측정을 845N 의 계획으로
   *  나눈 값이 나온다 — 어느 모델에도 존재하지 않는 숫자이고, 화면에는 멀쩡한
   *  곡선으로 보인다. 이 저장소가 가장 하지 말라고 적어 둔 종류의 그림이다.
   *
   *  그래서 붙드는 단위를 «데이터»가 아니라 «완성된 그래프»로 올린다. 마지막으로
   *  분자와 분모가 같은 모델이었던 결과를 통째로 들고 있다가, 새 조합이 완성되면
   *  교체한다. 그동안 그려지는 것은 「이전 모델의 진짜 그래프」이고, 그것을
   *  흐리게 두는 것(`data-stale`)이 「이건 지금 값이 아니다」라는 말이 된다.
   *
   *  ref 는 렌더가 아니라 effect 에서만 쓴다 — 렌더 중에 쓰면 StrictMode 의
   *  이중 렌더에서 순서가 보장되지 않는다. */
  const settledTimeline = useRef<typeof timelineNow>(null);
  const timelineIsStale = coverage.isPlaceholderData;
  useEffect(() => {
    if (!timelineIsStale && timelineNow !== null) settledTimeline.current = timelineNow;
  }, [timelineNow, timelineIsStale]);
  const timeline = timelineIsStale ? settledTimeline.current : timelineNow;


  /** 숫자와 % 사이를 한 칸 띄운다. 붙여 쓰면 「48%」가 한 덩어리로 뭉쳐
   *  숫자가 답답해 보이고, 특히 큰 활자(링 가운데 30px)에서 두드러진다.
   *
   *  ⚠️ 일반 공백이 아니라 U+00A0 이다. 좁은 칸에서 「48」과 「%」가 서로 다른
   *  줄로 갈라지면 그건 띄어쓰기가 아니라 «깨진 값»으로 읽힌다.
   *
   *  이 화면의 모든 백분율은 이 함수 하나를 지난다 — 링·카드·계열 게이지·
   *  모드 행·추이의 축과 끝값·상단 StatTile. 표기 규칙이 한 군데 있으면
   *  다음에 바꿀 때도 한 군데다. */
  const percent = (ratio: number): string => `${Math.round(ratio * 100)}\u00a0%`;


  /** 분류 라벨 → 색 슬롯.
   *
   *  ⚠️ 이것은 «표»이지 규칙이 아니다. `startsWith('LTE')` 같은 규칙은 새 분류가
   *  생기는 순간 조용히 틀리고, 표는 새 분류가 생기면 «색이 안 붙는 것»으로
   *  눈에 띈다. 조용히 틀리는 쪽보다 눈에 띄게 비는 쪽이 낫다.
   *  분류 자체는 계획 데이터(`progress_bucket_id`)가 소유한다 — 여기서 만들지 않는다. */
  const BAND_SLOT: Record<string, string> = {
    'Unlicensed band': 'unlicensed',
    'Licensed band': 'licensed',
    mmWave: 'mmwave',
  };
  const bandOf = (label: string): string => BAND_SLOT[label] ?? 'other';
  const clockOf = (iso: string | null | undefined): string =>
    iso != null && iso.length >= 19 ? iso.slice(11, 19) : t('routes.home.progressNone');

  /** `2026-09-08T05:11:02+00:00` -> `09-08 05:11`. A flow needs an ORDER, so
   *  it prints the instant rather than an age: "3h ago" beside "3h ago" tells
   *  the reader nothing about which came first. */
  const stampOf = (iso: string | null | undefined): string =>
    iso != null && iso.length >= 16 ? `${iso.slice(5, 10)} ${iso.slice(11, 16)}` : '—';

  /** Absolute instants are unreadable on a monitor — "08:15:47" makes the
   *  reader subtract. Relative age answers the only question a heartbeat
   *  raises: is this chamber still talking to us? */
  const agoOf = (iso: string | null | undefined): string => {
    if (iso == null) return t('routes.home.agoNever');
    const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
    if (Number.isNaN(seconds)) return t('routes.home.agoNever');
    if (seconds < 60) return t('routes.home.agoSeconds', { n: String(seconds) });
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return t('routes.home.agoMinutes', { n: String(minutes) });
    return t('routes.home.agoHours', { n: String(Math.floor(minutes / 60)) });
  };

  return (
    <section className="console" aria-labelledby="home-heading">
      <PageHeader
        eyebrow={t(`routes.home.scope.${scope}.eyebrow`)}
        title={t(`routes.home.scope.${scope}.title`)}
        titleId="home-heading"
        description={t(`routes.home.scope.${scope}.description`)}
      />

      <div className="console-grid" data-scope={scope} data-testid="console-grid">
        {/* ── fleet — 현장 범위 전용 ─────────────────────────────────────
            PM 화면에서 뺐다. 「구성 5개 중 1개 가용」은 사실이지만 PM 의 결정에
            들어가지 않는 사실이고, 화면의 첫 번째 띠는 그의 질문에 답해야 한다. */}
        {showsOperator && (
        <Card
          as="section"
          className="console-panel console-grid__wide"
          aria-labelledby="console-fleet-heading"
        >
          <div className="console-panel__head">
            <h2 className="console-panel__title" id="console-fleet-heading">
              {t('routes.home.fleetHeading')}
            </h2>
            <span className="console-panel__meta mono">{clockOf(chambers.data?.server_time)}</span>
          </div>
          {chambers.isPending && <BlockSkeleton lines={2} testId="console-fleet-loading" />}
          {chambers.isError && (
            <ErrorState
              testId="console-fleet-error"
              message={describeApiError(chambers.error, 'platform')}
            />
          )}
          {chambers.isSuccess && (
            <div className="stat-strip" data-testid="console-stat-strip">
              <StatTile
                testId="stat-chambers-total"
                label={t('routes.home.statTotal')}
                value={String(fleet.total)}
              />
              <StatTile
                testId="stat-chambers-idle"
                label={t('routes.home.statIdle')}
                value={String(fleet.idle)}
                change={{
                  direction: fleet.idle > 0 ? 'up' : 'flat',
                  sentiment: fleet.idle > 0 ? 'good' : 'neutral',
                  label: t('routes.home.statIdleHint', { count: String(fleet.idle) }),
                }}
              />
              <StatTile
                testId="stat-chambers-running"
                label={t('routes.home.statRunning')}
                value={String(fleet.inUse)}
                change={{
                  direction: fleet.inUse > 0 ? 'up' : 'flat',
                  sentiment: 'neutral',
                  label: t('routes.home.statRunningHint', { count: String(fleet.inUse) }),
                }}
              />
              <StatTile
                testId="stat-chambers-offline"
                label={t('routes.home.statOffline')}
                value={String(fleet.offline)}
                change={{
                  direction: fleet.offline > 0 ? 'up' : 'flat',
                  sentiment: fleet.offline > 0 ? 'bad' : 'good',
                  label: t('routes.home.statOfflineHint', { count: String(fleet.offline) }),
                }}
              />
              {showsProgramme && (
                <StatTile
                  testId="stat-programme"
                  label={t('routes.home.statProgramme')}
                  {...(programmeOverall === null
                    ? { unavailable: t('routes.home.statProgrammeEmpty') }
                    : {
                        value: percent(programmeOverall),
                        change: {
                          direction: 'flat' as const,
                          sentiment: 'neutral' as const,
                          label: t('routes.home.statProgrammeHint', {
                            count: String(activeProjects.length),
                          }),
                        },
                      })}
                />
              )}
              <StatTile
                testId="stat-failure-rate"
                label={t('routes.home.statFailureRate')}
                unavailable={t('routes.home.statNoContract')}
              />
            </div>
          )}
        </Card>

        )}

        {/* ── running now — 현장 범위 전용 ─────────────────────────────── */}
        {showsOperator && (
        <Card
          as="section"
          className="console-panel console-grid__narrow"
          aria-labelledby="console-running-heading"
        >
          <div className="console-panel__head">
            <h2 className="console-panel__title" id="console-running-heading">
              {t('routes.home.inProgressHeading')}
            </h2>
            <span className="console-panel__meta">
              {t('routes.home.activeProjects', { count: String(activeProjects.length) })}
            </span>
          </div>
          {runningRows.length === 0 ? (
            <EmptyState
              testId="console-running-empty"
              title={t('routes.home.inProgressEmptyTitle')}
              description={t('routes.home.inProgressEmptyBody')}
            />
          ) : (
            <HealthBars
              testId="console-running-bars"
              rows={runningRows}
              formatRatio={percent}
              forceTone="progress"
            />
          )}
        </Card>

        )}

        {/* ── operator: 내 프로젝트 + 할 일 ──────────────────────────────
            이 화면의 첫 번째 질문은 「내가 뭘 해야 하나」다. 그래서 이 패널이
            맨 앞에 서고, 챔버·집계는 그 뒤의 맥락이 된다. 순서가 곧 주장이다. */}
        {showsOperator && (
          <Card
            as="section"
            className="console-panel console-grid__wide"
            aria-labelledby="console-todo-heading"
          >
            <div className="console-panel__head">
              <h2 className="console-panel__title" id="console-todo-heading">
                {t('routes.home.todoHeading')}
              </h2>
              <span className="console-panel__meta mono">
                {t('routes.home.todoCount', { count: String(todoTotal) })}
              </span>
            </div>

            {/* 내 프로젝트 — 고르는 것이 아니라 «지금 무엇을 보고 있는가»의 표시.
                실제 선택은 프로젝트 컨텍스트(`?project=`)가 생기면 그쪽이 갖는다. */}
            <ul className="console-projects" data-testid="console-projects">
              {activeProjects.map((project) => (
                <li
                  className="console-projects__item"
                  data-current={project.project_id === focusProjectId}
                  key={project.project_id ?? project.project_code}
                >
                  <span className="console-projects__code mono">{project.project_code}</span>
                  <span className="console-projects__model">
                    {project.model_name ?? project.eut_description ?? '—'}
                  </span>
                </li>
              ))}
            </ul>

            {todo.length === 0 ? (
              <EmptyState
                testId="console-todo-empty"
                title={t('routes.home.todoEmptyTitle')}
                description={t('routes.home.todoEmptyBody')}
              />
            ) : (
              <ul className="console-todo" data-testid="console-todo">
                {todo.map((row) => (
                  <li className="console-todo__row" key={row.technology}>
                    <span className="console-todo__tech">{row.technology}</span>
                    <span className="console-todo__count mono">
                      {t('routes.home.todoRemaining', { count: String(row.remaining) })}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        )}

        {/* ── operator: 기술별 진행 ───────────────────────────────────────── */}
        {showsOperator && (
          <Card
            as="section"
            className="console-panel console-grid__wide"
            aria-labelledby="console-tech-heading"
          >
            <div className="console-panel__head">
              <h2 className="console-panel__title" id="console-tech-heading">
                {t('routes.home.techHeading')}
              </h2>
              <span className="console-panel__meta mono">
                {activeProjects[0]?.project_code ?? ''}
              </span>
            </div>
            {focusProjectId === null || technologyRows.length === 0 ? (
              <EmptyState
                testId="console-tech-empty"
                title={t('routes.home.techEmptyTitle')}
                description={t('routes.home.techEmptyBody')}
              />
            ) : (
              <HealthBars
                testId="console-tech-bars"
                rows={technologyRows}
                formatRatio={percent}
                warnBelow={0.99}
                badBelow={0.5}
              />
            )}
          </Card>
        )}

        {/* ── operator: 측정 흐름 ─────────────────────────────────────────
            Replaces a standalone "nonconformities" list. A separate failure
            count is one more scattered aggregate; a failure sitting red inside
            the timeline says the same thing AND says when it happened and what
            came before it. */}
        {showsOperator && (
          <Card
            as="section"
            className="console-panel console-grid__narrow"
            aria-labelledby="console-flow-heading"
          >
            <div className="console-panel__head">
              <h2 className="console-panel__title" id="console-flow-heading">
                {t('routes.home.flowHeading')}
              </h2>
              <span className="console-panel__meta mono">{nonconformities.length}</span>
            </div>
            {flow.length === 0 ? (
              <EmptyState
                testId="console-flow-empty"
                title={t('routes.home.flowEmptyTitle')}
                description={t('routes.home.flowEmptyBody')}
              />
            ) : (
              <>
                <ol className="console-flow" data-testid="console-flow">
                  {flow.map((row) => {
                    const kind = verdictToStatusKind(row.latest_verdict ?? '');
                    const failing = FAILING_VERDICTS.has(
                      (row.latest_verdict ?? '').trim().toLowerCase(),
                    );
                    return (
                      <li
                        className="console-flow__row"
                        data-failing={failing}
                        key={row.condition_hash ?? row.technology}
                      >
                        <span className="console-flow__when mono">
                          {stampOf(row.latest_measured_at)}
                        </span>
                        <span className="console-flow__what">
                          <span className="console-flow__tech">{row.technology ?? '—'}</span>
                          <span className="console-flow__who mono">
                            {t('routes.home.issueMeta', {
                              attempt: String(row.latest_attempt_number ?? 0),
                              operator: row.latest_operator ?? '—',
                            })}
                          </span>
                        </span>
                        {kind !== null && <StatusBadge status={kind} />}
                      </li>
                    );
                  })}
                </ol>
                {/* The declared holes. See the `flow` comment above. */}
                <p className="console-flow__gap">{t('routes.home.flowMissing')}</p>
              </>
            )}
          </Card>
        )}

        {/* ── pm: 전체 + 대분류 도넛 2×2 ─────────────────────────────────
            네 칸: 전체 / Unlicensed / Licensed / mmWave. 도넛이 넷이라 눈이
            같은 형태끼리 비교하고, 모드는 그 아래 작은 줄로 붙어 「어디가
            늦나」를 한 단계 더 내려가서 답한다. */}
        {showsProgramme && (
          <Card
            as="section"
            className="console-panel console-grid__wide"
            aria-labelledby="console-programme-heading"
          >
            <div className="console-panel__head">
              <h2 className="console-panel__title" id="console-programme-heading">
                {t('routes.home.programmeHeading')}
              </h2>
              {/* 모델 토글. 「진행 중 3건」이라는 «숫자»는 이 화면에서 아무것도
                  하지 않는 사실이었다 — 그 자리에 «고를 수 있는 것»을 둔다. */}
              <div className="panel-actions">
              <div className="model-toggle" role="group" aria-label={t('routes.home.modelFilterLabel')}>
                {activeProjects.map((project) => (
                  <button
                    type="button"
                    className="model-toggle__btn"
                    key={project.project_id ?? project.project_code}
                    aria-pressed={activeModelId === (project.project_id ?? null)}
                    onClick={() => setModelFilter(project.project_id ?? null)}
                  >
                    {project.model_name ?? project.project_code}
                  </button>
                ))}
                </div>
                <button
                  type="button"
                  className="export-btn"
                  aria-expanded={exportOpen}
                  onClick={() => setExportOpen((open) => !open)}
                >
                  <svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6">
                    <path d="M8 2v8m0 0 3-3m-3 3L5 7" strokeLinecap="round" strokeLinejoin="round" />
                    <path d="M2.5 11v1.5A1.5 1.5 0 0 0 4 14h8a1.5 1.5 0 0 0 1.5-1.5V11" strokeLinecap="round" />
                  </svg>
                  {t('routes.home.exportLabel')}
                </button>
              </div>
            </div>

            {exportOpen && (
              <aside className="export-note" data-testid="export-note">
                <p className="export-note__lede">{t('routes.home.exportMockup')}</p>
                <ul className="export-note__list">
                  <li>{t('routes.home.exportQ1')}</li>
                  <li>{t('routes.home.exportQ2')}</li>
                  <li>{t('routes.home.exportQ3')}</li>
                  <li>{t('routes.home.exportQ4')}</li>
                </ul>
              </aside>
            )}

            {categories.length === 0 ? (
              <EmptyState
                testId="console-programme-empty"
                title={t('routes.home.programmeEmptyTitle')}
                description={t('routes.home.programmeEmptyBody')}
              />
            ) : (
              <>
              {/* 좌 «상태» · 우 «속도». 링은 「지금 어디」 하나를 크게 말하고,
                  추이는 「어떻게 왔고 100% 까지 얼마나 남았나」를 말한다. 둘은
                  같은 질문의 두 시제라 나란히 서야 의미가 있고, 그래서 하나를
                  다른 하나로 대체하지 않았다. 대분류 카드 셋은 그 아래 한 줄로
                  내려간다 — 위 두 개가 「전체」를 말한 다음에 오는 것이 「쪼갬」이다. */}
              <div className="programme-head">
                {programmeOverall !== null && (
                  <DonutProgress
                    testId="programme-donut"
                    ratio={programmeOverall}
                    label={percent(programmeOverall)}
                    title={t('routes.home.donutTitle')}
                    caption={t('routes.home.donutCaption')}
                    legend={[
                      {
                        id: 'done',
                        kind: 'done',
                        label: t('routes.home.donutDone'),
                        value: t('routes.home.donutCases', { count: String(doneTotal) }),
                        time: hours(spentTotal),
                      },
                      {
                        id: 'rest',
                        kind: 'rest',
                        label: t('routes.home.donutRest'),
                        value: t('routes.home.donutCases', {
                          count: String(Math.max(0, plannedTotal - doneTotal)),
                        }),
                        time: hours(Math.max(0, plannedMinutes - spentTotal)),
                      },
                    ]}
                  />
                )}
                {timeline !== null && (
                  <StackedTrend
                    testId="programme-trend"
                    bands={timeline.bands}
                    points={timeline.points}
                    bandSlot={bandOf}
                    stale={timelineIsStale}
                    formatRatio={percent}
                    ceilingLabel={t('routes.home.trendCeiling')}
                  />
                )}
              </div>
              <div className="band-cards" data-testid="programme-categories">
                  {categories.map((category) => (
                    <section
                      className="band-card"
                      data-band={bandOf(category.label)}
                      key={category.id}
                    >
                      {/* flowdeck 의 "Run minutes" 위젯 형태. 큰 숫자를 카드 안에
                          세우는 대신 라벨을 굵게, 값을 작고 조용하게 두고, 시각적
                          무게는 미터 한 줄이 진다. 카드가 셋 나란히 설 때 큰
                          숫자 셋은 서로 경쟁하지만 미터 셋은 «비교»가 된다. */}
                      <div className="band-card__top">
                        <b className="band-card__name">{category.label}</b>
                        <span className="band-card__pct">{percent(category.ratio)}</span>
                      </div>
                      <div className="band-card__meter">
                        <i style={{ width: `${category.ratio * 100}%` }} />
                      </div>
                      <p className="band-card__caption">
                        {category.detail} · {category.time}
                      </p>
                    </section>
                  ))}
              </div>
              </>
            )}

            {/* 대분류 3열 × 계열 세로 누적.
                위의 카드 셋과 «같은 가로 순서»로 열이 서므로, 카드에서 눈이
                멈춘 자리 바로 아래에 그 분류의 속이 있다. 계열이 열 안에서
                세로로 쌓이면 「U-NII 가 어디까지」와 「그 안의 U-NII-3 은」이
                한 시선에 같이 들어온다 — 층을 옆으로 펼치면 그 관계가 끊긴다. */}
            {categories.length > 0 && (
              <div className="band-columns" data-testid="programme-columns">
                {categories.map((category) => (
                  <section
                    className="band-column"
                    data-band={bandOf(category.label)}
                    key={category.id}
                  >
                    <h3 className="eyebrow band-column__title">{category.label}</h3>

                    {category.families.map((family) => (
                      <div className="family-block" key={family.id} data-tone={family.tone}>
                        {/* 행 자체가 게이지다. 얇은 트랙을 따로 두면 「라벨 줄」과
                            「막대 줄」이 갈라져 눈이 두 번 움직이는데, 채움을
                            배경으로 내리면 한 번에 읽힌다. 눈금은 계측기 미터의
                            어휘이고, 여기서는 25% 단위를 세어 준다. */}
                        <div className="gauge" style={{ '--fill': `${family.ratio * 100}%` } as CSSProperties}>
                          <span className="gauge__ticks" aria-hidden="true" />
                          <span className="gauge__name">{family.label}</span>
                          <span className="gauge__pct">{percent(family.ratio)}</span>
                        </div>

                        <ul className="mode-list">
                          {family.modes.map((mode) => (
                            <li
                              className="mode-list__row"
                              key={mode.id}
                              data-linked={linkedMode === mode.label}
                            >
                              {/* 링크는 «양방향»이다. 이슈에서 진행률로만 갈 수
                                  있으면, 진행률을 보다가 「이건 왜 늦지」라고
                                  물은 사람이 이슈 목록에서 그 모드를 눈으로 찾아야
                                  한다. 버튼인 이유는 이슈 행과 같다 — 올리면
                                  켜지고 누르면 고정되는 것은 상호작용이다. */}
                              <button
                                type="button"
                                className="gauge gauge--sm"
                                style={{ '--fill': `${mode.ratio * 100}%` } as CSSProperties}
                                data-linked={linkedMode === mode.label}
                                aria-pressed={pinnedMode === mode.label}
                                onMouseEnter={() => setHoverMode(mode.label)}
                                onMouseLeave={() => setHoverMode(null)}
                                onFocus={() => setHoverMode(mode.label)}
                                onBlur={() => setHoverMode(null)}
                                onClick={() =>
                                  setPinnedMode((cur) => (cur === mode.label ? null : mode.label))
                                }
                              >
                                <span className="gauge__ticks" aria-hidden="true" />
                                <span className="gauge__name">{mode.label}</span>
                                <span className="gauge__pct">{percent(mode.ratio)}</span>
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </section>
                ))}
              </div>
            )}
          </Card>
        )}

        {/* ── pm: 이슈 트래커 ─────────────────────────────────────────────
            진행률 옆에 서는 이유: PM 이 「몇 % 인가」 다음에 반드시 묻는 것이
            「무엇이 막혀 있나」이고, 그 둘을 다른 화면에 두면 보고 자리에서
            탭을 옮기게 된다. 데이터는 새로 만들지 않았다 — 진행률이 이미 읽는
            커버리지의 «부적합 판정» 행이 그대로 이슈다. */}
        {showsProgramme && (
          <Card
            as="section"
            className="console-panel console-grid__narrow"
            aria-labelledby="console-issues-heading"
          >
            <div className="console-panel__head">
              <h2 className="console-panel__title" id="console-issues-heading">
                {t('routes.home.issuesHeading')}
              </h2>
              <span className="console-panel__meta">
                {t('routes.home.issuesMeta', { count: String(nonconformities.length) })}
              </span>
            </div>
            {nonconformities.length === 0 ? (
              <EmptyState
                testId="console-issues-empty"
                title={t('routes.home.issuesEmptyTitle')}
                description={t('routes.home.issuesEmptyBody')}
              />
            ) : (
              <ul className="issue-list" data-testid="console-issues">
                {issueGroups.slice(0, 12).map((row) => {
                  const kind = verdictToStatusKind(row.verdict);
                  return (
                    <li className="issue-row" key={row.mode}>
                      {/* 버튼인 이유: 이 행은 «상호작용»한다 — 올리면 왼쪽이 켜지고
                          누르면 고정된다. `<li tabIndex>` 로 흉내 내면 접근성
                          도구에게는 그냥 목록 항목이라 그 동작이 존재하지 않는다. */}
                      <button
                        type="button"
                        className="issue-row__btn"
                        data-linked={linkedMode === row.mode}
                        aria-pressed={pinnedMode === row.mode}
                        onMouseEnter={() => setHoverMode(row.mode)}
                        onMouseLeave={() => setHoverMode(null)}
                        onFocus={() => setHoverMode(row.mode)}
                        onBlur={() => setHoverMode(null)}
                        onClick={() => {
                          const next = pinnedMode === row.mode ? null : row.mode;
                          setPinnedMode(next);
                          // 상태 반영 뒤에 찾아야 `data-linked` 가 붙어 있다.
                          window.requestAnimationFrame(() => revealMode(next));
                        }}
                      >
                        <span className="issue-row__head">
                          <span className="issue-row__tech">{row.mode}</span>
                          {row.count > 1 && (
                            <span className="issue-row__count">{row.count}</span>
                          )}
                          {kind !== null && <StatusBadge status={kind} />}
                        </span>
                        <span className="issue-row__meta">
                          {stampOf(row.latestAt)} ·{' '}
                          {t('routes.home.issueWho', {
                            attempt: String(row.attempt),
                            operator: row.operator,
                          })}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        )}

        {/* ── the fleet, as blocks — 현장 범위에서만 ─────────────────────
            A PM does not act on "which bay is free"; showing it spends the
            screen's most valuable band on something their decisions never
            read. Scope is what makes three homes three homes. */}
        {showsOperator && (
        <>
        {/* ── the fleet, as blocks ────────────────────────────────────────
            A chamber is a room, not a row (see `ChamberCard`). The grid keeps
            the fleet's shape so "which bay is free" is answered by looking,
            not by reading a status column and mapping it back to a place. */}
        <section
          className="console-fleet console-grid__full"
          aria-labelledby="console-cards-heading"
        >
          <div className="console-panel__head">
            <h2 className="console-panel__title" id="console-cards-heading">
              {t('routes.home.chamberTableHeading')}
            </h2>
            <span className="console-panel__meta">
              {t('routes.home.fleetCount', { count: String(items.length) })}
            </span>
          </div>
          {items.length === 0 ? (
            <EmptyState
              testId="console-table-empty"
              title={t('routes.home.chamberTableEmptyTitle')}
              description={t('routes.home.chamberTableEmptyBody')}
            />
          ) : (
            <div className="chamber-grid" data-testid="chamber-grid">
              {items.map((chamber) => (
                <ChamberCard
                  key={chamber.chamber_id}
                  name={chamber.name}
                  chamberId={chamber.chamber_id}
                  status={chamberStatusKind(chamber.status)}
                  running={chamber.progress?.is_running === true}
                  ratio={chamber.progress?.ratio ?? null}
                  {...(chamber.progress != null
                    ? {
                        progressLabel: t('routes.home.progressDetail', {
                          completed: String(chamber.progress.completed),
                          total: String(chamber.progress.total),
                        }),
                      }
                    : {})}
                  heartbeatLabel={agoOf(chamber.last_heartbeat_at)}
                  address={chamber.base_url}
                  {...(chamber.unavailable_reason != null
                    ? { note: chamber.unavailable_reason }
                    : {})}
                />
              ))}
            </div>
          )}
        </section>
        </>
        )}

      </div>
    </section>
  );
}
