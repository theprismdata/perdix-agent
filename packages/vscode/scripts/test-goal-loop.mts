import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium, type Page } from "playwright";
import { ChatController, chatMessage } from "../src/chat-controller.js";
import { chatHtml } from "../src/webview.js";
import { connectToServer } from "../../cli/src/agent/connection.js";
import { spawnServer } from "../../cli/src/agent/spawn.js";

const root = fileURLToPath(new URL("../", import.meta.url));
const repo = resolve(root, "../..");
const project = await mkdtemp(join(tmpdir(), "perdix-loop-browser-"));
const output = join(repo, "dist", "goal-loop-qa");
await mkdir(output, { recursive: true });
await mkdir(join(project, ".perdix"));
await writeFile(join(project, "answer"), "wrong");
await writeFile(join(project, "credentials.json"), "{}");
await writeFile(join(project, ".perdix/config.json"), JSON.stringify({
  verify: { commands: [{ name: "answer", run: "node -e \"if(require('fs').readFileSync('answer','utf8')!=='42') process.exit(1)\"" }] },
}));
await writeFile(join(project, "fake.json"), JSON.stringify([
  { text: "첫 작업 완료" },
  { tool_uses: [{ id: "repair", name: "write", input: { path: "answer", content: "42" } }] },
  { text: "파일 수정 완료" },
]));
const errors: string[] = [];
const posts: unknown[] = [];
let page: Page | undefined;
let activeSend: Promise<void> | undefined;
const controller = new ChatController({
  post: (message) => {
    posts.push(message);
    if (page) void page.evaluate((data) => window.dispatchEvent(new MessageEvent("message", { data })), message)
      .catch((error: unknown) => errors.push(String(error)));
  },
  connect: async () => {
    const server = spawnServer({ command: "uv", source: "dev", args: [
      "run", "--project", join(repo, "server"), "perdix-server", "--provider", "fake",
      "--model", "fake", "--fake-script", join(project, "fake.json"), "--db", join(project, "events.db"),
      "--credentials", join(project, "credentials.json"), "--trust-project",
    ] }, { cwd: project, logDir: project });
    const connection = await connectToServer(server);
    return connection.newSession({ cwd: project });
  },
});
const palette = (light: boolean) => `:root{--vscode-sideBar-background:${light ? "#f7f8fa" : "#181a20"};--vscode-foreground:${light ? "#202431" : "#e1e3ec"};--vscode-descriptionForeground:${light ? "#545b6b" : "#9ca1b2"};--vscode-font-family:Arial,sans-serif;--vscode-font-size:13px;--vscode-panel-border:${light ? "#c5cad4" : "#353945"};--vscode-input-background:${light ? "#fff" : "#242832"};--vscode-button-background:#515bbe;--vscode-button-foreground:white;--vscode-button-secondaryBackground:${light ? "#e4e7ee" : "#2c303c"};--vscode-textLink-foreground:${light ? "#404c9d" : "#b2b8ff"};--vscode-dropdown-background:${light ? "#fff" : "#242832"};--vscode-focusBorder:${light ? "#404c9d" : "#8790ff"};--vscode-errorForeground:${light ? "#b11b1b" : "#ff9393"};}`;
let origin = "";
const http = createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", origin);
  if (url.pathname === "/") {
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    response.end(chatHtml(origin, origin + "/chat.js", origin + "/chat.css" + url.search, "loop-test"));
  } else if (url.pathname === "/chat.js") {
    response.setHeader("Content-Type", "application/javascript");
    response.end(await readFile(join(root, "media/chat.js")));
  } else if (url.pathname === "/chat.css") {
    response.setHeader("Content-Type", "text/css");
    response.end(palette(url.searchParams.get("theme") === "light") + await readFile(join(root, "media/chat.css"), "utf8"));
  } else { response.writeHead(404); response.end(); }
});
await new Promise<void>((done) => http.listen(0, "127.0.0.1", done));
const address = http.address();
assert.ok(address && typeof address !== "string");
origin = `http://127.0.0.1:${address.port}`;
const browser = await chromium.launch({ headless: true });
try {
  page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.exposeFunction("hostMessage", (raw: unknown) => {
    const message = chatMessage.parse(raw);
    switch (message.type) {
      case "ready": return controller.initialize();
      case "send": activeSend = controller.send(message.text); return;
      case "permission": controller.answer(message.requestId, message.optionId); return;
      case "cancel": return controller.cancel();
      case "new": case "history": case "keys": case "folder": case "settings": case "mode": case "model":
        throw new Error(`검증 시나리오 밖의 입력: ${message.type}`);
      default: {
        const unreachable: never = message;
        throw new Error(String(unreachable));
      }
    }
  });
  await page.addInitScript({ content: `window.acquireVsCodeApi = () => ({
    postMessage: (message) => window.hostMessage(message),
    getState: () => null, setState: () => {}
  });` });
  await page.goto(origin);
  await page.waitForFunction(() => !document.querySelector<HTMLButtonElement>("#send")?.disabled);
  await page.locator("#prompt").fill("/lo");
  assert.equal(await page.locator("#slash-menu [role=option]").count(), 4);
  await page.screenshot({ path: join(output, "menu-before-start.png") });
  await page.locator("#prompt").press("Enter");
  assert.equal(await page.locator("#prompt").inputValue(), "/loop ");
  await page.locator("#prompt").fill("/loop answer 파일을 42로 고쳐줘");
  await page.locator("#prompt").press("Enter");
  await page.getByRole("button", { name: "Allow once", exact: true }).waitFor();
  await page.locator("#prompt").fill("/");
  assert.equal(await page.locator("#slash-menu [role=option]").count(), 2);
  await page.screenshot({ path: join(output, "menu-while-verifying.png") });
  await page.getByRole("option", { name: "/loop status · 진행 상태 확인" }).click();
  await page.locator("#prompt").press("Enter");
  assert.equal(controller.isBusy, true);
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await page.getByRole("button", { name: "Allow once", exact: true }).waitFor();
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await page.getByRole("button", { name: "Allow once", exact: true }).waitFor();
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#messages")?.textContent?.includes("검증 통과 · 완료 기준 통과"));
  await activeSend;
  assert.equal(controller.isBusy, false);
  assert.equal(await readFile(join(project, "answer"), "utf8"), "42");
  for (const theme of ["light", "dark"]) {
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto(`${origin}/?theme=${theme}`);
      await page.waitForFunction(() => !document.querySelector<HTMLButtonElement>("#send")?.disabled);
      await page.locator("#prompt").fill("/");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: join(output, `menu-${theme}-${width}.png`) });
      await page.locator("#prompt").press("Escape");
      await page.locator("#messages").evaluate((element) => { element.scrollTop = element.scrollHeight; });
      await page.screenshot({ path: join(output, `result-${theme}-${width}.png`) });
    }
  }
  await page.setViewportSize({ width: 260, height: 640 });
  await page.locator("#prompt").fill("/");
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await page.screenshot({ path: join(output, "menu-narrow-260.png") });
  assert.deepEqual(errors, []);
  await writeFile(join(output, "events.json"), JSON.stringify(posts, null, 2));
  console.log("PASS: browser slash menu → Python ACP goal → failed verifier → file repair → passed verifier; busy status, keyboard, light/dark, narrow layout");
} finally {
  await writeFile(join(output, "events.json"), JSON.stringify(posts, null, 2));
  await writeFile(join(output, "browser-errors.json"), JSON.stringify(errors, null, 2));
  await page?.screenshot({ path: join(output, "last-state.png") });
  await controller.dispose();
  await browser.close();
  await new Promise<void>((done) => http.close(() => done()));
  await rm(project, { recursive: true, force: true });
}
