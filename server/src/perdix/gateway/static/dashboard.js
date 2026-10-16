import { WorkflowGraph } from "./graph.js";
import { WorkflowEditor } from "./workflow-editor.js";
const byId = (id) => document.getElementById(id);
const labels = { queued: "대기 중", running: "실행 중", waiting: "승인 대기", completed: "완료", failed: "실패", cancelled: "취소됨", interrupted: "중단됨" };
const sourceLabels = { web: "웹", telegram: "Telegram", github: "GitHub" };
let token = sessionStorage.getItem("perdixGatewayToken") || "";
let selected = null;
let cursor = 0;
let jobs = [];
let refreshing = false;
let connected = false;
let networkFailed = false;
let channelStatus = null;
let generation = 0;
const rows = new Map();
const approvals = new Map();
const permissionEvents = new Map();
const workflowGraph = new WorkflowGraph(api);
const workflowEditor = new WorkflowEditor(api, async (job) => {
  jobs = [job, ...jobs.filter((item) => item.id !== job.id)];
  await selectJob(job.id); notice("새 워크플로 계획을 접수했습니다.");
}, () => workflowGraph.mode === "workflow" ? workflowGraph.snapshot?.plan : null);

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function notice(message, error = false) {
  byId("notice").textContent = message;
  byId("notice").className = error ? "error" : "";
}
async function api(path, body) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) }, ...(body === undefined ? {} : { method: "POST", body: JSON.stringify(body) }) });
  if (response.status === 401) disconnect();
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const error = new Error(typeof data.detail === "string" ? data.detail : "요청에 실패했습니다. 입력을 확인하고 다시 시도하세요.");
    error.status = response.status;
    throw error;
  }
  return response.json();
}
function disconnect(focus = false) {
  generation += 1;
  workflowGraph.reset();
  workflowEditor.reset();
  connected = false;
  token = "";
  sessionStorage.removeItem("perdixGatewayToken");
  byId("token").value = "";
  byId("workspace").hidden = true;
  byId("login-panel").hidden = false;
  byId("disconnect").hidden = true;
  rows.clear(); approvals.clear(); permissionEvents.clear();
  byId("jobs").replaceChildren();
  selected = null; cursor = 0; jobs = [];
  if (focus) byId("token").focus();
}
function relativeTime(seconds) {
  const minutes = Math.max(0, Math.floor((Date.now() / 1000 - seconds) / 60));
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}분 전`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)}시간 전`;
  return `${Math.floor(minutes / 1440)}일 전`;
}
function renderJobs() {
  byId("empty").hidden = jobs.length > 0;
  byId("job-count").textContent = `${jobs.length}개`;
  const ids = new Set(jobs.map((job) => job.id));
  for (const [id, row] of rows) if (!ids.has(id)) { row.remove(); rows.delete(id); }
  for (const [index, job] of jobs.entries()) {
    let row = rows.get(job.id);
    if (!row) {
      row = element("button", undefined, "job-row"); row.type = "button";
      row.append(element("span", job.prompt, "job-title"), element("span", "", "job-description"));
      row.addEventListener("click", () => selectJob(job.id)); rows.set(job.id, row);
    }
    row.setAttribute("aria-pressed", String(selected === job.id));
    row.lastChild.textContent = `${job.project} · ${sourceLabels[job.source] || job.source} · ${labels[job.status]} · ${relativeTime(job.createdAt)}`;
    const current = byId("jobs").children[index];
    if (current !== row) byId("jobs").insertBefore(row, current || null);
  }
}
function renderDetail() {
  const job = jobs.find((item) => item.id === selected);
  byId("detail-empty").hidden = Boolean(job);
  byId("detail-body").hidden = !job;
  if (!job) return;
  byId("job-meta").textContent = `${job.project} · ${sourceLabels[job.source]} · ${labels[job.status]} · ${relativeTime(job.createdAt)}`;
  byId("job-prompt").textContent = job.prompt;
  byId("cancel").hidden = !["queued", "running", "waiting"].includes(job.status);
  byId("result").hidden = !job.result;
  byId("result-copy").textContent = job.result;
}
async function selectJob(id) {
  generation += 1;
  selected = id; cursor = 0;
  workflowGraph.selectJob(id);
  byId("timeline").replaceChildren(); byId("permissions").replaceChildren();
  approvals.clear(); permissionEvents.clear();
  renderJobs(); renderDetail();
  if (matchMedia("(max-width: 880px)").matches) byId("detail-heading").focus();
  try { await refreshDetails(); } catch (error) { notice(error.message, true); }
}
function renderEvent(event) {
  const data = event.data;
  if (event.kind === "permission") { permissionEvents.set(data.permissionId, data); return; }
  if (event.kind === "permissionResolved") { permissionEvents.delete(data.permissionId); return; }
  if (event.kind === "notificationFailed") {
    byId("timeline").append(element("p", data, "muted")); return;
  }
  if (event.kind !== "update") return;
  if (data.sessionUpdate === "agent_message_chunk" && data.content?.type === "text") {
    let last = byId("timeline").lastElementChild;
    if (!last || !last.classList.contains("log-copy")) {
      last = element("p", "", "log-copy"); byId("timeline").append(last);
    }
    last.textContent += data.content.text;
  } else if (data.sessionUpdate === "tool_call" || data.sessionUpdate === "tool_call_update") {
    const block = element("div", undefined, "log-item");
    block.append(element("p", data.title || "도구 실행", "small"));
    for (const item of data.content || []) if (item.type === "content" && item.content?.text) block.append(element("p", item.content.text, "tool-copy"));
    byId("timeline").append(block);
  }
}
async function refreshDetails() {
  const id = selected, version = generation;
  await workflowGraph.refresh();
  if (!id) return;
  const events = await api(`/api/jobs/${id}/events?after=${cursor}`);
  if (selected !== id || generation !== version) return;
  for (const event of events) { renderEvent(event); cursor = event.seq; }
  const pending = await api(`/api/jobs/${id}/permissions`);
  if (selected !== id || generation !== version) return;
  const keys = new Set(pending.map((p) => p.permissionId));
  for (const [key, card] of approvals) if (!keys.has(key)) { card.remove(); approvals.delete(key); }
  for (const permission of pending) {
    if (approvals.has(permission.permissionId)) continue;
    const detail = permissionEvents.get(permission.permissionId);
    const card = element("section", undefined, "approval");
    card.append(element("h3", detail?.toolCall?.title || "실행 권한 확인"));
    if (detail?.toolCall?.rawInput) card.append(element("p", JSON.stringify(detail.toolCall.rawInput, null, 2), "tool-copy"));
    for (const content of detail?.toolCall?.content || []) if (content.type === "content" && content.content?.text) card.append(element("p", content.content.text, "tool-copy"));
    card.append(element("p", "도구 내용을 확인한 뒤 실행 여부를 선택하세요.", "small"));
    const actions = element("div", undefined, "actions");
    for (const option of permission.options) {
      const names = { allow_once: "이번만 허용", allow_always: "이 세션에서 항상 허용", reject_once: "이번 요청 거절", reject_always: "이 세션에서 항상 거절" };
      const button = element("button", names[option.kind] || option.name, option.kind.startsWith("allow") ? "secondary" : "danger");
      button.type = "button";
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api(`/api/jobs/${id}/permissions/${permission.permissionId}`, { optionId: option.optionId });
          card.remove(); approvals.delete(permission.permissionId); notice("승인 요청을 처리했습니다.");
          byId("detail-heading").focus();
        } catch (error) { notice(error.message, true); button.disabled = false; }
      });
      actions.append(button);
    }
    card.append(actions); approvals.set(permission.permissionId, card); byId("permissions").append(card);
  }
}
async function refresh() {
  if (!connected || refreshing) return;
  refreshing = true; const version = generation;
  try {
    const nextJobs = await api("/api/jobs");
    if (generation !== version) return;
    jobs = nextJobs; renderJobs(); renderDetail(); await refreshDetails();
    const status = await api("/api/status");
    if (generation === version) {
      const recovered = networkFailed;
      networkFailed = false; renderChannels(status);
      if (recovered) notice("연결이 복구되었습니다.");
    }
  } catch (error) {
    if (generation === version && connected) {
      networkFailed = true;
      if (channelStatus) renderChannels(channelStatus);
      notice("게이트웨이에 연결할 수 없습니다. 자동으로 다시 연결하고 있습니다.", true);
    }
  }
  finally { refreshing = false; }
}
async function connect() {
  const status = await api("/api/status");
  sessionStorage.setItem("perdixGatewayToken", token);
  connected = true; byId("login-panel").hidden = true; byId("workspace").hidden = false; byId("disconnect").hidden = false;
  byId("project").replaceChildren(...status.projects.map((project) => {
    const option = element("option", project.name); option.value = project.name; return option;
  }));
  workflowGraph.setProject(byId("project").value);
  workflowEditor.setProject(byId("project").value, status.workflowDefaultModel || "");
  renderChannels(status);
  byId("github-details").hidden = !status.projects.some((p) => p.githubRepository);
  byId("token").value = ""; notice("연결되었습니다."); renderDetail(); await refresh();
}
function renderChannels(status) {
  channelStatus = status;
  byId("channels").replaceChildren(...[`Web · ${networkFailed ? "재연결 중" : "연결됨"}`, `Telegram · ${status.channels.telegram === "disabled" ? "미설정" : status.channels.telegram === "error" ? "연결 실패" : status.channels.telegram === "connected" ? "연결됨" : "연결 중"}`, `GitHub · ${status.channels.github === "disabled" ? "미설정" : "등록됨"}`].map((text) => element("span", text, "badge")));
}
byId("login-form").addEventListener("submit", async (event) => {
  event.preventDefault(); token = byId("token").value.trim();
  const button = event.submitter; button.disabled = true; button.textContent = "연결 중…";
  try { await connect(); byId("prompt").focus(); } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; button.textContent = "연결하기"; }
});
for (const [form, path, body] of [
  ["submit-form", "/api/jobs", () => ({ project: byId("project").value, prompt: byId("prompt").value })],
  ["github-form", "/api/github/import", () => ({ project: byId("project").value, issueNumber: Number(byId("issue").value), instruction: byId("instruction").value })],
]) byId(form).addEventListener("submit", async (event) => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  const buttonLabel = button.textContent;
  const status = byId(`${form}-status`); status.hidden = false; status.className = "small";
  status.textContent = form === "github-form" ? "GitHub에서 이슈를 가져오고 있습니다…" : "작업을 접수하고 있습니다…";
  button.textContent = "접수 중…";
  try {
    const job = await api(path, body()); jobs = [job, ...jobs.filter((j) => j.id !== job.id)];
    if (form === "submit-form") byId("prompt").value = "";
    await selectJob(job.id); notice("작업을 접수했습니다."); status.textContent = "작업을 접수했습니다.";
  } catch (error) { notice(error.message, true); status.textContent = error.message; status.className = "small error"; }
  finally { button.disabled = false; button.textContent = buttonLabel; }
});
byId("cancel").addEventListener("click", async () => {
  const button = byId("cancel"); button.disabled = true;
  try { await api(`/api/jobs/${selected}/cancel`, {}); notice("작업을 취소했습니다."); await refresh(); }
  catch (error) { notice(error.message, true); } finally { button.disabled = false; }
});
byId("disconnect").addEventListener("click", () => { disconnect(true); notice("연결을 해제했습니다. 접수한 작업은 계속 실행됩니다."); });
byId("theme").addEventListener("change", () => {
  if (byId("theme").value === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = byId("theme").value;
});
setInterval(refresh, 2000);
if (token) connect().catch((error) => notice(error.message, true));

byId("project").addEventListener("change", () => { workflowGraph.setProject(byId("project").value); workflowEditor.setProject(byId("project").value); });
