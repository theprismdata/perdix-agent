import { execFile } from "node:child_process";
import { open } from "node:fs/promises";
import { homedir } from "node:os";
import { basename, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import type { PromptImage } from "../agent/types.js";

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
export const MAX_IMAGES = 4;
const run = promisify(execFile);

export class ImageAttachmentError extends Error {}

export interface AttachedImage {
  readonly name: string;
  readonly image: PromptImage;
}

export function imageFromBytes(bytes: Buffer): PromptImage {
  if (!bytes.length || bytes.length > MAX_IMAGE_BYTES) {
    throw new ImageAttachmentError("이미지는 파일당 5 MiB 이하여야 합니다.");
  }
  let mimeType: string;
  if (bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))) mimeType = "image/png";
  else if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) mimeType = "image/jpeg";
  else if (["GIF87a", "GIF89a"].includes(bytes.toString("ascii", 0, 6))) mimeType = "image/gif";
  else if (bytes.toString("ascii", 0, 4) === "RIFF" && bytes.toString("ascii", 8, 12) === "WEBP") mimeType = "image/webp";
  else throw new ImageAttachmentError("PNG, JPEG, GIF, WebP 이미지 파일을 첨부하세요.");
  return { type: "image", mimeType, data: bytes.toString("base64") };
}

export function droppedImagePath(value: string): { path: string; remainder: string } | null {
  const trimmed = value.trim();
  const quoted = /^(?:"([^"]+)"|'([^']+)')(?:\s+([\s\S]*))?$/.exec(trimmed);
  if (quoted) return { path: quoted[1] ?? quoted[2] ?? "", remainder: quoted[3] ?? "" };
  const bare = /^(.*?\.(?:png|jpe?g|gif|webp))(?=\s|$)([\s\S]*)$/i.exec(trimmed);
  if (!bare?.[1]) return null;
  return { path: bare[1].replace(/\\([\s'"()])/g, "$1"), remainder: (bare[2] ?? "").trim() };
}

export async function readImage(path: string, cwd: string): Promise<AttachedImage> {
  const local = path.startsWith("file://") ? fileURLToPath(path) : path;
  const expanded = local.startsWith("~/") ? resolve(homedir(), local.slice(2)) : resolve(cwd, local);
  const file = await open(expanded, "r");
  try {
    const stat = await file.stat();
    if (!stat.isFile()) throw new ImageAttachmentError("일반 이미지 파일을 선택하세요.");
    if (stat.size > MAX_IMAGE_BYTES) throw new ImageAttachmentError("이미지는 파일당 5 MiB 이하여야 합니다.");
    const bytes = Buffer.alloc(MAX_IMAGE_BYTES + 1);
    let length = 0;
    while (length < bytes.length) {
      const { bytesRead } = await file.read(bytes, length, bytes.length - length, length);
      if (!bytesRead) break;
      length += bytesRead;
    }
    return { name: basename(expanded), image: imageFromBytes(bytes.subarray(0, length)) };
  } finally { await file.close(); }
}

const MAC_CLIPBOARD = `ObjC.import('AppKit');
const pasteboard = $.NSPasteboard.generalPasteboard;
function image() {
  for (const type of ['public.png', 'public.tiff']) {
    const data = pasteboard.dataForType(type);
    if (data.isNil()) continue;
    if (type === 'public.png') return ObjC.unwrap(data.base64EncodedStringWithOptions(0));
    const bitmap = $.NSBitmapImageRep.imageRepWithData(data);
    if (bitmap.isNil()) continue;
    const png = bitmap.representationUsingTypeProperties($.NSPNGFileType, $({}));
    if (!png.isNil()) return ObjC.unwrap(png.base64EncodedStringWithOptions(0));
  }
  return '';
}
image();`;
const WINDOWS_CLIPBOARD = "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; " +
  "$image=[Windows.Forms.Clipboard]::GetImage(); if ($image) { " +
  "$stream=New-Object IO.MemoryStream; try { $image.Save($stream,[Drawing.Imaging.ImageFormat]::Png); " +
  "[Convert]::ToBase64String($stream.ToArray()) } finally { $stream.Dispose(); $image.Dispose() } }";

export async function readClipboardImage(): Promise<AttachedImage> {
  const env: NodeJS.ProcessEnv = {};
  for (const key of ["PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY"]) {
    if (process.env[key]) env[key] = process.env[key];
  }
  const commands = process.platform === "darwin"
    ? [{ command: "/usr/bin/osascript", args: ["-l", "JavaScript", "-e", MAC_CLIPBOARD], base64: true }]
    : process.platform === "win32"
      ? [{ command: "powershell.exe", args: ["-NoProfile", "-NonInteractive", "-STA", "-Command", WINDOWS_CLIPBOARD], base64: true }]
      : [{ command: "wl-paste", args: ["--no-newline", "--type", "image/png"], base64: false },
        { command: "xclip", args: ["-selection", "clipboard", "-t", "image/png", "-o"], base64: false }];
  for (const command of commands) {
    try {
      const { stdout } = await run(command.command, command.args, {
        encoding: "buffer", timeout: 5000, maxBuffer: Math.ceil(MAX_IMAGE_BYTES * 4 / 3) + 1024,
        windowsHide: true, env,
      });
      const bytes = command.base64 ? Buffer.from(stdout.toString().trim(), "base64") : stdout;
      if (bytes.length) return { name: "clipboard.png", image: imageFromBytes(bytes) };
    } catch (error) {
      if (error instanceof ImageAttachmentError) throw error;
      if (!(error instanceof Error)) throw error;
    }
  }
  throw new ImageAttachmentError("클립보드에서 이미지를 읽지 못했습니다. /image <파일 경로>로 첨부하세요.");
}
