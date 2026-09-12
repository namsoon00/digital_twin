import { editorWorkDetailPayload, renderWorkDetailButton } from "../navigation/detail.mjs";
import { renderConsoleEmpty } from "../shared/console.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { renderHypothesisDevelopmentPanel } from "../experiments/workspace.mjs";

function investmentModelOverviewWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Judgment Criteria",
    "현재 판단 기준",
    "활성 모델·규칙·가설과 데이터 연결",
    renderInvestmentModelOverview(false)
  );
}

function investmentModelManagementWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Investment Model Management",
    "투자모델 관리",
    "활성 릴리스와 변경 초안, 검증, 배포 이력을 분리해 관리합니다.",
    renderInvestmentModelManagementWorkspace()
  );
}

function renderInvestmentModelManagementTabs() {
  var payload = investmentModelPayload();
  var sections = Array.isArray(((payload.governance || {}).managementSections)) ? payload.governance.managementSections : [
    { id: "release", label: "활성 릴리스" }, { id: "inventory", label: "규칙·가설" },
    { id: "validation", label: "검증 게이트" }, { id: "changes", label: "변경 초안" },
    { id: "audit", label: "배포·감사" }
  ];
  return '<nav class="investment-model-management-tabs" role="tablist" aria-label="투자모델 관리 영역">' + sections.map(function (item) {
    var selected = decisionsState.investmentModelManagementTab === item.id;
    return '<button type="button" role="tab" data-investment-model-management-tab="' + escapeHtml(item.id) + '" aria-selected="' + (selected ? "true" : "false") + '"' + (selected ? ' class="active"' : '') + '>' + escapeHtml(item.label || item.id) + '</button>';
  }).join("") + '</nav>';
}

function renderInvestmentModelInventoryManagement(payload) {
  var inventory = payload.inventory || {};
  return [
    '<section class="investment-model-management-section"><header><span class="label">MODEL INVENTORY</span><strong>규칙과 가설의 운영 인벤토리</strong><p>활성 릴리스가 참조하는 개수이며 이 화면에서 원시 임계값을 직접 수정하지 않습니다.</p></header>',
    '<div class="investment-model-metrics"><div><span>TBox 클래스</span><strong>' + escapeHtml(Number(inventory.classes || 0)) + '</strong><em>개념</em></div><div><span>관계 유형</span><strong>' + escapeHtml(Number(inventory.relations || 0)) + '</strong><em>관계</em></div><div><span>실행 규칙</span><strong>' + escapeHtml(Number(inventory.rules || 0)) + '</strong><em>TypeDB</em></div><div><span>가설</span><strong>' + escapeHtml(Number(inventory.hypotheses || 0)) + '</strong><em>수명주기 포함</em></div></div>',
    renderInvestmentModelManagementActions(),
    '<div class="investment-model-policy-separation"><strong>실시간 운영 정책은 별도 관리</strong><p>알림 주기와 런타임 설정은 활성 추론 릴리스의 규칙을 직접 변경하지 않습니다.</p>' + renderWorkDetailButton("strategy-model-policy-editor", "", "레거시 런타임 정책 확인", "text-button compact") + '</div>',
    '</section>'
  ].join("");
}

function renderInvestmentModelChangeDraft(payload) {
  var active = payload.activeRelease || {};
  var candidate = payload.candidate || {};
  var relationLabels = { older: "활성보다 이전 버전", newer: "활성보다 신규 버전", same: "활성과 동일", unresolved: "비교 정보 부족" };
  return '<section class="investment-model-management-section"><header><span class="label">CHANGE DRAFT</span><strong>활성 릴리스와 비교 포인터</strong><p>신규 승격 후보와 롤백 보관본을 분리합니다.</p></header><div class="investment-model-release-compare"><article><span>현재 활성</span><strong>' + escapeHtml(active.deploymentId || "활성 릴리스 없음") + '</strong><em>' + escapeHtml(active.releaseShortHash || active.runtimeRevision || "식별자 미기록") + '</em></article><b aria-hidden="true">→</b><article><span>' + escapeHtml(candidate.roleLabel || "후보") + '</span><strong>' + escapeHtml(candidate.deploymentId || "후보 없음") + '</strong><em>' + escapeHtml(relationLabels[candidate.relationToActive] || "비교 정보 부족") + '</em></article></div><p class="investment-model-change-explanation">' + escapeHtml(candidate.explanation || "변경 초안이 등록되면 규칙·TBox·프롬프트·데이터 바인딩 차이를 표시합니다.") + '</p>' + (candidate.eligibleForPromotion ? renderWorkDetailButton("experiment-promotion-board", "", "후보 검증과 승격 이력", "text-button compact primary") : renderWorkDetailButton("experiment-audit-board", "", "롤백·배포 이력", "text-button compact")) + '</section>';
}

