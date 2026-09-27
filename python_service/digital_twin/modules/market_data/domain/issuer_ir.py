"""Official issuer IR source registry and coverage assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Tuple


ISSUER_IR_DATASET_ID = "issuer.ir_documents"


@dataclass(frozen=True)
class IssuerIrSource:
    symbol: str
    issuer_name: str
    market: str
    source_urls: Tuple[str, ...]
    fallback_datasets: Tuple[str, ...]

    @property
    def primary_url(self) -> str:
        return self.source_urls[0] if self.source_urls else ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "issuerName": self.issuer_name,
            "market": self.market,
            "primaryUrl": self.primary_url,
            "sourceUrls": list(self.source_urls),
            "fallbackDatasets": list(self.fallback_datasets),
        }


_SOURCES = {
    row.symbol: row
    for row in (
        IssuerIrSource("000660", "SK하이닉스", "KR", ("https://news.skhynix.com/en/category/ir/",), ("opendart.document",)),
        IssuerIrSource("000680", "LS네트웍스", "KR", ("https://www.lsnetworks.co.kr/html/ir/finance.asp",), ("opendart.document",)),
        IssuerIrSource("005380", "현대자동차", "KR", ("https://www.hyundai.com/worldwide/en/company/ir",), ("opendart.document",)),
        IssuerIrSource("028260", "삼성물산", "KR", ("https://www.samsungcnt.com/ir/event-earnings/earnings-release.do",), ("opendart.document",)),
        IssuerIrSource("035420", "NAVER", "KR", ("https://www.navercorp.com/en/investment/earnings",), ("opendart.document",)),
        IssuerIrSource("035720", "카카오", "KR", ("https://www.kakaocorp.com/ir/noticeList?lang=en",), ("opendart.document",)),
        IssuerIrSource("066570", "LG전자", "KR", ("https://www.lg.com/global/investor-relations/ir-events/",), ("opendart.document",)),
        IssuerIrSource("376900", "로킷헬스케어", "KR", (
            "https://rokithealthcare.com/kr/ir/announcement",
            "https://prd-main-app.rokithc.com/ir/announcement/getList?page=0&size=80&sort=",
        ), ("opendart.document",)),
        IssuerIrSource("AAPL", "Apple", "US", ("https://investor.apple.com/", "https://www.apple.com/newsroom/rss-feed.rss"), ("sec.document",)),
        IssuerIrSource("CPNG", "Coupang", "US", ("https://ir.aboutcoupang.com/",), ("sec.document",)),
        IssuerIrSource("MSTR", "Strategy", "US", ("https://www.strategy.com/investor-relations",), ("sec.document",)),
        IssuerIrSource("NVDA", "NVIDIA", "US", ("https://investor.nvidia.com/",), ("sec.document",)),
        IssuerIrSource("PLTR", "Palantir", "US", ("https://investors.palantir.com/",), ("sec.document",)),
        IssuerIrSource("TSLA", "Tesla", "US", ("https://ir.tesla.com/",), ("sec.document",)),
    )
}


def issuer_ir_sources() -> Dict[str, IssuerIrSource]:
    return dict(_SOURCES)


def issuer_ir_source(symbol: object) -> Optional[IssuerIrSource]:
    return _SOURCES.get(str(symbol or "").upper().strip())


def issuer_ir_coverage(
    subjects: Iterable[object],
    facts: Iterable[Mapping[str, object]],
    collection_states: Iterable[Mapping[str, object]],
) -> Dict[str, object]:
    fact_rows = list(facts or [])
    fact_by_symbol = {
        str(row.get("subjectKey") or "").upper().strip(): dict(row)
        for row in fact_rows
        if str(row.get("datasetId") or "") == ISSUER_IR_DATASET_ID
    }
    facts_by_dataset_symbol = {
        (str(row.get("datasetId") or ""), str(row.get("subjectKey") or "").upper().strip()): dict(row)
        for row in fact_rows
        if str(row.get("datasetId") or "") and str(row.get("subjectKey") or "").strip()
    }
    collection_by_symbol = {
        str(row.get("subjectKey") or "").upper().strip(): dict(row)
        for row in collection_states or []
        if str(row.get("datasetId") or "") == ISSUER_IR_DATASET_ID
    }
    items = []
    counts = {"fresh": 0, "stale": 0, "blocked": 0, "empty": 0, "pending": 0, "not-collected": 0, "unconfigured": 0}
    for subject in subjects or []:
        symbol = str(getattr(subject, "symbol", "") or getattr(subject, "subject_key", "") or "").upper().strip()
        if not symbol:
            continue
        source = issuer_ir_source(symbol)
        fact = fact_by_symbol.get(symbol, {})
        schedule = collection_by_symbol.get(symbol, {})
        quality = dict(fact.get("quality") or {}) if isinstance(fact.get("quality"), Mapping) else {}
        if not source:
            state, reason = "unconfigured", "공식 IR 원천이 등록되지 않았습니다."
        elif quality.get("accessState") == "blocked":
            state, reason = "blocked", "공식 IR 페이지가 자동 수집 요청을 차단했습니다. 공시 원문을 대체 근거로 사용합니다."
        elif quality.get("dataUsable") is True:
            state = "fresh" if str(fact.get("freshnessState") or "") == "fresh" else "stale"
            reason = "공식 IR 문서 목록이 최신입니다." if state == "fresh" else "공식 IR 문서 목록의 신선도 갱신이 필요합니다."
        elif fact:
            state, reason = "empty", "공식 IR 페이지는 확인했지만 검증 가능한 문서 링크를 찾지 못했습니다."
        elif schedule.get("lastSuccessAt"):
            state, reason = "empty", "공식 IR 원천 조회는 완료됐지만 사용할 문서가 없습니다."
        elif schedule:
            state, reason = "pending", "공식 IR 원천 수집을 기다리고 있습니다."
        else:
            state, reason = "not-collected", "공식 IR 원천이 아직 수집 일정에 등록되지 않았습니다."
        counts[state] = counts.get(state, 0) + 1
        fallback_evidence = []
        for dataset_id in source.fallback_datasets if source else ():
            fallback_fact = facts_by_dataset_symbol.get((dataset_id, symbol), {})
            fallback_quality = dict(fallback_fact.get("quality") or {}) if isinstance(fallback_fact.get("quality"), Mapping) else {}
            fallback_evidence.append({
                "datasetId": dataset_id,
                "ready": bool(fallback_fact) and fallback_quality.get("dataUsable") is not False,
                "freshnessState": str(fallback_fact.get("freshnessState") or "not-collected"),
                "sourceAsOf": str(fallback_fact.get("sourceAsOf") or ""),
                "documentState": str(fallback_quality.get("documentState") or ""),
            })
        fallback_ready = any(row["ready"] and row["freshnessState"] == "fresh" for row in fallback_evidence)
        items.append({
            "symbol": symbol,
            "name": str(getattr(subject, "name", "") or (source.issuer_name if source else symbol)),
            "market": str(getattr(subject, "market", "") or (source.market if source else "")),
            "state": state,
            "usable": state == "fresh",
            "authoritativeUsable": state == "fresh" or fallback_ready,
            "reason": reason,
            "primaryUrl": source.primary_url if source else "",
            "fallbackDatasets": list(source.fallback_datasets) if source else [],
            "fallbackReady": fallback_ready,
            "fallbackEvidence": fallback_evidence,
            "documentCount": int(quality.get("documentCount") or 0),
            "latestPublishedAt": str(quality.get("latestPublishedAt") or ""),
            "lastSuccessAt": str(schedule.get("lastSuccessAt") or fact.get("fetchedAt") or ""),
            "lastError": str(schedule.get("lastError") or ""),
        })
    configured = sum(1 for row in items if row["state"] != "unconfigured")
    direct_usable = sum(1 for row in items if row["usable"])
    fallback_for_gap = sum(1 for row in items if not row["usable"] and row["fallbackReady"])
    authoritative_usable = sum(1 for row in items if row["authoritativeUsable"])
    return {
        "datasetId": ISSUER_IR_DATASET_ID,
        "targetCount": len(items),
        "configuredCount": configured,
        "directUsableCount": direct_usable,
        "coveragePercent": round(direct_usable / len(items) * 100.0, 1) if items else 100.0,
        "status": "complete" if items and direct_usable == len(items) else "partial" if direct_usable else "unavailable",
        "fallbackReadyForGapCount": fallback_for_gap,
        "authoritativeUsableCount": authoritative_usable,
        "authoritativeCoveragePercent": round(authoritative_usable / len(items) * 100.0, 1) if items else 100.0,
        "authoritativeStatus": "complete" if items and authoritative_usable == len(items) else "partial" if authoritative_usable else "unavailable",
        "stateCounts": counts,
        "items": items,
    }
