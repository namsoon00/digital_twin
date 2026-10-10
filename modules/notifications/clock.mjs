function notificationClock(value) {
  if (!value) return "-";
  var raw = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw + " (시각 미기록)";
  var normalized = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(raw) ? raw + "Z" : raw;
  var date = new Date(normalized);
  if (Number.isNaN(date.getTime())) return raw;
  var parts = {};
  new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(date).forEach(function (part) {
    if (part.type !== "literal") parts[part.type] = part.value;
  });
  return parts.year + "-" + parts.month + "-" + parts.day + " " + parts.hour + ":" + parts.minute + ":" + parts.second + " KST";
}

export { notificationClock };
