import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { findRepoRoot, resolveServerCommand, splitCommand } from "./spawn.js";

function fakeRepo(): string {
  const root = mkdtempSync(join(tmpdir(), "perdix-repo-"));
  mkdirSync(join(root, "server"));
  writeFileSync(join(root, "server", "pyproject.toml"), "");
  mkdirSync(join(root, "packages", "cli", "dist"), { recursive: true });
  return root;
}

describe("resolveServerCommand", () => {
  it("prefers --server, then PERDIX_SERVER", () => {
    const env = { PATH: "", PERDIX_SERVER: "/opt/perdix-server --db x" };
    expect(resolveServerCommand({ server: "srv -a", env })).toEqual({ command: "srv", args: ["-a"], source: "flag" });
    expect(resolveServerCommand({ env })).toEqual({ command: "/opt/perdix-server", args: ["--db", "x"], source: "env" });
  });

  it("falls back to uv dev mode when the repo has server/pyproject.toml", () => {
    const root = fakeRepo();
    const cmd = resolveServerCommand({ env: { PATH: "" }, moduleDir: join(root, "packages", "cli", "dist") });
    expect(cmd).toEqual({
      command: "uv",
      args: ["run", "--project", join(root, "server"), "perdix-server"],
      source: "dev",
    });
  });

  it("finds the repo root from cwd and fails when nothing is found", () => {
    const root = fakeRepo();
    expect(findRepoRoot(join(root, "packages", "cli"))).toBe(root);
    const empty = mkdtempSync(join(tmpdir(), "perdix-empty-"));
    expect(() => resolveServerCommand({ env: { PATH: "" }, moduleDir: empty, cwd: empty })).toThrow(/not found/);
  });

  it("splits commands on whitespace", () => {
    expect(splitCommand("  uv  run x ")).toEqual({ command: "uv", args: ["run", "x"] });
    expect(() => splitCommand("   ")).toThrow();
  });
});
