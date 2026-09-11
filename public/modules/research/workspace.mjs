import { stockDisplayName } from "../instruments/catalog.mjs";
import { feedEvidenceDataMeta, feedPipelineStages, feedResearchEvidenceItems, feedSourceChannels } from "../market/feed.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { DEFAULT_RESEARCH_EVIDENCE_LIMIT } from "./constants.mjs";
import { categoricalStateOrder, feedQualitySignals, renderResearchDisclosureSections, renderResearchEvidenceQuality, researchClaimVerificationMeta, researchDisclosureDetail, researchEvidenceImpactMeta, researchEvidenceKindLabel, researchEvidenceKoreanSummary, researchEvidencePolarityLabel, researchEvidenceSummaryQualityMeta, researchEvidenceTranslationMeta, researchPromptAdmissionMeta } from "./quality.mjs";
import { currentResearchEvidence, formatFeedTime } from "./requests.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { researchState } from "../state/research.mjs";

function compareResearchEvidenceForDisplay(left, right) {
  var leftMeta = researchEvidenceImpactMeta(left);
  var rightMeta = researchEvidenceImpactMeta(right);
  var leftStates = [
    categoricalStateOrder(leftMeta.materialityState, ["context", "notable", "material"]),
    categoricalStateOrder(leftMeta.relevanceState, ["unrelated", "context", "related", "direct"]),
    categoricalStateOrder(leftMeta.sourceTrustState, ["unknown", "limited", "standard", "trusted"])
  ];
  var rightStates = [
    categoricalStateOrder(rightMeta.materialityState, ["context", "notable", "material"]),
    categoricalStateOrder(rightMeta.relevanceState, ["unrelated", "context", "related", "direct"]),
    categoricalStateOrder(rightMeta.sourceTrustState, ["unknown", "limited", "standard", "trusted"])
  ];
  for (var index = 0; index < leftStates.length; index += 1) {
    if (leftStates[index] !== rightStates[index]) return rightStates[index] - leftStates[index];
  }
  var leftPublishedAt = Date.parse((left || {}).publishedAt || (left || {}).observedAt || "") || 0;
  var rightPublishedAt = Date.parse((right || {}).publishedAt || (right || {}).observedAt || "") || 0;
  if (leftPublishedAt !== rightPublishedAt) return rightPublishedAt - leftPublishedAt;
  return String((left || {}).title || "").localeCompare(String((right || {}).title || ""));
}

function feedImpactCounts() {
  var evidence = currentResearchEvidence();
  var items = Array.isArray(evidence.items) ? evidence.items : [];
  var counts = { watch: 0, danger: 0, hold: 0 };
  items.forEach(function (item) {
    var meta = researchEvidenceImpactMeta(item);
    if (meta.tone === "watch") counts.watch += 1;
    else if (meta.tone === "danger") counts.danger += 1;
    else counts.hold += 1;
  });
  return counts;
}

