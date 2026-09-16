// Tests for extension/cloud_sync.js: the cloud copy of each capture.
// The local capture flow must never wait on or break because of the cloud.
// Run: node tests_and_eval/extension_cloud_sync_test.js
const assert = require("assert");
const path = require("path");
const sync = require(path.join(__dirname, "..", "extension", "cloud_sync.js"));

function fakeStorage(config = {}) {
  const local = { ...config };
  return {
    local: {
      get: async (keys) => Object.fromEntries(keys.map((k) => [k, local[k]])),
      set: async (obj) => Object.assign(local, obj),
    },
    _local: local,
  };
}

function fakeFetch(responses) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    const next = responses.length ? responses.shift() : { status: 200 };
    if (next.throw) throw new Error("network down");
    return { ok: next.status >= 200 && next.status < 300, status: next.status };
  };
  fn.calls = calls;
  return fn;
}

const CONFIG = { cloudApiBase: "https://api.example.com", cloudToken: "jhi_secret" };
const capture = (id) => ({ job_id: id, title: "ML Engineer", raw_text: "text" });
const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("does nothing until a cloud URL and token are configured", async () => {
  const storage = fakeStorage({});
  const fetch = fakeFetch([]);
  assert.strictEqual(await sync.copyCaptureToCloud(storage, fetch, capture("1")), "disabled");
  assert.strictEqual(fetch.calls.length, 0);
});

test("sends the capture with the admin token", async () => {
  const storage = fakeStorage(CONFIG);
  const fetch = fakeFetch([{ status: 200 }]);
  assert.strictEqual(await sync.copyCaptureToCloud(storage, fetch, capture("1")), "sent");
  const call = fetch.calls[0];
  assert.strictEqual(call.url, "https://api.example.com/api/v1/admin/captures");
  assert.strictEqual(call.init.method, "POST");
  assert.strictEqual(call.init.headers.Authorization, "Bearer jhi_secret");
  assert.deepStrictEqual(JSON.parse(call.init.body), capture("1"));
  assert.deepStrictEqual(await sync.queuedCaptures(storage), []);
});

test("an unreachable cloud keeps the capture queued, then sends it in order later", async () => {
  const storage = fakeStorage(CONFIG);
  await sync.copyCaptureToCloud(storage, fakeFetch([{ throw: true }]), capture("1"));
  await sync.copyCaptureToCloud(storage, fakeFetch([{ status: 503 }]), capture("2"));
  assert.deepStrictEqual((await sync.queuedCaptures(storage)).map((c) => c.job_id), ["1", "2"]);

  const later = fakeFetch([{ status: 200 }, { status: 200 }]);
  assert.strictEqual(await sync.flushQueue(storage, later), 2);
  assert.deepStrictEqual(later.calls.map((c) => JSON.parse(c.init.body).job_id), ["1", "2"]);
  assert.deepStrictEqual(await sync.queuedCaptures(storage), []);
});

test("a flush stops at the first failure so order is kept", async () => {
  const storage = fakeStorage(CONFIG);
  for (const id of ["1", "2", "3"]) await sync.copyCaptureToCloud(storage, fakeFetch([{ throw: true }]), capture(id));
  const flaky = fakeFetch([{ status: 200 }, { throw: true }]);
  assert.strictEqual(await sync.flushQueue(storage, flaky), 1);
  assert.deepStrictEqual((await sync.queuedCaptures(storage)).map((c) => c.job_id), ["2", "3"]);
});

test("a capture the cloud rejects (4xx) is dropped, not retried forever", async () => {
  const storage = fakeStorage(CONFIG);
  assert.strictEqual(await sync.copyCaptureToCloud(storage, fakeFetch([{ status: 400 }]), capture("1")), "rejected");
  assert.deepStrictEqual(await sync.queuedCaptures(storage), []);
});

test("an expired or revoked token (401/403) keeps captures queued for when it is fixed", async () => {
  const storage = fakeStorage(CONFIG);
  assert.strictEqual(await sync.copyCaptureToCloud(storage, fakeFetch([{ status: 401 }]), capture("1")), "queued");
  assert.deepStrictEqual((await sync.queuedCaptures(storage)).map((c) => c.job_id), ["1"]);
});

test("revisiting the same job keeps one queued copy, the newest", async () => {
  const storage = fakeStorage(CONFIG);
  await sync.copyCaptureToCloud(storage, fakeFetch([{ throw: true }]), { ...capture("1"), title: "old" });
  await sync.copyCaptureToCloud(storage, fakeFetch([{ throw: true }, { throw: true }]), { ...capture("1"), title: "new" });
  const queued = await sync.queuedCaptures(storage);
  assert.deepStrictEqual(queued.map((c) => [c.job_id, c.title]), [["1", "new"]]);
});

test("the queue is capped, dropping the oldest", async () => {
  const storage = fakeStorage(CONFIG);
  for (let i = 0; i < sync.MAX_QUEUE + 5; i++) {
    await sync.enqueueCapture(storage, capture(String(i)));
  }
  const queued = await sync.queuedCaptures(storage);
  assert.strictEqual(queued.length, sync.MAX_QUEUE);
  assert.strictEqual(queued[0].job_id, "5");
});

test("never throws, even when storage and fetch both fail", async () => {
  const broken = {
    local: { get: async () => { throw new Error("storage broken"); }, set: async () => { throw new Error("storage broken"); } },
  };
  assert.strictEqual(await sync.copyCaptureToCloud(broken, fakeFetch([{ throw: true }]), capture("1")), "error");
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`ok    ${name}`);
    } catch (err) {
      failed++;
      console.log(`FAIL  ${name}\n      ${err.message}`);
    }
  }
  console.log(failed ? `\n${failed} failure(s)` : `\nall ${tests.length} tests passed`);
  process.exit(failed ? 1 : 0);
})();
