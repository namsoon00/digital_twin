import { enabledServiceAccounts, serviceAccounts } from "../accounts/identity.mjs";
import { allAccountWatchlistSymbols } from "../accounts/watchlist.mjs";
import { currentInvestmentCalendar } from "../calendar/commands.mjs";
import { investmentCalendarUpcomingEvents } from "../calendar/workspace.mjs";
import { buildTradeSignalItems, modelStatsForItems } from "../decisions/signals.mjs";
import { ontologyExperimentItems, ontologyExperimentPayload } from "../experiments/workspace.mjs";
import { watchlistSymbols } from "../instruments/catalog.mjs";
import { editorWorkDetailPayload, renderInfoIconButton } from "../navigation/detail.mjs";
import { activePageMode, normalizeTabId, pageSupportsMode } from "../navigation/routes.mjs";
import { activeNotificationDecisionJob, notificationJobDecisionRoute, notificationJobStatusLabel } from "../notifications/history.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { notificationEnabledRuleCount, notificationTemplateItems } from "../notifications/workspace.mjs";
import { promptTemplateRows } from "../ontology/governance.mjs";
import { ontologyRuleboxRules } from "../ontology/world.mjs";
import { notificationJobSummaryText } from "../realtime/events.mjs";
import { currentResearchEvidence } from "../research/requests.mjs";
import { feedImpactCounts } from "../research/workspace.mjs";
import { isConfiguredSetting, modelVariableGuide, settingsStatusLabel, settingsStatusTone } from "../settings/fields.mjs";
import { formatClock, formatMoney } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { activeTabMeta, notificationPolicyCatalog, pageModeOptions, pageStructureMeta, tabById, webStyleContract } from "./catalog.mjs";
import { cardTypeAttrs } from "./layout.mjs";
import { navigationState } from "../state/navigation.mjs";
import { notificationsState } from "../state/notifications.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";
import { universeState } from "../state/universe.mjs";

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
      metrics: [["계정", serviceAccounts().length || 0], ["평가", formatMoney(portfolio.total || 0)], ["알림", enabledRules + "/" + notificationPolicyCatalog().length]],
      flow: ["계정·시장 데이터", "운영 현황", "알림·투자 판단"]
    },
    accounts: {
      steps: [["01", "상태", "계정 진단"], ["02", "연결", "증권사·잔고"], ["03", "저장", "DB 반영"]],
      metrics: [["활성", enabledServiceAccounts().length + "/" + serviceAccounts().length], ["Toss", configuredCount(["tossClientId", "tossClientSecret"]) + "/2"], ["계좌", serviceAccounts().filter(function (account) { return account.accountSeq; }).length + "/" + serviceAccounts().length]],
      flow: ["Toss/API 인증", "계정 원장", "포트폴리오 스냅샷"]
    },
    watchlist: {
      steps: [["01", "계정", "대상 선택"], ["02", "종목", "관찰 편집"], ["03", "연결", "알림 입력"]],
      metrics: [["계정", serviceAccounts().length || 0], ["관심", allAccountWatchlistSymbols().length || watchlistSymbols().length], ["시세", watchlist.length]],
      flow: ["계정·시장 유니버스", "관찰 종목", "시세·뉴스 수집 대상"]
    },
    symbols: {
      steps: [["01", "목록", "시장 유니버스"], ["02", "필터", "검색·구분"], ["03", "편입", "계정 연결"]],
      metrics: [["기본", watchlistSymbols().length], ["계정", allAccountWatchlistSymbols().length], ["시장", symbolMarketCount()]],
      flow: ["KRX/NASDAQ 카탈로그", "종목 검색", "관심종목 편입"]
    },
    calendar: {
      steps: [["01", "예정", "주요 이벤트"], ["02", "등록", "종목·시장"], ["03", "알림", "리마인더 큐"]],
      metrics: [["전체", (currentInvestmentCalendar().summary || {}).total || 0], ["예정", (currentInvestmentCalendar().summary || {}).upcoming || 0], ["다음", formatClock((currentInvestmentCalendar().summary || {}).nextStartsAt)]],
      flow: ["실적·거시·공시 일정", "투자 캘린더 원장", "알림 큐·온톨로지 요청"]
    },
    feed: {
      steps: [["01", "영향", "호재·악재"], ["02", "근거", "기사 요약"], ["03", "소스", "수집 품질"]],
      metrics: [["피드", (shellState.feed && shellState.feed.items ? shellState.feed.items.length : 0)], ["근거", ((currentResearchEvidence().summary || {}).total || 0)], ["오류", (shellState.feed && shellState.feed.errors ? shellState.feed.errors.length : 0)]],
      flow: ["관심·보유 종목 뉴스/공시", "기사 요약·영향 판단", "투자 판단 근거"]
    },
    system: {
      steps: [["01", "지도", "처음 보는 사람"], ["02", "데이터", "수집·저장"], ["03", "이벤트", "알림·추론"]],
      metrics: [["워커", "6"], ["이벤트", "12+"], ["저장소", "MySQL"]],
      flow: ["앱 구조", "데이터 흐름 문서", "운영 기준"]
    },
    notifications: {
      steps: [["01", "상태", "조건·변화"], ["02", "게이트", "발송·보류"], ["03", "본문", "템플릿·발송"]],
      metrics: [["관리 룰", enabledRules + "/" + notificationPolicyCatalog().length], ["템플릿", notificationTemplateItems().length], ["큐", notificationJobSummaryText(shellState.realtime.notificationJobs)]],
      flow: ["추론 결과·변화 상태", "발송/보류 게이트", "알림 이력"]
    },
    modeling: {
      steps: [["01", "판단", "오늘 할 일"], ["02", "근거", "뉴스·차트"], ["03", "검증", "그래프·품질"]],
      metrics: [["보유", positions.length], ["관심", watchlist.length], ["추론 보류", ((snapshot.investmentAnalysis || {}).graphGate || {}).blockedCount || 0]],
      flow: ["계정·시세·뉴스 근거", "온톨로지/모델 판단", "액션 큐·알림 후보"]
    },
    experiments: {
      steps: [["01", "초안", "후보 규칙"], ["02", "재생", "샌드박스"], ["03", "승격", "운영 검토"]],
      metrics: [["전체", (ontologyExperimentPayload().count || ontologyExperimentItems().length || 0)], ["활성", ontologyExperimentPayload().activeCount || 0], ["최근", ((ontologyExperimentPayload().latestRun || {}).promotionStatus || "대기")]],
      flow: ["후보 관계 규칙", "샌드박스 검증", "운영 RuleBox"]
    },
    ontology: {
      steps: [["01", "TBox", "스키마"], ["02", "ABox", "현재 실체"], ["03", "관계", "근거 연결"]],
      metrics: [["TBox", ((strategy.tbox || {}).classes || []).length], ["ABox", abox.entityCount || 0], ["관계", abox.relationCount || strategy.relationCount || 0]],
      flow: ["TBox 규칙", "ABox 관계", "InferenceBox 판단"]
    },
    monitoring: {
      steps: [["01", "스냅샷", "계좌 수집"], ["02", "감지", "가격·수급"], ["03", "상세", "종목 확인"]],
      metrics: [["보유", positions.length], ["관심", watchlist.length], ["평가", formatMoney(portfolio.total || 0)]],
      flow: ["계좌 스냅샷", "가격·수급 감지", "알림 후보"]
    },
    settings: {
      steps: [["01", "기본", "표시·전달"], ["02", "고급", "API·게이트"], ["03", "진단", "잠금·오류"]],
      metrics: [["저장", settingsState.settingsSaved ? "완료" : "대기"], ["잠금", settingsState.serverSettingsLocked ? "읽기전용" : "수정"], ["API", configuredCount(["alphaVantageApiKey", "coingeckoApiKey", "fredApiKey", "opendartApiKey"]) + "/4"]],
      flow: ["운영 정책", "런타임 설정", "수집·알림 동작"]
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
  (universeState.symbolUniverse.items || []).forEach(function (item) {
    var market = String(item.market || item.exchange || "").trim();
    if (market) seen[market] = true;
  });
  return Object.keys(seen).length || "-";
}

