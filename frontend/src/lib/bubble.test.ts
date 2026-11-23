import { agent, ready, apply, summary } from "../test/factories";
import { selectDeskViews } from "../state/selectors";
import { bubbleText, mockHint, statusText } from "./bubble";

const view = (s: ReturnType<typeof ready>, id: string) => selectDeskViews(s).find((v) => v.id === id)!;

describe("bubbleText / statusText", () => {
  it("idle shows the duty and 打瞌睡中", () => {
    const v = view(ready(), "pm");
    expect(bubbleText(v)).toBe("do things");
    expect(statusText(v)).toBe("打瞌睡中");
  });

  it("working shows the latest phase text, else '<duty>中…'", () => {
    let s = apply(ready(), "station:update", { station: "pm", status: "working", task: summary() }, 1);
    expect(bubbleText(view(s, "pm"))).toBe("do things中…");
    s = apply(s, "agent:phase", { task_id: "t1", station: "pm", step: 1, of: 3, phase: "intake", text: "讀任務：「加登入頁」", passed: null, rework_target: null }, 2);
    expect(bubbleText(view(s, "pm"))).toBe("讀任務：「加登入頁」");
    expect(statusText(view(s, "pm"))).toBe("工作中");
  });

  it("reject, waiting, cheer and hello have the prototype's lines", () => {
    let s = apply(ready(), "station:update", { station: "swe", status: "rework", task: summary("t1", "in_progress", 1) }, 1);
    expect(bubbleText(view(s, "swe"))).toBe("啊，被退件了…");
    expect(statusText(view(s, "swe"))).toBe("被退件了");
    s = apply(s, "task:awaiting_approval", { task: summary("t1", "awaiting_approval"), station: "qa", approval: "after_qa" }, 2);
    expect(bubbleText(view(s, "qa"))).toBe("老闆，「加登入頁」這樣可以嗎？");
    expect(statusText(view(s, "qa"))).toBe("等你點頭");
    s = apply(s, "task:completed", { task: summary("t1", "done"), done_count: 1, deliverables: [] }, 3);
    expect(statusText(view(s, "pm"))).toBe("完成！");
    s = apply(s, "agent:joined", { agent: agent("designer", { builtin: false, display_name: "設計師", duty: "畫介面草圖" }), pipeline_id: "main", position: 1 }, 4);
    expect(bubbleText(view(s, "designer"))).toBe("大家好，我是 設計師，負責畫介面草圖！");
    expect(statusText(view(s, "designer"))).toBe("報到");
  });

  it("a pending exit overrides the status line", () => {
    const s = apply(ready(), "pipeline:pending", { pipeline_id: "main", seats: ["pm", "qa"], reason: "task_in_flight", task_id: "t1" }, 1);
    expect(statusText(view(s, "swe"))).toBe("這張做完就離開");
  });

  it("mockHint explains both reasons", () => {
    expect(mockHint("force_mock")).toContain("強制");
    expect(mockHint("missing_key")).toContain("金鑰");
  });
});
