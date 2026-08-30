/**
 * Workbench area (시험 분야) SSOT — ADR-0017 Phase 2 진입 택소노미 (2026-06-22).
 *
 * 제품 비전의 4개 "분야"(headless 시험 분야 = FCC 규제 도메인)를 단일 SSOT 로 둔다.
 * 이것은 **웹 작업대의 최상위 내비게이션 택소노미**(어떤 규제 분야에서 작업하는가)이며,
 * provider 내부 측정-항목 taxonomy(BT/BLE/DTS/UNII·채널·대역폭 등 — ADR-0010 D-3 가
 * platform core 의 derive 를 금지하는 대상)와는 다른 층이다.
 *
 * ADR-0010 정합:
 * - provider_id 를 분야 식별자로 쓰지 않는다. 분야 id(`WorkbenchAreaId`)는 별도 키 공간.
 * - 가용성(available)은 "그 분야의 headless provider 가 배포되었는가" 로 정의한다.
 *   현재 배포 provider 는 `fcc-unlicensed-conducted` 하나 →
 *   `unlicensed_conducted` 만 available, 나머지 3개는 'planned'(준비 중). 후속 provider
 *   (mmWave / radiated / licensed) 가 배포되면 본 SSOT 의 status 를 승격한다(또는 추후
 *   provider descriptor 의 read-only `workbench_area` capability 로 백엔드 파생 전환).
 *
 * 라벨/설명은 i18n SSOT(`routes.fields.areas.<id>.*`)에서만 — 여기엔 표시 문자열을 박지
 * 않는다(키만 보유). 화면은 이 배열을 단일 소스로 렌더한다(분야 리터럴 산재 0).
 */

export type WorkbenchAreaId =
  | 'unlicensed_conducted'
  | 'unlicensed_radiated'
  | 'mmwave'
  | 'licensed_conducted';

export type WorkbenchAreaStatus = 'available' | 'planned';

export interface WorkbenchArea {
  readonly id: WorkbenchAreaId;
  /** i18n key for the human label (`routes.fields.areas.<id>.label`). */
  readonly labelKey: string;
  /** i18n key for the one-line blurb (`routes.fields.areas.<id>.blurb`). */
  readonly blurbKey: string;
  /**
   * 'available' when a headless provider for this area is deployed (selectable);
   * 'planned' when it is on the roadmap but not yet wired (shown but disabled).
   */
  readonly status: WorkbenchAreaStatus;
}

/**
 * The four product-defined workbench areas, in entry-display order. Only the
 * deployed provider's area is 'available' today (honest — three are roadmap).
 * This is the single source the field-selection screen renders.
 */
export const WORKBENCH_AREAS: readonly WorkbenchArea[] = [
  {
    id: 'unlicensed_conducted',
    labelKey: 'routes.fields.areas.unlicensed_conducted.label',
    blurbKey: 'routes.fields.areas.unlicensed_conducted.blurb',
    status: 'available',
  },
  {
    id: 'unlicensed_radiated',
    labelKey: 'routes.fields.areas.unlicensed_radiated.label',
    blurbKey: 'routes.fields.areas.unlicensed_radiated.blurb',
    status: 'planned',
  },
  {
    id: 'mmwave',
    labelKey: 'routes.fields.areas.mmwave.label',
    blurbKey: 'routes.fields.areas.mmwave.blurb',
    status: 'planned',
  },
  {
    id: 'licensed_conducted',
    labelKey: 'routes.fields.areas.licensed_conducted.label',
    blurbKey: 'routes.fields.areas.licensed_conducted.blurb',
    status: 'planned',
  },
] as const;

/** Lookup one area by id (undefined for an unknown id — caller decides fallback). */
export function findWorkbenchArea(id: string): WorkbenchArea | undefined {
  return WORKBENCH_AREAS.find((area) => area.id === id);
}

/** True when `id` is a known, currently-available workbench area (selectable). */
export function isAvailableArea(id: string): boolean {
  return findWorkbenchArea(id)?.status === 'available';
}
