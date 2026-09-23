import { INVESTMENT_CALENDAR_CANDIDATE_PAGE_SIZE } from "./constants.mjs";
import { investmentCalendarDisplayTitle, investmentCalendarMonthDate, investmentCalendarPayload } from "./workspace.mjs";
import { mergeUniqueItems, mobileInfiniteScrollEnabled } from "../navigation/infinite-list.mjs";
import { loadNotificationJobs, loadNotificationSchedules } from "../notifications/requests.mjs";
import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { appDateTimeParts, currentAppTimezone } from "../settings/preferences.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { calendarState } from "../state/calendar.mjs";
import { settingsState } from "../state/settings.mjs";

function localDateTimeInput(date) {
  var value = date instanceof Date ? date : new Date();
  var parts = appDateTimeParts(value);
  if (!parts) return "";
  return [
    parts.year,
    "-",
    parts.month,
    "-",
    parts.day,
    "T",
    parts.hour,
    ":",
    parts.minute
  ].join("");
}

function defaultInvestmentCalendarDraft() {
  var start = new Date(Date.now() + 24 * 60 * 60 * 1000);
  start.setMinutes(0, 0, 0);
  return {
    eventId: "",
    title: "",
    eventType: "earnings",
    startsAt: localDateTimeInput(start),
    timezone: currentAppTimezone(),
    importance: "70",
    symbolsText: "",
    marketsText: "",
    notes: "",
    reminderOffsetsText: "1440,60,0"
  };
}

function csvTokens(value) {
  return String(value || "").split(",").map(function (item) {
    return item.trim();
  }).filter(Boolean);
}

function currentInvestmentCalendar() {
  return calendarState.investmentCalendar || {
    events: [],
    summary: { total: 0, upcoming: 0, nextStartsAt: "", byType: [] },
    eventTypes: staticInvestmentCalendarEventTypes(),
    preview: false
  };
}

function loadInvestmentCalendarDetail(eventId) {
  var key = String(eventId || "");
  var cache = calendarState.investmentCalendarDetails || (calendarState.investmentCalendarDetails = {});
  if (!key || isStaticPreviewHost() || (cache[key] && Date.now() - cache[key].loadedAt < 60000)) return Promise.resolve();
  return requestJson("/api/investment-calendar/events/" + encodeURIComponent(key), { key: "calendar-detail:" + key, cacheTtlMs: 60000 })
    .then(function (payload) { if (payload && payload.event) cache[key] = { event: payload.event, loadedAt: Date.now() }; })
    .catch(function () { return null; })
    .finally(function () { render(); });
}

export { loadInvestmentCalendarDetail };

function currentInvestmentCalendarCandidates() {
  return calendarState.investmentCalendarCandidates || {
    candidates: [],
    summary: {},
    feedback: {},
    status: "pending",
    limit: 100
  };
}

function staticInvestmentCalendarEventTypes() {
  return [
    { type: "earnings", label: "실적발표" },
    { type: "dividend", label: "배당/권리" },
    { type: "macro", label: "거시지표" },
    { type: "centralBank", label: "중앙은행" },
    { type: "disclosure", label: "공시" },
    { type: "shareholderMeeting", label: "주주총회" },
    { type: "lockup", label: "락업해제" },
    { type: "listing", label: "상장/이전상장" },
    { type: "adrListing", label: "ADR/GDR 상장" },
    { type: "indexInclusion", label: "지수 편입" },
    { type: "capitalMarketEvent", label: "자본시장 이벤트" },
    { type: "spinoff", label: "분할/스핀오프" },
    { type: "capitalRaise", label: "증자/자금조달" },
    { type: "portfolioReview", label: "포트폴리오 점검" },
    { type: "custom", label: "사용자 이벤트" }
  ];
}

function investmentCalendarEventTypes() {
  var payload = currentInvestmentCalendar();
  return Array.isArray(payload.eventTypes) && payload.eventTypes.length ? payload.eventTypes : staticInvestmentCalendarEventTypes();
}

