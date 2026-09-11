import { configuredChip } from "../accounts/directory.mjs";
import { allAccountWatchlistSymbols } from "../accounts/watchlist.mjs";
import { newsProviderLabel, newsStateSettingLabel, settingEnabled } from "../decisions/signals.mjs";
import { renderStrategyOverviewActionCard } from "../decisions/strategy.mjs";
import { clientKnownStockInfo, inferKnownStockSymbolFromText, stockDisplayName, watchlistSymbols } from "../instruments/catalog.mjs";
import { marketLabel } from "../instruments/universe-view.mjs";
import { editorWorkDetailPayload, renderInfoIconButton, renderWorkDetailButton } from "../navigation/detail.mjs";
import { activePageMode, activeSectionForPageMode, modeSectionsForPage, normalizeFeedSection } from "../navigation/routes.mjs";
import { feedFreshness, renderFeedInlineDetail, renderFeedQualityPanel, researchEvidenceImpactMeta, researchEvidenceKindLabel, researchEvidenceTextCorpus } from "../research/quality.mjs";
import { currentResearchEvidence, feedTimeValue, formatFeedTime } from "../research/requests.mjs";
import { feedImpactCounts, renderFeedImpactInboxPanel, renderResearchEvidencePanel } from "../research/workspace.mjs";
import { defaultSettings } from "../settings/defaults.mjs";
import { isConfiguredSetting, renderSettingsApiCard, settingValue, settingsSaveButtonClass, settingsSaveButtonLabel, settingsSaveDisabledAttr, settingsStatusLabel, settingsStatusTone } from "../settings/fields.mjs";
import { renderSettingsSmartSavePanel } from "../settings/legacy.mjs";
import { recordChangedAt, renderRecordChangedAt, sourceLabel } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { feedSections } from "../shell/catalog.mjs";
import { configuredCount } from "../shell/commands.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { renderManagedPage } from "../shell/pages.mjs";
import { marketState } from "../state/market.mjs";
import { researchState } from "../state/research.mjs";
import { shellState } from "../state/shell.mjs";

function renderFeedPage(snapshot) {
  var section = activeSectionForPageMode("feed", feedSections, normalizeFeedSection(marketState.activeFeedSection));
  marketState.activeFeedSection = section;
  var settingsMode = activePageMode("feed") === "settings";
  var body = [
    '<section class="admin-grid feed-view feed-view-' + escapeHtml(settingsMode ? "settings" : "console") + '" data-single-tab-console="feed">',
    renderFeedSectionBar(),
    settingsMode ? renderFeedSettingsPanel() : renderFeedUnifiedConsole(snapshot),
    '</section>'
  ].join("");
  return renderManagedPage("feed", snapshot, [
    body
  ].join(""));
}

function renderFeedSectionBar() {
  var visibleSections = modeSectionsForPage("feed", feedSections);
  var activeId = activeSectionForPageMode("feed", feedSections, marketState.activeFeedSection);
  if (activePageMode("feed") !== "settings") {
    return [
      '<div class="feed-section-bar feed-drilldown-bar" data-section-mode="results" data-feed-active-section="' + escapeHtml(activeId) + '">',
      '<div class="feed-section-tabs feed-drilldown-rail" role="toolbar" aria-label="피드 상세 보기">',
      renderWorkDetailButton("feed-impact-board", "", "영향 뉴스", "text-button compact"),
      renderWorkDetailButton("feed-theme-board", "", "테마", "text-button compact"),
      renderWorkDetailButton("feed-portfolio-board", "", "내 종목", "text-button compact"),
      renderWorkDetailButton("feed-source-board", "", "소스·품질", "text-button compact"),
      renderInfoIconButton("feed", "뉴스·근거 탭의 단일 화면 운영 방식"),
      '</div>',
      '<div class="feed-section-actions">',
      '<button class="text-button" data-action="refresh-research-evidence">' + (researchState.researchEvidenceLoading ? "조회 중" : "근거 새로고침") + '</button>',
      '<button class="text-button" data-page-mode-page="feed" data-page-mode="settings">수집 설정</button>',
      '</div>',
      '</div>'
    ].join("");
  }
  return [
    '<div class="feed-section-bar" data-section-mode="' + escapeHtml(activePageMode("feed")) + '" data-feed-active-section="' + escapeHtml(activeId) + '">',
    '<div class="feed-section-tabs" role="tablist" aria-label="피드 섹션">',
    visibleSections.map(function (item) {
      var active = activeId === item.id;
      return [
        '<button type="button" role="tab" class="' + (active ? "active" : "") + '" data-feed-section="' + escapeHtml(item.id) + '"' + (active ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
        '<strong>' + escapeHtml(item.label) + '</strong>',
        '<span>' + escapeHtml(item.description) + '</span>',
        '</button>'
      ].join("");
    }).join(""),
    '</div>',
    '<div class="feed-section-actions">',
    '<button class="text-button" data-action="refresh-research-evidence">' + (researchState.researchEvidenceLoading ? "조회 중" : "근거 새로고침") + '</button>',
    activePageMode("feed") === "settings"
      ? '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>'
      : '<button class="text-button" data-page-mode-page="feed" data-page-mode="settings">수집 설정</button>',
    '</div>',
    '</div>'
  ].join("");
}

function renderFeedUnifiedConsole(snapshot) {
  return [
    '<div class="feed-workbench feed-overview-workbench feed-unified-console">',
    '<div class="feed-primary-column">',
    renderFeedImpactInboxPanel(snapshot, { compact: true, limit: 6 }),
    renderFeedMarketBriefPanel(snapshot),
    '</div>',
    '<aside class="feed-side-column">',
    renderFeedPortfolioNewsPanel(snapshot, { compact: true }),
    renderFeedThemeClusterPanel(snapshot, { limit: 4 }),
    renderFeedSourceLedgerPanel({ compact: true }),
    renderFeedQualityPanel(),
    '</aside>',
    '</div>'
  ].join("");
}

function feedImpactBoardWorkDetailPayload() {
  return editorWorkDetailPayload(
    "News Impact",
    "영향 뉴스 전체 보기",
    "기본 화면은 핵심 뉴스만, 상세 화면은 기사 요약·주가 영향·근거 원장을 함께 봅니다.",
    '<div class="feed-impact-workspace-wide">' + renderFeedImpactInboxPanel(shellState.snapshot || {}, { limit: 16 }) + renderResearchEvidencePanel() + '</div>'
  );
}

function feedThemeBoardWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Market Themes",
    "테마와 시장 흐름",
    "섹터·자산 흐름은 투자 판단의 배경 데이터로만 보고, 세부 항목은 여기서 확인합니다.",
    '<div class="feed-theme-workspace">' + renderFeedMarketBriefPanel(shellState.snapshot || {}) + renderFeedThemeClusterPanel(shellState.snapshot || {}, { full: true }) + '</div>'
  );
}

function feedPortfolioBoardWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Portfolio Evidence",
    "내 종목 관련 근거",
    "보유·관심 종목에 직접 연결되는 뉴스만 분리해서 확인합니다.",
    '<div class="feed-portfolio-workspace">' + renderFeedPortfolioNewsPanel(shellState.snapshot || {}, { full: true }) + renderFeedImpactInboxPanel(shellState.snapshot || {}, { compact: true, portfolioOnly: true, limit: 12 }) + '</div>'
  );
}

function feedSourceBoardWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Source Quality",
    "소스와 수집 품질",
    "수집 채널, 파이프라인, 품질 신호는 운영 점검이 필요할 때만 엽니다.",
    '<div class="feed-source-workspace">' + renderFeedSourceLedgerPanel({ full: true }) + renderFeedChannelPanel() + renderFeedPipelinePanel() + renderFeedQualityPanel() + '</div>'
  );
}

function renderFeedSectionContent(snapshot, section) {
  var active = normalizeFeedSection(section);
  if (active === "settings") {
    return renderFeedSettingsPanel();
  }
  if (active === "impact") {
    return [
      '<div class="feed-impact-workspace-wide">',
      renderFeedImpactInboxPanel(snapshot, { limit: 12 }),
      renderResearchEvidencePanel(),
      '</div>'
    ].join("");
  }
  if (active === "themes") {
    return [
      '<div class="feed-theme-workspace">',
      renderFeedMarketBriefPanel(snapshot),
      renderFeedThemeClusterPanel(snapshot, { full: true }),
      '</div>'
    ].join("");
  }
  if (active === "portfolio") {
    return [
      '<div class="feed-portfolio-workspace">',
      renderFeedPortfolioNewsPanel(snapshot, { full: true }),
      renderFeedImpactInboxPanel(snapshot, { compact: true, portfolioOnly: true, limit: 8 }),
      '</div>'
    ].join("");
  }
  if (active === "sources") {
    return [
      '<div class="feed-source-workspace">',
      renderFeedSourceLedgerPanel({ full: true }),
      renderFeedChannelPanel(),
      renderFeedPipelinePanel(),
      renderFeedQualityPanel(),
      '</div>'
    ].join("");
  }
  return [
    '<div class="feed-workbench feed-overview-workbench">',
    '<div class="feed-primary-column">',
    renderFeedMarketBriefPanel(snapshot),
    renderFeedThemeClusterPanel(snapshot, { limit: 4 }),
    renderFeedImpactInboxPanel(snapshot, { compact: true, limit: 5 }),
    '</div>',
    '<aside class="feed-side-column">',
    renderFeedPortfolioNewsPanel(snapshot, { compact: true }),
    renderFeedSourceLedgerPanel({ compact: true }),
    renderFeedQualityPanel(),
    '</aside>',
    '</div>'
  ].join("");
}

function feedSourceTone(enabled, ready) {
  if (!enabled) return "hold";
  return ready === false ? "caution" : "watch";
}

function feedSourceChannels() {
  var newsArchiveEnabled = settingEnabled("newsCollectionEnabled");
  var internationalProviders = String(settingValue("newsCollectionInternationalProviders") || settingValue("newsCollectionProviders") || defaultSettings.newsCollectionInternationalProviders || defaultSettings.newsCollectionProviders || "google_rss_us,yahoo_search,yahoo_finance,gdelt").split(",").map(function (value) {
    return String(value || "").trim().toLowerCase();
  });
  var koreanProviders = String(settingValue("newsCollectionKoreanProviders") || defaultSettings.newsCollectionKoreanProviders || "google_rss_kr,yahoo_search,yahoo_finance,gdelt").split(",").map(function (value) {
    return String(value || "").trim().toLowerCase();
  });
  var selectedNewsProviders = internationalProviders.concat(koreanProviders);
  function usesNewsProvider(names) {
    return names.some(function (name) { return selectedNewsProviders.indexOf(name) >= 0; });
  }
  return [
    {
      label: "KIS 장중 수급",
      enabled: settingEnabled("kisMarketSignalsEnabled"),
      ready: configuredCount(["kisAppKey", "kisAppSecret"]) >= 2,
      route: "시장 신호 -> 가격·수급 근거",
      cadence: (settingValue("kisMarketSignalCacheMinutes") || defaultSettings.kisMarketSignalCacheMinutes || "3") + "분 캐시"
    },
    {
      label: "뉴스 헤드라인",
      enabled: settingEnabled("externalNewsEnabled"),
      ready: true,
      route: "외부 뉴스 -> 최신 근거",
      cadence: newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider)
    },
    {
      label: "뉴스 아카이브",
      enabled: newsArchiveEnabled,
      ready: true,
      route: "관심·보유 종목 -> Evidence DB",
      cadence: (settingValue("newsCollectionIntervalSeconds") || defaultSettings.newsCollectionIntervalSeconds || "60") + "초 주기"
    },
    {
      label: "Google News KR",
      enabled: newsArchiveEnabled && settingEnabled("newsCollectionGoogleKrEnabled") && usesNewsProvider(["google_rss_kr", "google_news_kr", "rss_kr", "kr"]),
      ready: true,
      route: "국내 종목 RSS -> 원문 확인 -> Evidence DB",
      cadence: "무키 RSS"
    },
    {
      label: "Google News US",
      enabled: newsArchiveEnabled && settingEnabled("newsCollectionGoogleUsEnabled") && usesNewsProvider(["google_rss_us", "google_news_us", "rss_us", "us"]),
      ready: true,
      route: "해외 종목 RSS -> 원문 확인 -> Evidence DB",
      cadence: "무키 RSS"
    },
    {
      label: "Yahoo Finance Search",
      enabled: newsArchiveEnabled && settingEnabled("newsCollectionYahooSearchEnabled") && usesNewsProvider(["yahoo_search", "yahoo_finance_search"]),
      ready: true,
      route: "종목 검색 -> 직접 기사 본문 -> Evidence DB",
      cadence: "무키 검색"
    },
    {
      label: "Yahoo Finance RSS",
      enabled: newsArchiveEnabled && settingEnabled("newsCollectionYahooRssEnabled") && usesNewsProvider(["yahoo_finance", "yahoo_finance_rss", "yahoo_rss"]),
      ready: true,
      route: "종목 RSS -> 원문 확인 -> Evidence DB",
      cadence: "무키 RSS"
    },
    {
      label: "GDELT News",
      enabled: newsArchiveEnabled && settingEnabled("newsCollectionGdeltSyncEnabled") && usesNewsProvider(["gdelt"]),
      ready: true,
      route: "글로벌 기사 검색 -> 교차 근거 -> Evidence DB",
      cadence: "무키 API"
    },
    {
      label: "뉴스 AI 분석",
      enabled: newsArchiveEnabled && settingEnabled("newsAiAnalysisAsyncEnabled"),
      ready: settingEnabled("newsAiAnalysisEnabled"),
      route: "저장 기사 -> 한글 요약·번역·품질 점검",
      cadence: (settingValue("newsAiAnalysisWorkerIntervalSeconds") || defaultSettings.newsAiAnalysisWorkerIntervalSeconds || "60") + "초 주기"
    },
    {
      label: "OpenDART 공시",
      enabled: settingEnabled("externalDartEnabled"),
      ready: isConfiguredSetting("opendartApiKey"),
      route: "국내 공시 -> 이벤트 근거",
      cadence: (settingValue("externalDartLookbackDays") || defaultSettings.externalDartLookbackDays || "14") + "일 조회"
    },
    {
      label: "SEC EDGAR",
      enabled: settingEnabled("externalSecEnabled"),
      ready: true,
      route: "미국 공시 -> 보조 근거",
      cadence: (settingValue("externalSecMaxSymbols") || defaultSettings.externalSecMaxSymbols || "3") + "종목"
    },
    {
      label: "FRED 거시",
      enabled: settingEnabled("externalFredEnabled"),
      ready: isConfiguredSetting("fredApiKey"),
      route: "금리·유동성 -> 리스크 맥락",
      cadence: (settingValue("externalFredSeries") || defaultSettings.externalFredSeries || "DGS10,DGS2").split(",").filter(Boolean).length + "개 지표"
    },
    {
      label: "CoinGecko 크립토",
      enabled: settingEnabled("externalCoinGeckoEnabled"),
      ready: true,
      route: "크립토 변동 -> 외부 위험 신호",
      cadence: (settingValue("externalCryptoIds") || defaultSettings.externalCryptoIds || "bitcoin,ethereum").split(",").filter(Boolean).length + "개 자산"
    },
    {
      label: "Alpha Vantage",
      enabled: settingEnabled("externalAlphaEnabled"),
      ready: isConfiguredSetting("alphaVantageApiKey"),
      route: "미장 가격 -> 해외 보조 신호",
      cadence: (settingValue("externalApiFetchIntervalMinutes") || defaultSettings.externalApiFetchIntervalMinutes || "30") + "분 캐시"
    },
    {
      label: "그래프 추론",
      enabled: settingEnabled("ontologyReasoningEnabled"),
      ready: settingEnabled("ontologyTypeDbEnabled"),
      route: "근거 -> 관계 추론 -> 알림 후보",
      cadence: (settingValue("ontologyReasoningIntervalSeconds") || defaultSettings.ontologyReasoningIntervalSeconds || "10") + "초 확인"
    }
  ].map(function (channel) {
    channel.tone = feedSourceTone(channel.enabled, channel.ready);
    return channel;
  });
}

