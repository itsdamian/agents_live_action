import { LEVEL_LABELS, formatAlertValue, highestLevel, metricLabel } from "./alerts";

describe("alerts", () => {
  it("grades info / warning / critical in Chinese", () => {
    expect(LEVEL_LABELS).toEqual({ info: "資訊", warning: "警告", critical: "嚴重" });
  });
  it("highestLevel picks the most severe, or null", () => {
    expect(highestLevel([])).toBeNull();
    expect(highestLevel(["info", "info"])).toBe("info");
    expect(highestLevel(["info", "critical", "warning"])).toBe("critical");
    expect(highestLevel(["warning", "info"])).toBe("warning");
  });
  it("metricLabel falls back to the raw name", () => {
    expect(metricLabel("cpu_percent")).toBe("CPU");
    expect(metricLabel("budget")).toBe("預算");
    expect(metricLabel("queue_depth")).toBe("queue_depth");
  });
  it("formatAlertValue adds % to percent metrics and $ to the budget", () => {
    expect(formatAlertValue("memory_percent", 81.234)).toBe("81.2%");
    expect(formatAlertValue("budget", 5.02)).toBe("$5.02");
    expect(formatAlertValue("queue_depth", 7)).toBe("7");
  });
});
