import { describe, expect, it } from "vitest";
import { checkNodeVersion, VERSION } from "./version.js";

describe("version", () => {
  it("has a version string", () => {
    expect(VERSION).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("rejects old node and accepts new node", () => {
    expect(checkNodeVersion("20.11.0")).toContain("Node.js >= 22");
    expect(checkNodeVersion("22.22.0")).toBeNull();
  });
});
