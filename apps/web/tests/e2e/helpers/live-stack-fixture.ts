/**
 * The one way a live e2e spec is gated, authenticated and handed identifiers.
 *
 * A spec calls `openLiveLane(...)` and gets back the seeded ids it asked for.
 * It never reads `FCC_LIVE_STACK_E2E`, never reads the manifest, and never
 * calls `test.skip` — a spec that could skip for a **data** reason is how the
 * only live lane in this repository sat unexecuted for two weeks while looking
 * green.
 *
 * Two failure kinds, deliberately distinct:
 *
 *  - the lane is switched off  → `test.skip`, with the env var named
 *  - the lane is on but unprovisioned → `LiveLaneProvisioningError`, naming what
 *    is missing and how to fix it
 *
 * The second is thrown rather than asserted so the failure reads as a
 * provisioning problem, not a product defect. The runner catches almost all of
 * these before a browser starts; what remains here is the one question that
 * needs a logged-in browser to answer — whether the rows the manifest names are
 * still in the database the screen talks to.
 */
import { readFileSync } from 'node:fs';

import { expect, test, type BrowserContext, type Locator, type Page } from '@playwright/test';

import {
  AUTH_PROFILE,
  LIVE_STACK_ENV,
  RESEED_COMMAND,
  SEED_MANIFEST_RELATIVE_PATH,
  resolveLiveLaneDecision,
} from '../../../scripts/live-lane-registry.mjs';

import { injectAuthenticatedSession, TEST_OPERATOR_PERMISSIONS } from './auth-fixture';
import { loginWithRealLocalAuth } from './local-auth-live-fixture';
import { loginWithRealKeycloak } from './real-auth-fixture';

/** Raised when the lane is armed but its seeded data is not there. */
export class LiveLaneProvisioningError extends Error {
  constructor(message: string) {
    super(
      `${message}\n\nThis is a PROVISIONING gap, not a product defect: the lane was ` +
        'armed and the manifest resolved, but the screen cannot see the seeded rows.',
    );
    this.name = 'LiveLaneProvisioningError';
  }
}

function repoRoot(): URL {
  return new URL('../../../../../', import.meta.url);
}

function readManifest(): Record<string, unknown> | null {
  const path = new URL(SEED_MANIFEST_RELATIVE_PATH, repoRoot()).pathname;
  try {
    return JSON.parse(readFileSync(path, 'utf8')) as Record<string, unknown>;
  } catch {
    // Absent or unreadable are the same answer to the gate — "no manifest" —
    // and the gate is the only place allowed to turn that into a message.
    return null;
  }
}

export type LiveLaneIds = Readonly<Record<string, unknown>>;

/** A revision row as the seed manifest records it. */
export interface SeededRevision {
  readonly family: string;
  readonly scope_kind: string;
  readonly scope_id: string;
  readonly profile_id: string;
  readonly revision_id: string;
  readonly revision_number: number;
  readonly state: string;
  readonly coupled_with: string | null;
}

export interface SeededProject {
  readonly code: string;
  readonly id: string;
}

export interface SeededChamber {
  readonly chamber_id: string;
  readonly equipment_config_keys: readonly string[];
}

function narrow<T>(
  ids: LiveLaneIds,
  selector: string,
  isShape: (value: Record<string, unknown>) => boolean,
  shapeDescription: string,
): T {
  const value = ids[selector];
  if (value === undefined || value === null || typeof value !== 'object') {
    throw new LiveLaneProvisioningError(
      `the lane resolved without ${selector}; the gate should have refused first`,
    );
  }
  if (!isShape(value as Record<string, unknown>)) {
    // The manifest is written by another process, so its shape is a claim this
    // side must check rather than assume. A wrong shape here is a manifest that
    // has drifted from the seed — say which field, not "cannot read property".
    throw new LiveLaneProvisioningError(
      `seed manifest entry for ${selector} is not ${shapeDescription}: ${JSON.stringify(value)}`,
    );
  }
  return value as T;
}

export function requireRevision(ids: LiveLaneIds, selector: string): SeededRevision {
  return narrow<SeededRevision>(
    ids,
    selector,
    (value) => typeof value.revision_id === 'string' && typeof value.family === 'string',
    'a revision (revision_id + family)',
  );
}

export function requireProject(ids: LiveLaneIds, selector: string): SeededProject {
  return narrow<SeededProject>(
    ids,
    selector,
    (value) => typeof value.id === 'string' && typeof value.code === 'string',
    'a project (code + id)',
  );
}

export function requireChamber(ids: LiveLaneIds, selector: string): SeededChamber {
  return narrow<SeededChamber>(
    ids,
    selector,
    (value) => typeof value.chamber_id === 'string' && Array.isArray(value.equipment_config_keys),
    'a chamber (chamber_id + equipment_config_keys)',
  );
}

