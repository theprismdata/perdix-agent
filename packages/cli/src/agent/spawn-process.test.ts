import * as childProcess from "node:child_process";
import { describe, expect, it, vi } from "vitest";
import { spawnServer } from "./spawn.js";

vi.mock("node:child_process", async (importOriginal) => {
  const original = await importOriginal<typeof import("node:child_process")>();
  return { ...original, spawn: vi.fn(original.spawn) };
});

describe("background ACP server", () => {
  it("hides the Windows console while keeping bidirectional stdio usable", async () => {
    const server = spawnServer({ command: process.execPath,
      args: ["-e", "process.stdin.once('data', data => { process.stdout.write('reply:' + data); process.stdin.destroy(); });"], source: "flag" }, { debug: true });
    try {
      expect(childProcess.spawn).toHaveBeenCalledWith(process.execPath, expect.any(Array), expect.objectContaining({
        windowsHide: true, stdio: ["pipe", "pipe", "inherit"],
      }));
      const writer = server.input.getWriter();
      await writer.write(new TextEncoder().encode("hello"));
      writer.releaseLock();
      const reader = server.output.getReader();
      let output = "";
      for (;;) { const next = await reader.read(); if (next.done) break; output += new TextDecoder().decode(next.value); }
      reader.releaseLock();
      expect(output).toBe("reply:hello");
      expect(await server.exited).toBe(0);
    } finally { server.kill(); }
  });
});
