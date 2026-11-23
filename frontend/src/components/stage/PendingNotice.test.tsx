import { screen } from "@testing-library/react";
import { apply, ready } from "../../test/factories";
import { renderWith } from "../../test/render";
import { PendingNotice } from "./PendingNotice";

describe("PendingNotice (the 202 mark)", () => {
  it("is absent without a pending lineup", () => {
    renderWith(<PendingNotice />, ready());
    expect(screen.queryByTestId("pending-notice")).toBeNull();
  });
  it("names the task the change waits for, and goes away on pipeline:updated", () => {
    const s = apply(ready(), "pipeline:pending", { pipeline_id: "main", seats: ["pm", "qa"], reason: "task_in_flight", task_id: "task-004" });
    const { update } = renderWith(<PendingNotice />, s);
    expect(screen.getByTestId("pending-notice")).toHaveTextContent("等 task-004 做完，下一張任務開始時生效");
    update(apply(s, "pipeline:updated", { pipeline: { id: "main", name: "主線", seats: ["pm", "qa"] }, agents: s.agents ? Object.values(s.agents) : [] }));
    expect(screen.queryByTestId("pending-notice")).toBeNull();
  });
});
