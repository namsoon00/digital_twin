import hashlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .market_data import clamp, number
from . import news_analysis as news_domain
from .ontology_decision_state import (
    DATA_STATE_LABELS,
    REVIEW_LEVEL_LABELS,
    VALIDATION_STATE_LABELS,
    conflict_state_from_roles,
)
from .portfolio import Position
from .symbol_universe import normalize_market


ACTIVE_INVESTMENT_OPINION_VERSION = "active-investment-opinion-v1"

ACTION_LABELS = {
    "BUY": "매수",
    "ADD": "추가매수",
    "HOLD": "보유",
    "TRIM": "분할매도",
    "SELL": "매도",
    "AVOID": "매수보류",
}

SUPPORT_KEYWORDS = (
    "beat",
    "upgrade",
    "raised guidance",
    "buyback",
    "dividend",
    "record revenue",
    "growth",
    "partnership",
    "contract",
    "approval",
    "launch",
    "strong demand",
    "surge",
    "실적",
    "상향",
    "수주",
    "계약",
    "승인",
    "자사주",
    "배당",
    "성장",
    "흑자",
    "개선",
    "최대",
)

RISK_KEYWORDS = (
    "miss",
    "downgrade",
    "lawsuit",
    "probe",
    "investigation",
    "recall",
    "offering",
    "dilution",
    "bankruptcy",
    "cut guidance",
    "weak demand",
    "loss",
    "risk",
    "하향",
    "소송",
    "조사",
    "리콜",
    "유상증자",
    "감자",
    "횡령",
    "적자",
    "손실",
    "부진",
    "처분",
    "매각",
    "기재정정",
    "불확실",
    "리스크",
)

KNOWN_COMPANY_ALIASES = {
    "005930": ["삼성전자", "Samsung Electronics", "Samsung"],
    "000660": ["SK하이닉스", "SK Hynix", "Hynix"],
    "035420": ["NAVER", "네이버"],
    "005380": ["현대차", "현대자동차", "Hyundai Motor"],
    "000020": ["동화약품"],
    "AAPL": ["Apple", "Apple Inc."],
    "NVDA": ["NVIDIA", "Nvidia"],
    "TSLA": ["Tesla"],
    "PLTR": ["Palantir"],
    "MSTR": ["MicroStrategy", "Strategy"],
    "STRC": ["Strategy"],
}

PEER_ALIASES = {
    "005930": ["SK하이닉스", "SK Hynix", "Micron", "TSMC"],
    "000660": ["삼성전자", "Samsung Electronics", "Micron", "TSMC"],
    "AAPL": ["Samsung Electronics", "Microsoft", "Google", "Alphabet"],
    "NVDA": ["AMD", "Broadcom", "Intel", "TSMC"],
    "TSLA": ["BYD", "Rivian", "Lucid", "Hyundai Motor"],
}

SECTOR_TOPIC_KEYWORDS = {
    "semiconductor": ["반도체", "메모리", "memory", "HBM", "DRAM", "D램", "NAND", "AI chip", "chip", "foundry"],
    "platform": ["플랫폼", "검색", "커머스", "cloud", "AI", "광고", "핀테크"],
    "auto": ["자동차", "전기차", "EV", "battery", "배터리", "mobility"],
    "crypto": ["bitcoin", "비트코인", "crypto", "digital asset"],
    "ai": ["AI", "artificial intelligence", "GPU", "accelerator"],
}

MARKET_TOPIC_KEYWORDS = [
    "코스피",
    "코스닥",
    "KOSPI",
    "KOSDAQ",
    "NASDAQ",
    "S&P",
    "Dow",
    "환율",
    "금리",
    "yield",
    "FOMC",
    "inflation",
    "시장",
]

LOW_RELIABILITY_SOURCE_TERMS = ["blog", "블로그", "cafe", "reddit", "rumor"]
HIGH_RELIABILITY_SOURCE_TERMS = ["dart", "sec", "edgar", "reuters", "bloomberg", "연합", "yonhap", "cnbc", "wsj", "marketwatch"]


