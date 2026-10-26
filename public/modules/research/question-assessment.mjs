const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const effects = {supports:"이번 설명을 뒷받침",weakens:"이번 설명을 약화",unchanged:"이번 설명 유지",insufficient:"설명을 바꿀 근거 부족"};

function inquiryView(row) {
  const chain = row.inquiryLineage;
  const next = row.inquiryContinuation;
  if (!chain && !next) return "";
  const relations = {cause:"원인 확인", "counter-evidence":"반대 설명 확인", persistence:"지속 조건 확인", "value-implication":"기업 가치 영향 확인"};
  const path = (chain?.path || []).map(step => `<li><strong>${escape(step.question)}</strong><br>당시 확인한 답: ${escape(step.answer)}</li>`).join("");
  return `<div class="question-assessment"><strong>이어지는 연구</strong>
    ${path ? `<ol>${path}<li><strong>이번 질문</strong> · ${escape(row.question)}</li></ol>` : ""}
    ${chain ? `<p>${escape(relations[chain.relation] || "후속 탐구")} · ${escape(chain.rationale)}</p><p><strong>이번 답으로 구분할 내용</strong><br>${escape(chain.expectedInsight)}</p>` : ""}
    ${next ? `<p><strong>이 답에서 이어진 다음 질문</strong><br>${escape(next.question)}</p><p>${escape(next.expectedInsight)}</p>` : ""}
    <p class="muted">이전 답은 당시 근거로 확인한 연구 이력입니다. 다음 질문의 답변 완료 여부와 별도로 보존합니다.</p></div>`;
}

function answerPartsView(closure) {
  if (!closure) return "";
  const confirmed = (closure.confirmedParts || []).map(part => `<li>${escape(part.text)}</li>`).join("");
  const groups = [["essential", "답변 완료에 필요한 항목"], ["supplementary", "보강 조사 항목"]].map(([role, label]) => {
    const rows = (closure.gapDetails || []).filter(gap => gap.role === role).map(gap =>
      `<li><strong>${escape(gap.requirement)}</strong><br>${escape(gap.causeLabel)}<br>다음 확인: ${escape(gap.nextAction)}</li>`).join("");
    return rows ? `<p><strong>${label}</strong></p><ul>${rows}</ul>` : "";
  }).join("");
  return `${confirmed ? `<p><strong>확인한 답변</strong></p><ul>${confirmed}</ul>` : closure.recordedAnswer ? `<p><strong>${closure.answerAuthority === "source-answer" ? "자료 조사에서 확보한 답변 · 최종 검토 전" : "기록된 답변"}</strong><br>${escape(closure.recordedAnswer)}</p>` : ""}${groups}`;
}

function workflowView(workflow) {
  if (!workflow) return "";
  const date = value => value ? new Date(value).toLocaleString("ko-KR", {timeZone:"Asia/Seoul",hour12:false}) + " KST" : "미정";
  const publication = workflow.publication || {};
  const delivery = publication.receipt?.source === "transport-receipt" && publication.receipt.deliveredAt
    ? `실제 발송 확인 · ${date(publication.receipt.deliveredAt)}`
    : publication.jobId ? "발송 영수증 미확인" : "연결된 AI 관찰 알림 없음 · 내부 연구 결과와 별도";
  const wait = workflow.wait || {};
  const closure = workflow.closure || {};
  const remaining = (closure.missingRequirements || []).map(x => `<li>${escape(x)}</li>`).join("");
  return `<div class="question-workflow"><strong>질문 처리 단계</strong><ol>${(workflow.stages || []).map(stage => `<li><strong>${escape(stage.label)}</strong> · ${escape(stage.detail)}</li>`).join("")}<li><strong>AI 관찰 알림 전달</strong> · ${escape(delivery)}</li></ol>
    ${closure.execution?.label ? `<p><strong>조사 진행 상태</strong> · ${escape(closure.execution.label)}</p>` : ""}
    ${wait.label ? `<p role="status"><strong>${escape(wait.label)}</strong><br>${escape(wait.explanation)}<br>다음 확인 ${escape(date(wait.nextCheckAt))}${wait.scope === "model-provider" ? " · 제공처 한도 초기화 시각은 미확인" : ""}</p>` : ""}
    ${answerPartsView(closure)}
    ${closure.nextStep ? `<p><strong>다음 처리</strong> · ${escape(closure.nextStep)}</p>${remaining && !closure.gapDetails?.length ? `<p>남은 확인 항목</p><ul>${remaining}</ul>` : ""}${closure.resumeWhen ? `<p><strong>재개 조건</strong> · ${escape(closure.resumeWhen)}</p>` : ""}` : ""}
    <p class="muted">예약·자료 확보·부분 답변은 질문 완료가 아닙니다. 가설 평가 기록은 예측 적중을 뜻하지 않습니다.</p></div>`;
}

