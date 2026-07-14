import hashlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .market_data import clamp, number
from . import news_analysis as news_domain
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


@dataclass
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
    impact_score: float = 0.0
    confidence: float = 0.55
    published_at: str = ""
    raw_payload: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        payload = dict(self.raw_payload or {})
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
            "impactScore": round(number(self.impact_score), 1),
            "confidence": round(number(self.confidence), 2),
            "relevanceScore": round(number(payload.get("relevanceScore")), 1),
            "relationScope": str(payload.get("relationScope") or ""),
            "sourceReliability": round(number(payload.get("sourceReliability")), 2),
            "eventType": str(payload.get("eventType") or ""),
            "materialityScore": round(number(payload.get("materialityScore")), 1),
            "ontologyRelations": list(payload.get("ontologyRelations") or []),
            "excludedReason": str(payload.get("excludedReason") or ""),
            "analysisSummary": str(payload.get("analysisSummary") or ""),
            "articleSummaryKo": str(payload.get("articleSummaryKo") or ""),
            "articleReadStatus": str(payload.get("articleReadStatus") or ""),
            "articleFacts": dict(payload.get("articleFacts") or {}),
            "stockImpact": str(payload.get("stockImpact") or ""),
            "stockImpactLabel": str(payload.get("stockImpactLabel") or ""),
            "stockImpactPolarity": str(payload.get("stockImpactPolarity") or ""),
            "stockImpactScore": round(number(payload.get("stockImpactScore")), 1),
            "stockImpactReasonKo": str(payload.get("stockImpactReasonKo") or ""),
            "aiAnalysis": dict(payload.get("aiAnalysis") or {}),
            "articleAiAnalysisVersion": str(payload.get("articleAiAnalysisVersion") or ""),
            "analysisConflict": bool(payload.get("analysisConflict")),
            "analysisConflictSource": str(payload.get("analysisConflictSource") or ""),
            "analysisConflictExistingPolarity": str(payload.get("analysisConflictExistingPolarity") or ""),
            "analysisConflictAiPolarity": str(payload.get("analysisConflictAiPolarity") or ""),
            "analysisConflictReasonKo": str(payload.get("analysisConflictReasonKo") or ""),
            "dataQualityRisk": str(payload.get("dataQualityRisk") or ""),
            "dataQualityRiskScore": round(number(payload.get("dataQualityRiskScore")), 1),
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


