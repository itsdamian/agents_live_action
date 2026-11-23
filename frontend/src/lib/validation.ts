// Client-side mirror of plan §4.2's rules, so mistakes get a friendly message before the request.
// The server stays the authority (it answers 422/409 with its own `detail` / `errors`).

import type { AgentKind, AgentView } from "../types/events";

export const LIMITS = { displayName: 14, duty: 24, prompt: 2000, customAgents: 16, seatsPerLine: 8 } as const;

export const RESERVED_IDS: readonly string[] = [
  "pm", "swe", "qa", "devops", "intake", "gate_enter", "gate_wait", "gate_exit", "rework", "done", "fail",
  "start", "end", "main", "agents", "pipelines", "history", "new", "export", "import",
];
const AGENT_ID_RE = /^[a-z][a-z0-9-]{1,23}$/;
const CONTROL_CHARS = /[\p{Cc}\p{Cf}]/u;

const length = (s: string) => Array.from(s).length;

export interface AgentFormValues {
  displayName: string;
  duty: string;
  prompt: string;
  kind: AgentKind;
  /** "" = first seat, else the id the new agent sits behind. */
  after: string;
}

export type FormErrors = Partial<Record<"displayName" | "duty" | "prompt" | "after" | "form", string>>;

export interface FormContext {
  agents: AgentView[];
  /** Seat ids of the pipeline the agent will join. */
  seats: string[];
}

export function validateAgentForm(v: AgentFormValues, ctx: FormContext): FormErrors {
  const errors: FormErrors = {};
  const name = v.displayName.trim();
  const duty = v.duty.trim();

  if (name === "") errors.displayName = "名字要填。";
  else if (length(name) > LIMITS.displayName) errors.displayName = `名字最多 ${LIMITS.displayName} 個字。`;
  else if (CONTROL_CHARS.test(name)) errors.displayName = "名字不能含控制字元（例如換行）。";
  else if (ctx.agents.some((a) => a.display_name.toLowerCase() === name.toLowerCase())) {
    errors.displayName = `已經有一位 ${name} 了，換個名字。`;
  }

  if (duty === "") errors.duty = "負責的事要填。";
  else if (length(duty) > LIMITS.duty) errors.duty = `負責的事最多 ${LIMITS.duty} 個字。`;
  else if (CONTROL_CHARS.test(duty)) errors.duty = "負責的事不能含控制字元（例如換行）。";

  const prompt = v.prompt.trim();
  if (prompt === "") errors.prompt = "要寫下給他的指示。";
  else if (length(prompt) > LIMITS.prompt) errors.prompt = `指示最多 ${LIMITS.prompt} 個字。`;

  if (v.kind === "check" && v.after === "") errors.after = "把關的人不能排在第一席，請選「坐在某人後面」。";

  if (ctx.agents.filter((a) => !a.builtin).length >= LIMITS.customAgents) {
    errors.form = `自訂同事最多 ${LIMITS.customAgents} 位，先請一位離開（刪除）。`;
  } else if (ctx.seats.length >= LIMITS.seatsPerLine) {
    errors.form = `這條線已經有 ${LIMITS.seatsPerLine} 席，先請一位離開。`;
  }
  return errors;
}

/** Why a lineup is not allowed (plan §4.2: 1–8 seats, unique, known, one make, first not check), or null. */
export function validateSeats(seats: string[], agents: Record<string, AgentView>): string | null {
  if (seats.length < 1) return "至少要留一位同事。";
  if (seats.length > LIMITS.seatsPerLine) return `一條線最多 ${LIMITS.seatsPerLine} 席。`;
  if (new Set(seats).size !== seats.length) return "同一位同事不能坐兩個位子。";
  if (seats.some((id) => !agents[id])) return "有同事不在名單裡。";
  if (!seats.some((id) => agents[id]?.kind === "make")) return "至少要有一位「做事」的同事。";
  if (agents[seats[0]!]?.kind === "check") return "把關的人不能排在第一席。";
  return null;
}

/** Insert `id` behind `after` (`null` = first). An unknown `after` appends. */
export function insertSeat(seats: string[], after: string | null, id: string): string[] {
  if (after === null) return [id, ...seats];
  const i = seats.indexOf(after);
  if (i === -1) return [...seats, id];
  return [...seats.slice(0, i + 1), id, ...seats.slice(i + 1)];
}

/** Mirror of the server's id derivation (used for the replay fakes only). */
export function deriveAgentId(displayName: string, taken: Iterable<string>): string {
  const used = new Set(taken);
  const slug = displayName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24);
  if (AGENT_ID_RE.test(slug) && !RESERVED_IDS.includes(slug) && !used.has(slug)) return slug;
  for (let n = 1; n < 100; n++) {
    const id = `agent-${String(n).padStart(2, "0")}`;
    if (!used.has(id)) return id;
  }
  return `agent-${Date.now() % 100000}`;
}