@dataclass(init=False)
class ResearchEvidence:
    evidence_id: str
    symbol: str
    kind: str
    source: str
    title: str
    summary: str = ""
    url: str = ""
    observed_at: str = ""
    polarity: str = "context"
    published_at: str = ""
    raw_payload: Dict[str, object] = field(default_factory=dict)
    source_trust_state: str = "unknown"
    materiality_state: str = "context"
    data_state: str = "partial"
    validation_state: str = "conditional"

    def __init__(
        self,
        evidence_id: str,
        symbol: str,
        kind: str,
        source: str,
        title: str,
        summary: str = "",
        url: str = "",
        observed_at: str = "",
        polarity: str = "context",
        *legacy_values: object,
        published_at: str = "",
        raw_payload: Dict[str, object] = None,
        source_trust_state: str = "",
        materiality_state: str = "",
        data_state: str = "",
        validation_state: str = "",
        **deprecated_values: object,
    ):
        """Create categorical evidence while accepting one legacy row shape.

        Historical rows used ``impact_score, confidence, published_at,
        raw_payload`` after ``polarity``.  We only read those values to map old
        persisted rows into categorical states; newly created evidence does not
        retain or expose them.
        """
        legacy_impact = deprecated_values.pop("impact_score", None)
        legacy_confidence = deprecated_values.pop("confidence", None)
        if deprecated_values:
            unknown = ", ".join(sorted(str(key) for key in deprecated_values))
            raise TypeError("Unexpected ResearchEvidence fields: " + unknown)
        if len(legacy_values) > 4:
            raise TypeError("ResearchEvidence accepts at most four legacy values")
        if legacy_values:
            legacy_impact = legacy_values[0]
        if len(legacy_values) >= 2:
            legacy_confidence = legacy_values[1]
        if len(legacy_values) >= 3 and not published_at:
            published_at = str(legacy_values[2] or "")
        if len(legacy_values) >= 4 and raw_payload is None and isinstance(legacy_values[3], dict):
            raw_payload = legacy_values[3]

        payload = dict(raw_payload or {}) if isinstance(raw_payload, dict) else {}
        if source_trust_state:
            payload["sourceTrustState"] = source_trust_state
        if materiality_state:
            payload["materialityState"] = materiality_state
        if data_state:
            payload["dataState"] = data_state
        if validation_state:
            payload["validationState"] = validation_state
        if not payload.get("sourceTrustState"):
            payload["sourceTrustState"] = (
                news_domain.news_source_trust_state(legacy_confidence)
                if legacy_confidence not in (None, "")
                else news_domain.source_trust_state_for_source(source, payload.get("provider") or "")
            )
        if not payload.get("materialityState"):
            payload["materialityState"] = news_domain.news_materiality_state(
                payload.get("eventType"),
                legacy_impact,
                relation_scope=payload.get("relationScope"),
                impact_polarity=payload.get("stockImpactPolarity") or polarity,
                source_trust_state=payload.get("sourceTrustState"),
            )
        states = news_domain.news_state_payload(payload)

        self.evidence_id = str(evidence_id or "")
        self.symbol = str(symbol or "").upper().strip()
        self.kind = str(kind or "news").strip() or "news"
        self.source = str(source or "Research").strip() or "Research"
        self.title = str(title or "").strip()
        self.summary = str(summary or "").strip()
        self.url = str(url or "").strip()
        self.observed_at = str(observed_at or "").strip()
        self.polarity = str(polarity or "context").strip().lower() or "context"
        self.published_at = str(published_at or "").strip()
        self.raw_payload = news_domain.public_news_payload(payload)
        self.source_trust_state = states["sourceTrustState"]
        self.materiality_state = states["materialityState"]
        self.data_state = states["dataState"]
        self.validation_state = states["validationState"]

    def state_payload(self) -> Dict[str, str]:
        payload = dict(self.raw_payload or {})
        payload.update({
            "sourceTrustState": self.source_trust_state,
            "materialityState": self.materiality_state,
            "dataState": self.data_state,
            "validationState": self.validation_state,
        })
        return news_domain.news_state_payload(payload)

    def to_dict(self) -> Dict[str, object]:
        payload = news_domain.public_news_payload(self.raw_payload or {})
        states = self.state_payload()
        evidence_role = str(self.polarity or "context").strip().lower()
        if evidence_role not in {"risk", "support", "counter", "context", "blocking"}:
            evidence_role = "context"
        return {
            "evidenceId": self.evidence_id,
            "symbol": self.symbol,
            "kind": self.kind,
            "source": self.source,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "observedAt": self.observed_at,
            "publishedAt": self.published_at,
            "polarity": self.polarity,
            "evidenceRole": evidence_role,
            "relationScope": str(payload.get("relationScope") or ""),
            "eventType": str(payload.get("eventType") or ""),
            **states,
            "ontologyRelations": list(payload.get("ontologyRelations") or []),
            "excludedReason": str(payload.get("excludedReason") or ""),
            "analysisSummary": str(payload.get("analysisSummary") or ""),
            "articleSummaryKo": str(payload.get("articleSummaryKo") or ""),
            "articleReadStatus": str(payload.get("articleReadStatus") or ""),
            "articleFacts": dict(payload.get("articleFacts") or {}),
            "stockImpact": str(payload.get("stockImpact") or ""),
            "stockImpactLabel": str(payload.get("stockImpactLabel") or ""),
            "stockImpactPolarity": str(payload.get("stockImpactPolarity") or ""),
            "stockImpactReasonKo": str(payload.get("stockImpactReasonKo") or ""),
            "aiAnalysis": dict(payload.get("aiAnalysis") or {}),
            "articleAiAnalysisVersion": str(payload.get("articleAiAnalysisVersion") or ""),
            "analysisConflict": bool(payload.get("analysisConflict")),
            "analysisConflictSource": str(payload.get("analysisConflictSource") or ""),
            "analysisConflictExistingPolarity": str(payload.get("analysisConflictExistingPolarity") or ""),
            "analysisConflictAiPolarity": str(payload.get("analysisConflictAiPolarity") or ""),
            "analysisConflictReasonKo": str(payload.get("analysisConflictReasonKo") or ""),
            "dataQualityRisk": str(payload.get("dataQualityRisk") or ""),
            "sourceKind": str(payload.get("sourceKind") or ""),
            "sourcePlatform": str(payload.get("sourcePlatform") or ""),
            "entityLinks": list(payload.get("entityLinks") or []),
            "qualityGate": dict(payload.get("qualityGate") or {}),
            "payload": payload,
        }


@dataclass(frozen=True)
class NewsCollectionTarget:
    symbol: str
    name: str
    market: str = ""
    currency: str = ""
    sector: str = ""

    def normalized_symbol(self) -> str:
        return str(self.symbol or "").upper().strip()

    def normalized_market(self) -> str:
        return normalize_market(self.market)

    def is_korean_market(self) -> bool:
        market = self.normalized_market()
        return market in {"KOSPI", "KOSDAQ"} or self.normalized_symbol().isdigit()

    def search_query(self) -> str:
        terms = []
        for raw in [self.name, self.symbol]:
            text = str(raw or "").replace('"', " ").strip()
            if text and text not in terms:
                terms.append(text)
        if not terms:
            return self.normalized_symbol()
        if len(terms) == 1:
            return terms[0]
        return "(" + " OR ".join('"' + term + '"' for term in terms[:2]) + ")"


