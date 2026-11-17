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
