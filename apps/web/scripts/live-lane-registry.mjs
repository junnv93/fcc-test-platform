/**
 * Live e2e lane registry — the ONE definition of what gates a live lane.
 *
 * Three consumers read this module and nothing else re-derives its facts:
 * `playwright.config.ts` (whether to spawn a web server), `run-live-e2e.mjs`
 * (which spec to run and what the stack must look like), and
 * `tests/e2e/helpers/live-stack-fixture.ts` (how to authenticate and which
 * seeded identifiers to hand the spec).
 *
 * ## Why this exists
 *
 * Before this module the gate was re-invented per lane: the env literal lived in
 * two places, the readiness probe was a module named after one lane, and the
 * spec path was hardcoded in that lane's runner. Adding a second lane meant
 * copying all three.
 *
 * The sharper problem was that **a skip is quieter than a failure**. The only
 * live lane silently skipped for two weeks because the central database had no
 * projects — and a skip renders green. "The lane is switched off" and "the lane
 * is switched on but its data precondition is absent" are different facts, and
 * only the first may skip. That distinction is the reason
 * `resolveLiveLaneDecision` is a pure function with its own test rather than a
 * few `test.skip` calls spread across specs.
 */

/** The single env var that arms every live lane. */
export const LIVE_STACK_ENV = 'FCC_LIVE_STACK_E2E';

/**
 * Manifest location and shape version, mirrored from `scripts/dev_seed/config.py`.
 * A parity test binds the two — a manifest written by one and read by the other
 * cannot drift silently.
 */
export const SEED_MANIFEST_RELATIVE_PATH = '.dev/dev-seed-manifest.json';
export const SEED_MANIFEST_VERSION = 2;

/**
 * Printed verbatim in every provisioning failure, so nobody has to guess.
 *
 * **Both legs, because the seed has two.** The central leg fills platform
 * PostgreSQL; the provider-local leg fills the SQLite database the headless
 * surface serves, from which the capability matrix resolves channels. They take
 * different environment (a DSN vs. a file path) and neither implies the other,
 * so advertising only one left an operator with a lane that failed for a reason
 * no advertised command could fix. Order does not matter: each leg owns its own
 * manifest section and refreshes it.
 */
export const RESEED_COMMAND = [
  'python scripts/dev_seed/central.py    (with FCC_CENTRAL_DB_URL / FCC_CENTRAL_PROVIDER_ID set)',
  'python scripts/dev_seed/headless.py   (with FCC_HEADLESS_DB_PATH set to the stack database)',
].join('\n  ');
export const STACK_COMMAND = 'apps/web/scripts/dev-stack-local.sh --fresh';

/**
 * Auth profiles. This is a **lane property, not a runner constant**, and the
 * reason is concrete: every reference *write* route calls `_require_actor`, so
 * under `FCC_PLATFORM_AUTH_MODE=disabled` the whole authoring workflow refuses.
 * Meanwhile the `test-plans` lane cannot move to real OIDC as things stand,
 * because the dev realm grants `test_plan:author` to `admins` only and the
 * lane's operator account does not hold it. One global setting would have to be
 * wrong for one of them.
 */
export const AUTH_PROFILE = Object.freeze({
  /** Backends composed with auth disabled; the spec injects a session. */
  OPEN: 'open',
  /** Backends enforcing OIDC; the spec logs in against the real IdP. */
  OIDC: 'oidc',
  /** Backends enforcing local JWT; the spec completes the real local form flow. */
  LOCAL: 'local',
});

/**
 * What the gateway probe saw. Classified on status **and** content type: the
 * Vite API proxy is configured under `server:` only, so a `vite preview` server
 * answers `/platform/...` with `index.html` and HTTP 200. A status-only probe
 * reads "there is no backend at all" as "the stack is ready" — which is exactly
 * what the lane-specific readiness helper this module replaces did.
 */
export const GATEWAY_POSTURE = Object.freeze({
  OPEN: 'gateway-open',
  ENFORCED: 'gateway-enforced',
  NOT_A_GATEWAY: 'not-a-gateway',
});

/**
 * Lane descriptors.
 *
 * `selectors` name what the lane needs from the seed manifest; they are
 * evaluated against the manifest's **facts**, which is why no lane name appears
 * in the manifest itself. Putting lane names there would place the lane set in
 * two languages at once.
 *
 * The key set here must equal the `tests/e2e/*-live.spec.ts` glob — a spec with
 * no descriptor, or a descriptor with no spec, is a red test.
 */
