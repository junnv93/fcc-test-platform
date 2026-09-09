import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';



import {
  fetchChambers,
  fetchClaimsPage,
  fetchCoveragePage,
  type ActiveClaimEnvelope,
  fetchPlanConditionsPage,
  type PlanConditionEnvelope,
  fetchProjectProgress,
  fetchProjects,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useT } from '@/i18n';
import { chamberStatusLabel } from '@/routes/chambers/status';
import { useCurrentOperator } from '@/shared/current-operator';
import { ROUTE_PATHS } from '@/shared/route-links';
import { BlockSkeleton, Card, ChamberCard, DonutProgress, EmptyState, ErrorState, HealthBars, PageHeader, ShareTreemap, hueOf, StackedTrend, StatTile, StatusBadge, chamberStatusKind, describeApiError, verdictToStatusKind } from '@/ui';

import type { HealthRow, ShareCell } from '@/ui';
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

/** 「잡혀만 있는 것」의 문턱. 배정된 지 이만큼 지났는데 아직 측정이 하나도
 *  붙지 않았으면 정체로 센다.
 *
 *  ⚠️ 24시간인 근거는 «하룻밤»이다 — 그보다 짧으면 정상적인 야간 공백이 전부
 *  정체로 잡히고, 그러면 이 표식은 늘 켜져 있어서 아무 말도 하지 않게 된다.
 *  ⚠️ 그리고 이것은 «추정»이 아니라 관측이다. 「늦어질 것이다」가 아니라
 *  「하루 동안 아무 일도 일어나지 않았다」는 사실만 말한다. */
const STALL_MS = 24 * 60 * 60 * 1000;

/** 대분류의 «선언된» 순서 — 주파수가 낮은 쪽부터. 정렬·추이 스택·카드가 같은
 *  순서를 써야 같은 밴드가 화면 어디서나 같은 자리에 온다.
 *  ⚠️ 모듈 상수다. 컴포넌트 안에 두면 렌더마다 새 배열이라 useMemo 가 매번 다시 돈다. */
const CATEGORY_ORDER = ['Unlicensed band', 'Licensed band', 'mmWave'];

