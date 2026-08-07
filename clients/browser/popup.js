import { buildBrowserCapture } from "./capture.mjs";

const button = document.querySelector("#capture");
const status = document.querySelector("#status");

function collectSelection() {
  return {
    selection: window.getSelection()?.toString() ?? "",
    title: document.title,
    origin: location.origin,
  };
}

function filenameFor(timestamp) {
  return `e2h-browser-capture-${timestamp.replaceAll(":", "-")}.json`;
}

async function saveCapture(documentValue) {
  const rendered = `${JSON.stringify(documentValue, null, 2)}\n`;
  const dataUrl = `data:application/json;charset=utf-8,${encodeURIComponent(rendered)}`;
  await chrome.downloads.download({
    url: dataUrl,
    filename: filenameFor(documentValue.captured_at),
    saveAs: true,
  });
}

async function captureSelection() {
  button.disabled = true;
  status.textContent = "Reading the current selection…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      throw new Error("No active page is available.");
    }
    const [execution] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: collectSelection,
    });
    const observed = execution?.result;
    if (!observed) {
      throw new Error("The active page did not return a selection.");
    }
    const capturedAt = new Date().toISOString();
    const capture = await buildBrowserCapture({
      ...observed,
      capturedAt,
      uuid: crypto.randomUUID(),
    });
    await saveCapture(capture);
    status.textContent = "Capture exported locally.";
  } catch (error) {
    status.textContent = error instanceof Error ? error.message : "Unable to capture selection.";
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", captureSelection);
