import { editorWorkDetailPayload } from "../navigation/detail.mjs";
import { defaultSettings } from "./defaults.mjs";
import { renderRuntimeSettingsSummary, renderSettingField, renderSettingSelect, settingValue, settingsHasPendingChanges, settingsSaveButtonClass, settingsSaveButtonLabel, settingsSaveDisabledAttr, settingsStatusLabel, settingsStatusTone } from "./fields.mjs";
import { activeInvestmentLanguageTerm, investmentLanguageTerms } from "./language.mjs";
import { appTimezoneOptions } from "./preferences.mjs";
import { renderShareRuntimePanel } from "./share.mjs";
import { formatClock } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { configuredCount } from "../shell/commands.mjs";
import { cardFormatAttrs, cardTypeAttrs } from "../shell/layout.mjs";
import { renderManagedPage } from "../shell/pages.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { settingsState } from "../state/settings.mjs";
import { shellState } from "../state/shell.mjs";

function renderSettingsPage() {
  return renderManagedPage("settings", shellState.snapshot || {}, [
    '<section class="admin-grid settings-view">',
    renderSettingsOverviewPanel(),
    renderSettingsResponsibilityPanel(),
    renderSettingsDetailHub(),
    '</section>'
  ].join(""));
}

function renderSettingsDetailHub() {
  return [
    '<article class="panel settings-detail-hub">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Settings Detail</p>',
    '<h2>설정 상세 편집</h2>',
    '<span>기본 화면은 저장 상태와 책임만 확인하고, 실제 입력 폼은 필요한 섹션만 펼칩니다.</span>',
    '</div>',
    '</div>',
    '<div class="settings-detail-grid">',
    renderSettingsDetailDisclosure("기본 설정 편집", "화면 표시와 알림 전달 채널", renderSettingsEnvironmentPanel() + renderSettingsDeliverySettingsPanel()),
    renderSettingsDetailDisclosure("AI 투자판단", "AI 사용 여부, 병렬 워커, 추론 깊이", renderSettingsAiOperationsPanel()),
    renderSettingsDetailDisclosure("보편언어 관리", "승인 용어, 사용자 수준별 표현, 금지 표현", renderInvestmentLanguagePanel()),
    renderSettingsDetailDisclosure("고급 설정", "외부 API, 신선도 게이트, 추론, 매핑값", renderSettingsExternalDataPanel()),
    renderSettingsDetailDisclosure("진단", "저장 상태, 잠금, API 준비도", renderSettingsDiagnosticsPanel()),
    '</div>',
    '</article>'
  ].join("");
}

function investmentLanguageStatusLabel(status) {
  return ({ approved: "승인", draft: "검토 중", deprecated: "사용 중지" })[String(status || "")] || "검토 중";
}

function investmentLanguageCategoryLabel(category) {
  return ({
    "instrument-archetype": "종목 성격",
    "position-intent": "계좌 역할",
    "investment-concept": "투자 개념",
    "decision-concept": "판단 개념",
    "market-concept": "가격·시장",
    "risk-concept": "위험 관리"
  })[String(category || "")] || "기타 용어";
}

function investmentLanguageWorkDetailPayload() {
  var payload = settingsState.investmentLanguage || {};
  var registry = payload.registry || {};
  var terms = Array.isArray(registry.terms) ? registry.terms : [];
  return editorWorkDetailPayload(
    "Ubiquitous Language",
    "투자 보편언어 관리",
    [registry.version || "버전 확인", terms.length + "개 용어", "TypeDB LanguageGovernance"].join(" · "),
    renderInvestmentLanguagePanel()
  );
}

