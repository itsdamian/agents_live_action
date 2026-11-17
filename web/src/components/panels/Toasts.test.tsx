import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import { DEFAULT_TTL_MS, ERROR_TTL_MS, ToastProvider, useToasts, type ToastApi } from "./Toasts";

let api: ToastApi;
function Probe() {
  api = useToasts();
  return null;
}
const mount = () => render(<ToastProvider><Probe /></ToastProvider>);

beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
afterEach(() => vi.useRealTimers());

describe("Toasts", () => {
  it("shows a toast and removes it after the default time", () => {
    mount();
    act(() => { api.push({ text: "存好了" }); });
    expect(screen.getByRole("status")).toHaveTextContent("存好了");
    act(() => { vi.advanceTimersByTime(DEFAULT_TTL_MS - 1); });
    expect(screen.getByText("存好了")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.queryByText("存好了")).toBeNull();
  });

  it("errors use role=alert and stay longer", () => {
    mount();
    act(() => { api.push({ kind: "error", text: "出錯了" }); });
    expect(screen.getByRole("alert")).toHaveTextContent("出錯了");
    act(() => { vi.advanceTimersByTime(DEFAULT_TTL_MS + 100); });
    expect(screen.getByText("出錯了")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(ERROR_TTL_MS); });
    expect(screen.queryByText("出錯了")).toBeNull();
  });

  it("ttlMs 0 keeps it until dismissed with the × button", async () => {
    mount();
    act(() => { api.push({ text: "常駐", ttlMs: 0 }); });
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(screen.getByText("常駐")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "關閉通知" }));
    expect(screen.queryByText("常駐")).toBeNull();
  });

  it("an action button runs its callback and closes the toast", async () => {
    mount();
    const onClick = vi.fn();
    act(() => { api.push({ text: "任務已派出", action: { label: "看詳情", onClick } }); });
    await userEvent.click(screen.getByRole("button", { name: "看詳情" }));
    expect(onClick).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("任務已派出")).toBeNull();
  });

  it("keeps at most four toasts", () => {
    mount();
    act(() => { for (let i = 1; i <= 6; i++) api.push({ text: `訊息${i}`, ttlMs: 0 }); });
    expect(screen.queryByText("訊息2")).toBeNull();
    expect(screen.getByText("訊息3")).toBeInTheDocument();
    expect(screen.getByText("訊息6")).toBeInTheDocument();
  });

  it("useToasts outside a provider is an error", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow(/ToastProvider/);
    spy.mockRestore();
  });
});
