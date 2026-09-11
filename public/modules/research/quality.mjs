import { newsStateSettingLabel, settingEnabled } from "../decisions/signals.mjs";
import { feedPipelineStages, feedSourceChannels, renderFeedDetailToggle } from "../market/feed.mjs";
import { currentResearchEvidence, feedTimeValue, formatFeedTime } from "./requests.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { isConfiguredSetting, settingValue } from "../settings/fields.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { configuredCount } from "../shell/commands.mjs";
import { cardFormatAttrs, cardTypeAttrs } from "../shell/layout.mjs";
import { researchState } from "../state/research.mjs";

function feedFreshness(value) {
  var time = typeof value === "number" ? value : feedTimeValue(value);
  if (!time) return { label: "미수집", tone: "caution" };
  var hours = Math.max(0, (Date.now() - time) / 3600000);
  if (hours < 1) return { label: "1시간 이내", tone: "watch" };
  if (hours < 24) return { label: Math.round(hours) + "시간 전", tone: "watch" };
  if (hours < 72) return { label: Math.round(hours / 24) + "일 전", tone: "caution" };
  return { label: Math.round(hours / 24) + "일 전", tone: "danger" };
}

function feedQualitySignals() {
  var evidence = currentResearchEvidence();
  var summary = evidence.summary || {};
  var articleAnalysis = evidence.articleAnalysis || {};
  var latest = feedFreshness(summary.latestSeenAt);
  var kisEnabled = settingEnabled("kisMarketSignalsEnabled");
  var newsEnabled = settingEnabled("newsCollectionEnabled");
  var dartEnabled = settingEnabled("externalDartEnabled");
  var secEnabled = settingEnabled("externalSecEnabled");
  var alphaEnabled = settingEnabled("externalAlphaEnabled");
  var fredEnabled = settingEnabled("externalFredEnabled");
  var cryptoEnabled = settingEnabled("externalCoinGeckoEnabled");
  return [
    {
      label: "저장된 리서치 근거",
      value: Number(summary.total || 0) + "건",
      tone: Number(summary.total || 0) ? latest.tone : "caution",
      description: "온톨로지와 AI 의견에 들어갈 수 있는 DB 저장 근거입니다. 최근 저장 " + latest.label + "."
    },
    {
      label: "시장·수급 데이터",
      value: kisEnabled ? (configuredCount(["kisAppKey", "kisAppSecret"]) + "/2") : "중지",
      tone: kisEnabled && configuredCount(["kisAppKey", "kisAppSecret"]) >= 2 ? "watch" : (kisEnabled ? "caution" : "hold"),
      description: "체결강도, 호가, 투자자 수급 같은 장중 신호를 관계 판단의 ABox 근거로 사용합니다."
    },
    {
      label: "뉴스 수집",
      value: newsEnabled ? String((settingValue("newsCollectionInternationalProviders") || settingValue("newsCollectionProviders") || defaultSettings.newsCollectionInternationalProviders || defaultSettings.newsCollectionProviders || "").split(",").filter(Boolean).length + (settingValue("newsCollectionKoreanProviders") || defaultSettings.newsCollectionKoreanProviders ? 1 : 0)) + "개 채널" : "중지",
      tone: newsEnabled ? "watch" : "hold",
      description: "Google News, Yahoo Finance, GDELT를 종목별로 수집해 research_evidence에 저장합니다."
    },
    {
      label: "기사 요약·번역",
      value: Number(articleAnalysis.summaryReadyCount || 0) + "건",
      tone: Number(articleAnalysis.summaryBlockedCount || 0) ? "danger" : (Number(articleAnalysis.summaryNeedsReviewCount || 0) || Number(articleAnalysis.translationPendingCount || 0) ? "caution" : "watch"),
      description: "한글 요약 통과 " + Number(articleAnalysis.summaryReadyCount || 0) + "건, 영문 제목 번역 완료 " + Number(articleAnalysis.translationCompleteCount || 0) + "건, 대기 " + Number(articleAnalysis.translationPendingCount || 0) + "건입니다."
    },
    {
      label: "뉴스 출처 계보",
      value: Number(articleAnalysis.provenanceCompleteCount || 0) + "/" + Number(articleAnalysis.newsCount || 0) + "건",
      tone: Number(articleAnalysis.unresolvedPublisherCount || 0) ? "caution" : "watch",
      description: "원 발행사 확인 " + Number(articleAnalysis.provenanceCompleteCount || 0) + "건, 미확인 " + Number(articleAnalysis.unresolvedPublisherCount || 0) + "건, 전재·중복 " + Number(articleAnalysis.duplicatePublicationCount || 0) + "건입니다."
    },
    {
      label: "공시 수집",
      value: dartEnabled ? (isConfiguredSetting("opendartApiKey") ? "준비됨" : "키 필요") : "중지",
      tone: dartEnabled && isConfiguredSetting("opendartApiKey") ? "watch" : (dartEnabled ? "caution" : "hold"),
      description: "OpenDART 주요 공시를 종목별 이벤트 근거로 저장합니다."
    },
    {
      label: "SEC 수집",
      value: secEnabled ? "사용" : "중지",
      tone: secEnabled ? "watch" : "hold",
      description: "미국 종목의 EDGAR filings를 보조 근거로 저장합니다."
    },
    {
      label: "거시·크립토 보조 신호",
      value: [fredEnabled ? "FRED" : "", cryptoEnabled ? "CoinGecko" : "", alphaEnabled ? "Alpha" : ""].filter(Boolean).join(" · ") || "중지",
      tone: (fredEnabled || cryptoEnabled || alphaEnabled) ? "watch" : "hold",
      description: "금리, 유동성, 크립토, 해외 가격 변화를 포트폴리오 관계 신호에 보조 입력으로 연결합니다."
    },
    {
      label: "외부 API 캐시",
      value: (settingValue("externalApiFetchIntervalMinutes") || defaultSettings.externalApiFetchIntervalMinutes || "30") + "분",
      tone: "watch",
      description: "워커가 같은 외부 신호 묶음을 다시 사용할 수 있는 최소 갱신 간격입니다."
    }
  ];
}