export function questionAssessmentView(row) {
  const workflow = inquiryView(row) + workflowView(row.workflow);
  const closure = row.workflow?.closure;
  const assessment = row.workflow && !row.workflow.judgmentTaskId ? null : row.questionAssessment;
  const progress = row.evidenceProgress;
  if (!assessment) {
    if (closure || !progress?.missingRequirements?.length) return workflow;
    return workflow + `<div class="question-assessment"><strong>최종 답변에 필요한 자료</strong><ul>${progress.missingRequirements.map((item) => `<li>${escape(item)}</li>`).join("")}</ul><p class="muted">새 원문이나 질문 조건이 바뀌면 다시 확인합니다. 조회 진행 기록은 답변 완료가 아닙니다.</p></div>`;
  }
  const sources = (assessment.sources || []).map((source) => {
    const period = source.periodEnd ? ` · 보고 기간 말 ${escape(source.periodEnd)}` : "";
    if (!source.sourceUrl) return `<li>${escape(source.title || "연결된 근거")}${period} · 원문 링크 미제공</li>`;
    let url;
    try { url = new URL(source.sourceUrl); } catch { return ""; }
    if (url.protocol !== "https:" || url.username || url.password) return "";
    return `<li><a href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">${escape(source.title || url.hostname)}</a>${period}</li>`;
  }).join("");
  return workflow + `<div class="question-assessment" data-judgment-task="${escape(assessment.taskId)}">${closure ? `<details><summary>AI 검토 기록</summary><p>${escape(assessment.answer)}</p></details>` : `<p><strong>${assessment.status === "answered" ? "원래 질문에 대한 AI 답변" : assessment.status === "dismissed" ? "질문 검토 종료" : "원래 질문의 부분 답변"}</strong><br>${escape(assessment.answer)}</p>`}
    ${row.answerResolution?.status === "answered" && assessment.status !== "answered" ? '<p class="muted">앞선 자료조사는 답변 확보로 기록했지만, 최종 검토에서 원래 완료 기준의 부족분을 확인했습니다.</p>' : ""}
    ${sources ? `<ul>${sources}</ul>` : ""}
    ${!closure && assessment.missingRequirements?.length ? `<p><strong>아직 답하지 못한 항목</strong></p><ul>${assessment.missingRequirements.map((item) => `<li>${escape(item)}</li>`).join("")}</ul>` : ""}
    <p><strong>AI 판단에 미친 영향 · ${escape(effects[assessment.judgmentImpact?.effect] || "영향 미확인")}</strong><br>${escape(assessment.judgmentImpact?.reason)}</p>
    <details><summary>이 답변과 함께 기록한 AI 설명</summary><p>${escape(assessment.judgment?.summary)}</p><p>가능한 설명: ${escape(assessment.judgment?.hypothesis)}</p><p>반대 근거와 한계: ${escape(assessment.judgment?.counterEvidence)}</p></details>
    <p class="muted">새 원문이나 질문 조건이 바뀌면 재검토합니다. 질문의 답변과 설명 가설은 투자 성과 검증이나 매매 승인과 구분합니다.</p></div>`;
}

export function questionPerformanceView(value) {
  if (!value) return "";
  return `<div class="panel"><strong>현재 표시된 질문 ${escape(value.sampleCount)}건의 진행 성과</strong>
    <p>최종 답변 ${escape(value.finalAnswers)}건 · 검토된 판단 연결 ${escape(value.reviewedJudgmentLinks)}건 · 부분 답변 보존 ${escape(value.partialAnswersRetained)}건</p>
    ${value.confirmedPartialAnswers != null ? `<p>확인한 답과 부족 항목을 분리한 부분 답변 ${escape(value.confirmedPartialAnswers)}건</p>` : ""}
    <p>기존 설명 뒷받침 ${escape(value.judgmentSupported)}건 · 약화 ${escape(value.judgmentChanged)}건 · 유지 ${escape(value.judgmentUnchanged)}건 · 근거 부족 ${escape(value.judgmentInsufficient)}건</p>
    <p>제공처 실행 대기 ${escape(value.providerWaiting)}건 · 연결된 실제 AI 관찰 발송 ${escape(value.receiptVerifiedNotifications)}건</p>
    ${value.executionStates ? `<p>같은 자료 재조사 중지 ${escape(value.executionStates["unchanged-material"] || 0)}건 · 조사 한도 도달 ${escape(value.executionStates["limit-reached"] || 0)}건 · 조사 진행 불가 ${escape((value.executionStates.blocked || 0) + (value.executionStates["source-unavailable"] || 0))}건 · 최종 검토 대기 ${escape(value.executionStates["review-pending"] || 0)}건</p>` : ""}
    <p class="muted">화면에 표시된 질문 표본입니다. 같은 알림은 한 번만 셉니다. 판단 정확도·수익률은 미측정입니다.</p></div>`;
}
