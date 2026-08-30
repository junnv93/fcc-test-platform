import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createProjectResultReference,
  fetchProjectResultReferences,
  platformClient,
  retireProjectResultReference,
  type CreateProjectResultReferenceRequest,
  type RetireProjectResultReferenceRequest,
} from '@/api/platform-client';

/**
 * Cross-session result selection — the publication half of the browser boundary.
 *
 * The selection component had tests; publish and retire had none, in any web
 * test file. That mattered because the contract's claim about this surface is a
 * *negative* one — the browser may send a provider id, a condition hash, and an
 * optional reason, and **nothing else**. Provenance, the content hash, the
 * attempt/session ids, and the selection-event id are resolved by the server
 * from the selected source. A request that carries them is forged input, and the
 * server answers 422.
 *
 * The allowed key set is therefore never spelled out here. It is read out of the
 * published contract artifact — the same bytes `npm run codegen` consumes — so
 * that widening the request on the server cannot leave this file quietly
 * asserting yesterday's shape, and so that the assertion cannot be satisfied by
 * editing the file under test.
 */

const CONTRACT_PATH = resolve(
  __dirname,
  '..',
  '..',
  '..',
  'packages',
  'api-artifacts',
  'artifacts',
  'platform-api.openapi.json',
);

const PROJECT_ID = '11111111-1111-4111-8111-111111111111';
const PROVIDER_ID = 'fcc-unlicensed';
const REVISION_ID = '22222222-2222-4222-8222-222222222222';

/** Server-owned facts a browser must never be able to assert about a publication. */
const SERVER_OWNED_FIELDS = [
  'result_json',
  'provenance_json',
  'content_sha256',
  'attempt_id',
  'source_attempt_id',
  'session_id',
  'source_session_id',
  'selection_event_id',
  'source_selection_event_id',
  'revision_id',
  'state',
] as const;

function contractSchema(name: string): {
  properties?: Record<string, unknown>;
  required?: string[];
  additionalProperties?: unknown;
} {
  const artifact = JSON.parse(readFileSync(CONTRACT_PATH, 'utf-8')) as {
    components: { schemas: Record<string, Record<string, unknown>> };
  };
  const schema = artifact.components.schemas[name];
  expect(schema, `${name} is absent from the published platform contract`).toBeDefined();
  return schema as ReturnType<typeof contractSchema>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('project result reference publication contract', () => {
  it('declares publication as a closed object of exactly the browser-owned fields', () => {
    const schema = contractSchema('CreateProjectResultReferenceRequest');
    const properties = Object.keys(schema.properties ?? {}).sort();

    // Closed, not merely documented — an open object is the same defect wearing
    // a different face, because it lets a forged field through the generator.
    expect(schema.additionalProperties).toBe(false);
    expect(properties).toEqual(['condition_hash', 'provider_id', 'reason']);
    expect((schema.required ?? []).sort()).toEqual(['condition_hash', 'provider_id']);

    // Non-emptiness for the negative below: the list of forbidden names must not
    // be trivially disjoint because the schema happens to be empty.
    expect(properties.length).toBeGreaterThan(0);
    for (const field of SERVER_OWNED_FIELDS) {
      expect(properties).not.toContain(field);
    }
  });

  it('declares retirement as a closed object that carries only a reason', () => {
    const schema = contractSchema('RetireProjectResultReferenceRequest');

    expect(schema.additionalProperties).toBe(false);
    expect(Object.keys(schema.properties ?? {}).sort()).toEqual(['reason']);
    expect(schema.required).toEqual(['reason']);
  });
});

describe('project result reference client', () => {
  it('publishes the caller body verbatim and adds no provenance of its own', async () => {
    const post = vi.spyOn(platformClient, 'POST').mockResolvedValue({
      data: { revision_id: REVISION_ID, state: 'published' },
      response: { status: 201 },
    } as never);
    const body: CreateProjectResultReferenceRequest = {
      provider_id: PROVIDER_ID,
      condition_hash: 'condition-a',
    };

    const published = await createProjectResultReference(PROJECT_ID, body);

    expect(published.state).toBe('published');
    expect(post).toHaveBeenCalledWith('/platform/projects/{project_id}/project-result-references', {
      params: { path: { project_id: PROJECT_ID } },
      body,
    });

    // The body that actually left the client — not the one we passed in — is what
    // the server sees, so the key set is asserted on the recorded call.
    const call = post.mock.calls[0];
    expect(call, 'the client never issued the POST').toBeDefined();
    const sent = (call?.[1] as unknown as { body: Record<string, unknown> }).body;
    const allowed = Object.keys(
      contractSchema('CreateProjectResultReferenceRequest').properties ?? {},
    );
    for (const key of Object.keys(sent)) {
      expect(allowed).toContain(key);
    }
    for (const field of SERVER_OWNED_FIELDS) {
      expect(sent).not.toHaveProperty(field);
    }
  });

  it('retires by revision id and surfaces the retired state', async () => {
    const post = vi.spyOn(platformClient, 'POST').mockResolvedValue({
      data: { revision_id: REVISION_ID, state: 'retired' },
      response: { status: 200 },
    } as never);
    const body: RetireProjectResultReferenceRequest = { reason: 'superseded by a rerun' };

    const retired = await retireProjectResultReference(PROJECT_ID, REVISION_ID, body);

    expect(retired.state).toBe('retired');
    expect(post).toHaveBeenCalledWith(
      '/platform/projects/{project_id}/project-result-references/{revision_id}/retire',
      { params: { path: { project_id: PROJECT_ID, revision_id: REVISION_ID } }, body },
    );
  });

  it('raises a typed error carrying the HTTP status when publication is refused', async () => {
    vi.spyOn(platformClient, 'POST').mockResolvedValue({
      error: { code: 'VALIDATION_FAILED', detail: 'server-owned field' },
      response: { status: 422 },
    } as never);

    await expect(
      createProjectResultReference(PROJECT_ID, {
        provider_id: PROVIDER_ID,
        condition_hash: 'condition-a',
      }),
    ).rejects.toMatchObject({ status: 422 });
  });

  it('raises a typed error carrying the HTTP status when retirement is refused', async () => {
    vi.spyOn(platformClient, 'POST').mockResolvedValue({
      error: { code: 'NOT_FOUND', detail: 'no such revision' },
      response: { status: 404 },
    } as never);

    await expect(
      retireProjectResultReference(PROJECT_ID, REVISION_ID, { reason: 'x' }),
    ).rejects.toMatchObject({ status: 404 });
  });

  it('lists published references without inventing a state filter', async () => {
    const get = vi.spyOn(platformClient, 'GET').mockResolvedValue({
      data: [{ revision_id: REVISION_ID, state: 'published' }],
      response: { status: 200, headers: new Headers() },
    } as never);

    const page = await fetchProjectResultReferences(PROJECT_ID, PROVIDER_ID, 'published');

    expect(page.items).toHaveLength(1);
    const listCall = get.mock.calls[0];
    expect(listCall, 'the client never issued the GET').toBeDefined();
    const query = (listCall?.[1] as unknown as { params: { query: Record<string, unknown> } })
      .params.query;
    expect(query.provider_id).toBe(PROVIDER_ID);
    expect(query.state).toBe('published');
  });
});
