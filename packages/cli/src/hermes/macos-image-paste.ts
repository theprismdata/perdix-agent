import { spawn } from "node:child_process";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

export const MAC_IMAGE_PASTE_CORE = `ObjC.import('AppKit');
function createImagePasteBridge(board, directory) {
  const textType = 'public.utf8-plain-text';
  let saved = null, uri = null, ownedCount = -1, observedCount = -1, sequence = 0;
  function restore() {
    if (saved && board.changeCount === ownedCount && ObjC.unwrap(board.stringForType(textType)) === uri) {
      board.clearContents;
      board.writeObjects($(saved));
    }
    saved = null; uri = null; ownedCount = -1; observedCount = -1;
  }
  function tick(active) {
    if (!active) { restore(); return; }
    const count = board.changeCount;
    if (count === ownedCount || count === observedCount) return;
    saved = null; uri = null; ownedCount = -1; observedCount = count;
    const text = board.stringForType(textType);
    if (!text.isNil() && ObjC.unwrap(text).length) return;
    let png = board.dataForType('public.png');
    if (png.isNil()) {
      const tiff = board.dataForType('public.tiff');
      if (tiff.isNil()) return;
      const bitmap = $.NSBitmapImageRep.imageRepWithData(tiff);
      if (bitmap.isNil()) return;
      png = bitmap.representationUsingTypeProperties($.NSPNGFileType, $({}));
    }
    if (png.isNil() || !png.length) return;
    const items = board.pasteboardItems, copies = [], prepared = [];
    for (let i = 0; i < items.count; i++) {
      const original = items.objectAtIndex(i), copy = $.NSPasteboardItem.alloc.init;
      const augmented = $.NSPasteboardItem.alloc.init;
      const types = original.types;
      for (let j = 0; j < types.count; j++) {
        const type = types.objectAtIndex(j), data = original.dataForType(type);
        if (!data.isNil()) { copy.setDataForType(data, type); augmented.setDataForType(data, type); }
      }
      copies.push(copy);
      prepared.push(augmented);
    }
    if (!prepared.length) return;
    const path = directory + '/screenshot-' + (++sequence) + '.png';
    if (!png.writeToFileAtomically(path, true)) throw new Error('Screenshot temporary file could not be written');
    if (board.changeCount !== count) return;
    const next = ObjC.unwrap($.NSURL.fileURLWithPath(path).absoluteString);
    prepared[0].setStringForType(next, textType);
    board.clearContents;
    if (!board.writeObjects($(prepared))) {
      board.clearContents;
      board.writeObjects($(copies));
      throw new Error('Screenshot clipboard representation could not be written');
    }
    saved = copies; uri = next; ownedCount = board.changeCount;
  }
  return { tick: tick, close: restore };
}
`;

export const MAC_IMAGE_PASTE_SCRIPT = MAC_IMAGE_PASTE_CORE + `
ObjC.bindFunction('kill', ['int', ['int', 'int']]);
function run(argv) {
  const bridge = createImagePasteBridge($.NSPasteboard.generalPasteboard, argv[0]);
  try {
    while ($.NSFileManager.defaultManager.fileExistsAtPath(argv[1]) && $.kill(Number(argv[2]), 0) === 0) {
      const app = $.NSWorkspace.sharedWorkspace.frontmostApplication;
      bridge.tick(!app.isNil() && ObjC.unwrap(app.bundleIdentifier) === 'com.apple.Terminal');
      $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.1));
    }
  } finally {
    bridge.close();
    $.NSFileManager.defaultManager.removeItemAtPathError(argv[0], null);
  }
}
`;

export function supportsMacTerminalImagePaste(
  env: NodeJS.ProcessEnv = process.env, platform: NodeJS.Platform = process.platform,
  terminal = Boolean(process.stdin.isTTY && process.stdout.isTTY),
): boolean {
  return platform === "darwin" && terminal && env.TERM_PROGRAM === "Apple_Terminal" && !env.SSH_CONNECTION && !env.SSH_TTY;
}

export async function startMacTerminalImagePaste(): Promise<{ close(): Promise<void> }> {
  if (!supportsMacTerminalImagePaste()) return { close: async () => {} };
  const directory = await mkdtemp(join(tmpdir(), "perdix-image-paste-"));
  const lease = join(directory, "active");
  await writeFile(lease, "", { mode: 0o600 });
  const child = spawn("/usr/bin/osascript", ["-l", "JavaScript", "-e", MAC_IMAGE_PASTE_SCRIPT,
    directory, lease, String(process.pid)], { env: { PATH: "/usr/bin:/bin" }, stdio: "ignore", detached: true });
  let failed = false;
  const exited = new Promise<void>(resolve => {
    child.once("error", () => { failed = true; resolve(); });
    child.once("close", code => { failed = code !== 0; resolve(); });
  });
  const release = () => { rmSync(lease, { force: true }); };
  process.once("exit", release);
  return { close: async () => {
    release();
    process.off("exit", release);
    await exited;
    await rm(directory, { recursive: true, force: true });
    if (failed) process.stderr.write("Perdix: macOS 이미지 붙여넣기 보조 프로세스가 종료되었습니다.\n");
  } };
}
