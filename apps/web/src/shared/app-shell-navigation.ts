import { ROUTE_PATHS } from './route-links';

import type { ProjectWorkflowActionId } from './project-workflow';

export interface AppShellNavItem {
  readonly to: string;
  readonly labelKey: string;
  readonly end: boolean;
}

export interface AppShellNavGroup {
  readonly labelKey: string;
  readonly items: readonly AppShellNavItem[];
}

export const APP_SHELL_NAV_GROUPS: readonly AppShellNavGroup[] = [
  {
    labelKey: 'routes.layout.navGroups.home',
    items: [{ to: '/', labelKey: 'routes.layout.nav.overview', end: true }],
  },
  {
    labelKey: 'routes.layout.navGroups.measure',
    items: [
      { to: ROUTE_PATHS.myProjects, labelKey: 'routes.layout.nav.myProjects', end: false },
      { to: ROUTE_PATHS.inventory, labelKey: 'routes.layout.nav.inventory', end: false },
      { to: ROUTE_PATHS.testPlans, labelKey: 'routes.layout.nav.testPlans', end: false },
      { to: ROUTE_PATHS.chambers, labelKey: 'routes.layout.nav.chambers', end: false },
      { to: ROUTE_PATHS.control, labelKey: 'routes.layout.nav.control', end: false },
    ],
  },
  {
    labelKey: 'routes.layout.navGroups.results',
    items: [
      { to: ROUTE_PATHS.progress, labelKey: 'routes.layout.nav.progress', end: false },
      { to: ROUTE_PATHS.projects, labelKey: 'routes.layout.nav.projects', end: false },
      { to: ROUTE_PATHS.jobs, labelKey: 'routes.layout.nav.jobs', end: false },
      { to: ROUTE_PATHS.sessions, labelKey: 'routes.layout.nav.sessions', end: false },
      // 플롯 보관 현황 — 성적서 발행 직전에 막히기 전에 미리 답을 보는 자리라
      // 성적서 항목들 **앞**에 둔다.
      {
        to: ROUTE_PATHS.artifactCustody,
        labelKey: 'routes.layout.nav.artifactCustody',
        end: false,
      },
      { to: ROUTE_PATHS.reports, labelKey: 'routes.layout.nav.reports', end: false },
      { to: ROUTE_PATHS.testReports, labelKey: 'routes.layout.nav.testReports', end: false },
      {
        to: ROUTE_PATHS.equipmentLists,
        labelKey: 'routes.layout.nav.equipmentLists',
        end: false,
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
      },
      { to: ROUTE_PATHS.membership, labelKey: 'routes.layout.nav.membership', end: false },
      { to: ROUTE_PATHS.providers, labelKey: 'routes.layout.nav.providers', end: false },
      { to: ROUTE_PATHS.diagnostics, labelKey: 'routes.layout.nav.diagnostics', end: false },
    ],
  },
] as const;

export const APP_SHELL_GRID_POC_ITEM = {
  to: '/grid-poc',
  labelKey: 'routes.layout.nav.gridPoc',
  end: false,
} as const;

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
