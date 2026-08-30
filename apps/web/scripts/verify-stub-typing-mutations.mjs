#!/usr/bin/env node
/**
 * Type-level mutation battery for the contract-derived transport stubs.
 *
 * ## Why this is a script in the tree and not a paragraph in a commit message
 *
 * The wave that introduced `tests/helpers/headless-contract.ts` attested
 * `C-3: PASS — 변이 16/16 KILLED`. An independent adversarial review then ran the
 * same axis and found **six survivors**, because the mutations existed only in
 * the commit message: nobody could re-run them, and two of the "kills" were
 * incidental (the mutation removed a generic parameter, so `tsc` complained
 * about an unused type — an assertion never fired).
 *
 * So the battery lives here, and it makes three commitments the prose could not:
 *
 * 1. **It asserts the mutation applied.** An unapplied mutation and a surviving
 *    one produce identical output; without this check a typo in a search string
 *    reads as a pass.
 * 2. **It discounts incidental diagnostics.** A run whose only errors are
 *    `TS6133`/`TS6196`/`TS7027`/`TS6192` (unused local, unused type, unreachable
 *    code) counts as **SURVIVED**, because nothing in the counterexample tree
 *    objected — the compiler merely noticed litter.
 * 3. **It restores from a byte copy**, so a crashed *or killed* run cannot leave
 *    the tree mutated. The pristine tree is checked first and last. ⚠️ The
 *    signal handlers are not belt-and-braces: `process.on('exit')` does not fire
 *    on `SIGTERM`, and a run this long is killed routinely (measured — see the
 *    handler registration below).
 *
 * ## Running it
 *
 *     cd apps/web && node scripts/verify-stub-typing-mutations.mjs
 *
 * Requires `node_modules` and the generated types (`npm run codegen`). The
 * backend pytest lane has neither, which is why
 * `TestTheTypeLevelBatteryIsInTheTree` asserts this file's *properties* rather
 * than executing it.
 *
 * Exit 0 iff every mutation was applied and killed by a non-incidental
 * diagnostic, and the pristine tree typechecks clean.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const WEB_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = join(WEB_ROOT, '..', '..');
const CONTRACT = join(WEB_ROOT, 'tests/helpers/headless-contract.ts');
const TRANSPORT = join(WEB_ROOT, 'tests/helpers/headless-transport.ts');
/**
 * The production module, added by `headless-envelope-consumption-axis`
 * (2026-09-12). The blob member of the envelope union now derives from a
 * declaration that lives here, so the mutations that matter most for that axis
 * are mutations of **production** code — a battery confined to the test helpers
 * could not reach them.
 */
const CLIENT = join(WEB_ROOT, 'src/api/headless-client.ts');

/** Diagnostics that mean "the compiler noticed litter", not "an assertion fired". */
const INCIDENTAL = /^TS(6133|6196|6192|7027)$/;

/**
 * Every mutation carries the export it attacks, so `TestTheTypeLevelBatteryIsInTheTree`
 * can check the table against the module's own exports and a newly exported
 * name cannot quietly skip the battery.
 */
