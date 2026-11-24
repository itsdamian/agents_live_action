// Live smoke against the real stack (F12: AC10 and AC11 on Docker with FORCE_MOCK=true).
// See playwright.live.config.ts for how to run it. The tests share one project and run in order:
// the agent added first is the check seat the gate tests stop at, and is removed near the end.

import { execFileSync } from "node:child_process";
import { statSync } from "node:fs";
import { expect, test, type Page } from "@playwright/test";

const PROJECT = process.env.LIVE_PROJECT ?? "example-project";
const COMPOSE = process.env.LIVE_COMPOSE_PROJECT;
const PHONE = { width: 375, height: 812 };
const DESKTOP = { width: 1280, height: 800 };
const RUN = Date.now().toString(36).slice(-4);
const AGENT = `資安${RUN}`;
const TASK_GATE = `live 閘門 ${RUN}`;
const TASK_DETAIL = `live 詳情 ${RUN}`;

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ request }) => {
  // Never drive a backend that could call a real LLM.
  const health = await (await request.get("/api/health")).json();
  if (health.force_mock !== true) throw new Error(`backend is not FORCE_MOCK: ${JSON.stringify(health)}`);
});

const problems: string[] = [];
test.beforeEach(async ({ page }) => {
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`${m.type()}: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
});

async function open(page: Page, viewport = DESKTOP) {
  await page.setViewportSize(viewport);
  await page.goto(`/?project=${encodeURIComponent(PROJECT)}`);
  await expect(page.getByTestId("connection")).toHaveText("已連線");
  await page.locator("[data-desk]").first().waitFor();
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: test.info().outputPath(`${name}.png`), fullPage: true });
}

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

/** Clickable elements whose touch area is under 44×44 (same rule as tests/e2e/smoke.spec.ts). */
async function tooSmall(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const selector = 'button, a[href], [role="tab"], summary, input:not([type="hidden"]), select, textarea';
    for (const field of document.querySelectorAll<HTMLElement>(selector)) {
      const box = field instanceof HTMLInputElement && (field.type === "checkbox" || field.type === "radio") ? field.closest("label") : null;
      const r = (box ?? field).getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.width < 43.5 || r.height < 43.5) out.push(`${field.tagName} "${(field.getAttribute("aria-label") ?? field.textContent ?? "").trim().slice(0, 20)}" ${r.width}×${r.height}`);
    }
    return out;
  });
}

const stat = async (page: Page, id: string) => Number(await page.getByTestId(`stat-${id}`).textContent());
const desk = (page: Page, name: string) => page.getByRole("group", { name: new RegExp(`^${name}：`) });

async function dispatch(page: Page, title: string, approval: string) {
  await page.getByRole("button", { name: "派一張任務", exact: true }).click();
  await page.getByLabel("標題").fill(title);
  await page.getByRole("radio", { name: approval }).check();
  await page.getByRole("button", { name: "派出任務" }).click();
  await expect(page.getByText(`任務「${title}」已派出。`)).toBeVisible();
}

test("AC11 header: project select, MOCK (FORCE_MOCK), connection status", async ({ page }) => {
  await open(page);
  const project = page.locator("header select, label:has-text('專案') select").first();
  await expect(project).toHaveValue(PROJECT);
  await expect(page.getByTestId("mock-indicator")).toHaveText("[MOCK MODE]（FORCE_MOCK 強制）");
  await shot(page, "01-header");
});

test("AC11 add an agent: a check seat after QA joins the line", async ({ page }) => {
  await open(page);
  const before = await page.locator("[data-desk]").count();
  await page.getByRole("button", { name: "新增 agent" }).click();
  await page.getByLabel(/^名字/).fill(AGENT);
  await page.getByLabel(/^負責什麼/).fill("檢查安全");
  await page.getByLabel(/^給他的指示/).fill("你是資安審查員，檢查輸入驗證與權限。");
  await page.getByLabel("把關（可以退件）").check();
  await page.getByLabel("座位").selectOption({ label: "坐在 QA 後面" });
  await page.getByRole("button", { name: "請他上工" }).click();
  await expect(desk(page, AGENT)).toBeVisible();
  await expect(page.locator("[data-desk]")).toHaveCount(before + 1);
  await page.keyboard.press("Escape");
  await shot(page, "02-agent-joined");
});

test("AC11 dispatch + gate: reject from the desk, then approve with Tab + Enter (AC10 b, f)", async ({ page }) => {
  await open(page);
  const done = await stat(page, "completed");
  await dispatch(page, TASK_GATE, "全部通過後給我看");

  const approve = page.getByRole("button", { name: "可以" });
  await expect(approve).toBeVisible({ timeout: 60_000 });
  await expect(desk(page, AGENT)).toHaveAttribute("data-state", "waiting");
  await shot(page, "03-gate-first");
  // AC10 b on the live gate
  expect(await tooSmall(page)).toEqual([]);

  await page.getByRole("button", { name: "重做" }).click();
  await page.getByLabel("哪裡要改？（選填）").fill("live：請補上輸入驗證");
  await page.getByRole("button", { name: "送出重做" }).click();
  await expect.poll(() => stat(page, "reworks")).toBeGreaterThanOrEqual(1);

  // The task goes round again and stops at the gate a second time.
  await expect(approve).toBeVisible({ timeout: 60_000 });
  await shot(page, "04-gate-second");

  // AC10 f: reach "可以" with the keyboard only, its focus ring is visible, Enter approves.
  await page.locator("body").click({ position: { x: 2, y: 2 } });
  let reached = false;
  for (let i = 0; i < 200 && !reached; i++) {
    await page.keyboard.press("Tab");
    reached = await page.evaluate(() => document.activeElement?.textContent?.trim() === "可以");
  }
  expect(reached).toBe(true);
  const ring = await page.evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const cs = getComputedStyle(el);
    return { focusVisible: el.matches(":focus-visible"), outline: cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0, shadow: cs.boxShadow !== "none" };
  });
  expect(ring.focusVisible).toBe(true);
  expect(ring.outline || ring.shadow).toBe(true);
  await shot(page, "05-gate-focus");
  await page.keyboard.press("Enter");

  await expect.poll(() => stat(page, "completed"), { timeout: 60_000 }).toBe(done + 1);
  await expect(approve).toHaveCount(0);
  await shot(page, "06-gate-done");
});

test("stats after a reload: 重做 vs the snapshot", async ({ page, request }) => {
  await open(page);
  const reworks = await stat(page, "reworks");
  const tasks = (await (await request.get(`/api/projects/${PROJECT}/tasks`)).json()).tasks as Array<{ id: string; title: string; rework_count: number }>;
  const history = (await (await request.get(`/api/projects/${PROJECT}/history`)).json()).items as Array<{ task_id: string; title: string; rework_count: number | null }>;
  const gateTask = tasks.find((t) => t.title === TASK_GATE);
  const gateHistory = history.find((h) => h.title === TASK_GATE);
  // Recorded, not asserted: the stats bar sums the snapshot's tasks[].rework_count.
  test.info().annotations.push({
    type: "reworks-after-reload",
    description: JSON.stringify({ statsBar: reworks, tasksRework: gateTask?.rework_count, historyRework: gateHistory?.rework_count }),
  });
  await shot(page, "07-after-reload");
});

test("AC11 task detail: approve from the panel, logs, deliverable download", async ({ page }) => {
  await open(page);
  await dispatch(page, TASK_DETAIL, "做完後先給我看");
  await page.getByRole("button", { name: "看詳情" }).click();
  const gate = page.locator('[aria-label="核准"]');
  await expect(gate.getByRole("button", { name: "可以" })).toBeVisible({ timeout: 60_000 });
  await shot(page, "08-detail-gate");
  await gate.getByRole("button", { name: "可以" }).click();
  await expect(gate).toHaveCount(0, { timeout: 60_000 });

  // Reopen from the note wall once the task is done (the panel reloads on reopen).
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /^完成：\d+ 張(，失敗 \d+)?，開啟便條牆$/ }).click();
  await page.locator("button[data-status]", { hasText: TASK_DETAIL }).click();
  await expect(page.getByRole("tab", { name: "概況" })).toBeVisible();

  await page.getByRole("tab", { name: "日誌" }).click();
  await expect(page.getByText("還沒有日誌。")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "原始檔" }).first()).toBeVisible();
  await shot(page, "09-detail-logs");

  await page.getByRole("tab", { name: "交付檔" }).click();
  const link = page.locator("a[download]").first();
  await expect(link).toBeVisible();
  const [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
  const saved = test.info().outputPath(download.suggestedFilename());
  await download.saveAs(saved);
  expect(statSync(saved).size).toBeGreaterThan(0);
  test.info().annotations.push({ type: "download", description: `${download.suggestedFilename()} ${statSync(saved).size} bytes` });
  await shot(page, "10-detail-files");

  for (const tab of ["各 agent 產出", "執行紀錄"]) {
    await page.getByRole("tab", { name: tab }).click();
    await expect(page.getByRole("tabpanel")).toBeVisible();
  }
  await shot(page, "11-detail-runs");
});

test("AC11 model menu: switching to Claude asks first, then says Mock stands in", async ({ page }) => {
  await open(page);
  const select = page.locator("label", { hasText: "模型" }).first().locator("select");
  const before = await select.inputValue();
  await select.selectOption({ label: "Claude Opus 5.5" });
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("切換到 Claude Opus 5.5？");
  await expect(dialog).toContainText("伺服器目前會改用 Mock");
  await shot(page, "12-model-confirm");
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(select).toHaveValue(before);

  await select.selectOption({ label: "Claude Opus 5.5" });
  await page.getByRole("alertdialog").getByRole("button", { name: "確定切換" }).click();
  await expect(page.getByTestId("llm-mock-note")).toHaveText("實際以 Mock 執行（伺服器強制 Mock）");
  await expect(select.locator("option:checked")).toHaveText("Claude Opus 5.5");
  await shot(page, "13-model-switched");

  await select.selectOption({ label: "Mock" }); // back, no confirmation needed
  await expect(page.getByTestId("llm-mock-note")).toHaveCount(0);
});

test("AC11 machine room drawer opens and closes", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: /^機房/ }).click();
  const sheet = page.locator("[data-sheet]");
  await expect(sheet).toBeVisible();
  await expect(sheet.getByText("目前沒有警示，機房一切正常。").or(sheet.getByRole("list", { name: "警示" }))).toBeVisible();
  await shot(page, "14-devops");
  await sheet.getByRole("button", { name: "關閉" }).click();
  await expect(sheet).toHaveCount(0);
});

test("AC11 remove an agent: the × sends the check seat away", async ({ page }) => {
  await open(page);
  const seats = await page.locator("[data-desk]").count();
  await page.getByRole("button", { name: `請 ${AGENT} 離開` }).click();
  await expect(desk(page, AGENT)).toHaveCount(0);
  await expect(page.locator("[data-desk]")).toHaveCount(seats - 1);
  await shot(page, "15-agent-left");
});

test("AC10 a, b, e at phone width: no sideways scroll, 44 px targets, full-screen sheets with 返回", async ({ page }) => {
  await open(page, PHONE);
  expect(await overflow(page)).toBeLessThanOrEqual(0);
  expect(await tooSmall(page)).toEqual([]);
  await shot(page, "16-phone-stage");

  for (const name of ["派一張任務", "新增 agent"]) {
    await page.getByRole("button", { name, exact: true }).click();
    const sheet = page.locator('[data-sheet="full"]');
    await expect(sheet).toBeVisible();
    const box = (await sheet.boundingBox())!;
    expect(box.width).toBeGreaterThanOrEqual(PHONE.width - 1);
    expect(await overflow(page)).toBeLessThanOrEqual(0);
    await sheet.getByRole("button", { name: "返回" }).click();
    await expect(sheet).toHaveCount(0);
  }

  await page.getByRole("button", { name: /^完成：\d+ 張(，失敗 \d+)?，開啟便條牆$/ }).click();
  const wall = page.locator('[data-sheet="full"]');
  await expect(wall).toBeVisible();
  await shot(page, "17-phone-wall");
  await page.locator("button[data-status]", { hasText: TASK_DETAIL }).click();
  await expect(page.getByRole("tab", { name: "概況" })).toBeVisible();
  expect((await page.locator('[data-sheet="full"]').last().boundingBox())!.width).toBeGreaterThanOrEqual(PHONE.width - 1);
  expect(await overflow(page)).toBeLessThanOrEqual(0);
  expect(await tooSmall(page)).toEqual([]);
  await shot(page, "18-phone-detail");
  while ((await page.locator('[data-sheet="full"]').count()) > 0) {
    await page.locator('[data-sheet="full"]').last().getByRole("button", { name: "返回" }).click();
  }
  await expect(page.locator("[data-sheet]")).toHaveCount(0);

  await page.setViewportSize(DESKTOP);
  expect(await overflow(page)).toBeLessThanOrEqual(0);
});

test.describe("AC10 c with reduced motion", () => {
  test.use({ reducedMotion: "reduce" });
  test("no character animation while a task runs", async ({ page }) => {
    await open(page);
    const running = (async () => {
      const counts: number[] = [];
      for (let i = 0; i < 20; i++) {
        counts.push(await page.evaluate(() => [...document.querySelectorAll("[data-desk] svg")].reduce((n, el) => n + el.getAnimations({ subtree: true }).length, 0)));
        await page.waitForTimeout(100);
      }
      return counts;
    })();
    await dispatch(page, `live 動態 ${RUN}`, "不用");
    expect(Math.max(...(await running))).toBe(0);
  });
});

test("no console errors or warnings in any of the above (before the disconnect test, which logs failed reconnects)", () => {
  expect(problems).toEqual([]);
});

test("AC11 disconnect banner while the backend is down", async ({ page }) => {
  test.skip(!COMPOSE, "set LIVE_COMPOSE_PROJECT to let the test stop and start the backend container");
  test.setTimeout(240_000);
  await open(page);
  const container = `${COMPOSE}-backend-1`;
  execFileSync("docker", ["stop", container]);
  try {
    await expect(page.locator("[data-banner]")).toContainText("連線中斷，正在重新連線", { timeout: 30_000 });
    await shot(page, "19-disconnected");
  } finally {
    execFileSync("docker", ["start", container]);
  }
  await expect(page.getByTestId("connection")).toHaveText("已連線", { timeout: 180_000 });
  await expect(page.locator("[data-banner]")).toHaveCount(0);
  await shot(page, "20-reconnected");
});
