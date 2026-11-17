import { screen } from "@testing-library/react";
import { ready } from "../../test/factories";
import { renderWith } from "../../test/render";
import { factoryReducer, type FactoryState } from "../../state/reducer";
import { ConnectionBanner, connectionBannerText } from "./ConnectionBanner";

const withConn = (s: FactoryState, c: Parameters<typeof factoryReducer>[1]) => factoryReducer(s, c);

describe("ConnectionBanner", () => {
  it("is silent while connecting and while open", () => {
    renderWith(<ConnectionBanner />, withConn(ready(), { type: "connection", status: "connecting" }));
    expect(screen.queryByRole("alert")).toBeNull();
    renderWith(<ConnectionBanner />, withConn(ready(), { type: "connection", status: "open" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the reconnect attempt and delay", () => {
    renderWith(<ConnectionBanner />, withConn(ready(), { type: "connection", status: "reconnecting", attempt: 3, delay: 10000 }));
    expect(screen.getByRole("alert")).toHaveTextContent("連線中斷，正在重新連線…（第 3 次，約 10 秒後）");
  });

  it("shows an error with its reason", () => {
    renderWith(<ConnectionBanner />, withConn(ready(), { type: "connection", status: "error", reason: "Unknown project: x" }));
    expect(screen.getByRole("alert")).toHaveTextContent("連不上伺服器：Unknown project: x");
  });

  it("the text helper covers closed, error without reason and replay", () => {
    expect(connectionBannerText("closed", 0, null, null, "live")).toBe("連線已關閉。");
    expect(connectionBannerText("error", 0, null, null, "live")).toBe("連不上伺服器。");
    expect(connectionBannerText("reconnecting", 1, null, null, "live")).toBe("連線中斷，正在重新連線…（第 1 次）");
    expect(connectionBannerText("reconnecting", 1, 2000, null, "replay")).toBeNull();
    expect(connectionBannerText("error", 0, null, "找不到檔案", "replay")).toBe("回放載入失敗：找不到檔案");
  });
});
