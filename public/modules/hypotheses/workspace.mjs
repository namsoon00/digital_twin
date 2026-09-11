import { instrumentTimelineEventWorkDetailPayload, renderInstrumentWorkspaceLink } from "../instruments/workspace.mjs";
import { editorWorkDetailPayload, renderWorkDetailButton } from "../navigation/detail.mjs";
import { render } from "../render/scheduler.mjs";
import { clearReadModelPoll, readModelIsWarming, requestJson, scheduleReadModelPoll } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { formatClock, latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { app } from "../shell/root.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { hypothesesState } from "../state/hypotheses.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

function hypothesisWorkspacePayload() {
  return hypothesesState.hypothesisWorkspace && typeof hypothesesState.hypothesisWorkspace === "object"
    ? hypothesesState.hypothesisWorkspace
    : { status: "loading", count: 0, summary: {}, items: [], events: [] };
}

function hypothesisWorkspaceItems() {
  var payload = hypothesisWorkspacePayload();
  return latestChangedFirst(Array.isArray(payload.items) ? payload.items : []);
}

function hypothesisWorkspaceItemByKey(lifecycleKey) {
  var target = String(lifecycleKey || "");
  return hypothesisWorkspaceItems().filter(function (item) {
    return String(item && item.lifecycleKey || "") === target;
  })[0] || null;
}

function syncActiveHypothesisLifecycleKey() {
  var items = hypothesisWorkspaceItems();
  if (!items.length) {
    hypothesesState.activeHypothesisLifecycleKey = "";
    return null;
  }
  var active = hypothesisWorkspaceItemByKey(hypothesesState.activeHypothesisLifecycleKey);
  if (!active) {
    hypothesesState.activeHypothesisLifecycleKey = String(items[0].lifecycleKey || "");
    active = items[0];
  }
  return active;
}

function activeHypothesisWorkspaceItem() {
  return syncActiveHypothesisLifecycleKey();
}

function loadHypothesisWorkspace(force) {
  if (isStaticPreviewHost()) {
    hypothesesState.hypothesisWorkspace = {
      status: "preview",
      count: 0,
      eventCount: 0,
      summary: { stateCounts: {}, outcomeStateCounts: {}, materialChangeCount: 0, activeCount: 0 },
      items: [],
      events: []
    };
    hypothesesState.hypothesisPolicyVersions = { status: "preview", versions: [] };
    hypothesesState.hypothesisWorkspaceLoaded = true;
    hypothesesState.hypothesisWorkspaceError = "";
    return Promise.resolve(hypothesesState.hypothesisWorkspace);
  }
  if (hypothesesState.hypothesisWorkspaceLoading && !force) return Promise.resolve(hypothesesState.hypothesisWorkspace);
  if (force) {
    hypothesesState.hypothesisWorkspaceDetails = {};
    hypothesesState.hypothesisWorkspaceDetailLoading = {};
    hypothesesState.hypothesisWorkspaceDetailErrors = {};
  }
  hypothesesState.hypothesisWorkspaceLoading = true;
  hypothesesState.hypothesisWorkspaceError = "";
  return requestJson("/api/investment-brain/hypotheses?view=summary&limit=40" + (force ? "&refresh=1" : ""), {
    key: "hypothesis-workspace",
    force: Boolean(force),
    cacheTtlMs: 15000,
    timeoutMs: 30000
  })
    .then(function (payload) {
      hypothesesState.hypothesisWorkspace = payload && typeof payload === "object" ? payload : {};
      hypothesesState.hypothesisWorkspaceLoaded = true;
      if (readModelIsWarming(payload)) {
        scheduleReadModelPoll("hypothesis-workspace", function () { loadHypothesisWorkspace(true); });
      } else {
        clearReadModelPoll("hypothesis-workspace");
      }
      syncActiveHypothesisLifecycleKey();
      return hypothesesState.hypothesisWorkspace;
    })
    .catch(function (error) {
      hypothesesState.hypothesisWorkspaceError = error.message || "가설 검증 정보를 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      hypothesesState.hypothesisWorkspaceLoading = false;
      if (shellState.snapshot) render();
    });
}

function loadHypothesisPolicyVersions(force) {
  if (isStaticPreviewHost()) {
    hypothesesState.hypothesisPolicyVersions = { status: "preview", versions: [] };
    hypothesesState.hypothesisPolicyVersionsError = "";
    return Promise.resolve(hypothesesState.hypothesisPolicyVersions);
  }
  if (hypothesesState.hypothesisPolicyVersionsLoading && !force) return Promise.resolve(hypothesesState.hypothesisPolicyVersions);
  if (hypothesesState.hypothesisPolicyVersions && !force) return Promise.resolve(hypothesesState.hypothesisPolicyVersions);
  hypothesesState.hypothesisPolicyVersionsLoading = true;
  hypothesesState.hypothesisPolicyVersionsError = "";
  render();
  return requestJson("/api/investment-brain/hypothesis-policy-versions?limit=20" + (force ? "&refresh=1" : ""), {
    key: "hypothesis-policy-versions",
    force: Boolean(force),
    cacheTtlMs: 30000,
    timeoutMs: 30000
  }).then(function (payload) {
    hypothesesState.hypothesisPolicyVersions = payload && typeof payload === "object" ? payload : { versions: [] };
    return hypothesesState.hypothesisPolicyVersions;
  }).catch(function (error) {
    hypothesesState.hypothesisPolicyVersionsError = error.message || "RuleBox 버전 이력을 읽지 못했습니다.";
    return null;
  }).finally(function () {
    hypothesesState.hypothesisPolicyVersionsLoading = false;
    if (shellState.snapshot) render();
  });
}

function loadHypothesisWorkspaceDetail(lifecycleKey) {
  var key = String(lifecycleKey || "").trim();
  if (!key || isStaticPreviewHost()) return Promise.resolve(null);
  if (hypothesesState.hypothesisWorkspaceDetails[key]) return Promise.resolve(hypothesesState.hypothesisWorkspaceDetails[key]);
  if (hypothesesState.hypothesisWorkspaceDetailLoading[key]) return Promise.resolve(null);
  hypothesesState.hypothesisWorkspaceDetailLoading[key] = true;
  hypothesesState.hypothesisWorkspaceDetailErrors[key] = "";
  render();
  return requestJson("/api/investment-brain/hypotheses/" + encodeURIComponent(key), {
    key: "hypothesis-workspace:" + key,
    timeoutMs: 30000
  }).then(function (payload) {
    hypothesesState.hypothesisWorkspaceDetails[key] = payload && typeof payload === "object" ? payload : {};
    return hypothesesState.hypothesisWorkspaceDetails[key];
  }).catch(function (error) {
    hypothesesState.hypothesisWorkspaceDetailErrors[key] = error.message || "가설 상세를 읽지 못했습니다.";
    return null;
  }).finally(function () {
    delete hypothesesState.hypothesisWorkspaceDetailLoading[key];
    if (shellState.snapshot) render();
  });
}

function hypothesisLifecycleTone(stateValue) {
  var value = String(stateValue || "").toLowerCase();
  if (value === "invalidated" || value === "expired") return "danger";
  if (value === "weakened") return "caution";
  if (value === "strengthened") return "watch";
  return "hold";
}

function hypothesisLifecycleLabel(stateValue) {
  var value = String(stateValue || "").toLowerCase();
  if (value === "observed") return "처음 관찰됨";
  if (value === "maintained") return "근거 유지";
  if (value === "strengthened") return "근거 강화";
  if (value === "weakened") return "근거 약화";
  if (value === "invalidated") return "관계 해제";
  if (value === "expired") return "근거 만료";
  return value;
}

function hypothesisOutcomeTone(stateValue) {
  var value = String(stateValue || "").toLowerCase();
  if (value === "contradicted") return "danger";
  if (value === "supported") return "watch";
  if (value === "inconclusive") return "caution";
  return "hold";
}

function hypothesisFreshnessLabel(status) {
  var value = String(status || "").toLowerCase();
  if (value === "fresh") return "최신";
  if (value === "aging") return "확인 필요";
  if (value === "stale") return "오래됨";
  if (value === "unavailable") return "없음";
  return value || "확인 대기";
}

function hypothesisHorizonLabel(minutes) {
  var value = Number(minutes || 0);
  if (!Number.isFinite(value) || value <= 0) return "기준 시점";
  if (value % 1440 === 0) return Math.round(value / 1440) + "일 뒤";
  if (value % 60 === 0) return Math.round(value / 60) + "시간 뒤";
  return Math.round(value) + "분 뒤";
}

function hypothesisObservationDomainLabel(value) {
  return {
    quote: "현재가",
    trend: "가격 흐름",
    flow: "거래 흐름",
    research: "뉴스·공시",
    portfolio: "보유 상태",
    static: "기본 정보"
  }[String(value || "").toLowerCase()] || String(value || "");
}

function hypothesisCriterionRoleLabel(value) {
  return {
    cause: "원인 확인",
    result: "시장 결과",
    invalidation: "반증 조건",
    context: "참고 조건"
  }[String(value || "").toLowerCase()] || String(value || "기준");
}

function hypothesisCriterionMetricLabel(value) {
  return {
    instrumentReturnPct: "종목 수익률",
    benchmarkReturnPct: "기준 수익률",
    excessReturnPct: "기준 대비 초과수익률",
    profitLossRate: "계정 손익률",
    volumeRatio: "거래량 배율",
    tradeStrength: "체결강도",
    foreignNetVolume: "외국인 순매수",
    institutionNetVolume: "기관 순매수",
    individualNetVolume: "개인 순매수",
    shareCountChangePct: "주식 수 변화율",
    freeCashFlowChangePct: "잉여현금흐름 변화율",
    verifiedEventCount: "검증 사건 수",
    counterEvidenceCount: "반대 근거 수"
  }[String(value || "")] || String(value || "관측값");
}

function hypothesisCriterionStateLabel(value) {
  return {
    passed: "충족",
    failed: "미충족",
    unknown: "자료 부족"
  }[String(value || "").toLowerCase()] || String(value || "확인 대기");
}

function hypothesisStringList(value, limit) {
  var source = Array.isArray(value) ? value : (value ? [value] : []);
  var rows = [];
  source.forEach(function (item) {
    var text = String(item == null ? "" : item).trim();
    if (text && rows.indexOf(text) < 0) rows.push(text);
  });
  return rows.slice(0, limit || rows.length);
}

function hypothesisDeltaLabel(key) {
  return {
    addedSupportingEvidenceIds: "새 지지 근거",
    removedSupportingEvidenceIds: "사라진 지지 근거",
    addedCounterEvidenceIds: "새 반대 근거",
    removedCounterEvidenceIds: "사라진 반대 근거",
    addedCausalPathIds: "새 인과 경로",
    removedCausalPathIds: "사라진 인과 경로",
    addedFormationConditionIds: "새 성립 조건",
    removedFormationConditionIds: "사라진 성립 조건",
    addedRuleIds: "새 연결 룰",
    removedRuleIds: "사라진 연결 룰",
    removedActivePath: "사라진 활성 경로"
  }[key] || String(key || "변화");
}

function renderHypothesisMetric(label, value, caption, tone) {
  return [
    '<section class="investment-today-status-cell ' + escapeHtml(tone || "hold") + '">',
    '<span>' + escapeHtml(label) + '</span>',
    '<strong>' + escapeHtml(value == null || value === "" ? "-" : value) + '</strong>',
    '<em>' + escapeHtml(caption || "") + '</em>',
    '</section>'
  ].join("");
}

function hypothesisQualityTone(value) {
  var stateValue = String(value || "").toLowerCase();
  if (stateValue === "revision-required" || stateValue === "freshness-blocked") return "danger";
  if (stateValue === "coverage-gap" || stateValue === "lifecycle-review") return "caution";
  if (stateValue === "stable") return "watch";
  return "hold";
}

function hypothesisQualityForItem(item) {
  if (item && item.qualityReview && typeof item.qualityReview === "object") return item.qualityReview;
  var payload = hypothesisWorkspacePayload();
  var review = payload.qualityReview && typeof payload.qualityReview === "object" ? payload.qualityReview : {};
  var key = String(item && item.lifecycleKey || "");
  return (Array.isArray(review.items) ? review.items : []).filter(function (entry) {
    return String(entry && entry.lifecycleKey || "") === key;
  })[0] || null;
}

function renderHypothesisGovernancePanel(payload) {
  var quality = payload && payload.qualityReview && typeof payload.qualityReview === "object" ? payload.qualityReview : {};
  var qualitySummary = quality.summary && typeof quality.summary === "object" ? quality.summary : {};
  var replay = hypothesesState.hypothesisReplay && typeof hypothesesState.hypothesisReplay === "object" ? hypothesesState.hypothesisReplay : {};
  var integrity = replay.integrity && typeof replay.integrity === "object" ? replay.integrity : {};
  var replayPerformance = replay.performanceSummary && typeof replay.performanceSummary === "object" ? replay.performanceSummary : {};
  var versions = hypothesesState.hypothesisPolicyVersions && typeof hypothesesState.hypothesisPolicyVersions === "object" ? hypothesesState.hypothesisPolicyVersions : {};
  var operational = payload && payload.operational && typeof payload.operational === "object" ? payload.operational : {};
  var versionRows = Array.isArray(versions.versions) ? versions.versions : [];
  var readOnly = isStaticPreviewHost() || settingsState.serverSettingsLocked;
  var reviewRequired = Number(qualitySummary.reviewRequiredCount || 0);
  return [
    '<section class="hypothesis-governance-panel">',
    '<div class="hypothesis-detail-section-head">',
    '<div><strong>검증 운영</strong><p>사후 결과와 정책 변경은 분리해 기록합니다. 이 화면의 검토는 투자 행동을 자동으로 바꾸지 않습니다.</p></div>',
    '<div class="settings-actions">',
    '<button class="text-button" type="button" data-action="run-hypothesis-replay"' + (hypothesesState.hypothesisReplayLoading ? ' disabled' : '') + '>' + escapeHtml(hypothesesState.hypothesisReplayLoading ? "재생 중" : "사후 결과 재생") + '</button>',
    '<button class="text-button" type="button" data-action="propose-hypothesis-quality-review"' + (readOnly || hypothesesState.hypothesisQualityReviewAction ? ' disabled' : '') + '>' + escapeHtml(hypothesesState.hypothesisQualityReviewAction ? "제안 저장 중" : "품질 검토 제안") + '</button>',
    '</div>',
    '</div>',
    '<div class="hypothesis-governance-metrics">',
    '<span class="tone-chip ' + escapeHtml(reviewRequired ? "caution" : "hold") + '">품질 검토 ' + escapeHtml(reviewRequired + "건") + '</span>',
    '<span class="tone-chip ' + escapeHtml(integrity.passed === false ? "danger" : "hold") + '">' + escapeHtml(replay.status ? (integrity.passed === false ? "재생 점검 필요" : "재생 기록 있음") : "재생 전") + '</span>',
    replayPerformance.observedHypothesisCount != null ? '<span class="tone-chip watch">성과 관측 가설 ' + escapeHtml(replayPerformance.observedHypothesisCount + "개") + '</span>' : '',
    replayPerformance.observedRuleCount != null ? '<span class="tone-chip watch">성과 관측 규칙 ' + escapeHtml(replayPerformance.observedRuleCount + "개") + '</span>' : '',
    '<span class="tone-chip hold">RuleBox 버전 ' + escapeHtml(versionRows.length + "개") + '</span>',
    operational.ruleOutcomeContractCount != null ? '<span class="tone-chip ' + escapeHtml(Number(operational.fallbackRuleContractCount || 0) ? "caution" : "watch") + '">구조화 계약 ' + escapeHtml((operational.structuredRuleContractCount || 0) + "/" + operational.ruleOutcomeContractCount) + '</span>' : '',
    operational.legacyLifecycleContractCount ? '<span class="tone-chip caution">이전 계약 누락 ' + escapeHtml(operational.legacyLifecycleContractCount + "건") + '</span>' : '',
    operational.symbolCount != null ? '<span class="tone-chip hold">검토 범위 ' + escapeHtml(operational.symbolCount + "종목 · 종목당 " + (operational.episodeLimitPerSymbol || "-") + "건") + '</span>' : '',
    '</div>',
    replay.summary ? '<p class="hypothesis-governance-result">' + escapeHtml(replay.summary) + '</p>' : '',
    renderHistoricalReplayPerformance(replay),
    operational.note ? '<p class="hypothesis-governance-result">' + escapeHtml(operational.note) + '</p>' : '',
    hypothesesState.hypothesisPolicyVersionsError ? '<p class="form-error">' + escapeHtml(hypothesesState.hypothesisPolicyVersionsError) + '</p>' : '',
    renderHypothesisPolicyVersions(versionRows, readOnly),
    '</section>'
  ].join("");
}

function renderHistoricalReplayPerformance(replay) {
  var rows = Array.isArray(replay && replay.performanceByRule) ? replay.performanceByRule.slice(0, 5) : [];
  if (!rows.length) return "";
  return [
    '<details class="hypothesis-governance-performance">',
    '<summary>규칙별 과거 관측 결과</summary>',
    '<div class="compact-table">',
    rows.map(function (row) {
      var rate = row.corroborationRate == null ? "결론 표본 없음" : "지지 " + Math.round(Number(row.corroborationRate) * 100) + "%";
      return '<div><code>' + escapeHtml(row.id || "-") + '</code><span>' + escapeHtml(rate + " · 적격 " + Number(row.eligibleCount || 0) + "건 · 유보 " + Number(row.inconclusiveCount || 0) + "건") + '</span></div>';
    }).join(""),
    '</div>',
    '<p>이 통계는 자동 배포 기준이 아니라 검토 근거입니다.</p>',
    '</details>'
  ].join("");
}

function hypothesisGovernanceWorkDetailPayload() {
  var payload = hypothesisWorkspacePayload();
  var body = hypothesesState.hypothesisPolicyVersionsLoading
    ? '<div class="work-detail-loading"><span class="spinner"></span><p>RuleBox 정책 버전 이력을 읽는 중입니다.</p></div>'
    : renderHypothesisGovernancePanel(payload);
  return editorWorkDetailPayload(
    "Hypothesis Governance",
    "가설 검증 운영",
    "사후 결과 재생과 RuleBox 버전 관리를 별도 레이어에서 처리합니다.",
    body
  );
}

function renderHypothesisPolicyVersions(rows, readOnly) {
  rows = latestChangedFirst(rows);
  if (!rows.length) {
    return [
      '<p class="subtle hypothesis-governance-result">저장된 RuleBox 버전이 없습니다. 현재 활성 규칙을 기준선으로 한 번 기록하면 이후 변경을 복원할 수 있습니다.</p>',
      readOnly ? '' : '<button class="text-button" type="button" data-action="record-hypothesis-policy-baseline">현재 규칙 기준선 기록</button>'
    ].join("");
  }
  return [
    '<div class="hypothesis-policy-version-list">',
    rows.slice(0, 6).map(function (version) {
      var id = String(version && version.id || "");
      var label = version.versionLabel || version.shortHash || id;
      var meta = [version.author || "", version.status || ""].filter(Boolean).join(" · ");
      return [
        '<div>',
        '<strong>' + escapeHtml(label) + '</strong>',
        '<span>' + escapeHtml(meta || "RuleBox 버전") + '</span>',
        renderRecordChangedAt(version),
        version.changeReason ? '<p>' + escapeHtml(version.changeReason) + '</p>' : '',
        '<button class="icon-text-button" type="button" data-hypothesis-policy-restore="' + escapeHtml(id) + '"' + (readOnly || !id ? ' disabled' : '') + '>복원 검토</button>',
        '</div>'
      ].join("");
    }).join(""),
    '</div>'
  ].join("");
}

function renderHypothesisWorkspacePanel() {
  var payload = hypothesisWorkspacePayload();
  var summary = payload.summary && typeof payload.summary === "object" ? payload.summary : {};
  var items = hypothesisWorkspaceItems();
  var outcomeCounts = summary.outcomeStateCounts && typeof summary.outcomeStateCounts === "object" ? summary.outcomeStateCounts : {};
  var stateCounts = summary.stateCounts && typeof summary.stateCounts === "object" ? summary.stateCounts : {};
  var strengtheningCount = Number(stateCounts.observed || 0) + Number(stateCounts.strengthened || 0);
  var weakeningCount = Number(stateCounts.weakened || 0) + Number(stateCounts.invalidated || 0) + Number(stateCounts.expired || 0);
  return [
    '<article class="panel hypothesis-workspace-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Hypothesis Review</p>',
    '<h2>가설 검증</h2>',
    '<p class="subtle">현재 추론에서 무엇이 바뀌었는지와 이후 관측이 가설을 지지하거나 반증하는지를 확인합니다. 사후 결과는 자동 주문이나 행동 결정을 바꾸지 않습니다.</p>',
    '</div>',
    '<div class="settings-actions">',
    '<span class="tone-chip ' + escapeHtml(Number(summary.materialChangeCount || 0) ? "caution" : "hold") + '">' + escapeHtml(Number(summary.materialChangeCount || 0) ? summary.materialChangeCount + "건 변화" : "변화 없음") + '</span>',
    renderWorkDetailButton("hypothesis-governance", "", "검증 운영", "text-button"),
    '<button class="text-button" type="button" data-action="refresh-hypothesis-workspace"' + (hypothesesState.hypothesisWorkspaceLoading ? ' disabled' : '') + '>' + escapeHtml(hypothesesState.hypothesisWorkspaceLoading ? "조회 중" : "새로고침") + '</button>',
    '</div>',
    '</div>',
    '<div class="investment-today-status-grid hypothesis-workspace-metrics">',
    renderHypothesisMetric("가설", payload.count == null ? items.length : payload.count, "현재 TypeDB 세대", items.length ? "watch" : "hold"),
    renderHypothesisMetric("유효", summary.activeCount || 0, "유지·강화·약화", Number(summary.activeCount || 0) ? "watch" : "hold"),
    renderHypothesisMetric("성립·강화", strengtheningCount, "새로 생기거나 근거가 늘어남", strengtheningCount ? "watch" : "hold"),
    renderHypothesisMetric("약화·해제", weakeningCount, "근거 감소·무효화·만료", weakeningCount ? "danger" : "hold"),
    renderHypothesisMetric("지지됨", outcomeCounts.supported || 0, "사후 관측", Number(outcomeCounts.supported || 0) ? "watch" : "hold"),
    renderHypothesisMetric("반증됨", outcomeCounts.contradicted || 0, "사후 관측", Number(outcomeCounts.contradicted || 0) ? "danger" : "hold"),
    '</div>',
    hypothesesState.hypothesisWorkspaceError ? '<p class="form-error">' + escapeHtml(hypothesesState.hypothesisWorkspaceError) + '</p>' : '',
    hypothesesState.hypothesisWorkspaceLoading && !hypothesesState.hypothesisWorkspaceLoaded ? '<div class="rule-strip"><span>TypeDB 가설 수명주기와 사후 관측을 읽는 중입니다.</span></div>' : '',
    '<div class="hypothesis-workspace-layout">',
    renderHypothesisWorkspaceList(items),
    '<section class="hypothesis-workspace-detail hypothesis-workspace-detail-placeholder">',
    '<div class="hypothesis-workspace-empty">',
    '<strong>가설을 선택해 상세 검토를 엽니다.</strong>',
    '<span>목록은 상태와 변경 여부만 유지합니다. 근거, 사후 결과, 전이 이력, 정책 편집은 선택한 가설의 상세 리포트에서 확인합니다.</span>',
    '</div>',
    '</section>',
    '</div>',
    '</article>'
  ].join("");
}

function renderHypothesisWorkspaceList(items) {
  if (!items.length) {
    return [
      '<section class="hypothesis-workspace-list">',
      '<div class="hypothesis-workspace-empty">',
      '<strong>확인할 가설이 아직 없습니다.</strong>',
      '<span>정상·정렬된 TypeDB 추론 세대가 저장되면 가설 상태와 이후 결과가 여기에 표시됩니다.</span>',
      '</div>',
      '</section>'
    ].join("");
  }
  return [
    '<section class="hypothesis-workspace-list" aria-label="가설 목록">',
    items.map(function (item) {
      var lifecycleKey = String(item.lifecycleKey || "");
      var outcome = item.outcomeAssessment && typeof item.outcomeAssessment === "object" ? item.outcomeAssessment : {};
      var meta = [
        item.scopeLabel || "가설",
        item.materialChange ? "새 변화" : "변화 없음"
      ].filter(Boolean).join(" · ");
      return [
        '<button class="hypothesis-workspace-card" type="button" data-work-detail="hypothesis-review" data-work-detail-key="' + escapeHtml(lifecycleKey) + '">',
        '<div class="hypothesis-workspace-card-top">',
        '<span class="tone-chip ' + escapeHtml(hypothesisLifecycleTone(item.state)) + '">' + escapeHtml(item.stateLabel || item.state || "관찰됨") + '</span>',
        '<span class="tone-chip ' + escapeHtml(hypothesisOutcomeTone(outcome.outcomeState)) + '">' + escapeHtml(outcome.outcomeStateLabel || "표본 부족") + '</span>',
        '</div>',
        '<strong>' + escapeHtml(item.symbol || "종목") + '</strong>',
        '<em>' + escapeHtml(meta || "현재 세대") + '</em>',
        renderRecordChangedAt(item),
        '<span class="hypothesis-workspace-card-action">상세 검토</span>',
        '</button>'
      ].join("");
    }).join(""),
    '</section>'
  ].join("");
}

function renderHypothesisDetailList(title, rows, emptyText) {
  var values = hypothesisStringList(rows, 12);
  return [
    '<section class="hypothesis-detail-section">',
    '<strong>' + escapeHtml(title) + '</strong>',
    values.length ? '<div class="hypothesis-detail-chip-list">' + values.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '<p>' + escapeHtml(emptyText || "기록된 항목이 없습니다.") + '</p>',
    '</section>'
  ].join("");
}

function renderHypothesisDelta(item) {
  var delta = item && item.evidenceDelta && typeof item.evidenceDelta === "object" ? item.evidenceDelta : {};
  var rows = Object.keys(delta).map(function (key) {
    var values = hypothesisStringList(delta[key], 5);
    return values.length ? { key: key, values: values } : null;
  }).filter(Boolean);
  return [
    '<section class="hypothesis-detail-section">',
    '<strong>이전 세대 대비 변화</strong>',
    rows.length ? '<div class="hypothesis-delta-list">' + rows.map(function (row) {
      return '<div><b>' + escapeHtml(hypothesisDeltaLabel(row.key)) + '</b><span>' + escapeHtml(row.values.join(", ")) + '</span></div>';
    }).join("") + '</div>' : '<p>이전 정상 추론 세대와 비교해 기록된 근거 변화가 없습니다.</p>',
    '</section>'
  ].join("");
}

function renderHypothesisFreshness(item) {
  var rows = Array.isArray(item && item.freshness) ? item.freshness : [];
  return [
    '<section class="hypothesis-detail-section">',
    '<strong>데이터 신선도</strong>',
    rows.length ? '<div class="hypothesis-freshness-list">' + rows.map(function (row) {
      var status = String(row && row.status || "").toLowerCase();
      return [
        '<div>',
        '<span class="tone-chip ' + escapeHtml(status === "fresh" ? "watch" : (status === "stale" || status === "unavailable" ? "danger" : "caution")) + '">' + escapeHtml(hypothesisFreshnessLabel(status)) + '</span>',
        '<strong>' + escapeHtml(row.domain || "데이터") + '</strong>',
        '<p>' + escapeHtml((row.required ? "판단에 필요한 데이터. " : "참고 데이터. ") + (row.reason || "현재 세대의 신선도 상태")) + '</p>',
        '</div>'
      ].join("");
    }).join("") + '</div>' : '<p>이 가설에는 별도 신선도 도메인이 등록되지 않았습니다.</p>',
    '</section>'
  ].join("");
}

function renderHypothesisOutcome(item) {
  var outcome = item && item.outcomeAssessment && typeof item.outcomeAssessment === "object" ? item.outcomeAssessment : {};
  var horizons = Array.isArray(outcome.horizonAssessments) ? outcome.horizonAssessments : [];
  var contract = outcome.outcomeContract && typeof outcome.outcomeContract === "object" ? outcome.outcomeContract : {};
  var criteria = Array.isArray(contract.criteria) ? contract.criteria : [];
  var criterionAssessments = Array.isArray(outcome.criterionAssessments) ? outcome.criterionAssessments : [];
  if (!Object.keys(outcome).length) {
    return '<section class="hypothesis-detail-section"><strong>사후 결과</strong><p>아직 연결된 사후 관측이 없습니다.</p></section>';
  }
  return [
    '<section class="hypothesis-detail-section hypothesis-outcome-section">',
    '<div class="hypothesis-detail-section-head">',
    '<strong>사후 결과</strong>',
    '<span class="tone-chip ' + escapeHtml(hypothesisOutcomeTone(outcome.outcomeState)) + '">' + escapeHtml(outcome.outcomeStateLabel || "표본 부족") + '</span>',
    '</div>',
    '<p>' + escapeHtml(outcome.summary || "사후 결과를 집계 중입니다.") + '</p>',
    '<div class="hypothesis-outcome-metrics">',
    '<span>표본 ' + escapeHtml(outcome.sampleCount == null ? 0 : outcome.sampleCount) + '/' + escapeHtml(outcome.minimumSampleCount == null ? "-" : outcome.minimumSampleCount) + '</span>',
    '<span>지지 ' + escapeHtml(outcome.supportedCount == null ? 0 : outcome.supportedCount) + '</span>',
    '<span>반증 ' + escapeHtml(outcome.contradictedCount == null ? 0 : outcome.contradictedCount) + '</span>',
    '<span>판단 불가 ' + escapeHtml(outcome.inconclusiveCount == null ? 0 : outcome.inconclusiveCount) + '</span>',
    '<span>독립 사건 ' + escapeHtml(outcome.independentEpisodeCount == null ? outcome.sampleCount || 0 : outcome.independentEpisodeCount) + '</span>',
    '</div>',
    horizons.length ? '<div class="hypothesis-horizon-list">' + horizons.map(function (row) {
      return '<div><b>' + escapeHtml(hypothesisHorizonLabel(row.horizonMinutes)) + '</b><span>' + escapeHtml(row.outcomeStateLabel || "표본 부족") + ' · 표본 ' + escapeHtml(row.sampleCount == null ? 0 : row.sampleCount) + '</span></div>';
    }).join("") + '</div>' : '',
    Object.keys(contract).length ? [
      '<div class="hypothesis-outcome-contract">',
      '<b>관측 계약</b>',
      '<span>확인 시점 ' + escapeHtml(hypothesisStringList(contract.outcomeHorizonMinutes, 6).map(hypothesisHorizonLabel).join(", ") || "기본") + '</span>',
      '<span>필수 데이터 ' + escapeHtml(hypothesisStringList(contract.requiredObservationDomains, 6).map(hypothesisObservationDomainLabel).join(", ") || "현재가") + '</span>',
      '<span>최소 표본 ' + escapeHtml(contract.minimumIndependentEpisodes == null ? "-" : contract.minimumIndependentEpisodes) + '건 · 최대 지연 ' + escapeHtml(contract.maximumObservationDelayMinutes == null ? "-" : contract.maximumObservationDelayMinutes) + '분</span>',
      criteria.length ? '<div class="hypothesis-horizon-list">' + criteria.slice(0, 12).map(function (criterion) {
        return '<div><b>' + escapeHtml(hypothesisCriterionRoleLabel(criterion.role) + " · " + (criterion.label || criterion.criterionId || "검증 기준")) + '</b><span>' + escapeHtml(hypothesisCriterionMetricLabel(criterion.metric) + " " + (criterion.operator || "") + " " + (criterion.threshold == null ? "" : criterion.threshold) + (criterion.unit || "")) + '</span></div>';
      }).join("") + '</div>' : '<span>규칙별 검증 기준 없음 · 새 판단은 보수적 가격 기준으로 동결</span>',
      '</div>'
    ].join("") : '',
    criterionAssessments.length ? '<div class="hypothesis-outcome-contract"><b>기준별 실제 관측</b>' + criterionAssessments.slice(0, 12).map(function (criterion) {
      var observed = criterion.observedValue == null ? "값 없음" : String(criterion.observedValue) + String(criterion.unit || "");
      return '<span>' + escapeHtml((criterion.label || criterion.criterionId || "기준") + " · " + hypothesisCriterionStateLabel(criterion.state) + " · " + observed) + '</span>';
    }).join("") + '</div>' : '',
    outcome.evaluationModeCounts && outcome.evaluationModeCounts["legacy-directional-fallback"] ? '<p class="subtle">구조화 계약 없이 가격 방향만 판정한 과거 표본 ' + escapeHtml(outcome.evaluationModeCounts["legacy-directional-fallback"]) + '건은 별도로 표시합니다.</p>' : '',
    outcome.missingObservationDomains && outcome.missingObservationDomains.length ? '<p class="subtle">필수 데이터가 비어 제외된 항목: ' + escapeHtml(hypothesisStringList(outcome.missingObservationDomains, 6).join(", ")) + '</p>' : '',
    outcome.excludedOutcomeCount ? '<p class="subtle">시점·품질 기준을 통과하지 못한 관측 ' + escapeHtml(outcome.excludedOutcomeCount) + '건은 결과 집계에서 제외했습니다.</p>' : '',
    '<p class="subtle">이 결과는 과거 검토용 기록이며 현재 투자 행동을 자동으로 바꾸지 않습니다.</p>',
    '</section>'
  ].join("");
}

function renderHypothesisQualityReview(item) {
  var review = hypothesisQualityForItem(item);
  if (!review) return "";
  return [
    '<section class="hypothesis-detail-section hypothesis-quality-review">',
    '<div class="hypothesis-detail-section-head">',
    '<div><strong>가설 품질 점검</strong><p>' + escapeHtml(review.reason || "사후 관측을 기준으로 가설의 검토 필요 여부를 확인합니다.") + '</p></div>',
    '<span class="tone-chip ' + escapeHtml(hypothesisQualityTone(review.qualityState)) + '">' + escapeHtml(review.qualityStateLabel || "관찰 유지") + '</span>',
    '</div>',
    '<p>다음 확인: ' + escapeHtml(review.nextCheck || "새 관측과 다음 TypeDB 추론 세대를 확인합니다.") + '</p>',
    '<p class="subtle">이 점검은 규칙을 자동 수정하거나 현재 투자 판단을 바꾸지 않습니다.</p>',
    '</section>'
  ].join("");
}

function renderHypothesisTransitions(item) {
  var rows = Array.isArray(item && item.transitions) ? item.transitions : [];
  return [
    '<section class="hypothesis-detail-section">',
    '<strong>최근 상태 이력</strong>',
    rows.length ? '<div class="hypothesis-transition-list">' + rows.map(function (row) {
      return '<div><b>' + escapeHtml(hypothesisLifecycleLabel(row.currentState) || row.currentStateLabel || row.currentState || "상태 변경") + '</b><span>' + escapeHtml(row.reason || row.transitionReason || "변경 사유 없음") + '</span><em>' + escapeHtml(row.occurredAt ? formatClock(row.occurredAt) : "") + '</em></div>';
    }).join("") + '</div>' : '<p>현재 상태를 만든 이전 전이 기록이 없습니다.</p>',
    '</section>'
  ].join("");
}

function hypothesisPolicyValue(policy, key) {
  var value = policy && policy[key];
  return Array.isArray(value) ? value.join("\n") : String(value == null ? "" : value);
}

function renderHypothesisPolicyEditor(item) {
  var policies = Array.isArray(item && item.editablePolicies) ? item.editablePolicies : [];
  var fallbackPolicy = item && item.policy && typeof item.policy === "object" ? item.policy : {};
  if (!policies.length) {
    return [
      '<section class="hypothesis-detail-section hypothesis-policy-section">',
      '<strong>RuleBox 수명주기 정책</strong>',
      '<p>이 가설은 TypeDB에 저장된 현재 정책을 따릅니다. 연결된 편집 가능 RuleBox 규칙을 읽지 못했으므로 이 화면에서는 상태만 확인할 수 있습니다.</p>',
      renderHypothesisDetailList("현재 다음 확인 데이터", fallbackPolicy.nextDataRequirements, "등록된 다음 확인 데이터가 없습니다."),
      '</section>'
    ].join("");
  }
  return policies.slice(0, 4).map(function (entry) {
    var policy = entry && entry.policy && typeof entry.policy === "object" ? entry.policy : {};
    var contract = policy.outcomeContract && typeof policy.outcomeContract === "object" ? policy.outcomeContract : {};
    var ruleId = String(entry && entry.ruleId || "");
    var preview = hypothesesState.hypothesisPolicyPreview && hypothesesState.hypothesisPolicyPreview[ruleId] && typeof hypothesesState.hypothesisPolicyPreview[ruleId] === "object" ? hypothesesState.hypothesisPolicyPreview[ruleId] : {};
    var disabled = !entry.editable || isStaticPreviewHost() || settingsState.serverSettingsLocked || hypothesesState.hypothesisPolicySaving === ruleId;
    var previewReady = preview.status === "ready-for-approval";
    var approveDisabled = disabled || !previewReady;
    return [
      '<section class="hypothesis-detail-section hypothesis-policy-section">',
      '<div class="hypothesis-detail-section-head">',
      '<div><strong>RuleBox 수명주기 정책</strong><p>' + escapeHtml(entry.label || ruleId || "연결 규칙") + '</p></div>',
      '<span class="tone-chip ' + escapeHtml(entry.editable ? "watch" : "hold") + '">' + escapeHtml(entry.editable ? "편집 가능" : "읽기 전용") + '</span>',
      '</div>',
      '<p>가설 상태는 직접 바꾸지 않습니다. 다음 정상 TypeDB 추론 세대에서 이 정책과 실제 근거를 비교해 갱신됩니다.</p>',
      '<form class="hypothesis-policy-form" data-hypothesis-policy-form="' + escapeHtml(ruleId) + '">',
      '<label class="setting-field wide"><span>성립 조건 ID</span><textarea name="formationConditionIds" rows="2"' + (disabled ? " disabled" : "") + '>' + escapeHtml(hypothesisPolicyValue(policy, "formationConditionIds")) + '</textarea></label>',
      '<label class="setting-field wide"><span>반증 조건 ID</span><textarea name="invalidationConditionIds" rows="2"' + (disabled ? " disabled" : "") + '>' + escapeHtml(hypothesisPolicyValue(policy, "invalidationConditionIds")) + '</textarea></label>',
      '<label class="setting-field"><span>유효 시간(분)</span><input name="validityMinutes" type="number" min="0" value="' + escapeHtml(hypothesisPolicyValue(policy, "validityMinutes")) + '"' + (disabled ? " disabled" : "") + '></label>',
      '<label class="setting-field"><span>반증 처리 방식</span><input name="invalidationMode" type="text" value="' + escapeHtml(hypothesisPolicyValue(policy, "invalidationMode")) + '"' + (disabled ? " disabled" : "") + '></label>',
      '<label class="setting-field wide"><span>필수 신선도 데이터</span><textarea name="requiredFreshnessDomains" rows="2"' + (disabled ? " disabled" : "") + '>' + escapeHtml(hypothesisPolicyValue(policy, "requiredFreshnessDomains")) + '</textarea></label>',
      '<label class="setting-field wide"><span>다음 확인 데이터</span><textarea name="nextDataRequirements" rows="2"' + (disabled ? " disabled" : "") + '>' + escapeHtml(hypothesisPolicyValue(policy, "nextDataRequirements")) + '</textarea></label>',
      '<div class="hypothesis-policy-contract">',
      '<strong>사후 관측 계약</strong>',
      '<label class="setting-field wide"><span>확인 시점(분)</span><textarea name="outcomeHorizonMinutes" rows="2"' + (disabled ? " disabled" : "") + '>' + escapeHtml(hypothesisPolicyValue(contract, "outcomeHorizonMinutes")) + '</textarea></label>',
      '<label class="setting-field wide"><span>사후 결과에 꼭 필요한 데이터</span><textarea name="requiredObservationDomains" rows="2" placeholder="quote, trend, flow, research, portfolio"' + (disabled ? " disabled" : "") + '>' + escapeHtml(hypothesisPolicyValue(contract, "requiredObservationDomains")) + '</textarea></label>',
      '<label class="setting-field"><span>최소 독립 표본</span><input name="minimumIndependentEpisodes" type="number" min="1" max="1000" value="' + escapeHtml(hypothesisPolicyValue(contract, "minimumIndependentEpisodes")) + '"' + (disabled ? " disabled" : "") + '></label>',
      '<label class="setting-field"><span>관측 최대 지연(분)</span><input name="maximumObservationDelayMinutes" type="number" min="1" max="10080" value="' + escapeHtml(hypothesisPolicyValue(contract, "maximumObservationDelayMinutes")) + '"' + (disabled ? " disabled" : "") + '></label>',
      '<label class="setting-field wide"><span>검증에서 볼 점</span><textarea name="verificationFocus" rows="2"' + (disabled ? " disabled" : "") + '>' + escapeHtml(hypothesisPolicyValue(contract, "verificationFocus")) + '</textarea></label>',
      '<label class="setting-field wide"><span>구조화 검증 기준(JSON)</span><textarea name="outcomeCriteria" rows="8" placeholder="원인·결과·반증 기준을 JSON 배열로 입력"' + (disabled ? " disabled" : "") + '>' + escapeHtml(JSON.stringify(Array.isArray(contract.criteria) ? contract.criteria : [], null, 2)) + '</textarea></label>',
      '</div>',
      '<label class="setting-field wide"><span>변경 사유</span><textarea name="changeReason" rows="2" placeholder="변경 이유를 남기세요."' + (disabled ? " disabled" : "") + '></textarea></label>',
      '<div class="settings-actions"><button class="text-button" type="submit"' + (disabled ? " disabled" : "") + '>' + escapeHtml(hypothesesState.hypothesisPolicySaving === ruleId ? "검증 중" : "TypeDB 미리보기") + '</button><button class="text-button primary" type="button" data-hypothesis-policy-approve="' + escapeHtml(ruleId) + '"' + (approveDisabled ? " disabled" : "") + '>승인 후 반영</button></div>',
      preview.status ? '<div class="hypothesis-policy-preview ' + escapeHtml(preview.status === "ready-for-approval" ? "watch" : "caution") + '"><strong>' + escapeHtml(preview.status === "ready-for-approval" ? "미리보기 통과" : "미리보기 보류") + '</strong><span>' + escapeHtml((preview.validation && preview.validation.reason) || (preview.validation && preview.validation.matchedCount != null ? "후보 규칙 검증 완료 · 일치 관계 " + preview.validation.matchedCount + "건" : "검증 결과를 확인하세요.")) + '</span></div>' : '',
      '</form>',
      '</section>'
    ].join("");
  }).join("");
}

function renderHypothesisWorkspaceDetail(item) {
  if (!item) {
    return [
      '<section class="hypothesis-workspace-detail">',
      '<div class="hypothesis-workspace-empty">',
      '<strong>선택된 가설이 없습니다.</strong>',
      '<span>정상 TypeDB 추론 세대가 저장되면 상태와 사후 결과를 확인할 수 있습니다.</span>',
      '</div>',
      '</section>'
    ].join("");
  }
  var outcome = item.outcomeAssessment && typeof item.outcomeAssessment === "object" ? item.outcomeAssessment : {};
  var expires = item.expiresAt ? formatClock(item.expiresAt) : "유효 기간 없음";
  return [
    '<section class="hypothesis-workspace-detail">',
    '<div class="hypothesis-detail-head">',
    '<div>',
    '<div class="hypothesis-detail-head-chips">',
    '<span class="tone-chip ' + escapeHtml(hypothesisLifecycleTone(item.state)) + '">' + escapeHtml(item.stateLabel || item.state || "관찰됨") + '</span>',
    '<span class="tone-chip ' + escapeHtml(hypothesisOutcomeTone(outcome.outcomeState)) + '">' + escapeHtml(outcome.outcomeStateLabel || "표본 부족") + '</span>',
    '</div>',
    '<h3>' + escapeHtml((item.symbol || "종목") + " · " + (item.scopeLabel || "가설")) + '</h3>',
    '<p>' + escapeHtml(item.transitionReason || "현재 TypeDB 세대의 가설 상태를 확인합니다.") + '</p>',
    '</div>',
    '</div>',
    '<div class="hypothesis-detail-metrics">',
    renderHypothesisMetric("마지막 전이", item.lastTransitionAt ? formatClock(item.lastTransitionAt) : "-", "상태 갱신", hypothesisLifecycleTone(item.state)),
    renderHypothesisMetric("만료 예정", expires, "RuleBox 유효 기간", item.expiresAt ? "caution" : "hold"),
    renderHypothesisMetric("범위", item.scopeLabel || "가설", item.scope === "account" ? "계정 맥락 포함" : "계정과 분리", "hold"),
    '</div>',
    renderHypothesisDelta(item),
    renderHypothesisDetailList("현재 지지 근거", item.supportingEvidenceIds, "현재 세대에 기록된 지지 근거 ID가 없습니다."),
    renderHypothesisDetailList("현재 반대 근거", item.counterEvidenceIds, "현재 세대에 기록된 반대 근거 ID가 없습니다."),
    renderHypothesisDetailList("TypeDB 추적 경로", item.causalPathIds, "현재 세대에 기록된 인과 경로 ID가 없습니다."),
    renderHypothesisFreshness(item),
    renderHypothesisOutcome(item),
    renderHypothesisQualityReview(item),
    renderHypothesisTransitions(item),
    renderHypothesisPolicyEditor(item),
    '</section>'
  ].join("");
}

function hypothesisReviewWorkDetailPayload(lifecycleKey) {
  var key = String(lifecycleKey || "").trim();
  var detail = hypothesesState.hypothesisWorkspaceDetails[key] && typeof hypothesesState.hypothesisWorkspaceDetails[key] === "object"
    ? hypothesesState.hypothesisWorkspaceDetails[key]
    : null;
  var loading = Boolean(hypothesesState.hypothesisWorkspaceDetailLoading[key]);
  var error = String(hypothesesState.hypothesisWorkspaceDetailErrors[key] || "");
  var item = detail && Array.isArray(detail.items) ? detail.items[0] : null;
  var review = detail && detail.qualityReview && typeof detail.qualityReview === "object" ? detail.qualityReview : {};
  if (item && Array.isArray(review.items)) {
    item = Object.assign({}, item, {
      qualityReview: review.items.filter(function (entry) {
        return String(entry && entry.lifecycleKey || "") === String(item.lifecycleKey || "");
      })[0] || null
    });
  }
  var timelineFallback = !item ? instrumentTimelineEventWorkDetailPayload("hypothesis-review", key) : null;
  if (timelineFallback) return timelineFallback;
  return editorWorkDetailPayload(
    "Hypothesis Review",
    item ? ((item.symbol || "종목") + " 가설 검증") : "가설 검증 상세",
    item ? "TypeDB 수명주기, 사후 관측, RuleBox 정책" : "선택한 가설만 상세 조회합니다.",
    loading
      ? '<div class="work-detail-loading"><span class="spinner"></span><p>가설 근거와 사후 결과를 읽는 중입니다.</p></div>'
      : (error
        ? '<p class="form-error">' + escapeHtml(error) + '</p>'
        : (item ? renderInstrumentWorkspaceLink(item.symbol, "종목 전체 흐름") + renderHypothesisWorkspaceDetail(item) : '<div class="ontology-empty">표시할 가설 상세가 없습니다.</div>'))
  );
}

function hypothesisFormValue(form, name) {
  var field = form && form.querySelector('[name="' + name + '"]');
  return field ? String(field.value || "").trim() : "";
}

function hypothesisFormList(form, name) {
  return hypothesisStringList(hypothesisFormValue(form, name).split(/[\n,]+/), 100);
}

function hypothesisFormBoundedInteger(form, name, fallback, minimum, maximum) {
  var raw = hypothesisFormValue(form, name);
  if (!raw) return fallback;
  var value = Number(raw);
  if (!Number.isFinite(value)) return null;
  return Math.max(minimum, Math.min(maximum, Math.round(value)));
}

function hypothesisPolicyFromForm(form) {
  var rawMinutes = hypothesisFormValue(form, "validityMinutes");
  var validityMinutes = rawMinutes === "" ? 0 : Number(rawMinutes);
  if (!Number.isFinite(validityMinutes) || validityMinutes < 0) {
    throw new Error("유효 시간은 0 이상의 분 단위 숫자로 입력하세요.");
  }
  var horizons = hypothesisFormList(form, "outcomeHorizonMinutes").map(function (value) {
    return Number(value);
  }).filter(function (value) {
    return Number.isFinite(value) && value > 0;
  }).map(function (value) {
    return Math.round(value);
  });
  if (hypothesisFormValue(form, "outcomeHorizonMinutes") && !horizons.length) {
    throw new Error("확인 시점은 0보다 큰 분 단위 숫자로 입력하세요.");
  }
  var minimumIndependentEpisodes = hypothesisFormBoundedInteger(form, "minimumIndependentEpisodes", 0, 1, 1000);
  var maximumObservationDelayMinutes = hypothesisFormBoundedInteger(form, "maximumObservationDelayMinutes", 0, 1, 10080);
  if (minimumIndependentEpisodes == null || maximumObservationDelayMinutes == null) {
    throw new Error("사후 관측 계약의 숫자 항목을 확인하세요.");
  }
  var criteria = [];
  var rawCriteria = hypothesisFormValue(form, "outcomeCriteria");
  if (rawCriteria) {
    try {
      criteria = JSON.parse(rawCriteria);
    } catch (_error) {
      throw new Error("구조화 검증 기준은 올바른 JSON 배열이어야 합니다.");
    }
    if (!Array.isArray(criteria)) throw new Error("구조화 검증 기준은 JSON 배열이어야 합니다.");
  }
  return {
    formationConditionIds: hypothesisFormList(form, "formationConditionIds"),
    invalidationConditionIds: hypothesisFormList(form, "invalidationConditionIds"),
    validityMinutes: Math.round(validityMinutes),
    requiredFreshnessDomains: hypothesisFormList(form, "requiredFreshnessDomains"),
    nextDataRequirements: hypothesisFormList(form, "nextDataRequirements"),
    invalidationMode: hypothesisFormValue(form, "invalidationMode"),
    outcomeContract: {
      outcomeHorizonMinutes: horizons,
      requiredObservationDomains: hypothesisFormList(form, "requiredObservationDomains"),
      minimumIndependentEpisodes: minimumIndependentEpisodes,
      maximumObservationDelayMinutes: maximumObservationDelayMinutes,
      verificationFocus: hypothesisFormList(form, "verificationFocus"),
      criteria: criteria
    }
  };
}

function hypothesisPolicyFormForRule(ruleId) {
  return app.querySelector('[data-hypothesis-policy-form="' + String(ruleId || "").replace(/"/g, "\\\"") + '"]');
}

function previewHypothesisLifecyclePolicy(form) {
  var ruleId = String(form && form.getAttribute("data-hypothesis-policy-form") || "").trim();
  if (!ruleId || hypothesesState.hypothesisPolicySaving) return;
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    showSnackbar("로컬 서버에서만 RuleBox 정책을 변경할 수 있습니다.", "danger");
    return;
  }
  var policy;
  try {
    policy = hypothesisPolicyFromForm(form);
  } catch (error) {
    showSnackbar(error.message || "정책 입력값을 확인하세요.", "caution");
    return;
  }
  hypothesesState.hypothesisPolicySaving = ruleId;
  hypothesesState.hypothesisWorkspaceError = "";
  render();
  sendJson("/api/investment-brain/hypothesis-policies/" + encodeURIComponent(ruleId) + "/preview", "POST", {
    policy: policy,
    changeReason: hypothesisFormValue(form, "changeReason"),
    symbol: activeHypothesisWorkspaceItem() && activeHypothesisWorkspaceItem().symbol || ""
  })
    .then(function (result) {
      hypothesesState.hypothesisPolicyPreview = hypothesesState.hypothesisPolicyPreview || {};
      hypothesesState.hypothesisPolicyPreview[ruleId] = result && typeof result === "object" ? result : {};
      if (result && result.status === "ready-for-approval") {
        showSnackbar("TypeDB 후보 규칙 검증을 통과했습니다. 승인 후 반영을 눌러 저장하세요.");
      } else {
        showSnackbar("정책 미리보기가 보류되었습니다. 검증 결과를 확인하세요.", "caution");
      }
    })
    .catch(function (error) {
      hypothesesState.hypothesisWorkspaceError = error.message || "RuleBox 정책 미리보기를 실행하지 못했습니다.";
      showSnackbar(hypothesesState.hypothesisWorkspaceError, "danger");
    })
    .finally(function () {
      hypothesesState.hypothesisPolicySaving = "";
      render();
    });
}

