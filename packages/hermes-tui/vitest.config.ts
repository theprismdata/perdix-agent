import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  esbuild: { jsx: "automatic" },
  resolve: { alias: {
    "@hermes/ink": fileURLToPath(new URL("./vendor/ui-tui/packages/hermes-ink/src/entry-exports.ts", import.meta.url)),
    "@hermes/shared": fileURLToPath(new URL("./vendor/apps/shared/src", import.meta.url)),
  } },
  test: { include: ["src/**/*.test.ts", "vendor/ui-tui/src/__tests__/theme.test.ts", "vendor/ui-tui/src/__tests__/brandingMcpCount.test.ts", "vendor/ui-tui/src/__tests__/createSlashHandler.test.ts"] },
});
