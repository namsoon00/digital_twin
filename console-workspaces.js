(function () {
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function number(value) {
    var parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function money(value) {
    return new Intl.NumberFormat("ko-KR", {
      style: "currency",
      currency: "KRW",
      maximumFractionDigits: 0
    }).format(number(value));
  }

  function percent(value) {
    var parsed = number(value);
    return (parsed > 0 ? "+" : "") + parsed.toFixed(1) + "%";
  }

  function quantity(value) {
    return new Intl.NumberFormat("ko-KR", { maximumFractionDigits: 4 }).format(number(value));
  }

  function clock(value) {
    var parsed = new Date(value || "");
    if (Number.isNaN(parsed.getTime())) return "기준 시각 없음";
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false
    }).format(parsed);
  }

  function tone(value, inverse) {
    var parsed = number(value);
    if (parsed === 0) return "neutral";
    if (inverse) return parsed > 0 ? "danger" : "positive";
    return parsed > 0 ? "positive" : "danger";
  }

  function empty(title, detail) {
    return '<div class="cws-empty"><strong>' + escapeHtml(title) + '</strong><span>' + escapeHtml(detail || "") + '</span></div>';
  }

  function metric(label, value, detail, state) {
    return [
      '<div class="cws-metric ' + escapeHtml(state || "neutral") + '">',
      '<span>' + escapeHtml(label) + '</span>',
      '<strong>' + escapeHtml(value) + '</strong>',
      '<em>' + escapeHtml(detail || "") + '</em>',
      '</div>'
    ].join("");
  }

  function sectionTabs(items, active, attribute) {
    return '<nav class="cws-tabs" aria-label="화면 보기">' + items.map(function (item) {
      var selected = item[0] === active;
      return '<button type="button" ' + attribute + '="' + escapeHtml(item[0]) + '" class="' + (selected ? "active" : "") + '"' + (selected ? ' aria-current="page"' : '') + '><strong>' + escapeHtml(item[1]) + '</strong><span>' + escapeHtml(item[2]) + '</span></button>';
    }).join("") + '</nav>';
  }

  function portfolioSummary(payload) {
    var summary = payload.summary || {};
    var positions = Array.isArray(payload.positions) ? payload.positions : [];
    var breaches = Array.isArray(payload.policyBreaches) ? payload.policyBreaches : [];
    var risk = payload.risk || {};
    var exposureRows = positions.length ? '<div class="cws-exposure-list">' + positions.map(function (item) {
      var width = Math.max(0, Math.min(100, number(item.currentWeightPct)));
      return [
        '<button type="button" class="cws-exposure-row" data-work-detail="market-instrument" data-work-detail-key="' + escapeHtml(item.symbol) + '">',
        '<span><strong>' + escapeHtml(item.name || item.symbol) + '</strong><em>' + escapeHtml(item.symbol + " · " + quantity(item.quantity) + "주") + '</em></span>',
        '<span class="cws-weight"><i style="--weight:' + width + '%"></i><b>' + escapeHtml(percent(item.currentWeightPct)) + '</b></span>',
        '<span class="' + tone(item.profitLossRate) + '"><strong>' + escapeHtml(percent(item.profitLossRate)) + '</strong><em>' + escapeHtml(money(item.marketValueKrw)) + '</em></span>',
        '<b aria-hidden="true">→</b>',
        '</button>'
      ].join("");
    }).join("") + '</div>' : empty("보유 종목이 없습니다", "계좌 스냅샷이 갱신되면 표시됩니다.");
    var breachRows = breaches.length ? '<div class="cws-policy-list">' + breaches.map(function (item) {
      return '<div><span>' + escapeHtml([item.exposure_type, item.key].filter(Boolean).join(" · ")) + '</span><strong class="danger">한도 ' + escapeHtml(percent(item.policyDeltaPct)) + ' 초과</strong></div>';
    }).join("") + '</div>' : '<div class="cws-policy-clear"><strong>배분 한도 안</strong><span>현재 저장된 노출 기준</span></div>';
    return [
      '<div class="cws-grid cws-grid-primary">',
      '<section class="cws-section"><header><div><span>보유 구성</span><h2>종목별 노출</h2></div><strong>' + positions.length + '개</strong></header>' + exposureRows + '</section>',
      '<section class="cws-section"><header><div><span>위험 예산</span><h2>정책 이탈</h2></div><strong>' + breaches.length + '건</strong></header>',
      breachRows,
      '<dl class="cws-risk-facts"><div><dt>기간 수익률</dt><dd class="' + tone(risk.periodReturnPct) + '">' + escapeHtml(percent(risk.periodReturnPct)) + '</dd></div><div><dt>연환산 변동성</dt><dd>' + escapeHtml(percent(risk.annualizedVolatilityPct)) + '</dd></div><div><dt>최대 낙폭</dt><dd class="danger">' + escapeHtml(percent(risk.maximumDrawdownPct)) + '</dd></div><div><dt>표본</dt><dd>' + escapeHtml(risk.sampleCount || 0) + '개</dd></div></dl>',
      '</section>',
      '</div>',
      '<section class="cws-data-line"><span>원장 대사 <strong>' + escapeHtml(summary.reconciliationStatus || "unknown") + '</strong></span><span>리밸런싱 <strong>' + escapeHtml(summary.rebalanceStatus || "not-ready") + '</strong></span><span>기준 <strong>' + escapeHtml(clock(summary.observedAt)) + '</strong></span></section>'
    ].join("");
  }

  function portfolioPositions(payload) {
    var items = Array.isArray(payload.positions) ? payload.positions : [];
    if (!items.length) return empty("보유 종목이 없습니다", "현재 원장에 열린 포지션이 없습니다.");
    return '<section class="cws-section cws-section-table"><header><div><span>포지션</span><h2>보유 종목 원장</h2></div><strong>' + items.length + '개</strong></header><div class="cws-table"><div class="cws-table-head cws-position-columns"><span>종목</span><span>수량</span><span>평균가</span><span>현재가</span><span>평가액</span><span>손익</span></div>' + items.map(function (item) {
      return '<button type="button" class="cws-table-row cws-position-columns" data-work-detail="market-instrument" data-work-detail-key="' + escapeHtml(item.symbol) + '"><span><strong>' + escapeHtml(item.name || item.symbol) + '</strong><em>' + escapeHtml(item.symbol + " · " + (item.market || "-")) + '</em></span><span>' + escapeHtml(quantity(item.quantity)) + '</span><span>' + escapeHtml(quantity(item.averagePrice)) + '</span><span>' + escapeHtml(quantity(item.currentPrice)) + '</span><span>' + escapeHtml(money(item.marketValueKrw)) + '</span><span class="' + tone(item.profitLossRate) + '"><strong>' + escapeHtml(percent(item.profitLossRate)) + '</strong><em>비중 ' + escapeHtml(percent(item.currentWeightPct)) + '</em></span></button>';
    }).join("") + '</div></section>';
  }

  function portfolioRebalance(payload) {
    var proposal = payload.proposal || {};
    var scenarios = Array.isArray(proposal.scenarios) ? proposal.scenarios : [];
    var candidates = Array.isArray(payload.candidates) ? payload.candidates : [];
    var breaches = Array.isArray(payload.policyBreaches) ? payload.policyBreaches : [];
    var scenarioRows = scenarios.length ? '<div class="cws-scenario-list">' + scenarios.map(function (item) {
      var legs = Array.isArray(item.legs) ? item.legs : [];
      return '<article><header><span><strong>' + escapeHtml(item.label || item.scenario_type || "시나리오") + '</strong><em>' + escapeHtml(item.data_state || "자료 확인") + '</em></span><b>' + escapeHtml(percent(item.turnover_pct)) + ' 회전</b></header><p>' + escapeHtml((item.policy_effects || ["정책 영향을 확인하세요."])[0]) + '</p><footer><span>주문 후보 ' + legs.length + '건</span><span>예상 비용 ' + money(item.estimated_cost) + '</span></footer></article>';
    }).join("") + '</div>' : empty("리밸런싱 시나리오가 없습니다", "다음 계좌 평가 주기에 다시 계산됩니다.");
    var candidateRows = candidates.length ? '<div class="cws-candidate-list">' + candidates.map(function (item) {
      return '<div><span><strong>' + escapeHtml(item.label || item.candidate_type) + '</strong><em>' + escapeHtml(item.affected_symbol || "포트폴리오 전체") + '</em></span><span class="' + (item.executable ? "positive" : "warning") + '">' + escapeHtml(item.executable ? "실행 가능" : "추론 확인") + '</span><b>' + escapeHtml(money(item.maximum_notional)) + '</b></div>';
    }).join("") + '</div>' : empty("행동 후보가 없습니다", "정책 이탈이 생기면 검토 후보가 생성됩니다.");
    return '<div class="cws-grid cws-grid-primary"><section class="cws-section"><header><div><span>배분 대안</span><h2>리밸런싱 시나리오</h2></div><strong>' + escapeHtml(proposal.status || "not-ready") + '</strong></header>' + scenarioRows + '</section><section class="cws-section"><header><div><span>검토 범위</span><h2>행동 후보</h2></div><strong>' + candidates.length + '건</strong></header>' + candidateRows + '<footer class="cws-section-note">정책 이탈 ' + breaches.length + '건 · 자동 주문 없음</footer></section></div>';
  }

  function portfolioActivity(payload) {
    var activities = Array.isArray(payload.activityEpisodes) ? payload.activityEpisodes : [];
    var ledger = Array.isArray(payload.ledgerEntries) ? payload.ledgerEntries : [];
    var plans = Array.isArray(payload.actionPlans) ? payload.actionPlans : [];
    var reviews = Array.isArray(payload.decisionReviews) ? payload.decisionReviews : [];
    var rows = activities.concat(plans, reviews, ledger).sort(function (left, right) {
      var leftAt = left.observedAt || left.reviewedAt || left.reviewed_at || left.createdAt || left.created_at || left.occurredAt || left.occurred_at || "";
      var rightAt = right.observedAt || right.reviewedAt || right.reviewed_at || right.createdAt || right.created_at || right.occurredAt || right.occurred_at || "";
      return new Date(rightAt).getTime() - new Date(leftAt).getTime();
    });
    if (!rows.length) return empty("원장 활동이 없습니다", "수량·현금 변화가 생기면 시간순으로 기록됩니다.");
    return '<section class="cws-section"><header><div><span>원장·행동</span><h2>계좌 활동</h2></div><strong>' + rows.length + '건</strong></header><div class="cws-timeline">' + rows.map(function (item) {
      var title = item.title || item.activityType || item.classification || item.action || item.entryType || item.entry_type || (item.reviewId || item.review_id ? "판단 사후 검토" : "계좌 변화");
      var symbols = Array.isArray(item.symbols) ? item.symbols.join(", ") : "";
      var symbol = item.symbol || item.subjectSymbol || symbols || item.key || "";
      var detail = item.summary || item.reason || item.description || item.status || item.source || (item.orderIntentCount != null ? "주문 후보 " + item.orderIntentCount + "건" : "저장된 원장 기록");
      var at = item.observedAt || item.reviewedAt || item.reviewed_at || item.occurredAt || item.occurred_at || item.createdAt || item.created_at || item.updatedAt || item.recordedAt;
      return '<article><time>' + escapeHtml(clock(at)) + '</time><span><strong>' + escapeHtml(title) + '</strong><em>' + escapeHtml([symbol, detail].filter(Boolean).join(" · ")) + '</em></span></article>';
    }).join("") + '</div></section>';
  }

  function renderPortfolio(payload, activeView, options) {
    payload = payload || {};
    options = options || {};
    var summary = payload.summary || {};
    var view = ["summary", "positions", "rebalance", "activity"].indexOf(activeView) >= 0 ? activeView : "summary";
    var content = options.loading && !payload.version
      ? '<div class="cws-loading" aria-busy="true"><span></span><strong>포트폴리오 원장을 읽고 있습니다.</strong></div>'
      : options.error && !payload.version
        ? empty("포트폴리오를 불러오지 못했습니다", options.error)
        : view === "positions" ? portfolioPositions(payload)
          : view === "rebalance" ? portfolioRebalance(payload)
            : view === "activity" ? portfolioActivity(payload)
              : portfolioSummary(payload);
    return [
      '<div class="cws-page cws-portfolio">',
      '<div class="cws-metrics">',
      metric("총 평가", money(summary.total), summary.positionCount + "개 보유"),
      metric("현금", money(summary.cash), "비중 " + percent(summary.cashWeightPct), summary.cashWeightPct < 3 ? "danger" : "neutral"),
      metric("기간 수익률", percent(summary.periodReturnPct), "저장 시계열 기준", tone(summary.periodReturnPct)),
      metric("최대 낙폭", percent(summary.maximumDrawdownPct), "위험 표본", "danger"),
      metric("정책 이탈", (summary.policyBreachCount || 0) + "건", summary.rebalanceStatus || "배분 확인", summary.policyBreachCount ? "danger" : "positive"),
      '</div>',
      sectionTabs([["summary", "요약", "노출·위험"], ["positions", "보유", "수량·손익"], ["rebalance", "리밸런싱", "정책·대안"], ["activity", "활동", "원장·검토"]], view, "data-portfolio-view"),
      '<div class="cws-view" data-portfolio-active="' + escapeHtml(view) + '">' + content + '</div>',
      '</div>'
    ].join("");
  }

  function healthLabel(state) {
    return { healthy: "정상", warning: "확인", critical: "장애", unknown: "미확인" }[String(state || "")] || state || "미확인";
  }

  function performanceSummary(payload, name) {
    var rows = payload && Array.isArray(payload.summary) ? payload.summary : [];
    return rows.filter(function (item) { return item.name === name; })[0] || {};
  }

  function operationsWebPerformance(performance) {
    var render = performanceSummary(performance, "render");
    var api = performanceSummary(performance, "api-request");
    var cache = performanceSummary(performance, "api-cache-hit");
    var hasSamples = number(render.sampleCount) + number(api.sampleCount) > 0;
    if (!hasSamples) return "";
    return '<section class="cws-section cws-web-performance"><header><div><span>브라우저 응답성</span><h2>현재 세션 화면 성능</h2></div><strong>최근 ' + escapeHtml((performance.samples || []).length) + '회</strong></header><div class="cws-metrics">' +
      metric("화면 렌더 p95", number(render.p95Ms).toFixed(1) + "ms", "최대 " + number(render.maxMs).toFixed(1) + "ms", number(render.p95Ms) <= 200 ? "positive" : "warning") +
      metric("API p95", number(api.p95Ms).toFixed(0) + "ms", "최대 " + number(api.maxMs).toFixed(0) + "ms", number(api.p95Ms) <= 1000 ? "positive" : "warning") +
      metric("렌더 표본", number(render.sampleCount) + "회", "현재 브라우저 세션") +
      metric("API 표본", number(api.sampleCount) + "회", "실제 네트워크 요청") +
      metric("캐시 적중", number(cache.sampleCount) + "회", "중복 읽기 절감", "positive") +
      '</div><footer class="cws-section-note">렌더 p95 200ms, API p95 1,000ms를 화면 반응성 기준으로 사용합니다.</footer></section>';
  }

  function operationsHealth(payload, performance) {
    var items = Array.isArray(payload.components) ? payload.components : [];
    var storage = payload.storage || {};
    var retention = storage.retentionPolicy || {};
    var storageSummary = '<div class="cws-metrics">' +
      metric("공용 디스크", number(storage.freeMb).toFixed(0) + "MB", "여유 공간") +
      metric("MySQL 파일", number(storage.mysqlSizeMb).toFixed(1) + "MB", "한도 " + number(storage.mysqlLimitMb).toFixed(0) + "MB", storage.mysqlCapacityStage === "normal" ? "positive" : "danger") +
      metric("MySQL 실데이터", number(storage.mysqlLiveDataMb).toFixed(1) + "MB", "회수 가능 " + number(storage.mysqlReclaimableMb).toFixed(1) + "MB") +
      metric("TypeDB", number(storage.typedbSizeMb).toFixed(1) + "MB", "WAL " + number(storage.typedbWalMb).toFixed(1) + "MB") +
      '</div>';
    var retentionSummary = '<section class="cws-section"><header><div><span>보관 정책</span><h2>검증 이력·시계열</h2></div></header><dl class="cws-queue-facts">' +
      '<div><dt>TypeDB</dt><dd>' + escapeHtml(retention.typedbActiveHours || 72) + '시간 · WAL ' + escapeHtml(retention.typedbWalTriggerMb || 4096) + 'MB</dd></div>' +
      '<div><dt>알림 원문</dt><dd>' + escapeHtml(retention.notificationPayloadDays || 30) + '일</dd></div>' +
      '<div><dt>추론 사례</dt><dd>' + escapeHtml(retention.reasoningCaseDays || 90) + '일</dd></div>' +
      '<div><dt>시계열</dt><dd>3분 ' + escapeHtml((retention.timeSeriesDays || {})["3m"] || 7) + '일 · 일봉 ' + escapeHtml((retention.timeSeriesDays || {})["1d"] || 1825) + '일</dd></div>' +
      '</dl></section>';
    return storageSummary + operationsWebPerformance(performance) + retentionSummary + '<section class="cws-section cws-section-table"><header><div><span>실행 상태</span><h2>핵심 구성요소</h2></div><strong>' + items.length + '개</strong></header><div class="cws-health-list">' + items.map(function (item) {
      return '<article class="' + escapeHtml(item.state || "unknown") + '"><span class="cws-health-dot" aria-hidden="true"></span><div><strong>' + escapeHtml(item.label) + '</strong><em>' + escapeHtml(item.detail) + '</em></div><span><b>' + escapeHtml(healthLabel(item.state)) + '</b><time>' + escapeHtml(item.updatedAt ? clock(item.updatedAt) : "") + '</time></span></article>';
    }).join("") + '</div></section>';
  }

  function operationsData(payload) {
    var items = Array.isArray(payload.providers) ? payload.providers : [];
    if (!items.length) return empty("공급자 상태가 없습니다", "외부 데이터 수집 워커 상태를 확인하세요.");
    return '<section class="cws-section cws-section-table"><header><div><span>외부 데이터</span><h2>공급자 상태</h2></div><strong>' + items.length + '개</strong></header><div class="cws-table"><div class="cws-table-head cws-provider-columns"><span>공급자</span><span>데이터셋</span><span>상태</span><span>성공</span><span>오류</span></div>' + items.map(function (item) {
      var state = item.state === "healthy" ? "positive" : "danger";
      return '<div class="cws-table-row cws-provider-columns"><span><strong>' + escapeHtml(item.providerId || "-") + '</strong><em>요청 ' + escapeHtml(item.requestCount || 0) + '회</em></span><span>' + escapeHtml(item.datasetId || "-") + '</span><span class="' + state + '"><strong>' + escapeHtml(item.state || "unknown") + '</strong></span><span>' + escapeHtml(clock(item.lastSuccessAt)) + '</span><span>' + escapeHtml(item.lastError || "없음") + '</span></div>';
    }).join("") + '</div></section>';
  }

  function queueFacts(queue, labels) {
    queue = queue || {};
    return '<dl class="cws-queue-facts">' + labels.map(function (item) {
      return '<div><dt>' + escapeHtml(item[1]) + '</dt><dd>' + escapeHtml(queue[item[0]] || 0) + '</dd></div>';
    }).join("") + '</dl>';
  }

  function operationsReasoning(payload) {
    var queues = payload.queues || {};
    var engine = queues.engine || {};
    var deployments = engine.deployments || {};
    var roles = [["delivery", "운영"], ["candidate", "후보"]];
    var deploymentRows = roles.map(function (role) {
      var item = deployments[role[0]] || {};
      var heartbeat = (engine.workerLiveness || {})[role[0]] || {};
      if (!item.deploymentId) return "";
      var pending = number(item.pendingCount) + number(item.awaitingSourceCount) + number(item.awaitingWorldProjectionCount);
      return '<article><span><strong>' + escapeHtml(role[1] + " · " + item.deploymentId) + '</strong><em>' + escapeHtml(item.productionDelivery ? "알림 전달 가능" : "비교 전용") + '</em></span><span class="' + (pending ? "warning" : "positive") + '"><strong>대기 ' + escapeHtml(pending) + '건</strong><em>심박 ' + escapeHtml(heartbeat.updatedAt ? clock(heartbeat.updatedAt) : "미확인") + '</em></span></article>';
    }).join("");
    var active = engine.activeDeployment || {};
    var ruleExecution = active.ruleExecutionReadiness || {};
    var reasons = Array.isArray(engine.reasons) ? engine.reasons : [];
    return '<div class="cws-grid cws-grid-primary"><section class="cws-section"><header><div><span>온톨로지</span><h2>관계 추론 대기열</h2></div></header>' + queueFacts(queues.reasoning, [["pending", "대기"], ["processing", "처리"], ["retrying", "재시도"]]) + '<footer class="cws-section-note">가장 오래된 요청 ' + escapeHtml(queues.reasoning && queues.reasoning.oldestRequestAt ? clock(queues.reasoning.oldestRequestAt) : "없음") + '</footer></section><section class="cws-section"><header><div><span>AI 판단</span><h2>검증 대기열</h2></div></header>' + queueFacts(queues.ai, [["pendingCount", "대기"], ["processingCount", "처리"], ["retryCount", "재시도"], ["failedCount", "실패"]]) + '</section></div>' +
      '<div class="cws-grid cws-grid-primary"><section class="cws-section"><header><div><span>배포별 소비</span><h2>운영·후보 추론 워커</h2></div><strong>' + escapeHtml(engine.status || "unknown") + '</strong></header><div class="cws-health-list">' + (deploymentRows || empty("배포 정보가 없습니다", "제어 포인터를 확인하세요.")) + '</div></section><section class="cws-section"><header><div><span>TypeDB 실행</span><h2>규칙 실행 경로</h2></div><strong class="positive">직접 TypeQL</strong></header><dl class="cws-queue-facts"><div><dt>상태</dt><dd>' + escapeHtml(ruleExecution.status || "ready") + '</dd></div><div><dt>실행 모드</dt><dd>' + escapeHtml(ruleExecution.mode || "typedb-direct-typeql") + '</dd></div></dl><footer class="cws-section-note">' + escapeHtml(reasons.length ? reasons.join(" · ") : "운영 차단 사유 없음") + '</footer></section></div>';
  }

  function operationsDelivery(payload) {
    var queue = (payload.queues || {}).notifications || {};
    return '<div class="cws-grid cws-grid-primary"><section class="cws-section"><header><div><span>알림 전달</span><h2>전송 상태</h2></div></header>' + queueFacts(queue, [["pending", "대기"], ["awaiting_ai", "AI 대기"], ["processing", "처리"], ["done", "완료"], ["suppressed", "보류"], ["failed", "실패"]]) + '<footer class="cws-section-actions"><button type="button" data-tab="notifications">투자 알림 보기</button><button type="button" data-work-detail="notification-diagnostics-board" data-work-detail-key="">전달 진단</button></footer></section><section class="cws-section"><header><div><span>운영 채널</span><h2>오류 알림·정책</h2></div></header><div class="cws-link-list"><button type="button" data-work-detail="settings-operations-notifications" data-work-detail-key=""><span><strong>운영자 채널</strong><em>시스템 오류와 복구 알림</em></span><b>→</b></button><button type="button" data-work-detail="notification-policy-board" data-work-detail-key=""><span><strong>발송 정책</strong><em>쿨다운·중복·시장 시간</em></span><b>→</b></button></div></section></div>';
  }

  function operationsGovernance() {
    var items = [
      ["investment-model-management", "투자모델·규칙", "릴리스·가설·검증·승격"],
      ["strategy-graphs-board", "TBox·ABox·추론", "개념·관계·규칙·InferenceBox"],
      ["settings-investment-language", "투자 보편언어", "내부 식별자와 사용자 표현"],
      ["calendar-candidate-board", "캘린더 후보", "공식 일정 승인·제외"],
      ["settings-data-sources", "수집 정책", "API·주기·신선도"],
      ["settings-runtime", "런타임 설정", "워커·저장소·성능" ]
    ];
    return '<section class="cws-section"><header><div><span>관리 도구</span><h2>모델·데이터 거버넌스</h2></div></header><div class="cws-link-list cws-link-grid">' + items.map(function (item) {
      return '<button type="button" data-work-detail="' + escapeHtml(item[0]) + '" data-work-detail-key=""><span><strong>' + escapeHtml(item[1]) + '</strong><em>' + escapeHtml(item[2]) + '</em></span><b>→</b></button>';
    }).join("") + '</div></section>';
  }

  function renderOperations(payload, activeView, options) {
    payload = payload || {};
    options = options || {};
    var view = ["health", "data", "reasoning", "delivery", "governance"].indexOf(activeView) >= 0 ? activeView : "health";
    var summary = payload.summary || {};
    var content = options.loading && !payload.version
      ? '<div class="cws-loading" aria-busy="true"><span></span><strong>운영 상태를 병렬로 확인하고 있습니다.</strong></div>'
      : options.error && !payload.version
        ? empty("운영 상태를 불러오지 못했습니다", options.error)
        : view === "data" ? operationsData(payload)
          : view === "reasoning" ? operationsReasoning(payload)
            : view === "delivery" ? operationsDelivery(payload)
              : view === "governance" ? operationsGovernance()
                : operationsHealth(payload, options.webPerformance);
    return [
      '<div class="cws-page cws-operations">',
      '<div class="cws-metrics">',
      metric("전체 상태", healthLabel(payload.state), "실행 구성요소", payload.state === "healthy" ? "positive" : payload.state === "critical" ? "danger" : "warning"),
      metric("정상", (summary.healthy || 0) + "개", "즉시 조치 없음", "positive"),
      metric("확인", ((summary.warning || 0) + (summary.unknown || 0)) + "개", "지연·미확인", summary.warning || summary.unknown ? "warning" : "neutral"),
      metric("장애", (summary.critical || 0) + "개", "운영 조치", summary.critical ? "danger" : "positive"),
      metric("기준", clock(payload.generatedAt), "상태 조회 시각"),
      '</div>',
      sectionTabs([["health", "상태", "구성요소"], ["data", "데이터", "공급자"], ["reasoning", "추론", "TypeDB·AI"], ["delivery", "전달", "알림 큐"], ["governance", "관리", "규칙·설정"]], view, "data-operations-view"),
      '<div class="cws-view" data-operations-active="' + escapeHtml(view) + '">' + content + '</div>',
      '</div>'
    ].join("");
  }

  window.OrbitAlphaConsoleWorkspaces = {
    renderPortfolio: renderPortfolio,
    renderOperations: renderOperations
  };
}());
