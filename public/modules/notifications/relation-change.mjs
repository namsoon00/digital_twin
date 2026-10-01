import { escapeHtml } from "../shared/text.mjs";

function renderRelationChangeEvidence(packet) {
  if (!packet || packet.version !== "relation-change-evidence-v1") return "";
  var current = packet.current || {};
  var labels = { unknown: "이전 미보존", added: "추가", removed: "제외", changed: "변경", unchanged: "유지" };
  function text(value) {
    return escapeHtml(value === undefined || value === null || value === "" ? "미보존" : String(value));
  }
  function values(row, kind) {
    if (!row) return "기록 없음";
    if (kind === "facts") return text(row.value);
    if (kind === "hypotheses") return text(row.claim || row.label) + "<br>상태: " + text(row.state)
      + "<br>연결 규칙: " + text((row.ruleIds || []).join(", "))
      + "<br>지지 근거: " + text((row.evidenceIds || []).join(", "))
      + "<br>반대 근거: " + text((row.counterEvidenceIds || []).join(", "))
      + "<br>반증 조건: " + text((row.invalidationConditions || []).join(" · "));
    return text(row.label) + " · " + (row.matched === true ? "성립" : row.matched === false ? "불성립" : "성립 여부 미기록")
      + (row.referenceOnly ? " · 참고 규칙" : "")
      + (row.conditions || []).map(function (c) {
        return "<br>" + text(c.label || c.field) + " " + text(c.operator) + " " + text(c.expectedValue)
          + " / 관측 " + text(c.observedValue) + " / 성립 " + text(c.matched)
          + " / 근거 " + text((c.evidenceIds || []).join(", "));
      }).join("") + "<br>추론 기록: " + text(row.traceId);
  }
  return '<section class="notification-detail-section"><strong>관계 변화 · 가설 → 규칙 → ABox 사실</strong><p>'
    + text(packet.reason) + '</p><p>' + (packet.baselineAvailable
      ? "비교 기준: 마지막 성공 발송 " + text(packet.baselineDeliveredAt)
      : "이전 발송의 상세 근거가 보존되지 않아 이전 값은 표시할 수 없습니다.")
    + '</p><p>관측: ' + text(current.observedAt) + " · 출처: " + text(current.source)
    + " · 자료 상태: " + text(current.dataState) + '</p><p>ABox: ' + text(current.sourceAboxSnapshotId)
    + " · 추론 세대: " + text(current.inferenceGenerationId) + '</p>'
    + (packet.transitions || []).map(function (row) {
      return '<p>' + text(row.previousStateLabel || "이전 상태 미기록") + " → " + text(row.currentStateLabel)
        + " · " + text(row.reason) + '</p>';
    }).join("")
    + [["hypotheses", "가설"], ["rules", "규칙과 조건"], ["facts", "ABox 관측 사실"]].map(function (entry) {
      var rows = ((packet.changes || {})[entry[0]] || []);
      return '<details open><summary>' + entry[1] + " · " + rows.length + '개</summary>'
        + (rows.length ? '<div style="overflow-x:auto"><table><thead><tr><th scope="col">항목</th><th scope="col">변화</th><th scope="col">이전 발송</th><th scope="col">이번 관측</th></tr></thead><tbody>'
          + rows.map(function (row) {
            return '<tr><th scope="row">' + text((row.current || row.previous || {}).label || row.id)
              + '<br><small>' + text(row.id) + '</small></th><td>' + text(labels[row.change] || row.change)
              + '</td><td>' + values(row.previous, entry[0]) + '</td><td>' + values(row.current, entry[0]) + '</td></tr>';
          }).join("") + '</tbody></table></div>' : '<p>상세 근거 미보존</p>') + '</details>';
    }).join("")
    + '<p>발송 판단 당시 저장된 근거입니다. 미보존 표시는 근거가 없다는 뜻이 아니며, 현재 데이터로 보충하지 않습니다. 관계 변화 안내이며 매수·매도 판단은 아닙니다.</p></section>';
}

export { renderRelationChangeEvidence };
