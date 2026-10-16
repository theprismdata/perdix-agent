import { test, expect } from "playwright/test";
import { createServer, type Server } from "node:http";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../../..");
const evidence = resolve(root, ".omo/evidence/verified-team-workflows");
const fixtureToken = "workflow-ui-fixture-token-only";
let server: Server;
let base: string;
let seq = 2;
let fail = false;
let missing = false;
let snapshotReads = 0;
let eventCursor = 0;
let nodeCount = 3;
let runFailure: string | undefined;
let controls: unknown[] = [];
let controlFailure = false;
const snapshot = () => ({
  run: { runId: "run-fixture", status: runFailure ? "blocked" : "running", verificationStatus: "failed", acceptanceStatus: "awaiting", failureReason: runFailure },
  plan: { tasks: Array.from({ length: nodeCount }, (_, i) => ({ taskId: `task-${i}`, title: i === 1 ? "<img src=x onerror=alert(1)>" : `작업 ${i + 1} · 변경 검토`, role: i === 0 ? "analyze" : "implement", model: "fake", paths: ["src/app.py"], acceptance: ["실제 검사 통과"], dependencies: i ? [`task-${Math.floor((i - 1) / 2)}`] : [] })) },
  attempts: [{ taskId: "task-1", attemptId: "attempt-1", status: seq > 2 ? "completed" : "failed", failureReason: "검증 명령 exit 1", artifacts: [{ path: "logs/verify.txt", kind: "log" }] }, { taskId: "task-1", attemptId: "attempt-2", status: seq > 2 ? "completed" : "running", candidateCommit: "abc123", artifacts: [] }],
  verification: { status: "failed", commands: [{ command: "pytest", exitCode: 1 }] }, seq,
});

