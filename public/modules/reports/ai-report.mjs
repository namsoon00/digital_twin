import { escapeHtml as e } from "../shared/text.mjs";
import { renderCompanyEvidenceReport } from "../instruments/company-report.mjs";

export function reportDate(value) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"
  }).format(date) + " KST" : "시각 미확인";
}

function lines(rows) {
  return (rows || []).length ? `<ul>${rows.map(row => `<li>${e(String(row))}</li>`).join("")}</ul>` : "";
}

function sourceLink(source) {
  let url;
  try { url = new URL(source.sourceUrl); } catch { return e(source.title || source.factId || "출처 식별자 미확인"); }
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return e(source.title || "원문 주소 확인 필요");
  return `<a href="${e(url.href)}" target="_blank" rel="noopener noreferrer">${e(source.title || "원문 보기")}</a>`;
}

function answerView(answer) {
  const status = {answered: "답변 확인", partial: "부분 답변", dismissed: "질문 종료 검토"}[answer.answerStatus] || "검토 기록";
  const impact = answer.judgmentImpact || {};
  const changes = {new: "당시 첫 의견", unchanged: "당시 의견 유지", strengthened: "당시 근거 강화", weakened: "당시 근거 약화", withdrawn: "당시 의견 철회"};
  return `<article class="answer"><div class="meta">${e(status)} · 검토 ${e(reportDate(answer.at))}</div>
    <h4>${e(answer.question)}</h4><p>${e(answer.answer || "")}</p>
    ${lines((answer.answerParts?.confirmed || []).map(p => p.text))}
    ${answer.judgment?.summary ? `<h5>답변을 검토한 당시의 AI 의견</h5><p>${e(answer.judgment.summary)}</p>` : ""}
    ${answer.judgment?.hypothesis ? `<p><strong>가능한 설명</strong> ${e(answer.judgment.hypothesis)}</p>` : ""}
    ${impact.reason ? `<h5>당시 의견에 미친 영향 · ${e(changes[impact.opinionChange] || "근거 연결")}</h5><p>${e(impact.reason)}</p>` : ""}
    ${answer.judgment?.counterEvidence ? `<p><strong>반대 근거·한계</strong> ${e(answer.judgment.counterEvidence)}</p>` : ""}
    ${answer.missingRequirements?.length ? `<h5>아직 확인하지 못한 항목</h5>${lines(answer.missingRequirements)}` : ""}
    <details><summary>답변의 원문과 검토 근거</summary><ul>${(answer.sources || []).map(s => `<li>${sourceLink(s)}${s.periodEnd ? ` · 보고기간 ${e(s.periodEnd)}` : ""}</li>`).join("")}</ul>
    <p class="meta">실행 ${e(answer.taskId)}<br>독립 검토 ${e(answer.researchReviewInputId)}</p></details></article>`;
}

function opinionView(opinion) {
  if (!opinion) return `<p class="empty">이번 조회 범위에서 독립 검토와 원문 연결을 확인한 AI 관찰을 찾지 못했습니다. 아래의 질문 답변·기업 자료와 구분합니다.</p>`;
  return `<p class="meta">분석 ${e(reportDate(opinion.at))} · 당시 근거에 대한 의견 · 현재 유효성 재검증 전</p>
    <p class="lead">${e(opinion.summary)}</p>
    ${[["가능한 설명", opinion.hypothesis], ["당시 보유·관심 상황에서의 의미", opinion.portfolioImpact],
       ["반대 근거와 한계", opinion.counterEvidence], ["당시 이전 의견과 비교", opinion.comparison]].map(([title, text]) =>
      text ? `<h4>${e(title)}</h4><p>${e(text)}</p>` : "").join("")}
    ${opinion.externalInfluences?.length ? `<h4>당시 확인한 외부 상황의 영향</h4>${opinion.externalInfluences.map(r =>
      `<p><strong>${r.assessment === "conditional" ? "조건부 설명" : "영향 미확인"}</strong> ${e(r.transmission)}</p>
      <p>적용 기간: ${e(r.horizon)}<br>가정: ${e(r.assumptions)}<br>반대 근거: ${e(r.counterEvidence)}</p>${lines(r.missingEvidence)}`).join("")}` : ""}
    ${opinion.followUpConditions?.length ? `<h4>당시 등록한 확인 조건</h4>${opinion.followUpConditions.map(r =>
      `<p>${e(r.description)}<br><small>등록 기한 ${e(reportDate(r.expiresAt))} · 이후 충족 여부를 이 보고서에서 새로 판정하지 않았습니다.</small></p>`).join("")}` : ""}
    <details><summary>의견의 정확한 근거·시점</summary><ul>${(opinion.evidence || []).map(r =>
      `<li>${e(r.label)} · ${e(typeof r.value === "object" ? JSON.stringify(r.value) : String(r.value))} · ${e(reportDate(r.sourceAsOf))} ${r.sourceUrl ? sourceLink(r) : ""}</li>`).join("")}</ul></details>`;
}