const MUTATIONS = [
  {
    id: 'HeadlessOkBody',
    why: 'collapse the derived success body to any',
    file: CONTRACT,
    from: 'export type HeadlessOkBody<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> = [',
    to: 'export type HeadlessOkBody<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> = any;\ntype _Ok<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> = [',
  },
  {
    id: 'HeadlessErrorBody',
    why: 'collapse the derived error body to any',
    file: CONTRACT,
    from: 'export type HeadlessErrorBody<\n  M extends HttpMethod,\n  P extends PathsWithMethod<Paths, M>,\n> = ErrorResponse<ResponseObjectMap<Operation<M, P>>, JsonErrorMedia>;',
    to: 'export type HeadlessErrorBody<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> = any;\ntype _Err<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> = ErrorResponse<\n  ResponseObjectMap<Operation<M, P>>,\n  JsonErrorMedia\n>;',
  },
  {
    id: 'JsonErrorMedia',
    why: 'revert the problem+json matcher to the upstream */json one',
    file: CONTRACT,
    from: 'type JsonErrorMedia = `${string}/${string}json`;',
    to: 'type JsonErrorMedia = `${string}/json`;',
  },
  {
    id: 'OperationBrand',
    why: 'erase the phantom that makes the path parameter measurable',
    file: CONTRACT,
    from: 'readonly __operation?: readonly [M, P];',
    to: 'readonly __operation?: unknown;',
  },
  {
    id: 'HeadlessOkEnvelope',
    why: 'weaken the success envelope response to a status literal',
    file: CONTRACT,
    from: 'export interface HeadlessOkEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>\n  extends OperationBrand<M, P> {\n  readonly data: HeadlessOkBody<M, P>;\n  readonly error?: undefined;\n  readonly response: Response;',
    to: 'export interface HeadlessOkEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>\n  extends OperationBrand<M, P> {\n  readonly data: HeadlessOkBody<M, P>;\n  readonly error?: undefined;\n  readonly response: { status: number };',
  },
  {
    id: 'HeadlessErrorEnvelope',
    why: 'weaken the error envelope response to a status literal',
    file: CONTRACT,
    from: 'export interface HeadlessErrorEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>\n  extends OperationBrand<M, P> {\n  readonly data?: undefined;\n  readonly error: HeadlessErrorBody<M, P>;\n  readonly response: Response;',
    to: 'export interface HeadlessErrorEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>\n  extends OperationBrand<M, P> {\n  readonly data?: undefined;\n  readonly error: HeadlessErrorBody<M, P>;\n  readonly response: { status: number };',
  },
  {
    id: 'HeadlessEmptyEnvelope',
    why: 'weaken the empty envelope response to a status literal',
    file: CONTRACT,
    from: 'export interface HeadlessEmptyEnvelope {\n  readonly data?: undefined;\n  readonly error?: undefined;\n  readonly response: Response;',
    to: 'export interface HeadlessEmptyEnvelope {\n  readonly data?: undefined;\n  readonly error?: undefined;\n  readonly response: { status: number };',
  },
  {
    id: 'HeadlessBlobEnvelope',
    why: 'weaken the blob envelope response to a status literal',
    file: CONTRACT,
    from: '  readonly data: Blob;\n  readonly error?: undefined;\n  readonly response: Response;',
    to: '  readonly data: Blob;\n  readonly error?: undefined;\n  readonly response: { status: number };',
  },
  {
    id: 'HeadlessEnvelope',
    why: 'drop the empty-body member from the union',
    file: CONTRACT,
    from: '  | HeadlessEmptyEnvelope\n  | (P extends HeadlessBlobParsedPath',
    to: '  | (P extends HeadlessBlobParsedPath',
  },
  {
    id: 'AnyHeadlessEnvelope',
    why: 'collapse the per-verb union to any',
    file: CONTRACT,
    from: 'export type AnyHeadlessEnvelope<M extends HttpMethod> = {',
    to: 'export type AnyHeadlessEnvelope<M extends HttpMethod> = any;\ntype _Any<M extends HttpMethod> = {',
  },
  {
    id: 'HeadlessResponseInit',
    why: 'let a stub override the status with anything',
    file: CONTRACT,
    from: 'export interface HeadlessResponseInit {\n  readonly status?: number;',
    to: 'export interface HeadlessResponseInit {\n  readonly status?: unknown;',
  },
  {
    id: 'headlessOk',
    why: 'stop constraining the success payload',
    file: CONTRACT,
    from: '  data: HeadlessOkBody<M, P>,\n  init?: HeadlessResponseInit,',
    to: '  data: unknown,\n  init?: HeadlessResponseInit,',
  },
  {
    id: 'headlessProblem',
    why: 'stop constraining the error payload',
    file: CONTRACT,
    from: '  error: HeadlessErrorBody<M, P>,',
    to: '  error: unknown,',
  },
  {
    id: 'headlessEmptyOk',
    why: 'let the empty builder answer a body',
    file: CONTRACT,
    from: 'export function headlessEmptyOk(init?: HeadlessResponseInit): HeadlessEmptyEnvelope {',
    to: 'export function headlessEmptyOk(init?: HeadlessResponseInit): any {',
  },
  {
    id: 'headlessDownload',
    why: 'let the download builder answer a non-blob',
    file: CONTRACT,
    from: '): HeadlessBlobEnvelope<M, P> {',
    to: '): any {',
  },
  {
    id: 'HeadlessProblemDetails',
    why: 'stop constraining the problem code to the published enum',
    file: CONTRACT,
    from: "  code: NonNullable<HeadlessProblemDetails>['code'],",
    to: '  code: any,',
  },
  {
    id: 'problemDetails',
    why: 'drop a required member from the problem document',
    file: CONTRACT,
    from: "    type: 'about:blank',\n    title: String(code),",
    to: '    title: String(code),',
  },
  {
    id: 'HeadlessRouteHandler',
    why: 'let a route handler answer anything',
    file: CONTRACT,
    from: ') => HeadlessEnvelope<M, P> | Promise<HeadlessEnvelope<M, P>>;',
    to: ') => unknown;\ntype _RH<M extends HttpMethod, P extends PathsWithMethod<Paths, M>> =\n  | HeadlessEnvelope<M, P>\n  | Promise<HeadlessEnvelope<M, P>>;',
  },
  {
    id: 'HeadlessRouteTable',
    why: 'unconstrain the route table keys',
    file: CONTRACT,
    from: 'export type HeadlessRouteTable = {',
    to: 'export type HeadlessRouteTable = Record<string, Record<string, unknown>>;\ntype _RT = {',
  },
  {
    id: 'HeadlessGetBodies',
    why: 'unconstrain the body map values',
    file: CONTRACT,
    from: 'export type HeadlessGetBodies = {',
    to: 'export type HeadlessGetBodies = Record<string, unknown>;\ntype _GB = {',
  },
  {
    id: 'getBodyRoutes',
    why: 'stop constraining the body map at the boundary',
    file: CONTRACT,
    from: 'export function getBodyRoutes(bodies: HeadlessGetBodies): HeadlessRouteTable {',
    to: 'export function getBodyRoutes(bodies: Record<string, unknown>): HeadlessRouteTable {',
  },
  {
    id: 'HeadlessStubMethod',
    why: 'let the spies resolve anything',
    file: TRANSPORT,
    from: ') => Promise<AnyHeadlessEnvelope<M>> | AnyHeadlessEnvelope<M>;',
    to: ') => Promise<any> | any;\ntype _Stub<M extends HttpMethod> = AnyHeadlessEnvelope<M>;',
  },
  {
    id: 'HeadlessTransportMocks',
    why: 'revert the spies to the bare Mock the wave removed',
    file: TRANSPORT,
    from: '  [V in Verb]: Mock<HeadlessStubMethod<VerbMethod[V]>>;',
    to: '  [V in Verb]: Mock;',
  },
  {
    id: 'HeadlessTransportSpies',
    why: 'mismap a verb to the wrong schema method',
    file: TRANSPORT,
    from: "  GET: 'get';",
    to: "  GET: 'post';",
  },
  {
    id: 'HeadlessRouteOptions',
    why: 'widen the unrouted mode so a typo becomes legal',
    file: TRANSPORT,
    from: "  readonly unrouted?: 'reject' | 'not-found';",
    to: '  readonly unrouted?: string;',
  },
  // --- the request axis (`stub-request-axis-typing`, 2026-09-11).
  //
  // The response axis above answers "can a stub return a body no server would
  // send". These answer the other direction: "can a suite read a request the
  // client never built". Both were `unknown` once; both are derived now.
  {
    id: 'HeadlessRequestInit',
    why: 'collapse the derived request to any',
    file: CONTRACT,
    from: '> = M extends keyof Paths[P] ? MaybeOptionalInit<Paths[P], M> : never;',
    to: '> = M extends keyof Paths[P] ? any : never;',
  },
  {
    id: 'HeadlessRequestInitUnknown',
    why: 'widen the derived request to unknown — the state before this wave',
    file: CONTRACT,
    from: '> = M extends keyof Paths[P] ? MaybeOptionalInit<Paths[P], M> : never;',
    to: '> = M extends keyof Paths[P] ? unknown : never;',
  },
  {
    id: 'HeadlessRouteHandlerInit',
    why: 'return the route handler init to unknown',
    file: CONTRACT,
    from: '  init: HeadlessRequestInit<M, P>,\n) => HeadlessEnvelope<M, P> | Promise<HeadlessEnvelope<M, P>>;',
    to: '  init: unknown,\n) => HeadlessEnvelope<M, P> | Promise<HeadlessEnvelope<M, P>>;',
  },
  {
    id: 'headlessRequests',
    why: 'stop constraining what the accessor hands back',
    file: TRANSPORT,
    from: '): HeadlessRequestInit<M, P>[] {',
    to: '): any[] {',
  },
  {
    id: 'headlessRequest',
    why: 'stop constraining the singular accessor',
    file: TRANSPORT,
    from: '): HeadlessRequestInit<M, P> {\n  const matches = headlessRequests(mock, method, path);',
    to: '): any {\n  const matches = headlessRequests(mock, method, path);',
  },
  {
    id: 'HeadlessStubMethodPath',
    why: 'return the stub path to a bare string — the docstring lied about this once',
    file: TRANSPORT,
    from: '  path: PathsWithMethod<HeadlessApiPaths, M>,\n  init?: unknown,',
    to: '  path: string,\n  init?: unknown,',
  },
  {
    id: 'HeadlessPath',
    why: 'let a suite helper take any string as an endpoint',
    file: CONTRACT,
    from: 'export type HeadlessPath<M extends HttpMethod> = PathsWithMethod<Paths, M>;',
    to: 'export type HeadlessPath<M extends HttpMethod> = M extends never ? never : string;',
  },
  // The behaviour an independent review found completely unsealed: deleting the
  // exactly-one refusal left tsc, lint, 1392 vitest tests and 304 pytest cases
  // all green. `headless-transport.test.ts` is what kills it now.
  {
    id: 'headlessRequestExactlyOne',
    gate: 'vitest',
    why: 'let the singular accessor answer when there is not exactly one match',
    file: TRANSPORT,
    from: '  if (matches.length !== 1) {',
    to: '  if (matches.length === -1) {',
  },
  {
    id: 'headlessRequestsFilter',
    gate: 'vitest',
    why: 'stop filtering by operation — answer whatever was called first',
    file: TRANSPORT,
    from: '    .filter((call) => call[0] === path)',
    to: '    .filter(() => true)',
  },
  // --- the consumption axis (`headless-envelope-consumption-axis`, 2026-09-12).
  //
  // The blob member used to ride on every operation. It now enters an
  // operation's union only through a declaration `src/api/headless-client.ts`
  // owns and consumes. Three things can undo that, and they fail in three
  // different places on purpose: the declaration can be widened, the constraint
  // that reads it can be loosened, and a new call site can simply write
  // `parseAs` itself and never touch either.
  {
    id: 'HeadlessBlobParsedPathConstraint',
    why: 'loosen the download-request constraint so any path can be blob-parsed',
    file: CLIENT,
    from: 'export function downloadRequest<P extends HeadlessBlobParsedPath, I extends object>(',
    to: 'export function downloadRequest<P extends string, I extends object>(',
  },
  {
    id: 'BLOB_PARSED_GET',
    why: 'annotate instead of `satisfies`, widening the declaration to every GET path',
    file: CLIENT,
    from: "} as const satisfies Record<string, PathsWithMethod<HeadlessPaths, 'get'>>;",
    to: "} as Record<string, PathsWithMethod<HeadlessPaths, 'get'>>;",
  },
  {
    id: 'toDownload',
    why: 'drop the second lock — let a non-blob response become a download',
    file: CLIENT,
    from: '    readonly data?: Blob | undefined;',
    to: '    readonly data?: unknown;',
  },
  {
    id: 'HeadlessEnvelopeBlobUnconditional',
    why: 'restore the widening — a Blob for every operation again',
    file: CONTRACT,
    from: '  | (P extends HeadlessBlobParsedPath ? HeadlessBlobEnvelope<M, P> : never);',
    to: '  | HeadlessBlobEnvelope<M, P>;',
  },
  {
    id: 'HeadlessEnvelopeBlobCollapsed',
    why: 'over-narrow to nothing — the failure a negatives-only seal certifies',
    file: CONTRACT,
    from: '  | (P extends HeadlessBlobParsedPath ? HeadlessBlobEnvelope<M, P> : never);',
    to: '  | (P extends never ? HeadlessBlobEnvelope<M, P> : never);',
  },
  {
    id: 'HeadlessBlobEnvelopeBrand',
    why: 'gate without scoping — a session-export blob answers a draft-export route',
    file: CONTRACT,
    from: 'export interface HeadlessBlobEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>\n  extends OperationBrand<M, P> {',
    to: 'export interface HeadlessBlobEnvelope<M extends HttpMethod, P extends PathsWithMethod<Paths, M>>\n  extends OperationBrand<M, PathsWithMethod<Paths, M>> {',
  },
  {
    id: 'inlineParseAsSite',
    gate: 'pytest',
    why: 'a new download call site that writes `parseAs` itself and never meets either type lock',
    file: CLIENT,
    from: "export async function fetchHeadlessJobs(): Promise<MeasurementJobList> {\n  const { data, error, response } = await headlessClient.GET('/headless/jobs', {});",
    to: "export async function fetchHeadlessJobs(): Promise<MeasurementJobList> {\n  const { data, error, response } = await headlessClient.GET('/headless/jobs', { parseAs: 'json' });",
  },
  // --- what an independent adversarial review got past the first version of
  // this axis (2026-09-12). Every one of these **compiled**, and every gate —
  // tsc, eslint, the structural census — stayed silent. They are here rather
  // than in the review's report because a counterexample nobody can re-run is
  // an attestation, not a seal.
  {
    id: 'undeclaredBlobDownload',
    gate: 'pytest',
    why: 'a download that never writes `parseAs` at all — `await response.blob()`, the shape `signed-download.ts` already uses',
    file: CLIENT,
    from: '/** GET `/headless/sessions/{session_id}/artifacts`',
    to: `export async function exportJobsAsFile(fallbackFilename: string): Promise<HeadlessDownload> {
  const { error, response } = await headlessClient.GET('/headless/jobs', {});
  if (error) {
    throw apiErrorFromResponse('jobs export failed', { error, response });
  }
  return {
    blob: await response.blob(),
    filename: filenameFromContentDisposition(
      response.headers.get('content-disposition') ?? null,
      fallbackFilename,
    ),
  };
}

/** GET \`/headless/sessions/{session_id}/artifacts\``,
  },
  {
    id: 'smuggledParseAsSpread',
    gate: 'pytest',
    why: 'the declared option object spread onto an undeclared operation — a real `parseAs: blob` on the wire, census sees one site',
    file: CLIENT,
    from: '/** GET `/headless/sessions/{session_id}/artifacts`',
    to: `const SMUGGLED_INIT = downloadRequest(BLOB_PARSED_GET.sessionResultsExport, {}).init;

export async function exportJobsAsFile(fallbackFilename: string): Promise<HeadlessDownload> {
  return toDownload(
    'jobs export failed',
    fallbackFilename,
    await headlessClient.GET('/headless/jobs', { ...SMUGGLED_INIT }),
  );
}

/** GET \`/headless/sessions/{session_id}/artifacts\``,
  },
  {
    id: 'doubleQuotedDeclarationEntry',
    gate: 'pytest',
    why: 'a JSON operation declared blob-parsed in a spelling the value parser cannot read',
    file: CLIENT,
    from: "  testPlanDraftExport: '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/export',",
    to:
      "  testPlanDraftExport: '/headless/projects/{project_id}/test-plan/drafts/{draft_id}/export',\n" +
      '  statsExport: "/report-automation/stats",',
  },
  {
    id: 'constReferencedDeclarationEntry',
    gate: 'pytest',
    why: 'same, via a const reference — prettier-stable, so it survives a reformat',
    file: CLIENT,
    from: 'const BLOB_PARSED_GET = {',
    to:
      "const STATS_PATH = '/report-automation/stats';\nconst BLOB_PARSED_GET = {\n" +
      '  statsExport: STATS_PATH,',
  },
  {
    id: 'substringCollisionSwallow',
    gate: 'pytest',
    why: 'an operation that swallows every failure, named so it contains the seam name',
    file: CLIENT,
    from: '/** GET `/headless/sessions/{session_id}/artifacts`',
    to: `function autoDownload(result: { readonly data?: unknown; readonly error?: unknown }): unknown {
  return result.data ?? result.error ?? null;
}

export async function fetchSomethingSilently(): Promise<unknown> {
  return autoDownload(await headlessClient.GET('/headless/status', {}));
}

/** GET \`/headless/sessions/{session_id}/artifacts\``,
  },
  {
    id: 'stringLiteralDelegateSwallow',
    gate: 'pytest',
    why: 'the same swallow, with the seam name in a string literal rather than a call',
    file: CLIENT,
    from: '/** GET `/headless/sessions/{session_id}/artifacts`',
    to: `export async function fetchStatusSwallowing(): Promise<HeadlessBackendStatusSnapshot | undefined> {
  const label = 'toDownload(x)';
  void label;
  const { data } = await headlessClient.GET('/headless/status', {});
  return data;
}

/** GET \`/headless/sessions/{session_id}/artifacts\``,
  },
];

