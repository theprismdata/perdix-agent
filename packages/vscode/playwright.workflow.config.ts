import { defineConfig } from "playwright/test";

export default defineConfig({
  testDir: "./workflow-ui-tests",
  testMatch: "**/*.spec.ts",
  timeout: 30_000,
  workers: 1,
  reporter: [["list"], ["json", { outputFile: "../../.omo/evidence/verified-team-workflows/task-10-browser.json" }]],
  outputDir: "../../.omo/evidence/verified-team-workflows/browser-artifacts",
  use: { browserName: "chromium", trace: "retain-on-failure" },
});
