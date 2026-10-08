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

  it("prompts interactively when --api-key is omitted", async () => {
    const path = tempCredentialsPath();
    const promptApiKey = vi.fn().mockResolvedValue("sk-prompted");
    const code = await runAuthCommand(["login", "--provider", "openai"], {
      env: {},
      credentialsPath: path,
      promptApiKey,
    });
    expect(code).toBe(0);
    expect(promptApiKey).toHaveBeenCalledOnce();
    expect(readCredentials(path)).toEqual({ openai: { api_key: "sk-prompted" } });
  });

  it("fails cleanly on an empty key", async () => {
    const path = tempCredentialsPath();
    const code = await runAuthCommand(["login", "--provider", "openai"], {
      env: {},
      credentialsPath: path,
      promptApiKey: vi.fn().mockResolvedValue(""),
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
    });
    expect(code).toBe(0);
    expect(readCredentials(path)).toEqual({});
  });
});
