import { invalidateJsonResponseCache } from "./json.mjs";
import { beginNetworkActivity, endNetworkActivity } from "../shell/network-activity.mjs";

function sendJson(path, method, payload, options) {
  options = options || {};
  var controller = typeof window.AbortController === "function" ? new window.AbortController() : null;
  var timeoutMs = Math.max(1000, Number(options.timeoutMs || 30000));
  var timeout = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;
  var activityId = beginNetworkActivity(path, method);
  return fetch(path, {
    method: method,
    headers: {
      "Accept": "application/json",
      "Content-Type": "application/json"
    },
    cache: "no-store",
    body: JSON.stringify(payload || {}),
    signal: controller ? controller.signal : undefined
  }).then(function (response) {
    return response.json().then(function (body) {
      if (!response.ok) throw new Error(body.error || "요청 실패");
      invalidateJsonResponseCache();
      return body;
    });
  }).catch(function (error) {
    if (error && error.name === "AbortError") {
      throw new Error("작업 응답 시간이 초과되었습니다. 서버 작업이 완료됐을 수 있으니 상태를 새로고침해 확인하세요.");
    }
    throw error;
  }).finally(function () {
    if (timeout) clearTimeout(timeout);
    endNetworkActivity(activityId);
  });
}

export { sendJson };
