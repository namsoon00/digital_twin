"""Render the shared customer investment document for Telegram and web."""

from __future__ import annotations

import html

from digital_twin.modules.read_models.contracts import CustomerInvestmentDocument


def render_customer_investment_document(
    document: CustomerInvestmentDocument,
) -> str:
    """Render a document that was normalized at its creation boundary."""

    if document.layout == "compact-rulebox-v1":
        parts = ["<b>" + html.escape(document.headline, quote=False) + "</b>", html.escape(document.lead, quote=False)]
        for section in document.sections:
            if section.rows:
                if section.key == "notification-purpose" and section.rows == (document.lead,):
                    continue
                title = "<b>" + html.escape(section.title, quote=False) + ":</b>"
                if len(section.rows) > 1:
                    parts.append(title + "\n" + "\n".join(
                        "• " + html.escape(row, quote=False) for row in section.rows))
                else:
                    parts.append(title + " " + html.escape(section.rows[0], quote=False))
        if document.detail_url:
            parts.append('<a href="' + html.escape(document.detail_url, quote=True) + '">전체 판단 근거·현재 데이터 보기</a>')
        footer = " · ".join(v for v in ["기준 " + document.reference_at if document.reference_at else "",
                                      "발송 " + document.sent_at if document.sent_at else "",
                                      document.notification_number] if v)
        if footer:
            parts.append("<i>" + html.escape(footer, quote=False) + "</i>")
        return "\n".join(parts)
    parts = ["<b>" + html.escape(document.headline, quote=False) + "</b>"]
    if document.target:
        parts.append("<code>" + html.escape(document.target, quote=False) + "</code>")
    if document.role_label:
        parts.append("<i>" + html.escape(document.role_label, quote=False) + "</i>")
    prose = document.role == "typedb-observation" and any(section.key == "trading" for section in document.sections)
    if document.lead:
        decision = any(section.key == "decision-review" for section in document.sections)
        parts.extend(["", "<b>이번 변화와 현재 판단</b>" if decision else "<b>종합해서 보면</b>" if prose else "<b>한눈에 보기</b>", ("" if prose else "• ") + html.escape(document.lead, quote=False)])
    for section in document.sections:
        if not section.rows:
            continue
        parts.extend([
            "",
            "<b>" + html.escape(section.title, quote=False) + "</b>",
            *(["\n\n".join(html.escape(row, quote=False) for row in section.rows)]
              if prose and section.key.startswith("hypotheses") else ["• " + html.escape(row, quote=False) for row in section.rows]),
        ])
    if document.links:
        parts.extend([
            "",
            "<b>원문</b>",
            *[
                "• <a href=\"" + html.escape(link.url, quote=True) + "\">"
                + html.escape(link.label, quote=False) + "</a>"
                for link in document.links
            ],
        ])
    if document.detail_url:
        parts.extend([
            "",
            "• <a href=\"" + html.escape(document.detail_url, quote=True)
            + "\">웹에서 상세 근거 보기</a>",
        ])
    footer = " · ".join(part for part in [
        "기준 " + document.reference_at if document.reference_at else "",
        "발송 " + document.sent_at if document.sent_at else "",
        "번호 " + document.notification_number if document.notification_number else "",
    ] if part)
    if footer:
        parts.extend(["", "<i>" + html.escape(footer, quote=False) + "</i>"])
    return "\n".join(
        part for part in parts if str(part).strip() or part == ""
    ).strip()
