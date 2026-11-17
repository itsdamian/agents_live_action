import { render, screen } from "@testing-library/react";
import { FactoryProvider } from "./state/FactoryProvider";
import App from "./App";

describe("App", () => {
  it("renders the page heading", () => {
    // replay with an unknown file keeps the test off the network and the backend
    render(
      <FactoryProvider search="?replay=none" fetch={async () => new Response("", { status: 404 })}>
        <App />
      </FactoryProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Agent 工廠現場" })).toBeInTheDocument();
  });
});
