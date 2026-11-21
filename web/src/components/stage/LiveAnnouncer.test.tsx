import { screen } from "@testing-library/react";
import { apply, ready, snapshot, summary } from "../../test/factories";
import { renderWith } from "../../test/render";
import { LiveAnnouncer } from "./LiveAnnouncer";

describe("LiveAnnouncer", () => {
  it("is a polite, atomic live region and says nothing on the first state", () => {
    renderWith(<LiveAnnouncer />, apply(ready(), "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "qa", approval: "after_qa" }));
    const region = screen.getByTestId("announcer");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region).toHaveAttribute("aria-atomic", "true");
    expect(region).toHaveTextContent("");
  });

  it("announces a gate that starts waiting later", () => {
    const s = ready();
    const { update } = renderWith(<LiveAnnouncer />, s);
    update(apply(s, "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "qa", approval: "after_qa" }), <LiveAnnouncer />);
    expect(screen.getByTestId("announcer")).toHaveTextContent("QA 等你點頭");
  });
});

describe("LiveAnnouncer after a reconnect", () => {
  it("takes the fresh snapshot as the new starting point instead of news", () => {
    const s = ready();
    const { update } = renderWith(<LiveAnnouncer />, s);
    // While the socket was down: QA left, two tasks finished, one failed. The reconnect's snapshot
    // carries all of it at once; none of it is a live change the person just witnessed.
    const base = snapshot();
    const agents = (base.agents ?? []).filter((a) => a.id !== "qa");
    const after = apply(s, "snapshot", snapshot({
      agents,
      pipelines: [{ id: "main", name: "主線", seats: agents.map((a) => a.id), pending_seats: null }],
      pipeline: { ...base.pipeline, done_count: 2, failed_count: 1 },
    }), 5);
    update(after, <LiveAnnouncer />);
    expect(screen.getByTestId("announcer")).toBeEmptyDOMElement();
    // …and changes after it are news again.
    update(apply(after, "task:failed", { task: summary("t9", "failed", 3), reason: "x", failed_count: 2 }, 6), <LiveAnnouncer />);
    expect(screen.getByTestId("announcer")).toHaveTextContent("一張任務失敗了");
  });
});
