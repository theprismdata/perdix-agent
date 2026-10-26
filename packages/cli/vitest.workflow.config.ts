import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["src/**/*.workflow.test.ts", "src/**/*.workflow.test.tsx"],
    environment: "node",
    testTimeout: 10_000,
  },
});