function investmentCalendarQueryString() {
  var filters = calendarState.investmentCalendarFilters || {};
  var params = new URLSearchParams();
  var windowRange = investmentCalendarQueryWindow();
  params.set("from", windowRange.from);
  params.set("to", windowRange.to);
  if (String(filters.symbol || "").trim()) params.set("symbol", String(filters.symbol || "").trim().toUpperCase());
  if (String(filters.eventType || "").trim()) params.set("eventType", String(filters.eventType || "").trim());
  if (String(filters.limit || "").trim()) params.set("limit", String(filters.limit || "").trim());
  var text = params.toString();
  return text ? "?" + text : "";
}

function investmentCalendarQueryWindow() {
  var monthDate = investmentCalendarMonthDate();
  var year = Number.isFinite(monthDate.getFullYear()) ? monthDate.getFullYear() : new Date().getFullYear();
  return {
    from: year + "-01-01T00:00:00Z",
    to: year + "-12-31T23:59:59Z"
  };
}

function staticInvestmentCalendarPayload(reason) {
  var start = new Date(Date.now() + 24 * 60 * 60 * 1000);
  start.setMinutes(0, 0, 0);
  var stamped = new Date().toISOString();
  var startsAt = start.toISOString();
  return {
    generatedAt: stamped,
    events: [{
      eventId: "preview-calendar-earnings",
      title: "삼성전자 실적 발표 점검",
      eventType: "earnings",
      eventTypeLabel: "실적발표",
      startsAt: startsAt,
      endsAt: "",
      timezone: "Asia/Seoul",
      allDay: false,
      status: "active",
      importance: 75,
      materialityLevel: "high",
      symbols: ["005930"],
      markets: ["KOSPI"],
      accountIds: [],
      source: "Static Preview",
      sourceUrl: "",
      notes: reason || "정적 미리보기용 투자 캘린더 이벤트입니다.",
      reminderOffsetsMinutes: [1440, 60, 0],
      payload: {},
      createdAt: stamped,
      updatedAt: stamped
    }],
    summary: { total: 1, upcoming: 1, nextStartsAt: startsAt, byType: [{ eventType: "earnings", count: 1 }] },
    eventTypes: staticInvestmentCalendarEventTypes(),
    preview: true
  };
}

function staticInvestmentCalendarCandidatesPayload() {
  return {
    candidates: [],
    summary: { pending: 0 },
    feedback: {},
    status: "pending",
    limit: 100,
    page: 0,
    pageSize: INVESTMENT_CALENDAR_CANDIDATE_PAGE_SIZE,
    total: 0,
    pageInfo: {
      page: 0,
      pageSize: INVESTMENT_CALENDAR_CANDIDATE_PAGE_SIZE,
      offset: 0,
      total: 0,
      pageCount: 1,
      hasPrev: false,
      hasNext: false
    },
    preview: true
  };
}

function investmentCalendarCandidateQueryString() {
  var pageSize = INVESTMENT_CALENDAR_CANDIDATE_PAGE_SIZE;
  var page = Number(calendarState.investmentCalendarCandidatePage || 0);
  if (!Number.isFinite(page)) page = 0;
  page = Math.max(0, page);
  var params = new URLSearchParams();
  params.set("status", "pending");
  params.set("page", String(page));
  params.set("pageSize", String(pageSize));
  params.set("limit", String(pageSize));
  return "?" + params.toString();
}

function loadInvestmentCalendar(force) {
  if (calendarState.investmentCalendarLoading) return Promise.resolve();
  if (calendarState.investmentCalendar && !force) return Promise.resolve(calendarState.investmentCalendar);
  calendarState.investmentCalendarLoading = true;
  calendarState.investmentCalendarError = "";
  render();
  var promise = isStaticPreviewHost()
    ? Promise.resolve(staticInvestmentCalendarPayload("정적 미리보기"))
    : requestJson("/api/investment-calendar/events" + investmentCalendarQueryString(), {
        key: "investment-calendar-events",
        timeoutMs: 8000,
        force: Boolean(force)
      });
  return promise
    .then(function (payload) {
      calendarState.investmentCalendar = payload;
      calendarState.investmentCalendarError = "";
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "투자 캘린더를 불러오지 못했습니다.";
    })
    .finally(function () {
      calendarState.investmentCalendarLoading = false;
      render();
    });
}