function renderInvestmentModelAudit(payload) {
  var governance = payload.governance || {};
  var release = payload.activeRelease || {};
  var stages = Array.isArray(governance.stages) ? governance.stages : [];
  return '<section class="investment-model-management-section"><header><span class="label">RELEASE AUDIT</span><strong>배포·롤백 감사</strong><p>현재 릴리스 식별자와 검증·승격 경로를 확인합니다.</p></header><dl class="investment-model-audit-ids"><div><dt>배포 ID</dt><dd>' + escapeHtml(release.deploymentId || "미기록") + '</dd></div><div><dt>릴리스 해시</dt><dd>' + escapeHtml(release.releaseFingerprint || "미기록") + '</dd></div><div><dt>RuleBox 해시</dt><dd>' + escapeHtml(release.ruleboxFingerprint || "미기록") + '</dd></div><div><dt>마지막 실행</dt><dd>' + escapeHtml(formatClock(release.lastRunAt) || "미기록") + '</dd></div></dl><div class="investment-model-lifecycle">' + stages.map(function (stage) { return '<span><i></i><strong>' + escapeHtml(stage) + '</strong></span>'; }).join("") + '</div><div class="investment-model-audit-actions">' + renderWorkDetailButton("experiment-audit-board", "", "배포 감사 원장", "text-button compact primary") + renderWorkDetailButton("strategy-trace-board", "", "추론 실행 원장", "text-button compact") + '</div></section>';
}

function renderInvestmentModelManagementWorkspace() {
  var payload = investmentModelPayload();
  var active = String(decisionsState.investmentModelManagementTab || "release");
  var body = active === "inventory" ? renderInvestmentModelInventoryManagement(payload)
    : active === "validation" ? renderInvestmentProductReadiness(payload.productReadiness || {}) + renderHypothesisDevelopmentPanel()
    : active === "changes" ? renderInvestmentModelChangeDraft(payload)
    : active === "audit" ? renderInvestmentModelAudit(payload)
    : renderInvestmentModelOverview(true);
  return renderInvestmentModelManagementTabs() + '<div class="investment-model-management-content" role="tabpanel">' + body + '</div>';
}

function investmentModelPayload() {
  return decisionsState.investmentModel && typeof decisionsState.investmentModel === "object" ? decisionsState.investmentModel : {};
}

function investmentModelStatusMeta(value) {
  var status = String(value || "unavailable");
  if (status === "warming") return { label: "준비 중", tone: "hold" };
  if (status === "ready" || status === "active" || status === "pass") return { label: "운영 중", tone: "watch" };
  if (status === "review" || status === "candidate") return { label: "점검 필요", tone: "caution" };
  return { label: "상태 확인", tone: "danger" };
}

function renderInvestmentProductReadiness(value) {
  var readiness = value && typeof value === "object" ? value : {};
  var gates = Array.isArray(readiness.gates) ? readiness.gates : [];
  var stage = String(readiness.stage || "internal-validation");
  var stageTone = readiness.generalAvailabilityReady ? "watch" : (readiness.closedBetaReady ? "caution" : "danger");
  var gateMarkup = gates.map(function (gate) {
    var passed = !!gate.passed;
    var detailTarget = String(gate.detailTarget || "investment-model-overview");
    return '<li class="' + (passed ? "pass" : "blocked") + '"><span aria-hidden="true">' + (passed ? "✓" : "!") + '</span><div><strong>' + escapeHtml(gate.label || gate.id || "검증 항목") + '</strong><p>' + escapeHtml(gate.detail || "상세 기록 없음") + '</p></div><em>' + (passed ? "통과" : "보완 필요") + '</em>' + renderWorkDetailButton(detailTarget, "", "상세", "text-button compact") + '</li>';
  }).join("");
  return [
    '<section class="investment-product-readiness" data-readiness-stage="' + escapeHtml(stage) + '">',
    '<header><div><span class="label">PRODUCT READINESS</span><strong>제품 출시 준비도</strong><p>엔진 실행 여부와 투자 판단 품질을 분리해 검증합니다.</p></div><span class="status-pill ' + escapeHtml(stageTone) + '">' + escapeHtml(readiness.stageLabel || "내부 검증") + '</span></header>',
    gates.length ? '<ul>' + gateMarkup + '</ul>' : '<p class="investment-product-readiness-empty">출시 품질 검증 자료를 아직 읽지 못했습니다.</p>',
    '<footer><strong>' + escapeHtml(readiness.releaseRecommended ? "정식 출시 검토 가능" : "현재 정식 출시 권고 안 함") + '</strong><span>규칙 변경과 출시 승격은 자동으로 수행하지 않습니다.</span></footer>',
    '</section>'
  ].join("");
}

