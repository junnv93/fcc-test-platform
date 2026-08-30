import type { UseTranslation } from '@/i18n';

/**
 * `Translate` — the `t` callable, nameable.
 *
 * The column descriptors introduced by §M7.2 are built by plain functions
 * rather than inside a component, so they need the translator passed in and
 * therefore need a NAME for its type. Deriving it from `UseTranslation` keeps
 * `@/i18n` the single owner of the signature: widening `t` there propagates
 * here instead of drifting against five hand-written copies in the routes.
 */
export type Translate = UseTranslation['t'];