export const LIVE_LANES = Object.freeze({
  'test-plans': Object.freeze({
    spec: 'tests/e2e/test-plans-live.spec.ts',
    authProfile: AUTH_PROFILE.OPEN,
    selectors: Object.freeze(['activeProject', 'provisionedChannelReference']),
  }),
  'reference-data': Object.freeze({
    spec: 'tests/e2e/reference-data-live.spec.ts',
    authProfile: AUTH_PROFILE.OIDC,
    selectors: Object.freeze([
      'providerCode',
      'publishedRevision',
      'candidateRevision',
      'coupledCandidate',
    ]),
  }),
  'chamber-config': Object.freeze({
    spec: 'tests/e2e/chamber-config-live.spec.ts',
    authProfile: AUTH_PROFILE.OIDC,
    selectors: Object.freeze(['chamberWithEquipmentConfig']),
  }),
  'local-auth': Object.freeze({
    spec: 'tests/e2e/local-auth-live.spec.ts',
    authProfile: AUTH_PROFILE.LOCAL,
    // This lane provisions its disposable local-auth database and credentials
    // before Playwright starts. Its lifecycle is therefore owned by the
    // dedicated stack runner, not the manifest-backed generic runner.
    runner: 'node scripts/run-local-auth-live-stack.mjs',
    // This lane owns its bootstrap row and obtains all facts from browser
    // responses; it must not depend on the dev-seed manifest.
    selectors: Object.freeze([]),
  }),
});

export function laneIds() {
  return Object.keys(LIVE_LANES).sort();
}

export function liveLaneRunnerScript(laneId) {
  const lane = LIVE_LANES[laneId];
  if (lane === undefined) throw new Error(`unknown live lane ${JSON.stringify(laneId)}`);
  return lane.runner ?? `node scripts/run-live-e2e.mjs --lane=${laneId}`;
}

export function isLiveStackEnabled(env) {
  return env[LIVE_STACK_ENV] === '1';
}

/**
 * Resolve one selector against the manifest, or return null.
 *
 * Each selector answers with the identifiers a spec needs. None of them fall
 * back to a literal: a selector that cannot be satisfied returns null and the
 * caller turns that into a named failure, because a lane running against
 * invented identifiers proves nothing.
 */
const SELECTORS = Object.freeze({
  providerCode: (manifest) => manifest.provider_code || null,

  activeProject: (manifest) => {
    const entries = Object.entries(manifest.projects ?? {}).sort();
    return entries.length > 0 ? { code: entries[0][0], id: entries[0][1] } : null;
  },

  /** A published revision, preferring a project-scoped one (editable without a room). */
  publishedRevision: (manifest) => pickRevision(manifest, 'PUBLISHED', (row) => !row.coupled_with),

  /** A candidate in a non-coupled family: cell editing renders only on candidates. */
  candidateRevision: (manifest) => pickRevision(manifest, 'CANDIDATE', (row) => !row.coupled_with),

  /**
   * A candidate in a coupled family — the lane asserts the sibling gate blocks
   * publishing, which is only observable on a coupled candidate.
   */
  coupledCandidate: (manifest) => pickRevision(manifest, 'CANDIDATE', (row) => !!row.coupled_with),

  /**
   * The provider-local Frequency Table resolves from its runtime table.
   *
   * Generating a test plan walks the capability matrix, which resolves every
   * channel through `frequency_table` in the SQLite database the headless
   * surface serves. With that table empty the generator refuses — correctly, and
   * with a named refusal (`503 REFERENCE_DATA_NOT_PROVISIONED`) — but a lane
   * reading that refusal cannot tell "the product is broken" from "this machine
   * was never seeded". This selector is what makes the second one say so.
   *
   * **One family, not the whole resolution.** A workbook-less dev database
   * legitimately reports the other five seed-managed families as `unresolved`,
   * and this lane touches none of them. Demanding all six would fail the lane
   * for data it never reads. Which families a lane needs is the lane's business;
   * the manifest carries facts.
   *
   * The verdict tokens are the node's own boot vocabulary
   * (`domain/services/reference_ownership_policy.ReferenceSourceVerdict`), so
   * "provisioned" means here exactly what it means when a node boots.
   */
  provisionedChannelReference: (manifest) => {
    const local = manifest.provider_local;
    if (!local || local.reference?.frequency_table !== 'table') return null;
    return {
      dbPath: local.db_path ?? null,
      rows: local.observed_rows?.frequency_table ?? null,
    };
  },

  chamberWithEquipmentConfig: (manifest) => {
    const chambers = (manifest.chambers ?? []).filter(
      (chamber) => (chamber.equipment_config_keys ?? []).length > 0,
    );
    return chambers.length > 0 ? chambers[0] : null;
  },
});

