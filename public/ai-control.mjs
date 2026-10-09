const $ = (id) => document.getElementById(id);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const labels = {pending:"대기",processing:"진행 중",completed:"완료",failed:"실패",running:"완료 미확인",observe:"관찰·재검토",research:"추가 조사"};
const workloads = {"independent-observation":"독립 관찰", "news-analysis":"뉴스 분석", "disclosure-analysis":"공시 분석", "model-review":"모델 검토", "research-planning":"조사 계획", "hypothesis-proposal":"가설 제안", "rule-change-proposal":"규칙 개선 제안", "investment-judgement":"투자 판단", "interactive-chat":"대화"};
const qualityLabels = {accepted:"문장·새로움 검토 통과",rejected:"품질 검토 보류","awaiting-review":"독립 검토 미완료","observation-only":"관찰 기록 · 발송 제안 없음"};
const followUpLabels = {pending:"관찰 중",triggered:"조건 전환 확인",expired:"기간 만료 · 평가 불가",unavailable:"자료 부족 · 평가 불가"};
const publicationLabels = {queued:"발송 검증 대기",recorded:"관찰 기록",suppressed:"발송 보류",done:"발송 완료",failed:"전송 실패",processing:"전송 중",pending:"전송 대기","retry-wait":"전송 재시도 대기","recovery-wait":"자동 복구 대기","receipt-unconfirmed":"전송 영수증 미확인",expired:"알림 유효시간 만료",superseded:"알림 대체·만료",unknown:"전송 상태 미확인"};
const developmentLabels = {pending:"가설 제안 대기",processing:"가설 개발 중",completed:"제안 처리 완료",failed:"제안 처리 실패",blocked:"검증 보류",unavailable:"현재 진행 기록 없음",proposed:"가설 접수",screening:"가설 구조 검토",compiled:"후보 작성",validating:"검증 중",validated:"검증 통과",rejected:"후보 기각","needs-data":"자료 대기","needs-revision":"명세 수정 필요","approval-required":"검토 대기","shadow-observing":"격리 실험 관측 중","adoption-ready":"운영 반영 검증 중","evolution-monitoring":"채택 후 검증 중",strengthened:"사후 검증 완료",superseded:"기준 버전 변경으로 종료","rolled-back":"이전 버전 복원",retired:"실험 종료"};
const reasons = {"no-new-research-path":"추가로 조사할 자료 경로를 찾지 못했습니다. 다음 관찰에서 다시 확인합니다.", "requirements-met":"요청한 자료 확인을 마쳤습니다.", "round-budget-exhausted":"이번 조사 한도까지 확인했습니다.", "cooldown":"최근 조사 결과를 활용하고 다음 확인을 기다립니다."};
const date = (value) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("ko-KR") : "미정";
let loading = false;
let dirty = false;
let brainDirty = false;
let brainCases = [];
const brainLabels = {open:"질문 등록",waiting:"자료·조사 대기","review-needed":"조사 결과 재검토",blocked:"추가 자료·기능 필요",answered:"AI 답변 기록",dismissed:"검토 종료",proposed:"개선 제안",planned:"개선 계획",implemented:"반영 신고 · 효과 미검증"};
const categoryLabels = {analysis:"분석 품질",data:"데이터",experience:"이용 경험",operations:"운영"};
function continuityView(result) {
  const coverage = result.memoryCoverage;
  const macro = (result.input?.facts || []).filter((row) => row.evidenceCategory === "macro");
  const intents = result.researchRequest ? [{researchRequest:result.researchRequest}] : (result.workQuestions || []);
  const searches = intents.filter((row) => row.researchRequest?.queryTerms?.length).map(({question,researchRequest:intent}) => `<p>${question ? `${escape(question)}<br>` : ""}질문별 검색어 · ${intent.queryTerms.map(escape).join(" / ")}<br><small>출처 · ${(intent.sourceTypes || []).map(escape).join(" / ")} · 자료 기간 ${escape(intent.maxAgeMinutes)}분 이내</small></p>`).join("");
  const memory = coverage?.continuityVersion ? `<p>기본 기억 · 이전 판단 ${escape(coverage.requiredAnalyses || 0)}건 · 과제·조사·피드백 ${escape(coverage.requiredResearch || 0)}건<br><small>과거 해석과 작업 상태이며 현재 시장 사실과 구분합니다.</small></p>` : "";
  const context = macro.length ? `<p>거시 근거 ${escape(macro.length)}개 · ${macro.map((row) => escape(row.label || row.seriesId || row.kind)).join(" / ")}</p>` : "";
  return memory || searches || context ? `<details class="continuity"><summary>판단에 연결한 기억·거시 자료·검색 조건</summary>${memory}${context}${searches}</details>` : "";
}
function retrievalView(retrieval) {
  if (!retrieval) return "";
  const states = {ready:"조회 완료",deferred:"자료 부족으로 보류","round-limit":"이번 조회 한도 도달","repeated-read":"중복 조회로 중단","context-budget":"입력 용량 한도로 보류","budget-fallback":"호출 여유 부족 · 기본 근거로 관찰","invalid-request":"조회 요청 오류"};
  const tools = {query_facts:"근거 조회",read_fact:"선택한 근거 확인",recall_memory:"과거 기억 조회"};
  const categories = {quote:"시세",valuation:"가치 평가",company:"기업·재무",research:"조사 자료",macro:"거시 지표",technical:"가격 흐름",flow:"수급",quality:"자료 품질",analyses:"과거 판단",memories:"조사·질문·피드백"};
  return `<details class="retrieval"><summary>AI가 조회한 과정 · ${escape(states[retrieval.status] || retrieval.status)}</summary>${(retrieval.steps || []).map((step, index) => `<p><strong>${index + 1}. ${step.correction ? "요청 보정 · " : ""}${escape(step.reason)}</strong></p>${(step.errors || []).map((error) => `<p class="muted">요청 규격 오류 · ${escape(error.field)} · 허용 형식: ${escape(typeof error.expected === "string" ? error.expected : JSON.stringify(error.expected))}</p>`).join("")}${(step.reads || []).map((read) => `<p>${escape(tools[read.request?.tool] || "조회")} · ${escape(categories[read.request?.category] || read.request?.category)} · ${read.request?.tool === "recall_memory" ? "현재 사실과 구분해 참고" : `사실 ${escape(read.factIds?.length || 0)}개`}<br><small>${read.status === "ok" ? "조회 결과 기록" : escape(states[read.status] || "조회 제한 또는 자료 확인 필요")}${read.omitted?.length ? ` · 용량 한도로 제외 ${escape(read.omitted.length)}개` : ""}${read.nextCursor || read.nextOffset != null ? " · 다음 페이지 있음" : ""}</small></p>`).join("")}`).join("")}<p class="muted">동일한 시점의 근거에서 선택해 읽었습니다. 조회하지 않은 자료가 없다는 뜻은 아닙니다.</p></details>`;
}
function brainView(brain = {}) {
  brainCases = brain.cases || [];
  const cards = brainCases.map((row) => {
    const feedback = row.kind === "service-feedback";
    const origin = row.origin || {};
    return `<div class="panel brain-card"><header><h3>${escape(row.symbol)} · ${escape(feedback ? categoryLabels[row.category] : "지속 질문")}</h3><span>${escape(brainLabels[row.status] || row.status)}</span></header>
      <p><strong>${escape(feedback ? row.problem : row.question)}</strong></p>
      ${feedback ? `<p>${escape(row.proposal)}</p><p class="muted">개선 확인 기준: ${escape(row.verification)}</p>` : `<p>${escape(row.reason)}</p><p class="muted">완료 기준: ${escape(row.completionCriterion)}<br>다음 확인 ${escape(date(row.nextCheckAt))} · 연결된 조사 ${escape(row.researchAttempts || 0)}회</p>`}
      <details><summary>처음 생긴 이유와 근거 · ${escape(date(origin.capturedAt))}</summary><p>${escape(origin.summary)}</p><p>${escape(origin.hypothesis)}</p><p class="muted">반대 근거: ${escape(origin.counterEvidence)}</p><pre>${escape(JSON.stringify({sourceSnapshots:origin.sourceSnapshots,evidence:origin.evidence},null,2))}</pre></details>
      <details><summary>진행과 평가 이력</summary>${(row.history || []).map(event => `<p>${escape(date(event.at))} · ${escape(brainLabels[event.status] || event.status)}<br>${escape(event.reason)}</p><pre>${escape(JSON.stringify(event.details || {},null,2))}</pre>`).join("") || '<p>기록이 없습니다.</p>'}</details>
      ${feedback ? `<form class="feedback-review" data-case="${escape(row.caseId)}"><label>검토 결과 <select name="status"><option value="planned">개선 계획에 반영</option><option value="implemented">반영 사실 기록</option><option value="dismissed">사유를 남기고 종료</option></select></label><label>검토 사유 또는 확인한 변경 <textarea name="note" minlength="8" maxlength="600" required placeholder="어떤 변경을 했거나 계획했는지 적어 주세요."></textarea></label><button type="submit">검토 기록</button><button type="reset">입력 취소</button><span class="feedback-status" role="status"></span></form><p class="muted">반영 사실 기록은 소유자의 보고입니다. 개선 효과의 입증과 구분하며 다음 AI 분석에서 참고합니다.</p>` : ""}
      </div>`;
  }).join("");
  return `<p class="muted">${(brain.goals || []).map(escape).join(" · ")}</p>${cards || '<p class="panel">아직 등록된 지속 질문이나 개선 제안이 없습니다. 새 관찰에서 근거가 있는 질문과 제안을 등록합니다.</p>'}`;
}
async function request(url, options) {
  const response = await fetch(url, {cache:"no-store", ...options});
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "AI 기록을 불러오지 못했습니다.");
  return payload;
}
function deliveryProgress(publication) {
  if (!publication?.jobId || publication.receipt) return "";
  return `${publication.deliveryAttempts != null ? ` · 처리 시도 ${escape(publication.deliveryAttempts)}/${escape(publication.deliveryMaxAttempts)}회` : ""}${publication.deliveryRetryAt ? ` · 다음 재시도 ${escape(date(publication.deliveryRetryAt))}` : ""}`;
}
function card(task, scheduling = {}) {
  const r = task.result || {};
  const diagnostic = r.publication?.diagnostic;
  const development = task.developmentProgress;
  const facts = r.input?.facts || [];
  const quote = facts.find((fact) => fact.currentPrice != null && Number(fact.currentPrice) > 0);
  const section = (name, text) => text ? `<div><h4>${name}</h4><p>${escape(text)}</p></div>` : "";
  return `<article><header><div><h3>${escape(r.input?.name || task.name || task.symbol)} · ${escape(labels[task.capability] || task.capability)}</h3><small>${escape(task.symbol)} · ${escape(date(task.updatedAt))}</small></div><span>${escape(labels[task.status] || task.status)}</span></header>
  ${r.publication ? `<p class="muted">알림: ${escape(publicationLabels[r.publication.deliveryStatus || r.publication.status] || r.publication.status)}${deliveryProgress(r.publication)} · ${escape(r.publication.reason)}</p>` : ""}
  ${diagnostic ? `<p class="muted">검증 미통과 초안 · 운영 알림: ${escape(publicationLabels[diagnostic.deliveryStatus || diagnostic.status] || diagnostic.status)}${deliveryProgress(diagnostic)} · ${escape(diagnostic.reason)}</p>` : ""}
  ${r.quality ? `<p class="muted">설명 검증: ${escape(qualityLabels[r.quality.status] || r.quality.status)}${r.quality.errors?.length ? " · " + escape(r.quality.errors.join(" / ")) : ""}</p>` : ""}
  ${r.quality?.diagnostics?.length || r.failure?.stage ? `<details><summary>검증 단계와 원인 분류</summary><pre>${escape(JSON.stringify(r.quality?.diagnostics || r.failure,null,2))}</pre></details>` : ""}
  ${r.dataRecovery ? '<p class="muted">현재 자료로는 확인 조건을 평가할 수 없습니다. 다음 관찰에서 자료를 다시 조회합니다.</p>' : ''}
  ${r.resumption?.scheduledAt ? `<p class="muted">예약 ${escape(date(r.resumption.scheduledAt))} · 실제 자료 확인 ${escape(date(r.resumption.capturedAt))}. 중단 기간의 관찰을 소급한 결과가 아닙니다.${r.resumption.coalescedTaskCount ? ` 동일 예약 ${escape(r.resumption.coalescedTaskCount)}건을 함께 처리했습니다.` : ''}</p>` : ''}
  ${r.development?.requestId ? `<details><summary>온톨로지 개선 · ${escape(developmentLabels[development?.status || "unavailable"] || development.status)}</summary><p>${escape(development?.question || (r.developmentQuestions || []).join(" / "))}</p>${r.development.reused ? '<p class="muted">같은 날의 기존 요청에 연결했습니다.</p>' : ''}${(development?.cases || []).map((row) => `<p>${escape(developmentLabels[row.status] || row.status)}${row.blockedReason ? " · " + escape(row.blockedReason) : ""}</p>`).join("")}<p class="muted">요청 접수와 실험 준비는 개선 효과의 입증이 아닙니다. 실제 결과 비교와 운영 검증을 거쳐 반영하며, 진행 결과는 다음 AI 분석에 전달됩니다.</p></details>` : r.development?.status === "blocked" ? `<p class="muted">온톨로지 개선 보류 · ${escape(r.development.reason)}</p>` : ""}
  ${r.summary ? `<p>${escape(r.summary)}</p>` : `<p class="muted">${escape((r.failure?.kind === "retrieval-contract" ? "AI 조회 요청이 규격을 충족하지 못했습니다. 판단 작성 전 중단했으며 오류와 재시도 기록을 확인할 수 있습니다." : "") || reasons[r.stopReason] || r.reason || (task.status === "pending" && scheduling.status === "budget-wait" ? "오늘 AI 사용 한도로 관찰을 기다리고 있습니다." : task.lastError === "ai-execution:recovery-wait" ? "AI 실행 오류로 대기 중이며 복구 시 새 자료로 다시 확인합니다." : task.lastError?.startsWith("ai-call-budget") ? "AI 호출 여유가 생기면 관찰을 다시 시작합니다." : task.lastError ? "작업에 실패해 재확인이 필요합니다." : task.status === "pending" ? "예약된 시점에 확인합니다." : "아직 분석 결과가 없습니다."))}</p>`}
  ${quote ? `<p>근거 시점 가격 <strong>${escape(Number(quote.currentPrice).toLocaleString("ko-KR"))} ${escape(quote.currency)}</strong>${quote.changeRate != null ? ` · 등락 ${escape(quote.changeRate)}%` : ""}<br><small>시세 기준 ${escape(date(quote.sourceAsOf || quote.asOf || quote.updatedAt))}</small></p>` : ""}
  <div class="analysis">${section("가능한 설명 · 가설",r.hypothesis)}${section("이전 알림과 비교",r.comparison)}${section("내 보유·관심 상황에서의 의미",r.portfolioImpact)}${section("반대 근거와 한계",r.counterEvidence)}${section("다음에 확인할 질문",(r.questions || []).join(" / "))}</div>
  ${retrievalView(r.input?.retrieval || r.failure?.retrieval)}
  ${continuityView(r)}
  ${r.followUpConditions?.length ? `<details><summary>등록한 확인 조건 ${r.followUpConditions.length}개</summary>${r.followUpConditions.map((row) => `<p>${escape(row.description)}<br><small>${escape(date(row.expiresAt))}까지 다음 관찰에서 확인</small></p>`).join("")}</details>` : ""}
  ${r.followUpEvaluations?.length ? `<details><summary>이전 설명의 확인 결과</summary>${r.followUpEvaluations.map((row) => `<p>${escape(row.description)} · ${escape(followUpLabels[row.status] || row.status)}<br><small>${escape(row.reason)}</small></p>`).join("")}</details>` : ""}
  ${r.publication?.receipt ? `<details><summary>실제 발송 원문 · ${escape(date(r.publication.receipt.deliveredAt))}</summary><pre>${escape(r.publication.receipt.body || "원문 보존 기간이 지나 본문을 표시할 수 없습니다.")}</pre></details>` : ""}
  ${diagnostic?.receipt ? `<details><summary>운영 채널로 보낸 미검증 원문 · ${escape(date(diagnostic.receipt.deliveredAt))}</summary><pre>${escape(diagnostic.receipt.body || "원문 보존 기간이 지나 본문을 표시할 수 없습니다.")}</pre></details>` : ""}
  ${r.claimEvidence ? `<details><summary>문장별 인용과 검토 기록</summary><pre>${escape(JSON.stringify({claims:r.claimEvidence,review:r.quality?.review},null,2))}</pre></details>` : ""}
  ${task.status === "pending" ? `<p class="muted">다음 확인 ${escape(date(scheduling.status === "budget-wait" && Date.parse(scheduling.nextCheckAt) > Date.parse(task.nextCheckAt) ? scheduling.nextCheckAt : task.nextCheckAt))}</p>` : ""}
  ${facts.length ? `<details><summary>사용한 사실 ${facts.length}개 · 시점과 출처 보기</summary><pre>${escape(JSON.stringify(r.input,null,2))}</pre></details>` : ""}</article>`;
}
const healthLabels = {"runtime-unconfirmed":"워커 생존 미확인","awaiting-result":"관찰 저장 확인 중",healthy:"호출 정상",recovering:"복구 확인 중",unavailable:"실행 오류 · 재시도 대기",degraded:"오류 확인 필요",delayed:"관찰 지연",paused:"일시 중지",idle:"예약 대기"};
function healthView(health) {
  if (!health) return '<p role="status">AI 실행 상태를 아직 확인하지 못했습니다.</p>';
  const failure = health.sharedExecution?.lastFailure;
  const runtime = health.runtime;
  const coverage = health.runtimeCoverage;
  const runtimeText = runtime ? `<p class="muted">이번 생존 확인 구간 시작 ${escape(date(runtime.startedAt))} · 마지막 생존 확인 ${escape(date(runtime.lastSeenAt))}<br>이번 구간에서 판단 저장 ${health.judgmentSavedSinceResume ? '확인' : '미확인'} · 관찰 확인 ${health.observationCheckedSinceResume ? '확인' : '미확인'}${health.carriedOverdueObservationTasks ? `<br>재시작 전 이월 예약 ${escape(health.carriedOverdueObservationTasks)}건 · 재시작 후 20분부터 처리 지연을 평가합니다.` : ''}${coverage && coverage.observedActiveSeconds != null ? `<br>최근 24시간 워커 생존 기록 ${escape(Math.round(coverage.observedActiveSeconds / 60))}분 · 기록 공백 ${escape(Math.round(coverage.unobservedSeconds / 60))}분. 기록 공백은 정확한 중단 시간이나 AI 실패 횟수를 뜻하지 않습니다.` : ''}</p>` : '';
  return `<div role="status"><p><strong>${escape(healthLabels[health.status] || "상태 미확인")}</strong> · ${escape(health.reason)}</p>${runtimeText}<p>최근 ${escape(health.callWindowMinutes)}분 중앙 AI 호출: 성공 ${escape(health.centralCallsCompleted)}회 · 실패 ${escape(health.centralCallsFailed)}회<br>마지막 판단 저장 ${escape(date(health.lastJudgmentAt))} · 마지막 관찰 확인 ${escape(date(health.lastObservationCheckAt))}${health.retryAt ? `<br>복구 재시도 ${escape(date(health.retryAt))}` : ""}</p>${failure ? `<p class="muted">마지막 실행 오류: ${escape(failure.message)} · ${escape(date(health.sharedExecution.lastFailureAt))}</p>` : ""}</div>`;
}
async function load() {
  if (loading) return;
  loading = true;
  try {
    const data = await request("/api/ai-control/status");
    $("error").hidden = true;
    const scheduling = data.observationScheduling || {};
    const waiting = data.enabled && scheduling.status === "budget-wait";
    $("overview").innerHTML = `${healthView(data.executionHealth)}${waiting ? `<p role="status"><strong>AI 관찰이 사용 한도로 대기 중입니다.</strong> ${scheduling.reason === "ai-task-budget-exhausted" ? "오늘 작업 시작 한도에 도달했습니다." : "새 분석과 독립 검토에 필요한 호출 여유가 없습니다."} 한도 갱신 ${escape(date(scheduling.nextCheckAt))}. 설정에서 한도를 늘리면 다음 실행부터 다시 확인합니다.</p>` : ""}<p>문장 검토 통과 ${escape(data.qualitySummary?.accepted ?? 0)}건 · 보류 ${escape(data.qualitySummary?.rejected ?? 0)}건. 검토 통과는 투자 가설의 적중이나 유료 가치 인증을 뜻하지 않습니다.</p><p>기존 규칙 기반 투자 알림은 종료했습니다. AI가 마지막 발송과 비교해 새로운 해석이 있을 때 알립니다. 같은 종목은 일반 관찰 3시간, 등록한 반증 조건의 새 전환은 1시간 간격이며 하루 최대 2회 · 계정당 하루 최대 8회입니다. 일일 한도는 UTC 기준입니다.</p><div class="metrics"><div class="metric">독립 관찰 상태<strong>${!data.enabled ? "일시 중지" : waiting ? "사용 한도 대기" : escape(healthLabels[data.executionHealth?.status] || "상태 미확인")}</strong></div><div class="metric">오늘 시작한 작업<strong>${escape(data.tasksStartedToday)} / ${data.budgetEnabled === false ? "제한 없음" : escape(data.dailyTaskBudget)}</strong></div><div class="metric">오늘 독립 AI 호출<strong>${escape(data.modelCallsUsedToday ?? 0)} / ${data.budgetEnabled === false ? "제한 없음" : escape(data.dailyCallBudget)}</strong></div><div class="metric">대기·진행 작업<strong>${escape(data.activeTaskCount)}</strong></div></div><p class="muted">관찰 분석과 발송 전 검토는 각각 호출을 사용합니다. 뉴스 분석 등 별도 작업의 호출은 아래 이력에 표시되며 독립 AI 한도와 구분됩니다.</p>`;
    if (!dirty) { $("enabled").checked = data.configuredEnabled; $("budgetEnabled").checked = data.budgetEnabled !== false; syncBudgetFields(); $("taskBudget").value = data.dailyTaskBudget; $("callBudget").value = data.dailyCallBudget; }
    if (!brainDirty) $("brain").innerHTML = brainView(data.brain);
    const analyses = data.tasks.filter((task) => task.result?.summary);
    const queue = data.tasks.filter((task) => !task.result?.summary);
    $("tasks").innerHTML = (analyses.map((task) => card(task, scheduling)).join("") || '<p class="panel">첫 관찰을 준비하고 있습니다. 유효한 계정·종목·그래프 근거가 준비되면 작업을 시작합니다.</p>') + `<details><summary>예약·조사·처리 기록 ${queue.length}개</summary>${queue.map((task) => card(task, scheduling)).join("")}</details>`;
    $("calls").innerHTML = '<table><thead><tr><th>작업</th><th>실행 상태</th><th>횟수</th></tr></thead><tbody>' + data.callsToday.map((row) => `<tr><td>${escape(workloads[row.workload] || row.workload)}</td><td>${escape(labels[row.status] || row.status)}</td><td>${escape(row.count)}</td></tr>`).join("") + '</tbody></table>';
  } catch (error) { $("error").hidden = false; $("error").textContent = error.message; }
  finally { loading = false; }
}
$("refresh").addEventListener("click", load);
$("brain").addEventListener("input", (event) => {
  const form = event.target.closest(".feedback-review");
  if (form) { form.dataset.dirty = "true"; brainDirty = true; }
});
$("brain").addEventListener("reset", (event) => {
  delete event.target.dataset.dirty;
  brainDirty = !!$("brain").querySelector('[data-dirty="true"]');
  if (!brainDirty) load();
});
$("brain").addEventListener("submit", async (event) => {
  const form = event.target.closest(".feedback-review");
  if (!form) return;
  event.preventDefault();
  const item = brainCases.find(row => row.caseId === form.dataset.case);
  const status = form.querySelector(".feedback-status");
  const button = form.querySelector('[type="submit"]');
  button.disabled = true;
  try {
    const saved = await request("/api/ai-control/feedback", {method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      caseId:item.caseId,accountId:item.accountId,symbol:item.symbol,revision:item.revision,
      status:form.elements.status.value,note:form.elements.note.value})});
    item.revision = saved.revision;
    form.reset();
    status.textContent = "검토 내용을 기록했습니다.";
    await load();
  } catch (error) { status.textContent = error.message; }
  finally { button.disabled = false; }
});
function syncBudgetFields() { for (const id of ["taskBudget", "callBudget"]) $(id).disabled = !$("budgetEnabled").checked; }
$("settings").addEventListener("input", () => { dirty = true; syncBudgetFields(); });
$("settings").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await request("/api/ai-control/settings", {method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({aiControlEnabled:$("enabled").checked ? "true" : "false",aiControlBudgetEnabled:$("budgetEnabled").checked ? "true" : "false",aiControlDailyTaskBudget:$("taskBudget").value,aiControlDailyCallBudget:$("callBudget").value})});
    dirty = false; $("saveStatus").textContent = "저장했습니다. 다음 작업부터 적용됩니다."; await load();
  } catch (error) { $("saveStatus").textContent = error.message; }
});
load();
setInterval(() => { if (!document.hidden) load(); },30000);
