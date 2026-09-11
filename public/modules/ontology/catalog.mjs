import { ontologyCatalogFilter } from "./requests.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { ontologyCatalogTabs } from "../shell/catalog.mjs";
import { ontologyState } from "../state/ontology.mjs";

function ontologyCatalogSummaryPayload() {
  return ontologyState.ontologyCatalogSummary && typeof ontologyState.ontologyCatalogSummary === "object"
    ? ontologyState.ontologyCatalogSummary
    : { status: ontologyState.ontologyCatalogSummaryLoading ? "loading" : "idle", counts: {}, diagnostics: [], boundedContexts: [] };
}

function ontologyCatalogTabMeta(tabId) {
  return ontologyCatalogTabs.filter(function (item) { return item.id === tabId; })[0] || ontologyCatalogTabs[0];
}

function ontologyCatalogStatusTone(value) {
  var status = String(value || "").toLowerCase();
  if (["ok", "ready", "active", "aligned", "empty"].indexOf(status) >= 0) return "watch";
  if (["warning", "drift", "partial", "world-required"].indexOf(status) >= 0) return "caution";
  if (["error", "failed", "not-found"].indexOf(status) >= 0) return "danger";
  return "hold";
}

function renderOntologyCatalogPanel() {
  var active = ontologyCatalogTabMeta(ontologyState.activeOntologyCatalogTab);
  var summary = ontologyCatalogSummaryPayload();
  return [
    '<section class="ontology-catalog" aria-label="온톨로지 카탈로그">',
    '<header class="ontology-catalog-head">',
    '<div><p class="label">Ontology Catalog</p><h2>전체 관계 구조와 추론 계보</h2><p>TBox 정의부터 실행 규칙, 가설, 현재 추론, 판단과 알림 연결을 같은 기준으로 확인합니다.</p></div>',
    '<div class="ontology-catalog-head-actions"><span class="tone-chip hold">읽기 전용</span><button class="icon-button" type="button" data-action="refresh-ontology-catalog" title="카탈로그 새로고침" aria-label="카탈로그 새로고침">↻</button></div>',
    '</header>',
    '<nav class="ontology-catalog-tabs" aria-label="온톨로지 카탈로그 보기">',
    ontologyCatalogTabs.map(function (item) {
      return '<button type="button" class="' + (item.id === active.id ? "active" : "") + '" data-ontology-catalog-tab="' + escapeHtml(item.id) + '"><strong>' + escapeHtml(item.label) + '</strong><span>' + escapeHtml(item.description) + '</span></button>';
    }).join(""),
    '</nav>',
    '<div class="ontology-catalog-body">',
    active.id === "overview" ? renderOntologyCatalogOverview(summary) : renderOntologyCatalogSection(active.id),
    '</div>',
    '</section>'
  ].join("");
}