test.beforeAll(async () => {
  server = createServer(async (req, res) => {
    const url = new URL(req.url || "/", "http://localhost");
    const send = (data: unknown, status = 200) => { res.writeHead(status, { "Content-Type": "application/json" }); res.end(JSON.stringify(data)); };
    if (url.pathname.startsWith("/api/")) {
      if (req.headers.authorization !== `Bearer ${fixtureToken}`) { send({ detail: "unauthorized" }, 401); return; }
      if (url.pathname === "/api/status") { send({ projects: [{ name: "fixture" }], channels: { telegram: "disabled", github: "disabled" } }); return; }
      if (url.pathname === "/api/jobs") { send([{ id: "job-1", project: "fixture", source: "web", status: "running", prompt: "병렬 변경 검증", createdAt: Date.now() / 1000 }]); return; }
      if (url.pathname === "/api/teams") { send({ teams: [{ id: "team-1", name: "수동 팀", project: "fixture" }] }); return; }
      if (url.pathname === "/api/teams/team-1") { send({ id: "team-1", name: "수동 팀", seq: 1, members: [{ name: "worker", role: "implement", active: true }], tasks: [{ id: "manual-task", owner: "worker", body: "배정된 작업", paths: ["app.py"], state: "completed", result: "수동 완료 보고" }] }); return; }
      if (url.pathname.endsWith("/workflow/control")) {
        let body = ""; for await (const chunk of req) body += String(chunk);
        controls.push(JSON.parse(body));
        send(controlFailure ? { detail: "실행 중에는 제어할 수 없습니다." } : { status: "queued" }, controlFailure ? 409 : 200); return;
      }
      if (url.pathname.includes("/workflow")) {
        if (fail) { send({ detail: "연결 실패" }, 503); return; }
        if (missing) { send({ detail: "no workflow" }, 404); return; }
        if (url.pathname.endsWith("/events")) { eventCursor = Number(url.searchParams.get("after")); send({ events: [{ seq: 2 }, ...(seq > 2 ? [{ seq }] : [])], seq }); return; }
        snapshotReads++; send(snapshot()); return;
      }
      send([]); return;
    }
    const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    if (!["index.html", "dashboard.js", "dashboard.css", "graph.js", "graph.css", "workflow-editor.js", "draft-plan.js", "draft-canvas.js", "editor.css", "favicon.svg"].includes(name)) { res.writeHead(404).end(); return; }
    const data = await readFile(resolve(root, "server/src/perdix/gateway/static", name));
    res.writeHead(200, { "Content-Type": name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : name.endsWith(".svg") ? "image/svg+xml" : "text/html" }); res.end(data);
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  const address = server.address(); if (!address || typeof address === "string") throw new Error("port missing");
  base = `http://127.0.0.1:${address.port}`;
});
test.afterAll(async () => { await new Promise<void>((done, reject) => server.close((error) => error ? reject(error) : done())); });
test.beforeEach(async ({ page }) => {
  seq = 2; fail = false; missing = false; snapshotReads = 0; nodeCount = 3; runFailure = undefined; controls = []; controlFailure = false;
  await page.goto(base);
  await page.getByLabel("연결 토큰").fill(fixtureToken);
  await page.getByRole("button", { name: "연결하기", exact: true }).click();
  await page.locator("#jobs button").first().click();
  await expect(page.locator(".graph-node")).toHaveCount(3);
});

test("dependency edges, safe text, keyboard selection, view controls, manual assignment", async ({ page }) => {
  await expect(page.locator(".graph-edge")).toHaveCount(2);
  await page.locator("#workflow-panel").screenshot({ path: `${evidence}/screenshots/workflow-running.png` });
  await page.locator(".graph-node").first().focus();
  await page.keyboard.press("ArrowRight"); await page.keyboard.press("Enter");
  await expect(page.locator("#graph-selection-title")).toHaveText("<img src=x onerror=alert(1)>");
  await expect(page.locator("#workflow-panel img")).toHaveCount(0);
  await expect(page.locator("#graph-selection-body")).toContainText("attempt-1");
  await expect(page.locator("#graph-selection-body")).toContainText("logs/verify.txt");
  const before = await page.locator("#task-graph").getAttribute("viewBox");
  await page.getByRole("button", { name: "그래프 확대", exact: true }).click();
  expect(await page.locator("#task-graph").getAttribute("viewBox")).not.toBe(before);
  await page.getByRole("button", { name: "맞춤 보기" }).click();
  expect(await page.locator("#task-graph").getAttribute("viewBox")).toBe(before);
  await page.getByRole("button", { name: "그래프 왼쪽으로 이동" }).click();
  expect(await page.locator("#task-graph").getAttribute("viewBox")).not.toBe(before);
  await page.locator("#graph-mode").selectOption("team");
  await expect(page.locator(".assignment")).toHaveCount(1);
  await expect(page.locator("#graph-legend")).toContainText("의존성이나 검증 통과를 뜻하지 않습니다");
  await page.locator("#workflow-panel").screenshot({ path: `${evidence}/screenshots/manual-team.png` });
});

test("cursor duplicates, gaps, reconnect, preserved focus and missing workflow", async ({ page }) => {
  await page.locator("#graph-list button").nth(1).focus();
  await expect.poll(() => eventCursor).toBe(2);
  const reads = snapshotReads;
  await page.waitForTimeout(2200);
  expect(snapshotReads).toBe(reads);
  seq = 5;
  await expect.poll(() => snapshotReads).toBeGreaterThan(reads);
  await expect(page.locator("#graph-list button").nth(1)).toBeFocused();
  await expect(page.locator("#graph-list button").nth(1)).toContainText("완료");
  fail = true; await expect(page.locator("#graph-status")).toContainText("자동으로 다시 연결");
  await page.locator("#workflow-panel").screenshot({ path: `${evidence}/screenshots/workflow-reconnect.png` });
  fail = false; await expect(page.locator("#graph-status")).toHaveText("최신 상태입니다.");
  missing = true; await expect(page.locator("#graph-status")).toContainText("계획이 없습니다");
  await page.locator("#workflow-panel").screenshot({ path: `${evidence}/screenshots/workflow-empty.png` });
  missing = false; await expect(page.locator("#graph-body")).toBeVisible();
});

test("explicit control requests, accept confirmation and server rejection", async ({ page }) => {
  await page.getByRole("button", { name: "계획 실행", exact: true }).click();
  await expect.poll(() => controls.length).toBe(1);
  expect(controls[0]).toEqual({ action: "start" });
  await page.locator("#graph-list button").nth(1).click();
  await page.getByRole("button", { name: "선택 작업 재시도" }).click();
  await expect.poll(() => controls.length).toBe(2);
  expect(controls[1]).toEqual({ action: "retry", taskId: "task-1" });
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "검증된 변경 수락" }).click();
  expect(controls).toHaveLength(2);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "검증된 변경 수락" }).click();
  await expect.poll(() => controls.length).toBe(3);
  expect(controls[2]).toEqual({ action: "accept" });
  controlFailure = true;
  await page.getByRole("button", { name: "재개 요청" }).click();
  await expect(page.locator("#workflow-control-status")).toHaveText("실행 중에는 제어할 수 없습니다.");
  await expect(page.getByRole("button", { name: "재개 요청" })).toBeEnabled();
});

test("run failure reason is visible as safe text", async ({ page }) => {
  runFailure = "리뷰 응답이 잘못되었습니다. <img src=x onerror=alert(1)>";
  seq = 6;
  await expect(page.locator("#graph-summary")).toContainText(runFailure);
  await expect(page.locator("#graph-summary")).toContainText("실행: 진행 차단");
  await expect(page.locator("#graph-summary img")).toHaveCount(0);
  await page.locator("#workflow-panel").screenshot({ path: `${evidence}/screenshots/workflow-run-failure.png` });
});

for (const width of [390, 1440]) for (const theme of ["light", "dark"] as const) {
  test.describe(`${width} ${theme}`, () => {
  test.use({ viewport: { width, height: 900 }, isMobile: width === 390, hasTouch: width === 390, deviceScaleFactor: width === 390 ? 3 : 1 });
  test(`20 nodes ${width} ${theme}`, async ({ page }) => {
    await page.locator("#theme").selectOption(theme);
    nodeCount = 20; seq = 20;
    await expect(page.locator(".graph-node")).toHaveCount(20);
    await expect(page.locator(".graph-edge")).toHaveCount(19);
    await page.getByRole("button", { name: "맞춤 보기" }).click();
    await page.locator("#workflow-panel").scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${evidence}/screenshots/workflow-${width}-${theme}.png`, fullPage: true });
    await page.locator("#graph-list button").last().click();
    await page.locator("#graph-selection").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${evidence}/screenshots/workflow-${width}-${theme}-end.png` });
  });
  });
}
