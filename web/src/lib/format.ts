/** US dollars: two decimals normally, four for amounts under one cent (LLM calls are cheap). */
export function formatCost(usd: number): string {
  if (usd === 0) return "$0.00";
  if (Math.abs(usd) < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

/** "每日 $5.00、專案 $20.00"; only the limits that are set; null when none is set. */
export function formatBudget(daily: number | null, project: number | null): string | null {
  const parts: string[] = [];
  if (daily !== null) parts.push(`每日 ${formatCost(daily)}`);
  if (project !== null) parts.push(`專案 ${formatCost(project)}`);
  return parts.length > 0 ? parts.join("、") : null;
}

/** "2026-11-17T09:05:03.123456" → "2026-11-17 09:05:03" (no time-zone guessing; shown as the server wrote it). */
export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  return iso.replace("T", " ").replace(/(\.\d+)?(Z|[+-]\d\d:\d\d)?$/, "").slice(0, 19);
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
