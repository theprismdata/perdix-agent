import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { AUTH_USAGE, runAuthCommand } from "./auth.js";
import { readCredentials } from "./credentials.js";

function tempCredentialsPath(): string {
  return join(mkdtempSync(join(tmpdir(), "perdix-creds-")), "credentials.json");
}

function fakeAnt(script: string): { dir: string; env: NodeJS.ProcessEnv } {
  const dir = mktempAnt();
  const path = join(dir, "ant");
  writeFileSync(path, `#!/bin/sh\n${script}\n`);
  chmodSync(path, 0o755);
  return { dir, env: { PATH: dir } };
}

function mktempAnt(): string {
  return mkdtempSync(join(tmpdir(), "perdix-ant-"));
}

describe("runAuthCommand", () => {
  it("captures status output without losing the final subprocess bytes", async () => {
    const { env } = fakeAnt('printf "fake auth status\\n"');
    const chunks: string[] = [];
    expect(await runAuthCommand(["status"], { env, onOutput: text => chunks.push(text) })).toBe(0);
    expect(chunks.join("")).toBe("fake auth status\n");
  });

  it("aborts an authentication subprocess when its UI closes", async () => {
    const { env } = fakeAnt('exec /bin/sleep 10');
    const controller = new AbortController();
    const result = runAuthCommand(["login"], { env, signal: controller.signal });
    controller.abort();
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
  });
  it("prints usage on help without needing ant on PATH", async () => {
    const stdout = vi.spyOn(process.stdout, "write").mockImplementation(() => true);
    try {
      const code = await runAuthCommand(["help"], { env: { PATH: "" } });
      expect(code).toBe(0);
      expect(stdout).toHaveBeenCalledWith(AUTH_USAGE);
    } finally {
      stdout.mockRestore();
    }
  });

  it("fails with install instructions when ant is missing", async () => {
    const stderr = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    try {
      const code = await runAuthCommand(["status"], { env: { PATH: "" } });
      expect(code).toBe(1);
      expect(stderr).toHaveBeenCalledWith(expect.stringContaining("brew install"));
    } finally {
      stderr.mockRestore();
    }
  });

  it("forwards to `ant auth status` by default and propagates exit code", async () => {
    const { env } = fakeAnt('[ "$1" = "auth" ] && [ "$2" = "status" ] && exit 0 || exit 9');
    const code = await runAuthCommand([], { env });
    expect(code).toBe(0);
  });

  it("forwards explicit action and args", async () => {
    const { env } = fakeAnt('[ "$1" = "auth" ] && [ "$2" = "login" ] && [ "$3" = "--profile" ] && [ "$4" = "work" ] && exit 0 || exit 9');
    const code = await runAuthCommand(["login", "--profile", "work"], { env });
    expect(code).toBe(0);
  });

  it("warns when ANTHROPIC_API_KEY shadows the profile", async () => {
    const { env } = fakeAnt("exit 0");
    const stderr = vi.spyOn(process.stderr, "write").mockImplementation(() => true);
    try {
      const code = await runAuthCommand(["status"], {
        env: { ...env, ANTHROPIC_API_KEY: "sk-ant-x" },
      });
      expect(code).toBe(0);
      expect(stderr).toHaveBeenCalledWith(expect.stringContaining("ANTHROPIC_API_KEY"));
    } finally {
      stderr.mockRestore();
    }
  });
});

describe.each(["gemini", "openrouter"])("runAuthCommand --provider %s", (provider) => {
  it("registers, reports and removes a Gemini key while preserving other providers", async () => {
    const path = tempCredentialsPath();
    writeFileSync(path, JSON.stringify({ openai: { api_key: "existing" } }));
    const output: string[] = [];
    const runServerAuth = vi.fn();
    const opts = { env: {}, credentialsPath: path, onOutput: (text: string) => { output.push(text); }, runServerAuth };
    expect(await runAuthCommand(["login", "--provider", provider, "--api-key", "test-gemini-key"], opts)).toBe(0);
    expect(readCredentials(path)).toEqual({ openai: { api_key: "existing" }, [provider]: { api_key: "test-gemini-key" } });
    expect(await runAuthCommand(["status", "--provider", provider], opts)).toBe(0);
    expect(output.at(-1)).toContain("credentials.json");
    expect(await runAuthCommand(["status", "--provider", provider], { ...opts, env: { [provider === "openrouter" ? "OPENROUTER_API_KEY" : "GEMINI_API_KEY"]: "env-key" } })).toBe(0);
    expect(output.at(-1)).toContain("환경변수");
    expect(await runAuthCommand(["logout", "--provider", provider], opts)).toBe(0);
    expect(readCredentials(path)).toEqual({ openai: { api_key: "existing" } });
    expect(output.join("")).not.toContain("test-gemini-key");
    expect(runServerAuth).not.toHaveBeenCalled();
  });

  it.each([{ args: [] }, { args: ["--api-key"] }, { args: ["--api-key", ""] }, { args: ["--api-key", "--other"] }])("rejects missing Gemini keys without another provider login: $args", async ({ args }) => {
    const path = tempCredentialsPath();
    const runServerAuth = vi.fn();
    expect(await runAuthCommand(["login", "--provider", provider, ...args], {
      env: {}, credentialsPath: path, runServerAuth, onOutput: () => {},
    })).toBe(1);
    expect(readCredentials(path)).toEqual({});
    expect(runServerAuth).not.toHaveBeenCalled();
  });
});

