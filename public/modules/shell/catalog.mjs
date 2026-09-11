import { navigationState } from "../state/navigation.mjs";

var tabs = [
  { id: "overview", label: "오늘", description: "판단·일정·위험", groupId: "workspace" },
  { id: "portfolio", label: "포트폴리오", description: "보유·위험·리밸런싱", groupId: "workspace" },
  { id: "calendar", label: "캘린더", description: "일정·리서치·알림", groupId: "workspace" },
  { id: "feed", label: "시장", description: "종목·뉴스·수급", groupId: "workspace" },
  { id: "modeling", label: "판단", description: "액션·근거", groupId: "workspace" },
  { id: "notifications", label: "알림", description: "변화·전달", groupId: "workspace" },
  { id: "experiments", label: "근거 점검", description: "부족 자료·차단", groupId: "workspace", hidden: true },
  { id: "settings", label: "설정", description: "계정·내 환경", groupId: "utility" },
  { id: "operations", label: "운영", description: "데이터·추론·전달", groupId: "utility" }
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

var bottomTabIds = ["overview", "portfolio", "feed", "modeling", "notifications"];

var managementTabIds = [];

var navigationGroups = [
  { id: "workspace", label: "투자 콘솔", description: "계좌·시장·판단", tabIds: ["overview", "portfolio", "feed", "modeling", "notifications", "calendar"] },
  { id: "utility", label: "관리", description: "설정·운영", tabIds: ["settings", "operations"] }
];

var pageStructureCatalog = {
  overview: {
    layer: "오늘의 관제",
    entity: "오늘의 작업 큐",
    objective: "포트폴리오, 판단, 일정, 운영 이상 중 지금 처리할 일만 우선순위로 봅니다.",
    workflow: ["핵심 상태", "우선순위", "다음 행동"]
  },
  portfolio: {
    layer: "계좌 포트폴리오",
    entity: "보유·위험·리밸런싱",
    objective: "현재 보유, 정책 이탈, 리밸런싱 대안과 원장 활동을 계좌 기준으로 봅니다.",
    workflow: ["보유 확인", "위험 점검", "배분 검토"]
  },
  accounts: {
    layer: "계정 원장",
    entity: "서비스 계정",
    objective: "계좌/API/알림 채널을 원장으로 정리하고 데이터 출처를 검증합니다.",
    workflow: ["계정 목록", "출처 검증", "저장 관리"]
  },
  watchlist: {
    layer: "관심 관리",
    entity: "관찰 종목",
    objective: "계정별 관찰 종목을 분리해 알림과 전략 판단의 입력으로 관리합니다.",
    workflow: ["계정 선택", "종목 편집", "알림 연결"]
  },
  calendar: {
    layer: "이벤트 캘린더",
    entity: "투자 이벤트",
    objective: "실적, 배당, 거시지표, 공시 같은 예정 이벤트를 관리하고 리마인더 알림으로 연결합니다.",
    workflow: ["일정 확인", "이벤트 등록", "알림 큐"]
  },
  symbols: {
    layer: "종목 카탈로그",
    entity: "시장 종목",
    objective: "시장 유니버스를 검색하고 관심종목 편입 후보를 정리합니다.",
    workflow: ["목록 조회", "필터 적용", "계정 편입"]
  },
  notifications: {
    layer: "변화 알림",
    entity: "알림 전달 기록",
    objective: "이전 상태에서 달라진 내용과 전송·미발송·실패 상태를 한 원장에서 확인합니다.",
    workflow: ["변화 확인", "전달 상태", "판단 연결"]
  },
  modeling: {
    layer: "투자 판단",
    entity: "투자 의견",
    objective: "종목별 최종 행동, 강도, 핵심 근거와 약화 조건을 한 큐에서 봅니다.",
    workflow: ["행동 후보", "근거 확인", "실행 전 점검"]
  },
  experiments: {
    layer: "투자 판단",
    entity: "근거 점검",
    objective: "현재 의견을 막는 부족한 자료, 상충 근거와 다음 확인 행동만 봅니다.",
    workflow: ["부족 원인", "영향 범위", "다음 확인"]
  },
  feed: {
    layer: "시장 탐색",
    entity: "종목·뉴스 컨텍스트",
    objective: "보유·관심 종목의 현재 상태와 주가 영향 뉴스를 중복 없이 연결해 봅니다.",
    workflow: ["종목 탐색", "영향 뉴스", "상세 분석"]
  },
  system: {
    layer: "구조 문서",
    entity: "시스템 흐름",
    objective: "데이터 수집부터 추론, 알림까지 전체 실행 흐름을 문서화합니다.",
    workflow: ["구조 이해", "이벤트 흐름", "운영 기준"]
  },
  settings: {
    layer: "설정 관리",
    entity: "계정·앱 환경",
    objective: "투자 계정과 개인 화면·알림 수신 환경을 관리합니다.",
    workflow: ["계정 선택", "환경 확인", "설정 편집"]
  },
  operations: {
    layer: "운영 관제",
    entity: "데이터·추론·전달 파이프라인",
    objective: "외부 데이터, 워커, TypeDB 추론, AI 판단과 알림 전달 상태를 운영자가 점검합니다.",
    workflow: ["상태 확인", "병목 진단", "관리 도구"]
  }
};

var notificationSections = [
  { id: "status", label: "현황", description: "발송 판단" },
  { id: "candidates", label: "후보", description: "발송 전 신호" },
  { id: "policy", label: "정책", description: "타입별 룰" },
  { id: "templates", label: "템플릿", description: "본문·미리보기" },
  { id: "diagnostics", label: "진단", description: "채널·실패 원인" }
];

var accountSections = [
  { id: "status", label: "상태", description: "계정 진단" },
  { id: "identity", label: "계정", description: "식별 정보" },
  { id: "connections", label: "연결", description: "증권사 인증" },
  { id: "balance", label: "자산 검증", description: "금액 산식" },
  { id: "history", label: "데이터 이력", description: "신선도" }
];

var settingsSections = [
  { id: "account", label: "계정", description: "투자 계정과 증권사 연결", scope: "계정별" },
  { id: "preferences", label: "내 환경", description: "화면과 알림 수신", scope: "사용자 환경" }
];

var strategySections = [
  { id: "overview", label: "오늘의 판단", description: "액션 큐" },
  { id: "evidence", label: "투자 근거", description: "관계·데이터" },
  { id: "charts", label: "통합 차트", description: "가격·흐름" },
  { id: "rules", label: "전략 룰", description: "조건·알림" },
  { id: "graphs", label: "온톨로지", description: "TBox·ABox" },
  { id: "proposals", label: "전략 제안", description: "승인·성과" },
  { id: "hypotheses", label: "가설 검증", description: "변화·반증·결과" },
  { id: "trace", label: "검증·리뷰", description: "성과·품질" }
];

var ontologySections = [
  { id: "overview", label: "개요", description: "요약·상태" },
  { id: "structure", label: "전체 구조", description: "흐름 지도" },
  { id: "graphs", label: "관계 그래프", description: "규칙·현재 데이터" },
  { id: "registry", label: "규칙·프롬프트", description: "런타임 관리" },
  { id: "trace", label: "관계 추적", description: "행·룰 검증" }
];

var ontologyCatalogTabs = [
  { id: "overview", label: "전체 현황", description: "구조·연결 상태" },
  { id: "classes", label: "개념", description: "TBox 개념" },
  { id: "relations", label: "관계", description: "TBox 관계" },
  { id: "rules", label: "실행 규칙", description: "TypeDB 규칙" },
  { id: "hypotheses", label: "가설", description: "변화·반증" },
  { id: "inferences", label: "추론 결과", description: "InferenceBox" }
];

var experimentSections = [
  { id: "overview", label: "현황", description: "파이프라인" },
  { id: "validation", label: "검증", description: "리플레이·비교" },
  { id: "promotion", label: "승격", description: "체크·반영" },
  { id: "audit", label: "이력", description: "실행·반영" },
  { id: "proposals", label: "전략제안", description: "승인·성과" }
];

var pageModeOptions = [
  { id: "results", label: "결과", description: "지금 봐야 할 상태와 결과" },
  { id: "settings", label: "설정", description: "편집, 정책, 고급 설정" }
];

var pageModeSectionMap = {
  accounts: {
    results: ["status", "connections", "balance", "history"],
    settings: ["identity"]
  },
  notifications: {
    results: ["status", "candidates"],
    settings: ["policy", "templates", "diagnostics"]
  },
  modeling: {
    results: ["overview", "evidence", "charts", "graphs", "proposals", "hypotheses", "trace"],
    settings: ["rules"]
  },
  feed: {
    results: ["overview", "impact", "themes", "portfolio", "sources"],
    settings: ["settings"]
  }
};

var pageModeEnabledTabs = [];

function activeTabMeta() {
  return tabs.filter(function (tab) { return tab.id === navigationState.activeTab; })[0] || tabs[0];
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
  { key: "investmentCalendarReminder", group: "일정", label: "투자 캘린더", description: "등록한 투자 이벤트의 리마인더 시점에 도달할 때" },
  { key: "newsDigest", group: "데이터", label: "뉴스/피드 새 정보", description: "관련성과 중요도 기준을 통과한 새 뉴스 근거가 들어올 때" },
  { key: "watchlistOntologySignal", group: "온톨로지 근거", label: "관심종목 관계 신호", description: "투자 인사이트에 넣을 TypeDB InferenceBox 기반 관심종목 근거 신호" },
  { key: "holdingTiming", group: "온톨로지 근거", label: "보유 타이밍 신호", description: "투자 인사이트에 넣을 보유 종목 타이밍 근거 신호" },
  { key: "ontologyInferenceMissing", group: "온톨로지 상태", label: "추론 결과 누락", description: "실계좌 데이터는 있지만 그래프 저장소 InferenceBox 추론 결과가 없을 때" },
  { key: "monitorHeartbeat", group: "실시간", label: "상태 확인 메시지", description: "실시간 워커가 살아 있는지 주기적으로 짧게 보낼 때" },
  { key: "monitorConnection", group: "실시간", label: "연결 상태 변화", description: "실시간 모니터링 중 토스 연결 상태가 바뀔 때" },
  { key: "externalDataConnection", group: "외부 API", label: "외부 API 연결", description: "외부 데이터 API 키, 한도, 응답 오류가 감지될 때" }
];

var userManagedNotificationTypes = ["investmentInsight", "investmentCalendarReminder", "newsDigest", "ontologyInferenceMissing", "monitorConnection", "externalDataConnection"];

var visibleNotificationTemplateTypes = ["default", "investmentInsight", "investmentCalendarReminder", "newsDigest", "ontologyInferenceMissing", "monitorConnection", "externalDataConnection", "modelReview", "workHandoff", "notification"];

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
  investmentCalendarReminder: "🗓️",
  modelBuy: "🟢",
  modelSell: "🔴",
  watchlistBuyCandidate: "👀",
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
  { key: "entrySmartMoneyMin", label: "외국인·기관 합산 순매수", unit: "주", step: "1" },
  { key: "entryTradeStrengthMin", label: "매수 체결강도", unit: "", step: "1" },
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

var feedSections = [
  { id: "overview", label: "요약", description: "시장 흐름" },
  { id: "impact", label: "영향 뉴스", description: "투자 영향" },
  { id: "themes", label: "테마", description: "섹터·자산" },
  { id: "portfolio", label: "내 종목", description: "보유·관심" },
  { id: "sources", label: "소스", description: "품질·신선도" },
  { id: "settings", label: "피드 설정", description: "수집 정책" }
];

var marketWorkspaceModes = [
  { id: "mine", label: "내 종목", description: "보유·관심" },
  { id: "universe", label: "전체 종목", description: "KOSPI·KOSDAQ·NASDAQ" },
  { id: "flow", label: "자금 흐름", description: "자산·섹터·신규 흐름" },
  { id: "news", label: "뉴스·수급", description: "시장 영향" }
];

export { accountSections, activeTabMeta, alertRuleCatalog, alertThresholdCatalog, appBrandName, appBrandSubtitle, bottomTabIds, experimentSections, feedSections, labelWithNotificationIcon, managementTabIds, marketWorkspaceModes, navigationGroupForTab, navigationGroups, notificationMessageTypeIcon, notificationPolicyCatalog, notificationSections, ontologyCatalogTabs, ontologySections, pageModeEnabledTabs, pageModeOptions, pageModeSectionMap, pageStructureMeta, settingsSections, strategySections, tabById, tabs, tabsForNavigationGroup, visibleNotificationTemplateType, webStyleContract };