function renderFeedImpactInboxPanel(snapshot, options) {
  options = options || {};
  var items = feedResearchEvidenceItems({
    snapshot: snapshot,
    portfolioOnly: options.portfolioOnly,
    limit: options.limit || (options.compact ? 4 : 6)
  });
  var metas = items.map(researchEvidenceImpactMeta);
  var good = metas.filter(function (item) { return item.tone === "watch"; }).length;
  var bad = metas.filter(function (item) { return item.tone === "danger"; }).length;
  var neutral = Math.max(0, items.length - good - bad);
  return [
    '<article class="panel feed-impact-inbox-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Impact Inbox</p>',
    '<h2>' + escapeHtml(options.portfolioOnly ? "내 종목 영향 인박스" : "투자 영향 인박스") + '</h2>',
    '<span>뉴스·공시 기사 요약을 먼저 읽고 종목별 영향, 출처, 데이터 상태를 함께 구분합니다.</span>',
    '</div>',
    '<div class="feed-impact-metrics" aria-label="주가 영향 요약">',
    renderFeedImpactMetric("호재", good, "watch"),
    renderFeedImpactMetric("악재", bad, "danger"),
    renderFeedImpactMetric("중립", neutral, "hold"),
    '</div>',
    '</div>',
    (researchState.researchEvidenceLoading && items.length) ? '<p class="data-refresh-status">저장 근거를 최신 상태로 다시 확인하고 있습니다. 현재 목록은 마지막 성공 조회 결과입니다.</p>' : '',
    items.length ? '<div class="feed-impact-workbench"><div class="feed-impact-grid">' + items.map(renderFeedImpactCard).join("") + '</div>' + renderResearchEvidenceDetailPanel(items, "기사 상세를 선택하세요") + '</div>' : (researchState.researchEvidenceLoading ? renderEmptyState({
      tone: "watch",
      label: "Evidence",
      title: "기사 요약을 불러오고 있습니다",
      description: "마지막 성공 데이터가 있으면 먼저 표시하고, 새 결과가 도착하면 영향 인박스를 갱신합니다.",
      meta: ["기사 요약", "주가 영향", "출처"]
    }) : '<p class="subtle feed-impact-empty">저장된 기사 분석이 아직 없습니다. 피드 설정에서 뉴스 아카이브를 켜거나 근거 새로고침을 실행하면 이곳에 주가 영향 요약이 표시됩니다.</p>'),
    '</article>'
  ].join("");
}

function renderFeedImpactMetric(label, value, tone) {
  return [
    '<span class="feed-impact-metric ' + escapeHtml(tone || "hold") + '"' + cardTypeAttrs("metric-cell", tone || "hold") + '>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '</span>'
  ].join("");
}

function feedEvidenceKey(item, index) {
  item = item || {};
  return String(item.evidenceId || item.id || [item.symbol, item.publishedAt || item.observedAt, item.title, index].join(":"));
}

function researchEvidenceItemByKey(key) {
  var evidence = currentResearchEvidence();
  var items = Array.isArray(evidence.items) ? evidence.items : [];
  return items.filter(function (item, index) {
    return feedEvidenceKey(item, index) === key;
  })[0] || null;
}

function renderFeedImpactCard(item, index) {
  item = item || {};
  var symbol = String(item.symbol || "").toUpperCase();
  var displayName = stockDisplayName(symbol, item.payload || item);
  var time = item.publishedAt || item.observedAt || "";
  var impact = researchEvidenceImpactMeta(item);
  var sourceMeta = feedEvidenceDataMeta(item);
  var summary = researchEvidenceKoreanSummary(item);
  var claimMeta = researchClaimVerificationMeta(item);
  var promptAdmission = researchPromptAdmissionMeta(item);
  var translation = researchEvidenceTranslationMeta(item);
  var summaryQuality = researchEvidenceSummaryQualityMeta(item);
  var key = feedEvidenceKey(item, index);
  var expanded = researchState.expandedResearchEvidenceKey === key;
  var isDisclosure = ["disclosure", "filing", "sec-filing", "sec_filing"].indexOf(String(item.kind || "").toLowerCase()) >= 0;
  return [
    '<section class="feed-impact-card compact ' + escapeHtml(impact.tone) + (expanded ? " active" : "") + '"' + cardTypeAttrs("evidence-card", impact.tone) + cardFormatAttrs("document-card", "compact") + '>',
    '<div class="feed-impact-card-head">',
    '<span class="tone-chip ' + escapeHtml(impact.tone) + '">' + escapeHtml(impact.label) + '</span>',
    '<div>',
    '<strong>' + escapeHtml(displayName || symbol || "관련 종목") + '</strong>',
    symbol && displayName !== symbol ? '<em>' + escapeHtml(symbol) + '</em>' : '',
    '</div>',
    '<b>' + escapeHtml(impact.materialityLabel) + '</b>',
    '</div>',
    '<div class="feed-impact-body">',
    '<p><strong>' + escapeHtml(isDisclosure ? "공시 요약" : "기사 요약") + '</strong> ' + escapeHtml(summary) + '</p>',
    '<h3>주가 영향: ' + escapeHtml(impact.label) + ' · ' + escapeHtml(researchEvidenceKindLabel(item.kind)) + '</h3>',
    '<div class="feed-impact-tags">',
    '<span>' + escapeHtml(impact.sourceTrustLabel) + '</span>',
    '<span>' + escapeHtml(impact.relevanceLabel) + '</span>',
    '<span class="' + escapeHtml(claimMeta.tone) + '">' + escapeHtml(claimMeta.label) + '</span>',
    '<span class="' + escapeHtml(translation.tone) + '">' + escapeHtml(translation.label) + '</span>',
    '<span class="' + escapeHtml(summaryQuality.tone) + '">' + escapeHtml(summaryQuality.label) + '</span>',
    '<span class="' + escapeHtml(promptAdmission.tone) + '">' + escapeHtml(promptAdmission.label + " · " + promptAdmission.freshnessLabel) + '</span>',
    '<span>' + escapeHtml(sourceMeta.source || "-") + '</span>',
    '<span class="' + escapeHtml(sourceMeta.tone || "hold") + '">' + escapeHtml(sourceMeta.dataLabel) + '</span>',
    '<span>' + escapeHtml(formatFeedTime(time) || "-") + '</span>',
    '</div>',
    '</div>',
    '<footer class="feed-impact-article">',
    '<span>' + escapeHtml(isDisclosure ? "공시" : "기사") + '</span>',
    '<strong>' + escapeHtml(translation.displayTitle || "제목 없음") + '</strong>',
    translation.showOriginal ? '<em>원제 ' + escapeHtml(translation.original) + '</em>' : '',
    '<button class="mini-button" type="button" data-research-evidence-toggle="' + escapeHtml(key) + '">' + escapeHtml(expanded ? "상세 표시 중" : "상세") + '</button>',
    item.url ? '<a class="open-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noreferrer" title="원문 열기">↗</a>' : '',
    '</footer>',
    '</section>'
  ].join("");
}

