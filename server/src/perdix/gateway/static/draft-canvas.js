import { roleNames } from "./draft-plan.js";

const ns = "http://www.w3.org/2000/svg";
const stateLabels = { pending: "대기", ready: "준비", running: "실행 중", completed: "완료", failed: "실패", blocked: "차단", interrupted: "중단", unknown: "결과 불명", cancelled: "취소" };
const brief = (text, length) => { const characters = Array.from(text); return characters.length > length ? characters.slice(0, length - 1).join("") + "…" : text; };
function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}
export class DraftCanvas {
  constructor(canvas, callbacks) {
    this.canvas = canvas; this.callbacks = callbacks; this.plan = { tasks: [] };
    this.positions = Object.create(null); this.selection = null; this.pending = null;
    this.view = [0, 0, 880, 480]; this.drag = null;
    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const port = event.target.closest("[data-port]");
      if (port) {
        if (port.dataset.port === "output") { this.pending = port.dataset.key; this.connectionDrag = { x: event.clientX, y: event.clientY, moved: false }; this.callbacks.hint("연결할 노드의 왼쪽 입력점을 선택하세요. Escape로 취소합니다."); }
        return;
      }
      const group = event.target.closest(".draft-node"), point = this.point(event);
      if (group) {
        this.callbacks.select(group.dataset.key);
        this.drag = { key: group.dataset.key, origin: point, start: { ...this.positions[group.dataset.key] } };
      } else this.drag = { key: null, origin: { x: event.clientX, y: event.clientY }, view: [...this.view] };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      if (this.connectionDrag && Math.hypot(event.clientX - this.connectionDrag.x, event.clientY - this.connectionDrag.y) > 4) this.connectionDrag.moved = true;
      const point = this.point(event);
      if (this.drag?.key) {
        const { key, origin, start } = this.drag;
        this.positions[key] = { x: Math.max(24, Math.min(5000, start.x + point.x - origin.x)), y: Math.max(24, Math.min(5000, start.y + point.y - origin.y)) };
        this.draw();
      } else if (this.drag) {
        const scale = this.view[2] / this.canvas.getBoundingClientRect().width;
        this.view[0] = this.drag.view[0] - (event.clientX - this.drag.origin.x) * scale;
        this.view[1] = this.drag.view[1] - (event.clientY - this.drag.origin.y) * scale;
        this.applyView();
      } else if (this.pending) this.draw(point);
    });
    canvas.addEventListener("pointerup", (event) => {
      if (this.drag?.key) this.callbacks.move();
      this.drag = null;
      if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
      const port = document.elementFromPoint(event.clientX, event.clientY)?.closest('[data-port="input"]');
      if (this.pending && this.connectionDrag?.moved && port && canvas.contains(port)) this.connect(port.dataset.key);
      this.connectionDrag = null;
    });
    canvas.addEventListener("pointercancel", () => { this.drag = null; this.cancelConnection(); });
    canvas.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); this.cancelConnection(); } });
  }
  point(event) {
    const matrix = this.canvas.getScreenCTM();
    return matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : { x: 0, y: 0 };
  }
  cancelConnection() { this.pending = null; this.connectionDrag = null; this.draw(); this.callbacks.hint("연결을 취소했습니다."); }
  connect(to) {
    const from = this.pending; this.pending = null;
    if (from) this.callbacks.connect(from, to);
    this.draw();
  }
  update(plan, positions, selection, states = this.states || {}) {
    this.plan = plan; this.positions = positions; this.selection = selection; this.states = states;
    // 위치가 없는 노드는 의존성 깊이별 열에 배치한다(왼쪽 선행 → 오른쪽 후행).
    const levels = new Map(), byKey = new Map(plan.tasks.map((task) => [task.taskId, task]));
    const level = (task, seen = new Set()) => {
      if (levels.has(task.taskId)) return levels.get(task.taskId);
      if (seen.has(task.taskId)) return 0;
      const next = new Set(seen).add(task.taskId), parents = (task.dependencies || []).map((key) => byKey.get(key)).filter(Boolean);
      const value = parents.length ? Math.max(...parents.map((parent) => level(parent, next))) + 1 : 0;
      levels.set(task.taskId, value); return value;
    };
    const rows = new Map();
    for (const task of plan.tasks) {
      const column = level(task), row = rows.get(column) || 0; rows.set(column, row + 1);
      if (!positions[task.taskId]) positions[task.taskId] = { x: 48 + column * 296, y: 48 + row * 136 };
    }
    if (this.pending && !plan.tasks.some((task) => task.taskId === this.pending)) this.pending = null;
    this.draw();
  }
  path(from, to) {
    const x = from.x + 224, y = from.y + 44;
    return `M${x},${y} C${x + 48},${y} ${to.x - 48},${to.y + 44} ${to.x},${to.y + 44}`;
  }
  draw(pointer) {
    const canvas = this.canvas, focused = document.activeElement;
    const focusKey = canvas.contains(focused) ? focused?.getAttribute("data-focus-key") : null;
    canvas.replaceChildren();
    const defs = svg("defs"), marker = svg("marker", { id: "draft-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto" });
    marker.append(svg("path", { d: "M0 0 L10 5 L0 10 z", class: "graph-arrow" })); defs.append(marker); canvas.append(defs);
    for (const task of this.plan.tasks) for (const dependency of task.dependencies || []) {
      const from = this.positions[dependency], to = this.positions[task.taskId];
      if (from && to) canvas.append(svg("path", { d: this.path(from, to), class: "graph-edge draft-edge", "marker-end": "url(#draft-arrow)" }));
    }
    if (this.pending && pointer) {
      const from = this.positions[this.pending];
      canvas.append(svg("path", { d: this.path(from, { x: pointer.x, y: pointer.y - 44 }), class: "graph-edge draft-connecting" }));
    }
    for (const task of this.plan.tasks) {
      const key = task.taskId, point = this.positions[key];
      const state = this.states?.[key];
      const group = svg("g", { class: `draft-node role-${task.role}`, ...(state ? { "data-state": state } : {}), transform: `translate(${point.x} ${point.y})`, "data-key": key, "aria-pressed": String(this.selection === key), "aria-label": `${task.title} 노드`, "data-focus-key": key, role: "button", tabindex: 0 });
      group.append(svg("rect", { width: 224, height: 88, rx: 8 }), svg("title", {}, `${task.title}\n${key}`), svg("text", { x: 18, y: 31, class: "node-title" }, brief(task.title, 15)), svg("text", { x: 18, y: 54, class: "node-meta" }, brief(task.acceptance?.[0] || "해야 할 일을 입력하세요", 18)), svg("text", { x: 18, y: 75, class: "node-meta node-role" }, roleNames[task.role]), svg("rect", { class: "node-role-bar", width: 6, height: 88, rx: 3 }));
      if (state) group.append(svg("text", { x: 208, y: 24, class: "node-state", "text-anchor": "end" }, stateLabels[state] || state));
      group.addEventListener("click", (event) => { if (!event.target.closest("[data-port]")) this.callbacks.select(key); });
      group.addEventListener("keydown", (event) => {
        if (event.target !== group) return;
        if (["Enter", " "].includes(event.key)) { event.preventDefault(); this.callbacks.select(key); }
        const directions = { ArrowLeft: [-24, 0], ArrowRight: [24, 0], ArrowUp: [0, -24], ArrowDown: [0, 24] };
        if (directions[event.key]) {
          event.preventDefault(); const [dx, dy] = directions[event.key];
          point.x = Math.max(24, Math.min(5000, point.x + dx)); point.y = Math.max(24, Math.min(5000, point.y + dy));
          this.draw(); this.callbacks.move();
        }
      });
      for (const [port, x, name] of [["input", 0, "입력"], ["output", 224, "출력"]]) {
        const handle = svg("g", { class: "draft-port", "data-port": port, "data-key": key, "data-focus-key": `${key}-${port}`, "aria-label": `${task.title} ${name} 연결점`, role: "button", tabindex: 0 });
        handle.append(svg("circle", { cx: x, cy: 44, r: 22, class: "draft-port-target" }), svg("circle", { cx: x, cy: 44, r: 8, class: "draft-port-dot" }));
        const activate = () => {
          if (port === "output") { this.pending = key; this.callbacks.hint("도착 노드의 입력 연결점을 선택하세요."); }
          else if (this.pending) this.connect(key);
          else this.callbacks.hint("먼저 출발 노드의 출력 연결점을 선택하세요.");
        };
        handle.addEventListener("click", (event) => { event.stopPropagation(); activate(); });
        handle.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); event.stopPropagation(); activate(); } });
        group.append(handle);
      }
      const add = svg("g", { class: "draft-add", role: "button", tabindex: 0, "aria-label": `${task.title} 다음에 단계 추가`, "data-focus-key": `${key}-add` });
      add.append(svg("title", {}, "다음 단계 추가"), svg("circle", { cx: 250, cy: 44, r: 15 }), svg("text", { x: 250, y: 50, "text-anchor": "middle" }, "+"));
      const addAfter = (event) => { event.stopPropagation(); this.callbacks.addAfter?.(key); };
      add.addEventListener("click", addAfter);
      add.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); addAfter(event); } });
      group.append(add);
      canvas.append(group);
    }
    this.applyView();
    if (focusKey) [...canvas.querySelectorAll("[data-focus-key]")].find((node) => node.dataset.focusKey === focusKey)?.focus({ preventScroll: true });
  }
  fit() {
    const points = this.plan.tasks.map((task) => this.positions[task.taskId]);
    if (!points.length) { this.view = [0, 0, 880, 480]; this.applyView(); return; }
    const pad = 40, minX = Math.min(...points.map((p) => p.x)) - pad, minY = Math.min(...points.map((p) => p.y)) - pad;
    const boundsW = Math.max(...points.map((p) => p.x)) + 224 + pad - minX, boundsH = Math.max(...points.map((p) => p.y)) + 88 + pad - minY;
    const rect = this.canvas.getBoundingClientRect(), cw = rect.width || 600, ch = rect.height || 480;
    // 캔버스 비율에 맞춰 중앙 정렬하고, 노드가 적을 때 과하게 커지지 않도록 확대를 제한한다.
    const scale = Math.min(cw / boundsW, ch / boundsH, 1.25), w = cw / scale, h = ch / scale;
    this.view = [minX - (w - boundsW) / 2, minY - (h - boundsH) / 2, w, h]; this.applyView();
  }
  zoom(factor) { const [x, y, w, h] = this.view, next = Math.max(200, Math.min(10000, w * factor)); this.view = [x + (w - next) / 2, y + (h - h * next / w) / 2, next, h * next / w]; this.applyView(); }
  applyView() { this.canvas.setAttribute("viewBox", this.view.join(" ")); }
}
