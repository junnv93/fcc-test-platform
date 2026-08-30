import { PERMISSION_TEST_PLAN_READ } from '@/api/permissions';
import { RequirePermission } from '@/auth/route-guard';
import { useT } from '@/i18n';
import { PageHeader } from '@/ui';

import { TestPlansWorkbench } from './TestPlansWorkbench';

/**
 * 시험 항목표 — test-plan draft authoring + publish (멀티챔버 P6 + P8, headless API).
 *
 * 시험원이 프로젝트별 테스트플랜 draft 를 **웹에서 직접 작성·편집**하고 발행한다("웹이
 * 메인 작업대"). draft 목록/상세 조회 위에, P8 에서 작성(create)·행 추가(add-row)·행
 * 삭제(remove-row)·검증(validate) authoring surface 를 더해 백엔드 headless draft API
 * 를 모두 typed generated client 로 소비한다:
 *   - 목록  `GET    .../test-plan/drafts`             (test_plan:read)
 *   - 상세  `GET    .../test-plan/drafts/{id}`         (test_plan:read)
 *   - 작성  `POST   .../test-plan/drafts`             (test_plan:author)
 *   - 행추가 `POST   .../test-plan/drafts/{id}/rows`    (test_plan:author)
 *   - 행삭제 `DELETE .../test-plan/drafts/{id}/rows/{n}` (test_plan:author)
 *   - 검증  `POST   .../test-plan/drafts/{id}/validate` (test_plan:read)
 *   - 발행  `POST   .../test-plan/drafts/{id}/publish`  (test_plan:author)
 *
 * 발행/작성 성공 시 목록 쿼리키와(해당 시) 선택된 draft 의 상세 쿼리키를 **함께**
 * invalidate 하여 목록 요약(status·row_count)과 상세 뷰가 같은 트랜잭션으로 갱신되게
 * 한다(둘이 같은 `queryKeys.testPlans.*` factory 호출이라 키 드리프트가 구조적으로 불가능).
 *
 * RBAC: 조회·검증은 `test_plan:read`, 작성/행편집/발행(write)은 `test_plan:author` 로
 * 각각 게이트한다(백엔드 `HEADLESS_API_PERMISSIONS` SSOT 미러 — 프론트 enum 박기 금지).
 * 행 추가/삭제는 추가로 DRAFT 상태(editable)에서만 노출한다(발행/보관된 draft 는 단말 —
 * 서버 409 를 UI 가 미리 차단). 서버는 배포 계층 trusted-header 로 재차 강제한다.
 *
 * C4 (route-component-decomposition): 이 화면은 route 하위 디렉토리로 분해되어 있다.
 * 본 entrypoint 는 페이지 셸(`PageHeader` + `test_plan:read` 게이트)만 보유하고, 목록
 * (`DraftRow`)·상세(`DraftDetail`)·행 항목(`DraftRowItem`)·검증 결과(`ValidateResult`)·
 * 행 추가 폼(`AddRowForm`) 등 자연 컴포넌트와 `orDash`/`trimToNull`/`parseCapabilityPath`
 * 유틸은 형제 모듈에 위치한다. API 호출·query key·권한·i18n key 는 분해 전과 byte-identical.
 */

/** Re-exported for unit testing — the predicate's single source of truth lives
 *  in `./status`, this entrypoint preserves the public import path. */
export { isPublishableDraft } from './status';

export function TestPlansRoute(): JSX.Element {
  const { t } = useT();
  return (
    <div className="test-plans">
      <section aria-labelledby="test-plans-heading">
        <PageHeader
          title={t('routes.testPlans.pageTitle')}
          titleId="test-plans-heading"
          description={t('routes.testPlans.pageDescription')}
        />
      </section>
      <RequirePermission permission={PERMISSION_TEST_PLAN_READ}>
        <TestPlansWorkbenchOverview />
        <TestPlansWorkbench />
      </RequirePermission>
    </div>
  );
}

function TestPlansWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="test-plans-workbench-overview"
      aria-label={t('routes.testPlans.workbenchNavAria')}
      data-testid="test-plans-workbench-overview"
    >
      <a className="test-plans-workbench-overview__item" href="#test-plans-project-heading">
        <span className="test-plans-workbench-overview__label">
          {t('routes.testPlans.stepProject')}
        </span>
        <span className="test-plans-workbench-overview__detail">
          {t('routes.testPlans.stepProjectDetail')}
        </span>
      </a>
      <a className="test-plans-workbench-overview__item" href="#test-plans-drafts-heading">
        <span className="test-plans-workbench-overview__label">
          {t('routes.testPlans.stepDrafts')}
        </span>
        <span className="test-plans-workbench-overview__detail">
          {t('routes.testPlans.stepDraftsDetail')}
        </span>
      </a>
      <a className="test-plans-workbench-overview__item" href="#test-plans-next-heading">
        <span className="test-plans-workbench-overview__label">
          {t('routes.testPlans.stepNext')}
        </span>
        <span className="test-plans-workbench-overview__detail">
          {t('routes.testPlans.stepNextDetail')}
        </span>
      </a>
    </nav>
  );
}

export default TestPlansRoute;
