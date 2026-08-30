import { type InfiniteData } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { PERMISSION_PLATFORM_ADMIN, PERMISSION_PLATFORM_READ } from '@/api/permissions';
import {
  assignMembership,
  fetchMembershipsPage,
  revokeMembership,
  type MembershipEnvelope,
  type PlatformPage,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { RequirePermission, useAuthSession } from '@/auth/route-guard';
import { t, useT } from '@/i18n';
import { type ApiError } from '@/shared/api-error';
import { isValidProjectId } from '@/shared/project-id';
import { ProjectSelectField } from '@/shared/ProjectSelectField';
import { ROUTE_PATHS } from '@/shared/route-links';
import { useKeysetPagination } from '@/shared/use-keyset-pagination';
import { useOnlineStatus } from '@/shared/use-online-status';
import { useOptimisticMutation } from '@/shared/use-optimistic-mutation';
import { selectLatestWriteMessage } from '@/shared/write-feedback';
import {
  DataTable,
  DataTableSkeleton,
  Button,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  LoadMoreButton,
  PageHeader,
  SectionBand,
  StatusBadge,
  StatusMessage,
  Toolbar,
  type DataTableColumn,
  type Translate,
} from '@/ui';

/**
 * Project membership / RBAC roster — central read model (FE-P8, 2026-06-13).
 *
 * The backend FE-P8 (membership union-authorize + assign/revoke write service +
 * `audit_events` atomicity) is shipped & sealed
 * (`tests/test_platform_rbac_membership_audit_fe_p8.py`); the typed client
 * helpers (`fetchMembershipsPage` / `assignMembership` / `revokeMembership`)
 * already exist. This route is the missing UI: the RBAC roster a project
 * viewer can read and a project admin can mutate.
 *
 * RBAC: the roster read is gated by `platform:read` (a viewer sees the table);
 * assign/revoke MUTATE central state and require admin authority. The backend
 * authorizes the UNION of the `platform:admin` token AND the caller's
 * project-membership effective permissions (`project_admin`), so the UI offers
 * the assign/revoke affordance to any authenticated user and only shows a
 * non-blocking advisory on a token-only miss — the backend re-enforces and a
 * lacking caller gets a 403 (surfaced as the forbidden error). Every change is
 * audited server-side. View code calls assign/revoke through the shared
 * `useOptimisticMutation` hook (Increment 3, 2026-06-13): the roster reflects the
 * change immediately, rolls back on error, and reconciles with central truth by
 * invalidating the read on settle. Unlike a claim, a membership's optimistic
 * identity is its natural `(user_subject, role_key)` key — the same key revoke
 * sends — so there is no placeholder-id reconciliation hazard here.
 *
 * SSOT: roster source = central `project_member_permissions` view (via the
 * platform read API). Permission literals mirror the backend
 * `PLATFORM_API_PERMISSIONS`. role_key choices are DATA-DERIVED from the loaded
 * roster (a `<datalist>`), never a hardcoded enum — the backend rbac_role_grants
 * SSOT is the validation authority (unknown role → 400).
 *
 * Team axis (W3-6 M1, 2026-07-31). `MembershipEnvelope.team` /
 * `AssignMembershipRequest.team` shipped in the contract but no screen read or
 * wrote them, so the RF/SAR classification a project is actually organised by
 * was invisible here. Two properties govern how it is surfaced:
 *
 *   1. **team is NOT a permission.** The domain SSOT
 *      (`src/domain/services/team_policy.py`) states it "NEVER gates
 *      permissions" — it is a classification/attribution axis, orthogonal to
 *      `role_key`. So nothing on this screen becomes enabled/disabled/hidden
 *      because of a team value, and the team cell deliberately does NOT reuse
 *      the `StatusBadge` the role cell uses: rendering both through the same
 *      badge would make the authority axis and the classification axis look
 *      like one thing, and an RBAC model distortion that happens in CSS is
 *      still an RBAC model distortion.
 *   2. **the team vocabulary is not copied here.** `TEAM_CODES = ('RF','SAR')`
 *      lives in Python and the generated contract types it as
 *      `team?: string | null` — no enum crosses the boundary, so the frontend
 *      has no legitimate way to *know* the value domain. Hardcoding it would
 *      split one domain SSOT across two languages. The suggestions are
 *      therefore DATA-DERIVED from the loaded roster (exactly what `role_key`
 *      already does) with free text allowed, and the server stays the
 *      validation authority.
 */

// Permission tokens are the backend mirror SSOT (`@/api/permissions`, sealed by
// tests/test_rbac_parity.py). Read gates the roster; the separate admin token
// gates assign/revoke — a viewer sees the RBAC table without being able to
// mutate it.

/** Display fallback — nullish OR empty/whitespace renders an em-dash. */
function orDash(value: string | null | undefined): string {
  return value !== undefined && value !== null && value.trim() !== '' ? value : '—';
}

/** Optimistic cache transform (Increment 3): reflect a just-assigned membership
 *  in the roster before the central write confirms. Upsert by (user, role): an
 *  existing grant for the same identity is replaced in place (so re-assigning a
 *  role does not duplicate the row), otherwise the new grant is appended to the
 *  first loaded page. Pure — returns a new InfiniteData, never mutates.
 *  `undefined` cache is returned unchanged; the settle invalidation reconciles. */
export function upsertOptimisticMembership(
  current: InfiniteData<PlatformPage<MembershipEnvelope>> | undefined,
  member: MembershipEnvelope,
): InfiniteData<PlatformPage<MembershipEnvelope>> | undefined {
  if (!current) return current;
  const matches = (m: MembershipEnvelope): boolean =>
    m.user_subject === member.user_subject && m.role_key === member.role_key;
  const alreadyPresent = current.pages.some((p) => p.items.some(matches));
  if (alreadyPresent) {
    return {
      ...current,
      pages: current.pages.map((p) => ({
        ...p,
        items: p.items.map((m) => (matches(m) ? member : m)),
      })),
    };
  }
  if (current.pages.length === 0) return current;
  return {
    ...current,
    pages: current.pages.map((p, i) => (i === 0 ? { ...p, items: [...p.items, member] } : p)),
  };
}

/** Optimistic cache transform (Increment 3): drop a just-revoked membership from
 *  every loaded page by (user, role). Pure — returns a new InfiniteData. */
export function removeOptimisticMembership(
  current: InfiniteData<PlatformPage<MembershipEnvelope>> | undefined,
  userSubject: string,
  roleKey: string,
): InfiniteData<PlatformPage<MembershipEnvelope>> | undefined {
  if (!current) return current;
  return {
    ...current,
    pages: current.pages.map((p) => ({
      ...p,
      items: p.items.filter((m) => !(m.user_subject === userSubject && m.role_key === roleKey)),
    })),
  };
}

/** Map a membership write failure to an operator-facing message. Delegates to
 *  the `describeApiError` 7-arm taxonomy SSOT (no inline status switch) and only
 *  supplies the membership-specific copy per arm: 400 = role_key unknown to the
 *  rbac_role_grants SSOT; 404 = user not yet onboarded by the IdP (assign) or no
 *  current assignment (revoke); 403 = missing platform:admin; network (no status)
 *  = offline/unreachable central server. Behaviour is byte-identical to the prior
 *  inline switch — only the dispatch moves to the SSOT. */
export function membershipErrorMessage(error: ApiError): string {
  return describeApiError(error, 'platform', {
    badRequest: t('routes.membership.errorUnknownRole'),
    forbidden: t('routes.membership.errorForbidden'),
    notFound: t('routes.membership.errorNotFound'),
    conflict: t('routes.membership.errorConflict'),
    network: t('routes.membership.errorOffline'),
    default: t('routes.membership.errorDefault'),
  });
}

export function MembershipRoute(): JSX.Element {
  const { t } = useT();
  return (
    <section className="membership" aria-labelledby="membership-heading">
      <PageHeader
        title={t('routes.membership.pageTitle')}
        titleId="membership-heading"
        description={t('routes.membership.pageDescription')}
      />
      <RequirePermission permission={PERMISSION_PLATFORM_READ}>
        <MembershipWorkbenchOverview />
        <MembershipRoster />
      </RequirePermission>
    </section>
  );
}

function MembershipWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="membership-workbench-overview"
      aria-label={t('routes.membership.workbenchNavAria')}
      data-testid="membership-workbench-overview"
    >
      <a className="membership-workbench-overview__item" href="#membership-project-heading">
        <span className="membership-workbench-overview__label">
          {t('routes.membership.stepProject')}
        </span>
        <span className="membership-workbench-overview__detail">
          {t('routes.membership.stepProjectDetail')}
        </span>
      </a>
      <a className="membership-workbench-overview__item" href="#membership-roster-heading">
        <span className="membership-workbench-overview__label">
          {t('routes.membership.stepRoster')}
        </span>
        <span className="membership-workbench-overview__detail">
          {t('routes.membership.stepRosterDetail')}
        </span>
      </a>
      <a className="membership-workbench-overview__item" href="#membership-admin-heading">
        <span className="membership-workbench-overview__label">
          {t('routes.membership.stepAccess')}
        </span>
        <span className="membership-workbench-overview__detail">
          {t('routes.membership.stepAccessDetail')}
        </span>
      </a>
    </nav>
  );
}

