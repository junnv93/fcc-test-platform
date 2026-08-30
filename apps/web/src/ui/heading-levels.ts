/**
 * Heading-level SSOT for the state primitives (contract §M8.3).
 *
 * `SectionBand` owns the section rung of the document outline. `EmptyState`
 * used to render its own `<h2>`, so a section that happened to be empty
 * announced two peer headings and the outline claimed a new section where
 * there was only a placeholder (e.g. `projects.tsx`: SectionBand immediately
 * followed by EmptyState). The two rungs are declared here once, and each
 * primitive derives its tag from this module, so the levels cannot drift back
 * into agreement.
 */

/** Rung owned by `SectionBand` — a real section of the page. */
export const SECTION_HEADING_LEVEL = 2 as const;

/** Rung owned by the state placeholders nested inside a section. */
export const STATE_HEADING_LEVEL = 3 as const;

/** Heading levels a caller may request for a nested state placeholder. */
export type StateHeadingLevel = 2 | 3 | 4;
