"""Best-effort templates for already-authored notification content."""

import html
import re
from dataclasses import replace

from digital_twin.modules.notifications.domain.notification.presentation import mapping, presentation_metadata


def _text(value):
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def content_body(content: object) -> str:
    """Optional structured blocks enhance free text, never replace missing facts."""

    values = mapping(content)
    blocks = []
    for key in ("summary", "body"):
        text = _text(values.get(key))
        if text:
            blocks.append(html.escape(text, quote=False))
    sections = values.get("sections")
    for raw in sections if isinstance(sections, (list, tuple)) else []:
        section = mapping(raw)
        rows = section.get("lines") or section.get("rows") or []
        if isinstance(rows, str):
            rows = rows.splitlines()
        lines = [_text(row) for row in rows] if isinstance(rows, (list, tuple)) else []
        lines = list(dict.fromkeys(line for line in lines if line))
        if not lines:
            continue
        title = _text(section.get("title"))
        blocks.append("\n".join([
            *(["<b>" + html.escape(title, quote=False) + "</b>"] if title else []),
            *["• " + html.escape(line, quote=False) for line in lines],
        ]))
    links = values.get("links")
    for raw in links if isinstance(links, (list, tuple)) else []:
        link = mapping(raw)
        url = _text(link.get("url"))
        if url.lower().startswith(("https://", "http://")):
            blocks.append('<a href="' + html.escape(url, quote=True) + '">'
                          + html.escape(_text(link.get("label")) or "원문 보기", quote=False) + "</a>")
    return "\n\n".join(blocks)


def _plain(line):
    return html.unescape(re.sub(r"<[^>]+>", "", line)).strip()


def notification_heading(message_type, context) -> str:
    info = presentation_metadata(message_type, context)
    return info["icon"] + " " + info["label"] + (" · " + info["subject"] if info["subject"] else "")


def present_notification(message_type, context, text) -> str:
    """Apply type identity without changing actions, dates, values or links."""

    if context.get("notificationReplayPreserveOriginal"):
        return str(text or "").strip()
    body = content_body(context.get("notificationContent")) or str(text or "").strip()
    if not body:
        return ""
    lines = body.splitlines()
    notices = []
    while lines and _plain(lines[0]).startswith(("🧪 테스트 알림", "[재발송]", "[검증]")):
        notices.append(lines.pop(0))
        while lines and not lines[0].strip():
            lines.pop(0)
    heading = notification_heading(message_type, context)
    if lines:
        first = _plain(lines[0])
        known_title = first in {
            str(context.get("title") or ""), str(context.get("headline") or ""), heading,
            str(mapping(context.get("customerInvestmentDocument")).get("headline") or ""),
        }
        generated_title = bool(re.match(r"^(?:🔔 새 알림|🔎 |🧠 |🧭 |📊 |🔗 |📦 |⚙️ |📋 |📰 |🗓️ |💼 |\[(?:관찰|주의|위험)\])", first))
        # Free body text is not assumed to contain a title.
        has_body = any(_plain(line) for line in lines[1:])
        if not content_body(context.get("notificationContent")) and (known_title or generated_title) and (has_body or first == heading):
            lines.pop(0)
    seen_rows = set()
    cleaned = []
    for line in lines:
        plain = _plain(line)
        if plain.startswith("• "):
            # Keep links distinct even when their visible labels are identical.
            key = line.strip()
            if key in seen_rows:
                continue
            seen_rows.add(key)
        cleaned.append(line)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()
    number = str(context.get("notificationNumber") or "").strip()
    if number and number not in body:
        footer = " · ".join(value for value in (
            "기준 " + str(context["referenceDate"]) if context.get("referenceDate") else "",
            "발송 " + str(context["sentTime"]) if context.get("sentTime") else "",
            "번호 " + number,
        ) if value)
        body += "\n\n<i>" + html.escape(footer, quote=False) + "</i>"
    return "\n\n".join([
        *notices, "<b>" + html.escape(heading, quote=False) + "</b>", *([body] if body else []),
    ])


def typed_customer_document(document, message_type, context):
    """Correct a document's label from its upstream publication, not its writer."""

    info = presentation_metadata(message_type, context)
    role = {
        "ai-interpretation": "ai-interpretation",
        "price-change": "typedb-observation",
        "relation-change": "typedb-observation",
    }.get(info["kind"], document.role)
    sections = document.sections
    if info["kind"] != "investment-decision":
        sections = tuple(section for section in sections if section.key != "action")
    seen = {document.lead.strip()} if document.lead else set()
    unique_sections = []
    for section in sections:
        rows = []
        for row in section.rows:
            if row.strip() not in seen:
                rows.append(row)
                seen.add(row.strip())
        if rows:
            unique_sections.append(replace(section, rows=tuple(rows)))
    return replace(document, role=role, headline=notification_heading(message_type, context),
                   role_label="", sections=tuple(unique_sections))
