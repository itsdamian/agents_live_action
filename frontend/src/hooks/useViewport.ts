import { useSyncExternalStore } from "react";

function subscribe(cb: () => void): () => void {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
}

/** Current `window.innerWidth`, updated on resize. */
export function useViewportWidth(): number {
  return useSyncExternalStore(subscribe, () => window.innerWidth, () => 1280);
}
