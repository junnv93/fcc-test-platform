import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { applyTokenSet, CLAIM_PERMISSIONS, __resetAuthStateForTests } from '@/auth/session';
import { MembershipRoute, membershipErrorMessage, membershipKey } from '@/routes/membership';

import { tableView } from './helpers/responsive-table';

import type { ReactElement } from 'react';

/**
 * FE-P8 (2026-06-13) — project membership / RBAC roster tests.
 *
 * The roster consumes the platform read API through the keyset page helper
 * (`fetchMembershipsPage`, mocked here — it owns the typed limit/cursor wire
 * shape, covered in platform-client.test.ts). assign/revoke are mocked so the
 * tests assert the view's RBAC gating + optimistic-write-then-reconcile wiring
 * (Increment 3 — the write applies to the cache immediately and reconciles by
 * invalidating the read on settle) + data-derived role suggestions, not the wire
 * shape.
 */

const platformApi = vi.hoisted(() => ({
  fetchProjectsPage: vi.fn(),
  fetchMembershipsPage: vi.fn(),
  assignMembership: vi.fn(),
  revokeMembership: vi.fn(),
}));
vi.mock('@/api/platform-client', () => platformApi);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';

function makeJwt(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).replace(/=+$/u, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/=+$/u, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return `${header}.${body}.sig`;
}

function authenticateAs(permissions: readonly string[]): void {
  applyTokenSet({
    accessToken: makeJwt({ sub: 'admin@corp', [CLAIM_PERMISSIONS]: permissions }),
    refreshToken: null,
    idToken: null,
    tokenType: 'Bearer',
    expiresIn: 600,
    scope: null,
    issuedAt: Date.now(),
  });
}

function membership(over: Record<string, unknown>): Record<string, unknown> {
  return {
    project_id: PROJECT_ID,
    user_subject: 'alice@corp',
    role_key: 'project_engineer',
    assigned_at: '2026-06-13T00:00:00+00:00',
    expires_at: null,
    ...over,
  };
}

interface Page {
  items: unknown[];
  nextCursor: string | null;
}
function page(items: unknown[], nextCursor: string | null = null): Page {
  return { items, nextCursor };
}
const EMPTY_PAGE: Page = page([]);

function renderMembership(entry: string): void {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui: ReactElement = (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[entry]}>
        <MembershipRoute />
      </MemoryRouter>
    </QueryClientProvider>
  );
  render(ui);
}

