function notificationEventSummary(job) {
  job = job || {};
  var document = job.customerInvestmentDocument || {};
  var stored = job.investmentSummary || {};
  var sections = Array.isArray(document.sections) ? document.sections : [];
  function section(keys) {
    return sections.filter(function (item) { return keys.includes(item.key); })
      .flatMap(function (item) { return Array.isArray(item.rows) ? item.rows : []; })
      .filter(function (item) { return typeof item === "string" && item.trim(); }).join(" ");
  }
  var reason = stored.reason || section(["importance", "reasons"]) || document.lead || ((job.reasoningTrace || {}).finalDecision || {}).summary || "";
  return {
    title: stored.headline || document.headline || "",
    change: section(["change"]) || "",
    reason: reason || job.textPreview || "투자 사건의 설명이 기록되지 않았습니다. 상세 원문을 확인하세요.",
    reasonLabel: reason ? "이유" : job.textPreview ? "본문 미리보기" : "이유"
  };
}

export { notificationEventSummary };