function configuredCount(keys) {
  return (keys || []).filter(function (key) {
    return isConfiguredSetting(key);
  }).length;
}

function renderPageCommandStrip(pageId, snapshot) {
  var profile = pageCommandProfile(pageId, snapshot);
  var compact = normalizeTabId(pageId || navigationState.activeTab) !== "overview";
  return [
    '<section class="page-command-strip ' + (compact ? "page-command-strip-compact " : "") + escapeHtml(webStyleContract.commandClass) + '" data-style-layer="command-strip" data-command-group="' + escapeHtml(profile.groupId) + '" aria-label="페이지 작업 상태">',
    '<div class="page-command-context">',
    '<span class="page-command-kicker">' + escapeHtml(profile.group + " / " + profile.layer) + '</span>',
    '<strong>' + escapeHtml(profile.entity) + '</strong>',
    '<em>' + escapeHtml(profile.objective) + '</em>',
    renderPageModeSwitch(pageId),
    '</div>',
    compact ? renderPageFlowSpine(profile) : '',
    '<div class="page-command-flow">',
    profile.steps.map(renderPageCommandStep).join(""),
    '</div>',
    '<div class="page-command-metrics">',
    profile.metrics.map(renderPageCommandMetric).join(""),
    '</div>',
    '</section>'
  ].join("");
}

