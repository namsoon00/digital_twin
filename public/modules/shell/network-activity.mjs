import { app } from "./root.mjs";

var networkActivitySequence = 0;

var networkActivities = {};

var networkActivityRevealTimer = null;

var pendingNetworkControl = null;

var pendingNetworkControlTimer = null;

var NETWORK_ACTIVITY_REVEAL_MS = 180;

function networkActivityBusyLabel(text, method, path) {
  var value = String(text || "").replace(/\s+/g, " ").trim();
  var target = [value, path || ""].join(" ").toLowerCase();
  if (/동기화|sync/.test(target)) return "동기화 중";
  if (/저장|save/.test(target)) return "저장 중";
  if (/등록|추가|승인|approve|create/.test(target)) return "등록 중";
  if (/삭제|delete/.test(target)) return "삭제 중";
  if (/거절|reject/.test(target)) return "처리 중";
  if (/분석|리서치|research|analy/.test(target)) return "분석 중";
  if (/탐색|discover/.test(target)) return "탐색 중";
  if (/검증|validate|revalidate/.test(target)) return "검증 중";
  if (/발송|send/.test(target)) return "발송 중";
  if (/갱신|refresh/.test(target)) return "갱신 중";
  if (/조회|검색|새로고침|불러오기|목록|read|search|list/.test(target)) return "조회 중";
  if (/확인|check/.test(target)) return "확인 중";
  if (String(method || "GET").toUpperCase() === "GET") return "조회 중";
  if (["PUT", "PATCH"].indexOf(String(method || "").toUpperCase()) >= 0) return "저장 중";
  if (String(method || "").toUpperCase() === "DELETE") return "삭제 중";
  return "처리 중";
}

function networkActivityControlText(control) {
  if (!control) return "";
  return String(control.getAttribute("aria-label") || control.textContent || control.value || "").replace(/\s+/g, " ").trim();
}

function networkActivityControlIsIconOnly(control) {
  return Boolean(
    control
    && control.matches
    && control.matches('.icon-button, .symbol-watch-toggle, [data-network-busy-icon-only="true"]')
  );
}

function networkActivityFormDescriptor(form) {
  if (!form || !form.attributes) return null;
  var attributes = Array.prototype.slice.call(form.attributes);
  var attribute = attributes.filter(function (item) {
    return /^data-/.test(item.name) && /-form$/.test(item.name) && !/^data-card-/.test(item.name);
  })[0] || attributes.filter(function (item) {
    return /^data-/.test(item.name) && !/^data-card-/.test(item.name);
  })[0];
  return attribute ? { scope: "form", name: attribute.name, value: attribute.value || "" } : null;
}

function networkActivityControlDescriptor(control) {
  if (!control) return null;
  var action = control.getAttribute && control.getAttribute("data-action");
  if (action) return { scope: "control", name: "data-action", value: action };
  var form = control.closest && control.closest("form");
  if (form && String(control.type || "").toLowerCase() === "submit") {
    var formDescriptor = networkActivityFormDescriptor(form);
    if (formDescriptor) return formDescriptor;
  }
  var attributes = Array.prototype.slice.call(control.attributes || []).filter(function (item) {
    return /^data-/.test(item.name)
      && !/^data-(card|style|console-row|tab|screen-info|network-activity)/.test(item.name);
  });
  var attribute = attributes.filter(function (item) {
    return /(action|refresh|sync|run|save|delete|remove|approve|reject|review|test|send|submit)/.test(item.name);
  })[0] || attributes[0];
  return attribute ? { scope: "control", name: attribute.name, value: attribute.value || "" } : null;
}

function networkActivityDescriptorKey(descriptor) {
  if (!descriptor) return "";
  return [descriptor.scope || "control", descriptor.name || "", descriptor.value || ""].join(":");
}

