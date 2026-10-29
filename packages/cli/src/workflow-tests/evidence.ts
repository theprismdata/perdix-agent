import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

export async function captureWorkflowEvidence(name: string, value: unknown): Promise<void> {
  const directory = process.env.PERDIX_WORKFLOW_EVIDENCE_DIR;
  if (!directory) return;
  await mkdir(directory, { recursive: true });
  await writeFile(join(directory, name), `${JSON.stringify(value, null, 2)}\n`);
}
