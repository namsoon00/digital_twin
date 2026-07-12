(function () {
  var app = document.getElementById("app");
  var ontologyGraphInstances = {};
  var defaultSettings = window.OrbitAlphaDefaultSettings || {};

  var tabs = [
    { id: "overview", label: "홈", description: "관제 요약", groupId: "command" },
    { id: "accounts", label: "계정", description: "계좌·API 원장", groupId: "market" },
    { id: "watchlist", label: "관심종목", description: "관찰 대상", groupId: "market" },
    { id: "symbols", label: "전체종목", description: "시장 유니버스", groupId: "market" },
    { id: "notifications", label: "알림", description: "신호 실행", groupId: "decision" },
    { id: "modeling", label: "투자 분석", description: "전략·관계·AI", groupId: "decision" },
    { id: "experiments", label: "실험", description: "온톨로지 Lab", groupId: "decision" },
    { id: "feed", label: "피드", description: "뉴스·근거 품질", groupId: "market" },
    { id: "system", label: "시스템", description: "흐름·매뉴얼", groupId: "control" },
    { id: "settings", label: "설정", description: "런타임 환경", groupId: "control" }
  ];
  var appBrandName = "Orbit Alpha";
  var appBrandSubtitle = "포트폴리오 신호 궤도 관제";
  var webStyleContract = {
    id: "orbit-alpha-console-v2",
    version: "20260712",
    shellClass: "web-style-shell",
    pageClass: "web-style-page",
    commandClass: "web-style-command-strip"
  };
  var bottomTabIds = ["overview", "watchlist", "notifications", "modeling", "experiments"];
  var managementTabIds = ["accounts", "symbols", "feed", "system", "settings"];
  var navigationGroups = [
    { id: "command", label: "Command", description: "오늘 먼저 보는 관제", tabIds: ["overview"] },
    { id: "market", label: "Market Desk", description: "계좌·종목·근거 정리", tabIds: ["accounts", "watchlist", "symbols", "feed"] },
    { id: "decision", label: "Decision Stack", description: "판단·알림·실험 실행", tabIds: ["modeling", "notifications", "experiments"] },
    { id: "control", label: "Control Plane", description: "문서·런타임 관리", tabIds: ["system", "settings"] }
  ];
  var pageStructureCatalog = {
    overview: {
      layer: "Mission Control",
      entity: "Portfolio Snapshot",
      objective: "계정, 포트폴리오, 신호, 모델 상태를 한 화면에서 먼저 판단합니다.",
      workflow: ["상태 확인", "위험 노출", "즉시 조치"]
    },
    accounts: {
      layer: "Account Registry",
      entity: "Service Account",
      objective: "계좌/API/알림 채널을 원장으로 정리하고 데이터 출처를 검증합니다.",
      workflow: ["계정 목록", "출처 검증", "저장 관리"]
    },
    watchlist: {
      layer: "Observation Book",
      entity: "Watch Symbol",
      objective: "계정별 관찰 종목을 분리해 알림과 전략 판단의 입력으로 관리합니다.",
      workflow: ["계정 선택", "종목 편집", "알림 연결"]
    },
    symbols: {
      layer: "Universe Catalog",
      entity: "Market Symbol",
      objective: "시장 유니버스를 검색하고 관심종목 편입 후보를 정리합니다.",
      workflow: ["목록 조회", "필터 적용", "계정 편입"]
    },
    notifications: {
      layer: "Signal Execution",
      entity: "Notification Job",
      objective: "왜 알림이 만들어졌는지 판단 로그, 정책, 템플릿을 분리해 확인합니다.",
      workflow: ["판단 확인", "정책 조정", "본문 발송"]
    },
    modeling: {
      layer: "Strategy Workbench",
      entity: "Investment Opinion",
      objective: "보유·관심 데이터와 관계 그래프를 투자 판단 후보로 압축합니다.",
      workflow: ["판단판", "근거 평가", "액션 큐"]
    },
    experiments: {
      layer: "Ontology Lab",
      entity: "Rule Experiment",
      objective: "새 관계 규칙을 샌드박스에서 재생하고 승격 가능성을 검토합니다.",
      workflow: ["규칙 초안", "재생 검증", "승격 판단"]
    },
    feed: {
      layer: "Evidence Pipeline",
      entity: "Research Evidence",
      objective: "뉴스, 공시, 시장 데이터를 근거 저장소로 수집하고 품질을 점검합니다.",
      workflow: ["소스 설정", "수집 품질", "근거 저장"]
    },
    system: {
      layer: "Operating Manual",
      entity: "System Flow",
      objective: "데이터 수집부터 추론, 알림까지 전체 실행 흐름을 문서화합니다.",
      workflow: ["구조 이해", "이벤트 흐름", "운영 기준"]
    },
    settings: {
      layer: "Runtime Control",
      entity: "Local Setting",
      objective: "로컬 우선 환경, 외부 API, 전달 채널 설정을 안전하게 관리합니다.",
      workflow: ["로컬 상태", "연결 설정", "변경 저장"]
    }
  };
  var notificationSections = [
    { id: "status", label: "현황", description: "발송 판단" },
    { id: "signals", label: "신호", description: "감지 내역" },
    { id: "policy", label: "정책", description: "타입별 룰" },
    { id: "templates", label: "템플릿", description: "본문·미리보기" },
    { id: "advanced", label: "고급", description: "채널·임계값" }
  ];
  var accountSections = [
    { id: "overview", label: "개요", description: "상태 요약" },
    { id: "connections", label: "연결", description: "API 출처" },
    { id: "balance", label: "금액", description: "산식 검증" },
    { id: "management", label: "관리", description: "계정·키" }
  ];
  var strategySections = [
    { id: "overview", label: "판단판", description: "오늘의 구조" },
    { id: "evidence", label: "근거 카드", description: "데이터·관계" },
    { id: "results", label: "액션 큐", description: "보류·후보" },
    { id: "graphs", label: "관계 그래프", description: "TBox·ABox" },
    { id: "registry", label: "규칙·프롬프트", description: "운영 기준" },
    { id: "trace", label: "검증 추적", description: "행·룰" }
  ];
  var ontologySections = [
    { id: "overview", label: "개요", description: "요약·상태" },
    { id: "structure", label: "전체 구조", description: "흐름 지도" },
    { id: "graphs", label: "관계 그래프", description: "규칙·현재 데이터" },
    { id: "registry", label: "규칙·프롬프트", description: "런타임 관리" },
    { id: "trace", label: "관계 추적", description: "행·룰 검증" }
  ];
  var pageModeOptions = [
    { id: "results", label: "결과", description: "지금 봐야 할 상태와 결과" },
    { id: "settings", label: "설정", description: "편집, 정책, 고급 설정" }
  ];
  var pageModeSectionMap = {
    accounts: {
      results: ["overview", "connections", "balance"],
      settings: ["management"]
    },
    notifications: {
      results: ["status", "signals"],
      settings: ["policy", "templates", "advanced"]
    },
    modeling: {
      results: ["overview", "evidence", "results", "graphs", "trace"],
      settings: ["registry"]
    }
  };
  var pageModeEnabledTabs = ["accounts", "notifications", "modeling", "feed"];

  function activeTabMeta() {
    return tabs.filter(function (tab) { return tab.id === state.activeTab; })[0] || tabs[0];
  }

  function tabById(tabId) {
    return tabs.filter(function (tab) { return tab.id === tabId; })[0] || null;
  }

  function navigationGroupById(groupId) {
    return navigationGroups.filter(function (group) { return group.id === groupId; })[0] || navigationGroups[0];
  }

  function navigationGroupForTab(tabId) {
    var tab = tabById(tabId) || tabs[0];
    return navigationGroupById(tab.groupId || "command");
  }

  function tabsForNavigationGroup(group) {
    var ids = (group && group.tabIds) || [];
    return ids.map(tabById).filter(Boolean);
  }

  function pageStructureMeta(pageId) {
    var tab = tabById(pageId) || tabs[0];
    var group = navigationGroupForTab(tab.id);
    var structure = pageStructureCatalog[pageId] || pageStructureCatalog[tab.id] || {};
    return {
      groupId: group.id,
      groupLabel: group.label,
      groupDescription: group.description,
      layer: structure.layer || tab.label,
      entity: structure.entity || tab.label,
      objective: structure.objective || tab.description || "",
      workflow: structure.workflow || [],
      tabLabel: tab.label || pageId
    };
  }

  var alertRuleCatalog = [
    { key: "investmentInsight", group: "투자 알림", label: "온톨로지 투자 인사이트", description: "관계 그래프에서 의미 있는 투자 인사이트가 생성될 때 실제 발송" },
    { key: "newsDigest", group: "데이터", label: "뉴스/피드 새 정보", description: "관련성과 중요도 기준을 통과한 새 뉴스 근거가 들어올 때" },
    { key: "watchlistOntologySignal", group: "온톨로지 근거", label: "관심종목 관계 신호", description: "투자 인사이트에 넣을 Neo4j InferenceBox 기반 관심종목 근거 신호" },
    { key: "holdingTiming", group: "온톨로지 근거", label: "보유 타이밍 신호", description: "투자 인사이트에 넣을 보유 종목 타이밍 근거 신호" },
    { key: "ontologyInferenceMissing", group: "온톨로지 상태", label: "추론 결과 누락", description: "실계좌 데이터는 있지만 Neo4j InferenceBox 추론 결과가 없을 때" },
    { key: "monitorHeartbeat", group: "실시간", label: "상태 확인 메시지", description: "실시간 워커가 살아 있는지 주기적으로 짧게 보낼 때" },
    { key: "monitorConnection", group: "실시간", label: "연결 상태 변화", description: "실시간 모니터링 중 토스 연결 상태가 바뀔 때" },
    { key: "externalDataConnection", group: "외부 API", label: "외부 API 연결", description: "외부 데이터 API 키, 한도, 응답 오류가 감지될 때" }
  ];
  var userManagedNotificationTypes = ["investmentInsight", "newsDigest", "ontologyInferenceMissing", "monitorConnection", "externalDataConnection"];
  var visibleNotificationTemplateTypes = ["default", "investmentInsight", "newsDigest", "ontologyInferenceMissing", "monitorConnection", "externalDataConnection", "modelReview", "workHandoff", "notification"];
  function managedNotificationType(key) {
    return userManagedNotificationTypes.indexOf(String(key || "")) >= 0;
  }
  function visibleNotificationTemplateType(key) {
    return visibleNotificationTemplateTypes.indexOf(String(key || "")) >= 0;
  }
  function notificationPolicyCatalog() {
    return alertRuleCatalog.filter(function (rule) {
      return managedNotificationType(rule.key);
    });
  }
  var notificationTypeEmojis = {
    default: "🔔",
    priceBuyLimit: "🟢",
    priceStop: "🛡️",
    priceTrim: "💰",
    investmentInsight: "🧭",
    modelBuy: "🟢",
    modelSell: "🔴",
    watchlistBuyCandidate: "👀",
    modelScoreGap: "⚖️",
    flowVolume: "📊",
    flowBuyShare: "🟢",
    flowSellShare: "🔴",
    flowOrderbook: "⚖️",
    trendMomentum: "📈",
    trendPullback: "📉",
    holdingProfit: "💰",
    holdingLoss: "🛡️",
    holdingConcentration: "📦",
    sectorConcentration: "🏭",
    marketCashLow: "💵",
    dataFreshness: "🕒",
    tossConnection: "🔌",
    orderPending: "⏳",
    orderReject: "⛔",
    watchlistQuote: "👀",
    watchlistQuotePending: "⏳",
    holdingTiming: "⚖️",
    monitorHeartbeat: "💓",
    monitorConnection: "🔌",
    monitorPositionChange: "📦",
    monitorPnlChange: "📊",
    monitorValueChange: "💵",
    monitorTrendChange: "📈",
    monitorCashChange: "💵",
    monitorDecisionChange: "🔁",
    externalEquityMove: "🇺🇸",
    externalCryptoMove: "🪙",
    externalMacroShift: "🏦",
    externalDartDisclosure: "📄",
    externalDataConnection: "🛰️",
    modelReview: "🧠",
    workHandoff: "✅",
    notification: "🔔"
  };
  function notificationMessageTypeIcon(type) {
    return notificationTypeEmojis[type] || "🔔";
  }
  function labelWithNotificationIcon(type, label) {
    var icon = notificationMessageTypeIcon(type);
    var text = String(label || type || "").trim();
    return [icon, text].filter(Boolean).join(" ");
  }
  var alertThresholdCatalog = [
    { key: "modelBuyScore", label: "모델 매수 점수", unit: "점", step: "1" },
    { key: "modelSellScore", label: "모델 매도 점수", unit: "점", step: "1" },
    { key: "watchlistBuyScore", label: "관심종목 매수 점수", unit: "점", step: "1" },
    { key: "modelScoreGap", label: "모델 점수 차이", unit: "점", step: "1" },
    { key: "volumeRatioHigh", label: "거래량 배율", unit: "x", step: "0.1" },
    { key: "buyShareHigh", label: "매수 체결 비중", unit: "%", step: "1" },
    { key: "sellShareHigh", label: "매도 체결 비중", unit: "%", step: "1" },
    { key: "orderbookImbalance", label: "호가 불균형", unit: "%", step: "1" },
    { key: "momentumUp", label: "상승 변화율", unit: "%", step: "0.1" },
    { key: "momentumDown", label: "하락 변화율", unit: "%", step: "0.1" },
    { key: "profitRateHigh", label: "익절 점검 수익률", unit: "%", step: "0.1" },
    { key: "lossRateLow", label: "손실 점검 수익률", unit: "%", step: "0.1" },
    { key: "lossRateBufferPct", label: "손실 기준 완충폭", unit: "%p", step: "0.1" },
    { key: "lossGuardVolumeConfirmRatio", label: "손실 확인 거래량 배율", unit: "x", step: "0.1" },
    { key: "lossGuardMa60SupportPct", label: "60일선 유지 기준", unit: "%", step: "0.1" },
    { key: "lossGuardWeakEvidencePenalty", label: "확인 약할 때 감점", unit: "점", step: "1" },
    { key: "positionWeightHigh", label: "단일 종목 비중", unit: "%", step: "1" },
    { key: "sectorWeightHigh", label: "섹터 비중", unit: "%", step: "1" },
    { key: "marketCashLow", label: "시장별 현금 하단", unit: "%", step: "1" },
    { key: "priceNearPercent", label: "가격 접근 허용폭", unit: "%", step: "0.1" },
    { key: "staleMinutes", label: "데이터 지연 시간", unit: "분", step: "1" },
    { key: "pendingOrderMinutes", label: "미체결 점검 시간", unit: "분", step: "1" },
    { key: "watchlistPriceDelta", label: "관심종목 현재가 변화", unit: "%", step: "0.1" },
    { key: "monitorPnlDelta", label: "실시간 손익률 변화", unit: "%p", step: "0.1" },
    { key: "monitorValueDelta", label: "실시간 평가액 변화", unit: "%", step: "0.1" },
    { key: "monitorMaDistance", label: "이동평균과 현재가 차이", unit: "%", step: "0.1" },
    { key: "monitorCashDelta", label: "실시간 현금비중 변화", unit: "%p", step: "1" },
    { key: "monitorExitPressureDelta", label: "실시간 판단 점수 변화", unit: "점", step: "1" },
    { key: "externalEquityChangePct", label: "미장 가격 변화", unit: "%", step: "0.1" },
    { key: "externalCryptoChange24hPct", label: "크립토 24h 변화", unit: "%", step: "0.1" },
    { key: "externalCryptoChange7dPct", label: "크립토 7d 변화", unit: "%", step: "0.1" },
    { key: "externalBitcoinChange24hPct", label: "비트코인 24h 변화", unit: "%", step: "0.1" },
    { key: "externalBitcoinChange7dPct", label: "비트코인 7d 변화", unit: "%", step: "0.1" },
    { key: "externalMacroRateDeltaBp", label: "거시 금리 변화", unit: "bp", step: "1" },
    { key: "entryPullbackMa20BelowPct", label: "매수 관찰 20일선 하단", unit: "%", step: "0.1" },
    { key: "entryPullbackMa20DeepPct", label: "매수 보류 낙폭 하단", unit: "%", step: "0.1" },
    { key: "entryMa5TimingMinPct", label: "매수 5일선 타이밍", unit: "%", step: "0.1" },
    { key: "entryMomentumMa20MinPct", label: "매수 20일선 회복 기준", unit: "%", step: "0.1" },
    { key: "entryMomentumMa60MinPct", label: "매수 60일선 회복 기준", unit: "%", step: "0.1" },
    { key: "entryMa60SupportPct", label: "매수 60일선 지지", unit: "%", step: "0.1" },
    { key: "entryVolumeMinRatio", label: "매수 최소 거래량", unit: "x", step: "0.1" },
    { key: "entryVolumeMaxRatio", label: "매수 과열 거래량", unit: "x", step: "0.1" },
    { key: "entrySmartMoneyMin", label: "매수 수급 회복", unit: "점", step: "1" },
    { key: "entryTradeStrengthMin", label: "매수 체결강도", unit: "점", step: "1" },
    { key: "entryOrderbookImbalanceMin", label: "매수 호가 우위", unit: "%", step: "1" },
    { key: "entryMaxPositionWeight", label: "매수 가능 종목 비중", unit: "%", step: "1" },
    { key: "entryMaxSectorWeight", label: "매수 가능 섹터 비중", unit: "%", step: "1" },
    { key: "macroRateDeltaBp", label: "금리 변화 기준", unit: "bp", step: "1" },
    { key: "macroRateHighPct", label: "고금리 참고 레벨", unit: "%", step: "0.1" },
    { key: "macroRateLowPct", label: "저금리 참고 레벨", unit: "%", step: "0.1" },
    { key: "macroCurveInversionPct", label: "금리 스프레드 참고", unit: "%p", step: "0.1" },
    { key: "usdKrwDeltaKrw", label: "USD/KRW 변화액", unit: "원", step: "1" },
    { key: "usdKrwDeltaPct", label: "USD/KRW 변화율", unit: "%", step: "0.1" },
    { key: "usdKrw7dDeltaKrw", label: "USD/KRW 7일 변화액", unit: "원", step: "1" },
    { key: "usdKrw7dDeltaPct", label: "USD/KRW 7일 변화율", unit: "%", step: "0.1" },
    { key: "usdKrwHigh", label: "USD/KRW 약세 참고", unit: "원", step: "1" },
    { key: "usdKrwLow", label: "USD/KRW 강세 참고", unit: "원", step: "1" },
    { key: "fxExposureReview", label: "외화 노출 참고", unit: "%", step: "1" },
    { key: "fxExposureHigh", label: "외화 노출 기준", unit: "%", step: "1" }
  ];
  var settingsMemoryStore = "";
  var snapshotMemoryStore = "";
  var staticBuildConfigPromise = null;
  var watchSuggestTimer = null;
  var watchSuggestRequestId = 0;
  var snackbarTimer = null;
  var realtimeSocket = null;
  var realtimeReconnectTimer = null;
  var realtimeReloadTimer = null;
  var realtimeSeenEventIds = {};
  var appNavLastScrollY = 0;
  var appNavHidden = false;
  var appNavScrollTicking = false;
  var topbarCollapsed = false;
  var topbarScrollTicking = false;
  var cachedSnapshot = loadCachedSnapshot();
  var state = {
    loading: !cachedSnapshot,
    refreshing: false,
    error: "",
    snapshot: cachedSnapshot,
    snapshotFromCache: Boolean(cachedSnapshot),
    researchEvidence: null,
    researchEvidenceLoading: false,
    researchEvidenceError: "",
    researchEvidenceFilters: { symbol: "", kind: "", limit: "80" },
    researchEvidenceDeleting: "",
    activeTab: initialTab(),
    previousTab: "",
    tabBarScrollLeft: 0,
    tabScrollPositions: {},
    pageViewModes: initialPageViewModes(),
    settings: loadSettings(),
    snackbar: null,
    realtime: {
      supported: typeof window.WebSocket !== "undefined",
      connected: false,
      lastEvent: "",
      lastEventAt: "",
      reconnects: 0,
      eventCounts: {},
      latestEvents: [],
      monitoring: {},
      notificationJobs: {}
    },
    showSecrets: false,
    settingsSaving: false,
    settingsSaved: false,
    serverSettingsLoaded: false,
    serverSettingsError: "",
    serverSettingsLocked: false,
    serverConfigured: {},
    notificationTemplates: [],
    notificationTemplateVariables: [],
    notificationTemplatesLoading: false,
    notificationTemplatesLoaded: false,
    notificationTemplatesError: "",
    notificationTemplatesSaved: false,
    notificationTemplateSending: "",
    notificationRules: [],
    notificationRuleConditionTypes: [],
    notificationRulesLoading: false,
    notificationRulesLoaded: false,
    notificationRulesError: "",
    notificationRulesSaved: false,
    notificationExpandedTypes: {},
    notificationExpandedGroups: {},
    activeNotificationJobKey: "",
    activeNotificationSection: initialNotificationSection(),
    activeAccountSection: initialAccountSection(),
    activeStrategySection: initialStrategySection(),
    activeOntologySection: initialOntologySection(),
    activeNotificationMessageType: "investmentInsight",
    notificationPolicyEditorOpen: false,
    activeNotificationTemplateType: "investmentInsight",
    notificationTemplateEditorOpen: false,
    notificationMarketHoursSessions: [],
    notificationJobItems: [],
    notificationJobsLoading: false,
    notificationJobsLoaded: false,
    notificationJobsError: "",
    notificationJobsSummary: {},
    notificationJobDiagnostics: {},
    notificationExpandedJobs: {},
    ontologyExperiments: null,
    ontologyExperimentsLoading: false,
    ontologyExperimentsLoaded: false,
    ontologyExperimentsError: "",
    ontologyExperimentAction: "",
    messageSchedules: [],
    messageSchedulesLoading: false,
    messageSchedulesError: "",
    messageSchedulesLoaded: false,
    staticBuildConfig: null,
    staticBuildConfigError: "",
    serviceAccounts: [],
    serviceAccountsLoading: false,
    serviceAccountsLoaded: false,
    serviceAccountsError: "",
    accountDraft: defaultAccountDraft(),
    editingAccountId: "",
    accountSaved: false,
    ontologyRulebox: null,
    ontologyRuleboxJson: "",
    ontologyRuleboxLoading: false,
    ontologyRuleboxSaving: false,
    ontologyRuleboxRunning: false,
    ontologyRuleboxProposing: false,
    ontologyRuleboxError: "",
    ontologyRuleboxLastRun: null,
    ontologyRuleboxChangeReason: "",
    activeWatchAccountId: "",
    editingWatchAccountId: "",
    editingWatchSymbol: "",
    watchlistSavingAccountId: "",
    watchlistError: "",
    watchSuggestQuery: "",
    watchSuggestItems: [],
    watchSuggestLoading: false,
    watchSuggestError: "",
    symbolUniverse: { items: [], summary: { markets: [], sources: [], total: 0, maxAgeHours: 24 } },
    symbolUniverseLoading: false,
    symbolUniverseRefreshing: false,
    symbolUniverseLoaded: false,
    symbolUniverseError: "",
    symbolUniverseQuery: "",
    symbolUniverseMarket: "",
    symbolUniverseOffset: 0,
    symbolUniverseLimit: 80,
    monitoringDetail: null,
    expandedOntologyGraphId: ""
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function beginnerFriendlyText(value) {
    var text = String(value == null ? "" : value);
    [
      ["온톨로지 판단", "관계 판단"],
      ["온톨로지 컨텍스트", "관계 분석 정보"],
      ["온톨로지 그래프", "관계 분석 데이터"],
      ["온톨로지", "관계 분석"],
      ["세계관 집중도", "관련 종목 비중"],
      ["세계관", "투자 관점"],
      ["손실 thesis 재검증", "손실 구간 보유 이유 재확인"],
      ["thesis 충돌", "보유 이유와 충돌"],
      ["thesis 훼손", "보유 이유 약화"],
      ["보유 thesis", "보유 이유"],
      ["종목 thesis", "종목 보유 이유"],
      ["기존 thesis", "기존 보유 이유"],
      ["thesis", "보유 이유"],
      ["관계 압력", "관계 신호"],
      ["증거", "근거"],
      ["컨텍스트", "정보"],
      ["가설", "설명"]
    ].forEach(function (pair) {
      text = text.split(pair[0]).join(pair[1]);
    });
    return text;
  }

  function normalizeFormulaAliases(value) {
    return String(value == null ? "" : value)
      .replace(/\bthesisScore\b/g, "buyReasonScore")
      .replace(/\bthesisWeight\b/g, "buyReasonWeight");
  }

  function requestJson(path) {
    return fetch(path, {
      headers: { "Accept": "application/json" },
      cache: "no-store"
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "요청 실패");
        return payload;
      });
    });
  }

  function loadCachedSnapshot() {
    var raw = readSessionPayload("orbitAlphaLastSnapshot", snapshotMemoryStore);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (error) {
      return null;
    }
  }

  function writeCachedSnapshot(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return false;
    try {
      var payload = JSON.stringify(snapshot);
      snapshotMemoryStore = payload;
      return writeSessionPayload("orbitAlphaLastSnapshot", payload);
    } catch (error) {
      return false;
    }
  }

  function readSessionPayload(key, fallback) {
    try {
      if (window.sessionStorage) {
        var value = window.sessionStorage.getItem(key);
        return value == null ? fallback : value;
      }
    } catch (error) {
      return fallback;
    }
    return fallback;
  }

  function writeSessionPayload(key, payload) {
    try {
      if (window.sessionStorage) {
        window.sessionStorage.setItem(key, payload);
      }
      return true;
    } catch (error) {
      return false;
    }
  }

  function sendJson(path, method, payload) {
    return fetch(path, {
      method: method,
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json"
      },
      cache: "no-store",
      body: JSON.stringify(payload || {})
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) throw new Error(body.error || "요청 실패");
        return body;
      });
    });
  }

  function realtimeWebSocketUrl() {
    var protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    var host = window.location.host || window.location.hostname || "127.0.0.1:3000";
    return protocol + "//" + host + "/ws";
  }

  function markRealtimeState(connected, eventName) {
    state.realtime.connected = Boolean(connected);
    state.realtime.lastEvent = eventName || state.realtime.lastEvent || "";
    state.realtime.lastEventAt = new Date().toISOString();
    if (state.snapshot) render();
  }

  function queueRealtimeReload(eventType) {
    if (isStaticPreviewHost()) return;
    if (realtimeReloadTimer) clearTimeout(realtimeReloadTimer);
    realtimeReloadTimer = setTimeout(function () {
      var tasks = [];
      if (/^settings\./.test(eventType)) {
        tasks.push(loadServerSettings());
        tasks.push(loadNotificationSchedules());
      } else if (/^account\./.test(eventType)) {
        tasks.push(loadServiceAccounts());
        tasks.push(load());
      } else if (/^notification_template\.|^notification_rule\.|^notification\./.test(eventType)) {
        tasks.push(loadNotificationTemplates());
        tasks.push(loadNotificationRules());
        tasks.push(loadNotificationJobs());
        tasks.push(loadNotificationSchedules());
      } else if (/^symbol_universe\./.test(eventType)) {
        tasks.push(loadSymbolUniverse());
      } else if (/^app\.|^chat\./.test(eventType)) {
        tasks.push(load());
      }
      if (!tasks.length) tasks.push(load());
      Promise.all(tasks.map(function (task) {
        return task.catch(function () { return null; });
      })).finally(function () {
        render();
      });
    }, 250);
  }

  function normalizeRealtimeEvent(event) {
    if (!event || typeof event !== "object") return null;
    var payload = event.payload && typeof event.payload === "object" ? event.payload : {};
    return {
      name: event.name || event.type || "",
      eventId: event.eventId || event.event_id || "",
      aggregateId: event.aggregateId || event.aggregate_id || "",
      occurredAt: event.occurredAt || event.occurred_at || "",
      payload: payload
    };
  }

  function notificationJobSummaryText(jobs) {
    jobs = jobs || {};
    var pending = Number(jobs.pending || 0);
    var processing = Number(jobs.processing || 0);
    var failed = Number(jobs.failed || 0);
    var suppressed = Number(jobs.suppressed || 0);
    if (pending || processing || failed || suppressed) {
      return "대기 " + pending + " · 처리 " + processing + " · 실패 " + failed + " · 제외 " + suppressed;
    }
    if (Number(jobs.done || 0)) return "완료 " + Number(jobs.done || 0);
    return "-";
  }

  function realtimeEventSnackbar(event) {
    var payload = event.payload || {};
    if (event.name === "notification.job_queued") {
      return { message: "알림 작업이 큐에 적재됐습니다: " + (payload.messageType || "notification"), tone: "success" };
    }
    if (event.name === "notification.test_requested") {
      return { message: "테스트 알림 요청을 접수했습니다.", tone: "success" };
    }
    if (event.name === "notification_template.updated") {
      return { message: "알림 템플릿이 갱신됐습니다: " + (payload.messageType || event.aggregateId || "-"), tone: "success" };
    }
    if (event.name === "notification_rule.updated") {
      return { message: "알림 발송 룰이 갱신됐습니다: " + (payload.messageType || event.aggregateId || "-"), tone: "success" };
    }
    if (event.name === "monitoring.alerts_detected") {
      return { message: "모니터링 알림 " + Number(payload.count || 0) + "건이 감지됐습니다.", tone: "danger" };
    }
    if (event.name === "monitoring.cycle_completed" && Number(payload.alertCount || 0) > 0) {
      return { message: "모니터링 사이클 완료: 알림 " + Number(payload.alertCount || 0) + "건", tone: "success" };
    }
    return null;
  }

  function recordRealtimeEvent(event, silent) {
    var normalized = normalizeRealtimeEvent(event);
    if (!normalized || !normalized.name) return;
    state.realtime.lastEvent = normalized.name;
    state.realtime.lastEventAt = normalized.occurredAt || new Date().toISOString();
    if (normalized.eventId) {
      if (realtimeSeenEventIds[normalized.eventId]) return;
      realtimeSeenEventIds[normalized.eventId] = true;
    }
    if (!silent) {
      var snackbar = realtimeEventSnackbar(normalized);
      if (snackbar) showSnackbar(snackbar.message, snackbar.tone);
    }
  }

  function applyRealtimeStatus(payload, silent) {
    payload = payload || {};
    state.realtime.eventCounts = payload.events || state.realtime.eventCounts || {};
    state.realtime.latestEvents = Array.isArray(payload.latestEvents) ? payload.latestEvents : state.realtime.latestEvents || [];
    state.realtime.monitoring = payload.monitoring || state.realtime.monitoring || {};
    state.realtime.notificationJobs = payload.notificationJobs || state.realtime.notificationJobs || {};
    state.realtime.latestEvents.forEach(function (event) {
      recordRealtimeEvent(event, silent);
    });
  }

  function handleRealtimeMessage(message) {
    var eventType = message.type || (message.payload && message.payload.event && message.payload.event.name) || "";
    if (!eventType || eventType === "realtime.heartbeat") return;
    if (eventType === "realtime.connected" || eventType === "realtime.status" || eventType === "realtime.pong") {
      applyRealtimeStatus(message.payload || {}, eventType === "realtime.connected");
      markRealtimeState(true, eventType);
      return;
    }
    recordRealtimeEvent((message.payload && message.payload.event) || {
      name: eventType,
      occurredAt: message.occurredAt,
      payload: message.payload || {}
    }, false);
    markRealtimeState(true, eventType);
    if (eventType !== "realtime.connected") queueRealtimeReload(eventType);
  }

  function connectRealtime() {
    if (isStaticPreviewHost() || !state.realtime.supported || realtimeSocket) return;
    try {
      realtimeSocket = new window.WebSocket(realtimeWebSocketUrl());
    } catch (error) {
      state.realtime.supported = false;
      return;
    }
    realtimeSocket.addEventListener("open", function () {
      state.realtime.reconnects = 0;
      markRealtimeState(true, "realtime.connected");
    });
    realtimeSocket.addEventListener("message", function (event) {
      try {
        handleRealtimeMessage(JSON.parse(event.data || "{}"));
      } catch (error) {
        handleRealtimeMessage({ type: "realtime.message" });
      }
    });
    realtimeSocket.addEventListener("close", function () {
      realtimeSocket = null;
      markRealtimeState(false, "realtime.disconnected");
      if (realtimeReconnectTimer) clearTimeout(realtimeReconnectTimer);
      var delay = Math.min(15000, 1000 + state.realtime.reconnects * 1500);
      state.realtime.reconnects += 1;
      realtimeReconnectTimer = setTimeout(connectRealtime, delay);
    });
    realtimeSocket.addEventListener("error", function () {
      markRealtimeState(false, "realtime.error");
    });
  }

  function showSnackbar(message, tone) {
    state.snackbar = {
      message: String(message || ""),
      tone: tone || "success"
    };
    if (snackbarTimer) clearTimeout(snackbarTimer);
    snackbarTimer = setTimeout(function () {
      state.snackbar = null;
      render();
    }, 2600);
    render();
  }

  function renderSnackbar() {
    if (!state.snackbar || !state.snackbar.message) return "";
    return [
      '<div class="snackbar ' + escapeHtml(state.snackbar.tone || "success") + '" role="status">',
      escapeHtml(state.snackbar.message),
      '</div>'
    ].join("");
  }

  function currentAppTheme() {
    var value = String((state.settings && state.settings.appTheme) || defaultSettings.appTheme || "light").toLowerCase();
    if (["light", "dark", "system"].indexOf(value) < 0) return "light";
    return value;
  }

  function resolvedAppTheme() {
    var theme = currentAppTheme();
    if (theme === "system" && window.matchMedia) {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    return theme;
  }

  function applyAppTheme() {
    var theme = resolvedAppTheme();
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-theme-setting", currentAppTheme());
  }

  function isStaticPreviewHost() {
    return window.location.protocol === "file:" || /\.github\.io$/i.test(window.location.hostname);
  }

  function staticLocalData(payload) {
    return payload && payload.localData && typeof payload.localData === "object" ? payload.localData : {};
  }

  function loadStaticBuildConfig() {
    if (!isStaticPreviewHost()) return Promise.resolve(null);
    if (state.staticBuildConfig) return Promise.resolve(state.staticBuildConfig);
    if (staticBuildConfigPromise) return staticBuildConfigPromise;
    staticBuildConfigPromise = requestJson("admin/config.json")
      .then(function (payload) {
        state.staticBuildConfig = payload;
        state.staticBuildConfigError = "";
        return payload;
      })
      .catch(function (error) {
        state.staticBuildConfigError = error.message || "정적 빌드 설정을 읽지 못했습니다.";
        return null;
      });
    return staticBuildConfigPromise;
  }

  function applyStaticBuildSettings(payload) {
    var localData = staticLocalData(payload);
    var settings = localData.settings && typeof localData.settings === "object" ? localData.settings : null;
    if (!settings) return;
    settings = settingsWithExplicitDataGaps(settings);
    state.settings = syncedModelAlertSettings(Object.assign({}, state.settings, settings));
    state.serverConfigured = localData.configured || {};
    state.serverSettingsLoaded = true;
    state.serverSettingsLocked = true;
    state.serverSettingsError = "";
    state.settingsSaved = true;
    persistSettings();
  }

  function applyStaticBuildAccounts(payload, forceDraft) {
    var localData = staticLocalData(payload);
    state.serviceAccounts = Array.isArray(localData.accounts) ? localData.accounts : [];
    state.serviceAccountsLoaded = true;
    syncActiveWatchAccountId();
    syncAccountDraftFromLoadedAccounts(Boolean(forceDraft));
  }

  function initialTab() {
    var params = new URLSearchParams(window.location.search);
    return normalizeTabId(params.get("tab"));
  }

  function initialNotificationSection() {
    var params = new URLSearchParams(window.location.search);
    if (String(params.get("tab") || "").toLowerCase() === "monitoring" && !params.get("notification")) {
      return "signals";
    }
    return normalizeNotificationSection(params.get("notification"));
  }

  function initialAccountSection() {
    var params = new URLSearchParams(window.location.search);
    return normalizeAccountSection(params.get("account"));
  }

  function initialStrategySection() {
    var params = new URLSearchParams(window.location.search);
    var requested = params.get("strategy");
    if (!requested && String(params.get("tab") || "").toLowerCase() === "ontology") {
      requested = params.get("ontology");
    }
    return normalizeStrategySection(requested);
  }

  function initialOntologySection() {
    var params = new URLSearchParams(window.location.search);
    return normalizeOntologySection(params.get("ontology"));
  }

  function normalizePageMode(value) {
    return String(value || "").toLowerCase() === "settings" ? "settings" : "results";
  }

  function pageSupportsMode(pageId) {
    return pageModeEnabledTabs.indexOf(normalizeTabId(pageId)) >= 0;
  }

  function sectionModeForPage(pageId, sectionId) {
    var config = pageModeSectionMap[normalizeTabId(pageId)];
    var section = String(sectionId || "").toLowerCase();
    if (!config) return "results";
    if ((config.settings || []).indexOf(section) >= 0) return "settings";
    return "results";
  }

  function initialPageModeForTab(pageId) {
    var params = new URLSearchParams(window.location.search);
    var explicit = params.get("mode");
    var normalized = normalizeTabId(pageId || params.get("tab"));
    if (explicit) return normalizePageMode(explicit);
    if (normalized === "accounts") return sectionModeForPage("accounts", initialAccountSection());
    if (normalized === "notifications") return sectionModeForPage("notifications", initialNotificationSection());
    if (normalized === "modeling") return sectionModeForPage("modeling", initialStrategySection());
    if (normalized === "settings") return "settings";
    return "results";
  }

  function initialPageViewModes() {
    var mode = initialPageModeForTab();
    var tab = initialTab();
    return {
      overview: "results",
      accounts: tab === "accounts" ? mode : "results",
      watchlist: "results",
      symbols: "results",
      notifications: tab === "notifications" ? mode : "results",
      modeling: tab === "modeling" ? mode : "results",
      experiments: "results",
      feed: tab === "feed" ? mode : "results",
      system: "results",
      settings: "settings"
    };
  }

  function activePageMode(pageId) {
    var normalized = normalizeTabId(pageId || state.activeTab);
    if (!pageSupportsMode(normalized)) return normalized === "settings" ? "settings" : "results";
    return normalizePageMode((state.pageViewModes || {})[normalized]);
  }

  function modeSectionsForPage(pageId, sections) {
    var normalized = normalizeTabId(pageId);
    var config = pageModeSectionMap[normalized];
    var mode = activePageMode(normalized);
    if (!config) return sections || [];
    var allowed = config[mode] || [];
    return (sections || []).filter(function (section) {
      return allowed.indexOf(section.id) >= 0;
    });
  }

  function activeSectionForPageMode(pageId, sections, current) {
    var visible = modeSectionsForPage(pageId, sections);
    var currentId = String(current || "").toLowerCase();
    if (visible.some(function (section) { return section.id === currentId; })) return currentId;
    return visible.length ? visible[0].id : currentId;
  }

  function firstSectionForPageMode(pageId, mode) {
    var config = pageModeSectionMap[normalizeTabId(pageId)] || {};
    var ids = config[normalizePageMode(mode)] || [];
    return ids[0] || "";
  }

  function setPageViewMode(pageId, mode) {
    var normalized = normalizeTabId(pageId || state.activeTab);
    if (!state.pageViewModes) state.pageViewModes = {};
    state.pageViewModes[normalized] = normalizePageMode(mode);
    var firstSection = firstSectionForPageMode(normalized, mode);
    if (normalized === "accounts" && firstSection) state.activeAccountSection = firstSection;
    if (normalized === "notifications" && firstSection) {
      state.activeNotificationSection = firstSection;
      state.notificationPolicyEditorOpen = false;
      state.notificationTemplateEditorOpen = false;
    }
    if (normalized === "modeling" && firstSection) state.activeStrategySection = firstSection;
  }

  function normalizeTabId(value) {
    var requested = String(value || "").toLowerCase();
    if (requested === "more") return "overview";
    if (requested === "monitoring") return "notifications";
    if (requested === "ontology" || requested === "relations") return "modeling";
    if (requested === "lab" || requested === "experiment") return "experiments";
    return tabs.some(function (tab) { return tab.id === requested; }) ? requested : "overview";
  }

  function normalizeNotificationSection(value) {
    var requested = String(value || "").toLowerCase();
    return notificationSections.some(function (section) { return section.id === requested; }) ? requested : "status";
  }

  function normalizeAccountSection(value) {
    var requested = String(value || "").toLowerCase();
    if (requested === "api" || requested === "source" || requested === "sources") return "connections";
    if (requested === "money" || requested === "portfolio" || requested === "audit") return "balance";
    if (requested === "settings" || requested === "form" || requested === "accounts") return "management";
    return accountSections.some(function (section) { return section.id === requested; }) ? requested : "overview";
  }

  function normalizeStrategySection(value) {
    var requested = String(value || "").toLowerCase();
    if (requested === "data" || requested === "cards" || requested === "card") return "evidence";
    if (requested === "policy" || requested === "rules" || requested === "prompts") return "registry";
    if (requested === "structure" || requested === "graph" || requested === "ontology") return "graphs";
    if (requested === "relations" || requested === "rules-trace") return "trace";
    return strategySections.some(function (section) { return section.id === requested; }) ? requested : "overview";
  }

  function normalizeOntologySection(value) {
    var requested = String(value || "").toLowerCase();
    if (requested === "graph") return "graphs";
    if (requested === "rules" || requested === "prompts") return "registry";
    if (requested === "relations" || requested === "rules-trace") return "trace";
    return ontologySections.some(function (section) { return section.id === requested; }) ? requested : "overview";
  }

  function normalizeOntologyGraphId(value) {
    var requested = String(value || "").toLowerCase().replace("-expanded", "");
    return requested === "tbox" || requested === "abox" ? requested : "";
  }

  function ontologyGraphDisplayMeta(graphId) {
    var normalized = normalizeOntologyGraphId(graphId);
    if (normalized === "abox") {
      return {
        title: "핵심 데이터 관계 그래프",
        eyebrow: "Data Relation Graph",
        description: "실제 데이터 중 AI 판단, 중요 변경, 알림 후보와 연결되는 관계를 큰 화면으로 확인합니다.",
        fitLabel: "데이터 관계 그래프 맞춤",
        layoutLabel: "데이터 관계 자동 배치"
      };
    }
    return {
      title: "전체 규칙 구조 그래프",
      eyebrow: "Rule Structure Graph",
      description: "TBox 분류, 관계 타입, 규칙 연결을 접지 않고 큰 화면으로 확인합니다.",
      fitLabel: "규칙 구조 그래프 맞춤",
      layoutLabel: "규칙 구조 자동 배치"
    };
  }

  function activeNotificationSectionMeta() {
    return notificationSections.filter(function (section) {
      return section.id === state.activeNotificationSection;
    })[0] || notificationSections[0];
  }

  function activeAccountSectionMeta() {
    return accountSections.filter(function (section) {
      return section.id === state.activeAccountSection;
    })[0] || accountSections[0];
  }

  function activeStrategySectionMeta() {
    return strategySections.filter(function (section) {
      return section.id === state.activeStrategySection;
    })[0] || strategySections[0];
  }

  function tabUrl(tab) {
    var normalized = normalizeTabId(tab);
    var params = new URLSearchParams(window.location.search);
    if (normalized !== "accounts") params.delete("account");
    if (normalized !== "notifications") params.delete("notification");
    if (normalized !== "modeling") params.delete("strategy");
    if (normalized !== "ontology") params.delete("ontology");
    params.delete("mode");
    if (normalized === "overview") {
      params.delete("tab");
    } else {
      params.set("tab", normalized);
    }
    var path = window.location.pathname || "/";
    var query = params.toString();
    var hash = window.location.hash || "";
    return path + (query ? "?" + query : "") + hash;
  }

  function accountSectionUrl(section) {
    var normalized = normalizeAccountSection(section);
    var params = new URLSearchParams(window.location.search);
    params.set("tab", "accounts");
    params.delete("notification");
    params.delete("strategy");
    params.delete("ontology");
    params.delete("mode");
    if (normalized === "overview") {
      params.delete("account");
    } else {
      params.set("account", normalized);
    }
    var path = window.location.pathname || "/";
    var query = params.toString();
    var hash = window.location.hash || "";
    return path + (query ? "?" + query : "") + hash;
  }

  function notificationSectionUrl(section) {
    var normalized = normalizeNotificationSection(section);
    var params = new URLSearchParams(window.location.search);
    params.set("tab", "notifications");
    params.delete("account");
    params.delete("strategy");
    params.delete("ontology");
    params.delete("mode");
    if (normalized === "status") {
      params.delete("notification");
    } else {
      params.set("notification", normalized);
    }
    var path = window.location.pathname || "/";
    var query = params.toString();
    var hash = window.location.hash || "";
    return path + (query ? "?" + query : "") + hash;
  }

  function strategySectionUrl(section) {
    var normalized = normalizeStrategySection(section);
    var params = new URLSearchParams(window.location.search);
    params.set("tab", "modeling");
    params.delete("account");
    params.delete("notification");
    params.delete("ontology");
    params.delete("mode");
    if (normalized === "overview") {
      params.delete("strategy");
    } else {
      params.set("strategy", normalized);
    }
    var path = window.location.pathname || "/";
    var query = params.toString();
    var hash = window.location.hash || "";
    return path + (query ? "?" + query : "") + hash;
  }

  function writeTabHistory(tab, replace) {
    if (!window.history) return;
    var method = replace ? "replaceState" : "pushState";
    if (!window.history[method]) return;
    var normalized = normalizeTabId(tab);
    window.history[method]({ tab: normalized }, "", tabUrl(normalized));
  }

  function writeNotificationSectionHistory(section) {
    if (!window.history || !window.history.replaceState) return;
    var normalized = normalizeNotificationSection(section);
    window.history.replaceState({ tab: "notifications", notification: normalized }, "", notificationSectionUrl(normalized));
  }

  function writeAccountSectionHistory(section) {
    if (!window.history || !window.history.replaceState) return;
    var normalized = normalizeAccountSection(section);
    window.history.replaceState({ tab: "accounts", account: normalized }, "", accountSectionUrl(normalized));
  }

  function writeStrategySectionHistory(section) {
    if (!window.history || !window.history.replaceState) return;
    var normalized = normalizeStrategySection(section);
    window.history.replaceState({ tab: "modeling", strategy: normalized }, "", strategySectionUrl(normalized));
  }

  function pageModeUrl(pageId, mode) {
    var params = new URLSearchParams(window.location.search);
    var normalized = normalizeTabId(pageId);
    params.set("tab", normalized);
    if (normalizePageMode(mode) === "settings") {
      params.set("mode", "settings");
    } else {
      params.delete("mode");
    }
    var path = window.location.pathname || "/";
    var query = params.toString();
    var hash = window.location.hash || "";
    return path + (query ? "?" + query : "") + hash;
  }

  function writePageModeHistory(pageId, mode) {
    if (!window.history || !window.history.replaceState) return;
    var normalized = normalizeTabId(pageId || state.activeTab);
    window.history.replaceState({ tab: normalized, mode: normalizePageMode(mode) }, "", pageModeUrl(normalized, mode));
  }

  function scrollKeyForTab(tab, notificationSection, strategySection, ontologySection, accountSection) {
    var normalized = normalizeTabId(tab || state.activeTab);
    if (normalized === "accounts") {
      return normalized + ":" + normalizeAccountSection(accountSection || state.activeAccountSection);
    }
    if (normalized === "notifications") {
      return normalized + ":" + normalizeNotificationSection(notificationSection || state.activeNotificationSection);
    }
    if (normalized === "modeling") {
      return normalized + ":" + normalizeStrategySection(strategySection || state.activeStrategySection);
    }
    if (normalized === "ontology") {
      return normalized + ":" + normalizeOntologySection(ontologySection || state.activeOntologySection);
    }
    return normalized;
  }

  function activeScrollKey() {
    return scrollKeyForTab(state.activeTab, state.activeNotificationSection, state.activeStrategySection, state.activeOntologySection, state.activeAccountSection);
  }

  function currentWorkspaceMain() {
    return app && app.querySelector ? app.querySelector(".workspace-main") : null;
  }

  function currentWorkspaceScroller() {
    var workspace = currentWorkspaceMain();
    if (!workspace) return null;
    var style = window.getComputedStyle ? window.getComputedStyle(workspace) : null;
    var overflowY = style ? String(style.overflowY || style.overflow || "") : "";
    if (overflowY === "visible" || overflowY === "clip") return null;
    if (overflowY === "auto" || overflowY === "scroll") return workspace;
    return workspace.scrollHeight > workspace.clientHeight + 1 ? workspace : null;
  }

  function scrollTopNumber(value) {
    var number = Number(value || 0);
    return isFinite(number) ? Math.max(0, number) : 0;
  }

  function windowScrollTop() {
    var doc = document.documentElement || {};
    var body = document.body || {};
    return scrollTopNumber(window.pageYOffset || doc.scrollTop || body.scrollTop || 0);
  }

  function maxWindowScrollTop() {
    var doc = document.documentElement || {};
    var body = document.body || {};
    var scrollHeight = Math.max(scrollTopNumber(doc.scrollHeight), scrollTopNumber(body.scrollHeight));
    return Math.max(0, scrollHeight - scrollTopNumber(window.innerHeight || doc.clientHeight || 0));
  }

  function clampScrollTop(value, max) {
    return Math.max(0, Math.min(scrollTopNumber(value), scrollTopNumber(max)));
  }

  function renderedScrollKey() {
    var workspace = currentWorkspaceMain();
    return workspace ? workspace.getAttribute("data-scroll-key") || "" : "";
  }

  function rememberRenderedPageScrollPosition() {
    var key = renderedScrollKey();
    if (!key) return;
    var scroller = currentWorkspaceScroller();
    state.tabScrollPositions[key] = {
      workspaceTop: scroller ? scrollTopNumber(scroller.scrollTop) : 0,
      windowTop: windowScrollTop()
    };
  }

  function restoreRenderedPageScrollPosition() {
    var key = renderedScrollKey() || activeScrollKey();
    var saved = state.tabScrollPositions[key] || {};
    var scroller = currentWorkspaceScroller();
    if (scroller) {
      var workspaceTop = scrollTopNumber(saved.workspaceTop) || scrollTopNumber(saved.windowTop);
      scroller.scrollTop = clampScrollTop(workspaceTop, Math.max(0, scroller.scrollHeight - scroller.clientHeight));
      if (window.scrollTo) window.scrollTo(0, 0);
      return;
    }
    if (window.scrollTo) {
      var windowTop = scrollTopNumber(saved.windowTop) || scrollTopNumber(saved.workspaceTop);
      window.scrollTo(0, clampScrollTop(windowTop, maxWindowScrollTop()));
    }
  }

  function bindPageScrollMemory() {
    var scroller = currentWorkspaceScroller();
    if (!scroller) return;
    scroller.addEventListener("scroll", function () {
      rememberRenderedPageScrollPosition();
      scheduleTopbarScrollState();
    }, { passive: true });
  }

  function currentShell() {
    return app && app.querySelector ? app.querySelector(".shell") : null;
  }

  function setTopbarCollapsed(collapsed) {
    topbarCollapsed = Boolean(collapsed);
    var shell = currentShell();
    if (!shell) return;
    shell.classList.toggle("topbar-collapsed", topbarCollapsed);
  }

  function syncTopbarScrollState() {
    var scroller = currentWorkspaceScroller();
    var scrollTop = scroller ? scrollTopNumber(scroller.scrollTop) : windowScrollTop();
    setTopbarCollapsed(scrollTop > 32);
  }

  function scheduleTopbarScrollState() {
    if (topbarScrollTicking) return;
    topbarScrollTicking = true;
    var frame = window.requestAnimationFrame || function (callback) {
      return window.setTimeout(callback, 16);
    };
    frame(function () {
      topbarScrollTicking = false;
      syncTopbarScrollState();
    });
  }

  function currentAppNav() {
    return app && app.querySelector ? app.querySelector(".app-nav") : null;
  }

  function closeAppNavMenu() {
    var menu = app && app.querySelector ? app.querySelector(".app-nav-menu") : null;
    if (menu) menu.open = false;
  }

  function setAppNavHidden(hidden) {
    appNavHidden = Boolean(hidden);
    var nav = currentAppNav();
    if (!nav) return;
    nav.classList.toggle("is-hidden", appNavHidden);
    if (appNavHidden) closeAppNavMenu();
  }

  function syncAppNavScrollState() {
    var scrollY = Math.max(0, Number(window.pageYOffset || document.documentElement.scrollTop || 0));
    var mobile = window.matchMedia ? window.matchMedia("(max-width: 860px)").matches : false;
    if (!mobile) {
      setAppNavHidden(false);
      appNavLastScrollY = scrollY;
      return;
    }
    var delta = scrollY - appNavLastScrollY;
    if (Math.abs(delta) > 3) closeAppNavMenu();
    if (scrollY > 120 && delta >= 0) {
      setAppNavHidden(true);
    } else if (scrollY > 72 && delta > 6) {
      setAppNavHidden(true);
    } else if (scrollY < 48 || delta < -8) {
      setAppNavHidden(false);
    } else {
      var nav = currentAppNav();
      if (nav) nav.classList.toggle("is-hidden", appNavHidden);
    }
    appNavLastScrollY = scrollY;
  }

  function scheduleAppNavScrollState() {
    if (appNavScrollTicking) return;
    appNavScrollTicking = true;
    var frame = window.requestAnimationFrame || function (callback) {
      return window.setTimeout(callback, 16);
    };
    frame(function () {
      appNavScrollTicking = false;
      syncAppNavScrollState();
    });
  }

  function currentTabBar() {
    return app && app.querySelector ? app.querySelector(".tab-bar") : null;
  }

  function rememberTabBarPosition() {
    var tabBar = currentTabBar();
    if (!tabBar) return;
    state.tabBarScrollLeft = Number(tabBar.scrollLeft || 0);
  }

  function restoreTabBarPosition() {
    var tabBar = currentTabBar();
    if (!tabBar || tabBar.scrollWidth <= tabBar.clientWidth) return;
    var maxScroll = Math.max(0, tabBar.scrollWidth - tabBar.clientWidth);
    var targetLeft = Math.max(0, Math.min(Number(state.tabBarScrollLeft || 0), maxScroll));
    var active = tabBar.querySelector("[aria-current='page']") || tabBar.querySelector(".active");
    tabBar.scrollLeft = targetLeft;
    if (active) {
      var padding = 8;
      var activeLeft = active.offsetLeft;
      var activeRight = activeLeft + active.offsetWidth;
      var visibleLeft = tabBar.scrollLeft;
      var visibleRight = visibleLeft + tabBar.clientWidth;
      if (activeLeft < visibleLeft + padding) {
        targetLeft = Math.max(0, activeLeft - padding);
      } else if (activeRight > visibleRight - padding) {
        targetLeft = Math.min(maxScroll, activeRight - tabBar.clientWidth + padding);
      }
      tabBar.scrollLeft = targetLeft;
    }
    state.tabBarScrollLeft = Number(tabBar.scrollLeft || 0);
  }

  function activePointerPoint(event) {
    var touch = event && event.changedTouches && event.changedTouches[0];
    var source = touch || event || {};
    return {
      x: Number(source.clientX || 0),
      y: Number(source.clientY || 0)
    };
  }

  function isTapMovement(start, event) {
    if (!start) return true;
    var point = activePointerPoint(event);
    return Math.abs(point.x - start.x) <= 12 && Math.abs(point.y - start.y) <= 12;
  }

  function stopActiveScrollMomentum() {
    var scroller = currentWorkspaceScroller();
    if (scroller) scroller.scrollTop = scrollTopNumber(scroller.scrollTop);
    if (window.scrollTo) window.scrollTo(0, windowScrollTop());
  }

  function activateTabButton(button, event) {
    var nextTab = button.getAttribute("data-tab") || "overview";
    if (nextTab === state.activeTab) return false;
    if (event && event.preventDefault) event.preventDefault();
    if (event && event.stopPropagation) event.stopPropagation();
    stopActiveScrollMomentum();
    closeAppNavMenu();
    navigateToTab(nextTab);
    return true;
  }

  function bindTabNavigation(button) {
    var pointerStart = null;
    var touchStart = null;
    var handledAt = 0;
    var markHandled = function () {
      handledAt = Date.now();
    };

    button.addEventListener("pointerdown", function (event) {
      pointerStart = activePointerPoint(event);
    }, { passive: true });
    button.addEventListener("pointercancel", function () {
      pointerStart = null;
    });
    button.addEventListener("pointerup", function (event) {
      if (!isTapMovement(pointerStart, event)) return;
      markHandled();
      activateTabButton(button, event);
    });
    button.addEventListener("touchstart", function (event) {
      touchStart = activePointerPoint(event);
    }, { passive: true });
    button.addEventListener("touchcancel", function () {
      touchStart = null;
    });
    button.addEventListener("touchend", function (event) {
      if (Date.now() - handledAt < 500) return;
      if (!isTapMovement(touchStart, event)) return;
      markHandled();
      activateTabButton(button, event);
    }, { passive: false });
    button.addEventListener("click", function (event) {
      if (Date.now() - handledAt < 700) {
        event.preventDefault();
        return;
      }
      activateTabButton(button, event);
    });
  }

  function navigateToTab(tab, options) {
    options = options || {};
    var nextTab = normalizeTabId(tab);
    if (nextTab === state.activeTab) return;
    rememberTabBarPosition();
    var priorTab = state.activeTab;
    state.activeTab = nextTab;
    if (nextTab !== "notifications") state.monitoringDetail = null;
    if (nextTab !== "notifications") state.notificationPolicyEditorOpen = false;
    if (nextTab !== "notifications") state.notificationTemplateEditorOpen = false;
    if (!options.skipPrevious) state.previousTab = priorTab;
    if (!options.skipHistory) writeTabHistory(nextTab, Boolean(options.replace));
    setAppNavHidden(false);
    render();
  }

  function syncTabFromLocation() {
    var nextTab = initialTab();
    var nextAccountSection = initialAccountSection();
    var nextNotificationSection = initialNotificationSection();
    var nextStrategySection = initialStrategySection();
    var nextOntologySection = initialOntologySection();
    var nextPageMode = initialPageModeForTab(nextTab);
    var accountSectionChanged = nextAccountSection !== state.activeAccountSection;
    var sectionChanged = nextNotificationSection !== state.activeNotificationSection;
    var strategySectionChanged = nextStrategySection !== state.activeStrategySection;
    var ontologySectionChanged = nextOntologySection !== state.activeOntologySection;
    var pageModeChanged = activePageMode(nextTab) !== nextPageMode;
    if (!state.pageViewModes) state.pageViewModes = {};
    state.pageViewModes[nextTab] = nextPageMode;
    state.activeAccountSection = nextAccountSection;
    state.activeNotificationSection = nextNotificationSection;
    state.activeStrategySection = nextStrategySection;
    state.activeOntologySection = nextOntologySection;
    if (nextTab !== "notifications" || sectionChanged) state.notificationPolicyEditorOpen = false;
    if (nextTab !== "notifications" || sectionChanged) state.notificationTemplateEditorOpen = false;
    if (nextTab === state.activeTab) {
      if (accountSectionChanged && nextTab === "accounts") render();
      if (sectionChanged && nextTab === "notifications") render();
      if (strategySectionChanged && nextTab === "modeling") render();
      if (ontologySectionChanged && nextTab === "ontology") render();
      if (pageModeChanged) render();
      return;
    }
    rememberTabBarPosition();
    state.previousTab = state.activeTab;
    state.activeTab = nextTab;
    if (nextTab !== "notifications") state.monitoringDetail = null;
    render();
  }

  function loadSettings() {
    try {
      var raw = readStoredSettings();
      return syncedModelAlertSettings(Object.assign({}, defaultSettings, raw ? JSON.parse(raw) : {}));
    } catch (error) {
      return syncedModelAlertSettings(Object.assign({}, defaultSettings));
    }
  }

  function settingsWithExplicitDataGaps(settings) {
    var next = Object.assign({}, settings || {});
    ["valuationAssumptions", "marketSignalInputs"].forEach(function (key) {
      if (!Object.prototype.hasOwnProperty.call(next, key)) next[key] = "";
    });
    return next;
  }

  function readStoredSettings() {
    try {
      var storage = window.localStorage;
      return storage ? storage.getItem("exitLensSettings") : settingsMemoryStore;
    } catch (error) {
      return settingsMemoryStore;
    }
  }

  function writeStoredSettings(payload) {
    settingsMemoryStore = payload;
    try {
      var storage = window.localStorage;
      if (storage) storage.setItem("exitLensSettings", payload);
      return true;
    } catch (error) {
      return true;
    }
  }

  function persistSettings() {
    state.settingsSaved = writeStoredSettings(JSON.stringify(state.settings));
    if (!state.settingsSaved) {
      state.error = "브라우저 저장소에 설정을 저장하지 못했습니다.";
    }
  }

  function applyServerSettings(payload) {
    var nextSettings = settingsWithExplicitDataGaps(payload.settings || {});
    state.settings = syncedModelAlertSettings(Object.assign({}, state.settings, nextSettings));
    state.serverConfigured = payload.configured || {};
    state.serverSettingsLocked = Boolean(payload.locked);
    state.serverSettingsLoaded = true;
    state.serverSettingsError = "";
    state.settingsSaved = true;
    persistSettings();
  }

  function defaultNotificationTemplates() {
    var richTemplate = "{telegramMessage}";
    var items = [
      {
        messageType: "default",
        template: richTemplate,
        description: "기본 알림 템플릿"
      }
    ];
    notificationPolicyCatalog().forEach(function (rule) {
      items.push({
        messageType: rule.key,
        template: richTemplate,
        description: rule.label + " · " + rule.description
      });
    });
    [
      { messageType: "modelReview", template: "{body}", description: "비동기 모델 리뷰 결과" },
      { messageType: "workHandoff", template: "{body}", description: "작업 완료 핸드오프" },
      { messageType: "notification", template: "{body}", description: "일반 텍스트 알림" }
    ].forEach(function (item) {
      items.push(item);
    });
    var seen = {};
    return items.filter(function (item) {
      if (!visibleNotificationTemplateType(item.messageType)) return false;
      if (seen[item.messageType]) return false;
      seen[item.messageType] = true;
      return true;
    }).map(function (item) {
      return Object.assign({ enabled: true, updatedAt: "" }, item);
    });
  }

  function defaultNotificationRuleConditionTypes() {
    return [
      { type: "text_contains_any", label: "메시지에 단어 포함" },
      { type: "context_contains_any", label: "정보 필드에 단어 포함" },
      { type: "context_equals", label: "정보 값 일치" },
      { type: "context_present", label: "정보 값 존재" },
      { type: "context_number_gte", label: "정보 숫자 이상" },
      { type: "context_number_lte", label: "정보 숫자 이하" },
      { type: "always", label: "항상 적용" }
    ];
  }

  function defaultNotificationRuleConditions() {
    return [
      { id: "severity_alert", label: "주의 등급", type: "context_equals", field: "severity", value: "ALERT", terms: [], score: 25, enabled: true },
      { id: "severity_watch", label: "관찰 등급", type: "context_equals", field: "severity", value: "WATCH", terms: [], score: 10, enabled: true },
      { id: "has_symbol", label: "종목 지정", type: "context_present", field: "symbol", value: "", terms: [], score: 10, enabled: true },
      {
        id: "important_terms",
        label: "핵심 투자 단어",
        type: "text_contains_any",
        field: "",
        value: "",
        terms: ["판단 변화", "모델 매수", "모델 매도", "손익률 급변", "평가액 급변", "보유 수량 변경", "새 보유", "이동평균", "신규 공시", "가격 변동", "크립토 변동", "거시 지표", "손절", "분할매도", "리스크"],
        score: 15,
        enabled: true
      },
      { id: "confirming_data", label: "확인 데이터 포함", type: "text_contains_any", field: "", value: "", terms: ["수급", "거래량", "투자자", "추세", "20일선", "60일선", "외국인", "기관"], score: 10, enabled: true },
      { id: "actionable_terms", label: "행동 필요 표현", type: "text_contains_any", field: "", value: "", terms: ["확인", "재확인", "점검", "기준", "후보", "검토"], score: 10, enabled: true },
      { id: "body_present", label: "본문 있음", type: "context_present", field: "body", value: "", terms: [], score: 5, enabled: true },
      { id: "status_noise", label: "상태성 노이즈", type: "text_contains_any", field: "", value: "", terms: ["정상 작동", "시세 대기", "현재가를 아직", "연결 확인 필요", "템플릿 테스트"], score: -25, enabled: true }
    ];
  }

  function defaultNotificationRuleBaseScore(messageType) {
    var type = String(messageType || "");
    var systemTypes = ["default", "modelReview", "workHandoff", "notification"];
    var highSignalTypes = [
      "investmentInsight", "modelBuy", "modelSell", "watchlistBuyCandidate", "holdingTiming", "monitorPositionChange", "monitorPnlChange", "monitorValueChange",
      "monitorTrendChange", "monitorCashChange", "monitorDecisionChange", "externalEquityMove", "externalCryptoMove",
      "externalMacroShift", "externalDartDisclosure"
    ];
    var lowSignalTypes = ["monitorHeartbeat", "watchlistQuotePending", "externalDataConnection"];
    if (systemTypes.indexOf(type) >= 0) return 85;
    if (highSignalTypes.indexOf(type) >= 0) return 35;
    if (lowSignalTypes.indexOf(type) >= 0) return 15;
    return 25;
  }

  function defaultNotificationRuleSimilarityEnabled(messageType) {
    return ["default", "modelReview", "workHandoff", "notification"].indexOf(String(messageType || "")) < 0;
  }

  function defaultNotificationRuleThreshold(messageType) {
    var type = String(messageType || "");
    if (["default", "modelReview", "workHandoff", "notification"].indexOf(type) >= 0) return 20;
    if (type === "investmentInsight") return 50;
    if (type === "externalEquityMove" || type === "externalCryptoMove") return 60;
    return 45;
  }

  function defaultNotificationRuleSimilarityWindow(messageType) {
    var type = String(messageType || "");
    if (type === "investmentInsight") return 180;
    if (["holdingTiming", "monitorHeartbeat", "externalEquityMove", "externalCryptoMove"].indexOf(type) >= 0) return 360;
    if (["watchlistQuotePending", "externalDataConnection"].indexOf(type) >= 0) return 180;
    if (["monitorPnlChange", "monitorValueChange", "monitorTrendChange", "monitorCashChange"].indexOf(type) >= 0) return 60;
    return 120;
  }

  function defaultNotificationRuleSimilarityPenalty(messageType) {
    var type = String(messageType || "");
    if (type === "investmentInsight") return -35;
    if (["externalEquityMove", "externalCryptoMove"].indexOf(type) >= 0) return -55;
    if (type === "holdingTiming" || type === "monitorHeartbeat") return -40;
    if (["watchlistQuotePending", "externalDataConnection"].indexOf(type) >= 0) return -30;
    return -20;
  }

  function defaultNotificationRuleSimilarityBypassDelta(messageType) {
    return ["investmentInsight", "modelBuy", "modelSell", "watchlistBuyCandidate", "monitorDecisionChange"].indexOf(String(messageType || "")) >= 0 ? 15 : 20;
  }

  function defaultNotificationRuleSimilarityBypassConditions(messageType) {
    var type = String(messageType || "");
    if (type === "externalEquityMove") {
      return [
        { id: "severity_upgrade", label: "등급 상승", type: "severity_upgrade", field: "", value: "", enabled: true, description: "관찰에서 주의처럼 중요도가 올라가면 반복이어도 보냅니다." },
        { id: "change_abs_delta", label: "변동률 추가 확대", type: "abs_number_delta_gte", field: "changePercent", value: 2, enabled: true, description: "이전 유사 알림보다 변동률 절대값이 기준 %p 이상 커지면 보냅니다." },
        { id: "volume_multiplier", label: "거래량 급증", type: "number_multiplier_gte", field: "volume", value: 1.5, enabled: true, description: "이전 유사 알림보다 거래량이 기준 배수 이상 커지면 보냅니다." }
      ];
    }
    if (type === "externalCryptoMove") {
      return [
        { id: "severity_upgrade", label: "등급 상승", type: "severity_upgrade", field: "", value: "", enabled: true, description: "관찰에서 주의처럼 중요도가 올라가면 반복이어도 보냅니다." },
        { id: "change_24h_abs_delta", label: "24시간 변동 확대", type: "abs_number_delta_gte", field: "change24h", value: 2, enabled: true, description: "이전 유사 알림보다 24시간 변동률 절대값이 기준 %p 이상 커지면 보냅니다." },
        { id: "change_7d_abs_delta", label: "7일 변동 확대", type: "abs_number_delta_gte", field: "change7d", value: 3, enabled: true, description: "이전 유사 알림보다 7일 변동률 절대값이 기준 %p 이상 커지면 보냅니다." },
        { id: "volume_multiplier", label: "거래액 급증", type: "number_multiplier_gte", field: "volume24h", value: 1.5, enabled: true, description: "이전 유사 알림보다 거래액이 기준 배수 이상 커지면 보냅니다." }
      ];
    }
    if (type === "holdingTiming") {
      return [
        { id: "severity_upgrade", label: "등급 상승", type: "severity_upgrade", field: "", value: "", enabled: true, description: "관찰에서 주의처럼 중요도가 올라가면 반복이어도 보냅니다." },
        { id: "holding_score_delta", label: "보유 모델 점수 변화", type: "abs_number_delta_gte", field: "holdingDecisionScore", value: 8, enabled: true, description: "이전 보유 타이밍 알림보다 모델 점수가 기준점 이상 달라지면 보냅니다." },
        { id: "loss_rate_worsened", label: "손익률 추가 악화", type: "number_delta_lte", field: "profitLossRate", value: 2, enabled: true, description: "이전 보유 타이밍 알림보다 손익률이 기준 %p 이상 나빠지면 보냅니다." }
      ];
    }
    return [];
  }

  function defaultNotificationRuleSimilarityFields() {
    return ["messageType", "accountId", "symbol", "severity", "title"];
  }

  function defaultNotificationRuleStateCooldownEnabled(messageType) {
    return ["investmentInsight", "holdingTiming", "externalEquityMove", "externalCryptoMove"].indexOf(String(messageType || "")) >= 0;
  }

  function defaultNotificationRuleStateCooldownMinutes(messageType) {
    return defaultNotificationRuleStateCooldownEnabled(messageType) ? 360 : 0;
  }

  function defaultMarketHoursSessions() {
    return [
      {
        market: "KR",
        label: "국장",
        timezone: "Asia/Seoul",
        openTime: "08:00",
        closeTime: "20:00",
        weekdays: [0, 1, 2, 3, 4],
        sessions: [
          { key: "pre", label: "프리마켓", openTime: "08:00", closeTime: "08:50" },
          { key: "regular", label: "정규장", openTime: "09:00", closeTime: "15:30" },
          { key: "after", label: "애프터마켓", openTime: "15:30", closeTime: "20:00" }
        ]
      },
      {
        market: "US",
        label: "미장",
        timezone: "America/New_York",
        openTime: "04:00",
        closeTime: "20:00",
        weekdays: [0, 1, 2, 3, 4],
        sessions: [
          { key: "pre", label: "프리마켓", openTime: "04:00", closeTime: "09:30" },
          { key: "regular", label: "정규장", openTime: "09:30", closeTime: "16:00" },
          { key: "after", label: "애프터마켓", openTime: "16:00", closeTime: "20:00" }
        ]
      }
    ];
  }

  function defaultNotificationRuleMarketHoursEnabled(messageType) {
    return [
      "modelBuy",
      "investmentInsight",
      "modelSell",
      "watchlistBuyCandidate",
      "watchlistQuote",
      "watchlistQuotePending",
      "holdingTiming",
      "monitorPositionChange",
      "monitorPnlChange",
      "monitorValueChange",
      "monitorTrendChange",
      "monitorDecisionChange",
      "externalEquityMove",
      "externalDartDisclosure"
    ].indexOf(String(messageType || "")) >= 0;
  }

  function defaultNotificationRuleMarketHoursMarkets(messageType) {
    var type = String(messageType || "");
    if (type === "externalEquityMove") return ["US"];
    if (type === "externalDartDisclosure") return ["KR"];
    return defaultNotificationRuleMarketHoursEnabled(type) ? ["KR", "US"] : [];
  }

  function defaultNotificationRule(messageType) {
    var type = String(messageType || "notification").trim() || "notification";
    return {
      messageType: type,
      enabled: true,
      threshold: defaultNotificationRuleThreshold(type),
      baseScore: defaultNotificationRuleBaseScore(type),
      lowScoreAction: "suppress",
      conditions: defaultNotificationRuleConditions().map(function (condition) {
        return Object.assign({}, condition, { terms: (condition.terms || []).slice() });
      }),
      similarityEnabled: defaultNotificationRuleSimilarityEnabled(type),
      similarityWindowMinutes: defaultNotificationRuleSimilarityWindow(type),
      similarityPenalty: defaultNotificationRuleSimilarityPenalty(type),
      similarityBypassScoreDelta: defaultNotificationRuleSimilarityBypassDelta(type),
      similarityBypassConditions: defaultNotificationRuleSimilarityBypassConditions(type),
      similarityFields: defaultNotificationRuleSimilarityFields(),
      stateCooldownEnabled: defaultNotificationRuleStateCooldownEnabled(type),
      stateCooldownMinutes: defaultNotificationRuleStateCooldownMinutes(type),
      marketHoursEnabled: defaultNotificationRuleMarketHoursEnabled(type),
      marketHoursMarkets: defaultNotificationRuleMarketHoursMarkets(type),
      updatedAt: ""
    };
  }

  function defaultNotificationRules() {
    var seen = {};
    var keys = [];
    defaultNotificationTemplates().forEach(function (item) {
      keys.push(item.messageType);
    });
    notificationPolicyCatalog().forEach(function (rule) {
      keys.push(rule.key);
    });
    return keys.filter(function (key) {
      if (!key || seen[key]) return false;
      seen[key] = true;
      return true;
    }).map(defaultNotificationRule);
  }

  function applyNotificationTemplates(payload) {
    state.notificationTemplates = Array.isArray(payload.templates) && payload.templates.length
      ? payload.templates
      : defaultNotificationTemplates();
    state.notificationTemplateVariables = Array.isArray(payload.variables) && payload.variables.length
      ? payload.variables
      : ["title", "statusHeadline", "titleHeadline", "telegramMessage", "readableMessage", "dataLines", "telegramDataLines", "triggerSummary", "triggerBlock", "criterionBlock", "criterionLines", "lines", "rawLines", "referenceDate", "eventGeneratedAt", "sentAt", "sentTime", "sentLine", "body", "messageType", "symbol", "rawSymbol", "symbolDisplayName", "severity", "metadata", "market", "changePercent", "change24h", "change7d", "price", "volume", "volume24h", "provider"];
    state.notificationTemplatesLoaded = true;
    state.notificationTemplatesLoading = false;
    state.notificationTemplatesError = "";
  }

  function loadNotificationTemplates() {
    state.notificationTemplatesLoading = true;
    state.notificationTemplatesError = "";
    if (isStaticPreviewHost()) {
      applyNotificationTemplates({ templates: defaultNotificationTemplates() });
      state.notificationTemplatesLoading = false;
      return Promise.resolve();
    }
    return requestJson("/api/notification-templates")
      .then(function (payload) {
        applyNotificationTemplates(payload);
      })
      .catch(function (error) {
        state.notificationTemplatesError = error.message || "알림 템플릿을 읽지 못했습니다.";
        state.notificationTemplates = defaultNotificationTemplates();
      })
      .finally(function () {
        state.notificationTemplatesLoading = false;
        if (state.snapshot) render();
      });
  }

  function applyNotificationRules(payload) {
    state.notificationRules = Array.isArray(payload.rules) && payload.rules.length
      ? payload.rules
      : defaultNotificationRules();
    state.notificationRuleConditionTypes = Array.isArray(payload.conditionTypes) && payload.conditionTypes.length
      ? payload.conditionTypes
      : defaultNotificationRuleConditionTypes();
    state.notificationMarketHoursSessions = Array.isArray(payload.marketHoursSessions) && payload.marketHoursSessions.length
      ? payload.marketHoursSessions
      : defaultMarketHoursSessions();
    state.notificationRulesLoaded = true;
    state.notificationRulesLoading = false;
    state.notificationRulesError = "";
  }

  function loadNotificationRules() {
    state.notificationRulesLoading = true;
    state.notificationRulesError = "";
    if (isStaticPreviewHost()) {
      applyNotificationRules({ rules: defaultNotificationRules(), conditionTypes: defaultNotificationRuleConditionTypes(), marketHoursSessions: defaultMarketHoursSessions() });
      state.notificationRulesLoading = false;
      return Promise.resolve();
    }
    return requestJson("/api/notification-rules")
      .then(function (payload) {
        applyNotificationRules(payload);
      })
      .catch(function (error) {
        state.notificationRulesError = error.message || "알림 룰을 읽지 못했습니다.";
        state.notificationRules = defaultNotificationRules();
        state.notificationRuleConditionTypes = defaultNotificationRuleConditionTypes();
        state.notificationMarketHoursSessions = defaultMarketHoursSessions();
      })
      .finally(function () {
        state.notificationRulesLoading = false;
        if (state.snapshot) render();
      });
  }

  function applyNotificationJobs(payload) {
    state.notificationJobItems = Array.isArray(payload.jobs) ? payload.jobs : [];
    var visibleJobs = {};
    state.notificationJobItems.forEach(function (job) {
      visibleJobs[notificationJobKey(job)] = true;
    });
    Object.keys(state.notificationExpandedJobs || {}).forEach(function (key) {
      if (!visibleJobs[key]) delete state.notificationExpandedJobs[key];
    });
    if (state.activeNotificationJobKey && !visibleJobs[state.activeNotificationJobKey]) {
      state.activeNotificationJobKey = "";
    }
    if (!state.activeNotificationJobKey && state.notificationJobItems.length) {
      state.activeNotificationJobKey = notificationJobKey(state.notificationJobItems[0]);
    }
    state.notificationJobsSummary = payload.summary && typeof payload.summary === "object" ? payload.summary : {};
    state.notificationJobDiagnostics = payload.diagnostics && typeof payload.diagnostics === "object" ? payload.diagnostics : {};
    state.notificationJobsLoaded = true;
    state.notificationJobsLoading = false;
    state.notificationJobsError = "";
    if (Object.keys(state.notificationJobsSummary).length) {
      state.realtime.notificationJobs = state.notificationJobsSummary;
    }
  }

  function loadNotificationJobs() {
    state.notificationJobsLoading = true;
    state.notificationJobsError = "";
    if (isStaticPreviewHost()) {
      applyNotificationJobs({ jobs: [], summary: {} });
      state.notificationJobsLoading = false;
      return Promise.resolve();
    }
    return requestJson("/api/notification-jobs?limit=40")
      .then(function (payload) {
        applyNotificationJobs(payload);
      })
      .catch(function (error) {
        if (String(error.message || "").indexOf("API를 찾지 못했습니다") >= 0) {
          applyNotificationJobs({ jobs: [], summary: {} });
          return;
        }
        state.notificationJobsError = error.message || "최근 알림 판단을 읽지 못했습니다.";
        state.notificationJobItems = [];
        state.notificationJobDiagnostics = {};
      })
      .finally(function () {
        state.notificationJobsLoading = false;
        if (state.snapshot) render();
      });
  }

  function applyNotificationSchedules(payload) {
    state.messageSchedules = Array.isArray(payload.schedules) ? payload.schedules : [];
    state.messageSchedulesLoaded = true;
    state.messageSchedulesLoading = false;
    state.messageSchedulesError = "";
  }

  function loadNotificationSchedules() {
    state.messageSchedulesLoading = true;
    state.messageSchedulesError = "";
    if (isStaticPreviewHost()) {
      applyNotificationSchedules({ schedules: [] });
      state.messageSchedulesLoading = false;
      return Promise.resolve();
    }
    return requestJson("/api/notification-schedules")
      .then(function (payload) {
        applyNotificationSchedules(payload);
      })
      .catch(function (error) {
        state.messageSchedulesError = error.message || "메시지 스케줄을 읽지 못했습니다.";
        state.messageSchedules = [];
      })
      .finally(function () {
        state.messageSchedulesLoading = false;
        if (state.snapshot) render();
      });
  }

  function messageScheduleByType(messageType) {
    return (state.messageSchedules || []).filter(function (item) {
      return item.messageType === messageType;
    })[0] || null;
  }

  function notificationTemplateByType(messageType) {
    return (state.notificationTemplates || []).filter(function (item) {
      return item.messageType === messageType;
    })[0] || null;
  }

  function isAlertTemplateType(messageType) {
    return alertRuleCatalog.some(function (rule) { return rule.key === messageType; });
  }

  function notificationTemplateForEdit(messageType) {
    var found = notificationTemplateByType(messageType);
    if (found) return found;
    return defaultNotificationTemplates().filter(function (item) {
      return item.messageType === messageType;
    })[0] || {
      messageType: messageType,
      template: "{telegramMessage}",
      description: "",
      enabled: true,
      updatedAt: ""
    };
  }

  function notificationTemplateVariables() {
    return state.notificationTemplateVariables.length
      ? state.notificationTemplateVariables
      : ["title", "statusHeadline", "titleHeadline", "telegramMessage", "readableMessage", "dataLines", "telegramDataLines", "triggerSummary", "triggerBlock", "criterionBlock", "criterionLines", "lines", "rawLines", "referenceDate", "eventGeneratedAt", "sentAt", "sentTime", "sentLine", "body", "messageType", "symbol", "rawSymbol", "symbolDisplayName", "severity", "metadata", "market", "changePercent", "change24h", "change7d", "price", "volume", "volume24h", "provider"];
  }

  function clampInteger(value, min, max, fallback) {
    var parsed = parseInt(String(value === 0 ? "0" : value || "").replace(/,/g, ""), 10);
    if (!Number.isFinite(parsed)) parsed = fallback;
    return Math.max(min, Math.min(max, parsed));
  }

  function notificationRuleByType(messageType) {
    return (state.notificationRules || []).filter(function (item) {
      return item.messageType === messageType;
    })[0] || null;
  }

  function normalizeNotificationRule(rule) {
    var normalized = Object.assign(defaultNotificationRule(rule && rule.messageType), rule || {});
    normalized.threshold = clampInteger(normalized.threshold, 0, 100, defaultNotificationRuleThreshold(normalized.messageType));
    normalized.baseScore = clampInteger(normalized.baseScore, 0, 100, defaultNotificationRuleBaseScore(normalized.messageType));
    normalized.enabled = normalized.enabled !== false;
    normalized.lowScoreAction = normalized.lowScoreAction || "suppress";
    normalized.similarityEnabled = normalized.similarityEnabled !== false;
    normalized.similarityWindowMinutes = clampInteger(normalized.similarityWindowMinutes, 0, 10080, defaultNotificationRuleSimilarityWindow(normalized.messageType));
    normalized.similarityPenalty = clampInteger(normalized.similarityPenalty, -100, 0, defaultNotificationRuleSimilarityPenalty(normalized.messageType));
    normalized.similarityBypassScoreDelta = clampInteger(normalized.similarityBypassScoreDelta, 0, 100, defaultNotificationRuleSimilarityBypassDelta(normalized.messageType));
    normalized.similarityBypassConditions = Array.isArray(normalized.similarityBypassConditions) && normalized.similarityBypassConditions.length
      ? normalized.similarityBypassConditions.map(function (condition) {
        return Object.assign({
          id: "",
          label: "",
          type: "abs_number_delta_gte",
          field: "",
          value: "",
          enabled: true,
          description: ""
        }, condition, {
          enabled: condition.enabled !== false
        });
      })
      : defaultNotificationRuleSimilarityBypassConditions(normalized.messageType);
    normalized.similarityFields = Array.isArray(normalized.similarityFields)
      ? normalized.similarityFields.map(function (field) { return String(field || "").trim(); }).filter(Boolean)
      : String(normalized.similarityFields || "").split(",").map(function (field) { return field.trim(); }).filter(Boolean);
    if (!normalized.similarityFields.length) normalized.similarityFields = defaultNotificationRuleSimilarityFields();
    normalized.stateCooldownEnabled = normalized.stateCooldownEnabled !== false;
    normalized.stateCooldownMinutes = clampInteger(normalized.stateCooldownMinutes, 0, 10080, defaultNotificationRuleStateCooldownMinutes(normalized.messageType));
    normalized.marketHoursEnabled = normalized.marketHoursEnabled !== false;
    normalized.marketHoursMarkets = Array.isArray(normalized.marketHoursMarkets)
      ? normalized.marketHoursMarkets.map(function (market) { return String(market || "").trim().toUpperCase(); }).filter(Boolean)
      : String(normalized.marketHoursMarkets || "").split(",").map(function (market) { return market.trim().toUpperCase(); }).filter(Boolean);
    if (!normalized.marketHoursMarkets.length && defaultNotificationRuleMarketHoursEnabled(normalized.messageType)) {
      normalized.marketHoursMarkets = defaultNotificationRuleMarketHoursMarkets(normalized.messageType);
    }
    normalized.conditions = Array.isArray(normalized.conditions) && normalized.conditions.length
      ? normalized.conditions.map(function (condition) {
        return Object.assign({
          id: "",
          label: "",
          type: "text_contains_any",
          field: "",
          value: "",
          terms: [],
          score: 0,
          enabled: true
        }, condition, {
          terms: Array.isArray(condition.terms) ? condition.terms.slice() : String(condition.terms || "").split(",").map(function (term) { return term.trim(); }).filter(Boolean),
          score: clampInteger(condition.score, -100, 100, 0),
          enabled: condition.enabled !== false
        });
      })
      : defaultNotificationRuleConditions();
    return normalized;
  }

  function notificationRuleForEdit(messageType) {
    return normalizeNotificationRule(notificationRuleByType(messageType) || defaultNotificationRule(messageType));
  }

  function ensureNotificationRule(messageType) {
    var existing = notificationRuleByType(messageType);
    if (existing) return existing;
    existing = defaultNotificationRule(messageType);
    state.notificationRules = (state.notificationRules || []).concat(existing);
    return existing;
  }

  function notificationRuleCondition(rule, conditionId) {
    return (rule.conditions || []).filter(function (condition) {
      return condition.id === conditionId;
    })[0] || null;
  }

  function notificationRuleBypassCondition(rule, conditionId) {
    return (rule.similarityBypassConditions || []).filter(function (condition) {
      return condition.id === conditionId;
    })[0] || null;
  }

  function updateNotificationRuleField(messageType, field, value) {
    var rule = ensureNotificationRule(messageType);
    if (field === "enabled") {
      rule.enabled = Boolean(value);
    } else if (field === "threshold") {
      rule.threshold = clampInteger(value, 0, 100, defaultNotificationRuleThreshold(messageType));
    } else if (field === "baseScore") {
      rule.baseScore = clampInteger(value, 0, 100, defaultNotificationRuleBaseScore(messageType));
    } else if (field === "lowScoreAction") {
      rule.lowScoreAction = String(value || "suppress");
    } else if (field === "similarityEnabled") {
      rule.similarityEnabled = Boolean(value);
    } else if (field === "similarityWindowMinutes") {
      rule.similarityWindowMinutes = clampInteger(value, 0, 10080, defaultNotificationRuleSimilarityWindow(messageType));
    } else if (field === "similarityPenalty") {
      rule.similarityPenalty = clampInteger(value, -100, 0, defaultNotificationRuleSimilarityPenalty(messageType));
    } else if (field === "similarityBypassScoreDelta") {
      rule.similarityBypassScoreDelta = clampInteger(value, 0, 100, defaultNotificationRuleSimilarityBypassDelta(messageType));
    } else if (field === "similarityFields") {
      rule.similarityFields = String(value || "").split(",").map(function (item) { return item.trim(); }).filter(Boolean);
    } else if (field === "stateCooldownEnabled") {
      rule.stateCooldownEnabled = Boolean(value);
    } else if (field === "stateCooldownMinutes") {
      rule.stateCooldownMinutes = clampInteger(value, 0, 10080, defaultNotificationRuleStateCooldownMinutes(messageType));
    } else if (field === "marketHoursEnabled") {
      rule.marketHoursEnabled = Boolean(value);
    } else if (field === "marketHoursMarkets") {
      rule.marketHoursMarkets = Array.isArray(value)
        ? value.map(function (item) { return String(item || "").trim().toUpperCase(); }).filter(Boolean)
        : String(value || "").split(",").map(function (item) { return item.trim().toUpperCase(); }).filter(Boolean);
    }
    state.notificationRulesSaved = false;
    state.notificationRulesError = "";
  }

  function updateNotificationRuleMarket(messageType, market, enabled) {
    var rule = ensureNotificationRule(messageType);
    var key = String(market || "").trim().toUpperCase();
    if (!key) return;
    var markets = Array.isArray(rule.marketHoursMarkets) ? rule.marketHoursMarkets.slice() : defaultNotificationRuleMarketHoursMarkets(messageType);
    markets = markets.map(function (item) { return String(item || "").trim().toUpperCase(); }).filter(Boolean);
    if (enabled && markets.indexOf(key) < 0) markets.push(key);
    if (!enabled) markets = markets.filter(function (item) { return item !== key; });
    rule.marketHoursMarkets = markets;
    state.notificationRulesSaved = false;
    state.notificationRulesError = "";
  }

  function updateNotificationRuleCondition(messageType, conditionId, field, value) {
    var rule = ensureNotificationRule(messageType);
    var condition = notificationRuleCondition(rule, conditionId);
    if (!condition) return;
    if (field === "enabled") {
      condition.enabled = Boolean(value);
    } else if (field === "score") {
      condition.score = clampInteger(value, -100, 100, 0);
    } else if (field === "field") {
      condition.field = String(value || "").trim();
    } else if (field === "value") {
      if (condition.type === "text_contains_any" || condition.type === "context_contains_any") {
        condition.terms = String(value || "").split(",").map(function (term) { return term.trim(); }).filter(Boolean);
      } else {
        condition.value = String(value || "");
      }
    }
    state.notificationRulesSaved = false;
    state.notificationRulesError = "";
  }

  function updateNotificationRuleBypassCondition(messageType, conditionId, field, value) {
    var rule = ensureNotificationRule(messageType);
    if (!Array.isArray(rule.similarityBypassConditions)) {
      rule.similarityBypassConditions = defaultNotificationRuleSimilarityBypassConditions(messageType);
    }
    var condition = notificationRuleBypassCondition(rule, conditionId);
    if (!condition) return;
    if (field === "enabled") {
      condition.enabled = Boolean(value);
    } else if (field === "field") {
      condition.field = String(value || "").trim();
    } else if (field === "value") {
      condition.value = String(value || "").trim();
    }
    state.notificationRulesSaved = false;
    state.notificationRulesError = "";
  }

  function updateNotificationTemplate(messageType, value) {
    var existing = notificationTemplateByType(messageType);
    if (!existing) {
      existing = notificationTemplateForEdit(messageType);
      state.notificationTemplates = (state.notificationTemplates || []).concat(existing);
    }
    existing.template = value;
    state.notificationTemplatesSaved = false;
    state.notificationTemplatesError = "";
  }

  function notificationTemplatePreviewContext(messageType) {
    var dataLabelPrefixes = [
      "미장 가격 변동",
      "크립토 변동",
      "비트코인 변동",
      "크립토 가격",
      "크립토 거래액",
      "매수 판단",
      "매도 판단",
      "모델 매수 점수",
      "모델 매도 점수",
      "적정가 대비",
      "24h 거래액",
      "현재가",
      "평단가",
      "수익률",
      "기준일",
      "발송시각",
      "연속 실패",
      "실패 단계",
      "재시도",
      "투자자",
      "기울기",
      "권장 액션",
      "거래량",
      "거래액",
      "가격",
      "수급",
      "추세",
      "출처",
      "이전",
      "현재",
      "변화",
      "상태",
      "손익",
      "평가",
      "보유",
      "신호"
    ];
    var dataLabelOrder = {
      "상태": 10,
      "연속 실패": 11,
      "실패 단계": 12,
      "재시도": 13,
      "손익": 20,
      "미장 가격 변동": 20,
      "현재가": 21,
      "평단가": 22,
      "수익률": 23,
      "매수 판단": 25,
      "매도 판단": 26,
      "수급": 30,
      "추세": 40,
      "권장 액션": 41,
      "기울기": 45,
      "투자자": 50,
      "신호": 60,
      "비트코인 변동": 70,
      "크립토 변동": 71,
      "크립토 가격": 72,
      "크립토 거래액": 73,
      "출처": 88,
      "기준일": 89,
      "발송시각": 90
    };
    var separateDataLabels = {
      "상태": true,
      "연속 실패": true,
      "실패 단계": true,
      "재시도": true,
      "손익": true,
      "미장 가격 변동": true,
      "현재가": true,
      "평단가": true,
      "수익률": true,
      "매수 판단": true,
      "매도 판단": true,
      "수급": true,
      "추세": true,
      "권장 액션": true,
      "기울기": true,
      "투자자": true,
      "신호": true,
      "비트코인 변동": true,
      "크립토 변동": true,
      "크립토 가격": true,
      "크립토 거래액": true,
      "출처": true,
      "기준일": true,
      "발송시각": true,
      "평가": true,
      "보유": true
    };
    function previewReferenceDate() {
      var now = new Date();
      var shifted = new Date(now.getTime() + 9 * 60 * 60000);
      function pad(value) {
        return String(value).padStart(2, "0");
      }
      return shifted.getUTCFullYear() + "-" + pad(shifted.getUTCMonth() + 1) + "-" + pad(shifted.getUTCDate()) + " " + pad(shifted.getUTCHours()) + ":" + pad(shifted.getUTCMinutes()) + " KST";
    }
    function previewSentTime() {
      return previewReferenceDate();
    }
    function plainBullet(text) {
      var cleaned = String(text || "").trim();
      return cleaned ? "• " + cleaned : "";
    }
    function htmlBullet(text) {
      var cleaned = String(text || "").trim();
      return cleaned ? "• " + escapeHtml(cleaned) : "";
    }
    function splitLabelValue(text) {
      var cleaned = String(text || "").trim();
      var marker = cleaned.indexOf(": ");
      if (marker > 0 && marker <= 18) {
        return {
          label: cleaned.slice(0, marker).trim(),
          value: cleaned.slice(marker + 2).trim()
        };
      }
      return { label: "", value: cleaned };
    }
    function criterionRow(text, rich) {
      var pair = splitLabelValue(text);
      if (pair.label && pair.value) {
        if (rich) return "• <b>" + escapeHtml(pair.label) + "</b>: <code>" + escapeHtml(pair.value) + "</code>";
        return "• " + pair.label + ": " + pair.value;
      }
      return rich ? htmlBullet(text) : plainBullet(text);
    }
    function criterionRows(items, rich) {
      return (items || []).map(function (item) { return criterionRow(item, rich); }).filter(Boolean).join("\n");
    }
    function splitDataLine(line) {
      var text = String(line || "").trim();
      for (var index = 0; index < dataLabelPrefixes.length; index += 1) {
        var label = dataLabelPrefixes[index];
        var colonPrefix = label + ": ";
        if (text.indexOf(colonPrefix) === 0) {
          var colonValue = text.slice(colonPrefix.length).trim();
          if (colonValue) return { label: label, value: colonValue };
        }
        var prefix = label + " ";
        if (text.indexOf(prefix) === 0) {
          var value = text.slice(prefix.length).trim();
          if (value) return { label: label, value: value };
        }
      }
      return { label: "", value: text };
    }
    function dataValue(rawItems, label) {
      for (var index = 0; index < (rawItems || []).length; index += 1) {
        var pair = splitDataLine(rawItems[index]);
        if (pair.label === label && pair.value) return pair.value;
      }
      return "";
    }
    function signedDirection(value) {
      var match = String(value || "").match(/([+-])\s*\d/);
      if (!match) return 0;
      return match[1] === "+" ? 1 : -1;
    }
    function dominantSignedDirection(value) {
      var text = String(value || "");
      var regex = /([+-])\s*(\d+(?:\.\d+)?)/g;
      var match;
      var dominant = 0;
      while ((match = regex.exec(text)) !== null) {
        var sign = match[1] === "-" ? -1 : 1;
        var numeric = parseFloat(match[2]);
        if (!Number.isNaN(numeric) && Math.abs(numeric) > Math.abs(dominant)) {
          dominant = sign * numeric;
        }
      }
      if (dominant > 0) return 1;
      if (dominant < 0) return -1;
      return 0;
    }
    function titleFromChange(value, positive, negative, neutral) {
      var direction = dominantSignedDirection(value);
      if (direction > 0) return positive;
      if (direction < 0) return negative;
      return neutral;
    }
    function firstDataText(rawItems, pattern) {
      var regex = new RegExp(pattern);
      for (var index = 0; index < (rawItems || []).length; index += 1) {
        var text = String(rawItems[index] || "").trim();
        if (regex.test(text)) return text;
      }
      return "";
    }
    function percentText(value) {
      var text = String(value || "").trim();
      var match = text.match(/[-+]?\d+(?:\.\d+)?%/);
      return match ? match[0] : text;
    }
    function notificationTitleIcon(type, rawItems, sample) {
      var status = dataValue(rawItems, "상태");
      var profit = dataValue(rawItems, "손익") || dataValue(rawItems, "수익률");
      var change = dataValue(rawItems, "변화");
      var signal = dataValue(rawItems, "신호");
      var titleText = String(sample && sample.title || "");
      if (type === "modelBuy" || type === "watchlistBuyCandidate") return "🟢";
      if (type === "investmentInsight") {
        var insightTypeIcon = dataValue(rawItems, "인사이트 유형");
        var actionIcon = dataValue(rawItems, "권장 액션");
        var insightBlobIcon = [status, profit, actionIcon, insightTypeIcon, titleText].join(" ");
        if (/손절|손실|축소/.test(insightBlobIcon)) return "🛡️";
        if (/분할|익절|수익|리밸런싱/.test(insightBlobIcon)) return "💰";
        if (/매수|기회/.test(insightBlobIcon)) return "🟢";
        if (insightBlobIcon.indexOf("외부") >= 0) return "🌐";
        return "🧭";
      }
      if (type === "modelSell") return "🔴";
      if (type === "holdingTiming") {
        var statusBlob = [status, profit, titleText].join(" ");
        if (/손절|손실/.test(statusBlob) || signedDirection(profit) < 0) return "🛡️";
        if (/분할|익절|수익/.test(statusBlob)) return "💰";
        return "⚖️";
      }
      if (type === "monitorPnlChange") {
        var pnlDirection = dominantSignedDirection(change);
        return pnlDirection > 0 ? "📈" : pnlDirection < 0 ? "📉" : "📊";
      }
      if (type === "monitorValueChange") return dominantSignedDirection(change) < 0 ? "💸" : "💵";
      if (type === "monitorTrendChange") {
        if (signal.indexOf("하향") >= 0 || signal.indexOf("이탈") >= 0) return "📉";
        if (signal.indexOf("상향") >= 0 || signal.indexOf("돌파") >= 0) return "📈";
        return "📊";
      }
      if (type === "monitorDecisionChange") {
        var current = dataValue(rawItems, "현재");
        if (/손절|손실/.test(current)) return "🛡️";
        if (/분할|익절|수익/.test(current)) return "💰";
        if (current.indexOf("리밸런싱") >= 0) return "⚖️";
        return "🔁";
      }
      return notificationMessageTypeIcon(type);
    }
    function notificationTitleHeadline(type, rawItems, sample, fallback) {
      var status = dataValue(rawItems, "상태");
      var profit = dataValue(rawItems, "손익") || dataValue(rawItems, "수익률");
      var change = dataValue(rawItems, "변화");
      var signal = dataValue(rawItems, "신호");
      var titleText = String(sample && sample.title || "");
      var symbol = String(sample && sample.symbol || "").toUpperCase();
      if (type === "investmentInsight") {
        var insightType = dataValue(rawItems, "인사이트 유형");
        var action = dataValue(rawItems, "권장 액션");
        var insightBlob = [status, profit, action, insightType, dataValue(rawItems, "핵심 결론"), titleText].join(" ");
        var profitText = percentText(profit);
        if (/손절|손실|축소/.test(insightBlob)) return (profitText && signedDirection(profit) < 0 ? "손실 " + profitText + ": " : "") + "손절·분할축소 점검";
        if (/분할|익절|수익|리밸런싱/.test(insightBlob)) return (profitText && signedDirection(profit) > 0 ? "수익 " + profitText + ": " : "") + "분할매도·리밸런싱 점검";
        if (/매수|기회/.test(insightBlob)) return "매수 후보: 진입 조건 점검";
        if (insightBlob.indexOf("외부") >= 0) return "외부 신호: 보유 영향 점검";
        return insightType ? insightType + ": 대응 기준 점검" : "투자 인사이트: 대응 기준 점검";
      }
      if (type === "modelBuy" || type === "watchlistBuyCandidate") return "매수 후보 감지";
      if (type === "modelSell") return "매도 기준 점검";
      if (type === "watchlistQuote") return "관심종목 시세 갱신";
      if (type === "watchlistQuotePending") return "관심종목 시세 미수집";
      if (type === "holdingTiming") {
        var statusBlob = [status, profit, titleText].join(" ");
        var profitText = percentText(profit);
        if (/손절|손실/.test(statusBlob) || signedDirection(profit) < 0) return (profitText ? "손실 " + profitText + ": " : "") + "손절·분할축소 권장";
        if (/분할|익절|수익/.test(statusBlob)) return (profitText ? "수익 " + profitText + ": " : "") + "분할매도 권장";
        if (statusBlob.indexOf("조건부") >= 0) return "조건부 보유: 추가매수 보류";
        return "보유 판단: 유지·대기";
      }
      if (type === "monitorHeartbeat") return "모니터링 상태 확인";
      if (type === "monitorConnection") {
        var connectionBlob = [status, (rawItems || []).slice(0, 2).join(" ")].join(" ").toLowerCase();
        if (/실패|오류|unauthorized|forbidden|timeout|error/.test(connectionBlob)) return "토스 연결 오류";
        return "토스 연결 상태 변경";
      }
      if (type === "monitorPositionChange") {
        var body = (rawItems || []).join(" ");
        if (body.indexOf("신규") >= 0) return "신규 보유 감지";
        if (/제외|청산|매도 완료/.test(body)) return "보유 제외 감지";
        return "보유 수량 변경";
      }
      if (type === "monitorPnlChange") return titleFromChange(change, "손익률 개선", "손익률 악화", "손익률 변화");
      if (type === "monitorValueChange") return titleFromChange(change, "평가액 증가", "평가액 감소", "평가액 변화");
      if (type === "monitorTrendChange") {
        if (signal.indexOf("하향") >= 0 || signal.indexOf("이탈") >= 0) return "이동평균 하향 신호";
        if (signal.indexOf("상향") >= 0 || signal.indexOf("돌파") >= 0) return "이동평균 상향 신호";
        return "이동평균·추세 신호";
      }
      if (type === "monitorCashChange") return titleFromChange(change, "현금 비중 증가", "현금 비중 감소", "현금 비중 변화");
      if (type === "monitorDecisionChange") {
        var current = dataValue(rawItems, "현재");
        if (/손절|손실/.test(current)) return "판단 변경: 손절·분할축소 권장";
        if (/분할|익절|수익/.test(current)) return "판단 변경: 분할매도 권장";
        if (current.indexOf("리밸런싱") >= 0) return "판단 변경: 리밸런싱 권장";
        if (current.indexOf("보유") >= 0) return "판단 변경: 보유 유지";
        return "판단 변경: 대응 액션 변경";
      }
      if (type === "externalEquityMove") return titleFromChange(dataValue(rawItems, "미장 가격 변동"), "미장 가격 급등", "미장 가격 급락", "미장 가격·거래량 급변");
      if (type === "externalCryptoMove") {
        var cryptoModel = sample && sample.cryptoMoveModel && typeof sample.cryptoMoveModel === "object" ? sample.cryptoMoveModel : {};
        var modelTitle = String(cryptoModel.titleLabel || sample && sample.cryptoMoveTitle || "").trim();
        if (modelTitle) return modelTitle;
        var cryptoLine = firstDataText(rawItems, "(비트코인|크립토).*?(24h|7d)");
        var asset = cryptoLine.indexOf("비트코인") >= 0 || symbol === "BTC" ? "비트코인" : "크립토";
        return titleFromChange(cryptoLine, asset + " 가격 급등", asset + " 가격 급락", asset + " 가격 급변");
      }
      if (type === "externalMacroShift") return "금리·거시 지표 변화";
      if (type === "externalDartDisclosure") return "국내 공시 감지";
      if (type === "externalDataConnection") return rawItems && rawItems[0] ? String(rawItems[0]).trim() + " 연결 점검" : "외부 API 연결 점검";
      return fallback || titleText || type;
    }
    function groupedDataRows(items) {
      var rows = [];
      for (var index = 0; index < items.length; index += 2) {
        rows.push("• " + items.slice(index, index + 2).join(", "));
      }
      return rows;
    }
    function orderedDataEntries(rawItems) {
      return rawItems.map(function (line, index) {
        var pair = splitDataLine(line);
        if (pair.label && pair.value) {
          return {
            kind: "pair",
            label: pair.label,
            value: pair.value,
            index: index,
            order: Object.prototype.hasOwnProperty.call(dataLabelOrder, pair.label) ? dataLabelOrder[pair.label] : 100 + index
          };
        }
        return {
          kind: "text",
          text: String(line || "").trim(),
          index: index,
          order: 100 + index
        };
      }).sort(function (a, b) {
        if (a.order !== b.order) return a.order - b.order;
        return a.index - b.index;
      });
    }
    function dataPairText(label, value, rich) {
      if (rich) return "<b>" + escapeHtml(label) + "</b>: <code>" + escapeHtml(value) + "</code>";
      return label + ": " + value;
    }
    function formattedDataRows(rawItems, rich) {
      var rows = [];
      var pairs = [];
      function flushPairs() {
        if (pairs.length) {
          rows = rows.concat(groupedDataRows(pairs));
          pairs = [];
        }
      }
      orderedDataEntries(rawItems).forEach(function (entry) {
        if (entry.kind === "pair") {
          var pairText = dataPairText(entry.label, entry.value, rich);
          if (separateDataLabels[entry.label]) {
            flushPairs();
            rows.push("• " + pairText);
          } else {
            pairs.push(pairText);
          }
        } else {
          flushPairs();
          rows.push(rich ? htmlBullet(entry.text) : plainBullet(entry.text));
        }
      });
      flushPairs();
      return rows.filter(Boolean).join("\n");
    }
    function plainDataRows(rawItems) {
      return formattedDataRows(rawItems, false);
    }
    function htmlDataRows(rawItems) {
      return formattedDataRows(rawItems, true);
    }
    function criterionLinesForSample(sample, type, triggerSummary, rawItems) {
      if (Array.isArray(sample.criteria) && sample.criteria.length) return sample.criteria.slice();
      var detected = "";
      if (type === "monitorPnlChange" || type === "monitorValueChange" || type === "monitorCashChange") {
        detected = rawItems.filter(function (line) { return /^변화\s/.test(line) || /^이전\s/.test(line) || /^현재\s/.test(line); }).join(", ");
      } else if (type === "monitorTrendChange") {
        detected = rawItems.filter(function (line) { return /^신호\s/.test(line) || /^추세[:\s]/.test(line); }).join(", ");
      } else if (type === "externalEquityMove") {
        detected = rawItems.filter(function (line) { return /^미장 가격 변동\s/.test(line) || /^현재가[:\s]/.test(line) || /^가격\s/.test(line); }).join(", ");
      } else if (rawItems.length) {
        detected = rawItems[0];
      }
      return ["설정: " + triggerSummary].concat(detected ? ["감지: " + detected] : []);
    }
    var type = messageType || "monitorHeartbeat";
    var samples = {
      default: {
        title: "삼성전자 관찰 알림",
        symbol: "005930",
        severity: "WATCH",
        lines: ["현재가 71,000원", "관찰 기준 유지", "다음 장에서 수급 재확인"],
        criteria: ["설정: 알림 조건이 실제 데이터에서 충족될 때", "감지: 현재가 71,000원"]
      },
      investmentInsight: {
        title: "삼성전자",
        symbol: "005930",
        severity: "WATCH",
        lines: ["인사이트 유형: 리스크 관리", "상태: 분할매도 권장 (77.6점)", "현재가: 101,300원", "평단가: 90,200원", "수익률: +12.2%", "수급: 거래량 1,200,000(1.4x), 거래액 1216억 원", "추세: 20일선보다 6.5% 낮음, 60일선보다 30.7% 낮음", "권장 액션: 분할매도·리밸런싱 기준 점검", "핵심 결론: 보유 판단과 외부 신호가 리스크 관리 쪽으로 기울었습니다.", "근거 신호: 보유 타이밍, 판단 변화, 거시 지표 변화", "다음 확인: 손절/분할축소 기준과 다음 조회 유지 여부를 확인하세요."],
        criteria: ["설정: 온톨로지 관계 그래프에서 의미 있는 투자 인사이트가 생성될 때", "감지: 보유 타이밍, 판단 변화 · 관계 강도 72점 · 신뢰도 81%"]
      },
      modelBuy: {
        title: "삼성전자 매수 후보",
        symbol: "005930",
        severity: "WATCH",
        lines: ["매수 판단 매수 후보 (78점)", "현재가: 71,000원", "평단가: 74,000원", "수익률: -4.1%", "적정가 대비 -12.4%", "거래량과 이동평균이 기준 이상"],
        criteria: ["설정: 모델 매수 기준 매수 후보 이상 (74점)", "감지: 매수 후보 (78점)"]
      },
      modelSell: {
        title: "엔비디아 분할매도 검토",
        symbol: "NVDA",
        severity: "ALERT",
        lines: ["매도 판단 분할매도 압력 (74점)", "현재가: $180", "평단가: $142", "수익률: +26.8%", "목표 수익률 도달", "20일선 이탈 여부 확인"],
        criteria: ["설정: 모델 매도 기준 분할매도 압력 이상 (72점)", "감지: 분할매도 압력 (74점)"]
      },
      watchlistBuyCandidate: {
        title: "Apple 관심종목 매수 후보",
        symbol: "AAPL",
        severity: "WATCH",
        lines: ["관심종목 매수 후보 (78점)", "관심 종목", "현재 $185", "거래량과 이동평균이 기준 이상"],
        criteria: ["설정: 관심종목 매수 기준 이상 (74점)", "감지: 매수 후보 (78점)"]
      },
      watchlistQuote: {
        title: "엔비디아",
        symbol: "NVDA",
        severity: "WATCH",
        lines: ["관심종목 시세 수집", "현재 $180", "20일선 $172(+4.7%)", "관심종목 알림 기준과 매수 후보를 확인하세요."],
        criteria: ["설정: 관심종목 가격 변화율 ±3% 이상", "감지: 현재가 $180 수집"]
      },
      watchlistQuotePending: {
        title: "카카오",
        symbol: "035720",
        severity: "INFO",
        lines: ["관심종목 시세 대기", "현재가를 아직 받지 못했습니다.", "토스 candles 응답, 종목 코드, 허용 IP를 확인하세요."],
        criteria: ["설정: 관심종목 현재가가 아직 수집되지 않았을 때", "감지: 현재가 없음"]
      },
      holdingTiming: {
        title: "SK하이닉스 매수·매도 타이밍",
        symbol: "000660",
        severity: "WATCH",
        lines: ["상태 조건부 보유 (52점)", "현재가: 150,000원", "평단가: 155,000원", "수익률: -3.2%", "추세: 현재 150,000원, 20일선 144,000원(+4.2%)", "수급: 거래량 31,000(1.7x), 거래액 48억 원", "투자자: 외국인 +22,000, 기관 -8,000", "권장 액션: 보유 유지, 추가매수는 새 매수 신호가 뜰 때까지 보류"],
        criteria: ["설정: 판단 상태가 위험/주의이거나 손익률이 -8% 이하일 때", "감지: 상태 조건부 보유 (52점), 수익률 -3.2%"]
      },
      monitorHeartbeat: {
        title: "실시간 모니터링",
        symbol: "",
        severity: "INFO",
        lines: ["모니터링 정상 작동", "상태 토스 계좌 동기화", "보유 5개", "평가 5,081만 원"],
        criteria: ["설정: 실시간 모니터링 워커 생존 확인 주기", "감지: 상태 토스 계좌 동기화, 보유 5개"]
      },
      monitorConnection: {
        title: "연결 상태 변화",
        symbol: "",
        severity: "WATCH",
        lines: ["상태 일시 인증 실패", "연속 실패 1회", "실패 단계 accounts", "재시도 access token 재발급 1회", "토스 조회 실패 · Toss accounts 단계 실패 · HTTP 401 Unauthorized"],
        criteria: ["설정: 토스 연결 모드가 live가 아니며 1회성 실패는 관찰로 보냅니다", "감지: 연속 실패 1회, stage=accounts, mode=demo"]
      },
      monitorPositionChange: {
        title: "SK하이닉스",
        symbol: "000660",
        severity: "WATCH",
        lines: ["보유 수량 변경", "이전 4주", "현재 5주", "현재가: 223,000원", "평단가: 257,500원", "수익률: -13.4%", "평가액 1,114만 원"],
        criteria: ["설정: 직전 스냅샷 대비 보유 수량이 달라졌을 때", "감지: 이전 4주, 현재 5주"]
      },
      monitorPnlChange: {
        title: "SK하이닉스",
        symbol: "000660",
        severity: "WATCH",
        lines: ["손익률 급변", "이전 -16.3%", "현재 -13.3%", "변화 +3.0%p", "현재가: 223,000원", "평단가: 257,500원", "수익률: -13.3%"],
        criteria: ["설정: 손익률 변화폭 ±2%p 이상", "감지: 변화 +3.0%p, 이전 -16.3%, 현재 -13.3%"]
      },
      monitorValueChange: {
        title: "SK하이닉스",
        symbol: "000660",
        severity: "WATCH",
        lines: ["평가액 급변", "이전 1,051만 원", "현재 1,114만 원", "변화 +6.0%", "현재가: 223,000원", "평단가: 257,500원", "수익률: -13.3%"],
        criteria: ["설정: 평가액 변화율 ±5% 이상", "감지: 변화 +6.0%, 이전 1,051만 원, 현재 1,114만 원"]
      },
      monitorTrendChange: {
        title: "SK하이닉스",
        symbol: "000660",
        severity: "ALERT",
        lines: ["이동평균 변화", "현재가: 150,000원", "평단가: 155,000원", "수익률: -3.2%", "신호 20일선 하향 이탈 · 60일선 상향 돌파", "추세: 20일선 144,000원보다 4.2% 높음, 60일선 137,500원보다 9.1% 높음", "수급: 거래량 31,000(1.7x), 거래액 48억 원", "투자자: 외국인 +22,000, 기관 -8,000"],
        criteria: ["설정: 20일/60일 이동평균 돌파, 크로스, 또는 현재가가 이동평균보다 8% 이상 높거나 낮을 때", "감지: 신호 20일선 하향 이탈 · 60일선 상향 돌파"]
      },
      monitorCashChange: {
        title: "현금비중",
        symbol: "",
        severity: "ALERT",
        lines: ["한국장", "이전 +12.0%", "현재 +1.0%", "변화 -11.0%p"],
        criteria: ["설정: 시장별 현금 비중 변화폭 ±10%p 이상", "감지: 변화 -11.0%p, 이전 +12.0%, 현재 +1.0%"]
      },
      monitorDecisionChange: {
        title: "SK하이닉스",
        symbol: "000660",
        severity: "WATCH",
        lines: ["판단 변화", "이전 위험 관찰 (36점)", "현재 조건부 보유 (52점)", "현재가: 150,000원", "평단가: 155,000원", "수익률: -3.2%", "권장 액션: 보유 유지, 추가매수는 새 매수 신호가 뜰 때까지 보류", "Codex 답변: 판단명이 바뀌어 재검토 필요"],
        criteria: ["설정: 판단 이름 변경 또는 위험 점수 변화 15점 이상", "감지: 이전 위험 관찰 (36점), 현재 조건부 보유 (52점)"]
      },
      externalEquityMove: {
        title: "미국 주식 변동",
        symbol: "AAPL",
        severity: "WATCH",
        lines: ["미장 가격 변동 +3.1%", "현재가: $180", "평단가: $155", "수익률: +16.1%", "거래량 58,000,000", "출처 Alpha Vantage"],
        criteria: ["설정: 미장 가격 변동률 ±3% 이상", "감지: 가격 변동 +3.1%, 현재가 $180"]
      },
      externalCryptoMove: {
        title: "크립토 변동",
        symbol: "BTC",
        severity: "WATCH",
        lines: ["비트코인 변동 24h +4.5% · 7d +11.2%", "크립토 가격 $61,227", "크립토 거래액 $42,000,000,000", "MSTR 등 비트코인 민감 종목 점검"],
        criteria: ["설정: 크립토 24h ±4% 또는 7d ±10% 이상", "감지: 크립토 변동 모델 100점, 7일 +11.2% (기준 ±4%), 24시간 +4.5%, 7일 +11.2%"],
        cryptoMoveTitle: "비트코인 가격 급등",
        cryptoMoveScore: 100,
        cryptoMoveDirection: "상승",
        cryptoMoveDominantPeriod: "7일",
        cryptoMoveDominantChange: 11.2,
        cryptoMoveReason: "7일 변동률 +11.2%가 기준 ±4%를 넘어서 비트코인 가격 급등으로 판단했습니다.",
        cryptoMoveModel: { titleLabel: "비트코인 가격 급등", score: 100, dominantPeriodLabel: "7일", dominantChange: 11.2, reason: "7일 변동률 +11.2%가 기준 ±4%를 넘어서 비트코인 가격 급등으로 판단했습니다." }
      },
      externalMacroShift: {
        title: "매크로 지표 변화",
        symbol: "DGS10",
        severity: "WATCH",
        lines: ["FRED 금리/스프레드 변화", "DGS10 4.35% (+25bp)", "10Y-2Y 0.4% (+30bp)", "성장주 할인율 재점검"],
        criteria: ["설정: FRED 금리 또는 10Y-2Y 스프레드 변화 ±15bp 이상", "감지: DGS10 +25bp, 10Y-2Y +30bp"]
      },
      externalDartDisclosure: {
        title: "국내 공시 감지",
        symbol: "005930",
        severity: "INFO",
        lines: ["신규 공시 감지", "단일판매·공급계약", "현재가: 71,000원", "평단가: 74,000원", "수익률: -4.1%", "접수일 20260701", "출처 OpenDART"],
        criteria: ["설정: OpenDART 접수번호가 직전 조회와 다를 때", "감지: 단일판매·공급계약, 접수일 20260701"]
      },
      externalDataConnection: {
        title: "외부 데이터 연결 상태",
        symbol: "",
        severity: "INFO",
        lines: ["FRED", "응답 지연", "키/호출 제한/응답 형식 확인"],
        criteria: ["설정: 외부 데이터 API 응답 오류, 호출 제한, 또는 응답 형식 문제가 감지될 때", "감지: FRED - 응답 지연"]
      },
      modelReview: {
        title: "내 매매 모델 점검",
        symbol: "005930",
        severity: "INFO",
        body: "내 매매 모델 점검\n- 매수 후보 2개\n- 분할매도 검토 1개\n- 기준값 변경 전후를 비교하세요."
      },
      workHandoff: {
        title: "작업 완료 핸드오프",
        symbol: "",
        severity: "INFO",
        body: "작업 완료 핸드오프\n- 커밋 abc1234\n- npm test 통과\n- origin/main push 완료"
      },
      notification: {
        title: "일반 알림",
        symbol: "",
        severity: "INFO",
        body: "일반 알림\n- 테스트 메시지입니다.\n- 템플릿 변경 후 발송 포맷을 확인하세요."
      }
    };
    var sample = samples[type] || samples.default;
    var rawItems = Array.isArray(sample.lines) ? sample.lines.filter(function (line) { return String(line || "").trim(); }) : [];
    var hasReferenceDate = rawItems.some(function (line) { return splitDataLine(line).label === "기준일"; });
    var referenceDate = sample.referenceDate || previewReferenceDate();
    if (!hasReferenceDate && referenceDate) rawItems.push("기준일 " + referenceDate);
    var sentTime = sample.sentTime || previewSentTime();
    if (type === "holdingTiming" && !rawItems.some(function (line) { return splitDataLine(line).label === "발송시각"; }) && sentTime) {
      rawItems.push("발송시각 " + sentTime);
    }
    var rawLines = rawItems.join("\n");
    var lines = rawItems.map(function (line) { return "- " + line; }).join("\n");
    var bulletLines = rawItems.map(plainBullet).join("\n");
    var rule = alertRuleCatalog.filter(function (item) { return item.key === type; })[0] || {};
    var messageTypeLabel = rule.label || notificationTemplateLabel(type);
    var severityLabel = sample.severity === "ALERT" ? "주의" : sample.severity === "WATCH" ? "관찰" : "정보";
    var displaySymbol = sample.symbol ? stockDisplayName(sample.symbol, sample) : "";
    var symbolLine = displaySymbol ? "종목: " + displaySymbol : "";
    var typeLine = messageTypeLabel ? "유형: " + messageTypeLabel : "";
    var severityLine = severityLabel ? "상태: " + severityLabel : "";
    var triggerSummary = rule.description ? rule.description : "조건이 실제 데이터에서 충족될 때 보냅니다.";
    var triggerLine = triggerSummary ? "발생 조건: " + triggerSummary : "";
    var criterionLines = criterionLinesForSample(sample, type, triggerSummary, rawItems);
    var dataLines = lines;
    var statusHeadline = severityLabel ? "[" + severityLabel + "]" : "";
    var titleIcon = notificationTitleIcon(type, rawItems, sample);
    var titleHeadline = notificationTitleHeadline(type, rawItems, sample, messageTypeLabel || sample.title);
    var headline = [statusHeadline, titleIcon, titleHeadline].filter(Boolean).join(" ");
    var targetValue = sample.title || displaySymbol || "";
    if (displaySymbol && sample.symbol && targetValue.toUpperCase() === String(sample.symbol || "").toUpperCase()) {
      targetValue = displaySymbol;
    }
    if (displaySymbol && targetValue && targetValue.indexOf(displaySymbol) < 0) {
      targetValue += " / " + displaySymbol;
    }
    var targetLine = targetValue ? "대상: " + targetValue : "";
    var triggerBlockRows = criterionRows(criterionLines, false);
    var triggerBlock = triggerBlockRows ? "발송 기준\n" + triggerBlockRows : "";
    var dataRows = plainDataRows(rawItems);
    var dataBlock = dataRows ? "데이터\n" + dataRows : "";
    var divider = "";
    var readableMessage = [
      headline,
      targetValue,
      "",
      dataBlock,
      triggerBlock ? "" : "",
      triggerBlock
    ].filter(function (line, index, list) {
      if (line === "") return index > 0 && list[index - 1] !== "";
      return String(line || "").trim();
    }).join("\n").trim();
    var telegramDataLines = htmlDataRows(rawItems);
    var telegramMessage = [
      "<b>" + escapeHtml(headline) + "</b>",
      targetValue ? "<code>" + escapeHtml(targetValue) + "</code>" : "",
      "",
      telegramDataLines ? "<b>데이터</b>" : "",
      telegramDataLines,
      criterionLines.length ? "" : "",
      criterionLines.length ? "<b>발송 기준</b>" : "",
      criterionRows(criterionLines, true)
    ].filter(function (line, index, list) {
      if (line === "") return index > 0 && list[index - 1] !== "";
      return String(line || "").trim();
    }).join("\n").trim();
    var body = sample.body || readableMessage || [sample.title, lines].filter(Boolean).join("\n");
    return {
      title: sample.title || samples.default.title,
      lines: lines,
      rawLines: rawLines,
      referenceDate: referenceDate,
      eventGeneratedAt: referenceDate,
      sentAt: sentTime,
      sentTime: sentTime,
      sentLine: "발송시각 " + sentTime,
      dataLines: dataLines,
      bulletLines: bulletLines,
      body: body,
      readableMessage: readableMessage,
      telegramMessage: telegramMessage,
      telegramDataLines: telegramDataLines,
      messageType: type,
      messageTypeLabel: messageTypeLabel,
      symbol: displaySymbol || sample.symbol || "",
      rawSymbol: sample.symbol || "",
      symbolDisplayName: displaySymbol,
      headline: headline,
      statusHeadline: statusHeadline,
      titleIcon: titleIcon,
      titleHeadline: titleHeadline,
      targetLine: targetLine,
      triggerBlock: triggerBlock,
      criterionBlock: triggerBlock,
      criterionLines: criterionLines.join("\n"),
      dataBlock: dataBlock,
      divider: divider,
      symbolLine: symbolLine,
      severity: sample.severity || "INFO",
      severityLabel: severityLabel,
      severityLine: severityLine,
      rule: type,
      typeLine: typeLine,
      triggerSummary: triggerSummary,
      triggerLine: triggerLine,
      key: type + ":preview",
      target: targetValue || type,
      rawTarget: sample.symbol || type,
      accountLabel: "기본 계정",
      accountId: "default",
      cryptoMoveModel: sample.cryptoMoveModel || "",
      cryptoMoveScore: sample.cryptoMoveScore || "",
      cryptoMoveDirection: sample.cryptoMoveDirection || "",
      cryptoMoveDominantPeriod: sample.cryptoMoveDominantPeriod || "",
      cryptoMoveDominantChange: sample.cryptoMoveDominantChange || "",
      cryptoMoveTitle: sample.cryptoMoveTitle || "",
      cryptoMoveReason: sample.cryptoMoveReason || ""
    };
  }

  function renderNotificationTemplatePreviewText(template, messageType) {
    var context = notificationTemplatePreviewContext(messageType);
    var rendered = String(template || "").replace(/\{([A-Za-z0-9_]+)\}/g, function (match, key) {
      return Object.prototype.hasOwnProperty.call(context, key) ? String(context[key]) : match;
    }).trim();
    var compacted = [];
    var previousBlank = false;
    rendered.split(/\r?\n/).forEach(function (line) {
      var cleaned = line.replace(/\s+$/, "");
      if (cleaned.trim()) {
        compacted.push(cleaned);
        previousBlank = false;
      } else if (compacted.length && !previousBlank) {
        compacted.push("");
        previousBlank = true;
      }
    });
    while (compacted.length && !compacted[compacted.length - 1].trim()) compacted.pop();
    return compacted.join("\n") || "(빈 메시지)";
  }

  function saveNotificationTemplate(messageType) {
    if (isStaticPreviewHost() || state.serverSettingsLocked) {
      state.notificationTemplatesError = "공유 모드에서는 알림 템플릿을 변경할 수 없습니다.";
      render();
      return Promise.resolve();
    }
    var item = notificationTemplateByType(messageType);
    if (!item) return Promise.resolve();
    state.notificationTemplatesLoading = true;
    state.notificationTemplatesError = "";
    render();
    return sendJson("/api/notification-templates", "PUT", item)
      .then(function (payload) {
        var saved = payload.template || item;
        state.notificationTemplates = (state.notificationTemplates || []).map(function (current) {
          return current.messageType === saved.messageType ? saved : current;
        });
        state.notificationTemplatesSaved = true;
        showSnackbar("알림 템플릿을 저장했습니다.");
      })
      .catch(function (error) {
        state.notificationTemplatesError = error.message || "알림 템플릿을 저장하지 못했습니다.";
        showSnackbar(state.notificationTemplatesError, "danger");
      })
      .finally(function () {
        state.notificationTemplatesLoading = false;
        render();
      });
  }

  function resetNotificationTemplate(messageType) {
    if (isStaticPreviewHost() || state.serverSettingsLocked) {
      state.notificationTemplatesError = "공유 모드에서는 알림 템플릿을 변경할 수 없습니다.";
      render();
      return Promise.resolve();
    }
    state.notificationTemplatesLoading = true;
    state.notificationTemplatesError = "";
    render();
    return sendJson("/api/notification-templates/" + encodeURIComponent(messageType), "DELETE", {})
      .then(function (payload) {
        var saved = payload.template;
        if (saved) {
          state.notificationTemplates = (state.notificationTemplates || []).map(function (current) {
            return current.messageType === saved.messageType ? saved : current;
          });
        }
        state.notificationTemplatesSaved = true;
        showSnackbar("알림 템플릿을 기본값으로 되돌렸습니다.");
      })
      .catch(function (error) {
        state.notificationTemplatesError = error.message || "알림 템플릿을 초기화하지 못했습니다.";
        showSnackbar(state.notificationTemplatesError, "danger");
      })
      .finally(function () {
        state.notificationTemplatesLoading = false;
        render();
      });
  }

  function saveNotificationRule(messageType) {
    if (isStaticPreviewHost() || state.serverSettingsLocked) {
      state.notificationRulesError = "공유 모드에서는 알림 룰을 변경할 수 없습니다.";
      render();
      return Promise.resolve();
    }
    var item = normalizeNotificationRule(ensureNotificationRule(messageType));
    state.notificationRulesLoading = true;
    state.notificationRulesError = "";
    render();
    return sendJson("/api/notification-rules", "PUT", item)
      .then(function (payload) {
        var saved = payload.rule || item;
        state.notificationRules = (state.notificationRules || []).map(function (current) {
          return current.messageType === saved.messageType ? saved : current;
        });
        if (!notificationRuleByType(saved.messageType)) {
          state.notificationRules = (state.notificationRules || []).concat(saved);
        }
        state.notificationRulesSaved = true;
        showSnackbar("알림 룰을 저장했습니다.");
      })
      .catch(function (error) {
        state.notificationRulesError = error.message || "알림 룰을 저장하지 못했습니다.";
        showSnackbar(state.notificationRulesError, "danger");
      })
      .finally(function () {
        state.notificationRulesLoading = false;
        render();
      });
  }

  function resetNotificationRule(messageType) {
    if (isStaticPreviewHost() || state.serverSettingsLocked) {
      state.notificationRulesError = "공유 모드에서는 알림 룰을 변경할 수 없습니다.";
      render();
      return Promise.resolve();
    }
    state.notificationRulesLoading = true;
    state.notificationRulesError = "";
    render();
    return sendJson("/api/notification-rules/" + encodeURIComponent(messageType), "DELETE", {})
      .then(function (payload) {
        var saved = payload.rule;
        if (saved) {
          var replaced = false;
          state.notificationRules = (state.notificationRules || []).map(function (current) {
            if (current.messageType === saved.messageType) {
              replaced = true;
              return saved;
            }
            return current;
          });
          if (!replaced) state.notificationRules = (state.notificationRules || []).concat(saved);
        }
        state.notificationRulesSaved = true;
        showSnackbar("알림 룰을 기본값으로 되돌렸습니다.");
      })
      .catch(function (error) {
        state.notificationRulesError = error.message || "알림 룰을 초기화하지 못했습니다.";
        showSnackbar(state.notificationRulesError, "danger");
      })
      .finally(function () {
        state.notificationRulesLoading = false;
        render();
      });
  }

  function canSendNotificationTemplateTest(messageType) {
    return alertRuleCatalog.some(function (rule) { return rule.key === messageType; });
  }

  function sendNotificationTemplateTest(messageType) {
    if (!canSendNotificationTemplateTest(messageType)) {
      showSnackbar("실제 데이터 발송은 알림 이벤트 타입에서만 가능합니다.", "danger");
      return Promise.resolve();
    }
    if (isStaticPreviewHost() || state.serverSettingsLocked) {
      state.notificationTemplatesError = "공유 모드에서는 실제 알림을 발송할 수 없습니다.";
      showSnackbar(state.notificationTemplatesError, "danger");
      render();
      return Promise.resolve();
    }
    state.notificationTemplateSending = messageType;
    state.notificationTemplatesError = "";
    render();
    var directInvestmentTest = messageType === "investmentInsight";
    return sendJson("/api/notification-templates/test-send", "POST", { messageType: messageType, bypassPolicy: directInvestmentTest })
      .then(function (payload) {
        var event = payload.event || {};
        if (payload.suppressed) {
          showSnackbar(payload.error || ("발송 우선도 " + (payload.score || 0) + "/" + (payload.threshold || 0) + "로 발송하지 않았습니다."), "danger");
        } else if (payload.delivered) {
          showSnackbar("테스트 알림을 실제 발송했습니다: " + (event.title || notificationTemplateLabel(messageType)));
        } else {
          showSnackbar("알림 발송 요청을 큐에 적재했습니다: " + (event.title || notificationTemplateLabel(messageType)));
        }
        return Promise.all([loadNotificationSchedules(), loadNotificationJobs()]);
      })
      .catch(function (error) {
        state.notificationTemplatesError = error.message || "실제 데이터 알림을 보내지 못했습니다.";
        showSnackbar(state.notificationTemplatesError, "danger");
      })
      .finally(function () {
        state.notificationTemplateSending = "";
        render();
      });
  }

  function textValueUnlessBoolean(value) {
    return typeof value === "boolean" ? "" : String(value || "");
  }

  function messageDeliveryLevelOptions() {
    return [
      { value: "absoluteBeginner", label: "왕초보", description: "전문 용어 없이 지금 확인할 행동만 짧게" },
      { value: "beginner", label: "초보", description: "핵심 수치와 쉬운 이유 중심" },
      { value: "intermediate", label: "중수", description: "가격·수급·추세·부족 데이터를 균형 있게" },
      { value: "advanced", label: "고수", description: "관계 규칙과 검증 근거까지 자세히" }
    ];
  }

  function normalizeMessageDeliveryLevel(value) {
    var text = String(value || "").trim();
    var aliases = {
      "왕초보": "absoluteBeginner",
      "absolute_beginner": "absoluteBeginner",
      "absolute-beginner": "absoluteBeginner",
      "초보": "beginner",
      "중수": "intermediate",
      "고수": "advanced"
    };
    var normalized = aliases[text] || text;
    return messageDeliveryLevelOptions().some(function (item) { return item.value === normalized; }) ? normalized : "absoluteBeginner";
  }

  function messageDeliveryLevelLabel(value) {
    var level = normalizeMessageDeliveryLevel(value);
    var matched = messageDeliveryLevelOptions().filter(function (item) { return item.value === level; })[0];
    return matched ? matched.label : "왕초보";
  }

  function syncAccountDraftFromLoadedAccounts(force, preferredAccountId) {
    var accounts = state.serviceAccounts || [];
    if (!accounts.length) {
      if (force) state.accountDraft = defaultAccountDraft();
      return;
    }
    var preferred = String(preferredAccountId || "").trim();
    if (preferred) {
      var matched = accounts.filter(function (account) {
        return accountIdOf(account) === preferred;
      })[0];
      if (matched) {
        state.editingAccountId = accountIdOf(matched);
        state.accountDraft = accountDraftFromAccount(matched);
        return;
      }
    }
    if (!force && state.editingAccountId) return;
    if (!force && state.accountDraft && state.accountDraft.id && state.accountDraft.id !== "main") return;
    var selected = accounts[0];
    state.editingAccountId = accountIdOf(selected);
    state.accountDraft = accountDraftFromAccount(selected);
  }

  function defaultAccountDraft() {
    var currentSettings = state && state.settings ? state.settings : defaultSettings;
    return {
      id: "main",
      label: "메인 계정",
      provider: "toss",
      baseUrl: "https://openapi.tossinvest.com",
      clientId: "",
      clientSecret: "",
      accountSeq: "",
      watchlistSymbols: currentSettings.watchlistSymbols || defaultSettings.watchlistSymbols,
      notifyProvider: currentSettings.notifyProvider || "telegram",
      telegramBotToken: "",
      telegramChatId: currentSettings.telegramChatId || "",
      notifyLinkUrl: currentSettings.notifyLinkUrl || defaultSettings.notifyLinkUrl,
      quietHoursEnabled: true,
      quietHoursStart: "22:00",
      quietHoursEnd: "05:00",
      quietHoursTimezone: "Asia/Seoul",
      messageDeliveryLevel: "absoluteBeginner",
      enabled: true
    };
  }

  function createNewAccountDraft() {
    var draft = defaultAccountDraft();
    var usedIds = {};
    (state.serviceAccounts || []).forEach(function (account) {
      var id = accountIdOf(account);
      if (id) usedIds[id] = true;
    });
    if (!usedIds[draft.id]) return draft;
    for (var index = 2; index < 1000; index += 1) {
      var candidate = "account-" + index;
      if (!usedIds[candidate]) {
        draft.id = candidate;
        draft.label = "추가 계정 " + index;
        return draft;
      }
    }
    draft.id = "account-" + Date.now();
    draft.label = "추가 계정";
    return draft;
  }

  function loadServiceAccounts(options) {
    options = options || {};
    state.serviceAccountsLoading = true;
    state.serviceAccountsError = "";
    if (isStaticPreviewHost()) {
      return loadStaticBuildConfig()
        .then(function (payload) {
          applyStaticBuildSettings(payload);
          applyStaticBuildAccounts(payload, Boolean(options.forceDraft));
        })
        .catch(function (error) {
          state.serviceAccountsError = error.message || "정적 계정 DB 스냅샷을 읽지 못했습니다.";
        })
        .finally(function () {
          state.serviceAccountsLoading = false;
          if (state.snapshot) render();
        });
    }
    return requestJson("/api/service-accounts")
      .then(function (payload) {
        state.serviceAccounts = Array.isArray(payload.accounts) ? payload.accounts : [];
        state.serviceAccountsLoaded = true;
        syncActiveWatchAccountId();
        syncAccountDraftFromLoadedAccounts(Boolean(options.forceDraft), options.draftAccountId);
      })
      .catch(function (error) {
        state.serviceAccountsError = error.message || "계정 DB를 읽지 못했습니다.";
      })
      .finally(function () {
        state.serviceAccountsLoading = false;
        if (state.snapshot) render();
      });
  }

  function accountDraftFromAccount(account) {
    return {
      id: account.id || "",
      label: account.label || account.id || "",
      provider: account.provider || "toss",
      baseUrl: account.baseUrl || "https://openapi.tossinvest.com",
      clientId: "",
      clientSecret: "",
      accountSeq: textValueUnlessBoolean(account.accountSeq),
      watchlistSymbols: Array.isArray(account.watchlistSymbols) ? account.watchlistSymbols.join(",") : String(account.watchlistSymbols || ""),
      notifyProvider: account.notifyProvider || settingValue("notifyProvider") || "telegram",
      telegramBotToken: "",
      telegramChatId: textValueUnlessBoolean(account.telegramChatId),
      notifyLinkUrl: account.notifyLinkUrl || settingValue("notifyLinkUrl") || defaultSettings.notifyLinkUrl,
      quietHoursEnabled: account.quietHoursEnabled !== false,
      quietHoursStart: account.quietHoursStart || "22:00",
      quietHoursEnd: account.quietHoursEnd || "05:00",
      quietHoursTimezone: account.quietHoursTimezone || "Asia/Seoul",
      messageDeliveryLevel: normalizeMessageDeliveryLevel(account.messageDeliveryLevel),
      enabled: account.enabled !== false
    };
  }

  function serviceAccountPayloadFromDraft() {
    var draft = state.accountDraft || defaultAccountDraft();
    var payload = {
      id: String(draft.id || "").trim(),
      label: String(draft.label || "").trim(),
      provider: String(draft.provider || "toss").trim(),
      baseUrl: String(draft.baseUrl || "https://openapi.tossinvest.com").trim(),
      accountSeq: String(draft.accountSeq || "").trim(),
      watchlistSymbols: normalizeSymbols(draft.watchlistSymbols || "").join(","),
      notifyProvider: String(draft.notifyProvider || "").trim(),
      telegramChatId: String(draft.telegramChatId || "").trim(),
      notifyLinkUrl: String(draft.notifyLinkUrl || "").trim(),
      quietHoursEnabled: draft.quietHoursEnabled !== false,
      quietHoursStart: String(draft.quietHoursStart || "22:00").trim(),
      quietHoursEnd: String(draft.quietHoursEnd || "05:00").trim(),
      quietHoursTimezone: String(draft.quietHoursTimezone || "Asia/Seoul").trim(),
      messageDeliveryLevel: normalizeMessageDeliveryLevel(draft.messageDeliveryLevel),
      enabled: draft.enabled !== false
    };
    if (String(draft.clientId || "").trim()) payload.clientId = String(draft.clientId || "").trim();
    if (String(draft.clientSecret || "").trim()) payload.clientSecret = String(draft.clientSecret || "").trim();
    if (String(draft.telegramBotToken || "").trim()) payload.telegramBotToken = String(draft.telegramBotToken || "").trim();
    return payload;
  }

  function saveServiceAccount() {
    if (isStaticPreviewHost()) {
      state.serviceAccountsError = "GitHub Pages에서는 실제 계정 DB를 저장할 수 없습니다. 로컬 서버에서 사용하세요.";
      render();
      return Promise.resolve();
    }
    var account = serviceAccountPayloadFromDraft();
    if (!account.id || !account.label) {
      state.serviceAccountsError = "계정 ID와 표시 이름은 필요합니다.";
      render();
      return Promise.resolve();
    }
    state.serviceAccountsLoading = true;
    state.serviceAccountsError = "";
    state.accountSaved = false;
    render();
    return sendJson("/api/service-accounts", "POST", { account: account })
      .then(function () {
        state.accountSaved = true;
        state.editingAccountId = account.id;
        return loadServiceAccounts({ forceDraft: true, draftAccountId: account.id });
      })
      .then(function () {
        showSnackbar("계정을 저장했습니다.");
      })
      .catch(function (error) {
        state.serviceAccountsError = error.message || "계정을 저장하지 못했습니다.";
        showSnackbar(state.serviceAccountsError, "danger");
      })
      .finally(function () {
        state.serviceAccountsLoading = false;
        render();
      });
  }

  function removeServiceAccount(id) {
    if (isStaticPreviewHost()) {
      state.serviceAccountsError = "GitHub Pages에서는 실제 계정 DB를 변경할 수 없습니다. 로컬 서버에서 사용하세요.";
      render();
      return Promise.resolve();
    }
    if (!id) return Promise.resolve();
    state.serviceAccountsLoading = true;
    state.serviceAccountsError = "";
    render();
    return sendJson("/api/service-accounts/" + encodeURIComponent(id), "DELETE")
      .then(function () {
        if (state.editingAccountId === id) {
          state.editingAccountId = "";
          state.accountDraft = defaultAccountDraft();
        }
        return loadServiceAccounts({ forceDraft: true });
      })
      .then(function () {
        showSnackbar("계정을 삭제했습니다.");
      })
      .catch(function (error) {
        state.serviceAccountsError = error.message || "계정을 삭제하지 못했습니다.";
        showSnackbar(state.serviceAccountsError, "danger");
      })
      .finally(function () {
        state.serviceAccountsLoading = false;
        render();
      });
  }

  function accountWatchlistPayload(account, symbols) {
    return {
      id: accountIdOf(account),
      label: String(account.label || account.id || "").trim(),
      provider: String(account.provider || "toss").trim(),
      baseUrl: String(account.baseUrl || "https://openapi.tossinvest.com").trim(),
      accountSeq: textValueUnlessBoolean(account.accountSeq),
      watchlistSymbols: normalizeSymbols((symbols || []).join(",")).join(","),
      notifyProvider: String(account.notifyProvider || settingValue("notifyProvider") || "telegram").trim(),
      notifyLinkUrl: String(account.notifyLinkUrl || settingValue("notifyLinkUrl") || "").trim(),
      quietHoursEnabled: account.quietHoursEnabled !== false,
      quietHoursStart: account.quietHoursStart || "22:00",
      quietHoursEnd: account.quietHoursEnd || "05:00",
      quietHoursTimezone: account.quietHoursTimezone || "Asia/Seoul",
      enabled: account.enabled !== false
    };
  }

  function saveAccountWatchlistSymbols(accountId, symbols) {
    var account = accountById(accountId);
    if (!account) {
      state.watchlistError = "관심 종목을 저장할 계정을 선택하세요.";
      render();
      return Promise.resolve();
    }
    if (isStaticPreviewHost() || state.serverSettingsLocked) {
      state.watchlistError = "GitHub Pages에서는 계정별 관심 종목을 저장할 수 없습니다. 로컬 서버에서 사용하세요.";
      showSnackbar(state.watchlistError, "danger");
      render();
      return Promise.resolve();
    }
    state.watchlistSavingAccountId = accountIdOf(account);
    state.watchlistError = "";
    state.watchSuggestQuery = "";
    state.watchSuggestItems = [];
    state.watchSuggestLoading = false;
    state.watchSuggestError = "";
    render();
    return sendJson("/api/service-accounts", "POST", { account: accountWatchlistPayload(account, symbols) })
      .then(function () {
        state.activeWatchAccountId = accountIdOf(account);
        state.editingWatchAccountId = "";
        state.editingWatchSymbol = "";
        return loadServiceAccounts();
      })
      .then(function () {
        showSnackbar("계정별 관심 종목을 저장했습니다.");
      })
      .catch(function (error) {
        state.watchlistError = error.message || "계정별 관심 종목을 저장하지 못했습니다.";
        showSnackbar(state.watchlistError, "danger");
      })
      .finally(function () {
        state.watchlistSavingAccountId = "";
        render();
      });
  }

  function addAccountWatchSymbol(accountId, symbol) {
    var next = normalizeSymbols(symbol || "");
    if (!next.length) {
      state.watchlistError = "추가할 종목을 입력하세요.";
      render();
      return Promise.resolve();
    }
    var account = accountById(accountId);
    var symbols = accountWatchlistSymbols(account);
    if (symbols.indexOf(next[0]) >= 0) {
      state.watchlistError = "선택한 계정에 이미 추가된 관심 종목입니다.";
      render();
      return Promise.resolve();
    }
    return saveAccountWatchlistSymbols(accountId, symbols.concat(next[0]));
  }

  function removeAccountWatchSymbol(accountId, symbol) {
    var removeSymbol = String(symbol || "").toUpperCase();
    return saveAccountWatchlistSymbols(accountId, accountWatchlistSymbols(accountById(accountId)).filter(function (item) {
      return item !== removeSymbol;
    }));
  }

  function replaceAccountWatchSymbol(accountId, original, nextValue) {
    var originalSymbol = String(original || "").toUpperCase();
    var next = normalizeSymbols(nextValue || "");
    if (!next.length) {
      state.watchlistError = "수정할 종목을 입력하세요.";
      render();
      return Promise.resolve();
    }
    var symbols = accountWatchlistSymbols(accountById(accountId));
    if (next[0] !== originalSymbol && symbols.indexOf(next[0]) >= 0) {
      state.watchlistError = "선택한 계정에 이미 추가된 관심 종목입니다.";
      render();
      return Promise.resolve();
    }
    return saveAccountWatchlistSymbols(accountId, symbols.map(function (symbol) {
      return symbol === originalSymbol ? next[0] : symbol;
    }));
  }

  function addSymbolToPreferredWatchlist(symbol) {
    var account = activeWatchAccount();
    return account ? addAccountWatchSymbol(accountIdOf(account), symbol) : addWatchSymbol(symbol);
  }

  function loadServerSettings() {
    if (isStaticPreviewHost()) {
      return loadStaticBuildConfig().then(function (payload) {
        applyStaticBuildSettings(payload);
      });
    }
    return requestJson("/api/settings")
      .then(function (payload) {
        applyServerSettings(payload);
      })
      .catch(function (error) {
        state.serverSettingsError = error.message || "서버 설정을 읽지 못했습니다.";
      });
  }

  function serverSettingsPayload() {
    syncModelAlertThresholdSettings();
    return {
      appTheme: settingValue("appTheme"),
      watchlistSymbols: settingValue("watchlistSymbols"),
      tossApiBaseUrl: settingValue("tossApiBaseUrl"),
      tossClientId: settingValue("tossClientId"),
      tossClientSecret: settingValue("tossClientSecret"),
      tossAccountSeq: settingValue("tossAccountSeq"),
      notifyProvider: settingValue("notifyProvider"),
      telegramBotToken: settingValue("telegramBotToken"),
      telegramChatId: settingValue("telegramChatId"),
      notifyLinkUrl: settingValue("notifyLinkUrl"),
      symbolUniverseMaxAgeHours: settingValue("symbolUniverseMaxAgeHours"),
      marketDataMaxAgeMinutes: settingValue("marketDataMaxAgeMinutes"),
      dataFreshnessEnabled: settingValue("dataFreshnessEnabled"),
      dataFreshnessDefaultMaxAgeMinutes: settingValue("dataFreshnessDefaultMaxAgeMinutes"),
      dataFreshnessQuoteMaxAgeMinutes: settingValue("dataFreshnessQuoteMaxAgeMinutes"),
      dataFreshnessExternalMaxAgeMinutes: settingValue("dataFreshnessExternalMaxAgeMinutes"),
      dataFreshnessExternalEquityMaxAgeMinutes: settingValue("dataFreshnessExternalEquityMaxAgeMinutes"),
      dataFreshnessExternalCryptoMaxAgeMinutes: settingValue("dataFreshnessExternalCryptoMaxAgeMinutes"),
      dataFreshnessMacroMaxAgeMinutes: settingValue("dataFreshnessMacroMaxAgeMinutes"),
      dataFreshnessDisclosureMaxAgeMinutes: settingValue("dataFreshnessDisclosureMaxAgeMinutes"),
      externalApiFetchIntervalMinutes: settingValue("externalApiFetchIntervalMinutes"),
      externalSignalCacheMaxAgeMinutes: settingValue("externalSignalCacheMaxAgeMinutes"),
      externalAlphaEnabled: settingValue("externalAlphaEnabled"),
      externalCoinGeckoEnabled: settingValue("externalCoinGeckoEnabled"),
      externalFredEnabled: settingValue("externalFredEnabled"),
      externalFredSeries: settingValue("externalFredSeries"),
      externalCryptoIds: settingValue("externalCryptoIds"),
      externalAlphaMaxSymbols: settingValue("externalAlphaMaxSymbols"),
      externalSecEnabled: settingValue("externalSecEnabled"),
      externalSecMaxSymbols: settingValue("externalSecMaxSymbols"),
      externalSecCompanyCiks: settingValue("externalSecCompanyCiks"),
      externalSecUserAgent: settingValue("externalSecUserAgent"),
      externalDartEnabled: settingValue("externalDartEnabled"),
      externalDartLookbackDays: settingValue("externalDartLookbackDays"),
      externalNewsEnabled: settingValue("externalNewsEnabled"),
      externalNewsProvider: settingValue("externalNewsProvider"),
      externalNewsMaxSymbols: settingValue("externalNewsMaxSymbols"),
      externalNewsLookbackHours: settingValue("externalNewsLookbackHours"),
      externalResearchEvidenceMaxItems: settingValue("externalResearchEvidenceMaxItems"),
      newsCollectionEnabled: settingValue("newsCollectionEnabled"),
      newsCollectionIntervalSeconds: settingValue("newsCollectionIntervalSeconds"),
      newsCollectionMaxSymbols: settingValue("newsCollectionMaxSymbols"),
      newsCollectionLookbackMinutes: settingValue("newsCollectionLookbackMinutes"),
      newsCollectionPerSymbolLimit: settingValue("newsCollectionPerSymbolLimit"),
      newsCollectionProviders: settingValue("newsCollectionProviders"),
      newsCollectionMinRelevanceScore: settingValue("newsCollectionMinRelevanceScore"),
      newsCollectionIncludeWatchlist: settingValue("newsCollectionIncludeWatchlist"),
      newsCollectionIncludeHoldings: settingValue("newsCollectionIncludeHoldings"),
      newsCollectionRateLimitSeconds: settingValue("newsCollectionRateLimitSeconds"),
      ontologyReasoningEnabled: settingValue("ontologyReasoningEnabled"),
      ontologyReasoningIntervalSeconds: settingValue("ontologyReasoningIntervalSeconds"),
      ontologyReasoningBatchSize: settingValue("ontologyReasoningBatchSize"),
      ontologyRuleCandidateAiEnabled: settingValue("ontologyRuleCandidateAiEnabled"),
      ontologyRuleCandidateAiUseCodex: settingValue("ontologyRuleCandidateAiUseCodex"),
      ontologyRuleCandidateAiCommand: settingValue("ontologyRuleCandidateAiCommand"),
      ontologyRuleCandidateAiTimeoutSeconds: settingValue("ontologyRuleCandidateAiTimeoutSeconds"),
      ontologyRuleCandidateAiIntervalMinutes: settingValue("ontologyRuleCandidateAiIntervalMinutes"),
      ontologyRuleCandidateAiMaxCandidates: settingValue("ontologyRuleCandidateAiMaxCandidates"),
      ontologyGraphStoreMode: settingValue("ontologyGraphStoreMode"),
      ontologyNeo4jEnabled: settingValue("ontologyNeo4jEnabled"),
      ontologyTypeDbEnabled: settingValue("ontologyTypeDbEnabled"),
      neo4jUri: settingValue("neo4jUri"),
      neo4jUser: settingValue("neo4jUser"),
      neo4jDatabase: settingValue("neo4jDatabase"),
      neo4jTimeoutSeconds: settingValue("neo4jTimeoutSeconds"),
      typedbAddress: settingValue("typedbAddress"),
      typedbUser: settingValue("typedbUser"),
      typedbDatabase: settingValue("typedbDatabase"),
      typedbTlsEnabled: settingValue("typedbTlsEnabled"),
      typedbTimeoutSeconds: settingValue("typedbTimeoutSeconds"),
      materialityGateEnabled: settingValue("materialityGateEnabled"),
      materialityMinimumScore: settingValue("materialityMinimumScore"),
      marketMaterialityMinimumScore: settingValue("marketMaterialityMinimumScore"),
      marketMaterialityPriceChangePct: settingValue("marketMaterialityPriceChangePct"),
      marketMaterialityTrendDistancePct: settingValue("marketMaterialityTrendDistancePct"),
      marketMaterialityVolumeRatio: settingValue("marketMaterialityVolumeRatio"),
      newsMaterialityMinimumScore: settingValue("newsMaterialityMinimumScore"),
      externalDartCorpCodes: settingValue("externalDartCorpCodes"),
      dartDisclosureAiAnalysisEnabled: settingValue("dartDisclosureAiAnalysisEnabled"),
      dartDisclosureAiUseCodex: settingValue("dartDisclosureAiUseCodex"),
      dartDisclosureAiCommand: settingValue("dartDisclosureAiCommand"),
      dartDisclosureAiTimeoutSeconds: settingValue("dartDisclosureAiTimeoutSeconds"),
      alphaVantageApiKey: settingValue("alphaVantageApiKey"),
      coingeckoApiKey: settingValue("coingeckoApiKey"),
      fredApiKey: settingValue("fredApiKey"),
      opendartApiKey: settingValue("opendartApiKey"),
      fxRates: settingValue("fxRates"),
      valuationAssumptions: settingValue("valuationAssumptions"),
      marketSignalInputs: settingValue("marketSignalInputs"),
      fairValueFormula: settingValue("fairValueFormula"),
      buyScoreFormula: settingValue("buyScoreFormula"),
      sellScoreFormula: settingValue("sellScoreFormula"),
      profitTakeScoreFormula: settingValue("profitTakeScoreFormula"),
      lossCutScoreFormula: settingValue("lossCutScoreFormula"),
      notificationScoreFormula: settingValue("notificationScoreFormula"),
      ontologyRelationRules: settingValue("ontologyRelationRules"),
      aiPromptTemplates: settingValue("aiPromptTemplates"),
      aiPromptPolicy: settingValue("aiPromptPolicy"),
      notificationAiGateEnabled: settingValue("notificationAiGateEnabled"),
      notificationAiGateMessageTypes: settingValue("notificationAiGateMessageTypes"),
      notificationAiUseCodex: settingValue("notificationAiUseCodex"),
      notificationAiTimeoutSeconds: settingValue("notificationAiTimeoutSeconds"),
      modelName: settingValue("modelName"),
      modelHypothesis: settingValue("modelHypothesis"),
      customBuyModelFormula: settingValue("customBuyModelFormula"),
      customSellModelFormula: settingValue("customSellModelFormula"),
      formulaWeights: settingValue("formulaWeights"),
      decisionThresholds: settingValue("decisionThresholds"),
      modelDecisionThresholds: settingValue("modelDecisionThresholds"),
      alertRules: settingValue("alertRules"),
      alertThresholds: settingValue("alertThresholds"),
      relationRuleThresholds: settingValue("relationRuleThresholds"),
      alertCadenceMinutes: settingValue("alertCadenceMinutes")
    };
  }

  function saveSettingsToServer() {
    if (isStaticPreviewHost()) {
      persistSettings();
      return Promise.resolve();
    }
    return sendJson("/api/settings", "PUT", { settings: serverSettingsPayload() })
      .then(function (payload) {
        applyServerSettings(payload);
        return Promise.all([
          loadNotificationSchedules(),
          loadOntologyRulebox(true)
        ])
          .catch(function (error) {
            state.messageSchedulesError = error.message || "설정 적용 후 운영 데이터를 다시 읽지 못했습니다.";
          });
      });
  }

  function loadOntologyRulebox(force) {
    if (isStaticPreviewHost()) return Promise.resolve(null);
    if (state.ontologyRuleboxLoading && !force) return Promise.resolve(state.ontologyRulebox);
    state.ontologyRuleboxLoading = true;
    state.ontologyRuleboxError = "";
    return requestJson("/api/ontology/rulebox")
      .then(function (payload) {
        applyOntologyRuleboxPayload(payload);
        return payload;
      })
      .catch(function (error) {
        state.ontologyRuleboxError = error.message || "Neo4j RuleBox를 읽지 못했습니다.";
        return null;
      })
      .finally(function () {
        state.ontologyRuleboxLoading = false;
        if (state.snapshot) render();
      });
  }

  function applyOntologyRuleboxPayload(payload) {
    state.ontologyRulebox = payload || {};
    state.ontologyRuleboxJson = JSON.stringify((payload && payload.rules) || [], null, 2);
    state.ontologyRuleboxError = "";
  }

  function parseOntologyRuleboxEditor() {
    var raw = String(state.ontologyRuleboxJson || "[]").trim() || "[]";
    var parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error("RuleBox JSON은 규칙 배열이어야 합니다.");
    return parsed;
  }

  function saveOntologyRulebox(seedDefaults) {
    if (state.ontologyRuleboxSaving) return;
    var rules = null;
    if (!seedDefaults) {
      try {
        rules = parseOntologyRuleboxEditor();
      } catch (error) {
        state.ontologyRuleboxError = error.message || "RuleBox JSON 형식을 확인하세요.";
        showSnackbar(state.ontologyRuleboxError, "danger");
        render();
        return;
      }
    }
    state.ontologyRuleboxSaving = true;
    state.ontologyRuleboxError = "";
    render();
    sendJson("/api/ontology/rulebox", "PUT", seedDefaults ? {
      clearInference: true,
      useBootstrapDefaults: true,
      changeReason: state.ontologyRuleboxChangeReason || "기본 RuleBox 시드"
    } : {
      rules: rules,
      clearInference: true,
      changeReason: state.ontologyRuleboxChangeReason || "RuleBox 관리 화면 저장"
    })
      .then(function (payload) {
        applyOntologyRuleboxPayload(payload);
        state.ontologyRuleboxChangeReason = "";
        showSnackbar(seedDefaults ? "기본 RuleBox를 Neo4j에 시드했습니다." : "Neo4j RuleBox를 저장했습니다.");
      })
      .catch(function (error) {
        state.ontologyRuleboxError = error.message || "Neo4j RuleBox를 저장하지 못했습니다.";
        showSnackbar(state.ontologyRuleboxError, "danger");
      })
      .finally(function () {
        state.ontologyRuleboxSaving = false;
        render();
      });
  }

  function appendRuleboxCandidate(candidateId) {
    var payload = state.ontologyRulebox || {};
    var candidates = Array.isArray(payload.changeCandidates) ? payload.changeCandidates : [];
    var candidate = candidates.filter(function (item) {
      return String(item.id || "") === String(candidateId || "");
    })[0];
    var proposedRule = candidate && candidate.proposedRule && typeof candidate.proposedRule === "object" ? candidate.proposedRule : null;
    if (!proposedRule) {
      showSnackbar("이 후보는 먼저 데이터나 스키마 보강이 필요합니다.", "caution");
      return;
    }
    try {
      var rules = parseOntologyRuleboxEditor();
      var proposedId = proposedRule.rule_id || proposedRule.ruleId || "";
      if (rules.some(function (rule) { return String(rule.rule_id || rule.ruleId || "") === String(proposedId); })) {
        showSnackbar("이미 같은 rule_id가 RuleBox JSON에 있습니다.", "caution");
        return;
      }
      rules.push(proposedRule);
      state.ontologyRuleboxJson = JSON.stringify(rules, null, 2);
      state.ontologyRuleboxChangeReason = state.ontologyRuleboxChangeReason || ("AI 후보 추가 검토: " + (candidate.title || proposedId));
      state.ontologyRuleboxError = "";
      showSnackbar("후보 규칙 초안을 JSON에 추가했습니다. 검토 후 저장하세요.");
      render();
    } catch (error) {
      state.ontologyRuleboxError = error.message || "RuleBox JSON 형식을 확인하세요.";
      showSnackbar(state.ontologyRuleboxError, "danger");
      render();
    }
  }

  function runOntologyRulebox() {
    if (state.ontologyRuleboxRunning) return;
    state.ontologyRuleboxRunning = true;
    state.ontologyRuleboxError = "";
    render();
    sendJson("/api/ontology/rulebox/run", "POST", { clearInference: true })
      .then(function (payload) {
        state.ontologyRuleboxLastRun = payload;
        showSnackbar(payload.status === "ok" ? "Neo4j RuleBox 추론을 실행했습니다." : "RuleBox 실행 결과: " + (payload.status || "확인 필요"), payload.status === "ok" ? "success" : "caution");
        return loadOntologyRulebox(true);
      })
      .catch(function (error) {
        state.ontologyRuleboxError = error.message || "Neo4j RuleBox 추론 실행에 실패했습니다.";
        showSnackbar(state.ontologyRuleboxError, "danger");
      })
      .finally(function () {
        state.ontologyRuleboxRunning = false;
        render();
      });
  }

  function proposeOntologyRuleCandidates() {
    if (state.ontologyRuleboxProposing) return;
    state.ontologyRuleboxProposing = true;
    state.ontologyRuleboxError = "";
    render();
    sendJson("/api/ontology/rulebox/candidates", "POST", { trigger: "manual" })
      .then(function (payload) {
        state.ontologyRuleboxCandidateResult = payload;
        if (payload && payload.rulebox) applyOntologyRuleboxPayload(payload.rulebox);
        showSnackbar(
          payload && payload.savedCount ? "AI 관계 후보를 Neo4j에 저장했습니다." : "AI 관계 후보 생성 결과가 없습니다.",
          payload && payload.savedCount ? "success" : "caution"
        );
      })
      .catch(function (error) {
        state.ontologyRuleboxError = error.message || "AI 관계 후보 생성에 실패했습니다.";
        showSnackbar(state.ontologyRuleboxError, "danger");
      })
      .finally(function () {
        state.ontologyRuleboxProposing = false;
        render();
      });
  }

  function loadOntologyExperiments(force) {
    if (isStaticPreviewHost()) {
      state.ontologyExperiments = { experiments: [], count: 0, activeCount: 0, latestRun: {} };
      state.ontologyExperimentsLoaded = true;
      state.ontologyExperimentsError = "";
      return Promise.resolve(state.ontologyExperiments);
    }
    if (state.ontologyExperimentsLoading && !force) return Promise.resolve(state.ontologyExperiments);
    state.ontologyExperimentsLoading = true;
    state.ontologyExperimentsError = "";
    return requestJson("/api/ontology/experiments/status")
      .then(function (payload) {
        state.ontologyExperiments = payload || {};
        state.ontologyExperimentsLoaded = true;
        return payload;
      })
      .catch(function (error) {
        state.ontologyExperimentsError = error.message || "온톨로지 실험 상태를 읽지 못했습니다.";
        return null;
      })
      .finally(function () {
        state.ontologyExperimentsLoading = false;
        if (state.snapshot) render();
      });
  }

  function runOntologyExperimentsOnce() {
    if (state.ontologyExperimentAction) return;
    if (isStaticPreviewHost()) {
      state.ontologyExperimentsError = "로컬 서버에서 실행할 수 있습니다.";
      showSnackbar(state.ontologyExperimentsError, "danger");
      render();
      return;
    }
    state.ontologyExperimentAction = "once";
    state.ontologyExperimentsError = "";
    render();
    sendJson("/api/ontology/experiments/once", "POST", { force: false })
      .then(function (payload) {
        showSnackbar("활성 실험 실행: " + (payload.runCount || 0) + "건, 건너뜀 " + (payload.skippedCount || 0) + "건");
        return loadOntologyExperiments(true);
      })
      .catch(function (error) {
        state.ontologyExperimentsError = error.message || "온톨로지 실험을 실행하지 못했습니다.";
        showSnackbar(state.ontologyExperimentsError, "danger");
      })
      .finally(function () {
        state.ontologyExperimentAction = "";
        render();
      });
  }

  function runOntologyExperiment(experimentId) {
    ontologyExperimentCommand(experimentId, "run", "실험을 실행했습니다.");
  }

  function activateOntologyExperiment(experimentId) {
    ontologyExperimentCommand(experimentId, "activate", "실험을 활성화했습니다.");
  }

  function pauseOntologyExperiment(experimentId) {
    ontologyExperimentCommand(experimentId, "pause", "실험을 일시정지했습니다.");
  }

  function ontologyExperimentCommand(experimentId, action, successMessage) {
    var id = String(experimentId || "").trim();
    if (!id || state.ontologyExperimentAction) return;
    if (isStaticPreviewHost()) {
      state.ontologyExperimentsError = "로컬 서버에서 실행할 수 있습니다.";
      showSnackbar(state.ontologyExperimentsError, "danger");
      render();
      return;
    }
    state.ontologyExperimentAction = action + ":" + id;
    state.ontologyExperimentsError = "";
    render();
    sendJson("/api/ontology/experiments/" + encodeURIComponent(id) + "/" + action, "POST", {})
      .then(function () {
        showSnackbar(successMessage || "실험 상태를 변경했습니다.");
        return loadOntologyExperiments(true);
      })
      .catch(function (error) {
        state.ontologyExperimentsError = error.message || "온톨로지 실험 요청에 실패했습니다.";
        showSnackbar(state.ontologyExperimentsError, "danger");
      })
      .finally(function () {
        state.ontologyExperimentAction = "";
        render();
      });
  }


  function normalizeSymbols(value) {
    return String(value || "")
      .split(/[,\s]+/)
      .map(function (symbol) { return symbol.trim().toUpperCase(); })
      .filter(Boolean)
      .filter(function (symbol, index, list) { return list.indexOf(symbol) === index; })
      .slice(0, 30);
  }

  function watchlistSymbols() {
    return normalizeSymbols(settingValue("watchlistSymbols"));
  }

  function fallbackKnownStockInfo(symbol) {
    var normalized = String(symbol || "").trim().toUpperCase();
    var map = {
      "005930": { name: "삼성전자", market: "KR", currency: "KRW", sector: "반도체" },
      "000660": { name: "SK하이닉스", market: "KR", currency: "KRW", sector: "반도체" },
      "005380": { name: "현대차", market: "KR", currency: "KRW", sector: "모빌리티" },
      "000020": { name: "동화약품", market: "KR", currency: "KRW", sector: "헬스케어" },
      "035420": { name: "NAVER", market: "KR", currency: "KRW", sector: "AI/플랫폼" },
      "035720": { name: "카카오", market: "KR", currency: "KRW", sector: "AI/플랫폼" },
      "051910": { name: "LG화학", market: "KR", currency: "KRW", sector: "소재" },
      "068270": { name: "셀트리온", market: "KR", currency: "KRW", sector: "헬스케어" },
      AAPL: { name: "Apple", market: "US", currency: "USD", sector: "AI/플랫폼" },
      MSFT: { name: "Microsoft", market: "US", currency: "USD", sector: "AI/플랫폼" },
      NVDA: { name: "NVIDIA", market: "US", currency: "USD", sector: "반도체" },
      AMD: { name: "AMD", market: "US", currency: "USD", sector: "반도체" },
      TSLA: { name: "Tesla", market: "US", currency: "USD", sector: "모빌리티" },
      MSTR: { name: "Strategy", market: "US", currency: "USD", sector: "디지털자산" },
      STRC: { name: "Strategy Preferred", market: "US", currency: "USD", sector: "디지털자산" },
      GOOGL: { name: "Alphabet", market: "US", currency: "USD", sector: "AI/플랫폼" },
      META: { name: "Meta", market: "US", currency: "USD", sector: "AI/플랫폼" }
    };
    return Object.assign({
      symbol: normalized,
      name: normalized || "관심 종목",
      market: "",
      currency: "",
      sector: ""
    }, map[normalized] || {});
  }

  function fallbackKnownStockSymbols() {
    return ["005930", "000660", "005380", "000020", "035420", "035720", "051910", "068270", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "MSTR", "STRC", "GOOGL", "META"];
  }

  function clientKnownStockInfo(symbol) {
    var normalized = String(symbol || "").trim().toUpperCase();
    var universeItem = (state.symbolUniverse.items || []).filter(function (item) {
      return String(item.symbol || "").toUpperCase() === normalized;
    })[0];
    if (universeItem) {
      return Object.assign(fallbackKnownStockInfo(normalized), {
        symbol: universeItem.symbol,
        name: universeItem.name || universeItem.symbol,
        market: universeItem.market || universeItem.exchange || "",
        currency: universeItem.currency || "",
        sector: universeItem.sector || "",
        source: universeItem.source || "",
        stale: Boolean(universeItem.stale)
      });
    }
    return fallbackKnownStockInfo(normalized);
  }

  function stockDisplayName(symbol, item) {
    var original = String(symbol || (item && (item.rawSymbol || item.symbol)) || "").trim().toUpperCase();
    var merged = Object.assign(clientKnownStockInfo(original), item || {}, { symbol: original });
    var explicit = String(
      (item && (item.symbolName || item.symbolDisplayName || item.displaySymbolName || item.displayName)) || ""
    ).trim();
    var name = explicit || String(merged.name || "").trim();
    if (!name || (original && name.toUpperCase() === original)) {
      name = original || "종목";
    }
    return name;
  }

  function stockDisplayMeta(item, parts) {
    return (parts || [])
      .map(function (part) { return String(part || "").trim(); })
      .filter(Boolean)
      .join(" · ");
  }

  function escapeRegExp(value) {
    return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function textWithDisplaySymbol(value, symbol, item) {
    var text = String(value || "");
    var original = String(symbol || (item && (item.rawSymbol || item.symbol)) || "").trim().toUpperCase();
    var display = stockDisplayName(original, item);
    if (!text || !original || !display || display.toUpperCase() === original) return text;
    var replaced = text.replace(new RegExp("\\b" + escapeRegExp(original) + "\\b", "g"), display);
    replaced = replaced.replace(new RegExp(escapeRegExp(display) + "\\s*[/·]\\s*" + escapeRegExp(display), "g"), display);
    return replaced;
  }

  function textWithKnownDisplaySymbols(value, preferredSymbol, item) {
    var text = textWithDisplaySymbol(value, preferredSymbol, item);
    fallbackKnownStockSymbols().forEach(function (symbol) {
      text = textWithDisplaySymbol(text, symbol, Object.assign({}, item || {}, { symbol: symbol }));
    });
    return text;
  }

  function inferKnownStockSymbolFromText(value) {
    var text = String(value || "").toUpperCase();
    if (!text) return "";
    var known = fallbackKnownStockSymbols();
    for (var index = 0; index < known.length; index += 1) {
      if (new RegExp("\\b" + escapeRegExp(known[index]) + "\\b").test(text)) return known[index];
    }
    var match = text.match(/\b\d{6}\b/);
    return match ? match[0] : "";
  }

  function defaultSymbolUniversePayload() {
    var items = ["005930", "000660", "TSLA", "AAPL", "NVDA", "MSFT", "AMD"].map(function (symbol) {
      var info = fallbackKnownStockInfo(symbol);
      var market = info.market === "US" ? "NASDAQ" : (info.market === "KR" ? "KOSPI" : info.market);
      return Object.assign({}, info, {
        market: market,
        exchange: market,
        assetType: "STOCK",
        source: "Orbit Alpha seed",
        sourceUrl: "local-default",
        fetchedAt: "",
        lastSeenAt: "",
        stale: true
      });
    });
    var query = String(state.symbolUniverseQuery || "").trim().toUpperCase();
    var marketFilter = String(state.symbolUniverseMarket || "").trim().toUpperCase();
    var filtered = items.filter(function (item) {
      var marketOk = !marketFilter || item.market === marketFilter;
      var queryOk = !query || String(item.symbol || "").toUpperCase().indexOf(query) >= 0 || String(item.name || "").toUpperCase().indexOf(query) >= 0;
      return marketOk && queryOk;
    });
    var limit = Math.max(1, Math.min(500, Number(state.symbolUniverseLimit || 80)));
    var offset = Math.max(0, Number(state.symbolUniverseOffset || 0));
    return {
      items: filtered.slice(offset, offset + limit),
      summary: {
        total: items.length,
        maxAgeHours: 24,
        sources: [],
        markets: ["KOSPI", "KOSDAQ", "NASDAQ"].map(function (market) {
          return {
            market: market,
            count: items.filter(function (item) { return item.market === market; }).length,
            lastSeenAt: "",
            stale: true,
            source: market === "NASDAQ" ? "Nasdaq Trader Symbol Directory" : "KRX KIND Listed Companies",
            sourceUrl: ""
          };
        })
      },
      resultTotal: filtered.length,
      limit: limit,
      offset: offset,
      hasMore: offset + limit < filtered.length
    };
  }

  function applySymbolUniverse(payload) {
    state.symbolUniverse = {
      items: Array.isArray(payload.items) ? payload.items : [],
      summary: payload.summary || { markets: [], sources: [], total: 0, maxAgeHours: 24 },
      resultTotal: Number(payload.resultTotal || 0),
      limit: Number(payload.limit || state.symbolUniverseLimit || 80),
      offset: Number(payload.offset || state.symbolUniverseOffset || 0),
      hasMore: Boolean(payload.hasMore)
    };
    state.symbolUniverseLimit = state.symbolUniverse.limit;
    state.symbolUniverseOffset = state.symbolUniverse.offset;
    state.symbolUniverseLoaded = true;
    state.symbolUniverseError = "";
  }

  function symbolUniversePath() {
    var params = new URLSearchParams();
    if (state.symbolUniverseQuery) params.set("query", state.symbolUniverseQuery);
    if (state.symbolUniverseMarket) params.set("market", state.symbolUniverseMarket);
    params.set("limit", String(state.symbolUniverseLimit || 80));
    params.set("offset", String(state.symbolUniverseOffset || 0));
    return "/api/symbol-universe?" + params.toString();
  }

  function loadSymbolUniverse() {
    state.symbolUniverseLoading = true;
    state.symbolUniverseError = "";
    if (isStaticPreviewHost()) {
      applySymbolUniverse(defaultSymbolUniversePayload());
      state.symbolUniverseLoading = false;
      return Promise.resolve();
    }
    return requestJson(symbolUniversePath())
      .then(function (payload) {
        applySymbolUniverse(payload);
      })
      .catch(function (error) {
        state.symbolUniverseError = error.message || "종목 유니버스를 읽지 못했습니다.";
        applySymbolUniverse(defaultSymbolUniversePayload());
      })
      .finally(function () {
        state.symbolUniverseLoading = false;
        if (state.snapshot) render();
      });
  }

  function refreshSymbolUniverse() {
    if (isStaticPreviewHost() || state.serverSettingsLocked) {
      state.symbolUniverseError = "공유 모드에서는 종목 유니버스를 갱신할 수 없습니다.";
      showSnackbar(state.symbolUniverseError, "danger");
      render();
      return Promise.resolve();
    }
    state.symbolUniverseRefreshing = true;
    state.symbolUniverseError = "";
    render();
    var markets = state.symbolUniverseMarket ? [state.symbolUniverseMarket] : ["KOSPI", "KOSDAQ", "NASDAQ"];
    return sendJson("/api/symbol-universe/refresh", "POST", { markets: markets })
      .then(function (payload) {
        if (payload.summary) {
          state.symbolUniverse.summary = payload.summary;
        }
        showSnackbar("전체 종목 목록을 갱신했습니다.");
        return loadSymbolUniverse();
      })
      .catch(function (error) {
        state.symbolUniverseError = error.message || "종목 유니버스를 갱신하지 못했습니다.";
        showSnackbar(state.symbolUniverseError, "danger");
      })
      .finally(function () {
        state.symbolUniverseRefreshing = false;
        render();
      });
  }

  function suggestionKey(item) {
    return String((item && item.symbol) || "").trim().toUpperCase();
  }

  function mergeSuggestionItems(primary, secondary, limit) {
    var seen = {};
    var merged = [];
    [primary || [], secondary || []].forEach(function (items) {
      items.forEach(function (item) {
        var key = suggestionKey(item);
        if (!key || seen[key]) return;
        seen[key] = true;
        merged.push(item);
      });
    });
    return merged.slice(0, limit || 8);
  }

  function localWatchSuggestItems(query) {
    var normalized = String(query || "").trim().toUpperCase();
    if (!normalized) return [];
    var candidates = [];
    (state.symbolUniverse.items || []).forEach(function (item) {
      candidates.push(item);
    });
    watchlistSymbols().concat(allAccountWatchlistSymbols()).forEach(function (symbol) {
      candidates.push(clientKnownStockInfo(symbol));
    });
    var toss = state.snapshot && state.snapshot.toss ? state.snapshot.toss : {};
    (toss.positions || []).concat(toss.watchlist || []).forEach(function (item) {
      if (item && item.symbol) candidates.push(item);
    });
    return mergeSuggestionItems(candidates.filter(function (item) {
      return String(item.symbol || "").toUpperCase().indexOf(normalized) >= 0
        || String(item.name || "").toUpperCase().indexOf(normalized) >= 0;
    }), [], 8);
  }

  function watchSuggestPath(query) {
    var params = new URLSearchParams();
    params.set("query", String(query || "").trim());
    params.set("limit", "8");
    params.set("offset", "0");
    return "/api/symbol-universe?" + params.toString();
  }

  function renderWatchSuggestList() {
    var query = String(state.watchSuggestQuery || "").trim();
    if (!query) return "";
    if (state.watchSuggestLoading) {
      return '<p class="subtle watch-suggest-message">종목을 검색하는 중입니다.</p>';
    }
    if (state.watchSuggestError) {
      return '<p class="form-error">' + escapeHtml(state.watchSuggestError) + '</p>';
    }
    var items = state.watchSuggestItems || [];
    if (!items.length) {
      return '<p class="subtle watch-suggest-message">검색 결과가 없습니다. 종목명을 다시 확인하세요.</p>';
    }
    return items.map(function (item) {
      var symbol = suggestionKey(item);
      return [
        '<button class="watch-suggest-option" type="button" data-watch-suggest-symbol="' + escapeHtml(symbol) + '">',
        '<span>',
        '<strong>' + escapeHtml(stockDisplayName(symbol, item)) + '</strong>',
        '<em>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || item.exchange), item.currency || item.assetType || "-"])) + '</em>',
        '</span>',
        '<b>추가</b>',
        '</button>'
      ].join("");
    }).join("");
  }

  function updateWatchSuggestBox(box) {
    if (box) box.innerHTML = renderWatchSuggestList();
  }

  function loadWatchSuggestions(query, box, input) {
    var normalized = String(query || "").trim();
    state.watchSuggestQuery = normalized;
    state.watchSuggestError = "";
    if (!normalized) {
      state.watchSuggestItems = [];
      state.watchSuggestLoading = false;
      updateWatchSuggestBox(box);
      return;
    }
    if (watchSuggestTimer) clearTimeout(watchSuggestTimer);
    watchSuggestTimer = setTimeout(function () {
      var requestId = ++watchSuggestRequestId;
      var localItems = localWatchSuggestItems(normalized);
      state.watchSuggestItems = localItems;
      state.watchSuggestLoading = true;
      updateWatchSuggestBox(box);
      var request = isStaticPreviewHost()
        ? Promise.resolve({ items: localItems })
        : requestJson(watchSuggestPath(normalized));
      request
        .then(function (payload) {
          if (requestId !== watchSuggestRequestId) return;
          if (input && String(input.value || "").trim() !== normalized) return;
          state.watchSuggestItems = mergeSuggestionItems(payload.items || [], localItems, 8);
          state.watchSuggestError = "";
        })
        .catch(function (error) {
          if (requestId !== watchSuggestRequestId) return;
          state.watchSuggestItems = localItems;
          state.watchSuggestError = localItems.length ? "" : (error.message || "종목 검색에 실패했습니다.");
        })
        .finally(function () {
          if (requestId !== watchSuggestRequestId) return;
          state.watchSuggestLoading = false;
          updateWatchSuggestBox(box);
        });
    }, 180);
  }

  function saveWatchlistSymbols(symbols) {
    state.settings.watchlistSymbols = normalizeSymbols(symbols.join(",")).join(",");
    state.editingWatchSymbol = "";
    state.watchlistError = "";
    state.watchSuggestQuery = "";
    state.watchSuggestItems = [];
    state.watchSuggestLoading = false;
    state.watchSuggestError = "";
    persistSettings();
    var save = isStaticPreviewHost()
      ? Promise.resolve()
      : saveSettingsToServer();
    return save.then(function () {
      showSnackbar("관심 종목을 저장했습니다.");
    }).catch(function (error) {
      state.watchlistError = error.message || "관심 종목을 서버 설정 DB에 저장하지 못했습니다.";
      showSnackbar(state.watchlistError, "danger");
    }).finally(function () {
      render();
    });
  }

  function addWatchSymbol(symbol) {
    var next = normalizeSymbols(symbol || "");
    if (!next.length) {
      state.watchlistError = "추가할 종목을 입력하세요.";
      render();
      return Promise.resolve();
    }
    var symbols = watchlistSymbols();
    if (symbols.indexOf(next[0]) >= 0) {
      state.watchlistError = "이미 추가된 관심 종목입니다.";
      render();
      return Promise.resolve();
    }
    return saveWatchlistSymbols(symbols.concat(next[0]));
  }

  function tossLensPath() {
    var params = new URLSearchParams();
    var symbols = allAccountWatchlistSymbols();
    if (!symbols.length) symbols = watchlistSymbols();
    if (symbols.length) params.set("watchlistSymbols", symbols.join(","));
    var query = params.toString();
    return "/api/flow-lens" + (query ? "?" + query : "");
  }

  function feedTimeValue(value) {
    var raw = String(value || "");
    var compact = raw.replace(/\D/g, "");
    if (compact.length >= 14) {
      return Date.UTC(
        Number(compact.slice(0, 4)),
        Number(compact.slice(4, 6)) - 1,
        Number(compact.slice(6, 8)),
        Number(compact.slice(8, 10)),
        Number(compact.slice(10, 12)),
        Number(compact.slice(12, 14))
      );
    }
    var parsed = Date.parse(raw);
    return Number.isNaN(parsed) ? 0 : parsed;
  }

  function formatFeedTime(value) {
    var raw = String(value || "");
    var compact = raw.replace(/\D/g, "");
    if (compact.length >= 14) {
      return compact.slice(4, 6) + "." + compact.slice(6, 8) + " " + compact.slice(8, 10) + ":" + compact.slice(10, 12);
    }
    return formatClock(value);
  }

  function currentResearchEvidence() {
    return state.researchEvidence || {
      items: [],
      summary: { total: 0, latestSeenAt: "", bySymbol: [], byKind: [], bySource: [], byPolarity: [] },
      symbol: "",
      kind: "",
      limit: Number(state.researchEvidenceFilters.limit || 80)
    };
  }

  function resolveResearchEvidenceSymbol(value) {
    var raw = String(value || "").trim();
    if (!raw) return "";
    var normalized = raw.toUpperCase();
    if (/^[A-Z0-9.\-]+$/.test(normalized)) return normalized;
    var candidates = [];
    var toss = (state.snapshot || {}).toss || {};
    (Array.isArray(toss.positions) ? toss.positions : []).forEach(function (item) {
      candidates.push(item);
    });
    (Array.isArray(toss.watchlist) ? toss.watchlist : []).forEach(function (item) {
      candidates.push(item);
    });
    (Array.isArray((state.symbolUniverse || {}).items) ? state.symbolUniverse.items : []).forEach(function (item) {
      candidates.push(item);
    });
    var exact = candidates.filter(function (item) {
      return String(item && (item.name || item.symbolName || item.displayName) || "").trim() === raw;
    })[0];
    if (exact && exact.symbol) return String(exact.symbol).toUpperCase();
    var partial = candidates.filter(function (item) {
      return String(item && (item.name || item.symbolName || item.displayName) || "").indexOf(raw) >= 0;
    })[0];
    return partial && partial.symbol ? String(partial.symbol).toUpperCase() : normalized;
  }

  function researchEvidenceQueryString() {
    var filters = state.researchEvidenceFilters || {};
    var params = new URLSearchParams();
    var symbol = resolveResearchEvidenceSymbol(filters.symbol);
    var kind = String(filters.kind || "").trim();
    var limit = String(filters.limit || "80").trim();
    if (symbol) params.set("symbol", symbol);
    if (kind) params.set("kind", kind);
    if (limit) params.set("limit", limit);
    var text = params.toString();
    return text ? "?" + text : "";
  }

  function staticResearchEvidencePayload(reason) {
    var stamped = new Date().toISOString();
    return {
      items: [
        {
          evidenceId: "preview:005930:news",
          symbol: "005930",
          kind: "news",
          source: "Static Preview",
          title: "반도체 업황 개선 기대",
          summary: reason || "정적 미리보기에서는 저장된 리서치 근거 예시를 보여줍니다.",
          url: "",
          observedAt: stamped,
          publishedAt: stamped,
          polarity: "support",
          impactScore: 6.5,
          confidence: 0.62,
          payload: { name: "삼성전자" }
        }
      ],
      summary: {
        total: 1,
        latestSeenAt: stamped,
        bySymbol: [{ name: "005930", count: 1, latestSeenAt: stamped }],
        byKind: [{ name: "news", count: 1, latestSeenAt: stamped }],
        bySource: [{ name: "Static Preview", count: 1, latestSeenAt: stamped }],
        byPolarity: [{ name: "support", count: 1, latestSeenAt: stamped }]
      },
      symbol: "005930",
      kind: "",
      limit: 80,
      preview: true
    };
  }

  function loadResearchEvidence(force) {
    if (state.researchEvidenceLoading) return Promise.resolve();
    if (state.researchEvidence && !force) return Promise.resolve(state.researchEvidence);
    state.researchEvidenceLoading = true;
    state.researchEvidenceError = "";
    render();

    var promise = isStaticPreviewHost()
      ? Promise.resolve(staticResearchEvidencePayload("정적 미리보기"))
      : requestJson("/api/research-evidence" + researchEvidenceQueryString());

    return promise
      .then(function (payload) {
        state.researchEvidence = payload;
        state.researchEvidenceError = "";
      })
      .catch(function (error) {
        state.researchEvidenceError = error.message || "저장된 근거를 불러오지 못했습니다.";
      })
      .finally(function () {
        state.researchEvidenceLoading = false;
        render();
      });
  }

  function deleteResearchEvidence(evidenceId) {
    var id = String(evidenceId || "").trim();
    if (!id || state.researchEvidenceDeleting) return Promise.resolve();
    if (window.confirm && !window.confirm("선택한 리서치 근거를 삭제할까요?")) return Promise.resolve();
    state.researchEvidenceDeleting = id;
    state.researchEvidenceError = "";
    render();
    return sendJson("/api/research-evidence/" + encodeURIComponent(id) + researchEvidenceQueryString(), "DELETE", {})
      .then(function (payload) {
        state.researchEvidence = payload;
        showSnackbar(payload.deleted ? "리서치 근거를 삭제했습니다." : "삭제할 근거를 찾지 못했습니다.", payload.deleted ? "success" : "danger");
      })
      .catch(function (error) {
        state.researchEvidenceError = error.message || "리서치 근거를 삭제하지 못했습니다.";
        showSnackbar(state.researchEvidenceError, "danger");
      })
      .finally(function () {
        state.researchEvidenceDeleting = "";
        render();
      });
  }

  function staticPreviewSnapshot() {
    var localData = staticLocalData(state.staticBuildConfig);
    var stamped = localData.generatedAt || new Date().toISOString();
    var accountCount = Number(localData.accountCount || 0);
    return {
      generatedAt: stamped,
      preview: true,
      headline: accountCount ? "빌드 시점 로컬 DB 설정을 표시합니다." : "로컬 서버에서 계정과 알림 설정을 관리합니다.",
      exitScore: 0,
      regime: "정적 미리보기",
      summary: [],
      toss: {
        mode: "preview",
        configured: false,
        status: "정적 미리보기",
        account: {},
        positions: [],
        watchlist: []
      },
      portfolio: {
        total: 0,
        invested: 0,
        cash: 0,
        concentration: 0,
        markets: [],
        sectors: []
      },
      tossDecision: {
        headline: "로컬 서버에서 실제 계정 데이터를 조회합니다.",
        overallPressure: 0,
        urgentCount: 0,
        holdingCount: 0,
        watchCount: 0,
        items: [],
        rules: []
      },
      checklist: []
    };
  }

  function formatClock(value) {
    if (!value) return "-";
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  function formatMoney(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number)) return "-";
    if (Math.abs(number) >= 100000000) return (number / 100000000).toFixed(1) + "억";
    if (Math.abs(number) >= 10000) return Math.round(number / 10000).toLocaleString("ko-KR") + "만";
    return number.toLocaleString("ko-KR");
  }

  function formatCurrency(value, currency) {
    var suffix = currency ? " " + currency : "";
    return formatMoney(value) + suffix;
  }

  function formatPrice(value, currency) {
    var number = Number(value || 0);
    if (!Number.isFinite(number)) return "-";
    var suffix = currency ? " " + currency : "";
    return number.toLocaleString("ko-KR", {
      maximumFractionDigits: Number.isInteger(number) ? 0 : 2
    }) + suffix;
  }

  function pct(value) {
    return Math.round(Number(value || 0)) + "%";
  }

  function signedPct(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "0%";
    return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 1) + "%";
  }

  function signedNumber(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "0";
    return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 1);
  }

  function signedMoney(value, currency) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "0" + (currency ? " " + currency : "");
    return (number > 0 ? "+" : "") + formatCurrency(number, currency);
  }

  function sourceLabel(value) {
    if (value === "holding") return "보유";
    if (value === "watchlist") return "관심";
    if (value === "cash") return "현금";
    return value || "-";
  }

  function numeric(value) {
    var parsed = Number(String(value == null ? "" : value).replace(/,/g, "").trim());
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function formulaSetting(name) {
    return normalizeFormulaAliases(settingValue(name) || defaultSettings[name] || "").trim();
  }

  function tokenizeFormula(expression) {
    var input = String(expression || "");
    var tokens = [];
    var index = 0;
    if (input.length > 1200) throw new Error("공식이 너무 깁니다.");
    while (index < input.length) {
      var char = input[index];
      if (/\s/.test(char)) {
        index += 1;
        continue;
      }
      if (/[0-9.]/.test(char)) {
        var start = index;
        index += 1;
        while (index < input.length && /[0-9.]/.test(input[index])) index += 1;
        var number = Number(input.slice(start, index));
        if (!Number.isFinite(number)) throw new Error("숫자 형식 오류");
        tokens.push({ type: "number", value: number });
        continue;
      }
      if (/[A-Za-z_]/.test(char)) {
        var nameStart = index;
        index += 1;
        while (index < input.length && /[A-Za-z0-9_]/.test(input[index])) index += 1;
        tokens.push({ type: "name", value: input.slice(nameStart, index) });
        continue;
      }
      if ("+-*/(),".indexOf(char) >= 0) {
        tokens.push({ type: char, value: char });
        index += 1;
        continue;
      }
      throw new Error("지원하지 않는 문자: " + char);
    }
    return tokens;
  }

  function evaluateFormula(expression, variables) {
    var tokens = tokenizeFormula(expression);
    var index = 0;
    variables = variables || {};

    function peek() {
      return tokens[index] || null;
    }

    function take(type) {
      var token = peek();
      if (token && token.type === type) {
        index += 1;
        return token;
      }
      return null;
    }

    function expect(type) {
      var token = take(type);
      if (!token) throw new Error("'" + type + "'가 필요합니다.");
      return token;
    }

    function safeNumber(value) {
      var number = Number(value);
      return Number.isFinite(number) ? number : 0;
    }

    function applyFormulaFunction(name, args) {
      var lower = String(name || "").toLowerCase();
      if (lower === "min") return Math.min.apply(Math, args);
      if (lower === "max") return Math.max.apply(Math, args);
      if (lower === "abs") return Math.abs(args[0] || 0);
      if (lower === "round") return Math.round(args[0] || 0);
      if (lower === "sqrt") return Math.sqrt(Math.max(0, args[0] || 0));
      if (lower === "pow") return Math.pow(args[0] || 0, args[1] || 0);
      if (lower === "clamp") return clamp(args[0] || 0, args[1] || 0, args[2] == null ? 100 : args[2]);
      throw new Error("지원하지 않는 함수: " + name);
    }

    function parsePrimary() {
      var token = peek();
      if (!token) throw new Error("공식이 끝났습니다.");
      if (take("number")) return token.value;
      if (take("name")) {
        if (take("(")) {
          var args = [];
          if (!take(")")) {
            do {
              args.push(parseExpression());
            } while (take(","));
            expect(")");
          }
          return safeNumber(applyFormulaFunction(token.value, args));
        }
        return safeNumber(variables[token.value]);
      }
      if (take("(")) {
        var value = parseExpression();
        expect(")");
        return value;
      }
      throw new Error("예상하지 못한 토큰: " + token.value);
    }

    function parseUnary() {
      if (take("+")) return parseUnary();
      if (take("-")) return -parseUnary();
      return parsePrimary();
    }

    function parseTerm() {
      var value = parseUnary();
      while (true) {
        if (take("*")) {
          value *= parseUnary();
        } else if (take("/")) {
          var divisor = parseUnary();
          value = divisor ? value / divisor : 0;
        } else {
          break;
        }
      }
      return value;
    }

    function parseExpression() {
      var value = parseTerm();
      while (true) {
        if (take("+")) {
          value += parseTerm();
        } else if (take("-")) {
          value -= parseTerm();
        } else {
          break;
        }
      }
      return value;
    }

    var output = parseExpression();
    if (index < tokens.length) throw new Error("공식 뒤에 해석되지 않은 값이 있습니다.");
    if (!Number.isFinite(output)) throw new Error("공식 결과가 숫자가 아닙니다.");
    return output;
  }

  function evaluateConfiguredFormula(expression, variables, fallback) {
    try {
      var value = evaluateFormula(expression, variables);
      return {
        value: value,
        error: "",
        usedFallback: false
      };
    } catch (error) {
      return {
        value: fallback,
        error: error.message || "공식 오류",
        usedFallback: true
      };
    }
  }

  function parseNumberAssignments(value, defaults) {
    var map = Object.assign({}, defaults || {});
    String(value || "")
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .filter(Boolean)
      .forEach(function (line) {
        var parts = line.split(/[=:,]/);
        var key = String(parts[0] || "").trim();
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return;
        map[key] = numeric(parts.slice(1).join(":"));
      });
    return map;
  }

  function strategyDefaultSettingNames() {
    return [
      "fairValueFormula",
      "buyScoreFormula",
      "sellScoreFormula",
      "profitTakeScoreFormula",
      "lossCutScoreFormula",
      "notificationScoreFormula",
      "ontologyRelationRules",
      "aiPromptTemplates",
      "aiPromptPolicy",
      "notificationAiGateEnabled",
      "notificationAiGateMessageTypes",
      "notificationAiUseCodex",
      "notificationAiTimeoutSeconds",
      "modelName",
      "modelHypothesis",
      "customBuyModelFormula",
      "customSellModelFormula",
      "formulaWeights",
      "decisionThresholds",
      "modelDecisionThresholds",
      "relationRuleThresholds",
      "alertThresholds"
    ];
  }

  function withDefaultStrategySettings(settings) {
    var next = Object.assign({}, settings || {});
    strategyDefaultSettingNames().forEach(function (name) {
      if (String(next[name] == null ? "" : next[name]).trim() === "") {
        next[name] = defaultSettings[name] || "";
      }
    });
    return next;
  }

  function syncedModelAlertSettings(settings) {
    var next = withDefaultStrategySettings(settings);
    var modelThresholds = parseNumberAssignments(next.modelDecisionThresholds, parseNumberAssignments(defaultSettings.modelDecisionThresholds));
    var thresholds = parseNumberAssignments(next.alertThresholds, parseNumberAssignments(defaultSettings.alertThresholds));
    thresholds.modelBuyScore = modelThresholds.modelBuy;
    thresholds.watchlistBuyScore = modelThresholds.modelBuy;
    thresholds.modelSellScore = modelThresholds.modelSell;
    next.alertThresholds = serializeNumberAssignments(thresholds, assignmentOrder("alertThresholds"));
    return next;
  }

  function syncModelAlertThresholdSettings() {
    state.settings = syncedModelAlertSettings(state.settings);
  }

  function formulaWeights() {
    var parsed = parseNumberAssignments(settingValue("formulaWeights"), parseNumberAssignments(defaultSettings.formulaWeights));
    if (parsed.buyReasonWeight == null && parsed.thesisWeight != null) parsed.buyReasonWeight = parsed.thesisWeight;
    if (parsed.thesisWeight == null && parsed.buyReasonWeight != null) parsed.thesisWeight = parsed.buyReasonWeight;
    return parsed;
  }

  function decisionThresholds() {
    return parseNumberAssignments(settingValue("decisionThresholds"), parseNumberAssignments(defaultSettings.decisionThresholds));
  }

  function modelDecisionThresholds() {
    return parseNumberAssignments(settingValue("modelDecisionThresholds"), parseNumberAssignments(defaultSettings.modelDecisionThresholds));
  }

  function assignmentOrder(settingName) {
    return String(defaultSettings[settingName] || "")
      .split(/\r?\n/)
      .map(function (line) { return String(line.split(/[=:,]/)[0] || "").trim(); })
      .filter(Boolean);
  }

  function serializeNumberAssignments(map, order) {
    var seen = {};
    var keys = (order || []).filter(function (key) {
      if (!Object.prototype.hasOwnProperty.call(map, key) || seen[key]) return false;
      seen[key] = true;
      return true;
    });
    Object.keys(map).sort().forEach(function (key) {
      if (!seen[key]) keys.push(key);
    });
    return keys.map(function (key) {
      return key + "=" + Number(map[key] || 0);
    }).join("\n");
  }

  function updateNumberAssignmentSetting(settingName, key, value) {
    if (!Object.prototype.hasOwnProperty.call(defaultSettings, settingName)) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(String(key || ""))) return;
    var map = parseNumberAssignments(settingValue(settingName), parseNumberAssignments(defaultSettings[settingName]));
    map[key] = numeric(value);
    state.settings[settingName] = serializeNumberAssignments(map, assignmentOrder(settingName));
    if (settingName === "modelDecisionThresholds") {
      syncModelAlertThresholdSettings();
    }
    persistSettings();
    state.settingsSaved = false;
    render();
  }

  function updateBooleanAssignmentSetting(settingName, key, enabled) {
    if (!Object.prototype.hasOwnProperty.call(defaultSettings, settingName)) return;
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(String(key || ""))) return;
    var map = parseNumberAssignments(settingValue(settingName), parseNumberAssignments(defaultSettings[settingName]));
    map[key] = enabled ? 1 : 0;
    state.settings[settingName] = serializeNumberAssignments(map, assignmentOrder(settingName));
    persistSettings();
    state.settingsSaved = false;
    render();
  }

  function formatSignalNumber(value, suffix) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "-";
    return number.toLocaleString("ko-KR", {
      maximumFractionDigits: Math.abs(number) >= 10 ? 0 : 1
    }) + (suffix || "");
  }

  function formatSignalRatio(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "-";
    return number.toFixed(number >= 10 ? 0 : 1) + "x";
  }

  function formatSignalVolume(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "-";
    return formatMoney(number);
  }

  function parseValuationAssumptions() {
    var map = {};
    String(settingValue("valuationAssumptions") || "")
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .filter(Boolean)
      .forEach(function (line) {
        var parts = line.split(",").map(function (part) { return part.trim(); });
        var symbol = String(parts[0] || "").toUpperCase();
        if (!symbol) return;
        map[symbol] = {
          symbol: symbol,
          eps: numeric(parts[1]),
          targetPer: numeric(parts[2]),
          margin: numeric(parts[3] || 15)
        };
      });
    return map;
  }

  function parseMarketSignals() {
    var map = {};
    String(settingValue("marketSignalInputs") || "")
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .filter(Boolean)
      .forEach(function (line) {
        var parts = line.split(",").map(function (part) { return part.trim(); });
        var symbol = String(parts[0] || "").toUpperCase();
        if (!symbol) return;
        map[symbol] = {
          symbol: symbol,
          tradeStrength: numeric(parts[1]),
          volumeRatio: numeric(parts[2]),
          buyVolume: numeric(parts[3]),
          sellVolume: numeric(parts[4]),
          bidAskImbalance: numeric(parts[5]),
          priceChangeRate: numeric(parts[6]),
          ma20: numeric(parts[7]),
          ma60: numeric(parts[8]),
          foreignNet: numeric(parts[9]),
          institutionNet: numeric(parts[10]),
          individualNet: numeric(parts[11]),
          source: "manual"
        };
      });
    return map;
  }

  function currentPriceOf(item) {
    var currentPrice = numeric(item.currentPrice);
    if (currentPrice) return currentPrice;
    var quantity = numeric(item.quantity);
    var marketValue = numeric(item.marketValue);
    return quantity ? marketValue / quantity : 0;
  }

  function valuationStatus(currentPrice, fairValue, marginPrice) {
    if (!currentPrice || !fairValue) return { label: "입력 필요", tone: "hold", rank: 4 };
    if (currentPrice <= marginPrice) return { label: "싸다", tone: "watch", rank: 1 };
    if (currentPrice <= fairValue) return { label: "적정권", tone: "hold", rank: 2 };
    if (currentPrice <= fairValue * 1.15) return { label: "비싼 편", tone: "caution", rank: 3 };
    return { label: "비싸다", tone: "danger", rank: 3 };
  }

  function buildValuationForItem(item, assumptions, weights, formula) {
    var symbol = String(item.symbol || "").toUpperCase();
    var assumption = assumptions[symbol] || {};
    var currentPrice = currentPriceOf(item);
    var baseFairValue = assumption.eps && assumption.targetPer ? assumption.eps * assumption.targetPer : 0;
    var margin = assumption.margin || 15;
    var variables = Object.assign({}, weights, {
      eps: assumption.eps || 0,
      targetPer: assumption.targetPer || 0,
      margin: margin,
      currentPrice: currentPrice,
      averagePrice: numeric(item.averagePrice),
      quantity: numeric(item.quantity),
      marketValue: numeric(item.marketValue),
      profitLoss: numeric(item.profitLoss),
      profitLossRate: numeric(item.profitLossRate)
    });
    var formulaResult = formula
      ? evaluateConfiguredFormula(formula, variables, baseFairValue)
      : { value: baseFairValue, error: "", usedFallback: false };
    var fairValue = Math.max(0, numeric(formulaResult.value));
    var marginPrice = fairValue ? fairValue * (1 - margin / 100) : 0;
    var gap = currentPrice && fairValue ? ((fairValue / currentPrice) - 1) * 100 : 0;
    var status = valuationStatus(currentPrice, fairValue, marginPrice);
    var reasons = [];
    if (formulaResult.error) {
      reasons.push("적정가 공식 오류로 기본값을 사용했습니다: " + formulaResult.error);
    }
    if (!fairValue) {
      reasons.push("적정가 공식 결과가 0입니다. EPS, 목표 PER, 가중치를 확인하세요.");
    } else if (!currentPrice) {
      reasons.push("현재가가 필요합니다.");
    } else {
      reasons.push("적정가 " + formatPrice(fairValue, item.currency) + "와 현재가 차이는 " + signedPct(gap) + "입니다.");
      reasons.push("안전마진 " + margin + "% 기준 매수가 상한은 " + formatPrice(marginPrice, item.currency) + "입니다.");
    }
    return {
      symbol: symbol,
      name: item.name || symbol,
      source: item.source || "watchlist",
      sector: item.sector || "-",
      market: item.market || "",
      currency: item.currency || "",
      currentPrice: currentPrice,
      eps: assumption.eps || 0,
      targetPer: assumption.targetPer || 0,
      margin: margin,
      formula: formula,
      formulaError: formulaResult.error,
      fairValue: fairValue,
      marginPrice: marginPrice,
      gap: gap,
      status: status.label,
      tone: status.tone,
      rank: status.rank,
      reasons: reasons
    };
  }

  function buildValuationItems(snapshot) {
    var assumptions = parseValuationAssumptions();
    var weights = formulaWeights();
    var formula = formulaSetting("fairValueFormula");
    return instrumentItems(snapshot)
      .map(function (item) {
        return buildValuationForItem(item, assumptions, weights, formula);
      })
      .sort(function (a, b) {
        if (a.rank !== b.rank) return a.rank - b.rank;
        return a.gap - b.gap;
      });
  }

  function signalValue(raw, keys) {
    var value = 0;
    keys.some(function (key) {
      if (raw && raw[key] != null && raw[key] !== "") {
        value = numeric(raw[key]);
        return true;
      }
      return false;
    });
    return value;
  }

  function marketSignalForItem(item, signalMap) {
    var symbol = String(item.symbol || "").toUpperCase();
    var fromItem = Object.assign({}, item || {}, item.marketSignal || item.tradeSignal || item.signal || {});
    var fromSettings = signalMap[symbol] || {};
    var merged = Object.assign({}, fromItem, fromSettings);
    return {
      symbol: symbol,
      tradeStrength: signalValue(merged, ["tradeStrength", "trade_strength", "executionStrength"]),
      volumeRatio: signalValue(merged, ["volumeRatio", "volume_ratio", "relativeVolume", "volumeMultiple"]),
      buyVolume: signalValue(merged, ["buyVolume", "buy_volume", "buyTradeVolume", "bidVolume"]),
      sellVolume: signalValue(merged, ["sellVolume", "sell_volume", "sellTradeVolume", "askVolume"]),
      bidAskImbalance: signalValue(merged, ["bidAskImbalance", "orderbookImbalance", "imbalance"]),
      priceChangeRate: signalValue(merged, ["priceChangeRate", "changeRate", "changePercent"]),
      ma20: signalValue(merged, ["ma20", "movingAverage20", "sma20"]),
      ma60: signalValue(merged, ["ma60", "movingAverage60", "sma60"]),
      foreignNet: signalValue(merged, ["foreignNet", "foreignNetVolume", "foreign_net_volume", "foreignNetBuy", "foreignInvestorNet", "foreignerNetBuy"]),
      institutionNet: signalValue(merged, ["institutionNet", "institutionNetVolume", "institution_net_volume", "institutionNetBuy", "institutionalNet", "institutionInvestorNet"]),
      individualNet: signalValue(merged, ["individualNet", "individualNetVolume", "individual_net_volume", "individualNetBuy", "retailNet", "personalNetBuy"]),
      source: merged.signalSource || merged.provider || merged.quoteSource || (Object.keys(fromItem).length ? "account" : "")
    };
  }

  function hasMarketSignal(signal) {
    return [
      "tradeStrength",
      "volumeRatio",
      "buyVolume",
      "sellVolume",
      "bidAskImbalance",
      "priceChangeRate",
      "ma20",
      "ma60",
      "foreignNet",
      "institutionNet",
      "individualNet"
    ].some(function (key) {
      return Number(signal[key] || 0) !== 0;
    });
  }

  function buyVolumeShare(signal) {
    var buy = Number(signal.buyVolume || 0);
    var sell = Number(signal.sellVolume || 0);
    var total = buy + sell;
    return total > 0 ? (buy / total) * 100 : 50;
  }

  function modelFeatureVariables(item, signal, valuation) {
    item = item || {};
    signal = signal || {};
    valuation = valuation || {};
    var current = currentPriceOf(item);
    var ma20 = Number(signal.ma20 || item.ma20 || 0);
    var ma60 = Number(signal.ma60 || item.ma60 || 0);
    var tradeStrength = Number(signal.tradeStrength || 100);
    var volumeRatio = Number(signal.volumeRatio || 1);
    var buyVolume = Number(signal.buyVolume || 0);
    var sellVolume = Number(signal.sellVolume || 0);
    var buyShare = buyVolumeShare(signal);
    var foreignNet = Number(signal.foreignNet || 0);
    var institutionNet = Number(signal.institutionNet || 0);
    var individualNet = Number(signal.individualNet || 0);
    var smartMoneyNet = foreignNet + institutionNet;
    var investorBase = Math.abs(foreignNet) + Math.abs(institutionNet) + Math.abs(individualNet);
    var investorBalance = smartMoneyNet - individualNet * 0.35;
    var bidAskImbalance = Number(signal.bidAskImbalance || 0);
    var priceChangeRate = Number(signal.priceChangeRate || 0);
    var trendDistance20 = current && ma20 ? ((current / ma20) - 1) * 100 : 0;
    var trendDistance60 = current && ma60 ? ((current / ma60) - 1) * 100 : 0;
    var maSpread = ma20 && ma60 ? ((ma20 / ma60) - 1) * 100 : 0;
    var volumePressure = clamp((volumeRatio - 1) * 10, -10, 25);
    var trendScore = clamp(trendDistance20 * 0.35 + trendDistance60 * 0.2 + maSpread * 0.4, -15, 15);
    var investorFlowScore = investorBase ? clamp((investorBalance / investorBase) * 100, -30, 30) : 0;
    var executionScore = clamp((tradeStrength - 100) * 0.5, -25, 25);
    var buyShareScore = clamp((buyShare - 50) * 0.7, -25, 25);
    var orderbookScore = clamp(bidAskImbalance * 0.5, -20, 20);
    var momentumScore = clamp(priceChangeRate * 4, -20, 20);
    var flowDirectionScore = clamp(
      executionScore * 0.35
        + buyShareScore * 0.35
        + orderbookScore * 0.2
        + momentumScore * 0.25
        + trendScore * 0.25
        + investorFlowScore * 0.2,
      -25,
      25
    );
    var volumeConfirmation = clamp(flowDirectionScore / 12, -1, 1);
    return {
      tradeStrength: tradeStrength,
      volumeRatio: volumeRatio,
      volumePressure: volumePressure,
      directionalVolumePressure: volumePressure * volumeConfirmation,
      volumeConfirmation: volumeConfirmation,
      volumeDryness: volumeRatio && volumeRatio < 1 ? clamp((1 - volumeRatio) * 10, 0, 10) : 0,
      buyVolume: buyVolume,
      sellVolume: sellVolume,
      buyShare: buyShare,
      sellShare: Math.max(0, 100 - buyShare),
      bidAskImbalance: bidAskImbalance,
      priceChangeRate: priceChangeRate,
      executionScore: executionScore,
      buyShareScore: buyShareScore,
      orderbookScore: orderbookScore,
      momentumScore: momentumScore,
      flowDirectionScore: flowDirectionScore,
      ma20: ma20,
      ma60: ma60,
      trendDistance20: trendDistance20,
      trendDistance60: trendDistance60,
      maSpread: maSpread,
      trendScore: trendScore,
      foreignNet: foreignNet,
      institutionNet: institutionNet,
      individualNet: individualNet,
      smartMoneyNet: smartMoneyNet,
      investorFlowBalance: investorBalance,
      investorFlowScore: investorFlowScore,
      currentPrice: current,
      fairValue: Number(valuation.fairValue || 0),
      fairValueGap: Number(valuation.gap || 0),
      valuationRank: Number(valuation.rank || 0)
    };
  }

  function marketSignalScores(signal, context) {
    context = context || {};
    var valuation = context.valuation || {};
    var item = context.item || {};
    var weights = formulaWeights();
    var featureVars = modelFeatureVariables(item, signal, valuation);
    var valuationGap = Number(valuation.gap || 0);
    var expensivePenalty = valuationGap < 0 ? Math.min(18, Math.abs(valuationGap) / 2) : 0;
    var undervalueBonus = valuationGap > 0 ? Math.min(14, valuationGap / 3) : 0;
    var expensiveBonus = expensivePenalty;
    var flowWeight = Number(weights.flowWeight || 1);
    var valuationWeight = Number(weights.valuationWeight || 1);
    var variables = Object.assign({}, weights, featureVars, {
      expensivePenalty: expensivePenalty,
      expensiveBonus: expensiveBonus,
      undervalueBonus: undervalueBonus,
      profitLossRate: numeric(item.profitLossRate),
      marketValue: numeric(item.marketValue),
      holding: item.source === "watchlist" ? 0 : 1,
      watchlist: item.source === "watchlist" ? 1 : 0
    });
    var fallbackBuyScore = 50
      + (
        featureVars.executionScore * 0.42
        + featureVars.directionalVolumePressure * 0.9
        + featureVars.buyShareScore * 0.55
        + featureVars.orderbookScore * 0.32
        + featureVars.momentumScore * 0.35
        + featureVars.trendScore * 0.45
        + featureVars.investorFlowScore * 0.35
      ) * flowWeight
      + undervalueBonus * valuationWeight
      - expensivePenalty * valuationWeight;
    var fallbackSellScore = 50
      + (
        -featureVars.executionScore * 0.38
        - featureVars.directionalVolumePressure * 0.85
        - featureVars.buyShareScore * 0.55
        - featureVars.orderbookScore * 0.3
        - featureVars.momentumScore * 0.4
        - featureVars.trendScore * 0.35
        - featureVars.investorFlowScore * 0.3
      ) * flowWeight
      + expensiveBonus * valuationWeight;
    var buyResult = evaluateConfiguredFormula(formulaSetting("buyScoreFormula"), variables, fallbackBuyScore);
    var sellResult = evaluateConfiguredFormula(formulaSetting("sellScoreFormula"), variables, fallbackSellScore);
    var errors = [];
    if (buyResult.error) errors.push("참고 매수 계산식 오류: " + buyResult.error);
    if (sellResult.error) errors.push("참고 매도 계산식 오류: " + sellResult.error);
    return {
      buyScore: Math.round(clamp(buyResult.value, 0, 100)),
      sellScore: Math.round(clamp(sellResult.value, 0, 100)),
      buyShare: Math.round(clamp(featureVars.buyShare, 0, 100)),
      errors: errors
    };
  }

  function instrumentItems(snapshot) {
    var toss = snapshot.toss || { positions: [], watchlist: [] };
    var seen = {};
    var items = [];
    (toss.positions || []).forEach(function (item) {
      if (item.source === "cash" || item.sector === "현금" || String(item.symbol || "").toUpperCase() === "CASH") return;
      var symbol = String(item.symbol || "").toUpperCase();
      if (!symbol || seen[symbol]) return;
      seen[symbol] = true;
      items.push(Object.assign({}, item, { source: item.source || "holding" }));
    });
    (toss.watchlist || []).forEach(function (item) {
      var symbol = String(item.symbol || "").toUpperCase();
      if (!symbol || seen[symbol]) return;
      seen[symbol] = true;
      items.push(Object.assign(clientKnownStockInfo(symbol), item, { source: "watchlist" }));
    });
    return items;
  }

  function tradeSignalReasons(signal, scores, valuation, hasData, relationRules) {
    if (!hasData) {
      return ["설정에서 거래량 배율, 매수/매도 체결량, 이동평균을 입력하면 관계 규칙을 평가합니다."];
    }
    var topRule = (relationRules || [])[0] || null;
    var reasons = [
      topRule ? "가장 강한 관계 규칙은 " + topRule.label + "이며 강도는 " + topRule.score + "점입니다." : "아직 강하게 성립한 관계 규칙은 없습니다.",
      "거래량: " + formatSignalRatio(signal.volumeRatio) + "을 보고 이동평균, 가격 변화, 수급 방향과의 관계를 확인합니다.",
      "매수 체결 비중은 " + scores.buyShare + "%이고 호가 불균형은 " + formatSignalNumber(signal.bidAskImbalance, "%") + "입니다."
    ];
    if (signal.ma20 || signal.ma60) {
      reasons.push("이동평균은 20일선 " + formatSignalNumber(signal.ma20, "") + ", 60일선 " + formatSignalNumber(signal.ma60, "") + "을 판단 항목으로 반영합니다.");
    }
    if (signal.foreignNet || signal.institutionNet || signal.individualNet) {
      reasons.push("투자자별 수급은 외국인 " + formatSignalVolume(signal.foreignNet) + ", 기관 " + formatSignalVolume(signal.institutionNet) + ", 개인 " + formatSignalVolume(signal.individualNet) + " 순매수로 검증합니다.");
    }
    if (valuation && valuation.status) {
      reasons.push("밸류에이션 분류는 " + valuation.status + "이며 관계 판단의 참고 정보로만 표시합니다.");
    } else {
      reasons.push("밸류에이션 가정이 없으면 가격·수급·추세 관계만으로 관찰 라벨을 만듭니다.");
    }
    (scores.errors || []).forEach(function (error) {
      reasons.push(error + " 기본 추천 공식을 대신 사용했습니다.");
    });
    return reasons;
  }

  function clientOntologyRuleMatches(item, signal, hasData) {
    var matches = [];
    var pnl = numeric(item.profitLossRate);
    var ma20Distance = numeric(signal && signal.trendDistance20);
    var ma60Distance = numeric(signal && signal.trendDistance60);
    var foreignNet = numeric(signal && signal.foreignNet);
    var institutionNet = numeric(signal && signal.institutionNet);
    var symbol = String(item.symbol || "").toUpperCase();
    if (pnl >= 10 && (ma20Distance <= -2 || ma60Distance <= -5)) {
      matches.push({ label: "수익 보유 + 추세 약화", score: Math.min(100, 55 + Math.abs(ma20Distance) + Math.max(0, pnl - 10)), tone: "caution" });
    }
    if (pnl <= -8 || ma20Distance <= -5) {
      matches.push({ label: "손실 보유 + 기준선 이탈", score: Math.min(100, 60 + Math.abs(pnl) + Math.abs(ma20Distance)), tone: "danger" });
    }
    if ((foreignNet || institutionNet) && ma20Distance) {
      var sameDirection = (ma20Distance > 0 && foreignNet + institutionNet > 0) || (ma20Distance < 0 && foreignNet + institutionNet < 0);
      if (sameDirection) {
        matches.push({ label: "추세와 수급 방향 일치", score: 55 + Math.min(25, Math.abs(ma20Distance)), tone: "watch" });
      }
    }
    if (String(item.source || "") === "watchlist" && ma20Distance <= -2 && ma20Distance >= -8 && ma60Distance >= -1 && numeric(signal && signal.tradeStrength) >= 100) {
      matches.push({ label: "눌림목 + 지지 수급", score: 64 + Math.min(16, numeric(signal.tradeStrength) - 100), tone: "watch" });
    }
    if (["MSTR", "STRC", "COIN", "MARA", "RIOT"].indexOf(symbol) >= 0) {
      matches.push({ label: "비트코인 민감 종목", score: 55, tone: "watch" });
    }
    if (!hasData || !numeric(signal && signal.tradeStrength) || !numeric(signal && signal.ma20)) {
      matches.push({ label: "핵심 데이터 부족", score: 45, tone: "hold" });
    }
    return matches.sort(function (a, b) { return b.score - a.score; });
  }

  function relationDecisionFromClientRules(item, relationRules, hasData) {
    if (!hasData) return { label: "관계 데이터 필요", tone: "hold", priority: 9 };
    var top = (relationRules || [])[0] || { label: "관계 관망", score: 35, tone: "hold" };
    var label = top.label || "관계 관망";
    var holding = item.source !== "watchlist";
    var priority = 6;
    if (/손실|이탈|매도|리스크/.test(label)) priority = holding ? 1 : 4;
    else if (/수익|익절|추세 약화/.test(label)) priority = 2;
    else if (/눌림목|매수/.test(label)) priority = holding ? 4 : 2;
    else if (/방향 일치|민감/.test(label)) priority = 3;
    return {
      label: label,
      tone: top.tone || "watch",
      priority: priority
    };
  }

  function buildTradeSignalItems(snapshot) {
    var signalMap = parseMarketSignals();
    var valuationMap = {};
    buildValuationItems(snapshot).forEach(function (item) {
      valuationMap[item.symbol] = item;
    });
    return instrumentItems(snapshot).map(function (item) {
      var symbol = String(item.symbol || "").toUpperCase();
      var signal = marketSignalForItem(item, signalMap);
      var hasData = hasMarketSignal(signal);
      var valuation = valuationMap[symbol] || null;
      var scores = hasData ? marketSignalScores(signal, { item: item, valuation: valuation }) : { buyScore: 0, sellScore: 0, buyShare: 0, errors: [] };
      var relationRules = clientOntologyRuleMatches(item, signal, hasData);
      var decision = relationDecisionFromClientRules(item, relationRules, hasData);
      return {
        symbol: symbol,
        name: item.name || symbol,
        source: item.source || "watchlist",
        sector: item.sector || "-",
        market: item.market || "",
        currency: item.currency || "",
        currentPrice: currentPriceOf(item),
        averagePrice: numeric(item.averagePrice),
        quantity: numeric(item.quantity),
        sellableQuantity: numeric(item.sellableQuantity || item.quantity),
        marketValue: numeric(item.marketValue),
        profitLoss: numeric(item.profitLoss),
        profitLossRate: numeric(item.profitLossRate),
        signal: signal,
        hasData: hasData,
        buyScore: scores.buyScore,
        sellScore: scores.sellScore,
        buyShare: scores.buyShare,
        valuation: valuation,
        action: decision.label,
        tone: decision.tone,
        priority: decision.priority,
        relationRules: relationRules,
        relationStrength: relationRules.length ? relationRules[0].score : 0,
        reasons: tradeSignalReasons(signal, scores, valuation, hasData, relationRules),
        triggers: ["관계 규칙", "거래량", "이동평균", "투자자 수급"]
      };
    }).sort(function (a, b) {
      if (a.priority !== b.priority) return a.priority - b.priority;
      return b.relationStrength - a.relationStrength;
    });
  }

  function compactSymbolList(symbols) {
    var unique = [];
    (symbols || []).forEach(function (symbol) {
      var key = String(symbol || "").toUpperCase();
      if (key && unique.indexOf(key) < 0) unique.push(key);
    });
    if (!unique.length) return "없음";
    var visible = unique.slice(0, 8).map(function (symbol) {
      return stockDisplayName(symbol);
    }).join(", ");
    return unique.length > 8 ? visible + " 외 " + (unique.length - 8) + "개" : visible;
  }

  function diagnosticTone(missingCount, totalCount) {
    if (!totalCount) return "hold";
    if (missingCount >= totalCount) return "danger";
    if (missingCount > 0) return "caution";
    return "watch";
  }

  function diagnosticCoverage(totalCount, missingCount) {
    return Math.max(0, totalCount - missingCount) + "/" + totalCount;
  }

  function settingUsesDefault(name) {
    return String(settingValue(name) || "").trim() === String(defaultSettings[name] || "").trim();
  }

  function settingEnabled(name) {
    var value = String(settingValue(name) || defaultSettings[name] || "1").trim().toLowerCase();
    return ["0", "false", "no", "off", "disabled"].indexOf(value) < 0;
  }

  function newsProviderLabel(value) {
    var key = String(value || "auto").toLowerCase().replace(/[-\s]/g, "_");
    if (key === "alpha" || key === "alphavantage" || key === "alpha_vantage") return "Alpha Vantage";
    if (key === "gdelt") return "GDELT";
    return "Auto";
  }

  function strategyDataDiagnostics(snapshot) {
    var items = buildTradeSignalItems(snapshot);
    var total = items.length;
    var modelThresholds = modelDecisionThresholds();
    var thresholds = alertThresholds();
    var thresholdMismatch = Math.round(Number(modelThresholds.modelBuy || 0)) !== Math.round(Number(thresholds.modelBuyScore || 0))
      || Math.round(Number(modelThresholds.modelSell || 0)) !== Math.round(Number(thresholds.modelSellScore || 0));
    var toss = snapshot && snapshot.toss ? snapshot.toss : {};

    function missingSymbols(predicate) {
      return items.filter(predicate).map(function (item) { return item.symbol; });
    }

    var missingValuation = missingSymbols(function (item) {
      return !item.valuation || !Number(item.valuation.fairValue || 0);
    });
    var missingPrice = missingSymbols(function (item) {
      return !Number(item.currentPrice || 0);
    });
    var missingTradeStrength = missingSymbols(function (item) {
      return !Number(item.signal && item.signal.tradeStrength || 0);
    });
    var missingExecutionVolume = missingSymbols(function (item) {
      var signal = item.signal || {};
      return !Number(signal.buyVolume || 0) || !Number(signal.sellVolume || 0);
    });
    var missingInvestorFlow = missingSymbols(function (item) {
      var signal = item.signal || {};
      return !Number(signal.foreignNet || 0) && !Number(signal.institutionNet || 0) && !Number(signal.individualNet || 0);
    });
    var missingOrderbook = missingSymbols(function (item) {
      return !Number(item.signal && item.signal.bidAskImbalance || 0);
    });

    return [
      {
        label: "Toss 계좌 데이터",
        value: toss.mode === "live" ? "live" : (toss.mode || "대기"),
        tone: toss.mode === "live" ? "watch" : "caution",
        description: toss.status || "계좌 연결 상태를 확인합니다.",
        symbols: [],
        action: "계정 탭의 Toss 연결값 확인"
      },
      {
        label: "현재가",
        value: diagnosticCoverage(total, missingPrice.length),
        tone: diagnosticTone(missingPrice.length, total),
        description: "현재가가 있어야 적정가와 현재가 차이, 가격 기준을 계산합니다.",
        symbols: missingPrice,
        action: "Toss prices/candles 응답 또는 종목 코드 확인"
      },
      {
        label: "적정가 가정",
        value: diagnosticCoverage(total, missingValuation.length),
        tone: diagnosticTone(missingValuation.length, total),
        description: "EPS, 목표 PER, 안전마진이 있어야 싸다/비싸다 판단이 안정됩니다.",
        symbols: missingValuation,
        action: "투자 분석 탭의 종목별 EPS/PER 입력"
      },
      {
        label: "체결강도",
        value: diagnosticCoverage(total, missingTradeStrength.length),
        tone: diagnosticTone(missingTradeStrength.length, total),
        description: "체결강도가 없으면 매수/매도 방향 점수가 중립값으로 계산됩니다.",
        symbols: missingTradeStrength,
        action: "Toss 체결 데이터 연결 또는 수동 수급 입력"
      },
      {
        label: "매수/매도 체결량",
        value: diagnosticCoverage(total, missingExecutionVolume.length),
        tone: diagnosticTone(missingExecutionVolume.length, total),
        description: "매수 체결 비중과 방향성 거래량을 계산하는 핵심 입력입니다.",
        symbols: missingExecutionVolume,
        action: "marketSignalInputs에 매수량/매도량 보강"
      },
      {
        label: "투자자 수급",
        value: diagnosticCoverage(total, missingInvestorFlow.length),
        tone: diagnosticTone(missingInvestorFlow.length, total),
        description: "외국인·기관·개인 순매수가 없으면 스마트머니 점수는 중립 처리됩니다.",
        symbols: missingInvestorFlow,
        action: "외국인/기관/개인 순매수 입력 또는 공급자 연결"
      },
      {
        label: "호가 불균형",
        value: diagnosticCoverage(total, missingOrderbook.length),
        tone: diagnosticTone(missingOrderbook.length, total),
        description: "호가 압력이 없으면 단기 진입/축소 신호가 약해집니다.",
        symbols: missingOrderbook,
        action: "호가 데이터 연결 또는 수동 수급 입력"
      },
      {
        label: "모델-알림 기준",
        value: thresholdMismatch ? "불일치" : "동기화",
        tone: thresholdMismatch ? "caution" : "watch",
        description: "모델 매수/매도 기준과 실제 알림 발송 기준을 같은 값으로 맞춥니다.",
        symbols: thresholdMismatch ? ["modelBuy/modelSell"] : [],
        action: "모델 설정 저장"
      },
      {
        label: "공식 저장 상태",
        value: [
          "buyScoreFormula",
          "sellScoreFormula",
          "profitTakeScoreFormula",
          "lossCutScoreFormula",
          "notificationScoreFormula"
        ].every(settingUsesDefault) ? "기본 공식" : "사용자 공식",
        tone: "watch",
        description: "기본 공식은 방향성 거래량, 이동평균, 투자자 수급, 보유 손익, 알림 중요도를 함께 씁니다.",
        symbols: [],
        action: "고급 공식은 필요할 때만 수정"
      }
    ];
  }

  function labActionPrices(item) {
    var valuation = item.valuation || {};
    var current = Number(item.currentPrice || valuation.currentPrice || 0);
    var average = Number(item.averagePrice || 0);
    var reference = average || current;
    var fairValue = Number(valuation.fairValue || 0);
    var marginPrice = Number(valuation.marginPrice || 0);
    var buyLimit = marginPrice || (current ? current * 0.92 : 0);
    var stopPrice = reference ? reference * 0.92 : (buyLimit ? buyLimit * 0.92 : 0);
    var trimOne = reference ? reference * 1.12 : (fairValue ? fairValue * 0.9 : 0);
    var trimTwoBase = reference ? reference * 1.25 : 0;
    var trimTwo = fairValue ? Math.max(fairValue, trimTwoBase) : trimTwoBase;
    if (fairValue && fairValue > reference && trimOne > fairValue) trimOne = fairValue;
    if (fairValue && trimTwo < trimOne) trimTwo = trimOne;
    return [
      { label: "현재가", value: current, tone: "hold" },
      { label: item.source === "watchlist" ? "진입 기준" : "평단", value: reference, tone: "hold" },
      { label: "매수 상한", value: buyLimit, tone: "watch" },
      { label: "손절 기준", value: stopPrice, tone: "danger" },
      { label: "1차 매도", value: trimOne, tone: "caution" },
      { label: "2차 매도", value: trimTwo, tone: "danger" },
      { label: "적정가", value: fairValue, tone: "watch" }
    ];
  }

  function labDraftDefaults(item) {
    var valuation = item.valuation || {};
    var current = Number(item.currentPrice || 0);
    var targetReturn = current && valuation.fairValue ? ((valuation.fairValue / current) - 1) * 100 : 15;
    return {
      thesisScore: item.hasData ? item.buyScore : 50,
      riskScore: item.hasData ? item.sellScore : 50,
      confidenceScore: item.hasData ? Math.max(item.buyScore, item.sellScore) : 50,
      targetReturn: Math.round(clamp(targetReturn, -50, 200)),
      stopLoss: 8,
      positionSize: item.source === "watchlist" ? 10 : 100
    };
  }

  function labDraftForItem(item) {
    return labDraftDefaults(item);
  }

  function modelFormulaVariables(item) {
    var valuation = item.valuation || {};
    var signal = item.signal || {};
    var draft = labDraftForItem(item);
    var weights = formulaWeights();
    var valuationGap = Number(valuation.gap || 0);
    var expensivePenalty = valuationGap < 0 ? Math.min(18, Math.abs(valuationGap) / 2) : 0;
    var undervalueBonus = valuationGap > 0 ? Math.min(14, valuationGap / 3) : 0;
    var featureVars = modelFeatureVariables(item, signal, valuation);
    var buyShare = Number(item.buyShare || featureVars.buyShare || 0);
    return Object.assign({}, weights, featureVars, {
      buyScore: Number(item.buyScore || 0),
      sellScore: Number(item.sellScore || 0),
      systemBuyScore: Number(item.buyScore || 0),
      systemSellScore: Number(item.sellScore || 0),
      buyShare: buyShare,
      sellShare: Math.max(0, 100 - buyShare),
      averagePrice: Number(item.averagePrice || 0),
      fairValueGap: valuationGap,
      expensivePenalty: expensivePenalty,
      expensiveBonus: expensivePenalty,
      undervalueBonus: undervalueBonus,
      profitLossRate: Number(item.profitLossRate || 0),
      buyReasonScore: Number(draft.buyReasonScore || draft.thesisScore || 0),
      thesisScore: Number(draft.thesisScore || 0),
      riskScore: Number(draft.riskScore || 0),
      confidenceScore: Number(draft.confidenceScore || 0),
      targetReturn: Number(draft.targetReturn || 0),
      stopLoss: Number(draft.stopLoss || 0),
      positionSize: Number(draft.positionSize || 0),
      holding: item.source === "watchlist" ? 0 : 1,
      watchlist: item.source === "watchlist" ? 1 : 0
    });
  }

  function customModelDecision(item, buyScore, sellScore) {
    var thresholds = modelDecisionThresholds();
    var holding = item.source !== "watchlist";
    if (holding && sellScore >= thresholds.modelSell) return { label: "내 모델 분할매도", tone: "danger", rank: 1 };
    if (holding && sellScore >= thresholds.modelReduce) return { label: "내 모델 리스크 축소", tone: "caution", rank: 2 };
    if (buyScore >= thresholds.modelBuy && !holding) return { label: "내 모델 매수 후보", tone: "watch", rank: 2 };
    if (buyScore >= thresholds.modelAdd && holding) return { label: "내 모델 보유 강화", tone: "watch", rank: 3 };
    if (Math.max(buyScore, sellScore) >= thresholds.modelHold) return { label: "내 모델 관찰", tone: "hold", rank: 4 };
    return { label: "내 모델 관망", tone: "hold", rank: 5 };
  }

  function customModelScoresFromVariables(item, variables) {
    variables = variables || {};
    var fallbackBuy = variables.buyScore * 0.35
      + Number(variables.buyReasonScore || variables.thesisScore || 0) * Number(variables.buyReasonWeight || variables.thesisWeight || 0.25)
      + variables.confidenceScore * Number(variables.confidenceWeight || 0.15)
      + Math.max(0, variables.targetReturn) * 0.15
      + variables.undervalueBonus * Number(variables.valuationWeight || 1)
      - variables.riskScore * Number(variables.riskControlWeight || 0.35);
    var fallbackSell = variables.sellScore * 0.35
      + variables.riskScore * Number(variables.riskControlWeight || 0.35)
      + variables.expensivePenalty * Number(variables.valuationWeight || 1)
      + Math.max(0, -variables.targetReturn) * 0.2
      - Number(variables.buyReasonScore || variables.thesisScore || 0) * 0.1;
    var buyResult = evaluateConfiguredFormula(formulaSetting("customBuyModelFormula"), variables, fallbackBuy);
    var sellResult = evaluateConfiguredFormula(formulaSetting("customSellModelFormula"), variables, fallbackSell);
    var buy = Math.round(clamp(buyResult.value, 0, 100));
    var sell = Math.round(clamp(sellResult.value, 0, 100));
    var decision = customModelDecision(item, buy, sell);
    var errors = [];
    if (buyResult.error) errors.push("참고 매수 계산식 오류: " + buyResult.error);
    if (sellResult.error) errors.push("참고 매도 계산식 오류: " + sellResult.error);
    return {
      buyScore: buy,
      sellScore: sell,
      action: decision.label,
      tone: decision.tone,
      rank: decision.rank,
      errors: errors,
      variables: variables
    };
  }

  function customModelScores(item) {
    return customModelScoresFromVariables(item, modelFormulaVariables(item));
  }

  function modelFeatureGroups() {
    return [
      {
        key: "execution",
        label: "체결량/호가",
        neutral: function () {
          return { tradeStrength: 100, buyVolume: 50, sellVolume: 50, bidAskImbalance: 0 };
        }
      },
      {
        key: "volume",
        label: "거래량",
        neutral: function () {
          return { volumeRatio: 1 };
        }
      },
      {
        key: "trend",
        label: "이동평균",
        neutral: function (item) {
          var current = currentPriceOf(item || {});
          return { ma20: current, ma60: current };
        }
      },
      {
        key: "investor",
        label: "투자자 수급",
        neutral: function () {
          return { foreignNet: 0, institutionNet: 0, individualNet: 0 };
        }
      }
    ];
  }

  function modelWithFeatureOverrides(item, overrides) {
    var baseSignal = Object.assign({}, item.signal || {}, overrides || {});
    var valuation = item.valuation || buildValuationForItem(item, parseValuationAssumptions(), formulaWeights(), formulaSetting("fairValueFormula"));
    var scores = marketSignalScores(baseSignal, { item: item, valuation: valuation });
    var nextItem = Object.assign({}, item, {
      signal: baseSignal,
      hasData: true,
      buyScore: scores.buyScore,
      sellScore: scores.sellScore,
      buyShare: scores.buyShare,
      valuation: valuation
    });
    return customModelScores(nextItem);
  }

  function modelFeatureContributions(variables) {
    variables = variables || {};
    var flowWeight = Number(variables.flowWeight || 1);
    var valuationWeight = Number(variables.valuationWeight || 1);
    return [
      {
        key: "execution",
        label: "체결 방향",
        buy: Number(variables.executionScore || 0) * 0.42 * flowWeight,
        sell: -Number(variables.executionScore || 0) * 0.38 * flowWeight,
        description: "실제 데이터가 연결될 때만 반영되는 체결 방향"
      },
      {
        key: "directionalVolume",
        label: "방향성 거래량",
        buy: Number(variables.directionalVolumePressure || 0) * 0.9 * flowWeight,
        sell: -Number(variables.directionalVolumePressure || 0) * 0.85 * flowWeight,
        description: "거래량 급증이 매수 쪽인지 매도 쪽인지 확인"
      },
      {
        key: "buyShare",
        label: "매수비중",
        buy: Number(variables.buyShareScore || 0) * 0.55 * flowWeight,
        sell: -Number(variables.buyShareScore || 0) * 0.55 * flowWeight,
        description: "매수 체결량과 매도 체결량의 상대 비중"
      },
      {
        key: "orderbook",
        label: "호가",
        buy: Number(variables.orderbookScore || 0) * 0.32 * flowWeight,
        sell: -Number(variables.orderbookScore || 0) * 0.3 * flowWeight,
        description: "호가 잔량 불균형"
      },
      {
        key: "momentum",
        label: "가격 변화",
        buy: Number(variables.momentumScore || 0) * 0.35 * flowWeight,
        sell: -Number(variables.momentumScore || 0) * 0.4 * flowWeight,
        description: "당일 또는 입력 기간 가격 변화"
      },
      {
        key: "trend",
        label: "이동평균",
        buy: Number(variables.trendScore || 0) * 0.45 * flowWeight,
        sell: -Number(variables.trendScore || 0) * 0.35 * flowWeight,
        description: "20일선, 60일선, 단기/중기 간격"
      },
      {
        key: "investor",
        label: "투자자 수급",
        buy: Number(variables.investorFlowScore || 0) * 0.35 * flowWeight,
        sell: -Number(variables.investorFlowScore || 0) * 0.3 * flowWeight,
        description: "외국인과 기관 순매수 대비 개인 순매수"
      },
      {
        key: "valuation",
        label: "밸류에이션",
        buy: (Number(variables.undervalueBonus || 0) - Number(variables.expensivePenalty || 0)) * valuationWeight,
        sell: Number(variables.expensiveBonus || variables.expensivePenalty || 0) * valuationWeight,
        description: "적정가 대비 저평가 또는 고평가"
      }
    ];
  }

  function modelFeatureAudit(item, model) {
    var baseline = model || customModelScores(item);
    var replay = customModelScoresFromVariables(item, Object.assign({}, baseline.variables || modelFormulaVariables(item)));
    var stable = replay.buyScore === baseline.buyScore
      && replay.sellScore === baseline.sellScore
      && replay.action === baseline.action;
    var groups = modelFeatureGroups().map(function (group) {
      var next = modelWithFeatureOverrides(item, group.neutral(item));
      var buyDelta = next.buyScore - baseline.buyScore;
      var sellDelta = next.sellScore - baseline.sellScore;
      var changed = next.action !== baseline.action || Math.abs(buyDelta) >= 3 || Math.abs(sellDelta) >= 3;
      return {
        key: group.key,
        label: group.label,
        buyDelta: buyDelta,
        sellDelta: sellDelta,
        action: next.action,
        changed: changed
      };
    });
    var variables = baseline.variables || {};
    return { stable: stable, replay: replay, groups: groups, variables: variables, contributions: modelFeatureContributions(variables) };
  }

  function modelStatsForItems(items) {
    var scored = items.map(function (item) {
      return {
        relationStrength: Number(item.relationStrength || 0),
        tone: item.tone || "hold"
      };
    });
    var relationAverage = scored.length ? scored.reduce(function (sum, score) { return sum + score.relationStrength; }, 0) / scored.length : 0;
    var riskAverage = scored.length ? scored.reduce(function (sum, score) {
      return sum + (score.tone === "danger" || score.tone === "caution" ? score.relationStrength : 0);
    }, 0) / scored.length : 0;
    var actionCount = scored.filter(function (score) {
      return score.relationStrength >= 55 || score.tone === "danger" || score.tone === "caution";
    }).length;
    return {
      buyAverage: relationAverage,
      sellAverage: riskAverage,
      relationAverage: relationAverage,
      riskAverage: riskAverage,
      actionCount: actionCount
    };
  }

  function alertRules() {
    return parseNumberAssignments(settingValue("alertRules"), parseNumberAssignments(defaultSettings.alertRules));
  }

  function alertThresholds() {
    return parseNumberAssignments(settingValue("alertThresholds"), parseNumberAssignments(defaultSettings.alertThresholds));
  }

  function relationRuleThresholds() {
    return parseNumberAssignments(settingValue("relationRuleThresholds"), parseNumberAssignments(defaultSettings.relationRuleThresholds));
  }

  function alertCadenceMinutes() {
    return parseNumberAssignments(settingValue("alertCadenceMinutes"), parseNumberAssignments(defaultSettings.alertCadenceMinutes));
  }

  function enabledAlertRule(rules, key) {
    return Number((rules || {})[key]) !== 0;
  }

  function alertSeverityRank(severity) {
    var ranks = { danger: 4, caution: 3, watch: 2, info: 1 };
    return ranks[severity] || 0;
  }

  function alertSeverityLabel(severity) {
    var labels = { danger: "긴급", caution: "주의", watch: "관찰", info: "정보" };
    return labels[severity] || "정보";
  }

  function alertRuleLabel(key) {
    var rule = alertRuleCatalog.filter(function (item) { return item.key === key; })[0];
    return rule ? rule.label : key;
  }

  function addAlert(alerts, rules, alert) {
    if (!alert || !alert.rule || !enabledAlertRule(rules, alert.rule)) return;
    alerts.push(Object.assign({
      id: [alert.rule, alert.symbol || "account", alert.title || ""].join(":"),
      severity: "info",
      value: "",
      threshold: "",
      source: alertRuleLabel(alert.rule)
    }, alert));
  }

  function labActionPriceMap(item) {
    var map = {};
    labActionPrices(item).forEach(function (line) {
      map[line.label] = Number(line.value || 0);
    });
    return map;
  }

  function priceBelowOrNear(current, target, nearPercent) {
    if (!current || !target) return false;
    return current <= target * (1 + Number(nearPercent || 0) / 100);
  }

  function priceAboveOrNear(current, target, nearPercent) {
    if (!current || !target) return false;
    return current >= target * (1 - Number(nearPercent || 0) / 100);
  }

  function addPriceAlerts(alerts, rules, thresholds, item) {
    var current = Number(item.currentPrice || 0);
    if (!current) return;
    var prices = labActionPriceMap(item);
    var near = Number(thresholds.priceNearPercent || 0);
    if (priceBelowOrNear(current, prices["매수 상한"], near)) {
      addAlert(alerts, rules, {
        rule: "priceBuyLimit",
        severity: "watch",
        symbol: item.symbol,
        title: item.name + " 매수 상한 접근",
        message: "현재가가 실험실 매수 상한 기준에 접근했습니다.",
        value: formatPrice(current, item.currency),
        threshold: formatPrice(prices["매수 상한"], item.currency),
        source: "가격선"
      });
    }
    if (priceBelowOrNear(current, prices["손절 기준"], near)) {
      addAlert(alerts, rules, {
        rule: "priceStop",
        severity: item.source === "watchlist" ? "caution" : "danger",
        symbol: item.symbol,
        title: item.name + " 손절 기준 접근",
        message: "현재가가 손절 기준선에 접근했습니다. 보유 사유와 리스크 허용폭을 다시 확인해야 합니다.",
        value: formatPrice(current, item.currency),
        threshold: formatPrice(prices["손절 기준"], item.currency),
        source: "가격선"
      });
    }
    if (priceAboveOrNear(current, prices["2차 매도"], near) || priceAboveOrNear(current, prices["1차 매도"], near)) {
      var trimTarget = priceAboveOrNear(current, prices["2차 매도"], near) ? prices["2차 매도"] : prices["1차 매도"];
      addAlert(alerts, rules, {
        rule: "priceTrim",
        severity: priceAboveOrNear(current, prices["2차 매도"], near) ? "danger" : "caution",
        symbol: item.symbol,
        title: item.name + " 분할매도 기준 접근",
        message: "현재가가 실험실 매도 기준선에 접근했습니다.",
        value: formatPrice(current, item.currency),
        threshold: formatPrice(trimTarget, item.currency),
        source: "가격선"
      });
    }
  }

  function addModelAlerts(alerts, rules, thresholds, item) {
    var model = customModelScores(item);
    if (model.buyScore >= Number(thresholds.modelBuyScore || 0)) {
      addAlert(alerts, rules, {
        rule: "modelBuy",
        severity: item.source === "watchlist" ? "watch" : "info",
        symbol: item.symbol,
        title: item.name + " 내 모델 매수 신호",
        message: model.action,
        value: Math.round(model.buyScore) + "점",
        threshold: Math.round(thresholds.modelBuyScore || 0) + "점",
        source: "내 모델"
      });
    }
    if (model.sellScore >= Number(thresholds.modelSellScore || 0)) {
      addAlert(alerts, rules, {
        rule: "modelSell",
        severity: item.source === "watchlist" ? "caution" : "danger",
        symbol: item.symbol,
        title: item.name + " 내 모델 매도 신호",
        message: model.action,
        value: Math.round(model.sellScore) + "점",
        threshold: Math.round(thresholds.modelSellScore || 0) + "점",
        source: "내 모델"
      });
    }
    var gap = Math.abs(Number(model.buyScore || 0) - Number(model.sellScore || 0));
    if (gap >= Number(thresholds.modelScoreGap || 0)) {
      addAlert(alerts, rules, {
        rule: "modelScoreGap",
        severity: model.sellScore > model.buyScore ? "caution" : "watch",
        symbol: item.symbol,
        title: item.name + " 모델 방향성 확대",
        message: "매수 점수와 매도 점수의 차이가 커졌습니다.",
        value: Math.round(model.buyScore) + " / " + Math.round(model.sellScore),
        threshold: Math.round(thresholds.modelScoreGap || 0) + "점 차이",
        source: "내 모델"
      });
    }
  }

  function addFlowAlerts(alerts, rules, thresholds, item) {
    var signal = item.signal || {};
    if (!item.hasData) return;
    var volumeRatio = Number(signal.volumeRatio || 0);
    var buyShare = Number(item.buyShare || 0);
    var sellShare = Math.max(0, 100 - buyShare);
    var imbalance = Number(signal.bidAskImbalance || 0);
    var priceChange = Number(signal.priceChangeRate || 0);
    if (volumeRatio >= Number(thresholds.volumeRatioHigh || 0)) {
      addAlert(alerts, rules, {
        rule: "flowVolume",
        severity: "watch",
        symbol: item.symbol,
        title: item.name + " 거래량 급증",
        message: "평소보다 거래량이 커졌습니다.",
        value: formatSignalRatio(volumeRatio),
        threshold: formatSignalRatio(thresholds.volumeRatioHigh),
        source: "수급"
      });
    }
    if (buyShare >= Number(thresholds.buyShareHigh || 0)) {
      addAlert(alerts, rules, {
        rule: "flowBuyShare",
        severity: "watch",
        symbol: item.symbol,
        title: item.name + " 매수 체결 우위",
        message: "매수 체결 비중이 높습니다.",
        value: pct(buyShare),
        threshold: pct(thresholds.buyShareHigh),
        source: "수급"
      });
    }
    if (sellShare >= Number(thresholds.sellShareHigh || 0)) {
      addAlert(alerts, rules, {
        rule: "flowSellShare",
        severity: item.source === "watchlist" ? "caution" : "danger",
        symbol: item.symbol,
        title: item.name + " 매도 체결 우위",
        message: "매도 체결 비중이 높습니다.",
        value: pct(sellShare),
        threshold: pct(thresholds.sellShareHigh),
        source: "수급"
      });
    }
    if (Math.abs(imbalance) >= Number(thresholds.orderbookImbalance || 0)) {
      addAlert(alerts, rules, {
        rule: "flowOrderbook",
        severity: imbalance < 0 ? "caution" : "watch",
        symbol: item.symbol,
        title: item.name + " 호가 불균형",
        message: imbalance < 0 ? "매도 호가 쪽 압력이 큽니다." : "매수 호가 쪽 압력이 큽니다.",
        value: signedPct(imbalance),
        threshold: pct(thresholds.orderbookImbalance),
        source: "수급"
      });
    }
    if (priceChange >= Number(thresholds.momentumUp || 0)) {
      addAlert(alerts, rules, {
        rule: "trendMomentum",
        severity: "watch",
        symbol: item.symbol,
        title: item.name + " 상승 모멘텀",
        message: "단기 가격 변화율이 상승 임계값을 넘었습니다.",
        value: signedPct(priceChange),
        threshold: signedPct(thresholds.momentumUp),
        source: "추세"
      });
    }
    if (priceChange <= Number(thresholds.momentumDown || 0)) {
      addAlert(alerts, rules, {
        rule: "trendPullback",
        severity: item.source === "watchlist" ? "caution" : "danger",
        symbol: item.symbol,
        title: item.name + " 하락 압력",
        message: "단기 가격 변화율이 하락 임계값을 밑돌았습니다.",
        value: signedPct(priceChange),
        threshold: signedPct(thresholds.momentumDown),
        source: "추세"
      });
    }
  }

  function addHoldingAlerts(alerts, rules, thresholds, item, portfolio) {
    if (item.source === "watchlist") return;
    var profitRate = Number(item.profitLossRate || 0);
    if (profitRate >= Number(thresholds.profitRateHigh || 0)) {
      addAlert(alerts, rules, {
        rule: "holdingProfit",
        severity: "caution",
        symbol: item.symbol,
        title: item.name + " 수익 구간",
        message: "익절 또는 비중 조절 기준을 확인할 구간입니다.",
        value: signedPct(profitRate),
        threshold: signedPct(thresholds.profitRateHigh),
        source: "보유"
      });
    }
    if (profitRate <= Number(thresholds.lossRateLow || 0)) {
      addAlert(alerts, rules, {
        rule: "holdingLoss",
        severity: "danger",
        symbol: item.symbol,
        title: item.name + " 손실 구간",
        message: "손실 허용폭과 손절 기준을 다시 확인할 구간입니다.",
        value: signedPct(profitRate),
        threshold: signedPct(thresholds.lossRateLow),
        source: "보유"
      });
    }
    var invested = Number(portfolio && (portfolio.invested || portfolio.total) || 0);
    var weight = invested ? (Number(item.marketValue || 0) / invested) * 100 : 0;
    if (weight >= Number(thresholds.positionWeightHigh || 0)) {
      addAlert(alerts, rules, {
        rule: "holdingConcentration",
        severity: "caution",
        symbol: item.symbol,
        title: item.name + " 단일 종목 비중 확대",
        message: "단일 보유 종목 비중이 설정값 이상입니다.",
        value: pct(weight),
        threshold: pct(thresholds.positionWeightHigh),
        source: "보유"
      });
    }
  }

  function addPortfolioAlerts(alerts, rules, thresholds, snapshot) {
    var portfolio = snapshot.portfolio || {};
    (portfolio.sectors || []).forEach(function (sector) {
      if (!sector || sector.sector === "현금") return;
      var ratio = Number(sector.ratio || 0);
      if (ratio >= Number(thresholds.sectorWeightHigh || 0)) {
        addAlert(alerts, rules, {
          rule: "sectorConcentration",
          severity: "caution",
          title: sector.sector + " 섹터 비중 확대",
          message: "계좌 내 섹터 노출이 설정값 이상입니다.",
          value: pct(ratio),
          threshold: pct(thresholds.sectorWeightHigh),
          source: "포트폴리오"
        });
      }
    });
    var markets = Array.isArray(portfolio.markets) ? portfolio.markets : [];
    if (!markets.length && Number(portfolio.total || 0) > 0) {
      markets = [{
        key: "total",
        label: "전체",
        cashRatio: Math.round((Number(portfolio.cash || 0) / Number(portfolio.total || 1)) * 100)
      }];
    }
    markets.forEach(function (market) {
      var cashRatio = Number(market.cashRatio || 0);
      if (cashRatio <= Number(thresholds.marketCashLow || 0)) {
        addAlert(alerts, rules, {
          rule: "marketCashLow",
          severity: cashRatio <= Number(thresholds.marketCashLow || 0) / 2 ? "danger" : "caution",
          title: (market.label || "전체") + " 현금 비중 부족",
          message: "신규 매수 전에 시장별 주문 가능 현금과 목표 비중을 확인해야 합니다.",
          value: pct(cashRatio),
          threshold: pct(thresholds.marketCashLow),
          source: "포트폴리오"
        });
      }
    });
  }

  function snapshotStamp(snapshot) {
    var toss = snapshot.toss || {};
    var raw = snapshot.generatedAt || snapshot.updatedAt || snapshot.asOf || toss.generatedAt || toss.updatedAt || toss.fetchedAt || "";
    var stamp = Date.parse(raw);
    return Number.isFinite(stamp) ? { raw: raw, stamp: stamp } : null;
  }

  function addDataAlerts(alerts, rules, thresholds, snapshot) {
    var toss = snapshot.toss || {};
    if (toss.mode !== "live") {
      addAlert(alerts, rules, {
        rule: "tossConnection",
        severity: "caution",
        title: "토스 live 연결 확인",
        message: toss.status || "토스 live 연결 상태를 확인해야 합니다.",
        value: toss.mode || "unknown",
        threshold: "live",
        source: "데이터"
      });
    }
    if (toss.mode === "live" && Array.isArray(toss.positions) && toss.positions.length === 0) {
      addAlert(alerts, rules, {
        rule: "tossConnection",
        severity: "caution",
        title: "보유 종목 없음",
        message: "토스 연결은 성공했지만 보유 종목 배열이 비어 있습니다.",
        value: "0개",
        threshold: "1개 이상",
        source: "데이터"
      });
    }
    var stamp = snapshotStamp(snapshot);
    if (stamp) {
      var minutes = (Date.now() - stamp.stamp) / 60000;
      if (minutes >= Number(thresholds.staleMinutes || 0)) {
        addAlert(alerts, rules, {
          rule: "dataFreshness",
          severity: minutes >= Number(thresholds.staleMinutes || 0) * 2 ? "caution" : "info",
          title: "데이터 갱신 지연",
          message: "마지막 데이터 생성 시각이 설정값보다 오래되었습니다.",
          value: Math.round(minutes) + "분",
          threshold: Math.round(thresholds.staleMinutes || 0) + "분",
          source: "데이터"
        });
      }
    }
  }

  function orderCandidates(snapshot) {
    var toss = snapshot.toss || {};
    return []
      .concat(Array.isArray(snapshot.orders) ? snapshot.orders : [])
      .concat(Array.isArray(toss.orders) ? toss.orders : [])
      .concat(Array.isArray(toss.orderStatus) ? toss.orderStatus : []);
  }

  function addOrderAlerts(alerts, rules, thresholds, snapshot) {
    orderCandidates(snapshot).forEach(function (order) {
      var status = String(order.status || order.orderStatus || order.state || "").toLowerCase();
      var symbol = String(order.symbol || order.ticker || order.stockCode || "").toUpperCase();
      var name = order.name || order.stockName || symbol || "주문";
      var createdAt = Date.parse(order.createdAt || order.orderTime || order.orderedAt || "");
      var ageMinutes = Number.isFinite(createdAt) ? (Date.now() - createdAt) / 60000 : 0;
      if (/pending|open|wait|partial|미체결|접수|부분/.test(status) && ageMinutes >= Number(thresholds.pendingOrderMinutes || 0)) {
        addAlert(alerts, rules, {
          rule: "orderPending",
          severity: "caution",
          symbol: symbol,
          title: name + " 미체결 주문",
          message: "미체결 주문이 설정 시간보다 오래 남아 있습니다.",
          value: Math.round(ageMinutes) + "분",
          threshold: Math.round(thresholds.pendingOrderMinutes || 0) + "분",
          source: "주문"
        });
      }
      if (/reject|fail|error|거부|실패/.test(status)) {
        addAlert(alerts, rules, {
          rule: "orderReject",
          severity: "danger",
          symbol: symbol,
          title: name + " 주문 실패",
          message: "주문 상태가 거부 또는 실패로 표시되었습니다.",
          value: order.status || order.orderStatus || "-",
          threshold: "정상",
          source: "주문"
        });
      }
    });
  }

  function buildAlertItems(snapshot) {
    if (!snapshot) return [];
    var rules = alertRules();
    var thresholds = alertThresholds();
    var alerts = [];
    var items = buildTradeSignalItems(snapshot);
    var portfolio = snapshot.portfolio || {};
    items.forEach(function (item) {
      addPriceAlerts(alerts, rules, thresholds, item);
      addModelAlerts(alerts, rules, thresholds, item);
      addFlowAlerts(alerts, rules, thresholds, item);
      addHoldingAlerts(alerts, rules, thresholds, item, portfolio);
    });
    addPortfolioAlerts(alerts, rules, thresholds, snapshot);
    addDataAlerts(alerts, rules, thresholds, snapshot);
    addOrderAlerts(alerts, rules, thresholds, snapshot);
    return alerts.sort(function (a, b) {
      var severityDiff = alertSeverityRank(b.severity) - alertSeverityRank(a.severity);
      if (severityDiff) return severityDiff;
      return String(a.title || "").localeCompare(String(b.title || ""), "ko");
    });
  }

  function alertStats(alerts) {
    return alerts.reduce(function (stats, alert) {
      stats.total += 1;
      stats[alert.severity] = (stats[alert.severity] || 0) + 1;
      return stats;
    }, { total: 0, danger: 0, caution: 0, watch: 0, info: 0 });
  }

  function load() {
    state.loading = !state.snapshot;
    state.refreshing = Boolean(state.snapshot);
    state.error = "";
    render();

    var loadPromise = isStaticPreviewHost()
      ? Promise.resolve(staticPreviewSnapshot())
      : requestJson(tossLensPath());

    return loadPromise
      .then(function (snapshot) {
        state.snapshot = snapshot;
        state.snapshotFromCache = false;
        state.error = "";
        writeCachedSnapshot(snapshot);
      })
      .catch(function (error) {
        state.error = error.message;
      })
      .finally(function () {
        state.loading = false;
        state.refreshing = false;
        render();
      });
  }

  function render() {
    applyAppTheme();
    rememberRenderedPageScrollPosition();
    destroyOntologyCytoscapeGraphs();
    if (state.loading && !state.snapshot) {
      app.innerHTML = renderLoading();
      return;
    }
    if (!state.snapshot) {
      app.innerHTML = renderError();
      bindActions();
      return;
    }
    app.innerHTML = renderDashboard(state.snapshot);
    bindActions();
    initOntologyCytoscapeGraphs();
    restoreTabBarPosition();
    restoreRenderedPageScrollPosition();
    bindPageScrollMemory();
    syncAppNavScrollState();
    syncTopbarScrollState();
    if (state.activeTab === "feed" && !state.researchEvidence && !state.researchEvidenceLoading) {
      loadResearchEvidence(false);
    }
    if (state.activeTab === "experiments" && !state.ontologyExperimentsLoaded && !state.ontologyExperimentsLoading) {
      loadOntologyExperiments(false);
    }
  }

  function renderLoading() {
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div class="topbar-copy">',
      '<p class="eyebrow">' + escapeHtml(appBrandName) + '</p>',
      '<h1>신호 궤도를 준비하는 중</h1>',
      '<p class="subtle">계좌, 관심 종목, 알림 워커, 모델 기준을 나눠 확인하고 있습니다.</p>',
      '</div>',
      '<div class="toolbar topbar-actions">',
      '<span class="status-pill demo">초기 동기화</span>',
      '</div>',
      '</section>',
      '<section class="loading-grid">',
      '<article class="panel loading-status-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Startup</p>',
      '<h2>먼저 볼 수 있는 화면을 준비합니다</h2>',
      '</div>',
      '<span class="metric">4</span>',
      '</div>',
      '<div class="loading-source-list">',
      renderLoadingSource("계좌 연결", "토스 live 또는 로컬 저장 계정 상태 확인"),
      renderLoadingSource("관심 종목", "계정별 관심 목록과 전체 종목 캐시 준비"),
      renderLoadingSource("알림 판단", "최근 발송 이력과 룰 설정 동기화"),
      renderLoadingSource("투자 분석", "전략 근거와 관계 그래프 계산"),
      '</div>',
      '</article>',
      '<article class="panel loading-preview-panel">',
      '<div class="panel-head"><div><p class="label">Preview</p><h2>데이터 준비 상태</h2></div></div>',
      '<div class="loading-shell-preview">',
      '<span></span><span></span><span></span>',
      '<span></span><span></span><span></span>',
      '</div>',
      '</article>',
      '</section>',
      '</main>'
    ].join("");
  }

  function renderLoadingSource(title, description) {
    return [
      '<div class="loading-source-row">',
      '<span class="loading-dot" aria-hidden="true"></span>',
      '<div>',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<em>' + escapeHtml(description) + '</em>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderEmptyState(options) {
    options = options || {};
    var tone = options.tone || "muted";
    var label = options.label || "State";
    var title = options.title || "표시할 데이터가 없습니다";
    var description = options.description || "데이터가 들어오면 같은 위치에 표시합니다.";
    var meta = Array.isArray(options.meta) ? options.meta : [];
    return [
      '<div class="empty-state ' + escapeHtml(tone) + '">',
      '<div class="empty-state-copy">',
      '<p class="label">' + escapeHtml(label) + '</p>',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<span>' + escapeHtml(description) + '</span>',
      '</div>',
      meta.length ? '<div class="empty-state-meta">' + meta.map(function (item) {
        return '<span>' + escapeHtml(item) + '</span>';
      }).join("") + '</div>' : '',
      options.action || '',
      '</div>'
    ].join("");
  }

  function renderError() {
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">' + escapeHtml(appBrandName) + '</p>',
      '<h1>' + escapeHtml(appBrandName) + '를 불러오지 못했습니다</h1>',
      '<p class="subtle">' + escapeHtml(state.error || "알 수 없는 오류") + "</p>",
      '</div>',
      '<button class="icon-button" type="button" data-action="refresh" title="새로고침" aria-label="새로고침">↻</button>',
      '</section>',
      '</main>'
    ].join("");
  }

  function renderDashboard(snapshot) {
    var toss = snapshot.toss || { mode: "demo" };
    var modeLabel = snapshot.preview ? "Pages preview" : (toss.mode === "live" ? "Toss live" : "Local server");
    var modeClass = toss.mode === "live" ? "live" : "demo";
    var tab = activeTabMeta();
    var structure = pageStructureMeta(tab.id);
    var showHomeDeskbar = state.activeTab === "overview";
    var subtitle = (structure.objective || tab.description || "운영") + " · 마지막 데이터 " + formatClock(snapshot.generatedAt);
    return [
      '<main class="shell console-shell ' + escapeHtml(webStyleContract.shellClass) + (showHomeDeskbar ? " shell-home" : " shell-page") + '" data-web-style="' + escapeHtml(webStyleContract.id) + '" data-web-style-version="' + escapeHtml(webStyleContract.version) + '" data-active-group="' + escapeHtml(structure.groupId) + '">',
      renderAppNavigation(tab, modeLabel, modeClass),
      '<section class="topbar web-style-topbar" data-style-region="topbar">',
      '<div class="topbar-copy">',
      '<p class="eyebrow">' + escapeHtml(structure.groupLabel + " / " + structure.layer) + '</p>',
      '<h1>' + escapeHtml(tab.label || "홈") + '</h1>',
      '<p class="subtle">' + escapeHtml(subtitle) + '</p>',
      '</div>',
      renderTopbarSyncState(),
      '</section>',
      renderDeskbar(snapshot, modeLabel, modeClass, { compact: !showHomeDeskbar, activeTab: tab }),
      '<section class="workspace-layout web-style-workspace" data-style-region="workspace">',
      renderTabs(),
      '<div class="workspace-main web-style-main" data-style-region="main" data-scroll-key="' + escapeHtml(activeScrollKey()) + '">',
      renderActiveTab(snapshot),
      '</div>',
      '</section>',
      renderOntologyGraphExpandedOverlay(),
      renderSnackbar(),
      '</main>'
    ].join("");
  }

  function renderOntologyGraphExpandedOverlay() {
    var graphId = normalizeOntologyGraphId(state.expandedOntologyGraphId);
    if (!graphId) return "";
    var meta = ontologyGraphDisplayMeta(graphId);
    var expandedGraphId = graphId + "-expanded";
    return [
      '<div class="ontology-graph-expanded-backdrop" data-ontology-graph-close>',
      '<section class="ontology-graph-expanded-dialog" role="dialog" aria-modal="true" aria-label="' + escapeHtml(meta.title) + '">',
      '<header class="ontology-graph-expanded-head">',
      '<div>',
      '<p class="label">' + escapeHtml(meta.eyebrow) + '</p>',
      '<h2>' + escapeHtml(meta.title) + '</h2>',
      '<span>' + escapeHtml(meta.description) + '</span>',
      '</div>',
      '<div class="ontology-graph-expanded-toolbar">',
      '<button class="icon-button" type="button" data-ontology-graph-fit="' + escapeHtml(expandedGraphId) + '" title="' + escapeHtml(meta.fitLabel) + '" aria-label="' + escapeHtml(meta.fitLabel) + '">⌖</button>',
      '<button class="icon-button" type="button" data-ontology-graph-layout="' + escapeHtml(expandedGraphId) + '" title="' + escapeHtml(meta.layoutLabel) + '" aria-label="' + escapeHtml(meta.layoutLabel) + '">↺</button>',
      '<button class="icon-button danger" type="button" data-ontology-graph-close="" title="큰 화면 닫기" aria-label="큰 화면 닫기">×</button>',
      '</div>',
      '</header>',
      '<div class="ontology-cytoscape ontology-cytoscape-expanded" data-ontology-cytoscape="' + escapeHtml(expandedGraphId) + '"><span>그래프 엔진 초기화 중</span></div>',
      '<footer class="ontology-graph-expanded-footer">',
      '<span>표시 기준: 핵심 관계 압축 · 원본과 동일한 그래프 데이터</span>',
      '</footer>',
      '</section>',
      '</div>'
    ].join("");
  }

  function renderTopbarSyncState() {
    if (state.refreshing) {
      return [
        '<div class="toolbar topbar-actions">',
        '<span class="status-pill demo">백그라운드 동기화 중</span>',
        '</div>'
      ].join("");
    }
    if (state.snapshotFromCache) {
      return [
        '<div class="toolbar topbar-actions">',
        '<span class="status-pill mock">직전 화면 유지</span>',
        '</div>'
      ].join("");
    }
    if (state.error && state.snapshot) {
      return [
        '<div class="toolbar topbar-actions">',
        '<span class="status-pill demo">갱신 확인 필요</span>',
        '</div>'
      ].join("");
    }
    return "";
  }

  function renderDeskbar(snapshot, modeLabel, modeClass, options) {
    options = options || {};
    var portfolio = snapshot.portfolio || {};
    var toss = snapshot.toss || {};
    var positions = Array.isArray(toss.positions) ? toss.positions.filter(function (item) {
      return item && item.source !== "cash";
    }).length : 0;
    var rules = alertRules();
    var policyRules = notificationPolicyCatalog();
    var enabledRules = policyRules.filter(function (rule) {
      return enabledAlertRule(rules, rule.key);
    }).length;
    var thresholds = modelDecisionThresholds();
    var decision = snapshot.tossDecision || {};
    var strategy = decision.ontologyStrategy || {};
    var abox = strategy.abox || {};
    var tbox = strategy.tbox || {};
    var relationCount = Number(abox.relationCount || strategy.relationCount || 0);
    var activeTab = options.activeTab || activeTabMeta();
    var structure = pageStructureMeta(activeTab.id);
    var compact = !!options.compact;
    if (compact) {
      return [
        '<section class="deskbar deskbar-compact web-style-deskbar" data-style-region="deskbar" data-style-rail="compact" aria-label="콘솔 상태 요약">',
        renderDeskbarCell("Domain", structure.groupLabel, structure.entity || activeTab.label || "현재 작업", "neutral"),
        renderDeskbarCell("Data", modeLabel, "Last " + formatClock(snapshot.generatedAt), modeClass),
        renderDeskbarCell("Portfolio", formatMoney(portfolio.total || 0), positions + " positions", "neutral"),
        renderDeskbarCell("Alerts", enabledRules + "/" + policyRules.length, state.realtime.connected ? "WebSocket live" : "HTTP polling", state.realtime.connected ? "live" : "demo"),
        '</section>'
      ].join("");
    }
    return [
      '<section class="deskbar deskbar-full web-style-deskbar" data-style-region="deskbar" data-style-rail="full" aria-label="운영 상태 요약">',
      renderDeskbarCell("Data", modeLabel, "Last " + formatClock(snapshot.generatedAt), modeClass),
      renderDeskbarCell("Portfolio", formatMoney(portfolio.total || 0), positions + " positions", "neutral"),
      renderDeskbarCell("Model", settingValue("modelName") || defaultSettings.modelName, "Buy " + Math.round(thresholds.modelBuy || 0) + " · Sell " + Math.round(thresholds.modelSell || 0), "neutral"),
      renderDeskbarCell("Ontology", (tbox.classes || []).length + " TBox / " + relationCount + " rel", (abox.entityCount || 0) + " ABox entities", "neutral"),
      renderDeskbarCell("Alerts", enabledRules + "/" + policyRules.length, state.realtime.connected ? "WebSocket live" : "HTTP polling", state.realtime.connected ? "live" : "demo"),
      '</section>'
    ].join("");
  }

  function renderDeskbarCell(label, value, detail, tone) {
    return [
      '<div class="deskbar-cell ' + escapeHtml(tone || "neutral") + '">',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value || "-") + '</strong>',
      '<span>' + escapeHtml(detail || "") + '</span>',
      '</div>'
    ].join("");
  }

  function navTabButton(tab, className) {
    var active = state.activeTab === tab.id;
    var structure = pageStructureMeta(tab.id);
    return [
      '<button type="button" class="' + escapeHtml(className) + (active ? " active" : "") + '" data-tab="' + escapeHtml(tab.id) + '" data-nav-group="' + escapeHtml(structure.groupId) + '"' + (active ? ' aria-current="page"' : "") + '>',
      '<span class="nav-tab-label">' + escapeHtml(tab.label) + '</span>',
      '<span class="nav-tab-description">' + escapeHtml(tab.description || "") + '</span>',
      '</button>'
    ].join("");
  }

  function renderAppNavigation(activeTab, modeLabel, modeClass) {
    var managementTabs = tabs.filter(function (tab) {
      return managementTabIds.indexOf(tab.id) >= 0;
    });
    var managementActive = managementTabs.some(function (tab) {
      return tab.id === state.activeTab;
    });
    function renderAppNavGroup(group, index) {
      var groupTabs = tabsForNavigationGroup(group);
      var active = groupTabs.some(function (tab) { return tab.id === state.activeTab; });
      if (!groupTabs.length) return "";
      return [
        index ? '<span class="app-nav-divider" aria-hidden="true"></span>' : '',
        '<section class="app-nav-group' + (active ? " active" : "") + '" data-nav-group="' + escapeHtml(group.id) + '">',
        '<span class="app-nav-section-label"><strong>' + escapeHtml(group.label) + '</strong><em>' + escapeHtml(group.description || "") + '</em></span>',
        groupTabs.map(function (tab) {
          return navTabButton(tab, "app-nav-tab " + group.id);
        }).join(""),
        '</section>'
      ].join("");
    }
    return [
      '<nav class="app-nav web-style-nav" data-style-region="navigation" aria-label="앱 네비게이션">',
      '<div class="app-nav-brand">',
      '<span class="app-brand-mark" aria-hidden="true"><span></span></span>',
      '<div class="app-brand-copy">',
      '<strong>' + escapeHtml(appBrandName) + '</strong>',
      '<span class="app-brand-subtitle">' + escapeHtml(navigationGroupForTab(activeTab.id).label + " · " + (activeTab.description || appBrandSubtitle)) + '</span>',
      '</div>',
      '</div>',
      '<div class="app-nav-tabs" aria-label="업무 구조 탭">',
      navigationGroups.map(renderAppNavGroup).join(""),
      '</div>',
      '<details class="app-nav-menu">',
      '<summary><strong>운영</strong><span>' + escapeHtml(managementActive ? activeTab.label : "알림·설정") + '</span></summary>',
      '<div class="app-nav-menu-list">',
      managementTabs.map(function (tab) {
        return navTabButton(tab, "app-nav-menu-item");
      }).join(""),
      '</div>',
      '</details>',
      '<div class="app-nav-tools">',
      '<span class="status-pill ' + modeClass + '">' + escapeHtml(modeLabel) + "</span>",
      '<button class="icon-button" type="button" data-action="refresh" title="새로고침" aria-label="새로고침">' + (state.refreshing ? "…" : "↻") + "</button>",
      '</div>',
      '</nav>'
    ].join("");
  }

  function renderTabs() {
    var bottomTabs = tabs.filter(function (tab) {
      return bottomTabIds.indexOf(tab.id) >= 0;
    });
    return [
      '<nav class="tab-bar" aria-label="주요 탭" style="--tab-count:' + bottomTabs.length + '">',
      bottomTabs.map(function (tab) {
        var active = state.activeTab === tab.id;
        return '<button type="button" class="' + (active ? "active" : "") + '" data-tab="' + escapeHtml(tab.id) + '"' + (active ? ' aria-current="page"' : "") + '><span class="tab-label">' + escapeHtml(tab.label) + '</span><span class="tab-description">' + escapeHtml(tab.description || "") + '</span></button>';
      }).join(""),
      '</nav>'
    ].join("");
  }

  function renderActiveTab(snapshot) {
    if (state.activeTab === "overview") {
      return renderManagedPage("overview", snapshot, [
        '<section class="admin-grid home-view">',
        renderAdminOverviewPanel(snapshot),
        renderAccountDirectoryPanel({ compact: true }),
        renderAccountWatchlistPanel({ compact: true }),
        renderAdminMonitoringPanel(snapshot),
        '</section>'
      ].join(""));
    }
    if (state.activeTab === "accounts") {
      return renderManagedPage("accounts", snapshot, [
        '<section class="admin-grid accounts-view">',
        renderAccountSectionBar(),
        renderAccountSectionContent(snapshot),
        '</section>'
      ].join(""));
    }
    if (state.activeTab === "watchlist") {
      return renderManagedPage("watchlist", snapshot, [
        '<section class="admin-grid watchlist-view">',
        renderAccountWatchlistPanel({ full: true, editable: true }, snapshot),
        '</section>'
      ].join(""));
    }
    if (state.activeTab === "symbols") {
      return renderManagedPage("symbols", snapshot, [
        '<section class="admin-grid symbol-universe-view">',
        renderSymbolUniversePanel({ full: true }),
        '</section>'
      ].join(""));
    }
    if (state.activeTab === "feed") {
      return renderFeedPage(snapshot);
    }
    if (state.activeTab === "system") {
      return renderSystemGuidePage(snapshot);
    }
    if (state.activeTab === "notifications") {
      return renderNotificationsPage();
    }
    if (state.activeTab === "modeling") {
      return renderStrategyModelingPage(snapshot);
    }
    if (state.activeTab === "experiments") {
      return renderOntologyExperimentsPage(snapshot);
    }
    if (state.activeTab === "ontology") {
      return renderOntologyPage(snapshot);
    }
    if (state.activeTab === "settings") {
      return renderSettingsPage();
    }
    return renderManagedPage("overview", snapshot, [
      '<section class="admin-grid">',
      renderAdminOverviewPanel(snapshot),
      renderAdminMonitoringPanel(snapshot),
      '</section>'
    ].join(""));
  }

  function renderManagedPage(pageId, snapshot, content) {
    var structure = pageStructureMeta(pageId || "overview");
    var mode = activePageMode(pageId || "overview");
    return [
      '<div class="managed-page managed-page-' + escapeHtml(pageId || "overview") + ' ' + escapeHtml(webStyleContract.pageClass) + ' web-style-screen-' + escapeHtml(pageId || "overview") + '" data-style-contract="' + escapeHtml(webStyleContract.id) + '" data-style-screen="' + escapeHtml(pageId || "overview") + '" data-page-mode="' + escapeHtml(mode) + '" data-structure-group="' + escapeHtml(structure.groupId) + '" data-structure-layer="' + escapeHtml(structure.layer) + '" data-structure-entity="' + escapeHtml(structure.entity) + '">',
      renderPageCommandStrip(pageId, snapshot),
      content,
      '</div>'
    ].join("");
  }

  function renderSystemGuidePage(snapshot) {
    return renderManagedPage("system", snapshot, [
      '<section class="admin-grid system-guide-view">',
      renderSystemGuideHero(snapshot),
      renderSystemQuickManualPanel(),
      renderSystemDataFlowPanel(snapshot),
      renderSystemEventFlowPanel(),
      renderSystemNotificationFlowPanel(),
      renderSystemOntologyPanel(snapshot),
      renderSystemOperationsPanel(),
      renderSystemGlossaryPanel(),
      '</section>'
    ].join(""));
  }

  function renderSystemGuideHero(snapshot) {
    var toss = (snapshot || {}).toss || {};
    var positions = Array.isArray(toss.positions) ? toss.positions.filter(function (item) { return item && item.source !== "cash"; }) : [];
    var strategy = (((snapshot || {}).tossDecision || {}).ontologyStrategy || {});
    var abox = strategy.abox || {};
    var evidence = Array.isArray(strategy.evidence) ? strategy.evidence : [];
    var metrics = [
      ["계정", serviceAccounts().length || 0, "MySQL 운영 DB에 저장된 연결 단위"],
      ["보유 종목", positions.length, "현금 제외 현재 포지션"],
      ["관계", abox.relationCount || strategy.relationCount || 0, "TBox/ABox에서 만들어진 연결"],
      ["근거", evidence.length || ((currentResearchEvidence().summary || {}).total || 0), "뉴스·시세·공시·모델 근거"]
    ];
    return [
      '<article class="panel system-guide-hero">',
      '<div class="system-guide-hero-copy">',
      '<p class="label">SYSTEM MANUAL</p>',
      '<h2>Orbit Alpha는 계좌, 시장 데이터, 규칙, AI 의견을 한 흐름으로 묶는 로컬 우선 투자 관제 시스템입니다.</h2>',
      '<p>처음 보는 사람은 먼저 계정과 관심종목을 등록하고, 데이터 수집 상태를 확인한 뒤, 알림과 투자 분석 탭에서 왜 신호가 생겼는지 확인하면 됩니다. 이 탭은 그 전체 흐름을 운영 매뉴얼처럼 설명합니다.</p>',
      '<div class="system-guide-kpis">',
      metrics.map(function (metric) {
        return [
          '<span>',
          '<em>' + escapeHtml(metric[0]) + '</em>',
          '<strong>' + escapeHtml(metric[1]) + '</strong>',
          '<b>' + escapeHtml(metric[2]) + '</b>',
          '</span>'
        ].join("");
      }).join(""),
      '</div>',
      '</div>',
      '<div class="system-orbit-map" aria-label="Orbit Alpha 시스템 구성 다이어그램">',
      '<span class="system-orbit-ring ring-one"></span>',
      '<span class="system-orbit-ring ring-two"></span>',
      '<div class="system-orbit-node core"><strong>Orbit Alpha</strong><em>로컬 관제</em></div>',
      '<div class="system-orbit-node data"><strong>Data</strong><em>시세·뉴스·공시</em></div>',
      '<div class="system-orbit-node model"><strong>Model</strong><em>전략 공식·관계 규칙</em></div>',
      '<div class="system-orbit-node alert"><strong>Alert</strong><em>Outbox·Telegram</em></div>',
      '<div class="system-orbit-node ui"><strong>Console</strong><em>탭별 운영 화면</em></div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSystemQuickManualPanel() {
    var steps = [
      ["01", "계정 등록", "계정 탭에서 Toss 자격 정보, 계좌 순번, Telegram 채널을 저장합니다. 저장된 secret은 화면에 다시 노출하지 않습니다."],
      ["02", "관심종목 구성", "관심종목 탭에서 계정별 추적 대상을 넣습니다. 보유 종목과 관심 종목은 알림 판단에서 서로 다른 관계로 다룹니다."],
      ["03", "데이터 확인", "피드 탭과 전체종목 탭에서 종목 카탈로그, 뉴스, 공시, 외부 API 수집 상태를 확인합니다."],
      ["04", "알림 정책 조정", "알림 탭에서 메시지 타입별 사용 여부, 임계값, 발송 템플릿, 최근 판단을 점검합니다."],
      ["05", "투자 분석 검토", "투자 분석 탭에서 가격·수급·추세·공시·뉴스 근거가 어떤 관계 규칙으로 연결됐는지 봅니다."]
    ];
    return [
      '<article class="panel system-manual-panel">',
      '<div class="panel-head"><div><p class="label">USER MANUAL</p><h2>처음 사용하는 순서</h2></div><span class="status-pill live">local-first</span></div>',
      '<div class="system-manual-grid">',
      steps.map(function (step) {
        return [
          '<section class="system-manual-step">',
          '<b>' + escapeHtml(step[0]) + '</b>',
          '<strong>' + escapeHtml(step[1]) + '</strong>',
          '<p>' + escapeHtml(step[2]) + '</p>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSystemDataFlowPanel(snapshot) {
    var generatedAt = formatClock((snapshot || {}).generatedAt);
    var sourceRows = [
      ["계좌·보유", "Toss snapshot", "monitor_snapshots", "보유 수량, 평단가, 평가액, 현금 비중"],
      ["전체종목·시세", "market-data worker", "symbol_universe, market_quote_cache", "시장별 종목명, 현재가, 캔들 기반 이동평균"],
      ["뉴스·공시", "news worker, OpenDART", "research_evidence, app_store", "기사 요약, 관련성, 중요도, 공시 제목"],
      ["거시·외부", "Alpha Vantage, CoinGecko, FRED", "external_signals cache", "미장 가격, 코인 변동, 금리·스프레드"],
      ["운영 설정", "웹 설정 API", "runtime_settings, notification_rules", "임계값, 템플릿, API 사용 여부"]
    ];
    return [
      '<article class="panel system-data-flow-panel">',
      '<div class="panel-head"><div><p class="label">DATA FLOW</p><h2>데이터가 화면과 알림까지 가는 흐름</h2><span>마지막 화면 데이터 ' + escapeHtml(generatedAt) + '</span></div></div>',
      '<div class="system-flow-diagram data-flow" aria-label="데이터 흐름 다이어그램">',
      renderSystemFlowNode("01", "외부·로컬 입력", ["Toss 계좌", "종목 카탈로그", "뉴스·공시·거시"]),
      renderSystemFlowNode("02", "수집 워커", ["monitor", "market-data", "news"]),
      renderSystemFlowNode("03", "운영 저장소", ["MySQL operational tables", "캐시·이벤트·Outbox"]),
      renderSystemFlowNode("04", "분석 계층", ["전략 공식", "온톨로지 규칙", "AI 의견"]),
      renderSystemFlowNode("05", "사용자 접점", ["웹 콘솔", "알림 큐", "Telegram"]),
      '</div>',
      '<div class="system-lineage-grid" role="table" aria-label="데이터 계보">',
      '<div class="system-lineage-head" role="row"><span>데이터</span><span>수집 주체</span><span>저장 위치</span><span>사용 목적</span></div>',
      sourceRows.map(function (row) {
        return [
          '<div class="system-lineage-row" role="row">',
          '<strong>' + escapeHtml(row[0]) + '</strong>',
          '<span>' + escapeHtml(row[1]) + '</span>',
          '<code>' + escapeHtml(row[2]) + '</code>',
          '<em>' + escapeHtml(row[3]) + '</em>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSystemFlowNode(index, title, items) {
    return [
      '<section class="system-flow-node">',
      '<b>' + escapeHtml(index) + '</b>',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<ul>',
      (items || []).map(function (item) { return '<li>' + escapeHtml(item) + '</li>'; }).join(""),
      '</ul>',
      '</section>'
    ].join("");
  }

  function renderSystemEventFlowPanel() {
    var events = [
      ["account.saved", "계정 저장", "계정 탭에서 저장된 계정을 마스킹된 payload로 이벤트 로그에 남깁니다."],
      ["market_data.collected", "시세 수집", "종목 유니버스에서 선택된 종목의 가격과 캔들 데이터를 저장합니다."],
      ["research_evidence.collected", "뉴스 근거 수집", "뉴스·공시 근거가 바뀌면 관련 종목과 중요도 정보를 이벤트로 남깁니다."],
      ["monitoring.snapshot_collected", "계좌 스냅샷", "각 계정의 보유, 현금, 판단 후보를 하나의 스냅샷으로 기록합니다."],
      ["monitoring.alerts_detected", "신호 감지", "가격·수급·추세·보유 변화가 알림 후보 이벤트로 묶입니다."],
      ["ontology.reasoning_requested", "관계 추론 요청", "중요한 데이터 변화가 있으면 온톨로지 추론 워커가 처리할 요청을 만듭니다."],
      ["ontology.reasoning_completed", "관계 추론 완료", "TBox/ABox 관계, 근거 카드, 의견 후보가 계산됩니다."],
      ["notification.job_queued", "알림 큐 적재", "사용자에게 보낼 메시지는 즉시 발송하지 않고 Outbox에 넣어 재시도 가능하게 합니다."]
    ];
    return [
      '<article class="panel system-event-panel">',
      '<div class="panel-head"><div><p class="label">EVENT FLOW</p><h2>이벤트 흐름</h2><span>각 기능은 서로 직접 호출하기보다 이벤트를 남기고 필요한 워커가 이어서 처리합니다.</span></div></div>',
      '<div class="system-event-track" aria-label="이벤트 흐름 다이어그램">',
      events.map(function (event, index) {
        return [
          '<section class="system-event-row">',
          '<b>' + String(index + 1).padStart(2, "0") + '</b>',
          '<code>' + escapeHtml(event[0]) + '</code>',
          '<strong>' + escapeHtml(event[1]) + '</strong>',
          '<p>' + escapeHtml(event[2]) + '</p>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSystemNotificationFlowPanel() {
    var nodes = [
      ["감지", "모니터링과 외부 데이터가 가격·수급·추세·공시·뉴스 변화를 찾습니다."],
      ["근거 묶음", "단일 신호를 바로 보내지 않고 투자 인사이트의 근거로 묶습니다."],
      ["관계 판단", "TBox/ABox 규칙이 보유, 관심, 데이터 품질, 리스크 관계를 계산합니다."],
      ["AI 문구", "제공된 데이터 안에서만 이유, 반대 근거, 실행 전 확인을 작성합니다."],
      ["Outbox", "notification_jobs에 저장한 뒤 워커가 재시도 가능하게 전달합니다."],
      ["사용자", "Telegram 또는 콘솔에서 왜 알림이 왔는지 확인합니다."]
    ];
    return [
      '<article class="panel system-notification-panel">',
      '<div class="panel-head"><div><p class="label">ALERT PIPELINE</p><h2>알림이 만들어지는 방식</h2></div></div>',
      '<div class="system-notification-flow" aria-label="알림 생성 흐름 다이어그램">',
      nodes.map(function (node, index) {
        return [
          '<section class="system-notification-node">',
          '<b>' + String(index + 1).padStart(2, "0") + '</b>',
          '<strong>' + escapeHtml(node[0]) + '</strong>',
          '<p>' + escapeHtml(node[1]) + '</p>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="system-guide-note">',
      '<strong>중요한 운영 원칙</strong>',
      '<p>투자 알림은 자동 주문이 아닙니다. 시스템은 근거를 모아 대응 우선순위를 제안하고, 실제 실행 전 확인할 조건을 사용자에게 보여줍니다.</p>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSystemOntologyPanel(snapshot) {
    var strategy = (((snapshot || {}).tossDecision || {}).ontologyStrategy || {});
    var tbox = strategy.tbox || {};
    var abox = strategy.abox || {};
    var relationCount = abox.relationCount || strategy.relationCount || 0;
    var cards = [
      ["TBox", "시스템의 용어 사전", "종목, 계좌, 가격, 추세, 뉴스, 공시, 리스크 같은 개념과 가능한 관계를 정의합니다.", (tbox.classes || []).length + " classes"],
      ["ABox", "현재 데이터", "지금 계정과 시장에서 실제로 관찰된 보유 종목, 관심 종목, 가격, 근거를 담습니다.", (abox.entityCount || 0) + " entities"],
      ["Evidence", "판단 근거", "시세, 수급, 이동평균, 뉴스, 공시, 외부 지표를 출처와 함께 보관합니다.", (Array.isArray(strategy.evidence) ? strategy.evidence.length : 0) + " cards"],
      ["Belief", "중간 해석", "근거를 읽고 추세 훼손, 수급 확인, 데이터 부족 같은 중간 판단을 만듭니다.", (Array.isArray(strategy.beliefs) ? strategy.beliefs.length : 0) + " beliefs"],
      ["Opinion", "사용자 의견", "분할축소, 보유, 추가 확인처럼 실행 전 점검 의견을 생성합니다.", (Array.isArray(strategy.opinions) ? strategy.opinions.length : 0) + " opinions"]
    ];
    return [
      '<article class="panel system-ontology-panel">',
      '<div class="panel-head"><div><p class="label">ONTOLOGY MODEL</p><h2>온톨로지와 모델링 구조</h2><span>현재 관계 수 ' + escapeHtml(relationCount) + '</span></div></div>',
      '<div class="system-ontology-map" aria-label="온톨로지 모델 다이어그램">',
      cards.map(function (card, index) {
        return [
          '<section class="system-ontology-card step-' + escapeHtml(index + 1) + '">',
          '<b>' + escapeHtml(card[0]) + '</b>',
          '<strong>' + escapeHtml(card[1]) + '</strong>',
          '<p>' + escapeHtml(card[2]) + '</p>',
          '<em>' + escapeHtml(card[3]) + '</em>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSystemOperationsPanel() {
    var rows = [
      ["매일 먼저 볼 것", "홈에서 연결 상태와 최근 알림, 투자 분석에서 관계 그래프와 근거 카드를 봅니다."],
      ["데이터가 이상할 때", "피드 탭의 수집 오류, 전체종목 탭의 최신성, 설정 탭의 API 키와 캐시 시간을 순서대로 확인합니다."],
      ["알림이 너무 많을 때", "알림 탭에서 메시지 타입별 사용 여부와 cadence, 임계값을 조정합니다."],
      ["모델 판단이 이상할 때", "투자 분석 탭에서 공식 입력, 기준값, 근거 카드, 반대 근거를 함께 확인합니다."],
      ["외부 공유 전", "로컬 우선 시스템이므로 `.env.local`, API 키, 계좌 정보, DB 접속 정보가 노출되지 않는지 먼저 확인합니다."]
    ];
    return [
      '<article class="panel system-operations-panel">',
      '<div class="panel-head"><div><p class="label">OPERATIONS</p><h2>운영 체크리스트</h2></div></div>',
      '<div class="system-ops-list">',
      rows.map(function (row) {
        return [
          '<section class="system-ops-row">',
          '<strong>' + escapeHtml(row[0]) + '</strong>',
          '<p>' + escapeHtml(row[1]) + '</p>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSystemGlossaryPanel() {
    var terms = [
      ["Snapshot", "한 시점의 계좌·보유·가격·판단 상태 묶음입니다."],
      ["Evidence", "알림과 판단에 쓰인 출처 있는 근거입니다."],
      ["TBox", "개념과 관계 규칙의 설계도입니다."],
      ["ABox", "현재 데이터로 채워진 실제 관계 그래프입니다."],
      ["Outbox", "보낼 알림을 먼저 저장하고 워커가 안전하게 전송하는 큐입니다."],
      ["Cadence", "같은 유형의 알림을 너무 자주 보내지 않도록 막는 시간 간격입니다."]
    ];
    return [
      '<article class="panel system-glossary-panel">',
      '<div class="panel-head"><div><p class="label">GLOSSARY</p><h2>핵심 용어</h2></div></div>',
      '<div class="system-glossary-grid">',
      terms.map(function (term) {
        return [
          '<section>',
          '<strong>' + escapeHtml(term[0]) + '</strong>',
          '<p>' + escapeHtml(term[1]) + '</p>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function pageCommandProfile(pageId, snapshot) {
    var toss = snapshot.toss || {};
    var positions = Array.isArray(toss.positions) ? toss.positions.filter(function (item) {
      return item && item.source !== "cash";
    }) : [];
    var watchlist = Array.isArray(toss.watchlist) ? toss.watchlist : [];
    var portfolio = snapshot.portfolio || {};
    var strategy = (snapshot.tossDecision || {}).ontologyStrategy || {};
    var investmentAnalysis = (snapshot.tossDecision || {}).investmentAnalysis || {};
    var abox = strategy.abox || {};
    var reasoningCards = Array.isArray(investmentAnalysis.reasoningCards) ? investmentAnalysis.reasoningCards : (Array.isArray(strategy.reasoningCards) ? strategy.reasoningCards : []);
    var enabledRules = notificationEnabledRuleCount();
    var profiles = {
      overview: {
        steps: [["01", "상태", "계정·데이터"], ["02", "위험", "노출·모니터링"], ["03", "조치", "알림·전략"]],
        metrics: [["계정", serviceAccounts().length || 0], ["평가", formatMoney(portfolio.total || 0)], ["알림", enabledRules + "/" + notificationPolicyCatalog().length]]
      },
      accounts: {
        steps: [["01", "목록", "계정 원장"], ["02", "검증", "API·잔고"], ["03", "저장", "DB 반영"]],
        metrics: [["활성", enabledServiceAccounts().length + "/" + serviceAccounts().length], ["Toss", configuredCount(["tossClientId", "tossClientSecret"]) + "/2"], ["Telegram", configuredCount(["telegramBotToken", "telegramChatId"]) + "/2"]]
      },
      watchlist: {
        steps: [["01", "계정", "대상 선택"], ["02", "종목", "관찰 편집"], ["03", "연결", "알림 입력"]],
        metrics: [["계정", serviceAccounts().length || 0], ["관심", allAccountWatchlistSymbols().length || watchlistSymbols().length], ["시세", watchlist.length]]
      },
      symbols: {
        steps: [["01", "목록", "시장 유니버스"], ["02", "필터", "검색·구분"], ["03", "편입", "계정 연결"]],
        metrics: [["기본", watchlistSymbols().length], ["계정", allAccountWatchlistSymbols().length], ["시장", symbolMarketCount()]]
      },
      feed: {
        steps: [["01", "소스", "수집 채널"], ["02", "품질", "오류·신선도"], ["03", "근거", "Evidence 저장"]],
        metrics: [["피드", (state.feed && state.feed.items ? state.feed.items.length : 0)], ["근거", ((currentResearchEvidence().summary || {}).total || 0)], ["오류", (state.feed && state.feed.errors ? state.feed.errors.length : 0)]]
      },
      system: {
        steps: [["01", "지도", "처음 보는 사람"], ["02", "데이터", "수집·저장"], ["03", "이벤트", "알림·추론"]],
        metrics: [["워커", "6"], ["이벤트", "12+"], ["저장소", "MySQL"]]
      },
      notifications: {
        steps: [["01", "판단", "최근 알림 이유"], ["02", "정책", "타입별 룰"], ["03", "본문", "템플릿·발송"]],
        metrics: [["관리 룰", enabledRules + "/" + notificationPolicyCatalog().length], ["템플릿", notificationTemplateItems().length], ["큐", notificationJobSummaryText(state.realtime.notificationJobs)]]
      },
      modeling: {
        steps: [["01", "판단판", "오늘의 구조"], ["02", "후보", "보유·관심"], ["03", "관계", "InferenceBox"]],
        metrics: [["보유", positions.length], ["관심", watchlist.length], ["추론 보류", ((snapshot.investmentAnalysis || {}).graphGate || {}).blockedCount || 0]]
      },
      experiments: {
        steps: [["01", "초안", "후보 규칙"], ["02", "재생", "샌드박스"], ["03", "승격", "운영 검토"]],
        metrics: [["전체", (ontologyExperimentPayload().count || ontologyExperimentItems().length || 0)], ["활성", ontologyExperimentPayload().activeCount || 0], ["최근", ((ontologyExperimentPayload().latestRun || {}).promotionStatus || "대기")]]
      },
      ontology: {
        steps: [["01", "TBox", "스키마"], ["02", "ABox", "현재 실체"], ["03", "관계", "근거 연결"]],
        metrics: [["TBox", ((strategy.tbox || {}).classes || []).length], ["ABox", abox.entityCount || 0], ["관계", abox.relationCount || strategy.relationCount || 0]]
      },
      monitoring: {
        steps: [["01", "스냅샷", "계좌 수집"], ["02", "감지", "가격·수급"], ["03", "상세", "종목 확인"]],
        metrics: [["보유", positions.length], ["관심", watchlist.length], ["평가", formatMoney(portfolio.total || 0)]]
      },
      settings: {
        steps: [["01", "로컬", "설정 DB"], ["02", "연결", "외부 Provider"], ["03", "저장", "변경 반영"]],
        metrics: [["저장", state.settingsSaved ? "완료" : "대기"], ["잠금", state.serverSettingsLocked ? "읽기전용" : "수정"], ["API", configuredCount(["alphaVantageApiKey", "coingeckoApiKey", "fredApiKey", "opendartApiKey"]) + "/4"]]
      }
    };
    var structure = pageStructureMeta(pageId || "overview");
    var profile = profiles[pageId] || profiles.overview;
    profile.groupId = structure.groupId;
    profile.group = structure.groupLabel;
    profile.layer = structure.layer;
    profile.entity = structure.entity;
    profile.objective = structure.objective;
    profile.workflow = structure.workflow;
    return profile;
  }

  function symbolMarketCount() {
    var seen = {};
    (state.symbolUniverse.items || []).forEach(function (item) {
      var market = String(item.market || item.exchange || "").trim();
      if (market) seen[market] = true;
    });
    return Object.keys(seen).length || "-";
  }

  function serviceAccounts() {
    return Array.isArray(state.serviceAccounts) ? state.serviceAccounts : [];
  }

  function enabledServiceAccounts() {
    return serviceAccounts().filter(function (account) {
      return account && account.enabled !== false;
    });
  }

  function configuredCount(keys) {
    return (keys || []).filter(function (key) {
      return isConfiguredSetting(key);
    }).length;
  }

  function renderPageCommandStrip(pageId, snapshot) {
    var profile = pageCommandProfile(pageId, snapshot);
    return [
      '<section class="page-command-strip ' + escapeHtml(webStyleContract.commandClass) + '" data-style-layer="command-strip" data-command-group="' + escapeHtml(profile.groupId) + '" aria-label="페이지 작업 상태">',
      '<div class="page-command-context">',
      '<span class="page-command-kicker">' + escapeHtml(profile.group + " / " + profile.layer) + '</span>',
      '<strong>' + escapeHtml(profile.entity) + '</strong>',
      '<em>' + escapeHtml(profile.objective) + '</em>',
      renderPageModeSwitch(pageId),
      '</div>',
      '<div class="page-command-flow">',
      profile.steps.map(renderPageCommandStep).join(""),
      '</div>',
      '<div class="page-command-metrics">',
      profile.metrics.map(renderPageCommandMetric).join(""),
      '</div>',
      '</section>'
    ].join("");
  }

  function renderPageModeSwitch(pageId) {
    var normalized = normalizeTabId(pageId || state.activeTab);
    if (!pageSupportsMode(normalized)) return "";
    var active = activePageMode(normalized);
    return [
      '<div class="page-mode-switch" role="tablist" aria-label="결과와 설정 보기 전환">',
      pageModeOptions.map(function (option) {
        var selected = option.id === active;
        return [
          '<button type="button" role="tab" class="' + (selected ? "active" : "") + '" data-page-mode-page="' + escapeHtml(normalized) + '" data-page-mode="' + escapeHtml(option.id) + '"' + (selected ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
          '<strong>' + escapeHtml(option.label) + '</strong>',
          '<span>' + escapeHtml(option.description) + '</span>',
          '</button>'
        ].join("");
      }).join(""),
      '</div>'
    ].join("");
  }

  function renderPageCommandStep(step) {
    return [
      '<span class="page-command-step">',
      '<b>' + escapeHtml(step[0]) + '</b>',
      '<strong>' + escapeHtml(step[1]) + '</strong>',
      '<em>' + escapeHtml(step[2]) + '</em>',
      '</span>'
    ].join("");
  }

  function renderPageCommandMetric(metric) {
    return [
      '<span class="page-command-metric">',
      '<em>' + escapeHtml(metric[0]) + '</em>',
      '<strong>' + escapeHtml(metric[1]) + '</strong>',
      '</span>'
    ].join("");
  }

  function renderStrategyModelingPage(snapshot) {
    return renderManagedPage("modeling", snapshot, [
      '<section class="admin-grid strategy-view investment-analysis-view">',
      renderStrategySectionBar(),
      renderStrategySectionContent(snapshot),
      '</section>'
    ].join(""));
  }

  function renderOntologyExperimentsPage(snapshot) {
    return renderManagedPage("experiments", snapshot, [
      '<section class="admin-grid ontology-experiments-view">',
      renderOntologyExperimentOverviewPanel(),
      renderOntologyExperimentLatestPanel(),
      renderOntologyExperimentListPanel(),
      '</section>'
    ].join(""));
  }

  function ontologyExperimentPayload() {
    return state.ontologyExperiments && typeof state.ontologyExperiments === "object" ? state.ontologyExperiments : {};
  }

  function ontologyExperimentItems() {
    var payload = ontologyExperimentPayload();
    return Array.isArray(payload.experiments) ? payload.experiments : [];
  }

  function ontologyExperimentLatestRun(experiment) {
    var history = Array.isArray(experiment.runHistory) ? experiment.runHistory : [];
    if (history.length) return history[0] || {};
    var lastResult = experiment.lastResult && typeof experiment.lastResult === "object" ? experiment.lastResult : {};
    var readiness = lastResult.promotionReadiness && typeof lastResult.promotionReadiness === "object" ? lastResult.promotionReadiness : {};
    var inference = lastResult.inference && typeof lastResult.inference === "object" ? lastResult.inference : {};
    var aggregate = inference.aggregateDelta && typeof inference.aggregateDelta === "object" ? inference.aggregateDelta : {};
    var sandbox = lastResult.sandbox && typeof lastResult.sandbox === "object" ? lastResult.sandbox : {};
    return {
      completedAt: lastResult.completedAt || "",
      promotionStatus: readiness.status || "",
      promotionScore: readiness.score,
      graphRunCount: sandbox.graphRunCount || 0,
      derivedRelationDelta: aggregate.derivedRelationCount || 0,
      newRelationTypes: aggregate.newRelationTypes || [],
      findings: lastResult.findings || []
    };
  }

  function ontologyExperimentStatusLabel(status) {
    return {
      active: "활성",
      paused: "일시정지",
      completed: "완료",
      draft: "초안"
    }[String(status || "").toLowerCase()] || status || "대기";
  }

  function ontologyExperimentStatusTone(status) {
    var key = String(status || "").toLowerCase();
    if (key === "active") return "watch";
    if (key === "paused") return "hold";
    if (key === "completed") return "neutral";
    return "caution";
  }

  function ontologyReadinessTone(status) {
    var key = String(status || "").toLowerCase();
    if (key === "promote-candidate") return "watch";
    if (key === "needs-data") return "hold";
    if (key === "needs-review") return "caution";
    return "neutral";
  }

  function ontologyReadinessLabel(status) {
    return {
      "promote-candidate": "승격 후보",
      "needs-review": "검토 필요",
      "needs-data": "데이터 필요"
    }[String(status || "").toLowerCase()] || status || "판정 대기";
  }

  function ontologyExperimentBusy(action, id) {
    var current = String(state.ontologyExperimentAction || "");
    return current === action || current === action + ":" + id;
  }

  function renderOntologyExperimentOverviewPanel() {
    var payload = ontologyExperimentPayload();
    var latest = payload.latestRun && typeof payload.latestRun === "object" ? payload.latestRun : {};
    return [
      '<article class="panel ontology-experiment-overview-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Ontology Lab</p>',
      '<h2>실험 실행 상태</h2>',
      '<p class="subtle">활성 실험은 새 모니터 스냅샷이 들어오면 샌드박스에서 다시 검증됩니다.</p>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(payload.enabled === false ? "hold" : "watch") + '">' + escapeHtml(payload.enabled === false ? "중지" : "실행") + '</span>',
      '</div>',
      '<div class="ontology-experiment-metrics">',
      renderOntologyExperimentMetric("전체", payload.count || ontologyExperimentItems().length || 0, "experiments"),
      renderOntologyExperimentMetric("활성", payload.activeCount || 0, "active"),
      renderOntologyExperimentMetric("일시정지", payload.pausedCount || 0, "paused"),
      renderOntologyExperimentMetric("배치", payload.batchSize || "-", "batch"),
      '</div>',
      '<div class="ontology-experiment-actions">',
      '<button class="text-button" type="button" data-lab-refresh' + (state.ontologyExperimentsLoading ? ' disabled' : '') + '>' + escapeHtml(state.ontologyExperimentsLoading ? "조회 중" : "새로고침") + '</button>',
      '<button class="text-button primary" type="button" data-lab-run-active' + (state.ontologyExperimentAction ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("once") ? "실행 중" : "활성 실험 실행") + '</button>',
      '</div>',
      state.ontologyExperimentsError ? '<p class="form-error">' + escapeHtml(state.ontologyExperimentsError) + '</p>' : '',
      latest.completedAt ? '<p class="subtle">최근 실행 ' + escapeHtml(formatClock(latest.completedAt)) + ' · ' + escapeHtml(ontologyReadinessLabel(latest.promotionStatus)) + '</p>' : '',
      '</article>'
    ].join("");
  }

  function renderOntologyExperimentMetric(label, value, caption) {
    return [
      '<section>',
      '<span>' + escapeHtml(caption || "") + '</span>',
      '<strong>' + escapeHtml(value) + '</strong>',
      '<em>' + escapeHtml(label || "") + '</em>',
      '</section>'
    ].join("");
  }

  function renderOntologyExperimentLatestPanel() {
    var payload = ontologyExperimentPayload();
    var latest = payload.latestRun && typeof payload.latestRun === "object" ? payload.latestRun : {};
    var relationTypes = Array.isArray(latest.newRelationTypes) ? latest.newRelationTypes : [];
    var findings = Array.isArray(latest.findings) ? latest.findings : [];
    return [
      '<article class="panel ontology-experiment-latest-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Latest Run</p>',
      '<h2>최근 실행 요약</h2>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(ontologyReadinessTone(latest.promotionStatus)) + '">' + escapeHtml(ontologyReadinessLabel(latest.promotionStatus)) + '</span>',
      '</div>',
      latest.completedAt ? [
        '<div class="ontology-experiment-run-grid">',
        renderOntologyExperimentMetric("그래프", latest.graphRunCount || 0, "graphs"),
        renderOntologyExperimentMetric("파생 변화", latest.derivedRelationDelta || 0, "delta"),
        renderOntologyExperimentMetric("점수", latest.promotionScore == null ? "-" : latest.promotionScore, "score"),
        '</div>',
        relationTypes.length ? '<div class="theme-radar ontology-experiment-tags">' + relationTypes.slice(0, 8).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
        findings.length ? '<div class="ontology-experiment-findings">' + findings.slice(0, 4).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
        '<p class="subtle">' + escapeHtml(formatClock(latest.completedAt)) + '</p>'
      ].join("") : renderEmptyState({
        label: "Latest Run",
        title: "아직 실행 이력이 없습니다",
        description: "활성 실험이 실행되면 최근 결과가 이곳에 표시됩니다."
      }),
      '</article>'
    ].join("");
  }

  function renderOntologyExperimentListPanel() {
    var experiments = ontologyExperimentItems();
    return [
      '<article class="panel ontology-experiment-list-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Experiments</p>',
      '<h2>실험 목록</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(experiments.length) + '</span>',
      '</div>',
      state.ontologyExperimentsLoading && !experiments.length ? '<div class="panel skeleton"></div>' : '',
      (!state.ontologyExperimentsLoading && !experiments.length) ? renderEmptyState({
        label: "Ontology Lab",
        title: "등록된 실험이 없습니다",
        description: "후보 RuleBox 실험을 만들면 이 탭에서 실행 상태를 볼 수 있습니다."
      }) : '',
      experiments.length ? '<div class="ontology-experiment-list">' + experiments.map(renderOntologyExperimentCard).join("") + '</div>' : '',
      '</article>'
    ].join("");
  }

  function renderOntologyExperimentCard(experiment) {
    experiment = experiment || {};
    var id = String(experiment.id || experiment.experimentId || "");
    var latest = ontologyExperimentLatestRun(experiment);
    var candidateRules = Array.isArray(experiment.candidateRules) ? experiment.candidateRules : [];
    var symbols = Array.isArray(experiment.symbols) ? experiment.symbols : [];
    var status = String(experiment.status || "draft");
    var active = status.toLowerCase() === "active";
    var actionBusy = state.ontologyExperimentAction && String(state.ontologyExperimentAction).indexOf(":" + id) >= 0;
    return [
      '<section class="ontology-experiment-card">',
      '<div class="ontology-experiment-card-head">',
      '<div>',
      '<strong>' + escapeHtml(experiment.title || "Ontology experiment") + '</strong>',
      '<span>' + escapeHtml(id || "-") + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(ontologyExperimentStatusTone(status)) + '">' + escapeHtml(ontologyExperimentStatusLabel(status)) + '</span>',
      '</div>',
      experiment.hypothesis ? '<p>' + escapeHtml(experiment.hypothesis) + '</p>' : '',
      '<div class="ontology-experiment-card-metrics">',
      renderOntologyExperimentMetric("후보 규칙", candidateRules.length, "rules"),
      renderOntologyExperimentMetric("그래프", latest.graphRunCount || 0, "graphs"),
      renderOntologyExperimentMetric("파생 변화", latest.derivedRelationDelta || 0, "delta"),
      renderOntologyExperimentMetric("판정", ontologyReadinessLabel(latest.promotionStatus), "readiness"),
      '</div>',
      symbols.length ? '<div class="theme-radar ontology-experiment-tags">' + symbols.slice(0, 12).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
      latest.completedAt ? '<p class="subtle">최근 실행 ' + escapeHtml(formatClock(latest.completedAt)) + '</p>' : '',
      '<div class="ontology-experiment-card-actions">',
      '<button class="text-button" type="button" data-lab-run="' + escapeHtml(id) + '"' + (actionBusy || !id ? ' disabled' : '') + '>' + escapeHtml(ontologyExperimentBusy("run", id) ? "실행 중" : "실행") + '</button>',
      active ? '<button class="text-button" type="button" data-lab-pause="' + escapeHtml(id) + '"' + (actionBusy || !id ? ' disabled' : '') + '>일시정지</button>' : '<button class="text-button primary" type="button" data-lab-activate="' + escapeHtml(id) + '"' + (actionBusy || !id ? ' disabled' : '') + '>활성화</button>',
      '</div>',
      '</section>'
    ].join("");
  }


  function renderOntologyPage(snapshot) {
    state.activeStrategySection = normalizeStrategySection(state.activeStrategySection || state.activeOntologySection);
    return renderStrategyModelingPage(snapshot);
  }

  function renderStrategySectionBar() {
    var visibleSections = modeSectionsForPage("modeling", strategySections);
    var activeId = activeSectionForPageMode("modeling", strategySections, state.activeStrategySection);
    return [
      '<div class="strategy-section-bar" data-section-mode="' + escapeHtml(activePageMode("modeling")) + '">',
      '<div class="strategy-section-tabs" role="tablist" aria-label="투자 분석 섹션">',
      visibleSections.map(function (item) {
        var active = activeId === item.id;
        return [
          '<button type="button" role="tab" class="' + (active ? "active" : "") + '" data-strategy-section="' + escapeHtml(item.id) + '"' + (active ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
          '<strong>' + escapeHtml(item.label) + '</strong>',
          '<span>' + escapeHtml(item.description) + '</span>',
          '</button>'
        ].join("");
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }

  function renderInvestmentTabWorkspace(kind, columns) {
    return [
      '<section class="investment-tab-workspace investment-tab-workspace-' + escapeHtml(kind || "overview") + '">',
      (columns || []).map(function (column) {
        return [
          '<div class="investment-tab-stack investment-tab-stack-' + escapeHtml(column.role || "main") + '">',
          column.html || "",
          '</div>'
        ].join("");
      }).join(""),
      '</section>'
    ].join("");
  }

  function ontologyStrategyParts(snapshot) {
    var decision = (snapshot || {}).tossDecision || {};
    var strategy = decision.ontologyStrategy || {};
    var tbox = strategy.tbox || {};
    var abox = strategy.abox || {};
    var rawEntities = Array.isArray(strategy.entities) ? strategy.entities : [];
    var rawRelations = Array.isArray(strategy.relations) ? strategy.relations : [];
    var tboxEntities = Array.isArray(strategy.tboxEntities) ? strategy.tboxEntities : rawEntities.filter(ontologyIsTboxItem);
    var tboxRelations = Array.isArray(strategy.tboxRelations) ? strategy.tboxRelations : rawRelations.filter(ontologyIsTboxItem);
    var aboxEntities = Array.isArray(strategy.aboxEntities) ? strategy.aboxEntities : ontologyAboxEntities(rawEntities);
    var aboxRelations = Array.isArray(strategy.aboxRelations) ? strategy.aboxRelations : ontologyAboxRelations(rawRelations);
    function mergeRows(rows, extras, keyFn) {
      var merged = [];
      var seen = {};
      (rows || []).concat(extras || []).forEach(function (row) {
        var key = keyFn(row);
        if (!key || seen[key]) return;
        seen[key] = true;
        merged.push(row);
      });
      return merged;
    }
    var entities = mergeRows(rawEntities, tboxEntities.concat(aboxEntities), function (item) { return item && item.id; });
    var relations = mergeRows(rawRelations, tboxRelations.concat(aboxRelations), function (item) {
      return item ? [item.source, ontologyTypeOf(item), item.target].join("|") : "";
    });
    var evidence = Array.isArray(strategy.evidence) ? strategy.evidence : [];
    var beliefs = Array.isArray(strategy.beliefs) ? strategy.beliefs : [];
    var opinions = Array.isArray(strategy.opinions) ? strategy.opinions : [];
    var entityLabels = ontologyEntityLabelMap(entities.concat(aboxEntities));
    return {
      decision: decision,
      investmentAnalysis: decision.investmentAnalysis || {},
      strategy: strategy,
      worldview: strategy.worldview || {},
      tbox: tbox,
      abox: abox,
      entities: entities,
      relations: relations,
      tboxEntities: tboxEntities,
      tboxRelations: tboxRelations,
      evidence: evidence,
      beliefs: beliefs,
      opinions: opinions,
      aboxEntities: aboxEntities,
      aboxRelations: aboxRelations,
      activeInvestmentOpinions: Array.isArray(strategy.activeInvestmentOpinions) ? strategy.activeInvestmentOpinions : [],
      executionPlans: Array.isArray(strategy.executionPlans) ? strategy.executionPlans : [],
      insights: Array.isArray(strategy.insights) ? strategy.insights : [],
      operationalOntology: strategy.operationalOntology || (strategy.worldview || {}).operationalOntology || {},
      dataQuality: Array.isArray(strategy.dataQuality) ? strategy.dataQuality : [],
      relationCounts: ontologyRelationCounts(aboxRelations),
      entityLabels: entityLabels
    };
  }

  function ontologyMacroSignalData(parts) {
    parts = parts || {};
    var entities = Array.isArray(parts.aboxEntities) ? parts.aboxEntities : ontologyAboxEntities(parts.entities || []);
    var relations = Array.isArray(parts.aboxRelations) ? parts.aboxRelations : ontologyAboxRelations(parts.relations || []);
    var fxSignals = [];
    var rateSignals = [];
    var signalIds = {};
    entities.forEach(function (entity) {
      var kind = String(entity && entity.kind || "");
      if (kind === "fx-rate") {
        fxSignals.push(entity);
        signalIds[entity.id] = true;
      }
      if (kind === "interest-rate" || kind === "yield-curve") {
        rateSignals.push(entity);
        signalIds[entity.id] = true;
      }
    });
    var macroRelations = relations.filter(function (relation) {
      var type = ontologyTypeOf(relation);
      if (type !== "HAS_FX_EXPOSURE" && type !== "HAS_RATE_SENSITIVITY") return false;
      return Boolean(signalIds[relation.source] || signalIds[relation.target]);
    });
    return {
      fxSignals: fxSignals,
      rateSignals: rateSignals,
      fxRelations: macroRelations.filter(function (relation) { return ontologyTypeOf(relation) === "HAS_FX_EXPOSURE"; }),
      rateRelations: macroRelations.filter(function (relation) { return ontologyTypeOf(relation) === "HAS_RATE_SENSITIVITY"; }),
      macroRelations: macroRelations
    };
  }

  function ontologyMacroNumber(value) {
    var number = Number(value || 0);
    return isFinite(number) ? number : 0;
  }

  function ontologyMacroHasValue(value) {
    return value !== undefined && value !== null && value !== "" && isFinite(Number(value));
  }

  function ontologyMacroValueText(entity) {
    entity = entity || {};
    var properties = entity.properties || {};
    var kind = String(entity.kind || "");
    if (kind === "fx-rate") {
      var base = String(properties.baseCurrency || properties.base || "").toUpperCase();
      var quote = String(properties.quoteCurrency || properties.quote || "").toUpperCase();
      var rate = ontologyMacroNumber(properties.rate || properties.value);
      if (base && quote && rate) return "1 " + base + " = " + rate.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) + " " + quote;
      return rate ? rate.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) : "-";
    }
    var rateValue = ontologyMacroNumber(properties.value);
    var suffix = kind === "yield-curve" ? "%p" : "%";
    return ontologyMacroHasValue(properties.value) ? rateValue.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) + suffix : "-";
  }

  function ontologyMacroMetaText(entity) {
    entity = entity || {};
    var properties = entity.properties || {};
    var rows = [];
    if (properties.seriesId) rows.push(String(properties.seriesId).toUpperCase());
    if (properties.pair) rows.push(String(properties.pair).toUpperCase());
    if (properties.provider) rows.push(String(properties.provider));
    if (properties.date) rows.push(String(properties.date));
    if (properties.fetchedAt) rows.push(formatClock(properties.fetchedAt));
    return rows.join(" · ") || "온톨로지 현재 데이터";
  }

  function ontologyMacroRelationCount(entity, relations) {
    var id = String(entity && entity.id || "");
    return (relations || []).filter(function (relation) {
      return relation && (relation.source === id || relation.target === id);
    }).length;
  }

  function ontologyMacroTone(entity, relations) {
    var id = String(entity && entity.id || "");
    var linked = (relations || []).filter(function (relation) {
      return relation && (relation.source === id || relation.target === id);
    });
    if (linked.some(function (relation) {
      return String((relation.properties || {}).polarity || "") === "risk";
    })) return "danger";
    return String(entity && entity.kind || "") === "fx-rate" ? "watch" : "hold";
  }

  function renderOntologyMacroSignalPanel(parts) {
    var data = ontologyMacroSignalData(parts);
    var total = data.fxSignals.length + data.rateSignals.length;
    return [
      '<article class="panel macro-signal-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Macro Ontology Signals</p>',
      '<h2>환율·금리 관계 신호</h2>',
      '<p class="subtle">환율과 금리는 각각 별도 신호로 보고, 포트폴리오와 종목의 노출 관계를 분리해 표시합니다.</p>',
      '</div>',
      '<span class="metric">' + escapeHtml(total) + '</span>',
      '</div>',
      '<div class="macro-signal-grid">',
      renderOntologyMacroSignalGroup("환율", "FX exposure", data.fxSignals, data.fxRelations, "fx"),
      renderOntologyMacroSignalGroup("금리", "Rate sensitivity", data.rateSignals, data.rateRelations, "rate"),
      '</div>',
      '<div class="rule-strip">',
      '<span>FX는 HAS_FX_EXPOSURE, 금리는 HAS_RATE_SENSITIVITY 관계로 연결됩니다.</span>',
      '<span>값이 없으면 추정하지 않고 현재 온톨로지에 들어온 행만 표시합니다.</span>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderOntologyMacroSignalGroup(title, subtitle, signals, relations, tone) {
    return [
      '<section class="macro-signal-group ' + escapeHtml(tone || "") + '">',
      '<div class="macro-signal-group-head">',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<span>' + escapeHtml(subtitle) + ' · ' + escapeHtml(signals.length) + ' signals · ' + escapeHtml(relations.length) + ' relations</span>',
      '</div>',
      '<div class="macro-signal-list">',
      signals.length ? signals.map(function (entity) {
        return renderOntologyMacroSignalRow(entity, relations);
      }).join("") : '<div class="ontology-empty">' + escapeHtml(title) + ' 신호 없음</div>',
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyMacroSignalRow(entity, relations) {
    var relationCount = ontologyMacroRelationCount(entity, relations);
    var tone = ontologyMacroTone(entity, relations);
    return [
      '<div class="macro-signal-row ' + escapeHtml(tone) + '">',
      '<div>',
      '<strong>' + escapeHtml(ontologyEntityDisplayLabel(entity, entity && entity.id)) + '</strong>',
      '<span>' + escapeHtml(ontologyMacroMetaText(entity)) + '</span>',
      '</div>',
      '<em>' + escapeHtml(ontologyMacroValueText(entity)) + '</em>',
      '<b>' + escapeHtml(relationCount) + ' rel</b>',
      '</div>'
    ].join("");
  }

  function renderOntologyMacroRelationPanel(parts) {
    parts = parts || {};
    var data = ontologyMacroSignalData(parts);
    var labels = parts.entityLabels || ontologyEntityLabelMap(parts.entities || []);
    return [
      '<section class="ontology-surface macro-relation-panel">',
      '<div class="ontology-surface-head">',
      '<strong>환율·금리 관계 행</strong>',
      '<span>' + escapeHtml(data.fxRelations.length) + ' FX exposure · ' + escapeHtml(data.rateRelations.length) + ' rate sensitivity</span>',
      '</div>',
      '<div class="macro-relation-list">',
      data.macroRelations.length ? data.macroRelations.slice(0, 28).map(function (relation) {
        return renderOntologyMacroRelationRow(relation, labels);
      }).join("") : '<div class="ontology-empty">환율·금리 관계 행 없음</div>',
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyMacroRelationRow(relation, entityLabels) {
    var type = ontologyTypeOf(relation);
    var properties = relation.properties || {};
    var source = ontologyEndpointLabel(relation.source, entityLabels);
    var target = ontologyEndpointLabel(relation.target, entityLabels);
    var label = properties.aiInfluenceLabel || properties.rateSeriesId || properties.source || "";
    var weight = Number(relation.weight || 0);
    return [
      '<div class="macro-relation-row ' + escapeHtml(type === "HAS_FX_EXPOSURE" ? "fx" : "rate") + '">',
      '<strong>' + escapeHtml(type) + '</strong>',
      '<span>' + escapeHtml(source + " → " + target) + '</span>',
      '<em>' + escapeHtml(label || (weight ? "weight " + weight.toFixed(2) : "-")) + '</em>',
      '</div>'
    ].join("");
  }

  function investmentDecisionItemMap(snapshot) {
    return ((snapshot || {}).tossDecision || {}).items ? ((snapshot || {}).tossDecision || {}).items.reduce(function (memo, item) {
      var symbol = String(item && item.symbol || "").toUpperCase();
      if (symbol) memo[symbol] = item;
      return memo;
    }, {}) : {};
  }

  function investmentReasoningCards(snapshot) {
    var parts = ontologyStrategyParts(snapshot);
    var cards = Array.isArray(parts.investmentAnalysis.reasoningCards) ? parts.investmentAnalysis.reasoningCards : [];
    if (!cards.length && Array.isArray(parts.strategy.reasoningCards)) cards = parts.strategy.reasoningCards;
    if (cards.length) return cards;
    var decisionMap = investmentDecisionItemMap(snapshot);
    return buildTradeSignalItems(snapshot).map(function (item) {
      var decisionItem = decisionMap[item.symbol] || {};
      var opinion = ontologyOpinionOf(decisionItem);
      var pressure = ontologyPressureOf(opinion) || item.relationStrength || 0;
      return {
        id: "reasoning-card:" + item.symbol,
        symbol: item.symbol,
        companyName: stockDisplayName(item.symbol, item),
        displayName: stockDisplayName(item.symbol, item),
        source: item.source || "watchlist",
        portfolioRelation: item.source === "watchlist" ? "WATCHES" : "HOLDS",
        status: item.hasData ? "readyForAiReview" : "needsData",
        finalOpinion: {
          action: opinion.action || item.action,
          tone: opinion.tone || item.tone || "hold",
          ontologyPressure: pressure,
          conviction: opinion.conviction || 0,
          thesis: opinion.thesis || (item.reasons || [])[0] || ""
        },
        legacyModel: {
          buyScore: item.buyScore,
          sellScore: item.sellScore,
          decision: item.action
        },
        strategyEvidence: (item.reasons || []).slice(0, 5).map(function (reason, index) {
          return { id: "client-evidence:" + item.symbol + ":" + index, kind: "strategy", source: "client-model", summary: reason, value: {}, confidence: item.hasData ? 0.65 : 0.35 };
        }),
        relationEvidence: (item.relationRules || []).slice(0, 5).map(function (rule, index) {
          return { id: "client-relation:" + item.symbol + ":" + index, type: rule.label || "RELATION_RULE", sourceLabel: stockDisplayName(item.symbol, item), targetLabel: rule.label || "관계 규칙", weight: Number(rule.score || 0) / 100 };
        }),
        beliefs: [],
        dataGaps: item.hasData ? [] : ["시장 신호 데이터 부족"],
        graphContext: {
          stockEntityId: "stock:" + item.symbol,
          tboxClasses: ["Stock", "Evidence", "Belief", "Opinion"],
          aboxEntityIds: ["stock:" + item.symbol],
          relationIds: [],
          evidenceIds: [],
          beliefIds: [],
          opinionId: "opinion:" + item.symbol
        },
        aiInference: {
          role: "ontology-first-investment-opinion",
          legacyModelRole: "not-used-for-scoring",
          question: "전략 근거와 관계 근거를 함께 읽고 다음 검증 순서를 설명합니다."
        }
      };
    });
  }

  function investmentAiInferencePacket(snapshot) {
    var parts = ontologyStrategyParts(snapshot);
    return parts.investmentAnalysis.aiInferencePacket || parts.strategy.aiInferencePacket || {};
  }

  function investmentAnalysisModel(snapshot) {
    var payload = (snapshot || {}).investmentAnalysis || {};
    if (payload && payload.contract) return payload;
    var toss = (snapshot || {}).toss || {};
    var positions = Array.isArray(toss.positions) ? toss.positions.filter(function (item) {
      return item && item.source !== "cash" && String(item.symbol || "").toUpperCase() !== "CASH";
    }) : [];
    var watchlist = Array.isArray(toss.watchlist) ? toss.watchlist : [];
    var decision = (snapshot || {}).tossDecision || {};
    var items = Array.isArray(decision.items) ? decision.items : [];
    return {
      contract: "investment-analysis-client-fallback-v1",
      generatedAt: (snapshot || {}).generatedAt || "",
      mode: toss.mode || "",
      status: toss.status || "",
      board: {
        title: "오늘의 투자 판단판",
        state: items.length ? "blocked" : "ready",
        tone: items.length ? "caution" : "watch",
        summary: "서버 분석 모델이 없어서 현재 스냅샷으로 기본 판단판을 구성했습니다.",
        metrics: [
          { label: "보유", value: positions.length, caption: "holding" },
          { label: "관심", value: watchlist.length, caption: "watchlist" },
          { label: "액션 후보", value: items.length, caption: "queue" },
          { label: "추론 보류", value: 0, caption: "blocked" }
        ],
        checklist: Array.isArray((snapshot || {}).checklist) ? (snapshot || {}).checklist : []
      },
      accountFocus: {
        holdingCount: positions.length,
        watchCount: watchlist.length,
        symbols: positions.concat(watchlist).map(function (item) { return String(item.symbol || "").toUpperCase(); }).filter(Boolean)
      },
      actionQueue: items,
      dataLineage: {
        actualCount: positions.concat(watchlist).length,
        mockCount: 0,
        items: positions.concat(watchlist).map(function (item) {
          return {
            symbol: item.symbol,
            name: item.name,
            source: item.quoteSource || item.source || "snapshot",
            quality: item.dataQuality || "actual",
            updatedAt: item.updatedAt || "",
            status: item.quoteStatus || ""
          };
        })
      },
      moneyFlow: { buckets: [], emergingFlows: [] },
      graphGate: { status: "blocked", tone: "caution", blockedCount: 0, relationCount: 0, entityCount: 0, requiredSource: "neo4jInferenceBox", nextChecks: [] }
    };
  }

  function renderInvestmentDecisionBoardPanel(snapshot) {
    var analysis = investmentAnalysisModel(snapshot);
    var board = analysis.board || {};
    var metrics = Array.isArray(board.metrics) ? board.metrics : [];
    var checklist = Array.isArray(board.checklist) ? board.checklist : [];
    return [
      '<article class="panel investment-board-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Investment Board</p>',
      '<h2>' + escapeHtml(board.title || "오늘의 투자 판단판") + '</h2>',
      '<p class="subtle">' + escapeHtml(board.summary || "데이터, 체크리스트, 그래프 추론 상태를 먼저 확인합니다.") + '</p>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(board.tone || "hold") + '">' + escapeHtml(board.state === "ready" ? "판단 가능" : "판단 보류") + '</span>',
      '</div>',
      '<div class="investment-board-metrics">',
      metrics.map(function (item) {
        return [
          '<section>',
          '<span>' + escapeHtml(item.caption || "") + '</span>',
          '<strong>' + escapeHtml(item.value) + '</strong>',
          '<em>' + escapeHtml(item.label || "") + '</em>',
          '</section>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="investment-checklist-grid">',
      checklist.length ? checklist.map(function (item) {
        return [
          '<div class="investment-check-row">',
          '<strong>' + escapeHtml(item.label || item.title || "-") + '</strong>',
          '<span>' + escapeHtml(item.status || "대기") + '</span>',
          '</div>'
        ].join("");
      }).join("") : '<div class="ontology-empty">오늘 체크리스트가 아직 없습니다.</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderInvestmentGraphGatePanel(snapshot) {
    var gate = investmentAnalysisModel(snapshot).graphGate || {};
    var checks = Array.isArray(gate.nextChecks) ? gate.nextChecks : [];
    return [
      '<article class="panel investment-gate-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Inference Gate</p>',
      '<h2>추론 가능 상태</h2>',
      '<p class="subtle">' + escapeHtml(gate.reason || "InferenceBox 관계와 데이터 신선도를 확인합니다.") + '</p>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(gate.tone || "hold") + '">' + escapeHtml(gate.status || "unknown") + '</span>',
      '</div>',
      '<div class="investment-gate-grid">',
      renderInvestmentGateMetric("요구 출처", gate.requiredSource || "neo4jInferenceBox"),
      renderInvestmentGateMetric("관계", gate.relationCount || 0),
      renderInvestmentGateMetric("엔티티", gate.entityCount || 0),
      renderInvestmentGateMetric("보류", gate.blockedCount || 0),
      '</div>',
      '<div class="rule-strip">',
      checks.slice(0, 3).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") || '<span>추론 상태 확인 대기</span>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderInvestmentGateMetric(label, value) {
    return '<section><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong></section>';
  }

  function renderInvestmentActionQueuePanel(snapshot) {
    var rows = Array.isArray(investmentAnalysisModel(snapshot).actionQueue) ? investmentAnalysisModel(snapshot).actionQueue : [];
    return [
      '<article class="panel investment-action-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Action Queue</p>',
      '<h2>보유·관심 종목 액션 큐</h2>',
      '<p class="subtle">매수·매도 결론이 아니라, 그래프 추론과 체크리스트를 통과하기 전 확인할 후보 목록입니다.</p>',
      '</div>',
      '<span class="metric">' + escapeHtml(rows.length) + '</span>',
      '</div>',
      '<div class="investment-action-list">',
      rows.length ? rows.slice(0, 10).map(renderInvestmentActionRow).join("") : '<div class="ontology-empty">액션 큐가 비어 있습니다.</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderInvestmentActionRow(row) {
    row = row || {};
    var graph = row.graph || {};
    var reasons = Array.isArray(row.reasons) ? row.reasons : [];
    var checks = Array.isArray(graph.nextChecks) ? graph.nextChecks : [];
    var name = row.name || stockDisplayName(row.symbol, row);
    return [
      '<div class="investment-action-row">',
      '<div class="investment-action-main">',
      '<strong>' + escapeHtml(name) + '</strong>',
      '<span>' + escapeHtml([row.symbol, sourceLabel(row.source), row.market, row.sector].filter(Boolean).join(" · ")) + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(row.tone || "hold") + '">' + escapeHtml(row.decision || "판단 대기") + '</span>',
      '<div class="investment-action-meta">',
      '<span>데이터 <strong>' + escapeHtml(row.dataQuality || "-") + '</strong></span>',
      '<span>API <strong>' + escapeHtml(row.apiSource || "-") + '</strong></span>',
      '<span>손익률 <strong>' + escapeHtml(row.profitLossRate || 0) + '%</strong></span>',
      graph.blocked ? '<span>차단 <strong>' + escapeHtml(graph.basis || "InferenceBox") + '</strong></span>' : '<span>추론 <strong>ready</strong></span>',
      '</div>',
      '<p>' + escapeHtml((reasons[0] || graph.reason || "다음 확인 조건을 먼저 봅니다.")) + '</p>',
      checks.length ? '<div class="investment-action-checks">' + checks.slice(0, 3).map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") + '</div>' : '',
      '</div>'
    ].join("");
  }

  function renderInvestmentDataLineagePanel(snapshot) {
    var lineage = investmentAnalysisModel(snapshot).dataLineage || {};
    var rows = Array.isArray(lineage.items) ? lineage.items : [];
    return [
      '<article class="panel investment-lineage-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Data Lineage</p>',
      '<h2>실제·mock·API 출처</h2>',
      '<p class="subtle">화면에 쓰인 기본 데이터가 실제인지 mock인지, 어떤 API에서 왔는지 분리합니다.</p>',
      '</div>',
      '<span class="metric">' + escapeHtml(lineage.actualCount || 0) + '/' + escapeHtml((lineage.actualCount || 0) + (lineage.mockCount || 0)) + '</span>',
      '</div>',
      '<div class="investment-lineage-list">',
      rows.length ? rows.slice(0, 12).map(function (row) {
        return [
          '<div class="investment-lineage-row">',
          '<div><strong>' + escapeHtml(row.name || stockDisplayName(row.symbol, row)) + '</strong><span>' + escapeHtml(row.symbol || "") + '</span></div>',
          '<em>' + escapeHtml(row.quality || "-") + '</em>',
          '<span>' + escapeHtml(row.source || "-") + '</span>',
          '<b>' + escapeHtml(row.updatedAt ? formatClock(row.updatedAt) : "-") + '</b>',
          '</div>'
        ].join("");
      }).join("") : '<div class="ontology-empty">데이터 출처가 없습니다.</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderInvestmentMoneyFlowPanel(snapshot) {
    var flow = investmentAnalysisModel(snapshot).moneyFlow || {};
    var buckets = Array.isArray(flow.buckets) ? flow.buckets : [];
    var emerging = Array.isArray(flow.emergingFlows) ? flow.emergingFlows : [];
    return [
      '<article class="panel investment-flow-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Global Flow Lens</p>',
      '<h2>세계 돈의 흐름 단서</h2>',
      '<p class="subtle">현재 계좌 노출과 외부 신호가 어떤 자산·시장 흐름을 봐야 하는지 알려줍니다.</p>',
      '</div>',
      '<span class="metric">' + escapeHtml(buckets.length + emerging.length) + '</span>',
      '</div>',
      '<div class="investment-flow-grid">',
      buckets.length ? buckets.map(function (bucket) {
        return [
          '<section>',
          '<span>' + escapeHtml(bucket.source || "") + '</span>',
          '<strong>' + escapeHtml(bucket.label || bucket.key || "-") + '</strong>',
          '<em>' + escapeHtml(bucket.value ? formatMoney(bucket.value) : bucket.caption || "-") + '</em>',
          '</section>'
        ].join("");
      }).join("") : '<div class="ontology-empty">시장 흐름 버킷 없음</div>',
      '</div>',
      '<div class="investment-emerging-list">',
      emerging.length ? emerging.map(function (item) {
        return '<div><strong>' + escapeHtml(item.title || "-") + '</strong><span>' + escapeHtml(item.description || "") + '</span><em>' + escapeHtml(item.source || "") + '</em></div>';
      }).join("") : '<div class="ontology-empty">새 흐름 후보가 아직 없습니다.</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderInvestmentBridgePanel(snapshot) {
    var parts = ontologyStrategyParts(snapshot);
    var cards = investmentReasoningCards(snapshot);
    var packet = investmentAiInferencePacket(snapshot);
    var graphInputs = packet.graphInputs || {};
    var steps = [
      ["01", "전략 근거", "가격·수급·추세·관계 점수", cards.length + " cards"],
      ["02", "관계 그래프", "HOLDS/WATCHES와 TBox 규칙", (graphInputs.relationCount || parts.relations.length || 0) + " relations"],
      ["03", "AI 추론 입력", packet.contract || "investment-ontology-ai-inference-v1", (packet.reasoningCardCount || cards.length || 0) + " refs"],
      ["04", "투자 의견", "관계·반대 신호·다음 검증", (graphInputs.opinionCount || parts.opinions.length || 0) + " opinions"]
    ];
    return [
      '<article class="panel investment-bridge-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Investment Analysis</p>',
      '<h2>전략 데이터와 관계 분석을 잇는 추론 구조</h2>',
      '<p class="subtle">최종 점수는 관계 규칙으로 계산하고, AI는 TBox/ABox 관계와 reasoning card를 기준으로 의견을 만듭니다.</p>',
      '</div>',
      '<span class="tone-chip watch">ontology-first</span>',
      '</div>',
      '<div class="investment-bridge-flow">',
      steps.map(function (step) {
        return [
          '<div class="investment-bridge-step">',
          '<b>' + escapeHtml(step[0]) + '</b>',
          '<span><strong>' + escapeHtml(step[1]) + '</strong><em>' + escapeHtml(step[2]) + '</em></span>',
          '<i>' + escapeHtml(step[3]) + '</i>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="rule-strip">',
      '<span>보유 종목은 HOLDS, 관심 종목은 WATCHES 관계로 구분합니다.</span>',
      '<span>AI 추론 입력은 strategyEvidence, relationEvidence, graphContext ID를 함께 전달합니다.</span>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderInvestmentAiPacketPanel(snapshot) {
    var packet = investmentAiInferencePacket(snapshot);
    var inputOrder = Array.isArray(packet.inputOrder) ? packet.inputOrder : [];
    var guardrails = Array.isArray(packet.guardrails) ? packet.guardrails : [];
    return [
      '<article class="panel investment-ai-packet-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">AI Inference Packet</p>',
      '<h2>AI 추론 입력 계약</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(packet.reasoningCardCount || investmentReasoningCards(snapshot).length || 0) + '</span>',
      '</div>',
      '<div class="investment-packet-grid">',
      '<section><strong>계약</strong><span>' + escapeHtml(packet.contract || "investment-ontology-ai-inference-v1") + '</span><em>' + escapeHtml(packet.promptVersion || "-") + '</em></section>',
      '<section><strong>입력 순서</strong><span>' + escapeHtml(inputOrder.join(" → ") || "tbox → abox → reasoningCards") + '</span><em>legacyModelRole=' + escapeHtml(packet.legacyModelRole || "not-used-for-scoring") + '</em></section>',
      '<section><strong>가드레일</strong><span>' + escapeHtml(guardrails.slice(0, 2).join(" / ") || "제공된 관계 데이터만 사용") + '</span><em>AI가 없는 값은 추정하지 않음</em></section>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderInvestmentReasoningCardPanel(snapshot, options) {
    options = options || {};
    var cards = investmentReasoningCards(snapshot);
    var visible = options.compact ? cards.slice(0, 3) : cards;
    return [
      '<article class="panel investment-evidence-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Reasoning Cards</p>',
      '<h2>투자 근거 카드</h2>',
      '<p class="subtle">각 카드는 전략 근거, 관계 근거, 그래프 참조, AI 질문을 같은 단위로 묶습니다.</p>',
      '</div>',
      '<span class="metric">' + escapeHtml(cards.length) + '</span>',
      '</div>',
      '<div class="investment-evidence-list">',
      visible.length ? visible.map(renderInvestmentReasoningCard).join("") : renderEmptyState({
        tone: "muted",
        label: "Reasoning",
        title: "연결된 투자 근거 카드가 없습니다",
        description: "계좌 스냅샷, 시세, 뉴스·공시 근거가 수집되면 전략 근거와 관계 근거를 묶어 표시합니다.",
        meta: ["TBox/ABox", "Evidence", "AI opinion"]
      }),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderInvestmentReasoningCard(card) {
    var finalOpinion = card.finalOpinion || {};
    var relationRows = Array.isArray(card.relationEvidence) ? card.relationEvidence : [];
    var influenceRows = Array.isArray(card.relationInfluences) ? card.relationInfluences : [];
    var evidenceRows = Array.isArray(card.strategyEvidence) ? card.strategyEvidence : [];
    var planRows = Array.isArray(card.executionPlans) ? card.executionPlans : [];
    var gaps = Array.isArray(card.dataGaps) ? card.dataGaps : [];
    var displayName = card.companyName || card.displayName || stockDisplayName(card.symbol);
    var tone = finalOpinion.tone || (gaps.length ? "hold" : "watch");
    var thesis = textWithKnownDisplaySymbols(beginnerFriendlyText(finalOpinion.thesis || finalOpinion.action || ""), card.symbol, { symbol: card.symbol, name: displayName });
    return [
      '<div class="investment-evidence-card">',
      '<div class="investment-evidence-head">',
      '<div>',
      '<strong>' + escapeHtml(displayName) + '</strong>',
      '<span>' + escapeHtml([card.portfolioRelation || "-", sourceLabel(card.source || ""), card.status || ""].filter(Boolean).join(" · ")) + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(finalOpinion.action || "관계 의견 대기") + '</span>',
      '</div>',
      '<div class="investment-evidence-grid">',
      '<span>관계 신호 <strong>' + escapeHtml(Math.round(Number(finalOpinion.ontologyPressure || finalOpinion.ontology_pressure || 0))) + '</strong></span>',
      '<span>확신 <strong>' + escapeHtml(finalOpinion.conviction || 0) + '</strong></span>',
      '<span>전략 근거 <strong>' + escapeHtml(evidenceRows.length) + '</strong></span>',
      '<span>관계 근거 <strong>' + escapeHtml(relationRows.length) + '</strong></span>',
      '<span>관계 영향 <strong>' + escapeHtml(influenceRows.length) + '</strong></span>',
      '<span>실행 계획 <strong>' + escapeHtml(planRows.length) + '</strong></span>',
      '</div>',
      '<div class="investment-evidence-narrative">',
      thesis ? '<p>' + escapeHtml(thesis) + '</p>' : '',
      gaps.length ? '<p>데이터 공백: ' + escapeHtml(gaps.join(", ")) + '</p>' : '',
      '</div>',
      '<div class="investment-evidence-columns">',
      renderReasoningCardList("전략 근거", evidenceRows.slice(0, 3).map(function (item) { return item.summary || item.kind || item.id; })),
      renderReasoningCardList("의견 영향", influenceRows.slice(0, 3).map(function (item) {
        var risk = Number(item.riskImpact || 0);
        var support = Number(item.supportImpact || 0);
        var impact = risk ? ("리스크 +" + Math.round(risk)) : support ? ("지지 +" + Math.round(support)) : "";
        return [item.label || item.type, item.scope, impact].filter(Boolean).join(" · ");
      })),
      renderReasoningCardList("관계 근거", relationRows.slice(0, 3).map(function (item) {
        return [item.type, item.sourceLabel, item.targetLabel].filter(Boolean).join(" · ");
      })),
      '</div>',
      renderReasoningExecutionPlan(planRows[0]),
      renderReasoningGraphRefs(card),
      '</div>'
    ].join("");
  }

  function compactPlanList(value, limit) {
    if (!Array.isArray(value)) return [];
    return value.map(function (item) { return String(item || "").trim(); }).filter(Boolean).slice(0, limit || 4);
  }

  function renderReasoningExecutionPlan(plan) {
    if (!plan || typeof plan !== "object") return "";
    var blocked = compactPlanList(plan.blockedActions, 3);
    var strengthen = compactPlanList(plan.strengthenConditions, 3);
    var weaken = compactPlanList(plan.weakenConditions, 3);
    var nextChecks = compactPlanList(plan.nextChecks, 4);
    var primary = plan.primaryActionLabel || plan.primaryAction || "실행 판단 대기";
    var meta = [plan.decisionStage, plan.actionGroup, plan.actionLevel].filter(Boolean).join(" · ");
    return [
      '<div class="reasoning-execution-plan">',
      '<div class="reasoning-execution-head">',
      '<strong>' + escapeHtml(primary) + '</strong>',
      '<span>' + escapeHtml(meta || "실행 조건 확인") + '</span>',
      '</div>',
      '<div class="reasoning-execution-grid">',
      renderReasoningCardList("다음 확인", nextChecks),
      renderReasoningCardList("보류 조건", blocked),
      renderReasoningCardList("강화 조건", strengthen),
      renderReasoningCardList("약화 조건", weaken),
      '</div>',
      '</div>'
    ].join("");
  }

  function renderReasoningCardList(title, rows) {
    return [
      '<div class="reasoning-card-list">',
      '<strong>' + escapeHtml(title) + '</strong>',
      rows.length ? rows.map(function (row) { return '<span>' + escapeHtml(row) + '</span>'; }).join("") : '<span>연결된 항목 없음</span>',
      '</div>'
    ].join("");
  }

  function renderReasoningGraphRefs(card) {
    var context = card.graphContext || {};
    var tboxClasses = Array.isArray(context.tboxClasses) ? context.tboxClasses : [];
    return [
      '<div class="reasoning-graph-refs">',
      '<span>Graph ref <strong>' + escapeHtml(context.stockEntityId || ("stock:" + (card.symbol || ""))) + '</strong></span>',
      '<span>TBox <strong>' + escapeHtml(tboxClasses.slice(0, 4).join(", ") || "-") + '</strong></span>',
      '<span>AI 질문 <strong>' + escapeHtml(((card.aiInference || {}).role) || "ontology-first-investment-opinion") + '</strong></span>',
      '</div>'
    ].join("");
  }

  function ontologyExecutionRows(cards, parts) {
    var rows = [];
    var seen = {};
    (cards || []).forEach(function (card) {
      var plans = Array.isArray(card.executionPlans) ? card.executionPlans : [];
      plans.forEach(function (plan) {
        if (!plan || typeof plan !== "object") return;
        var symbol = String(plan.symbol || card.symbol || "").toUpperCase();
        var primary = String(plan.primaryActionLabel || plan.primaryAction || "").trim();
        var key = [symbol, primary, plan.decisionStage || ""].join("|");
        if (seen[key]) return;
        seen[key] = true;
        rows.push({
          symbol: symbol,
          displayName: card.companyName || card.displayName || stockDisplayName(symbol),
          relation: card.portfolioRelation || "",
          opinion: card.finalOpinion || {},
          plan: plan
        });
      });
    });
    if (!rows.length && parts && Array.isArray(parts.activeInvestmentOpinions)) {
      parts.activeInvestmentOpinions.forEach(function (opinion) {
        var plan = opinion && typeof opinion.executionPlan === "object" ? opinion.executionPlan : null;
        if (!plan) return;
        var symbol = String(opinion.symbol || plan.symbol || "").toUpperCase();
        var key = [symbol, plan.primaryActionLabel || plan.primaryAction || ""].join("|");
        if (seen[key]) return;
        seen[key] = true;
        rows.push({
          symbol: symbol,
          displayName: stockDisplayName(symbol),
          relation: "",
          opinion: opinion,
          plan: plan
        });
      });
    }
    return rows;
  }

  function renderOntologyExecutionPlanPanel(cards, parts) {
    var rows = ontologyExecutionRows(cards || [], parts || {});
    return [
      '<section class="ontology-surface ontology-execution-surface">',
      '<div class="ontology-surface-head">',
      '<strong>실행 계획과 다음 확인</strong>',
      '<span>' + escapeHtml(rows.length) + ' plans · 보유/관심 판단 이후 확인할 조건</span>',
      '</div>',
      '<div class="ontology-execution-list">',
      rows.length ? rows.slice(0, 8).map(renderOntologyExecutionPlanRow).join("") : '<div class="ontology-empty">실행 계획 데이터가 아직 없습니다.</div>',
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyExecutionPlanRow(row) {
    var plan = row.plan || {};
    var nextChecks = compactPlanList(plan.nextChecks, 3);
    var blocked = compactPlanList(plan.blockedActions, 2);
    var strengthen = compactPlanList(plan.strengthenConditions, 2);
    var weaken = compactPlanList(plan.weakenConditions, 2);
    var primary = plan.primaryActionLabel || plan.primaryAction || "실행 판단 대기";
    var tone = (row.opinion || {}).tone || (plan.actionLevel === "action" ? "caution" : "hold");
    return [
      '<div class="ontology-execution-row">',
      '<div class="ontology-execution-title">',
      '<strong>' + escapeHtml(row.displayName || row.symbol || "-") + '</strong>',
      '<span>' + escapeHtml([row.relation, plan.decisionStage, plan.actionGroup, plan.actionLevel].filter(Boolean).join(" · ") || "관계 조건 확인") + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(primary) + '</span>',
      '<div class="ontology-execution-detail">',
      renderOntologyPlanMiniList("다음 확인", nextChecks),
      renderOntologyPlanMiniList("보류", blocked),
      renderOntologyPlanMiniList("강화", strengthen),
      renderOntologyPlanMiniList("약화", weaken),
      '</div>',
      '</div>'
    ].join("");
  }

  function renderOntologyPlanMiniList(label, rows) {
    return [
      '<div class="ontology-plan-mini-list">',
      '<strong>' + escapeHtml(label) + '</strong>',
      rows.length ? rows.map(function (item) { return '<span>' + escapeHtml(item) + '</span>'; }).join("") : '<span>-</span>',
      '</div>'
    ].join("");
  }

  function renderOntologyOperationalPanel(parts) {
    var operational = (parts || {}).operationalOntology || {};
    var pipelines = Array.isArray(operational.pipelines) ? operational.pipelines : [];
    return [
      '<section class="ontology-surface ontology-operational-surface">',
      '<div class="ontology-surface-head">',
      '<strong>운영 온톨로지</strong>',
      '<span>' + escapeHtml(operational.dispatchMode || "insight-driven-only") + '</span>',
      '</div>',
      '<div class="ontology-operational-grid">',
      renderOntologyOperationalMetric("수집 파이프라인", operational.collectionPipelineCount || pipelines.length || 0, "DataPipeline"),
      renderOntologyOperationalMetric("인사이트", operational.insightCount || ((parts || {}).insights || []).length || 0, "Insight"),
      renderOntologyOperationalMetric("디스패치", operational.dispatchMode || "-", "NotificationDispatch"),
      '</div>',
      '<div class="ontology-operational-list">',
      pipelines.length ? pipelines.map(function (pipeline) {
        return [
          '<div class="ontology-operational-row">',
          '<strong>' + escapeHtml(pipeline.key || "-") + '</strong>',
          '<span>target ' + escapeHtml(pipeline.targetMinutes || "-") + '분</span>',
          '<em>configured ' + escapeHtml(pipeline.configuredMinutes || "-") + '분</em>',
          '</div>'
        ].join("");
      }).join("") : '<div class="ontology-empty">수집 파이프라인 정보가 없습니다.</div>',
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyOperationalMetric(label, value, caption) {
    return [
      '<div class="ontology-operational-metric">',
      '<span>' + escapeHtml(caption || "") + '</span>',
      '<strong>' + escapeHtml(value) + '</strong>',
      '<em>' + escapeHtml(label) + '</em>',
      '</div>'
    ].join("");
  }

  function renderOntologyInsightPanel(parts) {
    parts = parts || {};
    var insights = Array.isArray(parts.insights) && parts.insights.length
      ? parts.insights
      : (parts.aboxEntities || []).filter(function (item) { return String(item && item.kind || "") === "insight"; });
    return [
      '<section class="ontology-surface ontology-insight-surface">',
      '<div class="ontology-surface-head">',
      '<strong>인사이트·알림 디스패치</strong>',
      '<span>' + escapeHtml(insights.length) + ' insights · ' + escapeHtml(((parts.operationalOntology || {}).dispatchMode) || "insight-driven-only") + '</span>',
      '</div>',
      '<div class="ontology-insight-list">',
      insights.length ? insights.slice(0, 8).map(renderOntologyInsightRow).join("") : '<div class="ontology-empty">생성된 인사이트가 없습니다.</div>',
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyInsightRow(item) {
    var properties = (item && item.properties) || {};
    var tone = properties.severity || "hold";
    var score = properties.score == null ? "-" : Math.round(Number(properties.score || 0));
    var confidence = properties.confidence == null ? "-" : Math.round(Number(properties.confidence || 0));
    return [
      '<div class="ontology-insight-row">',
      '<div>',
      '<strong>' + escapeHtml(ontologyEntityDisplayLabel(item, item && item.id)) + '</strong>',
      '<span>' + escapeHtml([properties.symbol, properties.insightType, properties.dispatchCandidate ? "dispatch candidate" : "reference"].filter(Boolean).join(" · ")) + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(score) + '</span>',
      '<em>confidence ' + escapeHtml(confidence) + '</em>',
      properties.thesis ? '<p>' + escapeHtml(textWithKnownDisplaySymbols(beginnerFriendlyText(properties.thesis), properties.symbol, { symbol: properties.symbol })) + '</p>' : '',
      '</div>'
    ].join("");
  }

  function renderOntologyDataQualityPanel(parts) {
    parts = parts || {};
    var nodes = Array.isArray(parts.dataQuality) && parts.dataQuality.length
      ? parts.dataQuality
      : (parts.aboxEntities || []).filter(function (item) {
        return ["data-quality", "data-freshness", "provenance", "source-reliability", "missing-data"].indexOf(String(item && item.kind || "")) >= 0;
      });
    return [
      '<section class="ontology-surface ontology-data-quality-surface">',
      '<div class="ontology-surface-head">',
      '<strong>데이터 품질·출처</strong>',
      '<span>' + escapeHtml(nodes.length) + ' nodes · freshness/provenance</span>',
      '</div>',
      '<div class="ontology-data-quality-list">',
      nodes.length ? nodes.slice(0, 10).map(renderOntologyDataQualityRow).join("") : '<div class="ontology-empty">데이터 품질 노드가 없습니다.</div>',
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyDataQualityRow(item) {
    var properties = (item && item.properties) || {};
    var value = properties.qualityScore != null ? Math.round(Number(properties.qualityScore || 0)) : (properties.status || properties.provider || properties.kind || "-");
    var meta = [
      item && item.kind,
      properties.fetchedAt ? formatClock(properties.fetchedAt) : "",
      properties.ageMinutes != null ? Math.round(Number(properties.ageMinutes || 0)) + "분" : "",
      Array.isArray(properties.sources) ? properties.sources.slice(0, 3).join(", ") : ""
    ].filter(Boolean).join(" · ");
    return [
      '<div class="ontology-data-quality-row">',
      '<div>',
      '<strong>' + escapeHtml(ontologyEntityDisplayLabel(item, item && item.id)) + '</strong>',
      '<span>' + escapeHtml(meta || "품질 정보") + '</span>',
      '</div>',
      '<em>' + escapeHtml(value) + '</em>',
      '</div>'
    ].join("");
  }

  function renderStrategySectionContent(snapshot) {
    var section = activeSectionForPageMode("modeling", strategySections, normalizeStrategySection(state.activeStrategySection));
    var parts = ontologyStrategyParts(snapshot);
    if (section === "evidence") {
      return renderInvestmentTabWorkspace("evidence", [
        { role: "main", html: renderInvestmentReasoningCardPanel(snapshot) },
        { role: "side", html: renderOntologyExecutionPlanPanel(investmentReasoningCards(snapshot), parts) + renderStrategyDataPanel(snapshot) }
      ]);
    }
    if (section === "results") {
      return renderInvestmentTabWorkspace("results", [
        { role: "main", html: renderInvestmentActionQueuePanel(snapshot) + renderModelPreviewPanel(snapshot) },
        { role: "side", html: renderInvestmentGraphGatePanel(snapshot) + renderInvestmentDataLineagePanel(snapshot) + renderOntologyExecutionPlanPanel(investmentReasoningCards(snapshot), parts) }
      ]);
    }
    if (section === "graphs") {
      return renderInvestmentTabWorkspace("graphs", [
        { role: "full", html: [
        '<article class="panel ontology-panel">',
        '<div class="panel-head">',
        '<div>',
        '<p class="label">Ontology Graphs</p>',
        '<h2>TBox·ABox 관계 그래프</h2>',
        '<p class="subtle">TBox 규칙 구조와 ABox 현재 데이터가 reasoning card를 거쳐 AI 의견으로 연결됩니다.</p>',
        '</div>',
        '<span class="metric">' + escapeHtml(parts.aboxRelations.length) + '</span>',
        '</div>',
        '<div class="ontology-dashboard">',
        renderInvestmentRuleRelationTextPanel(parts),
        renderOntologyRelationshipGraphs(parts.tbox, parts.abox, parts.aboxEntities, parts.aboxRelations, parts.evidence, parts.beliefs, parts.opinions, parts.entityLabels, parts.relationCounts),
        renderOntologyClassPanel(parts.tbox),
        renderOntologyAboxPanel(parts.abox, parts.aboxEntities, parts.evidence, parts.beliefs, parts.opinions),
        '</div>',
        '</article>'
      ].join("") }
      ]);
    }
    if (section === "registry") {
      return renderInvestmentTabWorkspace("registry", [
        { role: "main", html: renderOntologyRuleEditorPanel(snapshot) + renderNeo4jRuleboxPanel(snapshot) },
        { role: "side", html: renderInvestmentAiPacketPanel(snapshot) + renderAiPromptRegistryPanel(snapshot) + renderAdminModelingPanel(snapshot) }
      ]);
    }
    if (section === "trace") {
      return renderInvestmentTabWorkspace("trace", [
        { role: "full", html: [
        '<article class="panel ontology-panel ontology-trace-panel">',
        '<div class="panel-head">',
        '<div>',
        '<p class="label">Relation Trace</p>',
        '<h2>관계형 데이터·규칙 추적</h2>',
        '</div>',
        '<span class="metric">' + escapeHtml(parts.relations.length) + '</span>',
        '</div>',
        '<div class="ontology-dashboard">',
        renderOntologyRelationalProjectionPanel(parts.entities, parts.relations, parts.evidence, parts.beliefs, parts.opinions, parts),
        renderOntologyInsightPanel(parts),
        renderOntologyDataQualityPanel(parts),
        renderOntologyMacroRelationPanel(parts),
        renderOntologyRelationPanel(parts.tbox, parts.relations, parts.aboxRelations, parts.relationCounts, parts.entityLabels),
        renderOntologyRulePanel(parts.tbox, parts.relationCounts, parts.evidence, parts.beliefs, parts.opinions),
        '</div>',
        '</article>'
      ].join("") }
      ]);
    }
    return renderInvestmentTabWorkspace("overview", [
      { role: "summary", html: renderInvestmentDecisionBoardPanel(snapshot) + renderInvestmentGraphGatePanel(snapshot) },
      { role: "main", html: renderInvestmentActionQueuePanel(snapshot) + renderInvestmentMoneyFlowPanel(snapshot) },
      { role: "side", html: renderInvestmentDataLineagePanel(snapshot) + renderInvestmentBridgePanel(snapshot) + renderOntologyMacroSignalPanel(parts) + renderInvestmentAiPacketPanel(snapshot) + renderOntologyOperationalPanel(parts) }
    ]);
  }

  function ontologyRuleboxRules() {
    var payload = state.ontologyRulebox || {};
    return Array.isArray(payload.rules) ? payload.rules : [];
  }

  function ontologyRuleId(rule) {
    return String(rule && (rule.rule_id || rule.ruleId || rule.id) || "");
  }

  function ontologyRuleConditions(rule) {
    return Array.isArray(rule && rule.conditions) ? rule.conditions : [];
  }

  function ontologyRuleDerivations(rule) {
    return Array.isArray(rule && rule.derivations) ? rule.derivations : [];
  }

  function ontologyRuleRelationTypes(rule) {
    var types = [];
    ontologyRuleConditions(rule).forEach(function (condition) {
      var type = String(condition.relation_type || condition.relationType || "").toUpperCase();
      if (type && types.indexOf(type) < 0) types.push(type);
    });
    ontologyRuleDerivations(rule).forEach(function (derivation) {
      var type = String(derivation.relation_type || derivation.relationType || "").toUpperCase();
      if (type && types.indexOf(type) < 0) types.push(type);
    });
    return types;
  }

  function ontologyReadableRuleRows(parts) {
    var rules = ontologyRuleboxRules();
    if (!rules.length) {
      return ((((parts || {}).tbox || {}).reasoningRuleDefinitions || [])).slice(0, 8).map(function (item, index) {
        return {
          id: "tbox-rule-" + index,
          label: item.text || item.label || "TBox reasoning rule",
          detail: item.bounded_context || item.boundedContext || "reasoning-insight",
          relationTypes: [],
          conditionCount: 0,
          derivationCount: 0
        };
      });
    }
    return rules.slice().sort(function (a, b) {
      var priority = String(b.action_level || b.actionLevel || "").localeCompare(String(a.action_level || a.actionLevel || ""));
      return priority || ontologyRuleId(a).localeCompare(ontologyRuleId(b));
    }).slice(0, 10).map(function (rule) {
      return {
        id: ontologyRuleId(rule),
        label: rule.label || ontologyRuleId(rule) || "RuleBox rule",
        detail: [rule.action_group || rule.actionGroup, rule.action_level || rule.actionLevel, rule.prompt_hint || rule.promptHint].filter(Boolean).join(" · "),
        relationTypes: ontologyRuleRelationTypes(rule),
        conditionCount: ontologyRuleConditions(rule).length,
        derivationCount: ontologyRuleDerivations(rule).length
      };
    });
  }

  function ontologyRelationPriority(type) {
    var order = [
      "HAS_INFERRED_RISK",
      "HAS_INFERRED_SUPPORT",
      "HAS_INFERRED_ENTRY_OPPORTUNITY",
      "CREATES_NOTIFICATION_INTENT",
      "REQUIRES_NEXT_CHECK",
      "HAS_TREND_TRANSITION",
      "HAS_EXTERNAL_SIGNAL",
      "HAS_DATA_QUALITY",
      "HAS_TRADE_FLOW",
      "BREAKS_LEVEL",
      "RETESTS_LEVEL",
      "RECLAIMS_LEVEL",
      "HOLDS",
      "WATCHES",
      "HAS_OPINION",
      "HAS_EVIDENCE"
    ];
    var index = order.indexOf(String(type || "").toUpperCase());
    return index < 0 ? 999 : index;
  }

  function ontologyReadableRelationRows(parts) {
    parts = parts || {};
    var labels = parts.entityLabels || {};
    var groups = {};
    (parts.aboxRelations || []).forEach(function (relation) {
      var type = ontologyTypeOf(relation) || "RELATED_TO";
      if (!groups[type]) groups[type] = { type: type, count: 0, examples: [] };
      groups[type].count += 1;
      if (groups[type].examples.length < 3) {
        groups[type].examples.push(ontologyEndpointLabel(relation.source, labels) + " → " + ontologyEndpointLabel(relation.target, labels));
      }
    });
    return Object.keys(groups).map(function (key) {
      return groups[key];
    }).sort(function (a, b) {
      var priority = ontologyRelationPriority(a.type) - ontologyRelationPriority(b.type);
      if (priority !== 0) return priority;
      if (b.count !== a.count) return b.count - a.count;
      return a.type.localeCompare(b.type);
    }).slice(0, 12);
  }

  function ontologyInferenceRelationTypes() {
    return {
      HAS_INFERRED_RISK: true,
      HAS_INFERRED_SUPPORT: true,
      HAS_INFERRED_ENTRY_OPPORTUNITY: true,
      HAS_ACTION_CANDIDATE: true,
      CREATES_NOTIFICATION_INTENT: true,
      REQUIRES_NEXT_CHECK: true,
      HAS_INFERENCE_TRACE: true
    };
  }

  function ontologyReadableInferenceRows(parts) {
    parts = parts || {};
    var labels = parts.entityLabels || {};
    var inferenceTypes = ontologyInferenceRelationTypes();
    var rows = (parts.relations || parts.aboxRelations || []).filter(function (relation) {
      var type = ontologyTypeOf(relation);
      return inferenceTypes[type] || ontologyBoxOf(relation) === "INFERENCEBOX";
    }).slice(0, 10).map(function (relation) {
      var props = relation.properties || {};
      return {
        type: ontologyTypeOf(relation),
        source: ontologyEndpointLabel(relation.source, labels),
        target: ontologyEndpointLabel(relation.target, labels),
        detail: [props.aiInfluenceLabel, props.decisionStage, props.ruleId].filter(Boolean).join(" · "),
        weight: relation.weight
      };
    });
    if (rows.length) return rows;
    return (parts.insights || []).slice(0, 6).map(function (item) {
      var props = item.properties || {};
      return {
        type: "INSIGHT",
        source: props.symbol ? stockDisplayName(props.symbol) : "ontology",
        target: ontologyEntityDisplayLabel(item, item && item.id),
        detail: [props.insightType, props.severity, props.confidence != null ? "confidence " + Math.round(Number(props.confidence || 0)) : ""].filter(Boolean).join(" · "),
        weight: props.score
      };
    });
  }

  function renderInvestmentRuleRelationTextPanel(parts) {
    var ruleRows = ontologyReadableRuleRows(parts);
    var relationRows = ontologyReadableRelationRows(parts);
    var inferenceRows = ontologyReadableInferenceRows(parts);
    return [
      '<section class="ontology-surface investment-rule-relation-text-panel">',
      '<div class="ontology-surface-head">',
      '<div>',
      '<strong>규칙과 관계 해설</strong>',
      '<span>그래프를 읽기 전에 RuleBox 조건, 현재 관계 행, 추론 출력을 압축해서 확인합니다.</span>',
      '</div>',
      '<span>' + escapeHtml(ruleRows.length) + ' rules · ' + escapeHtml(relationRows.length) + ' relation groups</span>',
      '</div>',
      '<div class="investment-relation-text-grid">',
      renderInvestmentRuleTextColumn("RuleBox 규칙", "조건과 파생 관계", ruleRows, renderInvestmentRuleTextRow),
      renderInvestmentRuleTextColumn("현재 ABox 관계", "실제 데이터 관계 묶음", relationRows, renderInvestmentRelationTextRow),
      renderInvestmentRuleTextColumn("InferenceBox 출력", "AI 판단으로 넘어가는 추론", inferenceRows, renderInvestmentInferenceTextRow),
      '</div>',
      '</section>'
    ].join("");
  }

  function renderInvestmentRuleTextColumn(title, caption, rows, renderer) {
    return [
      '<section class="investment-relation-text-column">',
      '<div class="investment-relation-text-head">',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<span>' + escapeHtml(caption) + '</span>',
      '</div>',
      '<div class="investment-relation-text-list">',
      rows.length ? rows.map(renderer).join("") : '<div class="ontology-empty">표시할 행이 없습니다.</div>',
      '</div>',
      '</section>'
    ].join("");
  }

  function renderInvestmentRuleTextRow(row) {
    return [
      '<div class="investment-relation-text-row rule">',
      '<strong>' + escapeHtml(row.label || row.id || "-") + '</strong>',
      '<span>' + escapeHtml(row.detail || row.id || "-") + '</span>',
      '<em>' + escapeHtml(row.conditionCount + " conditions · " + row.derivationCount + " derives") + '</em>',
      row.relationTypes.length ? '<b>' + escapeHtml(row.relationTypes.slice(0, 4).join(" / ")) + '</b>' : '',
      '</div>'
    ].join("");
  }

  function renderInvestmentRelationTextRow(row) {
    return [
      '<div class="investment-relation-text-row relation">',
      '<strong>' + escapeHtml(row.type || "-") + '</strong>',
      '<span>' + escapeHtml((row.examples || []).join(" · ") || "-") + '</span>',
      '<em>' + escapeHtml(row.count + " rows") + '</em>',
      '</div>'
    ].join("");
  }

  function renderInvestmentInferenceTextRow(row) {
    var weight = row.weight == null || row.weight === "" ? "" : "weight " + Number(row.weight || 0).toFixed(2);
    return [
      '<div class="investment-relation-text-row inference">',
      '<strong>' + escapeHtml(row.type || "-") + '</strong>',
      '<span>' + escapeHtml([row.source, row.target].filter(Boolean).join(" → ")) + '</span>',
      '<em>' + escapeHtml([row.detail, weight].filter(Boolean).join(" · ") || "-") + '</em>',
      '</div>'
    ].join("");
  }

  function renderStrategyProcessPanel(snapshot) {
    var items = buildTradeSignalItems(snapshot);
    var stats = modelStatsForItems(items);
    var thresholds = modelDecisionThresholds();
    var steps = [
      ["01", "데이터 정합", "보유·관심·시장 입력", items.length + " symbols"],
      ["02", "근거 추출", "손익·수급·추세·외부 신호", modelVariableGuide().length + " fields"],
      ["03", "관계 규칙", "관계 규칙 성립 여부", ontologyRuleRows().length + " rules"],
      ["04", "AI Prompt", "비동기 해석 정보", promptTemplateRows().length + " prompts"],
      ["05", "Result", "종목별 판단 결과", Math.round(stats.buyAverage || 0) + " / " + Math.round(stats.sellAverage || 0)],
      ["06", "Alert", "주기·템플릿·발송 정책 연결", notificationEnabledRuleCount() + " types"]
    ];
    return [
      '<article class="panel strategy-process-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Strategy Workflow</p>',
      '<h2>데이터에서 알림까지의 계산 순서</h2>',
      '</div>',
      '<span class="tone-chip hold">read-only model</span>',
      '</div>',
      '<div class="process-rail">',
      steps.map(renderProcessStep).join(""),
      '</div>',
      '<div class="rule-strip">',
      '<span>모델링 화면은 입력값, 공식, 기준값, 결과를 순서대로 검증하는 운영 화면입니다.</span>',
      '<span>공식 오류는 기본값으로 대체하고 결과 카드에 오류를 표시합니다.</span>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderProcessStep(step) {
    return [
      '<div class="process-step">',
      '<b>' + escapeHtml(step[0]) + '</b>',
      '<span>',
      '<strong>' + escapeHtml(step[1]) + '</strong>',
      '<em>' + escapeHtml(step[2]) + '</em>',
      '</span>',
      '<i>' + escapeHtml(step[3]) + '</i>',
      '</div>'
    ].join("");
  }

  function renderAdminOverviewPanel(snapshot) {
    var rules = alertRules();
    var cadences = alertCadenceMinutes();
    var policyRules = notificationPolicyCatalog();
    var enabledRules = policyRules.filter(function (rule) { return enabledAlertRule(rules, rule.key); }).length;
    var realtimeKeys = policyRules.filter(function (rule) {
      return ["투자 알림", "외부 API", "실시간"].indexOf(rule.group) >= 0;
    }).map(function (rule) { return rule.key; });
    var realtimeCadence = realtimeKeys.reduce(function (min, key) {
      var value = Number(cadences[key] || 0);
      return value > 0 ? Math.min(min, value) : min;
    }, 9999);
    var portfolio = snapshot.portfolio || {};
    var accounts = state.serviceAccounts || [];
    var activeAccounts = accounts.filter(function (account) { return account.enabled !== false; }).length;
    var configuredAccounts = accounts.filter(function (account) {
      return account.clientId && account.clientSecret;
    }).length;
    var telegramAccounts = accounts.filter(function (account) {
      return account.telegramBotToken && account.telegramChatId;
    }).length;
    return [
      '<article class="panel admin-overview-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Home</p>',
      '<h2>운영 요약</h2>',
      '</div>',
      '<span class="status-pill ' + (isStaticPreviewHost() ? "demo" : "live") + '">' + (isStaticPreviewHost() ? "Pages preview" : "Local server") + '</span>',
      '</div>',
      '<div class="home-command-grid">',
      '<div class="home-command-main">',
      '<span class="home-kicker">계정 ' + escapeHtml(activeAccounts + "/" + accounts.length) + ' · 알림 ' + escapeHtml(enabledRules + "/" + policyRules.length) + '</span>',
      '<strong>' + escapeHtml(tossModeLabel(snapshot)) + '</strong>',
      '<p>계정 연결, 관심종목, 알림 템플릿, 모델 기준을 한 곳에서 운영합니다.</p>',
      '</div>',
      '<div class="home-action-grid">',
      renderHomeAction("accounts", "계정", configuredAccounts + "개 API 연결", "토스·텔레그램"),
      renderHomeAction("notifications", "알림", enabledRules + "개 활성", "템플릿 테스트"),
      renderHomeAction("modeling", "투자 분석", "전략·관계·AI", "근거 카드"),
      '</div>',
      '</div>',
      '<div class="admin-stat-grid home-stat-grid">',
      renderAdminStat("활성 계정", activeAccounts + "/" + accounts.length, ""),
      renderAdminStat("토스 API", configuredAccounts, "개"),
      renderAdminStat("텔레그램", telegramAccounts, "개"),
      renderAdminStat("최소 알림 주기", (realtimeCadence === 9999 ? "-" : realtimeCadence), realtimeCadence === 9999 ? "" : "분"),
      renderAdminStat("평가 자산", formatMoney(portfolio.total || 0), ""),
      renderAdminStat("데이터", snapshot.preview ? "Preview" : "Live", ""),
      '</div>',
      '<div class="home-signal-strip">',
      renderHomeSignal("토스", configuredAccounts ? "API 정보 저장됨" : "API 정보 필요", configuredAccounts ? "ok" : "warn"),
      renderHomeSignal("알림", telegramAccounts ? "텔레그램 연결됨" : "알림 채널 확인", telegramAccounts ? "ok" : "warn"),
      renderHomeSignal("실시간", state.realtime.connected ? "웹소켓 연결됨" : "HTTP 대기", state.realtime.connected ? "ok" : "warn"),
      renderHomeSignal("저장소", isStaticPreviewHost() ? "정적 미리보기" : "MySQL 운영 DB", isStaticPreviewHost() ? "warn" : "ok"),
      '</div>',
      '</article>'
    ].join("");
  }

  function tossModeLabel(snapshot) {
    var toss = snapshot.toss || {};
    if (snapshot.preview) return "정적 미리보기 모드";
    if (toss.mode === "live") return "토스 실데이터 연결됨";
    return "로컬 서버 대기";
  }

  function renderHomeAction(tab, label, value, caption) {
    return [
      '<button class="home-action" data-tab="' + escapeHtml(tab) + '">',
      '<span>' + escapeHtml(label) + '</span>',
      '<strong>' + escapeHtml(value) + '</strong>',
      '<em>' + escapeHtml(caption) + '</em>',
      '</button>'
    ].join("");
  }

  function renderHomeSignal(label, value, tone) {
    return [
      '<span class="home-signal ' + escapeHtml(tone || "ok") + '">',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value) + '</strong>',
      '</span>'
    ].join("");
  }

  function renderAdminStat(label, value, suffix) {
    return [
      '<span>',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value) + escapeHtml(suffix || "") + '</strong>',
      '</span>'
    ].join("");
  }

  function accountWatchlistSymbols(account) {
    if (!account) return [];
    return Array.isArray(account.watchlistSymbols)
      ? normalizeSymbols(account.watchlistSymbols.join(","))
      : normalizeSymbols(account.watchlistSymbols || "");
  }

  function accountIdOf(account) {
    return String((account && (account.id || account.accountId)) || "").trim();
  }

  function accountById(id) {
    var normalized = String(id || "").trim();
    return (state.serviceAccounts || []).filter(function (account) {
      return accountIdOf(account) === normalized;
    })[0] || null;
  }

  function syncActiveWatchAccountId() {
    var accounts = state.serviceAccounts || [];
    if (!accounts.length) {
      state.activeWatchAccountId = "";
      return;
    }
    if (accountById(state.activeWatchAccountId)) return;
    state.activeWatchAccountId = accountIdOf(accounts[0]);
  }

  function activeWatchAccount() {
    syncActiveWatchAccountId();
    return accountById(state.activeWatchAccountId);
  }

  function preferredWatchlistSymbols() {
    var account = activeWatchAccount();
    return account ? accountWatchlistSymbols(account) : watchlistSymbols();
  }

  function watchlistAccountLabel(account) {
    account = account || activeWatchAccount();
    return account ? String(account.label || account.id || "계정") : "기본 관심목록";
  }

  function watchSymbolDisplay(symbol, item) {
    var original = String(symbol || (item && item.symbol) || "").trim().toUpperCase();
    var name = stockDisplayName(original, item);
    return {
      symbol: original,
      name: name,
      label: name
    };
  }

  function watchSymbolListText(symbols) {
    var labels = (symbols || []).map(function (symbol) {
      return watchSymbolDisplay(symbol).label;
    }).filter(Boolean);
    return labels.length ? labels.join(", ") : "-";
  }

  function renderWatchSymbolChip(symbol, item) {
    var display = watchSymbolDisplay(symbol, item);
    return '<span class="chip" title="' + escapeHtml(display.name) + '">' + escapeHtml(display.label) + '</span>';
  }

  function allAccountWatchlistSymbols() {
    var seen = {};
    var symbols = [];
    (state.serviceAccounts || []).forEach(function (account) {
      accountWatchlistSymbols(account).forEach(function (symbol) {
        if (seen[symbol]) return;
        seen[symbol] = true;
        symbols.push(symbol);
      });
    });
    return symbols;
  }

  function realtimeEventLabel(name) {
    return {
      "realtime.connected": "웹소켓 연결",
      "realtime.status": "실시간 상태",
      "settings.updated": "설정 변경",
      "account.saved": "계정 저장",
      "account.removed": "계정 삭제",
      "notification_template.updated": "알림 템플릿",
      "notification_rule.updated": "알림 룰",
      "notification.test_requested": "테스트 알림 요청",
      "notification.job_queued": "알림 큐 적재",
      "monitoring.snapshot_collected": "모니터링 스냅샷",
      "monitoring.alerts_detected": "모니터링 알림",
      "monitoring.cycle_completed": "모니터링 사이클",
      "symbol_universe.refreshed": "전체 종목 갱신"
    }[name] || name || "-";
  }

  function renderAccountDirectoryPanel(options) {
    options = options || {};
    var accounts = state.serviceAccounts || [];
    var enabled = accounts.filter(function (account) { return account.enabled !== false; }).length;
    var tossReady = accounts.filter(function (account) { return account.clientId && account.clientSecret; }).length;
    var telegramReady = accounts.filter(function (account) { return account.telegramBotToken && account.telegramChatId; }).length;
    var quietEnabled = accounts.filter(function (account) { return account.quietHoursEnabled !== false; }).length;
    var classes = "panel account-directory-panel" + (options.full ? " account-directory-wide" : "");
    return [
      '<article class="' + classes + '">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Account DB</p>',
      '<h2>DB 저장 계정</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(enabled + "/" + accounts.length) + '</span>',
      '</div>',
      '<div class="account-directory-summary">',
      renderDirectoryStat("활성", enabled + "/" + accounts.length),
      renderDirectoryStat("토스 API", tossReady + "개"),
      renderDirectoryStat("텔레그램", telegramReady + "개"),
      renderDirectoryStat("알림 금지", quietEnabled + "개"),
      '</div>',
      '<div class="account-card-list">',
      state.serviceAccountsLoading ? '<p class="subtle">계정 DB를 읽는 중입니다.</p>' : '',
      state.serviceAccountsError ? '<p class="form-error">' + escapeHtml(state.serviceAccountsError) + '</p>' : '',
      accounts.length ? accounts.map(function (account) {
        return renderAccountDirectoryRow(account, options);
      }).join("") : '<p class="subtle">아직 DB에 저장된 계정이 없습니다. 계정 탭에서 등록하세요.</p>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderDirectoryStat(label, value) {
    return [
      '<span>',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value) + '</strong>',
      '</span>'
    ].join("");
  }

  function renderAccountDirectoryRow(account, options) {
    options = options || {};
    var symbols = accountWatchlistSymbols(account);
    var enabled = account.enabled !== false;
    var provider = String(account.provider || "toss").toUpperCase();
    return [
      '<div class="account-card">',
      '<div class="account-card-head">',
      '<span class="account-provider-badge">' + escapeHtml(provider.slice(0, 4)) + '</span>',
      '<div>',
      '<strong>' + escapeHtml(account.label || account.id || "-") + '</strong>',
      '<span>' + escapeHtml(account.id || "-") + ' · ' + escapeHtml(account.provider || "toss") + ' · ' + escapeHtml(enabled ? "사용" : "중지") + '</span>',
      '</div>',
      '<button class="mini-button" data-account-edit="' + escapeHtml(account.id || "") + '">수정</button>',
      '</div>',
      options.compact ? renderAccountCredentialPills(account) : renderAccountCredentialSummary(account),
      '<div class="account-card-meta"><span class="chip">관심 ' + escapeHtml(symbols.length) + '개</span><span class="chip">' + escapeHtml(accountQuietHoursText(account)) + '</span></div>',
      options.compact ? '' : '<div class="chip-row">' + (symbols.length ? symbols.map(function (symbol) {
        return renderWatchSymbolChip(symbol);
      }).join("") : '<span class="subtle">계정에 저장된 관심 종목이 없습니다.</span>') + '</div>',
      '</div>'
    ].join("");
  }

  function renderAccountCredentialPills(account) {
    account = account || {};
    return [
      '<div class="account-credential-pills">',
      configuredChip("Toss API", Boolean(account.clientId && account.clientSecret)),
      configuredChip("계좌 seq", Boolean(account.accountSeq), account.accountSeq || "선택"),
      configuredChip("Telegram", Boolean(account.telegramBotToken && account.telegramChatId)),
      configuredChip("알림 금지", account.quietHoursEnabled !== false, accountQuietHoursText(account)),
      '</div>'
    ].join("");
  }

  function accountQuietHoursText(account) {
    account = account || {};
    if (account.quietHoursEnabled === false) return "알림 금지 꺼짐";
    return "알림 금지 " + String(account.quietHoursStart || "22:00") + "-" + String(account.quietHoursEnd || "05:00") + " " + String(account.quietHoursTimezone || "Asia/Seoul");
  }

  function configuredChip(label, configured, detail) {
    return [
      '<span class="chip ' + (configured ? "ok" : "missing") + '">',
      escapeHtml(label + " " + (configured ? "설정됨" : "미설정")),
      detail ? '<em>' + escapeHtml(detail) + '</em>' : '',
      '</span>'
    ].join("");
  }

  function renderAccountCredentialSummary(account) {
    account = account || {};
    var provider = account.notifyProvider || settingValue("notifyProvider") || "-";
    return [
      '<div class="account-credential-grid">',
      '<div>',
      '<strong>토스</strong>',
      '<span>' + escapeHtml(account.baseUrl || "https://openapi.tossinvest.com") + '</span>',
      '<div class="chip-row">',
      configuredChip("API key", Boolean(account.clientId)),
      configuredChip("Secret", Boolean(account.clientSecret)),
      configuredChip("계좌 seq", Boolean(account.accountSeq), account.accountSeq || "선택 안함"),
      '</div>',
      '</div>',
      '<div>',
      '<strong>텔레그램</strong>',
      '<span>' + escapeHtml(provider + (account.notifyLinkUrl ? " · " + account.notifyLinkUrl : "")) + '</span>',
      '<div class="chip-row">',
      configuredChip("Bot token", Boolean(account.telegramBotToken)),
      configuredChip("Chat ID", Boolean(account.telegramChatId), account.telegramChatId ? "저장됨" : ""),
      configuredChip("알림 링크", Boolean(account.notifyLinkUrl)),
      '</div>',
      '</div>',
      '<div>',
      '<strong>알림 금지 시간</strong>',
      '<span>' + escapeHtml(accountQuietHoursText(account)) + '</span>',
      '<div class="chip-row">',
      configuredChip("금지 시간", account.quietHoursEnabled !== false, accountQuietHoursText(account)),
      configuredChip("전달 수준", true, messageDeliveryLevelLabel(account.messageDeliveryLevel)),
      '</div>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderEmptyAccountCredentialSummary() {
    return [
      '<div class="account-credential-grid empty">',
      '<div>',
      '<strong>토스</strong>',
      '<span>새 계정을 저장하면 API key, secret, 계좌 seq 설정 상태가 여기에 표시됩니다.</span>',
      '<div class="chip-row">' + configuredChip("API key", false) + configuredChip("Secret", false) + '</div>',
      '</div>',
      '<div>',
      '<strong>텔레그램</strong>',
      '<span>알림 채널 저장 후 bot token과 chat id 상태를 확인할 수 있습니다.</span>',
      '<div class="chip-row">' + configuredChip("Bot token", false) + configuredChip("Chat ID", false) + '</div>',
      '</div>',
      '<div>',
      '<strong>알림 금지 시간</strong>',
      '<span>기본값은 22:00-05:00 Asia/Seoul입니다.</span>',
      '<div class="chip-row">' + configuredChip("금지 시간", true, "22:00-05:00 Asia/Seoul") + '</div>',
      '<div class="chip-row">' + configuredChip("전달 수준", true, "왕초보") + '</div>',
      '</div>',
      '</div>'
    ].join("");
  }

  function accountRowStatusChip(account) {
    var ready = Boolean(account.clientId && account.clientSecret && account.telegramBotToken && account.telegramChatId);
    if (account.enabled === false) return '<span class="status-pill demo">중지</span>';
    return '<span class="status-pill ' + (ready ? "live" : "demo") + '">' + escapeHtml(ready ? "연결 완료" : "설정 확인") + '</span>';
  }

  function renderAccountWatchlistPanel(options, snapshot) {
    options = options || {};
    var accounts = state.serviceAccounts || [];
    var merged = allAccountWatchlistSymbols();
    var activeAccount = activeWatchAccount();
    var editable = Boolean(options.editable);
    var classes = "panel account-watchlist-panel" + (options.full ? " account-watchlist-wide" : "");
    return [
      '<article class="' + classes + '">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Account Watchlist</p>',
      '<h2>' + escapeHtml(editable ? "계정별 관심 종목 등록" : "계정별 관심 종목") + '</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(merged.length) + '</span>',
      '</div>',
      editable ? renderAccountWatchlistWorkbench(accounts, activeAccount, snapshot) : [
        '<div class="watch-account-list">',
        accounts.length ? accounts.map(function (account) {
          return renderAccountWatchlistRow(account, { selectable: false });
        }).join("") : '<p class="subtle">계정 DB를 읽으면 계정별 관심 종목이 여기에 표시됩니다.</p>',
        '</div>'
      ].join(""),
      '</article>'
    ].join("");
  }

  function renderAccountWatchlistWorkbench(accounts, activeAccount, snapshot) {
    return [
      '<div class="account-watchlist-workbench">',
      '<div class="watch-account-rail">',
      '<div class="account-column-head"><strong>계정 선택</strong><span>관심 종목은 선택한 계정에 저장됩니다.</span></div>',
      state.serviceAccountsLoading ? '<p class="subtle">계정 DB를 읽는 중입니다.</p>' : '',
      state.serviceAccountsError ? '<p class="form-error">' + escapeHtml(state.serviceAccountsError) + '</p>' : '',
      accounts.length ? accounts.map(function (account) {
        return renderAccountWatchlistRow(account, { selectable: true });
      }).join("") : '<p class="subtle">계정 탭에서 먼저 계정을 등록하세요.</p>',
      '</div>',
      renderAccountWatchlistEditor(activeAccount, snapshot),
      '</div>'
    ].join("");
  }

  function renderAccountWatchlistRow(account, options) {
    options = options || {};
    var symbols = accountWatchlistSymbols(account);
    var active = accountIdOf(account) === state.activeWatchAccountId;
    var tag = options.selectable ? "button" : "div";
    var attrs = options.selectable
      ? ' type="button" data-watch-account-select="' + escapeHtml(accountIdOf(account)) + '"'
      : "";
    return [
      '<' + tag + ' class="watch-account-row' + (options.selectable ? " selectable" : "") + (active ? " active" : "") + '"' + attrs + '>',
      '<div>',
      '<strong>' + escapeHtml(account.label || account.id || "-") + '</strong>',
      '<span>' + escapeHtml(account.id || "-") + ' · ' + escapeHtml(account.enabled === false ? "중지" : "사용") + '</span>',
      '</div>',
      '<div class="chip-row">',
      symbols.length ? symbols.map(function (symbol) {
        return renderWatchSymbolChip(symbol);
      }).join("") : '<span class="subtle">저장된 관심 종목 없음</span>',
      '</div>',
      options.selectable ? '<span class="watch-account-action">' + escapeHtml(active ? "선택됨" : "관리") + '</span>' : '',
      '</' + tag + '>'
    ].join("");
  }

  function accountWatchlistQuoteLookup(snapshot) {
    var toss = snapshot && snapshot.toss ? snapshot.toss : {};
    var lookup = {};
    (toss.watchlist || []).forEach(function (item) {
      lookup[String(item.symbol || "").toUpperCase()] = item;
    });
    ((toss.positions || []) || []).forEach(function (item) {
      var symbol = String(item.symbol || "").toUpperCase();
      if (!symbol) return;
      lookup[symbol] = Object.assign({}, item, {
        source: item.source || "holding",
        quoteStatus: "보유 종목으로 분류됨"
      });
    });
    return lookup;
  }

  function renderAccountWatchlistEditor(account, snapshot) {
    if (!account) {
      return [
        '<div class="account-watchlist-editor empty">',
        '<div class="settings-note">',
        '<strong>계정이 필요합니다</strong>',
        '<p>관심 종목은 계정별로 저장됩니다. 계정 탭에서 계정을 만든 뒤 이 화면에서 종목을 등록하세요.</p>',
        '</div>',
        '</div>'
      ].join("");
    }
    var accountId = accountIdOf(account);
    var symbols = accountWatchlistSymbols(account);
    var lookup = accountWatchlistQuoteLookup(snapshot);
    var locked = isStaticPreviewHost() || state.serverSettingsLocked || state.watchlistSavingAccountId === accountId;
    return [
      '<div class="account-watchlist-editor">',
      '<div class="account-watchlist-editor-head">',
      '<div>',
      '<strong>' + escapeHtml(watchlistAccountLabel(account)) + '</strong>',
      '<span>' + escapeHtml(accountId + " · 관심 " + symbols.length + "개 · " + (account.enabled === false ? "중지" : "사용")) + '</span>',
      '</div>',
      '<button class="mini-button" type="button" data-account-edit="' + escapeHtml(accountId) + '">계정 설정</button>',
      '</div>',
      '<div class="watch-editor account-watch-editor">',
      '<form class="watch-add-form" data-watch-add-form data-watch-account-id="' + escapeHtml(accountId) + '">',
      '<input name="symbol" data-watch-symbol-input placeholder="회사명으로 검색" value="' + escapeHtml(state.watchSuggestQuery || "") + '" autocomplete="off"' + (locked ? " disabled" : "") + ' />',
      '<button class="text-button primary"' + (locked ? " disabled" : "") + '>' + escapeHtml(state.watchlistSavingAccountId === accountId ? "저장 중" : "추가") + '</button>',
      '</form>',
      '<div class="watch-suggest-box" data-watch-suggest-list data-watch-account-id="' + escapeHtml(accountId) + '">' + renderWatchSuggestList() + '</div>',
      '<p class="subtle">검색 후 저장하면 이 계정의 알림·모니터링 기준으로 쓰입니다. 시장 전체 목록은 전체종목 탭에서 따로 봅니다.</p>',
      state.watchlistError ? '<p class="form-error">' + escapeHtml(state.watchlistError) + '</p>' : '',
      '</div>',
      '<div class="account-watch-symbol-list">',
      symbols.length ? symbols.map(function (symbol) {
        return renderAccountWatchSymbolRow(account, symbol, lookup[symbol] || clientKnownStockInfo(symbol), locked);
      }).join("") : '<p class="subtle">이 계정에 등록된 관심 종목이 없습니다.</p>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderAccountWatchSymbolRow(account, symbol, item, locked) {
    var accountId = accountIdOf(account);
    var original = String(symbol || "").toUpperCase();
    if (state.editingWatchAccountId === accountId && state.editingWatchSymbol === original) {
      return [
        '<form class="account-watch-edit-row" data-account-watch-edit-form="' + escapeHtml(original) + '" data-watch-account-id="' + escapeHtml(accountId) + '">',
        '<input name="symbol" value="' + escapeHtml(original) + '" autocomplete="off" />',
        '<button class="text-button primary">저장</button>',
        '<button class="text-button" type="button" data-account-watch-cancel>취소</button>',
        '</form>'
      ].join("");
    }
    var merged = Object.assign(clientKnownStockInfo(original), item || {}, { symbol: original });
    return [
      '<div class="account-watch-symbol-row">',
      '<div class="account-watch-symbol-main">',
      '<strong>' + escapeHtml(stockDisplayName(original, merged)) + '</strong>',
      '<span>' + escapeHtml(stockDisplayMeta(merged, [marketLabel(merged.market || "-"), merged.sector || "-"])) + '</span>',
      renderWatchAlertMeta(merged),
      '</div>',
      '<div class="account-watch-symbol-side">',
      '<strong>' + escapeHtml(merged.currentPrice ? formatCurrency(merged.currentPrice, merged.currency) : "시세 대기") + '</strong>',
      '<span>' + escapeHtml(merged.changeRate == null ? merged.quoteStatus || "토스 시세 연결 후 표시" : signedPct(merged.changeRate)) + '</span>',
      '<div class="row-actions">',
      '<button class="mini-button" data-account-watch-edit="' + escapeHtml(original) + '" data-watch-account-id="' + escapeHtml(accountId) + '"' + (locked ? " disabled" : "") + '>수정</button>',
      '<button class="mini-button danger" data-account-watch-remove="' + escapeHtml(original) + '" data-watch-account-id="' + escapeHtml(accountId) + '"' + (locked ? " disabled" : "") + '>삭제</button>',
      '</div>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderMonitorLedgerCell(label, value, tone) {
    return [
      '<div class="monitor-ledger-cell ' + escapeHtml(tone || "") + '">',
      '<span>' + escapeHtml(label || "-") + '</span>',
      '<strong>' + escapeHtml(value == null ? "-" : value) + '</strong>',
      '</div>'
    ].join("");
  }

  function renderMonitorRuntimeRow(label, value, detail, tone) {
    return [
      '<div class="monitor-runtime-row ' + escapeHtml(tone || "") + '">',
      '<span>' + escapeHtml(label || "-") + '</span>',
      '<strong>' + escapeHtml(value || "-") + '</strong>',
      detail ? '<em>' + escapeHtml(detail) + '</em>' : '',
      '</div>'
    ].join("");
  }

  function renderMonitorAlertSummary() {
    var event = normalizeRealtimeEvent(state.realtime.monitoring && state.realtime.monitoring.alerts);
    var payload = event && event.payload || {};
    var count = Number(payload.count || 0);
    var symbols = Array.isArray(payload.symbols) ? payload.symbols.slice(0, 4) : [];
    var symbolText = symbols.length ? symbols.map(function (symbol) {
      return stockDisplayName(symbol, clientKnownStockInfo(symbol));
    }).join(", ") : "최근 감지된 모니터링 알림 없음";
    return [
      '<section class="monitor-alert-summary ' + escapeHtml(count ? "active" : "idle") + '">',
      '<span>최근 모니터링 알림</span>',
      '<strong>' + escapeHtml(count ? count + "건 감지" : "대기 중") + '</strong>',
      '<p>' + escapeHtml(symbolText) + '</p>',
      '<em>' + escapeHtml(event && event.occurredAt ? formatClock(event.occurredAt) : "알림 이벤트 대기") + '</em>',
      '</section>'
    ].join("");
  }

  function renderMonitorRuntimeBoard() {
    var cycleEvent = normalizeRealtimeEvent(state.realtime.monitoring && state.realtime.monitoring.cycle);
    var cyclePayload = cycleEvent && cycleEvent.payload || {};
    var cycleValue = cycleEvent
      ? "스냅샷 " + Number(cyclePayload.snapshotCount || 0) + " · 알림 " + Number(cyclePayload.alertCount || 0)
      : "사이클 대기";
    var cycleDetail = cycleEvent && cycleEvent.occurredAt ? formatClock(cycleEvent.occurredAt) : "모니터링 사이클 이벤트 대기";
    return [
      '<section class="monitor-board-section monitor-runtime-strip monitor-runtime-board" aria-label="모니터링 런타임 상태">',
      '<div class="monitor-section-head">',
      '<strong>런타임 신호</strong>',
      '<span>실시간 연결, 사이클, 큐 상태</span>',
      '</div>',
      '<div class="monitor-runtime-timeline">',
      renderMonitorRuntimeRow("웹소켓 최근 이벤트", state.realtime.lastEvent ? realtimeEventLabel(state.realtime.lastEvent) : "이벤트 대기", state.realtime.lastEventAt ? formatClock(state.realtime.lastEventAt) : "연결 이벤트 대기", state.realtime.connected ? "live" : ""),
      renderMonitorRuntimeRow("최근 모니터링 사이클", cycleValue, cycleDetail, ""),
      renderMonitorRuntimeRow("알림 큐", notificationJobSummaryText(state.realtime.notificationJobs), "notification worker queue", "live"),
      '</div>',
      renderMonitorAlertSummary(),
      '</section>'
    ].join("");
  }

  function renderAdminMonitoringPanel(snapshot) {
    var toss = snapshot.toss || {};
    var portfolio = snapshot.portfolio || {};
    var accounts = state.serviceAccounts || [];
    var enabledCount = accounts.filter(function (account) { return account.enabled !== false; }).length;
    var positions = ((toss.positions || []) || []).filter(function (item) {
      return item.source !== "cash" && item.sector !== "현금";
    });
    var healthRows = [
      ["활성 계정", enabledCount + "/" + accounts.length, "live"],
      ["보유 종목", positions.length + "개", ""],
      ["평가 금액", formatMoney(portfolio.total || 0), ""],
      ["토스 연결", toss.status || "-", ""],
      ["마지막 갱신", formatClock(snapshot.generatedAt), ""]
    ];
    var marketRows = (portfolio.markets || []).map(function (market) {
      return renderMonitorLedgerCell(market.label || market.key || "-", "현금 " + pct(market.cashRatio || 0), "");
    }).join("");
    var liveLabel = snapshot.preview ? "정적 미리보기" : "실데이터 실행";
    return [
      '<article class="panel admin-monitoring-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Monitoring</p>',
      '<h2>모니터링 실행 상태</h2>',
      '</div>',
      '<span class="status-pill ' + (snapshot.preview ? "demo" : "live") + '">' + escapeHtml(snapshot.preview ? "Preview" : "Live") + '</span>',
      '</div>',
      '<div class="monitor-board">',
      '<section class="monitor-board-section monitor-status-board">',
      '<div class="monitor-section-head">',
      '<strong>현재 실행 상태</strong>',
      '<span>연결 상태와 핵심 지표</span>',
      '</div>',
      '<div class="monitor-primary-state">',
      '<div class="monitor-primary-head">',
      '<span class="tone-chip ' + (snapshot.preview ? "hold" : "watch") + '">' + escapeHtml(liveLabel) + '</span>',
      '</div>',
      '<div class="monitor-primary-copy">',
      '<strong>' + escapeHtml(toss.status || "연결 상태 확인") + '</strong>',
      '<em>최근 데이터 ' + escapeHtml(formatClock(snapshot.generatedAt)) + ' · ' + escapeHtml(realtimeEventLabel(state.realtime.lastEvent)) + '</em>',
      '</div>',
      '</div>',
      '<div class="monitor-health-ledger">',
      healthRows.map(function (row) {
        return renderMonitorLedgerCell(row[0], row[1], row[2]);
      }).join(""),
      '</div>',
      '</section>',
      renderMonitorRuntimeBoard(),
      marketRows ? '<section class="monitor-board-section monitor-market-section"><div class="monitor-section-head"><strong>시장별 현금</strong><span>매수 여력 기준</span></div><div class="monitor-market-ledger">' + marketRows + '</div></section>' : '',
      '</div>',
      '<div class="rule-strip"><span>실제 백그라운드 워커 실행/중지는 로컬 명령으로 관리하고, 웹은 저장된 계정과 알림 설정을 같은 로컬 DB/설정 파일에 기록합니다.</span></div>',
      '</article>'
    ].join("");
  }

  function accountSnapshotMode(snapshot) {
    var toss = (snapshot || {}).toss || {};
    if ((snapshot || {}).preview) return "preview";
    if ((snapshot || {}).mock || (snapshot || {}).dataMode === "mock") return "mock";
    return toss.mode || (snapshot && snapshot.dataMode) || "unknown";
  }

  function accountSnapshotModeLabel(snapshot) {
    var mode = accountSnapshotMode(snapshot);
    if (mode === "live") return "실제 데이터";
    if (mode === "mock") return "mock 데이터";
    if (mode === "preview") return "정적 미리보기";
    if (mode === "demo") return "demo 데이터";
    return "데이터 대기";
  }

  function accountSnapshotTone(snapshot) {
    var mode = accountSnapshotMode(snapshot);
    if (mode === "live") return "live";
    if (mode === "mock" || mode === "preview" || mode === "demo") return "demo";
    return "watch";
  }

  function timestampAgeMinutes(value) {
    if (!value) return null;
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return Math.max(0, Math.round((Date.now() - date.getTime()) / 60000));
  }

  function accountFreshness(snapshot) {
    var generatedAt = (snapshot || {}).generatedAt || "";
    var age = timestampAgeMinutes(generatedAt);
    var maxAge = Number(settingValue("marketDataMaxAgeMinutes") || settingValue("dataFreshnessDefaultMaxAgeMinutes") || 30);
    if (age == null) {
      return { label: "기준시각 없음", detail: "스냅샷 generatedAt 없음", tone: "warn" };
    }
    return {
      label: age <= maxAge ? "신선" : "지연",
      detail: age + "분 전 · 기준 " + maxAge + "분",
      tone: age <= maxAge ? "ok" : "warn"
    };
  }

  function accountSnapshotItems(snapshot) {
    var toss = (snapshot || {}).toss || {};
    var positions = Array.isArray(toss.positions) ? toss.positions : [];
    var watchlist = Array.isArray(toss.watchlist) ? toss.watchlist : [];
    return positions.concat(watchlist).filter(function (item) {
      return item && String(item.symbol || "").toUpperCase() !== "CASH";
    });
  }

  function accountDataQualityCounts(snapshot) {
    var mode = accountSnapshotMode(snapshot);
    return accountSnapshotItems(snapshot).reduce(function (memo, item) {
      var quality = String(item.dataQuality || "").toLowerCase();
      if (mode === "mock" || mode === "preview" || quality.indexOf("mock") >= 0 || quality.indexOf("demo") >= 0) {
        memo.mock += 1;
      } else if (quality.indexOf("cache") >= 0 || quality.indexOf("cached") >= 0) {
        memo.cached += 1;
      } else if (item.currentPrice || item.marketValue || item.quoteSource) {
        memo.actual += 1;
      } else {
        memo.pending += 1;
      }
      return memo;
    }, { actual: 0, cached: 0, mock: 0, pending: 0 });
  }

  function currentAccountLabel(snapshot) {
    var accounts = state.serviceAccounts || [];
    if (accounts.length === 1) return accounts[0].label || accounts[0].id || "계정";
    var displayNumber = (((snapshot || {}).toss || {}).account || {}).displayNumber || "";
    if (displayNumber) return "현재 조회 계좌 " + displayNumber;
    return accounts.length ? "다중 계정" : "계정 미등록";
  }

  function renderAccountControlMetric(label, value, detail, tone) {
    return [
      '<span class="account-control-metric ' + escapeHtml(tone || "neutral") + '">',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value == null ? "-" : value) + '</strong>',
      detail ? '<b>' + escapeHtml(detail) + '</b>' : '',
      '</span>'
    ].join("");
  }

  function renderAccountApiStatusRow(label, value, detail, tone) {
    return [
      '<div class="account-api-row ' + escapeHtml(tone || "neutral") + '">',
      '<span>' + escapeHtml(label) + '</span>',
      '<strong>' + escapeHtml(value || "-") + '</strong>',
      '<em>' + escapeHtml(detail || "") + '</em>',
      '</div>'
    ].join("");
  }

  function renderAccountApiLedger(accounts, snapshot) {
    var configured = state.serverConfigured || {};
    var tossReady = accounts.filter(function (account) { return account.clientId && account.clientSecret; }).length;
    var telegramReady = accounts.filter(function (account) { return account.telegramBotToken && account.telegramChatId; }).length;
    var items = accountSnapshotItems(snapshot);
    var kisItems = items.filter(function (item) {
      return String(item.quoteSource || item.signalSource || "").toLowerCase().indexOf("kis") >= 0;
    }).length;
    return [
      '<section class="account-api-ledger">',
      renderAccountApiStatusRow("Toss Open API", tossReady + "/" + accounts.length + " 계정", ((snapshot.toss || {}).status || "계정별 key/secret 기준"), tossReady ? "ok" : "warn"),
      renderAccountApiStatusRow("KIS 시세·수급", configured.kisAppKey && configured.kisAppSecret ? "키 저장됨" : "키 필요", kisItems ? kisItems + "개 종목 보강" : "시세 보강 대기", configured.kisAppKey && configured.kisAppSecret ? "ok" : "warn"),
      renderAccountApiStatusRow("OpenDART 공시", configured.opendartApiKey ? "키 저장됨" : "키 필요", settingValue("externalDartEnabled") ? "공시 수집 사용" : "공시 수집 꺼짐", configured.opendartApiKey ? "ok" : "warn"),
      renderAccountApiStatusRow("코인·매크로", [configured.coingeckoApiKey ? "CoinGecko" : "", configured.fredApiKey ? "FRED" : ""].filter(Boolean).join(" / ") || "선택 키 없음", "BTC·금리·환율 흐름 보강", configured.coingeckoApiKey || configured.fredApiKey ? "ok" : "neutral"),
      renderAccountApiStatusRow("알림 채널", telegramReady + "/" + accounts.length + " Telegram", "계정별 bot token/chat id 저장 상태", telegramReady ? "ok" : "warn"),
      '</section>'
    ].join("");
  }

  function renderAccountQualityLedger(snapshot) {
    var counts = accountDataQualityCounts(snapshot);
    var total = counts.actual + counts.cached + counts.mock + counts.pending;
    return [
      '<section class="account-quality-ledger">',
      renderAccountControlMetric("실제", counts.actual, total ? "화면 데이터 중 " + total + "개" : "데이터 없음", "ok"),
      renderAccountControlMetric("캐시", counts.cached, "레이트리밋/실패 시 사용", counts.cached ? "warn" : "neutral"),
      renderAccountControlMetric("mock", counts.mock, "실제와 구분 표시", counts.mock ? "warn" : "neutral"),
      renderAccountControlMetric("대기", counts.pending, "시세 또는 원장 미수집", counts.pending ? "warn" : "neutral"),
      '</section>'
    ].join("");
  }

  function renderAccountBalanceAudit(snapshot) {
    var portfolio = (snapshot || {}).portfolio || {};
    return [
      '<section class="account-balance-audit">',
      '<div class="account-board-title">',
      '<strong>계좌 금액 검증</strong>',
      '<span>총 평가 = 투자 평가액 + 현금, 환율과 원장 합계를 같이 봅니다.</span>',
      '</div>',
      '<div class="account-balance-grid">',
      renderAccountControlMetric("투자 평가액", formatMoney(portfolio.invested || 0), "보유 종목 원화환산", "neutral"),
      renderAccountControlMetric("현금/주문 가능", formatMoney(portfolio.cash || 0), portfolioCashBasisText(snapshot || {}, portfolio), "neutral"),
      renderAccountControlMetric("총 평가", formatMoney(portfolio.total || 0), "스냅샷 portfolio.total", "ok"),
      renderAccountControlMetric("산식 차이", exposureDiffText(portfolio.total || 0, numeric(portfolio.invested) + numeric(portfolio.cash)), "total - (invested + cash)", Math.abs(numeric(portfolio.total) - numeric(portfolio.invested) - numeric(portfolio.cash)) < 1 ? "ok" : "warn"),
      '</div>',
      '<div class="source-stack compact">',
      renderPortfolioBasisRows(snapshot || {}, portfolio),
      '</div>',
      '</section>'
    ].join("");
  }

  function renderAccountSectionBar() {
    var visibleSections = modeSectionsForPage("accounts", accountSections);
    var activeId = activeSectionForPageMode("accounts", accountSections, state.activeAccountSection);
    return [
      '<div class="account-section-bar" data-section-mode="' + escapeHtml(activePageMode("accounts")) + '">',
      '<div class="account-section-tabs" role="tablist" aria-label="계정 섹션">',
      visibleSections.map(function (item) {
        var active = activeId === item.id;
        return [
          '<button type="button" role="tab" class="' + (active ? "active" : "") + '" data-account-section="' + escapeHtml(item.id) + '"' + (active ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
          '<strong>' + escapeHtml(item.label) + '</strong>',
          '<span>' + escapeHtml(item.description) + '</span>',
          '</button>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="account-section-actions">',
      '<button class="text-button" data-action="refresh">데이터 새로고침</button>',
      activePageMode("accounts") === "settings"
        ? '<button class="text-button primary" data-action="new-service-account">새 계정</button>'
        : '<button class="text-button" data-account-section="management">계정 관리</button>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderAccountSectionContent(snapshot) {
    var section = activeSectionForPageMode("accounts", accountSections, normalizeAccountSection(state.activeAccountSection));
    if (section === "connections") return renderAccountConnectionsPanel(snapshot);
    if (section === "balance") return renderAccountBalancePanel(snapshot);
    if (section === "management") return renderAdminAccountPanel();
    return renderAccountCommandCenter(snapshot);
  }

  function renderAccountConnectionsPanel(snapshot) {
    snapshot = snapshot || {};
    var accounts = state.serviceAccounts || [];
    return [
      '<article class="panel account-connections-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Account Connections</p>',
      '<h2>API 연결과 데이터 출처</h2>',
      '<p class="subtle">저장된 키의 존재 여부, 화면 데이터 출처, 실제·캐시·mock 비중을 분리해서 봅니다.</p>',
      '</div>',
      '<span class="status-pill ' + escapeHtml(accountSnapshotTone(snapshot)) + '">' + escapeHtml(accountSnapshotModeLabel(snapshot)) + '</span>',
      '</div>',
      '<div class="account-command-layout">',
      '<div>',
      '<div class="account-board-title"><strong>API 출처 상태</strong><span>키 원문 없이 연결 가능성과 사용 출처만 표시합니다.</span></div>',
      renderAccountApiLedger(accounts, snapshot),
      '</div>',
      '<div>',
      '<div class="account-board-title"><strong>데이터 품질</strong><span>레이트리밋이나 실패 시 캐시가 섞였는지 확인합니다.</span></div>',
      renderAccountQualityLedger(snapshot),
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderAccountBalancePanel(snapshot) {
    snapshot = snapshot || {};
    return [
      '<article class="panel account-balance-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Balance Audit</p>',
      '<h2>계좌 금액 기준</h2>',
      '<p class="subtle">화면의 평가액이 어떤 현금 기준, 환율, 보유 원장 합계에서 나왔는지 확인합니다.</p>',
      '</div>',
      '<span class="metric">' + escapeHtml(formatMoney(((snapshot || {}).portfolio || {}).total || 0)) + '</span>',
      '</div>',
      renderAccountBalanceAudit(snapshot),
      '</article>'
    ].join("");
  }

  function renderAccountCommandCenter(snapshot) {
    snapshot = snapshot || {};
    var accounts = state.serviceAccounts || [];
    var enabled = accounts.filter(function (account) { return account.enabled !== false; }).length;
    var tossReady = accounts.filter(function (account) { return account.clientId && account.clientSecret; }).length;
    var freshness = accountFreshness(snapshot);
    var modeLabel = accountSnapshotModeLabel(snapshot);
    return [
      '<article class="panel account-command-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Account Control</p>',
      '<h2>계정 관제 보드</h2>',
      '<p class="subtle">API 키 원문은 숨긴 상태로, 계좌 데이터 출처와 금액 산식만 검증합니다.</p>',
      '</div>',
      '<span class="status-pill ' + escapeHtml(accountSnapshotTone(snapshot)) + '">' + escapeHtml(modeLabel) + '</span>',
      '</div>',
      '<div class="account-command-grid">',
      renderAccountControlMetric("현재 계좌", currentAccountLabel(snapshot), enabled + "/" + accounts.length + " 활성", enabled ? "ok" : "warn"),
      renderAccountControlMetric("Toss 준비", tossReady + "/" + accounts.length, "계정별 API key/secret", tossReady ? "ok" : "warn"),
      renderAccountControlMetric("데이터 신선도", freshness.label, freshness.detail, freshness.tone),
      renderAccountControlMetric("스냅샷", formatClock(snapshot.generatedAt), (snapshot.toss || {}).status || "조회 상태 대기", accountSnapshotTone(snapshot)),
      '</div>',
      '<div class="account-overview-ledger">',
      '<div class="account-board-title"><strong>데이터 품질</strong><span>연결/금액 탭에서 세부 검증을 볼 수 있습니다.</span></div>',
      renderAccountQualityLedger(snapshot),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderAdminAccountPanel() {
    var accounts = state.serviceAccounts || [];
    var draft = state.accountDraft || defaultAccountDraft();
    var locked = state.serverSettingsLocked || isStaticPreviewHost();
    var editingAccount = accounts.filter(function (account) {
      return account.id === state.editingAccountId;
    })[0] || null;
    var active = accounts.filter(function (account) { return account.enabled !== false; }).length;
    var tossReady = accounts.filter(function (account) { return account.clientId && account.clientSecret; }).length;
    var telegramReady = accounts.filter(function (account) { return account.telegramBotToken && account.telegramChatId; }).length;
    return [
      '<article class="panel admin-account-panel account-manager-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Accounts</p>',
      '<h2>DB 저장 계정</h2>',
      '</div>',
      '<div class="settings-actions">',
      '<button class="text-button" data-action="new-service-account">새 계정</button>',
      '<span class="metric">' + escapeHtml(accounts.length) + '</span>',
      '</div>',
      '</div>',
      '<div class="account-manager-summary">',
      renderDirectoryStat("등록", accounts.length + "개"),
      renderDirectoryStat("활성", active + "개"),
      renderDirectoryStat("토스 API", tossReady + "개"),
      renderDirectoryStat("텔레그램", telegramReady + "개"),
      '</div>',
      '<div class="admin-account-layout">',
      '<div class="admin-account-list">',
      '<div class="account-column-head"><strong>저장된 계정</strong><span>API 원문은 표시하지 않습니다.</span></div>',
      state.serviceAccountsLoading ? '<p class="subtle">계정 정보를 읽는 중입니다.</p>' : '',
      state.serviceAccountsError ? '<p class="form-error">' + escapeHtml(state.serviceAccountsError) + '</p>' : '',
      state.accountSaved ? '<p class="lab-message">계정 설정을 저장했습니다.</p>' : '',
      accounts.length ? accounts.map(renderServiceAccountRow).join("") : '<p class="subtle">아직 등록된 서비스 계정이 없습니다.</p>',
      '</div>',
      '<form class="admin-account-form" data-account-form>',
      '<div class="settings-note">',
      '<strong>' + escapeHtml(state.editingAccountId ? "계정 수정" : "새 계정 등록") + '</strong>',
      '<p>저장된 API 값은 아래 상태 칩으로 확인하세요. 수정하지 않을 secret 칸은 비워두면 기존 값을 유지합니다.</p>',
      '</div>',
      editingAccount ? renderAccountCredentialSummary(editingAccount) : renderEmptyAccountCredentialSummary(),
      '<div class="admin-form-grid">',
      renderAccountField("id", "계정 ID", "text", "main", { required: true, disabled: Boolean(state.editingAccountId) }),
      renderAccountField("label", "표시 이름", "text", "메인 계정", { required: true }),
      renderAccountField("provider", "증권사", "text", "toss"),
      renderAccountField("baseUrl", "Toss API Base URL", "url", "https://openapi.tossinvest.com", { wide: true }),
      renderAccountField("clientId", "Toss API Key", state.showSecrets ? "text" : "password", "새 값 입력 시 교체", { configured: Boolean(editingAccount && editingAccount.clientId) }),
      renderAccountField("clientSecret", "Toss Secret Key", state.showSecrets ? "text" : "password", "새 값 입력 시 교체", { configured: Boolean(editingAccount && editingAccount.clientSecret) }),
      renderAccountField("accountSeq", "계좌 순번", "text", "선택", { configured: Boolean(editingAccount && editingAccount.accountSeq) }),
      renderAccountField("watchlistSymbols", "관심 종목", "text", "NVDA,005930", { wide: true }),
      renderAccountField("notifyProvider", "알림 채널", "text", "telegram"),
      renderAccountField("telegramBotToken", "Telegram Bot Token", state.showSecrets ? "text" : "password", "새 값 입력 시 교체", { configured: Boolean(editingAccount && editingAccount.telegramBotToken) }),
      renderAccountField("telegramChatId", "Telegram Chat ID", "text", "chat id", { configured: Boolean(editingAccount && editingAccount.telegramChatId) }),
      renderAccountField("notifyLinkUrl", "알림 링크 URL", "url", "http://127.0.0.1:3000?tab=notifications", { wide: true }),
      '<label class="admin-check-field">',
      '<input data-account-field="quietHoursEnabled" type="checkbox"' + (draft.quietHoursEnabled !== false ? " checked" : "") + ' />',
      '<span>알림 금지 시간 적용</span>',
      '</label>',
      renderAccountField("quietHoursStart", "알림 금지 시작", "time", "22:00"),
      renderAccountField("quietHoursEnd", "알림 금지 종료", "time", "05:00"),
      renderAccountField("quietHoursTimezone", "알림 금지 타임존", "text", "Asia/Seoul"),
      renderAccountSelectField("messageDeliveryLevel", "메시지 전달 수준", messageDeliveryLevelOptions(), { wide: true }),
      '<label class="admin-check-field">',
      '<input data-account-field="enabled" type="checkbox"' + (draft.enabled !== false ? " checked" : "") + ' />',
      '<span>이 계정을 모니터링에 사용</span>',
      '</label>',
      '</div>',
      '<div class="settings-actions">',
      '<button class="text-button primary" type="submit"' + (locked ? ' disabled' : '') + '>계정 저장</button>',
      '<button class="text-button" type="button" data-action="toggle-secrets">' + (state.showSecrets ? "secret 숨기기" : "secret 보기") + '</button>',
      locked ? '<span class="subtle">로컬 서버에서만 저장할 수 있습니다.</span>' : '',
      '</div>',
      '</form>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderAccountField(name, label, type, placeholder, options) {
    options = options || {};
    var draft = state.accountDraft || defaultAccountDraft();
    var value = draft[name] == null ? "" : draft[name];
    var fieldPlaceholder = options.configured && !value ? "저장됨 - 새 값 입력 시 교체" : (placeholder || "");
    return [
      '<label class="setting-field' + (options.wide ? " wide" : "") + '">',
      '<span>' + escapeHtml(label) + '</span>',
      '<input data-account-field="' + escapeHtml(name) + '" name="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(value) + '" placeholder="' + escapeHtml(fieldPlaceholder) + '" autocomplete="off"' + (options.required ? " required" : "") + (options.disabled ? " disabled" : "") + ' />',
      options.configured ? '<em class="setting-field-note">저장됨</em>' : '',
      '</label>'
    ].join("");
  }

  function renderAccountSelectField(name, label, optionsList, options) {
    options = options || {};
    var draft = state.accountDraft || defaultAccountDraft();
    var value = normalizeMessageDeliveryLevel(draft[name]);
    return [
      '<label class="setting-field' + (options.wide ? " wide" : "") + '">',
      '<span>' + escapeHtml(label) + '</span>',
      '<select data-account-field="' + escapeHtml(name) + '" name="' + escapeHtml(name) + '">',
      (optionsList || []).map(function (item) {
        var selected = normalizeMessageDeliveryLevel(item.value) === value ? " selected" : "";
        return '<option value="' + escapeHtml(item.value) + '"' + selected + '>' + escapeHtml(item.label + " · " + item.description) + '</option>';
      }).join(""),
      '</select>',
      '<em class="setting-field-note">알림 판단은 그대로 두고 설명 방식만 바꿉니다.</em>',
      '</label>'
    ].join("");
  }

  function renderServiceAccountRow(account) {
    var watchlist = watchSymbolListText(accountWatchlistSymbols(account));
    return [
      '<div class="service-account-row">',
      '<div class="service-account-main">',
      '<div class="service-account-title">',
      '<strong>' + escapeHtml(account.label || account.id || "-") + '</strong>',
      accountRowStatusChip(account),
      '</div>',
      '<span class="service-account-line">' + escapeHtml(account.id || "-") + ' · ' + escapeHtml(account.provider || "toss") + ' · ' + escapeHtml(account.enabled === false ? "중지" : "사용") + '</span>',
      '<span class="service-account-line">관심 ' + escapeHtml(watchlist || "-") + '</span>',
      renderAccountExposureGrid(account),
      '</div>',
      '<div class="service-account-meta">',
      '<div class="row-actions">',
      '<button class="mini-button" data-account-edit="' + escapeHtml(account.id || "") + '">수정</button>',
      '<button class="mini-button danger" data-account-remove="' + escapeHtml(account.id || "") + '">삭제</button>',
      '</div>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderAccountExposureGrid(account) {
    account = account || {};
    var symbols = accountWatchlistSymbols(account);
    return [
      '<div class="account-exposure-grid" aria-label="계정 노출 상태">',
      renderAccountExposureItem("토스 API", account.clientId && account.clientSecret ? "연결" : "확인", account.clientId && account.clientSecret ? "ok" : "warn"),
      renderAccountExposureItem("계좌 seq", account.accountSeq ? String(account.accountSeq) : "선택 안함", account.accountSeq ? "ok" : "warn"),
      renderAccountExposureItem("텔레그램", account.telegramBotToken && account.telegramChatId ? "연결" : "미설정", account.telegramBotToken && account.telegramChatId ? "ok" : "warn"),
      renderAccountExposureItem("관심종목", symbols.length + "개", symbols.length ? "ok" : "neutral"),
      renderAccountExposureItem("전달수준", messageDeliveryLevelLabel(account.messageDeliveryLevel), "neutral"),
      '</div>'
    ].join("");
  }

  function renderAccountExposureItem(label, value, tone) {
    return [
      '<span class="account-exposure-item ' + escapeHtml(tone || "neutral") + '">',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value) + '</strong>',
      '</span>'
    ].join("");
  }

  function renderNotificationsPage() {
    var section = activeSectionForPageMode("notifications", notificationSections, normalizeNotificationSection(state.activeNotificationSection));
    var content = renderNotificationSectionContent();
    return renderManagedPage("notifications", state.snapshot || {}, [
      '<section class="admin-grid notifications-view">',
      renderNotificationCommandCenter(section),
      content,
      '</section>',
      section === "signals" ? renderMonitoringDetailOverlay(state.snapshot || {}) : ''
    ].join(""));
  }

  function notificationEnabledRuleCount() {
    var rules = alertRules();
    return notificationPolicyCatalog().filter(function (rule) {
      return enabledAlertRule(rules, rule.key);
    }).length;
  }

  function notificationTemplateItems() {
    var templates = state.notificationTemplates.length ? state.notificationTemplates : defaultNotificationTemplates();
    return templates.filter(function (item) {
      return visibleNotificationTemplateType(item.messageType);
    });
  }

  function renderNotificationSectionBar() {
    var visibleSections = modeSectionsForPage("notifications", notificationSections);
    var activeId = activeSectionForPageMode("notifications", notificationSections, state.activeNotificationSection);
    return [
      '<div class="notification-section-bar" data-section-mode="' + escapeHtml(activePageMode("notifications")) + '">',
      '<div class="notification-section-tabs" role="tablist" aria-label="알림 설정 섹션">',
      visibleSections.map(function (item) {
        var active = activeId === item.id;
        return [
          '<button type="button" role="tab" class="' + (active ? "active" : "") + '" data-notification-section="' + escapeHtml(item.id) + '"' + (active ? ' aria-selected="true"' : ' aria-selected="false"') + '>',
          '<strong>' + escapeHtml(item.label) + '</strong>',
          '<span>' + escapeHtml(item.description) + '</span>',
          '</button>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="notification-section-actions">',
      '<button class="text-button" data-action="refresh-notification-jobs"' + (state.notificationJobsLoading ? ' disabled' : '') + '>판단 새로고침</button>',
      activePageMode("notifications") === "settings"
        ? '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>'
        : '<button class="text-button" data-page-mode-page="notifications" data-page-mode="settings">알림 설정</button>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderNotificationCommandCenter(sectionId) {
    var section = notificationSections.filter(function (item) {
      return item.id === normalizeNotificationSection(sectionId);
    })[0] || activeNotificationSectionMeta();
    var summary = state.notificationJobsSummary || state.realtime.notificationJobs || {};
    var failedCount = Number(summary.failed || 0);
    var pendingCount = Number(summary.pending || 0);
    var statusTone = failedCount ? "danger" : (pendingCount ? "watch" : "muted");
    var statusText = failedCount ? failedCount + "건 실패 확인" : (pendingCount ? pendingCount + "건 대기" : "대기 없음");
    return [
      '<section class="notification-command-center" aria-label="알림 운영 요약">',
      '<div class="notification-command-head">',
      '<div>',
      '<p class="label">Notifications</p>',
      '<h2>알림 운영</h2>',
      '<span>' + escapeHtml(section.label + " · " + section.description) + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(statusTone) + '">' + escapeHtml(statusText) + '</span>',
      '</div>',
      renderNotificationSectionBar(),
      renderNotificationOpsRail(),
      '</section>'
    ].join("");
  }

  function renderNotificationOpsRail() {
    var summary = state.notificationJobsSummary || state.realtime.notificationJobs || {};
    var templateCount = notificationTemplateItems().length;
    var scheduleCount = Array.isArray(state.messageSchedules) ? state.messageSchedules.length : 0;
    var items = [
      ["대기", Number(summary.pending || 0), "watch"],
      ["발송", Number(summary.done || 0), "watch"],
      ["보류", Number(summary.suppressed || 0), "muted"],
      ["실패", Number(summary.failed || 0), Number(summary.failed || 0) ? "danger" : "muted"],
      ["관리 룰", notificationEnabledRuleCount() + "/" + notificationPolicyCatalog().length, "policy"],
      ["템플릿", templateCount + "개", "muted"],
      ["스케줄", scheduleCount || "-", "muted"]
    ];
    return [
      '<section class="notification-ops-rail" aria-label="알림 상태 요약">',
      items.map(renderNotificationOpsCell).join(""),
      '</section>'
    ].join("");
  }

  function renderNotificationOpsCell(item) {
    return [
      '<span class="notification-ops-cell ' + escapeHtml(item[2] || "muted") + '">',
      '<em>' + escapeHtml(item[0]) + '</em>',
      '<strong>' + escapeHtml(String(item[1])) + '</strong>',
      '</span>'
    ].join("");
  }

  function renderNotificationSectionContent() {
    var section = activeSectionForPageMode("notifications", notificationSections, normalizeNotificationSection(state.activeNotificationSection));
    if (section === "signals") return renderNotificationSignalPanel(state.snapshot || {});
    if (section === "policy") return renderAdminMessagePanel();
    if (section === "templates") return renderNotificationTemplateManagerPanel();
    if (section === "advanced") {
      return [
        renderAdminDeliveryPanel(),
        renderNotificationAdvancedRulePanel(),
        renderNotificationThresholdPanel()
      ].join("");
    }
    return renderNotificationDecisionPanel();
  }

  function renderNotificationSignalPanel(snapshot) {
    return [
      renderAdminMonitoringPanel(snapshot),
      renderAlertCenterPanel(snapshot),
      renderMonitoringInstrumentPanel(snapshot),
      renderPortfolioPanel(snapshot)
    ].join("");
  }

  function renderAdminMessagePanel() {
    var rules = alertRules();
    var cadences = alertCadenceMinutes();
    var groups = alertRuleGroups();
    var editorOpen = Boolean(state.notificationPolicyEditorOpen);
    return [
      '<article class="panel admin-message-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Messages</p>',
      '<h2>메시지 타입별 알림</h2>',
      '<p class="subtle">메시지 타입을 그룹별로 확인하고 상세 편집은 레이어로 띄워 수정합니다.</p>',
      '</div>',
      '<div class="settings-actions">',
      '<button class="text-button compact" data-action="expand-message-types">그룹 펼치기</button>',
      '<button class="text-button compact" data-action="collapse-message-types">전체 접기</button>',
      '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
      '</div>',
      '</div>',
      '<div class="settings-body">',
      state.messageSchedulesError ? '<p class="form-error">' + escapeHtml(state.messageSchedulesError) + '</p>' : '',
      state.notificationRulesError ? '<p class="form-error">' + escapeHtml(state.notificationRulesError) + '</p>' : '',
      state.notificationRulesSaved ? '<p class="lab-message">알림 룰을 저장했습니다.</p>' : '',
      renderNotificationPolicyListScreen(groups, rules, cadences),
      '</div>',
      editorOpen ? renderNotificationPolicyEditorLayer() : '',
      '</article>'
    ].join("");
  }

  function renderNotificationPolicyListScreen(groups, rules, cadences) {
    return [
      '<div class="notification-policy-list-screen">',
      '<div class="flow-title"><div><strong>사용 중인 알림 타입</strong><span>투자 근거 신호와 레거시 타입은 인사이트 합성 입력으로만 유지하고, 여기서는 직접 관리하는 타입만 편집합니다.</span></div></div>',
      '<div class="admin-message-group-list">',
      groups.map(function (group) {
        return renderAdminMessageGroup(group, rules, cadences);
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }

  function alertRuleGroups() {
    var order = [];
    var byGroup = {};
    notificationPolicyCatalog().forEach(function (rule) {
      var group = rule.group || "기타";
      if (!byGroup[group]) {
        byGroup[group] = [];
        order.push(group);
      }
      byGroup[group].push(rule);
    });
    return order.map(function (group) {
      return { name: group, rules: byGroup[group] };
    });
  }

  function notificationGroupExpanded(group) {
    return Boolean(state.notificationExpandedGroups && state.notificationExpandedGroups[group]);
  }

  function renderAdminMessageGroup(group, rules, cadences) {
    var expanded = notificationGroupExpanded(group.name);
    var enabledCount = group.rules.filter(function (rule) {
      return enabledAlertRule(rules, rule.key);
    }).length;
    var selectedInGroup = group.rules.some(function (rule) {
      return rule.key === activeNotificationRule().key;
    });
    return [
      '<section class="admin-message-group">',
      '<button class="admin-message-group-head" type="button" data-message-group-toggle="' + escapeHtml(group.name) + '" aria-expanded="' + escapeHtml(expanded ? "true" : "false") + '">',
      '<span><strong>' + escapeHtml(group.name) + '</strong><em>' + escapeHtml(enabledCount + "/" + group.rules.length + "개 사용" + (selectedInGroup ? " · 선택됨" : "")) + '</em></span>',
      '<b>' + escapeHtml(expanded ? "접기" : "보기") + '</b>',
      '</button>',
      expanded ? '<div class="admin-message-list">' + group.rules.map(function (rule) {
        return renderAdminMessageRow(
          rule,
          enabledAlertRule(rules, rule.key),
          cadences[rule.key],
          messageScheduleByType(rule.key),
          notificationTemplateForEdit(rule.key)
        );
      }).join("") + '</div>' : '',
      '</section>'
    ].join("");
  }

  function notificationTypeExpanded(messageType) {
    return Boolean(state.notificationExpandedTypes && state.notificationExpandedTypes[messageType]);
  }

  function renderAdminMessageRow(rule, checked, cadence, schedule, template) {
    var ruleId = "alert-rule-" + String(rule.key || "").replace(/[^A-Za-z0-9_-]/g, "-");
    var active = activeNotificationRule().key === rule.key;
    var editing = active && state.notificationPolicyEditorOpen;
    return [
      '<div class="admin-message-row ' + (active ? "active" : "collapsed") + '">',
      '<input id="' + escapeHtml(ruleId) + '" type="checkbox" data-alert-rule="' + escapeHtml(rule.key) + '"' + (checked ? " checked" : "") + ' />',
      '<label class="admin-message-main" for="' + escapeHtml(ruleId) + '">',
      '<strong>' + escapeHtml(labelWithNotificationIcon(rule.key, rule.label)) + '</strong>',
      '<em>' + escapeHtml(rule.group + " · " + rule.description) + '</em>',
      '</label>',
      '<span class="admin-cadence-field">',
      '<input data-alert-cadence="' + escapeHtml(rule.key) + '" type="number" min="10" step="10" value="' + escapeHtml(cadence) + '" />',
      '<b>분</b>',
      '</span>',
      '<button class="admin-message-toggle" type="button" data-message-select="' + escapeHtml(rule.key) + '" aria-pressed="' + escapeHtml(editing ? "true" : "false") + '">',
      '<span>' + escapeHtml(editing ? "편집 중" : "상세 편집") + '</span>',
      '</button>',
      '<div class="admin-message-schedule">',
      renderMessageScheduleSummary(schedule, true),
      '</div>',
      '</div>'
    ].join("");
  }

  function notificationRuleByKey(key) {
    return notificationPolicyCatalog().filter(function (rule) {
      return rule.key === key;
    })[0] || null;
  }

  function activeNotificationRule() {
    var selected = notificationRuleByKey(state.activeNotificationMessageType);
    return selected || notificationRuleByKey("investmentInsight") || notificationPolicyCatalog()[0];
  }

  function renderNotificationPolicyDetailPanel() {
    var rule = activeNotificationRule();
    var template = notificationTemplateForEdit(rule.key);
    var schedule = messageScheduleByType(rule.key);
    return [
      '<aside class="notification-policy-detail" aria-label="선택한 알림 상세">',
      '<div class="notification-policy-detail-head">',
      '<div>',
      '<p class="label">Selected Policy</p>',
      '<h3>' + escapeHtml(labelWithNotificationIcon(rule.key, rule.label)) + '</h3>',
      '<span>' + escapeHtml(rule.group + " · " + rule.description) + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(scheduleStatusClass(schedule)) + '">' + escapeHtml(scheduleStatusLabel(schedule)) + '</span>',
      '</div>',
      '<div class="notification-policy-schedule">',
      renderMessageScheduleSummary(schedule),
      '</div>',
      '<div class="notification-policy-editor">',
      renderNotificationTemplateRow(template, { policyDetail: true }),
      renderNotificationRuleEditor(rule.key, { inline: true }),
      '</div>',
      '</aside>'
    ].join("");
  }

  function renderNotificationPolicyEditorLayer() {
    return [
      '<div class="notification-policy-modal-backdrop" data-notification-editor-close></div>',
      '<section class="notification-policy-editor-layer" role="dialog" aria-modal="true" aria-label="알림 상세 편집">',
      '<div class="notification-policy-modal-head">',
      '<div>',
      '<p class="label">Edit Policy</p>',
      '<h2>알림 상세 편집</h2>',
      '<span>템플릿, 발송 기준, 반복 억제 조건을 수정합니다.</span>',
      '</div>',
      '<button class="icon-button" type="button" data-notification-editor-close aria-label="상세 편집 닫기">&times;</button>',
      '</div>',
      renderNotificationPolicyDetailPanel(),
      '</section>'
    ].join("");
  }

  function notificationJobStatusLabel(status) {
    var labels = {
      pending: "대기",
      processing: "처리 중",
      done: "발송",
      failed: "실패",
      suppressed: "보류"
    };
    return labels[status] || status || "-";
  }

  function notificationJobToneClass(status) {
    if (status === "done" || status === "pending" || status === "processing") return "watch";
    if (status === "suppressed") return "muted";
    if (status === "failed") return "danger";
    return "muted";
  }

  function notificationJobScoreText(job) {
    if (job.honeyScore === null || typeof job.honeyScore === "undefined") return "-";
    return String(job.honeyScore) + "/" + String(job.honeyThreshold || 0);
  }

  function notificationJobSimilarityText(job) {
    var count = Number(job.honeySimilarityRecentCount || 0);
    var penalty = Number(job.honeySimilarityPenalty || 0);
    var windowMinutes = Number(job.honeySimilarityWindowMinutes || 0);
    if (!count && !penalty) return "유사 감점 없음";
    return windowMinutes + "분 내 " + count + "회 · 우선도 " + penalty;
  }

  function notificationJobMarketHoursText(job) {
    if (!job.marketHoursEnabled) return "";
    if (job.marketHoursReason) return job.marketHoursReason;
    if (job.marketHoursStatus === "open") return "장 시간 열림";
    if (job.marketHoursStatus === "closed") return "장 시간 외";
    return "";
  }

  function notificationJobQuietHoursText(job) {
    if (!job.quietHoursSuppressed) return "";
    return job.quietHoursReason || "계정 알림 금지 시간";
  }

  function notificationJobStateCooldownText(job) {
    if (!job.honeyStateCooldownEnabled && !job.honeyStateReason) return "";
    if (job.honeyStateReason) return job.honeyStateReason;
    if (job.honeyStateDecision === "new_threshold") return "신규 임계값 상태";
    if (job.honeyStateDecision === "material_change") return "의미 있는 추가 확대";
    if (job.honeyStateDecision === "sustained_summary") return "지속 상태 요약";
    if (job.honeyStateDecision === "cooldown") return "같은 임계값 상태 지속";
    return "";
  }

  function renderNotificationDecisionPanel() {
    var jobs = state.notificationJobItems || [];
    var summary = state.notificationJobsSummary || state.realtime.notificationJobs || {};
    var diagnostics = state.notificationJobDiagnostics || {};
    var hasError = Boolean(state.notificationJobsError);
    var summaryItems = ["pending", "done", "suppressed", "failed"].map(function (key) {
      return '<span class="chip">' + escapeHtml(notificationJobStatusLabel(key)) + ' ' + escapeHtml(Number(summary[key] || 0)) + '</span>';
    }).join("");
    var activeJob = activeNotificationDecisionJob(jobs);
    var stateMessage = hasError
      ? renderNotificationStateMessage("hold", "최근 판단 API 연결 확인", state.notificationJobsError)
      : renderEmptyState({
        tone: "muted",
        label: "Decisions",
        title: "아직 판단 이력이 없습니다",
        description: "알림 워커가 발송, 보류, 실패 판단을 남기면 이곳에 시간순으로 표시합니다.",
        meta: ["Outbox 기준", "최근 40건"]
      });
    return [
      '<article class="panel notification-decision-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Decisions</p>',
      '<h2>최근 알림 판단</h2>',
      '</div>',
      '<div class="notification-decision-summary">',
      summaryItems,
      '</div>',
      '</div>',
      renderNotificationDecisionDiagnostics(diagnostics),
      '<div class="notification-decision-body' + (jobs.length ? " has-detail" : "") + '">',
      '<div class="notification-decision-master">',
      '<div class="notification-decision-status">',
      '<span class="tone-chip ' + escapeHtml(hasError ? "hold" : "watch") + '">' + escapeHtml(hasError ? "확인 필요" : "현황") + '</span>',
      '<span>' + escapeHtml(jobs.length ? "최근 " + jobs.length + "건 · 선택 리포트 분리" : (hasError ? "연결 상태 확인" : "판단 이력 없음")) + '</span>',
      '<em>' + escapeHtml(state.notificationJobsLoading ? "백그라운드 갱신 중" : "마지막 결과 유지") + '</em>',
      '</div>',
      jobs.length ? '<div class="notification-decision-list" role="listbox" aria-label="최근 알림 판단 목록">' + jobs.map(function (job) {
        return renderNotificationDecisionRow(job, notificationJobKey(job) === notificationJobKey(activeJob));
      }).join("") + '</div>' : stateMessage,
      '</div>',
      jobs.length ? renderNotificationDecisionDetail(activeJob) : '',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderNotificationDecisionDiagnostics(diagnostics) {
    diagnostics = diagnostics || {};
    var reasons = Array.isArray(diagnostics.suppressionReasons) ? diagnostics.suppressionReasons : [];
    var chips = [];
    var staleCount = Number(diagnostics.staleProcessingCount || 0);
    if (staleCount) {
      chips.push("처리 재시도 가능 " + staleCount + "건");
    }
    reasons.slice(0, 3).forEach(function (item) {
      chips.push(String(item.reason || "보류") + " " + Number(item.count || 0) + "건");
    });
    if (!chips.length) return "";
    return '<div class="notification-decision-diagnostics">' + chips.map(function (chip) {
      return '<span>' + escapeHtml(chip) + '</span>';
    }).join("") + '</div>';
  }

  function renderNotificationStateMessage(tone, title, description) {
    return [
      '<div class="notification-state-message ' + escapeHtml(tone || "muted") + '">',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<span>' + escapeHtml(description || "") + '</span>',
      '</div>'
    ].join("");
  }

  function activeNotificationDecisionJob(jobs) {
    jobs = Array.isArray(jobs) ? jobs : [];
    if (!jobs.length) return null;
    var selectedKey = state.activeNotificationJobKey || "";
    var selected = jobs.filter(function (job) {
      return notificationJobKey(job) === selectedKey;
    })[0];
    return selected || jobs[0];
  }

  function renderNotificationDetailMetric(label, value, tone) {
    return [
      '<span class="notification-detail-metric ' + escapeHtml(tone || "") + '">',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value || "-") + '</strong>',
      '</span>'
    ].join("");
  }

  function notificationJobDetailPayload(job) {
    var resolvedSymbol = notificationJobResolvedSymbol(job);
    var displaySymbol = resolvedSymbol ? stockDisplayName(resolvedSymbol, job) : "";
    var title = textWithKnownDisplaySymbols(job.title || "", resolvedSymbol, job);
    var preview = textWithKnownDisplaySymbols(job.lastError || job.textPreview || "-", resolvedSymbol, job);
    var fullText = notificationJobFullText(job, resolvedSymbol);
    var reasons = Array.isArray(job.honeyReasons) ? job.honeyReasons.slice(0, 6) : [];
    return {
      resolvedSymbol: resolvedSymbol,
      displaySymbol: displaySymbol,
      title: title,
      preview: preview,
      fullText: fullText,
      reasons: reasons
    };
  }

  function renderNotificationDecisionDetail(job) {
    if (!job) {
      return renderEmptyState({
        tone: "muted",
        label: "Report",
        title: "선택된 알림이 없습니다",
        description: "왼쪽 목록에서 알림 판단을 선택하면 상세 리포트를 표시합니다."
      });
    }
    var payload = notificationJobDetailPayload(job);
    var gateRows = [
      notificationJobSimilarityText(job),
      notificationJobStateCooldownText(job),
      notificationJobMarketHoursText(job),
      notificationJobQuietHoursText(job),
      job.suppressionSummary || "",
      job.nextEligibleAt ? "다음 발송 가능 " + formatClock(job.nextEligibleAt) : ""
    ].filter(Boolean);
    var fingerprint = textWithKnownDisplaySymbols(job.honeyFingerprint || "", payload.resolvedSymbol, job);
    return [
      '<aside class="notification-decision-detail" aria-label="선택 알림 판단 상세">',
      '<div class="notification-detail-head">',
      '<div>',
      '<p class="label">Decision Report</p>',
      '<h3>' + escapeHtml(payload.title || payload.displaySymbol || job.messageTypeLabel || job.messageType || "알림 판단") + '</h3>',
      '<span>' + escapeHtml([payload.displaySymbol, labelWithNotificationIcon(job.messageType, job.messageTypeLabel || job.messageType), formatClock(job.createdAt)].filter(Boolean).join(" · ")) + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(notificationJobToneClass(job.status)) + '">' + escapeHtml(notificationJobStatusLabel(job.status)) + '</span>',
      '</div>',
      '<div class="notification-detail-metrics">',
      renderNotificationDetailMetric("우선도", notificationJobScoreText(job), "score"),
      renderNotificationDetailMetric("상태", notificationJobStatusLabel(job.status), notificationJobToneClass(job.status)),
      renderNotificationDetailMetric("반복 판단", notificationJobSimilarityText(job), "muted"),
      renderNotificationDetailMetric("발송 가능", job.nextEligibleAt ? formatClock(job.nextEligibleAt) : "조건 충족 시", "muted"),
      '</div>',
      '<section class="notification-detail-section primary">',
      '<strong>판단 요약</strong>',
      '<p>' + escapeHtml(payload.preview) + '</p>',
      '</section>',
      gateRows.length ? '<section class="notification-detail-section"><strong>게이트와 보류 조건</strong><div class="notification-detail-tags">' + gateRows.map(function (row) {
        return '<span>' + escapeHtml(textWithKnownDisplaySymbols(row, payload.resolvedSymbol, job)) + '</span>';
      }).join("") + '</div></section>' : '',
      payload.reasons.length ? '<section class="notification-detail-section"><strong>판단 근거</strong><div class="notification-detail-reasons">' + payload.reasons.map(function (reason) {
        return '<p>' + escapeHtml(textWithKnownDisplaySymbols(reason, payload.resolvedSymbol, job)) + '</p>';
      }).join("") + '</div></section>' : '',
      payload.fullText && payload.fullText !== payload.preview ? '<section class="notification-detail-section"><strong>전체 메시지</strong><pre class="notification-full-message">' + escapeHtml(payload.fullText) + '</pre></section>' : '',
      fingerprint ? '<section class="notification-detail-section"><strong>중복 판단 키</strong><code class="notification-fingerprint">' + escapeHtml(fingerprint) + '</code></section>' : '',
      '</aside>'
    ].join("");
  }

  function notificationJobKey(job) {
    return String((job && job.jobId) || [job && job.createdAt, job && job.messageType, job && job.title].join(":"));
  }

  function notificationJobFullText(job, resolvedSymbol) {
    return textWithKnownDisplaySymbols(String((job && (job.fullText || job.text)) || ""), resolvedSymbol, job);
  }

  function notificationJobExpanded(job) {
    return Boolean((state.notificationExpandedJobs || {})[notificationJobKey(job)]);
  }

  function renderNotificationDecisionRow(job, selected) {
    var reasons = Array.isArray(job.honeyReasons) ? job.honeyReasons.slice(0, 5) : [];
    var resolvedSymbol = notificationJobResolvedSymbol(job);
    var displaySymbol = resolvedSymbol ? stockDisplayName(resolvedSymbol, job) : "";
    var title = textWithKnownDisplaySymbols(job.title || "", resolvedSymbol, job);
    var target = title || displaySymbol || job.messageType || "-";
    if (displaySymbol && resolvedSymbol && target.toUpperCase() === String(resolvedSymbol || "").toUpperCase()) {
      target = displaySymbol;
    }
    if (displaySymbol && title && title.indexOf(displaySymbol) < 0) {
      target = title + " / " + displaySymbol;
    }
    var preview = textWithKnownDisplaySymbols(job.lastError || job.textPreview || "-", resolvedSymbol, job);
    var fullText = notificationJobFullText(job, resolvedSymbol);
    var expanded = notificationJobExpanded(job);
    var canExpand = Boolean(fullText && fullText !== preview);
    var fullMessageToggle = canExpand ? [
      '<div class="notification-decision-actions">',
      '<button type="button" class="mini-button" data-notification-full-toggle="' + escapeHtml(notificationJobKey(job)) + '" aria-expanded="' + escapeHtml(expanded ? "true" : "false") + '">',
      escapeHtml(expanded ? "접기" : "전체 메시지"),
      '</button>',
      '</div>'
    ].join("") : "";
    var fullMessageBlock = canExpand && expanded ? '<pre class="notification-full-message">' + escapeHtml(fullText) + '</pre>' : "";
    var fingerprint = textWithKnownDisplaySymbols(job.honeyFingerprint || "", resolvedSymbol, job);
    var suppression = textWithKnownDisplaySymbols(job.suppressionSummary || "", resolvedSymbol, job);
    var nextEligible = job.nextEligibleAt ? "다음 가능 " + formatClock(job.nextEligibleAt) : "";
    var processing = job.recoverableProcessing ? "처리 중 지연 " + String(job.processingAgeMinutes || 0) + "분 · 워커 재시도 가능" : "";
    var rowKey = notificationJobKey(job);
    return [
      '<div class="notification-decision-row ' + (selected ? "active " : "") + escapeHtml(notificationJobToneClass(job.status)) + '" role="option" tabindex="0" data-notification-job-select="' + escapeHtml(rowKey) + '" aria-selected="' + escapeHtml(selected ? "true" : "false") + '">',
      '<div class="notification-decision-top">',
      '<span class="tone-chip ' + escapeHtml(notificationJobToneClass(job.status)) + '">' + escapeHtml(notificationJobStatusLabel(job.status)) + '</span>',
      '<strong>' + escapeHtml(labelWithNotificationIcon(job.messageType, job.messageTypeLabel || job.messageType || "-")) + '</strong>',
      '<span>' + escapeHtml(formatClock(job.createdAt)) + '</span>',
      '</div>',
      '<div class="notification-decision-target">' + escapeHtml(target || job.messageType || "-") + '</div>',
      '<div class="notification-decision-score">',
      '<span>발송 우선도 ' + escapeHtml(notificationJobScoreText(job)) + '</span>',
      '<span>' + escapeHtml(notificationJobSimilarityText(job)) + '</span>',
      notificationJobStateCooldownText(job) ? '<span>' + escapeHtml(notificationJobStateCooldownText(job)) + '</span>' : '',
      notificationJobMarketHoursText(job) ? '<span>' + escapeHtml(notificationJobMarketHoursText(job)) + '</span>' : '',
      notificationJobQuietHoursText(job) ? '<span>' + escapeHtml(notificationJobQuietHoursText(job)) + '</span>' : '',
      suppression ? '<span>' + escapeHtml(suppression) + '</span>' : '',
      nextEligible ? '<span>' + escapeHtml(nextEligible) + '</span>' : '',
      processing ? '<span>' + escapeHtml(processing) + '</span>' : '',
      job.honeySimilarityBypassed ? '<span>' + escapeHtml(job.honeySimilarityBypassReason ? "반복 예외 " + job.honeySimilarityBypassReason : "반복 예외 적용") + '</span>' : '',
      '</div>',
      '<p>' + escapeHtml(preview) + '</p>',
      fullMessageToggle,
      fullMessageBlock,
      reasons.length ? '<div class="notification-decision-reasons">' + reasons.map(function (reason) {
        return '<span>' + escapeHtml(textWithKnownDisplaySymbols(reason, resolvedSymbol, job)) + '</span>';
      }).join("") + '</div>' : '',
      fingerprint ? '<code class="notification-fingerprint">' + escapeHtml(fingerprint) + '</code>' : '',
      '</div>'
    ].join("");
  }

  function notificationJobResolvedSymbol(job) {
    var explicit = String(job && (job.symbol || job.rawSymbol) || "").trim().toUpperCase();
    if (explicit) return explicit;
    var reasons = Array.isArray(job && job.honeyReasons) ? job.honeyReasons.join(" ") : "";
    return inferKnownStockSymbolFromText([
      job && job.title,
      job && job.textPreview,
      job && job.lastError,
      job && job.honeyFingerprint,
      reasons
    ].join(" "));
  }

  function notificationTemplateLabel(messageType) {
    var found = alertRuleCatalog.filter(function (rule) { return rule.key === messageType; })[0];
    if (found) return found.label;
    var labels = {
      default: "기본 템플릿",
      modelReview: "모델 리뷰",
      workHandoff: "작업 완료",
      notification: "일반 알림"
    };
    return labels[messageType] || messageType;
  }

  function scheduleStatusLabel(schedule) {
    if (!schedule) return "이력 없음";
    if (schedule.status === "event") return "이벤트 발생 시";
    if (schedule.status === "disabled") return "꺼짐";
    if (schedule.status === "waiting") return "대기 중";
    if (schedule.status === "ready") return "발송 가능";
    return schedule.status || "확인 필요";
  }

  function scheduleStatusClass(schedule) {
    if (!schedule) return "muted";
    if (schedule.status === "event") return "watch";
    if (schedule.status === "disabled") return "muted";
    if (schedule.status === "waiting") return "hold";
    if (schedule.status === "ready") return "watch";
    return "muted";
  }

  function scheduleTimeText(value) {
    return value ? formatClock(value) : "-";
  }

  function scheduleTargetText(targets) {
    if (!Array.isArray(targets) || !targets.length) return "최근 실제 발송 대상 없음";
    return targets.slice(0, 3).map(function (item) {
      var target = item.target ? textWithDisplaySymbol(item.target, item.target, item) : "전체";
      return target + " · " + scheduleTimeText(item.sentAt);
    }).join(" / ");
  }

  function renderMessageScheduleSummary(schedule, compact) {
    if (!schedule) {
      return [
        '<div class="message-schedule-summary muted">',
        '<span>로컬 서버에서 실제 발송 이력을 읽으면 표시됩니다.</span>',
        '</div>'
      ].join("");
    }
    return [
      '<div class="message-schedule-summary">',
      '<span class="tone-chip ' + escapeHtml(scheduleStatusClass(schedule)) + '">' + escapeHtml(scheduleStatusLabel(schedule)) + '</span>',
      '<span>' + escapeHtml(schedule.cadenceText || "조건 충족 시 발송") + '</span>',
      '<span>마지막 ' + escapeHtml(scheduleTimeText(schedule.lastSentAt)) + '</span>',
      '<span>다음 가능 ' + escapeHtml(scheduleTimeText(schedule.nextEligibleAt)) + '</span>',
      '</div>',
      compact ? '' :
      '<div class="message-schedule-detail">',
      '<strong>언제 오나</strong>',
      '<p>' + escapeHtml(schedule.triggerSummary || "조건이 실제 데이터에서 충족될 때 보냅니다.") + '</p>',
      '<em>' + escapeHtml(scheduleTargetText(schedule.recentTargets)) + '</em>',
      '</div>'
    ].join("");
  }

  function notificationRuleConditionTypeLabel(type) {
    var found = (state.notificationRuleConditionTypes || []).filter(function (item) {
      return item.type === type;
    })[0];
    return found ? found.label : type;
  }

  function notificationRuleConditionValue(condition) {
    if (condition.type === "text_contains_any" || condition.type === "context_contains_any") {
      return Array.isArray(condition.terms) ? condition.terms.join(", ") : "";
    }
    return String(condition.value || "");
  }

  function renderNotificationRuleCondition(messageType, condition, disabled) {
    var conditionId = String(condition.id || "");
    var fieldNeeded = /^context_/.test(condition.type || "");
    var valueNeeded = ["context_equals", "context_number_gte", "context_number_lte"].indexOf(condition.type) >= 0;
    var termsNeeded = condition.type === "text_contains_any" || condition.type === "context_contains_any";
    return [
      '<div class="notification-rule-condition">',
      '<label class="notification-rule-condition-main">',
      '<input type="checkbox" data-notification-rule-condition-enabled="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '"' + (condition.enabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' />',
      '<span><strong>' + escapeHtml(condition.label || conditionId) + '</strong><em>' + escapeHtml(notificationRuleConditionTypeLabel(condition.type)) + '</em></span>',
      '</label>',
      '<label><span>점수</span><input type="number" min="-100" max="100" step="1" data-notification-rule-condition-score="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(condition.score) + '"' + (disabled ? " disabled" : "") + ' /></label>',
      fieldNeeded ? '<label><span>필드</span><input type="text" data-notification-rule-condition-field="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(condition.field || "") + '"' + (disabled ? " disabled" : "") + ' /></label>' : '',
      termsNeeded ? '<label class="notification-rule-condition-value"><span>단어</span><textarea rows="2" data-notification-rule-condition-value="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '"' + (disabled ? " disabled" : "") + '>' + escapeHtml(notificationRuleConditionValue(condition)) + '</textarea></label>' : '',
      valueNeeded ? '<label class="notification-rule-condition-value"><span>값</span><input type="text" data-notification-rule-condition-value="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(notificationRuleConditionValue(condition)) + '"' + (disabled ? " disabled" : "") + ' /></label>' : '',
      '</div>'
    ].join("");
  }

  function notificationRuleSimilarityFieldsText(rule) {
    return (Array.isArray(rule.similarityFields) ? rule.similarityFields : defaultNotificationRuleSimilarityFields()).join(", ");
  }

  function notificationRuleBypassTypeLabel(type) {
    var labels = {
      severity_upgrade: "등급 상승",
      score_delta_gte: "발송 우선도 상승",
      abs_number_delta_gte: "절대값 차이 이상",
      number_delta_gte: "숫자 증가 이상",
      number_delta_lte: "숫자 감소 이상",
      number_multiplier_gte: "배수 증가 이상"
    };
    return labels[type] || type || "반복 예외";
  }

  function notificationRuleBypassNeedsField(type) {
    return ["severity_upgrade", "abs_number_delta_gte", "number_delta_gte", "number_delta_lte", "number_multiplier_gte"].indexOf(type) >= 0;
  }

  function notificationRuleBypassNeedsValue(type) {
    return ["score_delta_gte", "abs_number_delta_gte", "number_delta_gte", "number_delta_lte", "number_multiplier_gte"].indexOf(type) >= 0;
  }

  function renderNotificationBypassCondition(messageType, condition, disabled) {
    var conditionId = String(condition.id || "");
    var type = String(condition.type || "");
    return [
      '<div class="notification-rule-bypass-condition">',
      '<label class="notification-rule-condition-main">',
      '<input type="checkbox" data-notification-rule-bypass-enabled="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '"' + (condition.enabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' />',
      '<span><strong>' + escapeHtml(condition.label || conditionId) + '</strong><em>' + escapeHtml(notificationRuleBypassTypeLabel(type)) + '</em>' + (condition.description ? '<small>' + escapeHtml(condition.description) + '</small>' : '') + '</span>',
      '</label>',
      notificationRuleBypassNeedsField(type) ? '<label><span>필드</span><input type="text" data-notification-rule-bypass-field="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(condition.field || "") + '"' + (disabled ? " disabled" : "") + ' /></label>' : '',
      notificationRuleBypassNeedsValue(type) ? '<label><span>기준값</span><input type="number" step="0.1" data-notification-rule-bypass-value="' + escapeHtml(messageType) + '" data-condition-id="' + escapeHtml(conditionId) + '" value="' + escapeHtml(condition.value) + '"' + (disabled ? " disabled" : "") + ' /></label>' : '',
      '</div>'
    ].join("");
  }

  function renderNotificationBypassConditionsEditor(messageType, rule, disabled) {
    var conditions = Array.isArray(rule.similarityBypassConditions) ? rule.similarityBypassConditions : [];
    if (!conditions.length) return "";
    return [
      '<div class="notification-rule-bypass-list">',
      '<div class="notification-rule-head notification-rule-subhead">',
      '<div><strong>반복 예외 조건</strong><span>조건이 맞으면 유사 감점을 적용하지 않고 발송합니다.</span></div>',
      '</div>',
      conditions.map(function (condition) {
        return renderNotificationBypassCondition(messageType, condition, disabled);
      }).join(""),
      '</div>'
    ].join("");
  }

  function renderNotificationSimilarityEditor(messageType, rule, disabled) {
    var summary = rule.similarityEnabled === false
      ? "유사 억제 꺼짐"
      : String(rule.similarityWindowMinutes || 0) + "분 내 같으면 " + String(rule.similarityPenalty || 0) + "점";
    return [
      '<div class="notification-rule-similarity">',
      '<div class="notification-rule-head notification-rule-subhead">',
      '<div><strong>유사 메시지</strong><span>' + escapeHtml(summary) + '</span></div>',
      '<label class="notification-rule-toggle"><input type="checkbox" data-notification-rule-similarity-enabled="' + escapeHtml(messageType) + '"' + (rule.similarityEnabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' /> 억제</label>',
      '</div>',
      '<div class="notification-rule-score-grid">',
      '<label><span>억제 시간</span><input type="number" min="0" max="10080" step="10" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="similarityWindowMinutes" value="' + escapeHtml(rule.similarityWindowMinutes) + '"' + (disabled ? " disabled" : "") + ' /></label>',
      '<label><span>반복 우선도 조정</span><input type="number" min="-100" max="0" step="1" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="similarityPenalty" value="' + escapeHtml(rule.similarityPenalty) + '"' + (disabled ? " disabled" : "") + ' /></label>',
      '<label><span>우선도 상승 예외</span><input type="number" min="0" max="100" step="1" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="similarityBypassScoreDelta" value="' + escapeHtml(rule.similarityBypassScoreDelta) + '"' + (disabled ? " disabled" : "") + ' /></label>',
      '</div>',
      '<label class="notification-rule-fields"><span>fingerprint 필드</span><textarea rows="2" data-notification-rule-fields="' + escapeHtml(messageType) + '"' + (disabled ? " disabled" : "") + '>' + escapeHtml(notificationRuleSimilarityFieldsText(rule)) + '</textarea></label>',
      renderNotificationBypassConditionsEditor(messageType, rule, disabled),
      '</div>'
    ].join("");
  }

  function renderNotificationStateCooldownEditor(messageType, rule, disabled) {
    var summary = rule.stateCooldownEnabled === false
      ? "상태 지속 억제 꺼짐"
      : "같은 임계값 상태는 " + String(rule.stateCooldownMinutes || 0) + "분 뒤 요약만 발송";
    return [
      '<div class="notification-rule-state">',
      '<div class="notification-rule-head notification-rule-subhead">',
      '<div><strong>상태 지속 억제</strong><span>' + escapeHtml(summary) + '</span></div>',
      '<label class="notification-rule-toggle"><input type="checkbox" data-notification-rule-state-enabled="' + escapeHtml(messageType) + '"' + (rule.stateCooldownEnabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' /> 적용</label>',
      '</div>',
      '<div class="notification-rule-score-grid">',
      '<label><span>요약 쿨다운</span><input type="number" min="0" max="10080" step="10" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="stateCooldownMinutes" value="' + escapeHtml(rule.stateCooldownMinutes) + '"' + (disabled ? " disabled" : "") + ' /></label>',
      '<label><span>신규 돌파</span><input type="text" value="발송" disabled /></label>',
      '<label><span>같은 상태</span><input type="text" value="보류" disabled /></label>',
      '</div>',
      '<p class="subtle">추가 확대 조건은 반복 예외 조건을 사용하고, fingerprint 필드가 같은 알림을 같은 상태로 봅니다.</p>',
      '</div>'
    ].join("");
  }

  function marketHoursSessionSummary(session) {
    if (Array.isArray(session.sessions) && session.sessions.length) {
      return session.sessions.map(function (item) {
        return String(item.label || "") + " " + String(item.openTime || "") + "-" + String(item.closeTime || "");
      }).join(" · ") + " " + String(session.timezone || "");
    }
    return String(session.openTime || "") + "-" + String(session.closeTime || "") + " " + String(session.timezone || "");
  }

  function renderNotificationMarketHoursEditor(messageType, rule, disabled) {
    var sessions = state.notificationMarketHoursSessions.length ? state.notificationMarketHoursSessions : defaultMarketHoursSessions();
    var selected = Array.isArray(rule.marketHoursMarkets) ? rule.marketHoursMarkets : defaultNotificationRuleMarketHoursMarkets(messageType);
    selected = selected.map(function (market) { return String(market || "").trim().toUpperCase(); });
    var summary = rule.marketHoursEnabled === false
      ? "장 시간 필터 꺼짐"
      : (selected.length ? selected.join(", ") : "시장 미선택") + " 거래 세션에만 발송";
    return [
      '<div class="notification-rule-market-hours">',
      '<div class="notification-rule-head notification-rule-subhead">',
      '<div><strong>장 시간 필터</strong><span>' + escapeHtml(summary) + '</span></div>',
      '<label class="notification-rule-toggle"><input type="checkbox" data-notification-rule-market-hours-enabled="' + escapeHtml(messageType) + '"' + (rule.marketHoursEnabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' /> 적용</label>',
      '</div>',
      '<div class="notification-rule-market-list">',
      sessions.map(function (session) {
        var market = String(session.market || "").toUpperCase();
        return [
          '<label class="notification-rule-market-option">',
          '<input type="checkbox" data-notification-rule-market-hours-market="' + escapeHtml(messageType) + '" data-market="' + escapeHtml(market) + '"' + (selected.indexOf(market) >= 0 ? " checked" : "") + (disabled ? " disabled" : "") + ' />',
          '<span><strong>' + escapeHtml(session.label || market) + '</strong><em>' + escapeHtml(marketHoursSessionSummary(session)) + '</em></span>',
          '</label>'
        ].join("");
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }

  function renderNotificationRuleEditor(messageType, options) {
    options = options || {};
    var rule = notificationRuleForEdit(messageType);
    var disabled = state.serverSettingsLocked || isStaticPreviewHost();
    var compact = Boolean(options.compact);
    var summary = rule.enabled === false
      ? "룰 꺼짐 · 점수만 기록하지 않고 그대로 보냅니다."
      : "발송 우선도 " + rule.threshold + " 이상이면 발송합니다.";
    return [
      '<div class="notification-rule-editor' + (options.inline ? " admin-message-rule" : "") + '">',
      '<div class="notification-rule-head">',
      '<div><strong>발송 우선도 룰</strong><span>' + escapeHtml(summary) + '</span></div>',
      '<label class="notification-rule-toggle"><input type="checkbox" data-notification-rule-enabled="' + escapeHtml(messageType) + '"' + (rule.enabled !== false ? " checked" : "") + (disabled ? " disabled" : "") + ' /> 적용</label>',
      '</div>',
      '<div class="notification-rule-score-grid">',
      '<label><span>최소 발송 우선도</span><input type="number" min="0" max="100" step="1" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="threshold" value="' + escapeHtml(rule.threshold) + '"' + (disabled ? " disabled" : "") + ' /></label>',
      '<label><span>기본 우선도</span><input type="number" min="0" max="100" step="1" data-notification-rule-number="' + escapeHtml(messageType) + '" data-rule-field="baseScore" value="' + escapeHtml(rule.baseScore) + '"' + (disabled ? " disabled" : "") + ' /></label>',
      '<label><span>낮은 우선도 처리</span><select data-notification-rule-action="' + escapeHtml(messageType) + '"' + (disabled ? " disabled" : "") + '>',
      '<option value="suppress"' + (rule.lowScoreAction === "suppress" ? " selected" : "") + '>발송 안 함</option>',
      '<option value="tag_only"' + (rule.lowScoreAction === "tag_only" ? " selected" : "") + '>우선도만 기록</option>',
      '</select></label>',
      '</div>',
      compact ? '<p class="subtle">유사 메시지, 상태 지속 억제, 장 시간 필터, 세부 조건은 고급 탭에서 조정합니다.</p>' : renderNotificationSimilarityEditor(messageType, rule, disabled),
      compact ? '' : renderNotificationStateCooldownEditor(messageType, rule, disabled),
      compact ? '' : renderNotificationMarketHoursEditor(messageType, rule, disabled),
      compact ? '' : '<div class="notification-rule-condition-list">',
      compact ? '' : (rule.conditions || []).map(function (condition) {
        return renderNotificationRuleCondition(messageType, condition, disabled);
      }).join(""),
      compact ? '' : '</div>',
      '<div class="settings-actions">',
      '<button class="text-button primary" data-rule-save="' + escapeHtml(messageType) + '"' + (disabled || state.notificationRulesLoading ? ' disabled' : '') + '>룰 저장</button>',
      '<button class="text-button" data-rule-reset="' + escapeHtml(messageType) + '"' + (disabled || state.notificationRulesLoading ? ' disabled' : '') + '>기본값</button>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderNotificationTemplateManagerPanel() {
    var templates = notificationTemplateItems();
    var editorOpen = Boolean(state.notificationTemplateEditorOpen);
    return [
      '<article class="panel notification-template-manager-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Templates</p>',
      '<h2>알림 템플릿</h2>',
      '<p class="subtle">템플릿 목록을 유지한 채 본문, 변수, 미리보기와 테스트 발송은 레이어에서 수정합니다.</p>',
      '</div>',
      '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
      '</div>',
      '<div class="settings-body">',
      state.notificationTemplatesError ? '<p class="form-error">' + escapeHtml(state.notificationTemplatesError) + '</p>' : '',
      state.notificationTemplatesSaved ? '<p class="lab-message">알림 템플릿을 저장했습니다.</p>' : '',
      '<div class="template-variable-row admin-template-variable-row">',
      notificationTemplateVariables().map(function (name) {
        return '<span class="chip">{' + escapeHtml(name) + '}</span>';
      }).join(""),
      '</div>',
      '<div class="notification-template-workbench notification-template-list-workbench">',
      '<div class="notification-template-index">',
      '<div class="flow-title"><div><strong>템플릿 목록</strong><span>수정을 누르면 목록 위로 템플릿 편집 레이어가 열립니다.</span></div></div>',
      '<div class="notification-template-select-list">',
      templates.map(renderNotificationTemplateSelector).join(""),
      '</div>',
      '</div>',
      '</div>',
      '</div>',
      editorOpen ? renderNotificationTemplateEditorLayer() : '',
      '</article>'
    ].join("");
  }

  function activeNotificationTemplate() {
    var templates = notificationTemplateItems();
    var selected = templates.filter(function (item) {
      return item.messageType === state.activeNotificationTemplateType;
    })[0];
    if (selected) return selected;
    return templates.filter(function (item) { return item.messageType === "investmentInsight"; })[0] || templates[0] || defaultNotificationTemplates()[0];
  }

  function renderNotificationTemplateSelector(item) {
    var active = activeNotificationTemplate().messageType === item.messageType;
    var editing = active && state.notificationTemplateEditorOpen;
    var kind = isAlertTemplateType(item.messageType) ? "알림" : "시스템";
    return [
      '<button type="button" class="notification-template-select-row' + (editing ? " active" : "") + '" data-template-select="' + escapeHtml(item.messageType || "") + '" aria-pressed="' + escapeHtml(editing ? "true" : "false") + '">',
      '<span><strong>' + escapeHtml(labelWithNotificationIcon(item.messageType, notificationTemplateLabel(item.messageType))) + '</strong><em>' + escapeHtml(kind + " · " + (item.messageType || "-")) + '</em></span>',
      '<b>' + escapeHtml(editing ? "편집 중" : "수정") + '</b>',
      '</button>'
    ].join("");
  }

  function renderNotificationTemplateEditorLayer() {
    var selected = activeNotificationTemplate();
    return [
      '<div class="notification-template-modal-backdrop" data-notification-template-editor-close></div>',
      '<section class="notification-template-editor-layer" role="dialog" aria-modal="true" aria-label="템플릿 상세 편집">',
      '<div class="notification-template-modal-head">',
      '<div>',
      '<p class="label">' + escapeHtml(isAlertTemplateType(selected.messageType) ? "Alert Template" : "System Template") + '</p>',
      '<h2>' + escapeHtml(labelWithNotificationIcon(selected.messageType, notificationTemplateLabel(selected.messageType))) + '</h2>',
      '<span>' + escapeHtml(selected.messageType || "-") + (selected.description ? " · " + selected.description : "") + '</span>',
      '</div>',
      '<button class="icon-button" type="button" data-notification-template-editor-close aria-label="템플릿 편집 닫기">&times;</button>',
      '</div>',
      renderNotificationTemplateRow(selected, { templateDetail: true }),
      '</section>'
    ].join("");
  }

  function renderNotificationTemplateRow(item, options) {
    options = options || {};
    var detailMode = Boolean(options.inline || options.policyDetail || options.templateDetail);
    var disabled = state.serverSettingsLocked || isStaticPreviewHost();
    var preview = renderNotificationTemplatePreviewText(item.template || "", item.messageType);
    var canTest = canSendNotificationTemplateTest(item.messageType);
    var sending = state.notificationTemplateSending === item.messageType;
    var schedule = messageScheduleByType(item.messageType);
    return [
      '<div class="notification-template-row' + (options.inline ? " admin-message-template" : "") + (options.policyDetail ? " notification-policy-template" : "") + (options.templateDetail ? " notification-template-detail-row" : "") + '">',
      '<div class="notification-template-meta">',
      '<strong>' + escapeHtml(detailMode ? "템플릿" : notificationTemplateLabel(item.messageType)) + '</strong>',
      '<span>' + escapeHtml(item.messageType || "-") + (item.description && !detailMode ? " · " + escapeHtml(item.description) : "") + '</span>',
      detailMode ? '' : '<div class="template-schedule-compact">' + renderMessageScheduleSummary(schedule) + '</div>',
      '</div>',
      '<textarea data-notification-template="' + escapeHtml(item.messageType || "") + '" rows="3"' + (disabled ? " disabled" : "") + '>' + escapeHtml(item.template || "") + '</textarea>',
      '<div class="settings-actions">',
      '<button class="text-button primary" data-template-save="' + escapeHtml(item.messageType || "") + '"' + (disabled || state.notificationTemplatesLoading ? ' disabled' : '') + '>템플릿 저장</button>',
      '<button class="text-button" data-template-reset="' + escapeHtml(item.messageType || "") + '"' + (disabled || state.notificationTemplatesLoading ? ' disabled' : '') + '>기본값</button>',
      canTest ? '<button class="text-button" data-template-test-send="' + escapeHtml(item.messageType || "") + '"' + (disabled || state.notificationTemplatesLoading || state.notificationTemplateSending ? ' disabled' : '') + '>' + escapeHtml(sending ? "발송 중" : "실제 데이터 발송") + '</button>' : '',
      '</div>',
      '<div class="notification-template-preview">',
      '<strong>미리보기</strong>',
      '<pre data-template-preview="' + escapeHtml(item.messageType || "") + '">' + escapeHtml(preview) + '</pre>',
      '</div>',
      (detailMode || options.templateOnly) ? '' : renderNotificationRuleEditor(item.messageType || "", { inline: true }),
      '</div>'
    ].join("");
  }

  function renderNotificationAdvancedRulePanel() {
    var rule = activeNotificationRule();
    return [
      '<article class="panel notification-advanced-rule-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Advanced Rule</p>',
      '<h2>선택 알림 고급 조건</h2>',
      '<p class="subtle">정책 탭에서 선택한 타입의 유사 메시지, 장 시간, 조건 점수를 세부 조정합니다.</p>',
      '</div>',
      '<span class="tone-chip hold">' + escapeHtml(rule.label) + '</span>',
      '</div>',
      '<div class="settings-body">',
      renderNotificationRuleEditor(rule.key, { inline: true }),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderNotificationThresholdPanel() {
    var thresholds = alertThresholds();
    return [
      '<article class="panel notification-threshold-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Advanced</p>',
      '<h2>알림 임계값</h2>',
      '<p class="subtle">모델, 실시간, 외부 데이터 알림이 발생하는 기준입니다. 자주 바꾸지 않는 값만 이곳에 모읍니다.</p>',
      '</div>',
      '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
      '</div>',
      '<div class="alert-threshold-section">',
      '<div class="alert-threshold-grid">',
      alertThresholdCatalog.map(function (item) {
        return renderAlertThresholdInput(item, thresholds[item.key]);
      }).join(""),
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderModelOperatingGuidePanel(snapshot) {
    var items = buildTradeSignalItems(snapshot);
    var stats = modelStatsForItems(items);
    var thresholds = modelDecisionThresholds();
    var cards = [
      {
        label: "1. 종목",
        value: "보유·관심",
        description: "계좌 보유 종목과 관심 종목을 같은 기준으로 계산합니다."
      },
      {
        label: "2. 가격",
        value: "적정가",
        description: "EPS, 목표 PER, 안전마진으로 현재가가 비싼지 싼지 봅니다."
      },
      {
        label: "3. 수급",
        value: "방향성 거래량",
        description: "거래량은 매수비중, 가격 변화, 추세와 같은 방향일 때만 강하게 반영합니다."
      },
      {
        label: "4. 행동",
        value: Math.round(thresholds.modelBuy || 0) + " / " + Math.round(thresholds.modelSell || 0) + "점",
        description: "기준 근처에서는 어떤 항목이 판단을 움직였는지와 판단 변화 가능성을 먼저 확인합니다."
      }
    ];
    var steps = [
      ["기본값으로 보기", "처음에는 수식을 바꾸지 말고 모델이 어떤 종목에 신호를 내는지 확인합니다."],
      ["영향 항목 확인", "점수보다 먼저 거래량, 이동평균, 가격 변화 중 무엇이 라벨을 움직였는지 봅니다."],
      ["가중치 조정", "가치, 수급, 리스크 중 더 믿는 축만 0.1~0.5씩 천천히 바꿉니다."],
      ["판단 기준 조정", "신호가 너무 많으면 기준 점수를 올리고, 너무 적으면 조금 낮춥니다."]
    ];
    return [
      '<article class="panel model-guide-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Strategy Operations</p>',
      '<h2>전략 운영 기준 관리</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(stats.actionCount) + '</span>',
      '</div>',
      '<div class="model-guide-grid">',
      cards.map(renderModelGuideCard).join(""),
      '</div>',
      '<div class="model-step-list">',
      steps.map(function (step, index) {
        return '<div><b>' + escapeHtml(index + 1) + '</b><span><strong>' + escapeHtml(step[0]) + '</strong><em>' + escapeHtml(step[1]) + '</em></span></div>';
      }).join(""),
      '</div>',
      '<div class="rule-strip"><span>이 화면은 주문 실행이 아니라 판단 기준을 만들고 검증하는 계산판입니다.</span><span>재료는 문헌과 실무에서 쓰이는 값이지만 계수는 초기 추천값이므로 저장 실험과 알림 이력으로 검증해야 합니다.</span></div>',
      '</article>'
    ].join("");
  }

  function valuationBeginnerText(item) {
    var valuation = item && item.valuation ? item.valuation : {};
    if (!item || !item.currentPrice || !valuation.fairValue) {
      return "가격 예시는 현재가와 적정가가 모두 있을 때 계산됩니다.";
    }
    var gap = Number(valuation.gap || 0);
    var direction = gap >= 0 ? "적정가보다 낮게 거래되어 싸게 보는 쪽" : "적정가보다 높게 거래되어 비싸게 보는 쪽";
    return item.name + " 예시: 현재가 " + formatPrice(item.currentPrice, item.currency)
      + ", 적정가 " + formatPrice(valuation.fairValue, item.currency)
      + "입니다. 적정가와 현재가 차이가 " + signedPct(gap) + "라 " + direction + "입니다.";
  }

  function volumeBeginnerText(item, variables) {
    var ratio = Number(variables.volumeRatio || 0);
    var pressure = Number(variables.directionalVolumePressure || 0);
    if (!ratio) return "거래량은 평소보다 관심이 늘었는지 보는 값입니다. 방향성 거래량은 그 관심이 매수 쪽인지 매도 쪽인지 분리합니다.";
    var direction = pressure > 0 ? "매수 쪽 거래량 확인" : (pressure < 0 ? "매도 쪽 거래량 확인" : "아직 뚜렷한 방향 없음");
    return item.name + "의 거래량은 평소 대비 " + formatSignalRatio(ratio)
      + "이고 방향성 거래량은 " + signedNumber(pressure) + "입니다. 이 예시는 " + direction + "으로 읽습니다.";
  }

  function decisionBeginnerText(item, model) {
    return item.name + "의 종합 판단은 매수 " + model.buyScore + "점, 매도 " + model.sellScore
      + "점입니다. 현재 라벨은 '" + model.action + "'이며, 실제 주문 전에 가격·수급·리스크를 다시 확인하는 신호로 봅니다.";
  }

  function beginnerModelRows(item, model) {
    if (!item || !model) {
      return [
        ["가격", "보유 또는 관심 종목 데이터가 들어오면 현재가와 적정가 예시를 표시합니다."],
        ["수급", "거래량, 매수비중, 이동평균을 쉬운 문장으로 풀어 표시합니다."],
        ["판단", "매수 점수와 매도 점수를 비교해 왜 해당 라벨이 나왔는지 설명합니다."]
      ];
    }
    var variables = model.variables || modelFormulaVariables(item);
    return [
      ["가격", valuationBeginnerText(item)],
      ["거래량", volumeBeginnerText(item, variables)],
      ["종합", decisionBeginnerText(item, model)]
    ];
  }

  function renderStrategyDataPanel(snapshot) {
    var diagnostics = strategyDataDiagnostics(snapshot);
    var issueCount = diagnostics.filter(function (item) {
      return item.tone === "caution" || item.tone === "danger";
    }).length;
    return [
      '<article class="panel strategy-data-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Data Readiness</p>',
      '<h2>전략 데이터 점검</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(issueCount) + '</span>',
      '</div>',
      '<div class="strategy-data-grid">',
      diagnostics.map(renderStrategyDataRow).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderStrategyDataRow(item) {
    var symbols = compactSymbolList(item.symbols || []);
    return [
      '<div class="strategy-data-row">',
      '<div class="strategy-data-main">',
      '<strong>' + escapeHtml(item.label) + '</strong>',
      '<span>' + escapeHtml(item.description) + '</span>',
      '<em>채울 곳: ' + escapeHtml(item.action) + '</em>',
      '</div>',
      '<div class="strategy-data-status">',
      '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.value) + '</span>',
      '<b>' + escapeHtml(symbols) + '</b>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderModelGuideCard(card) {
    return [
      '<div class="model-guide-step">',
      '<em>' + escapeHtml(card.label) + '</em>',
      '<strong>' + escapeHtml(card.value) + '</strong>',
      '<p>' + escapeHtml(card.description) + '</p>',
      '</div>'
    ].join("");
  }

  function modelWeightCatalog() {
    return [
      { key: "valuationWeight", label: "적정가 영향", description: "현재가가 적정가보다 싼지 비싼지를 얼마나 크게 볼지", step: "0.05", unit: "배" },
      { key: "flowWeight", label: "수급 영향", description: "거래량, 매수비중, 이동평균을 얼마나 크게 볼지", step: "0.05", unit: "배" },
      { key: "riskControlWeight", label: "위험 영향", description: "위험 점수가 높을 때 매수 점수를 낮추고 매도 점수를 높이는 정도", step: "0.05", unit: "배" },
      { key: "buyReasonWeight", label: "내 판단 영향", description: "종목별로 입력한 내 매수 점수를 얼마나 반영할지", step: "0.05", unit: "배" },
      { key: "confidenceWeight", label: "확신 영향", description: "확신 점수가 높을 때 매수 판단을 얼마나 보강할지", step: "0.05", unit: "배" },
      { key: "growthWeight", label: "성장성", description: "적정가 계산에서 성장 기대를 얼마나 반영할지", step: "0.05", unit: "배" },
      { key: "qualityWeight", label: "이익 품질", description: "EPS와 목표 PER을 얼마나 신뢰할지", step: "0.05", unit: "배" },
      { key: "riskWeight", label: "안전성", description: "적정가 계산에서 사업·재무 위험을 얼마나 보수적으로 볼지", step: "0.05", unit: "배" }
    ];
  }

  function modelThresholdCatalog() {
    return [
      { key: "modelBuy", label: "관심 종목 매수 후보", description: "관심 종목의 매수 점수가 이 값 이상이면 매수 후보로 표시", step: "1", unit: "점" },
      { key: "modelAdd", label: "보유 종목 보유 강화", description: "이미 보유한 종목의 매수 점수가 이 값 이상이면 추가 확인", step: "1", unit: "점" },
      { key: "modelSell", label: "분할매도 검토", description: "보유 종목의 매도 점수가 이 값 이상이면 분할매도 후보", step: "1", unit: "점" },
      { key: "modelReduce", label: "리스크 축소", description: "매도 압력이 커졌지만 즉시 매도 전 한 번 더 확인할 구간", step: "1", unit: "점" },
      { key: "modelHold", label: "관찰 시작", description: "매수나 매도까지는 아니지만 계속 볼 종목으로 표시", step: "1", unit: "점" }
    ];
  }

  function renderModelWeightGrid(weights) {
    return [
      '<div class="model-setting-card-grid">',
      modelWeightCatalog().map(function (item) {
        return renderModelNumberCard("formulaWeights", item, weights[item.key]);
      }).join(""),
      '</div>'
    ].join("");
  }

  function renderModelThresholdGrid(thresholds) {
    return [
      '<div class="model-setting-card-grid threshold-grid">',
      modelThresholdCatalog().map(function (item) {
        return renderModelNumberCard("modelDecisionThresholds", item, thresholds[item.key]);
      }).join(""),
      '</div>'
    ].join("");
  }

  function renderModelNumberCard(settingName, item, value) {
    return [
      '<label class="model-setting-card">',
      '<span>',
      '<strong>' + escapeHtml(item.label) + '</strong>',
      '<em>' + escapeHtml(item.description) + '</em>',
      '</span>',
      '<div class="model-setting-input">',
      '<input type="number" step="' + escapeHtml(item.step || "0.01") + '" value="' + escapeHtml(value == null ? 0 : value) + '" data-number-setting="' + escapeHtml(settingName) + '" data-number-key="' + escapeHtml(item.key) + '" />',
      item.unit ? '<b>' + escapeHtml(item.unit) + '</b>' : '',
      '</div>',
      '</label>'
    ].join("");
  }

  function renderAdminModelingPanel(snapshot) {
    var items = buildTradeSignalItems(snapshot);
    var stats = modelStatsForItems(items);
    var weights = formulaWeights();
    var thresholds = modelDecisionThresholds();
    return [
      '<article class="panel admin-modeling-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Operations Policy</p>',
      '<h2>모델·알림 정책 관리</h2>',
      '</div>',
      '<div class="settings-actions">',
      '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
      '</div>',
      '</div>',
      '<div class="lab-stats-grid model-stats-grid">',
      renderLabStat("평균 관계 강도", Math.round(stats.relationAverage || stats.buyAverage), "점"),
      renderLabStat("위험 관계 강도", Math.round(stats.riskAverage || stats.sellAverage), "점"),
      renderLabStat("판단 발생", stats.actionCount, "개"),
      '</div>',
      '<div class="model-editor">',
      '<div class="settings-note model-settings-note">',
      '<strong>처음 운영할 때</strong>',
      '<p>최종 판단 점수는 관계 규칙으로만 계산합니다. 기본값으로 운영하고, 반복 알림이나 과소 알림이 보일 때 관계 기준값을 조정합니다.</p>',
      '</div>',
      '<div class="settings-grid">',
      renderModelSettingField("modelName", "운영 정책 이름", "text", "나의 모델"),
      renderModelFormulaField("modelHypothesis", "보조 모델 설명", "예: 수급이 살아 있고 적정가보다 싸며 리스크가 낮을 때만 산다."),
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>관계 점수 기준</strong><span>성립한 관계 규칙의 점수가 기준 이상이면 종목 카드와 알림 라벨이 바뀝니다.</span></div></div>',
      renderModelThresholdGrid(thresholds),
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>참고 가중치</strong><span>적정가와 보조 계산에 쓰는 값입니다. 최종 판단 점수는 관계 규칙이 정합니다.</span></div></div>',
      renderModelWeightGrid(weights),
      '</div>',
      '<div class="model-section advanced-model-section">',
      '<div class="flow-title"><div><strong>고급 참고 공식</strong><span>관계 규칙을 보조하는 참고 계산입니다. 최종 매수·매도 점수로 쓰지 않습니다.</span></div></div>',
      '<div class="settings-grid">',
      renderModelFormulaField("customBuyModelFormula", "참고 매수 계산식", "buyScore * 0.35 + buyReasonScore * buyReasonWeight"),
      renderModelFormulaField("customSellModelFormula", "참고 매도 계산식", "sellScore * 0.35 + riskScore * riskControlWeight"),
      renderModelFormulaField("profitTakeScoreFormula", "참고 익절 계산식", "baseScore + profitTakePnlScore + holdingSignalScore"),
      renderModelFormulaField("lossCutScoreFormula", "참고 손실 관리 계산식", "baseScore + lossCutPnlScore + holdingSignalScore + lossGuardConfirmationScore - lossGuardWeakEvidencePenalty"),
      renderModelFormulaField("notificationScoreFormula", "알림 발송 공식", "rawScore"),
      '</div>',
      '<div class="settings-note model-settings-note">',
      '<strong>공식 변수 도움말</strong>',
      '<p>아래 이름들은 고급 공식에서 사용할 수 있는 값입니다. 수식 편집이 익숙하지 않으면 기본 공식과 위의 가중치만 사용하고, 변경 결과는 알림 이력과 실제 종목 흐름으로 검증하세요.</p>',
      '</div>',
      renderVariableGuide(modelVariableGuide()),
      '<div class="rule-strip"><span>공식은 +, -, *, /, 괄호와 min, max, abs, round, sqrt, pow, clamp 함수를 지원합니다.</span><span>공식 오류가 있으면 기본 공식으로 계산하고 종목 카드에 오류를 표시합니다.</span></div>',
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderAdminDeliveryPanel() {
    return renderAlertDeliveryPanel();
  }

  function ontologyOpinionOf(item) {
    return item && item.ontologyOpinion ? item.ontologyOpinion : {};
  }

  function ontologyPressureOf(opinion) {
    return Number(opinion.ontology_pressure || opinion.ontologyPressure || 0);
  }

  function ontologyTypeOf(relation) {
    return String(relation && (relation.type || relation.relation_type || relation.relationType) || "").toUpperCase();
  }

  function ontologyBoxOf(item) {
    var properties = item && item.properties ? item.properties : {};
    return String(
      (item && (item.ontologyBox || item.box)) ||
      properties.ontologyBox ||
      properties.box ||
      ""
    ).toUpperCase();
  }

  function ontologyIsTboxItem(item) {
    var kind = String(item && item.kind || "");
    return ontologyBoxOf(item) === "TBOX" || kind.indexOf("tbox-") === 0;
  }

  function ontologyEntityLabelMap(entities) {
    return (entities || []).reduce(function (labels, entity) {
      var id = String(entity && entity.id || "");
      if (id) labels[id] = ontologyEntityDisplayLabel(entity, id);
      return labels;
    }, {});
  }

  function ontologyEndpointLabel(id, labels) {
    var key = String(id || "");
    if (labels && labels[key]) return labels[key];
    var symbol = ontologyAboxSymbolFromId(key);
    if (symbol) return stockDisplayName(symbol);
    return key || "-";
  }

  function ontologyEntityDisplayLabel(entity, fallbackId) {
    entity = entity || {};
    var properties = entity.properties || {};
    var id = String(entity.id || fallbackId || "");
    var symbol = String(properties.symbol || ontologyAboxSymbolFromId(id) || "").trim().toUpperCase();
    if (symbol) {
      var label = String(entity.label || properties.name || properties.displayName || "").trim();
      var item = Object.assign({}, properties, { symbol: symbol });
      if (label && label.toUpperCase() !== symbol) item.name = label;
      return stockDisplayName(symbol, item);
    }
    return entity.label || properties.name || id || "-";
  }

  function ontologyRelationCounts(relations) {
    return (relations || []).reduce(function (counts, relation) {
      var type = ontologyTypeOf(relation) || "RELATED_TO";
      counts[type] = (counts[type] || 0) + 1;
      return counts;
    }, {});
  }

  function ontologyEntityCounts(entities) {
    return (entities || []).reduce(function (counts, entity) {
      var kind = String(entity && entity.kind || "entity");
      counts[kind] = (counts[kind] || 0) + 1;
      return counts;
    }, {});
  }

  function ontologyTopEntries(counts, limit) {
    return Object.keys(counts || {}).map(function (key) {
      return { key: key, value: counts[key] };
    }).sort(function (a, b) {
      if (b.value !== a.value) return b.value - a.value;
      return a.key.localeCompare(b.key);
    }).slice(0, limit || 8);
  }

  function ontologyAboxEntities(entities) {
    return (entities || []).filter(function (item) { return !ontologyIsTboxItem(item); });
  }

  function ontologyAboxRelations(relations) {
    return (relations || []).filter(function (item) { return !ontologyIsTboxItem(item); });
  }

  function ontologyEvidenceCount(evidence, kind) {
    return (evidence || []).filter(function (item) {
      return String(item && item.kind || "") === kind;
    }).length;
  }

  function ontologyBeliefCount(beliefs, polarity) {
    return (beliefs || []).filter(function (item) {
      return String(item && item.polarity || "") === polarity;
    }).length;
  }

  function ontologyContradictionCount(opinions) {
    return (opinions || []).reduce(function (count, opinion) {
      return count + (Array.isArray(opinion && opinion.contradictions) ? opinion.contradictions.length : 0);
    }, 0);
  }

  function ontologyRuleTrace(rule, index, relationCounts, evidence, beliefs, opinions) {
    var fallback = {
      input: "현재 데이터 행",
      relation: "규칙 구조 조건",
      output: "계산된 판단 근거와 AI 의견 행",
      rows: 0
    };
    if (index === 0) {
      return {
        input: "HOLDS + EXPOSED_TO",
        relation: "계좌와 업종 연결",
        output: "위험 판단 근거 " + ontologyBeliefCount(beliefs, "risk") + "개",
        rows: Number(relationCounts.HOLDS || 0) + Number(relationCounts.EXPOSED_TO || 0)
      };
    }
    if (index === 1) {
      return {
        input: "추세 + 수급 근거",
        relation: "종목별 연결",
        output: "긍정/위험 판단 근거 " + (ontologyBeliefCount(beliefs, "support") + ontologyBeliefCount(beliefs, "risk")) + "개",
        rows: ontologyEvidenceCount(evidence, "trend") + ontologyEvidenceCount(evidence, "flow")
      };
    }
    if (index === 2) {
      return {
        input: "관계 점수 + 시장 근거",
        relation: "USES_EVIDENCE_FROM",
        output: "반대 신호 " + ontologyContradictionCount(opinions) + "개",
        rows: Number(relationCounts.USES_EVIDENCE_FROM || 0) + ontologyEvidenceCount(evidence, "relation-rule")
      };
    }
    if (index === 3) {
      return {
        input: "데이터 품질 근거",
        relation: "신뢰도 확인",
        output: "AI 의견 신뢰도 " + (opinions || []).length + "개",
        rows: ontologyEvidenceCount(evidence, "data-quality")
      };
    }
    if (index === 4) {
      return {
        input: "관계 규칙 근거",
        relation: "최종 점수로 사용",
        output: "AI 의견 " + (opinions || []).length + "개",
        rows: Number(relationCounts.USES_EVIDENCE_FROM || 0)
      };
    }
    fallback.output = rule || fallback.output;
    return fallback;
  }

  function renderOntologyRelationshipGraphs(tbox, abox, aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels, relationCounts) {
    return [
      '<section class="ontology-relationship-graphs" aria-label="TBox ABox 관계 그래프">',
      renderOntologyTboxGraph(tbox, relationCounts),
      renderOntologyAboxGraph(abox, aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels),
      '</section>'
    ].join("");
  }

  function ontologyShortText(value, limit) {
    var text = String(value || "");
    var max = limit || 16;
    return text.length > max ? text.slice(0, Math.max(1, max - 1)) + "…" : text;
  }

  function ontologyGraphImportantRelationTypes() {
    return {
      HOLDS: true,
      WATCHES: true,
      REPRESENTS_STOCK: true,
      HAS_PRICE: true,
      HAS_OBSERVATION: true,
      HAS_EXTERNAL_SIGNAL: true,
      HAS_DATA_QUALITY: true,
      HAS_TRADE_FLOW: true,
      HAS_TREND_TRANSITION: true,
      BREAKS_LEVEL: true,
      RETESTS_LEVEL: true,
      RECLAIMS_LEVEL: true,
      HAS_INFERRED_RISK: true,
      HAS_INFERRED_SUPPORT: true,
      HAS_INFERRED_ENTRY_OPPORTUNITY: true,
      CREATES_NOTIFICATION_INTENT: true,
      REQUIRES_NEXT_CHECK: true,
      HAS_OPINION: true,
      HAS_EVIDENCE: true,
      PASSES_IMPORTANCE_GATE: true,
      TRIGGERS_MATERIALITY_ASSESSMENT: true
    };
  }

  function ontologyTboxGraphNodes(tbox) {
    var contexts = tbox.boundedContexts || [];
    var classDefs = tbox.classDefinitions || (tbox.classes || []).map(function (name) {
      return { name: name, label: name, bounded_context: "investment-core", parent: "" };
    });
    var ruleDefs = tbox.reasoningRuleDefinitions || (tbox.reasoningRules || []).map(function (text) {
      return { text: text, bounded_context: "reasoning-insight" };
    });
    var nodes = {};
    var contextIndex = {};
    contexts.forEach(function (context, index) {
      var key = String(context.key || context.id || "context-" + index);
      contextIndex[key] = index;
      nodes["ctx:" + key] = {
        id: "ctx:" + key,
        label: context.label || key,
        kind: "context",
        title: (context.label || key) + " · " + (context.description || ""),
        x: 90 + index * 160,
        y: 72
      };
    });
    var contextCounts = {};
    classDefs.forEach(function (item) {
      var name = String(item.name || item.className || "");
      if (!name) return;
      var contextKey = String(item.bounded_context || item.boundedContext || "investment-core");
      var index = contextIndex[contextKey];
      if (index === undefined) index = 0;
      var count = contextCounts[contextKey] || 0;
      contextCounts[contextKey] = count + 1;
      nodes["class:" + name] = {
        id: "class:" + name,
        label: item.label || name,
        kind: "schema",
        title: name + (item.description ? " · " + item.description : ""),
        x: 90 + index * 160,
        y: 150 + count * 42
      };
    });
    ruleDefs.forEach(function (item, index) {
      var contextKey = String(item.bounded_context || item.boundedContext || "reasoning-insight");
      var ctxIndex = contextIndex[contextKey];
      if (ctxIndex === undefined) ctxIndex = Math.min(contexts.length - 1, 4);
      nodes["rule:" + index] = {
        id: "rule:" + index,
        label: "R" + (index + 1),
        kind: "rule",
        title: item.text || item,
        x: 90 + ctxIndex * 160,
        y: 500 + (index % 4) * 48
      };
    });
    return nodes;
  }

  function ontologyTboxGraphEdges(tbox) {
    var classDefs = tbox.classDefinitions || (tbox.classes || []).map(function (name) {
      return { name: name, bounded_context: "investment-core", parent: "" };
    });
    var relationDefs = tbox.relationDefinitions || [];
    var ruleDefs = tbox.reasoningRuleDefinitions || (tbox.reasoningRules || []).map(function (text) {
      return { text: text, bounded_context: "reasoning-insight" };
    });
    var edges = [];
    classDefs.forEach(function (item) {
      var name = String(item.name || item.className || "");
      var contextKey = String(item.bounded_context || item.boundedContext || "investment-core");
      if (!name) return;
      edges.push({ source: "ctx:" + contextKey, target: "class:" + name, type: "DEFINES_CLASS", kind: "schema" });
      if (item.parent) {
        edges.push({ source: "class:" + name, target: "class:" + item.parent, type: "IS_A", kind: "schema" });
      }
    });
    var relationSeen = {};
    relationDefs.forEach(function (item) {
      var sourceContext = String(item.source_context || item.sourceContext || item.bounded_context || item.boundedContext || "");
      var targetContext = String(item.target_context || item.targetContext || item.bounded_context || item.boundedContext || "");
      var name = String(item.name || item.relationType || "");
      var relationType = name.toUpperCase();
      if (!sourceContext || !targetContext || !name) return;
      var key = sourceContext + "|" + targetContext + "|" + relationType;
      if (relationSeen[key]) return;
      relationSeen[key] = true;
      edges.push({ source: "ctx:" + sourceContext, target: "ctx:" + targetContext, type: relationType, kind: "schema" });
    });
    ruleDefs.forEach(function (item, index) {
      var contextKey = String(item.bounded_context || item.boundedContext || "reasoning-insight");
      edges.push({ source: "rule:" + index, target: "ctx:" + contextKey, type: "CONSTRAINS_ASSERTIONS", kind: "rule" });
    });
    return edges;
  }

  function renderOntologyTboxGraph(tbox, relationCounts) {
    var graphNodes = ontologyTboxGraphNodes(tbox);
    var graphEdges = ontologyTboxGraphEdges(tbox);
    return [
      '<section class="ontology-graph-panel ontology-tbox-graph">',
      '<div class="ontology-surface-head">',
      '<div>',
      '<strong>전체 규칙 구조 그래프</strong>',
      '<span>TBox 분류, 관계 타입, 규칙 연결을 접지 않고 모두 표시합니다.</span>',
      '</div>',
      '<div class="ontology-graph-actions">',
      '<button class="icon-button" type="button" data-ontology-graph-expand="tbox" title="규칙 구조 큰 화면으로 보기" aria-label="규칙 구조 그래프 큰 화면으로 보기">⤢</button>',
      '<button class="icon-button" type="button" data-ontology-graph-fit="tbox" title="규칙 구조 그래프 맞춤" aria-label="규칙 구조 그래프 맞춤">⌖</button>',
      '<button class="icon-button" type="button" data-ontology-graph-layout="tbox" title="규칙 구조 자동 배치" aria-label="규칙 구조 자동 배치">↺</button>',
      '</div>',
      '</div>',
      '<div class="ontology-graph-meta">',
      '<span>표시 ' + escapeHtml(graphNodes.length) + ' 노드 · ' + escapeHtml(graphEdges.length) + ' 관계</span>',
      '<span>전체 ' + escapeHtml(((tbox.boundedContexts || []).length || 0)) + ' 컨텍스트 · ' + escapeHtml((tbox.classes || []).length || 0) + ' 분류 · ' + escapeHtml((tbox.relationTypes || []).length || 0) + ' 관계 타입</span>',
      '</div>',
      '<div class="ontology-cytoscape" data-ontology-cytoscape="tbox"><span>그래프 엔진 초기화 중</span></div>',
      '<div class="ontology-graph-caption">',
      '<span>모든 relation type을 각각의 관계 edge로 표시합니다.</span>',
      '<span>점선 rule edge는 TBox 규칙이 어떤 컨텍스트의 판단을 제약하는지 나타냅니다.</span>',
      '</div>',
      '</section>'
    ].join("");
  }

  function ontologyEntityGraphLabel(entity) {
    return ontologyEntityDisplayLabel(entity, entity && entity.id);
  }

  function ontologyAddGraphNode(nodesById, id, label, kind, title, symbol) {
    if (!id || nodesById[id]) return;
    nodesById[id] = {
      id: id,
      label: label || id,
      kind: kind || "entity",
      title: title || label || id,
      symbol: symbol || ""
    };
  }

  function ontologyAboxKind(entity) {
    var kind = String(entity && entity.kind || "entity");
    if (kind === "ai-review") return "review";
    if (kind === "model") return "model";
    return kind;
  }

  function ontologyAboxSymbolFromId(id) {
    var value = String(id || "");
    return value.indexOf("stock:") === 0 ? value.slice(6).toUpperCase() : "";
  }

  function ontologyPositionAboxGraphNodes(nodesById) {
    var groups = {};
    Object.keys(nodesById).forEach(function (id) {
      var node = nodesById[id];
      var kind = node.kind || "entity";
      if (!groups[kind]) groups[kind] = [];
      groups[kind].push(node);
    });
    Object.keys(groups).forEach(function (kind) {
      groups[kind].sort(function (a, b) { return String(a.label).localeCompare(String(b.label)); });
    });
    var layout = {
      portfolio: { x: 82, y: 245, step: 70 },
      cash: { x: 82, y: 86, step: 70 },
      stock: { x: 236, y: 148, step: 104 },
      sector: { x: 404, y: 72, step: 66 },
      market: { x: 404, y: 214, step: 66 },
      currency: { x: 404, y: 296, step: 66 },
      "fx-pair": { x: 404, y: 366, step: 62 },
      "fx-rate": { x: 572, y: 350, step: 72 },
      "interest-rate": { x: 720, y: 376, step: 72 },
      "yield-curve": { x: 720, y: 448, step: 72 },
      risk: { x: 572, y: 78, step: 74 },
      opportunity: { x: 572, y: 184, step: 74 },
      model: { x: 572, y: 292, step: 74 },
      review: { x: 572, y: 368, step: 74 },
      evidence: { x: 720, y: 132, step: 104 },
      rule: { x: 720, y: 274, step: 80 },
      belief: { x: 850, y: 132, step: 104 },
      opinion: { x: 850, y: 208, step: 104 },
      "research-evidence": { x: 720, y: 132, step: 96 },
      "news-article": { x: 720, y: 132, step: 96 },
      "disclosure-filing": { x: 720, y: 210, step: 92 },
      "fact-change": { x: 572, y: 224, step: 78 },
      "trend-transition": { x: 572, y: 156, step: 78 },
      "missing-data": { x: 572, y: 300, step: 78 },
      "next-check": { x: 850, y: 300, step: 86 },
      "alert-candidate": { x: 850, y: 388, step: 86 },
      "inference-trace": { x: 720, y: 330, step: 78 },
      entity: { x: 404, y: 380, step: 62 }
    };
    Object.keys(groups).forEach(function (kind) {
      var spec = layout[kind] || layout.entity;
      groups[kind].forEach(function (node, index) {
        node.x = spec.x;
        node.y = spec.y + index * spec.step;
      });
    });
    var stockY = {};
    (groups.stock || []).forEach(function (node) {
      var symbol = node.symbol || ontologyAboxSymbolFromId(node.id);
      if (symbol) stockY[symbol] = node.y;
    });
    Object.keys(nodesById).forEach(function (id) {
      var node = nodesById[id];
      if (!node.symbol || !stockY[node.symbol]) return;
      if (node.kind === "evidence") node.y = Math.max(70, stockY[node.symbol] - 34);
      if (node.kind === "research-evidence" || node.kind === "news-article" || node.kind === "disclosure-filing") node.y = Math.max(70, stockY[node.symbol] - 44);
      if (node.kind === "fact-change" || node.kind === "trend-transition" || node.kind === "missing-data") node.y = stockY[node.symbol];
      if (node.kind === "belief") node.y = Math.max(70, stockY[node.symbol] - 34);
      if (node.kind === "opinion") node.y = stockY[node.symbol] + 30;
      if (node.kind === "next-check" || node.kind === "alert-candidate") node.y = stockY[node.symbol] + 46;
    });
  }

  function ontologySelectGraphRelations(relations) {
    var important = ontologyGraphImportantRelationTypes();
    var rows = (relations || []).filter(function (relation) {
      var type = ontologyTypeOf(relation);
      if (important[type]) return true;
      if (ontologyBoxOf(relation) === "INFERENCEBOX") return true;
      var props = relation.properties || {};
      return Boolean(props.aiInfluenceLabel || props.materialityPassed || props.decisionStage);
    });
    rows.sort(function (a, b) {
      var priority = ontologyRelationPriority(ontologyTypeOf(a)) - ontologyRelationPriority(ontologyTypeOf(b));
      if (priority !== 0) return priority;
      return Number(b.weight || 0) - Number(a.weight || 0);
    });
    return rows.slice(0, 120);
  }

  function ontologyBuildAboxGraph(aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels) {
    var nodesById = {};
    var entityById = (aboxEntities || []).reduce(function (memo, entity) {
      if (entity && entity.id) memo[entity.id] = entity;
      return memo;
    }, {});
    var graphRelations = ontologySelectGraphRelations(aboxRelations || []);
    graphRelations.forEach(function (relation) {
      [relation.source, relation.target].forEach(function (id) {
        var entity = entityById[id] || { id: id, label: ontologyEndpointLabel(id, entityLabels), kind: "entity" };
        var label = ontologyEntityGraphLabel(entity);
        var properties = entity.properties || {};
        var symbol = String(properties.symbol || ontologyAboxSymbolFromId(id) || "").toUpperCase();
        ontologyAddGraphNode(nodesById, id, label, ontologyAboxKind(entity), label, symbol);
      });
    });
    var edges = graphRelations.map(function (relation) {
      return { source: relation.source, target: relation.target, type: ontologyTypeOf(relation), kind: "assertion" };
    });
    ontologyAddGraphNode(nodesById, "runtime-rules", "Runtime Rules", "rule", "ABox assertions evaluated by TBox reasoning rules");
    (opinions || []).slice(0, 5).forEach(function (opinion) {
      var symbol = String(opinion && opinion.symbol || "").toUpperCase();
      var stockId = "stock:" + symbol;
      if (!symbol || !nodesById[stockId]) return;
      var displayName = nodesById[stockId].label || stockDisplayName(symbol);
      var stockEvidence = (evidence || []).filter(function (item) { return String(item && item.subject || "") === stockId; });
      var stockBeliefs = (beliefs || []).filter(function (item) { return String(item && item.subject || "") === stockId; });
      var evidenceId = "evidence-set:" + symbol;
      var beliefId = "belief-set:" + symbol;
      var opinionId = "opinion:" + symbol;
      ontologyAddGraphNode(nodesById, evidenceId, "근거 " + stockEvidence.length, "evidence", displayName + " 근거 " + stockEvidence.length + "개", symbol);
      ontologyAddGraphNode(nodesById, beliefId, "판단 근거 " + stockBeliefs.length, "belief", displayName + " 판단 근거 " + stockBeliefs.length + "개", symbol);
      ontologyAddGraphNode(nodesById, opinionId, "의견 " + displayName, "opinion", textWithKnownDisplaySymbols(beginnerFriendlyText(opinion.thesis || opinion.action || displayName), symbol, { symbol: symbol, name: displayName }), symbol);
      edges.push({ source: stockId, target: evidenceId, type: "HAS_EVIDENCE", kind: "derived" });
      edges.push({ source: evidenceId, target: "runtime-rules", type: "EVALUATED_BY", kind: "rule" });
      edges.push({ source: "runtime-rules", target: beliefId, type: "DERIVES", kind: "rule" });
      edges.push({ source: beliefId, target: opinionId, type: "HAS_OPINION", kind: "derived" });
    });
    ontologyPositionAboxGraphNodes(nodesById);
    return { nodesById: nodesById, edges: edges };
  }

  function renderOntologyAboxGraph(abox, aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels) {
    var portfolioId = String(abox && abox.portfolioId || "flow-lens");
    var graph = ontologyBuildAboxGraph(aboxEntities, aboxRelations, evidence, beliefs, opinions, entityLabels);
    var graphNodeCount = Object.keys(graph.nodesById || {}).length;
    var graphEdgeCount = (graph.edges || []).length;
    return [
      '<section class="ontology-graph-panel ontology-abox-graph">',
      '<div class="ontology-surface-head">',
      '<div>',
      '<strong>핵심 데이터 관계 그래프</strong>',
      '<span>실제 데이터 중 AI 판단, 중요 변경, 알림 후보와 연결되는 관계만 압축 표시합니다.</span>',
      '</div>',
      '<div class="ontology-graph-actions">',
      '<button class="icon-button" type="button" data-ontology-graph-expand="abox" title="데이터 관계 큰 화면으로 보기" aria-label="데이터 관계 그래프 큰 화면으로 보기">⤢</button>',
      '<button class="icon-button" type="button" data-ontology-graph-fit="abox" title="현재 데이터 그래프 맞춤" aria-label="현재 데이터 그래프 맞춤">⌖</button>',
      '<button class="icon-button" type="button" data-ontology-graph-layout="abox" title="현재 데이터 자동 배치" aria-label="현재 데이터 자동 배치">↺</button>',
      '</div>',
      '</div>',
      '<div class="ontology-graph-meta">',
      '<span>표시 ' + escapeHtml(graphNodeCount) + ' 노드 · ' + escapeHtml(graphEdgeCount) + ' 관계</span>',
      '<span>전체 ' + escapeHtml(aboxEntities.length) + ' 데이터 행 · ' + escapeHtml(aboxRelations.length) + ' 관계 행 · ' + escapeHtml((beliefs || []).length) + ' 판단 근거</span>',
      '<span>현재 실행 데이터 · 계좌 ' + escapeHtml(portfolioId) + '</span>',
      '</div>',
      '<div class="ontology-cytoscape" data-ontology-cytoscape="abox"><span>그래프 엔진 초기화 중</span></div>',
      '<div class="ontology-graph-caption">',
      '<span>실선은 현재 데이터에서 확인된 핵심 관계입니다.</span>',
      '<span>점선은 근거가 규칙을 거쳐 판단 근거, 다음 확인, 알림 후보로 이어지는 관계입니다.</span>',
      '</div>',
      '</section>'
    ].join("");
  }

  function ontologyEdgeLabel(type) {
    var label = String(type || "");
    return label.replace("USES_EVIDENCE_FROM", "USES_EVIDENCE").replace("REQUESTS_OPINION_FROM", "REQUESTS_OPINION");
  }

  function ontologyGraphClass(value) {
    return String(value || "entity").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  }

  function ontologyCyColor(name, fallback) {
    var styles = window.getComputedStyle ? window.getComputedStyle(document.documentElement) : null;
    var value = styles ? String(styles.getPropertyValue(name) || "").trim() : "";
    return value || fallback;
  }

  function ontologyCyElements(nodesById, edges) {
    var nodes = Object.keys(nodesById || {}).map(function (id) {
      var node = nodesById[id] || {};
      return {
        group: "nodes",
        data: {
          id: id,
          label: ontologyShortText(node.label || id, node.kind === "rule" ? 10 : 18),
          fullLabel: node.label || id,
          kind: node.kind || "entity",
          title: node.title || node.label || id
        },
        position: {
          x: Number(node.x || 0),
          y: Number(node.y || 0)
        },
        classes: "node-" + ontologyGraphClass(node.kind)
      };
    });
    var seen = {};
    var edgeItems = (edges || []).filter(function (edge) {
      return edge && nodesById[edge.source] && nodesById[edge.target];
    }).map(function (edge, index) {
      var key = [edge.source, edge.type, edge.target, edge.kind, index].join("|");
      var id = "edge:" + key;
      if (seen[id]) id += ":" + index;
      seen[id] = true;
      return {
        group: "edges",
        data: {
          id: id,
          source: edge.source,
          target: edge.target,
          label: ontologyShortText(ontologyEdgeLabel(edge.type), 18),
          fullLabel: edge.type || "",
          kind: edge.kind || "assertion"
        },
        classes: "edge-" + ontologyGraphClass(edge.kind)
      };
    });
    return nodes.concat(edgeItems);
  }

  function ontologyCurrentGraphData() {
    var parts = ontologyStrategyParts(state.snapshot || {});
    var tboxNodes = ontologyTboxGraphNodes(parts.tbox);
    var tboxEdges = ontologyTboxGraphEdges(parts.tbox);
    var aboxGraph = ontologyBuildAboxGraph(parts.aboxEntities, parts.aboxRelations, parts.evidence, parts.beliefs, parts.opinions, parts.entityLabels);
    return {
      tbox: { elements: ontologyCyElements(tboxNodes, tboxEdges) },
      abox: { elements: ontologyCyElements(aboxGraph.nodesById, aboxGraph.edges) }
    };
  }

  function ontologyCytoscapeStyle() {
    var ink = ontologyCyColor("--ink", "#172033");
    var muted = ontologyCyColor("--muted", "#64748b");
    var panel = ontologyCyColor("--panel", "#ffffff");
    var blue = ontologyCyColor("--blue", "#2563eb");
    var green = ontologyCyColor("--green", "#059669");
    var red = ontologyCyColor("--red", "#dc2626");
    var amber = ontologyCyColor("--amber", "#d97706");
    var violet = ontologyCyColor("--violet", "#7c3aed");
    var line = ontologyCyColor("--line", "#d8e0ea");
    return [
      {
        selector: "node",
        style: {
          "shape": "round-rectangle",
          "width": 132,
          "height": 44,
          "background-color": panel,
          "border-width": 1.4,
          "border-color": line,
          "label": "data(label)",
          "color": ink,
          "font-size": 10,
          "font-weight": 800,
          "text-valign": "center",
          "text-halign": "center",
          "text-wrap": "wrap",
          "text-max-width": 112,
          "overlay-opacity": 0
        }
      },
      { selector: ".node-context", style: { "width": 150, "height": 48, "background-color": "#eef2ff", "border-color": violet, "color": ink, "font-weight": 900 } },
      { selector: ".node-rule", style: { "width": 74, "height": 36, "background-color": "#fff7ed", "border-color": amber, "color": amber, "font-size": 9 } },
      { selector: ".node-schema, .node-portfolio, .node-stock", style: { "background-color": "#eff6ff", "border-color": blue } },
      { selector: ".node-sector, .node-market, .node-currency, .node-cash", style: { "background-color": "#ecfdf5", "border-color": green } },
      { selector: ".node-fx-rate, .node-fx-pair", style: { "background-color": "#ecfeff", "border-color": green } },
      { selector: ".node-interest-rate, .node-yield-curve", style: { "background-color": "#fff7ed", "border-color": amber } },
      { selector: ".node-risk", style: { "background-color": "#fef2f2", "border-color": red } },
      { selector: ".node-evidence, .node-belief, .node-opinion, .node-review, .node-model, .node-research-evidence, .node-news-article, .node-disclosure-filing", style: { "background-color": "#f5f3ff", "border-color": violet } },
      { selector: ".node-fact-change, .node-trend-transition", style: { "background-color": "#eff6ff", "border-color": blue } },
      { selector: ".node-missing-data, .node-data-quality, .node-source-reliability", style: { "background-color": "#fff7ed", "border-color": amber } },
      { selector: ".node-next-check, .node-alert-candidate, .node-inference-trace", style: { "background-color": "#f8fafc", "border-color": ink } },
      {
        selector: "edge",
        style: {
          "curve-style": "taxi",
          "taxi-direction": "rightward",
          "taxi-turn": 32,
          "taxi-turn-min-distance": 12,
          "target-arrow-shape": "triangle",
          "target-arrow-color": blue,
          "line-color": blue,
          "width": 1.25,
          "opacity": 0.82,
          "label": "data(label)",
          "font-size": 7,
          "font-weight": 700,
          "color": muted,
          "text-background-color": panel,
          "text-background-opacity": 0.82,
          "text-background-padding": 2,
          "text-rotation": "autorotate",
          "text-margin-y": -6
        }
      },
      { selector: ".edge-schema", style: { "line-color": blue, "target-arrow-color": blue, "width": 1.8 } },
      { selector: ".edge-assertion", style: { "line-color": green, "target-arrow-color": green } },
      { selector: ".edge-derived", style: { "line-color": violet, "target-arrow-color": violet, "line-style": "dashed" } },
      { selector: ".edge-rule", style: { "line-color": amber, "target-arrow-color": amber, "line-style": "dashed" } },
      { selector: ":selected", style: { "border-width": 3, "border-color": ink, "line-color": ink, "target-arrow-color": ink, "opacity": 1 } }
    ];
  }

  function destroyOntologyCytoscapeGraphs() {
    Object.keys(ontologyGraphInstances || {}).forEach(function (key) {
      var instance = ontologyGraphInstances[key];
      if (instance && typeof instance.destroy === "function") instance.destroy();
    });
    ontologyGraphInstances = {};
  }

  function initOntologyCytoscapeGraphs() {
    var containers = Array.prototype.slice.call(app.querySelectorAll("[data-ontology-cytoscape]"));
    if (!containers.length) return;
    if (!window.cytoscape) {
      containers.forEach(function (container) {
        container.innerHTML = '<span>Cytoscape.js를 불러오지 못했습니다.</span>';
      });
      return;
    }
    var graphs = ontologyCurrentGraphData();
    containers.forEach(function (container) {
      var graphId = container.getAttribute("data-ontology-cytoscape") || "";
      var sourceGraphId = normalizeOntologyGraphId(graphId);
      var graph = graphs[sourceGraphId];
      if (!graph || !graph.elements.length) {
        container.innerHTML = '<span>표시할 그래프 관계가 없습니다.</span>';
        return;
      }
      container.innerHTML = "";
      ontologyGraphInstances[graphId] = window.cytoscape({
        container: container,
        elements: graph.elements,
        style: ontologyCytoscapeStyle(),
        layout: { name: "preset", fit: true, padding: 28 },
        minZoom: 0.25,
        maxZoom: 2.4,
        wheelSensitivity: 0.18,
        boxSelectionEnabled: false,
        autoungrabify: false
      });
      ontologyGraphInstances[graphId].ready(function () {
        this.fit(undefined, 30);
      });
    });
  }

  function fitOntologyGraph(graphId) {
    var instance = ontologyGraphInstances[graphId];
    if (!instance) return;
    instance.fit(undefined, 30);
    instance.center();
  }

  function layoutOntologyGraph(graphId) {
    var instance = ontologyGraphInstances[graphId];
    if (!instance) return;
    instance.layout({
      name: "breadthfirst",
      directed: true,
      padding: 36,
      spacingFactor: normalizeOntologyGraphId(graphId) === "abox" ? 1.25 : 1.1,
      avoidOverlap: true,
      animate: true,
      animationDuration: 260,
      fit: true
    }).run();
  }

  function renderOntologyClassPanel(tbox) {
    var classes = tbox.classes || [];
    var relationTypes = tbox.relationTypes || [];
    var contexts = tbox.boundedContexts || [];
    var classDefs = tbox.classDefinitions || [];
    var grouped = {};
    classDefs.forEach(function (item) {
      var key = String(item.bounded_context || item.boundedContext || "schema");
      if (!grouped[key]) grouped[key] = [];
      grouped[key].push(item);
    });
    return [
      '<section class="ontology-surface ontology-tbox-surface">',
      '<div class="ontology-surface-head">',
      '<strong>규칙 구조</strong>',
      '<span>' + escapeHtml(contexts.length || 0) + ' 컨텍스트 · ' + escapeHtml(classes.length) + ' 분류 · ' + escapeHtml(relationTypes.length) + ' 관계 종류</span>',
      '</div>',
      contexts.length && classDefs.length ? '<div class="ontology-context-class-grid">' + contexts.map(function (context) {
        var key = String(context.key || "");
        var rows = grouped[key] || [];
        return [
          '<div class="ontology-context-class-group">',
          '<strong>' + escapeHtml(context.label || key) + '</strong>',
          '<span>' + escapeHtml(context.description || "") + '</span>',
          '<div class="ontology-class-grid">',
          rows.slice(0, 18).map(function (item) {
            return '<em>' + escapeHtml(item.label || item.name) + '</em>';
          }).join("") || '<em>분류 없음</em>',
          rows.length > 18 ? '<em>+' + escapeHtml(rows.length - 18) + '</em>' : '',
          '</div>',
          '</div>'
        ].join("");
      }).join("") + '</div>' : '<div class="ontology-class-grid">' + (classes.length ? classes.map(function (item) {
        return '<span>' + escapeHtml(item) + '</span>';
      }).join("") : '<span>등록된 규칙 분류 없음</span>') + '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyAboxPanel(abox, aboxEntities, evidence, beliefs, opinions) {
    var counts = ontologyEntityCounts(aboxEntities);
    return [
      '<section class="ontology-surface ontology-abox-surface">',
      '<div class="ontology-surface-head">',
      '<strong>현재 데이터</strong>',
      '<span>' + escapeHtml(abox.entityCount || aboxEntities.length || 0) + ' 데이터 · ' + escapeHtml(abox.beliefCount || beliefs.length || 0) + ' 판단 근거</span>',
      '</div>',
      renderOntologyDistribution(counts, "데이터 구성"),
      '<div class="ontology-abox-metrics">',
      renderOntologyMiniMetric("근거", evidence.length),
      renderOntologyMiniMetric("판단 근거", beliefs.length),
      renderOntologyMiniMetric("AI 의견", opinions.length),
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyRelationalProjectionPanel(entities, relations, evidence, beliefs, opinions, parts) {
    parts = parts || {};
    var rows = [
      { name: "데이터 행", count: entities.length, key: "id", fk: "규칙 구조/현재 데이터 구분" },
      { name: "현재 데이터 행", count: (parts.aboxEntities || []).length || ontologyAboxEntities(entities).length, key: "ABox id", fk: "실제 계정·종목·관측값" },
      { name: "관계 행", count: relations.length, key: "source + type + target", fk: "출발점,도착점 -> 데이터 id" },
      { name: "현재 관계 행", count: (parts.aboxRelations || []).length || ontologyAboxRelations(relations).length, key: "ABox relation", fk: "현재 데이터 그래프 edge" },
      { name: "근거 행", count: evidence.length, key: "id", fk: "대상 -> 데이터 id" },
      { name: "판단 근거 행", count: beliefs.length, key: "id", fk: "대상 -> 데이터 id" },
      { name: "AI 의견 행", count: opinions.length, key: "회사 표시명", fk: "회사명 -> 종목 데이터" },
      { name: "실행 계획 행", count: (parts.executionPlans || []).length, key: "symbol + action", fk: "AI 의견 -> 실행 계획" },
      { name: "인사이트 행", count: (parts.insights || []).length, key: "subject + type", fk: "관계 변화 -> 알림 후보" }
    ];
    return [
      '<section class="ontology-surface ontology-projection-surface">',
      '<div class="ontology-surface-head">',
      '<strong>테이블 저장 구조</strong>',
      '<span>운영 DB 관점 · 규칙 구조와 현재 데이터를 행 단위로 표시</span>',
      '</div>',
      '<div class="ontology-projection-grid">',
      rows.map(renderOntologyProjectionRow).join(""),
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyProjectionRow(row) {
    return [
      '<div class="ontology-projection-row">',
      '<strong>' + escapeHtml(row.name) + '</strong>',
      '<span>PK ' + escapeHtml(row.key) + '</span>',
      '<span>' + escapeHtml(row.fk) + '</span>',
      '<em>' + escapeHtml(row.count) + '</em>',
      '</div>'
    ].join("");
  }

  function renderOntologyMiniMetric(label, value) {
    return '<span><em>' + escapeHtml(label) + '</em><strong>' + escapeHtml(value) + '</strong></span>';
  }

  function renderOntologyDistribution(counts, label) {
    var entries = ontologyTopEntries(counts, 8);
    if (!entries.length) return '<div class="ontology-empty">' + escapeHtml(label) + ' 없음</div>';
    var max = entries.reduce(function (current, item) {
      return Math.max(current, Number(item.value || 0));
    }, 1);
    return [
      '<div class="ontology-distribution" aria-label="' + escapeHtml(label) + '">',
      entries.map(function (item) {
        var width = Math.max(8, Math.round((Number(item.value || 0) / max) * 100));
        return [
          '<div class="ontology-distribution-row">',
          '<span>' + escapeHtml(item.key) + '</span>',
          '<b><i style="width:' + escapeHtml(width) + '%"></i></b>',
          '<em>' + escapeHtml(item.value) + '</em>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>'
    ].join("");
  }

  function renderOntologyRelationPanel(tbox, relations, aboxRelations, relationCounts, entityLabels) {
    var relationTypes = (tbox.relationTypes || []).slice();
    ontologyTopEntries(relationCounts, 20).forEach(function (item) {
      if (relationTypes.indexOf(item.key) < 0) relationTypes.push(item.key);
    });
    return [
      '<section class="ontology-surface ontology-relation-surface">',
      '<div class="ontology-surface-head">',
      '<strong>TBox Relation Constraints</strong>',
      '<span>' + escapeHtml(aboxRelations.length) + ' ABox relation rows · ' + escapeHtml(relations.length) + ' total rows</span>',
      '</div>',
      '<div class="ontology-relation-table">',
      relationTypes.slice(0, 18).map(function (type) {
        return renderOntologyRelationRow(type, relationCounts[type] || 0, aboxRelations, entityLabels);
      }).join(""),
      '</div>',
      '</section>'
    ].join("");
  }

  function renderOntologyRelationRow(type, count, relations, entityLabels) {
    var sample = (relations || []).filter(function (item) {
      return ontologyTypeOf(item) === type;
    })[0] || {};
    var source = ontologyEndpointLabel(sample.source, entityLabels);
    var target = ontologyEndpointLabel(sample.target, entityLabels);
    var example = sample.source ? source + ' → ' + target : "TBox declared only";
    return [
      '<div class="ontology-relation-row ' + (count ? "active" : "empty") + '">',
      '<strong>' + escapeHtml(type) + '</strong>',
      '<span>' + escapeHtml(example) + '</span>',
      '<em>' + escapeHtml(count) + '</em>',
      '</div>'
    ].join("");
  }

  function renderOntologyRulePanel(tbox, relationCounts, evidence, beliefs, opinions) {
    var rules = tbox.reasoningRules || [];
    return [
      '<section class="ontology-surface ontology-rule-surface ontology-rule-trace-surface">',
      '<div class="ontology-surface-head">',
      '<strong>규칙 추적</strong>',
      '<span>' + escapeHtml(rules.length) + ' 규칙 -> 현재 데이터에서 계산된 행</span>',
      '</div>',
      '<div class="ontology-rule-list ontology-rule-trace-list">',
      rules.length ? rules.map(function (rule, index) {
        var trace = ontologyRuleTrace(rule, index, relationCounts, evidence, beliefs, opinions);
        return [
          '<div class="ontology-rule-row ontology-rule-trace-row">',
          '<b>' + escapeHtml(index + 1) + '</b>',
          '<span class="ontology-rule-body">',
          '<strong>' + escapeHtml(rule) + '</strong>',
          '<em>input: ' + escapeHtml(trace.input) + '</em>',
          '<em>constraint: ' + escapeHtml(trace.relation) + '</em>',
          '<em>output: ' + escapeHtml(trace.output) + '</em>',
          '</span>',
          '<i>' + escapeHtml(trace.rows) + '</i>',
          '</div>'
        ].join("");
      }).join("") : '<div class="ontology-empty">reasoning rule 없음</div>',
      '</div>',
      '</section>'
    ].join("");
  }

  function renderLabStat(label, value, suffix) {
    return [
      '<span>',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value) + escapeHtml(suffix || "") + '</strong>',
      '</span>'
    ].join("");
  }

  function ontologyRuleRows() {
    return String(settingValue("ontologyRelationRules") || defaultSettings.ontologyRelationRules || "")
      .split(/\r?\n/)
      .map(function (line) { return line.trim(); })
      .filter(Boolean)
      .map(function (line) {
        var parts = line.split("|").map(function (part) { return part.trim(); });
        return {
          id: parts[0] || "",
          label: parts[1] || parts[0] || "",
          condition: parts[2] || "",
          relation: parts[3] || "",
          signal: parts[4] || "",
          prompt: parts.slice(5).join(" | ")
        };
      });
  }

  function promptTemplateRows() {
    var rows = [];
    var current = null;
    String(settingValue("aiPromptTemplates") || defaultSettings.aiPromptTemplates || "")
      .split(/\r?\n/)
      .forEach(function (line) {
        var cleaned = line.trim();
        if (!cleaned) return;
        var header = cleaned.match(/^\[([^\]]+)\]$/);
        if (header) {
          current = { id: header[1], label: header[1], purpose: "", version: "" };
          rows.push(current);
          return;
        }
        if (!current || cleaned.indexOf("=") < 0) return;
        var key = cleaned.split("=", 1)[0].trim();
        var value = cleaned.slice(cleaned.indexOf("=") + 1).trim();
        current[key] = value;
        if (key === "label") current.label = value;
        if (key === "purpose") current.purpose = value;
        if (key === "version") current.version = value;
      });
    return rows;
  }

  function renderOntologyRuleEditorPanel(snapshot) {
    var rules = ontologyRuleRows();
    var thresholds = modelDecisionThresholds();
    var relationThresholdValues = relationRuleThresholds();
    return [
      '<article class="panel model-panel ontology-rule-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Relation Rule Registry</p>',
      '<h2>관계 규칙 관리</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(rules.length) + '</span>',
      '</div>',
      '<div class="lab-stats-grid model-stats-grid">',
      renderLabStat("관계 규칙", rules.length, "개"),
      renderLabStat("보유 종목", (snapshot.positions || []).filter(function (item) { return item.symbol !== "CASH"; }).length, "개"),
      renderLabStat("판단 기준", Math.round(thresholds.modelSell || 0), "점"),
      renderLabStat("손실 기준", signedPct(relationThresholdValues.lossRateLow || -8), ""),
      '</div>',
      '<div class="model-editor">',
      '<div class="settings-note model-settings-note">',
      '<strong>관계 규칙 런타임</strong>',
      '<p>저장된 관계 규칙 메타데이터는 백엔드 relation context의 label, relation type, signal type, prompt hint에 반영됩니다. 조건 평가는 검증된 엔진을 사용합니다.</p>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>관계 규칙 원본</strong><span>형식: ruleId | label | condition | relationType | signalType | promptHint</span></div></div>',
      '<label class="setting-field wide"><textarea data-model-setting="ontologyRelationRules" rows="10" autocomplete="off">' + escapeHtml(settingValue("ontologyRelationRules") || defaultSettings.ontologyRelationRules) + '</textarea></label>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>성립 가능한 관계</strong><span>알림은 이 관계가 실제 데이터와 연결될 때 발생합니다.</span></div></div>',
      '<div class="source-stack ontology-rule-list">',
      rules.map(function (rule) {
        return [
          '<div class="source-row">',
          '<span>' + escapeHtml(rule.signal || rule.relation || "relation") + '</span>',
          '<strong>' + escapeHtml(rule.label) + '</strong>',
          '<em>' + escapeHtml(rule.condition || rule.prompt || rule.id) + '</em>',
          '</div>'
        ].join("");
      }).join("") || '<p class="subtle">등록된 관계 규칙이 없습니다.</p>',
      '</div>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>관계 성립 기준값</strong><span>ABox에 들어온 가격·수급·추세·외부 신호가 어떤 관계로 성립할지 정합니다. 알림 발송 기준과 분리됩니다.</span></div></div>',
      renderNumberSettingGrid("relationRuleThresholds", relationThresholdValues, ["lossRateLow", "lossRateBufferPct", "lossGuardVolumeConfirmRatio", "lossGuardMa60SupportPct", "lossGuardWeakEvidencePenalty", "profitRateHigh", "sectorWeightHigh", "positionWeightHigh", "externalBitcoinChange24hPct", "externalBitcoinChange7dPct", "entryPullbackMa20BelowPct", "entryPullbackMa20DeepPct", "entryMa60SupportPct", "entryVolumeMinRatio", "entryVolumeMaxRatio", "entrySmartMoneyMin", "entryTradeStrengthMin", "entryOrderbookImbalanceMin", "entryMaxPositionWeight", "entryMaxSectorWeight"]),
      '</div>',
      '<div class="rule-strip"><span>실제 점수는 관계 규칙의 성립 강도입니다. 공식 입력값은 참고 계산과 발송 정책에만 사용합니다.</span></div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderNeo4jRuleboxPanel() {
    var payload = state.ontologyRulebox || {};
    var rules = Array.isArray(payload.rules) ? payload.rules : [];
    var relationTypes = Array.isArray(payload.relationTypes) ? payload.relationTypes : [];
    var versions = Array.isArray(payload.versions) ? payload.versions : [];
    var candidates = Array.isArray(payload.changeCandidates) ? payload.changeCandidates : [];
    var lastRun = state.ontologyRuleboxLastRun || {};
    var disabled = state.ontologyRuleboxSaving || state.ontologyRuleboxRunning || state.ontologyRuleboxProposing || state.serverSettingsLocked;
    return [
      '<article class="panel model-panel neo4j-rulebox-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Neo4j RuleBox</p>',
      '<h2>그래프 규칙 실행 콘솔</h2>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(payload.status === "ok" ? "watch" : payload.configured ? "caution" : "hold") + '">' + escapeHtml(ruleboxStatusLabel(payload)) + '</span>',
      '</div>',
      '<div class="model-editor neo4j-rulebox-editor">',
      '<div class="lab-stats-grid model-stats-grid">',
      renderLabStat("규칙", payload.ruleCount || rules.length || 0, "개"),
      renderLabStat("조건", payload.conditionCount || countRuleboxConditions(rules), "개"),
      renderLabStat("파생 관계", payload.derivationCount || countRuleboxDerivations(rules), "개"),
      renderLabStat("버전", payload.versionCount || versions.length || 0, "개"),
      '</div>',
      '<div class="settings-note model-settings-note">',
      '<strong>Neo4j를 RuleBox 원본으로 사용합니다.</strong>',
      '<p>저장 시 RuleBox와 InferenceBox를 지우고 규칙 구조를 다시 적재한 뒤 버전 해시를 남깁니다. 실행 버튼은 Neo4j 안의 관계 조건을 읽어 InferenceBox 관계를 다시 만듭니다.</p>',
      '</div>',
      '<div class="rulebox-console-strip">',
      '<span><strong>source</strong>' + escapeHtml(payload.source || "-") + '</span>',
      '<span><strong>engine</strong>' + escapeHtml(payload.engineVersion || "-") + '</span>',
      '<span><strong>relations</strong>' + escapeHtml(relationTypes.length ? relationTypes.join(", ") : "-") + '</span>',
      lastRun.status ? '<span><strong>last run</strong>' + escapeHtml(lastRun.status + (lastRun.reason ? " · " + lastRun.reason : "")) + '</span>' : '',
      '</div>',
      state.ontologyRuleboxLoading ? '<p class="lab-message">Neo4j RuleBox를 읽는 중입니다.</p>' : '',
      state.ontologyRuleboxError ? '<p class="form-error">' + escapeHtml(state.ontologyRuleboxError) + '</p>' : '',
      payload.reason ? '<p class="lab-message caution">' + escapeHtml(payload.reason) + '</p>' : '',
      '<label class="setting-field wide"><span>변경 이유</span><input data-ontology-rulebox-change-reason type="text" autocomplete="off" placeholder="예: 피어 뉴스 후보 검토, 판단 단계 정책 보강" value="' + escapeHtml(state.ontologyRuleboxChangeReason || "") + '"></label>',
      '<div class="settings-actions rulebox-actions">',
      '<button class="text-button" type="button" data-action="refresh-rulebox"' + (state.ontologyRuleboxLoading ? ' disabled' : '') + '>새로고침</button>',
      '<button class="text-button" type="button" data-action="seed-rulebox"' + (disabled ? ' disabled' : '') + '>기본값 시드</button>',
      '<button class="text-button primary" type="button" data-action="save-rulebox"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(state.ontologyRuleboxSaving ? "저장 중" : "RuleBox 저장") + '</button>',
      '<button class="text-button primary" type="button" data-action="run-rulebox"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(state.ontologyRuleboxRunning ? "실행 중" : "Neo4j 추론 실행") + '</button>',
      '<button class="text-button primary" type="button" data-action="propose-rulebox-candidates"' + (disabled ? ' disabled' : '') + '>' + escapeHtml(state.ontologyRuleboxProposing ? "생성 중" : "AI 후보 생성") + '</button>',
      '</div>',
      '<div class="settings-grid compact-settings-grid">',
      '<label class="setting-field"><span>AI 후보 사용</span><select data-model-setting="ontologyRuleCandidateAiEnabled"><option value="1"' + ((settingValue("ontologyRuleCandidateAiEnabled") || defaultSettings.ontologyRuleCandidateAiEnabled) !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("ontologyRuleCandidateAiEnabled") || defaultSettings.ontologyRuleCandidateAiEnabled) === "0" ? " selected" : "") + '>끄기</option></select></label>',
      '<label class="setting-field"><span>Codex 사용</span><select data-model-setting="ontologyRuleCandidateAiUseCodex"><option value="1"' + ((settingValue("ontologyRuleCandidateAiUseCodex") || defaultSettings.ontologyRuleCandidateAiUseCodex) !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("ontologyRuleCandidateAiUseCodex") || defaultSettings.ontologyRuleCandidateAiUseCodex) === "0" ? " selected" : "") + '>로컬</option></select></label>',
      '<label class="setting-field"><span>주기(분)</span><input data-model-setting="ontologyRuleCandidateAiIntervalMinutes" type="number" min="5" step="5" value="' + escapeHtml(settingValue("ontologyRuleCandidateAiIntervalMinutes") || defaultSettings.ontologyRuleCandidateAiIntervalMinutes) + '"></label>',
      '<label class="setting-field"><span>최대 후보</span><input data-model-setting="ontologyRuleCandidateAiMaxCandidates" type="number" min="1" max="10" step="1" value="' + escapeHtml(settingValue("ontologyRuleCandidateAiMaxCandidates") || defaultSettings.ontologyRuleCandidateAiMaxCandidates) + '"></label>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>최근 버전</strong><span>저장된 RuleBox 해시와 변경 이유입니다.</span></div></div>',
      '<div class="source-stack rulebox-version-list">',
      versions.length ? versions.map(renderNeo4jRuleboxVersionRow).join("") : '<p class="subtle">아직 기록된 RuleBox 버전이 없습니다.</p>',
      '</div>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>AI 관계 후보 검토</strong><span>후보는 JSON 초안에만 추가됩니다. enabled=false 상태로 검토 후 활성화하세요.</span></div></div>',
      '<div class="source-stack rulebox-candidate-list">',
      candidates.length ? candidates.map(function (candidate) { return renderNeo4jRuleboxCandidateRow(candidate, disabled); }).join("") : '<p class="subtle">검토할 관계 후보가 없습니다.</p>',
      '</div>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>Neo4j 저장 원본 JSON</strong><span>GraphInferenceRule 배열입니다. 조건과 derivation을 추가하면 다음 실행부터 관계 추론 대상이 됩니다.</span></div></div>',
      '<label class="setting-field wide"><textarea data-ontology-rulebox-json rows="18" autocomplete="off">' + escapeHtml(state.ontologyRuleboxJson || JSON.stringify(rules, null, 2)) + '</textarea></label>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>활성 규칙 요약</strong><span>Neo4j에 적재된 RuleBox 노드를 사람이 읽기 쉽게 펼친 목록입니다.</span></div></div>',
      '<div class="source-stack rulebox-rule-list">',
      rules.length ? rules.map(renderNeo4jRuleboxRuleRow).join("") : '<p class="subtle">Neo4j RuleBox 규칙이 비어 있습니다.</p>',
      '</div>',
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function ruleboxStatusLabel(payload) {
    if (!payload || !payload.configured) return "Neo4j 미연결";
    if (payload.status === "ok") return "Neo4j 연결";
    return payload.status || "확인 필요";
  }

  function countRuleboxConditions(rules) {
    return (rules || []).reduce(function (count, rule) {
      return count + ((rule.conditions || []).length || 0);
    }, 0);
  }

  function countRuleboxDerivations(rules) {
    return (rules || []).reduce(function (count, rule) {
      return count + ((rule.derivations || []).length || 0);
    }, 0);
  }

  function renderNeo4jRuleboxRuleRow(rule) {
    var conditions = Array.isArray(rule.conditions) ? rule.conditions : [];
    var derivations = Array.isArray(rule.derivations) ? rule.derivations : [];
    var relationTypes = derivations.map(function (item) {
      return item.relation_type || item.relationType || "";
    }).filter(Boolean);
    return [
      '<div class="source-row rulebox-rule-row">',
      '<span>' + escapeHtml(rule.enabled === false ? "disabled" : (rule.action_group || rule.actionGroup || "enabled")) + '</span>',
      '<strong>' + escapeHtml(rule.label || rule.rule_id || rule.ruleId || "Rule") + '</strong>',
      '<em>' + escapeHtml((rule.rule_id || rule.ruleId || "") + " · " + conditions.length + " conditions · " + (relationTypes.join(", ") || derivations.length + " derivations")) + '</em>',
      '</div>'
    ].join("");
  }

  function renderNeo4jRuleboxVersionRow(version) {
    return [
      '<div class="source-row rulebox-version-row">',
      '<span>' + escapeHtml(version.versionLabel || version.shortHash || "-") + '</span>',
      '<strong>' + escapeHtml(version.changeReason || "변경 이유 없음") + '</strong>',
      '<em>' + escapeHtml([formatClock(version.createdAt), (version.ruleCount || 0) + " rules", version.author || ""].filter(Boolean).join(" · ")) + '</em>',
      '</div>'
    ].join("");
  }

  function renderNeo4jRuleboxCandidateRow(candidate, disabled) {
    var requiresData = Array.isArray(candidate.requiresData) ? candidate.requiresData : [];
    var proposed = candidate.proposedRule && typeof candidate.proposedRule === "object" ? candidate.proposedRule : null;
    var canAppend = proposed && candidate.status !== "covered";
    return [
      '<div class="source-row rulebox-candidate-row">',
      '<span>' + escapeHtml(candidate.status || "candidate") + '</span>',
      '<strong>' + escapeHtml(candidate.title || candidate.id || "관계 후보") + '</strong>',
      '<em>' + escapeHtml(candidate.rationale || "") + (requiresData.length ? '<br><small>' + escapeHtml("필요 데이터: " + requiresData.join(", ")) + '</small>' : '') + '</em>',
      '<button class="text-button" type="button" data-action="append-rulebox-candidate" data-candidate-id="' + escapeHtml(candidate.id || "") + '"' + (!canAppend || disabled ? ' disabled' : '') + '>' + escapeHtml(candidate.status === "covered" ? "반영됨" : proposed ? "JSON에 추가" : "데이터 필요") + '</button>',
      '</div>'
    ].join("");
  }

  function renderAiPromptRegistryPanel(snapshot) {
    var prompts = promptTemplateRows();
    return [
      '<article class="panel model-panel prompt-registry-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Prompt Registry</p>',
      '<h2>AI 분석 프롬프트 관리</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(prompts.length) + '</span>',
      '</div>',
      '<div class="settings-body">',
      '<div class="settings-note">',
      '<strong>프롬프트는 관계 규칙을 쉽게 풀어 쓰는 설정입니다.</strong>',
      '<p>기본 알림은 관계 규칙으로 발생하고, 저장된 템플릿은 백엔드 promptContext.promptTemplate으로 반영됩니다. AI는 성립 이유, 반대 근거, 부족 데이터, 모델 개선점을 비동기로 설명합니다.</p>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>실시간 알림 AI 검증</strong><span>알림 워커가 AI 답변을 기다린 뒤 검증된 실행 메시지만 보낼지 정합니다.</span></div></div>',
      '<div class="settings-grid compact-settings-grid">',
      '<label class="setting-field"><span>AI 검증 대기</span><select data-model-setting="notificationAiGateEnabled"><option value="1"' + ((settingValue("notificationAiGateEnabled") || defaultSettings.notificationAiGateEnabled) !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("notificationAiGateEnabled") || defaultSettings.notificationAiGateEnabled) === "0" ? " selected" : "") + '>끄기</option></select></label>',
      '<label class="setting-field"><span>Codex 사용</span><select data-model-setting="notificationAiUseCodex"><option value="1"' + ((settingValue("notificationAiUseCodex") || defaultSettings.notificationAiUseCodex) !== "0" ? " selected" : "") + '>사용</option><option value="0"' + ((settingValue("notificationAiUseCodex") || defaultSettings.notificationAiUseCodex) === "0" ? " selected" : "") + '>로컬 검증만</option></select></label>',
      '<label class="setting-field"><span>타임아웃(초)</span><input data-model-setting="notificationAiTimeoutSeconds" type="number" min="30" step="10" value="' + escapeHtml(settingValue("notificationAiTimeoutSeconds") || defaultSettings.notificationAiTimeoutSeconds) + '"></label>',
      '</div>',
      '<label class="setting-field wide"><span>적용 알림 타입</span><textarea data-model-setting="notificationAiGateMessageTypes" rows="3" autocomplete="off">' + escapeHtml(settingValue("notificationAiGateMessageTypes") || defaultSettings.notificationAiGateMessageTypes) + '</textarea></label>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>프롬프트 정책</strong><span>AI가 데이터를 해석할 때 반드시 지켜야 하는 경계입니다.</span></div></div>',
      '<label class="setting-field wide"><textarea data-model-setting="aiPromptPolicy" rows="6" autocomplete="off">' + escapeHtml(settingValue("aiPromptPolicy") || defaultSettings.aiPromptPolicy) + '</textarea></label>',
      '</div>',
      '<div class="model-section">',
      '<div class="flow-title"><div><strong>프롬프트 템플릿</strong><span>알림 타입별 AI 질문과 출력 계약입니다.</span></div></div>',
      '<label class="setting-field wide"><textarea data-model-setting="aiPromptTemplates" rows="14" autocomplete="off">' + escapeHtml(settingValue("aiPromptTemplates") || defaultSettings.aiPromptTemplates) + '</textarea></label>',
      '</div>',
      '<div class="source-stack prompt-registry-list">',
      prompts.map(function (prompt) {
        return [
          '<div class="source-row prompt-registry-row">',
          '<span>' + escapeHtml(prompt.id) + '</span>',
          '<strong>' + escapeHtml(prompt.label || prompt.id) + '</strong>',
          '<em>' + escapeHtml([prompt.version, prompt.purpose].filter(Boolean).join(" · ")) + '</em>',
          '</div>'
        ].join("");
      }).join("") || '<p class="subtle">등록된 프롬프트가 없습니다.</p>',
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderModelPreviewPanel(snapshot) {
    var items = buildTradeSignalItems(snapshot).map(function (item) {
      return Object.assign({}, item, { model: customModelScores(item) });
    }).sort(function (a, b) {
      if (a.model.rank !== b.model.rank) return a.model.rank - b.model.rank;
      return Math.max(b.model.buyScore, b.model.sellScore) - Math.max(a.model.buyScore, a.model.sellScore);
    });
    return [
      '<article class="panel model-preview-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Model Preview</p>',
      '<h2>현재 종목 판단 결과</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(items.length) + '</span>',
      '</div>',
      '<div class="signal-list">',
      items.length ? items.map(renderModelPreviewRow).join("") : '<p class="subtle">보유 종목이나 관심 종목이 있으면 여기서 매수 후보, 보유 강화, 분할매도 판단이 표시됩니다.</p>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderModelPreviewRow(item) {
    var model = item.model || customModelScores(item);
    var displayName = stockDisplayName(item.symbol, item);
    return [
      '<div class="signal-row model-preview-row">',
      '<div class="signal-main">',
      '<div class="flow-title">',
      '<div>',
      '<strong>' + escapeHtml(displayName) + '</strong>',
      '<span>' + escapeHtml(stockDisplayMeta(item, [sourceLabel(item.source), "현재 " + (item.currentPrice ? formatPrice(item.currentPrice, item.currency) : "-")])) + '</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(model.tone || "hold") + '">' + escapeHtml(model.action) + '</span>',
      '</div>',
      renderModelRelationRuleSummary(item),
      '<div class="lab-model-grid">',
      '<span>관계 신호 <strong>' + escapeHtml(Math.round(item.relationStrength || 0)) + '</strong></span>',
      '<span>성립 규칙 <strong>' + escapeHtml((item.relationRules || []).length) + '</strong></span>',
      '<span>참고 매수 <strong class="buy">' + escapeHtml(model.buyScore) + '</strong></span>',
      '<span>참고 매도 <strong class="sell">' + escapeHtml(model.sellScore) + '</strong></span>',
      '<span>기본 매수 점수 <strong>' + escapeHtml(item.hasData ? item.buyScore : "-") + '</strong></span>',
      '<span>기본 매도 점수 <strong>' + escapeHtml(item.hasData ? item.sellScore : "-") + '</strong></span>',
      '</div>',
      renderModelPlainLanguageExplanation(item, model),
      renderModelFeatureAudit(item, model),
      model.errors.length ? '<div class="exit-reasons">' + model.errors.map(function (error) { return '<p>' + escapeHtml(error) + '</p>'; }).join("") + '</div>' : '',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderModelRelationRuleSummary(item) {
    var rules = item.relationRules || [];
    return [
      '<div class="model-feature-audit model-relation-summary">',
      '<div class="feature-audit-head">',
      '<strong>관계 규칙</strong>',
      '<span class="tone-chip ' + escapeHtml((rules[0] && rules[0].tone) || "hold") + '">' + escapeHtml(rules.length ? Math.round(rules[0].score) + "점" : "대기") + '</span>',
      '</div>',
      '<div class="feature-audit-grid">',
      rules.length ? rules.slice(0, 4).map(function (rule) {
        return '<span>' + escapeHtml(rule.label) + ' <strong>' + escapeHtml(Math.round(rule.score || 0)) + '</strong></span>';
      }).join("") : '<span>성립한 관계 규칙이 없습니다.</span>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderModelPlainLanguageExplanation(item, model) {
    var rows = beginnerModelRows(item, model);
    return [
      '<div class="model-feature-audit model-plain-explain">',
      '<div class="feature-audit-head">',
      '<strong>쉬운 해석</strong>',
      '<span class="tone-chip hold">실제 데이터 예시</span>',
      '</div>',
      '<div class="variable-grid">',
      rows.map(function (row) {
        return '<span><strong>' + escapeHtml(row[0]) + '</strong>' + escapeHtml(row[1]) + '</span>';
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }

  function renderModelFeatureAudit(item, model) {
    if (!item.hasData) {
      return [
        '<div class="model-feature-audit">',
        '<div class="feature-audit-head">',
        '<strong>재계산 확인</strong>',
        '<span class="tone-chip hold">데이터 부족</span>',
        '</div>',
        '<div class="feature-audit-grid"><span>거래량, 이동평균, 투자자별 수급을 입력하면 같은 입력으로 다시 계산하고, 항목별 제외 실험을 실행합니다.</span></div>',
        '</div>'
      ].join("");
    }
    var audit = modelFeatureAudit(item, model);
    var variables = audit.variables || {};
    var signal = item.signal || {};
    var contributionRows = (audit.contributions || []).slice().sort(function (a, b) {
      return (Math.abs(b.buy) + Math.abs(b.sell)) - (Math.abs(a.buy) + Math.abs(a.sell));
    }).slice(0, 6);
    var featureRows = [
      ["거래량", formatSignalRatio(variables.volumeRatio)],
      ["방향성 거래량", signedNumber(variables.directionalVolumePressure)],
      ["흐름 방향", signedNumber(variables.flowDirectionScore)],
      ["20일선 차이", formatSignalNumber(variables.trendDistance20, "%")],
      ["60일선 차이", formatSignalNumber(variables.trendDistance60, "%")],
      ["외국인", formatSignalVolume(signal.foreignNet)],
      ["기관", formatSignalVolume(signal.institutionNet)],
      ["개인", formatSignalVolume(signal.individualNet)],
      ["수급점수", formatSignalNumber(variables.investorFlowScore, "")]
    ];
    return [
      '<div class="model-feature-audit">',
      '<div class="feature-audit-head">',
      '<strong>재계산 확인</strong>',
      '<span class="tone-chip ' + (audit.stable ? "watch" : "caution") + '">' + (audit.stable ? "같은 입력 재현됨" : "재계산 확인 필요") + '</span>',
      '</div>',
      '<div class="feature-audit-grid">',
      featureRows.map(function (row) {
        return '<span>' + escapeHtml(row[0]) + ' <strong>' + escapeHtml(row[1]) + '</strong></span>';
      }).join(""),
      '</div>',
      '<div class="feature-delta-grid">',
      audit.groups.map(function (group) {
        var tone = group.changed ? "changed" : "stable";
        return '<span class="' + tone + '"><b>' + escapeHtml(group.label) + '</b><strong>매수 ' + escapeHtml(signedNumber(group.buyDelta)) + ' / 매도 ' + escapeHtml(signedNumber(group.sellDelta)) + '</strong><em>' + escapeHtml(group.changed ? "판단 변화 가능" : "판단 유지") + '</em></span>';
      }).join(""),
      '</div>',
      '<div class="feature-contribution-grid">',
      '<strong>판단을 움직인 항목</strong>',
      contributionRows.map(function (row) {
        var tone = Math.abs(row.buy) >= Math.abs(row.sell) ? "buy" : "sell";
        return '<span class="' + tone + '"><b>' + escapeHtml(row.label) + '</b><strong>매수 ' + escapeHtml(signedNumber(row.buy)) + ' / 매도 ' + escapeHtml(signedNumber(row.sell)) + '</strong><em>' + escapeHtml(row.description) + '</em></span>';
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }

  function renderAlertCenterPanel(snapshot) {
    var alerts = buildAlertItems(snapshot);
    var stats = alertStats(alerts);
    return [
      '<article class="panel alert-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Alert Center</p>',
      '<h2>매수·매도 타이밍 알림</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(stats.total) + '</span>',
      '</div>',
      '<div class="alert-stat-grid">',
      renderAlertStat("긴급", stats.danger, "danger"),
      renderAlertStat("주의", stats.caution, "caution"),
      renderAlertStat("관찰", stats.watch, "watch"),
      renderAlertStat("정보", stats.info, "info"),
      '</div>',
      '<div class="alert-list">',
      alerts.length ? alerts.map(function (alert, index) {
        return renderAlertRow(alert, index);
      }).join("") : '<p class="subtle">현재 켜진 규칙에서 발생한 알림이 없습니다.</p>',
      '</div>',
      '<div class="rule-strip"><span>알림은 주문 지시가 아니라 가격선, 수급, 모델 점수, 보유 리스크를 다시 확인하라는 신호입니다.</span></div>',
      '</article>'
    ].join("");
  }

  function renderAlertStat(label, value, severity) {
    return [
      '<span class="alert-stat ' + escapeHtml(severity) + '">',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(value) + '</strong>',
      '</span>'
    ].join("");
  }

  function renderAlertRow(alert, index) {
    var displaySymbol = alert.symbol ? stockDisplayName(alert.symbol, alert) : "";
    var title = textWithDisplaySymbol(alert.title || "-", alert.symbol, alert);
    var meta = [
      displaySymbol || "",
      alert.source || "",
      alert.value ? "현재 " + alert.value : "",
      alert.threshold ? "기준 " + alert.threshold : ""
    ].filter(Boolean);
    return [
      '<div class="alert-row ' + escapeHtml(alert.severity || "info") + '" role="button" tabindex="0" data-monitor-alert-detail="' + escapeHtml(index) + '" aria-label="' + escapeHtml((title || "알림") + " 상세 보기") + '">',
      '<span class="alert-severity ' + escapeHtml(alert.severity || "info") + '">' + escapeHtml(alertSeverityLabel(alert.severity)) + '</span>',
      '<div class="alert-main">',
      '<div class="flow-title">',
      '<div>',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<span>' + escapeHtml(meta.join(" · ")) + '</span>',
      '</div>',
      '<span class="tone-chip hold">' + escapeHtml(alertRuleLabel(alert.rule)) + '</span>',
      '</div>',
      '<p>' + escapeHtml(alert.message || "") + '</p>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderAlertThresholdInput(item, value) {
    return [
      '<label class="lab-control alert-threshold">',
      '<span>' + escapeHtml(item.label) + (item.unit ? " (" + escapeHtml(item.unit) + ")" : "") + '</span>',
      '<input data-alert-threshold="' + escapeHtml(item.key) + '" type="number" step="' + escapeHtml(item.step || "1") + '" value="' + escapeHtml(value) + '" />',
      '</label>'
    ].join("");
  }

  function renderAlertDeliveryPanel() {
    var secretType = state.showSecrets ? "text" : "password";
    return [
      '<article class="panel alert-delivery-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Delivery</p>',
      '<h2>웹·푸시 알림 설정</h2>',
      '</div>',
      '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
      '</div>',
      '<div class="settings-body">',
      '<div class="settings-note">',
      '<strong>저장 위치</strong>',
      '<p>웹 알림 규칙, 모델 설정, 푸시 채널 설정은 같은 설정 저장소를 사용합니다. 저장하면 로컬 서버의 알림 워커도 같은 값을 읽습니다.</p>',
      state.serverSettingsError ? '<p class="form-error">' + escapeHtml(state.serverSettingsError) + '</p>' : '',
      state.serverSettingsLocked ? '<p class="form-error">공유 모드에서는 서버 설정 저장이 잠겨 있습니다.</p>' : '',
      '</div>',
      renderSettingsApiSummary(),
      '<div class="settings-grid">',
      renderSettingField("notifyProvider", "알림 제공자", "text", "telegram"),
      renderSettingField("notifyLinkUrl", "알림 링크 URL", "url", "http://127.0.0.1:3000?tab=notifications"),
      renderSettingField("telegramBotToken", "Telegram Bot Token", secretType, "bot token", { preserveConfigured: true }),
      renderSettingField("telegramChatId", "Telegram Chat ID", "text", "chat id", { preserveConfigured: true }),
      '</div>',
      '<div class="settings-actions">',
      '<button class="' + settingsSaveButtonClass() + '" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
      '<button class="text-button" data-action="toggle-secrets">' + (state.showSecrets ? "secret 숨기기" : "secret 보기") + '</button>',
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderModelSettingField(name, label, type, placeholder) {
    return [
      '<label class="setting-field">',
      '<span>' + escapeHtml(label) + '</span>',
      '<input data-model-setting="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(settingValue(name) || defaultSettings[name] || "") + '" placeholder="' + escapeHtml(placeholder || "") + '" autocomplete="off" />',
      '</label>'
    ].join("");
  }

  function renderModelFormulaField(name, label, placeholder) {
    return [
      '<label class="setting-field wide">',
      '<span>' + escapeHtml(label) + '</span>',
      '<textarea data-model-setting="' + escapeHtml(name) + '" rows="3" autocomplete="off" placeholder="' + escapeHtml(placeholder || "") + '">' + escapeHtml(formulaSetting(name)) + '</textarea>',
      '</label>'
    ].join("");
  }

  function renderNumberSettingGrid(settingName, map, keys) {
    return [
      '<div class="model-number-grid">',
      keys.map(function (key) {
        return renderNumberSettingInput(settingName, key, map[key]);
      }).join(""),
      '</div>'
    ].join("");
  }

  function numberSettingCatalogItem(key) {
    var catalogs = alertThresholdCatalog.concat(modelWeightCatalog()).concat(modelThresholdCatalog());
    return catalogs.filter(function (item) { return item.key === key; })[0] || null;
  }

  function renderNumberSettingInput(settingName, key, value) {
    var item = numberSettingCatalogItem(key);
    var label = item ? item.label : key;
    var unit = item && item.unit ? " " + item.unit : "";
    var step = item && item.step ? item.step : "0.01";
    return [
      '<label class="lab-control">',
      '<span>' + escapeHtml(label) + escapeHtml(unit) + '</span>',
      '<input type="number" step="' + escapeHtml(step) + '" value="' + escapeHtml(value == null ? 0 : value) + '" data-number-setting="' + escapeHtml(settingName) + '" data-number-key="' + escapeHtml(key) + '" />',
      '</label>'
    ].join("");
  }

  function modelVariableGuide() {
    return [
      ["buyScore", "수급/가치 기반 시스템 매수 점수"],
      ["sellScore", "수급/가치 기반 시스템 매도 점수"],
      ["volumeRatio", "거래량 배율"],
      ["buyShare", "매수 체결 비중"],
      ["sellShare", "매도 체결 비중"],
      ["bidAskImbalance", "호가 불균형"],
      ["priceChangeRate", "가격 변화율"],
      ["volumePressure", "거래량 배율을 -10~25 범위로 점수화"],
      ["directionalVolumePressure", "거래량이 매수/매도 어느 방향을 확인하는지 반영"],
      ["volumeConfirmation", "거래량 방향 확인 강도"],
      ["buyShareScore", "매수 체결 비중을 -25~25 범위로 점수화"],
      ["orderbookScore", "호가 불균형을 -20~20 범위로 점수화"],
      ["momentumScore", "가격 변화율을 -20~20 범위로 점수화"],
      ["flowDirectionScore", "체결, 비중, 호가, 가격, 추세, 투자자 수급의 합성 방향"],
      ["ma20", "20일 이동평균"],
      ["ma60", "60일 이동평균"],
      ["trendDistance20", "20일선과 현재가 차이"],
      ["trendDistance60", "60일선과 현재가 차이"],
      ["maSpread", "20일선과 60일선의 간격"],
      ["trendScore", "이동평균 추세 점수"],
      ["foreignNet", "외국인 순매수"],
      ["institutionNet", "기관 순매수"],
      ["individualNet", "개인 순매수"],
      ["smartMoneyNet", "외국인+기관 순매수"],
      ["investorFlowScore", "투자자별 수급 점수"],
      ["buyReasonScore", "실험실에서 입력한 내 매수 점수"],
      ["riskScore", "실험실에서 입력한 위험 점수"],
      ["confidenceScore", "확신 점수"],
      ["targetReturn", "목표 수익률"],
      ["stopLoss", "허용 손절률"],
      ["positionSize", "비중 계획"],
      ["fairValueGap", "적정가와 현재가 차이"],
      ["undervalueBonus", "저평가 보너스"],
      ["expensivePenalty", "고평가/매도 보너스"],
      ["profitLossRate", "보유 수익률"],
      ["baseScore", "보유 모델 기본 점수"],
      ["profitTakePnlScore", "수익 구간에서 익절을 점검하게 하는 점수"],
      ["lossCutPnlScore", "손실 구간에서 손실 관리를 점검하게 하는 점수"],
      ["lossThreshold", "손실 관리 기준 손익률"],
      ["lossRateBufferPct", "손실 기준 근처 흔들림을 흡수하는 완충 구간"],
      ["lossRateDepth", "손실 기준을 넘은 폭"],
      ["lossRateNearThreshold", "손실 기준 완충 구간 안에 있는지 여부"],
      ["lossGuardConfirmationCount", "손실 관리 확인 신호 개수"],
      ["lossGuardConfirmationScore", "60일선 이탈, 거래량, 매도 체결, 투자자 수급, 이동평균 기울기가 손실 관리를 확인할 때 더하는 점수"],
      ["lossGuardWeakEvidencePenalty", "손실 기준 근처인데 60일선은 유지되고 거래량·수급 확인이 약할 때 빼는 점수"],
      ["sectorConcentrationScore", "한 업종에 많이 몰렸을 때 더하는 점수"],
      ["sellableScore", "팔 수 있는 수량이 있을 때 더하는 점수"],
      ["holdingSignalScore", "수급과 이동평균 흐름을 반영한 보유 점수"],
      ["rawScore", "알림 조건을 모두 더한 기본 발송 우선도"],
      ["symbolScore", "종목명이 있는 알림에 더하는 점수"],
      ["confirmingDataScore", "수급·추세 같은 확인 데이터가 있는 알림 점수"],
      ["actionableScore", "확인이나 점검이 필요한 알림 점수"],
      ["noisePenalty", "상태성 반복 알림을 낮추는 점수"]
    ];
  }

  function renderVariableGuide(items) {
    return [
      '<div class="variable-grid">',
      items.map(function (item) {
        return '<span><strong>' + escapeHtml(item[0]) + '</strong>' + escapeHtml(item[1]) + '</span>';
      }).join(""),
      '</div>'
    ].join("");
  }

  function portfolioFxRates() {
    return parseNumberAssignments(settingValue("fxRates"), parseNumberAssignments(defaultSettings.fxRates));
  }

  function portfolioFxRateText(rates) {
    return Object.keys(rates || {}).sort().map(function (key) {
      return key.toUpperCase() + "=" + Number(rates[key] || 0).toLocaleString("ko-KR");
    }).join(", ");
  }

  function portfolioItemCurrency(item) {
    var explicit = String(item && item.currency || "").toUpperCase();
    if (explicit) return explicit;
    var market = String(item && item.market || "").toUpperCase();
    var symbol = String(item && item.symbol || "");
    if (market === "US") return "USD";
    if (market === "KR" || market === "KOSPI" || market === "KOSDAQ" || symbol.match(/^\d{6}$/)) return "KRW";
    return "KRW";
  }

  function portfolioValueInBase(value, currency, rates) {
    var code = String(currency || "KRW").toUpperCase();
    return Math.max(0, numeric(value) * Number((rates || {})[code] || 1));
  }

  function portfolioPositionBaseValue(item, rates) {
    var marketValue = numeric(item && item.marketValue);
    if (!marketValue && numeric(item && item.quantity) && currentPriceOf(item || {})) {
      marketValue = numeric(item.quantity) * currentPriceOf(item);
    }
    return portfolioValueInBase(marketValue, portfolioItemCurrency(item || {}), rates);
  }

  function portfolioHoldingPositions(snapshot) {
    var toss = snapshot.toss || {};
    return (toss.positions || []).filter(function (item) {
      return item && item.source !== "cash" && item.sector !== "현금" && String(item.symbol || "").toUpperCase() !== "CASH";
    });
  }

  function portfolioCashPositions(snapshot) {
    var toss = snapshot.toss || {};
    return (toss.positions || []).filter(function (item) {
      return item && (item.source === "cash" || item.sector === "현금" || String(item.symbol || "").toUpperCase() === "CASH");
    });
  }

  function portfolioSum(items, picker) {
    return (items || []).reduce(function (total, item) {
      return total + numeric(picker(item));
    }, 0);
  }

  function exposureDiffText(actual, expected) {
    var diff = numeric(actual) - numeric(expected);
    if (Math.abs(diff) < 1) return "일치";
    return "차이 " + (diff > 0 ? "+" : "-") + formatMoney(Math.abs(diff));
  }

  function portfolioCashBasisText(snapshot, portfolio) {
    var cashPositions = portfolioCashPositions(snapshot);
    var account = (snapshot.toss || {}).account || {};
    if (cashPositions.length) return "CASH 포지션 marketValue 우선";
    if (numeric(account.orderableAmount)) return "계좌 orderableAmount / buying-power";
    if (numeric(portfolio.cash)) return "계좌 현금 필드";
    return "현금 없음 또는 API 미응답";
  }

  function renderPortfolioMarketRows(portfolio) {
    return (portfolio.markets || []).filter(function (market) {
      return Number(market.total || 0) > 0;
    }).map(function (market) {
      var label = market.label || market.key || "-";
      var value = [
        "투자 " + formatMoney(market.invested || 0),
        "현금 " + formatMoney(market.cash || 0),
        "합계 " + formatMoney(market.total || 0),
        "현금비중 " + pct(market.cashRatio || 0)
      ].join(" · ");
      return '<div class="source-row"><span>' + escapeHtml(label) + '</span><strong>' + escapeHtml(value) + '</strong></div>';
    }).join("");
  }

  function renderPortfolioBasisRows(snapshot, portfolio) {
    var rates = portfolioFxRates();
    var holdings = portfolioHoldingPositions(snapshot);
    var ledgerInvested = holdings.reduce(function (total, item) {
      return total + portfolioPositionBaseValue(item, rates);
    }, 0);
    var marketTotal = portfolioSum(portfolio.markets || [], function (market) { return market.total; });
    var sectorTotal = portfolioSum(portfolio.sectors || [], function (sector) { return sector.value; });
    var formulaTotal = numeric(portfolio.invested) + numeric(portfolio.cash);
    var source = (snapshot.toss && snapshot.toss.status ? snapshot.toss.status : "토스 스냅샷") + " · " + (snapshot.dataMode || (snapshot.mock ? "mock" : "live"));
    var rows = [
      ["데이터 원천", source],
      ["노출 계산 기준", "KRW 기준 원화환산 · 총 평가 = 투자 평가액 + 현금/주문 가능"],
      ["환율 기준", portfolioFxRateText(rates)],
      ["현금 기준", portfolioCashBasisText(snapshot, portfolio)],
      ["총 평가 산식", formatMoney(portfolio.invested || 0) + " + " + formatMoney(portfolio.cash || 0) + " = " + formatMoney(formulaTotal)],
      ["총 평가 차이", exposureDiffText(portfolio.total || 0, formulaTotal)],
      ["보유 원장 합계", holdings.length + "개 marketValue 원화환산 = " + formatMoney(ledgerInvested)],
      ["투자 평가액 차이", exposureDiffText(portfolio.invested || 0, ledgerInvested)],
      ["시장별 합계", (portfolio.markets || []).length + "개 시장 total = " + formatMoney(marketTotal)],
      ["시장 합계 차이", exposureDiffText(portfolio.total || 0, marketTotal)],
      ["섹터별 합계", (portfolio.sectors || []).length + "개 sector value = " + formatMoney(sectorTotal)],
      ["섹터 합계 차이", exposureDiffText(portfolio.total || 0, sectorTotal)]
    ];
    return rows.map(function (row) {
      return '<div class="source-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
    }).join("");
  }

  function renderPortfolioPanel(snapshot) {
    var portfolio = snapshot.portfolio || { sectors: [] };
    var marketRows = renderPortfolioMarketRows(portfolio);
    return [
      '<article class="panel portfolio-exposure-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Toss Portfolio</p>',
      '<h2>계좌 노출</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(formatMoney(portfolio.total)) + '</span>',
      '</div>',
      '<div class="allocation">',
      '<div class="source-row"><span>투자 평가액</span><strong>' + escapeHtml(formatMoney(portfolio.invested || 0)) + '</strong></div>',
      '<div class="source-row"><span>현금/주문 가능</span><strong>' + escapeHtml(formatMoney(portfolio.cash || 0)) + '</strong></div>',
      marketRows,
      (portfolio.sectors || []).map(function (sector) {
        return [
          '<div class="bar-row">',
          '<div class="bar-meta"><span>' + escapeHtml(sector.sector) + '</span><strong>' + escapeHtml(pct(sector.ratio)) + '</strong></div>',
          '<div class="bar-track"><span style="width:' + Math.min(100, Math.max(2, sector.ratio)) + '%"></span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="source-stack">',
      renderPortfolioBasisRows(snapshot, portfolio),
      '</div>',
      '<div class="rule-strip"><span>금액이 맞지 않으면 먼저 보유 원장 합계, 현금 기준, 환율 기준의 차이 행을 확인하세요.</span></div>',
      '</article>'
    ].join("");
  }

  function renderMonitoringInstrumentPanel(snapshot) {
    var items = instrumentItems(snapshot);
    var signalMap = {};
    buildTradeSignalItems(snapshot).forEach(function (item) {
      signalMap[item.symbol] = item;
    });
    var holdings = items.filter(function (item) { return item.source !== "watchlist"; }).length;
    var watch = items.length - holdings;
    var priced = items.filter(function (item) { return Boolean(currentPriceOf(item)); }).length;
    return [
      '<article class="panel monitoring-instrument-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Monitoring Universe</p>',
      '<h2>보유·관심 종목 통합</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(items.length) + '</span>',
      '</div>',
      '<div class="monitoring-instrument-summary">',
      '<div><span>보유</span><strong>' + escapeHtml(holdings) + '</strong></div>',
      '<div><span>관심</span><strong>' + escapeHtml(watch) + '</strong></div>',
      '<div><span>시세</span><strong>' + escapeHtml(priced) + '</strong></div>',
      '</div>',
      '<div class="monitoring-instrument-list">',
      items.length ? items.map(function (item) {
        var symbol = String(item.symbol || "").toUpperCase();
        return renderMonitoringInstrumentRow(item, signalMap[symbol]);
      }).join("") : renderEmptyState({
        tone: "muted",
        label: "Universe",
        title: "보유·관심 종목이 아직 없습니다",
        description: "계정 탭에서 연결을 확인하거나 관심종목 탭에서 추적 대상을 추가하면 모니터링 원장이 채워집니다.",
        meta: ["계정 연결", "관심종목"]
      }),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderMonitoringInstrumentRow(item, signal) {
    var symbol = String(item.symbol || "").toUpperCase();
    var holding = item.source !== "watchlist";
    var price = currentPriceOf(item);
    var sourceLabel = holding ? "보유" : "관심";
    var sourceClass = holding ? "holding" : "watchlist";
    var valueText = holding
      ? formatCurrency(item.marketValue || 0, item.currency)
      : (price ? formatCurrency(price, item.currency) : "시세 대기");
    var detailText = holding
      ? "수량 " + (item.quantity || "-") + " · 평단 " + formatCurrency(item.averagePrice || 0, item.currency)
      : (item.quoteStatus || "관심 기준 관찰");
    var performanceText = holding
      ? signedMoney(item.profitLoss, item.currency) + " · " + signedPct(item.profitLossRate)
      : (item.changeRate == null ? "등락률 대기" : signedPct(item.changeRate));
    var signalText = signal && signal.hasData
      ? "매수 " + signal.buyScore + " · 매도 " + signal.sellScore
      : "수급 입력 필요";
    var displayName = stockDisplayName(symbol, item);
    return [
      '<div class="monitoring-instrument-row" role="button" tabindex="0" data-monitor-instrument-detail="' + escapeHtml(symbol) + '" aria-label="' + escapeHtml(displayName + " 상세 보기") + '">',
      '<div class="monitoring-instrument-main">',
      '<div class="monitoring-instrument-title">',
      '<strong>' + escapeHtml(displayName) + '</strong>',
      '<span class="source-chip ' + escapeHtml(sourceClass) + '">' + escapeHtml(sourceLabel) + '</span>',
      '</div>',
      '<span>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || "-"), item.sector || "-"])) + '</span>',
      '<span>' + escapeHtml(detailText) + '</span>',
      '</div>',
      '<div class="monitoring-instrument-side">',
      '<strong>' + escapeHtml(valueText) + '</strong>',
      '<span>' + escapeHtml(performanceText) + '</span>',
      '<span class="tone-chip ' + escapeHtml(signal && signal.tone ? signal.tone : "hold") + '">' + escapeHtml(signal && signal.action ? signal.action : "관찰") + '</span>',
      '<em>' + escapeHtml(signalText) + '</em>',
      '</div>',
      '</div>'
    ].join("");
  }

  function monitoringSignalItemBySymbol(snapshot, symbol) {
    var target = String(symbol || "").toUpperCase();
    if (!target) return null;
    var items = buildTradeSignalItems(snapshot || state.snapshot || {});
    return items.filter(function (item) {
      return String(item.symbol || "").toUpperCase() === target;
    })[0] || null;
  }

  function monitoringAlertByIndex(snapshot, index) {
    var alerts = buildAlertItems(snapshot || state.snapshot || {});
    var selectedIndex = Number(index);
    if (!Number.isFinite(selectedIndex) || selectedIndex < 0 || selectedIndex >= alerts.length) return null;
    return alerts[selectedIndex];
  }

  function monitoringDetailCurrency(value, currency) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "-";
    return formatCurrency(number, currency);
  }

  function monitoringDetailQuantity(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "-";
    return number.toLocaleString("ko-KR", {
      maximumFractionDigits: Number.isInteger(number) ? 0 : 4
    });
  }

  function renderMonitoringDetailMetric(label, value, tone) {
    var displayValue = value == null || value === "" ? "-" : value;
    return [
      '<span class="monitoring-detail-metric ' + escapeHtml(tone || "") + '">',
      '<em>' + escapeHtml(label) + '</em>',
      '<strong>' + escapeHtml(displayValue) + '</strong>',
      '</span>'
    ].join("");
  }

  function renderMonitoringDetailSignalGrid(item) {
    var signal = item.signal || {};
    return [
      '<div class="monitoring-detail-block">',
      '<div class="flow-title">',
      '<div>',
      '<strong>신호 입력값</strong>',
      '<span>매수·매도 점수를 만든 수급, 추세, 투자자별 데이터입니다.</span>',
      '</div>',
      '</div>',
      '<div class="monitoring-detail-signal-grid">',
      renderMonitoringDetailMetric("거래량 배율", formatSignalRatio(signal.volumeRatio)),
      renderMonitoringDetailMetric("매수량", formatSignalVolume(signal.buyVolume)),
      renderMonitoringDetailMetric("매도량", formatSignalVolume(signal.sellVolume)),
      renderMonitoringDetailMetric("호가 불균형", formatSignalNumber(signal.bidAskImbalance, "%")),
      renderMonitoringDetailMetric("가격 변화", formatSignalNumber(signal.priceChangeRate, "%")),
      renderMonitoringDetailMetric("20일선", formatSignalNumber(signal.ma20, "")),
      renderMonitoringDetailMetric("60일선", formatSignalNumber(signal.ma60, "")),
      renderMonitoringDetailMetric("외국인 순매수", formatSignalVolume(signal.foreignNet)),
      renderMonitoringDetailMetric("기관 순매수", formatSignalVolume(signal.institutionNet)),
      renderMonitoringDetailMetric("개인 순매수", formatSignalVolume(signal.individualNet)),
      '</div>',
      '</div>'
    ].join("");
  }

  function renderMonitoringDetailReasons(item) {
    var reasons = Array.isArray(item.reasons) ? item.reasons : [];
    return [
      '<div class="monitoring-detail-block">',
      '<div class="flow-title">',
      '<div>',
      '<strong>판단 근거</strong>',
      '<span>현재 라벨을 만든 데이터 해석입니다.</span>',
      '</div>',
      '</div>',
      '<div class="monitoring-detail-reasons">',
      reasons.length ? reasons.map(function (reason) {
        return '<p>' + escapeHtml(reason) + '</p>';
      }).join("") : '<p>표시할 판단 근거가 없습니다.</p>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderMonitoringDetailTriggers(item) {
    var triggers = Array.isArray(item.triggers) ? item.triggers : [];
    if (!triggers.length) return "";
    return [
      '<div class="trigger-list monitoring-detail-triggers">',
      triggers.map(function (trigger) {
        return '<span>' + escapeHtml(trigger) + '</span>';
      }).join(""),
      '</div>'
    ].join("");
  }

  function renderMonitoringInstrumentDetail(item) {
    var sourceClass = item.source === "holding" ? "holding" : "watchlist";
    var valuationText = item.valuation && item.valuation.status ? item.valuation.status : "가정 대기";
    var pnlText = item.source === "holding"
      ? signedMoney(item.profitLoss, item.currency) + " · " + signedPct(item.profitLossRate)
      : "-";
    var displayName = stockDisplayName(item.symbol, item);
    return [
      '<div class="monitoring-detail-content">',
      '<div class="monitoring-detail-head">',
      '<div>',
      '<p class="label">Instrument Detail</p>',
      '<h2>' + escapeHtml(displayName) + '</h2>',
      '<span>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || "-"), item.sector || "-"])) + '</span>',
      '</div>',
      '<div class="monitoring-detail-badges">',
      '<span class="source-chip ' + escapeHtml(sourceClass) + '">' + escapeHtml(sourceLabel(item.source)) + '</span>',
      '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.action || "관망") + '</span>',
      '</div>',
      '</div>',
      '<div class="monitoring-detail-metric-grid">',
      renderMonitoringDetailMetric("현재가", monitoringDetailCurrency(item.currentPrice, item.currency)),
      renderMonitoringDetailMetric("평가액", monitoringDetailCurrency(item.marketValue, item.currency)),
      renderMonitoringDetailMetric("평단", monitoringDetailCurrency(item.averagePrice, item.currency)),
      renderMonitoringDetailMetric("수량", monitoringDetailQuantity(item.quantity)),
      renderMonitoringDetailMetric("손익", pnlText, Number(item.profitLoss || 0) < 0 ? "sell" : "buy"),
      renderMonitoringDetailMetric("매수 점수", item.hasData ? Math.round(item.buyScore) + "점" : "-", "buy"),
      renderMonitoringDetailMetric("매도 점수", item.hasData ? Math.round(item.sellScore) + "점" : "-", "sell"),
      renderMonitoringDetailMetric("매수 체결비중", item.hasData ? pct(item.buyShare) : "-"),
      renderMonitoringDetailMetric("가치 판단", valuationText),
      '</div>',
      renderMonitoringDetailSignalGrid(item),
      renderMonitoringDetailReasons(item),
      renderMonitoringDetailTriggers(item),
      '</div>'
    ].join("");
  }

  function renderMonitoringAlertRelatedInstrument(item) {
    if (!item) return "";
    return [
      '<div class="monitoring-detail-block">',
      '<div class="flow-title">',
      '<div>',
      '<strong>관련 종목 신호</strong>',
      '<span>알림이 가리키는 종목의 현재 매수·매도 판단입니다.</span>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.action || "관망") + '</span>',
      '</div>',
      '<div class="monitoring-detail-metric-grid compact">',
      renderMonitoringDetailMetric("현재가", monitoringDetailCurrency(item.currentPrice, item.currency)),
      renderMonitoringDetailMetric("매수 점수", item.hasData ? Math.round(item.buyScore) + "점" : "-", "buy"),
      renderMonitoringDetailMetric("매도 점수", item.hasData ? Math.round(item.sellScore) + "점" : "-", "sell"),
      renderMonitoringDetailMetric("매수 체결비중", item.hasData ? pct(item.buyShare) : "-"),
      '</div>',
      '<div class="monitoring-detail-reasons compact">',
      (item.reasons || []).map(function (reason) {
        return '<p>' + escapeHtml(reason) + '</p>';
      }).join(""),
      '</div>',
      '</div>'
    ].join("");
  }

  function renderMonitoringAlertDetail(alert, relatedItem) {
    var severity = alert.severity || "info";
    var displaySymbol = alert.symbol ? stockDisplayName(alert.symbol, relatedItem || alert) : "";
    var title = textWithDisplaySymbol(alert.title || "알림 상세", alert.symbol, relatedItem || alert);
    var message = textWithDisplaySymbol(alert.message || "세부 메시지가 없습니다.", alert.symbol, relatedItem || alert);
    return [
      '<div class="monitoring-detail-content">',
      '<div class="monitoring-detail-head">',
      '<div>',
      '<p class="label">Alert Detail</p>',
      '<h2>' + escapeHtml(title) + '</h2>',
      '<span>' + escapeHtml([displaySymbol || "", alert.source || "", alertRuleLabel(alert.rule)].filter(Boolean).join(" · ")) + '</span>',
      '</div>',
      '<div class="monitoring-detail-badges">',
      '<span class="alert-severity ' + escapeHtml(severity) + '">' + escapeHtml(alertSeverityLabel(severity)) + '</span>',
      '<span class="tone-chip hold">' + escapeHtml(alertRuleLabel(alert.rule)) + '</span>',
      '</div>',
      '</div>',
      '<div class="monitoring-detail-message">',
      '<strong>알림 메시지</strong>',
      '<p>' + escapeHtml(message) + '</p>',
      '</div>',
      '<div class="monitoring-detail-metric-grid">',
      renderMonitoringDetailMetric("종목", displaySymbol || "-"),
      renderMonitoringDetailMetric("출처", alert.source || "-"),
      renderMonitoringDetailMetric("현재", alert.value || "-"),
      renderMonitoringDetailMetric("기준", alert.threshold || "-"),
      renderMonitoringDetailMetric("심각도", alertSeverityLabel(severity)),
      renderMonitoringDetailMetric("규칙", alertRuleLabel(alert.rule)),
      '</div>',
      renderMonitoringAlertRelatedInstrument(relatedItem),
      '</div>'
    ].join("");
  }

  function renderMonitoringDetailEmpty() {
    return [
      '<div class="monitoring-detail-content">',
      '<div class="monitoring-detail-head">',
      '<div>',
      '<p class="label">Detail</p>',
      '<h2>상세 데이터를 찾지 못했습니다</h2>',
      '<span>스냅샷이 갱신되었거나 선택 항목이 사라졌습니다.</span>',
      '</div>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderMonitoringDetailOverlay(snapshot) {
    var selection = state.monitoringDetail || {};
    if (!selection.type) return "";
    var content = "";
    if (selection.type === "instrument") {
      var item = monitoringSignalItemBySymbol(snapshot, selection.symbol);
      content = item ? renderMonitoringInstrumentDetail(item) : renderMonitoringDetailEmpty();
    } else if (selection.type === "alert") {
      var alert = monitoringAlertByIndex(snapshot, selection.index);
      var relatedItem = alert && alert.symbol ? monitoringSignalItemBySymbol(snapshot, alert.symbol) : null;
      content = alert ? renderMonitoringAlertDetail(alert, relatedItem) : renderMonitoringDetailEmpty();
    } else {
      content = renderMonitoringDetailEmpty();
    }
    return [
      '<div class="monitoring-detail-backdrop" data-monitoring-detail-close>',
      '<aside class="monitoring-detail-drawer" role="dialog" aria-modal="true" aria-label="모니터링 상세">',
      '<div class="monitoring-detail-toolbar">',
      '<span>상세 보기</span>',
      '<button class="icon-button" type="button" data-monitoring-detail-close aria-label="상세 닫기">&times;</button>',
      '</div>',
      content,
      '</aside>',
      '</div>'
    ].join("");
  }

  function marketLabel(market) {
    var key = String(market || "").toUpperCase();
    if (key === "KOSPI") return "코스피";
    if (key === "KOSDAQ") return "코스닥";
    if (key === "NASDAQ") return "나스닥";
    if (key === "US") return "미국";
    if (key === "KR") return "한국";
    return key || "-";
  }

  function freshnessLabel(item) {
    if (!item || !item.lastSeenAt) return "초기 데이터";
    return (item.stale ? "갱신 필요" : "신선") + " · " + formatClock(item.lastSeenAt);
  }

  function renderSymbolUniversePanel(options) {
    var full = Boolean(options && options.full);
    var universe = state.symbolUniverse || {};
    var summary = universe.summary || {};
    var items = universe.items || [];
    var markets = summary.markets || [];
    var sources = summary.sources || [];
    var marketData = summary.marketData || {};
    var limit = Number(universe.limit || state.symbolUniverseLimit || 80);
    var offset = Number(universe.offset || state.symbolUniverseOffset || 0);
    var resultTotal = Number(universe.resultTotal || 0);
    if (!resultTotal) resultTotal = state.symbolUniverseQuery || state.symbolUniverseMarket ? items.length : Number(summary.total || items.length || 0);
    var visibleFrom = resultTotal && items.length ? offset + 1 : 0;
    var visibleTo = resultTotal ? Math.min(offset + items.length, resultTotal) : items.length;
    var hasPrev = offset > 0;
    var hasNext = Boolean(universe.hasMore || (resultTotal && offset + items.length < resultTotal));
    var renderedItems = full ? items : items.slice(0, 12);
    return [
      '<article class="panel symbol-universe-panel' + (full ? " symbol-universe-full" : "") + '">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Symbol Universe</p>',
      '<h2>' + escapeHtml(full ? "전체 종목 정보" : "전체 종목 카탈로그") + '</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(summary.total || items.length || 0) + '</span>',
      '</div>',
      '<div class="symbol-summary-grid">',
      (markets.length ? markets.map(renderSymbolMarketSummary).join("") : '<p class="subtle">아직 저장된 전체 종목 목록이 없습니다.</p>') + renderSymbolMarketDataSummary(marketData),
      '</div>',
      full && sources.length ? '<div class="symbol-source-grid">' + sources.map(renderSymbolSourceSummary).join("") + '</div>' : '',
      '<form class="symbol-filter-form ' + (full ? "full" : "compact") + '" data-symbol-search-form>',
      '<label>',
      '<span>시장</span>',
      '<select name="market" data-symbol-market>',
      '<option value="">전체 시장</option>',
      ["KOSPI", "KOSDAQ", "NASDAQ"].map(function (market) {
        return '<option value="' + escapeHtml(market) + '"' + (state.symbolUniverseMarket === market ? " selected" : "") + '>' + escapeHtml(marketLabel(market)) + '</option>';
      }).join(""),
      '</select>',
      '</label>',
      '<label>',
      '<span>검색어</span>',
      '<input name="query" data-symbol-query placeholder="회사명 검색" value="' + escapeHtml(state.symbolUniverseQuery || "") + '" autocomplete="off" />',
      '</label>',
      full ? '<label><span>표시 수</span><select name="limit" data-symbol-limit>' + [80, 200, 500].map(function (value) {
        return '<option value="' + value + '"' + (Number(state.symbolUniverseLimit || 80) === value ? " selected" : "") + '>' + value + '개</option>';
      }).join("") + '</select></label>' : '',
      full ? '<label><span>추가 대상</span><select name="watchAccount" data-symbol-add-account>' + renderWatchAccountSelectOptions() + '</select></label>' : '',
      '<button class="text-button primary">검색</button>',
      '<button class="text-button" type="button" data-action="refresh-symbol-universe">' + escapeHtml(state.symbolUniverseRefreshing ? "갱신 중" : "목록 갱신") + '</button>',
      '</form>',
      state.symbolUniverseError ? '<p class="form-error">' + escapeHtml(state.symbolUniverseError) + '</p>' : '',
      '<p class="symbol-universe-note subtle">코스피·코스닥은 KRX KIND, 나스닥은 Nasdaq Trader 심볼 디렉터리를 운영 DB에 저장합니다. 원천 호출이 실패해도 마지막 성공 목록을 계속 사용합니다.</p>',
      full ? '<div class="symbol-pager"><span>' + escapeHtml(resultTotal ? visibleFrom + "-" + visibleTo + " / " + resultTotal + "개 표시" : "표시할 종목 없음") + '</span><div><button class="mini-button" data-symbol-page="prev"' + (hasPrev ? "" : " disabled") + '>이전</button><button class="mini-button" data-symbol-page="next"' + (hasNext ? "" : " disabled") + '>다음</button></div></div>' : '',
      full ? renderSymbolBulkActionBar(renderedItems) : '',
      '<div class="symbol-result-list">',
      state.symbolUniverseLoading ? renderEmptyState({
        tone: "watch",
        label: "Catalog",
        title: "종목 카탈로그를 갱신하고 있습니다",
        description: "마지막 성공 목록은 유지하고, 검색 조건에 맞는 결과만 백그라운드로 다시 읽습니다.",
        meta: [marketLabel(state.symbolUniverseMarket || "전체"), String(limit) + "개 단위"]
      }) : (renderedItems.length ? renderedItems.map(renderSymbolUniverseRow).join("") : renderEmptyState({
        tone: "muted",
        label: "Catalog",
        title: "검색 조건에 맞는 종목이 없습니다",
        description: "시장 필터와 검색어를 줄이거나 목록 갱신을 실행해 최신 카탈로그를 다시 불러오세요.",
        meta: [marketLabel(state.symbolUniverseMarket || "전체"), state.symbolUniverseQuery || "검색어 없음"]
      })),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSymbolMarketSummary(market) {
    return [
      '<div class="symbol-summary-metric">',
      '<span>' + escapeHtml(marketLabel(market.market)) + '</span>',
      '<strong>' + escapeHtml(market.count || 0) + '</strong>',
      '<em>' + escapeHtml(freshnessLabel(market)) + '</em>',
      '</div>'
    ].join("");
  }

  function renderSymbolMarketDataSummary(summary) {
    if (!summary || !summary.count) return "";
    return [
      '<div class="symbol-summary-metric">',
      '<span>수집 시세</span>',
      '<strong>' + escapeHtml(summary.count || 0) + '</strong>',
      '<em>' + escapeHtml(summary.latestUpdatedAt ? formatClock(summary.latestUpdatedAt) : "수집 대기") + '</em>',
      '</div>'
    ].join("");
  }

  function renderSymbolSourceSummary(source) {
    var ok = String(source.status || "").toLowerCase() === "ok";
    return [
      '<div class="symbol-source-status ' + (ok ? "ok" : "warn") + '">',
      '<span>' + escapeHtml(marketLabel(source.market)) + ' API</span>',
      '<strong>' + escapeHtml(source.status || "-") + '</strong>',
      '<em>' + escapeHtml(source.lastSuccessAt ? formatClock(source.lastSuccessAt) : "성공 기록 없음") + '</em>',
      '</div>'
    ].join("");
  }

  function renderWatchAccountSelectOptions() {
    var accounts = state.serviceAccounts || [];
    return accounts.length ? accounts.map(function (account) {
      var id = accountIdOf(account);
      return '<option value="' + escapeHtml(id) + '"' + (id === state.activeWatchAccountId ? " selected" : "") + '>' + escapeHtml(account.label || id) + '</option>';
    }).join("") : '<option value="">기본 관심목록</option>';
  }

  function visibleSymbolUniverseSymbols(items) {
    var seen = {};
    var symbols = [];
    (items || []).forEach(function (item) {
      var symbol = String(item.symbol || "").toUpperCase();
      if (!symbol || seen[symbol]) return;
      seen[symbol] = true;
      symbols.push(symbol);
    });
    return symbols;
  }

  function renderSymbolBulkActionBar(items) {
    var symbols = visibleSymbolUniverseSymbols(items);
    var registered = preferredWatchlistSymbols();
    var missing = symbols.filter(function (symbol) {
      return registered.indexOf(symbol) < 0;
    });
    var account = activeWatchAccount();
    return [
      '<div class="symbol-bulk-bar">',
      '<div>',
      '<strong>' + escapeHtml(account ? watchlistAccountLabel(account) : "기본 관심목록") + '</strong>',
      '<span>' + escapeHtml("현재 페이지 " + symbols.length + "개 중 " + missing.length + "개 추가 가능") + '</span>',
      '</div>',
      '<button class="text-button primary" type="button" data-action="add-visible-symbols"' + (missing.length ? "" : " disabled") + '>페이지 종목 일괄 추가</button>',
      '</div>'
    ].join("");
  }

  function addVisibleSymbolsToPreferredWatchlist() {
    var symbols = visibleSymbolUniverseSymbols(state.symbolUniverse.items || []);
    var registered = preferredWatchlistSymbols();
    var missing = symbols.filter(function (symbol) {
      return registered.indexOf(symbol) < 0;
    });
    if (!missing.length) {
      showSnackbar("현재 페이지 종목은 이미 관심목록에 있습니다.");
      return Promise.resolve();
    }
    var account = activeWatchAccount();
    return account
      ? saveAccountWatchlistSymbols(accountIdOf(account), registered.concat(missing))
      : saveWatchlistSymbols(registered.concat(missing));
  }

  function renderSymbolUniverseRow(item) {
    var symbol = String(item.symbol || "").toUpperCase();
    var account = activeWatchAccount();
    var already = preferredWatchlistSymbols().indexOf(symbol) >= 0;
    var targetText = account ? watchlistAccountLabel(account) : "기본 관심목록";
    var hasPrice = Boolean(item.currentPrice);
    var priceText = hasPrice ? formatCurrency(item.currentPrice, item.currency) : "시세 수집 대기";
    var quality = String(item.dataQuality || "").toLowerCase();
    var qualityLabel = quality === "actual" ? "실제 데이터" : (quality === "cached" ? "저장 데이터" : "");
    var dataLine = hasPrice
      ? [qualityLabel, item.quoteSource || "", item.marketDataUpdatedAt ? formatClock(item.marketDataUpdatedAt) : ""].filter(Boolean).join(" · ")
      : (item.quoteStatus || "추천용 시세 수집 순서를 기다리는 중");
    return [
      '<div class="symbol-result-row">',
      '<div class="symbol-result-main">',
      '<div class="symbol-result-title">',
      '<strong>' + escapeHtml(stockDisplayName(symbol, item)) + '</strong>',
      '<span>' + escapeHtml(stockDisplayMeta(item, [marketLabel(item.market || item.exchange), item.sector || item.assetType || "STOCK"])) + '</span>',
      '</div>',
      '<div class="symbol-result-meta">',
      '<span>' + escapeHtml(marketLabel(item.market || item.exchange)) + '</span>',
      '<span>' + escapeHtml(item.assetType || "STOCK") + '</span>',
      '<span>' + escapeHtml(item.currency || "-") + '</span>',
      '<span>' + escapeHtml(item.stale ? "갱신 필요" : "신선") + '</span>',
      '</div>',
      '<p>' + escapeHtml(item.source || "-") + ' · ' + escapeHtml(item.lastSeenAt ? formatClock(item.lastSeenAt) : "초기 데이터") + '</p>',
      '<p>' + escapeHtml(dataLine) + '</p>',
      '</div>',
      '<div class="symbol-result-side">',
      '<strong>' + escapeHtml(priceText) + '</strong>',
      '<span>' + escapeHtml((item.sector || "섹터 미분류") + " · " + targetText) + '</span>',
      '<button class="mini-button subtle" data-symbol-add-watch="' + escapeHtml(symbol) + '"' + (already ? " disabled" : "") + '>' + (already ? "등록됨" : "추가") + '</button>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderWatchAlertMeta(item) {
    var rules = alertRules();
    var hasPrice = Boolean(item.currentPrice);
    var quoteRule = enabledAlertRule(rules, "watchlistQuote");
    var pendingRule = enabledAlertRule(rules, "watchlistQuotePending");
    var quality = String(item.dataQuality || "").toLowerCase();
    var qualityLabel = quality === "actual" ? "실제 데이터" : (quality === "cached" ? "저장 데이터" : (quality === "mock" ? "mock 데이터" : ""));
    var chips = [
      '<span class="chip ' + (hasPrice ? "ok" : "missing") + '">' + escapeHtml(item.quoteStatus || (hasPrice ? "시세 수집" : "시세 대기")) + '</span>',
      '<span class="chip ' + (quoteRule ? "ok" : "missing") + '">시세 알림 ' + escapeHtml(quoteRule ? "ON" : "OFF") + '</span>'
    ];
    if (qualityLabel) {
      chips.push('<span class="chip ' + (quality === "actual" ? "ok" : "") + '">' + escapeHtml(qualityLabel) + '</span>');
    }
    if (item.quoteSource) {
      chips.push('<span class="chip">' + escapeHtml(item.quoteSource) + '</span>');
    }
    if (!hasPrice) {
      chips.push('<span class="chip ' + (pendingRule ? "ok" : "missing") + '">대기 알림 ' + escapeHtml(pendingRule ? "ON" : "OFF") + '</span>');
    }
    return [
      '<div class="watch-row-meta">',
      '<div class="chip-row">' + chips.join("") + '</div>',
      item.quoteMessage ? '<p>' + escapeHtml(item.quoteMessage) + '</p>' : '',
      '</div>'
    ].join("");
  }

  function renderFeedPage(snapshot) {
    var settingsMode = activePageMode("feed") === "settings";
    var body = settingsMode ? [
      '<section class="admin-grid feed-view feed-view-settings">',
      renderFeedSettingsPanel(),
      '</section>'
    ].join("") : [
      '<section class="admin-grid feed-view feed-view-results">',
      '<div class="feed-workbench">',
      '<div class="feed-primary-column">',
      renderFeedOverviewPanel(),
      renderResearchEvidencePanel(),
      '</div>',
      '<aside class="feed-side-column">',
      renderFeedPipelinePanel(),
      renderFeedQualityPanel(),
      renderFeedChannelPanel(),
      '</aside>',
      '</div>',
      '</section>'
    ].join("");
    return renderManagedPage("feed", snapshot, [
      body
    ].join(""));
  }

  function feedSourceTone(enabled, ready) {
    if (!enabled) return "hold";
    return ready === false ? "caution" : "watch";
  }

  function feedSourceChannels() {
    var newsArchiveEnabled = settingEnabled("newsCollectionEnabled");
    var graphStoreMode = settingValue("ontologyGraphStoreMode") || defaultSettings.ontologyGraphStoreMode || "neo4j";
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
        ready: graphStoreMode !== "typedb" || settingEnabled("ontologyTypeDbEnabled"),
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
    var latest = feedFreshness(summary.latestSeenAt);
    var channels = feedSourceChannels();
    var activeChannels = channels.filter(function (channel) { return channel.enabled; }).length;
    var readyChannels = channels.filter(function (channel) { return channel.enabled && channel.ready !== false; }).length;
    var graphStoreMode = settingValue("ontologyGraphStoreMode") || defaultSettings.ontologyGraphStoreMode || "neo4j";
    var graphStoreLabel = graphStoreMode === "typedb" ? "TypeDB" : (graphStoreMode === "dual" ? "Neo4j + TypeDB" : "Neo4j");
    return [
      { step: "01", title: "원천 수집", tone: activeChannels ? "watch" : "hold", value: activeChannels + "/" + channels.length, detail: "사용 중인 수집 채널" },
      { step: "02", title: "준비도 확인", tone: readyChannels === activeChannels ? "watch" : "caution", value: readyChannels + "/" + Math.max(activeChannels, 1), detail: "키·연결·무키 채널 확인" },
      { step: "03", title: "근거 저장", tone: Number(summary.total || 0) ? latest.tone : "caution", value: Number(summary.total || 0) + "건", detail: "최근 저장 " + latest.label },
      { step: "04", title: "관계 추론", tone: settingEnabled("ontologyReasoningEnabled") ? "watch" : "hold", value: graphStoreLabel, detail: "배치 " + (settingValue("ontologyReasoningBatchSize") || defaultSettings.ontologyReasoningBatchSize || "20") },
      { step: "05", title: "알림 후보", tone: settingEnabled("materialityGateEnabled") ? "watch" : "hold", value: (settingValue("materialityMinimumScore") || defaultSettings.materialityMinimumScore || "65") + "점", detail: "중요도 게이트 기준" }
    ];
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
      '<p class="label">Feed Command</p>',
      '<h2>피드 운영 대시보드</h2>',
      '</div>',
      '<span class="tone-chip ' + escapeHtml(warningChannels ? "caution" : "watch") + '">' + escapeHtml(warningChannels ? "확인 필요" : "운영 가능") + '</span>',
      '</div>',
      '<div class="feed-command-body">',
      '<div class="feed-command-metrics">',
      renderFeedCommandMetric("저장 근거", Number(summary.total || 0) + "건", "최근 " + latest.label, Number(summary.total || 0) ? latest.tone : "caution"),
      renderFeedCommandMetric("수집 채널", activeChannels + "/" + channels.length, warningChannels ? warningChannels + "개 키 확인" : "준비 완료", warningChannels ? "caution" : "watch"),
      renderFeedCommandMetric("근거 종류", kinds.length + "종", kinds.slice(0, 3).map(function (entry) { return researchEvidenceKindLabel(entry.name); }).join(" · ") || "대기", kinds.length ? "watch" : "hold"),
      renderFeedCommandMetric("게이트", (settingValue("materialityMinimumScore") || defaultSettings.materialityMinimumScore || "65") + "점", "관계 알림 기준", settingEnabled("materialityGateEnabled") ? "watch" : "hold"),
      '</div>',
      '<div class="feed-flow-map">',
      feedPipelineStages().map(renderFeedFlowNode).join(""),
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderFeedCommandMetric(label, value, detail, tone) {
    return [
      '<span class="feed-command-metric ' + escapeHtml(tone || "hold") + '">',
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

  function renderFeedPipelinePanel() {
    return [
      '<article class="panel feed-pipeline-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Data Flow</p>',
      '<h2>수집·판단 흐름</h2>',
      '</div>',
      '</div>',
      '<div class="feed-pipeline-list">',
      feedPipelineStages().map(function (stage) {
        return [
          '<div class="feed-pipeline-row ' + escapeHtml(stage.tone || "hold") + '">',
          '<span>' + escapeHtml(stage.step) + '</span>',
          '<div>',
          '<strong>' + escapeHtml(stage.title) + '</strong>',
          '<em>' + escapeHtml(stage.detail || "") + '</em>',
          '</div>',
          '<b>' + escapeHtml(stage.value || "-") + '</b>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
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
      '<span class="metric">' + escapeHtml(feedSourceChannels().filter(function (channel) { return channel.enabled; }).length) + '</span>',
      '</div>',
      '<div class="feed-channel-grid">',
      feedSourceChannels().map(function (channel) {
        return [
          '<div class="feed-channel-row ' + escapeHtml(channel.tone || "hold") + '">',
          '<div>',
          '<span class="tone-chip ' + escapeHtml(channel.tone || "hold") + '">' + escapeHtml(channel.enabled ? (channel.ready === false ? "키 확인" : "사용") : "중지") + '</span>',
          '<strong>' + escapeHtml(channel.label) + '</strong>',
          '<em>' + escapeHtml(channel.route) + '</em>',
          '</div>',
          '<b>' + escapeHtml(channel.cadence || "-") + '</b>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderFeedSettingsPanel() {
    var archiveScope = (settingValue("newsCollectionMaxSymbols") || defaultSettings.newsCollectionMaxSymbols || "40") + "종목 · "
      + (settingValue("newsCollectionLookbackMinutes") || defaultSettings.newsCollectionLookbackMinutes || "180") + "분";
    var graphStoreMode = settingValue("ontologyGraphStoreMode") || defaultSettings.ontologyGraphStoreMode || "neo4j";
    var graphStoreLabel = graphStoreMode === "typedb" ? "TypeDB" : (graphStoreMode === "dual" ? "Neo4j+TypeDB" : (settingEnabled("ontologyNeo4jEnabled") ? "Neo4j" : "로컬"));
    var reasoningScope = graphStoreLabel + " · "
      + (settingValue("ontologyReasoningIntervalSeconds") || defaultSettings.ontologyReasoningIntervalSeconds || "10") + "초";
    return [
      '<article class="panel feed-settings-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Feed Operations</p>',
      '<h2>피드 수집 설정</h2>',
      '</div>',
      '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
      '</div>',
      '<div class="settings-body feed-settings-body">',
      '<div class="settings-api-grid feed-settings-summary">',
      renderSettingsApiCard("원천 준비도", "뉴스·공시·SEC", [
        configuredChip("KIS 수급", settingEnabled("kisMarketSignalsEnabled"), configuredCount(["kisAppKey", "kisAppSecret"]) + "/2"),
        configuredChip("뉴스", settingEnabled("externalNewsEnabled"), newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider)),
        configuredChip("OpenDART", settingEnabled("externalDartEnabled"), isConfiguredSetting("opendartApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("SEC", settingEnabled("externalSecEnabled"), "무키"),
        configuredChip("Alpha", settingEnabled("externalAlphaEnabled"), isConfiguredSetting("alphaVantageApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("FRED", settingEnabled("externalFredEnabled"), isConfiguredSetting("fredApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("CoinGecko", settingEnabled("externalCoinGeckoEnabled"), isConfiguredSetting("coingeckoApiKey") ? "키 저장됨" : "키 없음")
      ]),
      renderSettingsApiCard("아카이브 범위", archiveScope, [
        configuredChip("관심", settingValue("newsCollectionIncludeWatchlist") !== "0", "포함"),
        configuredChip("보유", settingValue("newsCollectionIncludeHoldings") !== "0", "포함"),
        configuredChip("관련성", true, (settingValue("newsCollectionMinRelevanceScore") || defaultSettings.newsCollectionMinRelevanceScore || "35") + "점")
      ]),
      renderSettingsApiCard("추론 흐름", reasoningScope, [
        configuredChip("추론", settingEnabled("ontologyReasoningEnabled"), settingValue("ontologyReasoningBatchSize") || defaultSettings.ontologyReasoningBatchSize || "20"),
        configuredChip("게이트", settingEnabled("materialityGateEnabled"), (settingValue("materialityMinimumScore") || defaultSettings.materialityMinimumScore || "65") + "점"),
        configuredChip("뉴스 기준", true, (settingValue("newsMaterialityMinimumScore") || defaultSettings.newsMaterialityMinimumScore || "65") + "점")
      ]),
      '</div>',
      '<div class="feed-settings-sections">',
      renderSettingsGroup("장중 시세·수급", "KIS 장중 수급과 호출량을 조정합니다.", [
        renderSettingSelect("kisMarketSignalsEnabled", "KIS 수급 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("kisMarketSignalMaxSymbols", "KIS 수급 종목 수", "number", "20"),
        renderSettingField("kisMarketSignalCacheMinutes", "KIS 수급 캐시(분)", "number", "3"),
        renderSettingField("kisMarketSignalGapSeconds", "KIS 호출 간격(초)", "number", "0.35"),
        renderSettingSelect("kisMarketSignalPreferLiveDuringMarketHours", "장중 KIS live 우선", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("kisMarketSignalLiveRefreshSeconds", "장중 live 최소 간격(초)", "number", "60")
      ].join(""), "market feed-compact"),
      renderSettingsGroup("뉴스 헤드라인", "빠른 외부 뉴스 조회 범위를 관리합니다.", [
        renderSettingSelect("externalNewsEnabled", "뉴스 헤드라인 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingSelect("externalNewsProvider", "뉴스 공급자", [
          { value: "auto", label: "자동" },
          { value: "gdelt", label: "GDELT" },
          { value: "alpha_vantage", label: "Alpha Vantage" }
        ]),
        renderSettingField("externalNewsMaxSymbols", "뉴스 조회 종목 수", "number", "3"),
        renderSettingField("externalNewsLookbackHours", "뉴스 조회 기간(시간)", "number", "48"),
        renderSettingField("externalResearchEvidenceMaxItems", "AI 전달 최신 근거 수", "number", "8")
      ].join(""), "research feed-compact"),
      renderSettingsGroup("뉴스 아카이브", "저장형 뉴스 수집 워커의 대상과 속도입니다.", [
        renderSettingSelect("newsCollectionEnabled", "뉴스 아카이브 실시간 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("newsCollectionIntervalSeconds", "뉴스 수집 주기(초)", "number", "60"),
        renderSettingField("newsCollectionMaxSymbols", "뉴스 수집 종목 수", "number", "40"),
        renderSettingField("newsCollectionLookbackMinutes", "뉴스 조회 기간(분)", "number", "180"),
        renderSettingField("newsCollectionPerSymbolLimit", "종목별 저장 기사 수", "number", "8"),
        renderSettingField("newsCollectionProviders", "뉴스 수집 채널", "text", "google_rss_kr,google_rss_us,gdelt"),
        renderSettingField("newsCollectionMinRelevanceScore", "뉴스 관련성 최소 점수", "number", "35"),
        renderSettingSelect("newsCollectionIncludeWatchlist", "관심종목 뉴스 포함", [
          { value: "1", label: "포함" },
          { value: "0", label: "제외" }
        ]),
        renderSettingSelect("newsCollectionIncludeHoldings", "보유종목 뉴스 포함", [
          { value: "1", label: "포함" },
          { value: "0", label: "제외" }
        ]),
        renderSettingField("newsCollectionRateLimitSeconds", "뉴스 호출 간격(초)", "number", "0.25")
      ].join(""), "research feed-wide"),
      renderSettingsGroup("그래프 추론", "수집 데이터가 그래프 저장소 관계 추론으로 넘어가는 경로입니다.", [
        renderSettingSelect("ontologyReasoningEnabled", "데이터 변경 추론", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingSelect("ontologyGraphStoreMode", "그래프 저장소 모드", [
          { value: "neo4j", label: "Neo4j 유지" },
          { value: "dual", label: "Neo4j + TypeDB 미러" },
          { value: "typedb", label: "TypeDB 단독" }
        ]),
        renderSettingSelect("ontologyNeo4jEnabled", "Neo4j 그래프 저장소", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("neo4jUri", "Neo4j URI", "text", "http://127.0.0.1:7474"),
        renderSettingField("neo4jUser", "Neo4j 사용자", "text", "neo4j"),
        renderSettingField("neo4jDatabase", "Neo4j DB", "text", "neo4j"),
        renderSettingField("neo4jTimeoutSeconds", "Neo4j 타임아웃(초)", "number", "8"),
        renderSettingSelect("ontologyTypeDbEnabled", "TypeDB 그래프 저장소", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("typedbAddress", "TypeDB 주소", "text", "127.0.0.1:1729"),
        renderSettingField("typedbUser", "TypeDB 사용자", "text", "admin"),
        renderSettingField("typedbDatabase", "TypeDB DB", "text", "orbit_alpha_ontology"),
        renderSettingSelect("typedbTlsEnabled", "TypeDB TLS", [
          { value: "0", label: "사용 안 함" },
          { value: "1", label: "사용" }
        ]),
        renderSettingField("typedbTimeoutSeconds", "TypeDB 타임아웃(초)", "number", "20"),
        renderSettingField("ontologyReasoningIntervalSeconds", "추론 요청 확인 주기(초)", "number", "10"),
        renderSettingField("ontologyReasoningBatchSize", "추론 요청 배치", "number", "20")
      ].join(""), "gate feed-wide"),
      renderSettingsGroup("중요도 게이트", "시세·뉴스·거래량 변화가 알림 후보로 들어가는 기준입니다.", [
        renderSettingSelect("materialityGateEnabled", "중요 변경 게이트", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("materialityMinimumScore", "중요 변경 기본 기준", "number", "65"),
        renderSettingField("marketMaterialityMinimumScore", "시장 데이터 중요 기준", "number", "65"),
        renderSettingField("marketMaterialityPriceChangePct", "가격 중요 변화율(%)", "number", "0.6"),
        renderSettingField("marketMaterialityTrendDistancePct", "추세 중요 이격(%)", "number", "2"),
        renderSettingField("marketMaterialityVolumeRatio", "거래량 중요 배율", "number", "1.5"),
        renderSettingField("newsMaterialityMinimumScore", "뉴스 중요 기준", "number", "65")
      ].join(""), "gate feed-compact"),
      renderSettingsGroup("공시·외부 원천", "공시, 미장, 거시, 크립토 원천의 사용 여부입니다.", [
        renderSettingSelect("externalDartEnabled", "OpenDART 공시 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("externalDartLookbackDays", "공시 조회 기간(일)", "number", "14"),
        renderSettingSelect("externalSecEnabled", "SEC EDGAR 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("externalSecMaxSymbols", "SEC 조회 종목 수", "number", "3"),
        renderSettingSelect("externalAlphaEnabled", "Alpha Vantage 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingSelect("externalFredEnabled", "FRED 거시 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingSelect("externalCoinGeckoEnabled", "CoinGecko 크립토 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("externalApiFetchIntervalMinutes", "외부 API 캐시(분)", "number", "30")
      ].join(""), "external feed-compact"),
      renderSettingsGroup("긴 매핑값", "종목 코드, CIK, 거시·코인 목록처럼 긴 입력값입니다.", [
        renderSettingField("externalFredSeries", "FRED 지표", "text", "DGS10,DGS2,DFF"),
        renderSettingField("externalCryptoIds", "CoinGecko 코인 ID", "text", "bitcoin,ethereum"),
        '<label class="setting-field wide">',
        '<span class="setting-field-label">OpenDART 종목 매핑</span>',
        '<div class="form-control-shell"><textarea data-setting="externalDartCorpCodes" rows="3" autocomplete="off" placeholder="005930=00126380">' + escapeHtml(settingValue("externalDartCorpCodes") || defaultSettings.externalDartCorpCodes) + '</textarea></div>',
        '</label>',
        '<label class="setting-field wide">',
        '<span class="setting-field-label">SEC CIK 매핑</span>',
        '<div class="form-control-shell"><textarea data-setting="externalSecCompanyCiks" rows="3" autocomplete="off" placeholder="AAPL=0000320193">' + escapeHtml(settingValue("externalSecCompanyCiks") || defaultSettings.externalSecCompanyCiks) + '</textarea></div>',
        '</label>',
      ].join(""), "mapping feed-wide"),
      '</div>',
      renderSettingsSmartSavePanel(),
      '</div>',
      '</article>'
    ].join("");
  }

  function feedFreshness(value) {
    var time = feedTimeValue(value);
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
    var latest = feedFreshness(summary.latestSeenAt);
    var kisEnabled = settingEnabled("kisMarketSignalsEnabled");
    var newsEnabled = settingEnabled("externalNewsEnabled");
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
        value: newsEnabled ? newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider) : "중지",
        tone: newsEnabled ? "watch" : "hold",
        description: "관심·보유 종목별 외부 헤드라인을 수집해 research_evidence에 저장합니다."
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
      '<button class="text-button" data-action="refresh-research-evidence">' + (state.researchEvidenceLoading ? "조회 중" : "저장 근거 조회") + '</button>',
      '</div>',
      '</div>',
      '<div class="feed-quality-grid">',
      feedQualitySignals().map(renderFeedQualitySignal).join(""),
      '</div>',
      '<div class="theme-radar feed-quality-tags">',
      kinds.length ? kinds.slice(0, 8).map(function (entry) {
        return '<span>' + escapeHtml(researchEvidenceKindLabel(entry.name)) + ' <strong>' + escapeHtml(entry.count) + '</strong></span>';
      }).join("") : '<span>저장 근거 대기</span>',
      '</div>',
      state.researchEvidenceError ? '<p class="form-error">' + escapeHtml(state.researchEvidenceError) + '</p>' : '',
      '</article>'
    ].join("");
  }

  function renderFeedQualitySignal(item) {
    return [
      '<div class="feed-quality-card">',
      '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.value || "-") + '</span>',
      '<strong>' + escapeHtml(item.label || "-") + '</strong>',
      '<p>' + escapeHtml(item.description || "") + '</p>',
      '</div>'
    ].join("");
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
      renderResearchEvidenceFilters(),
      '<div class="research-evidence-list">',
      state.researchEvidenceLoading ? '<div class="panel skeleton"></div>' : '',
      state.researchEvidenceError ? '<p class="form-error">' + escapeHtml(state.researchEvidenceError) + '</p>' : '',
      (!state.researchEvidenceLoading && !state.researchEvidenceError && !items.length) ? '<p class="subtle">저장된 뉴스·공시·SEC 근거가 아직 없습니다. 외부 데이터 워커가 수집하면 이곳에 표시됩니다.</p>' : '',
      (!state.researchEvidenceLoading && items.length) ? items.map(renderResearchEvidenceItem).join("") : '',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderResearchEvidenceFilters() {
    var filters = state.researchEvidenceFilters || {};
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
        { value: "sec-filing", label: "SEC" },
        { value: "market-move", label: "가격 변동" }
      ].map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' + (String(filters.kind || "") === option.value ? " selected" : "") + '>' + escapeHtml(option.label) + '</option>';
      }).join(""),
      '</select>',
      '</label>',
      '<label class="setting-field">',
      '<span>조회 수</span>',
      '<select data-research-filter="limit">',
      ["30", "80", "150", "300"].map(function (value) {
        return '<option value="' + escapeHtml(value) + '"' + (String(filters.limit || "80") === value ? " selected" : "") + '>' + escapeHtml(value) + '건</option>';
      }).join(""),
      '</select>',
      '</label>',
      '<div class="settings-actions feed-filter-actions">',
      '<button class="text-button primary" type="submit">' + (state.researchEvidenceLoading ? "조회 중" : "조회") + '</button>',
      '</div>',
      '</form>'
    ].join("");
  }

  function renderResearchEvidenceItem(item) {
    var symbol = String(item.symbol || "").toUpperCase();
    var displayName = stockDisplayName(symbol, item.payload || item);
    var time = item.publishedAt || item.observedAt || "";
    var deleting = state.researchEvidenceDeleting === item.evidenceId;
    return [
      '<div class="research-evidence-item">',
      '<div class="research-evidence-main">',
      '<div class="research-evidence-meta">',
      '<span>' + escapeHtml(displayName) + (symbol && displayName !== symbol ? ' <em>' + escapeHtml(symbol) + '</em>' : '') + '</span>',
      '<span>' + escapeHtml(researchEvidenceKindLabel(item.kind)) + '</span>',
      '<span>' + escapeHtml(item.source || "-") + '</span>',
      '<span>' + escapeHtml(formatFeedTime(time) || "-") + '</span>',
      '</div>',
      '<h3>' + escapeHtml(item.title || "제목 없음") + '</h3>',
      '<p>' + escapeHtml(item.summary || "요약 없음") + '</p>',
      '<div class="research-evidence-metrics">',
      '<span>방향 <strong>' + escapeHtml(researchEvidencePolarityLabel(item.polarity)) + '</strong></span>',
      '<span>영향 <strong>' + escapeHtml(item.impactScore == null ? "-" : item.impactScore) + '</strong></span>',
      '<span>신뢰 <strong>' + escapeHtml(Math.round(Number(item.confidence || 0) * 100)) + '%</strong></span>',
      '</div>',
      '</div>',
      '<div class="research-evidence-actions">',
      item.url ? '<a class="open-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noreferrer" title="원문 열기">↗</a>' : '<span class="open-link muted">-</span>',
      '<button class="mini-button danger" type="button" data-research-delete="' + escapeHtml(item.evidenceId || "") + '"' + (deleting || item.evidenceId === "preview:005930:news" ? " disabled" : "") + '>' + (deleting ? "삭제 중" : "삭제") + '</button>',
      '</div>',
      '</div>'
    ].join("");
  }

  function settingValue(name) {
    return state.settings && state.settings[name] != null ? state.settings[name] : "";
  }

  function isConfiguredSetting(name) {
    return Boolean(state.serverConfigured && state.serverConfigured[name]);
  }

  function renderSettingField(name, label, type, placeholder, options) {
    options = options || {};
    var fieldPlaceholder = placeholder || "";
    if (options.preserveConfigured && isConfiguredSetting(name)) {
      fieldPlaceholder = "설정됨 - 새 값 입력 시 교체";
    }
    var configuredNote = options.preserveConfigured && isConfiguredSetting(name);
    return [
      '<label class="setting-field setting-field-' + escapeHtml(type || "text") + '">',
      '<span class="setting-field-label">' + escapeHtml(label) + '</span>',
      '<div class="form-control-shell">',
      '<input data-setting="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(settingValue(name)) + '" placeholder="' + escapeHtml(fieldPlaceholder) + '" autocomplete="off" />',
      '</div>',
      configuredNote ? '<em class="setting-field-note">저장됨</em>' : '',
      '</label>'
    ].join("");
  }

  function renderSettingsApiSummary() {
    return [
      '<div class="settings-api-grid">',
      renderSettingsApiCard("토스 API", settingValue("tossApiBaseUrl") || defaultSettings.tossApiBaseUrl, [
        configuredChip("Client ID", isConfiguredSetting("tossClientId")),
        configuredChip("Secret", isConfiguredSetting("tossClientSecret")),
        configuredChip("Account Seq", isConfiguredSetting("tossAccountSeq"), isConfiguredSetting("tossAccountSeq") ? "저장됨" : "선택")
      ]),
      renderSettingsApiCard("텔레그램", settingValue("notifyProvider") || "telegram", [
        configuredChip("Bot token", isConfiguredSetting("telegramBotToken")),
        configuredChip("Chat ID", isConfiguredSetting("telegramChatId"), isConfiguredSetting("telegramChatId") ? "저장됨" : ""),
        configuredChip("알림 링크", Boolean(settingValue("notifyLinkUrl")))
      ]),
      renderSettingsApiCard("외부 데이터", "가격·크립토·거시·공시", [
        configuredChip("Alpha", settingEnabled("externalAlphaEnabled"), isConfiguredSetting("alphaVantageApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("CoinGecko", settingEnabled("externalCoinGeckoEnabled"), isConfiguredSetting("coingeckoApiKey") ? "키 저장됨" : "키 없음"),
        configuredChip("FRED", settingEnabled("externalFredEnabled"), isConfiguredSetting("fredApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("OpenDART", settingEnabled("externalDartEnabled"), isConfiguredSetting("opendartApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("SEC", settingEnabled("externalSecEnabled"), "무키"),
        configuredChip("뉴스", settingEnabled("externalNewsEnabled"), newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider)),
        configuredChip("공시 AI", settingValue("dartDisclosureAiAnalysisEnabled") !== "0", settingValue("dartDisclosureAiUseCodex") === "0" ? "로컬" : "AI")
      ]),
      '</div>'
    ].join("");
  }

  function renderRuntimeSettingsSummary() {
    var externalEnabledCount = [
      "externalAlphaEnabled",
      "externalCoinGeckoEnabled",
      "externalFredEnabled",
      "externalSecEnabled",
      "externalDartEnabled",
      "externalNewsEnabled"
    ].filter(settingEnabled).length;
    return [
      '<div class="settings-api-grid">',
      renderSettingsApiCard("앱 환경", appThemeLabel(settingValue("appTheme") || defaultSettings.appTheme), [
        configuredChip("테마", true, appThemeLabel(settingValue("appTheme") || defaultSettings.appTheme)),
        configuredChip("종목 카탈로그", Boolean(settingValue("symbolUniverseMaxAgeHours")), (settingValue("symbolUniverseMaxAgeHours") || defaultSettings.symbolUniverseMaxAgeHours) + "시간")
      ]),
      renderSettingsApiCard("알림 전달", settingValue("notifyProvider") || "telegram", [
        configuredChip("Bot token", isConfiguredSetting("telegramBotToken")),
        configuredChip("Chat ID", isConfiguredSetting("telegramChatId"), isConfiguredSetting("telegramChatId") ? "저장됨" : ""),
        configuredChip("알림 링크", Boolean(settingValue("notifyLinkUrl")))
      ]),
      renderSettingsApiCard("외부 데이터", externalEnabledCount + "/6개 수집 사용", [
        configuredChip("Alpha", settingEnabled("externalAlphaEnabled"), isConfiguredSetting("alphaVantageApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("CoinGecko", settingEnabled("externalCoinGeckoEnabled"), isConfiguredSetting("coingeckoApiKey") ? "키 저장됨" : "키 없음"),
        configuredChip("FRED", settingEnabled("externalFredEnabled"), isConfiguredSetting("fredApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("OpenDART", settingEnabled("externalDartEnabled"), isConfiguredSetting("opendartApiKey") ? "키 저장됨" : "키 필요"),
        configuredChip("SEC", settingEnabled("externalSecEnabled"), "무키"),
        configuredChip("뉴스", settingEnabled("externalNewsEnabled"), newsProviderLabel(settingValue("externalNewsProvider") || defaultSettings.externalNewsProvider)),
        configuredChip("공시 AI", settingValue("dartDisclosureAiAnalysisEnabled") !== "0", settingValue("dartDisclosureAiUseCodex") === "0" ? "로컬" : "AI")
      ]),
      '</div>'
    ].join("");
  }

  function appThemeLabel(value) {
    var key = String(value || "light").toLowerCase();
    if (key === "dark") return "다크";
    if (key === "system") return "시스템 설정";
    return "라이트";
  }

  function renderSettingsApiCard(title, subtitle, chips) {
    return [
      '<div class="settings-api-row">',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<span>' + escapeHtml(subtitle || "-") + '</span>',
      '<div class="chip-row">' + chips.join("") + '</div>',
      '</div>'
    ].join("");
  }

  function renderSettingSelect(name, label, options) {
    var current = settingValue(name) || defaultSettings[name] || "";
    return [
      '<label class="setting-field setting-field-select">',
      '<span class="setting-field-label">' + escapeHtml(label) + '</span>',
      '<div class="form-control-shell select-shell">',
      '<select data-setting="' + escapeHtml(name) + '">',
      options.map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' + (String(current) === String(option.value) ? " selected" : "") + '>' + escapeHtml(option.label) + '</option>';
      }).join(""),
      '</select>',
      '</div>',
      '</label>'
    ].join("");
  }

  function settingsSaveDisabledAttr() {
    return state.serverSettingsLocked || state.settingsSaving || !settingsHasPendingChanges() ? ' disabled' : '';
  }

  function settingsHasPendingChanges() {
    return !state.settingsSaved || Boolean(state.serverSettingsError);
  }

  function settingsSaveButtonLabel() {
    if (state.settingsSaving) return "저장 중";
    if (settingsHasPendingChanges()) return state.serverSettingsError ? "다시 저장" : "변경 저장";
    return "저장됨";
  }

  function settingsSaveButtonClass() {
    return settingsHasPendingChanges() || state.settingsSaving ? "text-button primary" : "text-button";
  }

  function settingsStatusLabel() {
    if (state.settingsSaving) return "DB 저장 중";
    if (state.serverSettingsError) return "저장 실패";
    return state.settingsSaved ? "DB 저장됨" : "저장 필요";
  }

  function settingsStatusTone() {
    if (state.settingsSaving) return "caution";
    if (state.serverSettingsError) return "danger";
    return state.settingsSaved ? "watch" : "hold";
  }

  function refreshSettingsSaveControls() {
    if (!app || !app.querySelectorAll) return;
    Array.prototype.slice.call(app.querySelectorAll('[data-action="save-settings"]')).forEach(function (button) {
      button.disabled = Boolean(state.serverSettingsLocked || state.settingsSaving || !settingsHasPendingChanges());
      button.className = settingsSaveButtonClass();
      button.textContent = settingsSaveButtonLabel();
    });
    Array.prototype.slice.call(app.querySelectorAll("[data-settings-status]")).forEach(function (item) {
      item.className = "tone-chip " + settingsStatusTone();
      item.textContent = settingsStatusLabel();
    });
    Array.prototype.slice.call(app.querySelectorAll("[data-settings-save-title]")).forEach(function (item) {
      item.textContent = settingsHasPendingChanges() ? "변경사항 저장 필요" : "변경사항 저장됨";
    });
    Array.prototype.slice.call(app.querySelectorAll("[data-settings-save-description]")).forEach(function (item) {
      item.textContent = settingsHasPendingChanges()
        ? "현재 화면의 앱 표시, 알림 전달, 외부 API 설정을 로컬 저장소에 반영합니다."
        : "입력값이 로컬 저장소와 동기화되어 있습니다.";
    });
  }

  function renderSettingsPage() {
    return renderManagedPage("settings", state.snapshot || {}, [
      '<section class="admin-grid settings-view">',
      renderSettingsOverviewPanel(),
      renderSettingsEnvironmentPanel(),
      renderSettingsDeliverySettingsPanel(),
      renderSettingsExternalDataPanel(),
      '</section>'
    ].join(""));
  }

  function renderSettingsGroup(title, description, content, tone) {
    return [
      '<section class="settings-fieldset ' + escapeHtml(tone || "neutral") + '">',
      '<div class="settings-fieldset-head">',
      '<div>',
      '<strong>' + escapeHtml(title) + '</strong>',
      '<span>' + escapeHtml(description || "") + '</span>',
      '</div>',
      '</div>',
      '<div class="settings-grid">',
      content,
      '</div>',
      '</section>'
    ].join("");
  }

  function renderSettingsOverviewPanel() {
    return [
      '<article class="panel settings-overview-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">App Settings</p>',
      '<h2>런타임 설정</h2>',
      '</div>',
      '<div class="settings-actions">',
      '<button class="text-button" type="button" data-action="settings-back">이전</button>',
      '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
      '</div>',
      '</div>',
      '<div class="settings-body">',
      '<div class="settings-status-band">',
      '<div class="settings-status-copy">',
      '<p class="settings-section-label">Local first</p>',
      '<strong>앱 표시와 외부 연결 설정</strong>',
      '<span>계정 연결은 계정 탭에서, 매매 판단 기준은 투자 분석 탭에서 관리합니다.</span>',
      '</div>',
      '<div class="settings-status-stack">',
      '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
      '<span class="chip">로컬 DB 우선</span>',
      '</div>',
      state.settingsSaving ? '<p class="lab-message">설정을 MySQL 운영 DB에 저장하는 중입니다.</p>' : '',
      state.serverSettingsError ? '<p class="form-error">' + escapeHtml(state.serverSettingsError) + '</p>' : '',
      state.serverSettingsLocked ? '<p class="form-error">공유 모드에서는 서버 설정 저장이 잠겨 있습니다.</p>' : '',
      '</div>',
      renderRuntimeSettingsSummary(),
      renderSettingsSmartSavePanel(),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSettingsEnvironmentPanel() {
    return [
      '<article class="panel settings-environment-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Display</p>',
      '<h2>앱 환경</h2>',
      '</div>',
      '</div>',
      '<div class="settings-body">',
      renderSettingsGroup("표시 환경", "콘솔 테마와 종목 카탈로그 신선도 기준입니다.", [
        renderSettingSelect("appTheme", "화면 테마", [
        { value: "light", label: "라이트" },
        { value: "dark", label: "다크" },
        { value: "system", label: "시스템 설정" }
        ]),
        renderSettingField("symbolUniverseMaxAgeHours", "전체 종목 신선도(시간)", "number", "24")
      ].join(""), "display"),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSettingsDeliverySettingsPanel() {
    var secretType = state.showSecrets ? "text" : "password";
    return [
      '<article class="panel settings-delivery-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Delivery</p>',
      '<h2>알림 전달 설정</h2>',
      '</div>',
      '</div>',
      '<div class="settings-body">',
      renderSettingsGroup("텔레그램 전달 채널", "사용자에게 나가는 링크와 채널 자격 정보를 관리합니다.", [
        renderSettingField("notifyProvider", "알림 제공자", "text", "telegram"),
        renderSettingField("telegramBotToken", "Telegram Bot Token", secretType, "bot token", { preserveConfigured: true }),
        renderSettingField("telegramChatId", "Telegram Chat ID", "text", "chat id", { preserveConfigured: true }),
        renderSettingField("notifyLinkUrl", "알림 링크 URL", "url", "http://127.0.0.1:3000?tab=notifications")
      ].join(""), "delivery"),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSettingsExternalDataPanel() {
    var secretType = state.showSecrets ? "text" : "password";
    return [
      '<article class="panel settings-external-data-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">External Data</p>',
      '<h2>외부 데이터 연결</h2>',
      '</div>',
      '</div>',
      '<div class="settings-body">',
      renderSettingsGroup("국내 시세·수급", "KIS API와 장중 수급 수집의 호출량, 캐시, live 우선 정책입니다.", [
        renderSettingField("kisBaseUrl", "KIS Base URL", "url", "https://openapi.koreainvestment.com:9443"),
        renderSettingField("kisAppKey", "KIS App Key", secretType, "app key", { preserveConfigured: true }),
        renderSettingField("kisAppSecret", "KIS App Secret", secretType, "app secret", { preserveConfigured: true }),
        renderSettingSelect("kisMarketSignalsEnabled", "KIS 수급 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("kisMarketSignalMaxSymbols", "KIS 수급 종목 수", "number", "20"),
        renderSettingField("kisMarketSignalCacheMinutes", "KIS 수급 캐시(분)", "number", "3"),
        renderSettingField("kisMarketSignalGapSeconds", "KIS 호출 간격(초)", "number", "0.35"),
        renderSettingSelect("kisMarketSignalPreferLiveDuringMarketHours", "장중 KIS live 우선", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("kisMarketSignalLiveRefreshSeconds", "장중 live 최소 간격(초)", "number", "60")
      ].join(""), "market"),
      renderSettingsGroup("해외·거시 원천", "미장, 코인, 금리 데이터를 판단 근거로 넣기 위한 API 연결입니다.", [
        renderSettingSelect("externalAlphaEnabled", "Alpha Vantage 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("alphaVantageApiKey", "Alpha Vantage API Key", secretType, "api key", { preserveConfigured: true }),
        renderSettingField("externalAlphaMaxSymbols", "미장 조회 종목 수", "number", "3"),
        renderSettingSelect("externalCoinGeckoEnabled", "CoinGecko 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("coingeckoApiKey", "CoinGecko API Key", secretType, "api key", { preserveConfigured: true }),
        renderSettingSelect("externalFredEnabled", "FRED 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("fredApiKey", "FRED API Key", secretType, "api key", { preserveConfigured: true }),
        renderSettingField("externalFredSeries", "FRED 지표", "text", "DGS10,DGS2,DFF"),
        renderSettingField("externalCryptoIds", "CoinGecko 코인 ID", "text", "bitcoin,ethereum")
      ].join(""), "external"),
      renderSettingsGroup("뉴스·공시 수집", "뉴스, OpenDART, SEC 원천과 리서치 근거 저장량을 조정합니다.", [
        renderSettingSelect("externalDartEnabled", "OpenDART 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("opendartApiKey", "OpenDART API Key", secretType, "api key", { preserveConfigured: true }),
        renderSettingField("externalDartLookbackDays", "공시 조회 기간(일)", "number", "14"),
        renderSettingSelect("externalSecEnabled", "SEC EDGAR 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("externalSecMaxSymbols", "SEC 조회 종목 수", "number", "3"),
        renderSettingField("externalSecUserAgent", "SEC User-Agent", "text", "DigitalTwin/1.0 local-contact"),
        renderSettingSelect("externalNewsEnabled", "뉴스 헤드라인 수집", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingSelect("externalNewsProvider", "뉴스 공급자", [
          { value: "auto", label: "자동" },
          { value: "gdelt", label: "GDELT" },
          { value: "alpha_vantage", label: "Alpha Vantage" }
        ]),
        renderSettingField("externalNewsMaxSymbols", "뉴스 조회 종목 수", "number", "3"),
        renderSettingField("externalNewsLookbackHours", "뉴스 조회 기간(시간)", "number", "48"),
        renderSettingField("externalResearchEvidenceMaxItems", "AI 전달 최신 근거 수", "number", "8"),
        renderSettingField("newsCollectionIntervalSeconds", "뉴스 수집 주기(초)", "number", "60"),
        renderSettingField("newsCollectionMaxSymbols", "뉴스 수집 종목 수", "number", "40"),
        renderSettingField("newsCollectionLookbackMinutes", "뉴스 조회 기간(분)", "number", "180"),
        renderSettingField("newsCollectionMinRelevanceScore", "뉴스 관련성 최소 점수", "number", "35")
      ].join(""), "research"),
      renderSettingsGroup("신선도·추론 게이트", "알림과 온톨로지 추론에 들어가기 전 데이터 유효성을 제한합니다.", [
        renderSettingField("externalApiFetchIntervalMinutes", "외부 API 캐시(분)", "number", "30"),
        renderSettingField("externalSignalCacheMaxAgeMinutes", "외부 신호 캐시 TTL(분)", "number", "10"),
        renderSettingSelect("dataFreshnessEnabled", "알림 데이터 신선도 게이트", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingField("dataFreshnessDefaultMaxAgeMinutes", "알림 기본 신선도(분)", "number", "10"),
        renderSettingField("dataFreshnessQuoteMaxAgeMinutes", "시세 알림 신선도(분)", "number", "10"),
        renderSettingField("dataFreshnessExternalMaxAgeMinutes", "외부 신호 신선도(분)", "number", "10"),
        renderSettingField("dataFreshnessExternalEquityMaxAgeMinutes", "미장 신호 신선도(분)", "number", "10"),
        renderSettingField("dataFreshnessExternalCryptoMaxAgeMinutes", "크립토 신호 신선도(분)", "number", "10"),
        renderSettingField("dataFreshnessMacroMaxAgeMinutes", "거시 신호 신선도(분)", "number", "120"),
        renderSettingField("dataFreshnessDisclosureMaxAgeMinutes", "공시 신선도(분)", "number", "120"),
        renderSettingField("marketDataMaxAgeMinutes", "추천 시세 신선도(분)", "number", "240"),
        renderSettingField("ontologyReasoningIntervalSeconds", "추론 요청 확인 주기(초)", "number", "10"),
        renderSettingField("ontologyReasoningBatchSize", "추론 요청 배치", "number", "20"),
        renderSettingField("materialityMinimumScore", "중요 변경 기본 기준", "number", "65"),
        renderSettingField("marketMaterialityPriceChangePct", "가격 중요 변화율(%)", "number", "0.6"),
        renderSettingField("newsMaterialityMinimumScore", "뉴스 중요 기준", "number", "65")
      ].join(""), "gate"),
      renderSettingsGroup("공시 AI와 매핑", "AI 해석 방식과 종목·CIK·환율 매핑처럼 긴 설정값을 관리합니다.", [
        renderSettingSelect("dartDisclosureAiAnalysisEnabled", "공시 AI 해석", [
          { value: "1", label: "사용" },
          { value: "0", label: "사용 안 함" }
        ]),
        renderSettingSelect("dartDisclosureAiUseCodex", "공시 해석 엔진", [
          { value: "1", label: "Codex AI" },
          { value: "0", label: "로컬 규칙" }
        ]),
        renderSettingField("dartDisclosureAiTimeoutSeconds", "공시 AI 타임아웃(초)", "number", "90"),
        renderSettingField("dartDisclosureAiCommand", "공시 AI 명령", "text", "비우면 Codex 사용"),
        '<label class="setting-field wide">',
        '<span class="setting-field-label">OpenDART 종목 매핑</span>',
        '<div class="form-control-shell"><textarea data-setting="externalDartCorpCodes" rows="3" autocomplete="off" placeholder="005930=00126380">' + escapeHtml(settingValue("externalDartCorpCodes") || defaultSettings.externalDartCorpCodes) + '</textarea></div>',
        '</label>',
        '<label class="setting-field wide">',
        '<span class="setting-field-label">SEC CIK 매핑</span>',
        '<div class="form-control-shell"><textarea data-setting="externalSecCompanyCiks" rows="3" autocomplete="off" placeholder="AAPL=0000320193">' + escapeHtml(settingValue("externalSecCompanyCiks") || defaultSettings.externalSecCompanyCiks) + '</textarea></div>',
        '</label>',
        '<label class="setting-field wide">',
        '<span class="setting-field-label">환율 설정</span>',
        '<div class="form-control-shell"><textarea data-setting="fxRates" rows="2" autocomplete="off" placeholder="USD=1400">' + escapeHtml(settingValue("fxRates") || defaultSettings.fxRates) + '</textarea></div>',
        '</label>'
      ].join(""), "mapping"),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSettingsSmartSavePanel() {
    return [
      '<div class="settings-smart-save">',
      '<div class="settings-smart-save-copy">',
      '<strong data-settings-save-title>' + escapeHtml(settingsHasPendingChanges() ? "변경사항 저장 필요" : "변경사항 저장됨") + '</strong>',
      '<span data-settings-save-description>' + escapeHtml(settingsHasPendingChanges() ? "현재 화면의 앱 표시, 알림 전달, 외부 API 설정을 로컬 저장소에 반영합니다." : "입력값이 로컬 저장소와 동기화되어 있습니다.") + '</span>',
      '</div>',
      '<div class="settings-actions settings-page-actions">',
      '<button class="' + settingsSaveButtonClass() + '" type="button" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
      '<button class="text-button" type="button" data-action="toggle-secrets">' + (state.showSecrets ? "secret 숨기기" : "secret 보기") + '</button>',
      '</div>',
      '</div>',
    ].join("");
  }

  function bindActions() {
    var refresh = app.querySelector('[data-action="refresh"]');
    if (refresh) {
      refresh.addEventListener("click", function () {
        if (!state.refreshing) load();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-ontology-graph-expand]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var graphId = normalizeOntologyGraphId(button.getAttribute("data-ontology-graph-expand"));
        if (!graphId) return;
        state.expandedOntologyGraphId = graphId;
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-ontology-graph-fit]")).forEach(function (button) {
      button.addEventListener("click", function () {
        fitOntologyGraph(button.getAttribute("data-ontology-graph-fit"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-ontology-graph-layout]")).forEach(function (button) {
      button.addEventListener("click", function () {
        layoutOntologyGraph(button.getAttribute("data-ontology-graph-layout"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-ontology-graph-close]")).forEach(function (button) {
      button.addEventListener("click", function (event) {
        if (button.classList && button.classList.contains("ontology-graph-expanded-backdrop") && event.target !== button) return;
        state.expandedOntologyGraphId = "";
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-tab]")).forEach(bindTabNavigation);

    Array.prototype.slice.call(app.querySelectorAll("[data-monitor-instrument-detail]")).forEach(function (row) {
      var openInstrumentDetail = function () {
        var symbol = String(row.getAttribute("data-monitor-instrument-detail") || "").toUpperCase();
        if (!symbol) return;
        state.monitoringDetail = { type: "instrument", symbol: symbol };
        render();
      };
      row.addEventListener("click", openInstrumentDetail);
      row.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        openInstrumentDetail();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-monitor-alert-detail]")).forEach(function (row) {
      var openAlertDetail = function () {
        var index = Number(row.getAttribute("data-monitor-alert-detail"));
        if (!Number.isFinite(index)) return;
        state.monitoringDetail = { type: "alert", index: index };
        render();
      };
      row.addEventListener("click", openAlertDetail);
      row.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        openAlertDetail();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-monitoring-detail-close]")).forEach(function (button) {
      button.addEventListener("click", function (event) {
        if (button.classList && button.classList.contains("monitoring-detail-backdrop") && event.target !== button) return;
        state.monitoringDetail = null;
        render();
      });
    });

    var settingsBack = app.querySelector('[data-action="settings-back"]');
    if (settingsBack) {
      settingsBack.addEventListener("click", function () {
        navigateToTab(state.previousTab || "overview", { replace: true, skipPrevious: true });
      });
    }

    Array.prototype.slice.call(app.querySelectorAll('[data-action="refresh-research-evidence"]')).forEach(function (refreshEvidence) {
      refreshEvidence.addEventListener("click", function () {
        loadResearchEvidence(true);
      });
    });

    var researchEvidenceForm = app.querySelector("[data-research-evidence-form]");
    if (researchEvidenceForm) {
      researchEvidenceForm.addEventListener("submit", function (event) {
        event.preventDefault();
        loadResearchEvidence(true);
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-research-filter]")).forEach(function (field) {
      var updateResearchFilter = function () {
        var name = field.getAttribute("data-research-filter");
        if (!name) return;
        state.researchEvidenceFilters[name] = field.value;
      };
      field.addEventListener("input", updateResearchFilter);
      field.addEventListener("change", updateResearchFilter);
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-research-delete]")).forEach(function (button) {
      button.addEventListener("click", function () {
        deleteResearchEvidence(button.getAttribute("data-research-delete"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll('[data-action="new-service-account"]')).forEach(function (newServiceAccount) {
      newServiceAccount.addEventListener("click", function () {
        setPageViewMode("accounts", "settings");
        state.editingAccountId = "";
        state.accountDraft = createNewAccountDraft();
        state.accountSaved = false;
        state.serviceAccountsError = "";
        state.activeAccountSection = "management";
        writeAccountSectionHistory("management");
        render();
      });
    });

    var accountForm = app.querySelector("[data-account-form]");
    if (accountForm) {
      accountForm.addEventListener("submit", function (event) {
        event.preventDefault();
        saveServiceAccount();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-account-field]")).forEach(function (field) {
      field.addEventListener("input", function () {
        var name = field.getAttribute("data-account-field");
        if (!name) return;
        state.accountDraft[name] = field.type === "checkbox" ? field.checked : field.value;
        state.accountSaved = false;
      });
      field.addEventListener("change", function () {
        var name = field.getAttribute("data-account-field");
        if (!name) return;
        state.accountDraft[name] = field.type === "checkbox" ? field.checked : field.value;
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-account-edit]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var id = button.getAttribute("data-account-edit");
        var account = (state.serviceAccounts || []).filter(function (item) { return item.id === id; })[0];
        if (!account) return;
        state.editingAccountId = id;
        state.accountDraft = accountDraftFromAccount(account);
        state.accountSaved = false;
        state.serviceAccountsError = "";
        state.activeTab = "accounts";
        setPageViewMode("accounts", "settings");
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-account-remove]")).forEach(function (button) {
      button.addEventListener("click", function () {
        removeServiceAccount(button.getAttribute("data-account-remove"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-watch-account-select]")).forEach(function (button) {
      button.addEventListener("click", function () {
        state.activeWatchAccountId = button.getAttribute("data-watch-account-select") || "";
        state.editingWatchAccountId = "";
        state.editingWatchSymbol = "";
        state.watchlistError = "";
        state.watchSuggestQuery = "";
        state.watchSuggestItems = [];
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-model-setting]")).forEach(function (field) {
      field.addEventListener("input", function () {
        var name = field.getAttribute("data-model-setting");
        if (!name) return;
        state.settings[name] = field.value;
        persistSettings();
        state.settingsSaved = false;
        refreshSettingsSaveControls();
      });
      field.addEventListener("change", function () {
        persistSettings();
        state.settingsSaved = false;
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-ontology-rulebox-json]")).forEach(function (field) {
      field.addEventListener("input", function () {
        state.ontologyRuleboxJson = field.value;
        state.ontologyRuleboxError = "";
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-ontology-rulebox-change-reason]")).forEach(function (field) {
      field.addEventListener("input", function () {
        state.ontologyRuleboxChangeReason = field.value;
      });
    });

    var refreshRuleboxButton = app.querySelector('[data-action="refresh-rulebox"]');
    if (refreshRuleboxButton) {
      refreshRuleboxButton.addEventListener("click", function () {
        loadOntologyRulebox(true).then(function () {
          showSnackbar("Neo4j RuleBox를 다시 읽었습니다.");
        });
      });
    }

    var seedRuleboxButton = app.querySelector('[data-action="seed-rulebox"]');
    if (seedRuleboxButton) {
      seedRuleboxButton.addEventListener("click", function () {
        saveOntologyRulebox(true);
      });
    }

    var saveRuleboxButton = app.querySelector('[data-action="save-rulebox"]');
    if (saveRuleboxButton) {
      saveRuleboxButton.addEventListener("click", function () {
        saveOntologyRulebox(false);
      });
    }

    var runRuleboxButton = app.querySelector('[data-action="run-rulebox"]');
    if (runRuleboxButton) {
      runRuleboxButton.addEventListener("click", function () {
        runOntologyRulebox();
      });
    }

    var proposeRuleboxCandidatesButton = app.querySelector('[data-action="propose-rulebox-candidates"]');
    if (proposeRuleboxCandidatesButton) {
      proposeRuleboxCandidatesButton.addEventListener("click", function () {
        proposeOntologyRuleCandidates();
      });
    }

    var refreshLabButton = app.querySelector("[data-lab-refresh]");
    if (refreshLabButton) {
      refreshLabButton.addEventListener("click", function () {
        loadOntologyExperiments(true).then(function () {
          showSnackbar("온톨로지 실험 상태를 다시 읽었습니다.");
        });
      });
    }

    var runActiveLabButton = app.querySelector("[data-lab-run-active]");
    if (runActiveLabButton) {
      runActiveLabButton.addEventListener("click", function () {
        runOntologyExperimentsOnce();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-lab-run]")).forEach(function (button) {
      button.addEventListener("click", function () {
        runOntologyExperiment(button.getAttribute("data-lab-run"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-lab-activate]")).forEach(function (button) {
      button.addEventListener("click", function () {
        activateOntologyExperiment(button.getAttribute("data-lab-activate"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-lab-pause]")).forEach(function (button) {
      button.addEventListener("click", function () {
        pauseOntologyExperiment(button.getAttribute("data-lab-pause"));
      });
    });


    Array.prototype.slice.call(app.querySelectorAll('[data-action="append-rulebox-candidate"]')).forEach(function (button) {
      button.addEventListener("click", function () {
        appendRuleboxCandidate(button.getAttribute("data-candidate-id"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-number-setting]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNumberAssignmentSetting(
          field.getAttribute("data-number-setting"),
          field.getAttribute("data-number-key"),
          field.value
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-alert-rule]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateBooleanAssignmentSetting("alertRules", field.getAttribute("data-alert-rule"), field.checked);
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-page-mode]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var page = normalizeTabId(button.getAttribute("data-page-mode-page") || state.activeTab);
        var mode = normalizePageMode(button.getAttribute("data-page-mode"));
        if (activePageMode(page) === mode) return;
        setPageViewMode(page, mode);
        if (page === "accounts") writeAccountSectionHistory(state.activeAccountSection);
        else if (page === "notifications") writeNotificationSectionHistory(state.activeNotificationSection);
        else if (page === "modeling") writeStrategySectionHistory(state.activeStrategySection);
        else writePageModeHistory(page, mode);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-section]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var section = normalizeNotificationSection(button.getAttribute("data-notification-section"));
        if (section === state.activeNotificationSection) return;
        state.activeNotificationSection = section;
        state.pageViewModes.notifications = sectionModeForPage("notifications", section);
        state.notificationPolicyEditorOpen = false;
        state.notificationTemplateEditorOpen = false;
        writeNotificationSectionHistory(section);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-account-section]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var section = normalizeAccountSection(button.getAttribute("data-account-section"));
        if (section === state.activeAccountSection) return;
        state.activeAccountSection = section;
        state.pageViewModes.accounts = sectionModeForPage("accounts", section);
        writeAccountSectionHistory(section);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-strategy-section]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var section = normalizeStrategySection(button.getAttribute("data-strategy-section"));
        if (section === state.activeStrategySection) return;
        state.activeStrategySection = section;
        state.pageViewModes.modeling = sectionModeForPage("modeling", section);
        writeStrategySectionHistory(section);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-ontology-section]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var legacySection = normalizeOntologySection(button.getAttribute("data-ontology-section"));
        var section = normalizeStrategySection(legacySection);
        if (section === state.activeStrategySection && state.activeTab === "modeling") return;
        state.activeTab = "modeling";
        state.activeOntologySection = legacySection;
        state.activeStrategySection = section;
        writeStrategySectionHistory(section);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-message-select]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var messageType = button.getAttribute("data-message-select") || "";
        if (!notificationRuleByKey(messageType)) return;
        state.activeNotificationMessageType = messageType;
        state.notificationPolicyEditorOpen = true;
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-editor-close]")).forEach(function (button) {
      button.addEventListener("click", function () {
        state.notificationPolicyEditorOpen = false;
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-template-select]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var messageType = button.getAttribute("data-template-select") || "";
        if (!notificationTemplateItems().some(function (item) { return item.messageType === messageType; })) return;
        state.activeNotificationTemplateType = messageType;
        state.notificationTemplateEditorOpen = true;
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-template-editor-close]")).forEach(function (button) {
      button.addEventListener("click", function () {
        state.notificationTemplateEditorOpen = false;
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-message-toggle]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var messageType = button.getAttribute("data-message-toggle");
        state.notificationExpandedTypes[messageType] = !notificationTypeExpanded(messageType);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-message-group-toggle]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var group = button.getAttribute("data-message-group-toggle") || "";
        if (!group) return;
        state.notificationExpandedGroups[group] = !notificationGroupExpanded(group);
        render();
      });
    });

    var expandMessageTypes = app.querySelector('[data-action="expand-message-types"]');
    if (expandMessageTypes) {
      expandMessageTypes.addEventListener("click", function () {
        state.notificationExpandedTypes = {};
        state.notificationExpandedGroups = {};
        alertRuleGroups().forEach(function (group) {
          state.notificationExpandedGroups[group.name] = true;
        });
        render();
      });
    }

    var collapseMessageTypes = app.querySelector('[data-action="collapse-message-types"]');
    if (collapseMessageTypes) {
      collapseMessageTypes.addEventListener("click", function () {
        state.notificationExpandedTypes = {};
        state.notificationExpandedGroups = {};
        render();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-alert-threshold]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNumberAssignmentSetting("alertThresholds", field.getAttribute("data-alert-threshold"), field.value);
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-alert-cadence]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNumberAssignmentSetting("alertCadenceMinutes", field.getAttribute("data-alert-cadence"), field.value);
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-template]")).forEach(function (field) {
      field.addEventListener("input", function () {
        var messageType = field.getAttribute("data-notification-template");
        updateNotificationTemplate(messageType, field.value);
        var row = field.closest ? field.closest(".notification-template-row") : null;
        var preview = row ? row.querySelector("[data-template-preview]") : null;
        if (preview) {
          preview.textContent = renderNotificationTemplatePreviewText(field.value, messageType);
        }
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-template-save]")).forEach(function (button) {
      button.addEventListener("click", function () {
        saveNotificationTemplate(button.getAttribute("data-template-save"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-template-reset]")).forEach(function (button) {
      button.addEventListener("click", function () {
        resetNotificationTemplate(button.getAttribute("data-template-reset"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-template-test-send]")).forEach(function (button) {
      button.addEventListener("click", function () {
        sendNotificationTemplateTest(button.getAttribute("data-template-test-send"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-enabled]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleField(field.getAttribute("data-notification-rule-enabled"), "enabled", field.checked);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-number]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleField(
          field.getAttribute("data-notification-rule-number"),
          field.getAttribute("data-rule-field"),
          field.value
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-action]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleField(field.getAttribute("data-notification-rule-action"), "lowScoreAction", field.value);
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-similarity-enabled]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleField(field.getAttribute("data-notification-rule-similarity-enabled"), "similarityEnabled", field.checked);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-state-enabled]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleField(field.getAttribute("data-notification-rule-state-enabled"), "stateCooldownEnabled", field.checked);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-market-hours-enabled]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleField(field.getAttribute("data-notification-rule-market-hours-enabled"), "marketHoursEnabled", field.checked);
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-market-hours-market]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleMarket(
          field.getAttribute("data-notification-rule-market-hours-market"),
          field.getAttribute("data-market"),
          field.checked
        );
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-fields]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleField(field.getAttribute("data-notification-rule-fields"), "similarityFields", field.value);
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-bypass-enabled]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleBypassCondition(
          field.getAttribute("data-notification-rule-bypass-enabled"),
          field.getAttribute("data-condition-id"),
          "enabled",
          field.checked
        );
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-bypass-field]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleBypassCondition(
          field.getAttribute("data-notification-rule-bypass-field"),
          field.getAttribute("data-condition-id"),
          "field",
          field.value
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-bypass-value]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleBypassCondition(
          field.getAttribute("data-notification-rule-bypass-value"),
          field.getAttribute("data-condition-id"),
          "value",
          field.value
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-condition-enabled]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleCondition(
          field.getAttribute("data-notification-rule-condition-enabled"),
          field.getAttribute("data-condition-id"),
          "enabled",
          field.checked
        );
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-condition-score]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleCondition(
          field.getAttribute("data-notification-rule-condition-score"),
          field.getAttribute("data-condition-id"),
          "score",
          field.value
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-condition-field]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleCondition(
          field.getAttribute("data-notification-rule-condition-field"),
          field.getAttribute("data-condition-id"),
          "field",
          field.value
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-rule-condition-value]")).forEach(function (field) {
      field.addEventListener("change", function () {
        updateNotificationRuleCondition(
          field.getAttribute("data-notification-rule-condition-value"),
          field.getAttribute("data-condition-id"),
          "value",
          field.value
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-rule-save]")).forEach(function (button) {
      button.addEventListener("click", function () {
        saveNotificationRule(button.getAttribute("data-rule-save"));
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-rule-reset]")).forEach(function (button) {
      button.addEventListener("click", function () {
        resetNotificationRule(button.getAttribute("data-rule-reset"));
      });
    });

    var refreshNotificationJobsButton = app.querySelector('[data-action="refresh-notification-jobs"]');
    if (refreshNotificationJobsButton) {
      refreshNotificationJobsButton.addEventListener("click", function () {
        loadNotificationJobs();
        render();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-full-toggle]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var key = button.getAttribute("data-notification-full-toggle") || "";
        if (!key) return;
        state.notificationExpandedJobs[key] = !state.notificationExpandedJobs[key];
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-notification-job-select]")).forEach(function (row) {
      var selectNotificationJob = function () {
        var key = row.getAttribute("data-notification-job-select") || "";
        if (!key || state.activeNotificationJobKey === key) return;
        state.activeNotificationJobKey = key;
        render();
      };
      row.addEventListener("click", function (event) {
        var target = event.target;
        while (target && target !== row) {
          if (/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(target.tagName || "")) return;
          target = target.parentNode;
        }
        selectNotificationJob();
      });
      row.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        selectNotificationJob();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-setting]")).forEach(function (field) {
      var updateSettingField = function () {
        var name = field.getAttribute("data-setting");
        if (!name) return;
        state.settings[name] = field.value;
        state.settingsSaved = false;
        if (name === "appTheme") applyAppTheme();
        refreshSettingsSaveControls();
      };
      field.addEventListener("input", updateSettingField);
      field.addEventListener("change", updateSettingField);
    });

    Array.prototype.slice.call(app.querySelectorAll('[data-action="save-settings"]')).forEach(function (saveSettings) {
      saveSettings.addEventListener("click", function () {
        if (state.settingsSaving) return;
        state.settingsSaving = true;
        state.serverSettingsError = "";
        render();
        saveSettingsToServer()
          .then(function () {
            state.settingsSaved = true;
            showSnackbar("설정을 저장했습니다.");
          })
          .catch(function (error) {
            state.serverSettingsError = error.message || "설정을 저장하지 못했습니다.";
            state.settingsSaved = false;
            showSnackbar(state.serverSettingsError, "danger");
          })
          .finally(function () {
            state.settingsSaving = false;
            render();
          });
      });
    });

    var watchAddForm = app.querySelector("[data-watch-add-form]");
    if (watchAddForm) {
      var watchSymbolInput = watchAddForm.querySelector("[data-watch-symbol-input]");
      var watchSuggestBox = app.querySelector("[data-watch-suggest-list]");
      if (watchSymbolInput) {
        watchSymbolInput.addEventListener("input", function () {
          loadWatchSuggestions(watchSymbolInput.value, watchSuggestBox, watchSymbolInput);
        });
        watchSymbolInput.addEventListener("focus", function () {
          if (watchSymbolInput.value) {
            loadWatchSuggestions(watchSymbolInput.value, watchSuggestBox, watchSymbolInput);
          }
        });
      }
      watchAddForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var input = watchAddForm.querySelector('input[name="symbol"]');
        var accountId = watchAddForm.getAttribute("data-watch-account-id") || "";
        if (accountId) {
          addAccountWatchSymbol(accountId, input ? input.value : "");
        } else {
          addWatchSymbol(input ? input.value : "");
        }
      });
    }

    var watchSuggestList = app.querySelector("[data-watch-suggest-list]");
    if (watchSuggestList) {
      watchSuggestList.addEventListener("click", function (event) {
        var target = event.target;
        while (target && target !== watchSuggestList && !target.getAttribute("data-watch-suggest-symbol")) {
          target = target.parentNode;
        }
        if (!target || target === watchSuggestList) return;
        event.preventDefault();
        var accountId = watchSuggestList.getAttribute("data-watch-account-id") || "";
        if (accountId) {
          addAccountWatchSymbol(accountId, target.getAttribute("data-watch-suggest-symbol"));
        } else {
          addWatchSymbol(target.getAttribute("data-watch-suggest-symbol"));
        }
      });
    }

    var symbolSearchForm = app.querySelector("[data-symbol-search-form]");
    if (symbolSearchForm) {
      symbolSearchForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var query = symbolSearchForm.querySelector("[data-symbol-query]");
        var market = symbolSearchForm.querySelector("[data-symbol-market]");
        var limit = symbolSearchForm.querySelector("[data-symbol-limit]");
        state.symbolUniverseQuery = query ? query.value.trim() : "";
        state.symbolUniverseMarket = market ? market.value : "";
        state.symbolUniverseLimit = limit ? Number(limit.value || 80) : state.symbolUniverseLimit;
        state.symbolUniverseOffset = 0;
        loadSymbolUniverse();
      });
    }

    var refreshSymbols = app.querySelector('[data-action="refresh-symbol-universe"]');
    if (refreshSymbols) {
      refreshSymbols.addEventListener("click", function () {
        refreshSymbolUniverse();
      });
    }

    var addVisibleSymbols = app.querySelector('[data-action="add-visible-symbols"]');
    if (addVisibleSymbols) {
      addVisibleSymbols.addEventListener("click", function () {
        addVisibleSymbolsToPreferredWatchlist();
      });
    }

    var symbolAddAccount = app.querySelector("[data-symbol-add-account]");
    if (symbolAddAccount) {
      symbolAddAccount.addEventListener("change", function () {
        state.activeWatchAccountId = symbolAddAccount.value || "";
        render();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-symbol-page]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var direction = button.getAttribute("data-symbol-page");
        var limit = Number(state.symbolUniverseLimit || 80);
        if (direction === "prev") {
          state.symbolUniverseOffset = Math.max(0, Number(state.symbolUniverseOffset || 0) - limit);
        } else if (direction === "next") {
          state.symbolUniverseOffset = Number(state.symbolUniverseOffset || 0) + limit;
        }
        loadSymbolUniverse();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-symbol-add-watch]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var symbol = String(button.getAttribute("data-symbol-add-watch") || "").toUpperCase();
        addSymbolToPreferredWatchlist(symbol);
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-account-watch-edit]")).forEach(function (button) {
      button.addEventListener("click", function () {
        state.editingWatchAccountId = button.getAttribute("data-watch-account-id") || "";
        state.editingWatchSymbol = String(button.getAttribute("data-account-watch-edit") || "").toUpperCase();
        state.watchlistError = "";
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-account-watch-remove]")).forEach(function (button) {
      button.addEventListener("click", function () {
        removeAccountWatchSymbol(
          button.getAttribute("data-watch-account-id") || "",
          button.getAttribute("data-account-watch-remove") || ""
        );
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-account-watch-edit-form]")).forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        var input = form.querySelector('input[name="symbol"]');
        replaceAccountWatchSymbol(
          form.getAttribute("data-watch-account-id") || "",
          form.getAttribute("data-account-watch-edit-form") || "",
          input ? input.value : ""
        );
      });
    });

    var accountWatchCancel = app.querySelector("[data-account-watch-cancel]");
    if (accountWatchCancel) {
      accountWatchCancel.addEventListener("click", function () {
        state.editingWatchAccountId = "";
        state.editingWatchSymbol = "";
        state.watchlistError = "";
        render();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll('[data-action="toggle-secrets"]')).forEach(function (toggleSecrets) {
      toggleSecrets.addEventListener("click", function () {
        state.showSecrets = !state.showSecrets;
        render();
      });
    });
  }

  if (window.matchMedia) {
    var systemThemeQuery = window.matchMedia("(prefers-color-scheme: dark)");
    var handleSystemThemeChange = function () {
      if (currentAppTheme() === "system") applyAppTheme();
    };
    if (systemThemeQuery.addEventListener) {
      systemThemeQuery.addEventListener("change", handleSystemThemeChange);
    } else if (systemThemeQuery.addListener) {
      systemThemeQuery.addListener(handleSystemThemeChange);
    }
  }

  if (window.addEventListener) {
    window.addEventListener("popstate", syncTabFromLocation);
    window.addEventListener("scroll", function () {
      rememberRenderedPageScrollPosition();
      scheduleAppNavScrollState();
      scheduleTopbarScrollState();
    }, { passive: true });
    window.addEventListener("resize", function () {
      rememberRenderedPageScrollPosition();
      restoreRenderedPageScrollPosition();
      scheduleAppNavScrollState();
      scheduleTopbarScrollState();
    });
    window.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      if (state.expandedOntologyGraphId) {
        state.expandedOntologyGraphId = "";
        render();
        return;
      }
      if (!state.monitoringDetail) return;
      state.monitoringDetail = null;
      render();
    });
  }

  applyAppTheme();
  connectRealtime();
  render();
  var snapshotLoadTask = load();
  var snapshotPrerequisites = [loadServerSettings(), loadServiceAccounts()];
  var supportingBootstrapTasks = [
    loadNotificationTemplates(),
    loadNotificationRules(),
    loadNotificationJobs(),
    loadNotificationSchedules(),
    loadOntologyRulebox(),
    loadSymbolUniverse()
  ];
  Promise.all(snapshotPrerequisites.map(function (task) {
    return task.catch(function () {
      return null;
    });
  })).finally(function () {
    if (state.snapshot) render();
  });
  Promise.all(supportingBootstrapTasks.map(function (task) {
    return task.catch(function () {
      return null;
    });
  })).finally(function () {
    if (state.snapshot) render();
  });
  snapshotLoadTask.catch(function () {
    return null;
  });
}());
