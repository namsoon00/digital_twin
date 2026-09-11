import test from "node:test";
import assert from "node:assert/strict";
import { createPanelScrollMemory } from "../../public/modules/navigation/panel-scroll.mjs";

test("case panel restores each tab through shorter content and keeps updates in place", () => {
  const memory = createPanelScrollMemory();
  let height = 2000;
  let top = 900;
  const scroller = { get scrollTop() { return top; }, set scrollTop(value) { top = Math.min(value, height); } };
  memory.replace(scroller, "case:history", "case:summary", () => { height = 120; });
  assert.equal(top, 120);
  memory.replace(scroller, "case:summary", "case:history", () => { height = 2000; });
  assert.equal(top, 900);
  top = 950;
  memory.replace(scroller, "case:history", "case:history", () => {});
  assert.equal(top, 950, "A delayed refresh must retain the user's newer position");
  const other = { scrollTop: 25 };
  memory.replace(other, "case:summary", "case:history", () => {});
  assert.equal(other.scrollTop, 25, "Another dialog must not inherit the old scroll history");
});
