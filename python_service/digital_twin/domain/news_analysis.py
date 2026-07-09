from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .market_data import clamp, number


NEWS_ANALYSIS_VERSION = "news-analysis-v2-domain-ontology"

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

EVENT_TYPE_KEYWORDS = {
    "earnings": ["실적", "earnings", "revenue", "profit", "매출", "영업이익", "순이익"],
    "guidance": ["guidance", "전망", "가이던스", "목표주가", "estimate", "forecast"],
    "supply_chain": ["공급", "supply", "supplier", "생산", "fab", "foundry", "라인", "공장"],
    "product": ["launch", "출시", "roadmap", "제품", "서비스", "chip", "GPU", "AI"],
    "regulation": ["regulation", "규제", "소송", "lawsuit", "probe", "investigation", "조사", "제재"],
    "capital_policy": ["buyback", "dividend", "자사주", "배당", "증자", "offering", "dilution"],
    "listing": ["listing", "상장", "ADR", "나스닥", "IPO"],
    "macro_sector": ["금리", "환율", "inflation", "FOMC", "업황", "수요", "demand"],
    "crypto_linked": ["bitcoin", "비트코인", "crypto", "암호화폐", "digital asset"],
    "price_commentary": ["주가", "shares", "stock", "목표주가", "급등", "급락"],
}

LOW_RELIABILITY_SOURCE_TERMS = ["blog", "블로그", "cafe", "reddit", "rumor"]
HIGH_RELIABILITY_SOURCE_TERMS = ["dart", "sec", "edgar", "reuters", "bloomberg", "연합", "yonhap", "cnbc", "wsj", "marketwatch"]


@dataclass(frozen=True)
class NewsAnalysis:
    relevance_score: float
    relation_scope: str
    source_reliability: float
    event_type: str
    materiality_score: float
    ontology_relations: List[Dict[str, object]] = field(default_factory=list)
    matched_aliases: List[str] = field(default_factory=list)
    mentioned_peers: List[str] = field(default_factory=list)
    topic_tags: List[str] = field(default_factory=list)
    market_topics: List[str] = field(default_factory=list)
    direct_mention: bool = False
    excluded_reason: str = ""

    def confidence(self) -> float:
        if self.excluded_reason:
            return 0.25
        return round(clamp(
            self.relevance_score / 100 * 0.45
            + self.source_reliability * 0.35
            + self.materiality_score / 100 * 0.20,
            0.25,
            0.92,
        ), 2)

    def to_payload(self) -> Dict[str, object]:
        return {
            "analysisVersion": NEWS_ANALYSIS_VERSION,
            "relevanceScore": round(number(self.relevance_score), 1),
            "relationScope": self.relation_scope,
            "matchedAliases": list(self.matched_aliases),
            "mentionedPeers": list(self.mentioned_peers),
            "topicTags": list(self.topic_tags),
            "marketTopics": list(self.market_topics),
            "sourceReliability": round(number(self.source_reliability), 2),
            "directMention": bool(self.direct_mention),
            "eventType": self.event_type,
            "materialityScore": round(number(self.materiality_score), 1),
            "ontologyRelations": list(self.ontology_relations),
            "excludedReason": self.excluded_reason,
            "analysisSummary": self.summary(),
        }

    def summary(self) -> str:
        scope = {
            "direct": "종목 직접 뉴스",
            "peer": "비교 기업 뉴스",
            "sector": "업종 뉴스",
            "market": "시장 환경 뉴스",
            "noise": "투자 관련 낮음",
        }.get(self.relation_scope, self.relation_scope)
        event = {
            "earnings": "실적",
            "guidance": "전망",
            "supply_chain": "공급망/생산",
            "product": "제품/서비스",
            "regulation": "규제/소송",
            "capital_policy": "자본정책",
            "listing": "상장/거래시장",
            "macro_sector": "거시/업황",
            "crypto_linked": "가상자산 연동",
            "price_commentary": "주가 해설",
            "general": "일반",
        }.get(self.event_type, self.event_type)
        if self.excluded_reason:
            return scope + " · 제외 사유: " + self.excluded_reason
        return scope + " · " + event + " · 관련성 " + ("%.1f" % self.relevance_score) + "점"


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


