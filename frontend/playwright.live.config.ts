import { defineConfig, devices } from "@playwright/test";
import { executablePath } from "./playwright.config";

// Live smoke (F12, AC10 + AC11 on Docker): drives the real stack through the frontend's Nginx.
// Not part of `npm run e2e`. Run only against a FORCE_MOCK=true backend (the spec checks
// /api/health first and stops otherwise):
//
//   LIVE_BASE_URL=http://localhost:8080 npm run e2e:live
//
// It creates tasks and agents in the project it opens (LIVE_PROJECT, default example-project).
// LIVE_COMPOSE_PROJECT=<compose -p name> also lets it stop and start the backend container to
// check the disconnect banner.
const baseURL = process.env.LIVE_BASE_URL;
if (!baseURL) throw new Error("Set LIVE_BASE_URL (e.g. http://localhost:8080) to run the live smoke tests.");

export default defineConfig({
  testDir: "./tests/live",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    acceptDownloads: true,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], launchOptions: { executablePath } },
    },
  ],
});
