import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import test from "node:test";

const require = createRequire(import.meta.url);
const { buildVSCodeCapture, sha256Hex } = require("./capture.cjs");

const capturedAt = "2026-08-07T09:00:00.000Z";
const uuid = "123e4567-e89b-12d3-a456-426614174000";

test("VS Code builder emits verifiable selection-only envelope", () => {
  const documentValue = buildVSCodeCapture({
    content: "selected code",
    label: "example.py",
    locator: "src/example.py",
    languageId: "python",
    selection: {
      start: { line: 1, character: 2 },
      end: { line: 2, character: 4 },
    },
    capturedAt,
    uuid,
  });
  assert.equal(documentValue.schema_version, "0.1");
  assert.equal(documentValue.client, "vscode");
  assert.equal(documentValue.observations.length, 1);
  assert.equal(documentValue.observations[0].kind, "vscode.selection");
  assert.equal(documentValue.observations[0].source.locator, "src/example.py");
  assert.equal(documentValue.observations[0].content_sha256, sha256Hex("selected code"));
  assert.equal(documentValue.metadata.background_capture, false);
});

test("VS Code builder rejects empty selections", () => {
  assert.throws(
    () =>
      buildVSCodeCapture({
        content: "\n\t",
        label: "example.py",
        locator: "src/example.py",
        languageId: "python",
        selection: {},
        capturedAt,
        uuid,
      }),
    /Select editor text/,
  );
});

test("VS Code manifest contributes only the explicit capture command", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("./package.json", import.meta.url), { encoding: "utf8" }),
  );
  assert.equal(manifest.main, "./extension.js");
  assert.equal(manifest.engines.vscode, "^1.74.0");
  assert.equal(manifest.contributes.commands.length, 1);
  assert.equal(manifest.contributes.commands[0].command, "e2h.captureSelection");
  assert.equal("activationEvents" in manifest, false);
});
