import historyJson from "../../public/replay/history.json?raw";
import type { HistoryResponse } from "../types/api";
import { PAGE_SIZE, queryHistory } from "./historyFilter";

const all = JSON.parse(historyJson) as HistoryResponse;

describe("queryHistory (replay stand-in for GET /history)", () => {
  it("no filter returns everything up to the page size", () => {
    const r = queryHistory(all, {});
    expect(PAGE_SIZE).toBe(50);
    expect(r.total).toBe(30);
    expect(r.items).toHaveLength(30);
    expect(r.counts).toEqual(all.counts);
  });
  it("filters by status list", () => {
    const r = queryHistory(all, { status: ["failed"] });
    expect(r.total).toBe(4);
    expect(r.items.every((i) => i.status === "failed")).toBe(true);
    expect(queryHistory(all, { status: ["done", "failed"] }).total).toBe(18);
  });
  it("filters by pipeline", () => {
    expect(queryHistory(all, { pipeline: "main" }).total).toBe(30);
    expect(queryHistory(all, { pipeline: "bugs" }).total).toBe(0);
  });
  it("pages with limit and offset; total stays the full match count", () => {
    const r = queryHistory(all, { limit: 10, offset: 25 });
    expect(r.items).toHaveLength(5);
    expect(r.total).toBe(30);
    expect(queryHistory(all, { limit: 10, offset: 0 }).items[0]!.task_id).toBe(all.items[0]!.task_id);
  });
  it("caps the limit at 200 and ignores a negative offset", () => {
    expect(queryHistory(all, { limit: 9999 }).items).toHaveLength(30);
    expect(queryHistory(all, { offset: -5, limit: 2 }).items).toHaveLength(2);
  });
});
