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
  {
    labelKey: 'routes.layout.navGroups.measure',
    items: [
      { to: ROUTE_PATHS.myProjects, labelKey: 'routes.layout.nav.myProjects', end: false, icon: 'folder' },
      { to: ROUTE_PATHS.inventory, labelKey: 'routes.layout.nav.inventory', end: false, icon: 'box' },
      { to: ROUTE_PATHS.testPlans, labelKey: 'routes.layout.nav.testPlans', end: false, icon: 'clipboard' },
      { to: ROUTE_PATHS.chambers, labelKey: 'routes.layout.nav.chambers', end: false, icon: 'sliders' },
      { to: ROUTE_PATHS.control, labelKey: 'routes.layout.nav.control', end: false, icon: 'antenna' },
    ],
  },
  {
    labelKey: 'routes.layout.navGroups.results',
    items: [
      { to: ROUTE_PATHS.progress, labelKey: 'routes.layout.nav.progress', end: false, icon: 'bars' },
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