function renderInvestmentLanguagePanel() {
  var payload = settingsState.investmentLanguage || {};
  var registry = payload.registry || {};
  var validation = payload.validation || {};
  var active = activeInvestmentLanguageTerm();
  var query = String(settingsState.investmentLanguageSearch || "").toLowerCase();
  var terms = investmentLanguageTerms().filter(function (item) {
    var haystack = [item.termId, item.preferredLabel, item.category, item.definition].join(" ").toLowerCase();
    return !query || haystack.indexOf(query) >= 0;
  });
  if (settingsState.investmentLanguageLoading && !settingsState.investmentLanguageLoaded) {
    return '<article class="panel investment-language-panel"><div class="settings-body"><p class="lab-message">보편언어 사전을 읽는 중입니다.</p></div></article>';
  }
  if (!registry.terms) {
    return [
      '<article class="panel investment-language-panel">',
      '<div class="settings-body">',
      '<p class="form-error">' + escapeHtml(settingsState.investmentLanguageError || "보편언어 사전을 불러오지 못했습니다.") + '</p>',
      '<button class="text-button" type="button" data-action="refresh-investment-language">다시 불러오기</button>',
      '</div>',
      '</article>'
    ].join("");
  }
  var renderings = active && active.renderings ? active.renderings : {};
  var preview = settingsState.investmentLanguagePreview || {};
  var coverage = validation.coverage || {};
  return [
    '<article class="panel investment-language-panel">',
    '<div class="panel-head">',
    '<div><p class="label">Ubiquitous Language</p><h2>투자 보편언어 사전</h2><span>내부 식별자는 유지하고 사용자에게 보이는 표현만 승인·관리합니다.</span></div>',
    '<div class="settings-actions">',
    '<span class="tone-chip ' + (validation.valid ? "watch" : "danger") + '">' + escapeHtml(validation.valid ? "검증 통과" : "수정 필요") + '</span>',
    '<button class="mini-button" type="button" data-action="refresh-investment-language">새로고침</button>',
    '</div>',
    '</div>',
    '<div class="investment-language-summary">',
    '<span><strong>' + escapeHtml(String((registry.terms || []).length)) + '</strong> 전체 용어</span>',
    '<span><strong>' + escapeHtml(String((registry.terms || []).filter(function (item) { return item.status === "approved"; }).length)) + '</strong> 승인</span>',
    '<span><strong>' + escapeHtml(String(coverage.coveredCount || 0)) + '/' + escapeHtml(String(coverage.requiredCount || 0)) + '</strong> TBox 적용</span>',
    '<span><strong>' + escapeHtml(String(registry.version || "-")) + '</strong> 사전 버전</span>',
    '</div>',
    settingsState.investmentLanguageError ? '<p class="form-error investment-language-error">' + escapeHtml(settingsState.investmentLanguageError) + '</p>' : '',
    '<div class="investment-language-workspace">',
    '<aside class="investment-language-list">',
    '<label class="investment-language-search"><span>용어 찾기</span><input type="search" value="' + escapeHtml(settingsState.investmentLanguageSearch || "") + '" placeholder="표현 또는 내부 식별자" data-investment-language-search></label>',
    '<div class="investment-language-term-list" role="listbox">',
    terms.map(function (item) {
      var selected = active && String(active.termId || "") === String(item.termId || "");
      return [
        '<button type="button" class="investment-language-term-row' + (selected ? " active" : "") + '" data-language-term="' + escapeHtml(item.termId || "") + '" role="option" aria-selected="' + (selected ? "true" : "false") + '">',
        '<span><strong>' + escapeHtml(item.preferredLabel || item.termId || "-") + '</strong><small>' + escapeHtml(investmentLanguageCategoryLabel(item.category)) + '</small></span>',
        '<em class="tone-chip ' + (item.status === "approved" ? "watch" : item.status === "deprecated" ? "danger" : "caution") + '">' + escapeHtml(investmentLanguageStatusLabel(item.status)) + '</em>',
        '</button>'
      ].join("");
    }).join("") || '<p class="empty-state compact">검색 결과가 없습니다.</p>',
    '</div>',
    '</aside>',
    active ? [
      '<form class="investment-language-editor" data-investment-language-form>',
      '<div class="investment-language-editor-head"><div><strong>' + escapeHtml(active.preferredLabel || active.termId) + '</strong><span>내부 식별자 ' + escapeHtml(active.termId || "-") + '</span></div><button class="text-button primary" type="submit"' + (settingsState.investmentLanguageSaving || settingsState.serverSettingsLocked ? ' disabled' : '') + '>' + escapeHtml(settingsState.investmentLanguageSaving ? "저장 중" : "검증 후 저장") + '</button></div>',
      '<div class="investment-language-form-grid">',
      '<label><span>내부 식별자</span><input name="termId" value="' + escapeHtml(active.termId || "") + '" readonly></label>',
      '<label><span>분류</span><input name="category" value="' + escapeHtml(active.category || "") + '"></label>',
      '<label><span>대표 표현</span><input name="preferredLabel" value="' + escapeHtml(active.preferredLabel || "") + '" required></label>',
      '<label><span>상태</span><select name="status"><option value="approved"' + (active.status === "approved" ? " selected" : "") + '>승인</option><option value="draft"' + (active.status === "draft" ? " selected" : "") + '>검토 중</option><option value="deprecated"' + (active.status === "deprecated" ? " selected" : "") + '>사용 중지</option></select></label>',
      '<label class="wide"><span>뜻</span><textarea name="definition" rows="3">' + escapeHtml(active.definition || "") + '</textarea></label>',
      '<label><span>왕초보 표현</span><input name="renderingAbsoluteBeginner" value="' + escapeHtml(renderings.absoluteBeginner || active.preferredLabel || "") + '"></label>',
      '<label><span>초보 표현</span><input name="renderingBeginner" value="' + escapeHtml(renderings.beginner || active.preferredLabel || "") + '"></label>',
      '<label><span>중수 표현</span><input name="renderingIntermediate" value="' + escapeHtml(renderings.intermediate || active.preferredLabel || "") + '"></label>',
      '<label><span>고수 표현</span><input name="renderingAdvanced" value="' + escapeHtml(renderings.advanced || active.preferredLabel || "") + '"></label>',
      '<label class="wide"><span>같은 뜻의 과거 표현</span><textarea name="aliases" rows="2" placeholder="쉼표 또는 줄바꿈으로 구분">' + escapeHtml((active.aliases || []).join(", ")) + '</textarea></label>',
      '<label class="wide"><span>사용 금지 표현</span><textarea name="forbiddenExpressions" rows="2" placeholder="알림에서 승인 표현으로 바꿀 말">' + escapeHtml((active.forbiddenExpressions || []).join(", ")) + '</textarea></label>',
      '<input name="owner" type="hidden" value="' + escapeHtml(active.owner || "ontology") + '">',
      '</div>',
      '</form>'
    ].join("") : '',
    '</div>',
    '<section class="investment-language-preview">',
    '<div class="investment-language-preview-head"><div><strong>문장 미리보기</strong><span>금지 표현과 내부 식별자가 사용자 수준에 맞게 바뀌는지 확인합니다.</span></div><div><select data-investment-language-preview-level><option value="absoluteBeginner"' + (settingsState.investmentLanguagePreviewLevel === "absoluteBeginner" ? " selected" : "") + '>왕초보</option><option value="beginner"' + (settingsState.investmentLanguagePreviewLevel === "beginner" ? " selected" : "") + '>초보</option><option value="intermediate"' + (settingsState.investmentLanguagePreviewLevel === "intermediate" ? " selected" : "") + '>중수</option><option value="advanced"' + (settingsState.investmentLanguagePreviewLevel === "advanced" ? " selected" : "") + '>고수</option></select><button class="mini-button" type="button" data-action="preview-investment-language">검사</button></div></div>',
    '<textarea rows="3" data-investment-language-preview-text>' + escapeHtml(settingsState.investmentLanguagePreviewText || "") + '</textarea>',
    preview.renderedText ? '<div class="investment-language-preview-result"><span>변환 결과</span><p>' + escapeHtml(preview.renderedText) + '</p><small>' + escapeHtml(String((preview.findings || []).length)) + '개 표현을 확인했습니다.</small></div>' : '',
    '</section>',
    '</article>'
  ].join("");
}

function renderSettingsDetailDisclosure(title, description, body) {
  return [
    '<details class="settings-detail-disclosure">',
    '<summary><strong>' + escapeHtml(title) + '</strong><span>' + escapeHtml(description || "") + '</span></summary>',
    '<div class="settings-detail-disclosure-body">',
    body,
    '</div>',
    '</details>'
  ].join("");
}

function renderSettingsResponsibilityPanel() {
  var rows = [
    {
      tab: "계정·연결",
      result: "계정 상태, Toss 연결, 자산 검증, 데이터 이력",
      setting: "계정 식별값, API secret, 계좌 seq, 알림 표현",
      href: "?tab=accounts&account=identity",
      action: "계정 설정"
    },
    {
      tab: "알림 운영",
      result: "조건 변화, 후보 신호, 발송/보류 판단",
      setting: "메시지 타입별 정책, 템플릿, 채널 진단",
      href: "?tab=notifications&notification=policy",
      action: "알림 설정"
    },
    {
      tab: "투자 판단",
      result: "오늘의 판단, 투자 근거, 통합 차트, 그래프 검증",
      setting: "전략 룰, RuleBox, 프롬프트 레지스트리",
      href: "?tab=modeling&strategy=rules",
      action: "전략 룰"
    },
    {
      tab: "뉴스·근거",
      result: "기사 요약, 호재/악재 판단, 수집 품질",
      setting: "뉴스 아카이브, 공시·외부 원천, 중요도 게이트",
      href: "?tab=feed&mode=settings&feed=settings",
      action: "피드 설정"
    }
  ];
  return [
    '<article class="panel settings-responsibility-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Control Map</p>',
    '<h2>탭별 결과와 설정 책임</h2>',
    '<span>결과 화면에서는 판단을 보고, 설정 화면에서는 그 판단을 만드는 입력과 정책만 관리합니다.</span>',
    '</div>',
    '</div>',
    '<div class="settings-responsibility-grid" role="table" aria-label="탭별 결과와 설정 책임">',
    '<div class="settings-responsibility-head" role="row"><span>탭</span><span>결과에서 볼 것</span><span>설정에서 바꿀 것</span><span>이동</span></div>',
    rows.map(function (row) {
      return [
        '<div class="settings-responsibility-row" role="row"' + cardTypeAttrs("ledger-row") + '>',
        '<strong>' + escapeHtml(row.tab) + '</strong>',
        '<span>' + escapeHtml(row.result) + '</span>',
        '<em>' + escapeHtml(row.setting) + '</em>',
        '<a class="text-button" href="' + escapeHtml(row.href) + '">' + escapeHtml(row.action) + '</a>',
        '</div>'
      ].join("");
    }).join(""),
    '</div>',
    '</article>'
  ].join("");
}

