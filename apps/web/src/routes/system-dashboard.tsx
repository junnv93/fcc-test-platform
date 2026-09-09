import { useT } from '@/i18n';

/**
 * `/system` — 시스템 대시보드. **목업이다.**
 *
 * ┌─ 목적 ────────────────────────────────────────────────────────────────┐
 * │ 누가   플랫폼을 «운영»하는 사람 (시험을 하는 사람이 아니다)            │
 * │ 언제   화면이 느리다 · 작업이 안 돈다 · 장비가 안 붙는다는 말을 들을 때│
 * │ 무엇에 답하나 — 아직 아무것도. 자리만 잡아 둔 것이다.                  │
 * └──────────────────────────────────────────────────────────────────────┘
 *
 * ⚠️ 「전체 총괄」을 복제했지만 «내용은 비웠다». 그쪽을 그대로 렌더하면 같은
 * 숫자가 두 경로에 살게 되고, 목업이 목업으로 보이지 않는다 — 누군가 이 화면을
 * 보고 판단을 내리는 순간 그것이 가장 비싼 실수가 된다. 그래서 뼈대(자리·제목·
 * 패널 구획)만 두고, 각 칸에는 「무엇이 여기 올 것인가」를 글로 적었다.
 *
 * ⚠️ 그리고 그 칸들은 «읽기 계약이 없어서» 비어 있는 것이지, 만들다 만 것이
 * 아니다. 큐 적체·응답 지연·챔버 연결 상태를 주는 읽기가 아직 없다. 값을
 * 그럴듯하게 채워 넣는 것은 이 저장소가 가장 하지 말라고 적어 둔 일이라
 * (§StatTile 의 `unavailable` 과 같은 판단), 없는 것은 없다고 적는다.
 */
export function SystemDashboardRoute(): JSX.Element {
  const { t } = useT();

  /* 목업의 칸들. 배열인 이유는 이것이 «목록»이기 때문이다 — 실제 위젯이 붙을
     때 하나씩 이 배열에서 빠져나가고, 배열이 비면 목업이 끝난 것이다. */
  const slots = [
    'ingest',
    'queue',
    'chambers',
    'storage',
    'auth',
    'errors',
  ] as const;

  return (
    <section className="console" aria-labelledby="system-dashboard-heading">
      <header className="console__head">
        <p className="eyebrow">{t('routes.systemDashboard.eyebrow')}</p>
        <h1 className="console__title" id="system-dashboard-heading">
          {t('routes.systemDashboard.title')}
        </h1>
        <p className="console__lede">{t('routes.systemDashboard.lede')}</p>
      </header>

      {/* 목업이라는 사실을 화면이 «스스로» 말한다. 주석에만 적으면 화면을 보는
          사람에게는 도달하지 않는다. */}
      <p className="mockup-banner" data-testid="system-dashboard-mockup">
        {t('routes.systemDashboard.mockupNote')}
      </p>

      <div className="mockup-grid" data-testid="system-dashboard-slots">
        {slots.map((slot) => (
          <section className="mockup-slot" key={slot}>
            <h2 className="mockup-slot__title">
              {t(`routes.systemDashboard.slots.${slot}.title`)}
            </h2>
            <p className="mockup-slot__body">
              {t(`routes.systemDashboard.slots.${slot}.body`)}
            </p>
            <span className="mockup-slot__tag">{t('routes.systemDashboard.noContract')}</span>
          </section>
        ))}
      </div>
    </section>
  );
}

export default SystemDashboardRoute;
