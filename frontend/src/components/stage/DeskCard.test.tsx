import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { clockTime, recentText } from "../../lib/recentText";
import { StaticFactoryProvider } from "../../state/FactoryProvider";
import type { RecentAction } from "../../state/recent";
import type { FactoryState } from "../../state/reducer";
import { apply, ready, summary } from "../../test/factories";
import { Stage } from "./Stage";

const original = window.innerWidth;
afterEach(() => {
  window.innerWidth = original;
  document.body.style.overflow = "";
});

const T0 = new Date(2026, 10, 21, 9, 5, 7).getTime();

function busy(title = "加登入頁"): FactoryState {
  const t = summary("t1", "in_progress", 0, title);
  let s = ready();
  s = apply(s, "station:update", { station: "pm", status: "working", task: t }, T0);
  s = apply(s, "station:update", { station: "pm", status: "done", task: t, success: true, error: null, execution_time: 1 }, T0 + 1000);
  return s;
}

const renderStage = (s: FactoryState) => render(<StaticFactoryProvider state={s}><Stage deskCards /></StaticFactoryProvider>);
const deskButton = (name: string) => screen.getByRole("button", { name: `看 ${name} 最近的動作` });

describe("desk card on a wide screen (plan §11.7)", () => {
  beforeEach(() => { window.innerWidth = 1280; });

  it("opens under the desk with the latest actions, newest first", async () => {
    renderStage(busy());
    const btn = deskButton("PM");
    expect(btn).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(btn);
    expect(btn).toHaveAttribute("aria-expanded", "true");
    const card = screen.getByRole("dialog", { name: "PM 最近的動作" });
    expect(btn).toHaveAttribute("aria-controls", card.id);
    expect(card.closest("[data-desk]")).toHaveAttribute("data-desk", "pm");
    const items = within(card).getAllByRole("listitem");
    expect(items.map((li) => li.textContent)).toEqual([
      `${clockTime(T0 + 1000)}交出「加登入頁」`,
      `${clockTime(T0)}接手「加登入頁」`,
    ]);
    expect(card).toHaveFocus();
  });

  it("opens and closes from the keyboard; Escape gives focus back to the desk", async () => {
    renderStage(busy());
    const btn = deskButton("PM");
    btn.focus();
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("dialog", { name: "PM 最近的動作" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(btn).toHaveFocus();
    expect(btn).toHaveAttribute("aria-expanded", "false");
  });

  it("關閉 and a press outside the desk close it; another desk switches the card", async () => {
    renderStage(busy());
    await userEvent.click(deskButton("PM"));
    await userEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(deskButton("PM")).toHaveFocus();

    await userEvent.click(deskButton("PM"));
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();

    await userEvent.click(deskButton("PM"));
    await userEvent.click(deskButton("SWE"));
    expect(screen.getByRole("dialog", { name: "SWE 最近的動作" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "PM 最近的動作" })).toBeNull();
  });

  it("says so when the desk has done nothing since the page opened", async () => {
    renderStage(ready());
    await userEvent.click(deskButton("QA"));
    expect(screen.getByRole("dialog", { name: "QA 最近的動作" })).toHaveTextContent("還沒有動作");
  });

  it("renders server text as text", async () => {
    renderStage(busy("<img src=x onerror=alert(1)>"));
    await userEvent.click(deskButton("PM"));
    const card = screen.getByRole("dialog");
    expect(card.querySelector("img")).toBeNull();
    expect(card).toHaveTextContent("交出「<img src=x onerror=alert(1)>」");
  });
});

describe("desk card on a phone (≤ 640 px)", () => {
  beforeEach(() => { window.innerWidth = 375; });

  it("uses the full-screen Sheet; 返回 closes it and focus returns to the desk", async () => {
    renderStage(busy());
    const btn = deskButton("PM");
    await userEvent.click(btn);
    const sheet = screen.getByRole("dialog", { name: "PM 最近的動作" });
    expect(sheet).toHaveAttribute("data-sheet", "full");
    expect(sheet).toHaveAttribute("aria-modal", "true");
    expect(within(sheet).getAllByRole("listitem")).toHaveLength(2);
    expect(document.querySelector("[data-desk-card]")).toBeNull();
    await userEvent.click(within(sheet).getByRole("button", { name: "返回" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(btn).toHaveFocus();
  });

  it("Escape closes the Sheet", async () => {
    renderStage(busy());
    await userEvent.click(deskButton("PM"));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(deskButton("PM")).toHaveFocus();
  });
});

describe("recentText", () => {
  const a = (over: Partial<RecentAction>): RecentAction => ({ at: 0, taskId: "t", title: "T", kind: "start", reworks: 0, detail: null, target: null, ...over });
  const nameOf = (id: string) => id.toUpperCase();
  it.each([
    [a({}), "接手「T」"],
    [a({ reworks: 2 }), "接手「T」（第 2 次重做）"],
    [a({ kind: "done" }), "交出「T」"],
    [a({ kind: "error", detail: "timeout" }), "「T」出錯：timeout"],
    [a({ kind: "error" }), "「T」出錯"],
    [a({ kind: "rework", reworks: 1 }), "「T」被退回來重做（第 1 次）"],
    [a({ kind: "pass" }), "「T」檢查通過"],
    [a({ kind: "fail", target: "swe" }), "「T」退件 → SWE"],
    [a({ kind: "waiting" }), "「T」等你點頭"],
    [a({ kind: "approved" }), "「T」你點頭了"],
    [a({ kind: "rejected", detail: "再改" }), "「T」你要求重做：再改"],
  ])("%#", (action, text) => {
    expect(recentText(action, nameOf)).toBe(text);
  });

  it("clockTime is HH:MM:SS", () => {
    expect(clockTime(T0)).toBe("09:05:07");
  });
});
