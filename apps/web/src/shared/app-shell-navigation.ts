import { ROUTE_PATHS } from './route-links';

import type { ProjectWorkflowActionId } from './project-workflow';
import type { NavIconName } from '@/ui';

export interface AppShellNavItem {
  readonly to: string;
  readonly labelKey: string;
  readonly end: boolean;
  /** ⚠️ 아이콘은 «데이터»다. 레이아웃 안에 경로→글리프 표를 두면 항목을 추가한
   *  사람이 그 표를 모르고 지나쳐 아이콘 없는 줄이 하나 생기고, 그건 아무
   *  검사도 잡지 못한다. 여기에 두면 타입이 강제한다. */
  readonly icon: NavIconName;
}

export interface AppShellNavGroup {
  readonly labelKey: string;
  readonly items: readonly AppShellNavItem[];
}

export const APP_SHELL_NAV_GROUPS: readonly AppShellNavGroup[] = [
  {
    labelKey: 'routes.layout.navGroups.home',
    items: [
      { to: '/', labelKey: 'routes.layout.nav.overview', end: true, icon: 'gauge' },
      { to: ROUTE_PATHS.overviewProgramme, labelKey: 'routes.layout.nav.overviewProgramme', end: false, icon: 'trend' },
      { to: ROUTE_PATHS.overviewCombined, labelKey: 'routes.layout.nav.overviewCombined', end: false, icon: 'grid' },
      { to: ROUTE_PATHS.systemDashboard, labelKey: 'routes.layout.nav.systemDashboard', end: false, icon: 'pulse' },
    ],
  },
  /* ── 시험하기 — «순서»대로 세 개 ────────────────────────────────────────
     계획을 세우고 → 배정받은 것을 하고 → 필요하면 원격으로 한다.

     ⚠️ 순서대로 «배열»하지만 마법사가 아니다. 좌측 탭은 «장소» 목록이지
     «단계» 목록이 아니고, 사람은 매일 중간부터 다시 들어온다 — 어제 하던
     측정을 이어 하려는데 「1단계 계획부터」가 위에서 매번 길을 막으면 안 된다.
     그래서 번호를 붙이지 않고, 각 항목은 혼자 서야 한다.

     🔴 2026-09-09 재편. 전에는 다섯이 나란히 있었는데 그중 하나(테스트 플랜)가
     나머지의 «전제»였고, 둘(시료·시험 구성)은 애초에 「시험을 한다」가 아니었다.
     다섯이 한 줄에 서면 매일 쓰는 것과 한 달에 한 번 쓰는 것이 같은 무게가 되고,
     그러면 순서가 있다는 사실 자체가 사라진다.

     ⚠️ 원격 측정이 마지막인 것은 그것이 «목적지»가 아니라 «수단»이기 때문이다.
     「원격으로 측정하자」가 아니라 「이 case 를 원격으로 하자」이므로 들어가는
     길은 내 작업 쪽이고, 이 탭은 직접 진입로로만 남는다. */
  {
    labelKey: 'routes.layout.navGroups.measure',
    items: [
      { to: ROUTE_PATHS.testPlans, labelKey: 'routes.layout.nav.testPlans', end: false, icon: 'clipboard' },
      { to: ROUTE_PATHS.myProjects, labelKey: 'routes.layout.nav.myProjects', end: false, icon: 'checklist' },
      { to: ROUTE_PATHS.control, labelKey: 'routes.layout.nav.control', end: false, icon: 'antenna' },
    ],
  },
  /* ── 샘플 정보 — 접수 담당의 자리 ──────────────────────────────────────
     시료는 시험원의 일이 아니라 «접수»의 일이고, 그 사람에게는 여기가 홈이다.
     ⚠️ 그리고 이 그룹은 나중에 커진다: 시료의 SW 바이너리 버전이 여기 들어와야
     한다. 측정은 그 펌웨어에서만 유효하고, 지금 `samples` 에는 그 칸이 없다
     (intent/sample-software-version). 독립시켜 두면 그때 자리가 이미 있다. */
  {
    labelKey: 'routes.layout.navGroups.samples',
    items: [
      { to: ROUTE_PATHS.inventory, labelKey: 'routes.layout.nav.inventory', end: false, icon: 'box' },
    ],
  },
  /* ── 설비·기준정보 ────────────────────────────────────────────────────
     챔버 등록·설정은 「시험을 한다」가 아니라 「설비를 관리한다」이다. 하루에 한
     번 볼까 말까인 것이 매일 쓰는 것들과 같은 줄에 있으면 매일 쓰는 것이 희석된다.
     그리고 실무자가 챔버를 등록할 일은 없다. */
  {
    labelKey: 'routes.layout.navGroups.facility',
    items: [
      { to: ROUTE_PATHS.chambers, labelKey: 'routes.layout.nav.chambers', end: false, icon: 'sliders' },
    ],
  },
  {
    labelKey: 'routes.layout.navGroups.results',
    items: [
      { to: ROUTE_PATHS.progress, labelKey: 'routes.layout.nav.progress', end: false, icon: 'bars' },
      // 「얼마나 했나」 다음은 「한 것이 맞나」다 — 세는 화면 바로 뒤에 둔다.
      { to: ROUTE_PATHS.dataReview, labelKey: 'routes.layout.nav.dataReview', end: false, icon: 'pulse' },
      { to: ROUTE_PATHS.projects, labelKey: 'routes.layout.nav.projects', end: false, icon: 'coverage' },
      { to: ROUTE_PATHS.jobs, labelKey: 'routes.layout.nav.jobs', end: false, icon: 'checklist' },
      { to: ROUTE_PATHS.sessions, labelKey: 'routes.layout.nav.sessions', end: false, icon: 'history' },
      // 플롯 보관 현황 — 성적서 발행 직전에 막히기 전에 미리 답을 보는 자리라
      // 성적서 항목들 **앞**에 둔다.
      {
        to: ROUTE_PATHS.artifactCustody,
        labelKey: 'routes.layout.nav.artifactCustody',
        end: false,
        icon: 'shield',
      },
      { to: ROUTE_PATHS.reports, labelKey: 'routes.layout.nav.reports', end: false, icon: 'file' },
      { to: ROUTE_PATHS.testReports, labelKey: 'routes.layout.nav.testReports', end: false, icon: 'books' },
      {
        to: ROUTE_PATHS.equipmentLists,
        labelKey: 'routes.layout.nav.equipmentLists',
        end: false,
        icon: 'instrument',
      },
    ],
  },
  {
    labelKey: 'routes.layout.navGroups.settings',
    items: [
      {
        to: ROUTE_PATHS.referenceData,
        labelKey: 'routes.layout.nav.referenceData',
        end: false,
        icon: 'database',
      },
      { to: ROUTE_PATHS.membership, labelKey: 'routes.layout.nav.membership', end: false, icon: 'users' },
      { to: ROUTE_PATHS.providers, labelKey: 'routes.layout.nav.providers', end: false, icon: 'plug' },
      { to: ROUTE_PATHS.diagnostics, labelKey: 'routes.layout.nav.diagnostics', end: false, icon: 'pulse' },
    ],
  },
] as const;