function renderFeedQualityPanel() {
  var evidence = currentResearchEvidence();
  var summary = evidence.summary || {};
  var kinds = Array.isArray(summary.byKind) ? summary.byKind : [];
  return [
    '<article class="panel feed-quality-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Data Quality</p>',
    '<h2>데이터 품질 상태</h2>',
    '</div>',
    '<div class="settings-actions">',
    '<button class="text-button" data-action="refresh-research-evidence">' + (researchState.researchEvidenceLoading ? "조회 중" : "저장 근거 조회") + '</button>',
    renderFeedDetailToggle("quality", "품질 전체", "text-button compact"),
    '</div>',
    '</div>',
    researchState.expandedFeedDetail === "quality" ? '' : '<div class="feed-quality-grid">' + feedQualitySignals().slice(0, 4).map(renderFeedQualitySignal).join("") + '</div>',
    researchState.expandedFeedDetail === "quality" ? '' : '<div class="theme-radar feed-quality-tags">' + (kinds.length ? kinds.slice(0, 8).map(function (entry) {
      return '<span>' + escapeHtml(researchEvidenceKindLabel(entry.name)) + ' <strong>' + escapeHtml(entry.count) + '</strong></span>';
    }).join("") : '<span>저장 근거 대기</span>') + '</div>',
    researchState.researchEvidenceError ? '<p class="form-error">' + escapeHtml(researchState.researchEvidenceError) + '</p>' : '',
    researchState.expandedFeedDetail === "quality" ? renderFeedInlineDetail("quality") : '',
    '</article>'
  ].join("");
}