function feedPipelineStages() {
  var evidence = currentResearchEvidence();
  var summary = evidence.summary || {};
  var articleAnalysis = evidence.articleAnalysis || {};
  var latest = feedFreshness(summary.latestSeenAt);
  var channels = feedSourceChannels();
  var activeChannels = channels.filter(function (channel) { return channel.enabled; }).length;
  var readyChannels = channels.filter(function (channel) { return channel.enabled && channel.ready !== false; }).length;
  return [
    { step: "01", title: "원천 수집", tone: activeChannels ? "watch" : "hold", value: activeChannels + "/" + channels.length, detail: "사용 중인 수집 채널" },
    { step: "02", title: "준비도 확인", tone: readyChannels === activeChannels ? "watch" : "caution", value: readyChannels + "/" + Math.max(activeChannels, 1), detail: "키·연결·무키 채널 확인" },
    { step: "03", title: "본문·요약", tone: Number(articleAnalysis.summaryBlockedCount || 0) ? "danger" : (Number(articleAnalysis.summaryNeedsReviewCount || 0) || Number(articleAnalysis.translationPendingCount || 0) ? "caution" : "watch"), value: Number(articleAnalysis.summaryReadyCount || 0) + "건", detail: "번역 완료 " + Number(articleAnalysis.translationCompleteCount || 0) + "건 · 대기 " + Number(articleAnalysis.translationPendingCount || 0) + "건" },
    { step: "04", title: "근거 저장", tone: Number(summary.total || 0) ? latest.tone : "caution", value: Number(summary.total || 0) + "건", detail: "최근 저장 " + latest.label },
    { step: "05", title: "관계 추론", tone: settingEnabled("ontologyReasoningEnabled") ? "watch" : "hold", value: "TypeDB", detail: "배치 " + (settingValue("ontologyReasoningBatchSize") || defaultSettings.ontologyReasoningBatchSize || "20") },
    { step: "06", title: "알림 후보", tone: settingEnabled("materialityGateEnabled") ? "watch" : "hold", value: settingEnabled("materialityGateEnabled") ? "조건 기반" : "꺼짐", detail: "실제 변화와 상태 전이 확인" }
  ];
}

function feedEvidencePayload(item) {
  return item && item.payload && typeof item.payload === "object" ? item.payload : {};
}

function feedResearchEvidenceItems(options) {
  options = options || {};
  var evidence = currentResearchEvidence();
  var fromCache = Boolean(evidence.fromCache || evidence.cached);
  var items = Array.isArray(evidence.items) ? evidence.items.map(function (item) {
    return fromCache ? Object.assign({}, item || {}, { fromCache: true }) : item;
  }) : [];
  if (options.portfolioOnly) {
    var symbolSet = feedPortfolioSymbolSet(options.snapshot || shellState.snapshot || {});
    items = items.filter(function (item) {
      return feedEvidenceSymbols(item).some(function (symbol) {
        return Boolean(symbolSet[symbol]);
      });
    });
  }
  if (options.limit) {
    return items.slice(0, Math.max(1, Number(options.limit) || items.length));
  }
  return items;
}