function renderPageFlowSpine(profile) {
  var flow = Array.isArray(profile.flow) ? profile.flow : [];
  if (flow.length < 3) return "";
  return [
    '<div class="page-flow-spine" aria-label="전체 데이터 흐름상 위치">',
    renderPageFlowNode("이전 입력", flow[0]),
    renderPageFlowNode("현재 처리", flow[1]),
    renderPageFlowNode("다음 출력", flow[2]),
    '</div>'
  ].join("");
}

function renderPageFlowNode(label, value) {
  return [
    '<span class="page-flow-node">',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
    '</span>'
  ].join("");
}

function renderSingleScreenFlowPanel(pageId, snapshot) {
  var normalized = normalizeTabId(pageId || navigationState.activeTab || "overview");
  var profile = pageCommandProfile(normalized, snapshot || {});
  var flow = Array.isArray(profile.flow) ? profile.flow : [];
  if (flow.length < 3) return "";
  return [
    '<section class="single-screen-flow-panel" aria-label="탭 단일 화면 흐름">',
    '<div class="single-screen-flow-copy">',
    '<span class="label">One Screen Flow</span>',
    '<strong>' + escapeHtml(profile.entity || activeTabMeta().label) + '</strong>',
    '<em>' + escapeHtml((profile.steps || []).map(function (step) { return step[1]; }).join(" · ")) + '</em>',
    '</div>',
    '<div class="single-screen-flow-map">',
    renderSingleScreenFlowNode("입력", flow[0]),
    renderSingleScreenFlowNode("처리", flow[1]),
    renderSingleScreenFlowNode("출력", flow[2]),
    '</div>',
    renderInfoIconButton(normalized, "이 탭의 데이터 흐름과 상세 설명"),
    '</section>'
  ].join("");
}

function renderSingleScreenFlowNode(label, value) {
  return [
    '<span class="single-screen-flow-node">',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
    '</span>'
  ].join("");
}

function screenInfoWorkDetailPayload(key) {
  var pageId = normalizeTabId(String(key || navigationState.activeTab || "overview").replace(/^page:/, ""));
  var snapshot = shellState.snapshot || {};
  var profile = pageCommandProfile(pageId, snapshot);
  var structure = pageStructureMeta(pageId);
  var drilldowns = screenDrilldownItems(pageId);
  return editorWorkDetailPayload(
    "Screen Architecture",
    (tabById(pageId) || {}).label || profile.entity || "운영 화면",
    "기본 화면은 하나의 콘솔로 압축하고, 설명·상세·설정은 필요할 때만 엽니다.",
    [
      '<section class="work-detail-section primary">',
      '<strong>이 탭의 역할</strong>',
      '<p>' + escapeHtml(structure.objective || profile.objective || "") + '</p>',
      '</section>',
      '<section class="work-detail-section">',
      '<strong>데이터 흐름</strong>',
      '<div class="screen-info-flow">',
      renderSingleScreenFlowNode("입력", (profile.flow || [])[0]),
      renderSingleScreenFlowNode("처리", (profile.flow || [])[1]),
      renderSingleScreenFlowNode("출력", (profile.flow || [])[2]),
      '</div>',
      '</section>',
      '<section class="work-detail-section">',
      '<strong>기본 화면에 남기는 것</strong>',
      '<div class="work-detail-metric-row">',
      (profile.steps || []).map(function (step) {
        return renderNotificationDetailMetric(step[1], step[2], "hold");
      }).join(""),
      '</div>',
      '</section>',
      drilldowns.length ? '<section class="work-detail-section"><strong>상세로 분리되는 것</strong><div class="work-detail-list">' + drilldowns.map(function (item) {
        return [
          '<div class="work-detail-row">',
          '<b>' + escapeHtml(item[0]) + '</b>',
          '<div><strong>' + escapeHtml(item[1]) + '</strong><span>' + escapeHtml(item[2]) + '</span></div>',
          '<em>' + escapeHtml(item[3]) + '</em>',
          '</div>'
        ].join("");
      }).join("") + '</div></section>' : ''
    ].join("")
  );
}

