import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  completeProject,
  createProject,
  type CreateProjectRequest,
  fetchProjectsPage,
  type PlatformPage,
  type ProjectDetailEnvelope,
  type ProjectEnvelope,
  type ProjectStatusFilter,
  reopenProject,
  updateProject,
  type UpdateProjectRequest,
} from '@/api/platform-client';
import { queryKeys } from '@/api/query-config';
import { useAuthSession } from '@/auth/route-guard';
import { useT } from '@/i18n';
import {
  buildProjectMetaPatch,
  EDITABLE_PROJECT_FIELDS,
  EMPTY_PROJECT_META_DRAFT,
  isProjectMetaField,
  projectMetaDraftFrom,
  type ProjectMetaDraft,
  type ProjectMetaField,
} from '@/shared/project-meta-patch';
import { projectWorkflowActions, projectWorkspaceHref } from '@/shared/project-workflow';
import { ROUTE_PATHS } from '@/shared/route-links';
import { SEARCH_DEBOUNCE_MS } from '@/shared/search-debounce';
import { useKeysetPagination } from '@/shared/use-keyset-pagination';
import { useOptimisticMutation } from '@/shared/use-optimistic-mutation';
import {
  BlockSkeleton,
  Button,
  Card,
  describeApiError,
  EmptyState,
  ErrorState,
  FieldGroup,
  LoadMoreButton,
  PageHeader,
  projectStatusKind,
  SectionBand,
  StatusBadge,
  StatusMessage,
  Toolbar,
  WorkbenchLayout,
} from '@/ui';

import type { ApiError } from '@/shared/api-error';

/**
 * 내 프로젝트 — 프로젝트(모델) 선택·생성 진입층 (ADR-0017 Phase 1, 2026-06-22). ★최우선.
 *
 * 비전의 진입 워크플로우 첫 단계: 로그인 → **프로젝트(모델) 선택/생성** → 분야 → 플랜 →
 * 측정. 기존 `/projects` 의 raw UUID 직접입력을 "모델로 고르기·만들기" 로 대체한다.
 * 프로젝트를 고르면 `?project=<id>` 로 컨텍스트를 이후 화면(진척/측정/리포트)에 전파한다.
 *
 * 인가(ADR-0017 D3): 목록/생성은 호출자 self-scoped 라 'authenticated' 레벨 —
 * 페이지가 RequireAuth 하위이므로 별도 permission 게이트를 두지 않는다(platform:read
 * 로 게이트하면 멤버십 없는 신규 시험원이 자기 빈 목록조차 못 보는 진입 chicken-and-egg).
 *
 * SSOT: 목록 데이터 = 중앙 `GET /platform/projects`(typed generated client).
 * 화면 문구는 전부 i18n, 모델/기술 리터럴 박지 않음.
 *
 * 서버측 검색 + keyset 이어 읽기(W3-B M-B, 2026-07-30): 이전에는 status 별 **전량**을
 * 한 번에 받아 로컬 순수 함수로 클라이언트에서 다시 걸렀다. 그 구조에서 검색
 * 결과 "없음"은 *"서버에 없다"* 가 아니라 *"내가 받아온 배열에 없다"* 였다 — 프로젝트가
 * 누적되면 거짓이 되는 답이다. 지금은 검색어가 `q` 로 서버에 가고 읽기는 `limit`/
 * `cursor` 로 유계이며, 남은 페이지는 [더보기]로 **드러난다**(잔여를 감추면 같은
 * 거짓말이 페이지 경계에서 재발한다). 재필터 경로는 남겨두지 않고 제거했다 —
 * 남아 있으면 서버 검색 봉인이 공허해진다.
 *
 * 표지 메타 편집(W3-B M2, 2026-07-30): 성적서 표지에 실리는 8칸(관리번호·고객·
 * 신청자·제조사·grantee code·시험 대상·규격)을 **생성 시점**과 **사후 편집** 양쪽에서
 * 채운다. 이전에는 생성 폼이 `model_name`/`management_number` 2칸만 노출하고 편집
 * 화면이 아예 없어서, 나머지 칸과 파생값 `fcc_id` 가 영구 공란으로 남았다.
 * 부분 갱신(PATCH) 의 diff 계산은 `@/shared/project-meta-patch` 단일 순수 지점이며
 * 생성 폼도 같은 함수를 공유한다(§"빈 칸 = 키 생략" == 빈 baseline 대비 diff).
 */

