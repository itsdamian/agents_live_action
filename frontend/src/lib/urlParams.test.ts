import { readUrlParams, withProject } from "./urlParams";

describe("readUrlParams", () => {
  it("reads project, replay and speed", () => {
    expect(readUrlParams("?project=demo&replay=default-run&speed=3")).toEqual({ project: "demo", replay: "default-run", speed: 3 });
  });
  it("defaults when absent or invalid", () => {
    expect(readUrlParams("")).toEqual({ project: null, replay: null, speed: 1 });
    expect(readUrlParams("?project=&replay=../x&speed=abc")).toEqual({ project: null, replay: null, speed: 1 });
    expect(readUrlParams("?speed=0")).toMatchObject({ speed: 1 });
    expect(readUrlParams("?speed=500")).toMatchObject({ speed: 1 });
  });
  it("withProject replaces project and keeps other params", () => {
    expect(withProject("?replay=x&project=a", "b")).toBe("?replay=x&project=b");
    expect(withProject("", "my project")).toBe("?project=my+project");
  });
});