function approveHypothesisLifecyclePolicy(ruleId) {
  var form = hypothesisPolicyFormForRule(ruleId);
  if (!form || hypothesesState.hypothesisPolicySaving) return;
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked) {
    showSnackbar("로컬 서버에서만 RuleBox 정책을 승인할 수 있습니다.", "danger");
    return;
  }
  var policy;
  try {
    policy = hypothesisPolicyFromForm(form);
  } catch (error) {
    showSnackbar(error.message || "정책 입력값을 확인하세요.", "caution");
    return;
  }
  if (!window.confirm("검증한 가설 수명주기 정책을 새 RuleBox 버전으로 저장합니다. 다음 TypeDB 추론 세대부터 적용됩니다. 계속할까요?")) return;
  hypothesesState.hypothesisPolicySaving = String(ruleId || "");
  hypothesesState.hypothesisWorkspaceError = "";
  render();
  sendJson("/api/investment-brain/hypothesis-policies/" + encodeURIComponent(ruleId) + "/approve", "POST", {
    policy: policy,
    changeReason: hypothesisFormValue(form, "changeReason"),
    author: "web-main",
    symbol: activeHypothesisWorkspaceItem() && activeHypothesisWorkspaceItem().symbol || ""
  })
    .then(function () {
      hypothesesState.hypothesisPolicyPreview = {};
      showSnackbar("RuleBox 정책을 승인해 저장했습니다. 새 추론 세대에서만 정책을 읽습니다.");
      return Promise.all([loadHypothesisWorkspace(true), loadHypothesisPolicyVersions(true)]);
    })
    .catch(function (error) {
      hypothesesState.hypothesisWorkspaceError = error.message || "RuleBox 정책을 승인하지 못했습니다.";
      showSnackbar(hypothesesState.hypothesisWorkspaceError, "danger");
    })
    .finally(function () {
      hypothesesState.hypothesisPolicySaving = "";
      render();
    });
}