function MyProjectsRoute(): JSX.Element {
  const { t } = useT();
  const queryClient = useQueryClient();

  // 진행 중(active) 프로젝트가 기본 뷰. 완료(completed)/전체(all)는 토글로 전환
  // (project-status-visibility). status 별로 캐시 키가 분리된다.
  const [statusFilter, setStatusFilter] = useState<ProjectStatusFilter>('active');

  // 검색은 두 state 로 쪼갠다: `searchDraft` 는 입력 컨트롤의 값(매 키스트로크
  // 즉시 반응), `searchTerm` 은 쿼리를 구동하는 **커밋된** 값이다. 하나로 합치면
  // 글자당 중앙 읽기가 한 번씩 나간다.
  //
  // URL 파라미터(`?q=`)를 쓰지 않는 것은 의도다 — `projects.tsx` 는 다른 URL 축
  // (`?project=`/`?tech=`)이 이미 있어서 거기에 얹는 게 자연스러웠지만, 이 라우트엔
  // URL 축이 하나도 없다. `useSearchParams` 를 끌어들이면 `setParam` 안정화까지
  // 복제해야 하고 얻는 것은 없다.
  const [searchDraft, setSearchDraft] = useState('');
  const [searchTerm, setSearchTerm] = useState('');

  // 디바운스: 사용자가 멈춘 뒤에만 draft 를 커밋한다(→ queryKey 변경 → 한 번의
  // 새 읽기). 타이핑이 이어지면 cleanup 이 타이머를 재예약하므로 버스트가 하나로
  // 합쳐진다. early-return 가드가 있어야 커밋 직후 리렌더에서 타이머를 다시 걸지
  // 않는다(`projects.tsx:571-580` 와 동형).
  useEffect(() => {
    if (searchDraft === searchTerm) return undefined;
    const handle = window.setTimeout(() => setSearchTerm(searchDraft), SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(handle);
    };
  }, [searchDraft, searchTerm]);

  // 빈 검색어는 **필터 없음**이다 — `q` 키 자체를 보내지 않는다(빈 문자열은 다른
  // 요청이다). 여기서 하는 것은 정규화가 아니라 **존재 판정**이고, 검색어 정규화
  // 규칙(casing/부분일치)은 백엔드 `normalize_search_term` 이 단독 소유한다.
  const searchQuery = searchTerm.trim() === '' ? undefined : searchTerm.trim();

  // keyset 이어 읽기(공유 SSOT). `statusFilter` 와 `searchQuery` 가 **키의 일부**라
  // 둘 중 하나가 바뀌면 새 캐시 항목이 되고 페이징이 첫 페이지부터 자동으로 다시
  // 시작한다 — `remove()`/`reset()` 을 손으로 부르지 않는다.
  const projects = useKeysetPagination<ProjectEnvelope, PlatformPage<ProjectEnvelope>>({
    queryKey: queryKeys.project.directory(statusFilter, searchQuery),
    fetchPage: (cursor) => fetchProjectsPage(statusFilter, searchQuery, cursor),
    getNextCursor: (page) => page.nextCursor ?? undefined,
  });
  const rows = projects.rows;

  // 완료/재개는 platform:admin 인데, 백엔드가 토큰 ∪ **프로젝트-멤버십 admin** 의 UNION 을
  // authorize(project_id=...) 로 판정한다(membership.tsx 와 동일). 클라이언트는 토큰만
  // 보므로 멤버십-admin 을 못 본다 → 토큰으로 게이트하면 멤버십-admin 에게 버튼이 안 보이는
  // false negative. 그래서 인증된 누구나 노출하고 백엔드를 최종 권위로 둔다(403 은 에러 표면화).
  const auth = useAuthSession();
  const canManage = auth.kind === 'authenticated';

  // 생성/완료/재개 후 lists() 프리픽스로 무효화 → 모든 status 변형(active/completed/all)
  // 이 한 번에 갱신된다(완료 처리한 프로젝트가 active 뷰에서 사라지고 completed 로 이동).
  const invalidateLists = (): void => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.project.lists() });
  };

  const createMutation = useMutation({
    mutationFn: (input: { modelName: string; meta: ProjectMetaDraft }) => {
      // 표지 메타는 전부 선택 입력이고 **빈 칸은 키를 생략**한다(백엔드가 NULL 로
      // 저장 — `''` 와 다르다). 그 계산은 빈 baseline 대비 diff 와 정확히 같으므로
      // 편집 폼과 같은 순수 함수 한 곳을 공유한다(두 번째 구현 금지).
      const body: CreateProjectRequest = {
        model_name: input.modelName,
        ...buildProjectMetaPatch(EMPTY_PROJECT_META_DRAFT, input.meta),
      };
      return createProject(body);
    },
    onSuccess: invalidateLists,
  });

  const lifecycleMutation = useMutation({
    mutationFn: (input: { projectId: string; action: 'complete' | 'reopen' }) =>
      input.action === 'complete'
        ? completeProject(input.projectId)
        : reopenProject(input.projectId),
    onSuccess: invalidateLists,
  });

  // ── 표지 메타 부분 편집 (W3-B M2) ─────────────────────────────────────────
  //
  // 상태는 **편집 중인 프로젝트만** 담는다 — 서버 payload 의 사본이 아니다.
  // `useEffect([projects.data], setDraft)` 로 서버 상태를 state 에 미러링하면
  // 이 라우트가 폴링/포커스 refetch 를 하는 순간 타이핑이 주기적으로 날아간다
  // (W2-C 가 ChamberAdminPanel 에서 실제로 겪은 결함). 렌더 값은 매번
  // `edit?.draft ?? projectMetaDraftFrom(project)` 로 **파생**하므로 동기화할
  // 대상 자체가 없다.
  //
  // `baseline` 은 편집 시작(첫 키스트로크) 시점의 서버 값을 1회 포획한 것이다.
  // 이것이 있어야 "사용자가 바꾼 칸"과 "서버가 그 사이 바뀐 칸"을 구분할 수 있고,
  // 그 구분이 곧 PATCH diff(= lost update 방지)의 근거다.
  const [edits, setEdits] = useState<Record<string, ProjectMetaEdit>>({});

  const discardEdit = (projectId: string): void => {
    setEdits((current) => {
      if (current[projectId] === undefined) return current;
      const next = { ...current };
      delete next[projectId];
      return next;
    });
  };

  // 낙관적 갱신을 **쓰지 않는다**(선결 판단 4): PATCH 는 관리번호 유일성(409)을
  // 서버가 검증해야 확정이므로, 낙관적으로 반영하면 충돌 시 "저장됐다가 사라지는"
  // 것을 사용자가 본다. `optimisticUpdate` 를 생략하면 이 훅은 옛
  // central-write-then-refresh 와 byte-identical 하게 동작한다(성공 시에만 무효화).
  const metaMutation = useOptimisticMutation<
    ProjectDetailEnvelope,
    { readonly projectId: string; readonly patch: UpdateProjectRequest }
  >({
    mutationFn: ({ projectId, patch }) => updateProject(projectId, patch),
    // 목록 프리픽스 — active/completed/all 세 변형이 한 번에 갱신된다.
    queryKey: queryKeys.project.lists(),
    onSuccess: (_saved, variables) => {
      // 쓰기가 안착했으므로 이 프로젝트의 로컬 편집은 더 이상 "지켜야 할 미저장
      // 작업"이 아니다 — 버리고 행이 다시 서버를 따라가게 한다.
      discardEdit(variables.projectId);
      // 상세(`/platform/projects/{id}`)도 같은 표지 메타를 나르므로 함께 무효화한다
      // (분야·시료·성적서 화면이 그 캐시를 읽는다). projectId 가 variables 에만
      // 있어서 훅의 정적 `invalidateKeys` 로는 표현할 수 없다.
      void queryClient.invalidateQueries({
        queryKey: queryKeys.project.detail(variables.projectId),
      });
    },
  });

  const updateMetaDraft = (
    project: ProjectEnvelope,
    field: ProjectMetaField,
    value: string,
  ): void => {
    setEdits((current) => {
      const server = projectMetaDraftFrom(project);
      const existing = current[project.project_id];
      return {
        ...current,
        [project.project_id]: {
          // 첫 키스트로크에서 1회만 포획 — 이후 키스트로크가 baseline 을 움직이면
          // diff 가 자기 자신과 비교되어 영원히 비게 된다.
          baseline: existing?.baseline ?? server,
          draft: { ...(existing?.draft ?? server), [field]: value },
        },
      };
    });
    metaMutation.reset();
  };

  const [modelDraft, setModelDraft] = useState('');
  const [createMeta, setCreateMeta] = useState<ProjectMetaDraft>(EMPTY_PROJECT_META_DRAFT);

  const trimmedModel = modelDraft.trim();
  const canCreate = trimmedModel !== '' && !createMutation.isPending;

  const createError = createMutation.error
    ? describeApiError(createMutation.error, 'platform', {
        forbidden: t('routes.myProjects.create.forbidden'),
        badRequest: t('routes.myProjects.create.invalid'),
        network: t('routes.myProjects.create.offline'),
        default: t('routes.myProjects.create.failed'),
      })
    : null;

  return (
    <section className="my-projects" aria-labelledby="my-projects-heading">
      <PageHeader
        title={t('routes.myProjects.title')}
        titleId="my-projects-heading"
        description={t('routes.myProjects.description')}
      />

      <MyProjectsWorkbenchOverview />

      <WorkbenchLayout
        className="my-projects-workbench"
        mainLabel={t('routes.myProjects.listSection')}
        railLabel={t('routes.myProjects.createSection')}
        testId="my-projects-workbench"
        main={
          <div className="my-projects-workbench__main">
            <Card
              as="section"
              className="my-projects-workbench-panel"
              aria-labelledby="my-projects-search-heading"
            >
              <SectionBand
                title={t('routes.myProjects.searchSection')}
                titleId="my-projects-search-heading"
              />
              {/* 검색은 **서버측** `q` 한 축이고 훑는 컬럼은 백엔드
                `PROJECT_SEARCH_COLUMNS` 가 단독으로 정한다 — 관리번호 ·
                프로젝트 코드(= 모델명, ADR-0017 D1) · 고객사. 로드된 배열을 다시
                거르지 않으므로 "없음"은 **현재 상태 필터 안의** 중앙 디렉토리에
                없다는 뜻이다 — 같은 요청이 `status` 로도 좁혀지므로 그보다 넓게
                말하면 딱 그 필터 폭만큼 거짓이 된다(넓히려면 아래 [전체] 토글). */}
              <form
                aria-label={t('routes.myProjects.search.ariaLabel')}
                onSubmit={(e) => e.preventDefault()}
              >
                <Toolbar ariaLabel={t('routes.myProjects.search.ariaLabel')} inline>
                  <FieldGroup label={t('routes.myProjects.search.label')} htmlFor="project-search">
                    <input
                      id="project-search"
                      data-testid="project-search"
                      type="search"
                      value={searchDraft}
                      placeholder={t('routes.myProjects.search.placeholder')}
                      onChange={(e) => setSearchDraft(e.target.value)}
                    />
                  </FieldGroup>
                </Toolbar>
              </form>
            </Card>

            {/* 진행 중 / 완료 / 전체 토글 (project-status-visibility) — 기본 active. */}
            <Toolbar ariaLabel={t('routes.myProjects.statusFilter.ariaLabel')} inline>
              <div
                className="status-filter"
                role="group"
                aria-label={t('routes.myProjects.statusFilter.ariaLabel')}
                data-testid="project-status-filter"
              >
                {(['active', 'completed', 'all'] as const).map((value) => (
                  <Button
                    key={value}
                    type="button"
                    variant={statusFilter === value ? 'primary' : 'ghost'}
                    data-testid={`project-status-${value}`}
                    aria-pressed={statusFilter === value}
                    onClick={() => setStatusFilter(value)}
                  >
                    {t(`routes.myProjects.statusFilter.${value}`)}
                  </Button>
                ))}
              </div>
            </Toolbar>

            {lifecycleMutation.isError && (
              <ErrorState
                testId="project-lifecycle-error"
                message={describeApiError(lifecycleMutation.error, 'platform', {
                  forbidden: t('routes.myProjects.lifecycle.forbidden'),
                  notFound: t('routes.myProjects.lifecycle.notFound'),
                  network: t('routes.myProjects.lifecycle.network'),
                  default: t('routes.myProjects.lifecycle.failed'),
                })}
              />
            )}

            <section
              className="my-projects-workbench-panel"
              aria-labelledby="my-projects-list-heading"
            >
              <SectionBand
                title={t('routes.myProjects.listSection')}
                titleId="my-projects-list-heading"
              />
              {projects.isLoading && <BlockSkeleton lines={4} testId="my-projects-loading" />}
              {projects.isError && (
                <ErrorState
                  testId="projects-error"
                  message={describeApiError(projects.error, 'platform', {
                    forbidden: t('routes.myProjects.list.forbidden'),
                    network: t('routes.myProjects.list.network'),
                    default: t('routes.myProjects.list.failed'),
                  })}
                />
              )}
              {/* 빈 상태는 **잔여 페이지가 없을 때만** 주장한다. 커서가 남아 있는데
                "없습니다"를 띄우면 클라이언트 필터가 하던 거짓말이 페이지 경계에서
                재발한다(아래 [더보기]가 그 잔여를 드러낸다). */}
              {projects.isSuccess &&
                rows.length === 0 &&
                !projects.hasNextPage &&
                (searchQuery !== undefined ? (
                  <EmptyState
                    testId="projects-empty"
                    title={t('routes.myProjects.list.emptyFilteredTitle', { query: searchQuery })}
                    description={t('routes.myProjects.list.emptyFilteredDescription')}
                  />
                ) : (
                  <EmptyState
                    testId="projects-empty"
                    title={t('routes.myProjects.list.emptyTitle')}
                    description={t('routes.myProjects.list.emptyDescription')}
                  />
                ))}
              {projects.isSuccess && rows.length > 0 && (
                <ul className="project-card-list" data-testid="project-card-list">
                  {rows.map((project) => (
                    <ProjectCard
                      key={project.project_id}
                      project={project}
                      canManage={canManage}
                      lifecyclePending={lifecycleMutation.isPending}
                      onLifecycle={(action) =>
                        lifecycleMutation.mutate({ projectId: project.project_id, action })
                      }
                      edit={edits[project.project_id]}
                      onMetaChange={(field, value) => updateMetaDraft(project, field, value)}
                      onMetaSave={(patch) =>
                        metaMutation.mutate({ projectId: project.project_id, patch })
                      }
                      onMetaDiscard={() => {
                        discardEdit(project.project_id);
                        metaMutation.reset();
                      }}
                      // 쓰기 상태는 **이 카드 몫만** 보인다. 단일 mutation 인스턴스가
                      // 모든 카드를 처리하므로, 대상 프로젝트로 좁히지 않으면 A 카드의
                      // 409 가 B 카드에도 뜬다.
                      metaPending={
                        metaMutation.isPending &&
                        metaMutation.variables?.projectId === project.project_id
                      }
                      metaError={
                        metaMutation.variables?.projectId === project.project_id
                          ? metaMutation.error
                          : null
                      }
                      metaSaved={
                        metaMutation.isSuccess &&
                        metaMutation.variables?.projectId === project.project_id
                      }
                    />
                  ))}
                </ul>
              )}
              {/* 잔여 존재를 사용자에게 드러내는 유일한 수단. `hasNextPage` 는
                `X-Next-Cursor` 헤더에서 파생되므로(=`nextCursor !== null`), 버튼의
                존재 자체가 "서버에 더 있다"는 서버측 사실이다. */}
              {projects.isSuccess && projects.hasNextPage && (
                <LoadMoreButton
                  testId="project-load-more"
                  onClick={projects.fetchNextPage}
                  isFetching={projects.isFetchingNextPage}
                />
              )}
            </section>
          </div>
        }
        rail={
          <div
            className="my-projects-workbench__rail"
            aria-labelledby="my-projects-create-heading"
            data-testid="my-projects-create-panel"
          >
            <section className="my-projects-create">
              <SectionBand
                title={t('routes.myProjects.createSection')}
                titleId="my-projects-create-heading"
              />
              {/* [새 프로젝트] — 모델명만 입력(ADR-0017 D1: project_code == 모델명). 인증된
                시험원이면 누구나 생성(생성자 자동 admin). 동명은 멱등 재사용. */}
              <form
                aria-label={t('routes.myProjects.create.ariaLabel')}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (canCreate)
                    createMutation.mutate({ modelName: trimmedModel, meta: createMeta });
                }}
              >
                <Toolbar ariaLabel={t('routes.myProjects.create.ariaLabel')}>
                  <FieldGroup
                    label={t('routes.myProjects.create.modelLabel')}
                    htmlFor="new-project-model"
                  >
                    <input
                      id="new-project-model"
                      data-testid="new-project-model"
                      value={modelDraft}
                      placeholder={t('routes.myProjects.create.modelPlaceholder')}
                      onChange={(e) => setModelDraft(e.target.value)}
                    />
                  </FieldGroup>
                  {/* 표지 메타 — 전부 선택 입력. 목록·필드 순서는
                    `EDITABLE_PROJECT_FIELDS` 단일 SSOT 가 정한다(편집 폼과 동일
                    순서·동일 라벨 키 → 두 폼이 어긋날 수 없다). */}
                  {EDITABLE_PROJECT_FIELDS.map((field) => (
                    <FieldGroup
                      key={field}
                      label={t(`routes.myProjects.metaField.${field}`)}
                      htmlFor={`new-project-${field}`}
                    >
                      <input
                        id={`new-project-${field}`}
                        data-testid={`new-project-${field}`}
                        value={createMeta[field]}
                        onChange={(e) =>
                          setCreateMeta((prev) => ({ ...prev, [field]: e.target.value }))
                        }
                      />
                    </FieldGroup>
                  ))}
                  <Button
                    type="submit"
                    variant="primary"
                    data-testid="new-project-submit"
                    disabled={!canCreate}
                  >
                    {createMutation.isPending
                      ? t('routes.myProjects.create.submitting')
                      : t('routes.myProjects.create.submit')}
                  </Button>
                </Toolbar>
              </form>
              {createError !== null && (
                <ErrorState testId="new-project-error" message={createError} />
              )}
              {createMutation.isSuccess && createMutation.error === null && (
                <StatusMessage
                  tone="success"
                  testId="new-project-success"
                  message={t('routes.myProjects.create.success', {
                    model: createMutation.data?.model_name ?? '',
                  })}
                />
              )}
              <p className="section-hint">{t('routes.myProjects.createHint')}</p>
            </section>
          </div>
        }
      />
    </section>
  );
}