function renderFeedQualitySignal(item) {
  return [
    '<div class="feed-quality-card"' + cardTypeAttrs("diagnostic-card", item.tone || "hold") + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.value || "-") + '</span>',
    '<strong>' + escapeHtml(item.label || "-") + '</strong>',
    '<p>' + escapeHtml(item.description || "") + '</p>',
    '</div>'
  ].join("");
}

function renderFeedInlineDetail(kind) {
  if (kind === "pipeline") {
    return [
      '<div class="feed-inline-detail">',
      '<div class="inline-detail-list">',
      feedPipelineStages().map(function (stage) {
        return [
          '<section class="inline-detail-row ' + escapeHtml(stage.tone || "hold") + '">',
          '<b>' + escapeHtml(stage.step) + '</b>',
          '<div><strong>' + escapeHtml(stage.title) + '</strong><span>' + escapeHtml(stage.detail || "") + '</span></div>',
          '<em>' + escapeHtml(stage.value || "-") + '</em>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }
  if (kind === "sources") {
    return [
      '<div class="feed-inline-detail">',
      '<div class="inline-detail-list">',
      feedSourceChannels().map(function (channel) {
        return [
          '<section class="inline-detail-row ' + escapeHtml(channel.tone || "hold") + '">',
          '<span class="tone-chip ' + escapeHtml(channel.tone || "hold") + '">' + escapeHtml(channel.enabled ? (channel.ready === false ? "키 확인" : "사용") : "중지") + '</span>',
          '<div><strong>' + escapeHtml(channel.label) + '</strong><span>' + escapeHtml(channel.route) + '</span></div>',
          '<em>' + escapeHtml(channel.cadence || "-") + '</em>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }
  if (kind === "quality") {
    return [
      '<div class="feed-inline-detail">',
      '<div class="inline-detail-grid">',
      feedQualitySignals().map(function (item) {
        return [
          '<section class="inline-detail-card ' + escapeHtml(item.tone || "hold") + '">',
          '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.value || "-") + '</span>',
          '<strong>' + escapeHtml(item.label || "-") + '</strong>',
          '<p>' + escapeHtml(item.description || "") + '</p>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }
  return "";
}

function researchEvidenceKindLabel(kind) {
  return {
    "news": "뉴스",
    "disclosure": "공시",
    "filing": "공시",
    "sec-filing": "SEC",
    "market-move": "가격 변동",
    "market-signal": "시장 신호",
    "financial-fact": "재무 사실",
    "fundamental": "펀더멘털",
    "macro": "거시",
    "crypto": "크립토",
    "investor-flow": "수급"
  }[String(kind || "").toLowerCase()] || kind || "근거";
}

function researchEvidencePolarityLabel(polarity) {
  return {
    "support": "우호",
    "risk": "위험",
    "context": "맥락"
  }[String(polarity || "").toLowerCase()] || polarity || "맥락";
}

function researchEvidenceState(item, key, fallback) {
  item = item || {};
  var payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  return String(item[key] || payload[key] || fallback || "").trim().toLowerCase();
}

function researchClaimVerificationMeta(item) {
  item = item || {};
  var payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  var governance = item.claimVerification && typeof item.claimVerification === "object"
    ? item.claimVerification
    : (payload.evidenceGovernance && typeof payload.evidenceGovernance === "object" ? payload.evidenceGovernance : {});
  var state = String(governance.claimState || governance.verificationStatus || "reported").toLowerCase();
  var labels = {
    "reported": "보도됨",
    "verified-primary": "공식 확인",
    "corroborated": "교차 확인",
    "conflicted": "출처 상충",
    "superseded": "정정됨",
    "expired": "기한 지남",
    "rejected": "근거 제외",
    "verified-secondary": "출처 확인"
  };
  var tone = state === "corroborated" || state === "verified-primary" ? "watch"
    : (state === "conflicted" || state === "superseded" || state === "rejected" ? "danger" : "hold");
  return {
    state: state,
    label: labels[state] || "검증 대기",
    tone: tone,
    eligible: Boolean(governance.investmentJudgmentEligible),
    publisher: governance.sourcePublisher || item.source || "",
    origin: governance.sourceOrigin || "",
    independentSources: Number(governance.independentSourceCount || 0),
    officialCount: Array.isArray(governance.officialEvidenceIds) ? governance.officialEvidenceIds.length : 0,
    corroboratingCount: Array.isArray(governance.corroboratingEvidenceIds) ? governance.corroboratingEvidenceIds.length : 0,
    conflictCount: Array.isArray(governance.conflictingEvidenceIds) ? governance.conflictingEvidenceIds.length : 0,
    claimCount: Number(governance.claimCount || 0)
  };
}

function researchPromptAdmissionMeta(item) {
  item = item || {};
  var admission = item.promptEvidenceAdmission && typeof item.promptEvidenceAdmission === "object"
    ? item.promptEvidenceAdmission
    : {};
  var usage = String(admission.usage || "blocked").toLowerCase();
  var freshness = String(admission.freshnessState || "unknown").toLowerCase();
  var usageLabels = {
    "decision": "판단 근거",
    "reference": "참고 근거",
    "alert": "알림 전용",
    "display": "열람 전용",
    "blocked": "사용 차단"
  };
  var freshnessLabels = {
    "fresh": "최신",
    "stale": "기한 지남",
    "future": "시각 오류",
    "unknown": "기준일 미확인"
  };
  var tone = usage === "decision" || usage === "reference" ? "watch"
    : (usage === "blocked" || freshness === "stale" || freshness === "future" ? "danger" : "hold");
  return {
    usage: usage,
    label: usageLabels[usage] || "사용 차단",
    freshness: freshness,
    freshnessLabel: freshnessLabels[freshness] || "기준일 미확인",
    tone: tone,
    ageMinutes: Number(admission.ageMinutes),
    reasonCodes: Array.isArray(admission.reasonCodes) ? admission.reasonCodes : []
  };
}

function renderResearchEvidenceQuality(quality) {
  quality = quality && typeof quality === "object" ? quality : {};
  var state = String(quality.alertState || "healthy").toLowerCase();
  var label = state === "degraded" ? "검증 경고" : (state === "attention" ? "확인 필요" : "정상");
  return [
    '<div class="research-evidence-metrics research-evidence-quality">',
    '<span>검증 상태 <strong>' + escapeHtml(label) + '</strong></span>',
    '<span>투자 사용 가능 <strong>' + escapeHtml(Number(quality.eligibleClaimCount || 0)) + '건</strong></span>',
    '<span>재게시 중복 <strong>' + escapeHtml(Number(quality.syndicatedDuplicateCount || 0)) + '건</strong></span>',
    '<span>미검증 <strong>' + escapeHtml(Number(quality.ungovernedEvidenceCount || 0)) + '건</strong></span>',
    '<span>원문 공시 <strong>' + escapeHtml(Number(quality.officialDocumentContentCount || 0)) + '건</strong></span>',
    '<span>메타데이터 공시 <strong>' + escapeHtml(Number(quality.officialMetadataOnlyCount || 0)) + '건</strong></span>',
    '</div>'
  ].join("");
}

function researchEvidenceTextCorpus(item) {
  item = item || {};
  var payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  return [
    item.title,
    item.summary,
    item.description,
    item.body,
    item.content,
    item.text,
    payload.title,
    payload.summary,
    payload.description,
    payload.body,
    payload.content,
    payload.text
  ].filter(Boolean).join(" ").toLowerCase();
}

function researchEvidenceKoreanSummary(item) {
  item = item || {};
  var payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  var disclosure = item.disclosureAnalysis && typeof item.disclosureAnalysis === "object" ? item.disclosureAnalysis
    : (payload.disclosureAnalysis && typeof payload.disclosureAnalysis === "object" ? payload.disclosureAnalysis : {});
  var summary = disclosure.summary || item.articleSummaryKo || item.analysisSummary || item.summaryKo || item.bodySummary || item.summary || item.description
    || payload.articleSummaryKo || payload.analysisSummary || payload.summaryKo || payload.bodySummary || payload.summary || payload.description
    || item.title || payload.title || "기사 분석이 아직 준비되지 않았습니다.";
  if (hasCorruptNewsText(summary)) return "원문 인코딩 점검으로 요약을 보류했습니다.";
  var koreanLetters = (String(summary).match(/[가-힣]/g) || []).length;
  var latinLetters = (String(summary).match(/[A-Za-z]/g) || []).length;
  if (latinLetters > Math.max(24, koreanLetters * 2)) return "기사 분석을 준비 중입니다. 원문과 분석 결과는 상세에서 확인하세요.";
  return summary;
}

function hasCorruptNewsText(value) {
  var text = String(value || "");
  return text.indexOf("\uFFFD") >= 0 || /(?:Ã.|Â.|â..){2,}/.test(text);
}

function researchEvidenceTranslationMeta(item) {
  item = item || {};
  var payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  var original = String(item.originalTitle || payload.originalTitle || item.title || payload.title || "").trim();
  var language = String(item.sourceLanguage || payload.sourceLanguage || "").trim().toLowerCase();
  if (!language && original) {
    var koreanLetters = (original.match(/[가-힣]/g) || []).length;
    var latinLetters = (original.match(/[A-Za-z]/g) || []).length;
    language = koreanLetters >= Math.max(2, Math.floor(latinLetters / 3)) ? "ko" : (latinLetters >= 12 ? "en" : "unknown");
  }
  var translated = String(item.translatedTitleKo || payload.translatedTitleKo || "").trim();
  var status = String(item.translationStatus || payload.translationStatus || "").trim().toLowerCase();
  var translatedKorean = (translated.match(/[가-힣]/g) || []).length >= 2;
  var complete = language === "en" && translatedKorean;
  if (complete) status = "complete";
  if (!status) status = language === "en" ? "pending" : "not-required";
  var labels = {
    complete: "번역 완료",
    pending: "번역 대기",
    unavailable: "번역 불가",
    "not-required": "원문 한국어"
  };
  return {
    original: original,
    translated: complete ? translated : "",
    displayTitle: complete ? translated : original,
    language: language,
    status: status,
    label: labels[status] || "번역 점검",
    tone: status === "complete" || status === "not-required" ? "watch" : (status === "unavailable" ? "danger" : "caution"),
    showOriginal: complete && Boolean(original) && original !== translated
  };
}

function researchEvidenceSummaryQualityMeta(item) {
  item = item || {};
  var payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  var quality = item.articleSummaryQuality && typeof item.articleSummaryQuality === "object" ? item.articleSummaryQuality
    : (payload.articleSummaryQuality && typeof payload.articleSummaryQuality === "object" ? payload.articleSummaryQuality : {});
  var state = String(item.summaryQualityState || payload.summaryQualityState || quality.state || "needs-review").trim().toLowerCase();
  var rawSummary = item.articleSummaryKo || item.summary || payload.articleSummaryKo || payload.summary || "";
  if (hasCorruptNewsText(rawSummary)) state = "blocked";
  var labels = {
    ready: "요약 검증 통과",
    "needs-review": "요약 점검 필요",
    blocked: "요약 보류"
  };
  return {
    state: state,
    label: labels[state] || "요약 점검 필요",
    tone: state === "ready" ? "watch" : (state === "blocked" ? "danger" : "caution"),
    issues: Array.isArray(quality.issues) ? quality.issues : []
  };
}

function researchDisclosureDetail(item) {
  item = item || {};
  var payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  var analysis = item.disclosureAnalysis && typeof item.disclosureAnalysis === "object" ? item.disclosureAnalysis
    : (payload.disclosureAnalysis && typeof payload.disclosureAnalysis === "object" ? payload.disclosureAnalysis : {});
  var quality = item.disclosureDocumentQuality && typeof item.disclosureDocumentQuality === "object" ? item.disclosureDocumentQuality
    : (payload.disclosureDocumentQuality && typeof payload.disclosureDocumentQuality === "object" ? payload.disclosureDocumentQuality : {});
  var audit = item.eligibilityAudit && typeof item.eligibilityAudit === "object" ? item.eligibilityAudit : {};
  return {
    analysis: analysis,
    quality: quality,
    audit: audit,
    confirmedFacts: Array.isArray(analysis.confirmedFacts) ? analysis.confirmedFacts : [],
    watchItems: Array.isArray(analysis.watchItems) ? analysis.watchItems : [],
    sourceSections: Array.isArray(analysis.sourceSections) ? analysis.sourceSections : [],
    numbers: Array.isArray(analysis.materialNumbers) ? analysis.materialNumbers : [],
    reasonCodes: Array.isArray(audit.reasonCodes) ? audit.reasonCodes : [],
    documentState: String(item.officialDocumentState || payload.officialDocumentState || "metadata-only"),
    documentVerified: Boolean(item.documentVerified || payload.documentVerified),
    analysisReady: Boolean(item.analysisReady || payload.analysisReady),
    documentCharCount: Number(item.documentCharCount || payload.documentCharCount || quality.documentCharCount || 0),
    sourceDocuments: item.sourceDocuments && typeof item.sourceDocuments === "object" ? item.sourceDocuments : {},
    sourceRevision: String(item.sourceRevision || payload.sourceRevision || payload.receiptNo || payload.accessionNumber || ""),
    sourceAsOf: String(item.sourceAsOf || payload.sourceAsOf || item.publishedAt || item.observedAt || ""),
    metadataDatasetId: String(item.externalFactDatasetId || payload.externalFactDatasetId || ""),
    metadataFactRevision: String(item.externalFactSourceRevision || payload.externalFactSourceRevision || ""),
    documentDatasetId: String(item.officialDocumentDatasetId || payload.officialDocumentDatasetId || ""),
    documentFactRevision: String(item.officialDocumentFactRevision || payload.officialDocumentFactRevision || ""),
    documentFetchedAt: String(item.officialDocumentFetchedAt || payload.officialDocumentFetchedAt || ""),
    documentHash: String(item.documentHash || payload.documentHash || "")
  };
}

function renderResearchDisclosureSections(item) {
  var detail = researchDisclosureDetail(item);
  var analysis = detail.analysis;
  var facts = detail.confirmedFacts.length
    ? '<ul>' + detail.confirmedFacts.map(function (value) { return '<li>' + escapeHtml(value) + '</li>'; }).join("") + '</ul>'
    : '<p>공식 원문에서 확인된 구조화 사실이 아직 없습니다.</p>';
  var watches = detail.watchItems.length
    ? '<ul>' + detail.watchItems.map(function (value) { return '<li>' + escapeHtml(value) + '</li>'; }).join("") + '</ul>'
    : '<p>후속 확인 항목을 준비 중입니다.</p>';
  var sections = detail.sourceSections.length ? '<details class="oa-reasoning-technical"><summary>원문 근거 위치</summary><ol>' + detail.sourceSections.map(function (entry) {
    return '<li><p>' + escapeHtml((entry || {}).text || entry) + '</p><code>' + escapeHtml(String((entry || {}).start)) + ' - ' + escapeHtml(String((entry || {}).end)) + '</code></li>';
  }).join("") + '</ol></details>' : '';
  var auditText = [
    '웹 ' + (detail.audit.displayEligible ? '표시' : '차단'),
    '알림 ' + (detail.audit.alertEligible ? '가능' : '차단'),
    '판단 ' + (detail.audit.reasoningEligible ? '가능' : '차단'),
    '프롬프트 ' + (detail.audit.promptEligible ? '포함 가능' : '제외')
  ].join(' · ');
  return [
    '<section class="work-detail-section primary"><strong>확인된 사실</strong>' + facts + '</section>',
    '<section class="work-detail-section"><strong>투자 영향 경로</strong><p>' + escapeHtml(analysis.impactSummary || '공식 원문 검증 후 영향 경로를 판단합니다.') + '</p></section>',
    '<section class="work-detail-section"><strong>불확실성과 다음 확인</strong><p>' + escapeHtml(analysis.uncertaintySummary || '원문 검증 상태를 확인해야 합니다.') + '</p>' + watches + '</section>',
    '<section class="work-detail-section"><strong>수집·사용 감사</strong><p>' + escapeHtml(auditText) + '</p><small>' + escapeHtml(detail.reasonCodes.join(' · ') || '제외 사유 없음') + '</small><div class="notification-detail-tags"><span>목록 ' + escapeHtml(detail.metadataDatasetId || '-') + '</span><span>원문 ' + escapeHtml(detail.documentDatasetId || '수집 대기') + '</span><span>문서 ' + escapeHtml(detail.documentState) + '</span><span>본문 ' + escapeHtml(detail.documentCharCount.toLocaleString('ko-KR')) + '자</span><span>revision ' + escapeHtml(detail.sourceRevision || '-') + '</span><span>기준 ' + escapeHtml(formatFeedTime(detail.sourceAsOf) || '-') + '</span></div>' + (detail.documentDatasetId ? '<details class="oa-reasoning-technical"><summary>원문 수집 계보</summary><div class="notification-detail-tags"><span>목록 revision ' + escapeHtml(detail.metadataFactRevision || '-') + '</span><span>원문 revision ' + escapeHtml(detail.documentFactRevision || '-') + '</span><span>원문 수집 ' + escapeHtml(formatFeedTime(detail.documentFetchedAt) || '-') + '</span><span>hash ' + escapeHtml(detail.documentHash ? detail.documentHash.slice(0, 16) : '-') + '</span></div></details>' : '') + '</section>',
    sections
  ].join('');
}

function researchEvidenceImpactMeta(item) {
  item = item || {};
  var payload = item.payload && typeof item.payload === "object" ? item.payload : {};
  var analysisStatus = String(item.analysisStatus || payload.analysisStatus || "").trim().toLowerCase();
  var articleReadStatus = String(item.articleReadStatus || payload.articleReadStatus || "").trim().toLowerCase();
  var pendingAnalysis = ["deferred", "pending", "missing", "error", "unavailable"].indexOf(analysisStatus) >= 0;
  var fallbackAnalysis = analysisStatus === "fallback";
  var feedSummaryOnly = ["feed-summary", "summary-only", "preview"].indexOf(articleReadStatus) >= 0;
  if (pendingAnalysis || fallbackAnalysis || feedSummaryOnly) {
    var stateLabel = pendingAnalysis
      ? "분석 대기"
      : (fallbackAnalysis ? "임시 분류" : "본문 확인 전");
    var stateSummary = pendingAnalysis
      ? "기사 본문 분석이 아직 완료되지 않았습니다. 영향 방향은 검증 전까지 판단하지 않습니다."
      : (fallbackAnalysis
        ? "임시 분류 결과입니다. 본문 분석이 완료되기 전까지 투자 판단 근거로 사용하지 않습니다."
        : "피드 요약만 확보됐습니다. 기사 본문을 확인한 뒤에만 주가 영향 방향을 판단합니다.");
    return {
      tone: "hold",
      label: stateLabel,
      analysisStatus: analysisStatus || (feedSummaryOnly ? "feed-summary" : "pending"),
      judgementEligible: false,
      relevanceState: researchEvidenceState(item, "relevanceState", "context"),
      relevanceLabel: newsStateSettingLabel("relevance", researchEvidenceState(item, "relevanceState", "context")),
      materialityState: researchEvidenceState(item, "materialityState", "notable"),
      materialityLabel: newsStateSettingLabel("materiality", researchEvidenceState(item, "materialityState", "notable")),
      sourceTrustState: researchEvidenceState(item, "sourceTrustState", "standard"),
      sourceTrustLabel: newsStateSettingLabel("trust", researchEvidenceState(item, "sourceTrustState", "standard")),
      validationState: researchEvidenceState(item, "validationState", "conditional"),
      summary: stateSummary
    };
  }
  var explicitPolarity = String(item.stockImpactPolarity || payload.stockImpactPolarity || "").toLowerCase();
  var hasExplicitImpact = ["risk", "support", "context", "mixed", "neutral"].indexOf(explicitPolarity) >= 0;
  var polarity = String(explicitPolarity || item.polarity || item.sentiment || item.direction || "").toLowerCase();
  var corpus = researchEvidenceTextCorpus(item || {});
  var positive = /호재|개선|상향|수주|계약|성장|흑자|회복|증가|강세|기대|beat|upgrade|growth|record|demand/.test(corpus);
  var negative = /악재|부진|하향|소송|규제|손실|적자|감소|약세|리콜|제재|miss|downgrade|lawsuit|weak|recall/.test(corpus);
  var tone = "hold";
  if (["risk", "negative", "bearish", "downside"].indexOf(polarity) >= 0 || (!hasExplicitImpact && negative)) {
    tone = "danger";
  } else if (["support", "positive", "bullish", "upside"].indexOf(polarity) >= 0 || (!hasExplicitImpact && positive)) {
    tone = "watch";
  }
  var validationState = researchEvidenceState(item, "validationState", "conditional");
  var label = item.stockImpactLabel || payload.stockImpactLabel || (tone === "watch" ? "호재" : (tone === "danger" ? "악재" : (hasExplicitImpact ? "중립" : "분석 대기")));
  var relevanceState = researchEvidenceState(item, "relevanceState", "context");
  var materialityState = researchEvidenceState(item, "materialityState", "notable");
  var sourceTrustState = researchEvidenceState(item, "sourceTrustState", "standard");
  var summary = item.stockImpactReasonKo || payload.stockImpactReasonKo || (tone === "watch"
    ? "주가에는 긍정적인 기사입니다. 실적, 업황, 수요, 계약, 정책 기대가 실제 가격과 거래량에 이어지는지 확인합니다."
    : (tone === "danger"
      ? "주가에는 부정적인 기사입니다. 실적 둔화, 비용, 규제, 수요 약화 같은 리스크 점검 요인으로 볼 수 있습니다."
      : (hasExplicitImpact
        ? "주가 영향은 아직 중립입니다. 단독 기사만으로 방향을 정하기보다 시세와 수급 변화가 같이 올라오는지 확인해야 합니다."
        : "기사 분석이 아직 완료되지 않았습니다. 영향 방향은 상세 근거가 확인되기 전까지 판단하지 않습니다.")));
  return {
    tone: tone,
    label: label,
    analysisStatus: analysisStatus || "ok",
    judgementEligible: true,
    relevanceState: relevanceState,
    relevanceLabel: newsStateSettingLabel("relevance", relevanceState),
    materialityState: materialityState,
    materialityLabel: newsStateSettingLabel("materiality", materialityState),
    sourceTrustState: sourceTrustState,
    sourceTrustLabel: newsStateSettingLabel("trust", sourceTrustState),
    validationState: validationState,
    summary: summary
  };
}

function categoricalStateOrder(value, states) {
  var index = states.indexOf(String(value || "").toLowerCase());
  return index < 0 ? 0 : index + 1;
}

export { categoricalStateOrder, feedFreshness, feedQualitySignals, renderFeedInlineDetail, renderFeedQualityPanel, renderResearchDisclosureSections, renderResearchEvidenceQuality, researchClaimVerificationMeta, researchDisclosureDetail, researchEvidenceImpactMeta, researchEvidenceKindLabel, researchEvidenceKoreanSummary, researchEvidencePolarityLabel, researchEvidenceSummaryQualityMeta, researchEvidenceTextCorpus, researchEvidenceTranslationMeta, researchPromptAdmissionMeta };
