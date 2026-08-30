import { Link } from 'react-router-dom';

import { useT } from '@/i18n';
import { isValidProjectId } from '@/shared/project-id';
import { ROUTE_PATHS } from '@/shared/route-links';

/**
 * Chamber workflow "next action" deep-links — single source of truth.
 *
 * The tester-facing chamber workbench surfaces the same follow-up screens from
 * several places: the always-visible rail (`ChambersNextActions`), the
 * post-start progress panel, and the start-failure recovery guidance. Defining
 * each link (path + label token + project-scoping rule) once here means a route
 * rename or label change touches one place, and the surfaces can never drift
 * apart on where "다음 작업" leads.
 *
 * Central-proxy invariant: every target is a hub-backed operator screen
 * (measurement history / job queue / reports / diagnostics). None points at a
 * chamber node — the browser only ever talks to the platform hub.
 */
export type NextActionKey = 'sessions' | 'jobs' | 'reports' | 'diagnostics';

interface NextActionSpec {
  readonly path: string;
  readonly labelKey: string;
  /** Reports is project-scoped so the operator lands filtered to the project
   *  context carried in the URL; the others are global operator screens. */
  readonly projectScoped: boolean;
}

const NEXT_ACTIONS: Readonly<Record<NextActionKey, NextActionSpec>> = {
  sessions: {
    path: ROUTE_PATHS.sessions,
    labelKey: 'routes.chambers.nextSessions',
    projectScoped: false,
  },
  jobs: { path: ROUTE_PATHS.jobs, labelKey: 'routes.chambers.nextJobs', projectScoped: false },
  reports: {
    path: ROUTE_PATHS.reports,
    labelKey: 'routes.chambers.nextReports',
    projectScoped: true,
  },
  diagnostics: {
    path: ROUTE_PATHS.diagnostics,
    labelKey: 'routes.chambers.nextDiagnostics',
    projectScoped: false,
  },
};

/** Build the href for one action, appending the project scope only for a
 *  project-scoped target with a valid project id (shared `isValidProjectId`
 *  SSOT), so an empty/invalid project never yields a broken query string. */
function hrefFor(spec: NextActionSpec, projectId: string): string {
  return spec.projectScoped && isValidProjectId(projectId)
    ? `${spec.path}?project=${encodeURIComponent(projectId)}`
    : spec.path;
}

/**
 * Render a set of next-action deep-links as `chambers-next__action` cards.
 *
 * Returns a Fragment (no wrapping element) so each caller owns its own
 * container — a `nav` landmark for the contextual affordances, the rail's
 * existing `div`, the recovery guidance block — with the right aria-label and
 * test id. The per-link test id is `${idPrefix}-${key}`; the rail keeps its
 * historical `chambers-next-*` ids via the default prefix.
 */
export function ChamberNextActionLinks({
  keys,
  projectId = '',
  idPrefix = 'chambers-next',
}: {
  readonly keys: readonly NextActionKey[];
  readonly projectId?: string;
  readonly idPrefix?: string;
}): JSX.Element {
  const { t } = useT();
  return (
    <>
      {keys.map((key) => {
        const spec = NEXT_ACTIONS[key];
        return (
          <Link
            key={key}
            to={hrefFor(spec, projectId)}
            className="chambers-next__action"
            data-testid={`${idPrefix}-${key}`}
          >
            {t(spec.labelKey)}
          </Link>
        );
      })}
    </>
  );
}
