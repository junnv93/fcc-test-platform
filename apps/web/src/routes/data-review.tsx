import { useT } from '@/i18n';

/**
 * `/data-review` — 데이터 리뷰. **목업이다.**
 *
 * ┌─ 목적 ────────────────────────────────────────────────────────────────┐
 * │ 누가   측정 결과를 «검토»하는 사람 (측정한 사람과 같을 수도 다를 수도) │
 * │ 언제   성적서로 넘기기 «전», 값이 말이 되는지 보는 자리                │
 * │ 무엇에 답하나 — 아직 아무것도. 자리만 잡아 둔 것이다.                  │
 * └──────────────────────────────────────────────────────────────────────┘
 *
 * ⚠️ 「진행률」과 「데이터 리뷰」는 다른 질문이다. 진행률은 «얼마나 했나»이고
 * 리뷰는 «한 것이 맞나»이다. 앞의 것은 세면 되고 뒤의 것은 봐야 한다 — 마진이
 * 아슬아슬한 것, 같은 조건을 두 번 쟀는데 값이 벌어진 것, 판정은 적합인데
 * 스펙 경계에 붙어 있는 것. 그 셋은 개수로는 보이지 않는다.
 *
 * ⚠️ 비어 있는 것은 미완성이 아니라 «선언»이다. 이 화면이 채워지려면 측정값
 * 자체(마진 · 스펙 한계 · 재측정 간 편차)를 주는 읽기가 필요한데, 오늘 그것이
 * 없다. 커버리지는 판정(`latest_verdict`)과 시각까지만 준다.
 * 각 칸에 「무엇이 여기 올 것인가」를 글로 적어 그 빈자리가 다음 사람에게
 * 그대로 전달되게 한다.
 */
export function DataReviewRoute(): JSX.Element {
  const { t } = useT();

  /* 실제 위젯이 붙을 때 하나씩 이 배열에서 빠져나가고, 배열이 비면 목업이
     끝난 것이다. */
  const slots = ['margin', 'spread', 'borderline', 'outlier', 'coverage', 'sign'] as const;

  return (
    <section className="console" aria-labelledby="data-review-heading">
      <header className="console__head">
        <p className="eyebrow">{t('routes.dataReview.eyebrow')}</p>
        <h1 className="console__title" id="data-review-heading">
          {t('routes.dataReview.title')}
        </h1>
        <p className="console__lede">{t('routes.dataReview.lede')}</p>
      </header>

      {/* 목업이라는 사실을 화면이 «스스로» 말한다. 주석에만 적으면 화면을 보는
          사람에게는 도달하지 않는다. */}
      <p className="mockup-banner" data-testid="data-review-mockup">
        {t('routes.dataReview.mockupNote')}
      </p>

      <div className="mockup-grid" data-testid="data-review-slots">
        {slots.map((slot) => (
          <section className="mockup-slot" key={slot}>
            <h2 className="mockup-slot__title">
              {t(`routes.dataReview.slots.${slot}.title`)}
            </h2>
            <p className="mockup-slot__body">{t(`routes.dataReview.slots.${slot}.body`)}</p>
            <span className="mockup-slot__tag">{t('routes.dataReview.noContract')}</span>
          </section>
        ))}
      </div>
    </section>
  );
}

export default DataReviewRoute;
