import { escapeHtml } from "../shared/text.mjs";
import { informationTime } from "../research/information.mjs";

function renderWorldStatus(diagnostics) {
  var observation = diagnostics.observation || {};
  var inventory = diagnostics.worldInventory || {};
  var rows = inventory.worlds || [];
  return '<section class="inline-detail-block"><strong>월드 관측 상태</strong><p>' +
    escapeHtml(observation.label || "진단 기준 시점 미확인") + '</p><p class="subtle">진단 ' +
    escapeHtml(informationTime(observation.generatedAt || diagnostics.generatedAt)) +
    (observation.refreshing ? ' · 새 진단을 읽는 중' : '') +
    (observation.refreshFailed ? ' · 최근 갱신 실패' : '') + '</p>' +
    (rows.length ? rows.map(function (row) {
      var label = { market: "시장", knowledge: "지식", portfolio: "포트폴리오", "shared-premise": "공유 전제" }[row.worldType] || row.worldType;
      return '<p><b>' + escapeHtml(label) + '</b> · ' + escapeHtml(row.worldId) + '<br>' +
        escapeHtml(row.status === "ok" ? "저장 상태 확인" : "저장 상태 " + (row.status || "미확인")) +
        ' · 갱신 ' + escapeHtml(informationTime(row.projectionAsOf)) + '<br>저장 범위 ' +
        escapeHtml(row.activeScopeCount == null ? "미확인" : row.activeScopeCount + "개") +
        ' · 원천 최신성·내용 충족도 별도 확인</p>';
    }).join("") : '<p>월드 목록 미확인 · 비어 있다는 뜻이 아닙니다.</p>') +
    (inventory.sharedPremiseRole === "offline-reconciliation" ? '<p>공유 전제 월드는 오프라인 대조용이며 실시간 추론의 필수 단계가 아닙니다.</p>' : '') +
    '<p class="subtle">' + escapeHtml(inventory.note || "진단 시점과 원천 데이터의 시점은 다릅니다.") + '</p></section>';
}

export { renderWorldStatus };
