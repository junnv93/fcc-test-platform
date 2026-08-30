import { useEffect, useRef } from 'react';

import { useT } from '@/i18n';
import { SHORTCUTS } from '@/shared/shortcuts';

import { Button } from './Button';

/**
 * ShortcutHelp — keyboard shortcut reference overlay (card B3 + design-followup
 * #1/#2, 2026-06-20).
 *
 * Opened with `?` and dismissed with Esc / backdrop / close button. The key
 * list is DERIVED from the `@/shared/shortcuts` SSOT — this component never
 * re-declares the key sequences or descriptions, so the overlay can no longer
 * drift from the bindings registered in AppLayout (sealed by
 * `tests/shared/shortcuts-ssot.test.ts`).
 *
 * Rendered as an accessible modal dialog (role="dialog" + aria-modal) with the
 * heading wired via aria-labelledby. Focus is trapped inside the dialog while
 * open (Tab/Shift+Tab cycle the focusable set) and restored to the previously
 * focused element on close — the `aria-modal="true"` contract is now backed by
 * real focus containment, not just the attribute. (A native `<dialog>` +
 * `showModal()` would delegate the trap to the browser, but jsdom does not
 * implement `showModal`, which would make the trap untestable; a dependency-free
 * trap keeps the behavior deterministic and sealed.)
 */

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface ShortcutHelpProps {
  readonly open: boolean;
  readonly onClose: () => void;
}

export function ShortcutHelp({ open, onClose }: ShortcutHelpProps): JSX.Element | null {
  const { t } = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  // Esc closes; Tab/Shift+Tab are trapped inside the dialog; focus lands on the
  // first focusable on open and is restored to the trigger on close.
  useEffect(() => {
    if (!open) return undefined;
    previouslyFocused.current = document.activeElement as HTMLElement | null;

    const focusables = (): HTMLElement[] => {
      const dialog = dialogRef.current;
      if (dialog === null) return [];
      return Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    };

    focusables()[0]?.focus();

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusables();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const dialog = dialogRef.current;
      const active = document.activeElement;
      const outside = dialog === null || active === null || !dialog.contains(active);
      if (event.shiftKey) {
        if (outside || active === first) {
          event.preventDefault();
          last?.focus();
        }
      } else if (outside || active === last) {
        event.preventDefault();
        first?.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      previouslyFocused.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    /* Backdrop click-to-dismiss is a mouse convenience; Escape (handled above)
       is the keyboard-equivalent close, and focus is trapped on the focusable
       controls — so the static-element click rules are satisfied behaviourally. */
    /* eslint-disable-next-line jsx-a11y/no-static-element-interactions, jsx-a11y/click-events-have-key-events */
    <div className="shortcut-help__backdrop" data-testid="shortcut-help-backdrop" onClick={onClose}>
      {/* stopPropagation keeps an in-dialog click from bubbling to the backdrop
          close; it adds no new interaction semantics to the dialog itself, and
          Escape (handled above) is the keyboard close path. */}
      {/* eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/click-events-have-key-events */}
      <div
        ref={dialogRef}
        className="shortcut-help"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcut-help-title"
        data-testid="shortcut-help"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="shortcut-help__header">
          <h2 className="shortcut-help__title" id="shortcut-help-title">
            {t('ui.shortcutHelp.title')}
          </h2>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="shortcut-help__close"
            onClick={onClose}
            aria-label={t('ui.shortcutHelp.closeLabel')}
          >
            ✕
          </Button>
        </div>
        <dl className="shortcut-help__list">
          {SHORTCUTS.map((shortcut) => (
            <div key={shortcut.id} className="shortcut-help__row">
              <dt>
                <kbd className="shortcut-help__keys">{shortcut.sequence}</kbd>
              </dt>
              <dd>{t(shortcut.descriptionKey)}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  );
}

export default ShortcutHelp;