def _lower_text(value: object) -> str:
    return str(value or "").casefold()


def _unique_texts(values: Iterable[object]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def target_aliases(target: NewsCollectionTarget) -> List[str]:
    symbol = target.normalized_symbol()
    aliases = [target.name, symbol]
    aliases.extend(KNOWN_COMPANY_ALIASES.get(symbol, []))
    return _unique_texts(aliases)


def peer_aliases(target: NewsCollectionTarget) -> List[str]:
    return _unique_texts(PEER_ALIASES.get(target.normalized_symbol(), []))


def sector_topic_keywords(target: NewsCollectionTarget) -> List[str]:
    text = _lower_text(" ".join([target.sector, target.market, target.currency, target.symbol, target.name]))
    topics: List[str] = []
    if any(token in text for token in ["반도체", "semiconductor", "005930", "000660", "nvda"]):
        topics.extend(SECTOR_TOPIC_KEYWORDS["semiconductor"])
    if any(token in text for token in ["플랫폼", "platform", "naver", "aapl", "pltr"]):
        topics.extend(SECTOR_TOPIC_KEYWORDS["platform"])
    if any(token in text for token in ["자동차", "auto", "005380", "tesla", "tsla"]):
        topics.extend(SECTOR_TOPIC_KEYWORDS["auto"])
    if any(token in text for token in ["mstr", "strc", "bitcoin", "crypto", "디지털자산"]):
        topics.extend(SECTOR_TOPIC_KEYWORDS["crypto"])
    if any(token in text for token in ["ai", "nvda", "aapl", "pltr"]):
        topics.extend(SECTOR_TOPIC_KEYWORDS["ai"])
    return _unique_texts(topics)


def matched_terms(text: str, terms: Iterable[str]) -> List[str]:
    lowered = _lower_text(text)
    return [term for term in _unique_texts(terms) if _lower_text(term) and _lower_text(term) in lowered]


def source_trust_state(source: object, provider: object = "") -> str:
    return news_domain.source_trust_state_for_source(source, provider)


def classify_news_relevance(
    target: NewsCollectionTarget,
    title: object,
    summary: object = "",
    source: object = "",
    provider: object = "",
) -> Dict[str, object]:
    return news_domain.classify_news_relevance(target, title, summary, source, provider)


@dataclass
class ActiveInvestmentOpinion:
    symbol: str
    action: str
    thesis: str
    review_level: str = "check"
    data_state: str = "partial"
    validation_state: str = "conditional"
    conflict_state: str = "context-only"
    time_horizon: str = "days"
    evidence: List[ResearchEvidence] = field(default_factory=list)
    counter_evidence: List[ResearchEvidence] = field(default_factory=list)
    missing_data: List[Dict[str, object]] = field(default_factory=list)
    invalidation_condition: str = ""
    next_check: str = ""
    execution_plan: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "engineVersion": ACTIVE_INVESTMENT_OPINION_VERSION,
            "symbol": self.symbol,
            "action": self.action,
            "actionLabel": ACTION_LABELS.get(self.action, self.action),
            "reviewLevel": self.review_level,
            "reviewLevelLabel": REVIEW_LEVEL_LABELS.get(self.review_level, REVIEW_LEVEL_LABELS["check"]),
            "dataState": self.data_state,
            "dataStateLabel": DATA_STATE_LABELS.get(self.data_state, DATA_STATE_LABELS["partial"]),
            "validationState": self.validation_state,
            "validationStateLabel": VALIDATION_STATE_LABELS.get(self.validation_state, VALIDATION_STATE_LABELS["conditional"]),
            "conflictState": self.conflict_state,
            "timeHorizon": self.time_horizon,
            "thesis": self.thesis,
            "evidence": [item.to_dict() for item in self.evidence],
            "counterEvidence": [item.to_dict() for item in self.counter_evidence],
            "missingData": list(self.missing_data or []),
            "invalidationCondition": self.invalidation_condition,
            "nextCheck": self.next_check,
            "executionPlan": dict(self.execution_plan or {}),
            "sourceUrls": source_urls([*self.evidence, *self.counter_evidence]),
            "promptContract": {
                "requiredDecision": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
                "decisionRole": "investment_opinion_not_order",
                "mustInclude": ["reviewLevel", "dataState", "evidence", "counterEvidence", "invalidationCondition", "sourceUrls"],
                "guardrails": [
                    "제공된 가격, 수급, 뉴스, 공시, SEC/OpenDART 자료만 사용합니다.",
                    "하나의 행동 의견은 반드시 선택하되 자동 주문 지시로 표현하지 않습니다.",
                    "반대 근거와 무효화 조건을 함께 표시합니다.",
                ],
            },
        }


def compact_text(value: object, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def stable_evidence_token(*values: object, length: int = 16) -> str:
    raw = "|".join(str(value or "").strip() for value in values if str(value or "").strip())
    if not raw:
        raw = "empty"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:max(8, min(40, int(length or 16)))]