def target_symbol(target: object) -> str:
    if hasattr(target, "normalized_symbol"):
        return str(target.normalized_symbol() or "").upper().strip()
    return str(getattr(target, "symbol", "") or "").upper().strip()


def target_aliases(target: object) -> List[str]:
    symbol = target_symbol(target)
    aliases = [getattr(target, "name", ""), symbol]
    aliases.extend(KNOWN_COMPANY_ALIASES.get(symbol, []))
    return _unique_texts(aliases)


def peer_aliases(target: object) -> List[str]:
    return _unique_texts(PEER_ALIASES.get(target_symbol(target), []))


def sector_topic_keywords(target: object) -> List[str]:
    text = _lower_text(" ".join([
        getattr(target, "sector", ""),
        getattr(target, "market", ""),
        getattr(target, "currency", ""),
        target_symbol(target),
        getattr(target, "name", ""),
    ]))
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
    text = _lower_text(str(source or "") + " " + str(provider or ""))
    if any(token in text for token in LOW_RELIABILITY_SOURCE_TERMS):
        return 0.42
    if any(token in text for token in HIGH_RELIABILITY_SOURCE_TERMS):
        return 0.82
    if any(token in text for token in ["google news", "gdelt", "yahoo finance", "investing", "매일경제", "한국경제", "이데일리", "머니투데이", "조선비즈", "서울경제"]):
        return 0.68
    return 0.58


def keyword_polarity(text: object) -> Tuple[str, float]:
    lowered = str(text or "").lower()
    support_hits = sum(1 for item in SUPPORT_KEYWORDS if item.lower() in lowered)
    risk_hits = sum(1 for item in RISK_KEYWORDS if item.lower() in lowered)
    if risk_hits > support_hits:
        return "risk", min(16.0, 6.0 + risk_hits * 4.0)
    if support_hits > risk_hits:
        return "support", min(14.0, 5.0 + support_hits * 3.5)
    return "context", 2.0


def classify_news_event_type(title: object, summary: object = "") -> str:
    text = _lower_text(str(title or "") + " " + str(summary or ""))
    best = ("general", 0)
    for event_type, keywords in EVENT_TYPE_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if _lower_text(keyword) in text)
        if hits > best[1]:
            best = (event_type, hits)
    return best[0]


def ontology_relations_for_news(scope: str, polarity: str, event_type: str) -> List[Dict[str, object]]:
    if scope == "noise":
        return []
    relations: List[Dict[str, object]] = [{
        "type": "NEWS_CONTEXT_FOR",
        "scope": scope,
        "eventType": event_type,
    }]
    if scope == "direct" and polarity in {"risk", "contradiction"}:
        relations.append({"type": "NEWS_RISK_FOR", "scope": scope, "eventType": event_type})
    if scope == "direct" and polarity == "support":
        relations.append({"type": "NEWS_SUPPORTS_ENTRY", "scope": scope, "eventType": event_type})
    if scope in {"peer", "sector", "market"}:
        relations.append({"type": "NEWS_PROPAGATES_CONTEXT", "scope": scope, "eventType": event_type})
    return relations


def platform_source_only_noise(
    target: object,
    title: object,
    summary: object,
    source: object,
    direct_hits: Iterable[str],
    topic_hits: Iterable[str],
    event_type: str,
) -> bool:
    symbol = target_symbol(target)
    text = _lower_text(str(title or "") + " " + str(summary or "") + " " + str(source or ""))
    if symbol == "035420" and direct_hits:
        platform_markers = ["naver blog", "네이버 블로그", "네이버 프리미엄콘텐츠", "네이버 포스트"]
        if any(marker in text for marker in platform_markers) and not list(topic_hits or []) and event_type == "general":
            return True
    return False


