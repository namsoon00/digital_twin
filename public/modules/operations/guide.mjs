import { serviceAccounts } from "../accounts/identity.mjs";
import { renderSystemOntologyAuditPanel } from "../ontology/audit.mjs";
import { currentResearchEvidence } from "../research/requests.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { renderManagedPage } from "../shell/pages.mjs";

function renderSystemGuidePage(snapshot) {
  return renderManagedPage("system", snapshot, [
    '<section class="admin-grid system-guide-view">',
    renderSystemGuideHero(snapshot),
    renderSystemOntologyAuditPanel(snapshot),
    renderSystemGuideDetailHub(snapshot),
    '</section>'
  ].join(""));
}

function renderSystemGuideDetailHub(snapshot) {
  return [
    '<article class="panel system-detail-hub">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">System Detail</p>',
    '<h2>운영 문서 상세</h2>',
    '<span>기본 화면은 전체 흐름만 보여주고, 필요한 설명만 펼쳐서 확인합니다.</span>',
    '</div>',
    '</div>',
    '<div class="system-detail-grid">',
    renderSystemDetailDisclosure("처음 사용하는 순서", "계정 등록부터 투자 판단 검토까지", renderSystemQuickManualPanel()),
    renderSystemDetailDisclosure("데이터와 이벤트 흐름", "저장소, 워커, 이벤트 계보", renderSystemDataFlowPanel(snapshot) + renderSystemEventFlowPanel()),
    renderSystemDetailDisclosure("알림과 온톨로지", "알림 생성 경로와 TBox/ABox 모델", renderSystemNotificationFlowPanel() + renderSystemOntologyPanel(snapshot)),
    renderSystemDetailDisclosure("하이닉스 알림 흐름", "000660 알림이 수집, 추론, AI 검증, 발송까지 가는 전체 경로", renderSystemHynixAlertFlowPanel()),
    renderSystemDetailDisclosure("운영 체크와 용어", "문제 점검 순서와 핵심 용어", renderSystemOperationsPanel() + renderSystemGlossaryPanel()),
    '</div>',
    '</article>'
  ].join("");
}