function renderResearchEvidenceDetailPanel(items, emptyTitle) {
  var item = researchEvidenceItemByKey(researchState.expandedResearchEvidenceKey);
  if (!item && Array.isArray(items)) {
    item = items.filter(function (entry, index) {
      return feedEvidenceKey(entry, index) === researchState.expandedResearchEvidenceKey;
    })[0] || null;
  }
  return [
    '<aside class="research-evidence-detail-panel" aria-label="선택 기사 상세">',
    item ? renderResearchEvidenceInlineDetail(item) : renderEmptyState({
      tone: "muted",
      label: "Detail",
      title: emptyTitle || "상세 항목을 선택하세요",
      description: "목록은 판단에 필요한 최소 정보만 보여주고, 기사 요약·주가 영향 분석·출처는 상세에서 확인합니다.",
      meta: ["기사 요약", "주가 영향", "출처"]
    }),
    '</aside>'
  ].join("");
}

function renderResearchEvidenceInlineDetail(item) {
  item = item || {};
  var symbol = String(item.symbol || "").toUpperCase();
  var displayName = stockDisplayName(symbol, item.payload || item);
  var time = item.publishedAt || item.observedAt || "";
  var impact = researchEvidenceImpactMeta(item);
  var sourceMeta = feedEvidenceDataMeta(item);
  var summary = researchEvidenceKoreanSummary(item);
  var claimMeta = researchClaimVerificationMeta(item);
  var promptAdmission = researchPromptAdmissionMeta(item);
  var translation = researchEvidenceTranslationMeta(item);
  var summaryQuality = researchEvidenceSummaryQualityMeta(item);
  var isDisclosure = ["disclosure", "filing", "sec-filing", "sec_filing"].indexOf(String(item.kind || "").toLowerCase()) >= 0;
  var disclosure = researchDisclosureDetail(item);
  var canDelete = Boolean(item.evidenceId) && item.evidenceId !== "preview:005930:news";
  var deleting = researchState.researchEvidenceDeleting === item.evidenceId;
  return [
    '<div class="research-evidence-detail inline-detail-surface">',
    '<div class="inline-detail-metrics">',
    renderNotificationDetailMetric("주가 영향", impact.label, impact.tone),
    renderNotificationDetailMetric("기사 중요성", impact.materialityLabel, impact.tone),
    renderNotificationDetailMetric("종목 관련성", impact.relevanceLabel, "muted"),
    renderNotificationDetailMetric("근거 종류", researchEvidenceKindLabel(item.kind), "muted"),
    renderNotificationDetailMetric("출처 신뢰", impact.sourceTrustLabel, "muted"),
    renderNotificationDetailMetric("원 발행사", sourceMeta.source || "-", sourceMeta.provenanceComplete ? "watch" : "caution"),
    renderNotificationDetailMetric("출처 등급", sourceMeta.publisherTier || "확인 중", sourceMeta.publisherTier === "D" ? "caution" : "muted"),
    renderNotificationDetailMetric("기사 관계", sourceMeta.relationshipLabel, sourceMeta.relationship === "exact-duplicate" || sourceMeta.relationship === "syndicated-copy" ? "caution" : "muted"),
    renderNotificationDetailMetric("데이터", sourceMeta.dataLabel, sourceMeta.tone),
    renderNotificationDetailMetric("주장 검증", claimMeta.label, claimMeta.tone),
    renderNotificationDetailMetric("AI 사용", promptAdmission.label, promptAdmission.tone),
    renderNotificationDetailMetric("신선도", promptAdmission.freshnessLabel, promptAdmission.tone),
    isDisclosure ? renderNotificationDetailMetric("공식 원문", disclosure.documentVerified ? "검증 완료" : "메타데이터만", disclosure.documentVerified ? "watch" : "caution") : '',
    isDisclosure ? renderNotificationDetailMetric("공시 분석", disclosure.analysisReady ? "준비 완료" : "보류", disclosure.analysisReady ? "watch" : "caution") : '',
    renderNotificationDetailMetric("번역", translation.label, translation.tone),
    renderNotificationDetailMetric("요약", summaryQuality.label, summaryQuality.tone),
    '</div>',
    '<section class="inline-detail-block primary">',
    '<strong>' + escapeHtml(isDisclosure ? "공시 요약" : "기사 요약") + '</strong>',
    '<p>' + escapeHtml(summary) + '</p>',
    '</section>',
    isDisclosure ? renderResearchDisclosureSections(item) : '',
    '<section class="inline-detail-block">',
    '<strong>' + escapeHtml(isDisclosure ? "공시 영향 판단" : "주가 영향 판단") + '</strong>',
    '<p>' + escapeHtml(impact.summary) + '</p>',
    '</section>',
    '<section class="inline-detail-block">',
    '<strong>' + escapeHtml(isDisclosure ? "공시 원문" : "기사") + '</strong>',
    '<p>' + escapeHtml(translation.displayTitle || "제목 없음") + '</p>',
    translation.showOriginal ? '<p class="subtle">원제 ' + escapeHtml(translation.original) + '</p>' : '',
    '<div class="inline-detail-tags">',
    '<span>종목 ' + escapeHtml(displayName || symbol || "-") + '</span>',
    '<span>출처 ' + escapeHtml(sourceMeta.source || "-") + '</span>',
    sourceMeta.republisher ? '<span>재배포 ' + escapeHtml(sourceMeta.republisher) + '</span>' : '',
    sourceMeta.distributionChannel ? '<span>수집 채널 ' + escapeHtml(sourceMeta.distributionChannel) + '</span>' : '',
    '<span>기사 유형 ' + escapeHtml(sourceMeta.contentTypeLabel) + '</span>',
    '<span>기사 관계 ' + escapeHtml(sourceMeta.relationshipLabel) + '</span>',
    '<span>시간 ' + escapeHtml(formatFeedTime(time) || "-") + '</span>',
    '<span>방향 ' + escapeHtml(researchEvidencePolarityLabel(item.polarity)) + '</span>',
    '<span>독립 출처 ' + escapeHtml(String(claimMeta.independentSources || 1)) + '곳</span>',
    '<span>공식 근거 ' + escapeHtml(String(claimMeta.officialCount)) + '건</span>',
    '<span class="' + escapeHtml(promptAdmission.tone) + '">AI 사용 ' + escapeHtml(promptAdmission.label) + '</span>',
    '<span class="' + escapeHtml(translation.tone) + '">' + escapeHtml(translation.label) + '</span>',
    '<span class="' + escapeHtml(summaryQuality.tone) + '">' + escapeHtml(summaryQuality.label) + '</span>',
    '</div>',
    '</section>',
    '<div class="settings-actions">',
    item.url ? '<a class="text-button primary" href="' + escapeHtml(item.url) + '" target="_blank" rel="noreferrer">원문 열기</a>' : '',
    canDelete ? '<button class="text-button danger" type="button" data-research-delete="' + escapeHtml(item.evidenceId || "") + '"' + (deleting ? " disabled" : "") + '>' + escapeHtml(deleting ? "삭제 중" : "근거 삭제") + '</button>' : '',
    '</div>',
    '</div>'
  ].join("");
}

