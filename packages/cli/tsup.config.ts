import { existsSync } from "node:fs";
import { defineConfig } from "tsup";

// ui/ 는 main에서 지연 로드한다. 모듈이 있을 때만 별도 엔트리(dist/ui/index.js)로 낸다.
const uiEntry = ["src/ui/index.tsx", "src/ui/index.ts"].find((p) => existsSync(p));

export default defineConfig({
  entry: { main: "src/main.ts", ...(uiEntry ? { "ui/index": uiEntry } : {}) },
  format: ["esm"],
  dts: false,
  clean: true,
  target: "node22",
});
