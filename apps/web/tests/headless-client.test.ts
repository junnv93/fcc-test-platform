import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

import { headlessClient, type HeadlessApiPaths } from '@/api/headless-client';

/**
 * FE-P1 (2026-05-25) — apps/web Headless API client + codegen drift gate.
 *
 * Validates the full SSOT chain:
 *   `packages/api-artifacts/artifacts/headless-api.openapi.json`
 *   (mirror of backend build_headless_openapi_schema SSOT) → `npm run codegen` → `src/api/generated/headless-api.types.ts` →
 *   `@/api/headless-client` typed openapi-fetch surface.
 *
 * The structural drift gate lives in the generated TypeScript file. Tests
 * `import { headlessClient }` to exercise the real production module load
 * path — `tests/setup.ts` (FE-P1 fix) seeds `window.__FCC_RUNTIME_CONFIG__`
 * on jsdom so module-load `getRuntimeConfig()` calls succeed without per-test
 * boilerplate.
 */

const APPS_WEB_ROOT = resolve(__dirname, '..');
const HEADLESS_TYPES_PATH = resolve(
  APPS_WEB_ROOT,
  'src',
  'api',
  'generated',
  'headless-api.types.ts',
);

describe('headless client surface', () => {
  it('exposes a typed `openapi-fetch` client', () => {
    expect(headlessClient).toBeDefined();
    expect(typeof headlessClient.GET).toBe('function');
    expect(typeof headlessClient.POST).toBe('function');
  });

  it('re-exports the generated paths type alias', () => {
    // Compile-time check: this assignment fails `npm run typecheck` if the
    // generator drift leaves `HeadlessApiPaths` undefined. Runtime is a noop.
    const _surface: HeadlessApiPaths | undefined = undefined;
    expect(_surface).toBeUndefined();
  });
});

describe('headless generated types — drift gate', () => {
  it('generated types file exists (run `npm run codegen` if missing)', () => {
    expect(existsSync(HEADLESS_TYPES_PATH)).toBe(true);
  });

  it('cites the headless-api OpenAPI artifact in the codegen banner', () => {
    const source = readFileSync(HEADLESS_TYPES_PATH, 'utf8');
    expect(source).toContain('packages/api-artifacts/artifacts/headless-api.openapi.json');
    expect(source).toMatch(/AUTO-GENERATED/);
  });

  it('preserves the distinct report creation vs outputs route shapes', () => {
    const source = readFileSync(HEADLESS_TYPES_PATH, 'utf8');
    // POST /headless/sessions/{session_id}/reports — submit_report_request
    expect(source).toContain('/headless/sessions/{session_id}/reports');
    expect(source).toContain('submit_report_request');
    // GET /headless/reports/{request_id}/outputs — list_report_outputs
    expect(source).toContain('/headless/reports/{request_id}/outputs');
    expect(source).toContain('list_report_outputs');
  });

  it('declares every backend operationId', () => {
    const source = readFileSync(HEADLESS_TYPES_PATH, 'utf8');
    const expected = [
      'health_check',
      'headless_status',
      'provider_capabilities',
      'list_measurement_jobs',
      'get_measurement_job',
      'submit_measurement_job',
      'stop_measurement_job',
      'list_session_results',
      'list_session_artifacts',
      'submit_report_request',
      'get_report_request',
      'list_report_outputs',
      'report_automation_stats',
      'cancel_report_automation_request',
      'headless_api_contract',
    ];
    for (const operationId of expected) {
      expect(source).toContain(operationId);
    }
  });

  it('exports a typed `paths` interface for openapi-fetch consumers', () => {
    const source = readFileSync(HEADLESS_TYPES_PATH, 'utf8');
    expect(source).toMatch(/export interface paths\b/);
  });
});
