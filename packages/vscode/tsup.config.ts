import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/extension.ts"],
  format: ["cjs"],
  outExtension: () => ({ js: ".cjs" }),
  external: ["vscode"],
  dts: false,
  clean: true,
  target: "node22",
});