function researchEvidenceWorkDetailPayload(key) {
  var item = researchEvidenceItemByKey(key);
  if (!item) return null;
  var symbol = String(item.symbol || "").toUpperCase();
  var displayName = stockDisplayName(symbol, item.payload || item);
  var time = item.publishedAt || item.observedAt || "";
  var impact = researchEvidenceImpactMeta(item);
  var sourceMeta = feedEvidenceDataMeta(item);
  var summary = researchEvidenceKoreanSummary(item);
  var claimMeta = researchClaimVerificationMeta(item);
  var promptAdmission = researchPromptAdmissionMeta(item);
  var translation = researchEvidenceTranslationMeta(item);
  var summaryQuality = researchEvidenceSummaryQualityMeta(item);
  var isDisclosure = ["disclosure", "filing", "sec-filing", "sec_filing"].indexOf(String(item.kind || "").toLowerCase()) >= 0;
  var disclosure = researchDisclosureDetail(item);
  return {
    kicker: "Research Evidence",
    title: translation.displayTitle || displayName || symbol || "뉴스·근거 상세",
    meta: [displayName || symbol, item.source || "-", formatFeedTime(time) || "-"].filter(Boolean).join(" · "),
    body: [
      '<section class="work-detail-section">',
      '<div class="work-detail-metric-row">',
      renderNotificationDetailMetric("주가 영향", impact.label, impact.tone),
      renderNotificationDetailMetric("기사 중요성", impact.materialityLabel, impact.tone),
      renderNotificationDetailMetric("종목 관련성", impact.relevanceLabel, "muted"),
      renderNotificationDetailMetric("근거 종류", researchEvidenceKindLabel(item.kind), "muted"),
      renderNotificationDetailMetric("출처 신뢰", impact.sourceTrustLabel, "muted"),
      renderNotificationDetailMetric("원 발행사", sourceMeta.source || "-", sourceMeta.provenanceComplete ? "watch" : "caution"),
      renderNotificationDetailMetric("출처 등급", sourceMeta.publisherTier || "확인 중", sourceMeta.publisherTier === "D" ? "caution" : "muted"),
      renderNotificationDetailMetric("기사 관계", sourceMeta.relationshipLabel, sourceMeta.relationship === "exact-duplicate" || sourceMeta.relationship === "syndicated-copy" ? "caution" : "muted"),
      renderNotificationDetailMetric("데이터", sourceMeta.dataLabel, sourceMeta.tone),
      renderNotificationDetailMetric("AI 사용", promptAdmission.label, promptAdmission.tone),
      renderNotificationDetailMetric("신선도", promptAdmission.freshnessLabel, promptAdmission.tone),
      isDisclosure ? renderNotificationDetailMetric("공식 원문", disclosure.documentVerified ? "검증 완료" : "메타데이터만", disclosure.documentVerified ? "watch" : "caution") : '',
      isDisclosure ? renderNotificationDetailMetric("공시 분석", disclosure.analysisReady ? "준비 완료" : "보류", disclosure.analysisReady ? "watch" : "caution") : '',
      renderNotificationDetailMetric("번역", translation.label, translation.tone),
      renderNotificationDetailMetric("요약", summaryQuality.label, summaryQuality.tone),
      '</div>',
      '</section>',
      '<section class="work-detail-section primary">',
      '<strong>' + escapeHtml(isDisclosure ? "공시 요약" : "기사 요약") + '</strong>',
      '<p>' + escapeHtml(summary) + '</p>',
      '</section>',
      isDisclosure ? renderResearchDisclosureSections(item) : '',
      '<section class="work-detail-section">',
      '<strong>' + escapeHtml(isDisclosure ? "공시 영향 판단" : "주가 영향 판단") + '</strong>',
      '<p>' + escapeHtml(impact.summary) + '</p>',
      '</section>',
      '<section class="work-detail-section">',
      '<strong>' + escapeHtml(isDisclosure ? "공시 원문" : "기사") + '</strong>',
      '<p>' + escapeHtml(translation.displayTitle || "제목 없음") + '</p>',
      translation.showOriginal ? '<p class="subtle">원제 ' + escapeHtml(translation.original) + '</p>' : '',
      '<div class="notification-detail-tags">',
      '<span>출처 ' + escapeHtml(sourceMeta.source || "-") + '</span>',
      sourceMeta.republisher ? '<span>재배포 ' + escapeHtml(sourceMeta.republisher) + '</span>' : '',
      sourceMeta.distributionChannel ? '<span>수집 채널 ' + escapeHtml(sourceMeta.distributionChannel) + '</span>' : '',
      '<span>기사 유형 ' + escapeHtml(sourceMeta.contentTypeLabel) + '</span>',
      '<span>기사 관계 ' + escapeHtml(sourceMeta.relationshipLabel) + '</span>',
      '<span>시간 ' + escapeHtml(formatFeedTime(time) || "-") + '</span>',
      '<span>방향 ' + escapeHtml(researchEvidencePolarityLabel(item.polarity)) + '</span>',
      '<span class="' + escapeHtml(translation.tone) + '">' + escapeHtml(translation.label) + '</span>',
      '<span class="' + escapeHtml(summaryQuality.tone) + '">' + escapeHtml(summaryQuality.label) + '</span>',
      '</div>',
      '</section>',
      item.url ? '<a class="text-button primary" href="' + escapeHtml(item.url) + '" target="_blank" rel="noreferrer">원문 열기</a>' : '',
    ].join("")
  };
}