def research_evidence_from_payload(payload: Dict[str, object], fallback_symbol: str = "") -> ResearchEvidence:
    source_payload = payload if isinstance(payload, dict) else {}
    symbol = str(source_payload.get("symbol") or fallback_symbol or "").upper().strip()
    kind = str(source_payload.get("kind") or "news").strip() or "news"
    source = str(source_payload.get("source") or source_payload.get("domain") or source_payload.get("provider") or "Research").strip()
    title = str(source_payload.get("title") or "").strip()
    url = str(source_payload.get("url") or "").strip()
    evidence_id = str(source_payload.get("evidenceId") or source_payload.get("evidence_id") or "").strip()
    if not evidence_id:
        evidence_id = "research:" + symbol + ":" + kind + ":" + stable_evidence_token(source, title, url)
    raw_payload = dict(source_payload.get("payload") or source_payload.get("rawPayload") or source_payload.get("raw_payload") or {})
    for key in [
        "relevanceScore",
        "relationScope",
        "matchedAliases",
        "mentionedPeers",
        "topicTags",
        "marketTopics",
        "sourceReliability",
        "directMention",
        "eventType",
        "materialityScore",
        "materialityPassed",
        "ontologyRelations",
        "excludedReason",
        "analysisSummary",
        "analysisVersion",
        "articleSummaryKo",
        "articleReadStatus",
        "articleTextPreview",
        "articleDigestVersion",
        "stockImpact",
        "stockImpactLabel",
        "stockImpactPolarity",
        "stockImpactScore",
        "stockImpactReasonKo",
        "aiAnalysis",
        "articleAiAnalysisVersion",
        "analysisConflict",
        "analysisConflictSource",
        "analysisConflictExistingPolarity",
        "analysisConflictAiPolarity",
        "analysisConflictReasonKo",
        "dataQualityRisk",
        "dataQualityRiskScore",
        "normalizedTitle",
        "normalizedSummary",
        "sourceKind",
        "sourcePlatform",
        "entityLinks",
        "qualityGate",
    ]:
        if key in source_payload and key not in raw_payload:
            raw_payload[key] = source_payload.get(key)
    if kind == "news" and title and news_domain.analysis_payload_requires_refresh(raw_payload):
        target = NewsCollectionTarget(
            symbol,
            str(source_payload.get("name") or source_payload.get("companyName") or symbol).strip(),
            str(source_payload.get("market") or "").strip(),
            str(source_payload.get("currency") or "").strip(),
            str(source_payload.get("sector") or "").strip(),
        )
        analysis = classify_news_relevance(target, title, source_payload.get("summary") or title, source, source_payload.get("provider") or raw_payload.get("provider") or "")
        for key, value in analysis.items():
            raw_payload[key] = value
    polarity = str(source_payload.get("polarity") or "").strip()
    if kind == "news" and not polarity:
        polarity = keyword_polarity(title + " " + str(source_payload.get("summary") or ""))
    summary_value = (
        source_payload.get("articleSummaryKo")
        or raw_payload.get("articleSummaryKo")
        or source_payload.get("summary")
        or title
    )
    raw_payload = news_domain.public_news_payload(raw_payload)
    states = news_domain.news_state_payload(raw_payload)
    return ResearchEvidence(
        evidence_id=evidence_id,
        symbol=symbol,
        kind=kind,
        source=source or "Research",
        title=title,
        summary=compact_text(summary_value, 520),
        url=url,
        observed_at=str(source_payload.get("observedAt") or source_payload.get("observed_at") or source_payload.get("seenDate") or ""),
        polarity=polarity or "context",
        published_at=str(source_payload.get("publishedAt") or source_payload.get("published_at") or source_payload.get("seenDate") or ""),
        raw_payload=raw_payload,
        source_trust_state=states["sourceTrustState"],
        materiality_state=states["materialityState"],
        data_state=states["dataState"],
        validation_state=states["validationState"],
    )


def keyword_polarity(text: object) -> str:
    return news_domain.keyword_polarity(text)


