import { render, screen } from "@testing-library/react";
import { useState } from "react";
import userEvent from "@testing-library/user-event";
import { ConfirmDialog } from "./ConfirmDialog";

function Harness({ onConfirm = () => {}, onCancel = () => {} }) {
  return (
    <>
      <button type="button">開啟</button>
      <ConfirmDialog title="確定嗎？" confirmLabel="好" onConfirm={onConfirm} onCancel={onCancel}>內容</ConfirmDialog>
    </>
  );
}

describe("ConfirmDialog", () => {
  it("is an alertdialog named by its title and described by its body", () => {
    render(<Harness />);
    const d = screen.getByRole("alertdialog", { name: "確定嗎？" });
    expect(d).toHaveAttribute("aria-modal", "true");
    expect(d).toHaveAccessibleDescription("內容");
  });

  it("starts on the cancel button and keeps Tab / Shift+Tab inside", async () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "好" })).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "好" })).toHaveFocus();
  });

  it("Escape cancels, the buttons confirm / cancel", async () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<Harness onConfirm={onConfirm} onCancel={onCancel} />);
    await userEvent.keyboard("{Escape}");
    expect(onCancel).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "好" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("gives focus back to the opener when it closes", async () => {
    function Toggle() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>開啟</button>
          {open && <ConfirmDialog title="確定嗎？" confirmLabel="好" onConfirm={() => setOpen(false)} onCancel={() => setOpen(false)}>內容</ConfirmDialog>}
        </>
      );
    }
    render(<Toggle />);
    await userEvent.click(screen.getByRole("button", { name: "開啟" }));
    expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getByRole("button", { name: "開啟" })).toHaveFocus();
  });
});