function feedEvidenceDataMeta(item) {
  item = item || {};
  var payload = feedEvidencePayload(item);
  var source = String(item.publisher || item.source || item.provider || payload.sourcePublisher || payload.source || payload.provider || payload.articleProvider || "").trim();
  var sourceLabel = source || researchEvidenceKindLabel(item.kind || payload.kind || "근거");
  var quality = String(item.dataQuality || item.quality || payload.dataQuality || payload.sourceQuality || payload.articleAnalysisSource || payload.articleReadStatus || "").toLowerCase();
  var id = String(item.evidenceId || item.id || "").toLowerCase();
  var mock = Boolean(item.mock || item.isMock || payload.mock || payload.isMock || id.indexOf("preview:") === 0 || quality.indexOf("mock") >= 0);
  var cached = Boolean(item.fromCache || item.cached || payload.fromCache || payload.cached || item.cachedAt || payload.cachedAt || quality.indexOf("cache") >= 0 || quality.indexOf("cached") >= 0);
  var actual = !mock && Boolean(item.evidenceId || item.url || item.publishedAt || item.observedAt || payload.url || payload.publishedAt);
  var dataLabel = mock ? "mock 데이터" : (cached ? "저장/캐시 데이터" : (actual ? "실제 데이터" : "출처 미상"));
  return {
    source: sourceLabel,
    publisherId: String(item.publisherId || ""),
    publisherTier: String(item.publisherTier || ""),
    publisherType: String(item.publisherType || ""),
    republisher: String(item.republisher || ""),
    distributionChannel: String(item.distributionChannel || ""),
    contentType: String(item.contentType || ""),
    contentTypeLabel: researchEvidenceContentTypeLabel(item.contentType),
    syndicationState: String(item.syndicationState || ""),
    relationship: String(item.evidenceRelationship || ""),
    relationshipLabel: researchEvidenceRelationshipLabel(item.evidenceRelationship),
    provenanceComplete: Boolean(item.provenanceComplete),
    dataLabel: dataLabel,
    tone: mock ? "caution" : (actual ? "watch" : "hold"),
    key: sourceLabel + "|" + dataLabel
  };
}

function researchEvidenceContentTypeLabel(value) {
  return {
    "official-filing": "공식 공시",
    "official-release": "공식 발표",
    "press-release": "보도자료",
    reporting: "취재 기사",
    analysis: "분석 기사",
    opinion: "의견·칼럼",
    aggregation: "기사 집계",
    automated: "자동 생성"
  }[String(value || "").toLowerCase()] || "유형 확인 중";
}

function researchEvidenceRelationshipLabel(value) {
  return {
    original: "원문",
    "exact-duplicate": "동일 원문",
    "syndicated-copy": "전재 기사",
    "same-story": "동일 사건",
    "independent-confirmation": "독립 확인",
    "follow-up": "후속 기사"
  }[String(value || "").toLowerCase()] || "관계 확인 중";
}

function feedEvidenceSymbols(item) {
  item = item || {};
  var payload = feedEvidencePayload(item);
  var symbols = [];
  function add(value) {
    String(value || "").split(/[,\s]+/).forEach(function (symbol) {
      var normalized = String(symbol || "").trim().toUpperCase();
      if (normalized && symbols.indexOf(normalized) < 0) symbols.push(normalized);
    });
  }
  add(item.symbol || payload.symbol);
  [item.symbols, item.relatedSymbols, payload.symbols, payload.relatedSymbols, payload.tickers].forEach(function (list) {
    if (Array.isArray(list)) list.forEach(add);
    else add(list);
  });
  add(inferKnownStockSymbolFromText(researchEvidenceTextCorpus(item)));
  return symbols;
}

function feedPortfolioInstrumentItems(snapshot) {
  var toss = snapshot && snapshot.toss ? snapshot.toss : {};
  var rows = [];
  var seen = {};
  function add(item, source) {
    item = item || {};
    var symbol = String(item.symbol || "").trim().toUpperCase();
    if (!symbol || symbol === "CASH") return;
    var existing = seen[symbol];
    var merged = Object.assign(clientKnownStockInfo(symbol), item, { symbol: symbol, source: source || item.source || "watchlist" });
    if (existing && existing.source === "holding") return;
    if (existing && merged.source !== "holding") return;
    if (!existing) rows.push(merged);
    seen[symbol] = merged;
    if (existing && merged.source === "holding") {
      rows = rows.map(function (row) { return String(row.symbol || "").toUpperCase() === symbol ? merged : row; });
    }
  }
  (Array.isArray(toss.positions) ? toss.positions : []).forEach(function (item) {
    if (item && item.source !== "cash" && item.sector !== "현금") add(item, "holding");
  });
  (Array.isArray(toss.watchlist) ? toss.watchlist : []).forEach(function (item) {
    add(item, "watchlist");
  });
  watchlistSymbols().concat(allAccountWatchlistSymbols()).forEach(function (symbol) {
    add(clientKnownStockInfo(symbol), "watchlist");
  });
  return rows;
}

function feedPortfolioSymbolSet(snapshot) {
  var set = {};
  feedPortfolioInstrumentItems(snapshot || {}).forEach(function (item) {
    var symbol = String(item.symbol || "").toUpperCase();
    if (symbol) set[symbol] = true;
  });
  return set;
}

function feedThemeLabelsForItem(item) {
  item = item || {};
  var payload = feedEvidencePayload(item);
  var labels = [];
  function add(label) {
    var value = String(label || "").trim();
    if (value && labels.indexOf(value) < 0) labels.push(value);
  }
  [item.theme, item.sector, item.asset, payload.theme, payload.sector, payload.asset].forEach(add);
  [item.themes, item.sectors, item.assets, payload.themes, payload.sectors, payload.assets].forEach(function (list) {
    if (Array.isArray(list)) list.forEach(add);
    else String(list || "").split(/[,\|]+/).forEach(add);
  });
  feedEvidenceSymbols(item).forEach(function (symbol) {
    add(clientKnownStockInfo(symbol).sector);
  });
  var corpus = researchEvidenceTextCorpus(item);
  [
    { label: "AI·플랫폼", pattern: /ai|인공지능|openai|llm|platform|software|cloud|데이터센터|클라우드|플랫폼/ },
    { label: "반도체·HBM", pattern: /반도체|hbm|memory|chip|foundry|nvidia|tsmc|gpu|semiconductor/ },
    { label: "금리·유동성", pattern: /fed|fomc|rate|yield|inflation|cpi|ppi|금리|인플레|국채|유동성/ },
    { label: "환율·달러", pattern: /usd|dollar|fx|환율|달러|원화|엔화|원달러/ },
    { label: "크립토·디지털자산", pattern: /bitcoin|btc|ethereum|crypto|coin|비트코인|이더리움|코인|디지털자산/ },
    { label: "금·원자재", pattern: /gold|oil|copper|commodity|crude|금값|금 |원유|구리|원자재/ },
    { label: "한국시장 접근성", pattern: /kospi|kosdaq|krx|korea|한국|코스피|코스닥|미국 개미|retail investor|외국인/ }
  ].forEach(function (entry) {
    if (entry.pattern.test(corpus)) add(entry.label);
  });
  if (!labels.length) add("미분류 흐름");
  return labels.slice(0, 4);
}

