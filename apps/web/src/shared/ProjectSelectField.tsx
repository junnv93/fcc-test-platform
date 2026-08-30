import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { fetchProjectsPage } from '@/api/platform-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { useT } from '@/i18n';
import { SEARCH_DEBOUNCE_MS } from '@/shared/search-debounce';
import { ProjectPicker } from '@/ui';

import { formatProjectOptionLabel } from './project-option';

/**
 * ProjectSelectField — the data-bound project selector used across the operator
 * routes (project-picker-ssot, 2026-06-26).
 *
 * It binds the central project directory (`GET /platform/projects`) to the
 * presentational `<ProjectPicker>` so a tester chooses a project by model name /
 * management number and the route receives the resolved `project_id`.
 *
 * **W3-B M-C (2026-07-30) — the unbounded read is gone.** This container used to
 * call the unbounded `fetchProjects` helper with 'active', which asks the
 * backend for *every* active project and turns each into an `<option>`. (That
 * helper still exists as the documented backward-compatibility baseline; it
 * simply has no callers left.) That scales badly, but the real
 * defect was honesty: past the page size the extra projects are simply absent
 * from the list with nothing on screen saying so, and a tester who cannot find a
 * model concludes it does not exist. The same lie `/my-projects` removed from
 * its own search (D3) was still alive here (D4), on six routes.
 *
 * So the field now reads ONE searchable page:
 *
 *  - the search box narrows **server-side** (`q`), across the directory's
 *    searchable identity columns — "no match" means no such *active* project
 *    exists, not "none among the rows this client happened to load". The status
 *    axis stays pinned to 'active' (below), so the empty-search copy claims
 *    exactly that scope and no more: overstating it to "the whole directory"
 *    would be the same class of lie, pointed the other way;
 *  - a `nextCursor` in the response means more matched than fit, and that fact
 *    is surfaced in the existing status note rather than hidden;
 *  - page 2+ is deliberately NOT read. A [load more] button inside a `<select>`
 *    is wrong both as an interaction and as accessibility semantics — narrowing
 *    the search is the way to reach row 201, and the note says so.
 *
 * Every screen shares the same `queryKeys.project.pickerOptions(q)` cache entry
 * (React Query dedupe), so mounting the selector on six routes with the same
 * search term still costs ONE request.
 *
 * The copy is the `components.projectSelect.*` i18n SSOT (one key set, not a
 * per-route project-id label); a route may override `label` for context (e.g.
 * the chamber starter's "for plan suggestions"). Loading / error / empty /
 * truncated are surfaced as the picker's status note so the control never
 * silently shows an empty — or a silently clipped — list.
 */
export interface ProjectSelectFieldProps {
  /** Selected `project_id` (`''` when none). */
  readonly value: string;
  /** Commit handler — receives the chosen `project_id`. */
  readonly onChange: (projectId: string) => void;
  /** Control id (label↔select wiring + stable per-route anchor). */
  readonly selectId: string;
  /** Stable test id for the select. */
  readonly selectTestId?: string;
  /** Stable test id for the status note. */
  readonly statusTestId?: string;
  /** Stable test id for the search input. */
  readonly searchTestId?: string;
  /** Optional label override (defaults to the shared `components.projectSelect.label`). */
  readonly label?: string;
}

export function ProjectSelectField({
  value,
  onChange,
  selectId,
  selectTestId,
  statusTestId,
  searchTestId,
  label,
}: ProjectSelectFieldProps): JSX.Element {
  const { t } = useT();

  const [searchDraft, setSearchDraft] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  // Debounce: commit the draft only after the user stops (→ new queryKey → one
  // new read). While typing continues the cleanup reschedules the timer, so a
  // burst collapses into a single server-side narrowing read. The early-return
  // guard is required — without it the re-render that follows a commit would
  // arm the timer again (same shape as `my-projects.tsx`).
  useEffect(() => {
    if (searchDraft === searchTerm) return undefined;
    const handle = window.setTimeout(() => setSearchTerm(searchDraft), SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(handle);
    };
  }, [searchDraft, searchTerm]);

  // A blank search is **no filter** — the `q` key is not sent at all (an empty
  // string is a different request). This is an existence decision, not
  // normalization: casing / partial-match rules are owned solely by the backend
  // `normalize_search_term`.
  const searchQuery = searchTerm.trim() === '' ? undefined : searchTerm.trim();

  // The selector sources ACTIVE projects (project-status-visibility) — you pick
  // an in-progress project to work on, not a completed one.
  const projects = useQuery({
    queryKey: queryKeys.project.pickerOptions(searchQuery),
    queryFn: () => fetchProjectsPage('active', searchQuery),
    ...REFETCH_STRATEGIES.NORMAL,
  });

  const options = (projects.data?.items ?? []).map((project) => ({
    value: project.project_id,
    label: formatProjectOptionLabel(project),
  }));
  // More matched than fit in this page. Not paged — surfaced (see the header).
  const truncated = projects.data?.nextCursor != null;

  // A single status note SSOT: loading → error → empty → truncated. `undefined`
  // means a normal, complete list (no note). The empty branch splits on whether
  // a search is active: telling someone who typed "zzz" to go create a project
  // is the same class of lie as claiming nothing exists.
  const statusMessage = projects.isPending
    ? t('components.projectSelect.loading')
    : projects.isError
      ? t('components.projectSelect.error')
      : options.length === 0
        ? searchQuery === undefined
          ? t('components.projectSelect.empty')
          : t('components.projectSelect.emptySearch')
        : truncated
          ? t('components.projectSelect.truncated', { count: options.length })
          : undefined;

  return (
    <ProjectPicker
      label={label ?? t('components.projectSelect.label')}
      selectId={selectId}
      value={value}
      onChange={onChange}
      options={options}
      placeholderLabel={t('components.projectSelect.placeholder')}
      // Only the FIRST load / an error disables the select — `isPending` is
      // false during a background refetch, so a refresh never steals a click.
      // The search box is never disabled (it has no `disabled` wiring at all):
      // locking the input during its own refetch would eat keystrokes and
      // focus, which is precisely when the user is typing.
      disabled={projects.isPending || projects.isError}
      search={{
        inputId: `${selectId}-search`,
        label: t('components.projectSelect.search.label'),
        placeholder: t('components.projectSelect.search.placeholder'),
        value: searchDraft,
        onChange: setSearchDraft,
        ...(searchTestId !== undefined ? { testId: searchTestId } : {}),
      }}
      {...(statusMessage !== undefined ? { statusMessage } : {})}
      {...(selectTestId !== undefined ? { selectTestId } : {})}
      {...(statusTestId !== undefined ? { statusTestId } : {})}
    />
  );
}

export default ProjectSelectField;