function loadInvestmentCalendarCandidates(force) {
  if (calendarState.investmentCalendarCandidatesLoading) return Promise.resolve();
  if (calendarState.investmentCalendarCandidates && !force) return Promise.resolve(calendarState.investmentCalendarCandidates);
  var requestedCandidatePage = Math.max(0, Number(calendarState.investmentCalendarCandidatePage || 0));
  var incrementalLoad = mobileInfiniteScrollEnabled()
    && requestedCandidatePage > 0
    && Boolean(currentInvestmentCalendarCandidates().candidates.length);
  calendarState.investmentCalendarCandidatesLoading = true;
  calendarState.investmentCalendarError = "";
  if (!incrementalLoad) render();
  var promise = isStaticPreviewHost()
    ? Promise.resolve(staticInvestmentCalendarCandidatesPayload())
    : requestJson("/api/investment-calendar/candidates" + investmentCalendarCandidateQueryString(), {
        key: "investment-calendar-candidates",
        timeoutMs: 8000,
        force: Boolean(force)
      });
  return promise
    .then(function (payload) {
      var requestedPage = requestedCandidatePage;
      if (mobileInfiniteScrollEnabled() && requestedPage > 0) {
        var previous = currentInvestmentCalendarCandidates();
        payload = Object.assign({}, payload, {
          candidates: mergeUniqueItems(previous.candidates, payload.candidates, function (candidate, index) {
            return String((candidate || {}).candidateId || (candidate || {}).id || [(candidate || {}).title, (candidate || {}).startsAt, index].join(":"));
          })
        });
      }
      calendarState.investmentCalendarCandidates = payload;
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "투자 캘린더 후보를 불러오지 못했습니다.";
    })
    .finally(function () {
      calendarState.investmentCalendarCandidatesLoading = false;
      render();
    });
}

function recommendInvestmentCalendarCandidates() {
  if (calendarState.investmentCalendarResearching) return Promise.resolve();
  var filters = calendarState.investmentCalendarFilters || {};
  var payload = {
    symbol: String(filters.symbol || "").trim().toUpperCase(),
    limit: 120,
    runCollection: true
  };
  calendarState.investmentCalendarResearching = true;
  calendarState.investmentCalendarError = "";
  render();
  var promise = isStaticPreviewHost()
    ? Promise.resolve({
        status: "preview",
        evidenceCount: 0,
        candidateCount: 0,
        storedCandidateCount: 0,
        collection: { status: "preview" }
      })
    : sendJson("/api/investment-calendar/candidates/research", "POST", payload);
  return promise
    .then(function (result) {
      calendarState.investmentCalendarResearchResult = result || {};
      calendarState.investmentCalendarCandidatePage = 0;
      showSnackbar("AI 일정 추천 완료 · 후보 " + Number((result || {}).storedCandidateCount || 0) + "건", "success");
      return loadInvestmentCalendarCandidates(true);
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "AI 일정 후보를 추천하지 못했습니다.";
      showSnackbar(calendarState.investmentCalendarError, "danger");
    })
    .finally(function () {
      calendarState.investmentCalendarResearching = false;
      render();
    });
}

function saveInvestmentCalendarEvent() {
  if (calendarState.investmentCalendarSaving) return Promise.resolve();
  var draft = calendarState.investmentCalendarDraft || defaultInvestmentCalendarDraft();
  var payload = {
    eventId: draft.eventId || undefined,
    title: draft.title,
    eventType: draft.eventType,
    startsAt: draft.startsAt,
    timezone: currentAppTimezone(),
    importance: draft.importance,
    symbols: csvTokens(draft.symbolsText),
    markets: csvTokens(draft.marketsText),
    notes: draft.notes,
    reminderOffsetsMinutes: csvTokens(draft.reminderOffsetsText)
  };
  calendarState.investmentCalendarSaving = true;
  calendarState.investmentCalendarError = "";
  render();
  return sendJson("/api/investment-calendar/events", "POST", payload)
    .then(function () {
      calendarState.investmentCalendarDraft = defaultInvestmentCalendarDraft();
      calendarState.calendarEntryModalOpen = false;
      showSnackbar("투자 캘린더 이벤트를 저장했습니다.");
      return loadInvestmentCalendar(true);
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "투자 캘린더 이벤트를 저장하지 못했습니다.";
      showSnackbar(calendarState.investmentCalendarError, "danger");
    })
    .finally(function () {
      calendarState.investmentCalendarSaving = false;
      render();
    });
}

