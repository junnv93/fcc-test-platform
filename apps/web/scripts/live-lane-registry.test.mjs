import { describe, expect, it } from 'vitest';

import {
  AUTH_PROFILE,
  GATEWAY_POSTURE,
  LIVE_LANES,
  LIVE_STACK_ENV,
  RESEED_COMMAND,
  SEED_MANIFEST_RELATIVE_PATH,
  SEED_MANIFEST_VERSION,
  laneIds,
  resolveLiveLaneDecision,
} from './live-lane-registry.mjs';
import { classifyPostureResponse } from './live-stack-readiness.mjs';

const ARMED = { [LIVE_STACK_ENV]: '1' };

function manifest(overrides = {}) {
  return {
    manifest_version: SEED_MANIFEST_VERSION,
    demo_prefix: 'DEMO',
    provider_code: 'fcc-unlicensed-conducted',
    projects: { 'DEMO-PROJ-01': '11111111-1111-4111-8111-111111111111' },
    chambers: [{ chamber_id: 'DEMO-CHAMBER-01', equipment_config_keys: ['Analyzer LAN:'] }],
    reference: [
      {
        family: 'ant_gain',
        scope_kind: 'project',
        scope_id: '11111111-1111-4111-8111-111111111111',
        profile_id: 'default',
        revision_id: 'rev-published',
        revision_number: 1,
        state: 'PUBLISHED',
        coupled_with: null,
      },
      {
        family: 'test_info',
        scope_kind: 'project',
        scope_id: '11111111-1111-4111-8111-111111111111',
        profile_id: 'default',
        revision_id: 'rev-candidate',
        revision_number: 2,
        state: 'CANDIDATE',
        coupled_with: null,
      },
      {
        family: 'correction',
        scope_kind: 'room',
        scope_id: 'DEMO-CHAMBER-01',
        profile_id: 'default',
        revision_id: 'rev-coupled',
        revision_number: 2,
        state: 'CANDIDATE',
        coupled_with: 'switch_port_mapping',
      },
    ],
    // The provider-local section: what the SQLite database the headless surface
    // serves reports about its reference families. The verdict tokens are the
    // node's own boot vocabulary, spelled out here as literals on purpose — a
    // fixture that imported them from the producer would agree with the producer
    // by construction and could not catch a change in either.
    provider_local: {
      db_path: '/dev/null/headless.db',
      db_present: true,
      reference: {
        analyzer_settings: 'unresolved',
        ant_gain: 'unresolved',
        correction: 'unresolved',
        frequency_table: 'table',
        switch_port_mapping: 'unresolved',
        test_info: 'unresolved',
      },
      observed_rows: { frequency_table: 15 },
      error: null,
    },
    ...overrides,
  };
}

/** The posture each lane's declared auth profile requires. */
function postureFor(laneId) {
  return LIVE_LANES[laneId].authProfile === AUTH_PROFILE.OPEN
    ? GATEWAY_POSTURE.OPEN
    : GATEWAY_POSTURE.ENFORCED;
}

