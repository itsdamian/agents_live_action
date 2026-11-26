// Desk sizing (plan §11.5). Pure: the Floor applies the result as CSS custom properties.
//
//   > 520 px:  desk 166 / tray 92        (compact: desk 120, avatar 96, bubble 2 lines)
//   ≤ 520 px:  desk 150 / tray 78        (compact: desk 112, avatar 96, bubble 2 lines)
// Compact applies to a line of more than 6 seats, or when a normal desk would leave fewer than
// two per row.

export const PAGE_GUTTER = 16;
export const STAGE_PADDING = 14;
export const DESK_GAP = 10;

export interface DeskMetrics {
  compact: boolean;
  deskWidth: number;
  trayWidth: number;
  avatarWidth: number;
  avatarHeight: number;
  bubbleHeight: number;
  /** Bubble lines before truncation. */
  bubbleLines: 2 | 3;
  /** How many desks fit side by side in the stage. */
  perRow: number;
}

export function availableWidth(viewportWidth: number): number {
  return Math.max(0, viewportWidth - 2 * PAGE_GUTTER - 2 * STAGE_PADDING);
}

export function perRow(available: number, deskWidth: number): number {
  return Math.max(1, Math.floor((available + DESK_GAP) / (deskWidth + DESK_GAP)));
}

export function deskMetrics(viewportWidth: number, seatCount: number): DeskMetrics {
  const small = viewportWidth <= 520;
  const available = availableWidth(viewportWidth);
  const normalWidth = small ? 150 : 166;
  const compact = seatCount > 6 || perRow(available, normalWidth) < 2;
  const deskWidth = compact ? (small ? 112 : 120) : normalWidth;
  return {
    compact,
    deskWidth,
    trayWidth: small ? 78 : compact ? 80 : 92,
    avatarWidth: compact ? 96 : 132,
    avatarHeight: compact ? 86 : 118,
    bubbleHeight: compact ? 54 : 76,
    bubbleLines: compact ? 2 : 3,
    perRow: perRow(available, deskWidth),
  };
}

/** CSS custom properties for a Floor. */
export function metricsStyle(m: DeskMetrics): Record<string, string> {
  return {
    "--desk-w": `${m.deskWidth}px`,
    "--tray-w": `${m.trayWidth}px`,
    "--avatar-w": `${m.avatarWidth}px`,
    "--avatar-h": `${m.avatarHeight}px`,
    "--bubble-h": `${m.bubbleHeight}px`,
    "--bubble-lines": String(m.bubbleLines),
  };
}