function renderOntologyCatalogOverview(summary) {
  var counts = summary.counts || {};
  var deployment = summary.deployedTBox || {};
  var rulebox = summary.rulebox || {};
  var hypotheses = summary.hypotheses || {};
  var inferencebox = summary.inferencebox || {};
  var ruleKnowledge = summary.ruleKnowledge || {};
  var statisticalSignals = summary.statisticalSignals || {};
  var signalMigration = statisticalSignals.migrationCounts || {};
  var metrics = [
    ["경계 문맥", counts.boundedContexts, "업무 의미 영역"],
    ["TBox 개념", counts.classes, summary.sourceTBox && summary.sourceTBox.version],
    ["TBox 관계", counts.relations, "관계 형식"],
    ["실행 규칙", counts.executableRules, rulebox.status || "확인 대기"],
    ["예측 가설 규칙", ruleKnowledge.hypothesisRuleCount, "결과 검증 대상"],
    ["가드레일 규칙", ruleKnowledge.guardrailRuleCount, "정책·품질·실행 제약"],
    ["모델 전환 완료", signalMigration["model-signal-production"] || 0, "6개 모델 계열"],
    ["모델 전환 대기", ["awaiting-governed-model-scorer", "shadow-signal-required", "unmapped", "missing"].reduce(function (sum, key) { return sum + Number(signalMigration[key] || 0); }, 0), "0이어야 정상"],
    ["현재 가설", counts.hypotheses, hypotheses.complete === false ? "일부 집계" : "전체 집계"],
    ["추론 세대", inferencebox.inferenceGenerationId ? 1 : "-", inferencebox.status || "계정 필요"]
  ];
  if (ontologyState.ontologyCatalogSummaryLoading && !ontologyState.ontologyCatalogSummaryLoaded) {
    return '<div class="work-detail-loading"><span class="spinner"></span><p>온톨로지 구조와 배포 상태를 읽는 중입니다.</p></div>';
  }
  if (ontologyState.ontologyCatalogSummaryError) {
    return '<div class="ontology-catalog-unavailable"><strong>카탈로그 요약을 읽지 못했습니다.</strong><p>' + escapeHtml(ontologyState.ontologyCatalogSummaryError) + '</p></div>';
  }
  return [
    '<div class="ontology-catalog-metrics">',
    metrics.map(function (item) {
      return '<div><span>' + escapeHtml(item[0]) + '</span><strong>' + escapeHtml(item[1] == null ? "-" : item[1]) + '</strong><em>' + escapeHtml(item[2] || "-") + '</em></div>';
    }).join(""),
    '</div>',
    '<section class="ontology-catalog-flow">',
    '<header><strong>실제 판단 연결 순서</strong><span>표시 문구가 아니라 저장된 ID로만 다음 단계와 연결합니다.</span></header>',
    '<ol>',
    (summary.lineage || []).map(function (item, index) {
      return '<li><b>' + escapeHtml(String(index + 1).padStart(2, "0")) + '</b><span>' + escapeHtml(item.label || item.type) + '</span><em>' + escapeHtml(item.type || "") + '</em></li>';
    }).join(""),
    '</ol>',
    '</section>',
    '<div class="ontology-catalog-status-line">',
    '<div><span>소스 TBox ↔ TypeDB</span><strong>' + escapeHtml(deployment.alignment === "aligned" ? "일치" : deployment.alignment || "확인 불가") + '</strong><em>' + escapeHtml([deployment.sourceVersion, deployment.sourceFingerprint].filter(Boolean).join(" · ") || deployment.reason || "-") + '</em></div>',
    '<div><span>실행 규칙 원본</span><strong>' + escapeHtml(rulebox.status || "확인 대기") + '</strong><em>' + escapeHtml(rulebox.source || rulebox.reason || "-") + '</em></div>',
    '<div><span>현재 추론 세계</span><strong>' + escapeHtml(inferencebox.status || "확인 대기") + '</strong><em>' + escapeHtml(inferencebox.worldId || inferencebox.reason || "계정을 선택하면 확인합니다.") + '</em></div>',
    '</div>',
    '<section class="ontology-catalog-diagnostics">',
    '<header><strong>구조 신뢰성 점검</strong><span>임의 점수 없이 정상·주의·확인 불가 상태로 표시합니다.</span></header>',
    '<div>',
    (summary.diagnostics || []).map(function (item) {
      return '<article><span class="status-pill ' + escapeHtml(ontologyCatalogStatusTone(item.status)) + '">' + escapeHtml(item.status || "unknown") + '</span><p><strong>' + escapeHtml(item.label || item.id) + '</strong><em>' + escapeHtml(item.detail || "-") + '</em></p><b>' + escapeHtml(item.count || 0) + '</b></article>';
    }).join("") || '<p class="subtle">점검 결과가 아직 없습니다.</p>',
    '</div>',
    '</section>'
  ].join("");
}

function ontologyCatalogContextOptions() {
  var summary = ontologyCatalogSummaryPayload();
  return (summary.boundedContexts || []).map(function (item) {
    return { id: item.key || "", label: item.label || item.key || "" };
  }).filter(function (item) { return item.id; });
}