function feedPipelineWorkDetailPayload() {
  return {
    kicker: "Data Flow",
    title: "수집·판단 흐름 전체",
    meta: "원천 수집부터 알림 후보까지",
    body: '<div class="work-detail-list">' + feedPipelineStages().map(function (stage) {
      return [
        '<section class="work-detail-row ' + escapeHtml(stage.tone || "hold") + '">',
        '<b>' + escapeHtml(stage.step) + '</b>',
        '<div><strong>' + escapeHtml(stage.title) + '</strong><span>' + escapeHtml(stage.detail || "") + '</span></div>',
        '<em>' + escapeHtml(stage.value || "-") + '</em>',
        '</section>'
      ].join("");
    }).join("") + '</div>'
  };
}

function feedSourcesWorkDetailPayload() {
  return {
    kicker: "Source Matrix",
    title: "수집 채널 상세",
    meta: "사용 여부, 준비도, 수집 주기",
    body: '<div class="work-detail-list">' + feedSourceChannels().map(function (channel) {
      return [
        '<section class="work-detail-row ' + escapeHtml(channel.tone || "hold") + '">',
        '<span class="tone-chip ' + escapeHtml(channel.tone || "hold") + '">' + escapeHtml(channel.enabled ? (channel.ready === false ? "키 확인" : "사용") : "중지") + '</span>',
        '<div><strong>' + escapeHtml(channel.label) + '</strong><span>' + escapeHtml(channel.route) + '</span></div>',
        '<em>' + escapeHtml(channel.cadence || "-") + '</em>',
        '</section>'
      ].join("");
    }).join("") + '</div>'
  };
}

