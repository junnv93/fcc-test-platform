# Frontend Architecture Decision Records

이 디렉토리는 FCC frontend platform 의 **stable architecture decision** 을 보관한다. 각 ADR 은 결정의 context / decision / consequences / alternatives / revisit conditions 를 명시한다.

## ADR Index

| # | Title | Status |
|---|-------|--------|
| [0001](0001-frontend-repo-location.md) | Frontend repo location — monorepo `apps/web/` vs 별도 `fcc-test-platform` repo | Proposed |
| [0002](0002-stack-selection.md) | Stack selection — React + Vite + TypeScript + React Router + TanStack Query | Proposed |
| [0003](0003-openapi-ts-client-generator.md) | OpenAPI → TS client generator — `openapi-typescript` 채택 | Proposed |
| [0004](0004-distributed-tracing-sdk.md) | Distributed tracing frontend SDK — OpenTelemetry browser | Proposed |
| [0005](0005-central-db-read-model.md) | Central DB read model — PostgreSQL or SQLite-extended | Proposed |
| [0006](0006-observability-backend.md) | Observability backend — Sentry + OpenTelemetry collector | Proposed |

## ADR 형식

[Michael Nygard ADR template](https://github.com/joelparkerhenderson/architecture-decision-record/blob/main/locales/en/templates/decision-record-template-by-michael-nygard/index.md) 기반:

- **Status**: `Proposed | Accepted | Deprecated | Superseded by ADR-XXXX`
- **Context**: 결정이 필요한 배경 + constraint
- **Decision**: 선택한 옵션 + 1줄 요약
- **Consequences**: positive + negative 모두
- **Alternatives Considered**: 거부한 옵션 + 거부 사유
- **Revisit Conditions**: 재검토 트리거
- **References**: 외부 자료

## 본 디렉토리의 SSOT 정책

- ADR 본문은 **stable architecture decision** 만 — 구현 detail 은 별 contract markdown
- 결정이 outdated 될 때 `Status: Deprecated` 또는 `Superseded by ADR-XXXX` 로 표시 (삭제 금지 — 학습 자산)
- 새 ADR 번호는 sequential, gap 없음

## 본 디렉토리 vs `docs/architecture/frontend/` (parent)

| 본 디렉토리 (`adr/`) | 부모 디렉토리 |
|--------------------|--------------|
| **stable** architecture decision | implementation contract markdown |
| 거의 변경 없음 (revisit condition 시점) | sprint 진행 중 갱신 |
| Michael Nygard ADR 형식 | 자유 markdown |

## 본 ADR 시리즈의 배경

2026-05-23 contract-only sprint (`fcc-platform-frontend-foundation-6phase`) 가 사용자 자평으로 임시방편으로 판정되어 revert (commit cc96970). 그 학습에 기반한 architecture-first redesign 의 1단계로서 본 6 ADR 신설.

이전 sprint 의 결함:
- column literal 중복 (backend DTO ↔ frontend evidence)
- hardcoded magic number 다수 (TTL=600 / rows=1000 / render=1500 산출 근거 0)
- runnable scaffold 0 — contract markdown 만
- production checklist (Core Web Vitals / bundle size / security headers / i18n / a11y) 0
- distributed tracing frontend 0

본 ADR 6 개가 위 결함의 architectural root cause 를 정리하고, 후속 sprint S1~S12 의 SSOT base 가 된다.

## Roadmap

본 ADR 시리즈는 sprint roadmap (`.claude/exec-plans/archive/2026-05-23-fcc-frontend-architecture-first-redesign.md`) 의 S0 단계. S1~S12 가 본 ADR 의 decision 을 실행한다.
