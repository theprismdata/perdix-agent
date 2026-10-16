import { test, expect, type Page } from "playwright/test";
import { readFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "../../..");
const evidence = resolve(root, ".omo/evidence/workflow-node-editor");
let submissions: { project: string; prompt: string }[];
let rejectSubmit: boolean;
const sourcePlan = { objective: "기존 실행", tasks: [{ taskId: "original", title: "기존 노드", role: "analyze", model: "fake-model", paths: ["."], acceptance: ["분석 완료"], dependencies: [] }] };
const assets = new Set(["index.html", "dashboard.js", "dashboard.css", "graph.js", "graph.css", "workflow-editor.js", "draft-plan.js", "draft-canvas.js", "editor.css", "favicon.svg"]);

test.beforeEach(async ({ page }) => {
  submissions = []; rejectSubmit = false;
  await mkdir(`${evidence}/screenshots`, { recursive: true });
  await page.route("**/*", async (route) => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (path.startsWith("/api/")) {
      if (req.headers().authorization !== "Bearer workflow-editor-fixture-token") return json({ detail: "unauthorized" }, 401);
      if (path === "/api/status") return json({ projects: [{ name: "fixture" }, { name: "other" }], workflowDefaultModel: "fake-model", channels: { telegram: "disabled", github: "disabled" } });
      if (path === "/api/jobs" && req.method() === "POST") {
        if (rejectSubmit) return json({ detail: "계획 제출 거절" }, 409);
        submissions.push(req.postDataJSON() as { project: string; prompt: string });
        return json({ id: "job-1", project: "fixture", prompt: "새 계획", status: "queued", source: "web" }, 202);
      }
      if (path === "/api/jobs") return json([{ id: "job-1", project: "fixture", prompt: "기존 실행", status: "completed", source: "web", createdAt: 0 }]);
      if (path.endsWith("/workflow/events")) return json({ seq: 1, events: [] });
      if (path.endsWith("/workflow")) return json({ seq: 1, run: { runId: "original-run", status: "completed" }, plan: sourcePlan, attempts: [] });
      return json([]);
    }
    const name = path === "/" ? "index.html" : path.slice(1);
    if (!assets.has(name)) return route.fulfill({ status: 404 });
    return route.fulfill({ contentType: name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : name.endsWith(".svg") ? "image/svg+xml" : "text/html", body: await readFile(resolve(root, "server/src/perdix/gateway/static", name)) });
  });
  await page.goto("http://perdix.test/");
  await page.getByLabel("연결 토큰").fill("workflow-editor-fixture-token");
  await page.getByRole("button", { name: "연결하기", exact: true }).click();
  await page.locator("#workflow-editor > summary").click();
});
async function add(page: Page, key: string, title = key, role = "구현") {
  await page.getByRole("button", { name: `+ ${role} 노드`, exact: true }).click();
  await page.getByLabel("노드 이름", { exact: true }).fill(title);
  await page.getByLabel("노드 ID", { exact: true }).fill(key);
  await page.getByLabel("완료 조건 · 한 줄에 하나", { exact: true }).fill("실제 검증 통과");
  await page.getByRole("button", { name: "노드 설정 적용", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("적용했습니다");
}
async function connect(page: Page, from: string, to: string) {
  await page.getByLabel("출발 노드", { exact: true }).selectOption(from);
  await page.getByLabel("도착 노드", { exact: true }).selectOption(to);
  await page.getByRole("button", { name: "노드 연결 추가", exact: true }).click();
}

test("editor adds nodes, rejects cycles/duplicates and deletes nodes with dependencies", async ({ page }) => {
  await add(page, "a", "요구사항 분석", "분석"); await add(page, "b", "기능 구현"); await add(page, "c", "변경 리뷰", "리뷰");
  await connect(page, "a", "b"); await connect(page, "b", "c");
  await expect(page.locator(".draft-edge")).toHaveCount(2);
  await connect(page, "c", "a"); await expect(page.locator("#draft-status")).toContainText("순환");
  await connect(page, "a", "b"); await expect(page.locator("#draft-status")).toContainText("이미");
  await page.getByRole("button", { name: "a → b 연결 삭제", exact: true }).click();
  await expect(page.locator(".draft-edge")).toHaveCount(1);
  await page.locator("#draft-list button").nth(1).click();
  await expect(page.locator("#draft-list button").nth(1)).toBeFocused();
  await page.getByRole("button", { name: "선택 노드 삭제", exact: true }).click();
  await expect(page.locator(".draft-node")).toHaveCount(2); await expect(page.locator(".draft-edge")).toHaveCount(0);
});

test("editor pointer connections, dragging, keyboard placement and project draft restore", async ({ page }) => {
  await add(page, "a", "분석", "분석"); await add(page, "b", "구현");
  const output = page.getByRole("button", { name: "분석 출력 연결점", exact: true });
  const input = page.getByRole("button", { name: "구현 입력 연결점", exact: true });
  await output.scrollIntoViewIfNeeded();
  const from = await output.boundingBox(), to = await input.boundingBox();
  if (!from || !to) throw new Error("ports missing");
  expect(from.y).toBeGreaterThanOrEqual(0); expect(to.y).toBeGreaterThanOrEqual(0);
  await test.info().attach("port-hit-targets", { contentType: "application/json", body: JSON.stringify(await page.evaluate(({ from, to }) => ({ from, to, height: innerHeight, scrollY, fromTarget: document.elementFromPoint(from.x + from.width / 2, from.y + from.height / 2)?.outerHTML, toTarget: document.elementFromPoint(to.x + to.width / 2, to.y + to.height / 2)?.outerHTML }), { from, to })) });
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2); await page.mouse.down();
  await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 8 }); await page.mouse.up();
  await expect(page.locator(".draft-edge")).toHaveCount(1);
  const node = page.locator('.draft-node[data-key="b"]');
  const original = await node.getAttribute("transform"), box = await node.boundingBox();
  if (!box) throw new Error("node missing");
  await page.mouse.move(box.x + box.width / 2, box.y + 12); await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 30, box.y + 80, { steps: 5 }); await page.mouse.up();
  expect(await node.getAttribute("transform")).not.toBe(original);
  await node.focus(); const moved = await node.getAttribute("transform"); await page.keyboard.press("ArrowDown");
  expect(await node.getAttribute("transform")).not.toBe(moved);
  const final = await node.getAttribute("transform");
  await page.locator("#project").selectOption("other"); await expect(page.locator(".draft-node")).toHaveCount(0);
  await page.locator("#project").selectOption("fixture"); await expect(page.locator(".draft-edge")).toHaveCount(1);
  await page.reload(); await page.locator("#workflow-editor > summary").click();
  await expect(page.locator('.draft-node[data-key="b"]')).toHaveAttribute("transform", final || "");
});

