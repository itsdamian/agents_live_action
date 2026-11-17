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
