import { describe, expect, it } from "vitest";
import { hermesUiEnv } from "./launch.js";

describe("Hermes UI process environment", () => {
  it("keeps terminal settings without forwarding keys or Hermes backend configuration", () => {
    // Given
    const env = { PATH: "/usr/bin", TERM: "xterm-256color", OPENAI_API_KEY: "secret", HERMES_HOME: "/old", HERMES_PYTHON: "/backend" };
    // When
    const actual = hermesUiEnv({ gatewayUrl: "ws://127.0.0.1:1234/token", cwd: "/project", env });
    // Then
    expect(actual).toMatchObject({ PATH: "/usr/bin", TERM: "xterm-256color", HERMES_TUI_GATEWAY_URL: "ws://127.0.0.1:1234/token", HERMES_TUI_CWD: "/project" });
    expect(actual.OPENAI_API_KEY).toBeUndefined();
    expect(actual.HERMES_PYTHON).toBeUndefined();
    expect(actual.HERMES_HOME).not.toBe("/old");
    expect(actual.HERMES_TUI_PERDIX).toBe("1");
  });
});