export function renderAIReport(report) {
  const c = report.coverage;
  return `<section class="report-intro"><h2>${e(report.title)}</h2>
    <p>생성 ${e(reportDate(report.createdAt))}<br>계좌 자료 기준 ${e(reportDate(report.snapshotAt))}</p>
    <p class="boundary">${e(report.boundary)}</p>
    <div class="metrics"><div><strong>${e(c.subjects)}</strong>전체 종목</div><div><strong>${e(c.withCompanySources)}</strong>재무 자료가 있는 종목</div>
    <div><strong>${e(c.withOpinion)}</strong>검토된 과거 AI 관찰</div><div><strong>${e(c.reviewedAnswers)}</strong>검토된 질문 답변</div></div>
    <p>금리·전쟁·선거·산업 등 모든 이슈를 새로 조사한 보고서는 아닙니다. 확인된 범위의 설명과 원문만 포함하며, 종목 간 매매 우선순위나 목표 비중을 새로 계산하지 않습니다.</p>
    <nav class="contents">${report.subjects.map((s, i) => `<a href="#subject-${i}">${e(s.name)} (${e(s.symbol)})</a>`).join("")}</nav></section>
    ${report.subjects.map((s, i) => `<section class="subject" id="subject-${i}"><header><span class="tag">${s.scope === "holding" ? "보유" : "관심"}</span><h2>${e(s.name)} <small>${e(s.symbol)}</small></h2></header>
      <h3>AI가 남긴 의견</h3>${opinionView(s.opinion)}
      <h3>질문에서 확인한 내용과 의미</h3>${s.answers.length ? s.answers.map(answerView).join("") : '<p class="empty">인용 근거와 독립 검토를 함께 확인한 답변이 없습니다.</p>'}
      ${s.coverage.omittedAnswers ? `<p>최근 답변 ${e(s.answers.length)}건을 표시했습니다. 추가 ${e(s.coverage.omittedAnswers)}건은 중앙 AI 조사 기록에서 확인할 수 있습니다.</p>` : ""}
      <h3>기업 가치·실적 자료</h3><p class="meta">수집 자료를 정리한 기업 보고서입니다. 위 AI 의견의 분석 시각과 별도로 확인하세요.</p>
      ${Object.keys(s.companyReport || {}).length ? renderCompanyEvidenceReport(s.companyReport) : '<p class="empty">기업 자료 보고서를 구성할 저장 자료가 없습니다.</p>'}
      <h3>다음에 확인할 질문</h3>${s.openQuestions.length ? `<ul>${s.openQuestions.map(q => `<li><strong>${e(q.question)}</strong>${q.reason ? `<p>${e(q.reason)}</p>` : ""}</li>`).join("")}</ul>` : '<p>이번 조회 범위에 열린 질문이 없습니다. 조사가 모두 완료됐다는 뜻은 아닙니다.</p>'}
      ${s.coverage.omittedOpenQuestions ? `<p>추가 질문 ${e(s.coverage.omittedOpenQuestions)}건은 중앙 AI 기록에서 확인할 수 있습니다.</p>` : ""}
      ${s.coverage.caseReadLimitReached ? '<p class="boundary">질문 조회 상한에 도달했습니다. 이 종목의 전체 조사 이력은 포함되지 않았습니다.</p>' : ""}
    </section>`).join("")}
    <footer class="meta">보고서 ${e(report.reportId)}<br>내용 식별자 ${e(report.contentHash)}</footer>`;
}

export function deliveryLabel(delivery) {
  if (delivery?.status === "delivered" && delivery.receiptVerified === true) return "텔레그램 전달 확인 · " + reportDate(delivery.deliveredAt);
  return ({"not-requested": "아직 발송을 요청하지 않았습니다.", pending: "발송 대기 중", processing: "전송 중", failed: "발송 실패 · 알림 이력에서 확인",
    suppressed: "발송 설정에 따라 보류됨", "receipt-unconfirmed": "실제 전송 영수증을 확인하지 못했습니다.", "retry-wait": "전송 재시도 대기 중"})[delivery?.status] || "발송 상태 확인 중";
}
