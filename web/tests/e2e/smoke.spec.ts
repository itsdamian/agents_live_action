// AC10 a–f (plan §11.6). jsdom cannot measure layout, touch areas or animations, so these run in
// a real browser against the recorded replays.

import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const PHONE = { width: 375, height: 812 };
const DESKTOP = { width: 1280, height: 800 };
const VIEWPORTS = [
  ["phone 375×812", PHONE],
  ["desktop 1280×800", DESKTOP],
] as const;

// No network beyond the preview server: web fonts get an empty answer (so no console error and
// the fallback font is used), anything else off localhost is aborted.
async function offline(context: BrowserContext) {
  await context.route(
    (url) => url.hostname !== "localhost" && url.hostname !== "127.0.0.1",
    (route) => {
      const host = new URL(route.request().url()).hostname;
      if (host === "fonts.googleapis.com") return route.fulfill({ status: 200, contentType: "text/css", body: "" });
      return route.abort("blockedbyclient");
    },
  );
}
test.beforeEach(({ context }) => offline(context));

async function open(page: Page, replay: string) {
  await page.goto(`/?replay=${replay}`);
  await page.locator("[data-desk]").first().waitFor();
}

/** Close whatever panel is open (Escape works in both the full-screen and the inline sheet). */
async function closePanel(page: Page) {
  await page.keyboard.press("Escape");
  await expect(page.locator("[data-sheet]")).toHaveCount(0);
}

const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

test.describe("AC10 a: no horizontal scrollbar", () => {
  for (const [label, viewport] of VIEWPORTS) {
    for (const replay of ["default-run", "crowded", "crowded-3x8", "gate"]) {
      test(`${replay} at ${label}`, async ({ page }) => {
        await page.setViewportSize(viewport);
        await open(page, replay);
        await page.waitForTimeout(500);
        expect(await overflow(page)).toBeLessThanOrEqual(0);
      });
    }
    test(`panels stay inside the screen at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await open(page, "default-run");
      for (const name of ["派一張任務", "新增 agent", "機房"]) {
        await page.getByRole("button", { name: new RegExp(`^${name}`) }).click();
        expect(await overflow(page), name).toBeLessThanOrEqual(0);
        await closePanel(page);
      }
      await page.getByRole("button", { name: /^完成：/ }).click();
      await page.locator("button[data-status]").first().waitFor();
      expect(await overflow(page), "便條牆").toBeLessThanOrEqual(0);
      await page.locator("button[data-status]").first().click();
      expect(await overflow(page), "任務詳情").toBeLessThanOrEqual(0);
    });
  }
});

interface Small {
  what: string;
  w: number;
  h: number;
}

/**
 * Clickable elements (buttons, links, tabs, form fields) whose touch area is smaller than 44×44
 * CSS px. A checkbox or radio counts its whole <label>, which is what a finger hits.
 */
async function tooSmall(page: Page): Promise<Small[]> {
  return page.evaluate(() => {
    const out: Small[] = [];
    const selector = 'button, a[href], [role="tab"], summary, input:not([type="hidden"]), select, textarea';
    for (const field of document.querySelectorAll<HTMLElement>(selector)) {
      const box = field instanceof HTMLInputElement && (field.type === "checkbox" || field.type === "radio") ? field.closest("label") : null;
      const el = box ?? field;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue; // not rendered
      if (r.width < 43.5 || r.height < 43.5) {
        const name = field.getAttribute("aria-label") ?? field.getAttribute("name") ?? el.textContent ?? "";
        out.push({ what: `${field.tagName.toLowerCase()} "${name.trim().slice(0, 24)}"`, w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10 });
      }
    }
    return out;
  });
}

test.describe("AC10 b: touch targets are at least 44×44", () => {
  for (const [label, viewport] of VIEWPORTS) {
    test(`stage, gate and main buttons at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await open(page, "gate"); // a desk waiting at the approval gate, with × on every desk
      await page.getByRole("button", { name: "可以" }).waitFor();
      expect(await page.getByRole("button", { name: /^請 .* 離開$/ }).count()).toBeGreaterThan(0);
      expect(await tooSmall(page)).toEqual([]);
    });

    test(`the reject note form at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await open(page, "gate");
      await page.getByRole("button", { name: "重做" }).click();
      await page.getByRole("button", { name: "送出重做" }).waitFor();
      expect(await tooSmall(page)).toEqual([]);
    });

    test(`note wall cards, chips and task detail at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await open(page, "default-run");
      await page.getByRole("button", { name: /^完成：/ }).click();
      await page.locator("button[data-status]").first().waitFor();
      expect(await tooSmall(page)).toEqual([]);
      await page.locator("button[data-status]").first().click();
      await page.getByRole("tab", { name: "概況" }).waitFor();
      expect(await tooSmall(page)).toEqual([]);
    });

    test(`the desk's recent-actions card at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await open(page, "default-run");
      const desk = page.getByRole("button", { name: /^看 .* 最近的動作$/ }).first();
      await desk.click();
      // A popover beside the desk on wide screens, a full-screen sheet on phones.
      const card = page.locator("[data-desk-card], [data-sheet]");
      await expect(card).toBeVisible();
      expect(await tooSmall(page)).toEqual([]);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
      await page.keyboard.press("Escape");
      await expect(card).toHaveCount(0);
    });

    test(`forms and the machine room at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await open(page, "custom-lineup");
      await page.getByRole("button", { name: "派一張任務" }).click();
      await page.locator("[data-sheet] textarea, [data-sheet] input").first().waitFor();
      expect(await tooSmall(page)).toEqual([]);
      await closePanel(page);
      await page.getByRole("button", { name: "新增 agent" }).click();
      expect(await tooSmall(page)).toEqual([]);
      await closePanel(page);
      await page.getByRole("button", { name: /^機房/ }).click();
      expect(await tooSmall(page)).toEqual([]);
    });
  }
});

