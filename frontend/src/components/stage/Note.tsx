// The paper note of one in-flight task. It flies between desks via useNoteFlight and, after it
// lands in the outbox, stays for the cheer and then disappears.

import { useEffect, useRef, useState } from "react";
import { useNoteFlight } from "../../hooks/useNoteFlight";
import { taskLabel } from "../../lib/notePath";
import { noteAnchor } from "../../state/selectors";
import type { NoteState } from "../../state/reducer";
import styles from "./Note.module.css";

/** How long a finished note rests in the outbox (flight + the desks' cheer). */
export const OUTBOX_HOLD_MS = 1800;

export interface NoteProps {
  note: NoteState;
  /** The stage element notes are positioned against. */
  stage: HTMLElement | null;
  seatIds: string[];
}

export function Note({ note, stage, seatIds }: NoteProps) {
  const ref = useRef<HTMLDivElement>(null);
  const anchor = noteAnchor(note);
  const [gone, setGone] = useState(false);
  useNoteFlight({ noteRef: ref, stage, anchor, seatIds });

  const atOutbox = note.at === "outbox";
  useEffect(() => {
    if (!atOutbox) {
      setGone(false);
      return;
    }
    const id = setTimeout(() => setGone(true), OUTBOX_HOLD_MS);
    return () => clearTimeout(id);
  }, [atOutbox]);

  const label = note.failed
    ? `便條「${note.title}」失敗`
    : `便條「${note.title}」${note.reworks > 0 ? `，重做 ${note.reworks} 次` : ""}`;

  return (
    <div
      ref={ref}
      className={styles.note}
      data-tone={note.tone}
      data-at={note.at}
      data-task={note.taskId}
      hidden={gone}
      role="img"
      aria-label={label}
    >
      {taskLabel(note.taskId)}
      {note.reworks > 0 && <i data-testid="rework-badge">{note.reworks}</i>}
    </div>
  );
}
