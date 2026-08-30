import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';

import { fetchProjectProgress } from '@/api/platform-client';
import { queryKeys, REFETCH_STRATEGIES } from '@/api/query-config';
import { useT } from '@/i18n';
import { formatPercent } from '@/shared/percent-display';
import { isValidProjectId } from '@/shared/project-id';
import { progressBadgeStatus, rollupForArea } from '@/shared/project-progress';
import { ROUTE_PATHS } from '@/shared/route-links';
import { type WorkbenchArea, WORKBENCH_AREAS } from '@/shared/workbench-areas';
import {
  describeApiError,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionBand,
  StatusBadge,
} from '@/ui';

import type { ProgressBucketEnvelope } from '@/api/platform-client';

/**
 * 분야(시험 분야) 선택 — ADR-0017 Phase 2 진입 단계 (2026-06-22). ★진입 워크플로우 2단계.
 *
 * 비전 흐름: 로그인 → 프로젝트(모델) 선택 → **분야 선택** → 플랜 → 측정. "내 프로젝트"
 * 에서 프로젝트를 고르면 이 화면으로 와 4개 분야(Unlicensed Conducted / Unlicensed
 * Radiated / mmWave / Licensed Conducted)를 1급 선택지로 보여준다.
 *
 * SSOT: 분야 목록 = `@/shared/workbench-areas`(provider_id 와 별도 키 공간 — ADR-0010).
 * 배포된 provider 분야만 선택 가능(available), 나머지는 '준비 중'(planned, 비활성). 분야를
 * 고르면 `?project=<id>&area=<areaId>` 컨텍스트를 이후 화면(진척 대시보드)에 전파.
 *
 * 진행률 배지(Phase 6 wiring, 2026-07-03): available 분야 카드는 프로젝트 진행률을 1회
 * fetch(`GET /platform/projects/{id}/progress`, N+1 없음)해 분야별 시간가중 % 를 배지로
 * 보인다. 정직 규칙은 `@/shared/project-progress` SSOT 재사용 — percent null(priced 시간
 * 없음) → "시간 미설정"(가짜 0% 금지). planned(미배포) 분야는 배지를 안 붙인다("준비 중"
 * 유지) — 배지 부재를 고장/빈값으로 오도하지 않기 위해.
 *
 * 오류 표면(W4-9 M2.4 정정, 2026-08-01): 진행률 fetch 가 실패하면 `@/ui/ErrorState`(기존
 * 프리미티브 재사용, 신규 패턴 금지)로 사용자에게 실패를 보인다 — 카드 목록은 계속 렌더되고
 * (블로킹 아님) 각 카드는 fetch-전 가용성 배지로 자연 저하한다(허위 % 금지, 위 규칙과 동일
 * SSOT). 이전에는 `progress.isError` 를 어디에서도 읽지 않아 실패가 완전히 침묵했다.
 *
 * 인가: 페이지가 RequireAuth 하위 + 분야 선택은 순수 내비게이션이라 별도 permission 게이트
 * 없음(다운스트림 화면이 자체 게이트). 프로젝트 미선택 시 "내 프로젝트" 로 안내.
 */
