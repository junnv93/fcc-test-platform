import type { WorkbenchAreaId } from './workbench-areas';
import type { components } from '@/api/generated/platform-api.types';

/**
 * Phase 6 P6-1 — read the provider-owned `workbench_area → technologies` mapping
 * from a provider UI descriptor.
 *
 * The mapping is OWNED by the provider (declared in its UI descriptor); the
 * platform forwards it read-only and the frontend consumes it from this runtime
 * data — it must NOT hardcode a `{ area: [tech, ...] }` table (ADR-0010, P6-1
 * MUST-7). This selector is the single consumption point so a later progress
 * dashboard (P6-4) rolls per-technology coverage up to the workbench-area level
 * using the provider's declaration, not a frontend-derived taxonomy.
 */
type ProviderUiDescriptor = components['schemas']['ProviderUiDescriptor'];

/**
 * The single headless provider deployed today (`fcc-unlicensed-conducted`,
 * serving the `unlicensed_conducted` area — mirrors the `workbench-areas` SSOT
 * doc). Report generation reads THIS provider's descriptor to resolve an area's
 * technology scope; only the provider *identity* is a deployment fact hardcoded
 * here — the report-type *values* are never hardcoded (they come from the
 * descriptor's `workbench_area_technologies`, ADR-0010). When further providers
 * (mmWave / radiated / licensed) deploy, resolve the id per area instead of
 * assuming this one.
 */
export const DEPLOYED_PROVIDER_ID = 'fcc-unlicensed-conducted';

/**
 * Technologies the provider declares for a workbench area, or `[]` when the
 * descriptor declares none for that area (an area no deployed provider serves).
 * Order is preserved from the descriptor (provider-defined).
 */
export function technologiesForArea(
  descriptor: ProviderUiDescriptor | undefined | null,
  areaId: WorkbenchAreaId,
): readonly string[] {
  const mapping = descriptor?.workbench_area_technologies;
  if (!mapping) return [];
  return mapping[areaId] ?? [];
}
