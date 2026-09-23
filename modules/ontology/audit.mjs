import { renderWorkDetailButton } from "../navigation/detail.mjs";
import { ontologyAuditClientSection, staticOntologyAuditPayload } from "./requests.mjs";
import { formatClock, formatInteger, latestChangedFirst, renderRecordChangedAt } from "../shared/format.mjs";
import { escapeHtml } from "../shared/text.mjs";
import { cardTypeAttrs } from "../shell/layout.mjs";
import { ontologyState } from "../state/ontology.mjs";
import { shellState } from "../state/shell.mjs";

function ontologyAuditPayload() {
  return ontologyState.ontologyAudit && typeof ontologyState.ontologyAudit === "object"
    ? ontologyState.ontologyAudit
    : staticOntologyAuditPayload(shellState.snapshot || {});
}

function ontologyAuditSectionOrder() {
  return [
    { id: "tbox", label: "TBox", description: "스키마" },
    { id: "abox", label: "ABox", description: "실체 데이터" },
    { id: "rulebox", label: "RuleBox", description: "규칙" },
    { id: "inferencebox", label: "InferenceBox", description: "추론 결과" },
    { id: "evidence", label: "근거 연결", description: "Evidence·Belief·Opinion" },
    { id: "sync", label: "동기화", description: "TypeDB 상태" }
  ];
}

function ontologyAuditSection(sectionId) {
  var loaded = ontologyState.ontologyAuditSections && ontologyState.ontologyAuditSections[sectionId];
  if (loaded && typeof loaded === "object") {
    return Object.assign({}, loaded, { rows: latestChangedFirst(Array.isArray(loaded.rows) ? loaded.rows : []) });
  }
  var payload = ontologyAuditPayload();
  var sections = payload.sections || {};
  var section = sections[sectionId] || ontologyAuditClientSection(sectionId, sectionId, "", []);
  return Object.assign({}, section, { rows: latestChangedFirst(Array.isArray(section.rows) ? section.rows : []) });
}

function ontologyAuditSectionLabel(sectionId) {
  var found = ontologyAuditSectionOrder().filter(function (item) { return item.id === sectionId; })[0];
  return found ? found.label : sectionId;
}

function ontologyAuditTone(value) {
  var text = String(value || "").toLowerCase();
  if (text === "ok" || text === "materialized" || text === "active") return "live";
  if (text === "disabled" || text === "preview" || text === "fallback") return "hold";
  if (text === "error" || text === "failed") return "danger";
  return "watch";
}

function ontologyAuditRowTitle(row) {
  row = row || {};
  return row.label || row.id || row.relationType || row.kind || "ontology row";
}

function ontologyAuditRowMeta(row) {
  row = row || {};
  var parts = [];
  if (row.box) parts.push(row.box);
  if (row.rowType) parts.push(row.rowType);
  if (row.kind && row.kind !== row.rowType) parts.push(row.kind);
  if (row.relationType) parts.push(row.relationType);
  if (row.symbol) parts.push(row.symbol);
  if (row.ruleId) parts.push(row.ruleId);
  return parts.join(" · ");
}

function ontologyAuditRowPath(row) {
  row = row || {};
  if (row.source || row.target) return [row.source || row.id || "-", row.target || "-"].join(" → ");
  if (row.status) return "상태 " + row.status;
  if (row.updatedAt) return "업데이트 " + formatClock(row.updatedAt);
  return row.id || row.key || "";
}