function restoreHypothesisPolicyVersion(versionId) {
  var target = String(versionId || "").trim();
  if (!target || isStaticPreviewHost() || settingsState.serverSettingsLocked || hypothesesState.hypothesisPolicySaving) return;
  if (!window.confirm("이 RuleBox 버전으로 복원하기 전에 현재 규칙을 TypeDB에서 다시 검증합니다. 계속할까요?")) return;
  hypothesesState.hypothesisPolicySaving = "restore:" + target;
  hypothesesState.hypothesisWorkspaceError = "";
  render();
  sendJson("/api/investment-brain/hypothesis-policy-versions/" + encodeURIComponent(target) + "/restore", "POST", {
    author: "web-main",
    changeReason: "웹에서 이전 RuleBox 버전 복원",
    symbol: activeHypothesisWorkspaceItem() && activeHypothesisWorkspaceItem().symbol || ""
  })
    .then(function () {
      hypothesesState.hypothesisPolicyPreview = {};
      showSnackbar("RuleBox 이전 버전을 검증 후 복원했습니다.");
      return Promise.all([loadHypothesisWorkspace(true), loadHypothesisPolicyVersions(true)]);
    })
    .catch(function (error) {
      hypothesesState.hypothesisWorkspaceError = error.message || "RuleBox 버전을 복원하지 못했습니다.";
      showSnackbar(hypothesesState.hypothesisWorkspaceError, "danger");
    })
    .finally(function () {
      hypothesesState.hypothesisPolicySaving = "";
      render();
    });
}