function MembershipRoster(): JSX.Element {
  const { t } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const projectId = searchParams.get('project') ?? '';

  const auth = useAuthSession();
  const isAuthenticated = auth.kind === 'authenticated';
  const currentActor = isAuthenticated ? auth.principal.subject : '';
  // The token may carry platform:admin directly, OR the user may hold it through
  // their project membership (project_admin). The backend authorizes the UNION
  // (platform_routes authorize: token ∪ membership) — so the UI must NOT
  // hard-hide assign/revoke on a token-only miss (that would wrongly block a
  // legitimate membership-granted admin). The affordance is offered to any
  // authenticated user; a token-only miss only surfaces a non-blocking advisory,
  // and the backend stays the authority (403 → membershipErrorMessage forbidden
  // arm). Mirrors the sample-inventory edit affordance pattern.
  const hasAdminToken =
    isAuthenticated && auth.principal.permissions.includes(PERMISSION_PLATFORM_ADMIN);
  const canAttemptAdmin = isAuthenticated;
  const isOnline = useOnlineStatus();

  // Keyset pagination (shared SSOT): load the roster one page at a time
  // (user-driven "더보기") instead of one unbounded fetch. The membership set is
  // project-scoped (no technology facet — membership is not tech-scoped).
  const memberships = useKeysetPagination<MembershipEnvelope, PlatformPage<MembershipEnvelope>>({
    queryKey: queryKeys.project.memberships(projectId),
    enabled: isValidProjectId(projectId),
    fetchPage: (cursor) => fetchMembershipsPage(projectId.trim(), cursor),
    getNextCursor: (page) => page.nextCursor ?? undefined,
  });
  const rows = memberships.rows;

  // Data-derived role suggestions: the roles actually present in the loaded
  // roster (NOT a hardcoded project_viewer/engineer/admin enum). The backend
  // rbac_role_grants SSOT validates the submitted role_key (unknown → 400).
  const roleOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows) {
      if (row.role_key && row.role_key.trim() !== '') seen.add(row.role_key);
    }
    return [...seen].sort((a, b) => a.localeCompare(b));
  }, [rows]);

  // Data-derived team suggestions — same derivation as `roleOptions` above, and
  // for a stronger reason: the generated contract types `team` as
  // `string | null`, so unlike an enum-typed field there is NO way for the
  // frontend to learn the value domain from the contract. Listing `RF`/`SAR`
  // here would fork the `team_policy.py` SSOT into a second language that no
  // test compares against the first. The roster is the only honest source, and
  // free text + a server 400 covers a team this project has not used yet.
  const teamOptions = useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows) {
      const team = row.team ?? '';
      if (team.trim() !== '') seen.add(team);
    }
    return [...seen].sort((a, b) => a.localeCompare(b));
  }, [rows]);

  // Cache key for the roster read AND its optimistic write/invalidation — one
  // factory call (Increment 1 SSOT) so the optimistic setQueryData, the rollback
  // snapshot, and the settle invalidation can never drift from the read above.
  const rosterKey = queryKeys.project.memberships(projectId);

  // Optimistic roster writes (Increment 3): the table reflects the assign/revoke
  // immediately, rolls back if the central write fails, and reconciles with the
  // committed central state on settle (invalidate → refetch). The transform runs
  // over the keyset InfiniteData cache and is derived from the mutation
  // *variables* (never the response).
  const assignMutation = useOptimisticMutation<
    MembershipEnvelope,
    { userSubject: string; roleKey: string; team: string | null; expiresAt: string | null },
    InfiniteData<PlatformPage<MembershipEnvelope>>
  >({
    mutationFn: (v) =>
      assignMembership(projectId.trim(), {
        user_subject: v.userSubject,
        role_key: v.roleKey,
        // Unspecified team OMITS the key rather than sending `''` (the W3-A/B
        // convention): the schema makes `team` optional, and an empty string is
        // a *value* the server would have to store or reject, not an absence.
        ...(v.team !== null ? { team: v.team } : {}),
        ...(v.expiresAt !== null ? { expires_at: v.expiresAt } : {}),
      }),
    queryKey: rosterKey,
    optimisticUpdate: (current, v) =>
      upsertOptimisticMembership(current, {
        project_id: projectId.trim(),
        user_subject: v.userSubject,
        role_key: v.roleKey,
        team: v.team,
        assigned_at: '',
        expires_at: v.expiresAt,
      }),
  });
  const revokeMutation = useOptimisticMutation<
    MembershipEnvelope,
    { userSubject: string; roleKey: string },
    InfiniteData<PlatformPage<MembershipEnvelope>>
  >({
    mutationFn: (v) =>
      revokeMembership(projectId.trim(), {
        user_subject: v.userSubject,
        role_key: v.roleKey,
      }),
    queryKey: rosterKey,
    optimisticUpdate: (current, v) => removeOptimisticMembership(current, v.userSubject, v.roleKey),
  });

  const writeError = assignMutation.error ?? revokeMutation.error;
  // WCAG 4.1.3: announce the most recent successful write (assign/revoke). The
  // shared selector picks the latest by submittedAt so two successes don't race.
  const writeSuccess = selectLatestWriteMessage([
    {
      isSuccess: assignMutation.isSuccess,
      submittedAt: assignMutation.submittedAt,
      message: assignMutation.data
        ? t('routes.membership.assignSuccess', {
            user: assignMutation.data.user_subject,
            role: assignMutation.data.role_key,
          })
        : '',
    },
    {
      isSuccess: revokeMutation.isSuccess,
      submittedAt: revokeMutation.submittedAt,
      message: revokeMutation.data
        ? t('routes.membership.revokeSuccess', {
            user: revokeMutation.data.user_subject,
            role: revokeMutation.data.role_key,
          })
        : '',
    },
  ]);
  const pendingKey =
    (assignMutation.isPending
      ? membershipKey(assignMutation.variables?.userSubject, assignMutation.variables?.roleKey)
      : undefined) ??
    (revokeMutation.isPending
      ? membershipKey(revokeMutation.variables?.userSubject, revokeMutation.variables?.roleKey)
      : undefined) ??
    null;

  const setProject = useCallback(
    (value: string): void => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (value === '') next.delete('project');
          else next.set('project', value);
          return next;
        },
        { replace: true },
      );
    },
    [setSearchParams],
  );

  const adminActions: AdminActions = {
    canAttempt: canAttemptAdmin,
    isOnline,
    pendingKey,
    onAssign: (userSubject, roleKey, team, expiresAt) =>
      assignMutation.mutate({ userSubject, roleKey, team, expiresAt }),
    onRevoke: (userSubject, roleKey) => revokeMutation.mutate({ userSubject, roleKey }),
  };

  return (
    <div className="membership-workbench" data-testid="membership-workbench">
      <div className="membership-workbench__main">
        <section
          className="membership-workbench-panel"
          aria-labelledby="membership-project-heading"
        >
          <SectionBand
            title={t('routes.membership.projectSection')}
            titleId="membership-project-heading"
          />
          <div aria-label={t('routes.membership.projectLookupAria')}>
            <Toolbar ariaLabel={t('routes.membership.projectLookupAria')}>
              <ProjectSelectField
                value={projectId}
                onChange={setProject}
                selectId="membership-project-select"
                selectTestId="membership-project-select"
              />
              {isValidProjectId(projectId) && auth.kind === 'authenticated' && (
                <div className="toolbar-status-group" data-testid="membership-toolbar-status">
                  <StatusBadge
                    status={isOnline ? 'pass' : 'missing'}
                    label={isOnline ? t('common.online') : t('common.offline')}
                    testId="membership-online-pill"
                    title={t('routes.membership.onlineBadgeTitle')}
                  />
                  <StatusBadge
                    status={hasAdminToken ? 'pass' : 'missing'}
                    label={
                      hasAdminToken
                        ? t('routes.membership.adminBadgeLabel')
                        : t('routes.membership.adminBadgeLabelNone')
                    }
                    testId="membership-admin-pill"
                    title={t('routes.membership.adminBadgeTitle')}
                  />
                </div>
              )}
            </Toolbar>
          </div>
        </section>

        <section className="membership-workbench-panel" aria-labelledby="membership-roster-heading">
          <SectionBand
            title={t('routes.membership.rosterSection')}
            titleId="membership-roster-heading"
          />
          {!isValidProjectId(projectId) && (
            <EmptyState
              testId="membership-project-empty"
              title={t('routes.membership.selectProjectTitle')}
              description={t('routes.membership.selectProjectDescription')}
            />
          )}
          {memberships.isLoading && (
            <DataTableSkeleton columns={membershipColumns(t, adminActions).length} rows={5} />
          )}
          {memberships.isError && (
            <ErrorState
              testId="membership-error"
              message={describeApiError(memberships.error, 'platform', {
                forbidden: t('routes.membership.readForbidden'),
                default:
                  (memberships.error as ApiError).status === 400
                    ? t('routes.membership.readInvalidProject')
                    : t('routes.membership.readFailed'),
                network: t('routes.membership.readNetwork'),
              })}
            />
          )}
          {memberships.isSuccess && rows.length === 0 && (
            <EmptyState
              testId="membership-empty"
              title={t('routes.membership.emptyTitle')}
              {...(canAttemptAdmin ? { description: t('routes.membership.emptyDescription') } : {})}
            />
          )}

          {memberships.isSuccess && rows.length > 0 && (
            <>
              <DataTable<MembershipEnvelope>
                testId="membership-roster"
                caption={t('routes.membership.tableCaption')}
                columns={membershipColumns(t, adminActions)}
                rows={rows}
                rowKey={(row, index) =>
                  // `membershipKey` returns undefined when either half of the
                  // natural key is missing; the index keeps such a row keyed
                  // without inventing a second key format.
                  membershipKey(row.user_subject, row.role_key) ?? `membership-${index}`
                }
                rowTestId="membership-row"
              />
              {memberships.hasNextPage && (
                <LoadMoreButton
                  testId="membership-load-more"
                  onClick={memberships.fetchNextPage}
                  isFetching={memberships.isFetchingNextPage}
                />
              )}
            </>
          )}
        </section>
      </div>

      <aside
        className="membership-workbench__rail"
        aria-labelledby="membership-admin-heading"
        data-testid="membership-admin-panel"
      >
        <section className="membership-admin">
          <SectionBand
            title={t('routes.membership.accessSection')}
            titleId="membership-admin-heading"
          />
          <p className="membership-admin__state" data-testid="membership-admin-state">
            {isValidProjectId(projectId)
              ? t('routes.membership.selectedProject', { project: projectId })
              : t('routes.membership.noProjectSelected')}
          </p>

          {/* Assign form — offered to any authenticated user. The backend authorizes
              token ∪ membership, so a membership-granted admin without the
              platform:admin token must still see the form (no false block). */}
          {isValidProjectId(projectId) && canAttemptAdmin && (
            <AssignForm
              roleOptions={roleOptions}
              teamOptions={teamOptions}
              actions={adminActions}
              actor={currentActor}
            />
          )}

          {/* Membership-effective permission affordance: token-only miss is advisory
              only (the user may hold admin via project membership). */}
          {isValidProjectId(projectId) && canAttemptAdmin && !hasAdminToken && (
            <p className="membership-token-note" role="note" data-testid="membership-token-hint">
              {t('routes.membership.adminTokenHint')}
            </p>
          )}

          {isValidProjectId(projectId) && canAttemptAdmin && !isOnline && (
            <p className="membership-offline-note" role="status" data-testid="membership-offline">
              {t('routes.membership.offlineNote')}
            </p>
          )}
          {writeError && (
            <ErrorState
              testId="membership-write-error"
              message={membershipErrorMessage(writeError)}
            />
          )}
          {writeSuccess !== null && !writeError && (
            <StatusMessage
              tone="success"
              testId="membership-write-success"
              message={writeSuccess}
            />
          )}

          <div className="membership-admin__links">
            <Link
              to={ROUTE_PATHS.projects}
              className="membership-admin__link"
              data-testid="membership-next-projects"
            >
              {t('routes.membership.nextProjects')}
            </Link>
            <Link
              to={ROUTE_PATHS.providers}
              className="membership-admin__link"
              data-testid="membership-next-providers"
            >
              {t('routes.membership.nextProviders')}
            </Link>
          </div>
          <p className="section-hint">{t('routes.membership.nextHint')}</p>
        </section>
      </aside>
    </div>
  );
}