function renderOntologyCatalogToolbar(section) {
  var filter = ontologyCatalogFilter(section);
  var contexts = ontologyCatalogContextOptions();
  var extra = "";
  if (section === "classes" || section === "relations") {
    extra = '<label><span>문맥</span><select data-ontology-catalog-filter="boundedContext"><option value="">전체</option>' + contexts.map(function (item) {
      return '<option value="' + escapeHtml(item.id) + '"' + (item.id === filter.boundedContext ? " selected" : "") + '>' + escapeHtml(item.label) + '</option>';
    }).join("") + '</select></label>';
  } else if (section === "rules") {
    var summary = ontologyCatalogSummaryPayload();
    var knowledge = summary.ruleKnowledge || {};
    var kinds = Object.keys(knowledge.ruleKindCounts || {});
    var theories = Object.keys(knowledge.theoryFamilyCounts || {});
    var statuses = Object.keys(knowledge.validationStatusCounts || {});
    extra = [
      '<label><span>사용 상태</span><select data-ontology-catalog-filter="enabled"><option value="">전체</option><option value="true"' + (filter.enabled === "true" ? " selected" : "") + '>사용</option><option value="false"' + (filter.enabled === "false" ? " selected" : "") + '>중지</option></select></label>',
      '<label><span>규칙 역할</span><select data-ontology-catalog-filter="ruleKind"><option value="">전체</option>' + kinds.map(function (item) { return '<option value="' + escapeHtml(item) + '"' + (filter.ruleKind === item ? " selected" : "") + '>' + escapeHtml(item) + '</option>'; }).join("") + '</select></label>',
      '<label><span>이론 계열</span><select data-ontology-catalog-filter="theoryFamily"><option value="">전체</option>' + theories.map(function (item) { return '<option value="' + escapeHtml(item) + '"' + (filter.theoryFamily === item ? " selected" : "") + '>' + escapeHtml(item) + '</option>'; }).join("") + '</select></label>',
      '<label><span>검증 상태</span><select data-ontology-catalog-filter="validationStatus"><option value="">전체</option>' + statuses.map(function (item) { return '<option value="' + escapeHtml(item) + '"' + (filter.validationStatus === item ? " selected" : "") + '>' + escapeHtml(item) + '</option>'; }).join("") + '</select></label>'
    ].join("");
  } else if (section === "hypotheses") {
    extra = '<label><span>범위</span><select data-ontology-catalog-filter="scope"><option value="">전체</option><option value="account"' + (filter.scope === "account" ? " selected" : "") + '>계정</option><option value="market"' + (filter.scope === "market" ? " selected" : "") + '>시장</option></select></label>';
  } else if (section === "inferences") {
    extra = '<label><span>종목</span><input data-ontology-catalog-filter="symbol" type="text" value="' + escapeHtml(filter.symbol || "") + '" placeholder="000660"></label>';
  }
  return [
    '<form class="ontology-catalog-toolbar" data-ontology-catalog-form>',
    '<label class="ontology-catalog-search"><span>검색</span><input data-ontology-catalog-filter="query" type="search" value="' + escapeHtml(filter.query || "") + '" placeholder="ID, 이름, 종목"></label>',
    extra,
    '<button class="text-button primary compact" type="submit">조회</button>',
    '</form>'
  ].join("");
}

function ontologyCatalogRowType(section) {
  return { classes: "class", relations: "relation", rules: "rule", hypotheses: "hypothesis", inferences: "inference" }[section] || section;
}

function ontologyExecutionGrainLabel(value) {
  return { instrument: "종목 단위", account: "계좌 단위", macro: "거시 단위", world: "월드 단위" }[String(value || "")] || String(value || "실행 단위 미지정");
}