function deleteInvestmentCalendarEvent(eventId) {
  var id = String(eventId || "").trim();
  if (!id || calendarState.investmentCalendarDeleting) return Promise.resolve();
  if (window.confirm && !window.confirm("선택한 투자 이벤트를 삭제할까요?")) return Promise.resolve();
  calendarState.investmentCalendarDeleting = id;
  calendarState.investmentCalendarError = "";
  render();
  return sendJson("/api/investment-calendar/events/" + encodeURIComponent(id), "DELETE", {})
    .then(function () {
      showSnackbar("투자 캘린더 이벤트를 삭제했습니다.");
      return loadInvestmentCalendar(true);
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "투자 캘린더 이벤트를 삭제하지 못했습니다.";
      showSnackbar(calendarState.investmentCalendarError, "danger");
    })
    .finally(function () {
      calendarState.investmentCalendarDeleting = "";
      render();
    });
}

function runInvestmentCalendarReminders() {
  if (calendarState.investmentCalendarRunning) return Promise.resolve();
  calendarState.investmentCalendarRunning = true;
  calendarState.investmentCalendarError = "";
  render();
  return sendJson("/api/investment-calendar/reminders/run", "POST", {})
    .then(function (payload) {
      showSnackbar("캘린더 리마인더 확인 완료 · 큐 " + Number(payload.queuedCount || 0) + "건", "success");
      return Promise.all([loadInvestmentCalendar(true), loadNotificationJobs(), loadNotificationSchedules()]);
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "캘린더 리마인더를 실행하지 못했습니다.";
      showSnackbar(calendarState.investmentCalendarError, "danger");
    })
    .finally(function () {
      calendarState.investmentCalendarRunning = false;
      render();
    });
}

function syncOfficialInvestmentCalendar() {
  if (calendarState.investmentCalendarSyncing) return Promise.resolve();
  calendarState.investmentCalendarSyncing = true;
  calendarState.investmentCalendarError = "";
  render();
  return sendJson("/api/investment-calendar/sync-official", "POST", {})
    .then(function (payload) {
      showSnackbar("공식 일정 동기화 완료 · 저장 " + Number(payload.savedCount || 0) + "건", "success");
      return loadInvestmentCalendar(true);
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "공식 일정을 동기화하지 못했습니다.";
      showSnackbar(calendarState.investmentCalendarError, "danger");
    })
    .finally(function () {
      calendarState.investmentCalendarSyncing = false;
      render();
    });
}

function discoverInvestmentCalendarEvents() {
  if (calendarState.investmentCalendarDiscovering) return Promise.resolve();
  var filters = calendarState.investmentCalendarFilters || {};
  var payload = {
    symbol: String(filters.symbol || "").trim().toUpperCase(),
    limit: 12,
    force: true
  };
  calendarState.investmentCalendarDiscovering = true;
  calendarState.investmentCalendarError = "";
  render();
  var promise = isStaticPreviewHost()
    ? Promise.resolve({
        status: "preview",
        targetCount: 0,
        evidenceCount: 0,
        tentativeCount: 0,
        reviewCandidateCount: 0,
        sources: []
      })
    : sendJson("/api/investment-calendar/discovery", "POST", payload);
  return promise
    .then(function (result) {
      calendarState.investmentCalendarDiscoveryResult = result || {};
      calendarState.investmentCalendarCandidatePage = 0;
      showSnackbar("일정 탐색 완료 · 확인 후보 " + Number((result || {}).reviewCandidateCount || 0) + "건", "success");
      return Promise.all([loadInvestmentCalendar(true), loadInvestmentCalendarCandidates(true)]);
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "투자 일정 탐색을 실행하지 못했습니다.";
      showSnackbar(calendarState.investmentCalendarError, "danger");
    })
    .finally(function () {
      calendarState.investmentCalendarDiscovering = false;
      render();
    });
}

function investmentCalendarCandidateById(candidateId) {
  var id = String(candidateId || "").trim();
  return (currentInvestmentCalendarCandidates().candidates || []).filter(function (item) {
    return String((item || {}).candidateId || "") === id;
  })[0] || null;
}