def source_urls(items: Iterable[ResearchEvidence]) -> List[str]:
    urls: List[str] = []
    seen = set()
    for item in items or []:
        url = str(getattr(item, "url", "") or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls[:8]


def news_target_from_facts(symbol: str, facts: Dict[str, object]) -> NewsCollectionTarget:
    facts = facts if isinstance(facts, dict) else {}
    normalized_symbol = str(symbol or facts.get("symbol") or "").upper().strip()
    return NewsCollectionTarget(
        normalized_symbol,
        str(facts.get("name") or normalized_symbol).strip(),
        str(facts.get("market") or "").strip(),
        str(facts.get("currency") or "").strip(),
        str(facts.get("sector") or "").strip(),
    )


def compact_amount(value: object, currency: str = "USD") -> str:
    amount = number(value)
    if not amount:
        return "-"
    abs_amount = abs(amount)
    prefix = "$" if str(currency or "").upper() == "USD" else ""
    if abs_amount >= 1_000_000_000:
        return prefix + str(round(amount / 1_000_000_000, 1)).rstrip("0").rstrip(".") + "B"
    if abs_amount >= 1_000_000:
        return prefix + str(round(amount / 1_000_000, 1)).rstrip("0").rstrip(".") + "M"
    return prefix + str(round(amount, 1)).rstrip("0").rstrip(".")


def sec_filing_url(cik: object, accession: object, primary_document: object) -> str:
    cik_text = "".join(ch for ch in str(cik or "") if ch.isdigit()).lstrip("0")
    accession_text = str(accession or "").replace("-", "").strip()
    document = str(primary_document or "").strip()
    if not cik_text or not accession_text or not document:
        return ""
    return "https://www.sec.gov/Archives/edgar/data/" + cik_text + "/" + accession_text + "/" + document


def sec_research_evidence(symbol: str, sec: Dict[str, object]) -> List[ResearchEvidence]:
    if not isinstance(sec, dict) or not sec:
        return []
    normalized_symbol = str(symbol or sec.get("symbol") or "").upper()
    company_name = str(sec.get("companyName") or sec.get("entityName") or normalized_symbol).strip()
    latest = sec.get("latestFiling") if isinstance(sec.get("latestFiling"), dict) else {}
    evidence: List[ResearchEvidence] = []
    if latest:
        form = str(latest.get("form") or "SEC filing").strip()
        filing_date = str(latest.get("filingDate") or latest.get("filed") or "").strip()
        url = str(latest.get("url") or "").strip() or sec_filing_url(sec.get("cik"), latest.get("accessionNumber"), latest.get("primaryDocument"))
        polarity = keyword_polarity(form + " " + company_name)
        evidence.append(ResearchEvidence(
            evidence_id="research:" + normalized_symbol + ":sec:" + (str(latest.get("accessionNumber") or form)),
            symbol=normalized_symbol,
            kind="filing",
            source=str(sec.get("provider") or "SEC EDGAR"),
            title=form,
            summary=(company_name + ", 제출일 " + (filing_date or "-")).strip(", "),
            url=url,
            observed_at=filing_date,
            polarity=polarity,
            published_at=filing_date,
            raw_payload={
                "relationScope": "direct",
                "eventType": "capital_policy",
                "sourceTrustState": "trusted",
                "materialityState": "material",
                "dataState": "sufficient",
                "validationState": "ready",
            },
        ))
    facts = sec.get("facts") if isinstance(sec.get("facts"), dict) else {}
    financial_rows = []
    for key, label in [
        ("revenue", "매출"),
        ("netIncome", "순이익"),
        ("assets", "자산"),
        ("liabilities", "부채"),
        ("equity", "자본"),
    ]:
        item = facts.get(key) if isinstance(facts.get(key), dict) else {}
        if item.get("value") in (None, ""):
            continue
        financial_rows.append(label + " " + compact_amount(item.get("value")))
    if financial_rows:
        net_income = facts.get("netIncome") if isinstance(facts.get("netIncome"), dict) else {}
        polarity = "risk" if number(net_income.get("value")) < 0 else "context"
        latest_end = next(
            (
                str(item.get("end") or "").strip()
                for item in facts.values()
                if isinstance(item, dict) and str(item.get("end") or "").strip()
            ),
            "",
        )
        evidence.append(ResearchEvidence(
            evidence_id="research:" + normalized_symbol + ":financial-facts",
            symbol=normalized_symbol,
            kind="financial-fact",
            source=str(sec.get("provider") or "SEC EDGAR"),
            title="회사 재무 요약",
            summary=company_name + ": " + ", ".join(financial_rows[:5]),
            observed_at=latest_end,
            polarity=polarity,
            published_at=latest_end,
            raw_payload={
                "relationScope": "direct",
                "eventType": "earnings",
                "sourceTrustState": "trusted",
                "materialityState": "notable",
                "dataState": "sufficient",
                "validationState": "ready",
            },
        ))
    return evidence


def research_evidence_from_facts(symbol: str, facts: Dict[str, object]) -> List[ResearchEvidence]:
    facts = facts or {}
    normalized_symbol = str(symbol or facts.get("symbol") or "").upper()
    target = news_target_from_facts(normalized_symbol, facts)
    evidence: List[ResearchEvidence] = []
    disclosure = facts.get("dartDisclosure") if isinstance(facts.get("dartDisclosure"), dict) else {}
    if disclosure:
        report = str(disclosure.get("reportName") or disclosure.get("report_name") or "OpenDART 공시").strip()
        polarity = keyword_polarity(report)
        receipt_no = str(disclosure.get("receiptNo") or disclosure.get("receipt_no") or "")
        evidence.append(ResearchEvidence(
            evidence_id="research:" + normalized_symbol + ":dart:" + (receipt_no or report),
            symbol=normalized_symbol,
            kind="disclosure",
            source=str(disclosure.get("provider") or "OpenDART"),
            title=report,
            summary="접수일 " + str(disclosure.get("receiptDate") or disclosure.get("receipt_date") or "-"),
            url=opendart_url(receipt_no),
            observed_at=str(disclosure.get("receiptDate") or disclosure.get("receipt_date") or ""),
            polarity=polarity,
            published_at=str(disclosure.get("receiptDate") or disclosure.get("receipt_date") or ""),
            raw_payload={
                "relationScope": "direct",
                "eventType": "capital_policy",
                "sourceTrustState": "trusted",
                "materialityState": "material",
                "dataState": "sufficient",
                "validationState": "ready",
            },
        ))
    news = facts.get("newsHeadlines") if isinstance(facts.get("newsHeadlines"), dict) else {}
    for item in (news.get("items") if isinstance(news.get("items"), list) else []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        polarity = keyword_polarity(title)
        url = str(item.get("url") or "").strip()
        source = str(item.get("domain") or item.get("source") or news.get("provider") or "GDELT").strip()
        summary = compact_text(item.get("articleSummaryKo") or item.get("summary") or title, 520)
        raw_payload = dict(item.get("payload") or item.get("rawPayload") or {})
        for key in [
            "articleSummaryKo",
            "articleReadStatus",
            "articleTextPreview",
            "articleDigestVersion",
            "stockImpact",
            "stockImpactLabel",
            "stockImpactPolarity",
            "stockImpactScore",
            "stockImpactReasonKo",
            "aiAnalysis",
            "articleAiAnalysisVersion",
        ]:
            if key in item and key not in raw_payload:
                raw_payload[key] = item.get(key)
        if news_domain.analysis_payload_requires_refresh(raw_payload):
            raw_payload.update(classify_news_relevance(
                target,
                title,
                summary,
                source,
                item.get("provider") or news.get("provider") or "",
            ))
        if not news_domain.relation_scope_is_investable(raw_payload.get("relationScope")):
            continue
        evidence.append(ResearchEvidence(
            evidence_id="research:" + normalized_symbol + ":news:" + stable_evidence_token(source, title, url, item.get("seenDate") or item.get("seendate")),
            symbol=normalized_symbol,
            kind="news",
            source=source,
            title=title,
            summary=summary,
            url=url,
            observed_at=str(item.get("seenDate") or item.get("seendate") or ""),
            polarity=polarity,
            published_at=str(item.get("publishedAt") or item.get("seenDate") or item.get("seendate") or ""),
            raw_payload=raw_payload,
        ))
    sec = facts.get("secFiling") if isinstance(facts.get("secFiling"), dict) else {}
    evidence.extend(sec_research_evidence(normalized_symbol, sec))
    return evidence


def research_evidence_from_external_signals(symbol: str, external_signals: Dict[str, object]) -> List[ResearchEvidence]:
    external_signals = external_signals or {}
    normalized_symbol = str(symbol or "").upper()
    facts = {
        "symbol": normalized_symbol,
        "dartDisclosure": ((external_signals.get("dartDisclosures") or {}).get(normalized_symbol) or {}) if isinstance(external_signals.get("dartDisclosures"), dict) else {},
        "newsHeadlines": ((external_signals.get("newsHeadlines") or {}).get(normalized_symbol) or {}) if isinstance(external_signals.get("newsHeadlines"), dict) else {},
    }
    evidence = research_evidence_from_facts(normalized_symbol, facts)
    sec = (external_signals.get("secFilings") or {}).get(normalized_symbol) if isinstance(external_signals.get("secFilings"), dict) else {}
    evidence.extend(sec_research_evidence(normalized_symbol, sec if isinstance(sec, dict) else {}))
    stored_group = external_signals.get("researchEvidence") if isinstance(external_signals.get("researchEvidence"), dict) else {}
    stored_items = stored_group.get(normalized_symbol) if isinstance(stored_group.get(normalized_symbol), list) else []
    for item in stored_items:
        if isinstance(item, dict) and item.get("title"):
            parsed = research_evidence_from_payload(item, normalized_symbol)
            payload = parsed.raw_payload if isinstance(parsed.raw_payload, dict) else {}
            if parsed.kind == "news" and not news_domain.relation_scope_is_investable(payload.get("relationScope")):
                continue
            evidence.append(parsed)
    quote = (external_signals.get("equityQuotes") or {}).get(normalized_symbol) if isinstance(external_signals.get("equityQuotes"), dict) else {}
    if isinstance(quote, dict) and quote:
        change = number(quote.get("changePercent"))
        if abs(change) >= 2.0:
            polarity = "support" if change > 0 else "risk"
            evidence.append(ResearchEvidence(
                evidence_id="research:" + normalized_symbol + ":quote",
                symbol=normalized_symbol,
                kind="market-move",
                source=str(quote.get("provider") or "market-data"),
                title="가격 변동 " + signed_pct(change),
                summary="현재가 " + str(quote.get("price") or "-") + ", 거래량 " + str(quote.get("volume") or "-"),
                observed_at=str(quote.get("latestTradingDay") or ""),
                polarity=polarity,
                published_at=str(quote.get("latestTradingDay") or ""),
                raw_payload={
                    "relationScope": "direct",
                    "eventType": "price_commentary",
                    "sourceTrustState": "standard",
                    "materialityState": "notable",
                    "dataState": "partial",
                    "validationState": "conditional",
                },
            ))
    yfinance_group = external_signals.get("yfinanceData") if isinstance(external_signals.get("yfinanceData"), dict) else {}
    yfinance_data = yfinance_group.get(normalized_symbol) if isinstance(yfinance_group.get(normalized_symbol), dict) else {}
    if yfinance_data:
        modules = [str(item) for item in yfinance_data.get("modulesCollected") or [] if str(item or "").strip()]
        quote_payload = yfinance_data.get("quote") if isinstance(yfinance_data.get("quote"), dict) else {}
        options = yfinance_data.get("optionChains") if isinstance(yfinance_data.get("optionChains"), list) else []
        option_summary = options[0].get("summary") if options and isinstance(options[0], dict) and isinstance(options[0].get("summary"), dict) else {}
        info = yfinance_data.get("info") if isinstance(yfinance_data.get("info"), dict) else {}
        freshness = yfinance_data.get("freshness") if isinstance(yfinance_data.get("freshness"), dict) else {}
        freshness_status = str(freshness.get("status") or "unknown")
        stale_modules = [str(item) for item in freshness.get("staleModules") or [] if str(item or "").strip()]
        title = "yfinance 종합 데이터"
        summary = compact_text(
            ", ".join([
                "모듈 " + str(len(modules)) + "개",
                "현재가 " + str(quote_payload.get("price") or info.get("currentPrice") or "-"),
                "옵션만기 " + str(len(yfinance_data.get("options") or [])) + "개",
                "put/call OI " + str(round(number(option_summary.get("putCallOpenInterestRatio")), 2)) if option_summary else "",
                "신선도 " + freshness_status,
            ]),
            360,
        )
        evidence.append(ResearchEvidence(
            evidence_id="research:" + normalized_symbol + ":yfinance",
            symbol=normalized_symbol,
            kind="financial-fact",
            source="yfinance",
            title=title,
            summary=summary,
            observed_at=str(yfinance_data.get("collectedAt") or ""),
            polarity="context",
            published_at=str(yfinance_data.get("collectedAt") or ""),
            raw_payload={
                "provider": "yfinance",
                "sourceKind": "unofficial-yahoo-finance-wrapper",
                "querySymbol": str(yfinance_data.get("querySymbol") or ""),
                "modulesCollected": modules,
                "quote": quote_payload,
                "analystPriceTargets": yfinance_data.get("analystPriceTargets") if isinstance(yfinance_data.get("analystPriceTargets"), dict) else {},
                "calendar": yfinance_data.get("calendar") if isinstance(yfinance_data.get("calendar"), dict) else {},
                "optionSummary": option_summary,
                "freshness": freshness,
                "moduleFreshness": yfinance_data.get("moduleFreshness") if isinstance(yfinance_data.get("moduleFreshness"), dict) else {},
                "statementMetricCounts": {
                    "incomeStatement": len(yfinance_data.get("incomeStatement") or []),
                    "balanceSheet": len(yfinance_data.get("balanceSheet") or []),
                    "cashFlow": len(yfinance_data.get("cashFlow") or []),
                },
                "sourceTrustState": "limited" if freshness_status == "stale" else "standard",
                "dataQualityRisk": "stale-yfinance-modules:" + ",".join(stale_modules[:5]) if stale_modules else "unofficial-yahoo-finance-wrapper",
                "materialityState": "context",
                "dataState": "partial" if freshness_status in {"stale", "unknown"} else "sufficient",
                "validationState": "conditional",
            },
        ))
    return evidence


def opendart_url(receipt_no: str) -> str:
    value = str(receipt_no or "").strip()
    if not value:
        return ""
    return "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + value


def signed_pct(value: float) -> str:
    rounded = round(number(value), 1)
    return ("+" if rounded > 0 else "") + str(rounded) + "%"


def relation_rule_items(relation_context: Dict[str, object]) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    seen = set()
    for key in ["activeRules", "matchedRules", "rules"]:
        for item in relation_context.get(key) or []:
            if not isinstance(item, dict):
                continue
            identity = (
                str(item.get("ruleId") or item.get("rule_id") or ""),
                str(item.get("relationType") or item.get("relation_type") or ""),
                str(item.get("label") or ""),
            )
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(item)
    return rows


def active_rule_labels(relation_context: Dict[str, object]) -> List[str]:
    labels: List[str] = []
    for item in relation_rule_items(relation_context):
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "").strip()
        if label:
            labels.append(label)
    return labels


def evidence_roles(evidence: List[ResearchEvidence], relation_context: Dict[str, object]) -> List[str]:
    roles: List[str] = []
    for item in evidence:
        if item.polarity in {"risk", "contradiction"}:
            roles.append("risk")
        elif item.polarity == "support":
            roles.append("support")
        else:
            roles.append("context")
    for item in relation_rule_items(relation_context):
        if not isinstance(item, dict):
            continue
        role = str(item.get("evidenceRole") or item.get("evidence_role") or "").strip().lower()
        if role in {"risk", "support", "counter", "context", "blocking"}:
            roles.append(role)
    return roles or ["context"]


def has_add_buy_candidate(relation_context: Dict[str, object]) -> bool:
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    if str(decision.get("actionGroup") or "") == "addBuy" or str(decision.get("decisionStage") or "") == "ADD_BUY_REVIEW":
        return True
    for item in relation_rule_items(relation_context):
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("ruleId") or item.get("rule_id") or "")
        relation_type = str(item.get("relationType") or item.get("relation_type") or "").upper()
        tbox_class = str(item.get("tboxClass") or item.get("tbox_class") or "")
        tbox_classes = [
            str(value or "")
            for value in (
                item.get("tboxClasses")
                or item.get("tbox_classes")
                or item.get("classes")
                or []
            )
        ]
        if relation_type == "ALLOWS_ACTION" or tbox_class == "AddBuyEligibility" or "AddBuyEligibility" in tbox_classes or "add_buy" in rule_id or "add-buy" in rule_id:
            return True
    return False