beforeEach(() => {
  __resetAuthStateForTests();
  sessionStorage.clear();
  platformApi.fetchProjectsPage.mockReset();
  platformApi.fetchMembershipsPage.mockReset();
  platformApi.assignMembership.mockReset();
  platformApi.revokeMembership.mockReset();
  platformApi.fetchProjectsPage.mockResolvedValue({
    items: [
      {
        project_id: PROJECT_ID,
        project_code: 'SM-TEST',
        model_name: 'SM-TEST',
        manufacturer: null,
        management_number: 'M-001',
        status: 'active',
        sample_count: 0,
      },
    ],
    nextCursor: null,
  });
  platformApi.fetchMembershipsPage.mockResolvedValue(EMPTY_PAGE);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('membershipKey', () => {
  it('joins (user, role) into a stable identity and is undefined when partial', () => {
    expect(membershipKey('alice@corp', 'project_admin')).toBe('alice@corp::project_admin');
    expect(membershipKey(undefined, 'project_admin')).toBeUndefined();
    expect(membershipKey('alice@corp', undefined)).toBeUndefined();
  });
});

describe('membershipErrorMessage', () => {
  it('maps HTTP status to operator-facing messages', () => {
    expect(membershipErrorMessage(Object.assign(new Error(), { status: 400 }))).toContain('역할');
    // Phase L (§4): the 403 copy is generic tester language, never the token.
    const forbidden = membershipErrorMessage(Object.assign(new Error(), { status: 403 }));
    expect(forbidden).toContain('권한');
    expect(forbidden).not.toContain('platform:admin');
    expect(membershipErrorMessage(Object.assign(new Error(), { status: 404 }))).toContain(
      '찾을 수 없',
    );
    // No status (offline / unreachable central server) → the undefined-status arm.
    expect(membershipErrorMessage(new Error('offline'))).toContain('오프라인');
  });
});

describe('MembershipRoute RBAC gating', () => {
  it('denies the roster without platform:read', async () => {
    authenticateAs(['platform:claim']);
    renderMembership('/membership');
    await waitFor(() => {
      expect(screen.getByText(/missing permission: platform:read/iu)).toBeInTheDocument();
    });
  });

  // Membership-effective permission affordance (P2 debt, 2026-06-23): the backend
  // authorizes the UNION of token ∪ project-membership effective permissions, so a
  // read-only token user MAY hold admin via membership (project_admin). The UI must
  // NOT hard-hide assign/revoke on a token-only miss (that would falsely block a
  // legitimate membership-granted admin) — it offers the controls + a non-blocking
  // advisory and lets the backend be the authority. Mirrors the sample-inventory
  // import affordance pattern.
  it('still offers assign + revoke on a token-only miss (membership may grant) with an advisory', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchMembershipsPage.mockResolvedValue(page([membership({})]));
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-roster')).toBeInTheDocument();
    });
    expect(screen.getByTestId('membership-workbench-overview')).toBeInTheDocument();
    expect(screen.getByTestId('membership-workbench')).toBeInTheDocument();
    // NOT falsely blocked: the assign form + revoke control are offered, with a
    // non-blocking advisory that membership may grant the permission.
    expect(screen.getByTestId('membership-row')).toBeInTheDocument();
    expect(screen.getByTestId('membership-assign-form')).toBeInTheDocument();
    expect(tableView('membership-roster').getByTestId('membership-revoke')).toBeInTheDocument();
    expect(screen.getByTestId('membership-token-hint')).toBeInTheDocument();
  });

  it('exposes assign + revoke controls for an admin token without the advisory', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(page([membership({})]));
    renderMembership(`/membership?project=${PROJECT_ID}`);
    // assign form is immediate (authenticated); revoke needs the roster row loaded.
    expect(screen.getByTestId('membership-assign-form')).toBeInTheDocument();
    // The token already carries platform:admin → no advisory hint.
    expect(screen.queryByTestId('membership-token-hint')).not.toBeInTheDocument();
    await waitFor(() => {
      expect(tableView('membership-roster').getByTestId('membership-revoke')).toBeInTheDocument();
    });
  });

  it('surfaces a backend 403 as the forbidden membership error (membership lacking too)', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchMembershipsPage.mockResolvedValue(EMPTY_PAGE);
    platformApi.assignMembership.mockRejectedValue(
      Object.assign(new Error('forbidden'), { status: 403 }),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-assign-form')).toBeInTheDocument();
    });
    await userEvent.type(screen.getByTestId('assign-user-input'), 'bob@corp');
    await userEvent.type(screen.getByTestId('assign-role-input'), 'project_viewer');
    await userEvent.click(screen.getByTestId('assign-submit'));
    await waitFor(() => {
      expect(screen.getByTestId('membership-write-error')).toBeInTheDocument();
    });
    // Existing forbidden copy is reused (no new error surface).
    expect(screen.getByTestId('membership-write-error')).toHaveTextContent('권한');
  });
});

describe('MembershipRoute admin write (optimistic write + reconcile)', () => {
  it('assigns a role and reconciles by refetching the roster on settle', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(EMPTY_PAGE);
    platformApi.assignMembership.mockResolvedValue(membership({}));
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-assign-form')).toBeInTheDocument();
    });

    await userEvent.type(screen.getByTestId('assign-user-input'), 'bob@corp');
    await userEvent.type(screen.getByTestId('assign-role-input'), 'project_viewer');
    await userEvent.click(screen.getByTestId('assign-submit'));

    await waitFor(() => {
      expect(platformApi.assignMembership).toHaveBeenCalledWith(PROJECT_ID, {
        user_subject: 'bob@corp',
        role_key: 'project_viewer',
      });
    });
    // optimistic write + reconcile: the roster is re-fetched (initial + settle invalidate).
    await waitFor(() => {
      expect(platformApi.fetchMembershipsPage.mock.calls.length).toBeGreaterThanOrEqual(2);
    });
  });

  it('revokes a role for the row holder', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(
      page([membership({ user_subject: 'carol@corp', role_key: 'project_admin' })]),
    );
    platformApi.revokeMembership.mockResolvedValue(
      membership({ user_subject: 'carol@corp', role_key: 'project_admin' }),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(tableView('membership-roster').getByTestId('membership-revoke')).toBeInTheDocument();
    });

    await userEvent.click(tableView('membership-roster').getByTestId('membership-revoke'));
    await waitFor(() => {
      expect(platformApi.revokeMembership).toHaveBeenCalledWith(PROJECT_ID, {
        user_subject: 'carol@corp',
        role_key: 'project_admin',
      });
    });
  });

  it('announces success via an aria-live status region after assign', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(EMPTY_PAGE);
    platformApi.assignMembership.mockResolvedValue(
      membership({ user_subject: 'bob@corp', role_key: 'project_viewer' }),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-assign-form')).toBeInTheDocument();
    });
    await userEvent.type(screen.getByTestId('assign-user-input'), 'bob@corp');
    await userEvent.type(screen.getByTestId('assign-role-input'), 'project_viewer');
    await userEvent.click(screen.getByTestId('assign-submit'));
    await waitFor(() => {
      const node = screen.getByTestId('membership-write-success');
      expect(node).toHaveAttribute('aria-live', 'polite');
      expect(node).toHaveTextContent('bob@corp');
      expect(node).toHaveTextContent('project_viewer');
    });
  });

  it('surfaces a write error message on a failed assign', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(EMPTY_PAGE);
    platformApi.assignMembership.mockRejectedValue(
      Object.assign(new Error('membership assign failed'), { status: 400 }),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-assign-form')).toBeInTheDocument();
    });
    await userEvent.type(screen.getByTestId('assign-user-input'), 'bob@corp');
    await userEvent.type(screen.getByTestId('assign-role-input'), 'nope');
    await userEvent.click(screen.getByTestId('assign-submit'));
    await waitFor(() => {
      expect(screen.getByTestId('membership-write-error')).toBeInTheDocument();
    });
  });
});

