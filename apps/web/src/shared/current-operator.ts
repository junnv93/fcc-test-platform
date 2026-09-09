import { useAuthSession } from '@/auth/route-guard';

/**
 * 로그인한 사람의 «사번».
 *
 * 측정·배정 원장(`measurement_attempts.operator` / `claim_events.operator`)은
 * 사번을 문자열로 담고, 로그인 신원은 OIDC subject 다. 두 축이 만나는 지점이
 * 사번이고, 그것을 토큰이 실어 나른다(`employee_id` 클레임 — IdP 의 사용자
 * 속성에서 나온다).
 *
 * ⚠️ 화면이 신원을 «추측»하지 않는다. 이름이나 이메일로 맞춰 보는 순간, 동명이인
 * 하나에 남의 배정이 내 것으로 표시되고 그 화면은 멀쩡해 보인다. 클레임이 없으면
 * `null` 을 돌려주고, 부르는 쪽은 「내 것을 가릴 수 없다」고 말해야 한다 —
 * 「내 것이 없다」가 아니라.
 *
 * ⚠️ 서버 쪽 권한과는 «무관»하다. 이것은 표시를 가르는 값이지 접근을 여는 값이
 * 아니다. 위조해 봐야 남의 행이 자기 색으로 보일 뿐, 읽을 수 있는 범위는
 * `platform:read` 가 그대로 정한다.
 */
export function useCurrentOperator(): string | null {
  const state = useAuthSession();
  if (state.kind !== 'authenticated') return null;
  const claim: unknown = state.principal.raw['employee_id'];
  if (typeof claim === 'string' && claim.trim() !== '') return claim.trim();
  // 일부 IdP 는 다값 속성을 배열로 싣는다.
  // ⚠️ `Array.isArray` 는 `unknown` 을 `any[]` 로 좁힌다 — 그 뒤의 `.find` 결과가
  // `any` 라 규칙에 걸린다. `unknown[]` 로 명시해 요소가 `unknown` 으로 남게 한다.
  if (Array.isArray(claim)) {
    const values = claim as unknown[];
    const first = values.find((v) => typeof v === 'string' && v.trim() !== '');
    if (typeof first === 'string') return first.trim();
  }
  return null;
}
