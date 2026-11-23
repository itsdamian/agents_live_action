import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, vi } from "vitest";
import { Sheet } from "./Sheet";

const original = window.innerWidth;
afterEach(() => {
  window.innerWidth = original;
  document.body.style.overflow = "";
});

function Harness({ onClose = () => {} }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>開啟面板</button>
      <button type="button">別的按鈕</button>
      {open && (
        <Sheet title="測試面板" onClose={() => { onClose(); setOpen(false); }}>
          <label>名字 <input /></label>
          <button type="button">內容按鈕</button>
        </Sheet>
      )}
    </>
  );
}

describe("Sheet on a wide screen", () => {
  beforeEach(() => { window.innerWidth = 1024; });

  it("is an inline panel with a 關閉 button, not a modal", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "開啟面板" }));
    const panel = screen.getByRole("region", { name: "測試面板" });
    expect(panel).toHaveAttribute("data-sheet", "inline");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.body.style.overflow).toBe("");
    await userEvent.click(screen.getByRole("button", { name: "關閉" }));
    expect(screen.queryByRole("region", { name: "測試面板" })).toBeNull();
  });

  it("focus moves into the panel when it opens and back to the opener when it closes", async () => {
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "開啟面板" });
    await userEvent.click(opener);
    expect(screen.getByRole("region", { name: "測試面板" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "測試面板" })).toBeNull();
    expect(opener).toHaveFocus();
  });

  it("Escape inside the panel closes it", async () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "開啟面板" }));
    await userEvent.click(screen.getByLabelText("名字"));
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("Sheet on a phone (≤ 640 px)", () => {
  beforeEach(() => { window.innerWidth = 375; });

  it("is a full-screen modal dialog named by its title, with a back button", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "開啟面板" }));
    const dialog = screen.getByRole("dialog", { name: "測試面板" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAttribute("data-sheet", "full");
    expect(screen.getByRole("button", { name: "返回" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "關閉" })).toBeNull();
  });

  it("640 px is still full screen, 641 px is inline", async () => {
    window.innerWidth = 640;
    const a = render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "開啟面板" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    a.unmount();
    window.innerWidth = 641;
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "開啟面板" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("moves focus into the sheet and keeps Tab inside (the page behind cannot be reached)", async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "開啟面板" }));
    expect(screen.getByRole("button", { name: "返回" })).toHaveFocus();
    const order = ["名字", "內容按鈕", "返回", "名字"];
    for (const name of order) {
      await userEvent.tab();
      const el = document.activeElement as HTMLElement;
      expect(el.getAttribute("aria-label") ?? el.closest("label")?.textContent?.trim() ?? el.textContent).toContain(name);
    }
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "返回" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "內容按鈕" })).toHaveFocus(); // wrapped to the end
  });

  it("Escape and the back button close it and give focus back to the opener", async () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const opener = screen.getByRole("button", { name: "開啟面板" });
    await userEvent.click(opener);
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
    await userEvent.click(opener);
    await userEvent.click(screen.getByRole("button", { name: "返回" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(opener).toHaveFocus();
  });

  it("locks the page scroll while open and restores it afterwards", async () => {
    document.body.style.overflow = "auto";
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "開啟面板" }));
    expect(document.body.style.overflow).toBe("hidden");
    await userEvent.keyboard("{Escape}");
    expect(document.body.style.overflow).toBe("auto");
  });
});