function typecheck() {
  try {
    execFileSync('npx', ['tsc', '--noEmit'], { cwd: WEB_ROOT, encoding: 'utf8', stdio: 'pipe' });
    return [];
  } catch (error) {
    const output = `${error.stdout ?? ''}${error.stderr ?? ''}`;
    return [...output.matchAll(/error (TS\d+):/g)].map((match) => match[1]);
  }
}

/**
 * Run the transport helper's executed witnesses.
 *
 * ⚠️ **Some of what this file protects is not a type.** `headlessRequest`'s
 * refusal to guess when a call count is not exactly one is *runtime*
 * behaviour — an independent review deleted it and this battery reported
 * SURVIVED, correctly, because `tsc` cannot see a thrown error. A type-level
 * battery that quietly counts runtime mutations as unkillable would be
 * certifying the wrong thing, so mutations tagged `gate: 'vitest'` are judged
 * by the suite that can actually observe them.
 *
 * Returns a pseudo-diagnostic list so the caller's real/incidental accounting
 * is unchanged: `['VITEST']` means a real assertion failed.
 */
function runWitnesses() {
  try {
    execFileSync('npx', ['vitest', 'run', 'tests/helpers/headless-transport.test.ts'], {
      cwd: WEB_ROOT,
      encoding: 'utf8',
      stdio: 'pipe',
    });
    return [];
  } catch {
    return ['VITEST'];
  }
}

