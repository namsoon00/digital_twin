const $ = (id) => document.getElementById(id);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const labels = {pending:"대기",processing:"진행 중",completed:"완료",failed:"실패",running:"완료 미확인",observe:"관찰·재검토",research:"추가 조사"};
const workloads = {"independent-observation":"독립 관찰", "news-analysis":"뉴스 분석", "disclosure-analysis":"공시 분석", "model-review":"모델 검토", "research-planning":"조사 계획", "hypothesis-proposal":"가설 제안", "rule-change-proposal":"규칙 개선 제안", "investment-judgement":"투자 판단", "interactive-chat":"대화"};
const qualityLabels = {accepted:"문장·새로움 검토 통과",rejected:"품질 검토 보류","awaiting-review":"독립 검토 미완료","observation-only":"관찰 기록 · 발송 제안 없음"};
const followUpLabels = {pending:"관찰 중",triggered:"조건 전환 확인",expired:"기간 만료 · 평가 불가",unavailable:"자료 부족 · 평가 불가"};
const publicationLabels = {queued:"발송 검증 대기",recorded:"관찰 기록",suppressed:"발송 보류",done:"발송 완료",failed:"전송 재시도",processing:"전송 중",pending:"전송 대기"};
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
  ${r.publication ? `<p class="muted">알림: ${escape(publicationLabels[r.publication.deliveryStatus || r.publication.status] || r.publication.status)} · ${escape(r.publication.reason)}</p>` : ""}
  ${r.quality ? `<p class="muted">설명 검증: ${escape(qualityLabels[r.quality.status] || r.quality.status)}${r.quality.errors?.length ? " · " + escape(r.quality.errors.join(" / ")) : ""}</p>` : ""}
  ${r.summary ? `<p>${escape(r.summary)}</p>` : `<p class="muted">${escape(reasons[r.stopReason] || r.reason || (task.lastError ? "작업에 실패해 재확인이 필요합니다." : task.status === "pending" ? "예약된 시점에 확인합니다." : "아직 분석 결과가 없습니다."))}</p>`}
  ${quote ? `<p>근거 시점 가격 <strong>${escape(Number(quote.currentPrice).toLocaleString("ko-KR"))} ${escape(quote.currency)}</strong>${quote.changeRate != null ? ` · 등락 ${escape(quote.changeRate)}%` : ""}<br><small>시세 기준 ${escape(date(quote.sourceAsOf || quote.asOf || quote.updatedAt))}</small></p>` : ""}
  <div class="analysis">${section("가능한 설명 · 가설",r.hypothesis)}${section("이전 알림과 비교",r.comparison)}${section("내 보유·관심 상황에서의 의미",r.portfolioImpact)}${section("반대 근거와 한계",r.counterEvidence)}${section("다음에 확인할 질문",(r.questions || []).join(" / "))}</div>
  ${r.followUpConditions?.length ? `<details><summary>등록한 확인 조건 ${r.followUpConditions.length}개</summary>${r.followUpConditions.map((row) => `<p>${escape(row.description)}<br><small>${escape(date(row.expiresAt))}까지 다음 관찰에서 확인</small></p>`).join("")}</details>` : ""}
  ${r.followUpEvaluations?.length ? `<details><summary>이전 설명의 확인 결과</summary>${r.followUpEvaluations.map((row) => `<p>${escape(row.description)} · ${escape(followUpLabels[row.status] || row.status)}<br><small>${escape(row.reason)}</small></p>`).join("")}</details>` : ""}
  ${r.publication?.receipt ? `<details><summary>실제 발송 원문 · ${escape(date(r.publication.receipt.deliveredAt))}</summary><pre>${escape(r.publication.receipt.body || "원문 보존 기간이 지나 본문을 표시할 수 없습니다.")}</pre></details>` : ""}
  ${r.claimEvidence ? `<details><summary>문장별 인용과 검토 기록</summary><pre>${escape(JSON.stringify({claims:r.claimEvidence,review:r.quality?.review},null,2))}</pre></details>` : ""}
  ${task.status === "pending" ? `<p class="muted">다음 확인 ${escape(date(task.nextCheckAt))}</p>` : ""}
  ${facts.length ? `<details><summary>사용한 사실 ${facts.length}개 · 시점과 출처 보기</summary><pre>${escape(JSON.stringify(r.input,null,2))}</pre></details>` : ""}</article>`;
}
async function load() {
  if (loading) return;
  loading = true;
  try {
    const data = await request("/api/ai-control/status");
    $("error").hidden = true;
    $("overview").innerHTML = `<p>문장 검토 통과 ${escape(data.qualitySummary?.accepted ?? 0)}건 · 보류 ${escape(data.qualitySummary?.rejected ?? 0)}건. 검토 통과는 투자 가설의 적중이나 유료 가치 인증을 뜻하지 않습니다.</p><p>기존 규칙 기반 투자 알림은 종료했습니다. AI가 마지막 발송과 비교해 새로운 해석이 있을 때 알립니다. 같은 종목은 일반 관찰 3시간, 등록한 반증 조건의 새 전환은 1시간 간격이며 하루 최대 2회 · 계정당 하루 최대 8회입니다. 일일 한도는 UTC 기준입니다.</p><div class="metrics"><div class="metric">독립 관찰 설정<strong>${data.enabled ? "사용" : "일시 중지"}</strong></div><div class="metric">오늘 시작한 작업<strong>${escape(data.tasksStartedToday)} / ${escape(data.dailyTaskBudget)}</strong></div><div class="metric">대기·진행 작업<strong>${escape(data.activeTaskCount)}</strong></div></div>`;
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
