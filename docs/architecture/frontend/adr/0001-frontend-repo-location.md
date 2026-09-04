# ADR-0001: Frontend repo location

**Status**: Accepted — 구현됨

> ⚠️ **상태 갱신 2026-09-05** — 원문은 `Proposed` 였으나 **이 결정은 이미 구현돼 있다.**
> `apps/web/` 에 실재. 라우트 49파일 / 19,542줄, UI 26개.
> 재구현하지 말 것. 현황은 `docs/architecture/2026-09-04-플랫폼-리팩토링-설계서.md` 참조.

**Date**: 2026-05-23
**Deciders**: shared web/platform maintainers + user

## Context

FCC frontend platform 의 production 코드 (React + TypeScript + Vite) 를 어디에 보관할지 결정해야 한다. 옵션:

1. **Monorepo `apps/web/`**: 본 `FCC_mobile_test_automation` 모노리포 내부에 `apps/web/` 디렉토리 신설. backend Python + frontend TS 같은 repo 에 공존.
2. **별도 `fcc-test-platform` repo**: 새 GitHub repo 신설. backend 와 frontend 가 별도 repo 에서 독립 lifecycle.

이전 contract-only sprint (revert 됨) 가 **fcc-test-platform repo 가 별도** 라고 가정하고 contract markdown 만 작성했으나, **그 repo 가 실제로 존재하지 않아 runnable artifact 0** 인 임시방편 결과. 본 ADR 이 그 root cause 결정을 정한다.

### Constraints

- `docs/architecture/repository_split_adr.md` 는 5 repo lane (fcc-test-contracts / fcc-test-platform / fcc-unlicensed-headless / etc) 로 계획됨 — 본 ADR 은 그 일부를 단기적으로 reconcile
- backend 는 Python (PySide6 GUI + FastAPI Headless/Session API) — frontend 와 언어/build tool 다름
- 본 모노리포는 이미 `web/platform-shell/` (919 LOC vanilla JS prototype) 보유 — reference 가치
- 본 모노리포 git history 가 매우 풍부 — backend 변경과 frontend 변경의 cross-reference 가 한 repo 에 보관 시 추적 유리

## Decision

**Phase 1 (S1~S10): Monorepo `apps/web/` 채택** + **Phase 2 (S11+): 별도 repo 로 lift-out 옵션 보존**

근거:
1. **Runnable first**: 별도 repo 가 실제 존재하지 않으면 contract-only 임시방편 위험. 본 모노리포 `apps/web/` 으로 즉시 runnable.
2. **Strangler Fig pattern**: backend SQLite → PostgreSQL strangler 와 동일 패턴 — 한 repo 에서 incremental 전환 후, 별도 repo lift-out 은 충분한 코드량 + 안정 후.
3. **Cross-reference 유리**: WEB-FE 변경이 backend `application/headless/api_contracts.py` DTO 변경과 한 PR 에서 review 가능 — single PR 강제 SSOT 정합.
4. **OpenAPI artifact 정합**: `docs/api/session-api.openapi.json` (F-2-D3 SSOT) 가 본 모노리포 에 있으므로, codegen 이 같은 repo 안에서 동작 — cross-repo build dependency 0.

## Consequences

### Positive
- Runnable scaffold 1차 commit 이 본 모노리포에서 가능 (Sprint S1)
- backend DTO ↔ frontend type 자동 동기화 강제 (single PR review)
- OpenAPI codegen build script 가 same repo 내 작동 — CI 단순

### Negative
- 본 모노리포가 multi-language (Python + TypeScript) — root README + CONTRIBUTING 갱신 필요
- npm/node 의존성이 본 모노리포에 추가 — `.gitignore` `node_modules` + lock file 정책
- frontend 팀과 backend 팀이 다른 organization 일 때 access control 복잡 (해당 시 별도 repo lift-out 트리거)
- multi-language monorepo build tool 도입 검토 필요 (Turborepo / Nx — S0 에서 결정 — 별도 ADR 또는 본 ADR 의 fast-follow)

## Alternatives Considered

### Option A: 별도 `fcc-test-platform` repo 즉시 신설
- **rejected because**: 별도 repo 신설 권한 / GitHub organization 정책 / CI access token 등 administrative blocker. 또한 cross-repo OpenAPI artifact sync 가 추가 mechanism 필요 (git submodule / Renovate / hashed artifact). 단기 (S1~S10) 에 runnable 우선 - 즉시 차단.

### Option B: Nx / Turborepo monorepo tool 사용
- **deferred**: monorepo tool 자체가 추가 dependency + learning curve. Phase 1 은 plain Vite + npm workspaces 로 시작 — Phase 2 에서 tool 필요성 측정 후 도입. 즉 본 ADR 은 monorepo 자체 채택만 결정하고 tool 채택은 별도 ADR (예정).

### Option C: `web/platform-shell/` prototype 확장
- **rejected because**: vanilla JS / 단일 HTML / 919 LOC prototype 은 React + TS strict + RR + TanStack Query stack 으로 lift 불가. 새 `apps/web/` 디렉토리 시작이 더 깔끔. `web/platform-shell/` 은 reference 로 보존 (특히 OIDC PKCE 119 LOC).

## Revisit Conditions

1. **frontend 팀이 backend 팀과 별도 organization 으로 분리** → 별도 repo lift-out
2. **CI build time 이 monorepo 로 인해 backend 측 build 지연** (예: > 5 분) → tool 도입 (Nx / Turborepo) 또는 lift-out
3. **frontend release cadence 가 backend 와 다름** (예: frontend 일주일 1회 vs backend 일 5회) → 별도 repo 로 deployment lifecycle 분리
4. **개발자 수 6명 이상** + frontend 전용 4명 이상 → 별도 repo (Conway's law)

## References

- `docs/architecture/repository_split_adr.md` — 전체 5-repo split 계획
- `web/platform-shell/` — 919 LOC vanilla JS prototype (reference)
- [Strangler Fig pattern](https://martinfowler.com/bliki/StranglerFigApplication.html)
- [Trunk-Based Development](https://trunkbaseddevelopment.com/monorepos/)