/**
 * Run the backend structural gate for the consumption axis.
 *
 * ⚠️ **Some of what this file protects is not a type *and* not a runtime
 * behaviour.** A new call site that writes `parseAs` itself never goes near
 * `downloadRequest` or the envelope union, so neither `tsc` nor vitest can see
 * it — the only thing that can is the census in
 * `TestBlobParsingIsADeclaredConsumptionAxis`. Leaving that mutation ungated
 * would report SURVIVED for a defect that is in fact caught, which is the same
 * false accounting the `gate: 'vitest'` mutations exist to avoid, pointed the
 * other way.
 *
 * The interpreter is resolved rather than assumed: this repository's venv first,
 * then whatever `python3` is on PATH. ⚠️ **A venv path that exists in the primary
 * checkout does not exist in a `git worktree`** (measured), so the fallback is
 * the normal case here, not the exotic one.
 *
 * ⚠️ **A runner that cannot start is not a kill.** An earlier version of this
 * comment promised an `UNGATED` verdict and the code did not implement it: the
 * `catch` returned `['PYTEST']` for *both* "the gate failed the mutation" and
 * "the gate never ran", and `['PYTEST']` is non-incidental, so a missing
 * interpreter printed **KILLED** (independent adversarial review, 2026-09-12 —
 * proved by pointing the resolver at a non-existent binary). On a box with no
 * pytest that would have certified the census mutations without ever running the
 * census. The runners are now probed once, before the loop.
 */
