import test from "node:test";
import assert from "node:assert/strict";
import { createJsonClient } from "../../public/modules/requests/client.mjs";
import { createLatestRequestLane } from "../../public/modules/requests/latest.mjs";
import { requestsAreSilent, withSilentRequests } from "../../public/modules/requests/activity-scope.mjs";

function fixture(options = {}) {
  const pending = [];
  const completed = [];
  const client = createJsonClient({
    fetch: (path, init) => new Promise((resolve, reject) => pending.push({ path, init, resolve, reject })),
    end: (_, result) => completed.push(result), ...options
  });
  const flush = () => Promise.resolve();
  const resolve = (index, payload) => pending[index].resolve({ ok: true, status: 200, json: async () => payload });
  return { client, pending, completed, flush, resolve };
}

test("deduplicates only equal request paths, even with a shared logical key", async () => {
  const f = fixture();
  const a = f.client.request("/api/list?account=A", { key: "list" });
  assert.equal(a, f.client.request("/api/list?account=A", { key: "list" }));
  const b = f.client.request("/api/list?account=B", { key: "list" });
  assert.notEqual(a, b);
  await f.flush();
  assert.equal(f.pending.length, 2);
  f.resolve(1, { account: "B" }); await b;
  f.resolve(0, { account: "A" }); await a;
  assert.deepEqual(await f.client.request("/api/list?account=B", { key: "list" }), { account: "B" });
  assert.equal(f.pending.length, 2);
});

test("forced requests cannot publish an older cache or clear a newer active request", async () => {
  const f = fixture();
  const a = f.client.request("/api/value");
  const b = f.client.request("/api/value", { force: true });
  await f.flush();
  f.resolve(0, "old"); await a;
  assert.equal(f.client.active("/api/value"), b);
  f.resolve(1, "new"); await b;
  assert.equal(await f.client.request("/api/value"), "new");
});

test("invalidating an in-flight read prevents mutation-era cache resurrection", async () => {
  const f = fixture();
  const a = f.client.request("/api/value");
  await f.flush();
  f.client.invalidate("/api/");
  const b = f.client.request("/api/value");
  await f.flush();
  f.resolve(1, "after mutation"); await b;
  f.resolve(0, "before mutation"); await a;
  assert.equal(await f.client.request("/api/value"), "after mutation");
});

test("bounded caches evict the oldest entry", async () => {
  const f = fixture({ cacheLimit: 2 });
  for (let index = 0; index < 3; index++) {
    const request = f.client.request("/api/" + index);
    await f.flush(); f.resolve(index, index); await request;
  }
  const again = f.client.request("/api/0");
  await f.flush(); assert.equal(f.pending.length, 4);
  f.resolve(3, 0); await again;
});

test("sync transport failure and timeout both release activity and in-flight state", async () => {
  const completed = [];
  const client = createJsonClient({ fetch: () => { throw new Error("sync failure"); }, end: (_, result) => completed.push(result) });
  await assert.rejects(client.request("/failure"), /sync failure/);
  assert.equal(client.active("/failure"), undefined);
  assert.equal(completed.length, 1);
  let timeout;
  let cleared = false;
  const f = fixture({ AbortController, setTimer: callback => { timeout = callback; return 1; }, clearTimer: () => { cleared = true; } });
  const promise = f.client.request("/timeout");
  await f.flush();
  timeout(); assert(f.pending[0].init.signal.aborted);
  f.pending[0].reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
  await assert.rejects(promise, /응답 시간이/);
  assert(cleared); assert.equal(f.completed[0].outcome, "timeout");
});

test("dispose aborts superseded requests too", async () => {
  const f = fixture({ AbortController });
  const requests = [f.client.request("/x"), f.client.request("/x", { force: true })];
  await f.flush(); f.client.dispose();
  f.pending.forEach((entry, index) => { assert(entry.init.signal.aborted); f.resolve(index, index); });
  await Promise.all(requests);
  await assert.rejects(f.client.request("/x"), /disposed/);
});

test("latest lane rejects A -> B -> A stale finalizers", async () => {
  const lane = createLatestRequestLane();
  const oldA = lane.begin("A"); const b = lane.begin("B"); const newA = lane.begin("A");
  const pending = Promise.resolve("A"); newA.track(pending);
  assert(!oldA.current()); assert(!b.current()); assert(newA.current());
  oldA.finish(); assert.equal(lane.active("A"), pending);
  newA.finish(); assert.equal(lane.active("A"), null);
  lane.invalidate(); assert(!newA.current());
});

test("silent request batches are nested and end synchronously", async () => {
  let resolve;
  const pending = withSilentRequests(() => {
    assert(requestsAreSilent()); withSilentRequests(() => assert(requestsAreSilent()));
    return new Promise(done => { resolve = done; });
  });
  assert(!requestsAreSilent()); resolve(); await pending;
  assert.throws(() => withSilentRequests(() => { throw new Error("test"); }));
  assert(!requestsAreSilent());
});
