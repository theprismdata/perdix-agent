import * as vscode from "vscode";
import { randomBytes } from "node:crypto";
import { ChatController, chatMessage } from "./chat-controller.js";
import { openExtensionConnection } from "./server-connection.js";
import { chatHtml } from "./webview.js";
import { DEFAULT_CREDENTIALS_PATH, ensureCredentialsFile } from "../../cli/src/agent/credentials.js";
import { readDefaultModel } from "../../cli/src/agent/preferences.js";

class ChatView implements vscode.WebviewViewProvider, vscode.Disposable {
  private view?: vscode.WebviewView;
  private readonly controller: ChatController;

  constructor(private readonly context: vscode.ExtensionContext) {
    const defaultModel = readDefaultModel();
    const initialCwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "작업 폴더를 열어 주세요";
    this.controller = new ChatController({
      initialInfo: {
        sessionId: "",
        cwd: initialCwd,
        currentMode: "chat",
        modes: [{ id: "chat", name: "기본 에이전트 · Chat" }],
        currentModel: defaultModel ?? null,
        models: defaultModel ? [{ value: defaultModel, name: defaultModel }] : [],
      },
      post: (message) => { void this.view?.webview.postMessage(message); },
      connect: async (sessionId) => {
        const cwd = this.workspacePath();
        const connection = await openExtensionConnection(cwd, this.serverPath(), vscode.workspace.isTrusted);
        try {
          return sessionId
            ? await connection.loadSession({ cwd, sessionId })
            : await connection.newSession({ cwd, model: defaultModel, mode: "chat" });
        } catch (error) { await connection.close(); throw error; }
      },
    });
  }

  private serverPath(): string {
    return vscode.workspace.getConfiguration("perdix").get<string>("serverPath", "");
  }

  private workspacePath(): string {
    if (!vscode.workspace.isTrusted) throw new Error("먼저 VS Code에서 이 작업 폴더를 신뢰해 주세요.");
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder || folder.uri.scheme !== "file") throw new Error("작업할 폴더를 먼저 열어 주세요.");
    return folder.uri.fsPath;
  }

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    const media = vscode.Uri.joinPath(this.context.extensionUri, "media");
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    const uri = (file: string) => view.webview.asWebviewUri(vscode.Uri.joinPath(media, file)).toString();
    view.webview.html = chatHtml(view.webview.cspSource, uri("chat.js"), uri("chat.css"), randomBytes(18).toString("hex"));
    const receiver = view.webview.onDidReceiveMessage((raw: unknown) => {
      void this.handle(raw).catch((error: unknown) => this.controller.error(error));
    });
    view.onDidDispose(() => {
      receiver.dispose();
      if (this.view === view) this.view = undefined;
    });
  }

  private async handle(raw: unknown): Promise<void> {
    const parsed = chatMessage.safeParse(raw);
    if (!parsed.success) return;
    const message = parsed.data;
    switch (message.type) {
      case "ready": await this.controller.initialize(); break;
      case "send": await this.controller.send(message.text); break;
      case "cancel": await this.controller.cancel(); break;
      case "new": await this.controller.reset(); break;
      case "permission": this.controller.answer(message.requestId, message.optionId); break;
      case "mode": case "model": await this.controller.configure(message.type, message.value); break;
      case "folder": await vscode.commands.executeCommand("vscode.openFolder"); break;
      case "settings": await vscode.commands.executeCommand("workbench.action.openSettings", "perdix.serverPath"); break;
      case "keys": {
        ensureCredentialsFile();
        const document = await vscode.workspace.openTextDocument(vscode.Uri.file(DEFAULT_CREDENTIALS_PATH));
        await vscode.window.showTextDocument(document);
        break;
      }
      case "history": {
        if (this.controller.isBusy) return;
        const cwd = this.workspacePath();
        const connection = await openExtensionConnection(cwd, this.serverPath());
        try {
          const sessions = await connection.listSessions(cwd);
          if (!sessions.length) { void vscode.window.showInformationMessage("저장된 대화가 없습니다."); break; }
          const selected = await vscode.window.showQuickPick(sessions.map((s) => ({
            label: s.title || s.sessionId, description: s.updatedAt ?? undefined, sessionId: s.sessionId,
          })), { title: "Perdix 대화 기록" });
          if (selected) await this.controller.reset(selected.sessionId);
        } finally { await connection.close(); }
        break;
      }
    }
  }

  dispose(): void { void this.controller.dispose().catch(() => {}); }
}

export function activate(context: vscode.ExtensionContext): void {
  const provider = new ChatView(context);
  context.subscriptions.push(provider,
    vscode.window.registerWebviewViewProvider("perdix.chat", provider),
    vscode.commands.registerCommand("perdix.open", () => vscode.commands.executeCommand("perdix.chat.focus")),
  );
}

export function deactivate(): void {}