const PYTEST_SELECTION = 'BlobParsingIsADeclaredConsumptionAxis or RoutesOwnNoHeadlessTransport';

function pythonInterpreter() {
  const candidates = [
    process.env.FCC_PYTHON,
    join(REPO_ROOT, 'fcc_test_env', 'bin', 'python'),
    join(REPO_ROOT, 'fcc_test_env', 'Scripts', 'python.exe'),
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate)) ?? 'python3';
}

/** Whether a gate's runner can start at all, judged once before any mutation. */
function probe(command, args) {
  try {
    execFileSync(command, args, { cwd: REPO_ROOT, encoding: 'utf8', stdio: 'pipe' });
    return true;
  } catch {
    return false;
  }
}

const RUNNABLE = {
  tsc: true, // the pristine typecheck below is its own probe
  vitest: probe('npx', ['vitest', '--version']),
  pytest: probe(pythonInterpreter(), ['-m', 'pytest', '--version']),
};

function runStructuralGate() {
  try {
    execFileSync(
      pythonInterpreter(),
      [
        '-m',
        'pytest',
        'tests/test_frontend_architecture_conformance.py',
        '-q',
        '-k',
        PYTEST_SELECTION,
      ],
      {
        cwd: REPO_ROOT,
        encoding: 'utf8',
        stdio: 'pipe',
        env: { ...process.env, QT_QPA_PLATFORM: 'offscreen' },
      },
    );
    return [];
  } catch {
    return ['PYTEST'];
  }
}