def confidence_from_analysis_payload(payload: Dict[str, object]) -> float:
    payload = payload if isinstance(payload, dict) else {}
    if payload.get("excludedReason"):
        return 0.25
    return round(clamp(
        number(payload.get("relevanceScore")) / 100 * 0.45
        + number(payload.get("sourceReliability")) * 0.35
        + number(payload.get("materialityScore")) / 100 * 0.20,
        0.25,
        0.92,
    ), 2)


def impact_from_analysis_payload(base_impact: object, payload: Dict[str, object]) -> float:
    payload = payload if isinstance(payload, dict) else {}
    relevance = number(payload.get("relevanceScore"))
    materiality = number(payload.get("materialityScore"))
    weight = max(0.55, min(1.35, relevance / 80 if relevance else 0.7))
    materiality_bonus = min(4.0, materiality / 25) if materiality else 0.0
    return round(number(base_impact) * weight + materiality_bonus, 1)


def analyze_news_item(
    target: object,
    title: object,
    summary: object = "",
    source: object = "",
    provider: object = "",
) -> NewsAnalysis:
    title_text = str(title or "")
    summary_text = str(summary or "")
    combined = title_text + " " + summary_text
    aliases = target_aliases(target)
    peers = peer_aliases(target)
    topics = sector_topic_keywords(target)
    direct_title = matched_terms(title_text, aliases)
    direct_body = matched_terms(summary_text, aliases)
    peer_hits = matched_terms(combined, peers)
    topic_hits = matched_terms(combined, topics)
    market_hits = matched_terms(combined, MARKET_TOPIC_KEYWORDS)
    reliability = source_reliability_score(source, provider)
    event_type = classify_news_event_type(title_text, summary_text)
    polarity, impact = keyword_polarity(combined)
    platform_only = platform_source_only_noise(target, title_text, summary_text, source, [*direct_title, *direct_body], topic_hits, event_type)

    score = 24.0
    scope = "noise"
    if platform_only:
        score = 18.0
        scope = "noise"
    elif direct_title:
        score = 95.0
        scope = "direct"
    elif direct_body:
        score = 82.0
        scope = "direct"
    elif peer_hits and topic_hits:
        score = 64.0
        scope = "peer"
    elif peer_hits:
        score = 58.0
        scope = "peer"
    elif topic_hits:
        score = 52.0
        scope = "sector"
    elif market_hits:
        score = 40.0
        scope = "market"

    if scope != "noise":
        score += max(-8.0, min(8.0, (reliability - 0.6) * 25.0))
    score = clamp(score, 0.0, 100.0)
    materiality = clamp(
        score * 0.45
        + reliability * 30
        + min(20, number(impact) * 1.2)
        + (8 if event_type in {"earnings", "guidance", "regulation", "capital_policy"} else 0),
        0.0,
        100.0,
    )
    excluded_reason = ""
    if platform_only:
        excluded_reason = "회사 뉴스가 아니라 플랫폼/블로그 출처명이 종목명처럼 잡힌 항목"
    elif scope == "noise":
        excluded_reason = "종목명, 비교 기업, 업종, 시장 주제가 기사 제목/요약에서 확인되지 않음"
    return NewsAnalysis(
        relevance_score=round(score, 1),
        relation_scope=scope,
        source_reliability=round(reliability, 2),
        event_type=event_type,
        materiality_score=round(materiality, 1),
        ontology_relations=ontology_relations_for_news(scope, polarity, event_type),
        matched_aliases=_unique_texts([*direct_title, *direct_body]),
        mentioned_peers=peer_hits,
        topic_tags=topic_hits[:8],
        market_topics=market_hits[:8],
        direct_mention=bool(direct_title or direct_body),
        excluded_reason=excluded_reason,
    )


def classify_news_relevance(
    target: object,
    title: object,
    summary: object = "",
    source: object = "",
    provider: object = "",
) -> Dict[str, object]:
    return analyze_news_item(target, title, summary, source, provider).to_payload()