function feedQualityWorkDetailPayload() {
  return {
    kicker: "Data Quality",
    title: "데이터 품질 상세",
    meta: "저장 근거, 원천 준비도, 캐시 상태",
    body: '<div class="work-detail-grid">' + feedQualitySignals().map(function (item) {
      return [
        '<section class="work-detail-card ' + escapeHtml(item.tone || "hold") + '">',
        '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.value || "-") + '</span>',
        '<strong>' + escapeHtml(item.label || "-") + '</strong>',
        '<p>' + escapeHtml(item.description || "") + '</p>',
        '</section>'
      ].join("");
    }).join("") + '</div>'
  };
}

function renderResearchEvidencePanel() {
  var evidence = currentResearchEvidence();
  var items = Array.isArray(evidence.items) ? evidence.items : [];
  var summary = evidence.summary || {};
  return [
    '<article class="panel research-evidence-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Evidence DB</p>',
    '<h2>저장 근거 조회·관리</h2>',
    '</div>',
    '<span class="metric">' + escapeHtml(Number(summary.total || 0)) + '</span>',
    '</div>',
    renderResearchEvidenceQuality(evidence.claimQuality),
    renderResearchEvidenceFilters(),
    '<div class="research-evidence-workbench">',
    '<div class="research-evidence-list">',
    researchState.researchEvidenceLoading && !items.length ? '<div class="panel skeleton"></div>' : '',
    researchState.researchEvidenceLoading && items.length ? '<p class="data-refresh-status">조회 조건에 맞춰 최신 근거를 다시 읽고 있습니다. 현재 목록은 마지막 성공 조회 결과입니다.</p>' : '',
    researchState.researchEvidenceError ? '<p class="form-error">' + escapeHtml(researchState.researchEvidenceError) + '</p>' : '',
    (!researchState.researchEvidenceLoading && !researchState.researchEvidenceError && !items.length) ? '<p class="subtle">저장된 뉴스·공시·SEC 근거가 아직 없습니다. 외부 데이터 워커가 수집하면 이곳에 표시됩니다.</p>' : '',
    items.length ? items.map(renderResearchEvidenceItem).join("") : '',
    '</div>',
    items.length ? renderResearchEvidenceDetailPanel(items, "근거 상세를 선택하세요") : '',
    '</div>',
    '</article>'
  ].join("");
}

