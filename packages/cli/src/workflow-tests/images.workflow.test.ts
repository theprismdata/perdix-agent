import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { z } from "zod";
import { HermesAdapter } from "../hermes/adapter.js";
import { startServerConnection } from "../agent/connection.js";
import type { HermesEvent } from "../hermes/events.js";
import { droppedImagePath, imageFromBytes, MAX_IMAGE_BYTES, readImage } from "../hermes/images.js";
import { workflowFixture } from "./acp-fixture.js";

const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGP4z8DAAMIM/4EAAB/uBfsL2WiLAAAAAElFTkSuQmCC", "base64");
const directories: string[] = [];
afterEach(async () => { await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true }))); });

async function imageFile() {
  const directory = await mkdtemp(join(tmpdir(), "perdix-images-"));
  directories.push(directory);
  const path = join(directory, "화면 screenshot.png");
  await writeFile(path, png);
  return { directory, path };
}

describe("image attachments", () => {
  it("round-trips an image through the Python stdio server and session reload", async () => {
    const { directory, path } = await imageFile();
    const script = join(directory, "fake.json");
    const agents = join(directory, "agents.json");
    await writeFile(script, JSON.stringify([{ text: "image accepted" }]));
    await writeFile(agents, "{}");
    const connection = await startServerConnection({
      cwd: directory,
      server: `uv run --project ${join(process.cwd(), "../../server")} perdix-server`,
      serverArgs: ["--provider", "fake", "--fake-script", script, "--db", join(directory, "sessions.db"), "--agents-config", agents],
    });
    const adapter = new HermesAdapter(connection, { cwd: directory });
    const events: HermesEvent[] = [];
    adapter.onEvent = event => events.push(event);
    try {
      const { session_id } = z.object({ session_id: z.string() }).parse(await adapter.request("session.create", {}));
      await adapter.request("image.attach", { session_id, path });
      await adapter.request("prompt.submit", { session_id, text: "" });
      expect(events).toEqual(expect.arrayContaining([expect.objectContaining({ type: "message.delta", payload: { text: "image accepted" } })]));
      const loaded = await connection.loadSession({ sessionId: session_id, cwd: directory });
      const replay: string[] = [];
      loaded.onEvent(event => { if (event.type === "user_text") replay.push(event.text); });
      expect(replay).toContain("[image]");
    } finally { adapter.disconnect(); await connection.close(); }
  }, 20_000);

  it("sends exact image bytes through the real ACP session and consumes them once", async () => {
    const { path } = await imageFile();
    const fixture = await workflowFixture();
    const adapter = new HermesAdapter(fixture.connection, { cwd: process.cwd() });
    try {
      await adapter.request("session.create", {});
      const attached = await adapter.request("image.attach", { session_id: "workflow-session", path: `'${path}'` });
      expect(attached).toMatchObject({ name: "화면 screenshot.png", count: 1 });
      await adapter.request("prompt.submit", { session_id: "workflow-session", text: "이 화면을 설명해줘" });
      expect(fixture.received[0]?.prompt).toEqual([
        { type: "text", text: "이 화면을 설명해줘" }, imageFromBytes(png),
      ]);
      await adapter.request("prompt.submit", { session_id: "workflow-session", text: "다음 질문" });
      expect(fixture.received[1]?.prompt).toEqual([{ type: "text", text: "다음 질문" }]);
    } finally { adapter.disconnect(); await fixture.connection.close(); }
  });

  it("supports image-only clipboard submissions and detaches deleted tokens", async () => {
    const fixture = await workflowFixture();
    const adapter = new HermesAdapter(fixture.connection, {
      cwd: process.cwd(), readClipboardImage: async () => ({ name: "clipboard.png", image: imageFromBytes(png) }),
    });
    try {
      await adapter.request("session.create", {});
      const params = { session_id: "workflow-session" };
      const attached = z.object({ path: z.string() }).parse(await adapter.request("clipboard.paste", params));
      expect(await adapter.request("image.detach", { ...params, path: attached.path })).toMatchObject({ detached: true, count: 0 });
      await expect(adapter.request("prompt.submit", { ...params, text: "" })).rejects.toThrow("메시지나 이미지");
      await adapter.request("clipboard.paste", params);
      await adapter.request("prompt.submit", { ...params, text: "" });
      expect(fixture.received[0]?.prompt).toEqual([imageFromBytes(png)]);
    } finally { adapter.disconnect(); await fixture.connection.close(); }
  });

  it("detects dropped paths with captions without attaching ordinary text", async () => {
    const { path } = await imageFile();
    const fixture = await workflowFixture();
    const adapter = new HermesAdapter(fixture.connection, { cwd: process.cwd() });
    try {
      await adapter.request("session.create", {});
      const params = { session_id: "workflow-session" };
      expect(await adapter.request("input.detect_drop", { ...params, text: "일반 메시지" })).toEqual({ matched: false });
      const dropped = z.object({ text: z.string() }).parse(await adapter.request("input.detect_drop", {
        ...params, text: `'${path}' 이 이미지 분석해줘`,
      }));
      expect(dropped.text).toBe("이 이미지 분석해줘");
      await adapter.request("prompt.submit", { ...params, text: dropped.text });
      expect(fixture.received[0]?.prompt).toEqual([{ type: "text", text: dropped.text }, imageFromBytes(png)]);
      expect(await adapter.request("input.detect_drop", { ...params, text: path })).toMatchObject({ text: "", is_image: true });
    } finally { adapter.disconnect(); await fixture.connection.close(); }
  });

  it("enforces attachment limits and offers file attachment without a paste command", async () => {
    const fixture = await workflowFixture();
    const adapter = new HermesAdapter(fixture.connection, {
      cwd: process.cwd(), readClipboardImage: async () => ({ name: "clipboard.png", image: imageFromBytes(png) }),
    });
    try {
      await adapter.request("session.create", {});
      const params = { session_id: "workflow-session" };
      for (let index = 0; index < 4; index++) await adapter.request("clipboard.paste", params);
      await expect(adapter.request("clipboard.paste", params)).rejects.toThrow("4장");
      const catalog = z.object({ canon: z.record(z.string(), z.string()) }).parse(await adapter.request("commands.catalog", params));
      expect(catalog.canon["/image"]).toBe("/image");
      expect(catalog.canon["/paste"]).toBeUndefined();
      expect(await adapter.request("image.list", params)).toMatchObject({ images: Array.from({ length: 4 }, () => ({ name: "clipboard.png" })) });
    } finally { adapter.disconnect(); await fixture.connection.close(); }
  });

  it("rejects missing, oversized, non-file and disguised non-image inputs", async () => {
    const { directory, path } = await imageFile();
    expect(await readImage(pathToFileURL(path).href, directory)).toMatchObject({ image: imageFromBytes(png) });
    expect(await readImage("화면 screenshot.png", directory)).toMatchObject({ image: imageFromBytes(png) });
    await expect(readImage("missing.png", directory)).rejects.toThrow();
    await expect(readImage(directory, directory)).rejects.toThrow("일반 이미지 파일");
    await writeFile(path, "not an image");
    await expect(readImage(path, directory)).rejects.toThrow("PNG, JPEG");
    await writeFile(path, Buffer.alloc(MAX_IMAGE_BYTES + 1));
    await expect(readImage(path, directory)).rejects.toThrow("5 MiB");
    expect(() => imageFromBytes(Buffer.alloc(0))).toThrow("5 MiB");
  });

  it("recognizes image MIME types from bytes and parses terminal path quoting", () => {
    expect(imageFromBytes(Buffer.from([255, 216, 255, 0])).mimeType).toBe("image/jpeg");
    expect(imageFromBytes(Buffer.from("GIF89a")).mimeType).toBe("image/gif");
    expect(imageFromBytes(Buffer.from("RIFF0000WEBP")).mimeType).toBe("image/webp");
    expect(droppedImagePath("/tmp/my\\ image.png caption")).toEqual({ path: "/tmp/my image.png", remainder: "caption" });
    expect(droppedImagePath('"C:\\Users\\test\\my image.png" caption')).toEqual({ path: "C:\\Users\\test\\my image.png", remainder: "caption" });
    expect(droppedImagePath("hello world")).toBeNull();
  });
});