function recordHypothesisPolicyBaseline() {
  if (isStaticPreviewHost() || settingsState.serverSettingsLocked || hypothesesState.hypothesisPolicySaving) return;
  hypothesesState.hypothesisPolicySaving = "baseline";
  hypothesesState.hypothesisWorkspaceError = "";
  render();
  sendJson("/api/investment-brain/hypothesis-policy-versions/baseline", "POST", { author: "web-main" })
    .then(function (result) {
      showSnackbar(result && result.status === "unchanged" ? "이미 RuleBox 기준선 버전이 있습니다." : "현재 RuleBox 기준선 버전을 기록했습니다.");
      return Promise.all([loadHypothesisWorkspace(true), loadHypothesisPolicyVersions(true)]);
    })
    .catch(function (error) {
      hypothesesState.hypothesisWorkspaceError = error.message || "RuleBox 기준선 버전을 기록하지 못했습니다.";
      showSnackbar(hypothesesState.hypothesisWorkspaceError, "danger");
    })
    .finally(function () {
      hypothesesState.hypothesisPolicySaving = "";
      render();
    });
}

function runHypothesisReplay() {
  if (hypothesesState.hypothesisReplayLoading || isStaticPreviewHost()) return;
  var payload = hypothesisWorkspacePayload();
  hypothesesState.hypothesisReplayLoading = true;
  hypothesesState.hypothesisWorkspaceError = "";
  render();
  sendJson("/api/investment-brain/hypothesis-replay", "POST", {
    accountId: payload.accountId || "",
    symbol: payload.symbol || "",
    limit: 500
  })
    .then(function (queued) {
      var job = queued && queued.job && typeof queued.job === "object" ? queued.job : {};
      if (!job.jobId) throw new Error("재현 작업 번호를 받지 못했습니다.");
      hypothesesState.hypothesisReplay = {
        status: "queued",
        summary: "과거 관측 검증을 백그라운드에서 실행하고 있습니다. 화면은 계속 사용할 수 있습니다."
      };
      render();
      return pollHistoricalReplayJob(job.jobId, 180);
    })
    .then(function (result) {
      hypothesesState.hypothesisReplay = result && typeof result === "object" ? result : {};
      showSnackbar("저장된 사후 관측을 다시 점검했습니다.");
    })
    .catch(function (error) {
      hypothesesState.hypothesisWorkspaceError = error.message || "사후 결과 재생을 실행하지 못했습니다.";
      showSnackbar(hypothesesState.hypothesisWorkspaceError, "danger");
    })
    .finally(function () {
      hypothesesState.hypothesisReplayLoading = false;
      render();
    });
}