const GATES = { tsc: typecheck, vitest: runWitnesses, pytest: runStructuralGate };

const backups = new Map([
  [CONTRACT, readFileSync(CONTRACT, 'utf8')],
  [TRANSPORT, readFileSync(TRANSPORT, 'utf8')],
  [CLIENT, readFileSync(CLIENT, 'utf8')],
]);
const restore = () => {
  for (const [file, text] of backups) writeFileSync(file, text);
};
process.on('exit', restore);
// ⚠️ **`exit` alone does not cover a killed run** — and this script's own
// docstring claimed it did. Measured 2026-09-12: a `SIGTERM` from a harness
// timeout terminated the process **between** the write and the restore, leaving
// `headlessEmptyOk` mutated to `any` in the working tree. The next `tsc` then
// reported an unrelated-looking "unused `@ts-expect-error`" in a file nobody had
// touched, which is the worst way to find out. Each mutation takes a full `tsc`
// run, so the battery is minutes long and being killed is the *normal* case, not
// the exotic one.
//
// `process.exit()` re-enters the `exit` handler, so the write happens once here
// and once there; that is idempotent (the same bytes) and cheaper than tracking
// state.
for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  process.on(signal, () => {
    restore();
    process.exit(130);
  });
}

const pristine = typecheck();
if (pristine.length > 0) {
  console.error(`pristine tree does not typecheck (${pristine.length} errors) — fix that first`);
  process.exit(2);
}
console.log(`pristine: 0 errors\n`);

