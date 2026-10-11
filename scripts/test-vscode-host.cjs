/* VS Code --extensionTestsPath로 실행하는 실제 확장 호스트 검증. */
const assert = require('node:assert/strict');
const vscode = require('vscode');
exports.run = async function () {
  const extension = vscode.extensions.getExtension('perdix.perdix-vscode');
  assert.ok(extension, 'Extension not registered');
  const count = vscode.window.terminals.length;
  await extension.activate();
  assert.ok(extension.isActive, 'Extension activation failed');
  const commands = await vscode.commands.getCommands(true);
  assert.ok(commands.includes('perdix.open'));
  assert.ok(commands.includes('perdix.chat.focus'));
  await vscode.commands.executeCommand('perdix.open');
  assert.equal(vscode.window.terminals.length, count, 'Webview should not create a terminal');
  assert.equal(extension.packageJSON.contributes.views.perdix[0].type, 'webview');
  console.log('PASS: actual VS Code extension activation, Webview focus, no terminal creation');
};
