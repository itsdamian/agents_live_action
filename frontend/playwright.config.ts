import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium, defineConfig, devices } from "@playwright/test";

// Smoke tests for the mobile / motion / keyboard acceptance criteria (AC10 a–f). They play the
// recorded replays against a production build served by `vite preview`: no backend, no network.
//
// Browser: never downloaded by the tests. In order: PW_CHROMIUM_PATH; the build this Playwright
// version expects (`npx playwright install chromium`); else the newest chrome-headless-shell
// already in the Playwright cache (an older build works for these smoke tests).
function localChromium(): string | undefined {
  if (process.env.PW_CHROMIUM_PATH) return process.env.PW_CHROMIUM_PATH;
  if (existsSync(chromium.executablePath())) return undefined;
  const cache = process.env.PLAYWRIGHT_BROWSERS_PATH || join(homedir(), process.platform === "darwin" ? "Library/Caches/ms-playwright" : ".cache/ms-playwright");
  if (!existsSync(cache)) return undefined;
  const builds = readdirSync(cache)
    .filter((d) => d.startsWith("chromium_headless_shell-"))
    .sort((a, b) => Number(b.split("-")[1]) - Number(a.split("-")[1]));
  for (const build of builds) {
    const dir = join(cache, build);
    for (const sub of readdirSync(dir)) {
      for (const bin of ["chrome-headless-shell", "chrome-headless-shell.exe"]) {
        const candidate = join(dir, sub, bin);
        if (existsSync(candidate)) return candidate;
      }
    }
  }
  return undefined;
}
export const executablePath = localChromium();
const PORT = 4173;
// E2E_BASE_URL runs the same smoke tests against an already-running build instead (e.g. the
// Docker image's Nginx on http://localhost:8080): no preview server is started then.
const external = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: [["list"]],
  use: {
    baseURL: external ?? `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], launchOptions: { executablePath } },
    },
  ],
  webServer: external
    ? undefined
    : {
        command: `npm run build && npx vite preview --port ${PORT} --strictPort`,
        url: `http://localhost:${PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