function renderSystemDetailDisclosure(title, description, body) {
  return [
    '<details class="system-detail-disclosure">',
    '<summary><strong>' + escapeHtml(title) + '</strong><span>' + escapeHtml(description || "") + '</span></summary>',
    '<div class="system-detail-disclosure-body">',
    body,
    '</div>',
    '</details>'
  ].join("");
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
    '<p>처음 보는 사람은 먼저 계정과 관심종목을 등록하고, 데이터 수집 상태를 확인한 뒤, 알림 운영과 투자 판단에서 왜 신호가 생겼는지 확인하면 됩니다. 이 탭은 그 전체 흐름을 운영 매뉴얼처럼 설명합니다.</p>',
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
    ["01", "계정 등록", "계정·연결에서 Toss 자격 정보, 계좌 순번, 관심 종목 기준을 저장합니다. 알림 채널과 전달 정책은 알림 운영/운영 설정에서 따로 관리합니다."],
    ["02", "관심종목 구성", "관심 관리 탭에서 계정별 추적 대상을 넣습니다. 보유 종목과 관심 종목은 알림 운영에서 서로 다른 관계로 다룹니다."],
    ["03", "데이터 확인", "뉴스·근거 탭과 종목 탐색 탭에서 종목 카탈로그, 뉴스, 공시, 외부 API 수집 상태를 확인합니다."],
    ["04", "알림 정책 조정", "알림 운영에서 메시지 타입별 사용 여부, 임계값, 발송 템플릿, 최근 판단을 점검합니다."],
    ["05", "투자 판단 검토", "투자 판단에서 가격·수급·추세·공시·뉴스 근거가 어떤 관계 규칙으로 연결됐는지 봅니다."]
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
    ["종목 탐색·시세", "market-data worker", "symbol_universe, market_quote_cache", "시장별 종목명, 현재가, 캔들 기반 이동평균"],
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
        '<div class="system-lineage-row" role="row"' + cardTypeAttrs("source-card") + '>',
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
    '<section class="system-flow-node"' + cardTypeAttrs("process-card") + '>',
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
    ["account.saved", "계정 저장", "계정·연결에서 저장된 계정을 마스킹된 payload로 이벤트 로그에 남깁니다."],
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
        '<section class="system-event-row"' + cardTypeAttrs("process-card") + '>',
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
        '<section class="system-notification-node"' + cardTypeAttrs("process-card") + '>',
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

function renderSystemHynixAlertFlowPanel() {
  var steps = [
    ["01", "데이터 수집", "Toss, KIS, OpenDART, 뉴스, 거시 지표", "하이닉스가 보유 종목이면 Toss 잔고에서 현재가, 평균매입가, 보유 수량, 평가금액, 손익률을 먼저 가져옵니다. KIS에서는 거래량, 체결강도, 호가, 외국인·기관·개인 수급을 붙이고, OpenDART와 뉴스 근거는 새 사건으로 저장합니다."],
    ["02", "스냅샷 저장", "monitoring.snapshot_collected", "한 번의 조회 결과를 계좌 스냅샷으로 저장합니다. 이 시점의 하이닉스 상태가 이후 알림의 기준시각이 됩니다. 이전 스냅샷과 비교할 수 있도록 손익률, 이동평균 위치, 판단 액션, 원천 뉴스 키도 함께 남깁니다."],
    ["03", "ABox 생성", "현재 데이터 그래프", "보유 종목 000660, 손익률, 5일·20일·60일 평균 가격, 거래량, 매수/매도 압력, 투자자별 수급 심리, 뉴스·공시 근거를 TypeDB에 넣을 실제 관계 데이터로 바꿉니다. 여기서 외국인·기관 동반 순매수, 개인 저가매수 위험 같은 수급 심리도 관계로 만들어집니다."],
    ["04", "TypeDB 네이티브 규칙 추론", "ABox → TypeDB native rule → InferenceBox", "저장된 관계 규칙이 TypeDB ABox를 읽고 손실 방어, 회복 확인, 추가매수 차단, 조건부 추가매수 검토 같은 추론 결과를 InferenceBox로 만듭니다. 추론 결과가 없으면 투자 판단 대신 추론 상태 점검 알림이 나옵니다."],
    ["05", "판단 또는 근거 설명", "TypeDB 행동 권한 준수", "TypeDB가 행동 생성 권한을 준 가설만 AI가 매수, 추가매수, 보유, 분할축소, 매도, 회피 판단으로 검증합니다. 위험·제약처럼 단독 행동 권한이 없는 관계는 매매 결론을 만들지 않고 확인할 근거와 다음 조건만 설명합니다."],
    ["06", "알림 게이트", "변화별 네 단계 전달", "손익·행동·주요 기준선 전환은 즉시 변화, 새 뉴스·공시와 중요 관계 변화는 중요 근거, 같은 상태는 정기 요약, 새 가치가 없는 참고 상태는 웹 기록으로 나눕니다. 기본 재알림 간격은 각각 10분, 60분, 360분이며 관리자 설정에서 바꿀 수 있습니다."],
    ["07", "메시지 생성", "템플릿과 사용자 레벨", "왕초보, 초보, 중수, 고수 레벨에 맞춰 문장 난이도와 노출 정보를 조절합니다. 하이닉스 보유 알림에는 현재가, 평균매입가, 수익률, 보유 수량, 종목 평가금액, 계좌 평가금액, 이동평균, 거래량, 매수/매도 압력, 투자자별 수급, 알림이 온 이유, 쿨다운 해제 이유가 들어갑니다."],
    ["08", "발송과 추적", "notification_jobs, Telegram, 웹 알림", "완성된 메시지는 Outbox에 저장되고 알림 워커가 Telegram과 웹 알림 목록으로 보냅니다. 메시지 끝의 알림 추적 번호로 어떤 판단 작업이었는지 다시 찾을 수 있습니다."]
  ];
  var ruleRows = [
    ["손실 방어", "graph.loss_guard.breakdown.v1", "보유 손익률이 계정의 손실 허용 기준보다 나쁘고 20일 또는 60일 평균 가격 아래로 약해지면 성립합니다.", "손실 관리, 분할축소, 매도 검토를 강화합니다."],
    ["수급 방어", "graph.loss_smart_money.defense.v1", "손실 구간이어도 외국인과 기관이 함께 순매수하면 성립합니다.", "전량 매도보다 분할축소나 보유 재확인 쪽으로 판단 강도를 낮출 수 있습니다."],
    ["큰 자금 매집", "graph.investor_flow.smart_money_accumulation.v1", "외국인·기관이 사고 개인이 파는 흐름, 또는 하락 중 외국인·기관이 받아내는 흐름이면 성립합니다.", "회복 가능성을 보는 반대 근거로 쓰지만 이것만으로 추가매수를 확정하지 않습니다."],
    ["개인 저가매수 위험", "graph.investor_flow.retail_dip_buying_risk.v1", "외국인·기관이 함께 팔고 개인이 받아내면 성립합니다.", "물타기 위험으로 보고 추가매수를 차단하거나 손실 기준 확인을 우선합니다."],
    ["조건부 추가매수", "graph.loss_smart_money.add_buy_review.v1", "손실 구간에서 외국인·기관 동반 순매수와 가격 회복, 거래량, 체결강도, 매수 호가 우위 중 여러 확인 조건이 같이 맞으면 성립합니다.", "소액 분할 추가매수 검토가 가능하지만 악재 공시, 비중 초과, 데이터 결측이 있으면 다시 낮춰 봅니다."],
    ["수익 보호", "graph.profit_protect.trend_break.v1", "수익 중인 보유 종목이 계정 수익 보호 기준을 넘었고 20일 평균 아래로 약해지면 성립합니다.", "수익을 지키기 위한 분할축소 기준을 확인합니다."]
  ];
  var gateRows = [
    ["무조건 후보가 되는 변화", "손익률이 계정 손실 구간에 들어가거나 수익 보호 구간에 들어갈 때", "보유 종목은 관심 종목보다 손익과 비중을 우선합니다.", "손익 구간, 보유 비중, 매도 가능 수량을 먼저 봅니다."],
    ["쿨다운을 풀 수 있는 변화", "손익률 1%p 이상 개선·악화, 60일 평균 아래 전환, 새 뉴스·공시, 최종 행동 변경", "같은 유형이어도 실제 상태가 바뀐 것으로 보고 다시 보낼 수 있습니다.", "메시지 상단과 알림이 온 이유에 어떤 변화였는지 표시합니다."],
    ["메시지를 막는 경우", "이전과 같은 조건이 계속되고 새 원천 근거가 없으며 쿨다운 시간도 지나지 않았을 때", "반복 알림으로 집중이 깨지지 않도록 발송을 보류합니다.", "하이닉스가 올라도 1%p 기준을 넘지 않거나 최종 행동이 그대로면 메시지가 안 올 수 있습니다."],
    ["데이터를 약하게 보는 경우", "투자자별 수급이 이전 조회와 같거나 KIS 응답이 비어 있거나 지연 상태일 때", "자료 상태를 일부 부족으로 바꾸고 행동 판단을 제한합니다.", "메시지의 데이터 빈 곳 또는 최신성 설명에 남깁니다."],
    ["추론 상태 알림", "TypeDB InferenceBox 결과가 0개이거나 네이티브 규칙 실행이 실패할 때", "투자 신호가 아니라 판단 엔진 점검 신호입니다.", "매수·매도 의견을 만들지 않고 TypeDB, 네이티브 규칙, 워커 상태를 확인하라고 보냅니다."]
  ];
  return [
    '<article class="panel system-event-panel">',
    '<div class="panel-head"><div><p class="label">HYNIX ALERT FLOW</p><h2>SK하이닉스 알림이 오는 전체 흐름</h2><span>000660 보유 알림은 데이터 수집, TypeDB 관계 추론, AI 의견 검증, 쿨다운 게이트를 모두 통과해야 발송됩니다.</span></div></div>',
    '<div class="system-guide-note">',
    '<strong>한 줄 요약</strong>',
    '<p>하이닉스 메시지는 “가격이 움직였다”만으로 오지 않습니다. 보유 손익, 평균 가격 위치, 투자자별 수급, 뉴스·공시, 계정 투자 성향, 이전 알림 대비 변화가 함께 의미 있다고 판단될 때 옵니다.</p>',
    '</div>',
    '<div class="system-event-track" aria-label="SK하이닉스 알림 처리 단계">',
    steps.map(function (step) {
      return [
        '<section class="system-event-row"' + cardTypeAttrs("process-card") + '>',
        '<b>' + escapeHtml(step[0]) + '</b>',
        '<code>' + escapeHtml(step[2]) + '</code>',
        '<strong>' + escapeHtml(step[1]) + '</strong>',
        '<p>' + escapeHtml(step[3]) + '</p>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    '<div class="system-lineage-grid" role="table" aria-label="SK하이닉스 관계 규칙">',
    '<div class="system-lineage-head" role="row"><span>판단 축</span><span>RuleBox 규칙</span><span>성립 조건</span><span>메시지 영향</span></div>',
    ruleRows.map(function (row) {
      return [
        '<div class="system-lineage-row" role="row"' + cardTypeAttrs("relationship-card") + '>',
        '<strong>' + escapeHtml(row[0]) + '</strong>',
        '<code>' + escapeHtml(row[1]) + '</code>',
        '<span>' + escapeHtml(row[2]) + '</span>',
        '<em>' + escapeHtml(row[3]) + '</em>',
        '</div>'
      ].join("");
    }).join(""),
    '</div>',
    '<div class="system-lineage-grid" role="table" aria-label="SK하이닉스 발송 게이트">',
    '<div class="system-lineage-head" role="row"><span>게이트</span><span>조건</span><span>설명</span><span>운영 포인트</span></div>',
    gateRows.map(function (row) {
      return [
        '<div class="system-lineage-row" role="row"' + cardTypeAttrs("reference-card") + '>',
        '<strong>' + escapeHtml(row[0]) + '</strong>',
        '<code>' + escapeHtml(row[1]) + '</code>',
        '<span>' + escapeHtml(row[2]) + '</span>',
        '<em>' + escapeHtml(row[3]) + '</em>',
        '</div>'
      ].join("");
    }).join(""),
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
        '<section class="system-ontology-card step-' + escapeHtml(index + 1) + '"' + cardTypeAttrs("relationship-card") + '>',
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
    ["매일 먼저 볼 것", "관제 홈에서 연결 상태와 최근 알림, 투자 판단에서 관계 그래프와 근거 카드를 봅니다."],
    ["데이터가 이상할 때", "뉴스·근거 탭의 수집 오류, 종목 탐색 탭의 최신성, 운영 설정 탭의 API 키와 캐시 시간을 순서대로 확인합니다."],
    ["알림이 너무 많을 때", "알림 운영에서 메시지 타입별 사용 여부와 cadence, 임계값을 조정합니다."],
    ["모델 판단이 이상할 때", "투자 판단에서 공식 입력, 기준값, 근거 카드, 반대 근거를 함께 확인합니다."],
    ["외부 공유 전", "로컬 우선 시스템이므로 `.env.local`, API 키, 계좌 정보, DB 접속 정보가 노출되지 않는지 먼저 확인합니다."]
  ];
  return [
    '<article class="panel system-operations-panel">',
    '<div class="panel-head"><div><p class="label">OPERATIONS</p><h2>운영 체크리스트</h2></div></div>',
    '<div class="system-ops-list">',
    rows.map(function (row) {
      return [
        '<section class="system-ops-row"' + cardTypeAttrs("reference-card") + '>',
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
        '<section' + cardTypeAttrs("reference-card") + '>',
        '<strong>' + escapeHtml(term[0]) + '</strong>',
        '<p>' + escapeHtml(term[1]) + '</p>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    '</article>'
  ].join("");
}