/** Stable per-assignment identity = (user_subject, role_key) — the central
 *  UPSERT key. Used for React keys + per-row pending state. */
export function membershipKey(userSubject?: string, roleKey?: string): string | undefined {
  if (userSubject === undefined || roleKey === undefined) return undefined;
  return `${userSubject}::${roleKey}`;
}

/** Admin write controls threaded down to the roster rows + assign form. */
export interface AdminActions {
  /** Whether the assign/revoke affordance is offered at all. Gated on
   *  authentication only (NOT the platform:admin token) because the backend
   *  authorizes the UNION of token ∪ project-membership effective permissions —
   *  hiding the controls on a token-only miss would falsely block a
   *  membership-granted admin. The backend stays the authority (403 surfaces via
   *  membershipErrorMessage's forbidden arm). */
  readonly canAttempt: boolean;
  readonly isOnline: boolean;
  readonly pendingKey: string | null;
  /** `team` is `null` when the operator left the field blank — the caller omits
   *  the key entirely rather than sending an empty string. It is passed through
   *  as data; NOTHING in this interface branches on its value (M1: team is not
   *  an authority axis). */
  readonly onAssign: (
    userSubject: string,
    roleKey: string,
    team: string | null,
    expiresAt: string | null,
  ) => void;
  readonly onRevoke: (userSubject: string, roleKey: string) => void;
}

