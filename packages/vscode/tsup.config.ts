import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/extension.ts"],
  format: ["cjs"],
  outExtension: () => ({ js: ".cjs" }),
  external: ["vscode"],
  noExternal: ["@perdix/protocol", "@agentclientprotocol/sdk", "zod"],
  dts: false,
  clean: true,
  target: "node22",
});
