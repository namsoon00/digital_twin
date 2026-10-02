import { escapeHtml } from "../shared/text.mjs";

function renderRelationChangeEvidence(packet) {
  if (!packet || packet.version !== "relation-change-evidence-v1") return "";
  var current = packet.current || {};
  var labels = { unknown: "이전 미보존", added: "추가", removed: "제외", changed: "변경", unchanged: "유지" };
  function text(value) {
    return escapeHtml(value === undefined || value === null || value === "" ? "미보존"
      : typeof value === "object" ? JSON.stringify(value) : String(value));
  }
  function outcome(row) {
    var contract = (row.claimContract || {}).outcomeContract || {};
    var periods = contract.outcomeHorizonMinutes || contract.horizonMinutes || [];
    var labels = { instrumentReturnPct: "확인 이후 주가 등락률(%)", ma20DistanceChangePp: "20일 평균 가격과의 거리 변화(%p)", excessReturnPct: "같은 기간 비교 기준 대비 수익률 차이(%p)" };
    var criteria = Array.isArray(contract.criteria) ? contract.criteria : [];
    if (!criteria.length && !periods.length) return "";
    return '<details><summary>검증 기준과 확인 기간</summary>'
      + (Array.isArray(periods) && periods.length ? '<p>확인 기간: ' + text(periods.join(" · ")) + '분</p>' : '')
      + criteria.map(function (item) {
        var field = item.field || item.metric;
        return '<p>' + text({ result: "결과 확인", invalidation: "반대 결과 확인", cause: "원인 확인" }[item.role] || item.role)
          + ': ' + text(labels[field] || field) + ' ' + text(item.operator) + ' ' + text(item.value === undefined ? item.threshold : item.value)
          + (item.benchmarkSymbol ? ' · 비교 기준 ' + text(item.benchmarkSymbol) : '')
          + (Number(item.horizonMinutes) > 0 ? ' · ' + text(item.horizonMinutes) + '분 뒤' : ' · 위 확인 기간 적용') + '</p>';
      }).join('') + '</details>';
  }
  function modelProof(condition) {
    var fields = { windowKey: "관측 구간", symbol: "종목", sampleCount: "저장 관측 수", hasSufficientHistory: "기간 자료 충분 여부",
      startPrice: "시작 가격", currentPrice: "구간 마지막 가격", priceChangePct: "구간 등락률(%)", recentPriceChangePct: "후반 구간 등락률(%)",
      drawdownFromPeakPct: "구간 고점 대비(%)", reboundFromTroughPct: "구간 저점 대비(%)", priceVelocityChangePct: "앞 구간 대비 후반 등락률 변화(%p)",
      evidenceId: "근거 연결", sourceFeatureSnapshotId: "원본 자료 버전", knowledgeCutoffAt: "자료 기준 시각" };
    var windows = Array.isArray(condition.sourceTemporalWindows) ? condition.sourceTemporalWindows : [];
    return "<br>연결된 분석 신호 조건 확인 · 근거 항목: " + text((condition.measuredFactIds || []).join(", "))
      + "<br>원본 자료 버전: " + text(condition.sourceFeatureSnapshotId) + " · 자료 기준: " + text(condition.knowledgeCutoffAt)
      + "<br>전체 근거 연결: " + text((condition.modelEvidenceIds || []).join(", "))
      + (windows.length ? '<details><summary>가설에 연결된 기간별 측정값</summary>' + windows.map(function (window) {
        return '<p>' + Object.entries(window).map(function (entry) { return text(fields[entry[0]] || entry[0]) + ': ' + text(entry[1]); }).join('<br>') + '</p>';
      }).join('') + '</details>' : "");
  }
  function values(row, kind) {
    if (!row) return "기록 없음";
    if (kind === "facts") return text(row.value);
    if (kind === "hypotheses") return text(row.expectedOutcome || row.claim || row.label) + "<br>상태: " + text(row.state)
      + "<br>연결 규칙: " + text((row.ruleIds || []).join(", "))
      + "<br>지지 근거: " + text((row.evidenceIds || []).join(", "))
      + "<br>반대 근거: " + text((row.counterEvidenceIds || []).join(", "))
      + "<br>반증 조건: " + text(row.falsificationContract || (row.invalidationConditions || []).join(" · "))
      + outcome(row)
      + (row.qualification && row.qualification.reason ? "<br>예측 검증: " + text(row.qualification.reason) : "");
    return text(row.label) + " · " + (row.matched === true ? "성립" : row.matched === false ? "불성립" : "성립 여부 미기록")
      + (row.referenceOnly ? " · 참고 규칙" : "")
      + (row.conditions || []).map(function (c) {
        if (c.modelSignalMatched) return modelProof(c);
        return "<br>" + text(c.label || c.field) + " " + text(c.operator) + " " + text(c.expectedValue)
          + " / 관측 " + text(c.observedValue) + " / 성립 " + text(c.matched === undefined ? c.matchedByTypeDB : c.matched)
          + " / 근거 " + text((c.evidenceIds || []).join(", "));
      }).join("") + (row.evidenceUsableForJudgement === false ? "<br>판단 근거 사용 보류: " + text(row.freshnessGateReason) : "")
      + "<br>추론 기록: " + text(row.traceId);
  }
  return '<section class="notification-detail-section"><details><summary>전체 추론 근거 · 가설·규칙·측정값</summary><p>'
    + text(packet.reason) + '</p><p>' + (packet.baselineAvailable
      ? "비교 기준: 마지막 성공 발송 " + text(packet.baselineDeliveredAt)
      : "이전 발송의 상세 근거가 보존되지 않아 이전 값은 표시할 수 없습니다.")
    + '</p><p>관측: ' + text(current.observedAt) + " · 출처: " + text(current.source)
    + " · 자료 상태: " + text(current.dataState) + '</p><p>ABox: ' + text(current.sourceAboxSnapshotId)
    + " · 추론 세대: " + text(current.inferenceGenerationId) + '</p>'
    + (current.marketSignalCoverage ? '<details><summary>자료별 집계 시각과 제공 범위</summary>'
      + Object.entries(current.marketSignalCoverage).map(function (entry) {
        var source = entry[1] || {};
        var label = { price: "가격", investor: "투자자별 수급", ccnl: "체결", orderbook: "호가" }[entry[0]] || entry[0];
        return '<p>' + text(label) + " · " + text(source.sourceAsOf) + " · " + text(source.status)
          + "<br>집계 방식: " + text(source.measurementType) + "<br>관측 항목: "
          + text((source.observedFields || source.fields || []).join(", ")) + '</p>';
      }).join("") + '</details>' : "")
    + (packet.transitions || []).map(function (row) {
      return '<p>' + text(row.previousStateLabel || "이전 상태 미기록") + " → " + text(row.currentStateLabel)
        + " · " + text(row.reason) + '</p>';
    }).join("")
    + [["hypotheses", "가설"], ["rules", "규칙과 조건"], ["facts", "ABox 관측 사실"]].map(function (entry) {
      var rows = ((packet.changes || {})[entry[0]] || []);
      return '<details' + (entry[0] === "facts" ? "" : " open") + '><summary>' + entry[1] + " · " + rows.length + '개</summary>'
        + (rows.length ? '<div style="overflow-x:auto"><table><thead><tr><th scope="col">항목</th><th scope="col">변화</th><th scope="col">이전 발송</th><th scope="col">이번 관측</th></tr></thead><tbody>'
          + rows.map(function (row) {
            return '<tr><th scope="row">' + text((row.current || row.previous || {}).label || row.id)
              + '<br><small>' + text(row.id) + '</small></th><td>' + text(labels[row.change] || row.change)
              + '</td><td>' + values(row.previous, entry[0]) + '</td><td>' + values(row.current, entry[0]) + '</td></tr>';
          }).join("") + '</tbody></table></div>' : '<p>상세 근거 미보존</p>') + '</details>';
    }).join("")
    + '<p>발송 판단 당시 저장된 근거입니다. 미보존 표시는 근거가 없다는 뜻이 아니며, 현재 데이터로 보충하지 않습니다. 관계 변화 안내이며 매수·매도 판단은 아닙니다.</p></details></section>';
}

export { renderRelationChangeEvidence };