function renderInvestmentModelContractNavigation() {
  var stages = [
    { id: "facts", label: "사실", detail: "입력·근거", target: "strategy-evidence-board" },
    { id: "relations", label: "관계", detail: "온톨로지", target: "strategy-graphs-board", section: "relations" },
    { id: "hypotheses", label: "가설", detail: "대안·반증", target: "strategy-graphs-board", section: "hypotheses" },
    { id: "inferences", label: "추론", detail: "규칙 실행", target: "strategy-graphs-board", section: "inferences" },
    { id: "decisions", label: "현재 의견", detail: "종목별 판단", target: "decision-action-queue" }
  ];
  return '<nav class="investment-model-contract" aria-label="판단 구조 상세 탐색">' + stages.map(function (stage, index) {
    return '<button type="button" data-work-detail="' + escapeHtml(stage.target) + '" data-work-detail-key="" data-model-contract-stage="' + escapeHtml(stage.id) + '"' + (stage.section ? ' data-ontology-catalog-section="' + escapeHtml(stage.section) + '"' : '') + '><span>' + escapeHtml(String(index + 1).padStart(2, "0")) + '</span><strong>' + escapeHtml(stage.label) + '</strong><em>' + escapeHtml(stage.detail) + '</em></button>';
  }).join("") + '</nav>';
}

function investmentStorageStatusLabel(status, health) {
  var currentStatus = String(status || "").toLowerCase();
  var currentHealth = String(health || "").toLowerCase();
  var role = currentStatus === "active" ? "활성" : (currentStatus === "candidate" ? "후보" : "상태 확인");
  var condition = ["ready", "healthy", "ok"].indexOf(currentHealth) >= 0 ? "정상" : (currentHealth ? "점검 필요" : "상태 확인");
  return role + " · " + condition;
}

function renderInvestmentModelBindings(bindings, release) {
  var graphId = String(bindings.graphStore || "");
  var timeSeriesId = String(bindings.timeSeries || "");
  var declaredTimeSeriesId = String(bindings.timeSeriesDeclared || "");
  var alignmentState = String(bindings.timeSeriesAlignmentState || "unknown");
  var mismatch = alignmentState === "mismatch";
  return [
    '<section class="investment-model-bindings" aria-label="활성 데이터 저장소">',
    '<div><span>그래프 저장소</span><strong>' + escapeHtml(bindings.graphStoreLabel || (graphId ? "TypeDB" : "미기록")) + '</strong><em>' + escapeHtml(String(bindings.graphStoreStatus || "active").toLowerCase() === "active" ? "활성" : "상태 확인") + '</em><small>' + escapeHtml(graphId || "저장소 ID 미기록") + '</small></div>',
    '<div data-binding-state="' + escapeHtml(mismatch ? "mismatch" : alignmentState) + '"><span>시계열 저장소</span><strong>' + escapeHtml(bindings.timeSeriesLabel || timeSeriesId || "미기록") + '</strong><em>' + escapeHtml(investmentStorageStatusLabel(bindings.timeSeriesStatus, bindings.timeSeriesHealth)) + '</em><small>' + escapeHtml(timeSeriesId || "백엔드 ID 미기록") + '</small></div>',
    '<div><span>마지막 추론</span><strong>' + escapeHtml(formatClock(release.lastRunAt || release.updatedAt) || "-") + '</strong><em>' + escapeHtml(release.deploymentId || "배포 ID 미기록") + '</em></div>',
    mismatch ? '<p class="investment-model-binding-warning"><strong>저장소 연결 기록 점검</strong><span>릴리스 기록 ' + escapeHtml(declaredTimeSeriesId || "미기록") + ' · 현재 활성 ' + escapeHtml(timeSeriesId || "미기록") + '</span></p>' : '',
    '</section>'
  ].join("");
}

