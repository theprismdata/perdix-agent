// 초안 편집 오류 안내. 최종 실행 계획 검증은 Python WorkflowPlan이 담당한다.
const roles = new Set(["analyze", "implement", "review"]);
const taskId = /^[a-z0-9][a-z0-9_-]{0,127}$/;
const strings = (value) => Array.isArray(value) && value.every((item) => typeof item === "string");
const filled = (value) => typeof value === "string" && value.trim().length > 0;
export const roleNames = { analyze: "분석", implement: "구현", review: "리뷰" };

function shape(plan) {
  return plan && typeof plan.objective === "string" && Array.isArray(plan.tasks) && plan.tasks.length <= 20 && plan.tasks.every((task) => task && typeof task.taskId === "string" && typeof task.title === "string" && roles.has(task.role) && typeof task.model === "string" && strings(task.paths) && strings(task.acceptance) && (task.dependencies === undefined || strings(task.dependencies)) && (task.artifactRequirements === undefined || strings(task.artifactRequirements)));
}
function cycle(tasks) {
  const done = new Set(), visiting = new Set(), byId = new Map(tasks.map((task) => [task.taskId, task]));
  function visit(key) {
    if (visiting.has(key)) return true;
    if (done.has(key)) return false;
    visiting.add(key);
    if ((byId.get(key)?.dependencies || []).some(visit)) return true;
    visiting.delete(key); done.add(key); return false;
  }
  return tasks.some((task) => visit(task.taskId));
}
export function planErrors(plan) {
  if (!shape(plan)) return ["계획 형식과 노드 수를 확인하세요. 노드는 최대 20개입니다."];
  const errors = [], ids = new Set(plan.tasks.map((task) => task.taskId));
  if (!filled(plan.objective)) errors.push("이 워크플로의 목표를 입력하세요.");
  if (!plan.tasks.length) errors.push("노드를 하나 이상 추가하세요.");
  if (ids.size !== plan.tasks.length) errors.push("노드 ID가 중복되었습니다.");
  for (const task of plan.tasks) {
    const name = task.title || task.taskId;
    if (!taskId.test(task.taskId)) errors.push(`${name}: ID는 영문 소문자·숫자로 시작하고 소문자·숫자·_-만 사용하세요.`);
    if (!filled(task.title) || !filled(task.model)) errors.push(`${name}: 이름과 모델을 입력하세요.`);
    if (!task.paths.length || !task.paths.every(filled) || task.paths.some((path) => /^(?:[a-z]:|[\\/])/i.test(path) || path.split(/[\\/]/).includes("..") || path.includes("\0"))) errors.push(`${name}: 프로젝트 안의 상대 파일 경로를 입력하세요.`);
    if (!task.acceptance.length || !task.acceptance.every(filled)) errors.push(`${name}: 완료 조건을 하나 이상 입력하세요.`);
    const dependencies = task.dependencies || [];
    if (dependencies.some((key) => !ids.has(key))) errors.push(`${name}: 존재하지 않는 선행 노드입니다.`);
    if (new Set(dependencies).size !== dependencies.length) errors.push(`${name}: 중복 연결이 있습니다.`);
  }
  if (cycle(plan.tasks)) errors.push("순환 연결은 실행할 수 없습니다.");
  if (("/workflow plan " + JSON.stringify(plan)).length > 32000) errors.push("계획이 너무 큽니다. 설명과 완료 조건을 줄여주세요.");
  return errors;
}
export function addDependency(plan, from, to) {
  const target = plan.tasks.find((task) => task.taskId === to);
  if (!target || !plan.tasks.some((task) => task.taskId === from)) throw new Error("연결할 작업을 선택하세요.");
  if (from === to) throw new Error("자기 자신에게 연결할 수 없습니다.");
  if ((target.dependencies || []).includes(from)) throw new Error("이미 연결된 노드입니다.");
  const tasks = plan.tasks.map((task) => task === target ? { ...task, dependencies: [...(task.dependencies || []), from] } : task);
  if (cycle(tasks)) throw new Error("순환 연결은 추가할 수 없습니다.");
  target.dependencies = [...(target.dependencies || []), from];
}
export function removeTask(plan, key) {
  plan.tasks = plan.tasks.filter((task) => task.taskId !== key);
  for (const task of plan.tasks) task.dependencies = (task.dependencies || []).filter((value) => value !== key);
}
export function replaceTask(plan, key, replacement) {
  if (!taskId.test(replacement.taskId)) throw new Error("ID는 소문자·숫자·_-만 사용할 수 있습니다.");
  if (plan.tasks.some((task) => task.taskId !== key && task.taskId === replacement.taskId)) throw new Error("노드 ID가 중복되었습니다.");
  const current = plan.tasks.find((task) => task.taskId === key);
  if (!current) throw new Error("수정할 노드를 선택하세요.");
  plan.tasks = plan.tasks.map((task) => task === current ? { ...replacement, dependencies: current.dependencies || [] } : task);
  for (const task of plan.tasks) task.dependencies = (task.dependencies || []).map((value) => value === key ? replacement.taskId : value);
}
export function newTask(plan, role, model) {
  let index = 1;
  while (plan.tasks.some((task) => task.taskId === `${role}-${index}`)) index++;
  return { taskId: `${role}-${index}`, title: `${roleNames[role]} 작업`, role, model, paths: ["."], acceptance: [], dependencies: [], artifactRequirements: [] };
}
export function restoreDraft(raw) {
  if (raw.length > 128000) throw new Error("저장된 초안이 너무 큽니다.");
  const value = JSON.parse(raw);
  if (value?.version !== 1 || !shape(value.plan)) throw new Error("저장된 초안 형식을 확인할 수 없습니다.");
  const positions = Object.create(null);
  for (const task of value.plan.tasks) {
    const point = value.positions?.[task.taskId];
    if (point && Number.isFinite(point.x) && Number.isFinite(point.y)) positions[task.taskId] = { x: Math.max(24, Math.min(5000, point.x)), y: Math.max(24, Math.min(5000, point.y)) };
  }
  return { plan: value.plan, positions };
}
