import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { runAuthCommand } from "./auth.js";

const directories: string[] = [];
afterEach(() => {
  for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
});

describe("named authentication profiles", () => {
  it("registers and selects each provider through the Python server without printing keys", async () => {
    const directory = mkdtempSync(join(tmpdir(), "perdix-auth-"));
    directories.push(directory);
    const server = resolve("../../server");
    const options = {
      env: { ...process.env, PERDIX_SERVER: `uv run --project ${server} perdix-server` },
      credentialsPath: join(directory, "credentials.json"),
      onOutput: (text: string) => { output += text; },
    };
    let output = "";

    for (const provider of ["anthropic", "openai", "gemini", "openrouter"]) {
      expect(await runAuthCommand(["login", "--provider", provider, "--profile", `${provider}-work`,
        "--api-key", `private-${provider}`], options)).toBe(0);
      expect(await runAuthCommand(["use", "--provider", provider, "--profile", `${provider}-work`], options)).toBe(0);
    }
    expect(await runAuthCommand(["list"], options)).toBe(0);

    const registry = JSON.parse(readFileSync(join(directory, "auth-profiles.json"), "utf8"));
    for (const provider of ["anthropic", "openai", "gemini", "openrouter"]) {
      expect(registry.defaults[provider]).toBe(`${provider}-work`);
      expect(output).not.toContain(`private-${provider}`);
    }
  }, 30_000);

  it("rejects unknown providers and path traversal before starting authentication", async () => {
    let output = "";
    const options = { env: {}, onOutput: (text: string) => { output += text; } };

    const result = await runAuthCommand(["login", "--provider", "unsupported", "--profile", "../escape"], options);

    expect(result).toBe(1);
    expect(output.length).toBeGreaterThan(0);
  });
});