function MyProjectsWorkbenchOverview(): JSX.Element {
  const { t } = useT();
  return (
    <nav
      className="my-projects-workbench-overview"
      aria-label={t('routes.myProjects.workbenchNavAria')}
      data-testid="my-projects-workbench-overview"
    >
      <a className="my-projects-workbench-overview__item" href="#my-projects-search-heading">
        <span className="my-projects-workbench-overview__label">
          {t('routes.myProjects.stepSearch')}
        </span>
        <span className="my-projects-workbench-overview__detail">
          {t('routes.myProjects.stepSearchDetail')}
        </span>
      </a>
      <a className="my-projects-workbench-overview__item" href="#my-projects-list-heading">
        <span className="my-projects-workbench-overview__label">
          {t('routes.myProjects.stepPick')}
        </span>
        <span className="my-projects-workbench-overview__detail">
          {t('routes.myProjects.stepPickDetail')}
        </span>
      </a>
      <a className="my-projects-workbench-overview__item" href="#my-projects-create-heading">
        <span className="my-projects-workbench-overview__label">
          {t('routes.myProjects.stepCreate')}
        </span>
        <span className="my-projects-workbench-overview__detail">
          {t('routes.myProjects.stepCreateDetail')}
        </span>
      </a>
    </nav>
  );
}