function pollHistoricalReplayJob(jobId, remainingAttempts) {
  return requestJson(
    "/api/investment-brain/replay-jobs/" + encodeURIComponent(jobId),
    { force: true, cacheTtlMs: 0, silent: true, timeoutMs: 10000 }
  ).then(function (payload) {
    var job = payload && payload.job && typeof payload.job === "object" ? payload.job : {};
    if (job.status === "completed") return job.result || {};
    if (job.status === "failed") throw new Error(job.lastError || "과거 관측 검증에 실패했습니다.");
    if (remainingAttempts <= 0) throw new Error("과거 관측 검증이 계속 실행 중입니다. 잠시 후 검증 운영 화면에서 상태를 확인하세요.");
    return new Promise(function (resolve) {
      setTimeout(resolve, 2000);
    }).then(function () {
      return pollHistoricalReplayJob(jobId, remainingAttempts - 1);
    });
  });
}

function proposeHypothesisQualityReview() {
  if (hypothesesState.hypothesisQualityReviewAction || isStaticPreviewHost() || settingsState.serverSettingsLocked) return;
  var payload = hypothesisWorkspacePayload();
  hypothesesState.hypothesisQualityReviewAction = true;
  hypothesesState.hypothesisWorkspaceError = "";
  render();
  sendJson("/api/investment-brain/hypothesis-quality-review", "POST", {
    accountId: payload.accountId || "",
    symbol: payload.symbol || "",
    marketId: payload.marketId || "",
    scope: payload.scope || "",
    reviewedBy: "web-main"
  })
    .then(function (result) {
      var count = Number(result && result.proposalCount || 0);
      showSnackbar(count ? "가설 품질 검토 제안 " + count + "건을 저장했습니다." : "새로 저장할 가설 품질 검토 제안이 없습니다.");
      return loadHypothesisWorkspace(true);
    })
    .catch(function (error) {
      hypothesesState.hypothesisWorkspaceError = error.message || "가설 품질 검토 제안을 저장하지 못했습니다.";
      showSnackbar(hypothesesState.hypothesisWorkspaceError, "danger");
    })
    .finally(function () {
      hypothesesState.hypothesisQualityReviewAction = false;
      render();
    });
}

export { approveHypothesisLifecyclePolicy, hypothesisGovernanceWorkDetailPayload, hypothesisReviewWorkDetailPayload, loadHypothesisPolicyVersions, loadHypothesisWorkspace, loadHypothesisWorkspaceDetail, previewHypothesisLifecyclePolicy, proposeHypothesisQualityReview, recordHypothesisPolicyBaseline, renderHypothesisWorkspacePanel, restoreHypothesisPolicyVersion, runHypothesisReplay };
