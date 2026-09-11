let silentDepth = 0;
export function requestsAreSilent() { return silentDepth > 0; }
export function withSilentRequests(task) {
  silentDepth++;
  try { return task(); } finally { silentDepth--; }
}