function screenDrilldownItems(pageId) {
  var rows = {
    accounts: [
      ["계정", "API 원문·계좌 순번", "수정 폼과 secret 확인은 계정 상세에서만 엽니다.", "설정 레이어"],
      ["자산", "금액 산식과 환율 기준", "요약에는 합계만 두고 산식은 검증 상세로 분리합니다.", "상세 레저"]
    ],
    notifications: [
      ["후보", "발송 전 신호", "기본 화면은 최근 판단 중심, 후보군은 드릴다운으로 봅니다.", "전체화면"],
      ["정책", "반복·쿨다운·템플릿", "운영 중 자주 보지 않는 설정은 설정 레이어로 숨깁니다.", "설정 레이어"]
    ],
    modeling: [
      ["근거", "뉴스·차트·온톨로지", "오늘의 판단 큐에서 선택한 종목만 상세 근거를 엽니다.", "상세 레이어"],
      ["룰", "RuleBox·프롬프트", "운영 화면이 아니라 편집 화면으로 분리합니다.", "설정 레이어"]
    ],
    feed: [
      ["영향", "호재·악재 뉴스", "리스트는 핵심 영향만, 기사 요약과 원문 근거는 상세로 봅니다.", "전체화면"],
      ["소스", "수집 채널·품질", "기본 화면에는 상태만 두고 채널/품질 원장은 상세로 엽니다.", "상세 레이어"]
    ],
    experiments: [
      ["검증", "리플레이·비교", "실험 목록에서 선택한 항목만 전체화면으로 검증합니다.", "전체화면"],
      ["승격", "운영 반영 조건", "승격 체크리스트와 추천 적용은 상세 심사로 보냅니다.", "상세 레이어"]
    ],
    system: [
      ["문서", "전체 설명과 용어", "기본 화면은 상태와 지도만, 긴 설명은 접기와 정보 화면으로 보냅니다.", "정보 화면"],
      ["감사", "TypeDB 행·RuleBox·InferenceBox", "요약 카드만 남기고 원장 행은 상세로 확인합니다.", "상세 레저"]
    ],
    settings: [
      ["기본", "표시·전달", "저장 상태만 보이고 입력 폼은 펼침 영역에서 수정합니다.", "설정 레이어"],
      ["고급", "외부 API·신선도·매핑", "운영 중 자주 보지 않는 값은 고급 상세로 숨깁니다.", "상세 편집"]
    ]
  };
  return rows[pageId] || [
    ["요약", "핵심 상태", "기본 화면에는 오늘 판단에 필요한 정보만 남깁니다.", "운영 콘솔"],
    ["상세", "원장·본문·설정", "긴 데이터는 클릭 후 별도 화면에서 확인합니다.", "상세 레이어"]
  ];
}

