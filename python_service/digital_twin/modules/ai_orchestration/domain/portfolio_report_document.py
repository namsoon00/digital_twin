"""Self-contained, script-free full report attachment from the frozen artifact."""
from html import escape

from .portfolio_analysis import DIMENSIONS, GLOBAL_DIMENSIONS, field
from .portfolio_report import verify_report, safe_url


def render_document(report):
    if report.get("version") != "ai-portfolio-report-v2" or not verify_report(report):
        raise ValueError("invalid full report")
    def claims(values, labels, sources=None):
        rows = []
        for key, label in labels.items():
            claim = values[key]
            status = {"supported": "근거 확인", "partial": "일부 근거", "unknown": "판단 보류"}[claim["status"]]
            rows.append(f'<h3>{escape(label)} <small>{status}</small></h3><p>{escape(claim["text"])}</p>')
            if claim["missing"]:
                rows.append('<p class="gap">추가 확인: ' + escape(" / ".join(claim["missing"])) + '</p>')
            if sources:
                for ref in claim["refs"]:
                    record = sources[ref["sourceId"]]
                    data = record["data"]
                    value = field(data, ref["field"])
                    url = safe_url(data.get("sourceUrl") or data.get("url"))
                    label = data.get("title") or data.get("label") or record["kind"]
                    title = '<a rel="noreferrer" href="' + escape(url, quote=True) + '">' + escape(str(label)) + '</a>' if url else escape(str(label))
                    rows.append('<details><summary>인용 근거 · ' + title + '</summary><p>' + escape(ref["field"]) + ': ' + escape(str(value)) + '</p><p>자료 시각 ' + escape(record["asOf"] or "미확인") + '</p></details>')
        return ''.join(rows)
    c = report["coverage"]
    parts = ['<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">',
        '<title>' + escape(report["title"]) + '</title><style>body{font:16px/1.75 system-ui,sans-serif;color:#173043;background:#f6f8fa;max-width:960px;margin:auto;padding:32px}section{padding:24px;background:white;border:1px solid #dce4e8;border-radius:12px;margin:24px 0}h1,h2,h3{line-height:1.4}small,.meta{font-size:13px;color:#546675}.gap{background:#fff4df;padding:10px}details{margin:8px 0}a{color:#146b8c}p{white-space:pre-wrap;overflow-wrap:anywhere}@media print{body{background:white;padding:0}section{break-inside:avoid}}</style><body>',
        '<h1>' + escape(report["title"]) + '</h1><p>' + escape(report["boundary"]) + '</p>',
        '<p class="meta">분석 시작 ' + escape(report["analysisStartedAt"]) + '<br>완료 ' + escape(report["createdAt"]) + '<br>계좌 기준 ' + escape(report["snapshotAt"]) + '</p>',
        f'<p>전체 {c["subjects"]}종목 · 보유 {c["holdings"]} · 관심 {c["watchlist"]} · 검토된 설명 {c["withReviewedAnalysis"]}종목 · 부족 항목 {c["withGaps"]}종목<br>확보 자료 {c["sourcesAvailable"]}건 중 본문 확인 {c["sourcesRead"]}건</p>',
        '<section><h2>전체 종합</h2>' + claims(report["overall"], GLOBAL_DIMENSIONS, report["overallSources"]) + '</section>']
    for subject in report["subjects"]:
        parts += ['<section><h2>' + escape(subject["name"]) + ' (' + escape(subject["symbol"]) + ') · ' + ('보유' if subject["scope"] == 'holding' else '관심') + '</h2>',
                  '<p class="meta">분석 ' + escape(subject["analyzedAt"]) + '</p>', claims(subject["claims"], DIMENSIONS, subject["sources"])]
        coverage = subject["sourceCoverage"]
        parts.append(f'<p class="meta">자료 {coverage["available"]}건 중 {coverage["read"]}건 확인 · 미열람 {coverage["unread"]}건</p>')
        if coverage.get("graphLimitation"):
            parts.append('<p class="gap">검증된 그래프 자료를 확보하지 못한 항목이 있습니다. 저장된 과거 조사와 계좌 자료의 범위로 제한했습니다.</p>')
        if coverage.get("memoryCoverage", {}).get("caseReadLimitReached"):
            parts.append('<p class="gap">과거 질문 조회 상한에 도달했습니다. 모든 과거 조사 이력을 읽은 것은 아닙니다.</p>')
        parts.append('</section>')
    parts += ['<p class="meta">보고서 ' + escape(report["reportId"]) + '<br>내용 해시 ' + escape(report["contentHash"]) + '</p></body></html>']
    return ''.join(parts)
