import { execFile } from "node:child_process";
import { randomUUID } from "node:crypto";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { expect, it } from "vitest";
import { z } from "zod";
import { MAC_IMAGE_PASTE_CORE, MAC_IMAGE_PASTE_SCRIPT, supportsMacTerminalImagePaste } from "../hermes/macos-image-paste.js";
import { commandEnabled } from "../../../hermes-tui/vendor/ui-tui/src/perdixFeatures.js";

const run = promisify(execFile);
const png = "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGP4z8DAAMIM/4EAAB/uBfsL2WiLAAAAAElFTkSuQmCC";

it("enables the bridge only for local interactive macOS Terminal sessions", () => {
  expect(supportsMacTerminalImagePaste({ TERM_PROGRAM: "Apple_Terminal" }, "darwin", true)).toBe(true);
  expect(supportsMacTerminalImagePaste({ TERM_PROGRAM: "Apple_Terminal", SSH_TTY: "/dev/pts/1" }, "darwin", true)).toBe(false);
  expect(supportsMacTerminalImagePaste({ TERM_PROGRAM: "Apple_Terminal" }, "darwin", false)).toBe(false);
  expect(supportsMacTerminalImagePaste({ TERM_PROGRAM: "vscode" }, "darwin", true)).toBe(false);
  expect(supportsMacTerminalImagePaste({ TERM_PROGRAM: "Apple_Terminal" }, "linux", true)).toBe(false);
  expect(commandEnabled("paste", true)).toBe(false);
  expect(commandEnabled("paste", false)).toBe(true);
});

it("exits the native helper and cleans temporary files when its session lease is released", async () => {
  if (process.platform !== "darwin") return;
  const directory = await mkdtemp(join(tmpdir(), "perdix-image-paste-lifecycle-"));
  const lease = join(directory, "active");
  await writeFile(lease, "");
  const script = MAC_IMAGE_PASTE_SCRIPT.replace("$.NSPasteboard.generalPasteboard",
    "$.NSPasteboard.pasteboardWithName('org.perdix.image-paste-test')");
  try {
    const pending = run("/usr/bin/osascript", ["-l", "JavaScript", "-e", script, directory, lease, String(process.pid)], {
      env: { PATH: "/usr/bin:/bin" }, timeout: 5000,
    });
    await new Promise(resolve => setTimeout(resolve, 150));
    await rm(lease, { force: true });
    await pending;
    await expect(stat(directory)).rejects.toThrow();
  } finally { await rm(directory, { recursive: true, force: true }); }
}, 10_000);

it("turns image-only pasteboard content into a pasteable file while preserving and restoring native data", async () => {
  if (process.platform !== "darwin") return;
  const directory = await mkdtemp(join(tmpdir(), "perdix-image-paste-test-"));
  const script = MAC_IMAGE_PASTE_CORE + `
function run(argv) {
  const board = $.NSPasteboard.pasteboardWithUniqueName;
  const data = $.NSData.alloc.initWithBase64EncodedStringOptions('${png}', 0);
  const textType = 'public.utf8-plain-text';
  const bridge = createImagePasteBridge(board, argv[0]);
  function copyImage() {
    board.declareTypesOwner($(['public.png', 'org.perdix.test']), null);
    board.setDataForType(data, 'public.png');
    board.setStringForType('original metadata', 'org.perdix.test');
  }
  try {
    copyImage();
    const before = board.stringForType(textType).isNil();
    bridge.tick(true);
    const uri = ObjC.unwrap(board.stringForType(textType));
    const preserved = ObjC.unwrap(board.dataForType('public.png').base64EncodedStringWithOptions(0)) === '${png}';
    const extra = ObjC.unwrap(board.stringForType('org.perdix.test')) === 'original metadata';
    bridge.tick(false);
    const restored = board.stringForType(textType).isNil() && !board.dataForType('public.png').isNil();
    bridge.tick(true);
    board.declareTypesOwner($([textType]), null);
    board.setStringForType('new clipboard text', textType);
    bridge.close();
    const newerKept = ObjC.unwrap(board.stringForType(textType)) === 'new clipboard text';
    bridge.tick(true);
    const plainKept = ObjC.unwrap(board.stringForType(textType)) === 'new clipboard text';
    copyImage();
    board.addTypesOwner($([textType]), null);
    board.setStringForType('image caption', textType);
    bridge.tick(true);
    const mixedKept = ObjC.unwrap(board.stringForType(textType)) === 'image caption';
    return JSON.stringify({before, uri, preserved, extra, restored, newerKept, plainKept, mixedKept});
  } finally { bridge.close(); board.releaseGlobally; }
}
`;
  try {
    const { stdout } = await run("/usr/bin/osascript", ["-l", "JavaScript", "-e", script, directory], {
      env: { PATH: "/usr/bin:/bin" }, timeout: 5000,
    });
    const result = z.object({ uri: z.string(), before: z.boolean(), preserved: z.boolean(), extra: z.boolean(),
      restored: z.boolean(), newerKept: z.boolean(), plainKept: z.boolean(), mixedKept: z.boolean() }).parse(JSON.parse(stdout));
    expect(result).toMatchObject({ before: true, preserved: true, extra: true, restored: true, newerKept: true, plainKept: true, mixedKept: true });
    expect(await readFile(fileURLToPath(result.uri))).toEqual(Buffer.from(png, "base64"));
  } finally { await rm(directory, { recursive: true, force: true }); }
}, 10_000);

