import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "../../../langflow-ui");
const components = resolve(root, "overrides/customization/components");
const SEAMS = [
  "custom-langflow-counts.tsx",
  "custom-account-menu-community-links.tsx",
  "custom-get-started-progress.tsx",
  "custom-empty-page.tsx",
];

describe("Langflow 화면 덮어쓰기 (ADR 0037)", () => {
  it("Langflow의 customization 확장점 네 곳만 덮어쓴다", () => {
    for (const name of SEAMS) {
      const path = resolve(components, name);
      expect(existsSync(path), name).toBe(true);
      expect(readFileSync(path, "utf8"), name).toContain("export default");
    }
  });

  it("홍보 요소(GitHub·Discord 링크, 별/인원 조회)를 다시 끌어오지 않는다", () => {
    for (const name of SEAMS) {
      const source = readFileSync(resolve(components, name), "utf8");
      expect(source, name).not.toMatch(/GITHUB_URL|DISCORD_URL|useCommunityCounts|FaGithub|FaDiscord|community-links"|langflow-counts"|get-started-progress"/);
    }
  });

  it("빌드 스크립트는 고정한 버전의 태그만 받아 쓰고 어긋나면 멈춘다", () => {
    const version = readFileSync(resolve(root, "LANGFLOW_VERSION"), "utf8").trim();
    expect(version).toMatch(/^\d+\.\d+\.\d+$/);
    const script = readFileSync(resolve(root, "build.sh"), "utf8");
    expect(script).toContain("LANGFLOW_VERSION");
    expect(script).toContain("describe --tags --exact-match");
    expect(script).toContain("set -euo pipefail");
  });
});