function feedThemeClusters(snapshot, options) {
  options = options || {};
  var items = feedResearchEvidenceItems({ snapshot: snapshot });
  var portfolioSymbols = feedPortfolioSymbolSet(snapshot || {});
  var clusters = {};
  items.forEach(function (item, index) {
    var impact = researchEvidenceImpactMeta(item);
    var time = item.publishedAt || item.observedAt || "";
    var timeValue = feedTimeValue(time);
    var symbols = feedEvidenceSymbols(item);
    var linkedPortfolio = symbols.some(function (symbol) { return Boolean(portfolioSymbols[symbol]); });
    feedThemeLabelsForItem(item).forEach(function (label) {
      if (!clusters[label]) {
        clusters[label] = {
          label: label,
          items: [],
          sources: {},
          watch: 0,
          danger: 0,
          hold: 0,
          latestTime: 0,
          portfolioCount: 0
        };
      }
      var cluster = clusters[label];
      cluster.items.push({ item: item, index: index, impact: impact, symbols: symbols, linkedPortfolio: linkedPortfolio });
      cluster.sources[feedEvidenceDataMeta(item).source] = true;
      if (impact.tone === "watch") cluster.watch += 1;
      else if (impact.tone === "danger") cluster.danger += 1;
      else cluster.hold += 1;
      cluster.latestTime = Math.max(cluster.latestTime, timeValue || 0);
      if (linkedPortfolio) cluster.portfolioCount += 1;
    });
  });
  return Object.keys(clusters).map(function (key) {
    var cluster = clusters[key];
    cluster.count = cluster.items.length;
    cluster.sourceCount = Object.keys(cluster.sources).length;
    cluster.tone = cluster.danger > cluster.watch ? "danger" : (cluster.watch > 0 ? "watch" : "hold");
    cluster.latestLabel = feedFreshness(cluster.latestTime).label;
    return cluster;
  }).sort(function (a, b) {
    return (b.portfolioCount - a.portfolioCount) || (b.count - a.count) || (b.latestTime - a.latestTime);
  }).slice(0, options.limit || 12);
}

function feedSourceLedgerRows() {
  var grouped = {};
  feedResearchEvidenceItems().forEach(function (item) {
    var meta = feedEvidenceDataMeta(item);
    var kind = researchEvidenceKindLabel(item.kind);
    var key = meta.key + "|" + kind;
    if (!grouped[key]) {
      grouped[key] = {
        source: meta.source,
        dataLabel: meta.dataLabel,
        kind: kind,
        tone: meta.tone,
        count: 0,
        latestTime: 0
      };
    }
    grouped[key].count += 1;
    grouped[key].latestTime = Math.max(grouped[key].latestTime, feedTimeValue(item.publishedAt || item.observedAt || "") || 0);
    if (meta.tone === "caution") grouped[key].tone = "caution";
  });
  return Object.keys(grouped).map(function (key) {
    var row = grouped[key];
    row.latestLabel = feedFreshness(row.latestTime).label;
    return row;
  }).sort(function (a, b) {
    return (b.latestTime - a.latestTime) || (b.count - a.count);
  });
}

function renderFeedMarketBriefPanel(snapshot) {
  var evidence = currentResearchEvidence();
  var summary = evidence.summary || {};
  var items = feedResearchEvidenceItems();
  var impacts = feedImpactCounts();
  var clusters = feedThemeClusters(snapshot, { limit: 99 });
  var portfolioItems = feedPortfolioInstrumentItems(snapshot || {});
  var sources = feedSourceLedgerRows();
  var actualCount = sources.filter(function (row) { return row.dataLabel === "실제 데이터"; }).reduce(function (total, row) { return total + row.count; }, 0);
  var mockCount = sources.filter(function (row) { return row.dataLabel === "mock 데이터"; }).reduce(function (total, row) { return total + row.count; }, 0);
  return [
    '<article class="panel feed-market-brief-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Market Impact Console</p>',
    '<h2>오늘의 시장 요약</h2>',
    '<span>뉴스·공시 근거를 영향, 테마, 내 종목, 소스 품질 순서로 재배치합니다.</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(items.length ? "watch" : "caution") + '">' + escapeHtml(items.length ? "근거 " + items.length + "건" : "수집 대기") + '</span>',
    '</div>',
    '<div class="feed-brief-grid">',
    renderFeedCommandMetric("저장 근거", Number(summary.total || items.length || 0) + "건", "최근 " + feedFreshness(summary.latestSeenAt).label, Number(summary.total || items.length) ? feedFreshness(summary.latestSeenAt).tone : "caution"),
    renderFeedCommandMetric("영향 뉴스", "호재 " + impacts.watch + " · 악재 " + impacts.danger, "중립 " + impacts.hold + "건", impacts.danger ? "danger" : (impacts.watch ? "watch" : "hold")),
    renderFeedCommandMetric("테마 흐름", clusters.length + "개", clusters.slice(0, 2).map(function (cluster) { return cluster.label; }).join(" · ") || "대기", clusters.length ? "watch" : "hold"),
    renderFeedCommandMetric("내 종목 연결", portfolioItems.length + "종목", "보유·관심 기준", portfolioItems.length ? "watch" : "hold"),
    '</div>',
    '<div class="feed-source-strip">',
    '<span><strong>실제</strong>' + escapeHtml(actualCount) + '건</span>',
    '<span><strong>mock</strong>' + escapeHtml(mockCount) + '건</span>',
    '<span><strong>소스</strong>' + escapeHtml(sources.length || 0) + '개</span>',
    '</div>',
    '</article>'
  ].join("");
}

function renderFeedThemeClusterPanel(snapshot, options) {
  options = options || {};
  var clusters = feedThemeClusters(snapshot, { limit: options.full ? 16 : (options.limit || 6) });
  return [
    '<article class="panel feed-theme-cluster-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Theme Flow</p>',
    '<h2>테마별 자금 흐름 후보</h2>',
    '<span>섹터·자산·거시 키워드로 묶은 근거입니다. 방향 예측이 아니라 확인할 흐름의 묶음입니다.</span>',
    '</div>',
    '<span class="metric">' + escapeHtml(clusters.length) + '</span>',
    '</div>',
    clusters.length ? '<div class="feed-theme-grid">' + clusters.map(function (cluster) {
      return renderFeedThemeClusterCard(cluster, options.full);
    }).join("") + '</div>' : renderEmptyState({
      tone: "muted",
      label: "Theme",
      title: "아직 묶을 뉴스 흐름이 없습니다",
      description: "뉴스 아카이브가 쌓이면 AI, 반도체, 금리, 환율, 크립토, 한국시장 접근성 같은 축으로 자동 그룹화됩니다.",
      meta: ["테마", "소스", "내 종목"]
    }),
    '</article>'
  ].join("");
}

