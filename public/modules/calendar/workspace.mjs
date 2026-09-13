import { currentInvestmentCalendar, currentInvestmentCalendarCandidates, defaultInvestmentCalendarDraft, investmentCalendarCandidateById, investmentCalendarEventTypes } from "./commands.mjs";
import { INVESTMENT_CALENDAR_CANDIDATE_PAGE_SIZE } from "./constants.mjs";
import { renderCalendarReleaseInformation } from "./results.mjs";
import { decisionStateMeta } from "../decisions/signals.mjs";
import { stockDisplayName, textWithDisplaySymbol } from "../instruments/catalog.mjs";
import { renderInstrumentWorkspaceLink } from "../instruments/workspace.mjs";
import { editorWorkDetailPayload, renderWorkDetailButton } from "../navigation/detail.mjs";
import { mobileInfiniteScrollEnabled, renderMobileInfiniteScrollFooter } from "../navigation/infinite-list.mjs";
import { renderNotificationDetailMetric } from "../notifications/reasoning.mjs";
import { appDateTimeParts } from "../settings/preferences.mjs";
import { consoleMetricTargetAttributes, renderConsoleManagedPage } from "../shared/console.mjs";
import { formatClock, latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml, uniqueTextItems } from "../shared/text.mjs";
import { cardFormatAttrs, cardTypeAttrs, renderEmptyState } from "../shell/layout.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { calendarState } from "../state/calendar.mjs";
import { settingsState } from "../state/settings.mjs";

function renderInvestmentCalendarPage(snapshot) {
  return renderConsoleManagedPage("calendar", [], [
    '<section class="admin-grid investment-calendar-view">',
    renderInvestmentCalendarSummaryPanel(),
    '<div class="investment-calendar-primary-grid">',
    renderInvestmentCalendarMonthPanel(),
    renderInvestmentCalendarRailPanel(),
    '</div>',
    '</section>'
  ].join(""));
}

function calendarCandidateBoardWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Calendar Governance",
    "캘린더 후보 검토",
    "공식 일정 확인·승인·제외",
    renderInvestmentCalendarCandidatePanel()
  );
}

function investmentCalendarEvents() {
  var payload = currentInvestmentCalendar();
  return Array.isArray(payload.events) ? payload.events : [];
}

function investmentCalendarUpcomingEvents() {
  var nowValue = Date.now();
  return investmentCalendarEvents().filter(function (event) {
    var value = Date.parse(event.allDay && event.localDate ? event.localDate + "T23:59:59" : (event.startsAt || ""));
    return Number.isFinite(value) && value >= nowValue;
  }).sort(function (a, b) {
    return Date.parse(a.startsAt || "") - Date.parse(b.startsAt || "");
  });
}

function investmentCalendarEventTypeLabel(type) {
  var found = investmentCalendarEventTypes().filter(function (item) {
    return item.type === type;
  })[0];
  return found ? found.label : (type || "이벤트");
}

function investmentCalendarTone(event) {
  var importance = Number((event || {}).importance || 0);
  if (importance >= 85) return "danger";
  if (importance >= 70) return "watch";
  return "hold";
}

function investmentCalendarDayKey(value) {
  var raw = String(value || "");
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw;
  var parts = appDateTimeParts(value);
  return parts ? [parts.year, parts.month, parts.day].join("-") : "";
}

function investmentCalendarEventDayKey(event) {
  var localDate = String((event || {}).localDate || "");
  if ((event || {}).allDay && /^\d{4}-\d{2}-\d{2}$/.test(localDate)) return localDate;
  return investmentCalendarDayKey((event || {}).startsAt);
}

function investmentCalendarScheduleLabel(event) {
  if ((event || {}).allDay) {
    return (investmentCalendarEventDayKey(event) || "날짜 미정") + " · 종일";
  }
  return formatClock((event || {}).startsAt);
}

function investmentCalendarMonthDate() {
  var parts = appDateTimeParts(new Date());
  var year = parts ? Number(parts.year) : new Date().getFullYear();
  var month = parts ? Number(parts.month) - 1 : new Date().getMonth();
  return new Date(year, month + Number(calendarState.investmentCalendarMonthOffset || 0), 1);
}

function investmentCalendarMonthKey(date) {
  var value = date instanceof Date ? date : new Date(date || "");
  if (Number.isNaN(value.getTime())) return "";
  return value.getFullYear() + "-" + String(value.getMonth() + 1).padStart(2, "0");
}

function investmentCalendarMonthLabel(date) {
  var value = date instanceof Date ? date : new Date(date || "");
  if (Number.isNaN(value.getTime())) return "캘린더";
  return value.getFullYear() + "년 " + (value.getMonth() + 1) + "월";
}

function investmentCalendarDayLabel(key) {
  if (!key) return "선택 날짜";
  var parts = String(key).split("-").map(function (part) { return Number(part); });
  if (parts.length !== 3 || parts.some(function (part) { return !Number.isFinite(part); })) return key;
  return parts[0] + "년 " + parts[1] + "월 " + parts[2] + "일";
}

function investmentCalendarEventTimeLabel(event) {
  if ((event || {}).allDay) return "종일";
  var parts = appDateTimeParts((event || {}).startsAt);
  return parts ? parts.hour + ":" + parts.minute : "시간 미정";
}

function investmentCalendarTimeStateLabel(event) {
  var payload = investmentCalendarPayload(event);
  var value = String(payload.timeState || "");
  if (value === "userConfirmed") return "사용자 확인 시각";
  if (value === "sourceProvided") return "출처 제공 시각";
  if (value === "estimatedDefault") return "확인 전 기본 시각";
  if (value === "operationalDefault") return "발표시각 미확정 · 알림 기준";
  if (value === "official") return "공식 발표 시각";
  if (payload.autoDetected && payload.scheduleState === "estimated") return "확인 전 기본 시각";
  if ((event || {}).allDay) return "종일 일정";
  return "등록 시각";
}

function investmentCalendarSymbolDetails(event) {
  return Array.isArray((event || {}).symbolDetails) ? event.symbolDetails : [];
}

function investmentCalendarSymbolDetail(event, symbol) {
  var normalized = String(symbol || "").trim().toUpperCase();
  return investmentCalendarSymbolDetails(event).filter(function (detail) {
    return String((detail || {}).symbol || "").trim().toUpperCase() === normalized;
  })[0] || { symbol: normalized };
}

function investmentCalendarDisplayText(event, value) {
  var text = String(value || "");
  ((event || {}).symbols || []).forEach(function (symbol) {
    text = textWithDisplaySymbol(text, symbol, investmentCalendarSymbolDetail(event, symbol));
  });
  return text;
}

function investmentCalendarDisplayTitle(event, fallback) {
  return investmentCalendarDisplayText(event, (event || {}).displayTitle || (event || {}).title || fallback || "투자 이벤트");
}