function FieldsRoute(): JSX.Element {
  const { t } = useT();
  const [searchParams] = useSearchParams();
  const projectId = (searchParams.get('project') ?? '').trim();
  const hasProject = isValidProjectId(projectId);

  // 분야별 진행률 배지 — 프로젝트 1회 fetch 로 전 분야 rollup(N+1 없음, D4). 진척
  // 대시보드(progress.tsx)와 같은 query key/정직 규칙을 공유한다. 프로젝트 미선택 시
  // 비활성(진척 endpoint 는 project 스코프).
  const progress = useQuery({
    queryKey: queryKeys.project.progress(projectId),
    queryFn: () => fetchProjectProgress(projectId),
    enabled: hasProject,
    ...REFETCH_STRATEGIES.NORMAL,
  });
  const rollups: readonly ProgressBucketEnvelope[] = progress.data ?? [];

  return (
    <section className="fields" aria-labelledby="fields-heading">
      <PageHeader
        title={t('routes.fields.title')}
        titleId="fields-heading"
        description={t('routes.fields.description')}
      />

      <FieldsWorkbenchOverview />

      {!hasProject && (
        <EmptyState
          testId="fields-no-project"
          title={t('routes.fields.noProjectTitle')}
          description={t('routes.fields.noProjectDescription')}
          action={
            <Link to="/my-projects" data-testid="fields-pick-project">
              {t('routes.fields.pickProject')}
            </Link>
          }
        />
      )}

      {hasProject && (
        <div className="fields-workbench" data-testid="fields-workbench">
          <div className="fields-workbench__main">
            <section className="fields-workbench-panel" aria-labelledby="fields-select-heading">
              <SectionBand
                title={t('routes.fields.selectSection')}
                titleId="fields-select-heading"
              />
              {progress.isError && (
                <ErrorState
                  testId="field-progress-error"
                  message={describeApiError(progress.error, 'platform', {
                    forbidden: t('routes.fields.progress.forbidden'),
                    network: t('routes.fields.progress.network'),
                    default: t('routes.fields.progress.failed'),
                  })}
                />
              )}
              <ul className="field-card-list" data-testid="field-card-list">
                {WORKBENCH_AREAS.map((area) => (
                  <FieldCard
                    key={area.id}
                    area={area}
                    projectId={projectId}
                    rollups={rollups}
                    progressReady={progress.isSuccess}
                  />
                ))}
              </ul>
            </section>
          </div>

          <aside
            className="fields-workbench__rail"
            aria-labelledby="fields-context-heading"
            data-testid="fields-context-panel"
          >
            <section className="fields-context">
              <SectionBand
                title={t('routes.fields.contextSection')}
                titleId="fields-context-heading"
              />
              <dl className="fields-context__list">
                <dt>{t('routes.fields.projectLabel')}</dt>
                <dd data-testid="fields-project-context">{projectId}</dd>
                <dt>{t('routes.fields.availableLabel')}</dt>
                <dd data-testid="fields-available-count">
                  {WORKBENCH_AREAS.filter((area) => area.status === 'available').length}
                </dd>
                <dt>{t('routes.fields.plannedLabel')}</dt>
                <dd data-testid="fields-planned-count">
                  {WORKBENCH_AREAS.filter((area) => area.status === 'planned').length}
                </dd>
              </dl>
              <p className="section-hint">{t('routes.fields.contextHint')}</p>
            </section>
          </aside>
        </div>
      )}
    </section>
  );
}

/** Build a `?project=&area=` deep-link. Both the progress (primary) and coverage
 *  (secondary) entry links carry the same area context forward. */
function fieldScopedHref(path: string, projectId: string, areaId: string): string {
  return `${path}?project=${encodeURIComponent(projectId)}&area=${encodeURIComponent(areaId)}`;
}

/**
 * One field (workbench area) entry card.
 *
 * available: primary link → 진척 대시보드(`/progress?project=&area=`, D3), 진행률
 * 배지(shared SSOT), + secondary coverage 링크(`/projects`). planned: 비활성 "준비 중"
 * (진행률 배지 미표시 — 배지 부재를 고장으로 오도 금지).
 */
