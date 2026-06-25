(function () {
  var app = document.getElementById("app");
  var defaultSettings = {
    watchlistSymbols: "NVDA,TSLA,000660",
    tossApiBaseUrl: "https://openapi.tossinvest.com",
    tossClientId: "",
    tossClientSecret: "",
    tossAccountSeq: ""
  };
  var tabs = [
    { id: "decision", label: "판단" },
    { id: "holdings", label: "보유" },
    { id: "watchlist", label: "관심" },
    { id: "settings", label: "설정" }
  ];
  var settingsMemoryStore = "";
  var state = {
    loading: true,
    refreshing: false,
    error: "",
    snapshot: null,
    dataMode: initialDataMode(),
    activeTab: initialTab(),
    settings: loadSettings(),
    showSecrets: false,
    settingsSaved: false
  };

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
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

  function isStaticPreviewHost() {
    return window.location.protocol === "file:" || /\.github\.io$/i.test(window.location.hostname);
  }

  function initialTab() {
    var params = new URLSearchParams(window.location.search);
    var requested = String(params.get("tab") || "").toLowerCase();
    return tabs.some(function (tab) { return tab.id === requested; }) ? requested : "decision";
  }

  function initialDataMode() {
    var params = new URLSearchParams(window.location.search);
    var queryMode = String(params.get("mock") || params.get("mode") || "").toLowerCase();
    if (queryMode === "1" || queryMode === "true" || queryMode === "mock") return "mock";
    if (isStaticPreviewHost()) return "mock";
    try {
      return window.localStorage.getItem("exitLensDataMode") === "mock" ? "mock" : "live";
    } catch (error) {
      return "live";
    }
  }

  function persistDataMode(value) {
    try {
      window.localStorage.setItem("exitLensDataMode", value);
    } catch (error) {
      // Storage can be unavailable in private contexts; the in-memory state is enough.
    }
  }

  function loadSettings() {
    try {
      var raw = readStoredSettings();
      return Object.assign({}, defaultSettings, raw ? JSON.parse(raw) : {});
    } catch (error) {
      return Object.assign({}, defaultSettings);
    }
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

  function removeStoredSettings() {
    settingsMemoryStore = "";
    try {
      var storage = window.localStorage;
      if (storage) storage.removeItem("exitLensSettings");
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

  function clearSettings() {
    state.settings = Object.assign({}, defaultSettings);
    state.showSecrets = false;
    state.settingsSaved = false;
    if (!removeStoredSettings()) {
      state.error = "브라우저 저장소에서 설정을 삭제하지 못했습니다.";
    }
  }

  function tossLensPath() {
    return state.dataMode === "mock" ? "/api/flow-lens?mock=1" : "/api/flow-lens";
  }

  function staticMockSnapshot() {
    var stamped = new Date().toISOString();
    var positions = [
      {
        symbol: "005930",
        name: "삼성전자",
        source: "holding",
        sector: "반도체",
        market: "KR",
        currency: "KRW",
        quantity: "12",
        sellableQuantity: "12",
        averagePrice: 65000,
        currentPrice: 72000,
        marketValue: 864000,
        profitLoss: 84000,
        profitLossRate: 10.8
      },
      {
        symbol: "AAPL",
        name: "Apple",
        source: "holding",
        sector: "AI/플랫폼",
        market: "US",
        currency: "USD",
        quantity: "2",
        sellableQuantity: "2",
        averagePrice: 210,
        currentPrice: 243.1,
        marketValue: 486.2,
        profitLoss: 66.2,
        profitLossRate: 15.8
      },
      {
        symbol: "CASH",
        name: "대기 현금",
        source: "cash",
        sector: "현금",
        market: "CASH",
        currency: "KRW",
        quantity: "1",
        sellableQuantity: "1",
        averagePrice: 0,
        currentPrice: 0,
        marketValue: 1250000,
        profitLoss: 0,
        profitLossRate: 0
      }
    ];
    var watchlist = [
      { symbol: "NVDA", name: "NVIDIA", source: "watchlist", sector: "반도체", market: "US", currency: "USD", quoteStatus: "시세 조회 대기" },
      { symbol: "TSLA", name: "Tesla", source: "watchlist", sector: "모빌리티", market: "US", currency: "USD", quoteStatus: "시세 조회 대기" },
      { symbol: "000660", name: "SK하이닉스", source: "watchlist", sector: "반도체", market: "KR", currency: "KRW", quoteStatus: "시세 조회 대기" }
    ];
    var decisionItems = [
      {
        symbol: "AAPL",
        name: "Apple",
        source: "holding",
        sector: "AI/플랫폼",
        market: "US",
        currency: "USD",
        marketValue: 486.2,
        profitLoss: 66.2,
        profitLossRate: 15.8,
        exitPressure: 72,
        decision: "분할 매도 기준 확인",
        tone: "danger",
        reasons: ["토스 잔고 기준 수익률이 +15.8%입니다.", "보유 수량 2주가 모두 매도 가능 수량으로 잡혀 있습니다."],
        triggers: ["분할 매도 비율", "평균단가 대비 목표 수익률", "매도 가능 수량"]
      },
      {
        symbol: "005930",
        name: "삼성전자",
        source: "holding",
        sector: "반도체",
        market: "KR",
        currency: "KRW",
        marketValue: 864000,
        profitLoss: 84000,
        profitLossRate: 10.8,
        exitPressure: 64,
        decision: "일부 익절 기준 확인",
        tone: "caution",
        reasons: ["토스 잔고 기준 수익률이 +10.8%입니다.", "반도체 보유 평가액이 계좌 내 주요 노출입니다."],
        triggers: ["평가손익", "보유 비중", "매도 가능 수량"]
      },
      {
        symbol: "NVDA",
        name: "NVIDIA",
        source: "watchlist",
        sector: "반도체",
        market: "US",
        currency: "USD",
        marketValue: 0,
        profitLoss: 0,
        profitLossRate: 0,
        exitPressure: 36,
        decision: "시세 기준 대기",
        tone: "hold",
        reasons: ["관심 종목은 토스 시세 연결 후 현재가와 기준가를 비교해야 합니다."],
        triggers: ["관심 종목", "현재가", "기준가"]
      }
    ];
    return {
      generatedAt: stamped,
      dataMode: "mock",
      mock: true,
      headline: "내 토스 계좌 기준으로 AAPL 분할 매도 기준 점검이 우선입니다.",
      exitScore: 57,
      regime: "토스 조회 전용",
      summary: [
        "보유 종목 2개와 관심 종목 3개를 토스 API 범위 안에서 분리했습니다.",
        "외부 텍스트 신호는 첫 화면 판단에서 제외했습니다.",
        "판단 근거는 수익률, 평가손익, 매도 가능 수량, 보유 비중으로 제한합니다."
      ],
      toss: {
        mode: "mock",
        configured: false,
        status: "GitHub Pages mock 데이터",
        account: { displayNumber: "demo", type: "BROKERAGE", orderableAmount: 1250000, currency: "KRW" },
        positions: positions,
        watchlist: watchlist
      },
      portfolio: {
        total: 2114486.2,
        invested: 864486.2,
        cash: 1250000,
        concentration: 41,
        sectors: [
          { sector: "현금", value: 1250000, ratio: 59 },
          { sector: "반도체", value: 864000, ratio: 41 },
          { sector: "AI/플랫폼", value: 486.2, ratio: 0 }
        ]
      },
      tossDecision: {
        headline: "내 토스 계좌 기준으로 AAPL 분할 매도 기준 점검이 우선입니다.",
        overallPressure: 57,
        urgentCount: 2,
        holdingCount: 2,
        watchCount: 3,
        items: decisionItems,
        rules: [
          "수익률과 평가손익은 토스 잔고에서 확인 가능한 값만 사용합니다.",
          "관심 종목은 보유가 아니므로 매도 판단 대신 시세 기준 대기 상태로 둡니다.",
          "외부 텍스트 신호는 토스 전용 판단 점수에 반영하지 않습니다."
        ]
      },
      checklist: [
        { label: "토스 잔고의 수익률, 평가손익, 매도 가능 수량 확인", status: "정상" },
        { label: "관심 종목은 토스 시세 연결 후 현재가 기준만 비교", status: "대기" },
        { label: "주문 실행은 읽기 전용 검증 이후 별도 단계에서만 열기", status: "잠금" }
      ]
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

  function pct(value) {
    return Math.round(Number(value || 0)) + "%";
  }

  function signedPct(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "0%";
    return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 1) + "%";
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

  function pressureLabel(score) {
    var value = Number(score || 0);
    if (value >= 72) return "높음";
    if (value >= 55) return "검토";
    if (value >= 38) return "관찰";
    return "낮음";
  }

  function load() {
    state.loading = !state.snapshot;
    state.refreshing = Boolean(state.snapshot);
    state.error = "";
    render();

    var loadPromise = state.dataMode === "mock" && isStaticPreviewHost()
      ? Promise.resolve(staticMockSnapshot())
      : requestJson(tossLensPath()).catch(function (error) {
        if (state.dataMode === "mock") return staticMockSnapshot();
        throw error;
      });

    return loadPromise
      .then(function (snapshot) {
        state.snapshot = snapshot;
        state.error = "";
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
  }

  function renderLoading() {
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Toss Lens</p>',
      '<h1>토스 계좌 판단판을 불러오는 중</h1>',
      '</div>',
      '</section>',
      '<section class="grid">',
      '<div class="panel skeleton tall"></div>',
      '<div class="panel skeleton"></div>',
      '<div class="panel skeleton"></div>',
      '</section>',
      '</main>'
    ].join("");
  }

  function renderError() {
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Toss Lens</p>',
      '<h1>토스 계좌 판단판을 만들지 못했습니다</h1>',
      '<p class="subtle">' + escapeHtml(state.error || "알 수 없는 오류") + "</p>",
      '</div>',
      '<button class="icon-button" data-action="refresh" title="새로고침">↻</button>',
      '</section>',
      '</main>'
    ].join("");
  }

  function renderDashboard(snapshot) {
    var toss = snapshot.toss || { mode: "demo" };
    var modeLabel = snapshot.mock ? "Mock" : (toss.mode === "live" ? "Toss live" : "Demo");
    var modeClass = snapshot.mock ? "mock" : (toss.mode === "live" ? "live" : "demo");
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Toss Lens</p>',
      '<h1>토스 계좌 기준 보유/관심 점검</h1>',
      '<p class="subtle">' + escapeHtml(snapshot.headline) + " · " + escapeHtml(formatClock(snapshot.generatedAt)) + "</p>",
      '</div>',
      '<div class="toolbar">',
      '<div class="mode-toggle" role="group" aria-label="데이터 모드">',
      '<button class="' + (state.dataMode === "live" ? "active" : "") + '" data-mode="live">실데이터</button>',
      '<button class="' + (state.dataMode === "mock" ? "active" : "") + '" data-mode="mock">Mock</button>',
      '</div>',
      '<span class="status-pill ' + modeClass + '">' + escapeHtml(modeLabel) + "</span>",
      '<button class="icon-button" data-action="refresh" title="새로고침">' + (state.refreshing ? "…" : "↻") + "</button>",
      '</div>',
      '</section>',
      renderTabs(),
      renderActiveTab(snapshot),
      '</main>'
    ].join("");
  }

  function renderTabs() {
    return [
      '<nav class="tab-bar" aria-label="앱 탭">',
      tabs.map(function (tab) {
        return '<button class="' + (state.activeTab === tab.id ? "active" : "") + '" data-tab="' + escapeHtml(tab.id) + '">' + escapeHtml(tab.label) + '</button>';
      }).join(""),
      '</nav>'
    ].join("");
  }

  function renderActiveTab(snapshot) {
    if (state.activeTab === "holdings") {
      return [
        '<section class="content-grid">',
        renderPortfolioPanel(snapshot),
        renderHoldingsPanel(snapshot),
        '</section>'
      ].join("");
    }
    if (state.activeTab === "watchlist") {
      return [
        '<section class="content-grid">',
        renderWatchlistPanel(snapshot),
        renderApiScopePanel(),
        '</section>'
      ].join("");
    }
    if (state.activeTab === "settings") {
      return [
        '<section class="content-grid">',
        renderSettingsPanel(),
        '</section>'
      ].join("");
    }
    return [
      '<section class="hero-grid">',
      renderScorePanel(snapshot),
      renderSourcePanel(snapshot),
      '</section>',
      '<section class="content-grid">',
      renderDecisionPanel(snapshot),
      renderChecklistPanel(snapshot),
      '</section>'
    ].join("");
  }

  function renderScorePanel(snapshot) {
    var decision = snapshot.tossDecision || {};
    var score = Number(snapshot.exitScore || decision.overallPressure || 0);
    return [
      '<article class="panel score-panel">',
      '<div class="score-wrap">',
      '<div class="score-ring" style="--score:' + score + '">',
      '<span>' + escapeHtml(score) + '</span>',
      '</div>',
      '<div>',
      '<p class="label">Toss Check</p>',
      '<h2>매도 검토 강도 ' + escapeHtml(pressureLabel(score)) + '</h2>',
      '<p class="subtle">토스 조회값으로 설명 가능한 기준만 사용합니다.</p>',
      '</div>',
      '</div>',
      '<div class="summary-list">',
      (snapshot.summary || []).map(function (item) {
        return '<p>' + escapeHtml(item) + '</p>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSourcePanel(snapshot) {
    var toss = snapshot.toss || { account: {} };
    var decision = snapshot.tossDecision || {};
    var account = toss.account || {};
    return [
      '<aside class="panel source-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">데이터 상태</p>',
      '<h2>토스 API 범위</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      '<div class="source-row"><span>표시 모드</span><strong>' + escapeHtml(snapshot.mock ? "Mock 데이터" : "기본 데이터") + '</strong></div>',
      '<div class="source-row"><span>토스</span><strong>' + escapeHtml(toss.status || "-") + '</strong></div>',
      '<div class="source-row"><span>계좌</span><strong>' + escapeHtml(account.displayNumber || "-") + '</strong></div>',
      '<div class="source-row"><span>주문 가능 금액</span><strong>' + escapeHtml(formatCurrency(account.orderableAmount || 0, account.currency || "KRW")) + '</strong></div>',
      '<div class="source-row"><span>보유/관심</span><strong>' + escapeHtml(decision.holdingCount || 0) + ' / ' + escapeHtml(decision.watchCount || 0) + '</strong></div>',
      '<div class="source-row"><span>외부 신호</span><strong>사용 안 함</strong></div>',
      '</div>',
      '</aside>'
    ].join("");
  }

  function renderDecisionPanel(snapshot) {
    var decision = snapshot.tossDecision || { items: [], rules: [] };
    var items = decision.items || [];
    return [
      '<article class="panel exit-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Account Priority</p>',
      '<h2>내 계좌 기준 오늘 먼저 점검할 종목</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(items.length) + '</span>',
      '</div>',
      '<div class="exit-list">',
      items.map(renderDecisionRow).join(""),
      '</div>',
      '<div class="rule-strip">',
      (decision.rules || []).map(function (rule) {
        return '<span>' + escapeHtml(rule) + '</span>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderDecisionRow(item) {
    return [
      '<div class="exit-row">',
      '<div class="exit-main">',
      '<div class="exit-title">',
      '<div>',
      '<h3>' + escapeHtml(item.name) + '</h3>',
      '<p class="subtle">' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.sector || "-") + '</p>',
      '</div>',
      '<div class="exit-badges">',
      '<span class="source-chip ' + escapeHtml(item.source) + '">' + escapeHtml(sourceLabel(item.source)) + '</span>',
      '<span class="decision-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.decision) + '</span>',
      '</div>',
      '</div>',
      '<div class="exit-reasons">',
      (item.reasons || []).map(function (reason) {
        return '<p>' + escapeHtml(reason) + '</p>';
      }).join(""),
      '</div>',
      '<div class="trigger-list">',
      (item.triggers || []).map(function (trigger) {
        return '<span>' + escapeHtml(trigger) + '</span>';
      }).join(""),
      '</div>',
      '</div>',
      '<div class="exit-score">',
      '<strong>' + escapeHtml(item.exitPressure || 0) + '</strong>',
      '<span>검토</span>',
      item.source === "holding" ? '<em>' + escapeHtml(signedPct(item.profitLossRate)) + '</em>' : '<em>watch</em>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderPortfolioPanel(snapshot) {
    var portfolio = snapshot.portfolio || { sectors: [] };
    return [
      '<article class="panel">',
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
      (portfolio.sectors || []).map(function (sector) {
        return [
          '<div class="bar-row">',
          '<div class="bar-meta"><span>' + escapeHtml(sector.sector) + '</span><strong>' + escapeHtml(pct(sector.ratio)) + '</strong></div>',
          '<div class="bar-track"><span style="width:' + Math.min(100, Math.max(2, sector.ratio)) + '%"></span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderHoldingsPanel(snapshot) {
    var toss = snapshot.toss || { positions: [] };
    var positions = (toss.positions || []).filter(function (item) {
      return item.source !== "cash" && item.sector !== "현금";
    });
    return [
      '<article class="panel holdings-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Holdings</p>',
      '<h2>보유 종목</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(positions.length) + '</span>',
      '</div>',
      '<div class="position-list">',
      positions.length ? positions.map(renderHoldingRow).join("") : '<p class="subtle">토스 잔고에서 보유 종목을 찾지 못했습니다.</p>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderHoldingRow(item) {
    return [
      '<div class="position-row rich-row">',
      '<div>',
      '<strong>' + escapeHtml(item.name) + '</strong>',
      '<span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.market || "-") + ' · 수량 ' + escapeHtml(item.quantity || "-") + '</span>',
      '<span>평균 ' + escapeHtml(formatCurrency(item.averagePrice || 0, item.currency)) + ' · 현재 ' + escapeHtml(formatCurrency(item.currentPrice || 0, item.currency)) + '</span>',
      '</div>',
      '<div class="right">',
      '<strong>' + escapeHtml(formatCurrency(item.marketValue, item.currency)) + '</strong>',
      '<span>' + escapeHtml(signedMoney(item.profitLoss, item.currency)) + ' · ' + escapeHtml(signedPct(item.profitLossRate)) + '</span>',
      '<span>매도 가능 ' + escapeHtml(item.sellableQuantity || item.quantity || "-") + '</span>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderWatchlistPanel(snapshot) {
    var toss = snapshot.toss || {};
    var watchlist = toss.watchlist || [];
    return [
      '<article class="panel watchlist-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Watchlist</p>',
      '<h2>관심 종목</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(watchlist.length) + '</span>',
      '</div>',
      '<div class="position-list">',
      watchlist.length ? watchlist.map(renderWatchRow).join("") : '<p class="subtle">설정 탭에서 관심 종목을 입력하세요.</p>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderWatchRow(item) {
    return [
      '<div class="position-row rich-row">',
      '<div>',
      '<strong>' + escapeHtml(item.name || item.symbol) + '</strong>',
      '<span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.market || "-") + ' · ' + escapeHtml(item.sector || "-") + '</span>',
      '</div>',
      '<div class="right">',
      '<strong>' + escapeHtml(item.currentPrice ? formatCurrency(item.currentPrice, item.currency) : "시세 대기") + '</strong>',
      '<span>' + escapeHtml(item.changeRate == null ? item.quoteStatus || "토스 시세 연결 후 표시" : signedPct(item.changeRate)) + '</span>',
      '</div>',
      '</div>'
    ].join("");
  }

  function renderApiScopePanel() {
    var rows = [
      ["계좌", "계좌 식별, 주문 가능 금액"],
      ["잔고", "보유 종목, 수량, 평가금액, 손익"],
      ["시세", "관심 종목 현재가, 등락률"],
      ["거래", "주문/정정/취소는 별도 잠금 단계"]
    ];
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Scope</p>',
      '<h2>토스 API로만 쓰는 정보</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      rows.map(function (row) {
        return '<div class="source-row"><span>' + escapeHtml(row[0]) + '</span><strong>' + escapeHtml(row[1]) + '</strong></div>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function settingValue(name) {
    return state.settings && state.settings[name] != null ? state.settings[name] : "";
  }

  function renderSettingField(name, label, type, placeholder) {
    return [
      '<label class="setting-field">',
      '<span>' + escapeHtml(label) + '</span>',
      '<input data-setting="' + escapeHtml(name) + '" type="' + escapeHtml(type || "text") + '" value="' + escapeHtml(settingValue(name)) + '" placeholder="' + escapeHtml(placeholder || "") + '" autocomplete="off" />',
      '</label>'
    ].join("");
  }

  function renderSettingsPanel() {
    var secretType = state.showSecrets ? "text" : "password";
    return [
      '<article class="panel settings-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">App Settings</p>',
      '<h2>토스 API 설정</h2>',
      '</div>',
      '<span class="tone-chip ' + (state.settingsSaved ? "watch" : "hold") + '">' + (state.settingsSaved ? "저장됨" : "로컬") + '</span>',
      '</div>',
      '<div class="settings-body">',
      '<div class="settings-note">',
      '<strong>저장 위치</strong>',
      '<p>입력값은 이 브라우저의 localStorage에만 저장됩니다. GitHub Pages에서는 서버로 secret을 전송하지 않습니다. 실제 API 호출은 로컬 서버 환경변수 연결 단계에서만 사용합니다.</p>',
      '</div>',
      '<div class="settings-grid">',
      renderSettingField("watchlistSymbols", "관심 종목", "text", "NVDA,TSLA,000660"),
      renderSettingField("tossApiBaseUrl", "Toss API Base URL", "url", "https://openapi.tossinvest.com"),
      renderSettingField("tossClientId", "Toss Client ID", "text", "client id"),
      renderSettingField("tossClientSecret", "Toss Client Secret", secretType, "client secret"),
      renderSettingField("tossAccountSeq", "Toss Account Seq", "text", "선택"),
      '</div>',
      '<div class="settings-actions">',
      '<button class="text-button primary" data-action="save-settings">저장</button>',
      '<button class="text-button" data-action="toggle-secrets">' + (state.showSecrets ? "숨기기" : "secret 보기") + '</button>',
      '<button class="text-button danger" data-action="clear-settings">삭제</button>',
      '</div>',
      '</div>',
      '</article>'
    ].join("");
  }

  function renderChecklistPanel(snapshot) {
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Read Only Guard</p>',
      '<h2>토스 전용 체크</h2>',
      '</div>',
      '</div>',
      '<div class="check-list">',
      (snapshot.checklist || []).map(function (item) {
        return [
          '<div class="check-row">',
          '<span class="check-state ' + escapeHtml(item.status) + '">' + escapeHtml(item.status) + '</span>',
          '<p>' + escapeHtml(item.label) + '</p>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function bindActions() {
    var refresh = app.querySelector('[data-action="refresh"]');
    if (refresh) {
      refresh.addEventListener("click", function () {
        if (!state.refreshing) load();
      });
    }

    Array.prototype.slice.call(app.querySelectorAll("[data-tab]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var nextTab = button.getAttribute("data-tab") || "decision";
        if (nextTab === state.activeTab) return;
        state.activeTab = nextTab;
        render();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-mode]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var nextMode = button.getAttribute("data-mode") || "live";
        if (nextMode === state.dataMode || state.refreshing) return;
        state.dataMode = nextMode === "mock" ? "mock" : "live";
        state.snapshot = null;
        persistDataMode(state.dataMode);
        load();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-setting]")).forEach(function (field) {
      field.addEventListener("input", function () {
        var name = field.getAttribute("data-setting");
        if (!name) return;
        state.settings[name] = field.value;
        state.settingsSaved = false;
      });
    });

    var saveSettings = app.querySelector('[data-action="save-settings"]');
    if (saveSettings) {
      saveSettings.addEventListener("click", function () {
        persistSettings();
        render();
      });
    }

    var toggleSecrets = app.querySelector('[data-action="toggle-secrets"]');
    if (toggleSecrets) {
      toggleSecrets.addEventListener("click", function () {
        state.showSecrets = !state.showSecrets;
        render();
      });
    }

    var clearSettingsButton = app.querySelector('[data-action="clear-settings"]');
    if (clearSettingsButton) {
      clearSettingsButton.addEventListener("click", function () {
        clearSettings();
        render();
      });
    }
  }

  load();
}());
