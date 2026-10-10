import { useEffect, useRef, type RefObject } from 'react';

const FOCUSABLE = [
  'a[href]', 'area[href]', 'button:not([disabled])', 'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])', 'textarea:not([disabled])', 'iframe', '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(',');

export function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.hasAttribute('inert') && el.getClientRects().length > 0,
  );
}

interface Options {
  /** Element to focus on open. Defaults to the first focusable element. */
  initialFocus?: RefObject<HTMLElement | null>;
  /** Close on Escape (default true). */
  closeOnEscape?: boolean;
}

/**
 * Dialog behaviour shared by Modal and SidePanel:
 * focus moves into the dialog and is trapped there, Escape closes it,
 * everything else on the page is made `inert` (not focusable, hidden from
 * assistive tech) and focus returns to the trigger on close.
 *
 * The dialog element must be rendered in a portal directly under <body>.
 */
export function useModalBehavior(
  open: boolean,
  dialogRef: RefObject<HTMLElement | null>,
  onClose: () => void,
  { initialFocus, closeOnEscape = true }: Options = {},
) {
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    // Inert everything outside the dialog's top-level portal node.
    let portalRoot: HTMLElement = dialog;
    while (portalRoot.parentElement && portalRoot.parentElement !== document.body) {
      portalRoot = portalRoot.parentElement;
    }
    const inerted: HTMLElement[] = [];
    for (const el of Array.from(document.body.children) as HTMLElement[]) {
      if (el === portalRoot || el.hasAttribute('inert') || el.tagName === 'SCRIPT') continue;
      el.setAttribute('inert', '');
      inerted.push(el);
    }

    const focusFirst = () => {
      const target = initialFocus?.current ?? focusableIn(dialog)[0] ?? dialog;
      target.focus({ preventScroll: true });
    };
    // Wait a frame so enter animations have mounted the content.
    const raf = requestAnimationFrame(focusFirst);

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && closeOnEscape) {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const items = focusableIn(dialog);
      if (items.length === 0) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !dialog.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !dialog.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    dialog.addEventListener('keydown', onKey);

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      cancelAnimationFrame(raf);
      dialog.removeEventListener('keydown', onKey);
      for (const el of inerted) el.removeAttribute('inert');
      document.body.style.overflow = prevOverflow;
      if (previouslyFocused && document.contains(previouslyFocused)) {
        previouslyFocused.focus({ preventScroll: true });
      }
    };
  }, [open, dialogRef, initialFocus, closeOnEscape]);
}