function renderSystemOntologyAuditPanel(snapshot) {
  var audit = ontologyAuditPayload();
  var summary = audit.summary || {};
  var filters = ontologyState.ontologyAuditFilters || {};
  var sampled = audit.summaryMode === "sampled";
  var sampledValue = "확인 전";
  var cards = [
    ["TBox", sampled ? sampledValue : ((audit.sections && audit.sections.tbox && audit.sections.tbox.total) || 0), "스키마 행"],
    ["ABox", sampled ? sampledValue : ((audit.sections && audit.sections.abox && audit.sections.abox.total) || 0), "실체 행"],
    ["RuleBox", sampled ? sampledValue : (summary.ruleCount || ((audit.sections && audit.sections.rulebox && audit.sections.rulebox.total) || 0)), "규칙"],
    ["Inference", sampled ? sampledValue : (summary.inferenceRelationCount || ((audit.sections && audit.sections.inferencebox && audit.sections.inferencebox.total) || 0)), "추론 관계"],
    ["Trace", sampled ? sampledValue : ((audit.sections && audit.sections.evidence && audit.sections.evidence.total) || 0), "근거 연결"],
    ["Sync", summary.diagnosticsStatus || audit.status || "-", audit.storeLabel || audit.graphStore || "TypeDB"]
  ];
  return [
    '<article class="panel ontology-audit-panel">',
    '<div class="panel-head ontology-audit-head">',
    '<div>',
    '<p class="label">ONTOLOGY AUDIT</p>',
    '<h2>온톨로지 감사 콘솔</h2>',
    '<span>운영 요약과 분리해서 TypeDB 원장, 규칙, 추론 세대, 근거 trace를 확인합니다.</span>',
    '</div>',
    '<span class="status-pill ' + escapeHtml(ontologyAuditTone(audit.status)) + '">' + escapeHtml(ontologyState.ontologyAuditLoading ? "조회 중" : (audit.status || "preview")) + '</span>',
    '</div>',
    '<form class="ontology-audit-toolbar" data-ontology-audit-form>',
    '<label><span>검색</span><input data-ontology-audit-filter="query" type="search" value="' + escapeHtml(filters.query || "") + '" placeholder="rule, relation, symbol, evidence"></label>',
    '<label><span>종목</span><input data-ontology-audit-filter="symbol" type="text" value="' + escapeHtml(filters.symbol || "") + '" placeholder="000660"></label>',
    '<label><span>행 수</span><select data-ontology-audit-filter="limit">',
    [40, 80, 120, 200].map(function (value) {
      return '<option value="' + value + '"' + (String(filters.limit || "80") === String(value) ? " selected" : "") + '>' + value + '</option>';
    }).join(""),
    '</select></label>',
    '<label><span>시작 행</span><input data-ontology-audit-filter="offset" type="number" min="0" step="1" value="' + escapeHtml(filters.offset || "0") + '"></label>',
    '<button class="text-button" type="submit"' + (ontologyState.ontologyAuditLoading ? ' disabled' : '') + '>' + escapeHtml(ontologyState.ontologyAuditLoading ? "조회 중" : "감사 조회") + '</button>',
    '<button class="text-button primary" type="button" data-action="refresh-ontology-audit"' + (ontologyState.ontologyAuditLoading ? ' disabled' : '') + '>새로고침</button>',
    '</form>',
    ontologyState.ontologyAuditError ? '<p class="form-error">' + escapeHtml(ontologyState.ontologyAuditError) + '</p>' : '',
    audit.error ? '<p class="data-refresh-status danger">TypeDB row 조회 오류: ' + escapeHtml(audit.error) + '</p>' : '',
    '<div class="ontology-audit-summary-grid">',
    cards.map(function (card) {
      return [
        '<section class="ontology-audit-metric"' + cardTypeAttrs("metric-card") + '>',
        '<em>' + escapeHtml(card[0]) + '</em>',
        '<strong>' + escapeHtml(typeof card[1] === "number" ? formatInteger(card[1]) : card[1]) + '</strong>',
        '<span>' + escapeHtml(card[2]) + '</span>',
        '</section>'
      ].join("");
    }).join(""),
    '</div>',
    sampled ? '<p class="subtle">빠른 확인에서는 건수를 계산하지 않습니다. 필요한 영역의 상세 보기를 열어 TypeDB 행을 조회하세요.</p>' : '',
    '<div class="ontology-audit-section-grid">',
    ontologyAuditSectionOrder().map(function (section) {
      return renderOntologyAuditSectionCard(section.id);
    }).join(""),
    '</div>',
    '</article>'
  ].join("");
}

function renderOntologyAuditSectionCard(sectionId) {
  var section = ontologyAuditSection(sectionId);
  var sampled = ontologyAuditPayload().summaryMode === "sampled" && !(ontologyState.ontologyAuditSections && ontologyState.ontologyAuditSections[sectionId]);
  var rows = Array.isArray(section.rows) ? section.rows : [];
  var visible = rows.slice(0, 5);
  return [
    '<section class="ontology-audit-section-card"' + cardTypeAttrs("ledger-card") + '>',
    '<header>',
    '<span><em>' + escapeHtml(section.label || ontologyAuditSectionLabel(sectionId)) + '</em><strong>' + escapeHtml(sampled ? "-" : formatInteger(section.total || rows.length || 0)) + '</strong></span>',
    '<p>' + escapeHtml(section.description || "") + '</p>',
    '</header>',
    '<div class="ontology-audit-row-list">',
    visible.length ? visible.map(function (row, index) {
      return renderOntologyAuditRow(sectionId, row, index);
    }).join("") : '<div class="ontology-empty">' + escapeHtml(sampled ? "상세 보기에서 이 영역을 조회합니다." : "조회된 행이 없습니다.") + '</div>',
    '</div>',
    '<footer>',
    section.hasMore ? '<span>더 많은 행이 있습니다. 필터를 좁히거나 API offset으로 확인하세요.</span>' : '<span>현재 조회 범위 전체 표시</span>',
    renderWorkDetailButton("ontology-audit-section", sectionId, "상세 보기", "mini-button"),
    '</footer>',
    '</section>'
  ].join("");
}