describe("runAuthCommand --provider openai", () => {
  it("does not require ant on PATH", async () => {
    const path = tempCredentialsPath();
    const code = await runAuthCommand(["login", "--provider", "openai", "--api-key", "sk-test"], {
      env: { PATH: "" },
      credentialsPath: path,
    });
    expect(code).toBe(0);
    expect(readCredentials(path)).toEqual({ openai: { api_key: "sk-test" } });
  });

  it("delegates ChatGPT login to the server when no API key exists", async () => {
    const path = tempCredentialsPath();
    const runServerAuth = vi.fn().mockResolvedValue(0);
    const code = await runAuthCommand(["login", "--provider", "openai"], {
      env: {},
      credentialsPath: path,
      runServerAuth,
    });
    expect(code).toBe(0);
    expect(runServerAuth).toHaveBeenCalledWith("login", {}, path);
    expect(readCredentials(path)).toEqual({});
  });

  it("fails cleanly on an empty key", async () => {
    const path = tempCredentialsPath();
    const code = await runAuthCommand(["login", "--provider", "openai", "--api-key", ""], {
      env: {},
      credentialsPath: path,
    });
    expect(code).toBe(1);
    expect(readCredentials(path)).toEqual({});
  });

  it("reports status from env, then file, then none", async () => {
    const path = tempCredentialsPath();
    const stdout = vi.spyOn(process.stdout, "write").mockImplementation(() => true);
    try {
      await runAuthCommand(["status", "--provider", "openai"], {
        env: {},
        credentialsPath: path,
        runServerAuth: async () => {
          process.stdout.write("openai: 로그인되어 있지 않음\n");
          return 0;
        },
      });
      expect(stdout).toHaveBeenLastCalledWith(expect.stringContaining("로그인되어 있지 않음"));

      await runAuthCommand(["login", "--provider", "openai", "--api-key", "sk-file"], {
        env: {},
        credentialsPath: path,
      });
      await runAuthCommand(["status", "--provider", "openai"], {
        env: {},
        credentialsPath: path,
      });
      expect(stdout).toHaveBeenLastCalledWith(expect.stringContaining("credentials.json"));

      await runAuthCommand(["status", "--provider", "openai"], {
        env: { OPENAI_API_KEY: "sk-env" },
        credentialsPath: path,
      });
      expect(stdout).toHaveBeenLastCalledWith(expect.stringContaining("환경변수"));
    } finally {
      stdout.mockRestore();
    }
  });

  it("logout removes the stored key", async () => {
    const path = tempCredentialsPath();
    await runAuthCommand(["login", "--provider", "openai", "--api-key", "sk-x"], {
      env: {},
      credentialsPath: path,
    });
    const code = await runAuthCommand(["logout", "--provider", "openai"], {
      env: {},
      credentialsPath: path,
      runServerAuth: vi.fn().mockResolvedValue(0),
    });
    expect(code).toBe(0);
    expect(readCredentials(path)).toEqual({});
  });

  it("does not open ChatGPT login when an environment key exists", async () => {
    const runServerAuth = vi.fn().mockResolvedValue(0);
    const code = await runAuthCommand(["login", "--provider", "openai"], {
      env: { OPENAI_API_KEY: "env-key" }, credentialsPath: tempCredentialsPath(), runServerAuth,
    });
    expect(code).toBe(0);
    expect(runServerAuth).not.toHaveBeenCalled();
  });

  it("does not open ChatGPT login when a saved key exists", async () => {
    const path = tempCredentialsPath();
    writeFileSync(path, JSON.stringify({ openai: { api_key: "saved-key" } }));
    const runServerAuth = vi.fn().mockResolvedValue(0);
    const code = await runAuthCommand(["login", "--provider", "openai"], {
      env: {}, credentialsPath: path, runServerAuth,
    });
    expect(code).toBe(0);
    expect(runServerAuth).not.toHaveBeenCalled();
  });

  it("propagates login failure from the Python server", async () => {
    const code = await runAuthCommand(["login", "--provider", "openai"], {
      env: {}, credentialsPath: tempCredentialsPath(), runServerAuth: async () => 7,
    });
    expect(code).toBe(7);
  });

  it("forwards OAuth authentication to the resolved server process", async () => {
    const dir = mktempAnt();
    const server = join(dir, "perdix-server");
    writeFileSync(server, '#!/bin/sh\n[ "$1" = "--openai-auth" ] && [ "$2" = "login" ] && [ "$3" = "--credentials" ] && exit 7 || exit 9\n');
    chmodSync(server, 0o755);
    const code = await runAuthCommand(["login", "--provider", "openai"], {
      env: { PATH: dir }, credentialsPath: tempCredentialsPath(),
    });
    expect(code).toBe(7);
  });
});