it("services native application events while the clipboard helper is running", async () => {
  if (process.platform !== "darwin") return;
  const directory = await mkdtemp(join(tmpdir(), "perdix-image-paste-events-"));
  const lease = join(directory, "active");
  await writeFile(lease, "");
  const script = MAC_IMAGE_PASTE_SCRIPT.replace(
    "const bridge = createImagePasteBridge($.NSPasteboard.generalPasteboard, argv[0]);",
    `const bridge = createImagePasteBridge($.NSPasteboard.pasteboardWithUniqueName, argv[0]);
    $.NSTimer.scheduledTimerWithTimeIntervalRepeatsBlock(0.05, false, function() {
      $.NSFileManager.defaultManager.removeItemAtPathError(argv[1], null);
    });`,
  );
  try {
    await run("/usr/bin/osascript", ["-l", "JavaScript", "-e", script, directory, lease, String(process.pid)], {
      env: { PATH: "/usr/bin:/bin" }, timeout: 1500,
    });
    await expect(stat(directory)).rejects.toThrow();
  } finally { await rm(directory, { recursive: true, force: true }); }
}, 5000);

it("adds a pasteable representation to an image copied by another process", async () => {
  if (process.platform !== "darwin") return;
  const directory = await mkdtemp(join(tmpdir(), "perdix-image-paste-external-"));
  const name = `org.perdix.image-paste.${randomUUID()}`;
  const producer = `ObjC.import('AppKit'); function run(argv) {
    const board = $.NSPasteboard.pasteboardWithName(argv[0]);
    board.declareTypesOwner($(['public.png']), null);
    board.setDataForType($.NSData.alloc.initWithBase64EncodedStringOptions('${png}', 0), 'public.png');
  }`;
  const consumer = MAC_IMAGE_PASTE_CORE + `function run(argv) {
    const board = $.NSPasteboard.pasteboardWithName(argv[0]);
    const bridge = createImagePasteBridge(board, argv[1]);
    try {
      bridge.tick(true);
      const text = board.stringForType('public.utf8-plain-text');
      return JSON.stringify({uri:text.isNil() ? '' : ObjC.unwrap(text),
        data:ObjC.unwrap(board.dataForType('public.png').base64EncodedStringWithOptions(0))});
    } finally { bridge.close(); board.releaseGlobally; }
  }`;
  try {
    await run('/usr/bin/osascript', ['-l', 'JavaScript', '-e', producer, name], {
      env: { PATH: '/usr/bin:/bin' }, timeout: 2000,
    });
    const { stdout } = await run('/usr/bin/osascript', ['-l', 'JavaScript', '-e', consumer, name, directory], {
      env: { PATH: '/usr/bin:/bin' }, timeout: 2000,
    });
    const result = z.object({uri:z.string(),data:z.string()}).parse(JSON.parse(stdout));
    expect(result.uri).toMatch(/^file:\/\//);
    expect(result.data).toBe(png);
    expect(await readFile(fileURLToPath(result.uri))).toEqual(Buffer.from(png, 'base64'));
  } finally { await rm(directory, { recursive: true, force: true }); }
}, 5000);