test.describe("AC10 c: reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("characters and the note do not animate, and the bubble text appears at once", async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    // Record every text the PM bubble ever shows on screen. Only the visible span: while typing,
    // the bubble also holds the whole sentence for screen readers, which is not what a person sees.
    await page.addInitScript(() => {
      const seen = new Set<string>();
      (window as unknown as { __bubbleTexts: Set<string> }).__bubbleTexts = seen;
      const record = () => {
        const t = document.querySelector('[data-desk="pm"] [data-bubble-text]')?.textContent;
        if (t) seen.add(t);
      };
      new MutationObserver(record).observe(document, { subtree: true, childList: true, characterData: true });
    });
    await open(page, "default-run");

    await expect(page.locator('[data-desk="pm"][data-state="working"]')).toBeVisible();
    const full = "讀任務：「加登入頁」";
    await expect(page.locator('[data-desk="pm"]').getByText(full, { exact: true })).toBeVisible();

    const animated = await page.evaluate(() => {
      const els = [...document.querySelectorAll("[data-desk], [data-desk] *, [data-task]")];
      return els.filter((e) => e.getAnimations().length > 0).map((e) => e.tagName + "." + e.getAttribute("class"));
    });
    expect(animated).toEqual([]);

    // The note moves desk to desk without a flight.
    await expect(page.locator('[data-task][data-at="swe"]')).toBeVisible({ timeout: 15_000 });
    expect(await page.evaluate(() => document.querySelector("[data-task]")!.getAnimations().length)).toBe(0);

    const texts = await page.evaluate(() => [...(window as unknown as { __bubbleTexts: Set<string> }).__bubbleTexts]);
    expect(texts, "the recorder saw the bubble").toContain(full);
    const partial = texts.filter((t) => t !== full && t.length > 0 && full.startsWith(t));
    expect(partial).toEqual([]);
  });

  test("the same page does animate without the preference (so the check above measures something)", async ({ browser }) => {
    const context = await browser.newContext({ viewport: DESKTOP, reducedMotion: "no-preference" });
    await offline(context);
    const page = await context.newPage();
    await open(page, "default-run");
    await expect(page.locator('[data-desk="pm"][data-state="working"]')).toBeVisible();
    const n = await page.evaluate(() => [...document.querySelectorAll('[data-desk="pm"] svg *')].reduce((sum, e) => sum + e.getAnimations().length, 0));
    expect(n).toBeGreaterThan(0);
    await context.close();
  });
});

interface Box {
  id: string;
  left: number;
  top: number;
  right: number;
  bottom: number;
}

async function deskBoxes(page: Page): Promise<Box[]> {
  return page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("[data-desk]")].map((el, i) => {
      const r = el.getBoundingClientRect();
      return { id: `${el.dataset.desk}#${i}`, left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    }),
  );
}

