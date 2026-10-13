/**
 * 실제 Python 서버(fake provider)를 띄우는 통합 테스트.
 * uv나 서버가 없으면 skip하지 않고 실패한다(docs/impl/m2.md §1).
 */
import { execFileSync, spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeAll, describe, expect, it } from "vitest";
import { connectToServer } from "./connection.js";
import { spawnServer } from "./spawn.js";
import type { AgentEvent } from "./types.js";

const here = dirname(fileURLToPath(import.meta.url));
const cliDir = resolve(here, "..", "..");
const repo = resolve(cliDir, "..", "..");
const serverDir = join(repo, "server");
const demo = join(serverDir, "tests", "fixtures", "demo.json");

function serverArgs(db: string): string[] {
  return ["run", "--project", serverDir, "perdix-server", "--provider", "fake", "--fake-script", demo, "--db", db];
}

/** 기대 순서가 이벤트 목록의 부분열로 나타나는지 확인한다. */
function indexOfAfter(events: AgentEvent[], from: number, match: (e: AgentEvent) => boolean): number {
  for (let i = from; i < events.length; i++) {
    const e = events[i];
    if (e && match(e)) return i;
  }
  return -1;
}

function expectOrder(events: AgentEvent[]): void {
  const steps: [string, (e: AgentEvent) => boolean][] = [
    ["agent_text", (e) => e.type === "agent_text"],
    ["tool_call", (e) => e.type === "tool_call"],
    ["tool_call_update(completed)", (e) => e.type === "tool_call_update" && e.status === "completed"],
    ["usage", (e) => e.type === "usage"],
    ["turn_ended(end_turn)", (e) => e.type === "turn_ended" && e.stopReason === "end_turn"],
  ];
  let at = 0;
  for (const [name, match] of steps) {
    const found = indexOfAfter(events, at, match);
    expect(found, `missing ${name} after index ${at}: ${JSON.stringify(events)}`).toBeGreaterThanOrEqual(0);
    at = found + 1;
  }
  expect(events.at(-1)).toMatchObject({ type: "turn_ended", stopReason: "end_turn" });
}

describe("integration with perdix-server (fake provider)", () => {
  beforeAll(() => {
    expect(existsSync(join(serverDir, "pyproject.toml")), "server/pyproject.toml is required").toBe(true);
    expect(existsSync(demo), "demo fixture is required").toBe(true);
  });

  it("streams agent_text → tool_call → tool_call_update → usage → turn_ended", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "perdix-it-"));
    const server = spawnServer(
      { command: "uv", args: serverArgs(join(tmp, "t.db")), source: "flag" },
      { debug: true, cwd: tmp },
    );
    const connection = await connectToServer(server);
    const session = await connection.newSession({ cwd: tmp });
    expect(session.info.sessionId).toBeTruthy();
    expect(session.info.modes.map((m) => m.id)).toEqual(expect.arrayContaining(["chat", "plan", "build"]));

    const events: AgentEvent[] = [];
    session.onEvent((e) => events.push(e));
    const result = await session.prompt("list files");
    expect(result.stopReason).toBe("end_turn");
    expect(events[0]).toEqual({ type: "turn_started" });
    expectOrder(events);

    await session.close();
    expect(await server.exited).not.toBeUndefined();
  }, 120_000);

  it("runs `perdix -p --json` headless and exits 0", async () => {
    const mainJs = join(cliDir, "dist", "main.js");
    if (!existsSync(mainJs)) {
      execFileSync("pnpm", ["exec", "tsup"], { cwd: cliDir, stdio: "inherit" });
    }
    const tmp = mkdtempSync(join(tmpdir(), "perdix-hl-"));
    const agentsConfig = join(tmp, "agents.json");
    writeFileSync(agentsConfig, JSON.stringify({
      models: { economy: { provider: "openrouter", model: "openai/gpt-6-luna" } },
      agents: { chat: { model: "economy" } },
    }));
    const serverCmd = ["uv", ...serverArgs(join(tmp, "t.db"))].join(" ");
    const child = spawn(process.execPath, [mainJs, "-p", "list files", "--json", "--cwd", tmp, "--server", serverCmd, "--agents-config", agentsConfig], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d: Buffer) => (stdout += d.toString()));
    child.stderr.on("data", (d: Buffer) => (stderr += d.toString()));
    const code = await new Promise<number | null>((r) => child.on("close", r));
    expect(code, stderr).toBe(0);

    const lines = stdout.trim().split("\n");
    const events = lines.map((l) => JSON.parse(l) as AgentEvent);
    expect(events[0]).toEqual({ type: "turn_started" });
    expectOrder(events);
    expect(events).toContainEqual(expect.objectContaining({ type: "usage", size: 1_050_000 }));
  }, 120_000);

  it("exits 1 when a trusted goal cannot obtain verification permission", async () => {
    const tmp = mkdtempSync(join(tmpdir(), "perdix-loop-hl-"));
    try {
      mkdirSync(join(tmp, ".perdix"));
      writeFileSync(join(tmp, ".perdix", "config.json"), JSON.stringify({
        verify: { commands: [{ name: "check", run: "node -e \"process.exit(0)\"" }] },
      }));
      const script = join(tmp, "fake.json");
      writeFileSync(script, JSON.stringify([{ text: "done" }]));
      const serverCmd = ["uv", "run", "--project", serverDir, "perdix-server", "--provider", "fake",
        "--fake-script", script, "--db", join(tmp, "events.db")].join(" ");
      const child = spawn(process.execPath, [join(cliDir, "dist", "main.js"), "-p", "/loop fix tests",
        "--json", "--cwd", tmp, "--trust-project", "--permission-mode", "deny", "--server", serverCmd],
      { stdio: ["ignore", "pipe", "pipe"] });
      let stdout = "";
      let stderr = "";
      child.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
      child.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
      const code = await new Promise<number | null>((done) => child.on("close", done));
      expect(code, stderr).toBe(1);
      const events: unknown[] = stdout.trim().split("\n").map((line) => JSON.parse(line));
      expect(events).toContainEqual({ type: "turn_ended", stopReason: "end_turn", verification: "failed" });
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  }, 120_000);
});
