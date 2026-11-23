import type { LlmChoice } from "../types/events";
import { DEFAULT_VALUE, choiceLabel, fromSelectValue, llmOptions, resolveLlmLocal, toSelectValue } from "./llmChoice";

const models = [{ id: "claude-opus-5-5", label: "Claude Opus 5.5" }, { id: "claude-haiku-5-5", label: "Claude Haiku 5.5" }];

describe("select values", () => {
  it("round-trips default, mock and a Claude model", () => {
    const values: Array<LlmChoice | null> = [null, "mock", { provider: "anthropic", model: "claude-opus-5-5" }];
    for (const v of values) {
      expect(fromSelectValue(toSelectValue(v))).toEqual(v);
    }
    expect(toSelectValue(null)).toBe(DEFAULT_VALUE);
  });
  it("unknown values mean 'follow the project default'", () => {
    expect(fromSelectValue("anthropic:")).toBeNull();
    expect(fromSelectValue("gpt")).toBeNull();
  });
});

describe("llmOptions", () => {
  it("lists only what the server sent, with or without the default entry", () => {
    expect(llmOptions(models, { withDefault: true }).map((o) => o.label)).toEqual(["跟專案預設", "Mock", "Claude Opus 5.5", "Claude Haiku 5.5"]);
    expect(llmOptions(models, { withDefault: false }).map((o) => o.label)).toEqual(["Mock", "Claude Opus 5.5", "Claude Haiku 5.5"]);
    expect(llmOptions([], { withDefault: false })).toEqual([{ value: "mock", label: "Mock" }]);
  });
  it("choiceLabel falls back to the model id", () => {
    expect(choiceLabel(models, { provider: "anthropic", model: "claude-haiku-5-5" })).toBe("Claude Haiku 5.5");
    expect(choiceLabel(models, { provider: "anthropic", model: "claude-x" })).toBe("claude-x");
    expect(choiceLabel(models, null)).toBe("跟專案預設");
  });
});

describe("resolveLlmLocal", () => {
  const sonnet = { provider: "anthropic" as const, model: "claude-sonnet-5-5" };
  const env = { forceMock: false, keyPresent: true };
  it("an agent's own choice wins over the project's", () => {
    expect(resolveLlmLocal({ provider: "anthropic", model: "claude-opus-5-5" }, "mock", env)).toMatchObject({ provider: "anthropic", model: "claude-opus-5-5", source: "agent", mock_reason: null });
    expect(resolveLlmLocal(null, sonnet, env)).toMatchObject({ model: "claude-sonnet-5-5", source: "project" });
  });
  it("Mock stands in, with its reason", () => {
    expect(resolveLlmLocal(null, "mock", env).mock_reason).toBe("configured_mock");
    expect(resolveLlmLocal(null, sonnet, { ...env, forceMock: true })).toMatchObject({ provider: "mock", mock_reason: "force_mock" });
    expect(resolveLlmLocal(null, sonnet, { ...env, keyPresent: false })).toMatchObject({ provider: "mock", mock_reason: "missing_key" });
  });
  it("FORCE_MOCK comes first: mock_reason is force_mock even when Mock was chosen (plan D22 / §D21)", () => {
    expect(resolveLlmLocal(null, "mock", { ...env, forceMock: true }).mock_reason).toBe("force_mock");
    expect(resolveLlmLocal("mock", sonnet, { forceMock: true, keyPresent: false })).toMatchObject({ source: "agent", mock_reason: "force_mock" });
  });
});
