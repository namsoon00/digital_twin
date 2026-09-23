function escapeHtml(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function beginnerFriendlyText(value) {
  var text = String(value == null ? "" : value);
  [
    ["온톨로지 판단", "관계 판단"],
    ["온톨로지 컨텍스트", "관계 분석 정보"],
    ["온톨로지 그래프", "관계 분석 데이터"],
    ["온톨로지", "관계 분석"],
    ["세계관 집중도", "관련 종목 비중"],
    ["세계관", "투자 관점"],
    ["손실 thesis 재검증", "손실 구간 보유 이유 재확인"],
    ["thesis 충돌", "보유 이유와 충돌"],
    ["thesis 훼손", "보유 이유 약화"],
    ["보유 thesis", "보유 이유"],
    ["종목 thesis", "종목 보유 이유"],
    ["기존 thesis", "기존 보유 이유"],
    ["thesis", "보유 이유"],
    ["관계 압력", "관계 신호"],
    ["증거", "근거"],
    ["컨텍스트", "정보"],
    ["가설", "설명"]
  ].forEach(function (pair) {
    text = text.split(pair[0]).join(pair[1]);
  });
  return text;
}

function uniqueTextItems(values) {
  var seen = {};
  var result = [];
  (Array.isArray(values) ? values : []).forEach(function (value) {
    var text = String(value || "").trim();
    if (!text || seen[text]) return;
    seen[text] = true;
    result.push(text);
  });
  return result;
}

export { beginnerFriendlyText, escapeHtml, uniqueTextItems };