function matchingNetworkActivityControls(descriptor) {
  if (!descriptor || !descriptor.name) return [];
  var roots = descriptor.scope === "form" ? app.querySelectorAll("form") : app.querySelectorAll("button, input[type=submit]");
  var matches = Array.prototype.slice.call(roots).filter(function (item) {
    return item.hasAttribute(descriptor.name) && String(item.getAttribute(descriptor.name) || "") === String(descriptor.value || "");
  });
  if (descriptor.scope !== "form") return matches;
  return matches.reduce(function (controls, form) {
    return controls.concat(Array.prototype.slice.call(form.querySelectorAll('button[type="submit"], input[type="submit"]')));
  }, []);
}

function clearNetworkBusyControl(control) {
  var original = control && control.__orbitAlphaNetworkBusy;
  if (!control || !original) return;
  if (String(control.tagName || "").toUpperCase() === "INPUT") control.value = original.value;
  else control.innerHTML = original.html;
  control.disabled = original.disabled;
  if (original.ariaBusy == null) control.removeAttribute("aria-busy");
  else control.setAttribute("aria-busy", original.ariaBusy);
  if (original.ariaLabel == null) control.removeAttribute("aria-label");
  else control.setAttribute("aria-label", original.ariaLabel);
  if (original.title == null) control.removeAttribute("title");
  else control.setAttribute("title", original.title);
  if (original.busyMinWidth) control.style.setProperty("--request-busy-min-width", original.busyMinWidth);
  else control.style.removeProperty("--request-busy-min-width");
  control.classList.remove("is-request-busy");
  control.removeAttribute("data-network-activity-key");
  delete control.__orbitAlphaNetworkBusy;
}

function markNetworkBusyControl(control, key, label) {
  if (!control || control.__orbitAlphaNetworkBusy) return;
  var rect = control.getBoundingClientRect ? control.getBoundingClientRect() : { width: 0 };
  var iconOnly = networkActivityControlIsIconOnly(control);
  control.__orbitAlphaNetworkBusy = {
    html: control.innerHTML,
    value: control.value,
    disabled: Boolean(control.disabled),
    ariaBusy: control.getAttribute("aria-busy"),
    ariaLabel: control.getAttribute("aria-label"),
    title: control.getAttribute("title"),
    busyMinWidth: control.style.getPropertyValue("--request-busy-min-width")
  };
  if (rect.width) control.style.setProperty("--request-busy-min-width", Math.ceil(rect.width) + "px");
  control.disabled = true;
  control.setAttribute("aria-busy", "true");
  control.setAttribute("aria-label", label);
  control.setAttribute("title", label);
  control.setAttribute("data-network-activity-key", key);
  control.classList.add("is-request-busy");
  if (String(control.tagName || "").toUpperCase() === "INPUT") {
    control.value = label;
    return;
  }
  control.innerHTML = "";
  var spinner = document.createElement("span");
  spinner.className = "button-activity-spinner";
  spinner.setAttribute("aria-hidden", "true");
  control.appendChild(spinner);
  if (!iconOnly) {
    var copy = document.createElement("span");
    copy.className = "button-activity-label";
    copy.textContent = label;
    control.appendChild(copy);
  }
}