function ontologyCatalogRowPresentation(section, row) {
  if (section === "classes") return {
    title: row.label || row.name,
    detail: [row.name, row.parent ? "부모 " + row.parent : "최상위 개념"].filter(Boolean).join(" · "),
    meta: row.boundedContext || "문맥 없음",
    side: row.materializationBox || "TBox"
  };
  if (section === "relations") return {
    title: row.name,
    detail: [row.sourceContext, row.targetContext].filter(Boolean).join(" → ") || "문맥 계약 없음",
    meta: row.boundedContext || "문맥 없음",
    side: row.materializationPolicy || "schema"
  };
  if (section === "rules") return {
    /* Statistical migration is governance metadata, not a live action. */
    title: row.label || row.ruleId,
    detail: row.ruleId,
    meta: [
      row.ruleKind || "역할 미지정",
      row.owner || "소유자 미지정",
      row.theoryFamily || "이론 미지정",
      row.assessmentScope || "판단 영역 미지정",
      ontologyExecutionGrainLabel(row.evaluationGrain),
      row.ownerWorld || "소유 월드 미지정",
      row.lifecycleClass || "실행군 미지정",
      "조건 " + Number(row.conditionCount || 0),
      "파생 " + Number(row.derivationCount || 0),
      (row.triggerFamilies || []).slice(0, 2).join(" / ") || "트리거 없음",
      ((row.statisticalSignalContract || {}).migrationState || "통계 전환 해당 없음")
    ].join(" · "),
    side: row.enabled === false ? "중지" : (row.knowledgeValidationStatus || "사용")
  };
  if (section === "hypotheses") return {
    title: [row.symbol, row.scope === "market" ? "시장 가설" : "계정 가설"].filter(Boolean).join(" · ") || row.lifecycleId,
    detail: row.lifecycleKey,
    meta: row.transitionReason || row.inferenceGenerationId || "변화 설명 없음",
    side: row.state || "관찰"
  };
  return {
    title: [row.symbol, row.label].filter(Boolean).join(" · ") || row.traceId,
    detail: row.ruleId || row.traceId,
    meta: "일치 조건 " + Number(row.matchedConditionCount || 0) + " · 파생 관계 " + Number(row.relationCount || 0),
    side: row.validationState || row.freshnessStatus || "추론"
  };
}

function renderOntologyCatalogRow(section, row) {
  var view = ontologyCatalogRowPresentation(section, row || {});
  var selected = ontologyState.ontologyCatalogSelection && ontologyState.ontologyCatalogSelection.type === ontologyCatalogRowType(section) && ontologyState.ontologyCatalogSelection.id === String(row.id || "");
  return [
    '<button class="ontology-catalog-row' + (selected ? " active" : "") + '" type="button" data-ontology-catalog-select="' + escapeHtml(ontologyCatalogRowType(section)) + '" data-ontology-catalog-id="' + escapeHtml(row.id || "") + '" data-ontology-catalog-symbol="' + escapeHtml(row.symbol || "") + '">',
    '<span><strong>' + escapeHtml(view.title || "-") + '</strong><em>' + escapeHtml(view.detail || "-") + '</em></span>',
    '<span>' + escapeHtml(view.meta || "-") + '</span>',
    '<b>' + escapeHtml(view.side || "-") + '</b>',
    '</button>'
  ].join("");
}