function renderSettingsGroup(title, description, content, tone) {
  return [
    '<section class="settings-fieldset ' + escapeHtml(tone || "neutral") + '"' + cardTypeAttrs("config-panel", tone || "neutral") + '>',
    '<div class="settings-fieldset-head">',
    '<div>',
    '<strong>' + escapeHtml(title) + '</strong>',
    '<span>' + escapeHtml(description || "") + '</span>',
    '</div>',
    '</div>',
    '<div class="settings-grid">',
    content,
    '</div>',
    '</section>'
  ].join("");
}

function renderSettingsAiRuntimeGroup(tone) {
  return renderSettingsGroup("AI 투자판단", "TypeDB 추론 결과를 AI가 검증하고 투자 의견으로 작성하는 실행 설정입니다.", [
    renderSettingSelect("notificationAiGateEnabled", "AI 투자판단", [
      { value: "1", label: "사용" },
      { value: "0", label: "사용 안 함" }
    ]),
    renderSettingSelect("notificationAiQueueWorkerCount", "병렬 AI 워커", [
      { value: "0", label: "중지" },
      { value: "1", label: "1개" },
      { value: "2", label: "2개 (권장)" },
      { value: "3", label: "3개" },
      { value: "4", label: "4개" }
    ]),
    renderSettingSelect("notificationAiUseCodex", "AI 실행 엔진", [
      { value: "1", label: "Codex AI" },
      { value: "0", label: "로컬 검증만" }
    ]),
    renderSettingSelect("notificationAiReasoningEffort", "AI 추론 깊이", [
      { value: "auto", label: "자동 (표준 높음·복합 최대, 권장)" },
      { value: "max", label: "최대" },
      { value: "high", label: "항상 높음" },
      { value: "medium", label: "보통" },
      { value: "low", label: "낮음" }
    ]),
    renderSettingSelect("notificationAiDeliveryDeadlineSeconds", "알림 AI 제한시간", [
      { value: "0", label: "완료까지 대기 (권장)" },
      { value: "300", label: "비상 제한 300초" },
      { value: "600", label: "비상 제한 600초" }
    ]),
    renderSettingSelect("localAiInvestmentReservedProcesses", "투자 판단 전용 슬롯", [
      { value: "0", label: "예약 없음" },
      { value: "1", label: "1개 (권장)" }
    ]),
    renderSettingSelect("notificationAiTypeDbFallbackEnabled", "AI 실패 시 TypeDB 알림", [
      { value: "1", label: "웹 이력에 저장 (권장)" },
      { value: "0", label: "실패 처리" }
    ])
  ].join(""), tone || "ai");
}

function renderSettingsOverviewPanel() {
  return [
    '<article class="panel settings-overview-panel"' + cardTypeAttrs("config-panel", settingsStatusTone()) + '>',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">App Settings</p>',
    '<h2>런타임 설정</h2>',
    '</div>',
    '<div class="settings-actions">',
    '<button class="text-button" type="button" data-action="settings-back">이전</button>',
    '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
    '</div>',
    '</div>',
    '<div class="settings-body">',
    '<div class="settings-status-band">',
    '<div class="settings-status-copy">',
    '<p class="settings-section-label">Local first</p>',
    '<strong>기본 설정 먼저, 고급 설정은 필요할 때만</strong>',
    '<span>화면 표시와 알림 전달은 기본 설정에서, API·게이트·매핑은 고급 설정에서 관리합니다.</span>',
    '</div>',
    '<div class="settings-status-stack">',
    '<span class="tone-chip ' + settingsStatusTone() + '" data-settings-status>' + settingsStatusLabel() + '</span>',
    '<span class="chip">로컬 DB 우선</span>',
    '<button class="mini-button" type="button" data-settings-runtime-toggle>' + escapeHtml(settingsState.settingsRuntimeExpanded ? "연결 접기" : "연결 상세") + '</button>',
    '</div>',
    settingsState.settingsSaving ? '<p class="lab-message">설정을 MySQL 운영 DB에 저장하는 중입니다.</p>' : '',
    settingsState.serverSettingsError ? '<p class="form-error">' + escapeHtml(settingsState.serverSettingsError) + '</p>' : '',
    settingsState.serverSettingsLocked ? '<p class="form-error">공유 모드에서는 서버 설정 저장이 잠겨 있습니다.</p>' : '',
    '</div>',
    renderRuntimeSettingsSummary(),
    settingsState.settingsRuntimeExpanded ? renderSettingsRuntimeInlineDetail() : '',
    renderSettingsSmartSavePanel(),
    '</div>',
    '</article>'
  ].join("");
}

function renderSettingsRuntimeInlineDetail() {
  var accessRole = String(settingsState.shareAccess && settingsState.shareAccess.role || "local-owner");
  var accessLabel = accessRole === "owner" ? "원격 소유자" : (accessRole === "viewer" ? "조회 전용" : "로컬 소유자");
  return [
    '<div class="settings-runtime-inline inline-detail-surface">',
    '<section class="inline-detail-block primary">',
    '<strong>연결 요약</strong>',
    '<p>설정 저장 상태, 로컬 운영 DB 잠금, 외부 API 준비도를 한 번에 확인합니다.</p>',
    '</section>',
    '<div class="inline-detail-grid">',
    renderSettingsDiagnosticMini("저장 상태", settingsStatusLabel(), settingsStatusTone(), settingsHasPendingChanges() ? "변경사항 저장 필요" : "로컬 설정과 동기화됨"),
    renderSettingsDiagnosticMini("접속 권한", accessLabel, settingsState.serverSettingsLocked ? "caution" : "watch", settingsState.serverSettingsLocked ? "조회 권한으로 연결됨" : "변경 권한으로 연결됨"),
    renderSettingsDiagnosticMini("서버 잠금", settingsState.serverSettingsLocked ? "읽기전용" : "수정 가능", settingsState.serverSettingsLocked ? "caution" : "watch", settingsState.serverSettingsLocked ? "공유 모드에서는 서버 설정 저장이 잠겨 있습니다." : "로컬 운영 DB 저장 가능"),
    renderSettingsDiagnosticMini("외부 API", configuredCount(["alphaVantageApiKey", "coingeckoApiKey", "fredApiKey", "opendartApiKey"]) + "/4", "hold", "Alpha, CoinGecko, FRED, OpenDART 준비도"),
    renderSettingsDiagnosticMini("최근 오류", settingsState.serverSettingsError ? "확인 필요" : "없음", settingsState.serverSettingsError ? "danger" : "watch", settingsState.serverSettingsError || "설정 API 오류가 없습니다."),
    '</div>',
    '</div>'
  ].join("");
}

