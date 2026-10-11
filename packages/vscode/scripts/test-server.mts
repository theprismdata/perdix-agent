import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve, sep } from "node:path";
import { ChatController } from "../src/chat-controller.js";
import { resolveExtensionServer } from "../src/server-connection.js";
import { connectToServer } from "../../cli/src/agent/connection.js";
import { spawnServer } from "../../cli/src/agent/spawn.js";

const root = mkdtempSync(join(tmpdir(), "perdix-webview-test-"));
const script = join(root, "fake.json");
writeFileSync(script, JSON.stringify([{ text: "Webview server reply" }]));
writeFileSync(join(root, "credentials.json"), "{}");
const events: unknown[] = [];
let sessionId = "";
const controller = new ChatController({
  post: (event) => events.push(event),
  connect: async (restoreId) => {
    const server = spawnServer(resolveExtensionServer(process.argv[2] ?? ""), { cwd: root, logDir: root,
      extraArgs: ["--provider", "fake", "--model", "fake-model", "--fake-script", script, "--db", join(root, "test.db"), "--credentials", join(root, "credentials.json"), "--permission-mode", "deny"] });
    const connection = await connectToServer(server);
    const session = restoreId ? await connection.loadSession({ sessionId: restoreId, cwd: root }) : await connection.newSession({ cwd: root });
    sessionId = session.info.sessionId;
    return session;
  },
});
try {
  await controller.send("hello");
  assert.ok(events.some((e) => JSON.stringify(e).includes("Webview server reply")), "No streamed server response");
  assert.ok(sessionId, "No session created");
  events.length = 0;
  await controller.reset(sessionId);
  assert.ok(events.some((e) => JSON.stringify(e).includes("Webview server reply")), "Session replay missing");
  assert.equal(controller.isBusy, false);
  console.log("PASS: installed Python server ACP initialize, new session, streamed prompt, session restore, shutdown (fake provider)");
} finally {
  await controller.dispose();
  assert.ok(resolve(root).startsWith(resolve(tmpdir()) + sep + "perdix-webview-test-"));
  rmSync(root, { recursive: true, force: true });
}
