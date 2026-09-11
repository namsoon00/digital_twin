"""Render the shared customer investment document for Telegram and web."""

from __future__ import annotations

import html

from digital_twin.domain.customer_investment_document import CustomerInvestmentDocument


def render_customer_investment_document(
    document: CustomerInvestmentDocument,
) -> str:
    """Render a document that was normalized at its creation boundary."""

    parts = ["<b>" + html.escape(document.headline, quote=False) + "</b>"]
    if document.target:
        parts.append("<code>" + html.escape(document.target, quote=False) + "</code>")
    if document.role_label:
        parts.append("<i>" + html.escape(document.role_label, quote=False) + "</i>")
    if document.lead:
        parts.extend(["", "<b>한눈에 보기</b>", "• " + html.escape(document.lead, quote=False)])
    for section in document.sections:
        if not section.rows:
            continue
        parts.extend([
            "",
            "<b>" + html.escape(section.title, quote=False) + "</b>",
            *["• " + html.escape(row, quote=False) for row in section.rows],
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
