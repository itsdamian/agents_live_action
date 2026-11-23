import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { StaticFactoryProvider } from "../../state/FactoryProvider";
import { initialState } from "../../state/reducer";
import { Bubble } from "./Bubble";

let reduced = false;
beforeEach(() => {
  vi.useFakeTimers();
  reduced = false;
  vi.stubGlobal("matchMedia", () => ({ get matches() { return reduced; }, addEventListener: () => {}, removeEventListener: () => {} }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const wrap = (ui: React.ReactElement) => <StaticFactoryProvider state={initialState("demo")}>{ui}</StaticFactoryProvider>;
const tick = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });
const bubbleText = () => document.querySelector("[data-state]")!.firstElementChild!.textContent;

describe("Bubble", () => {
  it("types the text out one character per 26 ms", () => {
    render(wrap(<Bubble deskId="pm" state="working" text="讀任務" typed />));
    expect(bubbleText()).toBe("");
    tick(26);
    expect(bubbleText()).toBe("讀");
    tick(26);
    expect(bubbleText()).toBe("讀任");
    tick(26);
    expect(bubbleText()).toBe("讀任務");
    tick(1000);
    expect(bubbleText()).toBe("讀任務");
  });

  it("restarts when the text changes, without showing the old text", () => {
    const { rerender } = render(wrap(<Bubble deskId="pm" state="working" text="第一句話" typed />));
    tick(26 * 4);
    expect(bubbleText()).toBe("第一句話");
    rerender(wrap(<Bubble deskId="pm" state="working" text="換下一句" typed />));
    expect(bubbleText()).toBe("");
    tick(26 * 2);
    expect(bubbleText()).toBe("換下");
  });

  it("shows the whole text at once with reduced motion", () => {
    reduced = true;
    render(wrap(<Bubble deskId="pm" state="working" text="整段一次出現" typed />));
    expect(bubbleText()).toBe("整段一次出現");
  });

  it("shows idle text immediately (not typed)", () => {
    render(wrap(<Bubble deskId="pm" state="idle" text="把需求拆成規格" typed={false} />));
    expect(screen.getByText("把需求拆成規格")).toBeInTheDocument();
  });

  it("keeps only the last two lines of a long text", () => {
    reduced = true;
    render(wrap(<Bubble deskId="swe" state="working" text={"第一行\n第二行\n第三行"} typed />));
    expect(bubbleText()).toBe("第二行\n第三行");
  });

  it("gives screen readers the full text while typing, hides the partial text", () => {
    render(wrap(<Bubble deskId="pm" state="working" text="完整句子" typed />));
    tick(26);
    const partial = screen.getByText("完");
    expect(partial).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("完整句子")).toBeInTheDocument();
    tick(26 * 5);
    expect(screen.queryByText("完整句子", { selector: "[aria-hidden]" })).toBeNull();
    expect(screen.getAllByText("完整句子")).toHaveLength(1);
  });

  it("exposes the desk state for the border colour", () => {
    render(wrap(<Bubble deskId="pm" state="reject" text="啊" typed={false} />));
    expect(document.querySelector("[data-state]")).toHaveAttribute("data-state", "reject");
  });

  it("stops its timer on unmount", () => {
    const { unmount } = render(wrap(<Bubble deskId="pm" state="working" text="很長很長的一句話" typed />));
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
