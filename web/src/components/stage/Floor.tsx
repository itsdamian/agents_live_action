// One pipeline = one floor: inbox tray, the desks in seat order, outbox tray.
// Metrics (desk width etc.) arrive as CSS custom properties.

import type { CSSProperties } from "react";
import { metricsStyle, type DeskMetrics } from "../../lib/layout";
import type { DeskView } from "../../state/selectors";
import { Desk } from "./Desk";
import styles from "./Floor.module.css";
import { Tray } from "./Tray";

export interface FloorProps {
  pipelineId: string;
  /** Shown as the floor's accessible name when several floors exist. */
  name?: string;
  views: DeskView[];
  metrics: DeskMetrics;
  queue: number;
  done: number;
  failed: number;
  onRemoveAgent?: (id: string) => void;
  onOpenInbox?: () => void;
  onOpenOutbox?: () => void;
}

export function Floor({ pipelineId, name, views, metrics, queue, done, failed, onRemoveAgent, onOpenInbox, onOpenOutbox }: FloorProps) {
  return (
    <div
      className={styles.floor}
      data-floor={pipelineId}
      data-compact={metrics.compact || undefined}
      style={metricsStyle(metrics) as CSSProperties}
      role="group"
      aria-label={name}
    >
      <Tray kind="inbox" count={queue} onOpen={onOpenInbox} />
      {views.map((v) => (
        <Desk key={v.id} view={v} onRemove={onRemoveAgent} compact={metrics.compact} />
      ))}
      <Tray kind="outbox" count={done} failed={failed} onOpen={onOpenOutbox} />
    </div>
  );
}
