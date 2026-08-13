"""Deterministic disclosure classification before evidence governance.

The classifier describes document subject and review importance. It never
chooses an investment action; claim governance and TypeDB rules decide whether
the persisted filing can affect a judgement.
"""

from __future__ import annotations

from typing import Dict, Iterable


DISCLOSURE_TAXONOMY_VERSION = "disclosure-taxonomy-v1"


def _text(*values: object) -> str:
    return " ".join(str(value or "").casefold().strip() for value in values if str(value or "").strip())


def _contains(text: str, terms: Iterable[str]) -> bool:
    return any(str(term or "").casefold() in text for term in terms)


def classify_disclosure(
    title: object,
    document_type: object = "",
    source: object = "",
) -> Dict[str, object]:
    """Return stable event and materiality metadata for a filing."""
    combined = _text(title, document_type)
    form = str(document_type or title or "").upper().strip()
    event_type = "general"
    materiality = "context"
    category = "routine"
    reason = "routine-or-unclassified-disclosure"

    if form in {"10-K", "10-Q", "20-F", "40-F"} or _contains(combined, [
        "사업보고서", "분기보고서", "반기보고서", "실적", "영업(잠정)실적",
    ]):
        event_type = "earnings"
        materiality = "material"
        category = "financial-results"
        reason = "periodic-or-earnings-disclosure"
    elif _contains(combined, [
        "매출액또는손익구조", "영업실적전망", "실적전망", "전망공시", "guidance",
    ]):
        event_type = "guidance"
        materiality = "material"
        category = "guidance"
        reason = "forward-financial-guidance"
    elif _contains(combined, [
        "유상증자", "무상증자", "전환사채", "신주인수권", "교환사채", "감자",
        "자기주식", "배당", "주식분할", "주식병합", "합병", "회사분할",
        "공개매수", "최대주주변경", "13d", "13g", "offering", "buyback",
    ]):
        event_type = "capital_policy"
        materiality = "material"
        category = "capital-structure"
        reason = "capital-structure-or-shareholder-return-change"
    elif _contains(combined, [
        "단일판매", "공급계약", "수주", "생산중단", "영업정지", "시설투자",
        "타법인주식", "자산양수도", "contract", "supply agreement",
    ]):
        event_type = "supply_chain"
        materiality = "material"
        category = "operations-contract"
        reason = "material-operating-or-contract-event"
    elif _contains(combined, [
        "소송", "제재", "압수수색", "조사", "해명", "불성실공시", "상장폐지",
        "관리종목", "회생", "파산", "litigation", "investigation", "antitrust",
    ]):
        event_type = "regulation"
        materiality = "material"
        category = "legal-regulatory"
        reason = "legal-regulatory-or-listing-risk"
    elif _contains(combined, ["신제품", "품목허가", "임상", "특허", "launch", "approval"]):
        event_type = "product"
        materiality = "notable"
        category = "product-development"
        reason = "product-or-approval-update"
    elif form in {"8-K", "6-K"}:
        materiality = "notable"
        category = "current-report"
        reason = "current-report-needs-document-level-classification"
    elif form in {"3", "4", "5", "DEF 14A", "DEF14A"} or _contains(combined, [
        "임원ㆍ주요주주", "임원·주요주주", "주식등의대량보유", "주주총회", "대표이사변경",
    ]):
        event_type = "capital_policy"
        materiality = "notable"
        category = "ownership-governance"
        reason = "ownership-or-governance-update"

    return {
        "version": DISCLOSURE_TAXONOMY_VERSION,
        "eventType": event_type,
        "materialityState": materiality,
        "disclosureCategory": category,
        "classificationReason": reason,
        "source": str(source or "").strip(),
    }