function settingsRuntimeWorkDetailPayload() {
  return {
    kicker: "Runtime Settings",
    title: "런타임 연결 상세",
    meta: settingsStatusLabel() + " · 외부 API " + configuredCount(["alphaVantageApiKey", "coingeckoApiKey", "fredApiKey", "opendartApiKey"]) + "/4",
    body: [
      '<section class="work-detail-section">',
      '<strong>현재 저장 상태</strong>',
      renderSettingsSmartSavePanel(),
      '</section>',
      '<section class="work-detail-section">',
      '<strong>연결 요약</strong>',
      renderRuntimeSettingsSummary(),
      '</section>',
      '<section class="work-detail-section">',
      '<strong>진단</strong>',
      '<div class="work-detail-grid">',
      renderSettingsDiagnosticMini("저장 상태", settingsStatusLabel(), settingsStatusTone(), settingsHasPendingChanges() ? "변경사항 저장 필요" : "로컬 설정과 동기화됨"),
      renderSettingsDiagnosticMini("서버 잠금", settingsState.serverSettingsLocked ? "읽기전용" : "수정 가능", settingsState.serverSettingsLocked ? "caution" : "watch", settingsState.serverSettingsLocked ? "공유 모드에서는 서버 설정 저장이 잠겨 있습니다." : "로컬 운영 DB 저장 가능"),
      renderSettingsDiagnosticMini("외부 API", configuredCount(["alphaVantageApiKey", "coingeckoApiKey", "fredApiKey", "opendartApiKey"]) + "/4", "hold", "Alpha, CoinGecko, FRED, OpenDART 준비도"),
      renderSettingsDiagnosticMini("최근 오류", settingsState.serverSettingsError ? "확인 필요" : "없음", settingsState.serverSettingsError ? "danger" : "watch", settingsState.serverSettingsError || "설정 API 오류가 없습니다."),
      '</div>',
      '</section>'
    ].join("")
  };
}

function renderSettingsScopeEditorIntro(scope, title, description) {
  return [
    '<section class="settings-editor-scope">',
    '<span class="settings-scope-chip">' + escapeHtml(scope) + '</span>',
    '<div><strong>' + escapeHtml(title) + '</strong><p>' + escapeHtml(description) + '</p></div>',
    '</section>'
  ].join("");
}

function settingsPreferencesWorkDetailPayload() {
  return editorWorkDetailPayload(
    "App Preferences",
    "화면과 시간 표시",
    "앱 환경 · 테마, 시간대, 캘린더 기본 시각",
    renderSettingsScopeEditorIntro("앱 환경", "표시 설정", "투자 계정이나 수집 워커를 바꾸지 않고 화면에 보이는 형식만 조정합니다.")
      + renderSettingsEnvironmentPanel()
      + renderSettingsSmartSavePanel()
  );
}

function settingsUserNotificationsWorkDetailPayload() {
  return editorWorkDetailPayload(
    "User Delivery",
    "투자 알림 수신",
    "사용자 채널 · 투자 인사이트와 뉴스 전달",
    renderSettingsScopeEditorIntro("사용자 채널", "투자 알림 전달", "증권 계정 인증이나 운영 장애 알림과 분리된 사용자 투자 알림 채널입니다.")
      + '<article class="panel settings-delivery-panel"><div class="settings-body">'
      + renderSettingsUserDeliveryGroup()
      + renderSettingsSmartSavePanel()
      + '</div></article>'
  );
}

function settingsOperationsNotificationsWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Operations Delivery",
    "운영자 알림 채널",
    "관리자 전용 · 장애와 파이프라인 상태 전달",
    renderSettingsScopeEditorIntro("관리자 전용", "운영 알림 전달", "투자 의견이 아니라 연결 장애, 수집 실패, 추론 상태와 작업 완료만 전달합니다.")
      + '<article class="panel settings-delivery-panel"><div class="settings-body">'
      + renderSettingsOperationsDeliveryGroup()
      + renderSettingsSmartSavePanel()
      + '</div></article>'
  );
}

function settingsAiRuntimeWorkDetailPayload() {
  return editorWorkDetailPayload(
    "AI Runtime",
    "AI 추론 실행 설정",
    "시스템 전체 · AI 사용 여부와 워커 실행량",
    renderSettingsScopeEditorIntro("시스템 전체", "AI 추론 런타임", "TypeDB 추론 결과를 검증하는 실행 엔진과 처리량에 적용됩니다.")
      + renderSettingsAiOperationsPanel()
  );
}

function settingsDataSourcesWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Data Operations",
    "외부 데이터와 API",
    "시스템 전체 · API 키, 수집, 신선도와 매핑",
    renderSettingsScopeEditorIntro("시스템 전체", "데이터 수집 정책", "이 값은 모든 투자 계정의 수집 워커와 데이터 품질 판정에 적용됩니다.")
      + renderSettingsExternalDataPanel()
      + renderSettingsSmartSavePanel()
  );
}

function settingsDiagnosticsWorkDetailPayload() {
  return editorWorkDetailPayload(
    "Operations Diagnostics",
    "저장소와 실행 진단",
    "관리자 전용 · 설정 DB, API와 추론 대기열",
    renderSettingsScopeEditorIntro("관리자 전용", "시스템 진단", "조회와 점검만 수행하며 투자 계정 값은 이 화면에서 수정하지 않습니다.")
      + renderSettingsDiagnosticsPanel()
  );
}

function renderSettingsDiagnosticMini(label, value, tone, detail) {
  return [
    '<section class="work-detail-card ' + escapeHtml(tone || "hold") + '"' + cardFormatAttrs("summary-list-card", "compact") + '>',
    '<span class="tone-chip ' + escapeHtml(tone || "hold") + '">' + escapeHtml(value || "-") + '</span>',
    '<strong>' + escapeHtml(label || "-") + '</strong>',
    '<p>' + escapeHtml(detail || "") + '</p>',
    '</section>'
  ].join("");
}

function renderSettingsEnvironmentPanel() {
  return [
    '<article class="panel settings-environment-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Display</p>',
    '<h2>기본 설정: 화면</h2>',
    '</div>',
    '</div>',
    '<div class="settings-body">',
    renderSettingsGroup("표시 환경", "콘솔 테마와 종목 카탈로그 신선도 기준입니다.", [
      renderSettingSelect("appTheme", "화면 테마", [
      { value: "light", label: "라이트" },
      { value: "dark", label: "다크" },
      { value: "system", label: "시스템 설정" }
      ]),
      renderSettingSelect("appTimezone", "표시 시간대", appTimezoneOptions()),
      renderSettingField("investmentCalendarCandidateDefaultTime", "캘린더 후보 기본 시각", "time", "09:00"),
      renderSettingField("symbolUniverseMaxAgeHours", "전체 종목 신선도(시간)", "number", "24")
    ].join(""), "display"),
    '</div>',
    '</article>'
  ].join("");
}

function renderSettingsDeliverySettingsPanel() {
  return [
    '<article class="panel settings-delivery-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Delivery</p>',
    '<h2>기본 설정: 알림 전달</h2>',
    '</div>',
    '</div>',
    '<div class="settings-body">',
    renderSettingsUserDeliveryGroup(),
    renderSettingsOperationsDeliveryGroup(),
    '</div>',
    '</article>'
  ].join("");
}

function renderSettingsUserDeliveryGroup() {
  var secretType = settingsState.showSecrets ? "text" : "password";
  return renderSettingsGroup("투자 알림 채널", "투자 인사이트와 뉴스를 앱 사용자에게 전달할 채널입니다.", [
    renderSettingField("notifyProvider", "알림 제공자", "text", "telegram"),
    renderSettingField("telegramBotToken", "Telegram Bot Token", secretType, "bot token", { preserveConfigured: true }),
    renderSettingField("telegramChatId", "Telegram Chat ID", "text", "chat id", { preserveConfigured: true }),
    renderSettingField("notifyLinkUrl", "알림 링크 URL", "url", "http://127.0.0.1:3000?tab=notifications")
  ].join(""), "delivery");
}

