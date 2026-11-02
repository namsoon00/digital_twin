import { renderAIReport, reportDate, deliveryLabel, analysisLabel } from "./modules/reports/ai-report.mjs?v=51f8a877d586095e";

const $ = id => document.getElementById(id);
let current = null;
let busy = false;
let delivery = null;
let deliveryTimer = null;
let deliveryChecks = 0;
let analysis = null;
let analysisTimer = null;
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
  $("create").disabled = busy || !$("account").value || Boolean(analysis && !["ready", "completed", "failed"].includes(analysis.status));
  $("refresh").disabled = busy || !$("account").value;
  $("account").disabled = busy;
  $("history").disabled = busy;
  $("send").disabled = busy || !current || !["not-requested", "other-channel"].includes(delivery?.status);
  $("print").disabled = busy || !current;
  $("download").disabled = busy || current?.version !== "ai-portfolio-report-v2";
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
  $("history").replaceChildren(...data.reports.map(r => new Option(`${reportDate(r.createdAt)} · ${r.version === "ai-portfolio-report-v2" ? "전체 AI 분석" : "과거 자료 모음"} · ${r.coverage.subjects}종목`, r.reportId)));
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
  const data = await api("/api/ai-reports/analyses", {accountId, requestId, sendWhenReady: $("auto-send").checked});
  sessionStorage.removeItem(key);
  showAnalysis(data.analysis);
});
$("send").onclick = () => action(async () => {
  const data = await api(`/api/ai-reports/${current.reportId}/send`, {accountId: current.accountId});
  delivery = data.delivery; $("delivery").textContent = deliveryLabel(delivery);
  deliveryChecks = 0; pollDelivery();
});
$("refresh").onclick = () => action(async () => { await refresh(current?.reportId || ""); await loadAnalysis(analysis?.analysisId || ""); });
$("history").onchange = () => action(() => open($("history").value));
$("account").onchange = () => action(async () => { clearTimeout(analysisTimer); analysis = null; $("analysis-progress").textContent = ""; await refresh(); await loadAnalysis(); });
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
  await loadAnalysis(params.get("analysisId") || "");
});


function showAnalysis(value) {
  analysis = value;
  $("analysis-progress").textContent = analysisLabel(value);
  clearTimeout(analysisTimer);
  controls();
  if (!value) return;
  if (!["ready", "completed", "failed"].includes(value.status)) {
    history.replaceState(null, "", `?accountId=${encodeURIComponent(value.accountId)}&analysisId=${value.analysisId}`);
    analysisTimer = setTimeout(() => { if (!busy) loadAnalysis(value.analysisId).catch(error => { $("analysis-progress").textContent = error.message; }); else showAnalysis(value); }, 15000);
  }
}
async function loadAnalysis(id = "") {
  const accountId = $("account").value;
  const suffix = /^[0-9a-f]{64}$/.test(id) ? "/" + id : "";
  const data = await api(`/api/ai-reports/analyses${suffix}?accountId=${encodeURIComponent(accountId)}`);
  if ($("account").value !== accountId) return;
  showAnalysis(data.analysis);
  if (data.analysis?.reportId && current?.reportId !== data.analysis.reportId) {
    await refresh(data.analysis.reportId); controls();
  }
}
$("download").onclick = () => action(async () => {
  const data = await api(`/api/ai-reports/${current.reportId}/document?accountId=${encodeURIComponent(current.accountId)}`);
  const url = URL.createObjectURL(new Blob([data.document], {type: "text/html;charset=utf-8"}));
  const link = document.createElement("a"); link.href = url; link.download = `orbit-alpha-${current.reportId.slice(0, 12)}.html`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
