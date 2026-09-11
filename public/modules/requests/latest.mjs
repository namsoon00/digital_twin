// A lane represents one replaceable result, not a global request generation.
export function createLatestRequestLane() {
  let generation = 0;
  let activeIdentity = null;
  let promise = null;
  return {
    begin(identity) {
      const ownGeneration = ++generation;
      activeIdentity = identity;
      promise = null;
      return {
        current: () => generation === ownGeneration,
        track(value) { if (generation === ownGeneration) promise = value; return value; },
        finish() { if (generation === ownGeneration) { activeIdentity = null; promise = null; } }
      };
    },
    active(identity) { return activeIdentity === identity ? promise : null; },
    invalidate() { generation++; activeIdentity = null; promise = null; }
  };
}