function renderSettingsOperationsDeliveryGroup() {
  var secretType = settingsState.showSecrets ? "text" : "password";
  return renderSettingsGroup("운영 알림 채널", "연결 장애, 데이터 파이프라인, 추론 상태, 작업 완료 알림만 별도 봇으로 보냅니다.", [
    renderSettingSelect("operatorReasoningReportEnabled", "운영자 추론 보고서", [
      { value: "0", label: "끄기" },
      { value: "1", label: "사용" }
    ]),
    renderSettingField("operationsTelegramBotToken", "운영 알림 Bot Token", secretType, "operations bot token", { preserveConfigured: true }),
    renderSettingField("operationsTelegramChatId", "운영 알림 Chat ID", "text", "기존 Chat ID 사용 가능", { preserveConfigured: true })
  ].join(""), "operations-delivery");
}

function renderSettingsAiOperationsPanel() {
  return [
    '<article class="panel settings-ai-operations-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">AI Runtime</p>',
    '<h2>AI 투자판단 운영</h2>',
    '<span>AI 판단을 켜고 실제 요청을 처리할 병렬 워커 수를 함께 관리합니다.</span>',
    '</div>',
    '</div>',
    '<div class="settings-body">',
    renderSettingsAiRuntimeGroup("ai"),
    renderSettingsSmartSavePanel(),
    '</div>',
    '</article>'
  ].join("");
}