def choose_action(position: Position, relation_context: Dict[str, object], conflict_state: str = "context-only") -> str:
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    action_group = str(decision.get("actionGroup") or "")
    action_level = str(decision.get("actionLevel") or "")
    decision_stage = str(decision.get("decisionStage") or "")
    decision_state = relation_context.get("decisionState") if isinstance(relation_context.get("decisionState"), dict) else {}
    data_state = str(decision_state.get("dataState") or decision.get("dataState") or relation_context.get("dataState") or "partial")
    is_watchlist = str(position.source or "") == "watchlist"
    if data_state in {"unavailable", "insufficient"}:
        return "AVOID" if is_watchlist else "HOLD"
    if is_watchlist:
        if action_group in {"entryRisk", "entryWait", "lossControl", "dataQuality", "rateRegime", "fxRegime", "macroRegime"}:
            return "AVOID"
        if action_group == "entry" and decision_stage in {"ENTRY_READY", "ENTRY_SPLIT_BUY"} and conflict_state == "support-only":
            return "BUY"
        return "AVOID"
    if action_group == "addBuy" or decision_stage == "ADD_BUY_REVIEW" or has_add_buy_candidate(relation_context):
        execution_plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
        add_buy_assessment = execution_plan.get("addBuyAssessment") if isinstance(execution_plan.get("addBuyAssessment"), dict) else {}
        blocked_reasons = list(add_buy_assessment.get("blockedReasons") or [])
        if not blocked_reasons and conflict_state == "support-only" and data_state == "sufficient":
            return "ADD"
        return "HOLD"
    if decision_stage == "LOSS_CUT":
        return "SELL"
    if action_group == "lossControl" or decision_stage in {"BREAKDOWN_ACCELERATION", "SUPPORT_RETEST_FAILED"}:
        return "SELL" if action_level == "urgent" and conflict_state == "risk-only" else "TRIM"
    if action_group in {"profitTake", "rebalance"}:
        return "TRIM"
    if action_group in {"eventRisk", "disclosure"}:
        return "HOLD"
    if action_group in {"executionRisk", "dataQuality", "factorRisk", "rateRegime", "fxRegime", "macroRegime"}:
        return "HOLD"
    if action_level == "urgent" and conflict_state == "risk-only":
        return "TRIM"
    if action_group == "entryRisk" or conflict_state in {"risk-only", "mixed"}:
        return "HOLD"
    return "HOLD"