describe('live lane gate: switched off vs unprovisioned', () => {
  it('skips — and names the env var — when the lane is switched off', () => {
    const decision = resolveLiveLaneDecision({ laneId: 'reference-data', env: {} });
    expect(decision.kind).toBe('skip');
    expect(decision.reason).toContain(LIVE_STACK_ENV);
  });

  it('fails, naming the manifest path AND the re-seed command, when the manifest is absent', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'reference-data',
      env: ARMED,
      manifest: null,
      gatewayPosture: GATEWAY_POSTURE.ENFORCED,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain(SEED_MANIFEST_RELATIVE_PATH);
    expect(decision.reason).toContain(RESEED_COMMAND);
  });

  it('fails naming BOTH versions when the manifest shape is stale', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'reference-data',
      env: ARMED,
      manifest: manifest({ manifest_version: SEED_MANIFEST_VERSION + 7 }),
      gatewayPosture: GATEWAY_POSTURE.ENFORCED,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain(String(SEED_MANIFEST_VERSION + 7));
    expect(decision.reason).toContain(String(SEED_MANIFEST_VERSION));
  });

  it('fails naming the lane and the unsatisfied selector', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'chamber-config',
      env: ARMED,
      manifest: manifest({ chambers: [] }),
      gatewayPosture: GATEWAY_POSTURE.ENFORCED,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain('chamber-config');
    expect(decision.reason).toContain('chamberWithEquipmentConfig');
  });

  it('says a provisioning gap is not a product defect', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'reference-data',
      env: ARMED,
      manifest: manifest({ reference: [] }),
      gatewayPosture: GATEWAY_POSTURE.ENFORCED,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain('provisioning');
  });

  it('runs and hands the spec its identifiers when everything is provisioned', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'reference-data',
      env: ARMED,
      manifest: manifest(),
      gatewayPosture: GATEWAY_POSTURE.ENFORCED,
    });
    expect(decision.kind).toBe('run');
    expect(decision.ids.providerCode).toBe('fcc-unlicensed-conducted');
    expect(decision.ids.publishedRevision.revision_id).toBe('rev-published');
    expect(decision.ids.candidateRevision.revision_id).toBe('rev-candidate');
    expect(decision.ids.coupledCandidate.revision_id).toBe('rev-coupled');
  });

  it('never resolves an identifier the manifest did not supply', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'test-plans',
      env: ARMED,
      manifest: manifest(),
      gatewayPosture: GATEWAY_POSTURE.OPEN,
    });
    expect(decision.kind).toBe('run');
    expect(decision.ids.activeProject.id).toBe('11111111-1111-4111-8111-111111111111');
  });

  /**
   * The reference-data precondition. Three states, because the operator's next
   * action differs in each: a manifest written before the provider-local leg
   * existed, a database that exists but was never seeded, and a seeded one.
   *
   * The first two must FAIL naming the leg command. Until this wave the lane had
   * no way to say either of them, so it ran and hit an opaque generator refusal
   * that read like a product defect.
   */
  it('fails — naming the provider-local leg — when the manifest has no provider_local section', () => {
    const stale = manifest();
    delete stale.provider_local;
    const decision = resolveLiveLaneDecision({
      laneId: 'test-plans',
      env: ARMED,
      manifest: stale,
      gatewayPosture: GATEWAY_POSTURE.OPEN,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain('provisionedChannelReference');
    expect(decision.reason).toContain('scripts/dev_seed/headless.py');
  });

  it('fails when the provider-local Frequency Table resolves to anything but its table', () => {
    for (const verdict of ['unresolved', 'workbook', 'unobserved']) {
      const decision = resolveLiveLaneDecision({
        laneId: 'test-plans',
        env: ARMED,
        manifest: manifest({
          provider_local: {
            db_path: '/dev/null/headless.db',
            db_present: true,
            reference: { frequency_table: verdict },
            observed_rows: { frequency_table: 0 },
            error: null,
          },
        }),
        gatewayPosture: GATEWAY_POSTURE.OPEN,
      });
      expect(decision.kind, verdict).toBe('fail');
      expect(decision.reason, verdict).toContain('scripts/dev_seed/headless.py');
    }
  });

  it('fails when the provider-local database is absent altogether', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'test-plans',
      env: ARMED,
      manifest: manifest({
        provider_local: {
          db_path: '/dev/null/headless.db',
          db_present: false,
          reference: {},
          observed_rows: {},
          error: null,
        },
      }),
      gatewayPosture: GATEWAY_POSTURE.OPEN,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain('provisionedChannelReference');
  });

  it('runs and hands the spec the provider-local fact when the table is populated', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'test-plans',
      env: ARMED,
      manifest: manifest(),
      gatewayPosture: GATEWAY_POSTURE.OPEN,
    });
    expect(decision.kind).toBe('run');
    expect(decision.ids.provisionedChannelReference.rows).toBe(15);
    expect(decision.ids.provisionedChannelReference.dbPath).toBe('/dev/null/headless.db');
  });

  it('advertises BOTH seed legs, because neither implies the other', () => {
    expect(RESEED_COMMAND).toContain('scripts/dev_seed/central.py');
    expect(RESEED_COMMAND).toContain('scripts/dev_seed/headless.py');
  });

  /**
   * THE property. Every other case above is an instance of it; this asserts it
   * over the whole matrix so a future branch cannot quietly re-introduce a
   * data-driven skip. Mutating any `fail` branch to `skip` turns this red.
   */
  it('an armed lane NEVER skips, whatever the manifest looks like', () => {
    const manifests = [
      undefined,
      null,
      {},
      manifest({ manifest_version: 999 }),
      manifest({ projects: {} }),
      manifest({ chambers: [] }),
      manifest({ reference: [] }),
      manifest({ provider_code: '' }),
      manifest({ reference: [{ family: 'x', state: 'PUBLISHED', coupled_with: null }] }),
      manifest({ provider_local: undefined }),
      manifest({ provider_local: { db_present: false, reference: {}, observed_rows: {} } }),
      manifest(),
    ];
    const postures = [
      undefined,
      GATEWAY_POSTURE.OPEN,
      GATEWAY_POSTURE.ENFORCED,
      GATEWAY_POSTURE.NOT_A_GATEWAY,
    ];
    let seen = 0;
    for (const laneId of laneIds()) {
      for (const candidate of manifests) {
        for (const gatewayPosture of postures) {
          const decision = resolveLiveLaneDecision({
            laneId,
            env: ARMED,
            manifest: candidate,
            gatewayPosture,
          });
          seen += 1;
          expect(decision.kind, `${laneId} / ${gatewayPosture}`).not.toBe('skip');
        }
      }
    }
    // Non-vacuity: the sweep must actually have entered its path.
    expect(seen).toBe(laneIds().length * manifests.length * postures.length);
    expect(seen).toBeGreaterThan(100);
  });

  it('an unknown lane fails rather than skipping', () => {
    const decision = resolveLiveLaneDecision({ laneId: 'nope', env: ARMED });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain('nope');
  });
});