function renderResearchEvidenceFilters() {
  var filters = researchState.researchEvidenceFilters || {};
  return [
    '<form class="research-evidence-filters" data-research-evidence-form>',
    '<label class="setting-field">',
    '<span>회사명 또는 코드</span>',
    '<input data-research-filter="symbol" type="text" value="' + escapeHtml(filters.symbol || "") + '" placeholder="삼성전자 또는 005930" autocomplete="off" />',
    '</label>',
    '<label class="setting-field">',
    '<span>근거 종류</span>',
    '<select data-research-filter="kind">',
    [
      { value: "", label: "전체" },
      { value: "news", label: "뉴스" },
      { value: "disclosure", label: "공시" },
      { value: "filing", label: "SEC" },
      { value: "market-move", label: "가격 변동" }
    ].map(function (option) {
      return '<option value="' + escapeHtml(option.value) + '"' + (String(filters.kind || "") === option.value ? " selected" : "") + '>' + escapeHtml(option.label) + '</option>';
    }).join(""),
    '</select>',
    '</label>',
    '<label class="setting-field">',
    '<span>조회 수</span>',
    '<select data-research-filter="limit">',
    ["8", "12", "30", "80", "150", "300"].map(function (value) {
      return '<option value="' + escapeHtml(value) + '"' + (String(filters.limit || DEFAULT_RESEARCH_EVIDENCE_LIMIT) === value ? " selected" : "") + '>' + escapeHtml(value) + '건</option>';
    }).join(""),
    '</select>',
    '</label>',
    '<div class="settings-actions feed-filter-actions">',
    '<button class="text-button primary" type="submit">' + (researchState.researchEvidenceLoading ? "조회 중" : "조회") + '</button>',
    '<button class="text-button" type="button" data-action="revalidate-research-evidence"' + (researchState.researchEvidenceRevalidating ? " disabled" : "") + '>' + (researchState.researchEvidenceRevalidating ? "검증 중" : "검증 갱신") + '</button>',
    '</div>',
    '</form>'
  ].join("");
}