function syncNetworkActivityDom() {
  var desired = {};
  Object.keys(networkActivities).forEach(function (id) {
    var activity = networkActivities[id];
    var key = networkActivityDescriptorKey(activity.descriptor);
    if (key) desired[key] = activity;
  });
  Array.prototype.slice.call(app.querySelectorAll("[data-network-activity-key]")).forEach(function (control) {
    if (!desired[control.getAttribute("data-network-activity-key")]) clearNetworkBusyControl(control);
  });
  Object.keys(desired).forEach(function (key) {
    matchingNetworkActivityControls(desired[key].descriptor).forEach(function (control) {
      markNetworkBusyControl(control, key, desired[key].label);
    });
  });
  var count = Object.keys(networkActivities).length;
  var progress = document.getElementById("app-request-progress");
  if (!progress) return;
  var now = Date.now();
  var progressVisible = Object.keys(networkActivities).some(function (id) {
    return now - Number(networkActivities[id].startedAt || now) >= NETWORK_ACTIVITY_REVEAL_MS;
  });
  if (networkActivityRevealTimer && (!count || progressVisible)) {
    clearTimeout(networkActivityRevealTimer);
    networkActivityRevealTimer = null;
  }
  if (count && !progressVisible && !networkActivityRevealTimer) {
    var nextRevealIn = Object.keys(networkActivities).reduce(function (minimum, id) {
      var elapsed = now - Number(networkActivities[id].startedAt || now);
      return Math.min(minimum, Math.max(0, NETWORK_ACTIVITY_REVEAL_MS - elapsed));
    }, NETWORK_ACTIVITY_REVEAL_MS);
    networkActivityRevealTimer = setTimeout(function () {
      networkActivityRevealTimer = null;
      syncNetworkActivityDom();
    }, nextRevealIn);
  }
  progress.classList.toggle("is-active", progressVisible);
  progress.setAttribute("aria-hidden", progressVisible ? "false" : "true");
  var latest = count ? networkActivities[Object.keys(networkActivities)[count - 1]] : null;
  var status = count > 1 ? "데이터 작업 " + count + "건 진행 중" : (latest ? latest.label : "작업 완료");
  progress.setAttribute("aria-valuetext", status);
  var live = progress.querySelector("[data-network-activity-status]");
  if (live) live.textContent = status;
}

function decorateRenderedBusyControls() {
  Array.prototype.slice.call(app.querySelectorAll("button:disabled:not(.is-request-busy)")).forEach(function (control) {
    var text = networkActivityControlText(control);
    var busy = /(조회|저장|등록|삭제|동기화|갱신|분석|탐색|검증|발송|확인|실행|처리|재생|불러오는|읽는) 중/.test(text);
    control.classList.toggle("is-state-busy", busy);
    if (busy) control.setAttribute("aria-busy", "true");
  });
}

function setPendingNetworkControl(control) {
  if (!control || control.disabled) return;
  if (pendingNetworkControlTimer) clearTimeout(pendingNetworkControlTimer);
  pendingNetworkControl = {
    control: control,
    descriptor: networkActivityControlDescriptor(control),
    text: networkActivityControlText(control)
  };
  pendingNetworkControlTimer = setTimeout(function () {
    pendingNetworkControl = null;
    pendingNetworkControlTimer = null;
  }, 0);
}

function bindNetworkActivityControls() {
  app.addEventListener("click", function (event) {
    var control = event.target && event.target.closest && event.target.closest("button, input[type=submit]");
    if (control && app.contains(control)) setPendingNetworkControl(control);
  }, true);
  app.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !app.contains(form)) return;
    var control = document.activeElement;
    if (!control || !form.contains(control) || !/^(BUTTON|INPUT)$/.test(control.tagName || "")) {
      control = form.querySelector('button[type="submit"], input[type="submit"]');
    }
    if (control) setPendingNetworkControl(control);
  }, true);
}

function beginNetworkActivity(path, method) {
  var pending = pendingNetworkControl;
  if (pendingNetworkControlTimer) clearTimeout(pendingNetworkControlTimer);
  pendingNetworkControl = null;
  pendingNetworkControlTimer = null;
  networkActivitySequence += 1;
  var id = "request-" + networkActivitySequence;
  networkActivities[id] = {
    id: id,
    descriptor: pending ? pending.descriptor : null,
    label: networkActivityBusyLabel(pending ? pending.text : "", method, path),
    startedAt: Date.now()
  };
  syncNetworkActivityDom();
  return id;
}

function endNetworkActivity(id) {
  if (id && networkActivities[id]) delete networkActivities[id];
  syncNetworkActivityDom();
}

export { beginNetworkActivity, bindNetworkActivityControls, decorateRenderedBusyControls, endNetworkActivity, syncNetworkActivityDom };
