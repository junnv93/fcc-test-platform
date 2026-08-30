import { useEffect, useRef } from 'react';

/**
 * use-hotkeys — dependency-free global keyboard shortcut layer (card B3).
 *
 * Internal-tool power users navigate faster by keyboard than by mouse. This
 * hook registers a small set of document-level shortcuts, including two-key
 * "g <x>" sequences (the Gmail/GitHub idiom), without pulling in a hotkey
 * dependency. It deliberately stays out of the way while a form field is
 * focused so typing never triggers navigation OR is swallowed: a key pressed
 * inside an input/textarea/select/contenteditable is left entirely alone (no
 * match, no preventDefault), so a literal "/" typed into a filter is preserved.
 * A binding may opt back in with `allowInField` for the rare global chord that
 * must fire even mid-typing; the default (and every current binding) is off.
 */

/** A single shortcut binding. */
export interface Hotkey {
  /** Space-separated key sequence, e.g. `"?"`, `"/"`, or `"g s"`. Keys match
   *  `KeyboardEvent.key`; `"?"` also matches Shift+Slash across layouts. */
  readonly sequence: string;
  /** Invoked (with the triggering event) when the sequence matches. */
  readonly handler: (event: KeyboardEvent) => void;
  /** Fire even while an input/textarea/select/contenteditable is focused.
   *  Default false so typing never triggers shortcuts. */
  readonly allowInField?: boolean;
}

// A multi-key sequence resets if the next key does not arrive promptly, so a
// stray `g` does not arm navigation indefinitely.
const SEQUENCE_RESET_MS = 1000;
const MAX_BUFFER = 4;

function isFormField(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

/** Normalize a key event to a comparable token. `"?"` is Shift+Slash on most
 *  layouts; accept both the produced `key` and the physical `code` form. */
export function eventToKey(event: KeyboardEvent): string {
  if (event.key === '?') return '?';
  if (event.code === 'Slash' && event.shiftKey) return '?';
  return event.key;
}

export function useHotkeys(hotkeys: readonly Hotkey[], enabled = true): void {
  // Keep the latest handlers without re-binding the listener every render.
  const ref = useRef<readonly Hotkey[]>(hotkeys);
  ref.current = hotkeys;

  useEffect(() => {
    if (!enabled) return undefined;
    let buffer: string[] = [];
    let timer: ReturnType<typeof setTimeout> | undefined;
    const reset = (): void => {
      buffer = [];
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
    };

    const onKeyDown = (event: KeyboardEvent): void => {
      // Leave OS / browser chords (Ctrl/Meta/Alt) untouched.
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const inField = isFormField(event.target);
      // While a form field is focused, only bindings that explicitly opt in with
      // `allowInField` are eligible; everything else is typing. If no eligible
      // binding exists, the key is left ENTIRELY alone — crucially it is NOT
      // pushed onto the sequence buffer, so an in-field key (e.g. a "g" typed
      // into a filter) can never become the prefix of an out-of-field sequence
      // ("g" then a later out-of-field "s" → no "g s" navigation). No
      // preventDefault either, so a literal "/" stays in the input.
      const eligible = inField
        ? ref.current.filter((hotkey) => hotkey.allowInField === true)
        : ref.current;
      if (inField && eligible.length === 0) {
        reset();
        return;
      }
      buffer.push(eventToKey(event));
      if (buffer.length > MAX_BUFFER) buffer = buffer.slice(-MAX_BUFFER);
      if (timer !== undefined) clearTimeout(timer);
      timer = setTimeout(reset, SEQUENCE_RESET_MS);

      for (const hotkey of eligible) {
        const seq = hotkey.sequence.split(' ');
        const tail = buffer.slice(-seq.length);
        if (tail.length === seq.length && tail.every((key, index) => key === seq[index])) {
          event.preventDefault();
          hotkey.handler(event);
          reset();
          return;
        }
      }
      // An eligible (allowInField) binding was registered but did not match this
      // keystroke while typing. Clear the buffer so the in-field key still can't
      // leak into a later out-of-field, non-allowInField sequence match. (A
      // consequence: allowInField chords fire as single keys mid-typing, not as
      // multi-key sequences — intentional, typing must stay unobstructed.)
      if (inField) reset();
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      reset();
    };
  }, [enabled]);
}
