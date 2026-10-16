import test from "node:test";
import assert from "node:assert/strict";
import { addDependency, removeTask, replaceTask, planErrors, restoreDraft } from "../../../server/src/perdix/gateway/static/draft-plan.js";

const task = (taskId, dependencies = []) => ({ taskId, title: taskId, role: "implement", model: "fake-model", paths: ["file.txt"], acceptance: ["검증"], dependencies, artifactRequirements: [] });
const plan = () => ({ objective: "동작 확인", tasks: [task("a"), task("b"), task("c")] });

test("연결은 출발→도착이며 중복·자기 연결·순환을 거절한다", () => {
  const p = plan(); addDependency(p, "a", "b"); addDependency(p, "b", "c");
  assert.deepEqual(p.tasks[1].dependencies, ["a"]);
  assert.throws(() => addDependency(p, "a", "b"), /이미/);
  assert.throws(() => addDependency(p, "a", "a"), /자기/);
  assert.throws(() => addDependency(p, "c", "a"), /순환/);
  assert.deepEqual(p.tasks[0].dependencies, []);
  assert.throws(() => addDependency(p, "missing", "a"), /작업/);
});
test("이름 변경은 참조를 보존하고 삭제는 연결을 정리한다", () => {
  const p = plan(); addDependency(p, "a", "b");
  replaceTask(p, "a", { ...task("renamed"), title: "수정" });
  assert.deepEqual(p.tasks[1].dependencies, ["renamed"]);
  assert.throws(() => replaceTask(p, "renamed", task("b")), /중복/);
  removeTask(p, "renamed"); assert.deepEqual(p.tasks[0].dependencies, []);
});
test("불완전한 필드·경로·DAG·20개 상한·본문 상한 검증", () => {
  assert.deepEqual(planErrors(plan()), []);
  for (const paths of [["../escape"], ["C:\\outside"], ["/etc"], []]) {
    const p = plan(); p.tasks[0].paths = paths; assert.ok(planErrors(p).length);
  }
  const p = plan(); p.tasks[0].model = " "; p.tasks[1].acceptance = []; p.tasks[2].taskId = "Bad ID";
  assert.ok(planErrors(p).length >= 3);
  assert.ok(planErrors({ objective: "x", tasks: [] }).length);
  assert.ok(planErrors({ objective: "x", tasks: Array.from({ length: 21 }, (_, i) => task(`n${i}`)) }).length);
  assert.ok(planErrors({ ...plan(), objective: "x".repeat(32000) }).length);
});
test("초안 복원은 버전·크기·shape를 검사하고 좌표를 실행 계획에서 분리한다", () => {
  const p = plan(); p.tasks[0].model = "";
  const draft = restoreDraft(JSON.stringify({ version: 1, plan: p, positions: { a: { x: 24, y: 48 } } }));
  assert.equal(draft.plan.tasks[0].model, ""); assert.equal(draft.positions.a.x, 24);
  for (const value of ["{}", "not json", JSON.stringify({ version: 2, plan: plan() }), JSON.stringify({ version: 1, plan: { tasks: [null] } })]) assert.throws(() => restoreDraft(value));
});
