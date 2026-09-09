import { Link } from 'react-router-dom';

import { useT } from '@/i18n';
import { ROUTE_PATHS } from '@/shared/route-links';

/**
 * `/correction-factors` — 보정값(FACTOR). **목업이다.**
 *
 * ┌─ 목적 ────────────────────────────────────────────────────────────────┐
 * │ 누가   설비를 관리하는 사람 · 측정값을 검토하는 사람                   │
 * │ 언제   「이 측정에 어떤 보정이 들어갔나」를 물을 때                     │
 * │ 무엇에 답하나 — 아직 아무것도. 자리만 잡아 둔 것이다.                  │
 * └──────────────────────────────────────────────────────────────────────┘
 *
 * ⚠️ **새 저장소가 아니다.** 보정값은 이미 개념으로 존재한다 —
 * `reference_revisions` 의 `family = 'correction'` 이고, 그 원본은 「기준정보」
 * 화면이 소유한다(ADR: 측정 기준 데이터의 AUTHORITATIVE ORIGIN 은 중앙이고
 * 챔버 PC 의 `reference_catalog.db` 는 그 «읽기 전용 복제본»이다).
 *
 * 그러므로 이 화면이 해야 하는 일은 «보정값을 또 저장하는 것»이 아니라, 그
 * 한 갈래만 «보정값의 어휘로» 보여 주는 것이다. 케이블 손실·안테나 이득·스위치
 * 경로처럼 사람이 실제로 부르는 이름으로.
 *
 * ⚠️ 그리고 보정값은 «방(room) 단위»다. `reference_scope_policy` 가 그렇게
 * 갈라 둔다 — 케이블은 그 차폐실에 볼트로 박혀 있고, 안테나 이득은 시료를
 * 따라간다. 이 화면이 채워질 때 그 축을 지워서는 안 된다.
 */
export function CorrectionFactorsRoute(): JSX.Element {
  const { t } = useT();

  const slots = ['cable', 'antenna', 'switch', 'analyzer', 'validity', 'trace'] as const;

  return (
    <section className="console" aria-labelledby="correction-heading">
      <header className="console__head">
        <p className="eyebrow">{t('routes.correctionFactors.eyebrow')}</p>
        <h1 className="console__title" id="correction-heading">
          {t('routes.correctionFactors.title')}
        </h1>
        <p className="console__lede">{t('routes.correctionFactors.lede')}</p>
      </header>

      <p className="mockup-banner" data-testid="correction-mockup">
        {t('routes.correctionFactors.mockupNote')}{' '}
        <Link to={ROUTE_PATHS.referenceData}>
          {t('routes.correctionFactors.originLink')}
        </Link>
      </p>

      <div className="mockup-grid" data-testid="correction-slots">
        {slots.map((slot) => (
          <section className="mockup-slot" key={slot}>
            <h2 className="mockup-slot__title">
              {t(`routes.correctionFactors.slots.${slot}.title`)}
            </h2>
            <p className="mockup-slot__body">
              {t(`routes.correctionFactors.slots.${slot}.body`)}
            </p>
            <span className="mockup-slot__tag">
              {t('routes.correctionFactors.notWired')}
            </span>
          </section>
        ))}
      </div>
    </section>
  );
}

export default CorrectionFactorsRoute;
