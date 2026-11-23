// Typewriter helpers for the speech bubble (pure; the timer lives in Bubble).

/** Typing speed of the prototype's `say()`. */
export const MS_PER_CHAR = 26;
/** Bubble keeps only the last lines of a long text (relevant once tokens stream in, stage 2). */
export const BUBBLE_MAX_LINES = 2;

/** How many characters are visible after `elapsedMs`. */
export function visibleCount(elapsedMs: number, total: number, msPerChar: number = MS_PER_CHAR): number {
  if (total <= 0 || elapsedMs <= 0) return 0;
  return Math.min(total, Math.floor(elapsedMs / msPerChar));
}

/** First `count` characters, counting code points so emoji and surrogate pairs are never split. */
export function sliceChars(text: string, count: number): string {
  if (count <= 0) return "";
  const chars = Array.from(text);
  return count >= chars.length ? text : chars.slice(0, count).join("");
}

/** The last `maxLines` non-empty lines of `text` (explicit newlines only). */
export function lastLines(text: string, maxLines: number = BUBBLE_MAX_LINES): string {
  const lines = text.split("\n");
  while (lines.length > 0 && lines[lines.length - 1]!.trim() === "") lines.pop();
  return lines.slice(-maxLines).join("\n");
}
