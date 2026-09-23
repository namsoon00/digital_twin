export function createViewLifetime() {
  let generation = 0;
  const cleanups = new Set();
  return {
    capture() { const current = generation; return () => generation === current; },
    own(cleanup) { cleanups.add(cleanup); return () => cleanups.delete(cleanup); },
    invalidate() {
      generation++;
      const previous = [...cleanups];
      cleanups.clear();
      previous.forEach(cleanup => cleanup());
    }
  };
}

// Navigation invalidates delayed layout work even when the next route has the
// same tab key (A -> B -> A), and before its replacement DOM is committed.
export const viewLifetime = createViewLifetime();
export const layoutLifetime = createViewLifetime();