function removeInvestmentCalendarCandidateFromLocalList(candidateId) {
  var id = String(candidateId || "").trim();
  var payload = calendarState.investmentCalendarCandidates;
  if (!id || !payload || !Array.isArray(payload.candidates)) return;
  var candidates = payload.candidates.filter(function (candidate) {
    return String((candidate || {}).candidateId || "") !== id;
  });
  var removed = payload.candidates.length - candidates.length;
  if (!removed) return;
  var summary = Object.assign({}, payload.summary || {});
  var pending = Number(summary.pending);
  if (Number.isFinite(pending)) summary.pending = Math.max(0, pending - removed);
  var storedPending = Number(summary.storedPending);
  if (Number.isFinite(storedPending)) summary.storedPending = Math.max(0, storedPending - removed);
  var pageInfo = Object.assign({}, payload.pageInfo || {});
  var total = Number(pageInfo.total);
  if (Number.isFinite(total)) {
    pageInfo.total = Math.max(0, total - removed);
    pageInfo.pageCount = Math.max(1, Math.ceil(pageInfo.total / Math.max(1, Number(pageInfo.pageSize || INVESTMENT_CALENDAR_CANDIDATE_PAGE_SIZE))));
    pageInfo.hasNext = Number(pageInfo.page || 0) + 1 < pageInfo.pageCount;
  }
  calendarState.investmentCalendarCandidates = Object.assign({}, payload, {
    candidates: candidates,
    summary: summary,
    total: Number.isFinite(Number(payload.total)) ? Math.max(0, Number(payload.total) - removed) : payload.total,
    pageInfo: pageInfo
  });
}

function openInvestmentCalendarCandidateConfirmation(candidateId) {
  var candidate = investmentCalendarCandidateById(candidateId);
  if (!candidate) {
    showSnackbar("등록할 캘린더 후보를 찾지 못했습니다.", "danger");
    return;
  }
  var payload = investmentCalendarPayload(candidate);
  var startsAt = String(candidate.startsAt || "");
  var scheduleParts = appDateTimeParts(startsAt);
  calendarState.investmentCalendarCandidateConfirmation = {
    candidateId: String(candidate.candidateId || ""),
    title: investmentCalendarDisplayTitle(candidate, "자동 감지 일정"),
    date: scheduleParts ? [scheduleParts.year, scheduleParts.month, scheduleParts.day].join("-") : String(candidate.localDate || startsAt.slice(0, 10) || ""),
    time: scheduleParts ? [scheduleParts.hour, scheduleParts.minute].join(":") : String(payload.eventLocalTime || ""),
    timezone: currentAppTimezone(),
    source: String(candidate.source || ""),
    error: ""
  };
  calendarState.investmentCalendarError = "";
  render();
}

function closeInvestmentCalendarCandidateConfirmation() {
  var confirmation = calendarState.investmentCalendarCandidateConfirmation || {};
  if (calendarState.investmentCalendarCandidateReviewing === confirmation.candidateId) return;
  calendarState.investmentCalendarCandidateConfirmation = null;
  render();
}

function submitInvestmentCalendarCandidateConfirmation() {
  var confirmation = calendarState.investmentCalendarCandidateConfirmation || {};
  var date = String(confirmation.date || "").trim();
  var time = String(confirmation.time || "").trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !/^\d{2}:\d{2}$/.test(time)) {
    confirmation.error = "발표 날짜와 시각을 모두 선택해 주세요.";
    calendarState.investmentCalendarCandidateConfirmation = confirmation;
    render();
    return Promise.resolve();
  }
  var startsAt = date + "T" + time;
  if (!Number.isFinite(Date.parse(startsAt))) {
    confirmation.error = "유효한 발표 날짜와 시각을 선택해 주세요.";
    calendarState.investmentCalendarCandidateConfirmation = confirmation;
    render();
    return Promise.resolve();
  }
  confirmation.error = "";
  calendarState.investmentCalendarCandidateConfirmation = confirmation;
  return approveInvestmentCalendarCandidate(confirmation.candidateId, startsAt);
}