def thesis_for_action(action: str, position: Position, labels: List[str]) -> str:
    rule_text = " · ".join(labels[:3]) if labels else "가격·수급·외부 리서치"
    name = str(position.name or position.symbol or "대상")
    if action == "BUY":
        return name + "는 " + rule_text + " 근거가 진입 쪽으로 우세해 매수 의견입니다."
    if action == "ADD":
        return name + "는 지지 근거가 리스크보다 커 추가매수 의견입니다."
    if action == "TRIM":
        return name + "는 " + rule_text + " 기준에서 리스크 관리가 우선이라 분할매도 의견입니다."
    if action == "SELL":
        return name + "는 손실 방어 조건이 성립했고 즉시 확인 단계라 매도 의견입니다."
    if action == "AVOID":
        return name + "는 진입 전 확인할 리스크가 커 매수보류 의견입니다."
    return name + "는 바로 사고팔기보다 보유 이유와 반대 신호를 한 번 더 확인하는 단계입니다."


def invalidation_for_action(action: str) -> str:
    if action in {"BUY", "ADD"}:
        return "가격·수급 지지 신호가 꺾이거나 부정 뉴스/공시가 추가되면 매수 의견을 무효화합니다."
    if action in {"TRIM", "SELL"}:
        return "20일선 회복, 거래량 동반 반등, 부정 공시 해소가 확인되면 매도 강도를 낮춥니다."
    if action == "AVOID":
        return "뉴스·공시 반대 근거가 해소되고 진입 관계 규칙이 재성립하면 재검토합니다."
    return "새 뉴스·공시가 들어오거나 확인 단계가 바뀌면 보유 의견을 재검토합니다."


