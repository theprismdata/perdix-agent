import { DraftCanvas } from "./draft-canvas.js";
import { addDependency, removeTask, replaceTask, newTask, planErrors, restoreDraft, roleNames } from "./draft-plan.js";

const id = (name) => document.getElementById(name);
const lines = (value) => value.split("\n").map((line) => line.trim()).filter(Boolean);
function element(tag, text) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; return node; }
const fields = ["taskId", "title", "role", "model", "paths", "acceptance", "artifactRequirements"];
export class WorkflowEditor {
  constructor(api, submitted, selectedPlan, statesOf = () => ({})) {
    this.api = api; this.submitted = submitted; this.selectedPlan = selectedPlan; this.statesOf = statesOf;
    this.project = ""; this.defaultModel = ""; this.epoch = 0; this.busy = false;
    this.sessions = new Map(); this.blank();
    this.dirty = false;
    this.canvas = new DraftCanvas(id("draft-graph"), { select: (key) => this.select(key), connect: (from, to) => this.connect(from, to), move: () => this.persist(false), hint: (text) => this.message(text), addAfter: (key) => this.addAfter(key) });
    id("manual-start").addEventListener("click", () => { this.add("analyze"); id("workflow-editor").scrollIntoView({ block: "nearest" }); });
    document.querySelectorAll("[data-add-role]").forEach((button) => button.addEventListener("click", () => this.add(button.dataset.addRole)));
    id("draft-new").addEventListener("click", () => {
      if (this.plan.tasks.length && !window.confirm("현재 초안을 비우고 새로 만드시겠습니까?")) return;
      this.blank(); this.render(); this.persist(); this.message("목표를 입력하고 노드를 추가하세요.");
    });
    id("draft-copy").addEventListener("click", () => this.copy());
    id("draft-objective").addEventListener("input", () => { this.plan.objective = id("draft-objective").value; this.persist(); });
    id("draft-node-form").addEventListener("input", () => { if (this.selection) this.pendingForms.set(this.selection, this.readForm()); });
    id("draft-node-form").addEventListener("change", () => this.flush());
    id("draft-node-form").addEventListener("submit", (event) => { event.preventDefault(); this.apply(); });
    id("draft-delete-node").addEventListener("click", () => {
      if (!this.selection) return;
      removeTask(this.plan, this.selection); this.pendingForms.delete(this.selection); delete this.positions[this.selection];
      this.selection = this.plan.tasks[0]?.taskId || null; this.render(); this.persist(); this.message("노드와 연결을 삭제했습니다.");
    });
    id("draft-connect").addEventListener("click", () => this.connect(id("draft-from").value, id("draft-to").value));
    id("draft-fit").addEventListener("click", () => this.canvas.fit());
    new ResizeObserver(() => { if (!this.canvas.drag) this.canvas.fit(); }).observe(id("draft-graph").parentElement);
    id("draft-zoom-in").addEventListener("click", () => this.canvas.zoom(.8));
    id("draft-zoom-out").addEventListener("click", () => this.canvas.zoom(1.25));
    id("draft-submit").addEventListener("click", () => { void this.submit(); });
  }
  blank() { this.plan = { objective: "", tasks: [] }; this.positions = Object.create(null); this.selection = null; this.pendingForms = new Map(); }
  message(text, error = false) { id("draft-status").textContent = text; id("draft-status").className = error ? "small error" : "small muted"; }
  // 새 요청이 시작되면 이전 초안을 비운다(새 AI 계획이 대신 채워진다).
  clear() { this.blank(); this.render(); this.persist(false); }
  reset() { this.epoch++; this.project = ""; this.sessions.clear(); this.blank(); this.render(); }
  setProject(project, model = this.defaultModel) {
    this.defaultModel = model;
    if (this.project === project) return;
    if (this.project) this.sessions.set(this.project, { plan: this.plan, positions: this.positions, selection: this.selection, pendingForms: this.pendingForms });
    this.project = project; this.epoch++; this.blank();
    const cached = this.sessions.get(project);
    if (cached) Object.assign(this, cached);
    else {
      try {
        const raw = sessionStorage.getItem(`perdixWorkflowDraft:${project}`);
        if (raw) { Object.assign(this, restoreDraft(raw)); this.selection = this.plan.tasks[0]?.taskId || null; this.message("이 프로젝트의 저장된 초안을 복원했습니다."); }
        else this.message("노드를 추가하거나 선택한 계획을 복사하세요.");
      } catch { this.message("초안을 복원하지 못했습니다. 새 초안으로 시작합니다.", true); }
    }
    this.render(); this.canvas.fit();
  }
  persist(markDirty = true) {
    if (markDirty) this.dirty = true;
    if (!this.project) return;
    try { sessionStorage.setItem(`perdixWorkflowDraft:${this.project}`, JSON.stringify({ version: 1, plan: this.plan, positions: this.positions })); }
    catch { this.message("탭에 초안을 저장하지 못했습니다. 화면을 닫기 전에 계획을 제출하세요.", true); }
  }
  add(role) {
    if (this.busy) return;
    if (this.plan.tasks.length >= 20) { this.message("노드는 최대 20개까지 추가할 수 있습니다.", true); return; }
    const task = newTask(this.plan, role, this.defaultModel);
    this.plan.tasks.push(task); this.selection = task.taskId; this.render(); this.canvas.fit(); this.persist();
    this.message(`${roleNames[role]} 노드를 추가했습니다. 완료 조건을 설정하세요.`);
    id("draft-title").focus();
  }
  copy() {
    const source = this.selectedPlan();
    if (!source) { this.message("먼저 작업 목록에서 워크플로 계획을 선택하세요.", true); return; }
    this.loadPlan(source, "계획을 초안으로 복사했습니다. 기존 실행은 변경되지 않습니다.");
  }
  // AI가 만든 계획이나 선택한 계획을 초안으로 불러온다. 기존 실행 기록은 바꾸지 않는다.
  loadPlan(source, text, force = false) {
    if (!force && this.plan.tasks.length && !window.confirm("현재 초안을 선택한 계획으로 바꾸시겠습니까? 실행 기록은 바뀌지 않습니다.")) return false;
    this.blank(); this.plan = structuredClone(source); this.selection = this.plan.tasks[0]?.taskId || null;
    this.render(); this.canvas.fit(); this.persist(); this.dirty = false; this.message(text);
    return true;
  }
  // 실행 상태만 바뀐 경우 노드 배치와 편집 중인 입력은 건드리지 않고 상태 표시만 갱신한다.
  refreshStates() { this.canvas.update(this.plan, this.positions, this.selection, this.statesOf()); }
  readForm() { return Object.fromEntries(fields.map((key) => [key, id(`draft-${key}`).value])); }
  select(key) { this.selection = key; this.render(); }
  // 입력칸을 벗어나면 자동으로 반영한다. 아직 완성되지 않은 입력은 조용히 보류한다.
  flush() { if (this.pendingForms.has(this.selection) && id("draft-node-form").checkValidity()) this.apply(); return !this.pendingForms.size; }
  addAfter(key) {
    if (this.busy) return;
    if (this.plan.tasks.length >= 20) { this.message("단계는 최대 20개까지 추가할 수 있습니다.", true); return; }
    if (!this.flush()) { this.message("수정 중인 단계의 빈 칸을 먼저 채워 주세요.", true); return; }
    const task = newTask(this.plan, "implement", this.defaultModel);
    task.title = "새 단계"; task.acceptance = ["여기에 해야 할 일을 적으세요"]; task.dependencies = [key];
    this.plan.tasks.push(task); this.selection = task.taskId; this.render(); this.canvas.fit(); this.persist();
    this.message("새 단계를 추가했습니다. 오른쪽에서 이름과 할 일을 고쳐 주세요."); id("draft-title").select();
  }
  apply() {
    if (!this.selection || !id("draft-node-form").reportValidity()) return;
    const values = this.readForm(), previous = this.selection;
    const task = { ...values, taskId: values.taskId.trim(), title: values.title.trim(), model: values.model.trim(), paths: lines(values.paths), acceptance: lines(values.acceptance), artifactRequirements: lines(values.artifactRequirements) };
    try {
      replaceTask(this.plan, previous, task);
      if (previous !== task.taskId) { this.positions[task.taskId] = this.positions[previous]; delete this.positions[previous]; }
      this.pendingForms.delete(previous); this.selection = task.taskId;
      this.render(); this.persist(); this.message("변경을 저장했습니다. 실행을 누르면 이 내용으로 진행합니다.");
    } catch (error) { this.message(error.message, true); }
  }
  connect(from, to) {
    if (this.busy) return;
    try { addDependency(this.plan, from, to); this.render(); this.persist(); this.message(`${from} → ${to} 연결을 추가했습니다.`); }
    catch (error) { this.message(error.message, true); }
  }
  render() {
    const focusedTask = document.activeElement?.getAttribute("data-draft-task");
    id("draft-objective").value = this.plan.objective;
    id("draft-count").textContent = `${this.plan.tasks.length}/20 노드 · ${this.project || "프로젝트 미선택"}`;
    id("draft-empty").hidden = this.plan.tasks.length > 0;
    // 계획(단계)이 생기기 전에는 캔버스·설정·실행 영역을 숨긴다.
    id("workflow-editor").hidden = this.plan.tasks.length === 0;
    this.canvas.update(this.plan, this.positions, this.selection, this.statesOf());
    const list = id("draft-list"); list.replaceChildren();
    for (const task of this.plan.tasks) {
      const button = element("button", `${task.title} · ${roleNames[task.role]}`); button.type = "button"; button.className = "job-row";
      button.setAttribute("data-draft-task", task.taskId);
      button.setAttribute("aria-pressed", String(task.taskId === this.selection)); button.addEventListener("click", () => this.select(task.taskId)); list.append(button);
    }
    for (const name of ["from", "to"]) {
      const select = id(`draft-${name}`), previous = select.value; select.replaceChildren();
      for (const task of this.plan.tasks) { const option = element("option", `${task.title} (${task.taskId})`); option.value = task.taskId; select.append(option); }
      if (this.plan.tasks.some((task) => task.taskId === previous)) select.value = previous;
      else if (name === "to" && this.plan.tasks[1]) select.value = this.plan.tasks[1].taskId;
    }
    const edges = id("draft-edges"); edges.replaceChildren();
    for (const task of this.plan.tasks) for (const from of task.dependencies || []) {
      const row = element("div"); row.className = "draft-edge-row"; row.append(element("span", `${from} → ${task.taskId}`));
      const button = element("button", "연결 삭제"); button.type = "button"; button.className = "danger"; button.setAttribute("aria-label", `${from} → ${task.taskId} 연결 삭제`);
      button.addEventListener("click", () => { task.dependencies = task.dependencies.filter((key) => key !== from); this.render(); this.persist(); this.message("연결을 삭제했습니다."); }); row.append(button); edges.append(row);
    }
    const task = this.plan.tasks.find((item) => item.taskId === this.selection);
    id("draft-node-form").hidden = !task; id("draft-inspector-empty").hidden = Boolean(task);
    if (task) {
      const values = this.pendingForms.get(task.taskId) || task;
      for (const field of fields) id(`draft-${field}`).value = Array.isArray(values[field]) ? values[field].join("\n") : values[field] || "";
    }
    if (focusedTask) [...list.querySelectorAll("button")].find((button) => button.getAttribute("data-draft-task") === focusedTask)?.focus({ preventScroll: true });
  }
  async submit() {
    if (this.busy) return null;
    if (!this.flush()) { this.message("수정 중인 단계의 빈 칸을 먼저 채워 주세요.", true); return null; }
    if (!this.plan.objective.trim() && this.plan.tasks[0]) this.plan.objective = this.plan.tasks[0].title;
    const errors = planErrors(this.plan);
    if (errors.length || !this.project) { this.message(errors.join("\n") || "프로젝트를 선택하세요.", true); return null; }
    const epoch = this.epoch, project = this.project;
    this.busy = true; id("draft-submit").disabled = true; id("draft-submit").textContent = "계획 제출 중…";
    try {
      const job = await this.api("/api/jobs", { project, prompt: "/workflow plan " + JSON.stringify(this.plan) });
      if (epoch !== this.epoch) return null;
      this.dirty = false; await this.submitted(job); this.message("수정한 계획을 저장했습니다.");
      return job;
    } catch (error) { if (epoch === this.epoch) this.message(error.message, true); return null; }
    finally { this.busy = false; id("draft-submit").disabled = false; id("draft-submit").textContent = "새 계획 만들기"; }
  }
}
