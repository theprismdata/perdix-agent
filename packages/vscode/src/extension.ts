import * as vscode from "vscode";
import { buildTerminalEnv } from "./terminal.js";

export function activate(context: vscode.ExtensionContext): void {
  const disposable = vscode.commands.registerCommand("perdix.open", () => {
    const terminal = vscode.window.createTerminal({
      name: "Perdix",
      env: buildTerminalEnv(),
    });
    terminal.show();
    terminal.sendText("perdix");
  });
  context.subscriptions.push(disposable);
}

export function deactivate(): void {}
