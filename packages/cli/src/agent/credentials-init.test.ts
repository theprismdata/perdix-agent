import { mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ensureCredentialsFile, providerStatus, readCredentials } from "./credentials.js";

function tempPath(): string {
  return join(mkdtempSync(join(tmpdir(), "perdix-init-")), ".perdix", "credentials.json");
}

describe("first-run credentials", () => {
  it("creates the directory and a template without treating empty keys as configured", () => {
    const path = tempPath();
    expect(ensureCredentialsFile(path)).toBe(true);
    expect(readCredentials(path)).toEqual({ anthropic: { api_key: "" }, openai: { api_key: "" }, gemini: { api_key: "" }, openrouter: { api_key: "" } });
    expect(providerStatus("anthropic", "ANTHROPIC_API_KEY", {}, path)).toBe("none");
    expect(providerStatus("openai", "OPENAI_API_KEY", { OPENAI_API_KEY: "env-key" }, path)).toBe("env");
    if (process.platform !== "win32") expect(statSync(path).mode & 0o777).toBe(0o600);
  });

  it.each(['{"anthropic":{"api_key":"saved-key"}}', '{ broken JSON', '{}'])(
    "preserves existing file bytes: %s", (contents) => {
      const path = tempPath();
      ensureCredentialsFile(path);
      writeFileSync(path, contents);
      expect(ensureCredentialsFile(path)).toBe(false);
      expect(readFileSync(path, "utf8")).toBe(contents);
    },
  );

  it("reports filesystem failures instead of overwriting a parent file", () => {
    const root = mkdtempSync(join(tmpdir(), "perdix-init-error-"));
    mkdirSync(join(root, "folder"));
    const blocker = join(root, "folder", "file");
    writeFileSync(blocker, "keep");
    expect(() => ensureCredentialsFile(join(blocker, "credentials.json"))).toThrow();
    expect(readFileSync(blocker, "utf8")).toBe("keep");
  });
});
