"use strict";

const crypto = require("node:crypto");

const MAX_LABEL_CHARS = 255;
const MAX_LOCATOR_CHARS = 4096;

function bounded(value, limit, fallback) {
  const text = String(value ?? "").trim();
  return (text || fallback).slice(0, limit);
}

function sha256Hex(text) {
  return crypto.createHash("sha256").update(text, "utf8").digest("hex");
}

function buildVSCodeCapture({ content, label, locator, languageId, selection, capturedAt, uuid }) {
  if (typeof content !== "string" || content.trim().length === 0) {
    throw new Error("Select editor text before capturing.");
  }
  if (typeof capturedAt !== "string" || !capturedAt) {
    throw new Error("capturedAt is required.");
  }
  if (typeof uuid !== "string" || !uuid) {
    throw new Error("uuid is required.");
  }
  return {
    schema_version: "0.1",
    id: `vscode-${uuid}`,
    capsule_id: "unassigned",
    client: "vscode",
    captured_at: capturedAt,
    observations: [
      {
        id: `selection-${uuid}`,
        kind: "vscode.selection",
        captured_at: capturedAt,
        content,
        content_sha256: sha256Hex(content),
        source: {
          label: bounded(label, MAX_LABEL_CHARS, "Untitled editor"),
          locator: bounded(locator, MAX_LOCATOR_CHARS, "untitled"),
        },
        metadata: {
          capture_mode: "manual-selection",
          language_id: bounded(languageId, 255, "plaintext"),
          selection,
          character_count: content.length,
        },
      },
    ],
    metadata: {
      capture_mode: "manual-selection",
      background_capture: false,
    },
  };
}

module.exports = { buildVSCodeCapture, sha256Hex };