function renderFeedThemeClusterCard(cluster, full) {
  var rows = (cluster.items || []).slice(0, full ? 4 : 2).map(function (entry) {
    var item = entry.item || {};
    var symbol = feedEvidenceSymbols(item)[0] || "";
    var displayName = stockDisplayName(symbol, item.payload || item);
    return [
      '<div class="feed-theme-news-row">',
      '<span class="tone-chip ' + escapeHtml(entry.impact.tone || "hold") + '">' + escapeHtml(entry.impact.label || "중립") + '</span>',
      '<div>',
      '<strong>' + escapeHtml(item.title || "제목 없음") + '</strong>',
      '<em>' + escapeHtml([displayName || symbol || "관련 종목", feedEvidenceDataMeta(item).source, formatFeedTime(item.publishedAt || item.observedAt || "")].filter(Boolean).join(" · ")) + '</em>',
      '</div>',
      '</div>'
    ].join("");
  }).join("");
  return [
    '<section class="feed-theme-card ' + escapeHtml(cluster.tone || "hold") + '"' + cardTypeAttrs("theme-card", cluster.tone || "hold") + cardFormatAttrs("summary-list-card", full ? "expanded" : "compact") + '>',
    '<div class="feed-theme-card-head">',
    '<div>',
    '<strong>' + escapeHtml(cluster.label) + '</strong>',
    '<span>' + escapeHtml("근거 " + cluster.count + "건 · 소스 " + cluster.sourceCount + "개 · 최근 " + cluster.latestLabel) + '</span>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(cluster.tone || "hold") + '">' + escapeHtml(cluster.danger ? "리스크 포함" : (cluster.watch ? "강세 재료" : "관찰")) + '</span>',
    '</div>',
    '<div class="feed-theme-metrics">',
    '<span>호재 <strong>' + escapeHtml(cluster.watch) + '</strong></span>',
    '<span>악재 <strong>' + escapeHtml(cluster.danger) + '</strong></span>',
    '<span>내 종목 <strong>' + escapeHtml(cluster.portfolioCount) + '</strong></span>',
    '</div>',
    rows ? '<div class="feed-theme-news-list">' + rows + '</div>' : '',
    '</section>'
  ].join("");
}

function renderFeedPortfolioNewsPanel(snapshot, options) {
  options = options || {};
  var instruments = feedPortfolioInstrumentItems(snapshot || {});
  var allItems = feedResearchEvidenceItems({ snapshot: snapshot, portfolioOnly: true });
  var rows = instruments.map(function (instrument) {
    var symbol = String(instrument.symbol || "").toUpperCase();
    var related = allItems.filter(function (item) {
      return feedEvidenceSymbols(item).indexOf(symbol) >= 0;
    });
    return { instrument: instrument, related: related };
  }).filter(function (row) {
    return options.full || row.related.length;
  }).sort(function (a, b) {
    return b.related.length - a.related.length;
  }).slice(0, options.compact ? 5 : 24);
  var holdings = instruments.filter(function (item) { return item.source === "holding"; }).length;
  return [
    '<article class="panel feed-portfolio-news-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Portfolio Lens</p>',
    '<h2>내 종목 영향 뉴스</h2>',
    '<span>보유·관심 종목과 연결된 기사만 먼저 모아 봅니다.</span>',
    '</div>',
    '<span class="metric">' + escapeHtml(allItems.length) + '</span>',
    '</div>',
    '<div class="feed-portfolio-summary">',
    '<span>보유 <strong>' + escapeHtml(holdings) + '</strong></span>',
    '<span>관심 <strong>' + escapeHtml(Math.max(0, instruments.length - holdings)) + '</strong></span>',
    '<span>연결 근거 <strong>' + escapeHtml(allItems.length) + '</strong></span>',
    '</div>',
    rows.length ? '<div class="feed-portfolio-news-list">' + rows.map(renderFeedPortfolioNewsRow).join("") + '</div>' : renderEmptyState({
      tone: "muted",
      label: "Portfolio",
      title: "내 종목과 연결된 뉴스가 아직 없습니다",
      description: "보유·관심 종목 코드가 저장 근거에 들어오면 이곳에서 먼저 볼 수 있습니다.",
      meta: ["보유", "관심", "뉴스"]
    }),
    '</article>'
  ].join("");
}

function renderFeedPortfolioNewsRow(row) {
  var instrument = row.instrument || {};
  var symbol = String(instrument.symbol || "").toUpperCase();
  var related = row.related || [];
  var impacts = { watch: 0, danger: 0, hold: 0 };
  related.forEach(function (item) {
    var meta = researchEvidenceImpactMeta(item);
    if (meta.tone === "watch") impacts.watch += 1;
    else if (meta.tone === "danger") impacts.danger += 1;
    else impacts.hold += 1;
  });
  var tone = impacts.danger ? "danger" : (impacts.watch ? "watch" : "hold");
  var latest = related.reduce(function (time, item) {
    return Math.max(time, feedTimeValue(item.publishedAt || item.observedAt || "") || 0);
  }, 0);
  return [
    '<div class="feed-portfolio-news-row ' + escapeHtml(tone) + '"' + cardTypeAttrs("portfolio-news-row", tone) + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div>',
    '<strong>' + escapeHtml(stockDisplayName(symbol, instrument)) + '</strong>',
    '<span>' + escapeHtml([symbol, sourceLabel(instrument.source), marketLabel(instrument.market || instrument.exchange), instrument.sector || ""].filter(Boolean).join(" · ")) + '</span>',
    renderRecordChangedAt(latest || recordChangedAt(instrument)),
    '</div>',
    '<em>' + escapeHtml("호재 " + impacts.watch + " · 악재 " + impacts.danger + " · 중립 " + impacts.hold) + '</em>',
    '<b>' + escapeHtml(related.length ? ("최근 " + feedFreshness(latest).label) : "연결 뉴스 대기") + '</b>',
    '</div>'
  ].join("");
}

function renderFeedSourceLedgerPanel(options) {
  options = options || {};
  var rows = feedSourceLedgerRows();
  var visibleRows = rows.slice(0, options.compact ? 5 : 24);
  return [
    '<article class="panel feed-source-ledger-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Source Ledger</p>',
    '<h2>소스 신선도 원장</h2>',
    '<span>각 근거가 실제 데이터인지, 캐시인지, mock인지 출처별로 분리합니다.</span>',
    '</div>',
    '<span class="metric">' + escapeHtml(rows.length) + '</span>',
    '</div>',
    visibleRows.length ? '<div class="feed-source-ledger-list">' + visibleRows.map(function (row) {
      return [
        '<div class="feed-source-ledger-row ' + escapeHtml(row.tone || "hold") + '"' + cardTypeAttrs("source-ledger-row", row.tone || "hold") + cardFormatAttrs("summary-list-card", "compact") + '>',
        '<div>',
        '<strong>' + escapeHtml(row.source) + '</strong>',
        '<span>' + escapeHtml(row.kind + " · " + row.dataLabel) + '</span>',
        renderRecordChangedAt(row.latestTime),
        '</div>',
        '<em>' + escapeHtml(row.count + "건") + '</em>',
        '<b>' + escapeHtml(row.latestLabel) + '</b>',
        '</div>'
      ].join("");
    }).join("") + '</div>' : renderEmptyState({
      tone: "muted",
      label: "Source",
      title: "표시할 소스 원장이 없습니다",
      description: "저장 근거가 쌓이면 실제 데이터, 저장/캐시 데이터, mock 데이터를 분리해 보여줍니다.",
      meta: ["API", "캐시", "mock"]
    }),
    '</article>'
  ].join("");
}