test.describe("AC10 d: desks never overlap and stay on screen", () => {
  const cases = [
    ["one line of 8", "crowded", 8],
    ["3 lines × 8", "crowded-3x8", 24],
  ] as const;
  for (const [label, viewport] of VIEWPORTS) {
    for (const [name, replay, count] of cases) {
      test(`${name} at ${label}`, async ({ page }) => {
        await page.setViewportSize(viewport);
        await open(page, replay);
        await page.waitForTimeout(300);
        const boxes = await deskBoxes(page);
        expect(boxes).toHaveLength(count);
        const inner = viewport.width;
        for (const b of boxes) {
          expect(b.left, `${b.id} left`).toBeGreaterThanOrEqual(0);
          expect(b.right, `${b.id} right`).toBeLessThanOrEqual(inner + 0.5);
        }
        const overlaps: string[] = [];
        for (let i = 0; i < boxes.length; i++) {
          for (let j = i + 1; j < boxes.length; j++) {
            const a = boxes[i]!;
            const b = boxes[j]!;
            const apart = a.right <= b.left + 0.5 || b.right <= a.left + 0.5 || a.bottom <= b.top + 0.5 || b.bottom <= a.top + 0.5;
            if (!apart) overlaps.push(`${a.id} × ${b.id}`);
          }
        }
        expect(overlaps).toEqual([]);
      });
    }
  }
});

test.describe("AC10 e: panels are full screen on a phone", () => {
  test("note wall and task detail cover the screen and close with 返回", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await open(page, "default-run");

    await page.getByRole("button", { name: /^完成：/ }).click();
    const wall = page.getByRole("dialog", { name: "便條牆" });
    await expect(wall).toBeVisible();
    const rect = await wall.evaluate((el) => {
      const r = el.getBoundingClientRect();
      return { left: r.left, top: r.top, width: r.width, height: r.height, iw: window.innerWidth, ih: window.innerHeight };
    });
    expect(rect).toMatchObject({ left: 0, top: 0 });
    expect(rect.width).toBeCloseTo(rect.iw, 0);
    expect(rect.height).toBeCloseTo(rect.ih, 0);

    await page.getByRole("button", { name: "返回" }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);

    // task detail
    await page.getByRole("button", { name: /^完成：/ }).click();
    await page.locator("button[data-status]").first().click();
    const detail = page.getByRole("dialog", { name: "任務詳情" });
    await expect(detail).toBeVisible();
    expect(await detail.evaluate((el) => Math.round(el.getBoundingClientRect().height) === window.innerHeight)).toBe(true);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });

  test("the page behind a full-screen panel cannot be reached with Tab", async ({ page }) => {
    await page.setViewportSize(PHONE);
    await open(page, "default-run");
    await page.getByRole("button", { name: "派一張任務" }).click();
    const dialog = page.getByRole("dialog", { name: "派一張任務" });
    await expect(dialog).toBeVisible();
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press("Tab");
      expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
    }
  });

  test("on a wide screen the same panels are inline, with a 關閉 button", async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await open(page, "default-run");
    await page.getByRole("button", { name: /^完成：/ }).click();
    await expect(page.getByRole("region", { name: "便條牆" })).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await page.getByRole("button", { name: "關閉" }).click();
    await expect(page.getByRole("region", { name: "便條牆" })).toHaveCount(0);
  });
});

test.describe("AC10 f: keyboard", () => {
  for (const [label, viewport] of VIEWPORTS) {
    test(`the gate is reachable with Tab and answered with Enter at ${label}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await open(page, "gate");
      await page.getByRole("button", { name: "可以" }).waitFor();

      let reached = false;
      for (let i = 0; i < 40 && !reached; i++) {
        await page.keyboard.press("Tab");
        reached = await page.evaluate(() => document.activeElement?.textContent?.trim() === "可以");
      }
      expect(reached, "Tab reaches 可以").toBe(true);

      // focus is visible
      const outline = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement;
        const cs = getComputedStyle(el);
        return { focusVisible: el.matches(":focus-visible"), style: cs.outlineStyle, width: parseFloat(cs.outlineWidth) };
      });
      expect(outline.focusVisible).toBe(true);
      expect(outline.style).not.toBe("none");
      expect(outline.width).toBeGreaterThanOrEqual(2);

      await page.keyboard.press("Enter");
      await expect(page.getByRole("button", { name: "可以" })).toHaveCount(0);
      await expect(page.locator('[data-desk="qa"]')).not.toHaveAttribute("data-state", "waiting");
    });
  }

  test("重做 opens the note field with Enter, Escape closes it again", async ({ page }) => {
    await page.setViewportSize(DESKTOP);
    await open(page, "gate");
    await page.getByRole("button", { name: "重做" }).focus();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("哪裡要改？（選填）")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "可以" })).toBeVisible();
  });
});

test("no console errors or warnings while a replay plays", async ({ page }) => {
  const problems: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "warning") problems.push(`${m.type()}: ${m.text()}`);
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  await page.setViewportSize(DESKTOP);
  await page.goto("/?replay=default-run&speed=4");
  await page.locator("[data-desk]").first().waitFor();
  await page.waitForTimeout(8_000);
  expect(problems).toEqual([]);
});
