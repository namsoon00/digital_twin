import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = file => readFileSync(new URL("../../public/modules/calendar/" + file, import.meta.url), "utf8")
  .replace(/^import .*;$/gm, "").replace(/^export .*;$/gm, "");
const warming = payload => payload?.status === "warming";

test("calendar polls a warming read once more and stops after receiving stored rows", async () => {
  const state = { investmentCalendarFilters: {} };
  const timers = [], requests = [], cleared = [];
  const responses = [{ status: "warming", events: [], summary: {total: null} }, {events: [{eventId: "saved"}]}];
  const context = vm.createContext({ calendarState: state, URLSearchParams,
    investmentCalendarMonthDate: () => new Date("2026-11-01T00:00:00Z"),
    render: () => {}, isStaticPreviewHost: () => false, readModelIsWarming: warming,
    scheduleReadModelPoll: (_key, callback) => timers.push(callback),
    clearReadModelPoll: key => cleared.push(key),
    requestJson: async (path, options) => {requests.push({path, options});return responses.shift();},
  });
  vm.runInContext(source("commands.mjs"), context);
  await context.loadInvestmentCalendar(false);
  assert.equal(timers.length, 1);
  timers[0]();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.investmentCalendar.events[0].eventId, "saved");
  assert.equal(requests.length, 2);
  assert.equal(requests[1].options.force, true);
  assert.deepEqual(cleared, ["investment-calendar-events"]);
  await context.loadInvestmentCalendar(false);
  assert.equal(requests.length, 2);
});

test("calendar rejects an old filter response and reads the newly selected filter", async () => {
  const state = {investmentCalendarFilters: {symbol: "FIRST"}};
  const pending = [];
  const context = vm.createContext({ calendarState: state, URLSearchParams,
    investmentCalendarMonthDate: () => new Date("2026-11-01T00:00:00Z"),
    render: () => {}, isStaticPreviewHost: () => false, readModelIsWarming: warming,
    scheduleReadModelPoll: () => {}, clearReadModelPoll: () => {},
    requestJson: path => new Promise(resolve => pending.push({path, resolve})),
  });
  vm.runInContext(source("commands.mjs"), context);
  const old = context.loadInvestmentCalendar(false);
  state.investmentCalendarFilters.symbol = "SECOND";
  pending[0].resolve({events: [{eventId: "old"}]});
  await old;
  assert.equal(state.investmentCalendar, undefined);
  assert.equal(pending.length, 2);
  assert.match(pending[1].path, /symbol=SECOND/);
  pending[1].resolve({events: [{eventId: "new"}]});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(state.investmentCalendar.events[0].eventId, "new");
});

test("warming calendar does not present unknown event counts as an empty calendar", () => {
  const context = vm.createContext({ currentInvestmentCalendar: () => ({status: "warming"}),
    readModelIsWarming: warming, renderConsoleManagedPage: (_name, _head, html) => html,
    renderEmptyState: payload => payload.title + payload.description,
  });
  vm.runInContext(source("workspace.mjs"), context);
  const html = context.renderInvestmentCalendarPage({});
  assert.match(html, /aria-busy="true"/);
  assert.match(html, /저장된 투자 일정을 확인/);
  assert.doesNotMatch(html, /등록된 투자 이벤트가 없습니다|저장 일정 없음/);
});