function renderOntologyCatalogSection(section) {
  var payload = ontologyState.ontologyCatalogPages[section] || { status: "loading", items: [], page: {} };
  var items = Array.isArray(payload.items) ? payload.items : [];
  var page = payload.page || {};
  var loading = Boolean(ontologyState.ontologyCatalogLoading[section]);
  return [
    renderOntologyCatalogToolbar(section),
    '<div class="ontology-catalog-list-head"><span>' + escapeHtml(ontologyCatalogTabMeta(section).label) + '</span><em>' + escapeHtml(page.total == null ? "-" : page.total) + '개</em><span class="status-pill ' + escapeHtml(ontologyCatalogStatusTone(payload.status)) + '">' + escapeHtml(loading ? "loading" : payload.status || "idle") + '</span></div>',
    loading && !items.length ? '<div class="work-detail-loading"><span class="spinner"></span><p>선택한 카탈로그를 읽는 중입니다.</p></div>' : '',
    !loading && payload.status && ["ok", "empty"].indexOf(String(payload.status).toLowerCase()) < 0 ? '<div class="ontology-catalog-unavailable"><strong>실제 저장소 데이터를 표시할 수 없습니다.</strong><p>' + escapeHtml(payload.reason || ontologyState.ontologyCatalogErrors[section] || "상태를 확인하세요.") + '</p></div>' : '',
    '<div class="ontology-catalog-list">',
    items.length ? items.map(function (row) { return renderOntologyCatalogRow(section, row); }).join("") : (!loading && ["ok", "empty"].indexOf(String(payload.status).toLowerCase()) >= 0 ? '<div class="ontology-empty">조건에 맞는 항목이 없습니다.</div>' : ''),
    '</div>',
    '<footer class="ontology-catalog-pagination">',
    '<button class="text-button compact" type="button" data-ontology-catalog-cursor="' + escapeHtml(page.previousCursor || "") + '"' + (!page.previousCursor || loading ? " disabled" : "") + '>이전</button>',
    '<span>' + escapeHtml(Number(page.offset || 0) + 1) + '–' + escapeHtml(Number(page.offset || 0) + items.length) + ' / ' + escapeHtml(page.total || 0) + '</span>',
    '<button class="text-button compact" type="button" data-ontology-catalog-cursor="' + escapeHtml(page.nextCursor || "") + '"' + (!page.nextCursor || loading ? " disabled" : "") + '>다음</button>',
    '</footer>',
    renderOntologyCatalogLineage(),
  ].join("");
}

function ontologyCatalogLineageItemLabel(type, item) {
  if (type === "classes") return item.label || item.name || item.id;
  if (type === "relations") return item.name || item.id;
  if (type === "rules") return item.label || item.ruleId || item.id;
  if (type === "hypotheses") return [item.symbol, item.lifecycleKey].filter(Boolean).join(" · ");
  if (type === "inferences") return [item.symbol, item.label || item.traceId].filter(Boolean).join(" · ");
  if (type === "decisions") return [item.symbol, item.action, item.episodeId].filter(Boolean).join(" · ");
  return [item.symbol, item.messageType, item.jobId].filter(Boolean).join(" · ");
}

