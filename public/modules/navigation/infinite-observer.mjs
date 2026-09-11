export function createInfiniteListObserver({ Observer, root, contains, prefetchDistance, requestNext }) {
  let observer = null;
  let live = true;
  const requested = new WeakSet();
  return {
    observe(sentinels) {
      if (!Observer || !live || !sentinels.length) return;
      observer = new Observer(entries => {
        if (!live) return;
        entries.forEach(entry => {
          const sentinel = entry.target;
          if (!entry.isIntersecting || !contains(sentinel) || requested.has(sentinel)) return;
          const button = sentinel.querySelector("[data-mobile-infinite-next]:not(:disabled)");
          if (!button || button.hidden) return;
          requested.add(sentinel);
          requestNext(sentinel, button);
        });
      }, { root, rootMargin: "0px 0px " + prefetchDistance + "px 0px", threshold: 0.01 });
      sentinels.forEach(sentinel => observer.observe(sentinel));
    },
    disconnect() { live = false; observer?.disconnect(); observer = null; }
  };
}