describe('live lane gate: auth posture is a lane property', () => {
  it('every lane declares a known auth profile', () => {
    const known = new Set(Object.values(AUTH_PROFILE));
    for (const laneId of laneIds()) {
      expect(known.has(LIVE_LANES[laneId].authProfile), laneId).toBe(true);
    }
  });

  it('refuses an oidc lane on an open stack, naming both modes', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'reference-data',
      env: ARMED,
      manifest: manifest(),
      gatewayPosture: GATEWAY_POSTURE.OPEN,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain(GATEWAY_POSTURE.ENFORCED);
    expect(decision.reason).toContain(GATEWAY_POSTURE.OPEN);
  });

  it('refuses an open lane on an enforcing stack', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'test-plans',
      env: ARMED,
      manifest: manifest(),
      gatewayPosture: GATEWAY_POSTURE.ENFORCED,
    });
    expect(decision.kind).toBe('fail');
  });

  it('accepts each lane on the posture it declares', () => {
    for (const laneId of laneIds()) {
      const decision = resolveLiveLaneDecision({
        laneId,
        env: ARMED,
        manifest: manifest(),
        gatewayPosture: postureFor(laneId),
      });
      expect(decision.kind, laneId).toBe('run');
    }
  });

  it('the reference lane is oidc because reference writes refuse an anonymous actor', () => {
    // Not a style choice: every reference write route calls `_require_actor`,
    // so under AUTH_MODE=disabled the fork/edit/publish workflow 403s — the
    // exact clicks this lane exists to verify.
    expect(LIVE_LANES['reference-data'].authProfile).toBe(AUTH_PROFILE.OIDC);
    expect(LIVE_LANES['chamber-config'].authProfile).toBe(AUTH_PROFILE.OIDC);
  });

  it('declares local auth as a distinct enforced profile with no seed selectors', () => {
    expect(AUTH_PROFILE.LOCAL).not.toBe(AUTH_PROFILE.OPEN);
    expect(AUTH_PROFILE.LOCAL).not.toBe(AUTH_PROFILE.OIDC);
    expect(LIVE_LANES['local-auth']).toMatchObject({
      spec: 'tests/e2e/local-auth-live.spec.ts',
      authProfile: AUTH_PROFILE.LOCAL,
      selectors: [],
    });
  });

  it('runs the local lane without a dev-seed manifest', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'local-auth',
      env: ARMED,
      manifest: undefined,
      gatewayPosture: GATEWAY_POSTURE.ENFORCED,
    });
    expect(decision.kind).toBe('run');
    expect(decision.ids).toEqual({});
  });

  it('requires an enforcing gateway for local auth', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'local-auth',
      env: ARMED,
      manifest: undefined,
      gatewayPosture: GATEWAY_POSTURE.OPEN,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain(GATEWAY_POSTURE.ENFORCED);
    expect(decision.reason).toContain(GATEWAY_POSTURE.OPEN);
  });
});

describe('gateway posture classification', () => {
  it('treats an HTML 200 as NOT a gateway', () => {
    // `vite preview` answers /platform with index.html and HTTP 200 because the
    // API proxy is registered under `server:` only. A status-only probe reads
    // "no backend at all" as "ready".
    expect(classifyPostureResponse({ status: 200, contentType: 'text/html; charset=utf-8' })).toBe(
      GATEWAY_POSTURE.NOT_A_GATEWAY,
    );
  });

  it('treats problem+json 403 as an enforcing gateway, not a broken one', () => {
    expect(classifyPostureResponse({ status: 403, contentType: 'application/problem+json' })).toBe(
      GATEWAY_POSTURE.ENFORCED,
    );
  });

  it('treats JSON 2xx as an open gateway', () => {
    expect(classifyPostureResponse({ status: 200, contentType: 'application/json' })).toBe(
      GATEWAY_POSTURE.OPEN,
    );
  });

  it('does not guess a posture from a bare status', () => {
    expect(classifyPostureResponse({ status: 502, contentType: 'text/plain' })).toBe(
      GATEWAY_POSTURE.NOT_A_GATEWAY,
    );
    expect(classifyPostureResponse({ status: 200, contentType: undefined })).toBe(
      GATEWAY_POSTURE.NOT_A_GATEWAY,
    );
  });

  it('a NOT_A_GATEWAY posture fails the lane and explains the preview proxy gap', () => {
    const decision = resolveLiveLaneDecision({
      laneId: 'reference-data',
      env: ARMED,
      manifest: manifest(),
      gatewayPosture: GATEWAY_POSTURE.NOT_A_GATEWAY,
    });
    expect(decision.kind).toBe('fail');
    expect(decision.reason).toContain('preview');
  });
});
