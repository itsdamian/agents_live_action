import styles from "./Tray.module.css";

export interface TrayProps {
  kind: "inbox" | "outbox";
  count: number;
  /** Outbox only: number of failed tasks (shown in red when > 0). */
  failed?: number;
  /** Makes the tray a button (opens the note wall, F10). */
  onOpen?: () => void;
}

const NAME = { inbox: "待辦", outbox: "完成" } as const;

export function Tray({ kind, count, failed = 0, onOpen }: TrayProps) {
  const body = (
    <>
      <div className={styles.name}>{NAME[kind]}</div>
      <div className={styles.num} data-testid={`${kind}-count`}>{count}</div>
      {kind === "inbox" && <small>張便條</small>}
      {kind === "outbox" && failed > 0 && <small className={styles.fail}>失敗 {failed}</small>}
      <span className={styles.spot} data-spot={kind} />
    </>
  );
  return onOpen ? (
    <button type="button" className={styles.tray} data-kind={kind} onClick={onOpen} aria-label={`${NAME[kind]}：${count} 張，開啟便條牆`}>
      {body}
    </button>
  ) : (
    <div className={styles.tray} data-kind={kind} role="group" aria-label={`${NAME[kind]}：${count} 張`}>
      {body}
    </div>
  );
}
