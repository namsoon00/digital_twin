import { defaultSettings } from "./defaults.mjs";
import { settingsState } from "../state/settings.mjs";

function currentAppTheme() {
  var value = String((settingsState.settings && settingsState.settings.appTheme) || defaultSettings.appTheme || "dark").toLowerCase();
  if (["light", "dark", "system"].indexOf(value) < 0) return "dark";
  return value;
}

function appTimezoneOptions() {
  return [
    { value: "Asia/Seoul", label: "서울" },
    { value: "America/New_York", label: "뉴욕" },
    { value: "America/Chicago", label: "시카고" },
    { value: "America/Los_Angeles", label: "로스앤젤레스" },
    { value: "Europe/London", label: "런던" },
    { value: "Europe/Berlin", label: "프랑크푸르트" },
    { value: "Asia/Tokyo", label: "도쿄" },
    { value: "Asia/Hong_Kong", label: "홍콩" },
    { value: "Asia/Singapore", label: "싱가포르" },
    { value: "Australia/Sydney", label: "시드니" },
    { value: "UTC", label: "UTC" }
  ];
}

function currentAppTimezone() {
  var value = String((settingsState && settingsState.settings && settingsState.settings.appTimezone) || defaultSettings.appTimezone || "Asia/Seoul").trim();
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value }).format(new Date());
    return value;
  } catch (error) {
    return "Asia/Seoul";
  }
}

function appTimezoneLabel(value) {
  var key = String(value || currentAppTimezone());
  var option = appTimezoneOptions().filter(function (item) { return item.value === key; })[0];
  return option ? option.label : key;
}

function appDateTimeParts(value) {
  var date = value instanceof Date ? value : new Date(value || "");
  if (Number.isNaN(date.getTime())) return null;
  var parts = {};
  new Intl.DateTimeFormat("en-CA", {
    timeZone: currentAppTimezone(),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23"
  }).formatToParts(date).forEach(function (part) {
    if (part.type !== "literal") parts[part.type] = part.value;
  });
  return parts;
}

function resolvedAppTheme() {
  var theme = currentAppTheme();
  if (theme === "system" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return theme;
}

function applyAppTheme() {
  var theme = resolvedAppTheme();
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-theme-setting", currentAppTheme());
  var themeMeta = document.querySelector('meta[name="theme-color"]');
  if (themeMeta) themeMeta.setAttribute("content", theme === "dark" ? "#0c1117" : "#f3f5f8");
}

export { appDateTimeParts, appTimezoneLabel, appTimezoneOptions, applyAppTheme, currentAppTheme, currentAppTimezone };
