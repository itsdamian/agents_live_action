// Wording of the desk card's recent actions (plan §11.7). Pure; the card renders the result as
// text nodes only.

import type { RecentAction } from "../state/recent";

/** `HH:MM:SS` in local time. */
export function clockTime(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** One line for a recent action. `nameOf` turns an agent id into its display name. */
export function recentText(a: RecentAction, nameOf: (id: string) => string): string {
  const t = `「${a.title}」`;
  switch (a.kind) {
    case "start":
      return a.reworks > 0 ? `接手${t}（第 ${a.reworks} 次重做）` : `接手${t}`;
    case "done":
      return `交出${t}`;
    case "error":
      return a.detail ? `${t}出錯：${a.detail}` : `${t}出錯`;
    case "rework":
      return `${t}被退回來重做（第 ${a.reworks} 次）`;
    case "pass":
      return `${t}檢查通過`;
    case "fail":
      return a.target ? `${t}退件 → ${nameOf(a.target)}` : `${t}退件`;
    case "waiting":
      return `${t}等你點頭`;
    case "approved":
      return `${t}你點頭了`;
    case "rejected":
      return a.detail ? `${t}你要求重做：${a.detail}` : `${t}你要求重做`;
  }
}