/**
 * 한 프로젝트의 **미저장** 표지 메타 편집.
 *
 * `draft` 는 사용자가 타이핑한 값, `baseline` 은 편집을 시작한 순간의 서버 값이다.
 * baseline 을 들고 있는 것이 "사용자가 이 칸을 바꿨다"와 "서버가 사용자 아래에서
 * 바뀌었다"를 구분하는 유일한 방법이고, 그 구분 없이는 최소 diff 를 만들 수 없다.
 */
interface ProjectMetaEdit {
  readonly draft: ProjectMetaDraft;
  readonly baseline: ProjectMetaDraft;
}

function ProjectCard({
  project,
  canManage,
  lifecyclePending,
  onLifecycle,
  edit,
  onMetaChange,
  onMetaSave,
  onMetaDiscard,
  metaPending,
  metaError,
  metaSaved,
}: {
  readonly project: ProjectEnvelope;
  readonly canManage: boolean;
  readonly lifecyclePending: boolean;
  readonly onLifecycle: (action: 'complete' | 'reopen') => void;
  readonly edit: ProjectMetaEdit | undefined;
  readonly onMetaChange: (field: ProjectMetaField, value: string) => void;
  readonly onMetaSave: (patch: UpdateProjectRequest) => void;
  readonly onMetaDiscard: () => void;
  readonly metaPending: boolean;
  readonly metaError: ApiError | null;
  readonly metaSaved: boolean;
}): JSX.Element {
  const { t } = useT();
  const actionById = new Map(
    projectWorkflowActions(project.project_id, [
      'fields',
      'progress',
      'workspace',
      'inventory',
      'testPlans',
      'chambers',
      'testReports',
    ]).map((action) => [action.id, action.href]),
  );
  // 단일 `isCompleted` 파생이 status badge 와 lifecycle 버튼을 함께 구동 → 둘이 어긋날 수
  // 없다. DB 가 status 를 {active, completed} 로 봉인(ck_projects_status); null legacy 행은
  // active 로 표시(create-time default 로 backfill 될 값).
  const isCompleted = project.status === 'completed';
  const statusLabelKey = isCompleted
    ? 'routes.myProjects.list.statusCompleted'
    : 'routes.myProjects.list.statusActive';

  return (
    <li className="project-card" data-testid="project-card">
      <Link
        to={projectWorkspaceHref(project.project_id)}
        className="project-card__link"
        data-testid="project-card-link"
      >
        <span className="project-card__model" data-testid="project-card-model">
          {project.model_name}
        </span>
        <StatusBadge
          status={projectStatusKind(project.status)}
          label={t(statusLabelKey)}
          testId="project-card-status"
        />
        <StatusBadge
          status="running"
          label={t('routes.myProjects.list.sampleCount', { n: project.sample_count })}
          testId="project-card-samples"
        />
        {project.management_number !== null &&
          project.management_number !== undefined &&
          project.management_number !== '' && (
            <span className="project-card__mgmt" data-testid="project-card-mgmt">
              {t('routes.myProjects.list.mgmtLabel', { value: project.management_number })}
            </span>
          )}
        {project.fcc_id !== null && project.fcc_id !== undefined && project.fcc_id !== '' && (
          <span className="project-card__fcc" data-testid="project-card-fcc">
            {t('routes.myProjects.list.fccLabel', { value: project.fcc_id })}
          </span>
        )}
        {project.customer !== null && project.customer !== undefined && project.customer !== '' && (
          <span className="project-card__customer" data-testid="project-card-customer">
            {project.customer}
          </span>
        )}
      </Link>
      {canManage && (
        <Button
          type="button"
          variant="secondary"
          className="project-card__lifecycle"
          data-testid="project-card-lifecycle"
          disabled={lifecyclePending}
          onClick={() => onLifecycle(isCompleted ? 'reopen' : 'complete')}
        >
          {isCompleted
            ? t('routes.myProjects.lifecycle.reopen')
            : t('routes.myProjects.lifecycle.complete')}
        </Button>
      )}
      <div className="project-card__actions" data-testid="project-card-actions">
        <Link
          to={actionById.get('fields') ?? '/fields'}
          className="project-card__action"
          data-testid="project-card-fields"
        >
          {t('routes.fields.title')}
        </Link>
        <Link
          to={actionById.get('progress') ?? '/progress'}
          className="project-card__action"
          data-testid="project-card-progress"
        >
          {t('routes.myProjects.list.nextProgress')}
        </Link>
        <Link
          to={actionById.get('workspace') ?? '/projects'}
          className="project-card__action"
          data-testid="project-card-coverage"
        >
          {t('routes.myProjects.list.nextCoverage')}
        </Link>
        <Link
          to={actionById.get('inventory') ?? '/inventory'}
          className="project-card__action"
          data-testid="project-card-inventory"
        >
          {t('routes.myProjects.list.nextInventory')}
        </Link>
        <Link
          to={actionById.get('testPlans') ?? '/test-plans'}
          className="project-card__action"
          data-testid="project-card-test-plans"
        >
          {t('routes.myProjects.list.nextTestPlans')}
        </Link>
        <Link
          to={actionById.get('chambers') ?? '/chambers'}
          className="project-card__action"
          data-testid="project-card-chambers"
        >
          {t('routes.progress.nextChambers')}
        </Link>
        <Link
          to={actionById.get('testReports') ?? ROUTE_PATHS.testReports}
          className="project-card__action"
          data-testid="project-card-test-reports"
        >
          {t('routes.myProjects.list.nextTestReports')}
        </Link>
      </div>
      {canManage && (
        <ProjectMetaEditor
          project={project}
          edit={edit}
          onChange={onMetaChange}
          onSave={onMetaSave}
          onDiscard={onMetaDiscard}
          pending={metaPending}
          error={metaError}
          saved={metaSaved}
        />
      )}
    </li>
  );
}