function renderPageModeSwitch(pageId) {
  var normalized = normalizeTabId(pageId || navigationState.activeTab);
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

function pageRoutineProfile(pageId, snapshot) {
  var normalized = normalizeTabId(pageId || "overview");
  snapshot = snapshot || {};
  var toss = snapshot.toss || {};
  var portfolio = snapshot.portfolio || {};
  var positions = Array.isArray(toss.positions) ? toss.positions.filter(function (item) {
    return item && item.source !== "cash";
  }) : [];
  var watchlist = Array.isArray(toss.watchlist) ? toss.watchlist : [];
  var notificationJobs = notificationsState.notificationJobItems || [];
  var activeJob = activeNotificationDecisionJob(notificationJobs);
  var movement = notificationJobDecisionRoute(activeJob);
  var evidence = currentResearchEvidence();
  var feedImpact = feedImpactCounts();
  var experiments = ontologyExperimentPayload();
  var defaultProfile = {
    tone: "hold",
    current: "상태 확인 대기",
    reason: "이 화면에서 오늘 확인할 정보를 같은 순서로 정리합니다.",
    action: "상세 보기",
    href: "?tab=" + encodeURIComponent(normalized)
  };
  var profiles = {
    overview: {
      tone: positions.length || notificationJobs.length ? "watch" : "hold",
      current: "계정 " + enabledServiceAccounts().length + "/" + serviceAccounts().length + " · 평가 " + formatMoney(portfolio.total || 0),
      reason: "오늘 먼저 봐야 할 연결, 포트폴리오, 알림 상태를 한 번에 모읍니다.",
      action: "알림 운영 보기",
      href: "?tab=notifications"
    },
    accounts: {
      tone: enabledServiceAccounts().length ? "watch" : "caution",
      current: "활성 계정 " + enabledServiceAccounts().length + "/" + serviceAccounts().length + " · Toss " + configuredCount(["tossClientId", "tossClientSecret"]) + "/2",
      reason: "계좌와 API 상태가 틀어지면 모든 판단의 입력이 흔들립니다.",
      action: "계정 설정",
      href: "?tab=accounts&account=identity"
    },
    watchlist: {
      tone: (allAccountWatchlistSymbols().length || watchlistSymbols().length) ? "watch" : "hold",
      current: "관심종목 " + (allAccountWatchlistSymbols().length || watchlistSymbols().length) + "개 · 실시간 시세 " + watchlist.length + "개",
      reason: "관심종목은 뉴스·근거, 알림 후보, 투자 판단의 관찰 입력입니다.",
      action: "관심종목 정리",
      href: "?tab=watchlist"
    },
    symbols: {
      tone: symbolMarketCount() === "-" ? "hold" : "watch",
      current: "시장 " + symbolMarketCount() + "개 · 기본 관심 " + watchlistSymbols().length + "개",
      reason: "전체 종목 카탈로그에서 추적할 종목을 찾고 계정별 관심 목록으로 넘깁니다.",
      action: "종목 검색",
      href: "?tab=symbols"
    },
    calendar: {
      tone: investmentCalendarUpcomingEvents().length ? "watch" : "hold",
      current: "예정 " + ((currentInvestmentCalendar().summary || {}).upcoming || investmentCalendarUpcomingEvents().length || 0) + "개 · 다음 " + formatClock((currentInvestmentCalendar().summary || {}).nextStartsAt),
      reason: "실적, 거시지표, 공시 같은 시간 기반 이벤트는 알림 누락보다 사전 등록 상태를 먼저 봐야 합니다.",
      action: "이벤트 등록",
      href: "?tab=calendar"
    },
    notifications: {
      tone: movement.tone,
      current: activeJob ? "최근 판단 " + notificationJobStatusLabel(activeJob.status) + " · " + movement.label : "최근 판단 없음",
      reason: activeJob ? "알림은 제목보다 상태 변화와 발송·보류 이유를 먼저 봐야 합니다." : "알림 워커가 판단을 남기면 상태 변화와 게이트를 이 위치에 표시합니다.",
      action: activeJob ? "판단 상세 확인" : "후보 신호 보기",
      href: activeJob ? "?tab=notifications" : "?tab=notifications&notification=candidates"
    },
    modeling: {
      tone: positions.length ? "watch" : "hold",
      current: "보유 " + positions.length + "개 · 관심 " + watchlist.length + "개",
      reason: "오늘의 판단을 먼저 보고, 필요할 때 근거와 검증 단계로 내려갑니다.",
      action: "투자 근거 확인",
      href: "?tab=modeling&strategy=evidence"
    },
    experiments: {
      tone: Number(experiments.activeCount || 0) ? "watch" : "hold",
      current: "전략 검증 " + (experiments.count || ontologyExperimentItems().length || 0) + "개 · 활성 " + (experiments.activeCount || 0) + "개",
      reason: "운영 룰로 올리기 전 새 관계 규칙을 샌드박스에서 검증합니다.",
      action: "실험 검토",
      href: "?tab=experiments"
    },
    feed: {
      tone: feedImpact.danger ? "danger" : (feedImpact.watch ? "watch" : "hold"),
      current: "호재 " + feedImpact.watch + " · 악재 " + feedImpact.danger + " · 중립 " + feedImpact.hold,
      reason: "기사 제목보다 기사 요약과 주가 영향 방향을 먼저 판단합니다.",
      action: "투자 판단에 반영",
      href: "?tab=modeling&strategy=evidence"
    },
    system: {
      tone: "hold",
      current: "수집 → 추론 → 알림 흐름 문서",
      reason: "새 기능을 붙이거나 문제가 생겼을 때 전체 데이터 흐름을 확인합니다.",
      action: "운영 기준 확인",
      href: "?tab=system"
    },
    settings: {
      tone: settingsStatusTone(),
      current: settingsStatusLabel() + " · 외부 API " + configuredCount(["alphaVantageApiKey", "coingeckoApiKey", "fredApiKey", "opendartApiKey"]) + "/4",
      reason: "기본 설정만 먼저 보고, API·게이트·매핑은 고급 설정에서 필요할 때만 엽니다.",
      action: "기본 설정 확인",
      href: "?tab=settings"
    }
  };
  return profiles[normalized] || defaultProfile;
}

function renderPageRoutinePanel(pageId, snapshot) {
  var profile = pageRoutineProfile(pageId, snapshot);
  return [
    '<section class="page-routine-panel ' + escapeHtml(profile.tone || "hold") + '" aria-label="오늘의 화면 루틴">',
    renderPageRoutineCell("현재 상태", profile.current, "state"),
    renderPageRoutineCell("왜 봐야 하나", profile.reason, "reason"),
    '<div class="page-routine-cell action">',
    '<span>다음 행동</span>',
    '<a class="text-button primary page-routine-action" href="' + escapeHtml(profile.href || "?tab=" + normalizeTabId(pageId)) + '">' + escapeHtml(profile.action || "상세 보기") + '</a>',
    '</div>',
    '</section>'
  ].join("");
}

function renderPageRoutineCell(label, value, kind) {
  return [
    '<div class="page-routine-cell ' + escapeHtml(kind || "") + '">',
    '<span>' + escapeHtml(label) + '</span>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
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

function renderStrategyProcessPanel(snapshot) {
  var items = buildTradeSignalItems(snapshot);
  var stats = modelStatsForItems(items);
  var ruleboxRules = ontologyRuleboxRules();
  var ruleboxCount = ontologyState.ontologyRuleboxLoaded
    ? (ruleboxRules.length || ((ontologyState.ontologyRulebox || {}).ruleCount || 0))
    : ((ontologyState.ontologyRulebox || {}).ruleCount || 0);
  var steps = [
    ["01", "데이터 정합", "보유·관심·시장 입력", items.length + " symbols"],
    ["02", "근거 추출", "손익·수급·추세·외부 신호", modelVariableGuide().length + " fields"],
    ["03", "RuleBox", "TypeDB 네이티브 관계 추론", (ontologyState.ontologyRuleboxLoaded ? ruleboxCount : "lazy") + " rules"],
    ["04", "AI Prompt", "비동기 해석 정보", promptTemplateRows().length + " prompts"],
    ["05", "Result", "종목별 판단 결과", "대응 " + stats.actionCount + " · 확인 " + stats.checkCount],
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
    '<span>모델링 화면은 실제 값, 성립 조건, 자료 상태, 결과를 순서대로 검증하는 운영 화면입니다.</span>',
    '<span>자료가 부족하거나 AI 검증이 막히면 행동 판단을 만들지 않고 보류 이유를 표시합니다.</span>',
    '</div>',
    '</article>'
  ].join("");
}

function renderProcessStep(step) {
  return [
    '<div class="process-step">',
    '<b>' + escapeHtml(step[0]) + '</b>',
    '<span' + cardTypeAttrs("metric-cell") + '>',
    '<strong>' + escapeHtml(step[1]) + '</strong>',
    '<em>' + escapeHtml(step[2]) + '</em>',
    '</span>',
    '<i>' + escapeHtml(step[3]) + '</i>',
    '</div>'
  ].join("");
}

export { configuredCount, pageCommandProfile, renderPageCommandStrip, renderPageRoutinePanel, renderSingleScreenFlowPanel, screenInfoWorkDetailPayload };
