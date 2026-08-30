/**
 * 서버측 검색 입력의 디바운스 창 — 명명 SSOT (W3-B M-B, 2026-07-30).
 *
 * 검색 입력은 type-ahead 다. 매 키스트로크를 쿼리에 커밋하면 글자당 중앙 읽기가
 * 한 번씩 나간다('S' → 'SM' → 'SM-'). 디바운스는 타이핑 버스트를 사용자가 멈춘
 * 뒤 **한 번의 서버측 좁히기 읽기**로 합친다(업계 표준 faceted-search 동작).
 *
 * **왜 라우트 로컬 상수가 아니라 이 모듈인가**
 *
 * 같은 문제를 이미 푼 선례가 `routes/projects.tsx::TECH_FILTER_DEBOUNCE_MS` 였다.
 * 그런데 그 상수는 **importer 가 0건**(정의 1 + 자기 사용 1)이라 라우트 파일에
 * 갇혀 있었고, W3-B 웨이브에는 그 파일이 범위 밖이었다. 그래서 두 선택지밖에
 * 없었다: 값을 복사해 리터럴 `250` 을 두 곳(디렉토리 목록 · 프로젝트 선택기)에
 * 만들거나, 공유 지점을 하나 만들거나. 앞의 것은 그대로 드리프트다.
 *
 * fe-honesty-debt M1 (2026-07-31): 그 라우트를 함께 소유하는 웨이브가 와서
 * `TECH_FILTER_DEBOUNCE_MS` 를 **제거하고 이 상수를 소비**하게 했다. 통합 근거는
 * *값이 같아서가 아니라 정책이 같아서*다 — 세 소비처(기술 facet · 디렉토리 검색 ·
 * 프로젝트 선택기)가 전부 "type-ahead 텍스트 입력을 사용자가 멈춘 뒤 **한 번의
 * 서버측 좁히기 읽기**로 커밋한다"는 동일 정책이고, 이 값이 정하는 축(*언제 커밋
 * 하는가*)은 어떤 파라미터로 좁히는지(`technology` vs `q`)와 직교한다. 한쪽만
 * 바뀌어야 할 이유가 생기면 그때 **이름 있는 두 번째 정책 상수**로 갈라야지,
 * 라우트 로컬 리터럴로 되돌아가서는 안 된다.
 *
 * 라우트 안에 두면 안 되는 또 하나의 이유: `TestNoNamedCadenceConstantBypassInRoutes`
 * 가 라우트 로컬 cadence 상수를 금지하고 그 allowlist CEILING 은 **0** 이다.
 * `_DEBOUNCE_MS` 는 그 정규식(`REFETCH|POLL|INTERVAL|STALE_TIME|…`)을 우연히
 * 빠져나가지만, 그 우연에 SSOT 를 의탁하지 않는다 —
 * `TestSearchDebounceIsSingleSsot` 가 그 축을 명시적으로 막는다.
 *
 * 이것은 refetch/cache cadence 가 **아니다** — `REFETCH_STRATEGIES`(staleTime /
 * gcTime / refetchInterval)는 "서버 데이터가 언제 낡는가"를 정하고, 이 값은
 * "사용자 입력을 언제 커밋하는가"를 정한다. 두 축은 별개이므로 query-config 의
 * 전략 번들에 섞지 않는다.
 */
export const SEARCH_DEBOUNCE_MS = 250;
