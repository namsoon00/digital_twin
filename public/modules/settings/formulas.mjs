import { render } from "../render/scheduler.mjs";
import { defaultSettings } from "./defaults.mjs";
import { settingValue } from "./fields.mjs";
import { persistSettings } from "./storage.mjs";
import { clamp, numeric } from "../shared/format.mjs";
import { settingsState } from "../state/settings.mjs";

function formulaSetting(name) {
  return String(settingValue(name) || defaultSettings[name] || "").trim();
}

function tokenizeFormula(expression) {
  var input = String(expression || "");
  var tokens = [];
  var index = 0;
  if (input.length > 1200) throw new Error("공식이 너무 깁니다.");
  while (index < input.length) {
    var char = input[index];
    if (/\s/.test(char)) {
      index += 1;
      continue;
    }
    if (/[0-9.]/.test(char)) {
      var start = index;
      index += 1;
      while (index < input.length && /[0-9.]/.test(input[index])) index += 1;
      var number = Number(input.slice(start, index));
      if (!Number.isFinite(number)) throw new Error("숫자 형식 오류");
      tokens.push({ type: "number", value: number });
      continue;
    }
    if (/[A-Za-z_]/.test(char)) {
      var nameStart = index;
      index += 1;
      while (index < input.length && /[A-Za-z0-9_]/.test(input[index])) index += 1;
      tokens.push({ type: "name", value: input.slice(nameStart, index) });
      continue;
    }
    if ("+-*/(),".indexOf(char) >= 0) {
      tokens.push({ type: char, value: char });
      index += 1;
      continue;
    }
    throw new Error("지원하지 않는 문자: " + char);
  }
  return tokens;
}

function evaluateFormula(expression, variables) {
  var tokens = tokenizeFormula(expression);
  var index = 0;
  variables = variables || {};

  function peek() {
    return tokens[index] || null;
  }

  function take(type) {
    var token = peek();
    if (token && token.type === type) {
      index += 1;
      return token;
    }
    return null;
  }

  function expect(type) {
    var token = take(type);
    if (!token) throw new Error("'" + type + "'가 필요합니다.");
    return token;
  }

  function safeNumber(value) {
    var number = Number(value);
    return Number.isFinite(number) ? number : 0;
  }

  function applyFormulaFunction(name, args) {
    var lower = String(name || "").toLowerCase();
    if (lower === "min") return Math.min.apply(Math, args);
    if (lower === "max") return Math.max.apply(Math, args);
    if (lower === "abs") return Math.abs(args[0] || 0);
    if (lower === "round") return Math.round(args[0] || 0);
    if (lower === "sqrt") return Math.sqrt(Math.max(0, args[0] || 0));
    if (lower === "pow") return Math.pow(args[0] || 0, args[1] || 0);
    if (lower === "clamp") return clamp(args[0] || 0, args[1] || 0, args[2] == null ? 100 : args[2]);
    throw new Error("지원하지 않는 함수: " + name);
  }

  function parsePrimary() {
    var token = peek();
    if (!token) throw new Error("공식이 끝났습니다.");
    if (take("number")) return token.value;
    if (take("name")) {
      if (take("(")) {
        var args = [];
        if (!take(")")) {
          do {
            args.push(parseExpression());
          } while (take(","));
          expect(")");
        }
        return safeNumber(applyFormulaFunction(token.value, args));
      }
      return safeNumber(variables[token.value]);
    }
    if (take("(")) {
      var value = parseExpression();
      expect(")");
      return value;
    }
    throw new Error("예상하지 못한 토큰: " + token.value);
  }

  function parseUnary() {
    if (take("+")) return parseUnary();
    if (take("-")) return -parseUnary();
    return parsePrimary();
  }

  function parseTerm() {
    var value = parseUnary();
    while (true) {
      if (take("*")) {
        value *= parseUnary();
      } else if (take("/")) {
        var divisor = parseUnary();
        value = divisor ? value / divisor : 0;
      } else {
        break;
      }
    }
    return value;
  }

  function parseExpression() {
    var value = parseTerm();
    while (true) {
      if (take("+")) {
        value += parseTerm();
      } else if (take("-")) {
        value -= parseTerm();
      } else {
        break;
      }
    }
    return value;
  }

  var output = parseExpression();
  if (index < tokens.length) throw new Error("공식 뒤에 해석되지 않은 값이 있습니다.");
  if (!Number.isFinite(output)) throw new Error("공식 결과가 숫자가 아닙니다.");
  return output;
}