export const APP_SHELL_GRID_POC_ITEM = {
  to: '/grid-poc',
  labelKey: 'routes.layout.nav.gridPoc',
  end: false,
  // ⚠️ 이 항목은 `AppShellNavItem` 을 «선언하지 않고» 같은 자리에 섞인다(dev
  // 게이트). 그래서 타입이 아이콘을 강제하지 못하고, 실제로 여기만 빠뜨려
  // tsc 가 잡았다 — 그 강제가 살아 있다는 증거이기도 하다.
  icon: 'grid',
} as const satisfies AppShellNavItem;

export const SETTINGS_GROUP_LABEL_KEY = 'routes.layout.navGroups.settings';

export const SESSION_ONLY_NAV_TARGETS: ReadonlySet<string> = new Set([ROUTE_PATHS.control]);

export const PROJECT_SIDEBAR_ACTIONS: readonly {
  readonly id: ProjectWorkflowActionId;
  readonly labelKey: string;
}[] = [
  { id: 'workspace', labelKey: 'routes.layout.projectNav.workspace' },
  { id: 'fields', labelKey: 'routes.layout.projectNav.fields' },
  { id: 'inventory', labelKey: 'routes.layout.projectNav.inventory' },
  { id: 'testPlans', labelKey: 'routes.layout.projectNav.testPlans' },
  { id: 'chambers', labelKey: 'routes.layout.projectNav.chambers' },
  { id: 'progress', labelKey: 'routes.layout.projectNav.progress' },
  { id: 'reports', labelKey: 'routes.layout.projectNav.reports' },
  { id: 'testReports', labelKey: 'routes.layout.projectNav.testReports' },
  { id: 'membership', labelKey: 'routes.layout.projectNav.membership' },
] as const;
