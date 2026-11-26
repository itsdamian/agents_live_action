import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PanelsProvider, usePanels } from "./PanelsContext";

function Probe() {
  const p = usePanels();
  return (
    <>
      <output data-testid="panel">{p.panel ? (p.panel.kind === "task-detail" ? `detail:${p.panel.taskId}` : p.panel.kind) : "none"}</output>
      <button onClick={() => p.toggle({ kind: "task-form" })}>form</button>
      <button onClick={() => p.toggle({ kind: "task-detail", taskId: "a" })}>a</button>
      <button onClick={() => p.toggle({ kind: "task-detail", taskId: "b" })}>b</button>
      <button onClick={() => p.open({ kind: "devops" })}>devops</button>
      <button onClick={p.close}>close</button>
    </>
  );
}
const panel = () => screen.getByTestId("panel").textContent;

describe("PanelsProvider", () => {
  it("one panel at a time; toggle closes the same panel and switches to another", async () => {
    render(<PanelsProvider><Probe /></PanelsProvider>);
    expect(panel()).toBe("none");
    await userEvent.click(screen.getByText("form"));
    expect(panel()).toBe("task-form");
    await userEvent.click(screen.getByText("form"));
    expect(panel()).toBe("none");
    await userEvent.click(screen.getByText("a"));
    await userEvent.click(screen.getByText("b"));
    expect(panel()).toBe("detail:b");
    await userEvent.click(screen.getByText("devops"));
    expect(panel()).toBe("devops");
    await userEvent.click(screen.getByText("close"));
    expect(panel()).toBe("none");
  });

  it("usePanels outside a provider is an error", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow(/PanelsProvider/);
    spy.mockRestore();
  });
});