function renderResearchEvidenceItem(item, index) {
  var symbol = String(item.symbol || "").toUpperCase();
  var displayName = stockDisplayName(symbol, item.payload || item);
  var time = item.publishedAt || item.observedAt || "";
  var impact = researchEvidenceImpactMeta(item);
  var sourceMeta = feedEvidenceDataMeta(item);
  var summary = researchEvidenceKoreanSummary(item);
  var claimMeta = researchClaimVerificationMeta(item);
  var translation = researchEvidenceTranslationMeta(item);
  var summaryQuality = researchEvidenceSummaryQualityMeta(item);
  var key = feedEvidenceKey(item, index);
  var expanded = researchState.expandedResearchEvidenceKey === key;
  var isDisclosure = ["disclosure", "filing", "sec-filing"].indexOf(String(item.kind || "").toLowerCase()) >= 0;
  var documentLabel = item.documentVerified ? "공식 원문 확인" : (isDisclosure ? "공식 메타데이터만" : "");
  return [
    '<div class="research-evidence-item compact ' + escapeHtml(impact.tone) + (expanded ? " active" : "") + '"' + cardTypeAttrs("evidence-card", impact.tone) + cardFormatAttrs("document-card", "compact") + ' role="button" tabindex="0" data-research-evidence-toggle="' + escapeHtml(key) + '" aria-label="' + escapeHtml((translation.displayTitle || "저장 근거") + " 상세 보기") + '">',
    '<div class="research-evidence-main">',
    '<div class="research-evidence-meta">',
    '<span class="tone-chip ' + escapeHtml(impact.tone) + '">' + escapeHtml(impact.label) + '</span>',
    '<span>' + escapeHtml(displayName) + (symbol && displayName !== symbol ? ' <em>' + escapeHtml(symbol) + '</em>' : '') + '</span>',
    '<span>' + escapeHtml(researchEvidenceKindLabel(item.kind)) + '</span>',
    '</div>',
    '<p><strong>' + (isDisclosure ? "공시 요약" : "기사 요약") + '</strong> ' + escapeHtml(summary) + '</p>',
    '<h3>주가 영향: ' + escapeHtml(impact.label) + ' · ' + escapeHtml(impact.materialityLabel) + '</h3>',
    '<div class="research-evidence-metrics">',
    '<span>방향 <strong>' + escapeHtml(researchEvidencePolarityLabel(item.polarity)) + '</strong></span>',
    '<span>중요성 <strong>' + escapeHtml(impact.materialityLabel) + '</strong></span>',
    '<span>관련성 <strong>' + escapeHtml(impact.relevanceLabel) + '</strong></span>',
    '<span>출처 <strong>' + escapeHtml(impact.sourceTrustLabel) + '</strong></span>',
    '<span>검증 <strong>' + escapeHtml(claimMeta.label) + '</strong></span>',
    '<span>번역 <strong class="' + escapeHtml(translation.tone) + '">' + escapeHtml(translation.label) + '</strong></span>',
    '<span>요약 <strong class="' + escapeHtml(summaryQuality.tone) + '">' + escapeHtml(summaryQuality.label) + '</strong></span>',
    isDisclosure ? '<span>문서 <strong>' + escapeHtml(documentLabel) + '</strong></span>' : '',
    '</div>',
    '<footer class="research-evidence-article">',
    '<span>기사</span>',
    '<strong>' + escapeHtml(translation.displayTitle || "제목 없음") + '</strong>',
    translation.showOriginal ? '<em>원제 ' + escapeHtml(translation.original) + '</em>' : '',
    '<em>' + escapeHtml([sourceMeta.source || "-", sourceMeta.dataLabel, formatFeedTime(time) || "-"].join(" · ")) + '</em>',
    '</footer>',
    '</div>',
    '</div>'
  ].join("");
}

export { compareResearchEvidenceForDisplay, feedEvidenceKey, feedImpactCounts, feedPipelineWorkDetailPayload, feedQualityWorkDetailPayload, feedSourcesWorkDetailPayload, renderFeedImpactInboxPanel, renderResearchEvidencePanel, researchEvidenceWorkDetailPayload };
