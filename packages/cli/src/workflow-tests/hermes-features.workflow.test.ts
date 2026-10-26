import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  absentFeature, mcpServersList, mcpServersStatus, profilesList, projectsTree, runtimeCheck,
} from "../hermes/features.js";

const directories: string[] = [];
afterEach(() => { for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true }); });

function mcpConfig(content: unknown): string {
  const directory = mkdtempSync(join(tmpdir(), "perdix-mcp-"));
  directories.push(directory);
  const path = join(directory, "mcp.json");
  writeFileSync(path, typeof content === "string" ? content : JSON.stringify(content));
  return path;
}

describe("Hermes 앱 요청에 대한 Perdix 응답", () => {
  it("MCP 서버는 설정 파일에서 읽고 환경변수 값은 보내지 않는다", () => {
    const path = mcpConfig({ mcpServers: {
      autorag: { command: "/bin/autorag-mcp", args: ["--x"], env: { API_KEY: "very-secret-value", PATH: "/bin" }, readOnly: true },
      off: { command: "tool", enabled: false },
    } });
    const list = mcpServersList(path) as { servers: Array<Record<string, unknown>> };
    expect(list.servers.map((server) => server.name)).toEqual(["autorag", "off"]);
    expect(list.servers[0]).toMatchObject({ transport: "stdio", command: "/bin/autorag-mcp", args: ["--x"], env: ["API_KEY", "PATH"], enabled: true, source: "config" });
    expect(JSON.stringify(list)).not.toContain("very-secret-value");

    const status = mcpServersStatus(path) as { servers: Array<Record<string, unknown>>; checked_at: number };
    expect(status.servers.map((server) => [server.name, server.status, server.disabled])).toEqual([
      ["autorag", "configured", false], ["off", "disabled", true],
    ]);
    expect(status.checked_at).toBeGreaterThan(0);
  });

  it("MCP 설정이 없거나 깨졌으면 빈 목록으로 답한다", () => {
    expect(mcpServersList(join(tmpdir(), "perdix-no-such-mcp.json"))).toEqual({ servers: [] });
    expect(mcpServersStatus(mcpConfig("{깨진 json")).servers).toEqual([]);
    expect(mcpServersList(mcpConfig({ mcpServers: "not-an-object" }))).toEqual({ servers: [] });
  });

  it("프로젝트는 저장된 세션을 작업 폴더별로 묶는다", () => {
    const tree = projectsTree([
      { sessionId: "a", cwd: "/work/app", title: "t", updatedAt: "2026-10-26T01:00:00Z" },
      { sessionId: "b", cwd: "/work/app", updatedAt: "2026-10-26T03:00:00Z" },
      { sessionId: "c", cwd: "/work/lib", updatedAt: null },
    ], "/work/app") as { projects: Array<Record<string, unknown>>; active_id: string | null };
    expect(tree.projects).toHaveLength(2);
    expect(tree.projects[0]).toMatchObject({ id: "/work/app", label: "app", sessionCount: 2, sessionIds: ["a", "b"] });
    expect(tree.projects[0]?.lastActive).toBe(Date.parse("2026-10-26T03:00:00Z") / 1000);
    expect(tree.projects[1]).toMatchObject({ label: "lib", sessionCount: 1, lastActive: 0 });
    expect(tree.active_id).toBe("/work/app");
    // 앱은 repos와 groups를 항상 순회한다(없으면 사이드바가 "failed to render"로 깨진다).
    for (const project of tree.projects) {
      const repos = project.repos as Array<{ groups: unknown[] }>;
      expect(Array.isArray(repos) && repos.length > 0).toBe(true);
      expect(repos.every((repo) => Array.isArray(repo.groups))).toBe(true);
      expect(Array.isArray(project.previewSessions)).toBe(true);
    }
    expect((projectsTree([], "/x") as { active_id: unknown }).active_id).toBeNull();
  });

  it("준비 상태와 프로필은 현재 모델을 반영한다", () => {
    expect(runtimeCheck({ model: "gpt-6-luna", provider: "openrouter" })).toMatchObject({ ok: true, model: "gpt-6-luna", provider: "openrouter" });
    expect(runtimeCheck(null)).toMatchObject({ ok: false });
    expect(String((runtimeCheck({ model: null }) as { error: string }).error)).toContain("모델");
    const profiles = profilesList({ model: "m", provider: "p" }, "/home/.perdix") as { profiles: Array<Record<string, unknown>>; bot_mode_protocol: boolean };
    expect(profiles.profiles).toHaveLength(1);
    expect(profiles.profiles[0]).toMatchObject({ name: "default", is_default: true, model: "m", provider: "p", path: "/home/.perdix" });
    expect(profiles.bot_mode_protocol).toBe(false);
  });

  it("Perdix에 없는 기능은 오류가 아니라 '없음'으로 답한다", () => {
    expect(absentFeature("ping")).toEqual({ pong: true });
    expect(absentFeature("pet.info")).toEqual({ enabled: false });
    expect(absentFeature("shared_metrics.status")).toEqual({ enabled: false, send: false, decided: true, reask: false });
    expect(absentFeature("free_tier.status")).toMatchObject({ enabled: false, available: false, has_guest: false });
    expect(absentFeature("wake.status")).toMatchObject({ enabled: false, listening: false, available: false });
    expect(absentFeature("connectors.list")).toEqual({ available: false, connectors: [] });
    expect(absentFeature("connectors.catalog")).toEqual({ connectors: [] });
    expect(absentFeature("connectors.accounts")).toEqual({ accounts: [] });
    expect(absentFeature("connectors.policy.get")).toMatchObject({ layers: [], effective: { mode: "unrestricted", version: 1 } });
    expect(absentFeature("billing.state")).toEqual({ ok: true, logged_in: false });
    expect(absentFeature("subscription.state")).toEqual({ ok: true, logged_in: false });
    expect(absentFeature("client.capabilities")).toEqual({ server_requests: ["approval"] });
    expect((absentFeature("i18n.languages") as { languages: Array<{ id: string }> }).languages.map((language) => language.id)).toEqual(["en", "ko"]);
    expect(absentFeature("정말.모르는.요청")).toBeUndefined();
  });
});
