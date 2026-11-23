import { act, renderHook } from "@testing-library/react";
import { vi } from "vitest";
import { useReducedMotion } from "./useReducedMotion";
import { useViewportWidth } from "./useViewport";

function stubMatchMedia(initial: boolean) {
  let matches = initial;
  const listeners = new Set<() => void>();
  const mq = {
    get matches() { return matches; },
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  };
  vi.stubGlobal("matchMedia", () => mq);
  return (value: boolean) => { matches = value; listeners.forEach((l) => l()); };
}

afterEach(() => vi.unstubAllGlobals());

describe("useReducedMotion", () => {
  it("is false when matchMedia is unavailable", () => {
    vi.stubGlobal("matchMedia", undefined);
    expect(renderHook(() => useReducedMotion()).result.current).toBe(false);
  });

  it("reads the media query and follows changes", () => {
    const set = stubMatchMedia(true);
    const { result } = renderHook(() => useReducedMotion());
    expect(result.current).toBe(true);
    act(() => set(false));
    expect(result.current).toBe(false);
  });
});

describe("useViewportWidth", () => {
  it("tracks window resize", () => {
    const original = window.innerWidth;
    try {
      window.innerWidth = 800;
      const { result } = renderHook(() => useViewportWidth());
      expect(result.current).toBe(800);
      act(() => { window.innerWidth = 375; window.dispatchEvent(new Event("resize")); });
      expect(result.current).toBe(375);
    } finally {
      window.innerWidth = original;
    }
  });
});
