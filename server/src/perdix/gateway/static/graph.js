const id = (name) => document.getElementById(name);
const ns = "http://www.w3.org/2000/svg";
const states = { pending: "대기", ready: "준비", running: "실행 중", completed: "완료", failed: "실패", blocked: "진행 차단", interrupted: "중단", unknown: "결과 불명", cancelled: "취소", unverified: "미검증", passed: "통과", stale: "재검증 필요", awaiting: "수락 대기", accepted: "수락됨", rejected: "거절됨" };
const label = (value) => states[value] || value || "미지정";
function html(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = text;
  return node;
}

export class WorkflowGraph {
  constructor(api) {
    this.api = api;
    this.job = null; this.project = ""; this.epoch = 0; this.seq = 0;
    this.snapshot = null; this.selection = null; this.mode = "workflow";
    this.nodes = []; this.rows = new Map(); this.view = [0, 0, 800, 360];
    id("graph-mode").addEventListener("change", () => {
      this.mode = id("graph-mode").value; this.clear();
      id("team-picker").hidden = this.mode !== "team";
      void this.refresh();
    });
    id("graph-team").addEventListener("change", () => { this.clear(); void this.refresh(); });
    document.querySelectorAll("[data-workflow-action]").forEach((button) => button.addEventListener("click", async () => {
      const action = button.dataset.workflowAction;
      const job = this.job, epoch = this.epoch;
      if (!job || this.mode !== "workflow") return;
      if (action === "accept" && !window.confirm("검증된 변경을 원본 브랜치에 반영하시겠습니까?")) return;
      const controls = [...document.querySelectorAll("[data-workflow-action]")];
      controls.forEach((control) => { control.disabled = true; });
      try {
        await this.api(`/api/jobs/${encodeURIComponent(job)}/workflow/control`, { action, ...(action === "retry" ? { taskId: this.selection } : {}) });
        if (epoch !== this.epoch) return;
        id("workflow-control-status").textContent = "요청을 접수했습니다. 실행 상태에서 결과를 확인하세요.";
        this.snapshot = null; await this.refresh();
      } catch (error) {
        if (epoch === this.epoch) id("workflow-control-status").textContent = error.message;
      } finally { controls.forEach((control) => { control.disabled = false; }); }
    }));
    id("graph-plus").addEventListener("click", () => this.zoom(.8));
    id("graph-minus").addEventListener("click", () => this.zoom(1.25));
    id("graph-fit").addEventListener("click", () => this.fit());
    document.querySelectorAll("[data-pan]").forEach((button) => button.addEventListener("click", () => {
      const direction = button.dataset.pan;
      this.view[direction === "left" || direction === "right" ? 0 : 1] += (direction === "left" || direction === "up" ? -1 : 1) * this.view[2] / 8;
      this.applyView();
    }));
    const canvas = id("task-graph"); let drag = null;
    canvas.addEventListener("pointerdown", (event) => {
      if (event.target.closest(".graph-node") || event.pointerType === "touch") return;
      drag = [event.clientX, event.clientY, ...this.view]; canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const scale = this.view[2] / canvas.getBoundingClientRect().width;
      this.view[0] = drag[2] - (event.clientX - drag[0]) * scale;
      this.view[1] = drag[3] - (event.clientY - drag[1]) * scale; this.applyView();
    });
    canvas.addEventListener("pointerup", () => { drag = null; });
    canvas.addEventListener("pointercancel", () => { drag = null; });
  }
  status(text, error = false) { id("graph-status").textContent = text; id("graph-status").className = error ? "error" : "muted"; }
  clear() {
    this.epoch++; this.seq = 0; this.snapshot = null; this.selection = null;
    id("workflow-control-status").textContent = "";
    this.rows.clear(); id("graph-list").replaceChildren(); id("task-graph").replaceChildren();
    id("graph-body").hidden = true;
  }
  reset() { this.clear(); this.job = null; this.project = ""; id("graph-team").replaceChildren(); this.status("목록에서 작업을 선택하세요."); }
  selectJob(job) { this.job = job; if (this.mode === "workflow") { this.clear(); this.status("작업 계획을 불러오는 중…"); } }
  setProject(project) { this.project = project; id("graph-team").replaceChildren(); if (this.mode === "team") { this.clear(); void this.refresh(); } }
  async refresh() {
    const epoch = this.epoch;
    try {
      if (this.mode === "team") { await this.refreshTeam(epoch); return; }
      if (!this.job) { this.status("목록에서 작업을 선택하세요."); return; }
      const base = `/api/jobs/${encodeURIComponent(this.job)}/workflow`;
      if (this.snapshot) {
        const delta = await this.api(`${base}/events?after=${this.seq}`);
        if (epoch !== this.epoch) return;
        const fresh = delta.events.filter((event) => event.seq > this.seq);
        if (!fresh.length && delta.seq <= this.seq) { this.status("최신 상태입니다."); return; }
        if (fresh.length && fresh[0].seq !== this.seq + 1) this.status("누락된 기록을 다시 불러오는 중…");
      }
      const snapshot = await this.api(base);
      if (epoch !== this.epoch) return;
      if (!Number.isInteger(snapshot.seq) || !Array.isArray(snapshot.plan?.tasks)) throw new Error("작업 계획 형식을 확인할 수 없습니다.");
      if (snapshot.seq < this.seq) throw new Error("최신 기록을 기다리고 있습니다.");
      this.snapshot = snapshot; this.seq = snapshot.seq;
      this.renderWorkflow(); if (this.nodes.length) this.status("최신 상태입니다.");
    } catch (error) {
      if (epoch !== this.epoch) return;
      if (error.status === 404) { this.snapshot = null; this.status(this.mode === "workflow" ? "이 작업에는 워크플로 계획이 없습니다. /workflow plan 명령으로 계획을 만드세요." : "조회할 팀이 없습니다."); id("graph-body").hidden = true; }
      else this.status(`${error.message} 자동으로 다시 연결합니다.`, true);
    }
  }
  async refreshTeam(epoch) {
    if (!this.project) return;
    const query = `?project=${encodeURIComponent(this.project)}`;
    if (!id("graph-team").options.length) {
      const data = await this.api(`/api/teams${query}`);
      if (epoch !== this.epoch) return;
      id("graph-team").replaceChildren(...data.teams.map((team) => { const option = html("option", team.name); option.value = team.id; return option; }));
    }
    const team = id("graph-team").value;
    if (!team) { this.status("등록된 수동 팀이 없습니다."); return; }
    const snapshot = await this.api(`/api/teams/${encodeURIComponent(team)}${query}`);
    if (epoch !== this.epoch) return;
    if (this.snapshot && this.seq === snapshot.seq) { this.status("최신 상태입니다."); return; }
    this.snapshot = snapshot; this.seq = snapshot.seq;
    const members = snapshot.members.map((member) => ({ key: `member:${member.name}`, title: member.name, role: member.role, state: member.active ? "활성" : "비활성", dependencies: [] }));
    const tasks = snapshot.tasks.map((task) => ({ key: task.id, title: task.body, role: task.owner, state: task.state, paths: task.paths, result: task.result, dependencies: members.some((member) => member.key === `member:${task.owner}`) ? [`member:${task.owner}`] : [] }));
    this.nodes = [...members, ...tasks];
    id("graph-summary").textContent = `${snapshot.name} · 읽기 전용`;
    id("graph-legend").textContent = "점선은 팀원 → 배정된 작업입니다. 작업 간 의존성이나 검증 통과를 뜻하지 않습니다.";
    id("workflow-controls").hidden = true;
    id("graph-log-link").hidden = true;
    this.render(); this.status("최신 상태입니다.");
  }
  renderWorkflow() {
    const { run, plan, attempts = [] } = this.snapshot;
    this.nodes = plan.tasks.map((task) => {
      const history = attempts.filter((attempt) => attempt.taskId === task.taskId);
      const latest = history.at(-1);
      return { ...task, key: task.taskId, state: latest?.status || "pending", history, role: task.role, dependencies: task.dependencies || [] };
    });
    id("graph-summary").textContent = `${run.runId} · 실행: ${label(run.status)} · 검증: ${label(run.verificationStatus)} · 수락: ${label(run.acceptanceStatus)}${run.failureReason ? ` · 사유: ${run.failureReason}` : ""}`;
    id("graph-legend").textContent = "화살표는 선행 작업 → 후행 작업을 뜻합니다. 담당자는 작업 속성입니다.";
    id("workflow-controls").hidden = false;
    id("graph-log-link").hidden = false;
    this.render();
  }
  render() {
    id("graph-body").hidden = !this.nodes.length;
    if (!this.nodes.length) { this.status("계획에 작업이 없습니다."); return; }
    const focused = document.activeElement?.getAttribute("data-node");
    const canvas = id("task-graph"); canvas.replaceChildren();
    const defs = svg("defs"), marker = svg("marker", { id: "graph-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
    marker.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "graph-arrow" })); defs.append(marker); canvas.append(defs);
    const levels = new Map();
    const level = (node, visiting = new Set()) => {
      if (levels.has(node.key)) return levels.get(node.key);
      if (visiting.has(node.key)) return 0;
      const next = new Set(visiting).add(node.key);
      const dependencies = node.dependencies.map((key) => this.nodes.find((item) => item.key === key)).filter(Boolean);
      const value = dependencies.length ? Math.max(...dependencies.map((item) => level(item, next))) + 1 : 0;
      levels.set(node.key, value); return value;
    };
    const columns = new Map();
    for (const node of this.nodes) {
      const col = level(node), row = columns.get(col) || 0; columns.set(col, row + 1);
      node.x = 24 + col * 296; node.y = 24 + row * 120;
    }
    for (const node of this.nodes) for (const key of node.dependencies) {
      const from = this.nodes.find((item) => item.key === key); if (!from) continue;
      const x = from.x + 224, y = from.y + 44, endX = node.x, endY = node.y + 44;
      canvas.append(svg("path", { class: `graph-edge${this.mode === "team" ? " assignment" : ""}`, d: `M${x},${y} C${x + 36},${y} ${endX - 36},${endY} ${endX},${endY}`, "marker-end": "url(#graph-arrow)" }));
    }
    const keys = new Set(this.nodes.map((node) => node.key));
    for (const [key, row] of this.rows) if (!keys.has(key)) { row.remove(); this.rows.delete(key); }
    for (const node of this.nodes) {
      const group = svg("g", { class: "graph-node", transform: `translate(${node.x} ${node.y})`, role: "button", tabindex: 0, "data-node": node.key, "aria-label": `${node.title}, ${label(node.state)}, 담당 ${node.role}` });
      group.append(svg("rect", { width: 224, height: 88, rx: 8 }), svg("title", {}, node.title), svg("text", { x: 12, y: 27 }, node.title.length > 19 ? `${node.title.slice(0, 18)}…` : node.title), svg("text", { x: 12, y: 53, class: "node-meta" }, `${label(node.state)} · ${node.role}`.slice(0, 27)), svg("text", { x: 12, y: 75, class: "node-meta" }, node.history ? `시도 ${node.history.length}회` : "수동 팀"));
      group.addEventListener("click", () => this.select(node.key));
      group.addEventListener("keydown", (event) => {
        if (["Enter", " "].includes(event.key)) { event.preventDefault(); this.select(node.key); }
        if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
          event.preventDefault(); const groups = [...canvas.querySelectorAll(".graph-node")], index = groups.indexOf(group);
          groups[(index + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? groups.length - 1 : 1)) % groups.length].focus();
        }
      }); canvas.append(group);
      let row = this.rows.get(node.key);
      if (!row) { row = html("button", undefined, "job-row"); row.type = "button"; row.addEventListener("click", () => this.select(node.key)); this.rows.set(node.key, row); id("graph-list").append(row); }
      row.textContent = `${node.title} · ${label(node.state)} · ${node.role}`;
    }
    if (!this.selection || !keys.has(this.selection)) this.fit();
    this.select(keys.has(this.selection) ? this.selection : this.nodes[0].key);
    if (focused) [...canvas.querySelectorAll(".graph-node")].find((node) => node.getAttribute("data-node") === focused)?.focus({ preventScroll: true });
  }
  select(key) {
    this.selection = key; const node = this.nodes.find((item) => item.key === key); if (!node) return;
    for (const [id, row] of this.rows) row.setAttribute("aria-pressed", String(id === key));
    for (const group of id("task-graph").querySelectorAll(".graph-node")) group.setAttribute("aria-pressed", String(group.getAttribute("data-node") === key));
    id("graph-selection-title").textContent = node.title;
    const body = id("graph-selection-body"); body.replaceChildren();
    const line = (title, value) => body.append(html("p", `${title}: ${value || "없음"}`, "small"));
    line("상태", label(node.state)); line("담당", node.role); line("모델", node.model);
    line("파일 범위", (node.paths || []).join(", ")); line(this.mode === "team" ? "배정 관계" : "선행 작업", node.dependencies.join(", "));
    if (node.acceptance) line("수락 조건", node.acceptance.map((item) => typeof item === "string" ? item : JSON.stringify(item)).join("\n"));
    for (const attempt of node.history || []) {
      line(`시도 ${attempt.attemptId}`, `${label(attempt.status)} · 세션 ${attempt.sessionId || "미지정"}`);
      if (attempt.failureReason) line("실패 이유", attempt.failureReason);
      if (attempt.candidateCommit) line("변경 커밋", attempt.candidateCommit);
      for (const artifact of attempt.artifacts || []) line("산출물", typeof artifact === "string" ? artifact : `${artifact.path} · ${artifact.kind}${artifact.description ? ` · ${artifact.description}` : ""}`);
    }
    if (node.result) line("결과", node.result);
    if (this.mode === "workflow") {
      const receipt = this.snapshot.verification;
      line("검증", receipt ? label(receipt.status) : "아직 검증하지 않았습니다.");
      if (receipt) {
        if (receipt.candidateCommit) line("검증 커밋", receipt.candidateCommit);
        for (const command of receipt.commands || []) {
          line("검증 명령", `${command.command} · 종료 코드 ${command.exitCode ?? "미실행"}${command.timedOut ? " · 시간 초과" : ""}${command.cancelled ? " · 취소됨" : ""}`);
          if (command.stdout) line("표준 출력", command.stdout);
          if (command.stderr) line("오류 출력", command.stderr);
        }
        for (const artifact of receipt.artifacts || []) line("검증 로그", artifact.path);
      }
    }
  }
  fit() {
    if (!this.nodes.length) return;
    this.view = [0, 0, Math.max(...this.nodes.map((node) => node.x)) + 248, Math.max(...this.nodes.map((node) => node.y)) + 112]; this.applyView();
  }
  zoom(factor) { const [, , width, height] = this.view; const next = Math.min(10000, Math.max(120, width * factor)); const ratio = next / width; this.view = [this.view[0] + (width - next) / 2, this.view[1] + height * (1 - ratio) / 2, next, height * ratio]; this.applyView(); }
  applyView() { id("task-graph").setAttribute("viewBox", this.view.join(" ")); }
}
