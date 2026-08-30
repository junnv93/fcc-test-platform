import { PERMISSION_PLATFORM_READ } from '@/api/permissions';
import { RequirePermission } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { PageHeader } from '@/ui';

import { ChambersWorkbench } from './ChambersWorkbench';

/**
 * 시험 챔버 — chamber availability + distributed remote measurement (멀티챔버 P6).
 *
 * 타깃 아키텍처상 챔버는 각각 독립 PC 노드(분석기/단말/스위치 보유)이고 웹은 메인
 * 작업대다. 이 화면은 (1) 중앙 `chamber_availability` view 를 읽어 각 챔버의 가용성
 * (idle/in_use/offline) 을 한눈에 보여주고, (2) idle 챔버에 대해 중앙 프록시(P5)로
 * 측정을 원격 시작하고 진행을 폴링한다. 브라우저는 노드를 직접 호출하지 않는다 —
 * 허브(`POST /platform/chambers/{id}/measurements`)가 노드 Session API 로 forward 한다.
 *
 * RBAC: 가용성 읽기는 `platform:read`, 측정 시작(write)은 `platform:claim` 으로 각각
 * 게이트한다(백엔드 `PLATFORM_API_PERMISSIONS` SSOT 미러 — 프론트 enum 박기 금지).
 * 진행 폴링은 읽기(`platform:read`)다. 발행 플랜 datalist 제안은 headless
 * `GET .../test-plan/publications`(백엔드 `test_plan:read` 게이트)에서 오므로 그 조회는
 * `platform:claim` 이 아니라 별도 `test_plan:read` 권한으로 게이트한다 — chamber 운영자가
 * 보유하는 프로젝트 스코프 역할(`project_viewer`/`project_engineer`/`project_admin`)은
 * `rbac_role_grants` SSOT(`docs/platform/central_db_schema.v1.json`)상 `platform:read|claim|admin`
 * 만 부여하고 `test_plan:read` 는 포함하지 않으므로(headless 시드 역할 `platform_rbac.py`
 * 와 별개) "claim 사용자는 항상 datalist 를 본다" 가정이 거짓이기 때문. 권한이 없으면 조회를 건너뛰고
 * (round-trip 0) datalist 없이 자유 텍스트 수동 입력만 남긴다(측정 시작 흐름은 불변).
 *
 * Phase 6 의 "원격 측정" 작업 흐름은 본 화면의 측정 시작/진행에 통합한다(기존
 * `/control` "원격 측정"(단일 로컬 세션 직접 제어)과 라벨 충돌을 피하고, 분산 모델을
 * 챔버 가용성과 같은 맥락에서 노출).
 *
 * C4 (route-component-decomposition): 이 화면은 route 하위 디렉토리로 분해되어 있다.
 * 본 entrypoint 는 페이지 셸(`PageHeader` + `platform:read` 게이트)만 보유하고,
 * fleet 요약/실행 중 진행/가용성 표/측정 시작 등 자연 컴포넌트와 `orDash`·상태 라벨
 * 유틸은 형제 모듈에 위치한다. 데이터 소스(쿼리/폴링)는 분해 전과 byte-identical.
 */

/** Re-exported for unit testing — the predicate's single source of truth lives
 *  in `./status`, this entrypoint preserves the public import path. */
export { isStartableChamber } from './status';

export function ChambersRoute(): JSX.Element {
  const { t } = useT();
  return (
    <section className="chambers" aria-labelledby="chambers-heading">
      <PageHeader
        title={t('routes.chambers.pageTitle')}
        titleId="chambers-heading"
        description={t('routes.chambers.pageDescription')}
      />
      <RequirePermission permission={PERMISSION_PLATFORM_READ}>
        <ChambersWorkbenchOverview />
        <ChambersWorkbench />
      </RequirePermission>
    </section>
  );
}

function ChambersWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="chambers-workbench-overview"
      aria-label={t('routes.chambers.workbenchNavAria')}
      data-testid="chambers-workbench-overview"
    >
      <a className="chambers-workbench-overview__item" href="#chambers-fleet-heading">
        <span className="chambers-workbench-overview__label">{t('routes.chambers.stepFleet')}</span>
        <span className="chambers-workbench-overview__detail">
          {t('routes.chambers.stepFleetDetail')}
        </span>
      </a>
      <a className="chambers-workbench-overview__item" href="#chambers-availability-heading">
        <span className="chambers-workbench-overview__label">
          {t('routes.chambers.stepAvailability')}
        </span>
        <span className="chambers-workbench-overview__detail">
          {t('routes.chambers.stepAvailabilityDetail')}
        </span>
      </a>
      <a className="chambers-workbench-overview__item" href="#chambers-next-heading">
        <span className="chambers-workbench-overview__label">{t('routes.chambers.stepNext')}</span>
        <span className="chambers-workbench-overview__detail">
          {t('routes.chambers.stepNextDetail')}
        </span>
      </a>
    </nav>
  );
}

export default ChambersRoute;
