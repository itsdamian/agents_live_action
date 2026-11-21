// A visually hidden polite live region for the sentences from lib/announce.

import { useEffect, useRef, useState } from "react";
import { announceSnapshot, announcements, type AnnounceSnapshot } from "../../lib/announce";
import { useFactoryState } from "../../state/FactoryProvider";
import ui from "../../styles/ui.module.css";

export function LiveAnnouncer() {
  const state = useFactoryState();
  const [message, setMessage] = useState("");
  const prev = useRef<AnnounceSnapshot | null>(null);
  const seenSnapshot = useRef(0);

  useEffect(() => {
    if (!state.ready) return;
    const next = announceSnapshot(state);
    // The first snapshot (and a reconnect's fresh one) is the starting point, not news.
    const fresh = state.snapshotCount !== seenSnapshot.current;
    seenSnapshot.current = state.snapshotCount;
    const lines = prev.current && !fresh ? announcements(prev.current, next, state) : [];
    prev.current = next;
    if (lines.length > 0) setMessage(lines.join("。"));
  }, [state]);

  return (
    <div className={ui.srOnly} role="status" aria-live="polite" aria-atomic="true" data-testid="announcer">
      {message}
    </div>
  );
}
