// "核准時機" (AC11b): the labels shown to the user, the values sent to the API.

import type { Approval, Priority } from "../types/events";

export const APPROVAL_OPTIONS: ReadonlyArray<{ value: Approval; label: string }> = [
  { value: "none", label: "不用" },
  { value: "after_swe", label: "做完後先給我看" },
  { value: "after_qa", label: "全部通過後給我看" },
];

export const PRIORITY_OPTIONS: ReadonlyArray<{ value: Priority; label: string }> = [
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
  { value: "critical", label: "緊急" },
];

export function approvalLabel(a: Approval): string {
  return APPROVAL_OPTIONS.find((o) => o.value === a)?.label ?? a;
}

export function priorityLabel(p: Priority): string {
  return PRIORITY_OPTIONS.find((o) => o.value === p)?.label ?? p;
}

export const STATUS_LABELS = {
  pending: "待辦",
  in_progress: "進行中",
  awaiting_approval: "等你核准",
  done: "完成",
  failed: "失敗",
} as const;