function AssignForm({
  roleOptions,
  teamOptions,
  actions,
  actor,
}: {
  readonly roleOptions: readonly string[];
  readonly teamOptions: readonly string[];
  readonly actions: AdminActions;
  readonly actor: string;
}): JSX.Element {
  const { t } = useT();
  const [userSubject, setUserSubject] = useState('');
  const [roleKey, setRoleKey] = useState('');
  const [team, setTeam] = useState('');
  const [expiresAt, setExpiresAt] = useState('');
  const offline = !actions.isOnline;
  // `team` is deliberately ABSENT from the readiness predicate. It is optional
  // in the schema, so "no team" is a valid membership — requiring it here would
  // be the frontend inventing a rule the system does not have, and it would
  // also make a classification field behave like an authority prerequisite.
  const ready = userSubject.trim() !== '' && roleKey.trim() !== '' && !offline;

  return (
    <form
      aria-label={t('routes.membership.assignFormAria')}
      data-testid="membership-assign-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) return;
        actions.onAssign(
          userSubject.trim(),
          roleKey.trim(),
          team.trim() === '' ? null : team.trim(),
          expiresAt.trim() === '' ? null : expiresAt.trim(),
        );
      }}
    >
      <Toolbar ariaLabel={t('routes.membership.assignFormAria')} inline>
        <FieldGroup label={t('routes.membership.assignUserLabel')} htmlFor="assign-user-input">
          <input
            id="assign-user-input"
            data-testid="assign-user-input"
            value={userSubject}
            placeholder={t('routes.membership.assignUserPlaceholder')}
            onChange={(e) => setUserSubject(e.target.value)}
          />
        </FieldGroup>
        <FieldGroup label={t('routes.membership.assignRoleLabel')} htmlFor="assign-role-input">
          <input
            id="assign-role-input"
            data-testid="assign-role-input"
            list="assign-role-options"
            value={roleKey}
            placeholder={t('routes.membership.assignRolePlaceholder')}
            onChange={(e) => setRoleKey(e.target.value)}
          />
          {/* Data-derived suggestions — the roles present in the loaded roster,
              never a hardcoded enum. The backend validates unknown roles (400). */}
          <datalist id="assign-role-options" data-testid="assign-role-options">
            {roleOptions.map((role) => (
              <option key={role} value={role} />
            ))}
          </datalist>
        </FieldGroup>
        <FieldGroup label={t('routes.membership.assignTeamLabel')} htmlFor="assign-team-input">
          <input
            id="assign-team-input"
            data-testid="assign-team-input"
            list="assign-team-options"
            value={team}
            placeholder={t('routes.membership.assignTeamPlaceholder')}
            onChange={(e) => setTeam(e.target.value)}
          />
          {/* Same derivation as the role suggestions above, and for a stronger
              reason — `team` is typed `string | null` in the contract, so there
              is no enum to read the value domain from. The teams already on the
              roster are the only source that cannot drift from the backend. */}
          <datalist id="assign-team-options" data-testid="assign-team-options">
            {teamOptions.map((option) => (
              <option key={option} value={option} />
            ))}
          </datalist>
        </FieldGroup>
        <FieldGroup
          label={t('routes.membership.assignExpiresLabel')}
          htmlFor="assign-expires-input"
        >
          <input
            id="assign-expires-input"
            data-testid="assign-expires-input"
            value={expiresAt}
            placeholder={t('routes.membership.assignExpiresPlaceholder')}
            onChange={(e) => setExpiresAt(e.target.value)}
          />
        </FieldGroup>
        <Button
          type="submit"
          variant="primary"
          data-testid="assign-submit"
          disabled={!ready}
          title={offline ? t('routes.membership.assignOfflineTitle') : undefined}
        >
          {t('routes.membership.assignButton')}
        </Button>
      </Toolbar>
      <p className="membership-actor-note" role="note" data-testid="assign-actor">
        {t('routes.membership.actorNotePrefix')}
        <code>{orDash(actor)}</code>
        {t('routes.membership.actorNoteSuffix')}
      </p>
    </form>
  );
}

