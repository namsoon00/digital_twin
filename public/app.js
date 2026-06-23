(function () {
  var viewKeys = ["assistant", "memories", "stocks", "travel", "assets", "schedule", "profile"];

  function isValidView(view) {
    return viewKeys.indexOf(view) >= 0;
  }

  function viewFromHash() {
    var hash = window.location.hash ? window.location.hash.slice(1) : "";
    return isValidView(hash) ? hash : "";
  }

  function readSavedView() {
    var hashView = viewFromHash();
    if (hashView) return hashView;

    try {
      var storedView = window.localStorage.getItem("digiter_twin.current_view");
      if (isValidView(storedView)) return storedView;
    } catch (error) {
      return "assistant";
    }

    return "assistant";
  }

  function saveCurrentView(view) {
    if (!isValidView(view)) return;

    try {
      window.localStorage.setItem("digiter_twin.current_view", view);
    } catch (error) {
      // Ignore storage failures; URL hash still preserves refresh state.
    }

    if (window.location.hash !== "#" + view) {
      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, "", "#" + view);
      } else {
        window.location.hash = view;
      }
    }
  }

  var app = document.getElementById("app");
  var state = {
    view: readSavedView(),
    snapshot: null,
    loading: true,
    error: "",
    sending: false,
    fallback: false,
    responseEngine: "codex",
    memoryFilter: "all",
    stockData: [],
    stockLoading: false,
    stockError: "",
    stockLoadedKey: "",
    stockFilter: "all",
    stockQuery: "",
    editingStockId: ""
  };

  var navItems = [
    { key: "assistant", label: "비서", icon: "A" },
    { key: "memories", label: "기억", icon: "M" },
    { key: "stocks", label: "주식", icon: "$" },
    { key: "travel", label: "여행", icon: "T" },
    { key: "assets", label: "자산", icon: "W" },
    { key: "schedule", label: "일정", icon: "C" },
    { key: "profile", label: "설정", icon: "P" }
  ];

  var categoryLabels = {
    identity: "정체성",
    preference: "선호",
    finance: "주식",
    travel: "여행",
    asset: "자산",
    schedule: "일정",
    work: "업무",
    other: "기타"
  };

  var domainLabels = {
    stock: "주식",
    trip: "여행",
    asset: "자산",
    schedule: "일정",
    task: "할 일",
    note: "메모"
  };

  var domainConfig = {
    stocks: {
      type: "stock",
      title: "주식",
      eyebrow: "Watchlist",
      primaryLabel: "종목명",
      secondaryLabel: "티커",
      dateLabel: "점검일",
      amountLabel: "목표 비중",
      noteLabel: "관찰 포인트",
      empty: "관심 종목이 없습니다."
    },
    travel: {
      type: "trip",
      title: "여행",
      eyebrow: "Trips",
      primaryLabel: "여행명",
      secondaryLabel: "지역",
      dateLabel: "출발일",
      amountLabel: "예산",
      noteLabel: "동선/예약 메모",
      empty: "여행 계획이 없습니다."
    },
    assets: {
      type: "asset",
      title: "자산",
      eyebrow: "Assets",
      primaryLabel: "자산 항목",
      secondaryLabel: "분류",
      dateLabel: "기준일",
      amountLabel: "금액",
      noteLabel: "현금흐름/리스크 메모",
      empty: "자산 기록이 없습니다."
    },
    schedule: {
      type: "schedule",
      title: "일정",
      eyebrow: "Calendar",
      primaryLabel: "일정명",
      secondaryLabel: "장소",
      dateLabel: "날짜",
      amountLabel: "예상 소요 시간",
      noteLabel: "준비/의존 항목",
      empty: "일정이 없습니다."
    }
  };

  var stockPresets = [
    { market: "KR", name: "삼성전자", ticker: "005930" },
    { market: "KR", name: "SK하이닉스", ticker: "000660" },
    { market: "KR", name: "NAVER", ticker: "035420" },
    { market: "KR", name: "카카오", ticker: "035720" },
    { market: "KR", name: "현대차", ticker: "005380" },
    { market: "KR", name: "기아", ticker: "000270" },
    { market: "KR", name: "LG에너지솔루션", ticker: "373220" },
    { market: "KR", name: "삼성바이오로직스", ticker: "207940" },
    { market: "KR", name: "셀트리온", ticker: "068270" },
    { market: "KR", name: "POSCO홀딩스", ticker: "005490" },
    { market: "US", name: "Apple", ticker: "AAPL" },
    { market: "US", name: "Microsoft", ticker: "MSFT" },
    { market: "US", name: "NVIDIA", ticker: "NVDA" },
    { market: "US", name: "Tesla", ticker: "TSLA" },
    { market: "US", name: "Alphabet", ticker: "GOOGL" },
    { market: "US", name: "Amazon", ticker: "AMZN" },
    { market: "US", name: "Meta Platforms", ticker: "META" },
    { market: "US", name: "Netflix", ticker: "NFLX" },
    { market: "US", name: "AMD", ticker: "AMD" },
    { market: "US", name: "JPMorgan Chase", ticker: "JPM" }
  ];

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function requestJson(url, options) {
    options = options || {};
    options.headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
    return fetch(url, options).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok) throw new Error(payload.error || "요청 실패");
        return payload;
      });
    });
  }

  function load() {
    return requestJson("/api/bootstrap")
      .then(function (snapshot) {
        state.snapshot = snapshot;
        state.error = "";
        if (state.view === "stocks") loadStocks(false);
      })
      .catch(function (error) {
        state.error = error.message;
      })
      .finally(function () {
        state.loading = false;
        render();
      });
  }

  function count(type) {
    return state.snapshot.items.filter(function (item) { return item.type === type; }).length;
  }

  function stockSymbols() {
    if (!state.snapshot) return [];
    return state.snapshot.items
      .filter(function (item) { return item.type === "stock"; })
      .map(function (item) { return String(item.ticker || item.title || "").trim(); })
      .filter(Boolean)
      .filter(function (symbol, index, list) { return list.indexOf(symbol) === index; });
  }

  function findStockPreset(ticker) {
    return stockPresets.filter(function (preset) {
      return preset.ticker === ticker;
    })[0];
  }

  function renderStockPresetOptions() {
    return (
      '<option value="">기업명/종목 선택</option>' +
      '<option value="__custom">직접 입력</option>' +
      stockPresets
        .map(function (preset) {
          return (
            '<option value="' +
            escapeHtml(preset.ticker) +
            '">' +
            escapeHtml("[" + preset.market + "] " + preset.name + " - " + preset.ticker) +
            "</option>"
          );
        })
        .join("")
    );
  }

  function loadStocks(force) {
    var symbols = stockSymbols();
    var key = symbols.join(",");
    if (!symbols.length) {
      state.stockData = [];
      state.stockLoadedKey = "";
      state.stockLoading = false;
      state.stockError = "";
      render();
      return Promise.resolve();
    }
    if (!force && key === state.stockLoadedKey && state.stockData.length) return Promise.resolve();
    state.stockLoading = true;
    state.stockError = "";
    render();
    return requestJson("/api/stocks?symbols=" + encodeURIComponent(key))
      .then(function (payload) {
        state.stockData = payload.stocks || [];
        state.stockLoadedKey = key;
      })
      .catch(function (error) {
        state.stockError = error.message;
      })
      .finally(function () {
        state.stockLoading = false;
        render();
      });
  }

  function topTitle() {
    if (!state.snapshot) return "Digiter Twin";
    if (state.view === "assistant") return state.snapshot.profile.assistantName + " 개인 비서";
    if (state.view === "memories") return "기억";
    if (state.view === "profile") return "설정";
    return domainConfig[state.view].title;
  }

  function topEyebrow() {
    if (state.view === "assistant") return "Private assistant";
    if (state.view === "memories") return "Memory";
    if (state.view === "profile") return "Persona";
    return domainConfig[state.view].eyebrow;
  }

  function render() {
    var snapshot = state.snapshot;
    var candidateCount = snapshot ? snapshot.memories.filter(function (memory) { return memory.status === "candidate"; }).length : 0;
    var approvedCount = snapshot ? snapshot.memories.filter(function (memory) { return memory.status === "approved"; }).length : 0;

    app.innerHTML =
      '<main class="main"><div class="workspace">' +
      '<header class="topbar"><div><p class="eyebrow">' +
      escapeHtml(topEyebrow()) +
      "</p><h1>" +
      escapeHtml(topTitle()) +
      "</h1></div>" +
      (snapshot
        ? '<div class="tabs"><span class="status-pill">' + approvedCount + " 기억</span><span class=\"status-pill " + (candidateCount ? "warn" : "muted") + '">' + candidateCount + " 후보</span></div>"
        : "") +
      "</header>" +
      (state.error ? '<div class="item-card">' + escapeHtml(state.error) + "</div>" : "") +
      (state.loading || !snapshot ? '<section class="panel panel-body"><span class="status-pill muted">로딩</span></section>' : renderActive()) +
      "</div></main>" +
      renderBottomTabs(candidateCount);

    bindEvents();
  }

  function renderBottomTabs(candidateCount) {
    return (
      '<nav class="bottom-tabs" aria-label="주요 메뉴">' +
      navItems
        .map(function (item) {
          return (
            '<button class="bottom-tab ' +
            (state.view === item.key ? "active" : "") +
            '" data-view="' +
            item.key +
            '" title="' +
            escapeHtml(item.label) +
            '"><span class="bottom-tab-icon">' +
            escapeHtml(item.icon) +
            '</span><span class="bottom-tab-label">' +
            escapeHtml(item.label) +
            "</span>" +
            (item.key === "memories" && candidateCount ? '<small class="bottom-tab-badge">' + candidateCount + "</small>" : "") +
            "</button>"
          );
        })
        .join("") +
      "</nav>"
    );
  }

  function renderActive() {
    if (state.view === "assistant") return renderAssistant();
    if (state.view === "memories") return renderMemories();
    if (state.view === "stocks") return renderStocks();
    if (state.view === "profile") return renderProfile();
    return renderDomain(domainConfig[state.view]);
  }

  function renderAssistant() {
    var snapshot = state.snapshot;
    var messages = snapshot.messages
      .map(function (message) {
        var name = message.role === "user" ? snapshot.profile.ownerName : snapshot.profile.assistantName;
        return (
          '<div class="message ' +
          escapeHtml(message.role) +
          '"><span class="message-meta">' +
          escapeHtml(name) +
          '</span><div class="message-bubble">' +
          escapeHtml(message.content) +
          "</div></div>"
        );
      })
      .join("");

    if (state.sending) {
      messages +=
        '<div class="message assistant"><span class="message-meta">' +
        escapeHtml(snapshot.profile.assistantName) +
        '</span><div class="message-bubble">정리 중</div></div>';
    }

    return (
      '<div class="grid-dashboard"><section class="panel chat-panel">' +
      '<div class="panel-header"><div><h2>대화</h2><p class="subtle">' +
      (state.fallback ? "로컬 응답" : state.responseEngine === "codex" ? "Codex 응답" : "AI 응답") +
      '</p></div><span class="status-pill">' +
      escapeHtml(snapshot.profile.assistantName) +
      "</span></div>" +
      '<div class="messages" id="messages">' +
      messages +
      "</div>" +
      '<form class="composer" id="chat-form"><textarea class="textarea" id="chat-input" placeholder="예: 이번 달 자산 배분을 점검해줘. 위험한 부분과 다음 행동을 나눠줘."></textarea><button class="primary-button" ' +
      (state.sending ? "disabled" : "") +
      ">보내기</button></form>" +
      "</section>" +
      renderAssistantSide() +
      "</div>"
    );
  }

  function renderAssistantSide() {
    var snapshot = state.snapshot;
    var metrics = [
      { label: "관심 종목", value: count("stock") },
      { label: "여행 계획", value: count("trip") },
      { label: "자산 기록", value: count("asset") },
      { label: "일정", value: count("schedule") + count("task") }
    ];
    var nextItems = snapshot.items.filter(function (item) { return item.type === "schedule" || item.type === "task"; }).slice(0, 4);
    var candidates = snapshot.memories.filter(function (memory) { return memory.status === "candidate"; }).slice(0, 3);
    return (
      '<aside class="stack"><section class="panel"><div class="panel-header"><h2>현황</h2></div><div class="panel-body metric-grid">' +
      metrics
        .map(function (metric) {
          return '<div class="metric"><strong>' + metric.value + '</strong><span>' + escapeHtml(metric.label) + "</span></div>";
        })
        .join("") +
      '</div></section><section class="panel"><div class="panel-header"><h2>다음 일정</h2></div><div class="panel-body list">' +
      (nextItems.length ? nextItems.map(renderItemCard).join("") : empty("일정이 없습니다.")) +
      '</div></section><section class="panel"><div class="panel-header"><h2>기억 후보</h2></div><div class="panel-body list">' +
      (candidates.length ? candidates.map(function (memory) { return renderMemoryCard(memory, true); }).join("") : empty("후보가 없습니다.")) +
      "</div></section></aside>"
    );
  }

  function renderMemories() {
    var filtered = state.snapshot.memories.filter(function (memory) {
      return state.memoryFilter === "all" || memory.status === state.memoryFilter;
    });
    return (
      '<div class="domain-grid"><section class="panel"><div class="panel-header"><h2>추가</h2></div>' +
      '<form class="panel-body stack" id="memory-form"><div class="field"><label>내용</label><textarea class="textarea" name="content"></textarea></div>' +
      '<div class="form-grid"><div class="field"><label>분류</label><select class="select" name="category">' +
      Object.keys(categoryLabels)
        .map(function (key) {
          return '<option value="' + key + '">' + categoryLabels[key] + "</option>";
        })
        .join("") +
      '</select></div><div class="field"><label>상태</label><select class="select" name="status"><option value="approved">승인</option><option value="candidate">후보</option></select></div></div>' +
      '<button class="primary-button">저장</button></form></section>' +
      '<section class="panel"><div class="panel-header"><h2>목록</h2><div class="tabs">' +
      ["all", "candidate", "approved"]
        .map(function (key) {
          var label = key === "all" ? "전체" : key === "candidate" ? "후보" : "승인";
          return '<button class="tab-button ' + (state.memoryFilter === key ? "active" : "") + '" data-memory-filter="' + key + '">' + label + "</button>";
        })
        .join("") +
      '</div></div><div class="panel-body list">' +
      (filtered.length ? filtered.map(function (memory) { return renderMemoryCard(memory, false); }).join("") : empty("기억이 없습니다.")) +
      "</div></section></div>"
    );
  }

  function renderMemoryCard(memory, compact) {
    return (
      '<div class="item-card"><div class="item-row"><div><div class="item-title">' +
      escapeHtml(memory.content) +
      '</div><div class="item-meta">' +
      escapeHtml(categoryLabels[memory.category] || memory.category) +
      " · 중요도 " +
      escapeHtml(memory.importance) +
      " · " +
      escapeHtml(memory.status === "approved" ? "승인" : memory.status === "candidate" ? "후보" : "보관") +
      "</div></div></div>" +
      (compact
        ? ""
        : '<div class="actions">' +
          (memory.status !== "approved" ? '<button class="secondary-button" data-memory-approve="' + memory.id + '">승인</button>' : "") +
          (memory.status !== "archived" ? '<button class="secondary-button" data-memory-archive="' + memory.id + '">보관</button>' : "") +
          '<button class="icon-button" data-memory-delete="' + memory.id + '" title="삭제">×</button></div>') +
      "</div>"
    );
  }

  function formatNumber(value, digits) {
    if (value === null || value === undefined || value === "") return "-";
    var numberValue = Number(value);
    if (!isFinite(numberValue)) return escapeHtml(value);
    return numberValue.toLocaleString(undefined, {
      maximumFractionDigits: digits === undefined ? 2 : digits,
      minimumFractionDigits: 0
    });
  }

  function formatPercent(value) {
    if (value === null || value === undefined || value === "") return "-";
    var numberValue = Number(value);
    if (!isFinite(numberValue)) return "-";
    return (numberValue > 0 ? "+" : "") + numberValue.toFixed(2) + "%";
  }

  function changeClass(value) {
    var numberValue = Number(value);
    if (!isFinite(numberValue) || numberValue === 0) return "flat";
    return numberValue > 0 ? "up" : "down";
  }

  function stockStatusKey(item) {
    return item && item.status === "holding" ? "holding" : "watch";
  }

  function stockStatusLabel(item) {
    return stockStatusKey(item) === "holding" ? "보유" : "관심";
  }

  function stockHoldingMeta(item) {
    var fields = item.fields || {};
    var values = [];
    if (stockStatusKey(item) === "holding" && item.amount !== undefined && item.amount !== "" && item.amount !== null) values.push("수량 " + item.amount);
    if (fields.averagePrice) values.push("평균단가 " + fields.averagePrice);
    if (fields.targetPrice) values.push("목표가 " + fields.targetPrice);
    return values.join(" · ");
  }

  function stockSearchText(item) {
    var fields = item.fields || {};
    return [
      item.title,
      item.ticker,
      item.notes,
      stockStatusLabel(item),
      fields.averagePrice,
      fields.targetPrice
    ]
      .join(" ")
      .toLowerCase();
  }

  function stockMatchesFilter(item) {
    var filter = state.stockFilter || "all";
    var query = String(state.stockQuery || "").trim().toLowerCase();
    if (filter !== "all" && stockStatusKey(item) !== filter) return false;
    if (query && stockSearchText(item).indexOf(query) < 0) return false;
    return true;
  }

  function findItem(id) {
    return state.snapshot.items.filter(function (item) { return item.id === id; })[0];
  }

  function itemAmountValue(item) {
    return item.amount === undefined || item.amount === null ? "" : String(item.amount);
  }

  function stockExists(ticker, status, currentId) {
    var cleanTicker = String(ticker || "").trim().toUpperCase();
    return state.snapshot.items.some(function (item) {
      return (
        item.type === "stock" &&
        item.id !== currentId &&
        stockStatusKey(item) === status &&
        String(item.ticker || item.title || "").trim().toUpperCase() === cleanTicker
      );
    });
  }

  function stockPatch(id, payload, refreshQuotes) {
    return requestJson("/api/items/" + id, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }).then(function () {
      return load().then(function () {
        if (refreshQuotes) return loadStocks(true);
        return undefined;
      });
    });
  }

  function renderStocks() {
    var items = state.snapshot.items.filter(function (item) { return item.type === "stock"; });
    var holdingCount = items.filter(function (item) { return stockStatusKey(item) === "holding"; }).length;
    var watchCount = items.length - holdingCount;
    return (
      '<div class="stock-layout"><section class="panel"><div class="panel-header"><div><h2>종목 추가</h2><p class="subtle">미국 종목은 AAPL, TSLA처럼 입력하고 국내 종목은 005930처럼 입력합니다.</p></div><span class="status-pill">' +
      escapeHtml("보유 " + holdingCount + " · 관심 " + watchCount) +
      '</span></div><form class="panel-body stack" id="stock-form">' +
      '<div class="field"><label>기업/종목</label><select class="select" name="preset" id="stock-preset">' +
      renderStockPresetOptions() +
      '</select></div>' +
      '<div class="field"><label>구분</label><select class="select" name="status" id="stock-status"><option value="holding">보유 주식</option><option value="watch">관심 주식</option></select></div>' +
      '<div class="form-grid"><div class="field"><label>기업명</label><input class="input" name="title" id="stock-title" placeholder="Apple" /></div><div class="field"><label>티커/종목코드</label><input class="input" name="ticker" id="stock-ticker" placeholder="AAPL 또는 005930" /></div></div>' +
      '<div class="form-grid"><div class="field"><label>보유 수량</label><input class="input" name="amount" inputmode="decimal" placeholder="10" /></div><div class="field"><label>평균단가</label><input class="input" name="averagePrice" inputmode="decimal" placeholder="선택 입력" /></div></div>' +
      '<div class="field"><label>목표가</label><input class="input" name="targetPrice" inputmode="decimal" placeholder="관심 종목의 목표 가격 또는 점검 기준" /></div>' +
      '<div class="field"><label>관찰 메모</label><textarea class="textarea" name="notes" placeholder="실적, 밸류에이션, 리스크, 확인할 뉴스"></textarea></div>' +
      '<div class="actions"><button class="primary-button">종목 추가</button><button class="secondary-button" type="button" id="stock-refresh">새로고침</button></div>' +
      '</form></section><section class="panel"><div class="panel-header stock-panel-header"><div><h2>종목 관리</h2><p class="subtle">가격은 지연될 수 있고, 뉴스는 최근 기사 검색 결과입니다.</p></div>' +
      (state.stockLoading ? '<span class="status-pill muted">불러오는 중</span>' : '<span class="status-pill">시장/뉴스</span>') +
      '</div><div class="panel-body stack">' +
      renderStockControls(items, holdingCount, watchCount) +
      (state.stockError ? '<div class="item-card">' + escapeHtml(state.stockError) + "</div>" : "") +
      renderStockCards(items) +
      "</div></section></div>"
    );
  }

  function renderStockControls(items, holdingCount, watchCount) {
    var tabs = [
      { key: "all", label: "전체", count: items.length },
      { key: "holding", label: "보유", count: holdingCount },
      { key: "watch", label: "관심", count: watchCount }
    ];
    return (
      '<div class="stock-controls"><div class="tabs">' +
      tabs
        .map(function (tab) {
          return (
            '<button class="tab-button ' +
            (state.stockFilter === tab.key ? "active" : "") +
            '" data-stock-filter="' +
            tab.key +
            '">' +
            escapeHtml(tab.label + " " + tab.count) +
            "</button>"
          );
        })
        .join("") +
      '</div><form class="stock-search-form" id="stock-filter-form"><input class="input" name="query" value="' +
      escapeHtml(state.stockQuery) +
      '" placeholder="종목명, 티커, 메모 검색" /><button class="secondary-button">검색</button><button class="secondary-button" type="button" id="stock-filter-clear">초기화</button></form></div>'
    );
  }

  function renderStockCards(items) {
    var filteredItems = items.filter(stockMatchesFilter);
    var holdings = filteredItems.filter(function (item) { return stockStatusKey(item) === "holding"; });
    var watchlist = filteredItems.filter(function (item) { return stockStatusKey(item) !== "holding"; });
    if (!items.length) return empty("보유 주식이나 관심 주식을 추가하면 가격과 관련 뉴스가 여기에 표시됩니다.");
    if (state.stockLoading && !state.stockData.length) return empty("종목 정보를 불러오는 중입니다.");
    if (!filteredItems.length) return empty("조건에 맞는 종목이 없습니다.");

    return (
      '<div class="stock-groups">' +
      renderStockGroup("보유 주식", holdings, "보유 주식이 없습니다.") +
      renderStockGroup("관심 주식", watchlist, "관심 주식이 없습니다.") +
      "</div>"
    );
  }

  function renderStockGroup(title, items, emptyText) {
    return (
      '<div class="stock-group"><div class="stock-group-header"><h3>' +
      escapeHtml(title) +
      '</h3><span class="status-pill">' +
      items.length +
      "</span></div>" +
      (items.length
        ? items
            .map(function (item) {
              var symbol = String(item.ticker || item.title || "").trim();
              var data = state.stockData.filter(function (entry) { return entry.inputSymbol === symbol; })[0];
              return renderStockCard(item, data);
            })
            .join("")
        : empty(emptyText)) +
      "</div>"
    );
  }

  function renderStockCard(item, data) {
    if (state.editingStockId === item.id) return renderStockEditor(item);
    var quote = data && data.quote;
    var news = data && data.news ? data.news : [];
    var change = quote ? quote.change : null;
    var pct = quote ? quote.changePercent : null;
    var changeText = quote && change !== null && change !== undefined ? (Number(change) > 0 ? "+" : "") + formatNumber(change, 2) + " (" + formatPercent(pct) + ")" : "-";
    var holdingMeta = stockHoldingMeta(item);
    return (
      '<article class="stock-card"><div class="stock-card-head"><div><div class="item-title">' +
      escapeHtml(quote ? quote.name : item.title) +
      '</div><div class="item-meta">' +
      escapeHtml(quote ? quote.displaySymbol + " · " + quote.exchange : item.ticker || item.title) +
      '</div></div><div class="stock-card-tools"><span class="stock-status ' +
      stockStatusKey(item) +
      '">' +
      escapeHtml(stockStatusLabel(item)) +
      '</span><button class="secondary-button compact-button" data-stock-toggle="' +
      item.id +
      '">' +
      escapeHtml(stockStatusKey(item) === "holding" ? "관심으로" : "보유로") +
      '</button><button class="secondary-button compact-button" data-stock-edit="' +
      item.id +
      '">수정</button><button class="icon-button" data-item-delete="' +
      item.id +
      '" title="삭제">×</button></div></div>' +
      (quote
        ? '<div class="quote-row"><div><div class="quote-price">' +
          formatNumber(quote.price, 2) +
          ' <span>' +
          escapeHtml(quote.currency || "") +
          '</span></div><div class="quote-change ' +
          changeClass(change) +
          '">' +
          escapeHtml(changeText) +
          '</div></div><div class="quote-meta"><span>' +
          escapeHtml(quote.source) +
          '</span><span>' +
          escapeHtml(quote.asOf || "시간 정보 없음") +
          "</span></div></div>" +
          '<div class="quote-grid"><div><span>시가</span><strong>' +
          formatNumber(quote.open, 2) +
          '</strong></div><div><span>고가</span><strong>' +
          formatNumber(quote.high, 2) +
          '</strong></div><div><span>저가</span><strong>' +
          formatNumber(quote.low, 2) +
          '</strong></div><div><span>거래량</span><strong>' +
          formatNumber(quote.volume, 0) +
          "</strong></div></div>"
        : '<div class="stock-message">' + escapeHtml(data && data.error ? data.error : "가격 정보를 아직 불러오지 못했습니다.") + "</div>") +
      (holdingMeta ? '<p class="item-meta">' + escapeHtml(holdingMeta) + "</p>" : "") +
      (item.notes ? '<p class="item-meta">' + escapeHtml(item.notes) + "</p>" : "") +
      '<div class="news-block"><h3>최근 소식</h3>' +
      (news.length
        ? news
            .slice(0, 5)
            .map(function (entry) {
              return '<a class="news-link" href="' + escapeHtml(entry.url) + '" target="_blank" rel="noreferrer"><strong>' + escapeHtml(entry.title) + '</strong><span>' + escapeHtml((entry.source || "뉴스") + (entry.publishedAt ? " · " + entry.publishedAt : "")) + "</span></a>";
            })
            .join("")
        : '<p class="item-meta">관련 뉴스를 찾지 못했습니다.</p>') +
      "</div></article>"
    );
  }

  function renderStockEditor(item) {
    var fields = item.fields || {};
    return (
      '<article class="stock-card editing"><form class="stock-edit-form" data-stock-edit-form="' +
      item.id +
      '"><div class="stock-card-head"><div><div class="item-title">종목 수정</div><div class="item-meta">' +
      escapeHtml(item.ticker || item.title) +
      '</div></div><button class="icon-button" type="button" data-stock-edit-cancel="' +
      item.id +
      '" title="취소">×</button></div><div class="form-grid"><div class="field"><label>기업명</label><input class="input" name="title" value="' +
      escapeHtml(item.title) +
      '" /></div><div class="field"><label>티커/종목코드</label><input class="input" name="ticker" value="' +
      escapeHtml(item.ticker || "") +
      '" /></div><div class="field"><label>구분</label><select class="select" name="status"><option value="holding" ' +
      (stockStatusKey(item) === "holding" ? "selected" : "") +
      '>보유 주식</option><option value="watch" ' +
      (stockStatusKey(item) === "watch" ? "selected" : "") +
      '>관심 주식</option></select></div><div class="field"><label>보유 수량</label><input class="input" name="amount" inputmode="decimal" value="' +
      escapeHtml(itemAmountValue(item)) +
      '" /></div><div class="field"><label>평균단가</label><input class="input" name="averagePrice" inputmode="decimal" value="' +
      escapeHtml(fields.averagePrice || "") +
      '" /></div><div class="field"><label>목표가</label><input class="input" name="targetPrice" inputmode="decimal" value="' +
      escapeHtml(fields.targetPrice || "") +
      '" /></div></div><div class="field"><label>관찰 메모</label><textarea class="textarea" name="notes">' +
      escapeHtml(item.notes || "") +
      '</textarea></div><div class="actions"><button class="primary-button">저장</button><button class="secondary-button" type="button" data-stock-edit-cancel="' +
      item.id +
      '">취소</button></div></form></article>'
    );
  }

  function renderDomain(config) {
    var items = state.snapshot.items.filter(function (item) { return item.type === config.type; });
    return (
      '<div class="domain-grid"><section class="panel"><div class="panel-header"><h2>추가</h2><span class="status-pill">' +
      items.length +
      '</span></div><form class="panel-body stack" id="item-form" data-type="' +
      config.type +
      '">' +
      '<div class="field"><label>' +
      escapeHtml(config.primaryLabel) +
      '</label><input class="input" name="title" /></div>' +
      '<div class="form-grid"><div class="field"><label>' +
      escapeHtml(config.secondaryLabel) +
      '</label><input class="input" name="secondary" /></div><div class="field"><label>상태</label><input class="input" name="status" value="' +
      (config.type === "schedule" ? "planned" : "open") +
      '" /></div><div class="field"><label>' +
      escapeHtml(config.dateLabel) +
      '</label><input class="input" name="date" type="date" /></div><div class="field"><label>' +
      escapeHtml(config.amountLabel) +
      '</label><input class="input" name="amount" /></div></div>' +
      '<div class="field"><label>' +
      escapeHtml(config.noteLabel) +
      '</label><textarea class="textarea" name="notes"></textarea></div><button class="primary-button">저장</button></form></section>' +
      '<section class="panel"><div class="panel-header"><h2>목록</h2></div><div class="panel-body list">' +
      (items.length ? items.map(renderDomainItem).join("") : empty(config.empty)) +
      "</div></section></div>"
    );
  }

  function renderDomainItem(item) {
    return '<div class="item-card">' + renderItemCard(item) + '<div class="actions"><button class="secondary-button" data-item-done="' + item.id + '">' + (item.status === "done" ? "열기" : "완료") + '</button><button class="icon-button" data-item-delete="' + item.id + '" title="삭제">×</button></div></div>';
  }

  function renderItemCard(item) {
    var details = [domainLabels[item.type], item.status, item.date, item.ticker, item.location, item.amount !== undefined && item.amount !== "" && item.amount !== null ? String(item.amount) + (item.currency ? " " + item.currency : "") : ""]
      .filter(Boolean)
      .join(" · ");
    return (
      '<div class="stack"><div class="item-row"><div><div class="item-title">' +
      escapeHtml(item.title) +
      '</div><div class="item-meta">' +
      escapeHtml(details) +
      "</div></div></div>" +
      (item.notes ? '<p class="item-meta">' + escapeHtml(item.notes) + "</p>" : "") +
      "</div>"
    );
  }

  function renderProfile() {
    var profile = state.snapshot.profile;
    var fields = [
      ["ownerName", "이름", "input"],
      ["assistantName", "비서 이름", "input"],
      ["preferredLanguage", "언어", "input"],
      ["tone", "말투", "input"],
      ["answerStyle", "답변 방식", "textarea"],
      ["decisionStyle", "의사결정 방식", "textarea"],
      ["financePolicy", "투자 기준", "textarea"],
      ["travelPolicy", "여행 기준", "textarea"],
      ["schedulePolicy", "일정 기준", "textarea"],
      ["assetPolicy", "자산 기준", "textarea"],
      ["riskStyle", "리스크 성향", "textarea"],
      ["boundaries", "경계", "textarea"]
    ];
    return (
      '<section class="panel"><div class="panel-header"><h2>프로필</h2></div><form class="panel-body stack" id="profile-form"><div class="form-grid">' +
      fields
        .map(function (field) {
          var key = field[0];
          var label = field[1];
          var type = field[2];
          if (type === "textarea") {
            return '<div class="field full"><label>' + label + '</label><textarea class="textarea" name="' + key + '">' + escapeHtml(profile[key]) + "</textarea></div>";
          }
          return '<div class="field"><label>' + label + '</label><input class="input" name="' + key + '" value="' + escapeHtml(profile[key]) + '" /></div>';
        })
        .join("") +
      '</div><button class="primary-button">저장</button></form></section>'
    );
  }

  function empty(text) {
    return '<div class="empty-state">' + escapeHtml(text) + "</div>";
  }

  function bindEvents() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-view]"), function (button) {
      button.addEventListener("click", function () {
        state.view = button.getAttribute("data-view");
        saveCurrentView(state.view);
        render();
        if (state.view === "stocks") loadStocks(false);
      });
    });

    var messages = document.getElementById("messages");
    if (messages) messages.scrollTop = messages.scrollHeight;

    var chatForm = document.getElementById("chat-form");
    if (chatForm) {
      var chatInput = document.getElementById("chat-input");
      if (chatInput) {
        chatInput.addEventListener("keydown", function (event) {
          if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
          event.preventDefault();
          if (chatForm.requestSubmit) {
            chatForm.requestSubmit();
          } else {
            chatForm.dispatchEvent(new Event("submit", { cancelable: true }));
          }
        });
      }

      chatForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var input = document.getElementById("chat-input");
        var message = input.value.trim();
        if (!message || state.sending) return;

        state.snapshot.messages.push({
          id: "local-" + Date.now(),
          role: "user",
          content: message,
          createdAt: new Date().toISOString()
        });
        state.sending = true;
        render();

        requestJson("/api/chat", {
          method: "POST",
          body: JSON.stringify({ message: message })
        })
          .then(function (response) {
            state.fallback = response.usedFallback;
            state.responseEngine = response.engine || (response.usedFallback ? "local" : "ai");
            return load();
          })
          .catch(function (error) {
            state.error = error.message;
            state.sending = false;
            render();
          })
          .finally(function () {
            state.sending = false;
            render();
          });
      });
    }

    var memoryForm = document.getElementById("memory-form");
    if (memoryForm) {
      memoryForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var form = new FormData(memoryForm);
        requestJson("/api/memories", {
          method: "POST",
          body: JSON.stringify({
            content: form.get("content"),
            category: form.get("category"),
            status: form.get("status"),
            importance: 3
          })
        }).then(load);
      });
    }

    Array.prototype.forEach.call(document.querySelectorAll("[data-memory-filter]"), function (button) {
      button.addEventListener("click", function () {
        state.memoryFilter = button.getAttribute("data-memory-filter");
        render();
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-memory-approve]"), function (button) {
      button.addEventListener("click", function () {
        patchMemory(button.getAttribute("data-memory-approve"), { status: "approved" });
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-memory-archive]"), function (button) {
      button.addEventListener("click", function () {
        patchMemory(button.getAttribute("data-memory-archive"), { status: "archived" });
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-memory-delete]"), function (button) {
      button.addEventListener("click", function () {
        requestJson("/api/memories/" + button.getAttribute("data-memory-delete"), { method: "DELETE" }).then(load);
      });
    });

    var itemForm = document.getElementById("item-form");
    if (itemForm) {
      itemForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var form = new FormData(itemForm);
        var type = itemForm.getAttribute("data-type");
        var secondary = String(form.get("secondary") || "");
        var payload = {
          type: type,
          title: form.get("title"),
          status: form.get("status"),
          date: form.get("date"),
          amount: form.get("amount"),
          notes: form.get("notes")
        };
        if (type === "stock") payload.ticker = secondary;
        else payload.location = secondary;
        if (type === "asset" || type === "trip") payload.currency = "KRW";
        requestJson("/api/items", { method: "POST", body: JSON.stringify(payload) }).then(load);
      });
    }

    var stockRefresh = document.getElementById("stock-refresh");
    if (stockRefresh) {
      stockRefresh.addEventListener("click", function () {
        loadStocks(true);
      });
    }

    var stockForm = document.getElementById("stock-form");
    if (stockForm) {
      var stockPreset = document.getElementById("stock-preset");
      var stockTitle = document.getElementById("stock-title");
      var stockTicker = document.getElementById("stock-ticker");
      if (stockPreset) {
        stockPreset.addEventListener("change", function () {
          var selected = stockPreset.value;
          var preset = findStockPreset(selected);
          if (preset) {
            stockTitle.value = preset.name;
            stockTicker.value = preset.ticker;
          } else if (selected !== "__custom") {
            stockTitle.value = "";
            stockTicker.value = "";
          }
        });
      }

      stockForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var form = new FormData(stockForm);
        var preset = findStockPreset(String(form.get("preset") || ""));
        var ticker = String(form.get("ticker") || "").trim().toUpperCase();
        var title = String(form.get("title") || "").trim();
        var status = form.get("status") === "watch" ? "watch" : "holding";
        if (preset) {
          ticker = ticker || preset.ticker;
          title = title || preset.name;
        }
        if (!ticker && title) ticker = title.toUpperCase();
        if (!title) title = ticker;
        if (!ticker && !title) {
          state.error = "티커나 종목코드를 입력하세요.";
          render();
          return;
        }
        if (stockExists(ticker, status, "")) {
          state.error = "이미 같은 구분으로 추가된 종목입니다.";
          render();
          return;
        }
        requestJson("/api/items", {
          method: "POST",
          body: JSON.stringify({
            type: "stock",
            title: title,
            ticker: ticker,
            status: status,
            amount: status === "holding" ? form.get("amount") : "",
            notes: form.get("notes"),
            fields: {
              averagePrice: String(form.get("averagePrice") || "").trim(),
              targetPrice: String(form.get("targetPrice") || "").trim()
            }
          })
        }).then(function () {
          return load().then(function () {
            return loadStocks(true);
          });
        });
      });
    }

    var stockFilterForm = document.getElementById("stock-filter-form");
    if (stockFilterForm) {
      stockFilterForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var form = new FormData(stockFilterForm);
        state.stockQuery = String(form.get("query") || "").trim();
        render();
      });
    }

    var stockFilterClear = document.getElementById("stock-filter-clear");
    if (stockFilterClear) {
      stockFilterClear.addEventListener("click", function () {
        state.stockQuery = "";
        state.stockFilter = "all";
        render();
      });
    }

    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-filter]"), function (button) {
      button.addEventListener("click", function () {
        state.stockFilter = button.getAttribute("data-stock-filter") || "all";
        render();
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-edit]"), function (button) {
      button.addEventListener("click", function () {
        state.editingStockId = button.getAttribute("data-stock-edit");
        render();
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-edit-cancel]"), function (button) {
      button.addEventListener("click", function () {
        if (state.editingStockId === button.getAttribute("data-stock-edit-cancel")) {
          state.editingStockId = "";
          render();
        }
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-toggle]"), function (button) {
      button.addEventListener("click", function () {
        var item = findItem(button.getAttribute("data-stock-toggle"));
        if (!item) return;
        var nextStatus = stockStatusKey(item) === "holding" ? "watch" : "holding";
        var ticker = String(item.ticker || item.title || "").trim().toUpperCase();
        if (stockExists(ticker, nextStatus, item.id)) {
          state.error = "이미 같은 구분으로 추가된 종목입니다.";
          render();
          return;
        }
        stockPatch(item.id, {
          status: nextStatus,
          amount: nextStatus === "holding" ? itemAmountValue(item) : ""
        }, false);
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-stock-edit-form]"), function (formElement) {
      formElement.addEventListener("submit", function (event) {
        event.preventDefault();
        var id = formElement.getAttribute("data-stock-edit-form");
        var form = new FormData(formElement);
        var ticker = String(form.get("ticker") || "").trim().toUpperCase();
        var title = String(form.get("title") || "").trim();
        var status = form.get("status") === "watch" ? "watch" : "holding";
        if (!ticker && title) ticker = title.toUpperCase();
        if (!title) title = ticker;
        if (!ticker && !title) {
          state.error = "티커나 종목코드를 입력하세요.";
          render();
          return;
        }
        if (stockExists(ticker, status, id)) {
          state.error = "이미 같은 구분으로 추가된 종목입니다.";
          render();
          return;
        }
        state.editingStockId = "";
        stockPatch(id, {
          title: title,
          ticker: ticker,
          status: status,
          amount: status === "holding" ? form.get("amount") : "",
          notes: form.get("notes"),
          fields: {
            averagePrice: String(form.get("averagePrice") || "").trim(),
            targetPrice: String(form.get("targetPrice") || "").trim()
          }
        }, true);
      });
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-item-done]"), function (button) {
      button.addEventListener("click", function () {
        var item = state.snapshot.items.filter(function (candidate) { return candidate.id === button.getAttribute("data-item-done"); })[0];
        requestJson("/api/items/" + item.id, {
          method: "PATCH",
          body: JSON.stringify({ status: item.status === "done" ? "open" : "done" })
        }).then(load);
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-item-delete]"), function (button) {
      button.addEventListener("click", function () {
        requestJson("/api/items/" + button.getAttribute("data-item-delete"), { method: "DELETE" }).then(load);
      });
    });

    var profileForm = document.getElementById("profile-form");
    if (profileForm) {
      profileForm.addEventListener("submit", function (event) {
        event.preventDefault();
        var form = new FormData(profileForm);
        var payload = {};
        Array.prototype.forEach.call(profileForm.elements, function (element) {
          if (element.name) payload[element.name] = form.get(element.name);
        });
        requestJson("/api/profile", { method: "PUT", body: JSON.stringify(payload) }).then(load);
      });
    }
  }

  function patchMemory(id, patch) {
    requestJson("/api/memories/" + id, { method: "PATCH", body: JSON.stringify(patch) }).then(load);
  }

  window.addEventListener("hashchange", function () {
    var nextView = viewFromHash();
    if (nextView && nextView !== state.view) {
      state.view = nextView;
      saveCurrentView(nextView);
      render();
      if (state.view === "stocks") loadStocks(false);
    }
  });

  saveCurrentView(state.view);
  load();
})();
