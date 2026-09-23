import { render } from "../render/scheduler.mjs";
import { requestJson } from "../requests/json.mjs";
import { sendJson } from "../requests/mutations.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { showSnackbar } from "../shell/snackbar.mjs";
import { isStaticPreviewHost } from "../shell/static-preview.mjs";
import { decisionsState } from "../state/decisions.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

function applyInvestmentLanguagePayload(payload) {
  settingsState.investmentLanguage = payload || null;
  settingsState.investmentLanguageLoaded = Boolean(payload && payload.registry);
  settingsState.investmentLanguageError = "";
  var terms = payload && payload.registry && Array.isArray(payload.registry.terms) ? payload.registry.terms : [];
  if (!decisionsState.activeInvestmentLanguageTermId && terms.length) {
    decisionsState.activeInvestmentLanguageTermId = String(terms[0].termId || "");
  }
}

function loadInvestmentLanguage(force) {
  if (isStaticPreviewHost()) return Promise.resolve(null);
  if (settingsState.investmentLanguageLoading && !force) return Promise.resolve(settingsState.investmentLanguage);
  if (settingsState.investmentLanguageLoaded && !force) return Promise.resolve(settingsState.investmentLanguage);
  settingsState.investmentLanguageLoading = true;
  settingsState.investmentLanguageError = "";
  return requestJson("/api/ontology/language")
    .then(function (payload) {
      applyInvestmentLanguagePayload(payload);
      return payload;
    })
    .catch(function (error) {
      settingsState.investmentLanguageError = error.message || "보편언어 사전을 읽지 못했습니다.";
      return null;
    })
    .finally(function () {
      settingsState.investmentLanguageLoading = false;
      if (shellState.snapshot) render();
    });
}

function investmentLanguageTerms() {
  var registry = settingsState.investmentLanguage && settingsState.investmentLanguage.registry;
  return registry && Array.isArray(registry.terms) ? registry.terms : [];
}

function decisionLanguageTerm(termId) {
  return investmentLanguageTerms().filter(function (item) {
    return String(item.termId || "") === String(termId || "");
  })[0] || null;
}

function renderDecisionInfoButton(termId, currentUse) {
  var term = decisionLanguageTerm(termId) || {};
  var label = term.preferredLabel || "정보 설명";
  var definition = term.definition || "이 항목이 현재 투자 판단에서 어떤 역할을 하는지 설명합니다.";
  return [
    '<details class="oa-decision-info">',
    '<summary aria-label="' + escapeHtml(label + " 설명") + '" title="' + escapeHtml(label + " 설명") + '"><span class="oa-help-icon" aria-hidden="true"></span></summary>',
    '<div role="note">',
    '<strong>' + escapeHtml(label) + '</strong>',
    '<p>' + escapeHtml(definition) + '</p>',
    currentUse ? '<span><b>이 화면에서는</b>' + escapeHtml(currentUse) + '</span>' : '',
    term.whyItMatters ? '<span><b>왜 중요한가</b>' + escapeHtml(term.whyItMatters) + '</span>' : '',
    term.doesNotMean ? '<span><b>주의</b>' + escapeHtml(term.doesNotMean) + '</span>' : '',
    '</div>',
    '</details>'
  ].join("");
}

function activeInvestmentLanguageTerm() {
  var activeId = String(decisionsState.activeInvestmentLanguageTermId || "");
  return investmentLanguageTerms().filter(function (item) {
    return String(item.termId || "") === activeId;
  })[0] || investmentLanguageTerms()[0] || null;
}

function commaSeparatedLanguageValues(value) {
  return String(value || "").split(/[\n,]/).map(function (item) {
    return item.trim();
  }).filter(Boolean);
}

function investmentLanguageTermFromForm(form) {
  var current = activeInvestmentLanguageTerm() || {};
  var value = function (name) {
    var field = form.querySelector('[name="' + name + '"]');
    return field ? field.value.trim() : "";
  };
  return Object.assign({}, current, {
    termId: value("termId") || current.termId,
    category: value("category"),
    preferredLabel: value("preferredLabel"),
    definition: value("definition"),
    status: value("status") || "draft",
    owner: value("owner") || "ontology",
    aliases: commaSeparatedLanguageValues(value("aliases")),
    forbiddenExpressions: commaSeparatedLanguageValues(value("forbiddenExpressions")),
    renderings: {
      absoluteBeginner: value("renderingAbsoluteBeginner"),
      beginner: value("renderingBeginner"),
      intermediate: value("renderingIntermediate"),
      advanced: value("renderingAdvanced")
    }
  });
}

function saveInvestmentLanguageTerm(form) {
  if (settingsState.investmentLanguageSaving || !form) return;
  var payload = settingsState.investmentLanguage || {};
  var registry = Object.assign({}, payload.registry || {});
  var nextTerm = investmentLanguageTermFromForm(form);
  registry.terms = investmentLanguageTerms().map(function (item) {
    return String(item.termId || "") === String(nextTerm.termId || "") ? nextTerm : item;
  });
  settingsState.investmentLanguageSaving = true;
  settingsState.investmentLanguageError = "";
  render();
  sendJson("/api/ontology/language", "PUT", { registry: registry })
    .then(function (response) {
      applyInvestmentLanguagePayload(response);
      showSnackbar("보편언어 사전과 TypeDB 관리 개념을 저장했습니다.");
    })
    .catch(function (error) {
      settingsState.investmentLanguageError = error.message || "보편언어 사전을 저장하지 못했습니다.";
      showSnackbar(settingsState.investmentLanguageError, "danger");
    })
    .finally(function () {
      settingsState.investmentLanguageSaving = false;
      render();
    });
}

function previewInvestmentLanguage() {
  if (isStaticPreviewHost()) return;
  settingsState.investmentLanguagePreview = null;
  sendJson("/api/ontology/language/preview", "POST", {
    text: settingsState.investmentLanguagePreviewText,
    level: settingsState.investmentLanguagePreviewLevel,
    registry: settingsState.investmentLanguage && settingsState.investmentLanguage.registry
  }).then(function (payload) {
    settingsState.investmentLanguagePreview = payload || {};
    render();
  }).catch(function (error) {
    settingsState.investmentLanguageError = error.message || "표현 미리보기를 만들지 못했습니다.";
    render();
  });
}

export { activeInvestmentLanguageTerm, investmentLanguageTerms, loadInvestmentLanguage, previewInvestmentLanguage, renderDecisionInfoButton, saveInvestmentLanguageTerm };
