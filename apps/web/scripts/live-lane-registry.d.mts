/**
 * Types for `live-lane-registry.mjs`.
 *
 * The registry is authored in `.mjs` so vitest's existing `scripts/**\/*.test.mjs`
 * include picks its unit test up with no config change, and so the Playwright
 * config and the Node runner can import it without a build step. TypeScript
 * infers parameter types from JS defaults, which turns `resolveLiveLaneDecision`'s
 * options object into `{ env?: {} }` — so the shape is declared here instead of
 * being weakened at the call sites.
 */

export declare const LIVE_STACK_ENV: 'FCC_LIVE_STACK_E2E';
export declare const SEED_MANIFEST_RELATIVE_PATH: string;
export declare const SEED_MANIFEST_VERSION: number;
export declare const RESEED_COMMAND: string;
export declare const STACK_COMMAND: string;

export declare const AUTH_PROFILE: {
  readonly OPEN: 'open';
  readonly OIDC: 'oidc';
  readonly LOCAL: 'local';
};
export type AuthProfile = 'open' | 'oidc' | 'local';

export declare const GATEWAY_POSTURE: {
  readonly OPEN: 'gateway-open';
  readonly ENFORCED: 'gateway-enforced';
  readonly NOT_A_GATEWAY: 'not-a-gateway';
};
export type GatewayPosture = 'gateway-open' | 'gateway-enforced' | 'not-a-gateway';

export interface LiveLaneDescriptor {
  readonly spec: string;
  readonly authProfile: AuthProfile;
  readonly runner?: string;
  readonly selectors: readonly string[];
}

export declare const LIVE_LANES: Readonly<Record<string, LiveLaneDescriptor>>;

export declare function laneIds(): string[];
export declare function liveLaneRunnerScript(laneId: string): string;
export declare function selectorNames(): string[];
export declare function isLiveStackEnabled(env: Record<string, string | undefined>): boolean;

export type LiveLaneDecision =
  | { kind: 'skip'; reason: string }
  | { kind: 'fail'; reason: string }
  | {
      kind: 'run';
      laneId: string;
      lane: LiveLaneDescriptor;
      /**
       * Selector name -> the manifest fact it resolved to. Typed as `unknown`
       * on purpose: the manifest is data written by another process, so a
       * consumer must narrow it (see `requireRevision` and friends in
       * `tests/e2e/helpers/live-stack-fixture.ts`) rather than assert a shape
       * this declaration cannot enforce.
       */
      ids: Record<string, unknown>;
    };

export declare function resolveLiveLaneDecision(input: {
  laneId?: string;
  env?: Record<string, string | undefined>;
  manifest?: Record<string, unknown> | null;
  gatewayPosture?: GatewayPosture | undefined;
}): LiveLaneDecision;
