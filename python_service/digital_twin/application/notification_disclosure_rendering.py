import html
from typing import Dict, List

from ..domain.disclosure_analysis import DISCLOSURE_ANALYSIS_PROMPT_VERSION, DisclosureAnalysisResult, split_labeled_text


def disclosure_analysis_block(result: DisclosureAnalysisResult, rich: bool = False) -> str:
    if not result or not result.lines:
        return ""
    rows: List[str] = []
    for line in result.lines:
        label, value = split_labeled_text(line)
        if rich and label and value:
            rows.append("• <b>" + html.escape(label, quote=False) + "</b>: " + html.escape(value, quote=False))
        elif rich:
            rows.append("• " + html.escape(str(line or "").strip(), quote=False))
        else:
            rows.append("• " + str(line or "").strip())
    if result.source:
        if rich:
            rows.append("• <b>분석출처</b>: " + html.escape(result.source, quote=False))
        else:
            rows.append("• 분석출처: " + result.source)
    title = "<b>AI 공시 해석</b>" if rich else "AI 공시 해석"
    return title + "\n" + "\n".join(row for row in rows if row.strip())


def insert_analysis_block(message: str, block: str, rich: bool = False) -> str:
    text = str(message or "").strip()
    if not text or not block or "AI 공시 해석" in text:
        return text
    marker = "\n\n<b>발송 기준</b>" if rich else "\n\n발송 기준"
    if marker in text:
        return text.replace(marker, "\n\n" + block + marker, 1)
    return text + "\n\n" + block


def context_with_disclosure_analysis(context: Dict[str, object], result: DisclosureAnalysisResult) -> Dict[str, object]:
    values = dict(context or {})
    if values.get("disclosureAnalysis") or "AI 공시 해석" in str(values.get("telegramMessage") or ""):
        return values
    plain_block = disclosure_analysis_block(result, False)
    telegram_block = disclosure_analysis_block(result, True)
    values["disclosureAnalysis"] = plain_block
    values["telegramDisclosureAnalysis"] = telegram_block
    values["disclosureAnalysisSource"] = result.source
    values["disclosureAnalysisVersion"] = DISCLOSURE_ANALYSIS_PROMPT_VERSION
    original_telegram = str(values.get("telegramMessage") or "").strip()
    original_readable = str(values.get("readableMessage") or "").strip()
    values["telegramMessage"] = insert_analysis_block(original_telegram, telegram_block, True)
    values["readableMessage"] = insert_analysis_block(original_readable, plain_block, False)
    if not values.get("body") or str(values.get("body") or "").strip() == original_telegram:
        values["body"] = values["telegramMessage"]
    return values
