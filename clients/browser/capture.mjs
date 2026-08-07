const MAX_LABEL_CHARS = 255;
const MAX_LOCATOR_CHARS = 4096;

function bounded(value, limit, fallback) {
  const text = String(value ?? "").trim();
  return (text || fallback).slice(0, limit);
}

export async function sha256Hex(text) {
  const bytes = new TextEncoder().encode(text);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function buildBrowserCapture({ selection, title, origin, capturedAt, uuid }) {
  if (typeof selection !== "string" || selection.trim().length === 0) {
    throw new Error("Select visible page text before capturing.");
  }
  if (typeof capturedAt !== "string" || !capturedAt) {
    throw new Error("capturedAt is required.");
  }
  if (typeof uuid !== "string" || !uuid) {
    throw new Error("uuid is required.");
  }
  const digest = await sha256Hex(selection);
  return {
    schema_version: "0.1",
    id: `browser-${uuid}`,
    capsule_id: "unassigned",
    client: "browser",
    captured_at: capturedAt,
    observations: [
      {
        id: `selection-${uuid}`,
        kind: "browser.selection",
        captured_at: capturedAt,
        content: selection,
        content_sha256: digest,
        source: {
          label: bounded(title, MAX_LABEL_CHARS, "Untitled page"),
          locator: bounded(origin, MAX_LOCATOR_CHARS, "unknown-origin"),
        },
        metadata: {
          capture_mode: "manual-selection",
          character_count: selection.length,
        },
      },
    ],
    metadata: {
      capture_mode: "manual-selection",
      persistent_host_permissions: false,
    },
  };
}
