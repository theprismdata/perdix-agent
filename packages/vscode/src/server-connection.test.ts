import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveExtensionServer } from "./server-connection.js";

describe("extension server discovery", () => {
  it("finds the Windows installer server without requiring PATH and keeps spaces intact", () => {
    const root = mkdtempSync(join(tmpdir(), "perdix webview "));
    try {
      const path = join(root, "Programs", "Perdix", "server", "perdix-server.exe");
      mkdirSync(join(root, "Programs", "Perdix", "server"), { recursive: true }); writeFileSync(path, "");
      expect(resolveExtensionServer("", { LOCALAPPDATA: root, PATH: "" }, "win32")).toEqual({ command: path, args: [], source: "bundled" });
      expect(resolveExtensionServer(path, { PATH: "" }, "win32").command).toBe(path);
    } finally { rmSync(root, { recursive: true, force: true }); }
  });

  it("reports missing server paths without silently running another executable", () => {
    expect(() => resolveExtensionServer("/missing/perdix-server", { PATH: "" })).toThrow("서버 파일");
    expect(() => resolveExtensionServer("", { PATH: "" }, "win32")).toThrow("서버를 찾지");
  });
});