function investmentCalendarSymbolMetaLabel(event) {
  return uniqueTextItems(((event || {}).symbols || []).concat((event || {}).markets || [])).join(" · ");
}

function investmentCalendarTargetLabel(event) {
  var targets = ((event || {}).symbols || []).map(function (symbol) {
    return stockDisplayName(symbol, investmentCalendarSymbolDetail(event, symbol));
  }).concat(((event || {}).markets || []));
  return targets.slice(0, 3).join(" · ") || "전체 포트폴리오";
}

function investmentCalendarReminderLabel(event) {
  if (String((event || {}).status || "").toLowerCase() === "tentative") return "검증 전 알림 대기";
  var offsets = Array.isArray((event || {}).reminderOffsetsMinutes) ? event.reminderOffsetsMinutes : [];
  return offsets.length ? "알림 " + offsets.join(", ") + "분 전" : "알림 없음";
}

function investmentCalendarPayload(event) {
  var payload = (event || {}).payload;
  return payload && typeof payload === "object" ? payload : {};
}

function investmentCalendarWatchItems(event) {
  var payload = investmentCalendarPayload(event);
  if (Array.isArray(payload.watchItems) && payload.watchItems.length) return payload.watchItems.slice(0, 5);
  var type = (event || {}).eventType || "";
  if (type === "adrListing") return ["상장 거래소와 예정일", "원주/ADR 교환비율과 수수료", "첫 거래 유동성과 원주 가격 차이"];
  if (type === "indexInclusion") return ["편입 지수와 적용일", "예상 패시브 매수 규모", "리밸런싱 전후 거래량"];
  if (type === "capitalRaise") return ["조달 규모와 목적", "발행가/전환가와 희석률", "자금 사용 계획"];
  if (type === "earnings") return ["매출/EPS의 시장 기대 대비 차이", "마진과 비용 구조", "다음 분기 가이던스"];
  if (type === "centralBank" || type === "macro") return ["컨센서스 대비 결과", "금리·달러·지수 선물 반응", "장초반 거래량 변화"];
  return ["예상치 대비 실제 결과", "발표 직후 가격·거래량 반응", "기존 투자 가정과 달라진 점"];
}

function investmentCalendarImpactText(event) {
  var payload = investmentCalendarPayload(event);
  if (payload.investmentImpact) return investmentCalendarDisplayText(event, payload.investmentImpact);
  var target = investmentCalendarTargetLabel(event);
  var type = (event || {}).eventType || "";
  if (type === "adrListing") return "ADR/GDR 상장은 해외 투자자 접근성, 거래 유동성, 원주와 예탁증서 간 가격 차이를 통해 " + target + "의 재평가와 변동성에 영향을 줄 수 있습니다.";
  if (type === "indexInclusion") return "지수 편입은 패시브 자금 유입과 리밸런싱 수급을 통해 " + target + "의 단기 거래량과 가격 변동성을 키울 수 있습니다.";
  if (type === "capitalRaise") return "증자·자금조달은 성장 투자 재원과 주식 희석 가능성을 동시에 만들며 단기 수급 부담으로 이어질 수 있습니다.";
  if (type === "earnings") return "실적 이벤트는 " + target + "의 이익 추정치, 밸류에이션 프리미엄, 다음 분기 가이던스 재평가로 이어질 수 있습니다.";
  if (type === "centralBank") return "중앙은행 결정은 할인율과 위험선호를 바꾸며 " + target + "의 멀티플, 기술주 수급, 환율 민감도에 영향을 줄 수 있습니다.";
  return "이벤트 결과가 " + target + "의 변동성, 뉴스 흐름, 포트폴리오 리스크 점검 우선순위에 영향을 줄 수 있습니다.";
}

function renderInvestmentCalendarImpactPreview(event) {
  var impact = investmentCalendarImpactText(event);
  if (!impact) return "";
  return '<p class="investment-calendar-impact-preview">' + escapeHtml(impact) + '</p>';
}

function renderInvestmentCalendarWatchList(event) {
  var items = investmentCalendarWatchItems(event);
  if (!items.length) return "";
  return '<div class="investment-calendar-watch-list">' + items.map(function (item) {
    return '<span>' + escapeHtml(item) + '</span>';
  }).join("") + '</div>';
}

function investmentCalendarSortEvents(events) {
  return latestChangedFirst(events || [], null, function (a, b) {
    return Date.parse(a.startsAt || "") - Date.parse(b.startsAt || "");
  });
}

function investmentCalendarEventsByDay(events) {
  return investmentCalendarSortEvents(events).reduce(function (map, event) {
    var key = investmentCalendarEventDayKey(event);
    if (!key) return map;
    if (!map[key]) map[key] = [];
    map[key].push(event);
    return map;
  }, {});
}

function investmentCalendarMonthDays(monthDate) {
  var first = new Date(monthDate.getFullYear(), monthDate.getMonth(), 1);
  var cursor = new Date(first);
  cursor.setDate(first.getDate() - first.getDay());
  var days = [];
  for (var index = 0; index < 42; index += 1) {
    days.push(new Date(cursor));
    cursor.setDate(cursor.getDate() + 1);
  }
  return days;
}

function investmentCalendarSelectedDayKey(monthDate, eventsByDay) {
  var currentMonthKey = investmentCalendarMonthKey(monthDate);
  var focused = calendarState.investmentCalendarFocusedDayKey || "";
  if (focused && focused.slice(0, 7) === currentMonthKey) return focused;
  var todayKey = investmentCalendarDayKey(new Date());
  if (todayKey.slice(0, 7) === currentMonthKey) return todayKey;
  var eventKeys = Object.keys(eventsByDay || {}).filter(function (key) {
    return key.slice(0, 7) === currentMonthKey;
  }).sort();
  return eventKeys[0] || investmentCalendarDayKey(monthDate);
}

