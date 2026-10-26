// Electron 메인 프로세스: perdix-gateway를 자식 프로세스로 띄우고
// 그 로컬 대시보드를 BrowserWindow로 감싼다. ADR 0035 참고.
// 새 UI 코드는 두지 않는다 — server/src/perdix/gateway/static을 그대로 로드한다.

const { app, BrowserWindow } = require("electron");
const { spawn } = require("node:child_process");
const { existsSync, mkdirSync, writeFileSync } = require("node:fs");
const { join, dirname } = require("node:path");
const http = require("node:http");

const PORT = 18792;
const HOST = "127.0.0.1";

function findRepoRoot(start) {
  let dir = start;
  for (;;) {
    if (existsSync(join(dir, "server", "pyproject.toml"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) return null;
    dir = parent;
  }
}

const repoRoot = findRepoRoot(__dirname);
if (!repoRoot) {
  throw new Error("server/pyproject.toml을 찾지 못했습니다 (개발 모드 전용 실행기입니다).");
}

const userData = app.getPath("userData");
mkdirSync(userData, { recursive: true });
const configPath = join(userData, "gateway-config.json");
writeFileSync(
  configPath,
  JSON.stringify(
    {
      host: HOST,
      port: PORT,
      auth: "none",
      db_path: join(userData, "gateway.db"),
      projects: {
        "perdix-agent": { cwd: repoRoot, trust_project: false },
      },
    },
    null,
    2,
  ),
);

let gatewayProcess = null;

function spawnGateway() {
  gatewayProcess = spawn("uv", ["run", "--project", join(repoRoot, "server"), "perdix-gateway", "--config", configPath], {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
  });
  gatewayProcess.stdout.on("data", (chunk) => process.stdout.write(`[gateway] ${chunk}`));
  gatewayProcess.stderr.on("data", (chunk) => process.stderr.write(`[gateway] ${chunk}`));
}

function waitForPort(host, port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const req = http.get({ host, port, path: "/", timeout: 1000 }, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() > deadline) return reject(new Error("gateway did not start in time"));
        setTimeout(attempt, 300);
      });
    };
    attempt();
  });
}

async function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 900,
    title: "Perdix",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  await win.loadURL(`http://${HOST}:${PORT}/`);
}

app.whenReady().then(async () => {
  spawnGateway();
  try {
    await waitForPort(HOST, PORT, 20000);
  } catch (err) {
    console.error(err);
  }
  await createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (gatewayProcess && gatewayProcess.exitCode === null) gatewayProcess.kill("SIGTERM");
});
