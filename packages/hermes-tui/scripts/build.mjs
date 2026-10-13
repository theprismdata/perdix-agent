import { build } from "esbuild";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const ink = resolve(root, "vendor/ui-tui/packages/hermes-ink/src/entry-exports.ts");
// 원본 패키지의 file: 의존성 대신 고정한 원본 경로를 빌드 시 연결한다.
await build({
  absWorkingDir: root,
  entryPoints: ["vendor/ui-tui/src/entry.tsx"],
  outfile: "dist/entry.js",
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  jsx: "automatic",
  alias: {
    "@hermes/ink/text-input": "ink-text-input",
    "@hermes/ink": ink,
    ink,
    "@hermes/shared": resolve(root, "vendor/apps/shared/src"),
  },
  banner: { js: "import { createRequire as __perdixCreateRequire } from 'node:module'; const require = __perdixCreateRequire(import.meta.url);" },
  metafile: true,
});
await build({
  absWorkingDir: root,
  entryPoints: ["src/launch.ts"],
  outdir: "dist",
  platform: "node",
  format: "esm",
  target: "node22",
});
