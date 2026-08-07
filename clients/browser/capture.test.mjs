import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { buildBrowserCapture, sha256Hex } from "./capture.mjs";

const capturedAt = "2026-08-07T09:00:00.000Z";
const uuid = "123e4567-e89b-12d3-a456-426614174000";

test("browser builder emits verifiable selection-only envelope", async () => {
  const documentValue = await buildBrowserCapture({
    selection: "observable selection",
    title: "Example",
    origin: "https://example.com",
    capturedAt,
    uuid,
  });
  assert.equal(documentValue.schema_version, "0.1");
  assert.equal(documentValue.client, "browser");
  assert.equal(documentValue.observations.length, 1);
  assert.equal(documentValue.observations[0].kind, "browser.selection");
  assert.equal(documentValue.observations[0].source.locator, "https://example.com");
  assert.equal(
    documentValue.observations[0].content_sha256,
    await sha256Hex("observable selection"),
  );
  assert.equal(documentValue.metadata.persistent_host_permissions, false);
});

test("browser builder rejects empty selections", async () => {
  await assert.rejects(
    buildBrowserCapture({
      selection: "   ",
      title: "Example",
      origin: "https://example.com",
      capturedAt,
      uuid,
    }),
    /Select visible page text/,
  );
});

test("browser manifest has no persistent host permission", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("./manifest.json", import.meta.url), { encoding: "utf8" }),
  );
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions, ["activeTab", "scripting", "downloads"]);
  assert.equal("host_permissions" in manifest, false);
});
