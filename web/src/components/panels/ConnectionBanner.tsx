// The old "斷線橫幅": visible while the live connection is down or failed (not during the first
// connect, and never in a healthy replay).

import { useEffect, useState } from "react";
import { useFactoryEnv, useFactoryState } from "../../state/FactoryProvider";
import ui from "../../styles/ui.module.css";
import styles from "./ConnectionBanner.module.css";

export function connectionBannerText(
  status: string,
  attempt: number,
  delay: number | null,
  reason: string | null,
  mode: "live" | "replay",
): string | null {
  if (mode === "replay") return status === "error" ? `回放載入失敗：${reason ?? "未知原因"}` : null;
  switch (status) {
    case "reconnecting":
      return `連線中斷，正在重新連線…（第 ${attempt} 次${delay ? `，約 ${Math.round(delay / 1000)} 秒後` : ""}）`;
    case "error":
      return reason ? `連不上伺服器：${reason}` : "連不上伺服器。";
    case "closed":
      return "連線已關閉。";
    default:
      return null;
  }
}

/**
 * What a screen reader hears when the connection goes down: one sentence per outage, whatever
 * the retry count does (null: nothing to say for this status).
 */
export function connectionOutageText(status: string, reason: string | null, mode: "live" | "replay"): string | null {
  if (mode === "replay") return status === "error" ? `回放載入失敗：${reason ?? "未知原因"}` : null;
  switch (status) {
    case "reconnecting":
      return "連線中斷，正在重新連線。";
    case "error":
      return reason ? `連不上伺服器：${reason}` : "連不上伺服器。";
    case "closed":
      return "連線已關閉。";
    default:
      return null;
  }
}

export const RECONNECTED_TEXT = "已重新連線。";

export function ConnectionBanner() {
  const { connection } = useFactoryState();
  const env = useFactoryEnv();
  // `down` lasts from the first failure until the socket is open again: the socket passes through
  // "connecting" before every retry, and that must neither hide the banner nor count as news.
  const [down, setDown] = useState(false);
  const [spoken, setSpoken] = useState("");
  const outage = connectionOutageText(connection.status, connection.reason, env.mode);

  useEffect(() => {
    if (connection.status === "open") {
      if (down) {
        setDown(false);
        setSpoken(RECONNECTED_TEXT);
      }
    } else if (outage && !down) {
      setDown(true);
      setSpoken(outage);
    }
  }, [connection.status, outage, down]);

  const text =
    connectionBannerText(connection.status, connection.attempt, connection.delay, connection.reason, env.mode) ??
    (down && connection.status === "connecting" ? "連線中斷，正在重新連線…" : null);
  return (
    <>
      {/* The only live region for the connection; its text changes once per outage and once on recovery. */}
      <div className={ui.srOnly} role="status" aria-live="polite" aria-atomic="true" data-testid="connection-announcer">
        {spoken}
      </div>
      {text && (
        <div className={styles.banner} data-banner data-status={connection.status}>
          {text}
        </div>
      )}
    </>
  );
}