function renderFeedOverviewPanel() {
  var evidence = currentResearchEvidence();
  var summary = evidence.summary || {};
  var latest = feedFreshness(summary.latestSeenAt);
  var channels = feedSourceChannels();
  var activeChannels = channels.filter(function (channel) { return channel.enabled; }).length;
  var warningChannels = channels.filter(function (channel) { return channel.enabled && channel.ready === false; }).length;
  var kinds = Array.isArray(summary.byKind) ? summary.byKind : [];
  return [
    '<article class="panel feed-overview-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">뉴스 근거 현황</p>',
    '<h2>피드 운영 대시보드</h2>',
    '</div>',
    '<span class="tone-chip ' + escapeHtml(warningChannels ? "caution" : "watch") + '">' + escapeHtml(warningChannels ? "확인 필요" : "운영 가능") + '</span>',
    '</div>',
    '<div class="feed-command-body">',
    '<div class="feed-command-metrics">',
    renderFeedCommandMetric("저장 근거", Number(summary.total || 0) + "건", "최근 " + latest.label, Number(summary.total || 0) ? latest.tone : "caution"),
    renderFeedCommandMetric("수집 채널", activeChannels + "/" + channels.length, warningChannels ? warningChannels + "개 키 확인" : "준비 완료", warningChannels ? "caution" : "watch"),
    renderFeedCommandMetric("근거 종류", kinds.length + "종", kinds.slice(0, 3).map(function (entry) { return researchEvidenceKindLabel(entry.name); }).join(" · ") || "대기", kinds.length ? "watch" : "hold"),
    renderFeedCommandMetric("게이트", settingEnabled("materialityGateEnabled") ? "조건 기반" : "꺼짐", "관계·변화 상태", settingEnabled("materialityGateEnabled") ? "watch" : "hold"),
    '</div>',
    '<div class="feed-flow-map">',
    feedPipelineStages().slice(0, 3).map(renderFeedFlowNode).join(""),
    '</div>',
    '</div>',
    '</article>'
  ].join("");
}

function renderFeedCommandMetric(label, value, detail, tone) {
  return [
    '<span class="feed-command-metric ' + escapeHtml(tone || "hold") + '"' + cardTypeAttrs("metric-cell", tone || "hold") + '>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '<b>' + escapeHtml(detail || "-") + '</b>',
    '</span>'
  ].join("");
}

function renderFeedFlowNode(stage) {
  return [
    '<span class="feed-flow-node ' + escapeHtml(stage.tone || "hold") + '">',
    '<b>' + escapeHtml(stage.step) + '</b>',
    '<strong>' + escapeHtml(stage.title) + '</strong>',
    '<em>' + escapeHtml(stage.value) + '</em>',
    '</span>'
  ].join("");
}

function renderFeedDetailToggle(kind, label, className) {
  var expanded = researchState.expandedFeedDetail === kind;
  return '<button class="' + escapeHtml(className || "mini-button") + '" type="button" data-feed-detail-toggle="' + escapeHtml(kind) + '">' + escapeHtml(expanded ? "접기" : label) + '</button>';
}

function renderFeedPipelinePanel() {
  return [
    '<article class="panel feed-pipeline-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Data Flow</p>',
    '<h2>수집·판단 흐름</h2>',
    '</div>',
    renderFeedDetailToggle("pipeline", "전체 흐름", "mini-button"),
    '</div>',
    researchState.expandedFeedDetail === "pipeline" ? '' : '<div class="feed-pipeline-list">' + feedPipelineStages().slice(0, 4).map(function (stage) {
      return [
        '<div class="feed-pipeline-row ' + escapeHtml(stage.tone || "hold") + '"' + cardTypeAttrs("process-card", stage.tone || "hold") + '>',
        '<span>' + escapeHtml(stage.step) + '</span>',
        '<div>',
        '<strong>' + escapeHtml(stage.title) + '</strong>',
        '<em>' + escapeHtml(stage.detail || "") + '</em>',
        '</div>',
        '<b>' + escapeHtml(stage.value || "-") + '</b>',
        '</div>'
      ].join("");
    }).join("") + '</div>',
    researchState.expandedFeedDetail === "pipeline" ? renderFeedInlineDetail("pipeline") : '',
    '</article>'
  ].join("");
}

function renderFeedChannelPanel() {
  return [
    '<article class="panel feed-channel-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Source Matrix</p>',
    '<h2>수집 채널 매트릭스</h2>',
    '</div>',
    '<div class="settings-actions">',
    '<span class="metric">' + escapeHtml(feedSourceChannels().filter(function (channel) { return channel.enabled; }).length) + '</span>',
    renderFeedDetailToggle("sources", "채널 전체", "mini-button"),
    '</div>',
    '</div>',
    researchState.expandedFeedDetail === "sources" ? '' : '<div class="feed-channel-grid">' + feedSourceChannels().slice(0, 5).map(function (channel) {
      return [
        '<div class="feed-channel-row ' + escapeHtml(channel.tone || "hold") + '"' + cardTypeAttrs("source-card", channel.tone || "hold") + '>',
        '<div>',
        '<span class="tone-chip ' + escapeHtml(channel.tone || "hold") + '">' + escapeHtml(channel.enabled ? (channel.ready === false ? "키 확인" : "사용") : "중지") + '</span>',
        '<strong>' + escapeHtml(channel.label) + '</strong>',
        '<em>' + escapeHtml(channel.route) + '</em>',
        '</div>',
        '<b>' + escapeHtml(channel.cadence || "-") + '</b>',
        '</div>'
      ].join("");
    }).join("") + '</div>',
    researchState.expandedFeedDetail === "sources" ? renderFeedInlineDetail("sources") : '',
    '</article>'
  ].join("");
}

function feedSettingsEditorLabel(key) {
  return {
    market: "장중 시세·수급",
    research: "뉴스·아카이브",
    graph: "그래프 추론",
    gate: "중요도 게이트",
    external: "공시·외부 원천",
    mapping: "긴 매핑값"
  }[String(key || "").toLowerCase()] || "피드 수집";
}