describe('MembershipRoute role suggestions (no hardcoded enum)', () => {
  it('derives the datalist options from the roles present in the loaded roster', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(
      page([
        membership({ user_subject: 'a@corp', role_key: 'project_admin' }),
        membership({ user_subject: 'b@corp', role_key: 'project_viewer' }),
        membership({ user_subject: 'c@corp', role_key: 'project_admin' }),
      ]),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    // Wait until the roster has loaded and the datalist is populated from it.
    await waitFor(() => {
      expect(screen.getByTestId('assign-role-options').querySelectorAll('option').length).toBe(2);
    });
    const options = Array.from(
      screen.getByTestId('assign-role-options').querySelectorAll('option'),
    ).map((o) => o.getAttribute('value'));
    // Distinct + sorted, exactly the roles present in the data (no hardcoded set).
    expect(options).toEqual(['project_admin', 'project_viewer']);
  });
});

describe('MembershipRoute project lookup', () => {
  it('does not query memberships until a project is selected', async () => {
    authenticateAs(['platform:read']);
    renderMembership('/membership');
    // No project param → no fetch.
    expect(platformApi.fetchMembershipsPage).not.toHaveBeenCalled();
    await screen.findByTestId('membership-project-select');
    expect(screen.getByTestId('membership-project-empty')).toBeInTheDocument();
    expect(platformApi.fetchMembershipsPage).not.toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByTestId('membership-project-select'), PROJECT_ID);
    await waitFor(() => expect(platformApi.fetchMembershipsPage).toHaveBeenCalled());
  });
});

/**
 * W3-6 M1 (2026-07-31) — the team axis.
 *
 * `MembershipEnvelope.team` / `AssignMembershipRequest.team` were in the
 * contract with zero frontend consumption: the roster did not display the RF/SAR
 * classification and assign could not set it. These cases seal the wiring AND —
 * more importantly — the two properties that make the wiring correct rather than
 * merely present: the value domain is never copied into the frontend, and the
 * classification axis never behaves like the authority axis.
 */
