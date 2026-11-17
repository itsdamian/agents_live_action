// The speech bubble. Phase texts are typed out at the prototype's speed; the partial text lives
// in the liveText store (D12) so only this bubble re-renders while it types. With reduced motion
// the whole text appears at once.

import { useLayoutEffect } from "react";
import { useReducedMotion } from "../../hooks/useReducedMotion";
import { MS_PER_CHAR, lastLines, sliceChars } from "../../lib/typewriter";
import { useFactoryEnv } from "../../state/FactoryProvider";
import { useLiveText } from "../../state/liveText";
import type { DeskStateName } from "../../state/deskMachine";
import styles from "./Bubble.module.css";

export interface BubbleProps {
  deskId: string;
  state: DeskStateName;
  text: string;
  /** Type the text out (working, hello, reject, waiting); idle/cheer show it at once. */
  typed: boolean;
}

export function Bubble({ deskId, state, text, typed }: BubbleProps) {
  const { liveText } = useFactoryEnv();
  const reduced = useReducedMotion();
  const partial = useLiveText(liveText, deskId);
  const animate = typed && !reduced;

  // Layout effect: reset before paint so the previous text never flashes under the new state.
  useLayoutEffect(() => {
    if (!animate) {
      liveText.clear(deskId);
      return;
    }
    const total = Array.from(text).length;
    let count = 0;
    liveText.set(deskId, "");
    if (total === 0) return;
    const id = setInterval(() => {
      count += 1;
      liveText.set(deskId, sliceChars(text, count));
      if (count >= total) clearInterval(id);
    }, MS_PER_CHAR);
    return () => {
      clearInterval(id);
      liveText.clear(deskId);
    };
  }, [animate, text, deskId, liveText]);

  const shown = lastLines(animate ? partial : text);
  const typing = animate && shown !== text;
  return (
    <div className={styles.bubble} data-state={state}>
      <span className={styles.text} aria-hidden={typing || undefined}>{shown}</span>
      {typing && <span className={styles.srOnly}>{text}</span>}
    </div>
  );
}