function evaluateConfiguredFormula(expression, variables, fallback) {
  try {
    var value = evaluateFormula(expression, variables);
    return {
      value: value,
      error: "",
      usedFallback: false
    };
  } catch (error) {
    return {
      value: fallback,
      error: error.message || "공식 오류",
      usedFallback: true
    };
  }
}

function parseNumberAssignments(value, defaults) {
  var map = Object.assign({}, defaults || {});
  String(value || "")
    .split(/\r?\n/)
    .map(function (line) { return line.trim(); })
    .filter(Boolean)
    .forEach(function (line) {
      var parts = line.split(/[=:,]/);
      var key = String(parts[0] || "").trim();
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return;
      map[key] = numeric(parts.slice(1).join(":"));
    });
  return map;
}

function strategyDefaultSettingNames() {
  return [
    "fairValueFormula",
    "ontologyRelationRules",
    "aiPromptTemplates",
    "aiPromptPolicy",
    "notificationAiGateEnabled",
    "notificationAiGateMessageTypes",
    "notificationAiUseCodex",
    "notificationAiModel",
    "notificationAiReasoningEffort",
    "notificationAiTimeoutSeconds",
    "notificationAiDeliveryDeadlineSeconds",
    "notificationAiTypeDbFallbackEnabled",
    "notificationAiFallbackOnFirstFailure",
    "notificationAiQueueWorkerCount",
    "localAiMaxConcurrentProcesses",
    "localAiInvestmentReservedProcesses",
    "notificationAiCapacityWaitSeconds",
    "modelName",
    "modelHypothesis",
    "relationRuleThresholds",
    "alertThresholds"
  ];
}

function withDefaultStrategySettings(settings) {
  var next = Object.assign({}, settings || {});
  strategyDefaultSettingNames().forEach(function (name) {
    if (String(next[name] == null ? "" : next[name]).trim() === "") {
      next[name] = defaultSettings[name] || "";
    }
  });
  return next;
}

function syncedModelAlertSettings(settings) {
  return withDefaultStrategySettings(settings);
}

function syncModelAlertThresholdSettings() {
  settingsState.settings = syncedModelAlertSettings(settingsState.settings);
}

function assignmentOrder(settingName) {
  return String(defaultSettings[settingName] || "")
    .split(/\r?\n/)
    .map(function (line) { return String(line.split(/[=:,]/)[0] || "").trim(); })
    .filter(Boolean);
}

function serializeNumberAssignments(map, order) {
  var seen = {};
  var keys = (order || []).filter(function (key) {
    if (!Object.prototype.hasOwnProperty.call(map, key) || seen[key]) return false;
    seen[key] = true;
    return true;
  });
  Object.keys(map).sort().forEach(function (key) {
    if (!seen[key]) keys.push(key);
  });
  return keys.map(function (key) {
    return key + "=" + Number(map[key] || 0);
  }).join("\n");
}

function updateNumberAssignmentSetting(settingName, key, value) {
  if (!Object.prototype.hasOwnProperty.call(defaultSettings, settingName)) return;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(String(key || ""))) return;
  var map = parseNumberAssignments(settingValue(settingName), parseNumberAssignments(defaultSettings[settingName]));
  map[key] = numeric(value);
  settingsState.settings[settingName] = serializeNumberAssignments(map, assignmentOrder(settingName));
  persistSettings();
  settingsState.settingsSaved = false;
  render();
}

function updateBooleanAssignmentSetting(settingName, key, enabled) {
  if (!Object.prototype.hasOwnProperty.call(defaultSettings, settingName)) return;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(String(key || ""))) return;
  var map = parseNumberAssignments(settingValue(settingName), parseNumberAssignments(defaultSettings[settingName]));
  map[key] = enabled ? 1 : 0;
  settingsState.settings[settingName] = serializeNumberAssignments(map, assignmentOrder(settingName));
  persistSettings();
  settingsState.settingsSaved = false;
  render();
}

export { evaluateConfiguredFormula, formulaSetting, parseNumberAssignments, syncModelAlertThresholdSettings, syncedModelAlertSettings, updateBooleanAssignmentSetting, updateNumberAssignmentSetting };