function renderInvestmentModelEvolution(evolution) {
  var current = evolution && typeof evolution === "object" ? evolution : {};
  var observations = current.observations || {};
  var proposals = current.proposals || {};
  var validation = current.validation || {};
  var stages = Array.isArray(current.stages) ? current.stages : [];
  var labels = {
    observe: "관찰", propose: "제안", replay: "과거 재현", compare: "후보 비교",
    approve: "승인", candidate: "후보 운영", promote: "승격", monitor: "성과 추적", rollback: "롤백"
  };
  return [
    '<section class="investment-model-evolution" data-evolution-state="' + escapeHtml(current.state || "warming-up") + '">',
    '<header><div><span class="label">LEARNING LOOP</span><strong>판단 품질 진화</strong></div><em>' + escapeHtml(current.state === "review-required" ? "검토 제안 있음" : current.state === "observing" ? "성과 관찰 중" : "표본 준비 중") + '</em></header>',
    '<div class="investment-model-evolution-metrics">',
    '<div><span>결과 표본</span><strong>' + escapeHtml(Number(observations.eligibleOutcomeEpisodeCount || 0)) + '건</strong><em>연결 ' + escapeHtml(Number(observations.outcomeCoveragePct || 0).toFixed(1)) + '%</em></div>',
    '<div><span>사용자 평가</span><strong>' + escapeHtml(Number(observations.messageFeedbackSampleCount || 0)) + '건</strong><em>' + escapeHtml(Number(observations.messageFeedbackSampleCount || 0) > 0 && observations.messageHelpfulPct != null ? "도움됨 " + Number(observations.messageHelpfulPct).toFixed(1) + "%" : "평가 자료 부족") + '</em></div>',
    '<div><span>검토 제안</span><strong>' + escapeHtml(Number(proposals.reviewRequiredCount || 0)) + '건</strong><em>자동 생성·수동 승인</em></div>',
    '<div><span>후보 비교</span><strong>' + escapeHtml(Number(validation.comparisonSampleCount || 0)) + '건</strong><em>동일 입력 기준</em></div>',
    '</div>',
    '<div class="investment-model-evolution-stages">' + stages.map(function (stage) { return '<span>' + escapeHtml(labels[stage] || stage) + '</span>'; }).join('') + '</div>',
    '<p>실제 결과와 사용자 평가가 개선 제안을 만들고, 과거 재현·후보 비교·승인을 통과한 변경만 운영에 반영됩니다.</p>',
    '</section>'
  ].join("");
}