/**
 * 성적서 표지 메타 편집 폼 — 카드 안 `<details>` 개시(선결 판단 1: 진입층과 같은
 * 화면, 별도 라우트 아님). 모달을 쓰지 않는 이유는 코드베이스에 dialog 프리미티브가
 * 없어서다 — 새로 만들면 포커스 트랩/ESC/스크롤 락을 부분 구현하게 되고, 그것이
 * 접근성 후퇴다. native `<details>` 는 키보드·스크린리더가 공짜로 동작한다.
 *
 * **저장 버튼은 dirty 필드가 0 일 때만 비활성**이다. 권한 토큰으로 잠그지 않는다:
 * 백엔드 인가는 `platform:admin` 토큰 **∪ 프로젝트-멤버십 admin** 의 UNION 인데
 * 브라우저는 토큰만 보므로, 토큰 미보유를 근거로 잠그면 멤버십-admin 이 정당한
 * 권한으로도 막히는 false negative 가 된다(403 을 표면화하는 쪽이 정직하다).
 * 빈 body 는 백엔드가 no-op 이 아니라 400 으로 거절하므로 dirty 0 게이트는
 * UX 가 아니라 계약이다.
 */
function ProjectMetaEditor({
  project,
  edit,
  onChange,
  onSave,
  onDiscard,
  pending,
  error,
  saved,
}: {
  readonly project: ProjectEnvelope;
  readonly edit: ProjectMetaEdit | undefined;
  readonly onChange: (field: ProjectMetaField, value: string) => void;
  readonly onSave: (patch: UpdateProjectRequest) => void;
  readonly onDiscard: () => void;
  readonly pending: boolean;
  readonly error: ApiError | null;
  readonly saved: boolean;
}): JSX.Element {
  const { t } = useT();
  // 렌더 값은 매 렌더 파생 — 편집 중이 아니면 서버 행을 그대로 읽는다(미러 없음).
  // 스냅샷 원천이 **목록 행**이라는 점이 중요하다: ProjectEnvelope 이 편집 8칸 전부와
  // 파생값 fcc_id 를 이미 나르므로 카드마다 상세를 조회하는 N+1 이 필요 없다.
  const serverDraft = projectMetaDraftFrom(project);
  const draft = edit?.draft ?? serverDraft;
  const baseline = edit?.baseline ?? serverDraft;
  const patch = buildProjectMetaPatch(baseline, draft);
  const dirtyFields = Object.keys(patch);
  // dirty 판정은 **전송할 body 그 자체**에서 파생한다. 별도 비교 함수를 두면
  // "버튼은 활성인데 body 는 비어 400" 같은 어긋남이 생길 수 있다.
  const isDirty = dirtyFields.length > 0;

  // 409 필드 귀속 — `params.field` 가 **이 폼에 실제로 있는 칸**일 때만 그 입력에
  // 붙인다. `PROJECT_IDENTIFIER_CONFLICT` 는 폼에 없는 `project_code` 충돌로도
  // 나므로, 모르는 값을 억지로 붙이면 사용자가 엉뚱한 칸을 고치려 한다. 그 경우는
  // 아래 `describeApiError` 의 일반 충돌 문구로 폴백한다.
  const conflictField =
    error?.status === 409 && isProjectMetaField(error.params?.field) ? error.params.field : null;

  return (
    <details className="advanced-disclosure" data-testid="project-meta-editor">
      <summary className="advanced-disclosure__summary" data-testid="project-meta-toggle">
        <span className="advanced-disclosure__title">{t('routes.myProjects.edit.section')}</span>
        <span className="advanced-disclosure__meta">
          {isDirty
            ? t('routes.myProjects.edit.dirtyCount', { n: dirtyFields.length })
            : t('routes.myProjects.edit.pristine')}
        </span>
      </summary>
      <form
        aria-label={t('routes.myProjects.edit.ariaLabel')}
        data-testid="project-meta-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (isDirty && !pending) onSave(patch);
        }}
      >
        <Toolbar ariaLabel={t('routes.myProjects.edit.ariaLabel')}>
          {EDITABLE_PROJECT_FIELDS.map((field) => (
            <FieldGroup
              key={field}
              label={t(`routes.myProjects.metaField.${field}`)}
              htmlFor={`project-meta-${project.project_id}-${field}`}
            >
              <input
                id={`project-meta-${project.project_id}-${field}`}
                data-testid={`project-meta-${field}`}
                value={draft[field]}
                aria-invalid={conflictField === field ? true : undefined}
                onChange={(e) => onChange(field, e.target.value)}
              />
            </FieldGroup>
          ))}
          <Button
            type="submit"
            variant="primary"
            data-testid="project-meta-save"
            disabled={!isDirty || pending}
          >
            {pending ? t('routes.myProjects.edit.saving') : t('routes.myProjects.edit.save')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            data-testid="project-meta-discard"
            disabled={!isDirty}
            onClick={onDiscard}
          >
            {t('routes.myProjects.edit.discard')}
          </Button>
        </Toolbar>
      </form>
      {isDirty && (
        <StatusMessage
          tone="info"
          testId="project-meta-unsaved"
          message={t('routes.myProjects.edit.unsaved', { n: dirtyFields.length })}
        />
      )}
      {error !== null && (
        <ErrorState
          testId="project-meta-error"
          message={describeApiError(error, 'platform', {
            badRequest: t('routes.myProjects.edit.invalid'),
            forbidden: t('routes.myProjects.edit.forbidden'),
            notFound: t('routes.myProjects.edit.notFound'),
            conflict: t('routes.myProjects.edit.conflict'),
            serviceUnavailable: t('routes.myProjects.edit.unavailable'),
            network: t('routes.myProjects.edit.network'),
            default: t('routes.myProjects.edit.failed'),
          })}
        />
      )}
      {saved && !isDirty && (
        <StatusMessage
          tone="success"
          testId="project-meta-success"
          message={t('routes.myProjects.edit.success')}
        />
      )}
      {/* FCC ID 는 **서버 파생값**이다(grantee code + 모델명 정규화 —
          `fcc_id_policy.py`). 입력 옆에 미리보기를 만드는 순간 그 정규화 규칙이
          TS 로 복제되어 두 번째 진실 원천이 된다. 저장 후 서버가 돌려주는 값을
          카드 상단에서 읽는다. */}
      <p className="section-hint">{t('routes.myProjects.edit.fccIdHint')}</p>
    </details>
  );
}

export default MyProjectsRoute;
export { MyProjectsRoute };