def source_reliability_score(source: object, provider: object = "") -> float:
    return news_domain.source_reliability_score(source, provider)


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
    conviction: float
    thesis: str
    time_horizon: str = "days"
    evidence: List[ResearchEvidence] = field(default_factory=list)
    counter_evidence: List[ResearchEvidence] = field(default_factory=list)
    missing_data: List[Dict[str, object]] = field(default_factory=list)
    invalidation_condition: str = ""
    next_check: str = ""
    score_breakdown: Dict[str, object] = field(default_factory=dict)
    execution_plan: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "engineVersion": ACTIVE_INVESTMENT_OPINION_VERSION,
            "symbol": self.symbol,
            "action": self.action,
            "actionLabel": ACTION_LABELS.get(self.action, self.action),
            "conviction": round(number(self.conviction), 1),
            "timeHorizon": self.time_horizon,
            "thesis": self.thesis,
            "evidence": [item.to_dict() for item in self.evidence],
            "counterEvidence": [item.to_dict() for item in self.counter_evidence],
            "missingData": list(self.missing_data or []),
            "invalidationCondition": self.invalidation_condition,
            "nextCheck": self.next_check,
            "executionPlan": dict(self.execution_plan or {}),
            "sourceUrls": source_urls([*self.evidence, *self.counter_evidence]),
            "scoreBreakdown": dict(self.score_breakdown or {}),
            "promptContract": {
                "requiredDecision": "BUY|ADD|HOLD|TRIM|SELL|AVOID",
                "decisionRole": "investment_opinion_not_order",
                "mustInclude": ["conviction", "evidence", "counterEvidence", "invalidationCondition", "sourceUrls"],
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
        polarity, _unused_impact = keyword_polarity(title + " " + str(source_payload.get("summary") or ""))
    confidence = number(source_payload.get("confidence")) or (news_domain.confidence_from_analysis_payload(raw_payload) if kind == "news" else 0.55)
    impact_score = number(source_payload.get("impactScore") or source_payload.get("impact_score"))
    if kind == "news" and not impact_score:
        _polarity, base_impact = keyword_polarity(title + " " + str(source_payload.get("summary") or ""))
        impact_score = news_domain.impact_from_analysis_payload(base_impact, raw_payload)
    summary_value = (
        source_payload.get("articleSummaryKo")
        or raw_payload.get("articleSummaryKo")
        or source_payload.get("summary")
        or title
    )
    return ResearchEvidence(
        evidence_id,
        symbol,
        kind,
        source or "Research",
        title,
        compact_text(summary_value, 520),
        url,
        str(source_payload.get("observedAt") or source_payload.get("observed_at") or source_payload.get("seenDate") or ""),
        polarity or "context",
        impact_score,
        confidence,
        str(source_payload.get("publishedAt") or source_payload.get("published_at") or source_payload.get("seenDate") or ""),
        raw_payload,
    )


def keyword_polarity(text: object) -> Tuple[str, float]:
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
        polarity, impact = keyword_polarity(form + " " + company_name)
        evidence.append(ResearchEvidence(
            "research:" + normalized_symbol + ":sec:" + (str(latest.get("accessionNumber") or form)),
            normalized_symbol,
            "filing",
            str(sec.get("provider") or "SEC EDGAR"),
            form,
            (company_name + ", 제출일 " + (filing_date or "-")).strip(", "),
            url,
            filing_date,
            polarity,
            impact,
            0.72,
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
        impact = 7.0 if polarity == "risk" else 4.0
        latest_end = next(
            (
                str(item.get("end") or "").strip()
                for item in facts.values()
                if isinstance(item, dict) and str(item.get("end") or "").strip()
            ),
            "",
        )
        evidence.append(ResearchEvidence(
            "research:" + normalized_symbol + ":financial-facts",
            normalized_symbol,
            "financial-fact",
            str(sec.get("provider") or "SEC EDGAR"),
            "회사 재무 요약",
            company_name + ": " + ", ".join(financial_rows[:5]),
            "",
            latest_end,
            polarity,
            impact,
            0.7,
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
        polarity, impact = keyword_polarity(report)
        receipt_no = str(disclosure.get("receiptNo") or disclosure.get("receipt_no") or "")
        evidence.append(ResearchEvidence(
            "research:" + normalized_symbol + ":dart:" + (receipt_no or report),
            normalized_symbol,
            "disclosure",
            str(disclosure.get("provider") or "OpenDART"),
            report,
            "접수일 " + str(disclosure.get("receiptDate") or disclosure.get("receipt_date") or "-"),
            opendart_url(receipt_no),
            str(disclosure.get("receiptDate") or disclosure.get("receipt_date") or ""),
            polarity,
            impact,
            0.78,
        ))
    news = facts.get("newsHeadlines") if isinstance(facts.get("newsHeadlines"), dict) else {}
    for item in (news.get("items") if isinstance(news.get("items"), list) else []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        polarity, impact = keyword_polarity(title)
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
        confidence = news_domain.confidence_from_analysis_payload(raw_payload)
        evidence.append(ResearchEvidence(
            "research:" + normalized_symbol + ":news:" + stable_evidence_token(source, title, url, item.get("seenDate") or item.get("seendate")),
            normalized_symbol,
            "news",
            source,
            title,
            summary,
            url,
            str(item.get("seenDate") or item.get("seendate") or ""),
            polarity,
            news_domain.impact_from_analysis_payload(impact, raw_payload),
            confidence,
            str(item.get("publishedAt") or item.get("seenDate") or item.get("seendate") or ""),
            raw_payload,
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
                "research:" + normalized_symbol + ":quote",
                normalized_symbol,
                "market-move",
                str(quote.get("provider") or "market-data"),
                "가격 변동 " + signed_pct(change),
                "현재가 " + str(quote.get("price") or "-") + ", 거래량 " + str(quote.get("volume") or "-"),
                "",
                str(quote.get("latestTradingDay") or ""),
                polarity,
                min(12.0, abs(change) * 1.5),
                0.65,
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


def active_rule_labels(relation_context: Dict[str, object]) -> List[str]:
    labels: List[str] = []
    for item in relation_context.get("activeRules") or relation_context.get("matchedRules") or []:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "").strip()
        if label:
            labels.append(label)
    return labels


def support_risk_scores(evidence: List[ResearchEvidence], relation_context: Dict[str, object]) -> Tuple[float, float]:
    def evidence_weight(item: ResearchEvidence) -> float:
        payload = item.raw_payload if isinstance(item.raw_payload, dict) else {}
        scope = str(payload.get("relationScope") or ("direct" if item.kind in {"disclosure", "filing", "market-move"} else "context")).lower()
        scope_weight = {
            "direct": 1.0,
            "peer": 0.62,
            "sector": 0.48,
            "market": 0.28,
            "context": 0.5,
        }.get(scope, 0.5)
        if not news_domain.relation_scope_is_investable(scope) and scope != "context":
            scope_weight = 0.0
        relevance = clamp(number(payload.get("relevanceScore")) / 100, 0.25, 1.0) if payload.get("relevanceScore") not in (None, "") else 0.75
        reliability = clamp(number(payload.get("sourceReliability")) or number(item.confidence) or 0.55, 0.35, 0.95)
        return round(scope_weight * (0.5 + relevance * 0.5) * (0.7 + reliability * 0.3), 4)

    support = sum(number(item.impact_score) * evidence_weight(item) for item in evidence if item.polarity == "support")
    risk = sum(number(item.impact_score) * evidence_weight(item) for item in evidence if item.polarity in {"risk", "contradiction"})
    for item in relation_context.get("activeRules") or []:
        if not isinstance(item, dict):
            continue
        score = number(item.get("strengthScore") or item.get("strength_score"))
        relation_type = str(item.get("relationType") or item.get("relation_type") or "").upper()
        label = str(item.get("label") or "")
        combined = relation_type + " " + label
        if any(token in combined for token in ["ENTRY_WAIT", "ENTRY_RISK", "LOSS", "RISK", "DISCLOSURE", "CONCENTRATION", "리스크", "손실", "매도", "하락", "대기", "보류", "차단"]):
            risk += min(22.0, score * 0.28)
            continue
        if any(token in combined for token in ["ENTRY_OPPORTUNITY", "SUPPORT", "CONFIRM", "기회", "소액 진입", "우호"]):
            support += min(18.0, score * 0.22)
    return support, risk


def choose_action(position: Position, relation_context: Dict[str, object], support_score: float, risk_score: float) -> str:
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    action_group = str(decision.get("actionGroup") or "")
    action_level = str(decision.get("actionLevel") or "")
    relation_score = number(decision.get("score") or relation_context.get("signalStrength"))
    is_watchlist = str(position.source or "") == "watchlist"
    if is_watchlist:
        if risk_score >= support_score + 8 or action_group in {"entryRisk", "entryWait", "lossControl", "dataQuality", "rateRegime", "fxRegime", "macroRegime"}:
            return "AVOID"
        if action_group == "entry" and relation_score >= 70 and support_score >= risk_score + 8:
            return "BUY"
        if action_group == "entry" and relation_score >= 55 and support_score >= risk_score + 16:
            return "BUY"
        return "AVOID"
    if action_group == "lossControl" or action_level == "urgent" or relation_score >= 85:
        return "SELL" if relation_score >= 78 or risk_score >= support_score + 18 else "TRIM"
    if action_group in {"profitTake", "rebalance"}:
        return "TRIM"
    if action_group == "entryRisk" or risk_score >= support_score + 16:
        return "HOLD"
    if support_score >= risk_score + 18 and relation_score < 55:
        return "ADD"
    return "HOLD"


def thesis_for_action(action: str, position: Position, labels: List[str], support_score: float, risk_score: float) -> str:
    rule_text = " · ".join(labels[:3]) if labels else "가격·수급·외부 리서치"
    name = str(position.name or position.symbol or "대상")
    if action == "BUY":
        return name + "는 " + rule_text + " 근거가 진입 쪽으로 우세해 매수 의견입니다."
    if action == "ADD":
        return name + "는 지지 근거가 리스크보다 커 추가매수 의견입니다."
    if action == "TRIM":
        return name + "는 " + rule_text + " 기준에서 리스크 관리가 우선이라 분할매도 의견입니다."
    if action == "SELL":
        return name + "는 리스크 점수 " + str(round(risk_score, 1)) + "가 지지 점수 " + str(round(support_score, 1)) + "보다 커 매도 의견입니다."
    if action == "AVOID":
        return name + "는 진입 전 확인할 리스크가 커 매수보류 의견입니다."
    return name + "는 적극 매수/매도보다 기존 포지션 유지와 다음 검증이 우선입니다."


def invalidation_for_action(action: str) -> str:
    if action in {"BUY", "ADD"}:
        return "가격·수급 지지 신호가 꺾이거나 부정 뉴스/공시가 추가되면 매수 의견을 무효화합니다."
    if action in {"TRIM", "SELL"}:
        return "20일선 회복, 거래량 동반 반등, 부정 공시 해소가 확인되면 매도 강도를 낮춥니다."
    if action == "AVOID":
        return "뉴스·공시 반대 근거가 해소되고 진입 관계 규칙이 재성립하면 재검토합니다."
    return "새 뉴스/공시 또는 관계 점수 급변이 나오면 보유 의견을 재검토합니다."


def next_check_for_action(action: str, evidence: List[ResearchEvidence]) -> str:
    if any(item.kind == "disclosure" for item in evidence):
        return "공시 원문, 접수번호, 장중 거래량 반응을 먼저 확인하세요."
    if any(item.kind == "news" for item in evidence):
        return "뉴스 출처와 후속 보도, 가격·수급 동조 여부를 확인하세요."
    if action in {"TRIM", "SELL"}:
        return "매도 가능 수량, 손실 기준, 다음 조회의 관계 점수 유지 여부를 확인하세요."
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
    support_score, risk_score = support_risk_scores(evidence, relation_context)
    action = choose_action(position, relation_context, support_score, risk_score)
    support_evidence = [item for item in evidence if item.polarity == "support"]
    risk_evidence = [item for item in evidence if item.polarity in {"risk", "contradiction"}]
    context_evidence = [item for item in evidence if item.polarity == "context"]
    primary = support_evidence if action in {"BUY", "ADD"} else risk_evidence if action in {"TRIM", "SELL", "AVOID"} else context_evidence + support_evidence
    counter = risk_evidence if action in {"BUY", "ADD", "HOLD"} else support_evidence
    relation_score = number((relation_context.get("decision") or {}).get("score") if isinstance(relation_context.get("decision"), dict) else relation_context.get("signalStrength"))
    conviction = clamp(48.0 + max(support_score, risk_score) * 0.45 + relation_score * 0.28 - len(missing_data_rows(relation_context)) * 4.0, 35.0, 94.0)
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
        conviction=conviction,
        thesis=thesis_for_action(action, position, labels, support_score, risk_score),
        evidence=primary[:5],
        counter_evidence=counter[:5],
        missing_data=missing_data_rows(relation_context)[:8],
        invalidation_condition=invalidation,
        next_check=next_check,
        score_breakdown={
            "supportScore": round(support_score, 1),
            "riskScore": round(risk_score, 1),
            "relationScore": round(relation_score, 1),
            "ontologyPressure": round(number(ontology_opinion.get("ontology_pressure") or ontology_opinion.get("ontologyPressure")), 1),
            "evidenceCount": len(evidence),
            "scoringBasis": "ontologyRelationRules",
        },
        execution_plan=dict(execution_plan or {}),
    )
