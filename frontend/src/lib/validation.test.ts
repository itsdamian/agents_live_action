import { agent, snapshot } from "../test/factories";
import {
  LIMITS, deriveAgentId, insertSeat, validateAgentForm, validateSeats, type AgentFormValues,
} from "./validation";

const base: AgentFormValues = { displayName: "設計師", duty: "畫介面草圖", prompt: "你是設計師", kind: "make", after: "pm" };
const agents = snapshot().agents;
const ctx = { agents, seats: ["pm", "swe", "qa"] };
const errorsOf = (over: Partial<AgentFormValues>, c = ctx) => validateAgentForm({ ...base, ...over }, c);

describe("validateAgentForm", () => {
  it("accepts a valid form", () => {
    expect(errorsOf({})).toEqual({});
  });

  it("name: required, ≤14 characters, no control characters, no duplicates", () => {
    expect(errorsOf({ displayName: "   " }).displayName).toBe("名字要填。");
    expect(errorsOf({ displayName: "一二三四五六七八九十一二三四" }).displayName).toBeUndefined(); // exactly 14
    expect(errorsOf({ displayName: "一二三四五六七八九十一二三四五" }).displayName).toBe("名字最多 14 個字。");
    expect(errorsOf({ displayName: "a\nb" }).displayName).toContain("控制字元");
    expect(errorsOf({ displayName: "a​b" }).displayName).toContain("控制字元"); // Cf: zero-width space
    expect(errorsOf({ displayName: "pm" }).displayName).toBe("已經有一位 pm 了，換個名字。");
  });

  it("counts emoji as one character", () => {
    expect(errorsOf({ displayName: "😀".repeat(14) }).displayName).toBeUndefined();
    expect(errorsOf({ displayName: "😀".repeat(15) }).displayName).toContain("最多 14");
  });

  it("duty: required, ≤24 characters", () => {
    expect(errorsOf({ duty: "" }).duty).toBe("負責的事要填。");
    expect(errorsOf({ duty: "字".repeat(24) }).duty).toBeUndefined();
    expect(errorsOf({ duty: "字".repeat(25) }).duty).toBe("負責的事最多 24 個字。");
    expect(errorsOf({ duty: "a\tb" }).duty).toContain("控制字元");
  });

  it("prompt: required, ≤2000 characters (newlines allowed)", () => {
    expect(errorsOf({ prompt: "" }).prompt).toBe("要寫下給他的指示。");
    expect(errorsOf({ prompt: "第一行\n第二行" }).prompt).toBeUndefined();
    expect(errorsOf({ prompt: "x".repeat(2000) }).prompt).toBeUndefined();
    expect(errorsOf({ prompt: "x".repeat(2001) }).prompt).toBe("指示最多 2000 個字。");
  });

  it("a check agent cannot take the first seat", () => {
    expect(errorsOf({ kind: "check", after: "" }).after).toContain("第一席");
    expect(errorsOf({ kind: "check", after: "qa" }).after).toBeUndefined();
    expect(errorsOf({ kind: "make", after: "" }).after).toBeUndefined();
  });

  it("limits: 16 custom agents, 8 seats per line", () => {
    const customs = Array.from({ length: LIMITS.customAgents }, (_, i) => agent(`c${i}`, { builtin: false, display_name: `C${i}` }));
    expect(errorsOf({}, { agents: [...agents, ...customs], seats: ["pm"] }).form).toContain("16");
    const eight = Array.from({ length: 8 }, (_, i) => `s${i}`);
    expect(errorsOf({}, { agents, seats: eight }).form).toContain("8 席");
    expect(errorsOf({}, { agents, seats: eight.slice(0, 7) }).form).toBeUndefined();
  });
});

describe("validateSeats", () => {
  const roster = Object.fromEntries([...agents, agent("sec", { kind: "check", builtin: false })].map((a) => [a.id, a]));
  it("accepts 1–8 unique known seats with a make and a non-check first", () => {
    expect(validateSeats(["pm"], roster)).toBeNull();
    expect(validateSeats(["pm", "swe", "qa", "sec"], roster)).toBeNull();
  });
  it("rejects each broken rule with a reason", () => {
    expect(validateSeats([], roster)).toContain("至少要留一位");
    expect(validateSeats(Array.from({ length: 9 }, (_, i) => `x${i}`), roster)).toContain("最多 8");
    expect(validateSeats(["pm", "pm"], roster)).toContain("兩個位子");
    expect(validateSeats(["pm", "ghost"], roster)).toContain("不在名單");
    expect(validateSeats(["qa", "sec"], roster)).toContain("做事");
    expect(validateSeats(["qa", "pm"], roster)).toContain("第一席");
  });
});

describe("insertSeat / deriveAgentId", () => {
  it("inserts behind `after`, at the front for null, and appends for an unknown id", () => {
    expect(insertSeat(["pm", "swe"], "pm", "x")).toEqual(["pm", "x", "swe"]);
    expect(insertSeat(["pm", "swe"], null, "x")).toEqual(["x", "pm", "swe"]);
    expect(insertSeat(["pm", "swe"], "ghost", "x")).toEqual(["pm", "swe", "x"]);
  });
  it("derives a legal id, avoiding reserved words and taken ids", () => {
    expect(deriveAgentId("Designer", [])).toBe("designer");
    expect(deriveAgentId("Code Review!", [])).toBe("code-review");
    expect(deriveAgentId("設計師", [])).toBe("agent-01");
    expect(deriveAgentId("設計師", ["agent-01"])).toBe("agent-02");
    expect(deriveAgentId("pm", [])).toBe("agent-01");
    expect(deriveAgentId("Designer", ["designer"])).toBe("agent-01");
    expect(deriveAgentId("x", [])).toBe("agent-01"); // one character is too short
  });
});