export function requireText(ids: LiveLaneIds, selector: string): string {
  const value = ids[selector];
  if (typeof value !== 'string' || value.trim() === '') {
    throw new LiveLaneProvisioningError(
      `seed manifest entry for ${selector} is not a non-empty string: ${JSON.stringify(value)}`,
    );
  }
  return value;
}

/**
 * Gate the lane, authenticate per its declared profile, and return its ids.
 *
 * `authProfile` is a lane property because one global choice would have to be
 * wrong for some lane: reference writes refuse an anonymous actor (so that lane
 * needs real OIDC), while the test-plans lane's operator account does not hold
 * `test_plan:author` (so it cannot use real OIDC today).
 */
export async function openLiveLane(
  laneId: string,
  page: Page,
  context: BrowserContext,
  options: { permissions?: readonly string[] } = {},
): Promise<LiveLaneIds> {
  const decision = resolveLiveLaneDecision({
    laneId,
    env: process.env,
    manifest: readManifest(),
    // Posture is the runner's job (it can probe before a browser exists);
    // leaving it undefined here keeps this from re-deciding what the runner
    // already decided, and from disagreeing with it.
    gatewayPosture: undefined,
  });

  if (decision.kind === 'skip') {
    test.skip(true, decision.reason);
    return {};
  }
  if (decision.kind === 'fail') {
    throw new LiveLaneProvisioningError(decision.reason);
  }

  if (decision.lane.authProfile === AUTH_PROFILE.LOCAL) {
    await loginWithRealLocalAuth(page);
  } else if (decision.lane.authProfile === AUTH_PROFILE.OIDC) {
    await loginWithRealKeycloak(page, context, {
      username: 'operator',
      // The deterministic API fixture would intercept the very calls this lane
      // exists to make against the real backend.
      installDeterministicFixture: false,
    });
  } else {
    await injectAuthenticatedSession(page, {
      permissions: options.permissions ?? TEST_OPERATOR_PERMISSIONS,
    });
  }

  return decision.ids;
}

/**
 * Assert a seeded identifier is visible to the logged-in screen.
 *
 * Asserted **through the DOM**, never through `page.request`: that is a separate
 * request context which does not carry the in-page bearer token, and measuring
 * with it produced a wrong 403 reading while this wave was being planned. Going
 * through the rendered page also proves the app's own auth path worked, which a
 * direct request never would.
 */
export async function requireSeededLocator(
  page: Page,
  locatorTestId: string,
  what: string,
): Promise<void> {
  // `.first()` because the responsive table renders the same row twice (a wide
  // table and a narrow card list). Without it Playwright raises a strict-mode
  // violation — and the first version of this helper caught that and reported a
  // "provisioning gap", which was simply wrong: the row was on the page. A catch
  // that renames every failure to one diagnosis is worse than no diagnosis.
  const locator = page.getByTestId(locatorTestId).first();
  try {
    await expect(locator).toBeVisible({ timeout: 15_000 });
  } catch (error) {
    // Carry the original message. This helper claims to know WHY the assertion
    // failed; when it is wrong, the evidence has to survive the claim.
    const cause = error instanceof Error ? error.message : String(error);
    throw new LiveLaneProvisioningError(
      `${what} is named by the seed manifest but the screen does not render it ` +
        `(expected data-testid=${locatorTestId}). The manifest and the database ` +
        `have diverged — re-seed, or check ${LIVE_STACK_ENV} points at the stack ` +
        `you seeded.\n\nUnderlying assertion failure:\n${cause}`,
    );
  }
}

/**
 * Assert a seeded value is offered by a `<select>` before choosing it.
 *
 * Without this, a wiped database surfaces as a Playwright timeout on
 * `selectOption` — the lane does fail rather than skip, which is the point, but
 * the message names a locator instead of naming the missing row and the command
 * that restores it. A failure whose diagnosis has to be reconstructed is only
 * half of what this gate promised.
 */
export async function requireSeededOption(
  select: Locator,
  value: string,
  what: string,
): Promise<void> {
  const option = select.locator(`option[value="${value}"]`);
  try {
    // WAIT before concluding. The options arrive from a query, so checking the
    // count immediately reports "the seed is missing" for what is really "the
    // request has not come back" — the same mistake as a catch that renames
    // every failure to one diagnosis, made one layer down.
    await expect(option).toHaveCount(1, { timeout: 15_000 });
  } catch {
    const offered = await select.locator('option').allTextContents();
    throw new LiveLaneProvisioningError(
      `${what} (${value}) is named by the seed manifest but the screen does not ` +
        `offer it after waiting. The screen currently offers: ` +
        `${JSON.stringify(offered)}. Re-seed the dev stack:\n  ${RESEED_COMMAND}`,
    );
  }
  await select.selectOption(value);
}