function renderInvestmentModelOverview(operator) {
  var payload = investmentModelPayload();
  if ((decisionsState.investmentModelLoading && !decisionsState.investmentModelLoaded) || payload.status === "warming") {
    return '<section class="investment-model-loading" aria-busy="true"><span class="spinner"></span><strong>활성 투자모델을 확인하는 중입니다</strong><p>릴리스·RuleBox·가설 인벤토리를 한 계약으로 읽고 있습니다.</p></section>';
  }
  if (decisionsState.investmentModelError && !decisionsState.investmentModelLoaded) {
    return renderConsoleEmpty("투자모델을 불러오지 못했습니다", decisionsState.investmentModelError, '<button class="text-button primary" type="button" data-action="refresh-investment-model">다시 시도</button>');
  }
  var model = payload.model || {};
  var release = payload.activeRelease || {};
  var inventory = payload.inventory || {};
  var validation = payload.validation || {};
  var bindings = payload.bindings || {};
  var cache = payload.cache || {};
  var productReadiness = payload.productReadiness || {};
  var meta = investmentModelStatusMeta(payload.status || release.status);
  var lifecycle = ((payload.governance || {}).stages || []).map(function (stage) {
    var labels = { draft: "초안", replay: "재현", compare: "비교", approval: "승인", candidate: "후보", promotion: "승격", observation: "관찰", retired: "폐기" };
    return '<span><i></i><strong>' + escapeHtml(labels[stage] || stage) + '</strong></span>';
  }).join("");
  var blockers = Array.isArray(validation.blockers) ? validation.blockers : [];
  var modelContract = operator ? [
    '<section class="investment-model-metrics">',
    '<div><span>실행 규칙</span><strong>' + escapeHtml(Number(inventory.rules || 0)) + '</strong><em>성립 조건 ' + escapeHtml(Number(inventory.conditions || 0)) + '</em></div>',
    '<div><span>관계 유형</span><strong>' + escapeHtml(Number(inventory.relations || 0)) + '</strong><em>TBox</em></div>',
    '<div><span>가설</span><strong>' + escapeHtml(Number(inventory.hypotheses || 0)) + '</strong><em>수명주기 포함</em></div>',
    '<div><span>승격 상태</span><strong>' + escapeHtml(validation.label || "확인") + '</strong><em>' + escapeHtml(validation.cohortId || "검증 코호트 없음") + '</em></div>',
    '</section>'
  ].join("") : [
    '<section class="investment-model-user-contract">',
    '<article><span>확인 사실</span><strong>가격·수급·기업·계정</strong><p>출처와 기준 시각이 저장된 값만 사용</p></article>',
    '<article><span>관계 추론</span><strong>TypeDB 규칙</strong><p>사실 사이의 성립 관계와 제한 조건 확인</p></article>',
    '<article><span>대안 검토</span><strong>지지·반박 가설</strong><p>한 방향만 선택하지 않고 반대 근거 비교</p></article>',
    '<article><span>최종 의견</span><strong>AI 판단</strong><p>TypeDB 허용 범위 안에서 행동 의견 작성</p></article>',
    '</section>'
  ].join("");
  var readinessMarkup = operator
    ? renderInvestmentProductReadiness(productReadiness)
    : '<details class="investment-model-user-readiness"><summary><span><strong>' + escapeHtml(productReadiness.stageLabel || "내부 검증") + '</strong><em>' + escapeHtml(productReadiness.releaseRecommended ? "정식 출시 검토 가능" : "정식 출시 전 검증 중") + '</em></span></summary>' + renderInvestmentProductReadiness(productReadiness) + '</details>';
  return [
    '<div class="investment-model-overview" data-model-status="' + escapeHtml(payload.status || "unavailable") + '">',
    '<section class="investment-model-identity">',
    '<div><span class="label">ACTIVE RELEASE</span><h3>' + escapeHtml(model.name || "Orbit Alpha 투자 판단 모델") + '</h3><p>' + escapeHtml(model.thesis || "관계와 반대 근거를 비교해 현재 투자 의견을 결정합니다.") + '</p></div>',
    '<aside><span class="status-pill ' + escapeHtml(meta.tone) + '">' + escapeHtml(meta.label) + '</span><strong>' + escapeHtml(release.deploymentId || "활성 릴리스 없음") + '</strong><em>release ' + escapeHtml(release.releaseShortHash || "-") + '</em></aside>',
    '</section>',
    renderInvestmentModelContractNavigation(),
    modelContract,
    renderInvestmentModelEvolution(payload.evolution || {}),
    readinessMarkup,
    renderInvestmentModelBindings(bindings, release),
    operator ? '<section class="investment-model-governance"><header><div><span class="label">RELEASE GOVERNANCE</span><strong>변경·승격 절차</strong></div><em>자동 승격 없음</em></header><div class="investment-model-lifecycle">' + lifecycle + '</div>' + (blockers.length ? '<div class="investment-model-blockers"><strong>운영 승격 차단</strong>' + blockers.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '<p>현재 릴리스는 운영 구조 검증을 통과했습니다. 제품 출시는 위 품질·성과·지연 게이트를 별도로 통과해야 합니다.</p>') + '</section>' : '<section class="investment-model-note"><strong>운영 상태와 출시 품질은 별도 기준입니다.</strong><p>규칙 편집과 승격은 운영 관리에서만 수행하며 AI 제안은 자동 반영하지 않습니다.</p></section>',
    '<div class="investment-model-refresh"><button class="text-button" type="button" data-action="refresh-investment-model"' + (decisionsState.investmentModelLoading || cache.refreshing ? " disabled" : "") + '>' + escapeHtml(decisionsState.investmentModelLoading || cache.refreshing ? "최신 상태 확인 중" : "상태 새로고침") + '</button>' + (!operator ? renderWorkDetailButton("investment-model-management", "", "운영자 모델 관리", "text-button compact") : "") + '</div>',
    '</div>'
  ].join("");
}

function renderInvestmentModelManagementActions() {
  return [
    '<section class="investment-model-actions">',
    '<header><span class="label">MODEL COMPONENTS</span><strong>모델 구성요소</strong><p>일반 설정과 분리된 판단 체계입니다.</p></header>',
    '<div>',
    renderWorkDetailButton("strategy-graphs-board", "", "온톨로지", "text-button compact"),
    renderWorkDetailButton("strategy-rulebox-editor", "", "실행 규칙", "text-button compact"),
    renderWorkDetailButton("hypothesis-governance", "", "가설·반증", "text-button compact"),
    renderWorkDetailButton("experiment-validation-board", "", "검증·승격", "text-button compact"),
    renderWorkDetailButton("strategy-trace-board", "", "추론 원장", "text-button compact"),
    '</div></section>'
  ].join("");
}

export { investmentModelManagementWorkDetailPayload, investmentModelOverviewWorkDetailPayload };
