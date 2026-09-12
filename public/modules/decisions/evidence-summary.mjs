function evidenceSummary(detail) {
  var evidence = (detail || {}).evidence || {};
  var explanation = (detail || {}).explanation || {};
  var records = Array.isArray(evidence.records) ? evidence.records : [];
  function count(key, rows) {
    var value = evidence[key];
    if (value !== null && value !== undefined && Number.isFinite(Number(value))) return Number(value);
    return Array.isArray(rows) ? rows.length : null;
  }
  var missingItems = Array.isArray(evidence.missingDataItems) ? evidence.missingDataItems : [];
  var missing = Array.isArray(evidence.missingData) ? evidence.missingData : [];
  return {
    support: count("supportCount", explanation.supportingCauses),
    counter: count("counterCount", explanation.counterCauses),
    missing: count("missingCount", missingItems.length || missing.length ? missingItems.concat(missing) : undefined),
    originals: records.filter(function (item) { return item.resolutionState === "resolved"; }).length,
    lineage: records.filter(function (item) { return item.resolutionState === "lineage-linked"; }).length,
    records: records.length
  };
}

function evidenceResolutionLabel(state) {
  return { resolved: "원천 자료 연결", "lineage-linked": "추론 계보 연결", "identifier-only": "식별자만 저장" }[state] || "연결 상태 확인 필요";
}

export { evidenceSummary, evidenceResolutionLabel };
