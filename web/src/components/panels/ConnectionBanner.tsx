// The old "斷線橫幅": visible while the live connection is down or failed (not during the first
// connect, and never in a healthy replay).

import { useFactoryEnv, useFactoryState } from "../../state/FactoryProvider";
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

export function ConnectionBanner() {
  const { connection } = useFactoryState();
  const env = useFactoryEnv();
  const text = connectionBannerText(connection.status, connection.attempt, connection.delay, connection.reason, env.mode);
  if (!text) return null;
  return (
    <div className={styles.banner} role="alert" data-status={connection.status}>
      {text}
    </div>
  );
}