/**
 * Column descriptor for the project membership roster (§M7.2).
 *
 * Who and with what role is the whole point of the table, so both stay
 * primary; the revoke control is the only write on this screen and stays
 * reachable. The assignment timestamp is audit provenance and folds first.
 * The manage column is dropped entirely (not folded) when the operator cannot
 * administer — that is a permission decision, not a viewport one.
 */
function membershipColumns(
  t: Translate,
  actions: AdminActions,
): readonly DataTableColumn<MembershipEnvelope>[] {
  const columns: DataTableColumn<MembershipEnvelope>[] = [
    {
      key: 'user',
      header: t('routes.membership.colUser'),
      priority: 'primary',
      rowHeader: true,
      cell: (row) => orDash(row.user_subject),
    },
    {
      key: 'role',
      header: t('routes.membership.colRole'),
      priority: 'primary',
      cell: (row) => (
        <StatusBadge status="claimed" label={orDash(row.role_key)} testId="membership-role" />
      ),
    },
    {
      // Classification axis (M1) — RF / SAR, orthogonal to `role`. Rendered as
      // a plain chip, NOT a `StatusBadge`: the role cell above uses
      // `StatusBadge status="claimed"`, and giving the team the same treatment
      // would make a classification read as an authority state. `data-team-present`
      // makes "no team" (a legal membership) distinguishable from "has team"
      // without either one being styled as a failure.
      key: 'team',
      header: t('routes.membership.colTeam'),
      priority: 'secondary',
      cell: (row) => {
        const team = (row.team ?? '').trim();
        return team === '' ? (
          <span data-testid="membership-team" data-team-present="false">
            {orDash(row.team)}
          </span>
        ) : (
          <span className="membership-team" data-testid="membership-team" data-team-present="true">
            {team}
          </span>
        );
      },
    },
    {
      key: 'assignedAt',
      header: t('routes.membership.colAssignedAt'),
      priority: 'detail',
      cell: (row) => orDash(row.assigned_at),
    },
    {
      key: 'expires',
      header: t('routes.membership.colExpires'),
      priority: 'secondary',
      cell: (row) => orDash(row.expires_at),
    },
  ];
  if (actions.canAttempt) {
    columns.push({
      key: 'manage',
      header: t('routes.membership.colManage'),
      priority: 'secondary',
      cell: (row) => <MembershipRevokeButton row={row} actions={actions} />,
    });
  }
  return columns;
}

function MembershipRevokeButton({
  row,
  actions,
}: {
  readonly row: MembershipEnvelope;
  readonly actions: AdminActions;
}): JSX.Element {
  const { t } = useT();
  const key = membershipKey(row.user_subject, row.role_key);
  const busy = actions.pendingKey !== null && actions.pendingKey === key;
  const offline = !actions.isOnline;
  return (
    <Button
      type="button"
      variant="danger"
      className="membership-revoke"
      data-testid="membership-revoke"
      disabled={busy || offline}
      title={offline ? t('routes.membership.revokeOfflineTitle') : undefined}
      onClick={() => {
        actions.onRevoke(row.user_subject, row.role_key);
      }}
    >
      {busy ? t('routes.membership.revokeBusy') : t('routes.membership.revokeButton')}
    </Button>
  );
}

export default MembershipRoute;