def next_check_for_action(action: str, evidence: List[ResearchEvidence]) -> str:
    if any(item.kind == "disclosure" for item in evidence):
        return "공시 원문, 접수번호, 장중 거래량 반응을 먼저 확인하세요."
    if any(item.kind == "news" for item in evidence):
        return "뉴스 출처와 후속 보도, 가격·수급 동조 여부를 확인하세요."
    if action in {"TRIM", "SELL"}:
        return "매도 가능 수량, 손실 기준, 다음 조회에서도 같은 위험 조건이 유지되는지 확인하세요."
    if action in {"BUY", "ADD"}:
        return "진입 가격, 손절 기준, 20일선·거래량 확인 조건을 함께 정하세요."
    return "다음 데이터 업데이트에서 같은 관계 규칙과 반대 근거를 다시 확인하세요."


def missing_data_rows(relation_context: Dict[str, object]) -> List[Dict[str, object]]:
    rows = relation_context.get("missingData") if isinstance(relation_context, dict) else []
    return [dict(item) if isinstance(item, dict) else {"label": str(item), "effect": ""} for item in rows or []]


def build_active_investment_opinion(
    position: Position,
    relation_context: Dict[str, object] = None,
    ontology_opinion: Dict[str, object] = None,
    legacy_model: Dict[str, object] = None,
    external_signals: Dict[str, object] = None,
) -> ActiveInvestmentOpinion:
    relation_context = relation_context or {}
    ontology_opinion = ontology_opinion or {}
    legacy_model = legacy_model or {}
    external_signals = external_signals or {}
    symbol = str(position.symbol or "").upper()
    facts = relation_context.get("facts") if isinstance(relation_context.get("facts"), dict) else {}
    evidence_by_key: Dict[str, ResearchEvidence] = {}
    for item in research_evidence_from_facts(symbol, facts) + research_evidence_from_external_signals(symbol, external_signals):
        evidence_by_key[item.evidence_id] = item
    evidence = list(evidence_by_key.values())
    execution_plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    roles = evidence_roles(evidence, relation_context)
    conflict_state = str((relation_context.get("decisionState") or {}).get("conflictState") or conflict_state_from_roles(roles))
    action = choose_action(position, relation_context, conflict_state)
    support_evidence = [item for item in evidence if item.polarity == "support"]
    risk_evidence = [item for item in evidence if item.polarity in {"risk", "contradiction"}]
    context_evidence = [item for item in evidence if item.polarity == "context"]
    primary = support_evidence if action in {"BUY", "ADD"} else risk_evidence if action in {"TRIM", "SELL", "AVOID"} else context_evidence + support_evidence
    counter = risk_evidence if action in {"BUY", "ADD", "HOLD"} else support_evidence
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    decision_state = relation_context.get("decisionState") if isinstance(relation_context.get("decisionState"), dict) else {}
    review_level = str(decision_state.get("reviewLevel") or decision.get("reviewLevel") or "check")
    data_state = str(decision_state.get("dataState") or decision.get("dataState") or "partial")
    graph_backed = bool(relation_context.get("graphStoreUsed") or relation_context.get("inferenceGenerationId"))
    validation_state = "blocked" if not graph_backed or data_state in {"unavailable", "insufficient"} else "conditional" if data_state == "partial" or conflict_state == "mixed" else "ready"
    labels = active_rule_labels(relation_context)
    invalidation = invalidation_for_action(action)
    if execution_plan.get("weakenConditions"):
        invalidation = " / ".join(str(item) for item in list(execution_plan.get("weakenConditions") or [])[:2])
    next_check = next_check_for_action(action, evidence)
    if execution_plan.get("nextChecks"):
        next_check = " / ".join(str(item) for item in list(execution_plan.get("nextChecks") or [])[:2])
    return ActiveInvestmentOpinion(
        symbol=symbol,
        action=action,
        thesis=thesis_for_action(action, position, labels),
        review_level=review_level,
        data_state=data_state,
        validation_state=validation_state,
        conflict_state=conflict_state,
        evidence=primary[:5],
        counter_evidence=counter[:5],
        missing_data=missing_data_rows(relation_context)[:8],
        invalidation_condition=invalidation,
        next_check=next_check,
        execution_plan=dict(execution_plan or {}),
    )