let survived = 0;
for (const mutation of MUTATIONS) {
  const gate = mutation.gate ?? 'tsc';
  if (!RUNNABLE[gate]) {
    // Neither KILLED nor SURVIVED: the gate did not run. Counted with the
    // survivors so the exit code cannot read as "everything was checked".
    console.error(
      `UNGATED   ${mutation.id.padEnd(24)} ${gate} runner unavailable — ${mutation.why}`,
    );
    survived += 1;
    continue;
  }
  const before = backups.get(mutation.file);
  if (!before.includes(mutation.from)) {
    console.error(`NOT APPLIED  ${mutation.id} — search text absent (this is a battery bug)`);
    survived += 1;
    continue;
  }
  writeFileSync(mutation.file, before.replace(mutation.from, mutation.to));
  const codes = GATES[gate]();
  restore();

  const real = codes.filter((code) => !INCIDENTAL.test(code));
  if (real.length > 0) {
    console.log(
      `KILLED    ${mutation.id.padEnd(24)} ${real.length} real / ${codes.length} total  — ${mutation.why}`,
    );
  } else {
    survived += 1;
    console.error(
      `SURVIVED  ${mutation.id.padEnd(24)} ${codes.length} incidental only — ${mutation.why}`,
    );
  }
}

console.log(`\n${MUTATIONS.length - survived}/${MUTATIONS.length} killed`);
process.exit(survived === 0 ? 0 : 1);
