import { act, renderHook } from "@testing-library/react";
import { vi } from "vitest";
import { createLiveText, useLiveText } from "./liveText";

const tok = (station: string, seq: number, text: string, attempt = 1) => ({
  task_id: "t1", station, attempt, seq, text, final: false, truncated: false,
});

describe("liveText store", () => {
  it("get returns '' for unknown stations; set/append/clear work", () => {
    const s = createLiveText();
    expect(s.get("pm")).toBe("");
    s.set("pm", "ab");
    s.append("pm", "cd");
    expect(s.get("pm")).toBe("abcd");
    s.clear("pm");
    expect(s.get("pm")).toBe("");
  });

  it("notifies subscribers on change only, and unsubscribe stops notifications", () => {
    const s = createLiveText();
    const l = vi.fn();
    const off = s.subscribe(l);
    s.set("pm", "a");
    s.set("pm", "a"); // same value
    s.clear("swe"); // nothing to clear
    expect(l).toHaveBeenCalledTimes(1);
    off();
    s.set("pm", "b");
    expect(l).toHaveBeenCalledTimes(1);
  });

  it("applyToken appends in seq order, drops duplicates and late tokens, restarts on a new attempt", () => {
    const s = createLiveText();
    s.applyToken(tok("swe", 1, "He"));
    s.applyToken(tok("swe", 2, "llo"));
    s.applyToken(tok("swe", 2, "llo")); // duplicate
    s.applyToken(tok("swe", 1, "He")); // late
    expect(s.get("swe")).toBe("Hello");
    s.applyToken(tok("swe", 1, "Retry", 2)); // attempt 2 starts over
    expect(s.get("swe")).toBe("Retry");
    s.applyToken(tok("qa", 1, "other"));
    expect(s.get("qa")).toBe("other");
  });

  it("clearAll empties everything and notifies once", () => {
    const s = createLiveText();
    s.set("pm", "a");
    s.set("swe", "b");
    const l = vi.fn();
    s.subscribe(l);
    s.clearAll();
    expect(s.get("pm") + s.get("swe")).toBe("");
    expect(l).toHaveBeenCalledTimes(1);
    s.clearAll(); // already empty
    expect(l).toHaveBeenCalledTimes(1);
  });

  it("useLiveText re-renders only the subscribed station's hook", () => {
    const s = createLiveText();
    let pmRenders = 0;
    let sweRenders = 0;
    const pm = renderHook(() => { pmRenders++; return useLiveText(s, "pm"); });
    const swe = renderHook(() => { sweRenders++; return useLiveText(s, "swe"); });
    const [p0, w0] = [pmRenders, sweRenders];
    act(() => s.applyToken(tok("pm", 1, "hi")));
    expect(pm.result.current).toBe("hi");
    expect(swe.result.current).toBe("");
    expect(pmRenders).toBeGreaterThan(p0);
    expect(sweRenders).toBe(w0);
  });
});
