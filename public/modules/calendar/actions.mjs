import { closeInvestmentCalendarCandidateConfirmation, deleteInvestmentCalendarEvent, loadInvestmentCalendar, loadInvestmentCalendarCandidates, saveInvestmentCalendarEvent, submitInvestmentCalendarCandidateConfirmation } from "./commands.mjs";
import { investmentCalendarDayKey } from "./workspace.mjs";
import { openWorkDetailLayer } from "../navigation/detail.mjs";
import { render } from "../render/scheduler.mjs";
import { calendarState } from "../state/calendar.mjs";

function bindCalendarControls(app) {
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-month-step]")).forEach(function (button) {
    button.addEventListener("click", function () {
      calendarState.investmentCalendarMonthOffset += Number(button.getAttribute("data-calendar-month-step") || 0);
      calendarState.investmentCalendarFocusedDayKey = "";
      loadInvestmentCalendar(true);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-month-today]")).forEach(function (button) {
    button.addEventListener("click", function () {
      calendarState.investmentCalendarMonthOffset = 0;
      calendarState.investmentCalendarFocusedDayKey = investmentCalendarDayKey(new Date());
      loadInvestmentCalendar(true);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-day-select]")).forEach(function (button) {
    button.addEventListener("click", function () {
      calendarState.investmentCalendarFocusedDayKey = button.getAttribute("data-calendar-day-select") || "";
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-candidate-confirm-close]")).forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (button.classList && button.classList.contains("calendar-entry-backdrop") && event.target !== button) return;
      closeInvestmentCalendarCandidateConfirmation();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-candidate-confirm-field]")).forEach(function (field) {
    var updateCandidateConfirmation = function () {
      var name = field.getAttribute("data-calendar-candidate-confirm-field");
      var confirmation = calendarState.investmentCalendarCandidateConfirmation;
      if (!name || !confirmation) return;
      confirmation[name] = field.value;
      confirmation.error = "";
    };
    field.addEventListener("input", updateCandidateConfirmation);
    field.addEventListener("change", updateCandidateConfirmation);
  });
var calendarCandidateConfirmForm = app.querySelector("[data-calendar-candidate-confirm-form]");
if (calendarCandidateConfirmForm) {
    calendarCandidateConfirmForm.addEventListener("submit", function (event) {
      event.preventDefault();
      submitInvestmentCalendarCandidateConfirmation();
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-entry-open]")).forEach(function (button) {
    button.addEventListener("click", function () {
      calendarState.calendarEntryModalOpen = true;
      render();
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-entry-close]")).forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (button.classList && button.classList.contains("calendar-entry-backdrop") && event.target !== button) return;
      calendarState.calendarEntryModalOpen = false;
      render();
    });
  });
var calendarForm = app.querySelector("[data-investment-calendar-form]");
if (calendarForm) {
    calendarForm.addEventListener("submit", function (event) {
      event.preventDefault();
      saveInvestmentCalendarEvent();
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-field]")).forEach(function (field) {
    var updateCalendarDraft = function () {
      var name = field.getAttribute("data-calendar-field");
      if (!name) return;
      calendarState.investmentCalendarDraft[name] = field.value;
    };
    field.addEventListener("input", updateCalendarDraft);
    field.addEventListener("change", updateCalendarDraft);
  });
var calendarFilterForm = app.querySelector("[data-investment-calendar-filter-form]");
if (calendarFilterForm) {
    calendarFilterForm.addEventListener("submit", function (event) {
      event.preventDefault();
      loadInvestmentCalendar(true);
    });
  }
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-filter]")).forEach(function (field) {
    var updateCalendarFilter = function () {
      var name = field.getAttribute("data-calendar-filter");
      if (!name) return;
      calendarState.investmentCalendarFilters[name] = field.value;
    };
    field.addEventListener("input", updateCalendarFilter);
    field.addEventListener("change", updateCalendarFilter);
  });
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-event-detail]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var key = button.getAttribute("data-calendar-event-detail") || "";
      var dayKey = button.getAttribute("data-calendar-event-day") || "";
      if (dayKey) calendarState.investmentCalendarFocusedDayKey = dayKey;
      openWorkDetailLayer("investment-calendar-event", key);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-candidate-page]")).forEach(function (button) {
    button.addEventListener("click", function () {
      var page = Number(button.getAttribute("data-calendar-candidate-page"));
      if (!Number.isFinite(page)) return;
      calendarState.investmentCalendarCandidatePage = Math.max(0, page);
      loadInvestmentCalendarCandidates(true);
    });
  });
Array.prototype.slice.call(app.querySelectorAll("[data-calendar-delete]")).forEach(function (button) {
    button.addEventListener("click", function () {
      deleteInvestmentCalendarEvent(button.getAttribute("data-calendar-delete"));
    });
  });
}

export { bindCalendarControls };
