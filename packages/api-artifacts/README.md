# @fcc/api-artifacts

Bundle of the shared FCC API contract artifacts (B3 / P14). It packages the
cross-language contract artifacts so every backend/frontend consumes one
surface through a normal npm dependency.

> ⚠️ **상태 갱신 2026-09-05** — 원문은 「모노레포가 **분리되면**(when the monorepo
> is split) 계약 표면을 `fcc-test-contracts` 로 들어올릴 수 있다」는 **미래형**이었다.
> **그 분리는 이미 일어났다.** `fcc-test-contracts` 는 정식 레포이고 2026-09-04 부터
> **자기 OpenAPI 를 스스로 발행한다**(`d83ebee` — 「조립기가 저쪽에 있어 다섯 사본이
> 낡았다」). 이 패키지의 사본이 그 발행본과 어긋나지 않는지는 CI 의
> `api-artifacts mirror drift gate`(`scripts/sync.mjs --check`)가 판정한다.
>
> ⚠️ 원문이 가리키던 `docs/architecture/repository_split_adr.md` 는 **이 저장소에
> 없다**(깨진 링크였다). 분리의 현재 형상은 루트 `README.md` 와
> [`../../docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md`](../../docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md) §0 에 있다.

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
