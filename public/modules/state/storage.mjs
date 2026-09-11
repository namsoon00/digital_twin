var snapshotMemoryStore = "";

function loadCachedSnapshot() {
  var raw = readSessionPayload("orbitAlphaLastSnapshot", "")
    || readPersistentPayload("orbitAlphaLastSnapshotPersistent", snapshotMemoryStore);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (error) {
    return null;
  }
}

function writeCachedSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return false;
  try {
    var payload = JSON.stringify(snapshot);
    snapshotMemoryStore = payload;
    var sessionWritten = writeSessionPayload("orbitAlphaLastSnapshot", payload);
    var persistentWritten = writePersistentPayload("orbitAlphaLastSnapshotPersistent", payload);
    return sessionWritten || persistentWritten;
  } catch (error) {
    return false;
  }
}

function loadCachedPayload(key, fallback) {
  var raw = readSessionPayload(key, fallback || "");
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (error) {
    return null;
  }
}

function writeCachedPayload(key, payload, memorySetter) {
  if (!payload || typeof payload !== "object") return false;
  try {
    var serialized = JSON.stringify(payload);
    if (typeof memorySetter === "function") memorySetter(serialized);
    return writeSessionPayload(key, serialized);
  } catch (error) {
    return false;
  }
}

function readSessionPayload(key, fallback) {
  try {
    if (window.sessionStorage) {
      var value = window.sessionStorage.getItem(key);
      return value == null ? fallback : value;
    }
  } catch (error) {
    return fallback;
  }
  return fallback;
}

function writeSessionPayload(key, payload) {
  try {
    if (window.sessionStorage) {
      window.sessionStorage.setItem(key, payload);
    }
    return true;
  } catch (error) {
    return false;
  }
}

function readPersistentPayload(key, fallback) {
  try {
    if (window.localStorage) {
      var value = window.localStorage.getItem(key);
      return value == null ? fallback : value;
    }
  } catch (error) {
    return fallback;
  }
  return fallback;
}

function writePersistentPayload(key, payload) {
  try {
    if (window.localStorage) window.localStorage.setItem(key, payload);
    return true;
  } catch (error) {
    return false;
  }
}

function loadSymbolUniverseRefreshHistory() {
  var raw = readPersistentPayload("orbitAlphaSymbolRefreshHistory", "");
  if (!raw) return [];
  try {
    var items = JSON.parse(raw);
    return Array.isArray(items) ? items.slice(0, 5) : [];
  } catch (error) {
    return [];
  }
}

function persistSymbolUniverseRefreshHistory(items) {
  writePersistentPayload("orbitAlphaSymbolRefreshHistory", JSON.stringify((items || []).slice(0, 5)));
}

export { loadCachedPayload, loadCachedSnapshot, loadSymbolUniverseRefreshHistory, persistSymbolUniverseRefreshHistory, readPersistentPayload, writeCachedPayload, writeCachedSnapshot, writePersistentPayload };
