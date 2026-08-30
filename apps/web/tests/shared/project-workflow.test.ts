import { describe, expect, it } from 'vitest';

import {
  projectWorkflowActions,
  projectWorkflowHref,
  projectWorkspaceHref,
  type ProjectWorkflowActionId,
} from '@/shared/project-workflow';

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

describe('projectWorkflowHref', () => {
  it('builds project-scoped hrefs from one SSOT', () => {
    expect(projectWorkspaceHref(PROJECT_ID)).toBe(`/projects?project=${PROJECT_ID}`);
    expect(projectWorkflowHref('fields', PROJECT_ID)).toBe(`/fields?project=${PROJECT_ID}`);
    expect(projectWorkflowHref('reports', PROJECT_ID)).toBe(`/reports?project=${PROJECT_ID}`);
  });

  it('drops the project query when the id is not resolvable', () => {
    expect(projectWorkspaceHref('not-a-uuid')).toBe('/projects');
    expect(projectWorkflowHref('inventory', 'not-a-uuid')).toBe('/inventory');
  });
});

describe('projectWorkflowActions', () => {
  it('preserves the requested workflow order', () => {
    const actions = projectWorkflowActions(PROJECT_ID, [
      'workspace',
      'testPlans',
      'reports',
    ] satisfies readonly ProjectWorkflowActionId[]);
    expect(actions.map((action) => action.id)).toStrictEqual(['workspace', 'testPlans', 'reports']);
    expect(actions.map((action) => action.href)).toStrictEqual([
      `/projects?project=${PROJECT_ID}`,
      `/test-plans?project=${PROJECT_ID}`,
      `/reports?project=${PROJECT_ID}`,
    ]);
  });
});
