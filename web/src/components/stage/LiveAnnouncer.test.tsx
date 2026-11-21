import { screen } from "@testing-library/react";
import { apply, ready, summary } from "../../test/factories";
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