function renderFeedSettingsPanel() {
  var archiveScope = (settingValue("newsCollectionMaxSymbols") || defaultSettings.newsCollectionMaxSymbols || "40") + "종목 · "
    + (settingValue("newsCollectionLookbackMinutes") || defaultSettings.newsCollectionLookbackMinutes || "180") + "분";
  var reasoningScope = "TypeDB · "
    + (settingValue("ontologyReasoningIntervalSeconds") || defaultSettings.ontologyReasoningIntervalSeconds || "10") + "초";
  var sourceReady = [
    settingEnabled("kisMarketSignalsEnabled"),
    settingEnabled("externalNewsEnabled"),
    settingEnabled("externalDartEnabled"),
    settingEnabled("externalSecEnabled"),
    settingEnabled("externalAlphaEnabled"),
    settingEnabled("externalPublicDataStockEnabled") || settingEnabled("externalPublicDataReferenceEnabled"),
    settingEnabled("externalFredEnabled"),
    settingEnabled("externalCoinGeckoEnabled")
  ].filter(Boolean).length;
  var cards = [
    {
      tone: settingEnabled("kisMarketSignalsEnabled") ? "watch" : "hold",
      value: settingEnabled("kisMarketSignalsEnabled") ? "사용" : "중지",
      title: "장중 시세·수급",
      description: "KIS 수급 수집 범위와 장중 live 우선 정책을 조정합니다.",
      type: "feed-settings-editor",
      key: "market",
      button: "수급 설정"
    },
    {
      tone: settingEnabled("newsCollectionEnabled") || settingEnabled("externalNewsEnabled") ? "watch" : "hold",
      value: archiveScope,
      title: "뉴스·아카이브",
      description: "뉴스 헤드라인, 저장형 아카이브, 기사 AI 분석 기준을 관리합니다.",
      type: "feed-settings-editor",
      key: "research",
      button: "뉴스 설정"
    },
    {
      tone: settingEnabled("ontologyReasoningEnabled") ? "watch" : "hold",
      value: reasoningScope,
      title: "그래프 추론",
      description: "TypeDB 연결과 추론 배치·주기를 수정합니다.",
      type: "feed-settings-editor",
      key: "graph",
      button: "추론 설정"
    },
    {
      tone: settingEnabled("materialityGateEnabled") ? "watch" : "hold",
      value: settingEnabled("materialityGateEnabled") ? "조건 기반" : "꺼짐",
      title: "중요도 게이트",
      description: "가격·추세·거래량·뉴스가 알림 후보로 들어가는 기준입니다.",
      type: "feed-settings-editor",
      key: "gate",
      button: "게이트 설정"
    },
    {
      tone: sourceReady ? "watch" : "hold",
      value: sourceReady + "/8",
      title: "공시·외부 원천",
      description: "공식 국내 일별 시세, 공시, 미장, 거시, 크립토 수집 여부를 관리합니다.",
      type: "feed-settings-editor",
      key: "external",
      button: "외부 원천"
    },
    {
      tone: "hold",
      value: "매핑",
      title: "긴 매핑값",
      description: "FRED·코인·OpenDART·SEC처럼 긴 값은 기본 화면에서 숨깁니다.",
      type: "feed-settings-editor",
      key: "mapping",
      button: "매핑 편집"
    }
  ];
  return [
    '<article class="panel feed-settings-panel feed-settings-overview-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Feed Operations</p>',
    '<h2>피드 수집 설정</h2>',
    '<p class="subtle">운영 화면에서는 원천 상태와 편집 진입점만 봅니다. 실제 입력은 상세 레이어에서 수정합니다.</p>',
    '</div>',
    '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
    '</div>',
    '<div class="settings-body feed-settings-body">',
    '<div class="settings-api-grid feed-settings-summary">',
    renderSettingsApiCard("원천 준비도", sourceReady + "/8개 사용", [
      configuredChip("KIS 수급", settingEnabled("kisMarketSignalsEnabled"), configuredCount(["kisAppKey", "kisAppSecret"]) + "/2"),
      configuredChip("뉴스", settingEnabled("externalNewsEnabled"), newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider)),
      configuredChip("OpenDART", settingEnabled("externalDartEnabled"), isConfiguredSetting("opendartApiKey") ? "키 저장됨" : "키 필요"),
      configuredChip("공식 일별 시세", settingEnabled("externalPublicDataStockEnabled"), isConfiguredSetting("publicDataPortalServiceKey") ? "키 저장됨" : "키 필요"),
      configuredChip("공식 기업·지수", settingEnabled("externalPublicDataReferenceEnabled"), isConfiguredSetting("publicDataPortalServiceKey") ? "키 저장됨" : "키 필요"),
      configuredChip("SEC", settingEnabled("externalSecEnabled"), "무키"),
      configuredChip("거시·크립토", settingEnabled("externalFredEnabled") || settingEnabled("externalCoinGeckoEnabled"), "보조 신호")
    ]),
    renderSettingsApiCard("아카이브 범위", archiveScope, [
      configuredChip("관심", settingValue("newsCollectionIncludeWatchlist") !== "0", "포함"),
      configuredChip("보유", settingValue("newsCollectionIncludeHoldings") !== "0", "포함"),
      configuredChip("관련성", true, newsStateSettingLabel("relevance", settingValue("newsCollectionMinimumRelevanceState") || defaultSettings.newsCollectionMinimumRelevanceState)),
      configuredChip("저장 중요도", settingEnabled("newsCollectionQualityGateEnabled"), newsStateSettingLabel("materiality", settingValue("newsCollectionMinimumMaterialityState") || defaultSettings.newsCollectionMinimumMaterialityState))
    ]),
    renderSettingsApiCard("추론 흐름", reasoningScope, [
      configuredChip("추론", settingEnabled("ontologyReasoningEnabled"), settingValue("ontologyReasoningBatchSize") || defaultSettings.ontologyReasoningBatchSize || "20"),
      configuredChip(
        "AI 판단",
        settingEnabled("notificationAiGateEnabled") && String(settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount || "0") !== "0",
        String(settingValue("notificationAiQueueWorkerCount") || defaultSettings.notificationAiQueueWorkerCount || "0") + "개 실행"
      ),
      configuredChip("게이트", settingEnabled("materialityGateEnabled"), "상태 전이"),
      configuredChip("뉴스 기준", true, newsStateSettingLabel("materiality", settingValue("newsDigestMinimumMaterialityState") || defaultSettings.newsDigestMinimumMaterialityState))
    ]),
    '</div>',
    '<div class="work-detail-grid feed-settings-action-grid">',
    cards.map(renderStrategyOverviewActionCard).join(""),
    '</div>',
    renderSettingsSmartSavePanel(),
    '</div>',
    '</article>'
  ].join("");
}

export { feedEvidenceDataMeta, feedImpactBoardWorkDetailPayload, feedPipelineStages, feedPortfolioBoardWorkDetailPayload, feedResearchEvidenceItems, feedSettingsEditorLabel, feedSourceBoardWorkDetailPayload, feedSourceChannels, feedThemeBoardWorkDetailPayload, renderFeedDetailToggle };
