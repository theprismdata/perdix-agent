import { existsSync } from "node:fs";
import { join } from "node:path";
import { connectToServer, type PerdixConnection } from "../../cli/src/agent/connection.js";
import { findOnPath, spawnServer, type ServerCommand } from "../../cli/src/agent/spawn.js";

export function resolveExtensionServer(
  configuredPath: string,
  env: NodeJS.ProcessEnv = process.env,
  platform = process.platform,
): ServerCommand {
  if (configuredPath) {
    if (!existsSync(configuredPath)) throw new Error("설정한 서버 파일이 없습니다. Perdix 서버 설정을 확인하세요.");
    return { command: configuredPath, args: [], source: "flag" };
  }
  if (platform === "win32" && env.LOCALAPPDATA) {
    const bundled = join(env.LOCALAPPDATA, "Programs", "Perdix", "server", "perdix-server.exe");
    if (existsSync(bundled)) return { command: bundled, args: [], source: "bundled" };
  }
  const onPath = findOnPath("perdix-server", env);
  if (onPath) return { command: onPath, args: [], source: "path" };
  throw new Error("Perdix 서버를 찾지 못했습니다. Windows 설치 프로그램을 설치하거나 '서버 설정'에서 실행 파일을 지정하세요.");
}

export async function openExtensionConnection(cwd: string, serverPath: string): Promise<PerdixConnection> {
  const server = spawnServer(resolveExtensionServer(serverPath), { cwd });
  try { return await connectToServer(server); }
  catch (error) { server.kill(); throw error; }
}
