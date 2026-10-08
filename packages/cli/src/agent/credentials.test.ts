import { mkdtempSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { providerStatus, readCredentials, removeProvider, setProviderApiKey } from "./credentials.js";

function tempPath(): string {
  return join(mkdtempSync(join(tmpdir(), "perdix-creds-")), "credentials.json");
}

describe("credentials file", () => {
  it("returns empty object when the file does not exist", () => {
    expect(readCredentials(tempPath())).toEqual({});
  });

  it("writes and reads back a provider api_key, matching perdix server's format", () => {
    const path = tempPath();
    setProviderApiKey("openai", "sk-test-123", path);
    expect(readCredentials(path)).toEqual({ openai: { api_key: "sk-test-123" } });
  });

  it("restricts file permissions to 0600 on write", () => {
    const path = tempPath();
    setProviderApiKey("openai", "sk-test-123", path);
    const mode = statSync(path).mode & 0o777;
    expect(mode).toBe(0o600);
  });

  it("preserves other providers when setting one", () => {
    const path = tempPath();
    setProviderApiKey("anthropic", "sk-ant-1", path);
    setProviderApiKey("openai", "sk-oai-1", path);
    expect(readCredentials(path)).toEqual({
      anthropic: { api_key: "sk-ant-1" },
      openai: { api_key: "sk-oai-1" },
    });
  });

  it("removes a provider and reports whether anything was removed", () => {
    const path = tempPath();
    setProviderApiKey("openai", "sk-oai-1", path);
    expect(removeProvider("openai", path)).toBe(true);
    expect(readCredentials(path)).toEqual({});
    expect(removeProvider("openai", path)).toBe(false);
  });

  it("falls back to empty object on malformed JSON instead of throwing", () => {
    const path = tempPath();
    setProviderApiKey("openai", "x", path);
    writeFileSync(path, "{ not json");
    expect(readCredentials(path)).toEqual({});
  });

  it("writes pretty JSON readable by the python server's expected shape", () => {
    const path = tempPath();
    setProviderApiKey("openai", "sk-test", path);
    const text = readFileSync(path, "utf8");
    expect(JSON.parse(text)).toEqual({ openai: { api_key: "sk-test" } });
  });
});

describe("providerStatus", () => {
  it("prefers env over the credentials file", () => {
    const path = tempPath();
    setProviderApiKey("openai", "sk-file", path);
    expect(providerStatus("openai", "OPENAI_API_KEY", { OPENAI_API_KEY: "sk-env" }, path)).toBe(
      "env",
    );
  });

  it("falls back to the file when env is unset", () => {
    const path = tempPath();
    setProviderApiKey("openai", "sk-file", path);
    expect(providerStatus("openai", "OPENAI_API_KEY", {}, path)).toBe("file");
  });

  it("reports none when neither env nor file has a key", () => {
    expect(providerStatus("openai", "OPENAI_API_KEY", {}, tempPath())).toBe("none");
  });
});
