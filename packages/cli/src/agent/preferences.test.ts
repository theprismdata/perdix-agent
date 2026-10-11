import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { expect, it } from "vitest";
import { readDefaultModel } from "./preferences.js";

it("reads the installer preference and ignores absent or malformed settings", () => {
  const path = join(mkdtempSync(join(tmpdir(), "perdix-preferences-")), "preferences.json");
  expect(readDefaultModel(path)).toBeUndefined();
  for (const value of ['null', '{broken', '{}', '{"default_model":42}', '{"default_model":" "}']) {
    writeFileSync(path, value);
    expect(readDefaultModel(path)).toBeUndefined();
  }
  writeFileSync(path, '{"default_model":"gpt-6.1-sol"}');
  expect(readDefaultModel(path)).toBe("gpt-6.1-sol");
});
