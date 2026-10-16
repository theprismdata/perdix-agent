import { roleNames } from "./draft-plan.js";

const ns = "http://www.w3.org/2000/svg";
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
  update(plan, positions, selection) {
    this.plan = plan; this.positions = positions; this.selection = selection;
    plan.tasks.forEach((task, index) => {
      if (!positions[task.taskId]) positions[task.taskId] = { x: 48 + (index % 3) * 296, y: 48 + Math.floor(index / 3) * 144 };
    });
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
      const group = svg("g", { class: "draft-node", transform: `translate(${point.x} ${point.y})`, "data-key": key, "aria-pressed": String(this.selection === key), "aria-label": `${task.title} 노드`, "data-focus-key": key, role: "button", tabindex: 0 });
      group.append(svg("rect", { width: 224, height: 88, rx: 8 }), svg("title", {}, `${task.title}\n${key}`), svg("text", { x: 16, y: 28 }, brief(task.title, 12)), svg("text", { x: 16, y: 53, class: "node-meta" }, brief(`${roleNames[task.role]} · ${task.model || "모델 설정 필요"}`, 12)), svg("text", { x: 16, y: 74, class: "node-meta" }, brief(key, 14)));
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
      canvas.append(group);
    }
    this.applyView();
    if (focusKey) [...canvas.querySelectorAll("[data-focus-key]")].find((node) => node.dataset.focusKey === focusKey)?.focus({ preventScroll: true });
  }
  fit() {
    const points = this.plan.tasks.map((task) => this.positions[task.taskId]);
    this.view = [0, 0, Math.max(880, ...points.map((point) => point.x + 272)), Math.max(480, ...points.map((point) => point.y + 136))]; this.applyView();
  }
  zoom(factor) { const [x, y, w, h] = this.view, next = Math.max(200, Math.min(10000, w * factor)); this.view = [x + (w - next) / 2, y + (h - h * next / w) / 2, next, h * next / w]; this.applyView(); }
  applyView() { this.canvas.setAttribute("viewBox", this.view.join(" ")); }
}
