/**
 * Project option label formatter — SSOT (project-picker-ssot, 2026-06-26).
 *
 * The platform exposes a project to operators by its human handles — the model
 * name (`project_code == model_name`, ADR-0017 D1) and the PM-assigned
 * management number — never the internal `project_id` UUID. A `<ProjectPicker>`
 * option therefore reads `<model> · <management number>` so a tester recognises
 * the row the same way they file it on the company server (the management number
 * is created before samples and is the primary lookup handle).
 *
 * Pure + dependency-free so it is unit-tested and reused by every project
 * selector without re-deriving the label shape (a drift in one place would make
 * two screens label the same project differently). The separator is a named
 * constant, not an inline literal sprinkled across call sites.
 */

/** Visible separator between the model name and the management number. */
export const PROJECT_OPTION_SEPARATOR = ' · ';

/** The subset of a project envelope needed to render a selectable option. */
export interface ProjectOptionSource {
  readonly model_name: string;
  readonly management_number?: string | null;
}

/**
 * Build the option label for a project: the model name, suffixed with the
 * management number when one is assigned (`<model> · <mgmt>`). Both values are
 * trimmed; a blank/absent management number collapses to the model name alone
 * (no dangling separator).
 */
export function formatProjectOptionLabel(project: ProjectOptionSource): string {
  const model = project.model_name.trim();
  const management = (project.management_number ?? '').trim();
  return management === '' ? model : `${model}${PROJECT_OPTION_SEPARATOR}${management}`;
}