function renderSettingsExternalDataPanel() {
  var secretType = settingsState.showSecrets ? "text" : "password";
  return [
    '<article class="panel settings-external-data-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Advanced</p>',
    '<h2>고급 설정</h2>',
    '<span>외부 API, 신선도 게이트, 그래프 추론, 매핑값은 필요할 때만 펼쳐서 수정합니다.</span>',
    '</div>',
    '</div>',
    '<div class="settings-body">',
    '<details class="settings-advanced-disclosure">',
    '<summary><strong>외부 데이터·추론·매핑 고급 설정 열기</strong><span>API 키, 캐시, 게이트, 공시 AI, 긴 매핑값</span></summary>',
    '<div class="settings-advanced-content">',
    renderSettingsGroup("국내 시세·수급", "KIS WebSocket 체결·호가와 REST 투자자 수급 수집 정책입니다.", [
      renderSettingField("kisEnv", "KIS 환경", "text", "prod"),
      renderSettingField("kisBaseUrl", "KIS Base URL", "url", "https://openapi.koreainvestment.com:9443"),
      renderSettingField("kisWebSocketUrl", "KIS WebSocket URL", "url", ""),
      renderSettingField("kisAppKey", "KIS App Key", secretType, "app key", { preserveConfigured: true }),
      renderSettingField("kisAppSecret", "KIS App Secret", secretType, "app secret", { preserveConfigured: true }),
      renderSettingSelect("kisRealtimeWebSocketEnabled", "KIS 체결·호가 WebSocket", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("kisRealtimeWebSocketSymbols", "WebSocket 고정 종목", "text", ""),
      renderSettingField("kisRealtimeWebSocketMaxSymbols", "WebSocket 종목 수", "number", "20"),
      renderSettingField("kisRealtimeWebSocketCollectSeconds", "WebSocket 연결 유지(초)", "number", "30"),
      renderSettingField("kisRealtimeWebSocketEventIntervalSeconds", "WebSocket 추론 묶음(초)", "number", "15"),
      renderSettingField("kisRealtimeWebSocketReconnectSeconds", "WebSocket 재연결 대기(초)", "number", "5"),
      renderSettingField("kisRealtimeWebSocketTimeoutSeconds", "WebSocket 타임아웃(초)", "number", "10"),
      renderSettingSelect("kisMarketSignalsEnabled", "KIS 수급 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("kisMarketSignalMaxSymbols", "KIS 수급 종목 수", "number", "20"),
      renderSettingField("kisMarketSignalCacheMinutes", "KIS 수급 캐시(분)", "number", "3"),
      renderSettingField("kisMarketSignalGapSeconds", "KIS 호출 간격(초)", "number", "0.35"),
      renderSettingSelect("kisMarketSignalPreferLiveDuringMarketHours", "장중 KIS 최신 시세 우선", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("kisMarketSignalLiveRefreshSeconds", "장중 시세 최소 조회 간격(초)", "number", "60"),
      renderSettingSelect("kisInvestorIntradayEstimateEnabled", "장중 외국인·기관 추정 수급", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("kisMarketSignalUnchangedStaleCount", "레거시 동일 수급값 판정 횟수", "number", "3")
    ].join(""), "market"),
    renderSettingsGroup("해외·거시 원천", "미장, 코인, 금리 데이터를 판단 근거로 넣기 위한 API 연결입니다.", [
      renderSettingSelect("externalAlphaEnabled", "Alpha Vantage 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("alphaVantageApiKey", "Alpha Vantage API Key", secretType, "api key", { preserveConfigured: true }),
      renderSettingField("externalAlphaMaxSymbols", "미장 조회 종목 수", "number", "3"),
      renderSettingSelect("externalAlphaRelatedSymbolsEnabled", "ADR·ETF 관련 종목 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalAlphaRelatedMaxSymbols", "ADR·ETF 관련 종목 수", "number", "8"),
      renderSettingSelect("externalYFinanceEnabled", "yfinance 종합 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalYFinanceMaxSymbols", "yfinance 조회 종목 수", "number", "8"),
      renderSettingField("externalYFinanceHistoryPeriod", "yfinance 가격 기간", "text", "1y"),
      renderSettingField("externalYFinanceHistoryInterval", "yfinance 가격 간격", "text", "1d"),
      renderSettingField("externalYFinanceHistoryRows", "yfinance 가격 행 수", "number", "90"),
      renderSettingField("externalYFinanceFinancialPeriods", "재무제표 기간 수", "number", "4"),
      renderSettingField("externalYFinanceTabularRows", "표 데이터 행 수", "number", "40"),
      renderSettingField("externalYFinanceOptionExpirations", "옵션 만기 수", "number", "2"),
      renderSettingField("externalYFinanceOptionsMaxRows", "옵션 행 수", "number", "40"),
      renderSettingField("externalYFinanceEarningsLimit", "실적 일정 수", "number", "16"),
      renderSettingField("externalYFinanceNewsLimit", "뉴스 수", "number", "10"),
      renderSettingField("externalYFinancePriceMaxAgeMinutes", "가격 신선도(분)", "number", "30"),
      renderSettingField("externalYFinanceOptionsMaxAgeMinutes", "옵션 신선도(분)", "number", "30"),
      renderSettingField("externalYFinanceNewsMaxAgeMinutes", "뉴스 신선도(분)", "number", "1440"),
      renderSettingField("externalYFinanceAnalystMaxAgeMinutes", "애널리스트 신선도(분)", "number", "10080"),
      renderSettingField("externalYFinanceFundamentalMaxAgeMinutes", "재무 신선도(분)", "number", "129600"),
      renderSettingSelect("externalCoinGeckoEnabled", "CoinGecko 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("coingeckoApiKey", "CoinGecko API Key", secretType, "api key", { preserveConfigured: true }),
      renderSettingSelect("externalFredEnabled", "FRED 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("fredApiKey", "FRED API Key", secretType, "api key", { preserveConfigured: true }),
      renderSettingField("externalFredSeries", "FRED 지표", "text", "DGS10,DGS2,DFF"),
      renderSettingField("externalCryptoIds", "CoinGecko 코인 ID", "text", "bitcoin,ethereum")
    ].join(""), "external"),
    renderSettingsGroup("AI 밸류에이션 제안", "사용자 적정가나 외부 적정가가 없을 때 임시 적정가를 제안합니다. 알림에는 항상 AI 제안과 사용자 승인 전 상태가 표시됩니다.", [
      renderSettingSelect("aiValuationAutoProposalEnabled", "AI 제안값 사용", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("aiValuationCurrentPriceAnchorEnabled", "현재가 임시 기준", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("aiValuationPreferredParValue", "우선주 액면 기준가", "number", "100"),
      renderSettingField("aiValuationPreferredRiskSpreadPct", "우선주 위험 가산율(%)", "number", "비우면 자동"),
      renderSettingField("aiValuationPreferredRequiredYieldPct", "우선주 요구수익률(%)", "number", "비우면 금리+위험"),
      renderSettingField("aiValuationPreferredMinimumMarginPct", "우선주 요구 안전마진(%)", "number", "8"),
      renderSettingField("aiValuationBaselineMinimumMarginPct", "일반주식 요구 안전마진(%)", "number", "15"),
      renderSettingField("valuationReviewOverrides", "사용자 검토 상태", "text", "예: MSTR,user_approved,BTC NAV 초안 승인")
    ].join(""), "valuation"),
    renderSettingsGroup("뉴스·공시 수집", "뉴스, OpenDART, SEC 원천과 리서치 근거 저장량을 조정합니다.", [
      renderSettingSelect("externalDartEnabled", "OpenDART 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("opendartApiKey", "OpenDART API Key", secretType, "api key", { preserveConfigured: true }),
      renderSettingField("externalDartLookbackDays", "공시 조회 기간(일)", "number", "14"),
      renderSettingSelect("externalSecEnabled", "SEC EDGAR 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalSecMaxSymbols", "SEC 조회 종목 수", "number", "3"),
      renderSettingField("externalSecContactEmail", "SEC 연락처 이메일", "text", "SEC 정책 준수용 이메일", { preserveConfigured: true }),
      renderSettingField("externalSecUserAgent", "SEC User-Agent", "text", "DigitalTwin/1.0 local-contact"),
      renderSettingSelect("externalSecDocumentTextEnabled", "SEC 원문 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("externalSecDocumentTextMaxChars", "SEC 원문 최대 글자", "number", "6000"),
      renderSettingField("externalSecDocumentMaxPerSymbol", "종목별 SEC 원문 수", "number", "3"),
      renderSettingSelect("externalNewsEnabled", "뉴스 헤드라인 수집", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("externalNewsProvider", "뉴스 공급자", [
        { value: "auto", label: "자동" },
        { value: "gdelt", label: "GDELT" },
        { value: "alpha_vantage", label: "Alpha Vantage" }
      ]),
      renderSettingField("externalNewsMaxSymbols", "뉴스 조회 종목 수", "number", "3"),
      renderSettingField("externalNewsLookbackHours", "뉴스 조회 기간(시간)", "number", "48"),
      renderSettingField("externalResearchEvidenceMaxItems", "AI 전달 최신 근거 수", "number", "8"),
      renderSettingField("newsCollectionIntervalSeconds", "뉴스 수집 주기(초)", "number", "60"),
      renderSettingField("newsCollectionMaxSymbols", "뉴스 수집 종목 수", "number", "40"),
      renderSettingField("newsCollectionLookbackMinutes", "뉴스 조회 기간(분)", "number", "180"),
      renderSettingSelect("newsCollectionQualityGateEnabled", "저장 전 뉴스 품질 검증", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("newsCollectionMinimumRelevanceState", "저장할 최소 관련성", [
        { value: "direct", label: "종목 직접 기사만" },
        { value: "related", label: "관련 기사부터" },
        { value: "context", label: "관련 맥락부터" }
      ]),
      renderSettingSelect("newsCollectionMinimumMaterialityState", "저장할 최소 중요성", [
        { value: "material", label: "중요 사건만" },
        { value: "notable", label: "확인할 정보부터" }
      ]),
      renderSettingSelect("newsCollectionMinimumSourceTrustState", "저장할 최소 출처", [
        { value: "standard", label: "일반 언론 이상" },
        { value: "trusted", label: "신뢰 출처만" }
      ]),
      renderSettingSelect("newsCollectionRequireArticleBody", "저장 시 원문 본문", [
        { value: "1", label: "본문 검증 필수" },
        { value: "0", label: "제목·요약도 허용" }
      ]),
      renderSettingSelect("newsDigestMinimumMaterialityState", "알림 뉴스 최소 중요성", [
        { value: "notable", label: "확인할 정보부터" },
        { value: "material", label: "중요 정보부터" },
        { value: "critical", label: "즉시 확인 정보만" }
      ]),
      renderSettingSelect("newsDigestMinimumSourceTrustState", "최소 출처 신뢰", [
        { value: "standard", label: "일반 출처부터" },
        { value: "trusted", label: "신뢰 출처부터" },
        { value: "primary", label: "공식 원문만" }
      ])
    ].join(""), "research"),
    renderSettingsGroup("신선도·추론 게이트", "알림과 온톨로지 추론에 들어가기 전 데이터 유효성을 제한합니다.", [
      renderSettingField("externalApiFetchIntervalMinutes", "외부 API 캐시(분)", "number", "30"),
      renderSettingField("externalSignalCacheMaxAgeMinutes", "외부 신호 캐시 TTL(분)", "number", "10"),
      renderSettingSelect("dataFreshnessEnabled", "알림 데이터 신선도 게이트", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("dataFreshnessDefaultMaxAgeMinutes", "알림 기본 신선도(분)", "number", "10"),
      renderSettingField("dataFreshnessQuoteMaxAgeMinutes", "시세 알림 신선도(분)", "number", "10"),
      renderSettingField("dataFreshnessKisPriceMaxAgeMinutes", "KIS 현재가 신선도(분)", "number", "3"),
      renderSettingField("dataFreshnessKisMicrostructureMaxAgeMinutes", "KIS 체결·호가 신선도(분)", "number", "2"),
      renderSettingField("dataFreshnessKisInvestorMaxAgeMinutes", "KIS 투자자 수급 신선도(분)", "number", "5"),
      renderSettingField("dataFreshnessExternalMaxAgeMinutes", "외부 신호 신선도(분)", "number", "10"),
      renderSettingField("dataFreshnessExternalEquityMaxAgeMinutes", "미장 신호 신선도(분)", "number", "10"),
      renderSettingField("dataFreshnessExternalCryptoMaxAgeMinutes", "크립토 신호 신선도(분)", "number", "10"),
      renderSettingField("dataFreshnessMacroMaxAgeMinutes", "거시 신호 신선도(분)", "number", "120"),
      renderSettingField("dataFreshnessDisclosureMaxAgeMinutes", "공시 신선도(분)", "number", "120"),
      renderSettingField("marketDataMaxAgeMinutes", "추천 시세 신선도(분)", "number", "240"),
      renderSettingField("ontologyReasoningIntervalSeconds", "추론 요청 확인 주기(초)", "number", "10"),
      renderSettingField("ontologyReasoningBatchSize", "추론 요청 배치", "number", "20"),
      renderSettingField("ontologyProjectionAuditStaleAfterSeconds", "중단 투영 감사 정리 기준(초, 0=자동)", "number", "0"),
      renderSettingSelect("ontologyReasoningMailboxEnabled", "실시간 최신 상태만 유지", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("ontologyReasoningSourceFreshnessEnabled", "추론 입력 원천 시각 검증", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("ontologyReasoningRealtimeEventMaxAgeMinutes", "실시간 입력 최대 경과(분)", "number", "15"),
      renderSettingField("ontologyReasoningResearchEventMaxAgeMinutes", "리서치 입력 최대 경과(분)", "number", "360"),
      renderSettingSelect("ontologyReasoningQueueAlertEnabled", "추론 대기 지연 운영 알림", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingField("ontologyReasoningQueueWarningAgeMinutes", "대기 지연 경고 시간(분)", "number", "30"),
      renderSettingField("ontologyReasoningQueueCriticalAgeMinutes", "대기 심각 시간(분)", "number", "90"),
      renderSettingField("ontologyReasoningQueueWarningPendingCount", "대기 요청 경고 수", "number", "100"),
      renderSettingField("ontologyReasoningQueueCriticalPendingCount", "대기 요청 심각 수", "number", "200"),
      renderSettingField("ontologyReasoningQueueConsecutiveObservations", "지연 연속 확인 횟수", "number", "3"),
      renderSettingField("ontologyReasoningQueueNoProgressMinutes", "처리 진행 신호 정체 시간(분)", "number", "15"),
      renderSettingField("ontologyReasoningQueueAlertReminderMinutes", "지연 운영 알림 재전송(분)", "number", "60"),
      renderSettingField("marketMaterialityPriceChangePct", "가격 중요 변화율(%)", "number", "0.6"),
      renderSettingField("marketMaterialityTrendDistancePct", "추세 중요 이격(%)", "number", "2"),
      renderSettingField("marketMaterialityVolumeRatio", "거래량 중요 배율", "number", "1.5"),
      renderSettingField("marketMaterialityInvestorFlowRatioPct", "외국인·기관 수급 중요 비중(%)", "number", "15")
    ].join(""), "gate"),
    renderSettingsGroup("공시 AI와 매핑", "AI 해석 방식과 종목·CIK·환율 매핑처럼 긴 설정값을 관리합니다.", [
      renderSettingSelect("dartDisclosureAiAnalysisEnabled", "공시 AI 해석", [
        { value: "1", label: "사용" },
        { value: "0", label: "사용 안 함" }
      ]),
      renderSettingSelect("dartDisclosureAiUseCodex", "공시 해석 엔진", [
        { value: "1", label: "Codex AI" },
        { value: "0", label: "로컬 규칙" }
      ]),
      renderSettingField("dartDisclosureAiTimeoutSeconds", "공시 AI 타임아웃(초)", "number", "90"),
      renderSettingField("dartDisclosureAiCommand", "공시 AI 명령", "text", "비우면 Codex 사용"),
      '<label class="setting-field wide">',
      '<span class="setting-field-label">OpenDART 종목 매핑</span>',
      '<div class="form-control-shell"><textarea data-setting="externalDartCorpCodes" rows="3" autocomplete="off" placeholder="005930=00126380">' + escapeHtml(settingValue("externalDartCorpCodes") || defaultSettings.externalDartCorpCodes) + '</textarea></div>',
      '</label>',
      '<label class="setting-field wide">',
      '<span class="setting-field-label">SEC CIK 매핑</span>',
      '<div class="form-control-shell"><textarea data-setting="externalSecCompanyCiks" rows="3" autocomplete="off" placeholder="AAPL=0000320193">' + escapeHtml(settingValue("externalSecCompanyCiks") || defaultSettings.externalSecCompanyCiks) + '</textarea></div>',
      '</label>',
      '<label class="setting-field wide">',
      '<span class="setting-field-label">환율 설정</span>',
      '<div class="form-control-shell"><textarea data-setting="fxRates" rows="2" autocomplete="off" placeholder="USD=1400">' + escapeHtml(settingValue("fxRates") || defaultSettings.fxRates) + '</textarea></div>',
      '</label>',
      renderSettingSelect("portfolioValuationBasis", "계좌 총 평가 기준", [
        { value: "broker-net", label: "토스 비용 반영" },
        { value: "broker-gross", label: "토스 비용 전" },
        { value: "mark-to-market", label: "최신 분석 시가" }
      ]),
      '<label class="setting-field wide">',
      '<span class="setting-field-label">종목별 투자 타입 프로필</span>',
      '<span class="setting-field-help">형식: 심볼|설명|타입들|의도|민감도|정책. 예: MSTR|비트코인 프록시 성장주|BitcoinProxy,HighVolatilityGrowth|trading|btc:high,fx:high|allowAddOnStrength=1,trimOnTrendBreak=1</span>',
      '<div class="form-control-shell"><textarea data-setting="instrumentProfiles" rows="8" autocomplete="off" placeholder="MSTR|비트코인 프록시 성장주|BitcoinProxy,HighVolatilityGrowth|trading|btc:high|allowAddOnStrength=1">' + escapeHtml(settingValue("instrumentProfiles") || defaultSettings.instrumentProfiles) + '</textarea></div>',
      '</label>'
    ].join(""), "mapping"),
    '</div>',
    '</details>',
    '</div>',
    '</article>'
  ].join("");
}

function renderSettingsDiagnosticsPanel() {
  var reasoning = ontologyState.ontologyReasoningStatus || {};
  var queue = reasoning.queueHealth && typeof reasoning.queueHealth === "object" ? reasoning.queueHealth : {};
  var queueDelay = reasoning.queueDelayHealth && typeof reasoning.queueDelayHealth === "object" ? reasoning.queueDelayHealth : {};
  var queueDispatch = reasoning.queueDispatch && typeof reasoning.queueDispatch === "object" ? reasoning.queueDispatch : {};
  var mailbox = reasoning.mailbox && typeof reasoning.mailbox === "object" ? reasoning.mailbox : {};
  var marketCompletion = reasoning.marketObservationReasoningCompletion && typeof reasoning.marketObservationReasoningCompletion === "object" ? reasoning.marketObservationReasoningCompletion : {};
  var reasoningStatus = String(queue.status || (ontologyState.ontologyReasoningStatusError ? "error" : "unknown")).toLowerCase();
  var reasoningTone = reasoningStatus === "healthy" ? "watch" : (reasoningStatus === "degraded" || reasoningStatus === "unknown" ? "caution" : "danger");
  var reasoningLabel = reasoningStatus === "healthy" ? "정상" : (reasoningStatus === "degraded" ? "대기 처리 중" : (reasoningStatus === "blocked" ? "차단됨" : "확인 필요"));
  var mailboxCount = Number(mailbox.pendingEntryCount || reasoning.mailboxPendingEntryCount || 0);
  var queueDelayState = String(queueDelay.state || "").toLowerCase();
  var queueDelayCandidate = String(queueDelay.candidateState || "").toLowerCase();
  var queueDelayOldest = String(queueDelay.oldestRequestAt || queueDispatch.oldestRequestAt || "");
  var queueDelayAge = Number(queueDelay.oldestRequestAgeMinutes || 0);
  var queueDelayProgressAt = String(queueDelay.lastProgressAt || "");
  var queueDelayProgressAge = Number(queueDelay.progressAgeMinutes || 0);
  if (!queueDelayAge && queueDelayOldest) {
    var queueDelayOldestMs = new Date(queueDelayOldest).getTime();
    if (!Number.isNaN(queueDelayOldestMs)) queueDelayAge = Math.max(0, Math.floor((Date.now() - queueDelayOldestMs) / 60000));
  }
  var queueDelayRequests = Number(queueDelay.rawPendingCount || reasoning.rawPendingCount || 0);
  var queueDelaySymbols = Number(queueDelay.pendingSymbolCount || (Array.isArray(reasoning.pendingSymbols) ? reasoning.pendingSymbols.length : 0));
  var queueDelayOverdue = Number(queueDelay.overduePendingSymbolCount || reasoning.overduePendingSymbolCount || 0);
  var queueDelayLabel = queueDelayState === "critical" ? "심각 지연" : (queueDelayState === "delayed" ? "지연" : (queueDelayState === "draining" ? "처리 진행" : (queueDelayCandidate === "critical" || queueDelayCandidate === "delayed" ? "확인 중" : reasoningLabel)));
  var queueDelayTone = queueDelayState === "critical" ? "danger" : (queueDelayState === "delayed" ? "caution" : (queueDelayState === "draining" ? "watch" : (queueDelayCandidate === "critical" || queueDelayCandidate === "delayed" ? "caution" : reasoningTone)));
  var queueDelayDetail = queueDelayOldest
    ? "최장 " + queueDelayAge + "분 · 요청 " + queueDelayRequests + "건 · 종목 " + queueDelaySymbols + "개 · 한도 초과 " + queueDelayOverdue + "개"
    : (mailboxCount + "개 최신 상태 대기 · 실시간 원천 " + (reasoning.sourceFreshness && reasoning.sourceFreshness.realtimeEventMaxAgeMinutes || "-") + "분 이내만 사용");
  if (queueDelayProgressAt) queueDelayDetail += " · 최근 진행 " + queueDelayProgressAge + "분 전";
  var marketCompletionStatus = String(marketCompletion.status || "unknown").toLowerCase();
  var marketCompletionPending = Number(marketCompletion.pendingAnchorCount || 0);
  var marketCompletionTone = marketCompletionStatus === "healthy" ? "watch" : (marketCompletionStatus === "pending" ? "caution" : "danger");
  var marketCompletionLabel = marketCompletionStatus === "healthy" ? "승인 정상" : (marketCompletionStatus === "pending" ? "승인 대기 " + marketCompletionPending + "건" : "확인 필요");
  var marketCompletionDetail = "완료 영수증 " + Number(marketCompletion.receiptCount || 0) + "건";
  if (marketCompletion.latestCompletedAt) marketCompletionDetail += " · 최근 " + formatClock(marketCompletion.latestCompletedAt);
  if (marketCompletion.missingTboxIdentityCount) marketCompletionDetail += " · TBox 식별 누락 " + Number(marketCompletion.missingTboxIdentityCount) + "건";
  var diagnostics = [
    {
      label: "저장 상태",
      value: settingsStatusLabel(),
      tone: settingsStatusTone(),
      detail: settingsHasPendingChanges() ? "변경사항 저장 필요" : "로컬 설정과 동기화됨"
    },
    {
      label: "서버 잠금",
      value: settingsState.serverSettingsLocked ? "읽기전용" : "수정 가능",
      tone: settingsState.serverSettingsLocked ? "caution" : "watch",
      detail: settingsState.serverSettingsLocked ? "공유 모드에서는 서버 설정 저장이 잠겨 있습니다." : "로컬 운영 DB 저장 가능"
    },
    {
      label: "외부 API",
      value: configuredCount(["alphaVantageApiKey", "coingeckoApiKey", "fredApiKey", "opendartApiKey"]) + "/4",
      tone: configuredCount(["alphaVantageApiKey", "coingeckoApiKey", "fredApiKey", "opendartApiKey"]) ? "watch" : "hold",
      detail: "Alpha, CoinGecko, FRED, OpenDART 준비도"
    },
    {
      label: "최근 오류",
      value: settingsState.serverSettingsError ? "확인 필요" : "없음",
      tone: settingsState.serverSettingsError ? "danger" : "watch",
      detail: settingsState.serverSettingsError || "설정 API 오류가 없습니다."
    },
    {
      label: "추론 대기열",
      value: ontologyState.ontologyReasoningStatusLoading ? "조회 중" : queueDelayLabel,
      tone: ontologyState.ontologyReasoningStatusLoading ? "caution" : queueDelayTone,
      detail: ontologyState.ontologyReasoningStatusError
        || queueDelayDetail
    },
    {
      label: "시세 추론 승인",
      value: ontologyState.ontologyReasoningStatusLoading ? "조회 중" : marketCompletionLabel,
      tone: ontologyState.ontologyReasoningStatusLoading ? "caution" : marketCompletionTone,
      detail: marketCompletion.reason || marketCompletionDetail
    }
  ];
  return [
    '<article class="panel settings-diagnostics-panel">',
    '<div class="panel-head">',
    '<div>',
    '<p class="label">Diagnostics</p>',
    '<h2>진단</h2>',
    '<span>저장 가능 여부, 외부 API 준비도, 설정 오류를 따로 확인합니다.</span>',
    '</div>',
    '<button class="text-button compact" type="button" data-action="refresh-ontology-reasoning-status"' + (ontologyState.ontologyReasoningStatusLoading ? ' disabled' : '') + '>추론 상태 새로고침</button>',
    '</div>',
    '<div class="settings-diagnostic-grid">',
    diagnostics.map(function (item) {
      return [
        '<section class="settings-diagnostic-card ' + escapeHtml(item.tone || "hold") + '"' + cardTypeAttrs("diagnostic-card", item.tone || "hold") + cardFormatAttrs("summary-list-card", "compact") + '>',
        '<span class="tone-chip ' + escapeHtml(item.tone || "hold") + '">' + escapeHtml(item.value) + '</span>',
        '<strong>' + escapeHtml(item.label) + '</strong>',
        '<p>' + escapeHtml(item.detail) + '</p>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    renderShareRuntimePanel(),
    '</article>'
  ].join("");
}

function renderSettingsSmartSavePanel() {
  return [
    '<div class="settings-smart-save">',
    '<div class="settings-smart-save-copy">',
    '<strong data-settings-save-title>' + escapeHtml(settingsHasPendingChanges() ? "변경사항 저장 필요" : "변경사항 저장됨") + '</strong>',
    '<span data-settings-save-description>' + escapeHtml(settingsHasPendingChanges() ? "현재 화면의 앱 표시, 알림 전달, 외부 API 설정을 로컬 저장소에 반영합니다." : "입력값이 로컬 저장소와 동기화되어 있습니다.") + '</span>',
    '</div>',
    '<div class="settings-actions settings-page-actions">',
    '<button class="' + settingsSaveButtonClass() + '" type="button" data-action="save-settings"' + settingsSaveDisabledAttr() + '>' + settingsSaveButtonLabel() + '</button>',
    '<button class="text-button" type="button" data-action="toggle-secrets">' + (settingsState.showSecrets ? "secret 숨기기" : "secret 보기") + '</button>',
    '</div>',
    '</div>',
  ].join("");
}

export { investmentLanguageWorkDetailPayload, renderSettingsAiRuntimeGroup, renderSettingsGroup, renderSettingsSmartSavePanel, settingsAiRuntimeWorkDetailPayload, settingsDataSourcesWorkDetailPayload, settingsDiagnosticsWorkDetailPayload, settingsOperationsNotificationsWorkDetailPayload, settingsPreferencesWorkDetailPayload, settingsRuntimeWorkDetailPayload, settingsUserNotificationsWorkDetailPayload };
