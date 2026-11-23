import type { NoteAnchor } from "../state/selectors";
import {
  FLIGHT_EASING,
  HOP_MS,
  MAX_FLIGHT_MS,
  anchorKey,
  flightDuration,
  flightKeyframes,
  notePath,
  spotOffset,
  spotSelector,
  taskLabel,
  translate,
} from "./notePath";

const inbox: NoteAnchor = { kind: "inbox" };
const outbox: NoteAnchor = { kind: "outbox" };
const desk = (id: string): NoteAnchor => ({ kind: "desk", id });
const keys = (path: NoteAnchor[]) => path.map(anchorKey);
const DEFAULT = ["pm", "swe", "qa"];

describe("notePath", () => {
  it("default lineup: inbox → pm → swe → qa → outbox, one hop at a time", () => {
    expect(keys(notePath(inbox, desk("pm"), DEFAULT))).toEqual(["desk:pm"]);
    expect(keys(notePath(desk("pm"), desk("swe"), DEFAULT))).toEqual(["desk:swe"]);
    expect(keys(notePath(desk("swe"), desk("qa"), DEFAULT))).toEqual(["desk:qa"]);
    expect(keys(notePath(desk("qa"), outbox, DEFAULT))).toEqual(["outbox"]);
  });

  it("a first appearance has no origin: just the target", () => {
    expect(keys(notePath(null, inbox, DEFAULT))).toEqual(["inbox"]);
  });

  it("staying put is an empty path", () => {
    expect(notePath(desk("swe"), desk("swe"), DEFAULT)).toEqual([]);
  });

  it("C2 reject: qa sends the note straight back to swe", () => {
    expect(keys(notePath(desk("qa"), desk("swe"), DEFAULT))).toEqual(["desk:swe"]);
  });

  it("C2 with a custom check seat: security → swe skips the desks in between", () => {
    const seats = ["pm", "swe", "qa", "security"];
    expect(keys(notePath(desk("security"), desk("swe"), seats))).toEqual(["desk:swe"]);
  });

  it("gate reject: the gate desk sends the note back to the make seat", () => {
    expect(keys(notePath(desk("qa"), desk("swe"), DEFAULT))).toEqual(["desk:swe"]);
    expect(keys(notePath(desk("writer"), desk("writer"), ["pm", "writer"]))).toEqual([]);
    expect(keys(notePath(desk("writer"), desk("pm"), ["pm", "writer"]))).toEqual(["desk:pm"]);
  });

  it("a forward move over a skipped desk visits it on the way", () => {
    expect(keys(notePath(desk("pm"), desk("qa"), DEFAULT))).toEqual(["desk:swe", "desk:qa"]);
    expect(keys(notePath(inbox, outbox, DEFAULT))).toEqual(["desk:pm", "desk:swe", "desk:qa", "outbox"]);
  });

  it("follows a custom lineup order", () => {
    const seats = ["pm", "designer", "swe", "qa"];
    expect(keys(notePath(desk("pm"), desk("designer"), seats))).toEqual(["desk:designer"]);
    expect(keys(notePath(desk("designer"), desk("swe"), seats))).toEqual(["desk:swe"]);
  });

  it("an unknown desk id flies straight to the target", () => {
    expect(keys(notePath(desk("ghost"), desk("qa"), DEFAULT))).toEqual(["desk:qa"]);
    expect(keys(notePath(desk("pm"), desk("ghost"), DEFAULT))).toEqual(["desk:ghost"]);
  });

  it("the inbox is only ever a start, and a failed note may also go from any desk to the outbox", () => {
    expect(keys(notePath(desk("swe"), outbox, DEFAULT))).toEqual(["desk:qa", "outbox"]);
  });
});

describe("coordinates", () => {
  it("spotOffset is the spot's position inside the stage", () => {
    expect(spotOffset({ left: 100, top: 50 }, { left: 340.5, top: 210 })).toEqual({ x: 240.5, y: 160 });
    expect(spotOffset({ left: 10, top: 10 }, { left: 0, top: 0 })).toEqual({ x: -10, y: -10 });
  });

  it("translate rounds to two decimals", () => {
    expect(translate({ x: 1.23456, y: -7 })).toBe("translate(1.23px, -7px)");
  });

  it("flightDuration is 600 ms per hop, capped", () => {
    expect(flightDuration(1)).toBe(HOP_MS);
    expect(flightDuration(2)).toBe(1200);
    expect(flightDuration(10)).toBe(MAX_FLIGHT_MS);
    expect(flightDuration(0)).toBe(HOP_MS);
  });

  it("flightKeyframes: start, waypoints and end with even offsets and the prototype easing", () => {
    const k = flightKeyframes([{ x: 0, y: 0 }, { x: 100, y: 0 }, { x: 100, y: 80 }]);
    expect(k).toEqual([
      { transform: "translate(0px, 0px)", offset: 0, easing: FLIGHT_EASING },
      { transform: "translate(100px, 0px)", offset: 0.5, easing: FLIGHT_EASING },
      { transform: "translate(100px, 80px)", offset: 1 },
    ]);
  });

  it("flightKeyframes with a single point is a plain placement", () => {
    expect(flightKeyframes([{ x: 5, y: 6 }])).toEqual([{ transform: "translate(5px, 6px)" }]);
  });
});

describe("helpers", () => {
  it("anchorKey / spotSelector", () => {
    expect(anchorKey(desk("swe"))).toBe("desk:swe");
    expect(anchorKey(inbox)).toBe("inbox");
    expect(spotSelector(desk("swe"))).toBe('[data-spot="swe"]');
    expect(spotSelector(outbox)).toBe('[data-spot="outbox"]');
    expect(spotSelector(desk('a"]b'))).toBe('[data-spot="a]b"]');
  });

  it("taskLabel shows the task number", () => {
    expect(taskLabel("task-004")).toBe("#4");
    expect(taskLabel("task-12")).toBe("#12");
    expect(taskLabel("abcdef")).toBe("#abcd");
  });
});