function approveInvestmentCalendarCandidate(candidateId, confirmedStartsAt) {
  var id = String(candidateId || "").trim();
  if (settingsState.serverSettingsLocked) {
    showSnackbar("조회 전용 링크에서는 캘린더 후보를 변경할 수 없습니다.", "caution");
    return Promise.resolve();
  }
  if (!id || calendarState.investmentCalendarCandidateReviewing) return Promise.resolve();
  var candidate = investmentCalendarCandidateById(id) || {};
  var startsAt = candidate.startsAt || "";
  var payload = investmentCalendarPayload(candidate);
  var automaticCandidate = Boolean(payload.autoDetected);
  var needsScheduleConfirmation = automaticCandidate && (payload.reviewRequired || payload.scheduleState !== "confirmed");
  var explicitStartsAt = String(confirmedStartsAt || "").trim();
  if (needsScheduleConfirmation || !startsAt) {
    if (!explicitStartsAt) {
      openInvestmentCalendarCandidateConfirmation(id);
      return Promise.resolve();
    }
    startsAt = explicitStartsAt;
  }
  if (!startsAt) return Promise.resolve();
  var confirmationActive = Boolean(
    calendarState.investmentCalendarCandidateConfirmation
    && calendarState.investmentCalendarCandidateConfirmation.candidateId === id
  );
  calendarState.investmentCalendarCandidateReviewing = id;
  calendarState.investmentCalendarError = "";
  render();
  return sendJson("/api/investment-calendar/candidates/" + encodeURIComponent(id) + "/approve", "POST", {
    startsAt: startsAt,
    timezone: currentAppTimezone(),
    reviewNote: confirmationActive ? "UI 날짜·시각 확인" : "UI 일정 확인"
  })
    .then(function (result) {
      calendarState.investmentCalendarCandidateConfirmation = null;
      if (String((((result || {}).candidate || {}).status) || "").toLowerCase() !== "pending") {
        removeInvestmentCalendarCandidateFromLocalList(id);
      }
      showSnackbar("캘린더 후보를 이벤트로 등록했습니다.", "success");
      return Promise.all([loadInvestmentCalendar(true), loadInvestmentCalendarCandidates(true)]);
    })
    .catch(function (error) {
      var message = error.message || "캘린더 후보를 승인하지 못했습니다.";
      if (confirmationActive && calendarState.investmentCalendarCandidateConfirmation) {
        calendarState.investmentCalendarCandidateConfirmation.error = message;
      } else {
        calendarState.investmentCalendarError = message;
        showSnackbar(message, "danger");
      }
    })
    .finally(function () {
      calendarState.investmentCalendarCandidateReviewing = "";
      render();
    });
}

function rejectInvestmentCalendarCandidate(candidateId) {
  var id = String(candidateId || "").trim();
  if (settingsState.serverSettingsLocked) {
    showSnackbar("조회 전용 링크에서는 캘린더 후보를 변경할 수 없습니다.", "caution");
    return Promise.resolve();
  }
  if (!id || calendarState.investmentCalendarCandidateReviewing) return Promise.resolve();
  if (window.confirm && !window.confirm("선택한 캘린더 후보를 거절할까요?")) return Promise.resolve();
  calendarState.investmentCalendarCandidateReviewing = id;
  calendarState.investmentCalendarError = "";
  render();
  return sendJson("/api/investment-calendar/candidates/" + encodeURIComponent(id) + "/reject", "POST", {
    reviewNote: "UI 거절"
  })
    .then(function (result) {
      if (String((((result || {}).candidate || {}).status) || "").toLowerCase() !== "pending") {
        removeInvestmentCalendarCandidateFromLocalList(id);
      }
      showSnackbar("캘린더 후보를 거절했습니다.", "success");
      return Promise.all([loadInvestmentCalendar(true), loadInvestmentCalendarCandidates(true)]);
    })
    .catch(function (error) {
      calendarState.investmentCalendarError = error.message || "캘린더 후보를 거절하지 못했습니다.";
      showSnackbar(calendarState.investmentCalendarError, "danger");
    })
    .finally(function () {
      calendarState.investmentCalendarCandidateReviewing = "";
      render();
    });
}

export { approveInvestmentCalendarCandidate, closeInvestmentCalendarCandidateConfirmation, currentInvestmentCalendar, currentInvestmentCalendarCandidates, defaultInvestmentCalendarDraft, deleteInvestmentCalendarEvent, discoverInvestmentCalendarEvents, investmentCalendarCandidateById, investmentCalendarEventTypes, loadInvestmentCalendar, loadInvestmentCalendarCandidates, recommendInvestmentCalendarCandidates, rejectInvestmentCalendarCandidate, runInvestmentCalendarReminders, saveInvestmentCalendarEvent, submitInvestmentCalendarCandidateConfirmation, syncOfficialInvestmentCalendar };
