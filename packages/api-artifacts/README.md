# @fcc/api-artifacts

Repo-split-ready bundle of the shared FCC API contract artifacts (B3 / P14).

It packages the four cross-language contract artifacts so that, when the
monorepo is split (see `docs/architecture/repository_split_adr.md`), the
contract surface can be lifted into its own `fcc-test-contracts` repo and
consumed by every backend/frontend via a normal npm dependency.

## Contents

| name | file | kind | codegen | canonical source (`docsSource`) |
|------|------|------|---------|----------------------------------|
| `session-api` | `artifacts/session-api.openapi.json` | openapi | ✅ | `docs/api/session-api.openapi.json` |
| `headless-api` | `artifacts/headless-api.openapi.json` | openapi | ✅ | `docs/api/headless-api.openapi.json` |
| `platform-api` | `artifacts/platform-api.openapi.json` | openapi | ✅ | `docs/api/platform-api.openapi.json` |
| `central-db-schema` | `artifacts/central_db_schema.v1.json` | db-schema | — | `docs/platform/central_db_schema.v1.json` |

`manifest.json` is the **single artifact SSOT** — the one place that declares
which files belong to the package, their kind, whether codegen consumes them,
and the canonical `docsSource` they mirror.

## Single-writer / SSOT model

```
 docs/api/*.openapi.json            ← scripts/export_session_api_schemas.py  (Python, canonical)
 docs/platform/central_db_schema..  ← hand-maintained contract source        (canonical)
            │
            │  node scripts/sync.mjs   (the ONLY writer of the mirror)
            ▼
 packages/api-artifacts/artifacts/*.json   ← byte-identical mirror (committed, sealed)
            │
            │  import { OPENAPI_SPECS } from '@fcc/api-artifacts'
            ▼
 apps/web/scripts/codegen.mjs  → src/api/generated/*.types.ts
```

No file has two writers: `docs/` is written by its own canonical tool, the
mirror only by `scripts/sync.mjs`. The mirror is kept **byte-identical** to its
`docsSource` and is sealed by:

- `node scripts/sync.mjs --check` — node/CI drift gate (runs in `frontend-build.yml`).
- `tests/test_api_artifacts_package.py` — pytest drift gate (repo's seal medium).

## Programmatic API

```js
import { ARTIFACTS, OPENAPI_SPECS, resolveArtifact, loadArtifact, MANIFEST } from '@fcc/api-artifacts';

OPENAPI_SPECS;                       // codegen specs with absolute path + typesBasename
resolveArtifact('central-db-schema'); // absolute path
loadArtifact('platform-api');         // parsed JSON
```

`apps/web` currently consumes this package by **relative path** from
`scripts/codegen.mjs` (no install/lockfile churn). A bare-specifier
`@fcc/api-artifacts` `file:` devDependency in `apps/web/package.json` is
deferred to the repo-split milestone: it requires regenerating
`apps/web/package-lock.json` (so `npm ci` stays in sync), which is a separate
**staging gate**. Relative-path consumption already gives codegen the single
artifact SSOT without that coupling.

## Re-mirror after a contract change

```bash
# 1. regenerate canonical OpenAPI from backend SSOT
python scripts/export_session_api_schemas.py
# 2. re-mirror into the package
node packages/api-artifacts/scripts/sync.mjs
# 3. regenerate TS clients
cd apps/web && npm run codegen
```

## Publishing (staging / manual gate)

`npm publish` is intentionally **not** automated here. This package is `private`
inside the monorepo. Actual publishing belongs to the repo-split milestone
(`docs/architecture/repository_split_adr.md`), where `private` is lifted,
versioning/CI publish is added, and `docsSource` is dropped (artifacts become
self-contained). Until then this package is consumed locally only.
