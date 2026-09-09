import { useQuery } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';

import { headlessClient } from '@/api/headless-client';
import { fetchChambers } from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { fetchSessionInfo } from '@/api/session-client';
import { getRuntimeConfig } from '@/config/runtime';
import { useT } from '@/i18n';
import { chamberStatusLabel } from '@/routes/chambers/status';
import {
  BlockSkeleton,
  Button,
  Card,
  describeApiError,
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
  chamberStatusKind,
} from '@/ui';

/**
 * `/diagnostics` — 진단.
 *
 * ┌─ 목적 ────────────────────────────────────────────────────────────────┐
 * │ 누가   개발자 · 플랫폼을 «고치는» 사람 (시험을 하는 사람이 아니다)     │
 * │ 언제   「뭔가 안 붙는다 / 느리다」는 말을 들었을 때                    │
 * │ 무엇에 답하나  무엇이 · 언제 · 어느 버전에서 · 어느 «홉»에서 끊겼나    │
 * └──────────────────────────────────────────────────────────────────────┘
 *
 * ── 🔴 화면을 «합쳤다» (2026-09-09) ─────────────────────────────────────
 * 한때 셋으로 나누려 했다 — 총괄(업무) / 연결(붙어 있나) / 시스템 대시보드
 * (버티나). 개념으로는 깔끔한데 «인과»를 끊는다:
 *
 *     서버가 메모리 압박 → 하트비트 처리 지연 → 노드가 「오프라인」으로 보임
 *
 * 원인과 결과가 다른 탭에 있으면 그 사슬을 아무도 못 본다. 그래서 「붙어 있나」와
 * 「버티나」를 한 화면에 둔다. 업무 축인 «전체 총괄»만 별개다 — 그쪽은 시험이
 * 어떻게 가는지를 묻고 여기는 기계가 어떤지를 묻는다.
 *
 * ── 배치 ────────────────────────────────────────────────────────────────
 * 운영자가 가져온 모니터링 화면의 «골격»을 그대로 쓴다:
 *   제목 → 갱신 주기 한 줄 → KPI 넷 → 3열(각 열이 카드 스택).
 * ⚠️ 골격만 빌리고 어휘는 우리 것이다(3단 색 위계 · 간격 토큰 · tabular-nums).
 * 전문가용이라고 다른 디자인을 쓰기 시작하면 그때부터 두 벌을 유지하게 된다.
 * ⚠️ 그리고 참고 화면이 하는 «중복»은 따라가지 않는다 — 그쪽은 총요청·에러율을
 * KPI 와 카드에서 두 번 말한다. 여기 KPI 넷은 전부 아래 목록에 «줄 단위로는
 * 없는» 집계다.
 *
 * ── 개발자용 화면의 조건 ────────────────────────────────────────────────
 * 밀도가 아니라 «재현 가능성»이다. 이 화면에 있는 것만으로 문제를 재현하거나
 * 남에게 넘길 수 있어야 한다. 그래서 버전·핀이 위쪽이고, 오류 «원문»을 접지
 * 않고, 시각은 ISO 절대값이 먼저이며(상대값은 괄호), 「전체 복사」가 있다.
 * ⚠️ 복사는 쓰기가 아니라 «가져가기»라 화면이 뷰어라는 규칙을 깨지 않는다.
 *
 * ⚠️ 메뉴는 «설정» 안에 둔다. 시험원이 눌러서 `POLICY_CONFLICT` 나 오류 원문을
 * 보면 「시스템이 고장 났다」로 읽는다.
 *
 * ⚠️ 오늘 «있는» 데이터는 도달성 · 노드 · 런타임뿐이다. 중앙 서버 자원(CPU ·
 * 메모리 · DB 커넥션 · 요청/에러율)은 읽기가 **하나도 없다**. 그 칸들은 만들다
 * 만 것이 아니라 계약이 없어서 비어 있고, 각자 필요한 계약의 이름을 댄다 —
 * 그래서 이 화면은 동시에 «엔드포인트 명세서»다.
 */

