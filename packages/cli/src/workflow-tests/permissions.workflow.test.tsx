import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render } from "ink-testing-library";
import type { PermissionRequest } from "../agent/types.js";
import { approvalOptions } from "../hermes/events.js";
import { PermissionPrompt } from "../ui/components/PermissionPrompt.js";

afterEach(cleanup);

const request: PermissionRequest = {
  requestId: "permission-1", toolCallId: "write-1", title: "write example.txt",
  options: [
    { optionId: "allow_once", kind: "allow_once", name: "Allow once" },
    { optionId: "allow_always", kind: "allow_always", name: "Allow always" },
    { optionId: "reject_once", kind: "reject_once", name: "Reject once" },
    { optionId: "yolo", kind: "allow_always", name: "YOLO — 이 세션의 모든 도구 허용" },
  ],
};

it("keeps tool approval separate from session approval in Hermes", () => {
  const options = approvalOptions(request);
  expect(options.get("always")).toBe("allow_always");
  expect(options.get("yolo")).toBe("yolo");
});

it("selects yolo through the running permission prompt", async () => {
  const answer = vi.fn();
  const ui = render(<PermissionPrompt request={request} onAnswer={answer} />);
  await vi.waitFor(() => expect(ui.stdin.listenerCount("readable")).toBeGreaterThan(0));
  ui.stdin.write("4");
  await vi.waitFor(() => expect(answer).toHaveBeenCalledWith({ optionId: "yolo" }));
});

it("keeps the always shortcut scoped to the tool", async () => {
  const answer = vi.fn();
  const ui = render(<PermissionPrompt request={request} onAnswer={answer} />);
  await vi.waitFor(() => expect(ui.stdin.listenerCount("readable")).toBeGreaterThan(0));
  ui.stdin.write("a");
  await vi.waitFor(() => expect(answer).toHaveBeenCalledWith({ optionId: "allow_always" }));
});