describe('MembershipRoute team axis (W3-6 M1)', () => {
  it('sends `team` in the assign body when specified (S1)', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(EMPTY_PAGE);
    platformApi.assignMembership.mockResolvedValue(membership({ team: 'RF' }));
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-assign-form')).toBeInTheDocument();
    });

    await userEvent.type(screen.getByTestId('assign-user-input'), 'bob@corp');
    await userEvent.type(screen.getByTestId('assign-role-input'), 'project_engineer');
    await userEvent.type(screen.getByTestId('assign-team-input'), 'RF');
    await userEvent.click(screen.getByTestId('assign-submit'));

    await waitFor(() => {
      expect(platformApi.assignMembership).toHaveBeenCalledWith(PROJECT_ID, {
        user_subject: 'bob@corp',
        role_key: 'project_engineer',
        team: 'RF',
      });
    });
  });

  it('OMITS the `team` key entirely when the field is left blank (S1)', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(EMPTY_PAGE);
    platformApi.assignMembership.mockResolvedValue(membership({}));
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-assign-form')).toBeInTheDocument();
    });

    await userEvent.type(screen.getByTestId('assign-user-input'), 'bob@corp');
    await userEvent.type(screen.getByTestId('assign-role-input'), 'project_viewer');
    // A whitespace-only entry is an ABSENT team, not a team named " ".
    await userEvent.type(screen.getByTestId('assign-team-input'), '   ');
    await userEvent.click(screen.getByTestId('assign-submit'));

    await waitFor(() => expect(platformApi.assignMembership).toHaveBeenCalled());
    const body = platformApi.assignMembership.mock.calls[0]?.[1] as Record<string, unknown>;
    // `in` rather than a value check: sending `team: ''` would be a *value* the
    // server has to store or reject; the key must not be present at all.
    expect('team' in body).toBe(false);
    expect(body).toEqual({ user_subject: 'bob@corp', role_key: 'project_viewer' });
  });

  it('displays the server `team` and distinguishes "no team" from a team (S2)', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchMembershipsPage.mockResolvedValue(
      page([
        membership({ user_subject: 'a@corp', role_key: 'project_engineer', team: 'RF' }),
        membership({ user_subject: 'b@corp', role_key: 'project_engineer', team: null }),
      ]),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-roster')).toBeInTheDocument();
    });

    const cells = tableView('membership-roster').getAllByTestId('membership-team');
    expect(cells).toHaveLength(2);
    // The server value is rendered verbatim — the frontend does not relabel or
    // normalize a team token it has no authority over.
    expect(cells[0]).toHaveTextContent('RF');
    expect(cells[0]).toHaveAttribute('data-team-present', 'true');
    // Absent team is a LEGAL membership, marked as absent rather than blank.
    expect(cells[1]).toHaveAttribute('data-team-present', 'false');
    expect(cells[1]).toHaveTextContent('—');
  });

  it('derives team suggestions from the loaded roster, never a hardcoded enum (S3 runtime half)', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(
      page([
        // Deliberately NOT the real RF/SAR domain: if the options came from a
        // frontend copy of `team_policy.py` this assertion would fail, because a
        // hardcoded list cannot reproduce project-specific data.
        membership({ user_subject: 'a@corp', team: 'ZULU' }),
        membership({ user_subject: 'b@corp', team: 'ALPHA' }),
        membership({ user_subject: 'c@corp', team: 'ZULU' }),
        membership({ user_subject: 'd@corp', team: null }),
      ]),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('assign-team-options').querySelectorAll('option').length).toBe(2);
    });
    const options = Array.from(
      screen.getByTestId('assign-team-options').querySelectorAll('option'),
    ).map((o) => o.getAttribute('value'));
    // Distinct + sorted, exactly the teams present in the data; the null row
    // contributes nothing (an absent team is not an option).
    expect(options).toEqual(['ALPHA', 'ZULU']);
  });

  it('never lets a team value gate an action — team is orthogonal to role (S4)', async () => {
    authenticateAs(['platform:read', 'platform:admin']);
    platformApi.fetchMembershipsPage.mockResolvedValue(
      page([
        membership({ user_subject: 'a@corp', role_key: 'project_engineer', team: 'RF' }),
        membership({ user_subject: 'b@corp', role_key: 'project_engineer', team: null }),
      ]),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-roster')).toBeInTheDocument();
    });

    // (a) The write control is offered identically for both rows. If team were
    //     wired as an authority axis, the team-less row would be the one to lose
    //     its affordance.
    const revokes = tableView('membership-roster').getAllByTestId('membership-revoke');
    expect(revokes).toHaveLength(2);
    for (const button of revokes) expect(button).toBeEnabled();

    // (b) Assign readiness does not depend on team: user + role alone arm the
    //     submit. Requiring a team would make a classification field a
    //     prerequisite for granting authority.
    await userEvent.type(screen.getByTestId('assign-user-input'), 'carol@corp');
    await userEvent.type(screen.getByTestId('assign-role-input'), 'project_admin');
    expect(screen.getByTestId('assign-submit')).toBeEnabled();

    // (c) Typing a team does not change that either way (no team-conditional
    //     enable/disable in either direction).
    await userEvent.type(screen.getByTestId('assign-team-input'), 'SAR');
    expect(screen.getByTestId('assign-submit')).toBeEnabled();
  });

  it('renders the team WITHOUT the role badge treatment (axes stay visually distinct, S4)', async () => {
    authenticateAs(['platform:read']);
    platformApi.fetchMembershipsPage.mockResolvedValue(
      page([membership({ user_subject: 'a@corp', role_key: 'project_engineer', team: 'RF' })]),
    );
    renderMembership(`/membership?project=${PROJECT_ID}`);
    await waitFor(() => {
      expect(screen.getByTestId('membership-roster')).toBeInTheDocument();
    });
    const table = tableView('membership-roster');
    const roleCell = table.getAllByTestId('membership-role')[0];
    const teamCell = table.getAllByTestId('membership-team')[0];
    // The role cell is a StatusBadge; the team cell must not be one. Sharing the
    // badge would present a classification as an authority state — an RBAC model
    // distortion that happens in CSS rather than in code.
    expect(roleCell?.className).toContain('status-badge');
    expect(teamCell?.className).not.toContain('status-badge');
    expect(teamCell?.className).toContain('membership-team');
  });
});
