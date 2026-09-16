// Cloud copy of each capture (productization plan §6). The local backend
// stays the source of truth for the panel: this copy is fire-and-forget,
// never delays or breaks the local call, and is off until a cloud URL and an
// admin token are saved on the options page.
//
// Captures the cloud can't take right now (offline, 5xx, expired token) wait
// in a queue in chrome.storage.local and are retried in order on the next
// capture and on a periodic alarm. A capture the cloud rejects as invalid
// (other 4xx) is dropped so it can't block the queue forever.
//
// Loaded by background.js via importScripts, and by Node for tests.

(function (root) {
  const QUEUE_KEY = "cloudQueue";
  const MAX_QUEUE = 300; // raw_text is a few KB; stays well under storage.local's 10MB
  const TIMEOUT_MS = 15000;
  const RETRY_STATUSES = new Set([401, 403, 408, 429]);

  // Every read-modify-write of the queue runs one at a time.
  let chain = Promise.resolve();
  function serialized(fn) {
    const run = chain.then(fn, fn);
    chain = run.catch(() => {});
    return run;
  }

  async function getConfig(storage) {
    const { cloudApiBase, cloudToken } = await storage.local.get(["cloudApiBase", "cloudToken"]);
    if (!cloudApiBase || !cloudToken) return null;
    return { base: cloudApiBase.replace(/\/+$/, ""), token: cloudToken };
  }

  async function queuedCaptures(storage) {
    const data = await storage.local.get([QUEUE_KEY]);
    return data[QUEUE_KEY] || [];
  }

  async function enqueueCapture(storage, capture) {
    // A revisit replaces the older queued copy of the same job.
    const queue = (await queuedCaptures(storage)).filter((c) => c.job_id !== capture.job_id);
    queue.push(capture);
    await storage.local.set({ [QUEUE_KEY]: queue.slice(-MAX_QUEUE) });
  }

  async function post(fetchFn, config, capture) {
    const controller = typeof AbortController === "function" ? new AbortController() : null;
    const timer = controller && setTimeout(() => controller.abort(), TIMEOUT_MS);
    try {
      const response = await fetchFn(`${config.base}/api/v1/admin/captures`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${config.token}` },
        body: JSON.stringify(capture),
        signal: controller ? controller.signal : undefined,
      });
      if (response.ok) return "sent";
      if (response.status >= 400 && response.status < 500 && !RETRY_STATUSES.has(response.status)) return "rejected";
      return "retry";
    } catch (_err) {
      return "retry";
    } finally {
      if (timer) clearTimeout(timer);
    }
  }

  // Sends queued captures oldest first, stopping at the first one that must
  // be retried so order is kept. Returns {sent, outcomes: {job_id: outcome}}.
  async function drain(storage, fetchFn, config) {
    const outcomes = {};
    let sent = 0;
    for (;;) {
      const queue = await queuedCaptures(storage);
      if (!queue.length) break;
      const outcome = await post(fetchFn, config, queue[0]);
      if (outcome === "retry") break;
      outcomes[queue[0].job_id] = outcome;
      if (outcome === "sent") sent++;
      await storage.local.set({ [QUEUE_KEY]: queue.slice(1) });
    }
    return { sent, outcomes };
  }

  function flushQueue(storage, fetchFn) {
    return serialized(async () => {
      const config = await getConfig(storage);
      return config ? (await drain(storage, fetchFn, config)).sent : 0;
    });
  }

  // Returns "disabled" | "sent" | "rejected" | "queued" | "error"; never throws.
  function copyCaptureToCloud(storage, fetchFn, capture) {
    return serialized(async () => {
      const config = await getConfig(storage);
      if (!config) return "disabled";
      await enqueueCapture(storage, capture);
      const { outcomes } = await drain(storage, fetchFn, config);
      return outcomes[capture.job_id] || "queued";
    }).catch(() => "error");
  }

  const api = { MAX_QUEUE, copyCaptureToCloud, flushQueue, enqueueCapture, queuedCaptures, getConfig };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.JhiCloudSync = api;
})(typeof self !== "undefined" ? self : this);
