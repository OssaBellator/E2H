"use strict";

const crypto = require("node:crypto");
const path = require("node:path");
const vscode = require("vscode");

const { buildVSCodeCapture } = require("./capture.cjs");

function resourceHint(document) {
  const label = path.basename(document.fileName || "untitled") || "untitled";
  const folder = vscode.workspace.getWorkspaceFolder(document.uri);
  if (folder) {
    const relative = vscode.workspace.asRelativePath(document.uri, false).replaceAll("\\", "/");
    return { label, locator: relative || label };
  }
  if (document.uri.scheme === "file") {
    return { label, locator: label };
  }
  return { label, locator: `${document.uri.scheme}:${label}` };
}

function selectionMetadata(selection) {
  return {
    start: { line: selection.start.line, character: selection.start.character },
    end: { line: selection.end.line, character: selection.end.character },
  };
}

async function captureSelection() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    await vscode.window.showWarningMessage("E2H Capture: no active text editor.");
    return;
  }
  const content = editor.document.getText(editor.selection);
  if (content.trim().length === 0) {
    await vscode.window.showWarningMessage("E2H Capture: select editor text before capturing.");
    return;
  }
  const source = resourceHint(editor.document);
  const capturedAt = new Date().toISOString();
  const capture = buildVSCodeCapture({
    content,
    label: source.label,
    locator: source.locator,
    languageId: editor.document.languageId,
    selection: selectionMetadata(editor.selection),
    capturedAt,
    uuid: crypto.randomUUID(),
  });
  const filename = `e2h-vscode-capture-${capturedAt.replaceAll(":", "-")}.json`;
  const selected = await vscode.window.showSaveDialog({
    title: "Save E2H capture",
    saveLabel: "Save capture",
    filters: { "E2H capture": ["json"] },
    defaultUri: vscode.workspace.workspaceFolders?.[0]
      ? vscode.Uri.joinPath(vscode.workspace.workspaceFolders[0].uri, filename)
      : undefined,
  });
  if (!selected) {
    return;
  }
  const rendered = Buffer.from(`${JSON.stringify(capture, null, 2)}\n`, "utf8");
  await vscode.workspace.fs.writeFile(selected, rendered);
  await vscode.window.showInformationMessage("E2H Capture: selection exported locally.");
}

function activate(context) {
  context.subscriptions.push(vscode.commands.registerCommand("e2h.captureSelection", captureSelection));
}

function deactivate() {}

module.exports = { activate, deactivate };
