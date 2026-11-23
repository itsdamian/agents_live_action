import { BUBBLE_MAX_LINES, MS_PER_CHAR, lastLines, sliceChars, visibleCount } from "./typewriter";

describe("visibleCount", () => {
  it("types one character per 26 ms, capped at the length", () => {
    expect(MS_PER_CHAR).toBe(26);
    expect(visibleCount(0, 10)).toBe(0);
    expect(visibleCount(25, 10)).toBe(0);
    expect(visibleCount(26, 10)).toBe(1);
    expect(visibleCount(130, 10)).toBe(5);
    expect(visibleCount(10_000, 10)).toBe(10);
  });
  it("handles empty text and negative time", () => {
    expect(visibleCount(500, 0)).toBe(0);
    expect(visibleCount(-5, 10)).toBe(0);
  });
  it("accepts another speed", () => {
    expect(visibleCount(100, 50, 10)).toBe(10);
  });
});

describe("sliceChars", () => {
  it("slices by characters", () => {
    expect(sliceChars("讀任務：「加登入頁」", 3)).toBe("讀任務");
    expect(sliceChars("abc", 0)).toBe("");
    expect(sliceChars("abc", 99)).toBe("abc");
  });
  it("never splits a surrogate pair", () => {
    expect(sliceChars("a😀b", 2)).toBe("a😀");
  });
});

describe("lastLines", () => {
  it("keeps the last two lines by default", () => {
    expect(BUBBLE_MAX_LINES).toBe(2);
    expect(lastLines("a\nb\nc\nd")).toBe("c\nd");
  });
  it("leaves short text alone and drops trailing blank lines", () => {
    expect(lastLines("one line")).toBe("one line");
    expect(lastLines("a\nb\n\n")).toBe("a\nb");
    expect(lastLines("")).toBe("");
  });
  it("takes any count", () => {
    expect(lastLines("a\nb\nc", 3)).toBe("a\nb\nc");
    expect(lastLines("a\nb\nc", 1)).toBe("c");
  });
});
