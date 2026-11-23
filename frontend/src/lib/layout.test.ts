import { PAGE_GUTTER, STAGE_PADDING, availableWidth, deskMetrics, metricsStyle, perRow } from "./layout";

describe("deskMetrics", () => {
  const grid = [
    [1280, 3], [1280, 8], [1280, 24],
    [375, 3], [375, 8], [375, 24],
  ] as const;

  it("never lets a desk exceed the available width (no horizontal scroll)", () => {
    for (const [vw, seats] of grid) {
      const m = deskMetrics(vw, seats);
      expect(m.deskWidth).toBeLessThanOrEqual(availableWidth(vw));
      expect(m.perRow).toBeGreaterThanOrEqual(1);
    }
  });

  it("1280 px, 3 seats: full-size desks like the prototype", () => {
    expect(deskMetrics(1280, 3)).toMatchObject({ compact: false, deskWidth: 166, trayWidth: 92, avatarWidth: 132, avatarHeight: 118, bubbleHeight: 76, bubbleLines: 3 });
  });

  it("1280 px: more than 6 seats switches to compact", () => {
    expect(deskMetrics(1280, 6).compact).toBe(false);
    for (const seats of [7, 8, 24]) {
      expect(deskMetrics(1280, seats)).toMatchObject({ compact: true, deskWidth: 120, avatarWidth: 96, avatarHeight: 86, bubbleLines: 2 });
    }
  });

  it("375 px, 3 seats: 150 px desks, two per row, not compact", () => {
    expect(deskMetrics(375, 3)).toMatchObject({ compact: false, deskWidth: 150, trayWidth: 78, perRow: 2 });
  });

  it("375 px, 8 and 24 seats: 112 px compact desks", () => {
    for (const seats of [8, 24]) {
      expect(deskMetrics(375, seats)).toMatchObject({ compact: true, deskWidth: 112, trayWidth: 78, avatarWidth: 96, bubbleLines: 2 });
    }
  });

  it("falls back to compact when a normal desk would leave fewer than two per row", () => {
    // 340 px wide: available 280 → 150 px desks give a single column
    expect(perRow(availableWidth(340), 150)).toBe(1);
    expect(deskMetrics(340, 3).compact).toBe(true);
  });

  it("breakpoint is at 520 px", () => {
    expect(deskMetrics(520, 3).deskWidth).toBe(150);
    expect(deskMetrics(521, 3).deskWidth).toBe(166);
  });

  it("availableWidth subtracts the page gutter and the stage padding on both sides", () => {
    expect(availableWidth(375)).toBe(375 - 2 * PAGE_GUTTER - 2 * STAGE_PADDING);
    expect(availableWidth(10)).toBe(0);
  });

  it("metricsStyle exposes the CSS custom properties", () => {
    expect(metricsStyle(deskMetrics(1280, 3))).toEqual({
      "--desk-w": "166px", "--tray-w": "92px", "--avatar-w": "132px", "--avatar-h": "118px",
      "--bubble-h": "76px", "--bubble-lines": "3",
    });
  });
});
