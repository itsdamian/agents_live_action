// Replay mode has no backend: UI actions that would call the API produce the events the
// server would have broadcast instead (same approach as the approval gate in GateControls).

import type { FactoryState } from "../state/reducer";
import type { AgentKind, AgentView, LlmChoice, Look, ServerEvent } from "../types/events";
import { deriveAgentId, insertSeat } from "./validation";
import { resolveLlmLocal } from "./llmChoice";

export const MAIN_PIPELINE = "main";

const roster = (state: FactoryState): AgentView[] => Object.values(state.agents);

function llmEnv(state: FactoryState) {
  return { forceMock: state.forceMock, keyPresent: state.llm?.anthropic_key_present ?? false };
}

function project(state: FactoryState): LlmChoice {
  return state.llm?.project ?? "mock";
}

function pipelineEvent(state: FactoryState, seats: string[], agents: AgentView[]): ServerEvent {
  const p = state.pipelines.find((x) => x.id === MAIN_PIPELINE);
  return {
    event: "pipeline:updated",
    pipeline_id: MAIN_PIPELINE,
    data: { pipeline: { id: MAIN_PIPELINE, name: p?.name ?? "主線", seats }, agents },
  };
}

export interface NewAgentInput {
  displayName: string;
  duty: string;
  kind: AgentKind;
  look: Look;
  llm: LlmChoice | null;
  /** Seat the agent sits behind; null = first. */
  after: string | null;
}

/** `POST /agents` while idle: the agent is created and seated at once. */
export function fakeCreateAgent(state: FactoryState, input: NewAgentInput): { agent: AgentView; events: ServerEvent[] } {
  const id = deriveAgentId(input.displayName, Object.keys(state.agents));
  const agent: AgentView = {
    id,
    display_name: input.displayName,
    duty: input.duty,
    kind: input.kind,
    look: input.look,
    builtin: false,
    llm: input.llm,
    effective_llm: resolveLlmLocal(input.llm, project(state), llmEnv(state)),
  };
  const seats = state.pipelines.find((p) => p.id === MAIN_PIPELINE)?.seats ?? [];
  const next = insertSeat(seats, input.after, id);
  return {
    agent,
    events: [
      { event: "agent:joined", pipeline_id: MAIN_PIPELINE, data: { agent, pipeline_id: MAIN_PIPELINE, position: next.indexOf(id) } },
      pipelineEvent(state, next, [...roster(state), agent]),
    ],
  };
}

/** `PUT /pipelines/main`: who left, who joined, then the new lineup. */
export function fakeSetSeats(state: FactoryState, seats: string[]): ServerEvent[] {
  const before = state.pipelines.find((p) => p.id === MAIN_PIPELINE)?.seats ?? [];
  const events: ServerEvent[] = [];
  for (const id of before) {
    if (!seats.includes(id)) {
      events.push({ event: "agent:left", pipeline_id: MAIN_PIPELINE, data: { agent_id: id, pipeline_id: MAIN_PIPELINE } });
    }
  }
  seats.forEach((id, position) => {
    const agent = state.agents[id];
    if (agent && !before.includes(id)) {
      events.push({ event: "agent:joined", pipeline_id: MAIN_PIPELINE, data: { agent, pipeline_id: MAIN_PIPELINE, position } });
    }
  });
  events.push(pipelineEvent(state, seats, roster(state)));
  return events;
}

/** `DELETE /agents/{id}`: leaves every lineup, then the roster no longer lists it. */
export function fakeDeleteAgent(state: FactoryState, id: string): ServerEvent[] {
  const seats = state.pipelines.find((p) => p.id === MAIN_PIPELINE)?.seats ?? [];
  const rest = roster(state).filter((a) => a.id !== id);
  const events: ServerEvent[] = [];
  if (seats.includes(id)) {
    events.push({ event: "agent:left", pipeline_id: MAIN_PIPELINE, data: { agent_id: id, pipeline_id: MAIN_PIPELINE } });
  }
  events.push(pipelineEvent(state, seats.filter((s) => s !== id), rest));
  return events;
}

/** `PUT /settings`: every agent's effective model is recomputed. */
export function fakeSetProjectLlm(state: FactoryState, choice: LlmChoice): ServerEvent[] {
  const env = llmEnv(state);
  const agents = roster(state).map((a) => ({ ...a, effective_llm: resolveLlmLocal(a.llm, choice, env) }));
  return [{ event: "llm:updated", data: { project_llm: choice, agents, effective: "now" } }];
}