function renderOntologyCatalogLineage() {
  if (!ontologyState.ontologyCatalogSelection) return '<div class="ontology-catalog-lineage-empty"><strong>연결 계보</strong><span>항목을 선택하면 앞뒤 단계와 누락 연결을 표시합니다.</span></div>';
  if (ontologyState.ontologyCatalogLineageLoading) return '<div class="work-detail-loading ontology-catalog-lineage-loading"><span class="spinner"></span><p>저장된 ID를 따라 판단과 알림 연결을 확인하는 중입니다.</p></div>';
  if (ontologyState.ontologyCatalogLineageError) return '<div class="ontology-catalog-unavailable"><strong>계보 조회 실패</strong><p>' + escapeHtml(ontologyState.ontologyCatalogLineageError) + '</p></div>';
  var payload = ontologyState.ontologyCatalogLineage || {};
  var lineage = payload.lineage || {};
  var selectedItem = (payload.selection || {}).item || {};
  var knowledge = selectedItem.knowledgeBasis || {};
  var statisticalContract = selectedItem.statisticalSignalContract || {};
  var executionUnit = selectedItem.executionUnit && typeof selectedItem.executionUnit === "object" ? selectedItem.executionUnit : {};
  var references = Array.isArray(knowledge.references) ? knowledge.references : [];
  var knowledgeDetail = knowledge.ruleKind ? [
    '<section class="ontology-rule-knowledge">',
    '<header><strong>규칙의 이론·검증 근거</strong><span>' + escapeHtml(knowledge.decisionEligibility || "") + '</span></header>',
    '<div class="ontology-rule-knowledge-grid">',
    '<p><span>역할</span><strong>' + escapeHtml(knowledge.ruleKind) + '</strong></p>',
    '<p><span>소유 모듈</span><strong>' + escapeHtml(knowledge.owner || "-") + '</strong></p>',
    '<p><span>이론 계열</span><strong>' + escapeHtml(knowledge.theoryFamily || "-") + '</strong></p>',
    '<p><span>투자 논지</span><strong>' + escapeHtml(knowledge.thesisFamily || "-") + '</strong></p>',
    '<p><span>검증 상태</span><strong>' + escapeHtml(knowledge.validationStatus || "-") + '</strong></p>',
    '<p><span>임계값 출처</span><strong>' + escapeHtml(knowledge.thresholdOrigin || "-") + '</strong></p>',
    '<p><span>가설 생성</span><strong>' + escapeHtml(knowledge.requiresHypothesis ? "예" : "아니오") + '</strong></p>',
    '<p><span>입력 계약</span><strong>' + escapeHtml(knowledge.inputContract || "-") + '</strong></p>',
    '<p><span>출력 계약</span><strong>' + escapeHtml(knowledge.outputContract || "-") + '</strong></p>',
    '<p><span>판단 권한</span><strong>' + escapeHtml(knowledge.decisionAuthority || "-") + '</strong></p>',
    '<p><span>전환 처리</span><strong>' + escapeHtml(knowledge.migrationDisposition || "-") + '</strong></p>',
    '</div>',
    '<p class="ontology-rule-knowledge-explanation">' + escapeHtml(knowledge.plainLanguageBasis || "근거 설명이 없습니다.") + '</p>',
    references.length ? '<div class="ontology-rule-references">' + references.map(function (item) { return '<a href="' + escapeHtml(item.url || "#") + '" target="_blank" rel="noopener noreferrer"><strong>' + escapeHtml(item.title || item.referenceId) + '</strong><span>' + escapeHtml(item.claim || item.applicability || "") + '</span></a>'; }).join("") + '</div>' : '<p class="subtle">연결된 외부 연구 문헌이 없습니다. 내부 정책 또는 운영 계약 기반 규칙입니다.</p>',
    '</section>'
  ].join("") : "";
  var statisticalDetail = statisticalContract.version ? [
    '<section class="ontology-rule-knowledge">',
    '<header><strong>통계 신호 전환 계약</strong><span>' + escapeHtml(statisticalContract.migrationState || "-") + '</span></header>',
    '<div class="ontology-rule-knowledge-grid">',
    '<p><span>현재 권한</span><strong>' + escapeHtml(statisticalContract.currentDecisionAuthority || "-") + '</strong></p>',
    '<p><span>후보 권한</span><strong>' + escapeHtml(statisticalContract.candidateDecisionAuthority || "-") + '</strong></p>',
    '<p><span>신호 구현</span><strong>' + escapeHtml(statisticalContract.signalAvailability || "-") + '</strong></p>',
    '<p><span>모델 릴리스</span><strong>' + escapeHtml((statisticalContract.releaseIds || []).join(", ") || "-") + '</strong></p>',
    '<p><span>릴리스 상태</span><strong>' + escapeHtml([statisticalContract.releaseStatus, statisticalContract.releaseValidationStatus].filter(Boolean).join(" · ") || "-") + '</strong></p>',
    '<p><span>운영 적격</span><strong>' + escapeHtml(statisticalContract.productionEligible ? "예" : "아니오") + '</strong></p>',
    '<p><span>규칙 결합</span><strong>' + escapeHtml(statisticalContract.hypothesisContractBinding === "exact-rule-id" ? "규칙 ID 정확 일치" : statisticalContract.hypothesisContractBinding || "-") + '</strong></p>',
    '<p><span>전환 우선순위</span><strong>' + escapeHtml(statisticalContract.migrationPriority == null ? "-" : statisticalContract.migrationPriority) + '</strong></p>',
    '</div>',
    '<p class="ontology-rule-knowledge-explanation">필요 신호: ' + escapeHtml((statisticalContract.signalTypes || []).join(", ") || "없음") + '</p>',
    '<p class="subtle">현재 차단: ' + escapeHtml((statisticalContract.promotionBlockers || []).join(" / ") || "없음") + '</p>',
    '<p class="subtle">승격 조건: ' + escapeHtml((statisticalContract.promotionGates || []).join(" / ") || "해당 없음") + '</p>',
    '</section>'
  ].join("") : "";
  var executionUnitDetail = selectedItem.evaluationGrain ? [
    '<section class="ontology-rule-knowledge">',
    '<header><strong>규칙 실행 경계</strong><span>' + escapeHtml(ontologyExecutionGrainLabel(selectedItem.evaluationGrain)) + '</span></header>',
    '<div class="ontology-rule-knowledge-grid">',
    '<p><span>평가 단위</span><strong>' + escapeHtml(ontologyExecutionGrainLabel(selectedItem.evaluationGrain)) + '</strong></p>',
    '<p><span>소유 월드</span><strong>' + escapeHtml(selectedItem.ownerWorld || "-") + '</strong></p>',
    '<p><span>실행 주기</span><strong>' + escapeHtml(selectedItem.executionCadence || "-") + '</strong></p>',
    '<p><span>증분 실행</span><strong>' + escapeHtml(selectedItem.incrementalEligible ? "가능" : "전체 평가 필요") + '</strong></p>',
    '<p><span>키 필드</span><strong>' + escapeHtml((executionUnit.keyFields || []).join(", ") || "-") + '</strong></p>',
    '<p><span>종목 fan-out</span><strong>' + escapeHtml(executionUnit.subjectFanoutAllowed ? "허용" : "금지") + '</strong></p>',
    '</div>',
    '<p class="ontology-rule-knowledge-explanation">트리거 사건: ' + escapeHtml((selectedItem.triggerEventClasses || []).join(", ") || "명시된 사건 없음") + '</p>',
    '</section>'
  ].join("") : "";
  var groups = [
    ["classes", "TBox 개념"], ["relations", "TBox 관계"], ["rules", "실행 규칙"],
    ["hypotheses", "가설"], ["inferences", "추론"], ["decisions", "판단"], ["notifications", "알림"]
  ];
  return [
    '<section class="ontology-catalog-lineage">',
    '<header><div><span>선택한 ID</span><strong>' + escapeHtml((payload.selection || {}).id || ontologyState.ontologyCatalogSelection.id) + '</strong></div><span class="status-pill ' + escapeHtml(ontologyCatalogStatusTone(payload.status)) + '">' + escapeHtml(payload.status || "확인 대기") + '</span></header>',
    '<div class="ontology-catalog-lineage-rail">',
    groups.map(function (group, index) {
      var rows = Array.isArray(lineage[group[0]]) ? lineage[group[0]] : [];
      return '<section><b>' + escapeHtml(String(index + 1).padStart(2, "0")) + '</b><span>' + escapeHtml(group[1]) + '</span><strong>' + escapeHtml(rows.length) + '</strong><div>' + (rows.length ? rows.slice(0, 3).map(function (item) { return '<em>' + escapeHtml(ontologyCatalogLineageItemLabel(group[0], item) || item.id || "-") + '</em>'; }).join("") : '<em>연결 없음</em>') + '</div></section>';
    }).join(""),
    '</div>',
    executionUnitDetail,
    knowledgeDetail,
    statisticalDetail,
    (payload.gaps || []).length ? '<div class="ontology-catalog-gaps"><strong>확인이 필요한 연결</strong>' + payload.gaps.map(function (gap) { return '<p><b>' + escapeHtml(gap.code || "gap") + '</b><span>' + escapeHtml(gap.detail || "-") + '</span></p>'; }).join("") + '</div>' : '<div class="ontology-catalog-lineage-ok">현재 조회 범위에서 누락 연결이 발견되지 않았습니다.</div>',
    '</section>'
  ].join("");
}

export { renderOntologyCatalogPanel };