function FieldCard({
  area,
  projectId,
  rollups,
  progressReady,
}: {
  readonly area: WorkbenchArea;
  readonly projectId: string;
  readonly rollups: readonly ProgressBucketEnvelope[];
  readonly progressReady: boolean;
}): JSX.Element {
  const { t } = useT();
  const label = t(area.labelKey);
  const blurb = t(area.blurbKey);

  if (area.status !== 'available') {
    // planned(미배포) — 진행률 배지 미표시, "준비 중" 유지.
    return (
      <li className="field-card" data-testid="field-card" data-area={area.id}>
        <div
          className="field-card__disabled"
          data-testid="field-card-disabled"
          aria-disabled="true"
          title={t('routes.fields.plannedTitle')}
        >
          <span className="field-card__label" data-testid="field-label">
            {label}
          </span>
          <StatusBadge
            status="draft"
            label={t('routes.fields.statusPlanned')}
            testId="field-status"
          />
          <span className="field-card__blurb">{blurb}</span>
        </div>
      </li>
    );
  }

  // available — 진행률 fetch 성공 전에는 가용성 배지, 성공 후에는 분야 진행률 배지
  // (percent null → "시간 미설정", 가짜 0% 금지 — shared SSOT).
  //
  // 숫자 렌더는 `shared/percent-display::formatPercent` 위임 (W2-C M5, 2026-07-28).
  // 옛 로컬 `Math.round(pct)` 는 99.6 → "100%"(남은 일이 있는데 완료 단정) / 0.4 →
  // "0%"(시작했는데 미착수 단정) 경계 거짓말을 만들었다. SSOT 는 두 경계 동치를
  // 반올림보다 먼저 판정하므로 그 표현이 구조적으로 불가능하다. 이 사이트가 W2 를
  // 관통한 백분율 부채의 마지막 항목이었다(allowlist → 0).
  //
  // tone(`progressBadgeStatus`)은 계속 **원시값** pct 를 받는다 — 반올림된 값으로
  // tone 을 정하면 W2-B 가 상환한 ChamberProgressBar 의 이중 거짓말(라벨 100% +
  // pass 팔레트)이 재발한다. null 분기("시간 미설정")도 그대로 — 진행률 자체가
  // 없는 것과 0% 는 다른 사실이다.
  let badge: JSX.Element;
  if (progressReady) {
    const totals = rollupForArea(rollups, area.id);
    const pct = totals?.percent ?? null;
    badge = (
      <StatusBadge
        status={progressBadgeStatus(pct)}
        label={pct === null ? t('routes.fields.progressUnset') : formatPercent(pct)}
        testId="field-progress"
        title={t('routes.fields.progressBadgeTitle')}
      />
    );
  } else {
    badge = (
      <StatusBadge status="pass" label={t('routes.fields.statusAvailable')} testId="field-status" />
    );
  }

  return (
    <li className="field-card" data-testid="field-card" data-area={area.id}>
      <Link
        to={fieldScopedHref(ROUTE_PATHS.progress, projectId, area.id)}
        className="field-card__link"
        data-testid="field-card-link"
      >
        <span className="field-card__label" data-testid="field-label">
          {label}
        </span>
        {badge}
        <span className="field-card__blurb">{blurb}</span>
      </Link>
      {/* Secondary coverage action. Reuses the card-action styling SSOT
          (`project-card__action*`, identical entry-card affordance in
          my-projects) plus a field-specific hook class for future targeting —
          global.css is not in this change's scope. */}
      <div className="field-card__actions project-card__actions" data-testid="field-card-actions">
        <Link
          to={fieldScopedHref(ROUTE_PATHS.projects, projectId, area.id)}
          className="field-card__action project-card__action"
          data-testid="field-card-coverage"
        >
          {t('routes.fields.coverageAction')}
        </Link>
      </div>
    </li>
  );
}

function FieldsWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="fields-workbench-overview"
      aria-label={t('routes.fields.workbenchNavAria')}
      data-testid="fields-workbench-overview"
    >
      <a className="fields-workbench-overview__item" href="#fields-select-heading">
        <span className="fields-workbench-overview__label">{t('routes.fields.stepChoose')}</span>
        <span className="fields-workbench-overview__detail">
          {t('routes.fields.stepChooseDetail')}
        </span>
      </a>
      <a className="fields-workbench-overview__item" href="#fields-context-heading">
        <span className="fields-workbench-overview__label">{t('routes.fields.stepContext')}</span>
        <span className="fields-workbench-overview__detail">
          {t('routes.fields.stepContextDetail')}
        </span>
      </a>
      <Link className="fields-workbench-overview__item" to="/my-projects">
        <span className="fields-workbench-overview__label">{t('routes.fields.stepProject')}</span>
        <span className="fields-workbench-overview__detail">
          {t('routes.fields.stepProjectDetail')}
        </span>
      </Link>
    </nav>
  );
}

export default FieldsRoute;
export { FieldsRoute };