test("editor long IDs fit cards and selection retains keyboard focus", async ({ page }) => {
  const key = "m".repeat(128);
  await add(page, key, "분석해야하는아주긴노드이름을여기에입력합니다", "분석");
  await page.locator("#draft-list button").first().click();
  await expect(page.locator("#draft-list button").first()).toBeFocused();
  await expect(page.getByLabel("노드 ID", { exact: true })).toHaveValue(key);
  const widths = await page.locator(".draft-node text").evaluateAll((nodes) => nodes.map((node) => node instanceof SVGTextElement ? node.getComputedTextLength() : 0));
  expect(Math.max(...widths)).toBeLessThanOrEqual(192);
  await page.setViewportSize({ width: 390, height: 900 });
  const mobileWidths = await page.locator(".draft-node text").evaluateAll((nodes) => nodes.map((node) => node instanceof SVGTextElement ? node.getComputedTextLength() : 0));
  expect(Math.max(...mobileWidths)).toBeLessThanOrEqual(192);
});

test("editor preserves applied drafts and submits a fresh plan without modifying original run", async ({ page }) => {
  await page.locator("#jobs button").first().click(); await expect(page.locator(".graph-node")).toHaveCount(1);
  await page.getByRole("button", { name: "선택한 계획 복사", exact: true }).click();
  await page.getByLabel("노드 이름", { exact: true }).fill("새 이름 <img src=x onerror=alert(1)>");
  await page.getByRole("button", { name: "새 계획 만들기", exact: true }).click();
  await expect(page.locator("#draft-status")).toContainText("설정을 적용"); expect(submissions).toHaveLength(0);
  await page.getByRole("button", { name: "노드 설정 적용", exact: true }).click();
  await expect(page.locator("#workflow-editor img")).toHaveCount(0);
  await expect(page.locator(".graph-node")).toHaveAttribute("aria-label", /기존 노드/);
  rejectSubmit = true;
  await page.getByRole("button", { name: "새 계획 만들기", exact: true }).click();
  await expect(page.locator("#draft-status")).toHaveText("계획 제출 거절");
  await expect(page.getByRole("button", { name: "새 계획 만들기", exact: true })).toBeEnabled();
  rejectSubmit = false;
  await page.getByRole("button", { name: "새 계획 만들기", exact: true }).click();
  await expect.poll(() => submissions.length).toBe(1);
  const payload = submissions[0]; expect(payload.project).toBe("fixture");
  expect(JSON.parse(payload.prompt.slice("/workflow plan ".length)).tasks[0].title).toContain("새 이름");
  expect(sourcePlan.tasks[0].title).toBe("기존 노드");
});

test("editor missing required values, renamed ID references and keyboard port connections", async ({ page }) => {
  await page.getByRole("button", { name: "새 계획 만들기", exact: true }).click(); await expect(page.locator("#draft-status")).toContainText("노드를 하나");
  await add(page, "a", "분석", "분석"); await add(page, "b", "구현");
  await page.getByRole("button", { name: "분석 출력 연결점", exact: true }).focus(); await page.keyboard.press("Enter");
  await page.getByRole("button", { name: "구현 입력 연결점", exact: true }).focus(); await page.keyboard.press("Enter");
  await expect(page.locator(".draft-edge")).toHaveCount(1);
  await page.locator("#draft-list button").first().click(); await page.getByLabel("노드 ID", { exact: true }).fill("analysis");
  await page.getByRole("button", { name: "노드 설정 적용", exact: true }).click();
  await expect(page.getByRole("button", { name: "analysis → b 연결 삭제", exact: true })).toBeVisible();
  await page.getByLabel("워크플로 목표", { exact: true }).fill("실행 확인");
  await page.getByRole("button", { name: "새 계획 만들기", exact: true }).click(); await expect.poll(() => submissions.length).toBe(1);
});

for (const width of [390, 1440]) for (const theme of ["light", "dark"] as const) {
  test(`editor readable layout ${width} ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 }); await page.locator("#theme").selectOption(theme);
    await page.getByLabel("워크플로 목표", { exact: true }).fill("로그인 기능을 구현하고 리뷰하기");
    await add(page, "analysis", "요구사항 분석", "분석"); await add(page, "implementation", "로그인 기능 구현"); await add(page, "review", "변경 결과 리뷰", "리뷰");
    await connect(page, "analysis", "implementation"); await connect(page, "implementation", "review");
    await page.locator("#workflow-editor").scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `${evidence}/screenshots/editor-${width}-${theme}.png`, fullPage: true });
    await page.locator("#draft-submit").scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${evidence}/screenshots/editor-${width}-${theme}-end.png` });
  });
}