function renderInvestmentCalendarSummaryPanel() {
  var payload = currentInvestmentCalendar();
  var summary = payload.summary || {};
  var upcoming = investmentCalendarUpcomingEvents();
  var next = upcoming[0] || {};
  var important = investmentCalendarEvents().filter(function (event) {
    return Number((event || {}).importance || 0) >= 80;
  }).length;
  return [
    '<article class="panel investment-calendar-summary-panel"' + cardTypeAttrs("source-card", "hold") + '>',
    '<div class="panel-head">',
    '<div><h2>투자 캘린더</h2></div>',
    '<div class="toolbar investment-calendar-summary-actions">',
    renderCalendarEntryButton("이벤트 등록", "text-button primary"),
    '<details class="investment-calendar-tool-menu">',
    '<summary class="text-button">일정 도구</summary>',
    '<div class="investment-calendar-tool-menu-list">',
    '<button class="text-button" type="button" data-action="run-investment-calendar-reminders"' + (calendarState.investmentCalendarRunning ? ' disabled' : '') + '>' + (calendarState.investmentCalendarRunning ? "확인 중" : "리마인더 확인") + '</button>',
    '<button class="text-button" type="button" data-action="refresh-investment-calendar"' + (calendarState.investmentCalendarLoading ? ' disabled' : '') + '>' + (calendarState.investmentCalendarLoading ? "조회 중" : "새로고침") + '</button>',
    '<button class="text-button" type="button" data-action="sync-official-investment-calendar"' + (calendarState.investmentCalendarSyncing ? ' disabled' : '') + '>' + (calendarState.investmentCalendarSyncing ? "동기화 중" : "공식일정 동기화") + '</button>',
    '<button class="text-button" type="button" data-action="discover-investment-calendar"' + (calendarState.investmentCalendarDiscovering ? ' disabled' : '') + '>' + (calendarState.investmentCalendarDiscovering ? "탐색 중" : "일정 탐색") + '</button>',
    '<button class="text-button" type="button" data-action="research-investment-calendar"' + (calendarState.investmentCalendarResearching ? ' disabled' : '') + '>' + (calendarState.investmentCalendarResearching ? "분석 중" : "뉴스 후보 분석") + '</button>',
    '</div>',
    '</details>',
    '</div>',
    '</div>',
    calendarState.investmentCalendarError ? '<p class="form-error">' + escapeHtml(calendarState.investmentCalendarError) + '</p>' : '',
    '<details class="oa-secondary-details" id="disclosure-calendar-metrics"><summary><strong>일정 현황</strong></summary><div class="investment-calendar-kpis">',
    renderCalendarKpi("전체", summary.total || 0, "등록 이벤트", "metric-cell", "hold", { type: "anchor", value: "calendar-events" }),
    renderCalendarKpi("예정", summary.upcoming || upcoming.length || 0, "표시 일정", "metric-cell", upcoming.length ? "watch" : "hold", { type: "anchor", value: "calendar-events" }),
    renderCalendarKpi("중요", important, "중요도 80+", "metric-cell", important ? "caution" : "hold", { type: "anchor", value: "calendar-events" }),
    renderCalendarKpi("다음", next.startsAt ? investmentCalendarScheduleLabel(next) : "대기", next.startsAt ? investmentCalendarEventTypeLabel(next.eventType) + " · " + investmentCalendarTargetLabel(next) : "등록 필요", "metric-cell", next.startsAt ? "watch" : "hold", next.startsAt ? { type: "detail", value: "investment-calendar-event", key: next.eventId || next.id || next.title || "" } : { type: "calendar-entry" }),
    '</div></details>',
    '</article>'
  ].join("");
}

function renderCalendarKpi(label, value, detail, cardType, tone, target) {
  var targetAttributes = consoleMetricTargetAttributes(target);
  return [
    '<button type="button" class="investment-calendar-kpi is-link"' + cardTypeAttrs(cardType || "metric-cell", tone || "hold") + targetAttributes + ' aria-label="' + escapeHtml([label || "일정", value == null ? "" : value, "상세 보기"].filter(Boolean).join(" ")) + '">',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value) + '</strong>',
    '<b>' + escapeHtml(detail || "") + '</b>',
    '<i class="oa-metric-arrow" aria-hidden="true">&rarr;</i>',
    '</button>'
  ].join("");
}

function renderInvestmentCalendarFormPanel(options) {
  options = options || {};
  var draft = calendarState.investmentCalendarDraft || defaultInvestmentCalendarDraft();
  return [
    '<article class="' + escapeHtml(options.compact ? "investment-calendar-form-panel compact-form" : "panel investment-calendar-form-panel") + '"' + cardTypeAttrs("config-panel", "hold") + '>',
    options.compact ? '' : '<div class="panel-head"><div><p class="label">EVENT ENTRY</p><h2>투자 이벤트 등록</h2></div></div>',
    '<form class="investment-calendar-form" data-investment-calendar-form>',
    '<label class="setting-field wide"><span>제목</span><input data-calendar-field="title" type="text" autocomplete="off" value="' + escapeHtml(draft.title || "") + '" placeholder="예: AAPL FY26 Q3 실적 발표"></label>',
    '<label class="setting-field"><span>유형</span><select data-calendar-field="eventType">',
    investmentCalendarEventTypes().map(function (item) {
      return '<option value="' + escapeHtml(item.type) + '"' + (draft.eventType === item.type ? " selected" : "") + '>' + escapeHtml(item.label) + '</option>';
    }).join(""),
    '</select></label>',
    '<label class="setting-field"><span>시작</span><input data-calendar-field="startsAt" type="datetime-local" value="' + escapeHtml(draft.startsAt || "") + '"></label>',
    '<label class="setting-field"><span>중요도</span><input data-calendar-field="importance" type="number" min="0" max="100" step="1" value="' + escapeHtml(draft.importance || "70") + '"></label>',
    '<label class="setting-field"><span>종목</span><input data-calendar-field="symbolsText" type="text" autocomplete="off" value="' + escapeHtml(draft.symbolsText || "") + '" placeholder="005930,AAPL"></label>',
    '<label class="setting-field"><span>시장</span><input data-calendar-field="marketsText" type="text" autocomplete="off" value="' + escapeHtml(draft.marketsText || "") + '" placeholder="KOSPI,NASDAQ"></label>',
    '<label class="setting-field"><span>알림(분 전)</span><input data-calendar-field="reminderOffsetsText" type="text" autocomplete="off" value="' + escapeHtml(draft.reminderOffsetsText || "1440,60,0") + '"></label>',
    '<label class="setting-field wide"><span>메모</span><textarea data-calendar-field="notes" rows="3" autocomplete="off">' + escapeHtml(draft.notes || "") + '</textarea></label>',
    '<div class="form-actions">',
    '<button class="text-button" type="button" data-action="reset-investment-calendar-draft">초기화</button>',
    '<button class="text-button primary" type="submit"' + (calendarState.investmentCalendarSaving ? ' disabled' : '') + '>' + (calendarState.investmentCalendarSaving ? "저장 중" : "이벤트 저장") + '</button>',
    '</div>',
    '</form>',
    '</article>'
  ].join("");
}

