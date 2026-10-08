import { describe, expect, it } from "vitest";
import { openDiffParams } from "./index.js";

describe("ide/openDiff params", () => {
  it("accepts valid params", () => {
    const parsed = openDiffParams.parse({
      path: "src/a.ts",
      oldText: "a",
      newText: "b",
      title: "edit",
    });
    expect(parsed.path).toBe("src/a.ts");
  });

  it("rejects invalid params", () => {
    expect(openDiffParams.safeParse({ path: "", oldText: 1 }).success).toBe(false);
  });
});
