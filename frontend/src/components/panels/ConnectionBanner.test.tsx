import { ready } from "../../test/factories";
import { renderWith } from "../../test/render";
import { factoryReducer, type FactoryState } from "../../state/reducer";
import { ConnectionBanner, connectionBannerText } from "./ConnectionBanner";
import { Header } from "./Header";

const withConn = (s: FactoryState, c: Parameters<typeof factoryReducer>[1]) => factoryReducer(s, c);

describe("ConnectionBanner", () => {
  it("is silent while connecting and while open", () => {
    renderWith(<ConnectionBanner />, withConn(ready(), { type: "connection", status: "connecting" }));
    expect(document.querySelector("[data-banner]")).toBeNull();
    renderWith(<ConnectionBanner />, withConn(ready(), { type: "connection", status: "open" }));
    expect(document.querySelector("[data-banner]")).toBeNull();
  });

  it("shows the reconnect attempt and delay", () => {
    renderWith(<ConnectionBanner />, withConn(ready(), { type: "connection", status: "reconnecting", attempt: 3, delay: 10000 }));
    expect(document.querySelector("[data-banner]")).toHaveTextContent("連線中斷，正在重新連線…（第 3 次，約 10 秒後）");
  });

  it("shows an error with its reason", () => {
    renderWith(<ConnectionBanner />, withConn(ready(), { type: "connection", status: "error", reason: "Unknown project: x" }));
    expect(document.querySelector("[data-banner]")).toHaveTextContent("連不上伺服器：Unknown project: x");
  });

  it("the text helper covers closed, error without reason and replay", () => {
    expect(connectionBannerText("closed", 0, null, null, "live")).toBe("連線已關閉。");
    expect(connectionBannerText("error", 0, null, null, "live")).toBe("連不上伺服器。");
    expect(connectionBannerText("reconnecting", 1, null, null, "live")).toBe("連線中斷，正在重新連線…（第 1 次）");
    expect(connectionBannerText("reconnecting", 1, 2000, null, "replay")).toBeNull();
    expect(connectionBannerText("error", 0, null, "找不到檔案", "replay")).toBe("回放載入失敗：找不到檔案");
  });
});

describe("connection announcements (one source, once per outage)", () => {
  /** Every time a live region appears with text, or its text changes, a screen reader speaks. */
  function recorder(container: HTMLElement) {
    const seen = new WeakMap<Element, string>();
    const spoken: string[] = [];
    return () => {
      for (const el of container.querySelectorAll('[role="status"], [role="alert"], [aria-live]')) {
        if (el.getAttribute("aria-live") === "off") continue;
        const text = (el.textContent ?? "").trim();
        if (seen.get(el) !== text && text) spoken.push(text);
        seen.set(el, text);
      }
      return spoken;
    };
  }

  it("says the connection dropped once, ignores retries, and says it is back once", () => {
    const tree = (
      <>
        <Header />
        <ConnectionBanner />
      </>
    );
    let s = withConn(ready(), { type: "connection", status: "open" });
    const { container, update } = renderWith(tree, s);
    const record = recorder(container);
    record();
    for (let attempt = 1; attempt <= 4; attempt++) {
      s = withConn(s, { type: "connection", status: "reconnecting", attempt, delay: 10000 });
      update(s);
      record();
      s = withConn(s, { type: "connection", status: "connecting" });
      update(s);
      record();
    }
    const bannerBetweenRetries = container.querySelector("[data-banner]") !== null;
    s = withConn(s, { type: "connection", status: "open" });
    update(s);
    expect(record()).toEqual(["連線中斷，正在重新連線。", "已重新連線。"]);
    // the visible banner stays up between retries and goes away once connected
    expect(bannerBetweenRetries).toBe(true);
    expect(container.querySelector("[data-banner]")).toBeNull();
  });

  it("an unknown project is said once, with its reason", () => {
    let s = withConn(ready(), { type: "connection", status: "connecting" });
    const { container, update } = renderWith(<ConnectionBanner />, s);
    const record = recorder(container);
    s = withConn(s, { type: "connection", status: "error", reason: "Unknown project: x" });
    update(s);
    update(s);
    expect(record()).toEqual(["連不上伺服器：Unknown project: x"]);
  });
});
