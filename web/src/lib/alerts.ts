// DevOps alert presentation: grading, labels and value formatting.

import type { DevOpsLevel } from "../types/events";
import { formatCost } from "./format";

export const LEVEL_LABELS: Record<DevOpsLevel, string> = { info: "資訊", warning: "警告", critical: "嚴重" };
export const LEVEL_ORDER: readonly DevOpsLevel[] = ["info", "warning", "critical"];

const METRIC_LABELS: Record<string, string> = {
  cpu_percent: "CPU",
  memory_percent: "記憶體",
  disk_percent: "磁碟",
  failed_tasks: "失敗任務數",
  budget: "預算",
};

export function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? metric;
}

export function formatAlertValue(metric: string, value: number): string {
  if (metric.endsWith("_percent")) return `${Math.round(value * 10) / 10}%`;
  if (metric === "budget") return formatCost(value);
  return String(value);
}

/** The most severe level among alerts (null when there are none). */
export function highestLevel(levels: readonly DevOpsLevel[]): DevOpsLevel | null {
  let best: DevOpsLevel | null = null;
  for (const l of levels) if (best === null || LEVEL_ORDER.indexOf(l) > LEVEL_ORDER.indexOf(best)) best = l;
  return best;
}
