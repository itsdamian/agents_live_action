import { formatBudget, formatCost } from "./format";

describe("format", () => {
  it("formatCost", () => {
    expect(formatCost(0)).toBe("$0.00");
    expect(formatCost(0.00456)).toBe("$0.0046");
    expect(formatCost(1.2)).toBe("$1.20");
    expect(formatCost(12.345)).toBe("$12.35");
  });
  it("formatBudget lists only the limits that are set", () => {
    expect(formatBudget(5, 20)).toBe("每日 $5.00、專案 $20.00");
    expect(formatBudget(5, null)).toBe("每日 $5.00");
    expect(formatBudget(null, 20)).toBe("專案 $20.00");
    expect(formatBudget(null, null)).toBeNull();
  });
});

import { formatBytes, formatTime } from "./format";

describe("formatTime / formatBytes", () => {
  it("formatTime drops fractions and zones and shows the server's wall clock", () => {
    expect(formatTime("2026-11-17T09:05:03.123456")).toBe("2026-11-17 09:05:03");
    expect(formatTime("2026-11-17T09:05:03Z")).toBe("2026-11-17 09:05:03");
    expect(formatTime("2026-11-17T09:05:03+08:00")).toBe("2026-11-17 09:05:03");
    expect(formatTime(null)).toBe("—");
  });
  it("formatBytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
