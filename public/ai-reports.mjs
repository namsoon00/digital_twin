import { renderAIReport, reportDate, deliveryLabel } from "./modules/reports/ai-report.mjs?v=483c8b0cacfd2c21";

const $ = id => document.getElementById(id);
let current = null;
let busy = false;
let delivery = null;
let deliveryTimer = null;
let deliveryChecks = 0;
function pollDelivery() {
  clearTimeout(deliveryTimer);
  if (!current || !["pending", "processing", "retry-wait"].includes(delivery?.status) || deliveryChecks >= 12) return;
  const reportId = current.reportId, accountId = current.accountId;
  deliveryTimer = setTimeout(async () => {
    if (busy) { pollDelivery(); return; }
    deliveryChecks++;
    try {
      const data = await api(`/api/ai-reports/${reportId}/delivery?accountId=${encodeURIComponent(accountId)}`);
      if (current?.reportId !== reportId) return;
      delivery = data.delivery; $("delivery").textContent = deliveryLabel(delivery); controls(); pollDelivery();
    } catch { $("delivery").textContent = "발송 상태 조회 실패 · 새로고침으로 다시 확인할 수 있습니다."; }
  }, 5000);
}
async function api(url, body) {
  const response = await fetch(url, body === undefined ? {cache: "no-store"} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "보고서를 불러오지 못했습니다.");
  return data;
}
function controls() {
  $("create").disabled = busy || !$("account").value;
  $("refresh").disabled = busy || !$("account").value;
  $("account").disabled = busy;
  $("history").disabled = busy;
  $("send").disabled = busy || !current || !["not-requested", "other-channel"].includes(delivery?.status);
  $("print").disabled = busy || !current;
}
async function action(fn) {
  if (busy) return;
  busy = true; controls(); $("message").textContent = "확인 중…";
  try { await fn(); $("message").textContent = ""; }
  catch (error) { $("message").textContent = error.message; }
  finally { busy = false; controls(); }
}
function show(data) {
  current = data.report; delivery = data.delivery;
  deliveryChecks = 0; pollDelivery();
  $("report").innerHTML = renderAIReport(current);
  $("delivery").textContent = deliveryLabel(delivery);
  history.replaceState(null, "", `?accountId=${encodeURIComponent(current.accountId)}&reportId=${current.reportId}`);
}
async function open(id) {
  show(await api(`/api/ai-reports/${id}?accountId=${encodeURIComponent($("account").value)}`));
}
async function refresh(preferred = "") {
  const data = await api(`/api/ai-reports?accountId=${encodeURIComponent($("account").value)}`);
  $("history").replaceChildren(...data.reports.map(r => new Option(`${reportDate(r.createdAt)} · ${r.coverage.subjects}종목`, r.reportId)));
  const id = preferred || data.reports[0]?.reportId;
  if (id) { await open(id); $("history").value = id; }
  else {
    current = null; delivery = null;
    $("report").innerHTML = '<p class="empty">저장된 보고서가 없습니다. 위 버튼으로 첫 보고서를 만들어 주세요.</p>';
    $("delivery").textContent = "";
  }
}
$("create").onclick = () => action(async () => {
  const accountId = $("account").value;
  const key = "ai-report-request:" + accountId;
  const requestId = sessionStorage.getItem(key) || crypto.randomUUID();
  sessionStorage.setItem(key, requestId); // Retry an uncertain response with the same identity.
  const data = await api("/api/ai-reports", {accountId, requestId});
  sessionStorage.removeItem(key);
  show(data); await refresh(data.report.reportId);
});
$("send").onclick = () => action(async () => {
  const data = await api(`/api/ai-reports/${current.reportId}/send`, {accountId: current.accountId});
  delivery = data.delivery; $("delivery").textContent = deliveryLabel(delivery);
  deliveryChecks = 0; pollDelivery();
});
$("refresh").onclick = () => action(() => refresh(current?.reportId || ""));
$("history").onchange = () => action(() => open($("history").value));
$("account").onchange = () => action(() => refresh());
$("print").onclick = () => window.print();
let printDetails = [];
window.addEventListener("beforeprint", () => {
  printDetails = [...$("report").querySelectorAll("details:not([open])")];
  printDetails.forEach(item => { item.open = true; });
});
window.addEventListener("afterprint", () => {
  printDetails.forEach(item => { item.open = false; }); printDetails = [];
});
await action(async () => {
  const data = await api("/api/ai-reports/accounts");
  $("account").replaceChildren(...data.accounts.map(a => new Option(a.label, a.accountId)));
  const params = new URLSearchParams(location.search);
  if (data.accounts.some(a => a.accountId === params.get("accountId"))) $("account").value = params.get("accountId");
  if (!$("account").value) throw new Error("보고서를 만들 수 있는 활성 계정이 없습니다.");
  const id = params.get("reportId") || "";
  await refresh(/^[0-9a-f]{64}$/.test(id) ? id : "");
});
