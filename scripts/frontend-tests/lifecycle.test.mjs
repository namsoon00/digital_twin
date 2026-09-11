import test from "node:test";
import assert from "node:assert/strict";
import { createViewLifetime } from "../../public/modules/navigation/lifecycle.mjs";
import { createInfiniteListObserver } from "../../public/modules/navigation/infinite-observer.mjs";

test("view lifetimes dispose once and distinguish a return to the same route", () => {
  const lifetime = createViewLifetime();
  const old = lifetime.capture(); let disposals = 0;
  lifetime.own(() => disposals++); lifetime.invalidate(); lifetime.invalidate();
  assert.equal(disposals, 1); assert(!old()); assert(lifetime.capture()());
  const unregister = lifetime.own(() => disposals++); unregister(); lifetime.invalidate();
  assert.equal(disposals, 1);
});

test("observer ignores detached and late entries, and requests each sentinel once", () => {
  let callback; let disconnected = 0; let requested = 0;
  class Observer {
    constructor(fn) { callback = fn; }
    observe() {}
    disconnect() { disconnected++; }
  }
  const button = { hidden: false };
  const sentinel = { connected: true, querySelector: () => button };
  const controller = createInfiniteListObserver({ Observer, root: null, contains: node => node.connected,
    prefetchDistance: 1400, requestNext: () => requested++ });
  controller.observe([sentinel]);
  callback([{ isIntersecting: true, target: sentinel }, { isIntersecting: true, target: sentinel }]);
  assert.equal(requested, 1);
  const detached = { connected: false, querySelector: () => button };
  callback([{ isIntersecting: true, target: detached }]); assert.equal(requested, 1);
  controller.disconnect(); controller.disconnect();
  callback([{ isIntersecting: true, target: { ...sentinel } }]);
  assert.equal(requested, 1); assert.equal(disconnected, 1);
});
