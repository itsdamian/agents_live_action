// LLM choices (plan D21). The model list always comes from the snapshot's `llm.models`;
// nothing here hard-codes a model name.

import type { EffectiveLlm, LlmChoice, LlmModelOption } from "../types/events";

export const DEFAULT_VALUE = "default";
export const MOCK_VALUE = "mock";
const PREFIX = "anthropic:";

export function toSelectValue(llm: LlmChoice | null): string {
  if (llm === null) return DEFAULT_VALUE;
  return llm === "mock" ? MOCK_VALUE : `${PREFIX}${llm.model}`;
}

/** `null` means "follow the project default". Unknown strings also map to null. */
export function fromSelectValue(value: string): LlmChoice | null {
  if (value === MOCK_VALUE) return "mock";
  if (value.startsWith(PREFIX) && value.length > PREFIX.length) {
    return { provider: "anthropic", model: value.slice(PREFIX.length) };
  }
  return null;
}

export interface LlmOption {
  value: string;
  label: string;
}

export function llmOptions(models: LlmModelOption[], opts: { withDefault: boolean }): LlmOption[] {
  return [
    ...(opts.withDefault ? [{ value: DEFAULT_VALUE, label: "跟專案預設" }] : []),
    { value: MOCK_VALUE, label: "Mock" },
    ...models.map((m) => ({ value: `${PREFIX}${m.id}`, label: m.label })),
  ];
}

export function choiceLabel(models: LlmModelOption[], choice: LlmChoice | null): string {
  if (choice === null) return "跟專案預設";
  if (choice === "mock") return "Mock";
  return models.find((m) => m.id === choice.model)?.label ?? choice.model;
}

export interface LlmEnv {
  forceMock: boolean;
  keyPresent: boolean;
}

/** Mirror of the server's `resolve_llm` (used for the replay fakes only). */
export function resolveLlmLocal(agentLlm: LlmChoice | null, projectLlm: LlmChoice, env: LlmEnv): EffectiveLlm {
  const choice = agentLlm ?? projectLlm;
  const source = agentLlm !== null ? "agent" : "project";
  // FORCE_MOCK is checked before anything else (plan D22): it is the reason even for a Mock choice.
  if (env.forceMock) return { provider: "mock", model: "mock-model", source, mock_reason: "force_mock" };
  if (choice === "mock") return { provider: "mock", model: "mock-model", source, mock_reason: "configured_mock" };
  if (!env.keyPresent) return { provider: "mock", model: "mock-model", source, mock_reason: "missing_key" };
  return { provider: "anthropic", model: choice.model, source, mock_reason: null };
}
