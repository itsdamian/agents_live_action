// Which panel is open (one at a time: a task form, a task's detail, the add-agent form, ...).

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

export type Panel =
  | { kind: "add-agent" }
  | { kind: "task-form" }
  | { kind: "task-detail"; taskId: string }
  | { kind: "devops" }
  /** `initial` picks the default filter: the inbox shows pending notes, the outbox done and failed ones. */
  | { kind: "note-wall"; initial?: "inbox" | "outbox" };

export interface PanelsApi {
  panel: Panel | null;
  open(panel: Panel): void;
  close(): void;
  toggle(panel: Panel): void;
}

const PanelsContext = createContext<PanelsApi | null>(null);

const same = (a: Panel | null, b: Panel) => a !== null && a.kind === b.kind && (a.kind !== "task-detail" || (b.kind === "task-detail" && a.taskId === b.taskId));

export function PanelsProvider({ children, initial = null }: { children: ReactNode; initial?: Panel | null }) {
  const [panel, setPanel] = useState<Panel | null>(initial);
  const open = useCallback((p: Panel) => setPanel(p), []);
  const close = useCallback(() => setPanel(null), []);
  const toggle = useCallback((p: Panel) => setPanel((cur) => (same(cur, p) ? null : p)), []);
  const api = useMemo(() => ({ panel, open, close, toggle }), [panel, open, close, toggle]);
  return <PanelsContext.Provider value={api}>{children}</PanelsContext.Provider>;
}

export function usePanels(): PanelsApi {
  const api = useContext(PanelsContext);
  if (!api) throw new Error("usePanels must be used inside <PanelsProvider>");
  return api;
}
