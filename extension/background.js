// Service worker — the only thing in this extension that talks to the
// local backend. Content-script fetch() calls are subject to the visited
// page's CSP (which LinkedIn may not allowlist 127.0.0.1 under), while a
// service worker's fetch is governed only by manifest host_permissions —
// so all network I/O is relayed through here via chrome.runtime.sendMessage.
//
// Also owns the SPA soft-navigation fallback: MV3 declarative
// content_scripts inject on a real document load, not on LinkedIn's own
// client-side pushState routing (e.g. clicking "Jobs" from the feed without
// a full page load) — onHistoryStateUpdated re-injects in that case.
// content_script.js's own __jhiInjected guard makes this a harmless no-op
// when the declarative injection already ran.

const API_BASE = "http://127.0.0.1:5050";

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "LOOKUP_JOB") {
    fetch(`${API_BASE}/api/extension/jobs/${encodeURIComponent(msg.payload.jobId)}`)
      .then((r) => r.json().then((data) => sendResponse({ ok: r.ok, status: r.status, data })))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (msg.type === "SCORE_JOB") {
    fetch(`${API_BASE}/api/extension/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(msg.payload),
    })
      .then((r) => r.json().then((data) => sendResponse({ ok: r.ok, status: r.status, data })))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true; // keep the message channel open for the async response
  }

  if (msg.type === "MARK_APPLIED") {
    // payload.version is "v1"/"v2" — which resume went out with this
    // application, for the August 2026 resume A/B test (CONTEXT.md
    // "Resume A/B Test"). Always required by this point; content_script.js
    // only sends MARK_APPLIED once a version button was actually clicked.
    fetch(`${API_BASE}/api/jobs/${msg.payload.jobId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ applied: true, applied_resume_version: msg.payload.version }),
    })
      .then((r) => r.json().then((data) => sendResponse({ ok: r.ok, data })))
      .catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  return false;
});

chrome.webNavigation.onHistoryStateUpdated.addListener(
  (details) => {
    chrome.scripting.executeScript({
      target: { tabId: details.tabId },
      files: ["content/extract.js", "content/panel.js", "content/content_script.js"],
    });
  },
  { url: [{ hostContains: "linkedin.com" }] }
);