export function ConsoleHome({ scope }: ConsoleHomeProps): JSX.Element {
  const { t } = useT();
  const me = useCurrentOperator();
  const showsOperator = scope === 'operator' || scope === 'manager';
  /** 계획 패널(대분류 카드 + 계열 열) — 세 화면 전부. */
  const showsProgramme = true;

  /** 현장 패널들(시험 구성 현황 · 진행 중인 측정 · 할 일 · 기술별 진행 ·
   *  측정 흐름 · 구성 블록)을 «잠시» 내린 스위치.
   *
   *  ⚠️ 지우지 않았다. 실무자 화면을 「진행률 + 이슈」 둘로 좁혀 보는 중이고,
   *  그 판단이 옳은지는 써 봐야 안다. 코드를 지우면 되돌리는 데 커밋을 뒤져야
   *  하고, 그러면 「일단 빼 보자」가 값싼 실험이 아니게 된다.
   *
   *  ⚠️ 그리고 이 상수는 «죽은 코드를 만든다». 아래 패널들이 렌더되지 않는
   *  동안에도 그 데이터(fleet · runningRows · technologyRows · flow)는 계속
   *  계산되고 질의도 계속 나간다. 그것을 아는 채로 두는 것이고, 이 스위치가
   *  영구가 되는 순간 — 즉 「돌아갈 생각이 없다」가 정해지는 순간 — 패널과
   *  그 데이터를 함께 지우는 것이 그때의 정공이다. 스위치가 오래 남으면
   *  그 자체가 부채다. */
  const SHOWS_FIELD_PANELS = false;
  /** 링과 추이는 «보고»의 도구다.
   *
   *  ⚠️ 실무자 화면에서 뺀다. 「전체 48%」와 「지난 2주 기울기」는 참이지만
   *  시험할 사람의 다음 행동을 바꾸지 않는다 — 그가 묻는 것은 「이 계열에서
   *  뭐가 남았나」이고 그 답은 아래 표에 있다. 화면의 첫 줄은 보는 사람의
   *  질문에 답해야 한다(현장 패널을 PM 화면에서 뺀 것과 같은 판단). */
  const showsProgrammeSummary = scope === 'pm' || scope === 'manager';

  /* ── 전체 총괄(`manager`)만의 축소 ────────────────────────────────────
     🔴 2026-09-09 — 총괄 화면을 «두 패널, 같은 높이»로 좁힌다.
     진행률(링 + 추이)과 이슈 트래커 둘만 남고 나머지는 내린다.

     ⚠️ 왜 총괄에서만인가. 세 화면은 «같은 부품, 다른 질문»이다 —
       operator  「내가 다음에 뭘 하나」  → 요약 띠 + 계열/모드 목록이 답한다
       pm        「어디까지 왔나」        → 링·추이·대분류·모드가 전부 답한다
       manager   「무엇이 어긋났나」      → 진행률 «하나»와 막힌 것 «하나»의 대조
     총괄이 pm 의 모든 것을 또 갖고 있으면 그것은 총괄이 아니라 pm 의 사본이고,
     사본은 언젠가 원본과 다른 말을 한다. 총괄이 답할 질문은 «대조»뿐이라
     대조에 쓰이지 않는 층(대분류 카드 · 계열/모드 열)은 여기서 소음이다.

     ⚠️ 이 셋은 «총괄 전용»이다. 같은 부품을 쓰는 다른 두 화면은 건드리지
     않는다 — 오늘 이미 한 번 겪었다(이슈 트래커의 하이라이트를 전역으로
     고쳐서 시험진행률 화면까지 바꿨다). 조건은 반드시 scope 로 쓴다. */

  /** 요약 띠(한 것 · 할 것 · 이슈)는 «실무자»의 것이다. 총괄에서는 아래 두
   *  패널이 같은 사실을 더 정확히 말하므로 같은 숫자를 두 번 세지 않는다. */
  const showsToday = scope === 'operator';

  /** 대분류 카드 셋과 계열/모드 열. 「쪼갬」은 pm 과 실무자의 도구다. */
  const showsBandBreakdown = scope !== 'manager';

  /** 내보내기는 «목업»이고, 총괄에서 뽑을 보고서가 따로 없다. */
  const showsExport = scope === 'pm';

  /* ── 총괄에 «더해지는» 것 ─────────────────────────────────────────────
     위에서 뺀 것들이 「pm 의 사본」이라 뺀 것이라면, 여기 더하는 셋은 총괄
     에서만 «대조»가 되는 것들이다. 축이 서로 다르다:

       설비 현황   어디서   (공간) — 챔버 × 그 챔버가 붙잡고 있는 잔여
       사람 부하   누가     (인력) — 사번 × 잡은 것 × 잔여 시간
       활동 흐름   언제     (시간) — 지금 도는 것, 방금 끝난 것

     ⚠️ 셋이 같은 사실을 세 번 말하는 것이 아니다. 챔버는 못 늘리고 사람은
     옮길 수 있다 — 총괄이 실제로 당길 수 있는 레버가 가운데 하나뿐이라,
     그 레버 옆에 「못 늘리는 것」과 「지금 벌어지는 일」이 같이 있어야 한다.
     ⚠️ 예측은 없다. 「이 속도면 언제 끝난다」는 한 번 틀리는 순간 그 옆의
     참인 숫자들까지 신뢰를 잃는다. 셋 다 관측된 사실만 말한다. */
  const showsFleet = scope === 'manager' || (showsOperator && SHOWS_FIELD_PANELS);
  const showsLoad = scope === 'manager';
  const showsActivity = scope === 'manager';

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
  /** 계획 조건. 이 화면이 이것을 받는 이유는 «시간»이다 — 커버리지는 무엇이
   *  측정됐는지는 알려주지만 그 하나가 몇 분짜리였는지는 모른다.
   *  `condition_hash` 로 조인해야 「이 날까지 몇 «시간»이 끝났나」가 나온다. */
  const planConditions = useQuery({
    queryKey: ['console-plan-conditions', activeModelId],
    queryFn: async () => {
      const items: PlanConditionEnvelope[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 25; page += 1) {
        const chunk = await fetchPlanConditionsPage(activeModelId ?? '', cursor);
        items.push(...chunk.items);
        if (chunk.nextCursor == null || chunk.nextCursor === '') break;
        cursor = chunk.nextCursor;
      }
      return items;
    },
    enabled: activeModelId !== null,
    placeholderData: keepPreviousData,
  });

  /** 지금 «배정된» 조건. 15명이 한 프로젝트를 나눠 하므로, 남은 목록에 남이
   *  이미 손대고 있는 것이 섞여 있으면 두 사람이 같은 것을 재게 된다.
   *
   *  ⚠️ 읽기만 한다. 잡기·놓기는 작업 화면의 일이다.
   *
   *  🔴 「내 것」은 «아직 계산할 수 없다». 이 원장의 `operator` 는 문자열이고
   *  로그인 신원은 OIDC subject 라 두 키가 다르다. 사번이 양쪽에 같은 값으로
   *  들어오기 전까지 「내 배정」과 「남의 배정」을 가르면 조용히 틀린다 — 그래서
   *  지금은 «배정된 것 전부»를 표시하고 누가 잡았는지를 함께 적는다. 키가
   *  맞춰지면 여기 필터 한 줄이 붙고 화면 구조는 그대로다. */
  const claims = useQuery({
    queryKey: ['console-claims', activeModelId],
    queryFn: async () => {
      const items: ActiveClaimEnvelope[] = [];
      let cursor: string | undefined;
      for (let page = 0; page < 25; page += 1) {
        const chunk = await fetchClaimsPage(activeModelId ?? '', cursor);
        items.push(...chunk.items);
        if (chunk.nextCursor == null || chunk.nextCursor === '') break;
        cursor = chunk.nextCursor;
      }
      return items;
    },
    enabled: activeModelId !== null && showsOperator,
    placeholderData: keepPreviousData,
  });

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

  /** 세션 → 사용자. 챔버가 「누가 쓰고 있나」를 말하려면 이 다리가 필요하다.
   *
   *  ⚠️ 새 계약을 만들지 않았다. 챔버 가용성 응답에는 `session_id` 가 있고
   *  배정 원장(`active_claims`)에는 `session_id` 와 `operator` 가 함께 있다 —
   *  둘은 이미 같은 키를 들고 있었고, 아무도 그 둘을 이어 보지 않았을 뿐이다.
   *
   *  ⚠️ 이 다리는 «끊어질 수 있다». claim 에 session_id 가 없으면(오늘 시드가
   *  그렇다) 방은 돌고 있는데 주인을 모른다. 그때 화면은 빈칸이 아니라
   *  「사용자 미상」이라고 말한다 — 빈칸은 「아무도 안 쓴다」로 읽히고, 그러면
   *  관리자가 쓰이는 방에 사람을 또 보낸다. */
  const operatorBySession = useMemo(() => {
    const map = new Map<string, string>();
    for (const claim of claims.data ?? []) {
      const session = (claim.session_id ?? '').trim();
      const who = (claim.operator ?? '').trim();
      if (session !== '' && who !== '') map.set(session, who);
    }
    return map;
  }, [claims.data]);

  /** 모델 칩을 눌러 «갈아타는 중»인가.
   *
   *  ⚠️ `isLoading` 이 아니라 `isFetching` 이다. `keepPreviousData` 를 쓰면 이전
   *  모델의 값이 그대로 서 있으므로 `isLoading` 은 false 이고, 그래서 지금까지
   *  이 화면에는 「바뀌는 중」이라는 표시가 «있을 수 없었다» — 값이 도착하는
   *  프레임에 통째로 갈아 끼워지는 것이 전부였고, 그것이 「딸깍」의 정체다. */
  const modelIsSwitching =
    planConditions.isFetching || coverage.isFetching || claims.isFetching;

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

  /** 조건 → 표준시간(분).
   *
   *  ⚠️ 이 지도가 없으면 아래 계산들이 «건수»를 세게 되고, 그러면 같은 화면의
   *  링(시간)과 다른 숫자가 된다 — 실제로 그랬다(61% vs 56%). 진행률이 시간
   *  베이스인 이상 부하도 시간 베이스여야 한다: 100건 중 80건을 했어도 남은
   *  20건이 더 오래 걸리는 경우가 대부분이다. */
  const minutesByCondition = useMemo(() => {
    const map = new Map<string, number>();
    for (const plan of planConditions.data ?? []) {
      map.set(plan.condition_hash ?? '', plan.planned_minutes ?? 0);
    }
    return map;
  }, [planConditions.data]);

  /** 사람별 부하 — 잡은 것 · 남은 시간 · 정체.
   *
   *  총괄이 실제로 당길 수 있는 레버는 「누굴 어디로 보낼까」 하나다. 챔버는
   *  늘릴 수 없고 계획은 줄일 수 없지만 사람은 옮길 수 있다. 그런데 지금까지
   *  이 화면에 그 레버가 «없었다» — 진행률은 얼마나 왔는지만 말하고, 누가
   *  무엇을 얼마나 들고 있는지는 어디에도 없었다.
   *
   *  ⚠️ 새 계약을 만들지 않았다. `active_claims` 는 이미 읽고 있고 `operator`
   *  필드가 있다. 남은 시간은 위 표준시간 지도로 곱한다.
   *  ⚠️ 이미 측정된 조건은 잔여에서 뺀다 — claim 이 남아 있어도 일은 끝났다.
   *  ⚠️ 사번이 비어 있는 claim 을 «버리지 않는다». 버리면 총합이 조용히 줄어
   *  화면이 실제보다 한가해 보인다. 「—」로 모아서 보이게 둔다. */
  const operatorLoad = useMemo(() => {
    const measured = new Set(
      (coverage.data?.items ?? []).map((row) => row.condition_hash ?? ''),
    );
    const now = Date.now();
    const rows = new Map<string, { who: string; held: number; minutes: number; stalled: number }>();
    for (const claim of claims.data ?? []) {
      const who = (claim.operator ?? '').trim();
      const key = who === '' ? '—' : who;
      const entry = rows.get(key) ?? { who: key, held: 0, minutes: 0, stalled: 0 };
      entry.held += 1;
      if (!measured.has(claim.condition_hash ?? '')) {
        entry.minutes += minutesByCondition.get(claim.condition_hash ?? '') ?? 0;
        const at = Date.parse(claim.occurred_at ?? '');
        if (Number.isFinite(at) && now - at > STALL_MS) entry.stalled += 1;
      }
      rows.set(key, entry);
    }
    return [...rows.values()].sort((a, b) => b.minutes - a.minutes || b.held - a.held);
  }, [claims.data, coverage.data, minutesByCondition]);

  /** 활동 흐름 — «시간 축». 지금 도는 것이 먼저, 그 아래로 방금 끝난 것.
   *
   *  ⚠️ 설비 현황과 겹치지 않는다. 챔버 카드는 「어디서 무엇을」(공간)을 말하고
   *  이 목록은 「언제 무엇이」(시간)를 말한다. 그리고 «완료»는 이 화면에서
   *  여기서만 시간 순으로 보인다 — 링도 추이도 「얼마나」만 말하지 「방금」은
   *  말하지 않는다.
   *
   *  ⚠️ 「측정 중」은 배정됐고 아직 측정 결과가 없는 claim 이다. 챔버의
   *  `is_running` 과 다른 사실이라 섞지 않는다 — 챔버가 돌고 있어도 그것이
   *  누구의 어느 조건인지는 claim 이 말한다.
   *
   *  ┌─ 🔴 실시간이 되면 «이 목록이 먼저 깨진다» ──────────────────────────┐
   *  │ 이 레인에는 이미 라이브 채널이 있다(`api/chamber-progress-stream.ts` ·│
   *  │ `api/session-events.ts`). 그러므로 이 목록이 폴링에서 스트림으로     │
   *  │ 바뀌는 것은 «언제»의 문제이지 «가능한가»의 문제가 아니다.            │
   *  │                                                                     │
   *  │ 그때 생기는 문제는 데이터가 아니라 «읽는 중의 재정렬»이다:           │
   *  │  ① 새 사건이 맨 위에 꽂히면 읽던 행이 아래로 밀린다. 지금은 호버로   │
   *  │     세부를 여는 구조라, 목록이 움직이면 «열어 둔 팝업이 다른 행의    │
   *  │     것»이 된다 — 화면이 거짓을 말하는 상태다.                        │
   *  │  ② 「…외 N건」의 N 이 쉬지 않고 바뀌면 그 줄은 정보가 아니라 소음이  │
   *  │     된다.                                                            │
   *  │                                                                     │
   *  │ 그래서 실시간으로 갈 때 필요한 것은 스트림이 아니라 «멈춤»이다 —     │
   *  │ 새 사건은 「N건 새로 들어옴」 배지로 쌓아 두고, 사람이 누를 때 목록을│
   *  │ 갈아 끼운다. 마우스가 목록 위에 있는 동안은 자동 갱신하지 않는다.    │
   *  │ ⚠️ 이슈 트래커도 같다. 그쪽은 세 화면이 공유하므로 더 조심해야 한다. │
   *  └─────────────────────────────────────────────────────────────────────┘ */
  const activity = useMemo(() => {
    const rows = coverage.data?.items ?? [];
    const measured = new Set(rows.map((row) => row.condition_hash ?? ''));
    const runningAll = (claims.data ?? [])
      .filter((claim) => !measured.has(claim.condition_hash ?? ''))
      .sort((a, b) => (b.occurred_at ?? '').localeCompare(a.occurred_at ?? ''));
    const running = runningAll
      .slice(0, 6)
      .map((claim) => ({
        id: `run-${claim.claim_id}`,
        kind: 'running' as const,
        mode: claim.technology ?? '—',
        who: claim.operator ?? '—',
        at: claim.occurred_at ?? '',
      }));
    const doneAll = [...rows]
      .filter((row) => (row.latest_measured_at ?? '') !== '')
      .sort((a, b) => (b.latest_measured_at ?? '').localeCompare(a.latest_measured_at ?? ''));
    const done = doneAll
      .slice(0, 8)
      .map((row) => ({
        id: `done-${row.condition_hash ?? ''}`,
        kind: 'done' as const,
        mode: row.technology ?? '—',
        who: row.latest_operator ?? '—',
        at: row.latest_measured_at ?? '',
      }));
    /* ⚠️ 잘린 사실을 «말한다». 목록은 14줄에서 멈추는데, 지금까지 이 저장소의
       다른 목록들은 그 사실을 화면에 적지 않았다 — 15번째 사건은 아무 흔적 없이
       사라지고, 보는 사람은 그것이 없는 것인지 안 보이는 것인지 알 수 없다.
       「없다」와 「내가 못 봤다」는 출력이 같아지면 안 된다. */
    const shownRunning = Math.min(runningAll.length, 6);
    const shownDone = Math.min(doneAll.length, 8);
    return {
      rows: [...running, ...done],
      hidden: runningAll.length - shownRunning + (doneAll.length - shownDone),
    };
  }, [claims.data, coverage.data]);

  /** 모드별로 묶은 이슈. 「802.11n HT20」이 두 줄, 「802.11b」가 두 줄 나오면
   *  목록이 길어지기만 하고 「무엇이 문제인가」는 오히려 흐려진다. 사람이 보는
   *  단위는 case 가 아니라 «모드»이고, 그 모드 안에 몇 건인지가 다음 정보다.
   *
   *  ⚠️ 개별 case 를 못 보게 된 것은 아니다 — 이 묶음을 펼쳐 case 를 보는 것은
   *  다음 단계이고, 그때 필요한 것은 조건별 attempts 계약이다(이미 있다:
   *  `/conditions/{hash}/attempts`). 지금은 «묶음»까지가 화면의 답이다. */
  /** 모드 → 「배정됐나 · 이슈 있나」.
   *
   *  이 둘은 «다른 사실»이라 한 칸에 섞지 않는다. 배정은 「내가(누군가) 할 일」이고
   *  이슈는 「다시 해야 할 일」이다. 그래서 표식이 반반으로 갈린다 — 왼쪽이 배정,
   *  오른쪽이 이슈. 이슈가 없으면 왼쪽 색 하나로 채운다.
   *
   *  ⚠️ 색을 하나로 합치면(예: 배정+이슈 = 더 진한 빨강) 두 사실 중 하나가
   *  사라진다. 「배정됐지만 멀쩡한 것」과 「배정 안 됐는데 터진 것」은 다음 행동이
   *  전혀 다르고, 그 구분이 이 표식의 전부다. */
  const modeMarks = useMemo(() => {
    const marks = new Map<string, { holders: string[]; mine: boolean; issues: number }>();
    for (const claim of claims.data ?? []) {
      const mode = claim.technology ?? '—';
      const cur = marks.get(mode) ?? { holders: [], mine: false, issues: 0 };
      const who = claim.operator ?? '—';
      if (!cur.holders.includes(who)) cur.holders.push(who);
      // 「내 것」은 사번이 같을 때만이다. 신원을 모르면(me === null) 아무것도
      // 내 것이 아니고, 화면은 그 사실을 한 줄로 말한다 — 전부 내 것처럼
      // 보이는 쪽이 훨씬 비싼 오해다.
      if (me !== null && who === me) cur.mine = true;
      marks.set(mode, cur);
    }
    for (const row of coverage.data?.items ?? []) {
      if (!FAILING_VERDICTS.has((row.latest_verdict ?? '').trim().toLowerCase())) continue;
      const mode = row.technology ?? '—';
      const cur = marks.get(mode) ?? { holders: [], mine: false, issues: 0 };
      cur.issues += 1;
      marks.set(mode, cur);
    }
    return marks;
  }, [claims.data, coverage.data, me]);

  /** 내가 측정한 건수. 신원을 모르면 `null` — 「0건」이 아니다. 그 둘은 다르고,
   *  0 을 보여 주면 아무것도 안 한 사람처럼 읽힌다. */
  const mineDone = useMemo(() => {
    if (me === null) return null;
    return (coverage.data?.items ?? []).filter((row) => row.latest_operator === me).length;
  }, [coverage.data, me]);

  /** 세 칸이 각각 들여다볼 넷.
   *
   *  ⚠️ 「한 것」만 열리고 나머지는 안 열리면, 세 칸이 같은 모양인데 하나만
   *  반응하는 상태가 된다 — 그건 기능이 아니라 고장으로 읽힌다. 같은 자리에
   *  같은 동작을 준다.
   *
   *  ⚠️ 내 것을 «위로» 올린다. 목록이 넷뿐이라 정렬 하나가 곧 「무엇을 보여줄
   *  것인가」이고, 이 화면에서 그 답은 내 일이다. 신원을 모르면 정렬이 원래
   *  순서로 남는다 — 추측해서 올리지 않는다. */
  const byMineFirst = <T,>(rows: readonly T[], who: (row: T) => string): T[] => {
    const copy = [...rows];
    if (me === null) return copy;
    return copy.sort((a, b) => Number(who(b) === me) - Number(who(a) === me));
  };

  /** 할 것 — 지금 «배정된» 조건 중 아직 측정되지 않은 것. */
  const todoPeek = useMemo(() => {
    const measured = new Set(
      (coverage.data?.items ?? []).map((row) => row.condition_hash ?? ''),
    );
    const open = (claims.data ?? []).filter(
      (row) => !measured.has(row.condition_hash ?? ''),
    );
    return byMineFirst(open, (row) => row.operator ?? '').slice(0, 4);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claims.data, coverage.data, me]);

  /** 이슈 — 부적합 판정이 난 것, 최근 순. */
  const issuePeek = useMemo(() => {
    const failed = (coverage.data?.items ?? []).filter((row) =>
      FAILING_VERDICTS.has((row.latest_verdict ?? '').trim().toLowerCase()),
    );
    failed.sort((a, b) =>
      (b.latest_measured_at ?? '').localeCompare(a.latest_measured_at ?? ''),
    );
    return byMineFirst(failed, (row) => row.latest_operator ?? '').slice(0, 4);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverage.data, me]);

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
    queries: activeProjects.map((project) => ({
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

    /* 모드별 실측 건수. 계획 읽기는 시간만 정확히 알고 건수는 커버리지가 안다 —
       각자 아는 것을 각자에게 묻는다. */
    const measuredByMode = new Map<string, number>();
    for (const row of coverage.data?.items ?? []) {
      const mode = row.technology ?? '';
      measuredByMode.set(mode, (measuredByMode.get(mode) ?? 0) + 1);
    }

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
        // ⚠️ `done` 은 더 이상 «시간 백분율 × 조건 수» 가 아니다.
        // 그 곱은 뜻이 없었다 — 55.8% 의 시간을 393건에 곱하면 219 가 나오는데
        // 실제로 끝난 것은 238건이었다(실측 2026-09-09). 건수는 건수로 센다.
        const one: Tally = {
          total,
          done: measuredByMode.get(bucket.progress_area ?? '') ?? 0,
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

    /** ⚠️ 진행률은 «시간»이다, 건수가 아니다.
     *
     *  100건 중 80건을 끝냈어도 남은 20건이 방사(RSE 90분 · RBE 75분)면 시간으로는
     *  절반쯤이다. 그리고 실제 시험이 그렇게 돈다 — 짧고 쉬운 것을 앞에서 걷어내고
     *  긴 것이 뒤에 남는다. 그래서 건수 진행률은 «끝날수록 과대평가»되고, 그 거짓은
     *  마감 앞에서 발견된다. 계획이 조건마다 표준시간을 들고 있으므로 시간으로 셀
     *  수 있고, 셀 수 있으면 그쪽이 맞다.
     *
     *  건수는 버리지 않는다 — 아래 `done`/`total` 로 남아 «보조»로 표시된다.
     *  둘이 어긋나는 것(61% vs 56%)이 그 자체로 「짧은 것부터 했구나」라는 정보다. */
    const ratioOf = (v: Tally): number => (v.planned === 0 ? 0 : v.spent / v.planned);
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
  }, [progressQueries, activeProjects, activeModelId, coverage.data, t]);

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
  /** 전체 계획 «분». 추이의 분모이자 링 범례의 남은 시간이다 — 한 값을 두 이름
   *  으로 만들면 언젠가 한쪽만 바뀐다. */
  const plannedMinutesTotal = useMemo(
    () => categories.reduce((n, c) => n + c.planned, 0),
    [categories],
  );

  /** 부하를 «면적»으로. 목록이 답하지 못하던 질문에 답한다 — 「이 사람이 전체
   *  중 얼마나」. 목록은 5.6h / 4.7h / 2.2h 를 «읽어서 비교»하게 하지만, 지도는
   *  그 비교를 눈이 대신한다.
   *
   *  🔴 «미배정»을 칸으로 넣는다. 사람들 합만으로 100% 를 만들면 「아직 아무도
   *  잡지 않은 일」이 그림에서 사라지고, 그러면 지도는 늘 「일이 고르게 나뉘어
   *  있다」고 말하게 된다 — 실제로는 절반이 미배정일 수 있는데도. 분모는 사람의
   *  합이 아니라 «남은 일 전체»다.
   *
   *  ⚠️ 남은 일 = 계획 시간 − 소진 시간. 이것도 시간 베이스다(§ratioOf). */
  const loadShare = useMemo(() => {
    const assigned = operatorLoad.reduce((acc, row) => acc + row.minutes, 0);
    const remaining = Math.max(0, plannedMinutesTotal - spentTotal);
    const cells: ShareCell[] = operatorLoad.map((row) => ({
      id: row.who,
      label: row.who,
      value: row.minutes,
      detail: hours(row.minutes),
      tone: row.stalled > 0 ? ('stalled' as const) : ('normal' as const),
      mine: row.who === me,
      hue: hueOf(row.who),
      title: `${row.who} · ${hours(row.minutes)} · ${row.held}`,
    }));
    return {
      cells,
      assigned,
      remaining,
      ratio: remaining === 0 ? 0 : Math.min(1, assigned / remaining),
    };
  }, [operatorLoad, plannedMinutesTotal, spentTotal, me]);

  /** 전체 진행률 — «시간». 대분류 카드와 같은 축이라 링과 카드가 어긋나지 않는다. */
  const programmeOverall = useMemo(() => {
    const planned = categories.reduce((n, c) => n + c.planned, 0);
    if (planned === 0) return null;
    return categories.reduce((n, c) => n + c.spent, 0) / planned;
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
    if (rows.length === 0 || plannedMinutesTotal === 0) return null;

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

    /* 조건 → 표준시간. ⚠️ 여기서 «다시 만들지» 않는다 — 부하 계산과 같은
       지도를 써야 한 화면의 두 숫자가 같은 표준시간을 근거로 삼는다. */
    const minutesOf = minutesByCondition;

    const perDay = new Map<string, Map<string, number>>();
    for (const row of rows) {
      const day = (row.latest_measured_at ?? '').slice(0, 10);
      if (day === '') continue;
      const band = bandOfMode.get(row.technology ?? '') ?? '—';
      const bucket = perDay.get(day) ?? new Map<string, number>();
      // 건수가 아니라 «분»을 더한다.
      bucket.set(band, (bucket.get(band) ?? 0) + (minutesOf.get(row.condition_hash ?? '') ?? 0));
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
        // 전체 계획 «시간» 대비 기여분. 셋을 더하면 그 날의 전체 진행률이 된다.
        values: bands.map((band) => (running.get(band) ?? 0) / plannedMinutesTotal),
      };
    });

    return { bands, days, points };
  }, [
    coverage.data,
    minutesByCondition,
    progressQueries,
    activeProjects,
    activeModelId,
    plannedMinutesTotal,
  ]);

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

  /** 이 숫자가 «어느 계획 판»에 대한 것인가.
   *
   *  계획은 개정될 때 새 `plan_id` 로 다시 발행되고 옛 판은 테이블에 남는다.
   *  진행률·항목 읽기 둘 다 최신 판만 보므로 화면의 모든 숫자가 «한 판»에 대한
   *  것인데, 그 판이 무엇인지는 어디에도 적혀 있지 않았다. 그래서 진행률이
   *  하룻밤에 떨어졌을 때 「일이 되돌려졌나」와 「계획이 늘었나」가 구별되지 않는다.
   *
   *  ⚠️ 여기서 말하는 것은 «어느 판인가»까지다. 「지난 판 대비 몇 건 추가·삭제」는
   *  이전 판을 봐야 하는데 이 읽기는 최신 판만 준다(그것이 옳다 — 두 판이 섞이면
   *  조건이 겹쳐 나온다). 판 이력을 주는 읽기가 생기면 그때 이 줄에 붙는다.
   *
   *  ⚠️ 그때 «추가와 삭제를 합치지 않는다». 순증 +1 로 접으면 삭제가 사라지는데,
   *  지워진 조건에 이미 측정이 있었다면 그 시간이 진행률에서 빠진다 — 늘어난 것은
   *  할 일이 는 것이고, 줄어든 것은 「한 것이 없던 일이 된」 것이다. */
  const planEdition = useMemo(() => {
    const first = (planConditions.data ?? [])[0];
    if (first === undefined) return null;
    const id = first.plan_id ?? '';
    if (id === '') return null;
    return { id, publishedAt: (first.plan_published_at ?? '').slice(0, 10) };
  }, [planConditions.data]);


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
        {/* ── 오늘의 세 숫자 ─────────────────────────────────────────────
            시험할 사람이 화면을 열자마자 묻는 것은 셋뿐이다: 얼마나 했나 ·
            뭐가 남았나 · 뭐가 터졌나. 그 셋을 한 줄로 먼저 답하고, 자세한 것은
            아래 계열 열이 답한다.

            ⚠️ 낮게 유지한다. 이 띠는 «시작점»이지 목적지가 아니라, 키가 커지면
            정작 일하는 목록을 화면 밖으로 밀어낸다. 큰 숫자를 세우지 않고 값과
            라벨을 한 줄에 눕히는 것이 그 결정이다.

            ⚠️ 시간이 앞, 건수가 뒤 — 이 콘솔의 진행률은 시간이다(§ratioOf).
            ⚠️ PM 화면에는 없다. 그쪽의 첫 줄은 링과 추이가 답한다. */}
        {showsToday && programmeOverall !== null && (
          <div className="today-strip console-grid__full" data-testid="console-today">
            {/* 「한 것」 칸만 «들여다볼» 수 있다. 숫자는 얼마나 했는지를 말하고,
                올려 보면 «무엇을» 했는지 넷이 나온다 — 아침에 화면을 열었을 때
                「어제 어디까지 했더라」에 답하는 자리다.
                ⚠️ 보기만 한다. 누르는 것도, 여기서 이어 하는 것도 아니다 —
                그건 작업 화면의 일이고, 이 칸은 «기억을 되살리는» 용도다. */}
            <div className="today-cell today-cell--peek">
              <span className="today-cell__label">{t('routes.home.today.done')}</span>
              <b className="today-cell__value">{hours(spentTotal)}</b>
              <span className="today-cell__aside">
                {t('routes.home.donutCases', { count: String(doneTotal) })}
                {mineDone !== null && (
                  <span className="today-cell__mine">
                    {' '}
                    · {t('routes.home.today.mineCount', { count: String(mineDone) })}
                  </span>
                )}
              </span>
              {me === null && (
                <span className="today-cell__unknown" title={t('routes.home.today.noIdentityHint')}>
                  {t('routes.home.today.noIdentity')}
                </span>
              )}
              {flow.length > 0 && (
                <div className="today-peek" role="note">
                  <p className="today-peek__head">{t('routes.home.today.recent')}</p>
                  <ul className="today-peek__list">
                    {byMineFirst(flow, (row) => row.latest_operator ?? '')
                      .slice(0, 4)
                      .map((row) => (
                      <li
                        key={row.condition_hash ?? ''}
                        data-mine={row.latest_operator === me ? 'true' : undefined}
                      >
                        <span className="today-peek__mode">{row.technology ?? '\u2014'}</span>
                        <span className="today-peek__who">{row.latest_operator ?? '\u2014'}</span>
                        <span className="today-peek__when">
                          {(row.latest_measured_at ?? '').slice(5, 16).replace('T', ' ')}
                        </span>
                      </li>
                      ))}
                  </ul>
                </div>
              )}
            </div>
            <div className="today-cell today-cell--peek">
              <span className="today-cell__label">{t('routes.home.today.todo')}</span>
              <b className="today-cell__value">
                {hours(Math.max(0, plannedMinutesTotal - spentTotal))}
              </b>
              <span className="today-cell__aside">
                {t('routes.home.donutCases', {
                  count: String(Math.max(0, plannedTotal - doneTotal)),
                })}
              </span>
              {todoPeek.length > 0 && (
                <div className="today-peek" role="note">
                  <p className="today-peek__head">{t('routes.home.today.assignedNow')}</p>
                  <ul className="today-peek__list">
                    {todoPeek.map((row) => (
                      <li
                        key={row.condition_hash ?? ''}
                        data-mine={row.operator === me ? 'true' : undefined}
                      >
                        <span className="today-peek__mode">{row.technology ?? '\u2014'}</span>
                        <span className="today-peek__who">{row.operator ?? '\u2014'}</span>
                        <span className="today-peek__when">
                          {(row.occurred_at ?? '').slice(5, 16).replace('T', ' ')}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            <div
              className="today-cell today-cell--peek"
              data-tone={nonconformities.length > 0 ? 'bad' : undefined}
            >
              <span className="today-cell__label">{t('routes.home.today.issues')}</span>
              <b className="today-cell__value">{String(nonconformities.length)}</b>
              <span className="today-cell__aside">{t('routes.home.today.issuesAside')}</span>
              {issuePeek.length > 0 && (
                <div className="today-peek" role="note">
                  <p className="today-peek__head">{t('routes.home.today.recentIssues')}</p>
                  <ul className="today-peek__list">
                    {issuePeek.map((row) => (
                      <li
                        key={row.condition_hash ?? ''}
                        data-mine={row.latest_operator === me ? 'true' : undefined}
                      >
                        <span className="today-peek__mode">{row.technology ?? '\u2014'}</span>
                        <span className="today-peek__who">{row.latest_operator ?? '\u2014'}</span>
                        <span className="today-peek__when">
                          {(row.latest_measured_at ?? '').slice(5, 16).replace('T', ' ')}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── fleet — 현장 범위 전용 ─────────────────────────────────────
            PM 화면에서 뺐다. 「구성 5개 중 1개 가용」은 사실이지만 PM 의 결정에
            들어가지 않는 사실이고, 화면의 첫 번째 띠는 그의 질문에 답해야 한다. */}
        {showsOperator && SHOWS_FIELD_PANELS && (
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
        {showsOperator && SHOWS_FIELD_PANELS && (
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
        {showsOperator && SHOWS_FIELD_PANELS && (
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
        {showsOperator && SHOWS_FIELD_PANELS && (
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
        {showsOperator && SHOWS_FIELD_PANELS && (
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
            data-busy={modelIsSwitching ? 'true' : undefined}
          >
            <div className="console-panel__head">
              <h2 className="console-panel__title" id="console-programme-heading">
                {t('routes.home.programmeHeading')}
              </h2>
              {/* 계획 판. 이 패널의 «모든» 숫자가 이 판에 대한 것이라는 사실을
                  숫자들보다 «먼저» 적는다. */}
              {planEdition !== null && (
                <span className="plan-edition" data-testid="plan-edition">
                  {planEdition.publishedAt === ''
                    ? t('routes.home.planEdition', { id: planEdition.id })
                    : t('routes.home.planEditionOn', {
                        id: planEdition.id,
                        at: planEdition.publishedAt,
                      })}
                </span>
              )}
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
                {/* ⚠️ 실무자 화면에서는 내린다. 시험할 사람이 이 화면에서
                    보고서를 뽑을 일이 없고, 그 자리는 모델 토글 옆이라 누르려던
                    것을 잘못 누르기 좋다. PM·관리자 화면에는 그대로 있다 —
                    「시험 진행률은 건드리지 않는다」(2026-09-09). */}
                {showsExport && (
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
                )}
              </div>
            </div>

            {showsExport && exportOpen && (
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
              {showsProgrammeSummary && (
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
                        primary: hours(spentTotal),
                        secondary: t('routes.home.donutCases', {
                          count: String(doneTotal),
                        }),
                      },
                      {
                        id: 'rest',
                        kind: 'rest',
                        label: t('routes.home.donutRest'),
                        primary: hours(Math.max(0, plannedMinutesTotal - spentTotal)),
                        secondary: t('routes.home.donutCases', {
                          count: String(Math.max(0, plannedTotal - doneTotal)),
                        }),
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
              </>
              )}
              {showsBandBreakdown && (
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
                      {/* ⚠️ 시간이 앞이다. 먼저 오는 숫자가 인용되는 숫자이고,
                          이 화면에서 진행률은 시간이다. 건수는 뒤에 조용히 서서
                          「그 시간이 몇 건에서 나왔나」를 보탠다. */}
                      <p className="band-card__caption">
                        {category.time}
                        <span className="band-card__aside"> · {category.detail}</span>
                      </p>
                    </section>
                  ))}
              </div>
              )}
              </>
            )}

            {/* 대분류 3열 × 계열 세로 누적.
                위의 카드 셋과 «같은 가로 순서»로 열이 서므로, 카드에서 눈이
                멈춘 자리 바로 아래에 그 분류의 속이 있다. 계열이 열 안에서
                세로로 쌓이면 「U-NII 가 어디까지」와 「그 안의 U-NII-3 은」이
                한 시선에 같이 들어온다 — 층을 옆으로 펼치면 그 관계가 끊긴다. */}
            {showsBandBreakdown && categories.length > 0 && (
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
                        {/* ⚠️ 펼치지 않고 «간다». 한 계열이 70행을 넘으면 이 열
                            하나만 길어져 옆 두 대분류와 높이가 어긋나고, 3열로
                            세운 이유(같은 높이에서 비교)가 사라진다.
                            ⚠️ 실무자 화면에서만 링크다 — PM 의 질문은 「어느 계열이
                            늦나」이고, 거기서 조건으로 내려가는 길을 열면 보고
                            자리에서 길을 잃는다. */}
                        {showsOperator ? (
                          <Link
                            className="gauge gauge--link"
                            style={{ '--fill': `${family.ratio * 100}%` } as CSSProperties}
                            to={`${ROUTE_PATHS.testItems}?model=${encodeURIComponent(
                              activeModelId ?? '',
                            )}&family=${encodeURIComponent(family.label)}`}
                          >
                            <span className="gauge__ticks" aria-hidden="true" />
                            <span className="gauge__name">{family.label}</span>
                            <span className="gauge__pct">{percent(family.ratio)}</span>
                            <span className="gauge__go" aria-hidden="true" />
                          </Link>
                        ) : (
                          <div
                            className="gauge"
                            style={{ '--fill': `${family.ratio * 100}%` } as CSSProperties}
                          >
                            <span className="gauge__ticks" aria-hidden="true" />
                            <span className="gauge__name">{family.label}</span>
                            <span className="gauge__pct">{percent(family.ratio)}</span>
                          </div>
                        )}

                        <ul className="mode-list">
                          {family.modes.map((mode) => (
                            <li
                              className="mode-list__row"
                              key={mode.id}
                              data-linked={linkedMode === mode.label}
                              data-assigned={
                                !showsOperator ||
                                (modeMarks.get(mode.label)?.holders.length ?? 0) === 0
                                  ? undefined
                                  : modeMarks.get(mode.label)?.mine === true
                                    ? 'mine'
                                    // ⚠️ 신원을 모르면 «남의 것»이 아니라 «주인
                                    // 미상»이다. 둘은 다르다 — 「남의 것」은 내가
                                    // 손대면 안 된다는 뜻이고, 「미상」은 아직
                                    // 아무 말도 못 한다는 뜻이다. 사번이 없을 때
                                    // 앞엣것을 주장하면 화면이 거짓을 말한다.
                                    : me === null
                                      ? 'unknown'
                                      : 'other'
                              }
                              data-issue={
                                showsOperator && (modeMarks.get(mode.label)?.issues ?? 0) > 0
                                  ? 'true'
                                  : undefined
                              }
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
                                title={
                                  showsOperator && modeMarks.has(mode.label)
                                    ? t('routes.home.markTitle', {
                                        who:
                                          (modeMarks.get(mode.label)?.holders ?? [])
                                            .map((w) => (w === me ? t('routes.home.mine', { who: w }) : w))
                                            .join(', ') || '\u2014',
                                        issues: String(modeMarks.get(mode.label)?.issues ?? 0),
                                      })
                                    : undefined
                                }
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
            data-busy={modelIsSwitching ? 'true' : undefined}
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
                        {/* 🔴 두 줄 → 한 줄 (2026-09-09). 「모드 · 배지 · 시각 ·
                            담당」이 두 줄을 쓰면 12건이 패널을 가득 채우고,
                            정작 이 목록의 값은 «얼마나 많은가»를 한눈에 보는
                            것이다. 한 줄이면 같은 높이에 두 배가 들어간다.

                            판정은 글자 배지가 아니라 ✕ 하나다 — 이 목록에
                            들어온 것은 전부 부적합이라 「Fail」을 12번 쓰는 것은
                            같은 말을 12번 하는 것이다. 아이콘은 그 자리를
                            1/4 로 줄이고, 색이 이미 같은 말을 한다.
                            ⚠️ 그래도 «글자»가 필요하다 — 색과 모양만으로 말하면
                            색각 이상에서 판정이 사라진다. `aria-label` 로 남긴다. */}
                        <span
                          className="issue-row__x"
                          role="img"
                          aria-label={kind !== null ? t(`ui.statusBadge.${kind}`) : ''}
                        >
                          <svg viewBox="0 0 12 12" aria-hidden="true" fill="none"
                               stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                            <path d="M3 3l6 6M9 3l-6 6" />
                          </svg>
                        </span>
                        <span className="issue-row__tech">{row.mode}</span>
                        {row.count > 1 && (
                          <span className="issue-row__count">{row.count}</span>
                        )}
                        {/* 🔴 순서를 뒤집었다. 이 목록은 「시각 · 담당」이었고
                            활동 흐름은 「담당 · 시각」이라, 두 목록이 나란히 서면
                            눈이 오른쪽 끝에서 서로 다른 것을 만난다. 시각을 «맨
                            오른쪽»으로 통일한다 — 둘 다 시간순 정렬이라 그 값이
                            세로로 비교되는 열이고, tabular-nums 가 그 열을 세운다.
                            ⚠️ 이 변경은 세 화면 전부에 걸린다. 같은 부품이 화면
                            마다 다르게 읽히면 안 되므로 그것이 옳다. */}
                        {/* ⚠️ 한 문자열이 아니라 «두 조각»이다. 총괄에서 이
                            칸이 좁아지면 담당만 내리고 시각은 남겨야 하는데,
                            한 문자열이면 CSS 가 그 안을 자를 수 없다. 조각을
                            나눠 두면 「무엇을 먼저 버리나」가 스타일의 결정이
                            되고, 화면마다 다른 판단을 줄 수 있다. */}
                        <span className="issue-row__meta issue-row__who">
                          {t('routes.home.issueWho', {
                            attempt: String(row.attempt),
                            operator: row.operator,
                          })}
                        </span>
                        <span className="issue-row__meta issue-row__when">
                          {stampOf(row.latestAt)}
                        </span>
                      </button>
                      {/* 반 칸으로 줄면 행에서 「담당 · 시각」이 사라진다(CSS).
                          사라진 것을 «되찾는» 자리다 — 목록은 「무엇이 몇 건」까지만
                          말하고, 올려 보면 그 모드의 최근 것이 나온다.
                          ⚠️ 총괄에서만 렌더한다. 다른 두 화면은 패널이 넓어 행이
                          이미 전부 말하고, 거기 팝업을 더하면 읽던 것을 가린다. */}
                      {showsActivity && (
                        <div className="row-peek" role="note">
                          <p className="row-peek__head">{row.mode}</p>
                          <ul className="row-peek__list">
                            <li>
                              <span>{t('routes.home.peekVerdict')}</span>
                              <b>{row.verdict || '—'}</b>
                            </li>
                            <li>
                              <span>{t('routes.home.peekCount')}</span>
                              <b>{t('routes.home.issuesMeta', { count: String(row.count) })}</b>
                            </li>
                            <li>
                              <span>{t('routes.home.peekWho')}</span>
                              <b>
                                {t('routes.home.issueWho', {
                                  attempt: String(row.attempt),
                                  operator: row.operator,
                                })}
                              </b>
                            </li>
                            <li>
                              <span>{t('routes.home.peekWhen')}</span>
                              <b>{stampOf(row.latestAt)}</b>
                            </li>
                          </ul>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        )}

        {/* ── 활동 흐름 ───────────────────────────────────────────────────
            이슈 트래커와 «같은 열»에 선다. 오른쪽 열은 이 화면에서 「무슨 일이
            일어나는가」를 맡고, 왼쪽 열은 「얼마나 · 누가」를 맡는다.

            ⚠️ 챔버 카드와 겹치지 않는다 — 카드는 「어디서 무엇을」(공간),
            이 목록은 「언제 무엇이」(시간)다. 그리고 «완료»는 이 화면에서 여기
            에서만 시간 순으로 보인다: 링도 추이도 「얼마나」만 말하지 「방금」은
            말하지 않는다. */}
        {showsActivity && (
          <Card
            as="section"
            className="console-panel console-grid__third"
            aria-labelledby="console-activity-heading"
            data-busy={modelIsSwitching ? 'true' : undefined}
          >
            <div className="console-panel__head">
              <h2 className="console-panel__title" id="console-activity-heading">
                {t('routes.home.activityHeading')}
              </h2>
              <span className="console-panel__meta">
                {t('routes.home.activityMeta', {
                  count: String(activity.rows.length + activity.hidden),
                })}
              </span>
            </div>
            {activity.rows.length === 0 ? (
              <EmptyState
                testId="console-activity-empty"
                title={t('routes.home.activityEmptyTitle')}
                description={t('routes.home.activityEmptyBody')}
              />
            ) : (
              <ul className="activity-list" data-testid="console-activity">
                {activity.rows.map((row) => (
                  <li className="activity-row" key={row.id} data-kind={row.kind}>
                    {/* ⚠️ 점 하나로 상태를 말하되 «글자»를 버리지 않는다. 색과
                        모양만으로 말하면 색각 이상에서 구분이 사라진다 —
                        이슈 목록의 ✕ 와 같은 규칙이다. */}
                    <span
                      className="activity-row__dot"
                      role="img"
                      aria-label={t(`routes.home.activity.${row.kind}`)}
                    />
                    <span className="activity-row__mode">{row.mode}</span>
                    <span className="activity-row__meta activity-row__who">{row.who}</span>
                    <span className="activity-row__meta activity-row__when">
                      {stampOf(row.at)}
                    </span>
                    <div className="row-peek" role="note">
                      <p className="row-peek__head">{row.mode}</p>
                      <ul className="row-peek__list">
                        <li>
                          <span>{t('routes.home.peekState')}</span>
                          <b>{t(`routes.home.activity.${row.kind}`)}</b>
                        </li>
                        <li>
                          <span>{t('routes.home.peekWho')}</span>
                          <b>{row.who}</b>
                        </li>
                        <li>
                          <span>{t('routes.home.peekWhen')}</span>
                          <b>{stampOf(row.at)}</b>
                        </li>
                      </ul>
                    </div>
                  </li>
                ))}
                {/* 잘린 만큼을 «세어서» 적는다. 「더 있다」가 아니라 「몇 건 더」다 —
                    3건과 300건은 다음 행동이 다르다. */}
                {activity.hidden > 0 && (
                  <li className="activity-row activity-row--more" data-testid="activity-more">
                    {t('routes.home.activityMore', { count: String(activity.hidden) })}
                  </li>
                )}
              </ul>
            )}
          </Card>
        )}


        {/* ── 사람별 부하 ─────────────────────────────────────────────────
            챔버 옆에 사람이 서는 이유: 총괄이 실제로 움직일 수 있는 것이 사람
            하나다. 설비는 「못 늘리는 것」이고 이 목록은 「옮길 수 있는 것」이라,
            둘이 같은 문단에 있어야 「어느 칸이 비었나」와 「누가 손이 비나」가
            한 시선에 들어온다.

            ⚠️ 시간이 앞, 건수가 뒤 — 이 콘솔의 진행률은 시간이다(§ratioOf).
            정렬도 시간 순이다. 「5건 들었지만 2h」와 「2건 들었지만 30h」 중
            총괄이 손대야 하는 것은 뒤쪽이고, 건수로 정렬하면 그것이 아래로 간다. */}
        {showsLoad && (
          <Card
            as="section"
            className="console-panel console-grid__under"
            aria-labelledby="console-load-heading"
            data-busy={modelIsSwitching ? 'true' : undefined}
          >
            <div className="console-panel__head">
              <h2 className="console-panel__title" id="console-load-heading">
                {t('routes.home.loadHeading')}
              </h2>
              <span className="console-panel__meta">
                {t('routes.home.loadMeta', { count: String(operatorLoad.length) })}
              </span>
            </div>
            {/* ⚠️ 목록이 아니라 «지도»다. 15인이 되면 목록은 15줄이고, 15줄은
                「누가 제일 많나」를 «읽어서» 알아내야 한다. 면적은 그 비교를 눈이
                대신한다 — 이 화면이 요구하는 것은 정확한 숫자가 아니라 한 번에
                보이는 «비중»이다. 정확한 값은 칸에 올리면 나온다. */}
            {loadShare.cells.length === 0 ? (
              <EmptyState
                testId="console-load-empty"
                title={t('routes.home.loadEmptyTitle')}
                description={t('routes.home.loadEmptyBody')}
              />
            ) : (
              <>
                {/* 🔴 지도의 분모는 «배정된 일»이다. 그 사실을 지도보다 «먼저»
                    적는다 — 이 줄이 없으면 아래 그림은 남은 일이 전부 나뉘어
                    있는 것처럼 보인다. 실측으로 미배정이 90% 였다. */}
                <div className="share-split">
                  <div
                    className="share-split__bar"
                    role="meter"
                    aria-valuemin={0}
                    aria-valuemax={1}
                    aria-valuenow={loadShare.ratio}
                    aria-label={t('routes.home.loadAssignedLabel')}
                  >
                    <span
                      className="share-split__fill"
                      style={{ width: `${loadShare.ratio * 100}%` }}
                    />
                  </div>
                  <span className="share-split__text">
                    {t('routes.home.loadAssigned', {
                      pct: String(Math.round(loadShare.ratio * 100)),
                      assigned: hours(loadShare.assigned),
                      remaining: hours(loadShare.remaining),
                    })}
                  </span>
                </div>
                <ShareTreemap cells={loadShare.cells} testId="console-load" />
              </>
            )}
          </Card>
        )}

        {/* 사람 부하는 이제 «전폭»이다. 위 두 목록이 오른쪽 두 칸을 나눠 쓰므로
            부하가 왼쪽 열에만 서면 그 오른쪽이 통째로 빈다. 그리고 15인 명단은
            폭이 넓을수록 낫다 — 아래 CSS 가 여러 단으로 흘린다. */
        }
        {/* 🔴 순서: 사람 → 활동 → 설비.
            처음엔 설비를 먼저 놓았는데(공간 → 인력 → 시간), 총괄이 실제로 묻는
            순서가 그것이 아니다. 관리자가 화면을 열고 먼저 보는 것은 「누가
            고생하고 있나」와 「지금 뭐가 도나」이고, 설비는 그 둘을 설명하는
            «배경»이다. 그리고 설비는 이 화면에서 유일하게 «움직일 수 없는»
            것이라 — 챔버는 늘릴 수 없다 — 행동으로 이어지지 않는 정보다.
            행동으로 이어지는 것이 위, 배경이 아래. */
        }
        {/* ── the fleet, as blocks — 현장 범위에서만 ─────────────────────
            A PM does not act on "which bay is free"; showing it spends the
            screen's most valuable band on something their decisions never
            read. Scope is what makes three homes three homes. */}
        {showsFleet && (
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
                  statusLabel={chamberStatusLabel(t, chamber.status)}
                  {...(chamber.status === 'in_use'
                    ? {
                        operatorLabel: (() => {
                          const who = operatorBySession.get((chamber.session_id ?? '').trim());
                          return who === undefined
                            ? t('routes.home.chamberUserUnknown')
                            : t('routes.home.chamberUser', { who });
                        })(),
                      }
                    : {})}
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
