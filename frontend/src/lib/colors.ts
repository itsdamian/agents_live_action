// Agent colours. The first three are the prototype's palette order for pm / swe / qa;
// other agents get a stable colour from a hash of their id (a seat move must not recolour them).

export const AGENT_COLORS = [
  "#FFC83D", "#5AC8A8", "#8C97FF", "#FF8FB1", "#FF9A4D", "#6FC3F7", "#C59BFF", "#A8D85A",
] as const;

const FIXED: Record<string, string> = { pm: AGENT_COLORS[0], swe: AGENT_COLORS[1], qa: AGENT_COLORS[2] };
const EXTRA = AGENT_COLORS.slice(3);

function hash(text: string): number {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) >>> 0;
  return h;
}

export function agentColor(id: string): string {
  return FIXED[id] ?? EXTRA[hash(id) % EXTRA.length]!;
}
