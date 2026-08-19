import { defineConfig, devices } from "@playwright/test";

const harnessId = process.env.STUDYDY_E2E_HARNESS_ID ?? "";

const browserEnvironment: Record<string, string> = {};
for (const [name, value] of Object.entries(process.env)) {
  if (value !== undefined && !name.startsWith("STUDYDY_E2E_")) {
    browserEnvironment[name] = value;
  }
}

if (!/^studydy-e2e-[0-9a-f]{32}$/.test(harnessId)) throw new Error("E2E_HARNESS_REQUIRED");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  outputDir: "test-results",
  reporter: [
    ["line"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:4173",
    headless: true,
    launchOptions: { env: browserEnvironment },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], browserName: "chromium" },
    },
  ],
});
