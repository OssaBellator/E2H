# Browser and VS Code capture clients

E2H includes two deliberately narrow first-party capture clients for turning text a user explicitly selects into a portable, locally saved evidence artifact.

Both clients emit the same `CaptureDocument` JSON envelope. Every observation includes the SHA-256 of the exact UTF-8 selection, and `e2h capture validate` recomputes those digests before accepting a document.

The clients do not upload captures, call a model, execute captured text, or monitor activity in the background.

## Shared capture envelope

A minimal capture looks like:

```json
{
  "schema_version": "0.1",
  "id": "browser-123e4567-e89b-12d3-a456-426614174000",
  "capsule_id": "unassigned",
  "client": "browser",
  "captured_at": "2026-08-07T09:00:00.000Z",
  "observations": [
    {
      "id": "selection-123e4567-e89b-12d3-a456-426614174000",
      "kind": "browser.selection",
      "captured_at": "2026-08-07T09:00:00.000Z",
      "content": "selected visible text",
      "content_sha256": "...",
      "source": {
        "label": "Example page",
        "locator": "https://example.com"
      },
      "metadata": {
        "capture_mode": "manual-selection",
        "character_count": 21
      }
    }
  ],
  "metadata": {
    "capture_mode": "manual-selection"
  }
}
```

The browser client uses `browser.selection`; the VS Code client uses `vscode.selection`. A capture document cannot mix an observation kind from another client.

The document itself can be content-addressed after normalization:

```bash
uv run e2h capture validate path/to/capture.json --json
```

Inspect metadata and hashes without printing the captured text:

```bash
uv run e2h capture inspect path/to/capture.json
```

Generate the JSON Schema:

```bash
uv run e2h capture schema --output .e2h/capture.schema.json
```

Capture documents are ordinary local artifacts. They can be placed in an E2H snapshot or referenced by later harness/context workflows without pretending that selected page/editor text was a chat message.

## Browser client

The unpacked Manifest V3 extension is under `clients/browser/`.

It requests only:

- `activeTab` — temporary access to the tab after the user invokes the extension;
- `scripting` — to read the current selection/title/origin from that active tab;
- `downloads` — to open a local Save As flow for the resulting JSON document.

It has no `host_permissions` entry, no content script that runs automatically, no storage permission, and no remotely hosted code. Chrome documents `activeTab` as temporary access triggered by a user action, which is a better fit for an explicit clipping/capture workflow than persistent access to every site. The MV3 scripting API can use that temporary grant. citeturn597018search1turn597018search10

### Load for local development

1. Open Chrome's extensions page and enable Developer mode.
2. Choose **Load unpacked**.
3. Select `clients/browser/`.
4. Select visible text on a normal web page.
5. Click the **E2H Capture** toolbar action and choose **Capture selection**.
6. Save the JSON file when Chrome opens the Save As dialog.

The exported locator is the page origin only. Query strings, fragments, and full paths are intentionally not copied into the default capture.

Some browser-internal or otherwise restricted pages do not allow extension script injection; the popup reports the browser error instead of attempting a broader permission request.

## VS Code client

The extension source is under `clients/vscode/` and has no runtime npm dependencies beyond the VS Code extension host itself.

It contributes one command:

```text
E2H: Capture Selection as Evidence
```

The command is also available from the editor context menu when a selection exists. VS Code can infer command activation for contributed commands on supported versions, so this extension does not activate at startup and does not declare a broad `*` activation event. citeturn296390search0turn296390search1

When invoked, the extension:

1. reads only the active editor's current selection;
2. records the language identifier and selection range;
3. uses a workspace-relative file path when the document belongs to a workspace;
4. falls back to a filename-only hint for files outside a workspace, avoiding an absolute local path in the capture;
5. hashes the exact selected text;
6. opens VS Code's Save dialog and writes the capture through `workspace.fs`.

The extension does not read neighboring file contents, enumerate the workspace, capture terminal output, observe edits, or run in the background.

### Run in an Extension Development Host

Open `clients/vscode/` as the extension project in VS Code and launch an Extension Development Host using the normal extension-development workflow. The manifest entry point is `extension.js`, so no build step is required for this repository version.

## Client tests

The capture-client workflow uses Node's built-in test runner and has no npm install step:

```bash
node --test clients/browser/capture.test.mjs clients/vscode/capture.test.mjs
```

The tests assert the generated envelope and content hashes, reject empty selections, ensure the browser manifest has no persistent host permissions, and ensure the VS Code manifest contributes only the explicit capture command.

Python tests separately validate that client-shaped documents satisfy E2H's strict capture model and detect content tampering.

## Privacy boundary

A valid SHA-256 proves only that the capture file's `content` still matches the bytes that its client hashed. It does not establish that the selected text is true, complete, authorized for redistribution, or safe to execute.

Captures can contain secrets or personal data because users may select sensitive text. Treat raw capture files as source evidence: store them deliberately, snapshot them only when appropriate, and do not infer that validation makes their content public or trustworthy.
