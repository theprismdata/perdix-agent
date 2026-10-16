import { test, expect } from "playwright/test";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { mkdtemp, readFile, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";

const root = resolve(import.meta.dirname, "../../..");
const evidence = resolve(root, ".omo/evidence/verified-team-workflows");
const token = "workflow-browser-fixture-token";
let process: ChildProcess;
let fixture: string;
let base: string;
let port = 0;
let output = "";
async function launch() {
  process = spawn(resolve(root, "server/.venv/bin/python"), [resolve(import.meta.dirname, "gateway_fixture.py"), fixture, "--port", String(port)], { cwd: resolve(root, "server"), stdio: ["ignore", "pipe", "pipe"] });
  process.stderr?.on("data", (chunk: Buffer) => { output += chunk.toString(); });
  const ready = await new Promise<string>((done, reject) => {
    let data = "";
    const timeout = setTimeout(() => reject(new Error(`Gateway launch timeout: ${output}`)), 30_000);
    process.once("exit", (code) => { clearTimeout(timeout); reject(new Error(`Gateway exit ${code}: ${output}`)); });
    process.stdout?.on("data", (chunk: Buffer) => { data += chunk.toString(); if (data.includes("\n")) { clearTimeout(timeout); done(data.split("\n")[0]!); } });
  });
  port = (JSON.parse(ready) as { port: number }).port;
  base = `http://127.0.0.1:${port}`;
  await expect.poll(async () => { try { return (await fetch(base)).status; } catch { return 0; } }).toBe(200);
}
async function stop() {
  if (process.exitCode !== null) return;
  const exited = new Promise<void>((done) => process.once("exit", () => done()));
  process.kill("SIGTERM"); await exited;
}

test.use({ trace: "on" });

test("real Gateway edited graph ACP parallel repair, restart and explicit Git acceptance", async ({ page, request }) => {
  test.setTimeout(150_000);
  fixture = await mkdtemp(join(tmpdir(), "perdix-browser-workflow-"));
  await mkdir(evidence, { recursive: true });
  await launch();
  try {
    const plan = JSON.parse(await readFile(join(fixture, "plan.json"), "utf8")) as { objective: string; tasks: { taskId: string; title: string; role: string; paths: string[]; acceptance: string[]; dependencies?: string[] }[] };
    const head = () => execFileSync("git", ["-C", join(fixture, "repo"), "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    const original = head();
    await page.goto(base);
    await page.getByLabel("연결 토큰").fill(token);
    await page.getByRole("button", { name: "연결하기", exact: true }).click();
    await page.locator("#workflow-editor > summary").click();
    await page.getByLabel("워크플로 목표", { exact: true }).fill(plan.objective);
    const names: Record<string, string> = { analyze: "분석", implement: "구현", review: "리뷰" };
    for (const task of plan.tasks) {
      await page.getByRole("button", { name: `+ ${names[task.role]} 노드`, exact: true }).click();
      await page.getByLabel("노드 이름", { exact: true }).fill(task.title);
      await page.getByLabel("노드 ID", { exact: true }).fill(task.taskId);
      await page.getByLabel("파일 범위 · 한 줄에 하나", { exact: true }).fill(task.paths.join("\n"));
      await page.getByLabel("완료 조건 · 한 줄에 하나", { exact: true }).fill(task.acceptance.join("\n"));
      await page.getByRole("button", { name: "노드 설정 적용", exact: true }).click();
    }
    for (const task of plan.tasks) for (const from of task.dependencies || []) {
      await page.getByLabel("출발 노드", { exact: true }).selectOption(from);
      await page.getByLabel("도착 노드", { exact: true }).selectOption(task.taskId);
      await page.getByRole("button", { name: "노드 연결 추가", exact: true }).click();
    }
    await page.getByRole("button", { name: "새 계획 만들기", exact: true }).click();
    await expect(page.locator(".graph-node")).toHaveCount(3, { timeout: 30_000 });
    expect(head()).toBe(original);
    await page.getByRole("button", { name: "계획 실행", exact: true }).click();
    const deadline = Date.now() + 90_000;
    while (Date.now() < deadline) {
      for (const button of await page.getByRole("button", { name: "이번만 허용", exact: true }).all()) {
        if (await button.isVisible()) await button.click();
      }
      if ((await page.locator("#graph-summary").textContent())?.includes("실행: 완료")) break;
      await page.waitForTimeout(200);
    }
    await expect(page.locator("#graph-summary")).toContainText("검증: 통과");
    await expect(page.locator("#graph-summary")).toContainText("수락: 수락 대기");
    expect(head()).toBe(original);
    const headers = { Authorization: `Bearer ${token}` };
    const jobs = await (await request.get(`${base}/api/jobs`, { headers })).json() as { id: string }[];
    const job = jobs[0]!.id;
    const snapshot = await (await request.get(`${base}/api/jobs/${job}/workflow`, { headers })).json() as { attempts: { taskId: string; status: string }[]; run: { candidateCommit: string; runId: string } };
    expect(snapshot.attempts.filter((attempt) => attempt.taskId === "a")).toHaveLength(2);
    expect(snapshot.attempts.filter((attempt) => attempt.taskId === "b")).toHaveLength(2);
    const events = await (await request.get(`${base}/api/jobs/${job}/workflow/events?after=0`, { headers })).json() as { events: { payload: { kind: string; receipt?: { status: string } } }[] };
    expect(events.events.filter((event) => event.payload.kind === "verification").map((event) => event.payload.receipt?.status)).toEqual(["failed", "passed"]);
    await writeFile(join(evidence, "task-10-real-events.json"), JSON.stringify(events, null, 2));
    await writeFile(join(evidence, "task-10-real-snapshot.json"), JSON.stringify(snapshot, null, 2));
    await page.locator("#workflow-panel").screenshot({ path: join(evidence, "screenshots/real-awaiting-acceptance.png") });
    await stop(); await launch();
    await expect(page.locator("#graph-status")).toHaveText("최신 상태입니다.", { timeout: 15_000 });
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "검증된 변경 수락" }).click();
    await expect(page.locator("#graph-summary")).toContainText("수락: 수락됨", { timeout: 30_000 });
    expect(head()).toBe(snapshot.run.candidateCommit);
    expect(await readFile(join(fixture, "repo/a.txt"), "utf8")).toBe("good");
    expect(await readFile(join(fixture, "repo/b.txt"), "utf8")).toBe("good");
    await page.locator("#workflow-panel").screenshot({ path: join(evidence, "screenshots/real-accepted.png") });
    await writeFile(join(evidence, "task-10-real-head.json"), JSON.stringify({ fixture, original, accepted: head(), candidate: snapshot.run.candidateCommit, runId: snapshot.run.runId }, null, 2));
  } finally {
    await stop();
    await writeFile(join(evidence, "task-10-real-gateway.log"), output || "Gateway emitted no errors.\n");
  }
});
