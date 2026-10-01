const $ = (id) => document.getElementById(id);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const labels = {pending:"대기",processing:"진행 중",completed:"완료",failed:"실패",running:"완료 미확인",observe:"관찰·재검토",research:"추가 조사"};
const workloads = {"independent-observation":"독립 관찰", "news-analysis":"뉴스 분석", "disclosure-analysis":"공시 분석", "model-review":"모델 검토", "research-planning":"조사 계획", "hypothesis-proposal":"가설 제안", "rule-change-proposal":"규칙 개선 제안", "investment-judgement":"투자 판단", "interactive-chat":"대화"};
const reasons = {"no-new-research-path":"추가로 조사할 자료 경로를 찾지 못했습니다. 다음 관찰에서 다시 확인합니다.", "requirements-met":"요청한 자료 확인을 마쳤습니다.", "round-budget-exhausted":"이번 조사 한도까지 확인했습니다.", "cooldown":"최근 조사 결과를 활용하고 다음 확인을 기다립니다."};
const date = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("ko-KR") : "미정";
let loading = false;
let dirty = false;
async function request(url, options) {
  const response = await fetch(url, {cache:"no-store", ...options});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "AI 기록을 불러오지 못했습니다.");
  return payload;
}
function card(task) {
  const r = task.result || {};
  const facts = r.input?.facts || [];
  const quote = facts.find((fact) => fact.currentPrice != null && Number(fact.currentPrice) > 0);
  const section = (name, text) => text ? `<div><h4>${name}</h4><p>${escape(text)}</p></div>` : "";
  return `<article><header><div><h3>${escape(r.input?.name || task.name || task.symbol)} · ${escape(labels[task.capability] || task.capability)}</h3><small>${escape(task.symbol)} · ${escape(date(task.updatedAt))}</small></div><span>${escape(labels[task.status] || task.status)}</span></header>
  ${r.summary ? `<p>${escape(r.summary)}</p>` : `<p class="muted">${escape(reasons[r.stopReason] || r.reason || (task.lastError ? "작업에 실패해 재확인이 필요합니다." : task.status === "pending" ? "예약된 시점에 확인합니다." : "아직 분석 결과가 없습니다."))}</p>`}
  ${quote ? `<p>근거 시점 가격 <strong>${escape(Number(quote.currentPrice).toLocaleString("ko-KR"))} ${escape(quote.currency)}</strong>${quote.changeRate != null ? ` · 등락 ${escape(quote.changeRate)}%` : ""}<br><small>시세 기준 ${escape(date(quote.sourceAsOf || quote.asOf || quote.updatedAt))}</small></p>` : ""}
  <div class="analysis">${section("가능한 설명 · 가설",r.hypothesis)}${section("이전 분석과 비교",r.comparison)}${section("반대 근거와 한계",r.counterEvidence)}${section("다음에 확인할 질문",(r.questions || []).join(" / "))}</div>
  ${task.status === "pending" ? `<p class="muted">다음 확인 ${escape(date(task.nextCheckAt))}</p>` : ""}
  ${facts.length ? `<details><summary>사용한 사실 ${facts.length}개 · 시점과 출처 보기</summary><pre>${escape(JSON.stringify(r.input,null,2))}</pre></details>` : ""}</article>`;
}
async function load() {
  if (loading) return;
  loading = true;
  try {
    const data = await request("/api/ai-control/status");
    $("error").hidden = true;
    $("overview").innerHTML = `<div class="metrics"><div class="metric">독립 관찰 설정<strong>${data.enabled ? "사용" : "일시 중지"}</strong></div><div class="metric">오늘 시작한 작업<strong>${escape(data.tasksStartedToday)} / ${escape(data.dailyTaskBudget)}</strong></div><div class="metric">대기·진행 작업<strong>${escape(data.activeTaskCount)}</strong></div></div>`;
    if (!dirty) { $("enabled").checked = data.configuredEnabled; $("taskBudget").value = data.dailyTaskBudget; $("callBudget").value = data.dailyCallBudget; }
    const analyses = data.tasks.filter((task) => task.result?.summary);
    const queue = data.tasks.filter((task) => !task.result?.summary);
    $("tasks").innerHTML = (analyses.map(card).join("") || '<p class="panel">첫 관찰을 준비하고 있습니다. 유효한 계정·종목·그래프 근거가 준비되면 작업을 시작합니다.</p>') + `<details><summary>예약·조사·처리 기록 ${queue.length}개</summary>${queue.map(card).join("")}</details>`;
    $("calls").innerHTML = '<table><thead><tr><th>작업</th><th>실행 상태</th><th>횟수</th></tr></thead><tbody>' + data.callsToday.map((row) => `<tr><td>${escape(workloads[row.workload] || row.workload)}</td><td>${escape(labels[row.status] || row.status)}</td><td>${escape(row.count)}</td></tr>`).join("") + '</tbody></table>';
  } catch (error) { $("error").hidden = false; $("error").textContent = error.message; }
  finally { loading = false; }
}
$("refresh").addEventListener("click", load);
$("settings").addEventListener("input", () => { dirty = true; });
$("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await request("/api/ai-control/settings", {method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({aiControlEnabled:$("enabled").checked ? "true" : "false",aiControlDailyTaskBudget:$("taskBudget").value,aiControlDailyCallBudget:$("callBudget").value})});
    dirty = false; $("saveStatus").textContent = "저장했습니다. 다음 작업부터 적용됩니다."; await load();
  } catch (error) { $("saveStatus").textContent = error.message; }
});
load();
setInterval(() => { if (!document.hidden) load(); },30000);
