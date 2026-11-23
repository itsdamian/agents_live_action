// Keeps keyboard focus inside a dialog: moves focus in on open, cycles Tab, closes on Escape and
// gives focus back to whatever had it before (the button that opened the dialog).

import { useEffect, type RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface FocusTrapOptions {
  onEscape?: () => void;
  /** Element to focus first (default: the first focusable element). */
  initialFocus?: RefObject<HTMLElement | null>;
}

export function focusableIn(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => !el.hasAttribute("hidden") && el.getAttribute("aria-hidden") !== "true");
}

export function useFocusTrap(containerRef: RefObject<HTMLElement | null>, { onEscape, initialFocus }: FocusTrapOptions = {}): void {
  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    (initialFocus?.current ?? focusableIn(root)[0] ?? root).focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onEscape?.();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusableIn(root);
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0]!;
      const last = items[items.length - 1]!;
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !root.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !root.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    };
    root.addEventListener("keydown", onKey);
    return () => {
      root.removeEventListener("keydown", onKey);
      if (previous && document.contains(previous)) previous.focus();
    };
  }, [containerRef]);
}

/** Non-modal variant: focus moves into `ref` when it mounts and back to the opener when it unmounts. */
export function useFocusOnOpen(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    el.focus({ preventScroll: false });
    return () => {
      if (previous && document.contains(previous)) previous.focus();
    };
  }, [ref]);
}
