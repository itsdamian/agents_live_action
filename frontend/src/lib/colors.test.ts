import { AGENT_COLORS, agentColor } from "./colors";

describe("agentColor", () => {
  it("keeps the prototype colours for pm / swe / qa", () => {
    expect([agentColor("pm"), agentColor("swe"), agentColor("qa")]).toEqual(["#FFC83D", "#5AC8A8", "#8C97FF"]);
  });
  it("is stable per id and drawn from the remaining palette", () => {
    expect(agentColor("designer")).toBe(agentColor("designer"));
    for (const id of ["designer", "security", "writer", "a1", "reviewer"]) {
      expect(AGENT_COLORS.slice(3)).toContain(agentColor(id));
    }
  });
});
