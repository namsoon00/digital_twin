import { feedTimeValue } from "../research/requests.mjs";
import { appDateTimeParts } from "../settings/preferences.mjs";
import { escapeHtml } from "./text.mjs";

function formatClock(value) {
  if (!value) return "-";
  var raw = String(value).trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw + " (시각 미기록)";
  var parts = appDateTimeParts(value);
  if (!parts) return String(value);
  return [
    parts.year,
    parts.month,
    parts.day
  ].join("-") + " " + [
    parts.hour,
    parts.minute,
    parts.second
  ].join(":");
}

var recordChangedAtFields = [
  "updatedAt", "updated_at", "modifiedAt", "modified_at", "changedAt", "changed_at",
  "lastModifiedAt", "last_modified_at", "lastTransitionAt", "reviewedAt", "reviewed_at",
  "completedAt", "completed_at", "processedAt", "processed_at", "deliveredAt", "delivered_at",
  "marketDataUpdatedAt", "lastSeenAt", "fetchedAt", "observedAt", "generatedAt", "refreshedAt",
  "createdAt", "created_at", "publishedAt", "published_at"
];

function recordChangedAt(record, fallback) {
  if (typeof record === "number" && Number.isFinite(record) && record > 0) return record;
  if (typeof record === "string" || record instanceof Date) {
    return feedTimeValue(record) ? record : (fallback && feedTimeValue(fallback) ? fallback : "");
  }
  var sources = [];
  if (record && typeof record === "object") {
    sources.push(record);
    [record.raw, record.payload, record.metadata, record.properties, record.graph, record.stateContract].forEach(function (item) {
      if (item && typeof item === "object") sources.push(item);
    });
  }
  for (var sourceIndex = 0; sourceIndex < sources.length; sourceIndex += 1) {
    var source = sources[sourceIndex];
    for (var fieldIndex = 0; fieldIndex < recordChangedAtFields.length; fieldIndex += 1) {
      var value = source[recordChangedAtFields[fieldIndex]];
      if (value && feedTimeValue(value)) return value;
    }
  }
  return fallback && feedTimeValue(fallback) ? fallback : "";
}

function recordChangedAtValue(record, fallback) {
  var changedAt = recordChangedAt(record, fallback);
  return typeof changedAt === "number" ? changedAt : feedTimeValue(changedAt);
}

function latestChangedFirst(rows, resolver, tieBreaker) {
  return (Array.isArray(rows) ? rows : []).map(function (row, index) {
    var resolved = resolver ? resolver(row, index) : row;
    return { row: row, index: index, time: recordChangedAtValue(resolved) };
  }).sort(function (a, b) {
    if (a.time !== b.time) return b.time - a.time;
    var tied = tieBreaker ? Number(tieBreaker(a.row, b.row) || 0) : 0;
    return tied || a.index - b.index;
  }).map(function (item) { return item.row; });
}

function recordChangedAtText(record, fallback) {
  var changedAt = recordChangedAt(record, fallback);
  return changedAt ? "최종 변경 " + formatClock(changedAt) : "변경일 미확인";
}

function renderRecordChangedAt(record, fallback, className) {
  var changedAt = recordChangedAt(record, fallback);
  var tag = changedAt ? "time" : "span";
  var datetime = changedAt ? new Date(changedAt).toISOString() : "";
  return '<' + tag + ' class="record-changed-at ' + escapeHtml(className || "") + '"' + (datetime ? ' datetime="' + escapeHtml(datetime) + '"' : '') + '>'
    + escapeHtml(recordChangedAtText(record, fallback)) + '</' + tag + '>';
}

function formatMoney(value) {
  var number = Number(value || 0);
  if (!Number.isFinite(number)) return "-";
  if (Math.abs(number) >= 100000000) return (number / 100000000).toFixed(1) + "억";
  if (Math.abs(number) >= 10000) return Math.round(number / 10000).toLocaleString("ko-KR") + "만";
  return number.toLocaleString("ko-KR");
}

function formatInteger(value) {
  var number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  return Math.round(number).toLocaleString("ko-KR");
}

function formatCurrency(value, currency) {
  var suffix = currency ? " " + currency : "";
  return formatMoney(value) + suffix;
}

function formatPrice(value, currency) {
  var number = Number(value || 0);
  if (!Number.isFinite(number)) return "-";
  var suffix = currency ? " " + currency : "";
  return number.toLocaleString("ko-KR", {
    maximumFractionDigits: Number.isInteger(number) ? 0 : 2
  }) + suffix;
}

function hasNumericValue(value) {
  if (value == null || value === "") return false;
  return Number.isFinite(Number(String(value).replace(/,/g, "").trim()));
}

function optionalSignedPct(value, available) {
  return available && hasNumericValue(value) ? signedPct(value) : "-";
}

function optionalPrice(value, currency, available) {
  return available && hasNumericValue(value) ? formatPrice(value, currency) : "시세 미수집";
}

function pct(value) {
  return Math.round(Number(value || 0)) + "%";
}

function signedPct(value) {
  var number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "0%";
  return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 1) + "%";
}

function signedNumber(value) {
  var number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "0";
  return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 1);
}

function signedMoney(value, currency) {
  var number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "0" + (currency ? " " + currency : "");
  return (number > 0 ? "+" : "") + formatCurrency(number, currency);
}

function sourceLabel(value) {
  if (value === "holding") return "보유";
  if (value === "watchlist") return "관심";
  if (value === "cash") return "현금";
  return value || "-";
}

function numeric(value) {
  var parsed = Number(String(value == null ? "" : value).replace(/,/g, "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function formatSignalNumber(value, suffix) {
  var number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "-";
  return number.toLocaleString("ko-KR", {
    maximumFractionDigits: Math.abs(number) >= 10 ? 0 : 1
  }) + (suffix || "");
}

function formatSignalRatio(value) {
  var number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "-";
  return number.toFixed(number >= 10 ? 0 : 1) + "x";
}

function formatSignalVolume(value) {
  var number = Number(value || 0);
  if (!Number.isFinite(number) || number === 0) return "-";
  return formatMoney(number);
}

export { clamp, formatClock, formatCurrency, formatInteger, formatMoney, formatPrice, formatSignalNumber, formatSignalRatio, formatSignalVolume, hasNumericValue, latestChangedFirst, numeric, optionalPrice, optionalSignedPct, pct, recordChangedAt, recordChangedAtValue, renderRecordChangedAt, signedMoney, signedPct, sourceLabel };
