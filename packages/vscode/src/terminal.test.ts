import { describe, expect, it } from "vitest";
import { buildTerminalEnv } from "./terminal.js";

describe("buildTerminalEnv", () => {
  it("sets PERDIX_IDE_PORT", () => {
    expect(buildTerminalEnv(1234)).toEqual({ PERDIX_IDE_PORT: "1234" });
  });
});
