function calendarEventKeys(event) {
  event = event || {};
  var keys = [];
  var id = String(event.eventId || event.id || "").trim();
  var title = String(event.displayTitle || event.title || "").trim().toLowerCase();
  var startsAt = String(event.startsAt || event.localDate || "").trim();
  if (id) keys.push("id:" + id);
  if (title && startsAt) keys.push("schedule:" + title + "|" + startsAt);
  return keys;
}

function mergeCalendarEvents(registeredEvents, detectedEvents) {
  var merged = [];
  var indexes = {};

  function add(event, origin) {
    if (!event || typeof event !== "object") return;
    var keys = calendarEventKeys(event);
    var existingIndex = keys.reduce(function (found, key) {
      return found >= 0 ? found : (Object.prototype.hasOwnProperty.call(indexes, key) ? indexes[key] : -1);
    }, -1);
    if (existingIndex >= 0) {
      if (origin === "registered") {
        merged[existingIndex] = Object.assign({}, merged[existingIndex], event, { calendarOrigin: "registered" });
      }
      calendarEventKeys(merged[existingIndex]).forEach(function (key) { indexes[key] = existingIndex; });
      return;
    }
    var projected = Object.assign({}, event, {
      calendarOrigin: origin,
      status: event.status || (origin === "registered" ? "active" : "tentative")
    });
    var index = merged.push(projected) - 1;
    calendarEventKeys(projected).forEach(function (key) { indexes[key] = index; });
  }

  (registeredEvents || []).forEach(function (event) { add(event, "registered"); });
  (detectedEvents || []).forEach(function (event) { add(event, "detected"); });
  return merged;
}

function calendarEventCanBeDeleted(event) {
  return Boolean(event && event.calendarOrigin !== "detected" && event.eventId);
}

export { calendarEventCanBeDeleted, calendarEventKeys, mergeCalendarEvents };
