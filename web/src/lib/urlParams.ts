// `?project=<id>` selects the project; `?replay=<name>` plays public/replay/<name>.jsonl without
// a backend; `?speed=<n>` speeds a replay up (default 1).

import { isValidReplayName } from "../net/replay";

export interface UrlParams {
  project: string | null;
  replay: string | null;
  speed: number;
}

export function readUrlParams(search: string): UrlParams {
  const q = new URLSearchParams(search);
  const project = q.get("project");
  const replay = q.get("replay");
  const speed = Number(q.get("speed") ?? "1");
  return {
    project: project !== null && project !== "" ? project : null,
    replay: replay !== null && isValidReplayName(replay) ? replay : null,
    speed: Number.isFinite(speed) && speed > 0 && speed <= 20 ? speed : 1,
  };
}

/** `search` with `project` replaced (other params kept). */
export function withProject(search: string, projectId: string): string {
  const q = new URLSearchParams(search);
  q.set("project", projectId);
  return `?${q.toString()}`;
}
