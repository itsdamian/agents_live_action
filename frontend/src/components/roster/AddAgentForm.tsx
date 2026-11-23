// "新同事報到": plan §4.2's fields and limits, plus the model choice (D21).
// Messages are validated here first (繁中); the server's 422/409 text is shown when it disagrees.

import { useId, useRef, useState, type FormEvent } from "react";
import { useLineup } from "../../hooks/useLineup";
import { DEFAULT_VALUE, fromSelectValue, llmOptions } from "../../lib/llmChoice";
import { LIMITS, validateAgentForm, type FormErrors } from "../../lib/validation";
import { useFactoryState } from "../../state/FactoryProvider";
import ui from "../../styles/ui.module.css";
import { LOOKS, type AgentKind, type Look } from "../../types/events";

export const LOOK_LABELS: Record<Look, string> = {
  cap: "工程帽",
  glasses: "眼鏡",
  headset: "耳機",
  bow: "蝴蝶結",
  antenna: "天線",
  beret: "貝雷帽",
};

export interface AddAgentFormProps {
  /** Called after the agent was created (the panel closes). */
  onDone?: () => void;
}

export function AddAgentForm({ onDone }: AddAgentFormProps) {
  const state = useFactoryState();
  const lineup = useLineup();
  const agents = Object.values(state.agents);
  const seats = lineup.mainSeats;

  const [displayName, setName] = useState("");
  const [duty, setDuty] = useState("");
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState<AgentKind>("make");
  const [look, setLook] = useState<Look>("beret");
  const [after, setAfter] = useState<string>(seats[seats.length - 1] ?? "");
  const [llm, setLlm] = useState(DEFAULT_VALUE);
  const [errors, setErrors] = useState<FormErrors>({});
  const [serverErrors, setServerErrors] = useState<string[]>([]);
  const refs = {
    displayName: useRef<HTMLInputElement>(null),
    duty: useRef<HTMLInputElement>(null),
    prompt: useRef<HTMLTextAreaElement>(null),
    after: useRef<HTMLSelectElement>(null),
  };
  const uid = useId();
  const idOf = (name: string) => `${uid}-${name}`;

  const models = state.llm?.models ?? [];
  const afterValue = seats.includes(after) || after === "" ? after : (seats[seats.length - 1] ?? "");

  async function submit(e: FormEvent) {
    e.preventDefault();
    const found = validateAgentForm({ displayName, duty, prompt, kind, after: afterValue }, { agents, seats });
    setErrors(found);
    setServerErrors([]);
    const order = ["displayName", "duty", "prompt", "after"] as const;
    const first = order.find((k) => found[k]);
    if (first) {
      refs[first].current?.focus();
      return;
    }
    if (found.form) return;
    const result = await lineup.createAgent({
      displayName: displayName.trim(),
      duty: duty.trim(),
      prompt: prompt.trim(),
      kind,
      look,
      llm: fromSelectValue(llm),
      after: afterValue === "" ? null : afterValue,
    });
    if (result.ok) {
      setName("");
      setDuty("");
      setPrompt("");
      setErrors({});
      onDone?.();
    } else {
      setErrors({ form: result.message ?? "送出失敗。" });
      setServerErrors(result.errors ?? []);
    }
  }

  const field = (key: keyof FormErrors) => ({
    "aria-invalid": errors[key] ? (true as const) : undefined,
    "aria-describedby": errors[key] ? idOf(`${key}-err`) : undefined,
  });
  const fieldError = (key: keyof FormErrors) =>
    errors[key] ? (
      <p id={idOf(`${key}-err`)} className={ui.err} role="alert">
        {errors[key]}
      </p>
    ) : null;

  return (
    <form className={ui.form} onSubmit={submit} noValidate aria-label="新同事報到">
      <label className={ui.field}>
        <span>名字（最多 {LIMITS.displayName} 字）</span>
        <input ref={refs.displayName} className={ui.input} value={displayName} onChange={(e) => setName(e.target.value)} placeholder="Designer" autoComplete="off" {...field("displayName")} />
        {fieldError("displayName")}
      </label>
      <label className={ui.field}>
        <span>負責什麼（最多 {LIMITS.duty} 字）</span>
        <input ref={refs.duty} className={ui.input} value={duty} onChange={(e) => setDuty(e.target.value)} placeholder="畫介面草圖" autoComplete="off" {...field("duty")} />
        {fieldError("duty")}
      </label>
      <label className={`${ui.field} ${ui.wide}`}>
        <span>給他的指示（prompt，最多 {LIMITS.prompt} 字）</span>
        <textarea ref={refs.prompt} className={ui.input} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="你是介面設計師，請根據規格畫出草圖……" {...field("prompt")} />
        {fieldError("prompt")}
      </label>
      <fieldset className={ui.fieldset}>
        <legend className={ui.legend}>工作方式</legend>
        <div className={ui.radios}>
          <label>
            <input type="radio" name={idOf("kind")} value="make" checked={kind === "make"} onChange={() => setKind("make")} /> 做事
          </label>
          <label>
            <input type="radio" name={idOf("kind")} value="check" checked={kind === "check"} onChange={() => setKind("check")} /> 把關（可以退件）
          </label>
        </div>
      </fieldset>
      <label className={ui.field}>
        <span>造型</span>
        <select className={ui.input} value={look} onChange={(e) => setLook(e.target.value as Look)}>
          {LOOKS.map((l) => (
            <option key={l} value={l}>{LOOK_LABELS[l]}</option>
          ))}
        </select>
      </label>
      <label className={ui.field}>
        <span>座位</span>
        <select ref={refs.after} className={ui.input} value={afterValue} onChange={(e) => setAfter(e.target.value)} {...field("after")}>
          <option value="">排最前面</option>
          {seats.map((id) => (
            <option key={id} value={id}>坐在 {state.agents[id]?.display_name ?? id} 後面</option>
          ))}
        </select>
        {fieldError("after")}
      </label>
      <label className={ui.field}>
        <span>模型</span>
        <select className={ui.input} value={llm} onChange={(e) => setLlm(e.target.value)}>
          {llmOptions(models, { withDefault: true }).map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </label>
      <div className={`${ui.actions} ${ui.wide}`}>
        <button type="submit" className={`${ui.btn} ${ui.primary}`} disabled={lineup.busy}>
          請他上工
        </button>
        {errors.form && (
          <div role="alert">
            <p className={ui.err}>{errors.form}</p>
            {serverErrors.map((m) => (
              <p key={m} className={ui.err}>{m}</p>
            ))}
          </div>
        )}
      </div>
    </form>
  );
}
