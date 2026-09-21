import { escapeHtml } from "../shared/text.mjs";

const reasons = {
  "view-needs-observed-state": "실제 관측 근거 없는 종합 의견",
  "mechanism-needs-observed-state": "규칙 이름만 인용한 원인 설명",
  "implication-needs-observed-state": "관측값 없는 투자 영향 설명",
  "event-response-observations-required": "사건 전후 비교 근거 없음",
  "earnings-quality-not-assessed": "일회성 손익 분리 근거 없음",
  "financial-price-causation-unproven": "실적과 가격 변화의 인과관계 미확인",
  "reused-financials-presented-as-new-filing": "기존 재무를 새 공시로 표현",
  "ungrounded-number": "출처에서 확인되지 않은 수치",
  "unknown-evidence-id": "인용 근거 없음",
  "unknown-hypothesis-id": "입력에 없는 가설을 인용",
  "evidence-not-allowed-for-section": "문장 의미와 근거 역할 불일치",
};

export function renderEvidenceValidation(narrative = {}) {
  const ledger = new Map((narrative.evidenceLedger || []).map(row => [row.evidenceId, row]));
  const validations = narrative.validations || [];
  if (!validations.length) return "";
  return '<details class="notification-ai-prompt-audit"><summary>문장별 근거 대조 · ' + validations.length + '건</summary><div class="notification-ai-claim-list notification-evidence-validation">' + validations.map(claim => {
    const evidence = (claim.evidenceIds || []).map(id => {
      const row = ledger.get(id);
      if (!row) return '<li>근거 기록 없음</li>';
      const value = row.value && typeof row.value === "object" ? JSON.stringify(row.value) : String(row.value ?? "관측값 없음");
      const features = row.featureSummary || {};
      const basis = features.measurementBasis === "condition-coverage" ? "조건 충족 확인 · 예측 성과 검증 아님" : "";
      const needsWindows = (row.modelEvidenceIds || []).some(id => String(id).includes("HAS_TEMPORAL_WINDOW"));
      const lineage = needsWindows && !(features.sourceTemporalWindows || []).length ? "기간별 원본 연결 미확인" : "";
      const role = (row.hypothesisRoles || {})[claim.hypothesisId];
      return '<li><strong>' + escapeHtml(row.label || row.kind || "근거") + '</strong><p>' + escapeHtml(value) + '</p><em>' + escapeHtml([row.source, row.sourceAsOf, basis, lineage, role === "support" ? "이 가설을 지지" : role === "counter" ? "이 가설에 반대" : ""].filter(Boolean).join(" · ")) + '</em></li>';
    }).join("");
    const reason = (claim.reasons || []).map(code => reasons[code] || "근거 검증 조건 미충족").join(" · ");
    return '<div><strong>' + (claim.status === "verified" ? "채택" : "제외") + '</strong><p>' + escapeHtml(claim.text || "") + '</p>' + (reason ? '<p>' + escapeHtml(reason) + '</p>' : "") + '<ul>' + evidence + '</ul></div>';
  }).join("") + '</div></details>';
}

export function renderDeliveredMessage(attempts = []) {
  const delivered = [...attempts].reverse().find(row => row.status === "delivered");
  if (!delivered) return "";
  const metadata = delivered.metadata || {};
  const message = metadata.renderedMessage;
  const baseline = metadata.deliveryBaseline || {};
  return '<details class="notification-ai-prompt-audit"><summary>실제 전송 본문</summary>' +
    (message ? '<pre>' + escapeHtml(message) + '</pre>' : '<p>' + (metadata.renderedMessageStatus === "expired" ? "본문 보관 기간 종료 · 발송 확인 기록 유지" : "본문 저장 기록 없음") + '</p>') +
    (baseline.deliveredAt ? '<p>비교한 직전 성공 발송: ' + escapeHtml(baseline.deliveredAt) + '</p>' : '') + '</details>';
}