/** 아직 계약이 없는 지표들. 배열인 이유는 이것이 «작업 목록»이기 때문이다 —
 *  읽기가 하나 생길 때마다 여기서 한 줄이 빠지고, 배열이 비면 끝난 것이다. */
const MISSING_METRICS = [
  'cpu',
  'memory',
  'disk',
  'dbPool',
  'dbSlow',
  'httpRate',
  'queue',
  'liveChannel',
] as const;

export function DiagnosticsRoute(): JSX.Element {
  const { t } = useT();
  const config = getRuntimeConfig();

  /* Session API 는 «챔버 PC 한 대»의 표면이다. 운영 배포는 중앙 서버이므로 그
     토폴로지에서는 false 이고, 호출하면 게이트웨이가 404 를 준다. `enabled` 가
     그것을 막는다(훅 자체는 무조건 호출한다 — Rules of Hooks).
     ⚠️ 그래서 «운영에서 이 칸이 비어 있는 것이 정상»이다. 화면이 그 사실을
     말하지 않으면 개발자가 정상을 장애로 읽는다. */
  const sessionApiEnabled = config.sessionApiEnabled;
  const info = useQuery({
    queryKey: queryKeys.session.info(),
    enabled: sessionApiEnabled,
    retry: false,
    queryFn: fetchSessionInfo,
  });

  /* 중앙 → 챔버 노드.
     ⚠️ 새 계약을 만들지 않았다 — 챔버 가용성 읽기가 이미 하트비트 · mode_verdict ·
     last_error 를 준다. 그런데 오늘 그 셋을 한자리에서 보는 화면이 없었다:
     `mode_verdict` 는 챔버 워크벤치 한 곳, `last_error` 는 관리자 패널 한 곳. */
  const chambers = useQuery({
    queryKey: queryKeys.chambers.list(),
    queryFn: fetchChambers,
    refetchInterval: 15_000,
  });

  /* 이 플랫폼은 «서버 하나»가 아니다 — ASGI 앱 셋(session · headless · platform)이
     한 게이트웨이 뒤에 경로로 갈려 있다. 「서버가 죽었다」는 말은 여기서 «어느
     앱이»까지 좁혀져야 한다.

     ⚠️ 왕복 시간은 새 계약 없이 잰다 — `performance.now()` 로 감싸면 된다. 이것이
     재는 것은 브라우저에서 그 서비스까지의 왕복이고, 진단이 알고 싶은 것이 정확히
     그것이다. 서버 «내부» 처리 시간은 서버가 스스로 말해야 하고 그 계약은 없다.
     ⚠️ `retry: false` — 진단에서 재시도는 거짓말이다. 세 번 만에 붙은 것을
     「붙음」이라 적으면 간헐 장애가 화면에서 사라진다. */
  const headless = useQuery({
    queryKey: ['diag-headless-health'],
    retry: false,
    refetchInterval: 15_000,
    queryFn: async () => {
      const started = performance.now();
      const { error, response } = await headlessClient.GET('/health', {});
      const ms = Math.round(performance.now() - started);
      if (error) throw new Error(`HTTP ${response.status}`);
      return { ms, status: response.status };
    },
  });

  const nodes = useMemo(() => chambers.data?.items ?? [], [chambers.data]);
  const online = nodes.filter((node) => node.status !== 'offline').length;
  const conflicts = nodes.filter((node) => node.mode_verdict === 'POLICY_CONFLICT').length;
  const faults = nodes.filter(
    (node) => node.last_error !== null && node.last_error !== undefined && node.last_error !== '',
  ).length;
  /** 서비스 셋 중 몇이 붙었나. ⚠️ Session API 는 이 토폴로지에서 «해당 없음»
   *  이므로 분모에서도 뺀다 — 없는 것을 「죽었다」로 세면 서버 파트가 늘 빨갛다. */
  const servicesTotal = sessionApiEnabled ? 3 : 2;
  const servicesUp =
    (chambers.isSuccess ? 1 : 0) +
    (headless.isSuccess ? 1 : 0) +
    (sessionApiEnabled && info.isSuccess ? 1 : 0);

  const freshest = nodes
    .map((node) => node.last_heartbeat_at ?? '')
    .filter((at) => at !== '')
    .sort()
    .at(-1);

  /** 시각을 «두 조각»으로 나눈다.
   *
   *  🔴 한 문자열로 두었더니 KPI 띠가 깨졌다. `2026-09-09T08:51:04.673Z (12초 전)`
   *  는 275px 짜리 두 줄이라, 그 칸만 큰 활자를 못 쓰고 11px 모노로 떨어졌다.
   *  그러면 한 줄에 선 네 칸 중 둘은 26px 그로테스크, 둘은 11px 모노가 되고
   *  카드 높이까지 64 / 77 로 갈린다(실측). 「값 하나를 못 넣어서 그 줄 전체의
   *  리듬을 버린」 셈이다.
   *
   *  ⚠️ 그래서 «자르는» 것이 아니라 «가른다» — 짧은 상대값은 값 줄이 가져가고
   *  긴 ISO 는 아래 보조 줄로 내린다. 개발자에게 ISO 절대값이 필요하다는 판단은
   *  그대로다(로그와 문자 단위로 대조한다). 사라지지 않고 자리만 바뀐다. */
  const rel = (iso: string | null | undefined): string => {
    if (iso == null || iso === '') return '—';
    const ms = Date.parse(iso);
    if (!Number.isFinite(ms)) return '—';
    const age = Math.max(0, Math.round((Date.now() - ms) / 1000));
    return age < 60
      ? t('routes.diagnostics.agoSeconds', { n: String(age) })
      : age < 3600
        ? t('routes.diagnostics.agoMinutes', { n: String(Math.floor(age / 60)) })
        : t('routes.diagnostics.agoHours', { n: String(Math.floor(age / 3600)) });
  };

  const abs = (iso: string | null | undefined): string => {
    if (iso == null || iso === '') return '—';
    const ms = Date.parse(iso);
    return Number.isFinite(ms) ? new Date(ms).toISOString() : iso;
  };

  /** 목록 안에서는 여전히 한 줄로 붙여 쓴다 — 거기서는 절대값이 먼저다. */
  const stamp = (iso: string | null | undefined): string =>
    iso == null || iso === '' ? '—' : `${abs(iso)} (${rel(iso)})`;

  const [copied, setCopied] = useState(false);
  const copyAll = useCallback(() => {
    const lines = [
      `# ${t('routes.diagnostics.title')} ${new Date().toISOString()}`,
      `env=${config.environmentName} build=${config.buildVersion} (${config.buildSha256.slice(0, 12)})`,
      `api=${config.apiBaseUrl} sessionApiEnabled=${String(sessionApiEnabled)}`,
      `nodes=${online}/${nodes.length} conflicts=${conflicts} faults=${faults}`,
      ...nodes.map(
        (node) =>
          `- ${node.chamber_id} status=${node.status} verdict=${node.mode_verdict ?? '-'} ` +
          `heartbeat=${node.last_heartbeat_at ?? '-'} error=${node.last_error ?? '-'}`,
      ),
    ];
    void navigator.clipboard?.writeText(lines.join('\n')).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 2000);
      },
      () => setCopied(false),
    );
  }, [config, nodes, online, conflicts, faults, sessionApiEnabled, t]);

  /** 한 홉. 참고 화면의 「서비스 건강 상태」 줄과 같은 모양이다 —
   *  이름 · 값 · 상태점, 그리고 필요하면 아랫줄에 원문. */
  const hop = (
    key: string,
    label: string,
    tone: 'ok' | 'bad' | 'warn' | 'none',
    value: string,
    meta: string,
    raw?: string,
  ): JSX.Element => (
    <li className="diag-hop" key={key} data-tone={tone}>
      <span className="diag-hop__dot" aria-hidden="true" />
      <span className="diag-hop__name">{label}</span>
      <span className="diag-hop__value">{value}</span>
      <span className="diag-hop__meta">{meta}</span>
      {raw !== undefined && <span className="diag-hop__raw">{raw}</span>}
    </li>
  );

  return (
    <section className="diagnostics" aria-labelledby="diagnostics-heading">
      <PageHeader
        title={t('routes.diagnostics.title')}
        titleId="diagnostics-heading"
        description={t('routes.diagnostics.description')}
      />

      <div className="diag-toolbar">
        <span className="diag-toolbar__note">{t('routes.diagnostics.refreshEvery')}</span>
        <Button variant="secondary" size="sm" onClick={copyAll} data-testid="diag-copy">
          {copied ? t('routes.diagnostics.copied') : t('routes.diagnostics.copyAll')}
        </Button>
      </div>

      {/* ══ 파트 ① 서버(중앙) ══════════════════════════
          🔴 이 화면은 «두 파트»다 — 서버, 그리고 그 서버에 붙은 TC.
          한때 열(런타임 / 도달성 / 자원)로 나눠 봤는데 그러면 「서버」가 세 카드에
          흔어져 조직 원리가 사라진다. 사람이 이 화면에 올 때 묻는 것은 둘 중
          하나다: 「서버가 이상한가」 아니면 「TC 가 이상한가」. 그 두 질문이
          그대로 두 파트다. */}
      <section className="diag-part" aria-labelledby="diag-server-heading">
        <div className="diag-part__head">
          <h2 className="diag-part__title" id="diag-server-heading">
            {t('routes.diagnostics.partServer')}
          </h2>
          <span className="diag-part__lede">{t('routes.diagnostics.partServerLede')}</span>
        </div>

        {/* ⚠️ KPI 는 아래 목록이 «줄 단위로는 말하지 않는» 집계만 십는다.
            참고 화면은 총요청·에러율을 위아래로 두 번 말하는데 그것은 요약이
            아니라 중복이다. */}
        <div className="diag-kpis" data-testid="diag-kpis-server">
          <Card as="section" className="diag-kpi">
            <span className="diag-kpi__label">{t('routes.diagnostics.kpiServices')}</span>
            <b className="diag-kpi__value" data-tone={servicesUp === servicesTotal ? 'ok' : 'bad'}>
              {`${servicesUp} / ${servicesTotal}`}
            </b>
          </Card>
          <Card as="section" className="diag-kpi">
            <span className="diag-kpi__label">{t('routes.diagnostics.kpiLatency')}</span>
            <b className="diag-kpi__value" data-tone={headless.isError ? 'bad' : 'ok'}>
              {headless.isSuccess
                ? t('routes.diagnostics.segMs', { ms: String(headless.data.ms) })
                : '\u2014'}
            </b>
          </Card>
          {/* ⚠️ 「읽기 없음」도 «같은 활자»다. 크기나 서체를 낮추면 그 칸이 다른
              종류의 물건으로 보이는데, 실제로는 옆 칸과 «같은 종류의 사실»이다 —
              하나는 관측됐고 하나는 아직 관측되지 않았을 뿐이다. 그 차이는
              색 한 단으로 충분하다. */}
          <Card as="section" className="diag-kpi" data-missing="true">
            <span className="diag-kpi__label">{t('routes.diagnostics.metrics.cpu')}</span>
            <b className="diag-kpi__value" data-tone="none">
              {t('routes.diagnostics.noRead')}
            </b>
          </Card>
          <Card as="section" className="diag-kpi" data-missing="true">
            <span className="diag-kpi__label">{t('routes.diagnostics.metrics.httpRate')}</span>
            <b className="diag-kpi__value" data-tone="none">
              {t('routes.diagnostics.noRead')}
            </b>
          </Card>
        </div>

        <div className="diag-cols">
          {/* 「지금 도는 게 뭐냐」 — 이 화면의 «전제»라 서버 파트의 첫 카드다. */}
          <Card as="section" className="diag-card" aria-labelledby="diag-runtime-heading">
            <h3 className="diag-card__title" id="diag-runtime-heading">
              {t('routes.diagnostics.runtimeHeading')}
            </h3>
            <dl className="diag-facts">
              <div>
                <dt>{t('routes.diagnostics.metricEnv')}</dt>
                <dd data-testid="env-name">{config.environmentName}</dd>
              </div>
              <div>
                <dt>{t('routes.diagnostics.metricBuild')}</dt>
                <dd data-testid="build-version">
                  {config.buildVersion} · {config.buildSha256.slice(0, 12)}
                </dd>
              </div>
              <div>
                <dt>{t('routes.diagnostics.metricBackend')}</dt>
                <dd className="diag-facts__wrap" data-testid="api-base-url">
                  {config.apiBaseUrl}
                </dd>
              </div>
              {/* ⚠️ 이 저장소에서 «실제로» 조용히 갈라지는 축이다 — 핀이
                  `pyproject.toml` 과 `requirements-central.txt` 두 곳에 있고
                  한쪽만 올리면 아무것도 빨개지지 않은 채 두 레인이 어긋난다. */}
              <div data-missing="true">
                <dt>{t('routes.diagnostics.metricKernelPin')}</dt>
                <dd title={t('routes.diagnostics.metricKernelPinNeed')}>
                  {t('routes.diagnostics.noRead')}
                </dd>
              </div>
              <div>
                <dt>{t('routes.diagnostics.authMode')}</dt>
                <dd>{config.authMode}</dd>
              </div>
              <div>
                <dt>{t('routes.diagnostics.oidcIssuer')}</dt>
                <dd className="diag-facts__wrap">{config.oidcIssuer}</dd>
              </div>
              <div>
                <dt>{t('routes.diagnostics.sessionApiFlag')}</dt>
                <dd>{String(sessionApiEnabled)}</dd>
              </div>
            </dl>
          </Card>

          {/* 이 플랫폼은 «서버 하나»가 아니다 — ASGI 앱 셋이 한 게이트웨이 뒤에
              경로로 갈려 있다. 「서버가 죽었다」는 여기서 «어느 앱이»까지 좀혀진다. */}
          <Card as="section" className="diag-card" aria-labelledby="diag-reach-heading">
            <h3 className="diag-card__title" id="diag-reach-heading">
              {t('routes.diagnostics.reachHeading')}
            </h3>
            <ul className="diag-hops" data-testid="diag-hops">
              {hop(
                'platform',
                t('routes.diagnostics.segPlatform'),
                chambers.isError ? 'bad' : chambers.isSuccess ? 'ok' : 'warn',
                chambers.isError
                  ? t('routes.diagnostics.segFail')
                  : chambers.isSuccess
                    ? t('routes.diagnostics.segOk')
                    : t('routes.diagnostics.segPending'),
                chambers.dataUpdatedAt > 0
                  ? stamp(new Date(chambers.dataUpdatedAt).toISOString())
                  : '\u2014',
                chambers.isError ? describeApiError(chambers.error) : undefined,
              )}
              {hop(
                'headless',
                t('routes.diagnostics.segHeadless'),
                headless.isError ? 'bad' : headless.isSuccess ? 'ok' : 'warn',
                headless.isError
                  ? t('routes.diagnostics.segFail')
                  : headless.isSuccess
                    ? t('routes.diagnostics.segMs', { ms: String(headless.data.ms) })
                    : t('routes.diagnostics.segPending'),
                headless.dataUpdatedAt > 0
                  ? stamp(new Date(headless.dataUpdatedAt).toISOString())
                  : '\u2014',
                headless.isError ? describeApiError(headless.error) : undefined,
              )}
              {hop(
                'session',
                t('routes.diagnostics.segSession'),
                !sessionApiEnabled ? 'none' : info.isError ? 'bad' : info.isSuccess ? 'ok' : 'warn',
                !sessionApiEnabled
                  ? t('routes.diagnostics.segNotApplicable')
                  : info.isError
                    ? t('routes.diagnostics.segFail')
                    : info.isSuccess
                      ? t('routes.diagnostics.segOk')
                      : t('routes.diagnostics.segPending'),
                !sessionApiEnabled
                  ? t('routes.diagnostics.segSessionCentral')
                  : info.dataUpdatedAt > 0
                    ? stamp(new Date(info.dataUpdatedAt).toISOString())
                    : '\u2014',
                sessionApiEnabled && info.isError ? describeApiError(info.error) : undefined,
              )}
            </ul>
          </Card>

          {/* 🔴 여덟 줄 전부 읽기가 없다. «목록»으로 두는 것이 요점이다: 빈 카드
              여덟 장이면 화면이 고장 난 것처럼 보이지만, 한 목록이면 «작업 목록»
              으로 읽힌다. 이 화면이 「세부가 없다」고 느껴지는 진짜 이유가 이것이라,
              그 사실 자체를 화면이 말해야 한다. */}
          <Card
            as="section"
            className="diag-card"
            data-missing="true"
            aria-labelledby="diag-resource-heading"
          >
            <h3 className="diag-card__title" id="diag-resource-heading">
              {t('routes.diagnostics.resourceHeading')}
            </h3>
            <p className="diag-note">{t('routes.diagnostics.resourceLede')}</p>
            <ul className="diag-missing">
              {MISSING_METRICS.map((metric) => (
                <li key={metric}>
                  <span>{t(`routes.diagnostics.metrics.${metric}`)}</span>
                  <b>{t('routes.diagnostics.noRead')}</b>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </section>

      {/* ══ 파트 ② 그 서버에 붙은 TC ═══════════════════════
          ⚠️ 「중앙 → TC 4/5」가 서버 파트가 아니라 «여기»에 있는 이유: 그것은
          서버의 상태가 아니라 «서버가 관측한 TC 의 상태»다. 관측자와 대상을
          섞으면 「서버가 4/5」라는 읽을 수 없는 문장이 된다. */}
      <section className="diag-part" aria-labelledby="diag-tc-heading">
        <div className="diag-part__head">
          <h2 className="diag-part__title" id="diag-tc-heading">
            {t('routes.diagnostics.partTc')}
          </h2>
          <span className="diag-part__lede">{t('routes.diagnostics.partTcLede')}</span>
        </div>

        <div className="diag-kpis" data-testid="diag-kpis-tc">
          <Card as="section" className="diag-kpi">
            <span className="diag-kpi__label">{t('routes.diagnostics.kpiNodes')}</span>
            <b
              className="diag-kpi__value"
              data-tone={nodes.length > 0 && online === nodes.length ? 'ok' : 'warn'}
            >{`${online} / ${nodes.length}`}</b>
          </Card>
          <Card as="section" className="diag-kpi">
            <span className="diag-kpi__label">{t('routes.diagnostics.kpiConflicts')}</span>
            <b className="diag-kpi__value" data-tone={conflicts > 0 ? 'bad' : 'ok'}>
              {String(conflicts)}
            </b>
          </Card>
          <Card as="section" className="diag-kpi">
            <span className="diag-kpi__label">{t('routes.diagnostics.kpiFaults')}</span>
            <b className="diag-kpi__value" data-tone={faults > 0 ? 'bad' : 'ok'}>
              {String(faults)}
            </b>
          </Card>
          <Card as="section" className="diag-kpi">
            <span className="diag-kpi__label">{t('routes.diagnostics.kpiHeartbeat')}</span>
            <b className="diag-kpi__value">{rel(freshest)}</b>
            {/* ⚠️ ISO 는 «버리지 않고» 내린다. 개발자는 이 값을 로그와 대조한다. */}
            <span className="diag-kpi__aside">{abs(freshest)}</span>
          </Card>
        </div>

        <div className="diag-cols diag-cols--tc">
          {/* ⚠️ 표가 아니라 «줄 목록»이다. 표로 만들면 ISO 시각(24자)이 열 하나를
              통째로 먹고, 오류 «원문»은 어차피 셀에 안 들어가 아랫줄로 흘러야 한다. */}
          <Card as="section" className="diag-card" aria-labelledby="diag-nodes-heading">
            <div className="diag-card__head">
              <h3 className="diag-card__title" id="diag-nodes-heading">
                {t('routes.diagnostics.nodesHeading')}
              </h3>
              <span className="diag-card__meta">
                {t('routes.diagnostics.nodesMeta', {
                  online: String(online),
                  total: String(nodes.length),
                })}
              </span>
            </div>

            {chambers.isLoading ? (
              <BlockSkeleton lines={5} testId="diag-nodes-loading" />
            ) : chambers.isError ? (
              <ErrorState testId="diag-nodes-error" message={describeApiError(chambers.error)} />
            ) : nodes.length === 0 ? (
              <EmptyState
                testId="diag-nodes-empty"
                title={t('routes.diagnostics.nodesEmptyTitle')}
                description={t('routes.diagnostics.nodesEmptyBody')}
              />
            ) : (
              <ul className="diag-nodes" data-testid="diag-nodes">
                {nodes.map((node) => (
                  <li className="diag-node" key={node.chamber_id} data-testid="diag-node-row">
                    <span className="diag-node__head">
                      <span className="diag-node__name">{node.name}</span>
                      <StatusBadge
                        status={chamberStatusKind(node.status)}
                        label={chamberStatusLabel(t, node.status)}
                        testId="diag-node-status"
                      />
                      <span
                        className="diag-node__verdict"
                        data-verdict={node.mode_verdict ?? 'UNDECLARED'}
                      >
                        {node.mode_verdict ?? '\u2014'}
                      </span>
                    </span>
                    <span className="diag-node__line">
                      <span className="diag-node__id">{node.chamber_id}</span>
                      <span className="diag-node__stamp">{stamp(node.last_heartbeat_at)}</span>
                    </span>
                    {node.last_error !== null &&
                      node.last_error !== undefined &&
                      node.last_error !== '' && (
                        /* 원문 그대로. 접지 않는다 — 접으면 이 화면을 열 이유가
                           없어진다. */
                        <span className="diag-node__error" data-testid="diag-node-error">
                          {node.last_error}
                        </span>
                      )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {/* 집계보다 원장이다. 「3시간 전부터 끊김」보다 「16:58 에 끊겼고 17:02 에
              한 번 붙었다 다시 끊겼다」가 원인에 훨씬 가깝다.
              ⚠️ `chamber_heartbeat_events` 는 원장으로 «존재하지만» 그것을 시간순
              으로 주는 계약이 없다. 있는 것과 읽을 수 있는 것은 다른 명제다. */}
          <Card
            as="section"
            className="diag-card"
            data-missing="true"
            aria-labelledby="diag-events-heading"
          >
            <div className="diag-card__head">
              <h3 className="diag-card__title" id="diag-events-heading">
                {t('routes.diagnostics.eventsHeading')}
              </h3>
              <span className="diag-card__meta">{t('routes.diagnostics.noRead')}</span>
            </div>
            <p className="diag-note">{t('routes.diagnostics.eventsNeed')}</p>
          </Card>
        </div>
      </section>

    </section>
  );
}

export default DiagnosticsRoute;