function renderInvestmentCalendarMonthPanel() {
  var events = investmentCalendarEvents();
  var filters = calendarState.investmentCalendarFilters || {};
  var monthDate = investmentCalendarMonthDate();
  var monthKey = investmentCalendarMonthKey(monthDate);
  var eventsByDay = investmentCalendarEventsByDay(events);
  var selectedDayKey = investmentCalendarSelectedDayKey(monthDate, eventsByDay);
  var monthEvents = investmentCalendarSortEvents(events).filter(function (event) {
    return investmentCalendarEventDayKey(event).slice(0, 7) === monthKey;
  });
  var selectedEvents = eventsByDay[selectedDayKey] || [];
  return [
    '<article class="panel investment-calendar-list-panel investment-calendar-month-panel" data-console-monitor-destination="calendar-events" tabindex="-1"' + cardTypeAttrs("process-card", events.length ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div><p class="label">CALENDAR</p><h2>' + escapeHtml(investmentCalendarMonthLabel(monthDate)) + ' 투자 캘린더</h2><span>각 날짜 셀에서 예정 이벤트를 바로 확인하고, 날짜를 선택해 상세 일정을 봅니다.</span></div>',
    '<div class="investment-calendar-month-controls" aria-label="캘린더 월 이동">',
    '<button class="mini-button" type="button" data-calendar-month-step="-1">이전</button>',
    '<button class="mini-button" type="button" data-calendar-month-today>이번 달</button>',
    '<button class="mini-button" type="button" data-calendar-month-step="1">다음</button>',
    '<span class="investment-calendar-month-count">이번 달 ' + escapeHtml(monthEvents.length) + ' · 로드 ' + escapeHtml(events.length) + '</span>',
    '</div>',
    '</div>',
    '<form class="investment-calendar-filters" data-investment-calendar-filter-form>',
    '<input data-calendar-filter="symbol" type="text" value="' + escapeHtml(filters.symbol || "") + '" placeholder="종목 필터" autocomplete="off">',
    '<select data-calendar-filter="eventType"><option value="">전체 유형</option>',
    investmentCalendarEventTypes().map(function (item) {
      return '<option value="' + escapeHtml(item.type) + '"' + (filters.eventType === item.type ? " selected" : "") + '>' + escapeHtml(item.label) + '</option>';
    }).join(""),
    '</select>',
    '<select data-calendar-filter="limit">',
    ["20", "80", "200"].map(function (limit) {
      return '<option value="' + limit + '"' + (String(filters.limit || "80") === limit ? " selected" : "") + '>' + limit + '개</option>';
    }).join(""),
    '</select>',
    '<button class="text-button primary" type="submit">' + (calendarState.investmentCalendarLoading ? "조회 중" : "조회") + '</button>',
    '</form>',
    '<div class="investment-calendar-month-shell">',
    calendarState.investmentCalendarLoading ? renderEmptyState({
      tone: "watch",
      label: "Calendar",
      title: "투자 이벤트를 조회하고 있습니다",
      description: "마지막 등록 상태를 유지하면서 조건에 맞는 이벤트와 리마인더 후보를 다시 읽습니다.",
      meta: [filters.symbol || "전체 종목", filters.eventType ? investmentCalendarEventTypeLabel(filters.eventType) : "전체 유형"]
    }) : (!events.length ? renderEmptyState({
      tone: "hold",
      label: "Calendar",
      title: "등록된 투자 이벤트가 없습니다",
      description: "실적, 거시지표, 공시, 점검 일정을 등록하면 리마인더와 알림 정책에 연결됩니다.",
      meta: ["등록 폼은 레이어에서 열림", "알림 큐와 온톨로지 요청으로 연결"],
      action: renderCalendarEntryButton("이벤트 등록", "text-button primary")
    }) : renderInvestmentCalendarMonthGrid(monthDate, eventsByDay, selectedDayKey) + renderInvestmentCalendarSelectedDayAgenda(selectedDayKey, selectedEvents)),
    '</div>',
    '</article>'
  ].join("");
}

function renderInvestmentCalendarMonthGrid(monthDate, eventsByDay, selectedDayKey) {
  var monthKey = investmentCalendarMonthKey(monthDate);
  var todayKey = investmentCalendarDayKey(new Date());
  return [
    '<div class="investment-calendar-month-scroller">',
    '<div class="investment-calendar-month-weekdays" aria-hidden="true">',
    ["일", "월", "화", "수", "목", "금", "토"].map(function (label) {
      return '<span>' + escapeHtml(label) + '</span>';
    }).join(""),
    '</div>',
    '<div class="investment-calendar-month-grid">',
    investmentCalendarMonthDays(monthDate).map(function (day) {
      var key = investmentCalendarDayKey(day);
      var dayEvents = eventsByDay[key] || [];
      var classes = [
        "investment-calendar-day-cell",
        key.slice(0, 7) === monthKey ? "is-current-month" : "is-outside-month",
        key === todayKey ? "is-today" : "",
        key === selectedDayKey ? "is-selected" : "",
        dayEvents.length ? "has-events" : ""
      ].filter(Boolean).join(" ");
      return [
        '<section class="' + escapeHtml(classes) + '">',
        '<button class="investment-calendar-day-head" type="button" data-calendar-day-select="' + escapeHtml(key) + '" aria-label="' + escapeHtml(investmentCalendarDayLabel(key)) + ' 선택">',
        '<strong>' + escapeHtml(day.getDate()) + '</strong>',
        dayEvents.length ? '<span>' + escapeHtml(dayEvents.length) + '건</span>' : '<span></span>',
        '</button>',
        '<div class="investment-calendar-day-events">',
        dayEvents.slice(0, 3).map(renderInvestmentCalendarDayEventChip).join(""),
        dayEvents.length > 3 ? '<button class="investment-calendar-day-more" type="button" data-calendar-day-select="' + escapeHtml(key) + '">+' + escapeHtml(dayEvents.length - 3) + '개 더보기</button>' : '',
        '</div>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    '</div>'
  ].join("");
}

function renderInvestmentCalendarDayEventChip(event) {
  var tone = investmentCalendarTone(event);
  var key = event.eventId || event.id || event.title || "";
  var dayKey = investmentCalendarEventDayKey(event);
  return [
    '<button class="investment-calendar-day-event ' + escapeHtml(tone) + '" type="button" data-calendar-event-detail="' + escapeHtml(key) + '" data-calendar-event-day="' + escapeHtml(dayKey) + '" aria-label="' + escapeHtml(investmentCalendarEventTimeLabel(event) + " " + investmentCalendarDisplayTitle(event, "이벤트")) + '">',
    '<span>' + escapeHtml(investmentCalendarEventTimeLabel(event)) + '</span>',
    '<strong>' + escapeHtml(investmentCalendarDisplayTitle(event, "이벤트")) + '</strong>',
    '<em>' + escapeHtml(investmentCalendarTargetLabel(event) || investmentCalendarEventTypeLabel(event.eventType)) + '</em>',
    '</button>'
  ].join("");
}

function renderInvestmentCalendarSelectedDayAgenda(dayKey, events) {
  var selectedEvents = investmentCalendarSortEvents(events || []);
  return [
    '<section class="investment-calendar-selected-day">',
    '<div class="investment-calendar-selected-day-head">',
    '<div><p class="label">SELECTED DAY</p><h3>' + escapeHtml(investmentCalendarDayLabel(dayKey)) + '</h3></div>',
    '<span>' + escapeHtml(selectedEvents.length) + '건</span>',
    '</div>',
    '<div class="investment-calendar-list">',
    selectedEvents.length ? selectedEvents.map(renderInvestmentCalendarEvent).join("") : '<p class="data-refresh-status">선택한 날짜에 등록된 투자 이벤트가 없습니다.</p>',
    '</div>',
    '</section>'
  ].join("");
}

function renderInvestmentCalendarEvent(event) {
  var tone = investmentCalendarTone(event);
  var key = event.eventId || event.id || event.title || "";
  return [
    '<section class="investment-calendar-event ' + escapeHtml(tone) + '"' + cardTypeAttrs("calendar-event", tone) + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div class="investment-calendar-event-main">',
    '<div class="investment-calendar-event-date">',
    '<strong>' + escapeHtml(investmentCalendarScheduleLabel(event)) + '</strong>',
    '<span>' + escapeHtml(investmentCalendarTimeStateLabel(event)) + '</span>',
    '</div>',
    '<div class="investment-calendar-event-copy">',
    '<p class="label">' + escapeHtml(investmentCalendarEventTypeLabel(event.eventType)) + '</p>',
    '<h3>' + escapeHtml(investmentCalendarDisplayTitle(event, "이벤트")) + '</h3>',
    renderRecordChangedAt(event),
    '<div class="notification-detail-tags">',
    '<span>중요도 ' + escapeHtml(event.importance || 0) + '</span>',
    '<span>' + escapeHtml(investmentCalendarTargetLabel(event)) + '</span>',
    investmentCalendarSymbolMetaLabel(event) ? '<span>' + escapeHtml(investmentCalendarSymbolMetaLabel(event)) + '</span>' : '',
    String(event.status || "").toLowerCase() === "tentative" ? '<span>검토 전 일정</span>' : '',
    investmentCalendarPayload(event).timeState === "operationalDefault" ? '<span>시각은 알림 기준</span>' : '',
    event.releaseInformation ? '<span>' + escapeHtml(event.releaseInformation.statusLabel) + '</span>' : '',
    '<span>' + escapeHtml(investmentCalendarReminderLabel(event)) + '</span>',
    '</div>',
    event.notes ? '<p class="subtle">' + escapeHtml(investmentCalendarDisplayText(event, event.notes)) + '</p>' : '',
    renderInvestmentCalendarImpactPreview(event),
    '</div>',
    '</div>',
    '<div class="investment-calendar-event-actions">',
    renderWorkDetailButton("investment-calendar-event", key, "상세", "mini-button"),
    '<button class="mini-button danger" type="button" data-calendar-delete="' + escapeHtml(event.eventId || "") + '"' + (calendarState.investmentCalendarDeleting === event.eventId ? ' disabled' : '') + '>' + (calendarState.investmentCalendarDeleting === event.eventId ? "삭제 중" : "삭제") + '</button>',
    '</div>',
    '</section>'
  ].join("");
}

function investmentCalendarCandidates() {
  var payload = currentInvestmentCalendarCandidates();
  return latestChangedFirst(Array.isArray(payload.candidates) ? payload.candidates : []);
}

function investmentCalendarCandidatePageInfo(candidates) {
  var payload = currentInvestmentCalendarCandidates();
  var serverInfo = payload.pageInfo || {};
  var pageSize = INVESTMENT_CALENDAR_CANDIDATE_PAGE_SIZE;
  var serverTotal = Number(serverInfo.total);
  if (Number.isFinite(serverTotal)) {
    pageSize = Number(serverInfo.pageSize || pageSize) || pageSize;
    var serverPage = Number(serverInfo.page || 0);
    var serverOffset = Number(serverInfo.offset || serverPage * pageSize);
    var serverPageCount = Math.max(1, Number(serverInfo.pageCount || Math.ceil(serverTotal / pageSize)) || 1);
    var cumulative = mobileInfiniteScrollEnabled();
    return {
      total: serverTotal,
      page: Math.max(0, serverPage),
      pageSize: pageSize,
      pageCount: serverPageCount,
      start: cumulative ? 0 : (serverTotal ? serverOffset : 0),
      end: cumulative ? Math.min((candidates || []).length, serverTotal) : Math.min(serverOffset + (candidates || []).length, serverTotal),
      visible: candidates || []
    };
  }
  var total = (candidates || []).length;
  var pageCount = Math.max(1, Math.ceil(total / pageSize));
  var page = Number(calendarState.investmentCalendarCandidatePage || 0);
  if (!Number.isFinite(page)) page = 0;
  page = Math.min(Math.max(0, page), pageCount - 1);
  var start = mobileInfiniteScrollEnabled() ? 0 : page * pageSize;
  var end = Math.min(start + pageSize, total);
  if (mobileInfiniteScrollEnabled()) end = Math.min((page + 1) * pageSize, total);
  return {
    total: total,
    page: page,
    pageSize: pageSize,
    pageCount: pageCount,
    start: start,
    end: end,
    visible: (candidates || []).slice(start, end)
  };
}

function renderInvestmentCalendarCandidatePanel() {
  var payload = currentInvestmentCalendarCandidates();
  var candidates = investmentCalendarCandidates();
  var pageInfo = investmentCalendarCandidatePageInfo(candidates);
  var pending = Number((payload.summary || {}).pending || candidates.length || 0);
  var mobile = mobileInfiniteScrollEnabled();
  var initialLoading = calendarState.investmentCalendarCandidatesLoading && !candidates.length;
  return [
    '<article class="panel investment-calendar-list-panel investment-calendar-candidate-panel"' + cardTypeAttrs("process-card", candidates.length ? "watch" : "hold") + '>',
    '<div class="panel-head">',
    '<div><p class="label">CALENDAR REVIEW QUEUE</p><h2>확인 전 일정 후보</h2><span>시장 데이터와 구조화 공시에서 찾은 날짜를 등록 전 검토 대상으로 보관합니다.</span></div>',
    '<span class="metric">' + escapeHtml(pending) + '</span>',
    '</div>',
    renderInvestmentCalendarDiscoveryStatus(),
    renderInvestmentCalendarResearchStatus(),
    candidates.length && !mobile ? renderInvestmentCalendarCandidatePager(pageInfo) : '',
    '<div class="investment-calendar-list" data-console-keyed-list="calendar-candidates" aria-busy="' + (calendarState.investmentCalendarCandidatesLoading ? "true" : "false") + '">',
    initialLoading ? renderEmptyState({
      tone: "watch",
      label: "Review",
      title: "자동 감지 후보를 조회하고 있습니다",
      description: "구조화된 시장 일정과 공시 후보를 읽고 있습니다.",
      meta: ["pending"]
    }) : (!candidates.length ? renderEmptyState({
      tone: "hold",
      label: "Review",
      title: "검토할 자동 후보가 없습니다",
      description: "시장 데이터나 공시에서 확인이 필요한 일정이 발견되면 여기에 쌓입니다.",
      meta: ["실적", "배당", "ADR/GDR", "지수 편입"]
    }) : pageInfo.visible.map(renderInvestmentCalendarCandidate).join("")),
    '</div>',
    candidates.length && mobile ? renderInvestmentCalendarCandidatePager(pageInfo) : '',
    '</article>'
  ].join("");
}

function renderInvestmentCalendarResearchStatus() {
  var result = calendarState.investmentCalendarResearchResult || {};
  var collection = result.collection || {};
  var hasResult = Object.keys(result).length > 0;
  return [
    '<div class="investment-calendar-research-strip">',
    '<div>',
    '<strong>' + escapeHtml(calendarState.investmentCalendarResearching ? "AI 리서치 실행 중" : (hasResult ? "최근 AI 추천 결과" : "AI 리서치 대기")) + '</strong>',
    '<span>' + escapeHtml(hasResult ? ("근거 " + Number(result.evidenceCount || 0) + "개 · 후보 " + Number(result.storedCandidateCount || 0) + "개 · 수집 " + (collection.status || "unknown")) : "추천 실행 시 최신 뉴스 수집 후 후보 큐에만 저장합니다.") + '</span>',
    '</div>',
    '<button class="mini-button" type="button" data-action="research-investment-calendar"' + (calendarState.investmentCalendarResearching ? ' disabled' : '') + '>' + escapeHtml(calendarState.investmentCalendarResearching ? "실행 중" : "뉴스 후보 분석") + '</button>',
    '</div>'
  ].join("");
}

function renderInvestmentCalendarDiscoveryStatus() {
  var result = calendarState.investmentCalendarDiscoveryResult || {};
  var hasResult = Object.keys(result).length > 0;
  var sourceCount = Array.isArray(result.sources) ? result.sources.length : 0;
  var sourceErrorCount = Array.isArray(result.sources) ? result.sources.filter(function (source) {
    return source && source.ok === false;
  }).length : 0;
  return [
    '<div class="investment-calendar-research-strip">',
    '<div>',
    '<strong>' + escapeHtml(calendarState.investmentCalendarDiscovering ? "일정 탐색 실행 중" : (hasResult ? (result.status === "partial" ? "최근 일정 탐색 결과 · 일부 출처 확인 필요" : "최근 일정 탐색 결과") : "정기 일정 탐색 대기")) + '</strong>',
    '<span>' + escapeHtml(hasResult ? ("대상 " + Number(result.targetCount || 0) + "개 · 근거 " + Number(result.evidenceCount || 0) + "개 · 확인 후보 " + Number(result.reviewCandidateCount || 0) + "개" + (sourceCount ? " · 출처 " + sourceCount + "개" : "") + (sourceErrorCount ? " · 오류 " + sourceErrorCount + "개" : "")) : "보유·관심 종목의 실적·배당·공시 날짜를 확인해 날짜와 시각을 검토하기 전에는 후보함에만 추가합니다.") + '</span>',
    '</div>',
    '<button class="mini-button primary" type="button" data-action="discover-investment-calendar"' + (calendarState.investmentCalendarDiscovering ? ' disabled' : '') + '>' + escapeHtml(calendarState.investmentCalendarDiscovering ? "탐색 중" : "일정 탐색") + '</button>',
    '</div>'
  ].join("");
}

function renderInvestmentCalendarCandidatePager(pageInfo) {
  var from = pageInfo.total ? pageInfo.start + 1 : 0;
  var canPrev = pageInfo.page > 0;
  var canNext = pageInfo.page < pageInfo.pageCount - 1;
  var navigationReady = !calendarState.investmentCalendarCandidatesLoading;
  if (mobileInfiniteScrollEnabled()) {
    return renderMobileInfiniteScrollFooter({
      loaded: pageInfo.end,
      total: pageInfo.total,
      loading: calendarState.investmentCalendarCandidatesLoading,
      hasNext: canNext,
      nextAttributes: 'data-calendar-candidate-page="' + escapeHtml(pageInfo.page + 1) + '"'
    });
  }
  return [
    '<div class="investment-calendar-candidate-toolbar">',
    '<span>후보 ' + escapeHtml(from) + '-' + escapeHtml(pageInfo.end) + ' / ' + escapeHtml(pageInfo.total) + ' · ' + escapeHtml(pageInfo.page + 1) + '/' + escapeHtml(pageInfo.pageCount) + '페이지</span>',
    '<div class="investment-calendar-candidate-pager">',
    '<button class="mini-button" type="button" data-calendar-candidate-page="' + escapeHtml(pageInfo.page - 1) + '"' + (canPrev && navigationReady ? '' : ' disabled') + '>이전</button>',
    '<button class="mini-button" type="button" data-calendar-candidate-page="' + escapeHtml(pageInfo.page + 1) + '"' + (canNext && navigationReady ? '' : ' disabled') + '>다음</button>',
    '</div>',
    '</div>'
  ].join("");
}

function renderInvestmentCalendarCandidate(candidate) {
  var id = candidate.candidateId || "";
  var busy = calendarState.investmentCalendarCandidateReviewing === id;
  var readOnly = isStaticPreviewHost() || settingsState.serverSettingsLocked;
  var payload = investmentCalendarPayload(candidate);
  var aiRecommended = Boolean(payload.aiResearchRecommended);
  var automaticCandidate = Boolean(payload.autoDetected);
  var needsScheduleConfirmation = automaticCandidate && (payload.reviewRequired || payload.scheduleState !== "confirmed");
  var timeState = String(payload.timeState || (candidate.allDay ? "estimatedDefault" : "sourceProvided"));
  var scheduleText = candidate.startsAt
    ? formatClock(candidate.startsAt) + (timeState === "estimatedDefault" ? " · 시각 확인 필요" : "")
    : "날짜 필요";
  var reviewReason = String(candidate.reviewReason || "");
  var reason = {
    missingDate: "날짜 확인 필요",
    sourceDataUnavailable: "원문 자료 사용 불가",
    sourceNeedsVerification: "출처 확인 필요",
    sourceTrustNeedsReview: "출처 상태 확인 필요",
    eventTermsUnclear: "일정 내용 확인 필요",
    dateNeedsVerification: "날짜 원문 확인 필요",
    feedbackReview: "이전 검토 반영 필요",
    aiResearchReview: "AI 검토 필요",
    aiResearchRecommended: "AI 추천 검토"
  }[reviewReason] || "검토 필요";
  var readinessState = String(candidate.readinessState || payload.readinessState || "needs-review");
  var candidateDataState = readinessState === "blocked" ? "unavailable" : (readinessState === "ready" ? "sufficient" : "partial");
  return [
    '<section class="investment-calendar-event watch" data-console-row-key="' + escapeHtml(id) + '"' + cardTypeAttrs("calendar-event", "watch") + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<div class="investment-calendar-event-main">',
    '<div class="investment-calendar-event-date">',
    '<strong>' + escapeHtml(scheduleText) + '</strong>',
    '<span>' + escapeHtml(decisionStateMeta("data", candidateDataState, "partial").label) + '</span>',
    '</div>',
    '<div class="investment-calendar-event-copy">',
    '<p class="label">' + escapeHtml((aiRecommended ? "AI 추천 · " : "") + investmentCalendarEventTypeLabel(candidate.eventType) + " · " + reason) + '</p>',
    '<h3>' + escapeHtml(investmentCalendarDisplayTitle(candidate, "자동 감지 후보")) + '</h3>',
    renderRecordChangedAt(candidate),
    '<div class="notification-detail-tags">',
    '<span>중요도 ' + escapeHtml(candidate.importance || 0) + '</span>',
    '<span>' + escapeHtml(investmentCalendarTargetLabel(candidate)) + '</span>',
    investmentCalendarSymbolMetaLabel(candidate) ? '<span>' + escapeHtml(investmentCalendarSymbolMetaLabel(candidate)) + '</span>' : '',
    '<span>' + escapeHtml(candidate.source || "research-evidence") + '</span>',
    '</div>',
    candidate.notes ? '<p class="subtle">' + escapeHtml(investmentCalendarDisplayText(candidate, candidate.notes)) + '</p>' : '',
    renderInvestmentCalendarImpactPreview(candidate),
    renderInvestmentCalendarWatchList(candidate),
    '</div>',
    '</div>',
    '<div class="investment-calendar-event-actions">',
    '<button class="mini-button primary" type="button" data-calendar-candidate-approve="' + escapeHtml(id) + '"' + (busy || readOnly ? ' disabled' : '') + '>' + escapeHtml(readOnly ? "조회 전용" : (busy ? "처리 중" : (needsScheduleConfirmation ? "시각 확인 후 등록" : "등록"))) + '</button>',
    '<button class="mini-button danger" type="button" data-calendar-candidate-reject="' + escapeHtml(id) + '"' + (busy || readOnly ? ' disabled' : '') + '>' + escapeHtml(readOnly ? "변경 불가" : "거절") + '</button>',
    '</div>',
    '</section>'
  ].join("");
}

function renderCalendarEntryButton(label, className) {
  return '<button class="' + escapeHtml(className || "mini-button") + '" type="button" data-calendar-entry-open>' + escapeHtml(label || "이벤트 등록") + '</button>';
}

function renderCalendarEntryModal() {
  if (!calendarState.calendarEntryModalOpen) return "";
  return [
    '<div class="calendar-entry-backdrop">',
    '<section class="calendar-entry-modal" role="dialog" aria-modal="true" aria-label="투자 이벤트 등록">',
    '<header class="calendar-entry-head">',
    '<div><p class="label">Event Entry</p><h2>투자 이벤트 등록</h2><span>실적, 거시지표, 공시 일정을 리마인더 후보로 연결합니다.</span></div>',
    '<button class="icon-button danger" type="button" data-calendar-entry-close aria-label="등록 닫기">&times;</button>',
    '</header>',
    renderInvestmentCalendarFormPanel({ compact: true }),
    '</section>',
    '</div>'
  ].join("");
}

function renderInvestmentCalendarCandidateConfirmation() {
  var confirmation = calendarState.investmentCalendarCandidateConfirmation;
  if (!confirmation) return "";
  var candidate = investmentCalendarCandidateById(confirmation.candidateId) || {};
  var busy = calendarState.investmentCalendarCandidateReviewing === confirmation.candidateId;
  return [
    '<div class="calendar-entry-backdrop" data-calendar-candidate-confirm-close>',
    '<section class="calendar-entry-modal calendar-candidate-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="calendar-candidate-confirm-title" tabindex="-1" data-calendar-candidate-confirm-dialog>',
    '<header class="calendar-entry-head">',
    '<div><p class="label">SCHEDULE CONFIRMATION</p><h2 id="calendar-candidate-confirm-title">발표 시각 확인</h2><span>' + escapeHtml(confirmation.title || "자동 감지 일정") + '</span></div>',
    '<button class="icon-button danger" type="button" data-calendar-candidate-confirm-close aria-label="시각 확인 닫기"' + (busy ? ' disabled' : '') + '>&times;</button>',
    '</header>',
    '<form class="calendar-candidate-confirm-form" data-calendar-candidate-confirm-form novalidate>',
    '<div class="calendar-candidate-confirm-context">',
    '<strong>' + escapeHtml(investmentCalendarDisplayTitle(candidate, confirmation.title || "자동 감지 일정")) + '</strong>',
    '<span>' + escapeHtml([investmentCalendarTargetLabel(candidate), investmentCalendarSymbolMetaLabel(candidate), candidate.source || confirmation.source, investmentCalendarTimeStateLabel(candidate)].filter(Boolean).join(" · ")) + '</span>',
    '</div>',
    '<div class="calendar-candidate-confirm-fields">',
    '<label class="setting-field"><span>발표 날짜</span><input type="date" aria-required="true" data-calendar-candidate-confirm-field="date" value="' + escapeHtml(confirmation.date || "") + '"' + (busy ? ' disabled' : '') + '></label>',
    '<label class="setting-field"><span>발표 시각</span><input type="time" aria-required="true" step="60" data-calendar-candidate-confirm-field="time" value="' + escapeHtml(confirmation.time || "") + '"' + (busy ? ' disabled' : '') + '></label>',
    '</div>',
    confirmation.error ? '<p class="form-error calendar-candidate-confirm-error" role="alert">' + escapeHtml(confirmation.error) + '</p>' : '',
    '<div class="form-actions calendar-candidate-confirm-actions">',
    '<button class="text-button" type="button" data-calendar-candidate-confirm-close' + (busy ? ' disabled' : '') + '>취소</button>',
    '<button class="text-button primary" type="submit"' + (busy ? ' disabled' : '') + '>' + escapeHtml(busy ? "등록 중" : "확인하고 등록") + '</button>',
    '</div>',
    '</form>',
    '</section>',
    '</div>'
  ].join("");
}

function renderInvestmentCalendarRailPanel() {
  var payload = currentInvestmentCalendar();
  var summary = payload.summary || {};
  var upcoming = investmentCalendarUpcomingEvents();
  var next = upcoming[0] || {};
  var reminderCandidates = upcoming.filter(function (event) {
    return String((event || {}).status || "").toLowerCase() === "active" && Array.isArray(event.reminderOffsetsMinutes) && event.reminderOffsetsMinutes.length;
  });
  var byType = (summary.byType || []).length ? summary.byType : [{ eventType: "custom", count: 0 }];
  var nextType = next.eventType ? investmentCalendarEventTypeLabel(next.eventType) : "캘린더 준비";
  var nextTarget = next.startsAt ? investmentCalendarTargetLabel(next) : "예정 이벤트 없음";
  return [
    '<aside class="investment-calendar-rail">',
    '<section class="panel investment-calendar-next-card"' + cardTypeAttrs("action-queue-card", next.startsAt ? "watch" : "hold") + '>',
    '<div class="panel-head"><div><p class="label">NEXT SCHEDULE</p><h2>다음 일정</h2></div></div>',
    '<div class="investment-calendar-next-body">',
    '<strong class="investment-calendar-next-time">' + escapeHtml(next.startsAt ? investmentCalendarScheduleLabel(next) : "등록 대기") + '</strong>',
    '<em>' + escapeHtml((String(next.status || "").toLowerCase() === "tentative" ? "검토 전 · " : "") + nextType + " · " + nextTarget) + '</em>',
    '<span class="investment-calendar-next-title">' + escapeHtml(next.startsAt ? investmentCalendarDisplayTitle(next, "투자 이벤트") : "예정 이벤트를 등록하면 알림 후보가 생성됩니다.") + '</span>',
    next.startsAt ? '<p>' + escapeHtml(investmentCalendarImpactText(next)) + '</p>' + renderWorkDetailButton("investment-calendar-event", next.eventId || next.id || next.title, "일정 상세", "text-button compact") : '',
    '</div>',
    renderCalendarEntryButton("이벤트 등록", "text-button primary"),
    '</section>',
    '<details class="oa-secondary-details" id="disclosure-calendar-operations"><summary><strong>일정 분포·연결 상태</strong></summary><div class="oa-secondary-content">',
    '<section class="panel investment-calendar-type-panel"' + cardTypeAttrs("diagnostic-card", "hold") + '>',
    '<div class="panel-head"><div><p class="label">EVENT MIX</p><h2>유형 분포</h2></div></div>',
    '<div class="investment-calendar-type-strip">',
    byType.slice(0, 8).map(function (item) {
      return '<span' + cardTypeAttrs("metric-cell", item.count ? "watch" : "hold") + '><strong>' + escapeHtml(investmentCalendarEventTypeLabel(item.eventType)) + '</strong><em>' + escapeHtml(item.count || 0) + '</em></span>';
    }).join(""),
    '</div>',
    '</section>',
    '<section class="panel investment-calendar-quality-panel"' + cardTypeAttrs("source-card", "hold") + '>',
    '<div class="panel-head"><div><p class="label">DATA QUALITY</p><h2>운영 연결</h2></div></div>',
    '<div class="investment-calendar-quality-list">',
    renderCalendarRailCheck("이벤트 저장소", summary.total ? "저장됨" : "대기", summary.total ? "watch" : "hold"),
    renderCalendarRailCheck("리마인더 대상", reminderCandidates.length ? reminderCandidates.length + "건 활성" : "승인 일정 없음", reminderCandidates.length ? "watch" : "hold"),
    renderCalendarRailCheck("판단 연결", "온톨로지 요청", "hold"),
    '</div>',
    '</section>',
    '</div></details>',
    '</aside>'
  ].join("");
}

function renderCalendarRailCheck(label, value, tone) {
  return [
    '<span class="investment-calendar-quality-row"' + cardTypeAttrs("health-card", tone || "hold") + '>',
    '<em>' + escapeHtml(label) + '</em>',
    '<strong>' + escapeHtml(value || "-") + '</strong>',
    '</span>'
  ].join("");
}

function investmentCalendarEntryWorkDetailPayload() {
  return {
    kicker: "Event Entry",
    title: "투자 이벤트 등록",
    meta: "실적, 거시지표, 공시, 점검 일정을 알림 후보로 연결합니다.",
    body: renderInvestmentCalendarFormPanel({ layer: true })
  };
}

function investmentCalendarEventByKey(key) {
  var target = String(key || "");
  return investmentCalendarEvents().filter(function (event) {
    return String(event.eventId || event.id || event.title || "") === target;
  })[0] || null;
}

function investmentCalendarEventWorkDetailPayload(key) {
  var event = investmentCalendarEventByKey(key);
  if (!event) return null;
  var tone = investmentCalendarTone(event);
  var payload = investmentCalendarPayload(event);
  return {
    kicker: "Calendar Event",
    title: investmentCalendarDisplayTitle(event, "투자 이벤트"),
    meta: [investmentCalendarEventTypeLabel(event.eventType), event.startsAt ? formatClock(event.startsAt) : "", investmentCalendarTargetLabel(event), investmentCalendarSymbolMetaLabel(event)].filter(Boolean).join(" · "),
    body: [
      renderInstrumentWorkspaceLink((event.symbols || [])[0], "종목 전체 흐름"),
      renderCalendarReleaseInformation(event),
      '<section class="work-detail-section primary">',
      '<strong>경제적 의미 · 일반 배경</strong>',
      '<p>' + escapeHtml(investmentCalendarImpactText(event)) + '</p>',
      renderInvestmentCalendarWatchList(event),
      '</section>',
      payload.positiveScenario || payload.negativeScenario ? [
        '<section class="work-detail-section">',
        '<strong>시나리오</strong>',
        payload.positiveScenario ? '<p><b>긍정</b> ' + escapeHtml(investmentCalendarDisplayText(event, payload.positiveScenario)) + '</p>' : '',
        payload.negativeScenario ? '<p><b>부정</b> ' + escapeHtml(investmentCalendarDisplayText(event, payload.negativeScenario)) + '</p>' : '',
        '</section>'
      ].join("") : '',
      '<section class="work-detail-section primary">',
      '<strong>이벤트 요약</strong>',
      '<p>' + escapeHtml(investmentCalendarDisplayText(event, event.notes || "등록된 메모가 없습니다. 이벤트 시점과 대상 종목을 확인한 뒤 알림 후보로 이어집니다.")) + '</p>',
      '</section>',
      '<div class="work-detail-metric-row">',
      renderNotificationDetailMetric("중요도", event.importance || 0, tone),
      renderNotificationDetailMetric("시각 상태", investmentCalendarTimeStateLabel(event), "muted"),
      renderNotificationDetailMetric("알림", investmentCalendarReminderLabel(event), "muted"),
      '</div>',
      '<section class="work-detail-section">',
      '<strong>대상</strong>',
      '<p>' + escapeHtml([investmentCalendarTargetLabel(event), investmentCalendarSymbolMetaLabel(event)].filter(Boolean).join(" · ")) + '</p>',
      '</section>'
    ].join(""),
    footer: '<button class="mini-button danger" type="button" data-calendar-delete="' + escapeHtml(event.eventId || "") + '">삭제</button>'
  };
}

export { calendarCandidateBoardWorkDetailPayload, investmentCalendarDayKey, investmentCalendarDisplayTitle, investmentCalendarEventWorkDetailPayload, investmentCalendarImpactText, investmentCalendarMonthDate, investmentCalendarPayload, investmentCalendarUpcomingEvents, renderCalendarEntryModal, renderInvestmentCalendarCandidateConfirmation, renderInvestmentCalendarPage };
