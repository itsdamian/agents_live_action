// The character: SVG copied from the prototype (agent-live-stage.html `looks()` / `avatar()`).
// Animations live in Avatar.module.css and are driven by `data-state` on the <svg>.

import type { Look } from "../../types/events";
import type { DeskStateName } from "../../state/deskMachine";
import styles from "./Avatar.module.css";

const K = "var(--char-ink)";

function LookShape({ look }: { look: Look }) {
  switch (look) {
    case "cap":
      return (
        <>
          <path d="M36 44 Q60 12 84 44 Z" fill={K} />
          <rect x="30" y="41" width="64" height="7" rx="3.5" fill={K} />
        </>
      );
    case "glasses":
      return (
        <g fill="none" stroke={K} strokeWidth="3">
          <circle cx="46" cy="64" r="10" />
          <circle cx="74" cy="64" r="10" />
          <path d="M56 64h8" />
        </g>
      );
    case "headset":
      return (
        <>
          <path d="M30 66 Q30 28 60 28 Q90 28 90 66" fill="none" stroke={K} strokeWidth="4" />
          <rect x="24" y="58" width="10" height="18" rx="5" fill={K} />
          <rect x="86" y="58" width="10" height="18" rx="5" fill={K} />
        </>
      );
    case "bow":
      return (
        <>
          <path d="M60 36 L42 26 L42 46 Z M60 36 L78 26 L78 46 Z" fill={K} />
          <circle cx="60" cy="36" r="5" fill={K} />
        </>
      );
    case "antenna":
      return (
        <>
          <path d="M60 40 V18" stroke={K} strokeWidth="3" />
          <circle cx="60" cy="14" r="6" fill={K} />
        </>
      );
    case "beret":
      return (
        <>
          <ellipse cx="56" cy="38" rx="26" ry="10" fill={K} transform="rotate(-10 56 38)" />
          <rect x="58" y="22" width="4" height="9" rx="2" fill={K} />
        </>
      );
  }
}

export interface AvatarProps {
  look: Look;
  color: string;
  state: DeskStateName;
}

export function Avatar({ look, color, state }: AvatarProps) {
  return (
    <svg className={styles.avatar} viewBox="0 0 120 108" aria-hidden="true" data-state={state} data-look={look}>
      <g className={styles.who}>
        <path d="M22 104 Q22 38 60 38 Q98 38 98 104 Z" fill={color} stroke={K} strokeWidth="3" />
        <g className={styles.eyeOpen} fill={K}>
          <circle cx="46" cy="64" r="4.5" />
          <circle cx="74" cy="64" r="4.5" />
        </g>
        <g className={styles.eyeShut} fill="none" stroke={K} strokeWidth="3" strokeLinecap="round">
          <path d="M41 65 q5 4 10 0" />
          <path d="M69 65 q5 4 10 0" />
        </g>
        <path d="M54 76 q6 5 12 0" fill="none" stroke={K} strokeWidth="3" strokeLinecap="round" />
        <path className={styles.sweat} d="M88 46 q5 8 0 11 q-5 -3 0 -11" fill="#6FC3F7" stroke={K} strokeWidth="2" />
        <LookShape look={look} />
        <text className={styles.zzz} x="90" y="40" fontSize="15" fontWeight="700" fill={K} fontFamily="sans-serif">
          z z
        </text>
      </g>
      <rect x="4" y="92" width="112" height="14" rx="5" fill="var(--desk)" stroke={K} strokeWidth="3" />
      <rect x="44" y="76" width="32" height="17" rx="3" fill={K} />
      <rect className={styles.screen} x="48" y="80" width="24" height="9" rx="1.5" fill={color} />
      <rect className={styles.armL} x="24" y="80" width="18" height="9" rx="4.5" fill={color} stroke={K} strokeWidth="3" />
      <rect className={styles.armR} x="78" y="80" width="18" height="9" rx="4.5" fill={color} stroke={K} strokeWidth="3" />
    </svg>
  );
}
