(function () {
  var app = document.getElementById("app");
  var state = {
    loading: true,
    refreshing: false,
    error: "",
    snapshot: null,
    selectedTheme: "all",
    dataMode: initialDataMode()
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

  function initialDataMode() {
    var params = new URLSearchParams(window.location.search);
    var queryMode = String(params.get("mock") || params.get("mode") || "").toLowerCase();
    if (queryMode === "1" || queryMode === "true" || queryMode === "mock") return "mock";
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

  function flowLensPath() {
    return state.dataMode === "mock" ? "/api/flow-lens?mock=1" : "/api/flow-lens";
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

  function pct(value) {
    return Math.round(Number(value || 0)) + "%";
  }

  function signedPct(value) {
    var number = Number(value || 0);
    if (!Number.isFinite(number) || number === 0) return "0%";
    return (number > 0 ? "+" : "") + number.toFixed(Math.abs(number) >= 10 ? 0 : 1) + "%";
  }

  function sourceLabel(value) {
    if (value === "holding") return "보유";
    if (value === "watchlist") return "관심";
    return value || "-";
  }

  function pressureLabel(score) {
    var value = Number(score || 0);
    if (value >= 72) return "높음";
    if (value >= 55) return "검토";
    if (value >= 38) return "관찰";
    return "낮음";
  }

  function filteredNews(snapshot) {
    if (!snapshot) return [];
    if (state.selectedTheme === "all") return snapshot.news || [];
    var theme = (snapshot.themes || []).filter(function (entry) {
      return entry.id === state.selectedTheme;
    })[0];
    if (!theme) return snapshot.news || [];
    var label = theme.label.toLowerCase();
    return (snapshot.news || []).filter(function (item) {
      var text = (item.title + " " + item.summary + " " + item.source).toLowerCase();
      if (theme.id === "ai") return /ai|chip|semiconductor|nvidia|data center|반도체|삼성|hynix/.test(text);
      if (theme.id === "rates") return /fed|rate|yield|bond|dollar|inflation|금리|달러/.test(text);
      if (theme.id === "korea") return /korea|kospi|krw|seoul|한국|코스피|외국인/.test(text);
      if (theme.id === "risk") return /war|tariff|risk|selloff|volatility|oil|위험|관세/.test(text);
      if (theme.id === "crypto") return /bitcoin|crypto|stablecoin|token|ethereum|코인|비트코인/.test(text);
      return text.indexOf(label) >= 0;
    });
  }

  function load() {
    state.loading = !state.snapshot;
    state.refreshing = Boolean(state.snapshot);
    state.error = "";
    render();

    return requestJson(flowLensPath())
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
      '<p class="eyebrow">Exit Lens</p>',
      '<h1>매도 타이밍을 불러오는 중</h1>',
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
      '<p class="eyebrow">Exit Lens</p>',
      '<h1>매도 판단 스냅샷을 만들지 못했습니다</h1>',
      '<p class="subtle">' + escapeHtml(state.error || "알 수 없는 오류") + "</p>",
      '</div>',
      '<button class="icon-button" data-action="refresh" title="새로고침">↻</button>',
      '</section>',
      '</main>'
    ].join("");
  }

  function renderDashboard(snapshot) {
    var news = filteredNews(snapshot);
    var toss = snapshot.toss || { mode: "demo" };
    var modeLabel = snapshot.mock ? "Mock" : (toss.mode === "live" ? "Toss live" : "Demo");
    var modeClass = snapshot.mock ? "mock" : (toss.mode === "live" ? "live" : "demo");
    return [
      '<main class="shell">',
      '<section class="topbar">',
      '<div>',
      '<p class="eyebrow">Exit Lens</p>',
      '<h1>보유/관심 종목 매도 타이밍</h1>',
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
      '<section class="hero-grid">',
      renderScorePanel(snapshot),
      renderSourcePanel(snapshot),
      '</section>',
      '<section class="content-grid">',
      renderExitPanel(snapshot),
      renderPortfolioPanel(snapshot),
      renderThemePanel(snapshot),
      renderNewsPanel(snapshot, news),
      renderSocialPanel(snapshot),
      renderChecklistPanel(snapshot),
      '</section>',
      '</main>'
    ].join("");
  }

  function renderScorePanel(snapshot) {
    var exitLens = snapshot.exitLens || {};
    var score = Number(snapshot.exitScore || exitLens.overallPressure || 0);
    return [
      '<article class="panel score-panel">',
      '<div class="score-wrap">',
      '<div class="score-ring" style="--score:' + score + '">',
      '<span>' + escapeHtml(score) + '</span>',
      '</div>',
      '<div>',
      '<p class="label">Exit Pressure</p>',
      '<h2>매도 압력 ' + escapeHtml(pressureLabel(score)) + '</h2>',
      '<p class="subtle">' + escapeHtml(exitLens.urgentCount || 0) + '개 종목은 매도/축소 기준 확인이 필요합니다.</p>',
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
    var exitLens = snapshot.exitLens || {};
    return [
      '<aside class="panel source-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">데이터 상태</p>',
      '<h2>연결 맥락</h2>',
      '</div>',
      '</div>',
      '<div class="source-stack">',
      '<div class="source-row"><span>표시 모드</span><strong>' + escapeHtml(snapshot.mock ? "Mock 데이터" : "기본 데이터") + '</strong></div>',
      '<div class="source-row"><span>토스</span><strong>' + escapeHtml(toss.status || "-") + '</strong></div>',
      '<div class="source-row"><span>계좌</span><strong>' + escapeHtml(toss.account && toss.account.displayNumber || "-") + '</strong></div>',
      '<div class="source-row"><span>보유/관심</span><strong>' + escapeHtml(exitLens.holdingCount || 0) + ' / ' + escapeHtml(exitLens.watchCount || 0) + '</strong></div>',
      '<div class="source-row"><span>매도 검토</span><strong>' + escapeHtml(exitLens.urgentCount || 0) + '개</strong></div>',
      '<div class="source-row"><span>뉴스</span><strong>' + escapeHtml((snapshot.news || []).length) + '건</strong></div>',
      '<div class="source-row"><span>포스팅</span><strong>' + escapeHtml((snapshot.social || []).length) + '건</strong></div>',
      '</div>',
      '</aside>'
    ].join("");
  }

  function renderExitPanel(snapshot) {
    var exitLens = snapshot.exitLens || { items: [], rules: [] };
    var items = exitLens.items || [];
    return [
      '<article class="panel exit-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Sell Timing</p>',
      '<h2>오늘 먼저 볼 종목</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(items.length) + '</span>',
      '</div>',
      '<div class="exit-list">',
      items.map(function (item) {
        var signals = item.matchedSignals || [];
        return [
          '<div class="exit-row">',
          '<div class="exit-main">',
          '<div class="exit-title">',
          '<div>',
          '<h3>' + escapeHtml(item.name) + '</h3>',
          '<p class="subtle">' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.sector) + '</p>',
          '</div>',
          '<div class="exit-badges">',
          '<span class="source-chip ' + escapeHtml(item.source) + '">' + escapeHtml(sourceLabel(item.source)) + '</span>',
          '<span class="decision-chip ' + escapeHtml(item.tone) + '">' + escapeHtml(item.decision) + '</span>',
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
          signals.length ? '<div class="evidence-list">' + signals.map(function (signal) {
            return '<span>' + escapeHtml(signal.type === "post" ? "X" : "뉴스") + ' · ' + escapeHtml(signal.source) + '</span>';
          }).join("") + '</div>' : '',
          '</div>',
          '<div class="exit-score">',
          '<strong>' + escapeHtml(item.exitPressure) + '</strong>',
          '<span>압력</span>',
          item.source === "holding" ? '<em>' + escapeHtml(signedPct(item.profitLossRate)) + '</em>' : '<em>watch</em>',
          '</div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="rule-strip">',
      (exitLens.rules || []).map(function (rule) {
        return '<span>' + escapeHtml(rule) + '</span>';
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderPortfolioPanel(snapshot) {
    var portfolio = snapshot.portfolio || { sectors: [] };
    var toss = snapshot.toss || { positions: [] };
    var positions = toss.positions || [];
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Toss Portfolio</p>',
      '<h2>보유 노출</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(formatMoney(portfolio.total)) + '</span>',
      '</div>',
      '<div class="allocation">',
      portfolio.sectors.map(function (sector) {
        return [
          '<div class="bar-row">',
          '<div class="bar-meta"><span>' + escapeHtml(sector.sector) + '</span><strong>' + escapeHtml(pct(sector.ratio)) + '</strong></div>',
          '<div class="bar-track"><span style="width:' + Math.min(100, Math.max(2, sector.ratio)) + '%"></span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '<div class="position-list">',
      positions.slice(0, 5).map(function (item) {
        return [
          '<div class="position-row">',
          '<div><strong>' + escapeHtml(item.name) + '</strong><span>' + escapeHtml(item.symbol) + ' · ' + escapeHtml(item.sector) + '</span></div>',
          '<div class="right"><strong>' + escapeHtml(formatMoney(item.marketValue)) + '</strong><span>' + escapeHtml(item.currency || "") + '</span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderThemePanel(snapshot) {
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Signal Themes</p>',
      '<h2>매도 판단에 영향을 주는 신호</h2>',
      '</div>',
      '</div>',
      '<div class="theme-tabs">',
      '<button class="' + (state.selectedTheme === "all" ? "active" : "") + '" data-theme="all">전체</button>',
      (snapshot.themes || []).map(function (theme) {
        return '<button class="' + (state.selectedTheme === theme.id ? "active" : "") + '" data-theme="' + escapeHtml(theme.id) + '">' + escapeHtml(theme.label) + '</button>';
      }).join(""),
      '</div>',
      '<div class="theme-list">',
      (snapshot.themes || []).map(function (theme) {
        return [
          '<div class="theme-row ' + escapeHtml(theme.color) + '">',
          '<div><strong>' + escapeHtml(theme.label) + '</strong><span>' + escapeHtml(theme.headline) + '</span></div>',
          '<div class="theme-count"><strong>' + escapeHtml(theme.count) + '</strong><span>X ' + escapeHtml(theme.socialCount || 0) + '</span></div>',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderNewsPanel(snapshot, news) {
    return [
      '<article class="panel news-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">News Feed</p>',
      '<h2>매도 근거 기사</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(news.length) + '</span>',
      '</div>',
      '<div class="news-list">',
      news.map(function (item) {
        var content = [
          '<div class="news-item">',
          '<div>',
          '<p class="news-source">' + escapeHtml(item.source) + '</p>',
          '<h3>' + escapeHtml(item.title) + '</h3>',
          item.summary && item.summary !== item.title ? '<p class="subtle">' + escapeHtml(item.summary) + '</p>' : '',
          '</div>',
          item.url ? '<a class="open-link" href="' + escapeHtml(item.url) + '" target="_blank" rel="noreferrer" title="원문 열기">↗</a>' : '',
          '</div>'
        ];
        return content.join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderSocialPanel(snapshot) {
    var posts = snapshot.social || [];
    return [
      '<article class="panel social-panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Social Pulse</p>',
      '<h2>X 포스팅 신호</h2>',
      '</div>',
      '<span class="metric">' + escapeHtml(posts.length) + '</span>',
      '</div>',
      '<div class="social-list">',
      posts.map(function (post) {
        var metrics = post.metrics || {};
        return [
          '<div class="social-item">',
          '<div>',
          '<p class="post-meta">' + escapeHtml(post.source || "post") + ' · @' + escapeHtml(post.author || "unknown") + '</p>',
          '<p>' + escapeHtml(post.text) + '</p>',
          '<div class="engagement">',
          '<span>↻ ' + escapeHtml(metrics.reposts || 0) + '</span>',
          '<span>♡ ' + escapeHtml(metrics.likes || 0) + '</span>',
          '<span>↩ ' + escapeHtml(metrics.replies || 0) + '</span>',
          '</div>',
          '</div>',
          post.url ? '<a class="open-link" href="' + escapeHtml(post.url) + '" target="_blank" rel="noreferrer" title="포스팅 열기">↗</a>' : '',
          '</div>'
        ].join("");
      }).join(""),
      '</div>',
      '</article>'
    ].join("");
  }

  function renderChecklistPanel(snapshot) {
    return [
      '<article class="panel">',
      '<div class="panel-head">',
      '<div>',
      '<p class="label">Trade Check</p>',
      '<h2>매도 전 확인할 것</h2>',
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

    Array.prototype.slice.call(app.querySelectorAll("[data-mode]")).forEach(function (button) {
      button.addEventListener("click", function () {
        var nextMode = button.getAttribute("data-mode") || "live";
        if (nextMode === state.dataMode || state.refreshing) return;
        state.dataMode = nextMode === "mock" ? "mock" : "live";
        state.selectedTheme = "all";
        state.snapshot = null;
        persistDataMode(state.dataMode);
        load();
      });
    });

    Array.prototype.slice.call(app.querySelectorAll("[data-theme]")).forEach(function (button) {
      button.addEventListener("click", function () {
        state.selectedTheme = button.getAttribute("data-theme") || "all";
        render();
      });
    });
  }

  load();
})();