function renderOntologyAuditRow(sectionId, row, index) {
  var tone = ontologyAuditTone(row && row.status);
  var key = sectionId + ":" + index;
  return [
    '<button class="ontology-audit-row" type="button" data-work-detail="ontology-audit-row" data-work-detail-key="' + escapeHtml(key) + '">',
    '<span class="ontology-audit-row-main">',
    '<strong>' + escapeHtml(ontologyAuditRowTitle(row)) + '</strong>',
    '<em>' + escapeHtml(ontologyAuditRowMeta(row)) + '</em>',
    '</span>',
    '<span class="ontology-audit-row-side">',
    row && row.status ? '<b class="tone-chip ' + escapeHtml(tone) + '">' + escapeHtml(row.status) + '</b>' : '',
    '<i>' + escapeHtml(ontologyAuditRowPath(row)) + '</i>',
    renderRecordChangedAt(row),
    '</span>',
    '</button>'
  ].join("");
}

function renderSystemOntologyAuditDetail(sectionId) {
  var section = ontologyAuditSection(sectionId);
  var rows = Array.isArray(section.rows) ? section.rows : [];
  return [
    '<section class="work-detail-section ontology-audit-detail-section">',
    '<strong>' + escapeHtml(section.label || ontologyAuditSectionLabel(sectionId)) + '</strong>',
    '<p>' + escapeHtml(section.description || "") + '</p>',
    '<div class="ontology-audit-detail-summary">',
    '<span><em>총 행</em><strong>' + escapeHtml(formatInteger(section.total || rows.length || 0)) + '</strong></span>',
    '<span><em>Entity</em><strong>' + escapeHtml(formatInteger(section.entityCount || 0)) + '</strong></span>',
    '<span><em>Relation</em><strong>' + escapeHtml(formatInteger(section.relationCount || 0)) + '</strong></span>',
    '<span><em>조회 범위</em><strong>' + escapeHtml(formatInteger(rows.length)) + '</strong></span>',
    '</div>',
    '<div class="ontology-audit-detail-list">',
    rows.length ? rows.map(function (row, index) {
      return renderOntologyAuditRow(sectionId, row, index);
    }).join("") : '<div class="ontology-empty">표시할 행이 없습니다.</div>',
    '</div>',
    section.hasMore ? '<p class="data-refresh-status">이 섹션은 API 조회 제한 때문에 일부만 표시됩니다. 검색어나 종목 필터로 범위를 줄이면 더 정확하게 확인할 수 있습니다.</p>' : '',
    '</section>'
  ].join("");
}

function ontologyAuditRowByKey(key) {
  var parts = String(key || "").split(":");
  var sectionId = parts[0] || "";
  var index = Number(parts[1] || 0);
  var section = ontologyAuditSection(sectionId);
  var rows = Array.isArray(section.rows) ? section.rows : [];
  return { sectionId: sectionId, section: section, row: rows[index] || null };
}

function renderOntologyAuditRowDetail(row) {
  row = row || {};
  var raw = row.raw && typeof row.raw === "object" ? row.raw : row;
  var fields = [
    ["Box", row.box],
    ["Type", row.rowType],
    ["Kind", row.kind],
    ["Relation", row.relationType],
    ["Symbol", row.symbol],
    ["Rule", row.ruleId],
    ["Updated", row.updatedAt ? formatClock(row.updatedAt) : ""]
  ].filter(function (item) { return item[1] != null && String(item[1]).trim(); });
  return [
    '<section class="work-detail-section ontology-audit-row-detail">',
    '<strong>' + escapeHtml(ontologyAuditRowTitle(row)) + '</strong>',
    '<p>' + escapeHtml(ontologyAuditRowPath(row)) + '</p>',
    '<div class="ontology-audit-field-grid">',
    fields.map(function (field) {
      return '<span><em>' + escapeHtml(field[0]) + '</em><strong>' + escapeHtml(field[1]) + '</strong></span>';
    }).join(""),
    '</div>',
    '<strong>Raw payload</strong>',
    '<pre class="raw-json-block">' + escapeHtml(JSON.stringify(raw, null, 2)) + '</pre>',
    '</section>'
  ].join("");
}

export { ontologyAuditRowByKey, ontologyAuditRowMeta, ontologyAuditRowTitle, ontologyAuditSection, ontologyAuditSectionLabel, renderOntologyAuditRowDetail, renderSystemOntologyAuditDetail, renderSystemOntologyAuditPanel };
