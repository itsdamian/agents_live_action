// What a desk's speech bubble says in each state (MVP: fixed texts and `agent:phase` text only).

import type { DeskView } from "../state/selectors";

export const STATUS_TEXT = {
  idle: "打瞌睡中",
  working: "工作中",
  reject: "被退件了",
  waiting: "等你點頭",
  cheer: "完成！",
  hello: "報到",
  leaving: "再見！",
} as const;

export const PENDING_EXIT_TEXT = "這張做完就離開";

export function statusText(view: DeskView): string {
  if (view.desk.pendingExit && view.desk.state !== "leaving") return PENDING_EXIT_TEXT;
  return STATUS_TEXT[view.desk.state];
}

export function bubbleText(view: DeskView): string {
  const name = view.agent?.display_name ?? view.id;
  const duty = view.agent?.duty ?? "";
  switch (view.desk.state) {
    case "hello":
      return `大家好，我是 ${name}，負責${duty}！`;
    case "working":
      return view.desk.text ?? `${duty}中…`;
    case "reject":
      return "啊，被退件了…";
    case "waiting":
      return view.gate ? `老闆，「${view.gate.title}」這樣可以嗎？` : "老闆，這樣可以嗎？";
    case "idle":
    case "cheer":
    case "leaving":
      return duty;
  }
}

/** Why Mock stands in for a Claude choice. */
export function mockReasonText(reason: "force_mock" | "missing_key"): string {
  return reason === "force_mock" ? "伺服器強制 Mock" : "找不到 API 金鑰";
}

/** Tooltip for a model badge when Mock stands in for something else. */
export function mockHint(reason: "force_mock" | "missing_key"): string {
  return `實際以 Mock 執行（${mockReasonText(reason)}）`;
}
