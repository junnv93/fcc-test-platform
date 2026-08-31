/** @fcc/api-artifacts — type declarations (B3 / P14). */

export type ArtifactKind = 'openapi' | 'db-schema';

export interface ArtifactManifestEntry {
  name: string;
  file: string;
  kind: ArtifactKind;
  codegen: boolean;
  typesBasename: string | null;
  docsSource: string;
}

export interface ArtifactManifest {
  package: string;
  description: string;
  artifacts: ArtifactManifestEntry[];
}

export interface Artifact extends ArtifactManifestEntry {
  /** Absolute on-disk path of the artifact inside the package. */
  path: string;
}

export interface OpenApiSpec {
  name: string;
  file: string;
  path: string;
  typesBasename: string;
}

export const MANIFEST: ArtifactManifest;
export const ARTIFACTS: ReadonlyArray<Artifact>;
export const OPENAPI_SPECS: ReadonlyArray<OpenApiSpec>;

export function resolveArtifact(name: string): string;
export function loadArtifact(name: string): unknown;
