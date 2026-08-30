import { projectScopedHref, ROUTE_PATHS } from './route-links';

export const PROJECT_WORKFLOW_ACTION_IDS = [
  'workspace',
  'fields',
  'inventory',
  'testPlans',
  'chambers',
  'progress',
  'reports',
  'testReports',
  'membership',
] as const;

export type ProjectWorkflowActionId = (typeof PROJECT_WORKFLOW_ACTION_IDS)[number];

const PROJECT_WORKFLOW_PATHS: Readonly<Record<ProjectWorkflowActionId, string>> = {
  workspace: ROUTE_PATHS.projects,
  fields: ROUTE_PATHS.fields,
  inventory: ROUTE_PATHS.inventory,
  testPlans: ROUTE_PATHS.testPlans,
  chambers: ROUTE_PATHS.chambers,
  progress: ROUTE_PATHS.progress,
  reports: ROUTE_PATHS.reports,
  testReports: ROUTE_PATHS.testReports,
  membership: ROUTE_PATHS.membership,
};

export interface ProjectWorkflowAction {
  readonly id: ProjectWorkflowActionId;
  readonly path: string;
  readonly href: string;
}

export function projectWorkflowHref(actionId: ProjectWorkflowActionId, projectId: string): string {
  return projectScopedHref(PROJECT_WORKFLOW_PATHS[actionId], projectId);
}

export function projectWorkspaceHref(projectId: string): string {
  return projectWorkflowHref('workspace', projectId);
}

export function projectWorkflowActions(
  projectId: string,
  actionIds: readonly ProjectWorkflowActionId[],
): readonly ProjectWorkflowAction[] {
  return actionIds.map((id) => ({
    id,
    path: PROJECT_WORKFLOW_PATHS[id],
    href: projectWorkflowHref(id, projectId),
  }));
}
