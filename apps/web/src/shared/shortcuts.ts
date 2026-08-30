/**
 * Keyboard shortcut SSOT (design-followup card #1, 2026-06-20).
 *
 * Before this module the shortcut universe lived in TWO hand-maintained places
 * — `_layout.tsx::useHotkeys` (sequence → handler) and
 * `ShortcutHelp.tsx::SHORTCUT_ROWS` (keys → description) — kept in step only by
 * a code comment. Adding or renaming a shortcut could silently desync the help
 * overlay from the real bindings, the one SSOT in this app guarded by a comment
 * rather than a gate.
 *
 * This module is the single declaration of every shortcut: its stable `id`, the
 * key `sequence`, the i18n `descriptionKey`, and the `scope` (a `global`
 * shortcut is wired to a document-level handler in AppLayout; a `modal`
 * shortcut — Esc — is handled by the dialog itself and only needs to appear in
 * the help table). Consumers DERIVE from here:
 *
 *   - `_layout.tsx` builds its `useHotkeys` input from {@link GLOBAL_SHORTCUTS}
 *     and a `Record<GlobalShortcutId, …>` handler map — TypeScript fails the
 *     build if a global shortcut has no handler (compile-time completeness).
 *   - `ShortcutHelp.tsx` renders {@link SHORTCUTS} directly (keys + description).
 *
 * Sealed by `tests/shared/shortcuts-ssot.test.ts`: the help overlay's rendered
 * sequence set equals the SHORTCUTS sequence set, and every `descriptionKey`
 * resolves in both `ko.json` and `en.json`.
 */

/** Where a shortcut's key handling lives. `global` = document-level listener in
 *  AppLayout; `modal` = handled by the component that owns the surface (Esc on
 *  the dialog). Both kinds appear in the help overlay. */
export type ShortcutScope = 'global' | 'modal';

/** A single shortcut's declaration — the only place keys + copy are paired. */
export interface ShortcutDef {
  /** Stable identity (handler-map key for global shortcuts). */
  readonly id: string;
  /** Visible/registered key sequence, e.g. `?`, `/`, `g s`, `Esc`. Matches
   *  `useHotkeys` sequence grammar for global shortcuts. */
  readonly sequence: string;
  /** i18n key for the action description (resolved at render). */
  readonly descriptionKey: string;
  /** Handler ownership — see {@link ShortcutScope}. */
  readonly scope: ShortcutScope;
}

/** The shortcut universe. `as const` preserves the literal `id`/`scope` so the
 *  derived id unions below are exact. */
export const SHORTCUTS = [
  { id: 'help', sequence: '?', descriptionKey: 'ui.shortcutHelp.rows.help', scope: 'global' },
  { id: 'search', sequence: '/', descriptionKey: 'ui.shortcutHelp.rows.search', scope: 'global' },
  {
    id: 'goto-sessions',
    sequence: 'g s',
    descriptionKey: 'ui.shortcutHelp.rows.gotoSessions',
    scope: 'global',
  },
  {
    id: 'goto-projects',
    sequence: 'g p',
    descriptionKey: 'ui.shortcutHelp.rows.gotoProjects',
    scope: 'global',
  },
  {
    id: 'goto-jobs',
    sequence: 'g j',
    descriptionKey: 'ui.shortcutHelp.rows.gotoJobs',
    scope: 'global',
  },
  {
    id: 'goto-chambers',
    sequence: 'g c',
    descriptionKey: 'ui.shortcutHelp.rows.gotoChambers',
    scope: 'global',
  },
  {
    id: 'goto-test-plans',
    sequence: 'g t',
    descriptionKey: 'ui.shortcutHelp.rows.gotoTestPlans',
    scope: 'global',
  },
  { id: 'close', sequence: 'Esc', descriptionKey: 'ui.shortcutHelp.rows.close', scope: 'modal' },
] as const satisfies readonly ShortcutDef[];

type Shortcut = (typeof SHORTCUTS)[number];

/** Every shortcut id. */
export type ShortcutId = Shortcut['id'];

/** Only the ids of `global`-scope shortcuts — AppLayout's handler map is keyed
 *  by this union so a new global shortcut without a handler fails `tsc`. */
export type GlobalShortcutId = Extract<Shortcut, { scope: 'global' }>['id'];

/** Global shortcuts only, with the narrowed `scope` literal preserved so
 *  `s.id` is a {@link GlobalShortcutId} at the call site. */
export const GLOBAL_SHORTCUTS: readonly (Shortcut & { scope: 'global' })[] = SHORTCUTS.filter(
  (shortcut): shortcut is Shortcut & { scope: 'global' } => shortcut.scope === 'global',
);
