const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const effects = {supports:"이번 설명을 뒷받침",weakens:"이번 설명을 약화",unchanged:"이번 설명 유지",insufficient:"설명을 바꿀 근거 부족"};

export function questionAssessmentView(row) {
  const assessment = row.questionAssessment;
  const progress = row.evidenceProgress;
  if (!assessment) {
    if (!progress?.missingRequirements?.length) return "";
    return `<div class="question-assessment"><strong>최종 답변에 필요한 자료</strong><ul>${progress.missingRequirements.map((item) => `<li>${escape(item)}</li>`).join("")}</ul><p class="muted">새 원문이나 질문 조건이 바뀌면 다시 확인합니다. 조회 진행 기록은 답변 완료가 아닙니다.</p></div>`;
  }
  const sources = (assessment.sources || []).map((source) => {
    let url;
    try { url = new URL(source.sourceUrl); } catch { return ""; }
    if (url.protocol !== "https:" || url.username || url.password) return "";
    return `<li><a href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">${escape(source.title || url.hostname)}</a>${source.periodEnd ? ` · 보고 기간 말 ${escape(source.periodEnd)}` : ""}</li>`;
  }).join("");
  return `<div class="question-assessment" data-judgment-task="${escape(assessment.taskId)}"><p><strong>${assessment.status === "answered" ? "원래 질문에 대한 AI 답변" : assessment.status === "dismissed" ? "질문 검토 종료" : "원래 질문의 부분 답변"}</strong><br>${escape(assessment.answer)}</p>
    ${row.answerResolution?.status === "answered" && assessment.status !== "answered" ? '<p class="muted">앞선 자료조사는 답변 확보로 기록했지만, 최종 검토에서 원래 완료 기준의 부족분을 확인했습니다.</p>' : ""}
    ${sources ? `<ul>${sources}</ul>` : ""}
    ${assessment.missingRequirements?.length ? `<p><strong>아직 답하지 못한 항목</strong></p><ul>${assessment.missingRequirements.map((item) => `<li>${escape(item)}</li>`).join("")}</ul>` : ""}
    <p><strong>AI 판단에 미친 영향 · ${escape(effects[assessment.judgmentImpact?.effect] || "영향 미확인")}</strong><br>${escape(assessment.judgmentImpact?.reason)}</p>
    <details><summary>이 답변과 함께 기록한 AI 설명</summary><p>${escape(assessment.judgment?.summary)}</p><p>가능한 설명: ${escape(assessment.judgment?.hypothesis)}</p><p>반대 근거와 한계: ${escape(assessment.judgment?.counterEvidence)}</p></details>
    <p class="muted">새 원문이나 질문 조건이 바뀌면 재검토합니다. 질문의 답변과 설명 가설은 투자 성과 검증이나 매매 승인과 구분합니다.</p></div>`;
}