function pickRevision(manifest, state, predicate) {
  const rows = (manifest.reference ?? [])
    .filter((row) => row.state === state && predicate(row))
    .sort((a, b) => String(a.family).localeCompare(String(b.family)));
  return rows.length > 0 ? rows[0] : null;
}

export function selectorNames() {
  return Object.keys(SELECTORS).sort();
}

/**
 * The gate. Returns exactly one of:
 *
 *   { kind: 'skip', reason }  — the lane is switched off
 *   { kind: 'fail', reason }  — the lane is on but cannot run; reason names what
 *                               is missing and how to fix it
 *   { kind: 'run', lane, laneId, ids }
 *
 * **A `fail` may never become a `skip`.** That is asserted as a property over
 * the whole matrix of manifest states, not as a comment.
 */
export function resolveLiveLaneDecision({ laneId, env = {}, manifest, gatewayPosture } = {}) {
  const lane = LIVE_LANES[laneId];
  if (lane === undefined) {
    return {
      kind: 'fail',
      reason: `unknown live lane ${JSON.stringify(laneId)}; known lanes: ${laneIds().join(', ')}`,
    };
  }

  if (!isLiveStackEnabled(env)) {
    return {
      kind: 'skip',
      reason: `${LIVE_STACK_ENV}=1 is not set, so the live lane is switched off`,
    };
  }

  // From here the lane is ARMED. Everything below is a failure, never a skip:
  // the operator asked for the live lane and it cannot run.
  if (lane.selectors.length > 0) {
    if (manifest === undefined || manifest === null) {
      return {
        kind: 'fail',
        reason:
          `${LIVE_STACK_ENV}=1 but no seed manifest was found at ` +
          `${SEED_MANIFEST_RELATIVE_PATH}. Seed the dev stack first:\n  ${RESEED_COMMAND}`,
      };
    }

    if (manifest.manifest_version !== SEED_MANIFEST_VERSION) {
      return {
        kind: 'fail',
        reason:
          `seed manifest declares manifest_version ${JSON.stringify(manifest.manifest_version)} ` +
          `but this checkout expects ${SEED_MANIFEST_VERSION}. Re-seed:\n  ${RESEED_COMMAND}`,
      };
    }
  }

  if (gatewayPosture === GATEWAY_POSTURE.NOT_A_GATEWAY) {
    return {
      kind: 'fail',
      reason:
        'the base URL is not an API gateway: /platform answered with HTML, which is what a ' +
        '`vite preview` server does because vite.config.ts registers the API proxy under ' +
        '`server:` only. A live lane needs the dev gateway:\n  ' +
        STACK_COMMAND,
    };
  }

  const expectedPosture =
    lane.authProfile === AUTH_PROFILE.OPEN ? GATEWAY_POSTURE.OPEN : GATEWAY_POSTURE.ENFORCED;
  if (gatewayPosture !== undefined && gatewayPosture !== expectedPosture) {
    return {
      kind: 'fail',
      reason:
        `lane ${laneId} declares authProfile=${lane.authProfile}, which needs a ` +
        `${expectedPosture} stack, but the running stack is ${gatewayPosture}. ` +
        `Restart the stack in the matching mode:\n  ${STACK_COMMAND}`,
    };
  }

  const ids = {};
  for (const selector of lane.selectors) {
    const resolve = SELECTORS[selector];
    if (resolve === undefined) {
      return {
        kind: 'fail',
        reason:
          `lane ${laneId} declares unknown selector ${JSON.stringify(selector)}; ` +
          `known selectors: ${selectorNames().join(', ')}`,
      };
    }
    const value = resolve(manifest);
    if (value === null || value === undefined) {
      return {
        kind: 'fail',
        reason:
          `lane ${laneId} needs ${selector} but the seed manifest does not provide it. ` +
          `The stack is armed and the manifest is present, so this is a provisioning gap, ` +
          `not a product defect. Re-seed:\n  ${RESEED_COMMAND}`,
      };
    }
    ids[selector] = value;
  }

  return { kind: 'run', laneId, lane, ids };
}
