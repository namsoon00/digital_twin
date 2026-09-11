// Weak ownership lets closed dialogs release their scroll history with the DOM.
function createPanelScrollMemory() {
  const positions = new WeakMap();
  return {
    replace(scroller, previousKey, nextKey, update) {
      if (!scroller) return update();
      let tabs = positions.get(scroller);
      if (!tabs) { tabs = new Map(); positions.set(scroller, tabs); }
      const before = Math.max(0, Number(scroller.scrollTop) || 0);
      if (previousKey !== nextKey) tabs.set(previousKey, before);
      const target = previousKey !== nextKey && tabs.has(nextKey) ? tabs.get(nextKey) : before;
      update();
      scroller.scrollTop = target;
    }
  };
}

export { createPanelScrollMemory };
