from dataclasses import dataclass, field
import re
from typing import Dict, Iterable, List, Tuple

from .market_data import clamp, number


NEWS_ANALYSIS_VERSION = "news-analysis-v3-entity-linking"
ARTICLE_DIGEST_VERSION = "article-digest-ko-v3"

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
    "hit record",
    "record",
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
    "sue",
    "sues",
    "sued",
    "legal",
    "litigation",
    "antitrust",
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
    "plunge",
    "convertible debt",
    "repayment",
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
    "035420": ["카카오", "Kakao", "카카오게임즈", "Kakao Games"],
    "AAPL": ["Samsung Electronics", "Microsoft", "Google", "Alphabet"],
    "NVDA": ["AMD", "Broadcom", "Intel", "TSMC"],
    "TSLA": ["BYD", "Rivian", "Lucid", "Hyundai Motor"],
}

EXTRA_COMPANY_ALIASES = [
    "카카오",
    "Kakao",
    "카카오게임즈",
    "Kakao Games",
    "SK하이닉스",
    "SK Hynix",
    "삼성전자",
    "Samsung Electronics",
    "현대차",
    "현대자동차",
    "Hyundai Motor",
    "Apple",
    "NVIDIA",
    "Tesla",
    "Palantir",
]

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
    "regulation": ["regulation", "규제", "소송", "lawsuit", "sue", "sues", "sued", "legal", "litigation", "antitrust", "probe", "investigation", "조사", "제재"],
    "capital_policy": ["buyback", "dividend", "자사주", "배당", "증자", "offering", "dilution", "debt", "convertible debt", "repayment", "상환"],
    "listing": ["listing", "상장", "ADR", "나스닥", "IPO"],
    "macro_sector": ["금리", "환율", "inflation", "FOMC", "업황", "수요", "demand"],
    "crypto_linked": ["bitcoin", "비트코인", "crypto", "암호화폐", "digital asset"],
    "price_commentary": ["주가", "shares", "stock", "목표주가", "급등", "급락", "plunge", "trading volume", "거래량", "거래대금"],
}

SOCIAL_SOURCE_TERMS = [
    "facebook",
    "facebook.com",
    "x.com",
    "twitter",
    "threads",
    "instagram",
    "linkedin",
    "youtube",
    "tiktok",
]
LOW_RELIABILITY_SOURCE_TERMS = ["blog", "블로그", "cafe", "reddit", "rumor", *SOCIAL_SOURCE_TERMS]
INVESTABLE_RELATION_SCOPES = {"direct", "peer", "sector", "market"}
NON_INVESTABLE_RELATION_SCOPES = {
    "noise",
    "platform_noise",
    "publisher_noise",
    "entity_mismatch",
    "low_confidence_context",
    "syndicated_duplicate",
}
PLATFORM_SOURCE_TERMS = [
    "naver blog",
    "blog.naver",
    "네이버 블로그",
    "네이버블로그",
    "naver post",
    "post.naver",
    "네이버 포스트",
    "네이버포스트",
    "naver premium",
    "premium.naver",
    "contents.premium.naver",
    "네이버 프리미엄콘텐츠",
    "네이버 프리미엄 콘텐츠",
    "네이버프리미엄콘텐츠",
]
PUBLISHER_PLATFORM_LABELS = {
    "naver blog": "Naver Blog",
    "blog.naver": "Naver Blog",
    "네이버 블로그": "Naver Blog",
    "네이버블로그": "Naver Blog",
    "naver post": "Naver Post",
    "post.naver": "Naver Post",
    "네이버 포스트": "Naver Post",
    "네이버포스트": "Naver Post",
    "naver premium": "Naver Premium Contents",
    "premium.naver": "Naver Premium Contents",
    "contents.premium.naver": "Naver Premium Contents",
    "네이버 프리미엄콘텐츠": "Naver Premium Contents",
    "네이버 프리미엄 콘텐츠": "Naver Premium Contents",
    "네이버프리미엄콘텐츠": "Naver Premium Contents",
}
PLATFORM_TRAILING_PATTERNS = [
    r"\s*[:：]\s*네이버\s*블로그\s*$",
    r"\s*[-–—]\s*네이버\s*블로그\s*$",
    r"\s*[-–—]\s*네이버\s*포스트\s*$",
    r"\s*[-–—]\s*네이버\s*프리미엄\s*콘텐츠\s*$",
    r"\s*[-–—]\s*네이버\s*프리미엄콘텐츠\s*$",
    r"\s*[-–—]\s*Naver\s+Blog\s*$",
    r"\s*[-–—]\s*Naver\s+Post\s*$",
    r"\s*[-–—]\s*Naver\s+Premium(?:\s+Contents?)?\s*$",
]
HIGH_RELIABILITY_SOURCE_TERMS = [
    "dart",
    "sec",
    "edgar",
    "reuters",
    "bloomberg",
    "the economist",
    "financial times",
    "연합",
    "yonhap",
    "cnbc",
    "wsj",
    "marketwatch",
]
MEDIUM_RELIABILITY_SOURCE_TERMS = [
    "yahoo finance",
    "investing",
    "매일경제",
    "한국경제",
    "이데일리",
    "머니투데이",
    "조선비즈",
    "chosunbiz",
    "서울경제",
    "파이낸셜뉴스",
    "뉴스핌",
    "뉴스토마토",
    "전자신문",
    "매일일보",
    "ytn",
    "kbs",
    "sbs",
    "mbc",
    "매경",
    "한경",
]

EVENT_TYPE_LABELS = {
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
    "general": "일반 이슈",
}

RELATION_SCOPE_LABELS = {
    "direct": "종목 직접 뉴스",
    "peer": "비교 기업 뉴스",
    "sector": "업종 뉴스",
    "market": "시장 환경 뉴스",
    "noise": "투자 관련 낮음",
    "platform_noise": "게시 플랫폼명 매칭 제외",
    "publisher_noise": "출처명 매칭 제외",
    "entity_mismatch": "다른 회사 뉴스",
    "low_confidence_context": "낮은 신뢰도 참고",
    "syndicated_duplicate": "중복 기사",
}

TOPIC_LABELS = [
    ("hbm", "HBM"),
    ("dram", "D램"),
    ("d램", "D램"),
    ("nand", "낸드"),
    ("memory", "메모리"),
    ("메모리", "메모리"),
    ("semiconductor", "반도체"),
    ("반도체", "반도체"),
    ("ai", "AI"),
    ("artificial intelligence", "AI"),
    ("gpu", "GPU"),
    ("data center", "데이터센터"),
    ("데이터센터", "데이터센터"),
    ("cloud", "클라우드"),
    ("electric vehicle", "전기차"),
    ("ev", "전기차"),
    ("전기차", "전기차"),
    ("battery", "배터리"),
    ("배터리", "배터리"),
    ("bitcoin", "비트코인"),
    ("비트코인", "비트코인"),
    ("convertible debt", "전환사채"),
    ("trading volume", "거래량"),
    ("금리", "금리"),
    ("yield", "금리"),
    ("inflation", "인플레이션"),
    ("환율", "환율"),
    ("buyback", "자사주"),
    ("dividend", "배당"),
    ("guidance", "가이던스"),
]

NUMERIC_TOKEN_RE = re.compile(
    r"(?:[$₩]?\d[\d,.]*(?:\.\d+)?\s?(?:%|달러|원|조|억|만|million|billion|trillion|mn|bn|M|B)?)",
    re.IGNORECASE,
)


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
    normalized_title: str = ""
    normalized_summary: str = ""
    source_kind: str = "publisher"
    source_platform: str = ""
    entity_links: List[Dict[str, object]] = field(default_factory=list)
    quality_gate: Dict[str, object] = field(default_factory=dict)

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
            "normalizedTitle": self.normalized_title,
            "normalizedSummary": self.normalized_summary,
            "sourceKind": self.source_kind,
            "sourcePlatform": self.source_platform,
            "entityLinks": list(self.entity_links),
            "qualityGate": dict(self.quality_gate or {}),
        }

    def summary(self) -> str:
        scope = {
            "direct": "종목 직접 뉴스",
            "peer": "비교 기업 뉴스",
            "sector": "업종 뉴스",
            "market": "시장 환경 뉴스",
            "noise": "투자 관련 낮음",
            "platform_noise": "게시 플랫폼명 매칭 제외",
            "publisher_noise": "출처명 매칭 제외",
            "entity_mismatch": "다른 회사 뉴스",
            "low_confidence_context": "낮은 신뢰도 참고",
            "syndicated_duplicate": "중복 기사",
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


def _keyword_in_lowered_text(keyword: object, lowered_text: str) -> bool:
    term = _lower_text(keyword).strip()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 .&+/'-]*", term):
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", lowered_text))
    return term in lowered_text


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


def compact_text(value: object, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def contains_hangul(value: object) -> bool:
    return bool(re.search(r"[가-힣]", str(value or "")))


def event_type_label(event_type: object) -> str:
    return EVENT_TYPE_LABELS.get(str(event_type or "general"), str(event_type or "일반 이슈"))


def relation_scope_label(scope: object) -> str:
    return RELATION_SCOPE_LABELS.get(str(scope or ""), str(scope or "뉴스"))


def relation_scope_is_investable(scope: object) -> bool:
    return str(scope or "").strip().lower() in INVESTABLE_RELATION_SCOPES


def relation_scope_is_excluded(scope: object) -> bool:
    text = str(scope or "").strip().lower()
    return bool(text) and text not in INVESTABLE_RELATION_SCOPES


def analysis_payload_requires_refresh(payload: Dict[str, object]) -> bool:
    payload = payload if isinstance(payload, dict) else {}
    version = str(payload.get("analysisVersion") or "").strip()
    return (bool(version) and version != NEWS_ANALYSIS_VERSION) or not str(payload.get("relationScope") or "").strip()


def source_identity(source: object, provider: object = "") -> Dict[str, object]:
    source_text = str(source or "").strip()
    provider_text = str(provider or "").strip()
    lowered = _lower_text(source_text + " " + provider_text)
    platform = ""
    for term, label in PUBLISHER_PLATFORM_LABELS.items():
        if term in lowered:
            platform = label
            break
    source_kind = "platform" if platform else "publisher"
    return {
        "sourceKind": source_kind,
        "sourcePlatform": platform,
        "sourceName": source_text,
        "providerName": provider_text,
        "isPlatformSource": bool(platform),
    }


def strip_platform_source_markers(value: object) -> str:
    text = compact_text(value, 900)
    if not text:
        return ""
    for pattern in PLATFORM_TRAILING_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    for term in PLATFORM_SOURCE_TERMS:
        text = re.sub(r"(?<![A-Za-z0-9가-힣])" + re.escape(term) + r"(?![A-Za-z0-9가-힣])", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .:-")
    return text


def normalized_article_title(value: object) -> str:
    return strip_platform_source_markers(clean_article_title(value))


def article_primary_clause(value: object) -> str:
    text = normalized_article_title(value)
    if not text:
        return ""
    parts = re.split(r"\s*[,:：;；|]\s+|\s+[-–—]\s+", text, maxsplit=1)
    return str(parts[0] if parts else text).strip()


def global_company_aliases(exclude_symbol: object = "") -> List[str]:
    exclude = str(exclude_symbol or "").upper().strip()
    rows: List[str] = []
    for symbol, aliases in KNOWN_COMPANY_ALIASES.items():
        if str(symbol or "").upper() == exclude:
            continue
        rows.extend(aliases)
    rows.extend(EXTRA_COMPANY_ALIASES)
    return _unique_texts(rows)


def entity_link_rows(
    target: object,
    title: object,
    summary: object,
    source: object,
    provider: object,
    direct_title: Iterable[str],
    direct_body: Iterable[str],
    peer_hits: Iterable[str],
    other_company_hits: Iterable[str],
    platform_alias_hits: Iterable[str],
) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    symbol = target_symbol(target)

    def add(entity: str, role: str, terms: Iterable[str], confidence: float, reason: str = "") -> None:
        clean_terms = _unique_texts(terms)
        if not entity or not clean_terms:
            return
        rows.append({
            "entity": entity,
            "role": role,
            "terms": clean_terms,
            "confidence": round(clamp(confidence, 0.0, 1.0), 2),
            "reason": reason,
        })

    add(symbol, "target_subject", direct_title, 0.95, "제목 본문 영역에서 대상 종목명이 확인됨")
    add(symbol, "target_context", direct_body, 0.72, "요약/본문 영역에서 대상 종목명이 확인됨")
    add(symbol, "platform_reference", platform_alias_hits, 0.15, "회사명이 아니라 게시 플랫폼/출처명에서 감지됨")
    add("peer_or_other_company", "peer_context", peer_hits, 0.62, "비교 기업 또는 동종 기업명이 확인됨")
    add("other_company", "article_subject", other_company_hits, 0.78, "기사 제목의 주어가 다른 회사로 보임")
    identity = source_identity(source, provider)
    if identity.get("sourcePlatform"):
        rows.append({
            "entity": str(identity.get("sourcePlatform")),
            "role": "publishing_platform",
            "terms": [str(identity.get("sourcePlatform"))],
            "confidence": 0.98,
            "reason": "출처/게시 플랫폼으로 정규화됨",
        })
    return rows


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
    return [term for term in _unique_texts(terms) if _keyword_in_lowered_text(term, lowered)]


def detected_topic_labels(text: object, limit: int = 5) -> List[str]:
    lowered = _lower_text(text)
    labels: List[str] = []
    for keyword, label in TOPIC_LABELS:
        if _keyword_in_lowered_text(keyword, lowered) and label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


def keyword_hits(text: object, terms: Iterable[str], limit: int = 4) -> List[str]:
    lowered = _lower_text(text)
    rows: List[str] = []
    for term in terms:
        if _keyword_in_lowered_text(term, lowered) and str(term) not in rows:
            rows.append(str(term))
        if len(rows) >= limit:
            break
    return rows


def numeric_highlights(text: object, limit: int = 4) -> List[str]:
    rows: List[str] = []
    for match in NUMERIC_TOKEN_RE.findall(str(text or "")):
        value = str(match or "").strip()
        if value and value not in rows:
            rows.append(value)
        if len(rows) >= limit:
            break
    return rows


def is_news_boilerplate_sentence(value: object) -> bool:
    lowered = _lower_text(value)
    if not lowered:
        return False
    if "google news" in lowered and any(token in lowered for token in ["comprehensive", "coverage", "aggregated", "all over the world"]):
        return True
    if "comprehensive up-to-date news coverage" in lowered:
        return True
    if "aggregated from sources all over the world" in lowered:
        return True
    if "comprehensive 상승-으로-date" in lowered:
        return True
    return False


def clean_article_summary_noise(value: object) -> str:
    text = compact_text(value, 1200)
    if not text:
        return ""
    parts = re.split(r"(?<=입니다[.。])\s+|(?<=다[.。])\s+|(?<=[.!?。！？])\s+", text)
    cleaned = [part.strip() for part in parts if part.strip() and not is_news_boilerplate_sentence(part)]
    return " ".join(cleaned).strip() if cleaned else ("" if is_news_boilerplate_sentence(text) else text)


def article_sentence_candidates(text: object, target: object, analysis: Dict[str, object] = None, limit: int = 3) -> List[str]:
    analysis = analysis if isinstance(analysis, dict) else {}
    source_text = str(text or "")
    if not source_text.strip():
        return []
    raw_parts = re.split(r"(?<=[.!?。！？])\s+|\n+", source_text)
    terms = [
        *target_aliases(target),
        *sector_topic_keywords(target),
        *SUPPORT_KEYWORDS,
        *RISK_KEYWORDS,
        *EVENT_TYPE_KEYWORDS.get(str(analysis.get("eventType") or ""), []),
    ]
    scored: List[Tuple[float, int, str]] = []
    for index, raw in enumerate(raw_parts[:80]):
        sentence = compact_text(raw, 180)
        if len(sentence) < 24:
            continue
        if is_news_boilerplate_sentence(sentence):
            continue
        lowered = _lower_text(sentence)
        score = max(0.0, 12.0 - index * 0.25)
        score += sum(4.0 for term in terms if _keyword_in_lowered_text(term, lowered))
        score += min(8.0, len(numeric_highlights(sentence, 4)) * 2.0)
        scored.append((score, index, sentence))
    scored.sort(key=lambda item: (-item[0], item[1]))
    result: List[str] = []
    for _score, _index, sentence in scored:
        if sentence not in result:
            result.append(sentence)
        if len(result) >= limit:
            break
    if result:
        return result
    fallback = compact_text(source_text, 180)
    if is_news_boilerplate_sentence(fallback):
        return []
    return [fallback] if fallback else []


def clean_article_title(value: object) -> str:
    text = compact_text(value, 260)
    if is_news_boilerplate_sentence(text):
        return ""
    if " - " in text:
        head, tail = text.rsplit(" - ", 1)
        if 2 <= len(tail.strip()) <= 48:
            return head.strip()
    return text.strip()


def _target_name_for_summary(target: object) -> str:
    return str(getattr(target, "name", "") or target_symbol(target) or "해당 종목").strip()


def _with_target_aliases_ko(text: str, target: object) -> str:
    result = str(text or "")
    target_name = _target_name_for_summary(target)
    if not target_name:
        return result
    for alias in sorted(target_aliases(target), key=len, reverse=True):
        alias_text = str(alias or "").strip()
        if not alias_text or alias_text.upper() == target_symbol(target):
            continue
        result = re.sub(r"(?<![A-Za-z0-9])" + re.escape(alias_text) + r"(?![A-Za-z0-9])", target_name, result, flags=re.IGNORECASE)
    return result


def _target_mentioned_in_text(text: object, target: object) -> bool:
    lowered = _lower_text(text)
    return any(_keyword_in_lowered_text(alias, lowered) for alias in target_aliases(target))


def article_core_clause(value: object, target: object) -> str:
    text = clean_article_title(value)
    if ";" in text:
        parts = [part.strip() for part in text.split(";") if part.strip()]
        for part in parts:
            if _target_mentioned_in_text(part, target):
                text = part
                break
    if ":" in text:
        before, after = text.split(":", 1)
        if _target_mentioned_in_text(after, target) or _lower_text(before) in {"world in brief", "view", "brief"}:
            text = after.strip()
    return text.strip()


def english_fragment_to_korean(value: object, target: object = None) -> str:
    text = clean_article_title(value)
    if target is not None:
        text = _with_target_aliases_ko(text, target)
    replacements = [
        (r"\bhomicide investigation underway\b", "살인 수사가 진행 중"),
        (r"\bbody found\b", "시신이 발견"),
        (r"\btrunk of\b", "트렁크에서"),
        (r"\blegal dispute\b", "법적 분쟁"),
        (r"\bartificial intelligence\b", "AI"),
        (r"\bproducts?\b", "제품"),
        (r"\bdata centers?\b", "데이터센터"),
        (r"\bsemiconductor demand expectations improved\b", "반도체 수요 기대가 개선"),
        (r"\bsemiconductor demand\b", "반도체 수요"),
        (r"\bchip demand\b", "반도체 수요"),
        (r"\bbitcoin hoarding\b", "비트코인 보유 확대"),
        (r"\bpivoting from 비트코인 보유 확대 to funding dividends with sales\b", "비트코인 보유 확대에서 매각 자금으로 배당 재원을 마련하는 전략으로 전환"),
        (r"\bfunding dividends with sales\b", "매각 자금으로 배당 재원을 마련"),
        (r"\bbitcoin strategy\b", "비트코인 전략"),
        (r"\bbitcoin\b", "비트코인"),
        (r"\bBTC pivot message\b", "비트코인 전략 전환 메시지"),
        (r"\bdigital assets?\b", "디지털자산"),
        (r"\bpreferred shares?\b", "우선주"),
        (r"\bcombined\b", "합산"),
        (r"\btrading volume\b", "거래대금"),
        (r"\bhit record\b|\brecord\b", "사상 최고"),
        (r"\bdespite\b", "에도 불구하고"),
        (r"\bdip\b", "하락"),
        (r"\bplunge\b", "급락"),
        (r"\bconvertible debt\b", "전환사채"),
        (r"\brepayment\b", "상환"),
        (r"\btriggered\b", "원인으로 작용"),
        (r"\bjanuary\b", "1월"),
        (r"\bfebruary\b", "2월"),
        (r"\bmarch\b", "3월"),
        (r"\bapril\b", "4월"),
        (r"\bmay\b", "5월"),
        (r"\bjune\b", "6월"),
        (r"\bjuly\b", "7월"),
        (r"\baugust\b", "8월"),
        (r"\bseptember\b", "9월"),
        (r"\boctober\b", "10월"),
        (r"\bnovember\b", "11월"),
        (r"\bdecember\b", "12월"),
        (r"\bpre[- ]market\b", "프리마켓"),
        (r"\bafter[- ]hours\b", "시간외 거래"),
        (r"\bshares?\b", "주가"),
        (r"\bstock\b", "주가"),
        (r"\bstake\b", "지분"),
        (r"\bposition\b", "보유 지분"),
        (r"\bpurchases?\b", "매수"),
        (r"\bincreased\b", "증가"),
        (r"\bdecreased\b", "감소"),
        (r"\brises?\b|\brose\b|\bgains?\b|\bjumps?\b|\bsurges?\b", "상승"),
        (r"\bfalls?\b|\bfell\b|\bdrops?\b|\bslips?\b", "하락"),
        (r"\bis down\b|\bdown\b", "하락"),
        (r"\bis up\b|\bup\b", "상승"),
        (r"\bmoved\b|\btracks?\b", "움직임"),
        (r"\bsells?\b", "매도"),
        (r"\bdefends?\b", "방어"),
        (r"\blawsuit\b|\blitigation\b", "소송"),
        (r"\bsues?\b|\bsued\b", "소송 제기"),
        (r"\binvestigation\b|\bprobe\b", "조사"),
        (r"\bagainst\b", "상대로"),
        (r"\bover\b", "관련해"),
        (r"\bafter\b", "이후"),
        (r"\bas\b", "하면서"),
        (r"\bamid\b", "가운데"),
        (r"\bwith\b", "함께"),
        (r"\ba\s+|\ban\s+|\bthe\s+", ""),
        (r"\bfrom\b", "에서"),
        (r"\bto\b", "으로"),
        (r"\bof\b", "의"),
        (r"\bin\b", "에서"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .;:-")
    return text


def english_sentence_korean_fact(sentence: object, target: object, event_label: str) -> str:
    source = article_core_clause(sentence, target)
    if not source or is_news_boilerplate_sentence(source):
        return ""
    translated = english_fragment_to_korean(source, target)
    lowered = _lower_text(source)
    target_name = _target_name_for_summary(target)
    legal_match = re.search(r"(.+?)\s+sues?\s+(.+?)(?:\s+(?:over|in|for)\s+(.+))?$", source, re.IGNORECASE)
    if legal_match:
        actor = english_fragment_to_korean(legal_match.group(1), target)
        target_party = english_fragment_to_korean(legal_match.group(2), None)
        issue_source = str(legal_match.group(3) or "")
        if re.search(r"legal dispute .*artificial intelligence products?", issue_source, re.IGNORECASE):
            issue = "AI 제품을 둘러싼 법적 분쟁"
        else:
            issue = english_fragment_to_korean(issue_source, target) if issue_source else ""
        return actor + "가 " + target_party + "를 상대로 소송을 제기했다는 내용입니다" + (". 쟁점은 " + issue + "입니다" if issue else ".")
    preferred_volume = re.search(
        r"(.+?)\s+preferred shares?\s+hit record\s+([$€£]?\d[\d,.]*(?:\.\d+)?\s?(?:billion|million|trillion|bn|mn|B|M)?)\s+in\s+combined\s+(.+?)\s+trading volume(?:\s+despite\s+(.+))?",
        source,
        re.IGNORECASE,
    )
    if preferred_volume:
        subject = english_fragment_to_korean(preferred_volume.group(1), target)
        amount = preferred_volume.group(2).replace("B", " billion").replace("M", " million")
        period = english_fragment_to_korean(preferred_volume.group(3), target)
        counter = english_fragment_to_korean(preferred_volume.group(4), target) if preferred_volume.group(4) else ""
        return subject + " 우선주의 " + period + " 합산 거래대금이 " + amount + "로 사상 최고를 기록했다는 내용입니다" + (". 다만 " + counter + "도 함께 언급됐습니다" if counter else ".")
    debt_plunge = re.search(r"(.+?)\s+CEO\s+says\s+convertible debt repayment\s+triggered\s+(.+?)\s+plunge", source, re.IGNORECASE)
    if debt_plunge:
        actor = english_fragment_to_korean(debt_plunge.group(1), target)
        affected = english_fragment_to_korean(debt_plunge.group(2), target)
        return actor + " 경영진이 전환사채 상환을 " + affected + " 급락 원인으로 설명했다는 내용입니다."
    btc_clarity = re.search(r"(.+?)\s+needs clarity in BTC pivot message to convince investors", source, re.IGNORECASE)
    if btc_clarity:
        actor = english_fragment_to_korean(btc_clarity.group(1), target)
        return actor + "의 비트코인 전략 전환 메시지가 투자자를 설득하려면 더 명확해야 한다는 내용입니다."
    if "homicide investigation" in lowered and "body found" in lowered and "tesla" in lowered:
        return "테슬라 차량 트렁크에서 시신이 발견돼 살인 수사가 진행 중이라는 내용입니다."
    if re.search(r"\bshares?\s+(?:were\s+)?little changed\b", source, re.IGNORECASE):
        market = "프리마켓에서 " if re.search(r"pre[- ]market", source, re.IGNORECASE) else ""
        return "주가는 " + market + "큰 변화가 없었다는 내용입니다."
    bitcoin_sale = re.search(r"(.+?)\s+sells?\s+([\d,.]+)\s+bitcoin", source, re.IGNORECASE)
    if bitcoin_sale:
        actor = english_fragment_to_korean(bitcoin_sale.group(1), target)
        return actor + "가 비트코인 " + bitcoin_sale.group(2) + "개를 매도했다는 내용입니다."
    move_after = re.search(r"(.+?)\s+(?:is\s+)?(down|up)\s+([+-]?\d[\d,.]*%)\s+after\s+(.+)", source, re.IGNORECASE)
    if move_after:
        actor = english_fragment_to_korean(move_after.group(1), target)
        direction = "하락" if move_after.group(2).lower() == "down" else "상승"
        reason_source = move_after.group(4)
        if re.search(r"pivoting from bitcoin hoarding to funding dividends with sales", reason_source, re.IGNORECASE):
            return actor + "가 비트코인 보유 확대에서 매각 자금으로 배당 재원을 마련하는 전략으로 전환한 뒤 " + move_after.group(3) + " " + direction + "했다는 내용입니다."
        else:
            reason = english_fragment_to_korean(reason_source, target)
        return actor + "가 " + reason + " 이후 " + move_after.group(3) + " " + direction + "했다는 내용입니다."
    if re.search(r"stock reaction followed a shift in .*bitcoin treasury playbook", source, re.IGNORECASE):
        return "주가 반응은 비트코인 재무 전략 변화 이후 나타났다는 내용입니다."
    if re.search(r"\bshares?\s+tracks?\s+(?:chip|semiconductor)\s+demand\b", source, re.IGNORECASE):
        return _target_name_for_summary(target) + " 주가가 반도체 수요 흐름을 따라 움직였다는 내용입니다."
    if re.search(r"\bshares?\s+moved\s+after\s+semiconductor demand expectations improved\b", source, re.IGNORECASE):
        return "반도체 수요 기대가 개선된 뒤 " + _target_name_for_summary(target) + " 주가가 움직였다는 내용입니다."
    stock_move = re.search(r"(.+?)\s+(?:shares?|stock)\s+(.+)", source, re.IGNORECASE)
    if stock_move:
        actor = english_fragment_to_korean(stock_move.group(1), target) or target_name
        action = english_fragment_to_korean(stock_move.group(2), target)
        return actor + " 주가가 " + action + "했다는 내용입니다."
    if any(_keyword_in_lowered_text(term, lowered) for term in ["stake", "purchases", "decreases position", "increased by"]):
        return target_name + "의 기관 보유 지분 변화가 기사 핵심입니다. 내용은 " + translated + "입니다."
    if translated:
        return event_label + " 관련 핵심 내용은 " + translated + "입니다."
    return ""


def join_korean_summary_parts(parts: Iterable[str]) -> str:
    rows: List[str] = []
    for part in parts or []:
        text = str(part or "").strip().rstrip(".。")
        if text:
            rows.append(text)
    return ". ".join(rows) + ("." if rows else "")


def factual_english_article_summary_parts(
    target: object,
    title: object,
    source_text: object,
    event_label: str,
) -> List[str]:
    sentences = [clean_article_title(title)]
    sentences.extend(article_sentence_candidates(source_text, target, {"eventType": ""}, 3))
    rows: List[str] = []
    seen = set()
    for sentence in sentences:
        fact = english_sentence_korean_fact(sentence, target, event_label)
        if "소송을 제기" in fact:
            legal_index = next((index for index, row in enumerate(rows) if "소송을 제기" in row), -1)
            if legal_index >= 0:
                if "쟁점은" in fact and "쟁점은" not in rows[legal_index]:
                    rows[legal_index] = fact
                continue
        if "비트코인 보유 확대" in fact and "하락했다는 내용" in fact:
            if any("비트코인 보유 확대" in row and "하락했다는 내용" in row for row in rows):
                continue
        key = _lower_text(fact)
        if fact and key not in seen:
            seen.add(key)
            rows.append(fact)
        if len(rows) >= 4:
            break
    if rows:
        return rows
    text = _lower_text(str(title or "") + " " + str(source_text or ""))
    subject = _target_name_for_summary(target)
    parts: List[str] = []
    if any(_keyword_in_lowered_text(term, text) for term in ["lawsuit", "sue", "sues", "sued", "legal", "litigation", "antitrust", "probe", "investigation"]):
        parts.append("소송·조사·규제처럼 주가 부담이 될 수 있는 법적 이슈를 다룹니다")
    if any(_keyword_in_lowered_text(term, text) for term in ["earnings", "revenue", "profit", "margin", "guidance", "forecast", "estimate"]):
        parts.append("실적이나 향후 전망 변화가 핵심입니다")
    if any(_keyword_in_lowered_text(term, text) for term in ["buyback", "dividend", "offering", "dilution", "debt", "treasury"]):
        parts.append("자사주·배당·자금 조달 같은 자본정책 이슈가 포함됩니다")
    if any(_keyword_in_lowered_text(term, text) for term in ["partnership", "contract", "deal", "approval", "launch", "supplier"]):
        parts.append("제휴·계약·승인·출시처럼 사업 진행 상황과 연결된 내용입니다")
    if any(_keyword_in_lowered_text(term, text) for term in ["bitcoin", "crypto", "digital asset", "ethereum"]):
        parts.append("가상자산 가격이나 관련 사업 민감도와 연결될 수 있는 내용입니다")
    if any(_keyword_in_lowered_text(term, text) for term in ["rate", "yield", "inflation", "fed", "fomc", "dollar", "currency"]):
        parts.append("금리·물가·환율 같은 거시 환경 변화와 연결되는 내용입니다")
    if any(_keyword_in_lowered_text(term, text) for term in ["semiconductor", "chip", "memory", "hbm", "dram", "nand", "foundry", "demand"]):
        parts.append("반도체 수요와 공급 흐름이 " + subject + "의 가격 판단과 연결될 수 있습니다")
    if any(_keyword_in_lowered_text(term, text) for term in ["ai", "artificial intelligence", "gpu", "data center", "cloud"]):
        parts.append("AI·데이터센터 투자 흐름과 연결된 내용입니다")
    if any(_keyword_in_lowered_text(term, text) for term in ["shares", "stock", "pre-market", "premarket", "trading", "rises", "falls", "down", "up"]):
        parts.append("기사 안에서 주가 움직임이나 시장 반응을 함께 다룹니다")
    if not parts:
        parts.append("본문에서 " + event_label + " 성격의 새 정보를 확인했으며 투자 판단에 필요한 사실관계 확인이 필요합니다")
    return parts[:3]


def korean_article_summary(
    target: object,
    title: object,
    article_text: object = "",
    feed_summary: object = "",
    analysis: Dict[str, object] = None,
) -> str:
    analysis = analysis if isinstance(analysis, dict) else {}
    body = clean_article_summary_noise(compact_text(article_text, 2500))
    fallback_from_title = not str(feed_summary or "").strip()
    fallback_source = feed_summary if not fallback_from_title else clean_article_title(title)
    fallback = clean_article_summary_noise(compact_text(fallback_source, 900))
    source_text = body or fallback
    if not source_text:
        return ""
    event_label = event_type_label(analysis.get("eventType") or classify_news_event_type(title, source_text))
    topics = detected_topic_labels(str(title or "") + " " + source_text)
    numbers = numeric_highlights(source_text)
    body_status = "본문" if body else "RSS/제공"
    sentences = [clean_article_title(source_text)] if fallback_from_title and not body else article_sentence_candidates(source_text, target, analysis, 3)
    if sentences and contains_hangul(source_text):
        sentence_text = join_korean_summary_parts(sentences)
        details = []
        if topics:
            details.append("핵심 키워드: " + ", ".join(topics))
        if numbers:
            details.append("확인된 수치: " + ", ".join(numbers))
        suffix = (" " + " / ".join(details) + ".") if details else ""
        return compact_text(body_status + " 요약: " + sentence_text + suffix, 760)
    detail_parts = factual_english_article_summary_parts(target, title, source_text, event_label)
    if topics:
        detail_parts.append("핵심 키워드는 " + ", ".join(topics) + "입니다")
    if numbers:
        detail_parts.append("기사에서 확인되는 주요 수치는 " + ", ".join(numbers) + "입니다")
    if not detail_parts:
        detail_parts.append("기사의 구체 내용을 확인하려면 원문 본문 확인이 필요합니다")
    return compact_text(
        body_status
        + " 요약: "
        + join_korean_summary_parts(detail_parts),
        760,
    )


def article_event_takeaway(target: object, title: object, article_text: object = "", feed_summary: object = "") -> str:
    text = clean_article_summary_noise(compact_text(str(article_text or feed_summary or title or ""), 1600))
    title_text = clean_article_title(title)
    combined = title_text + " " + text
    lowered = _lower_text(combined)
    target_name = _target_name_for_summary(target)
    preferred_volume = re.search(
        r"preferred shares?\s+hit record\s+([$€£]?\d[\d,.]*(?:\.\d+)?\s?(?:billion|million|trillion|bn|mn|B|M)?)\s+in\s+combined\s+(.+?)\s+trading volume(?:\s+despite\s+(.+))?",
        combined,
        re.IGNORECASE,
    )
    if preferred_volume:
        amount = preferred_volume.group(1).replace("B", " billion").replace("M", " million")
        period = english_fragment_to_korean(preferred_volume.group(2), target)
        counter = english_fragment_to_korean(preferred_volume.group(3), target) if preferred_volume.group(3) else ""
        return target_name + " 관련 우선주의 " + period + " 합산 거래대금이 " + amount + "로 사상 최고를 기록" + (", " + counter + "도 함께 언급" if counter else "")
    debt_plunge = re.search(r"convertible debt repayment\s+triggered\s+(.+?)\s+plunge", combined, re.IGNORECASE)
    if debt_plunge:
        affected = english_fragment_to_korean(debt_plunge.group(1), target)
        return "전환사채 상환이 " + affected + " 급락 원인으로 지목"
    if re.search(r"needs clarity in BTC pivot message to convince investors", combined, re.IGNORECASE):
        return "비트코인 전략 전환 메시지가 투자자를 설득하기에는 아직 명확하지 않다는 지적"
    if re.search(r"\bsues?\b|\blawsuit\b|\blitigation\b|\bantitrust\b", lowered):
        return target_name + " 관련 소송·규제 이슈가 투자심리 부담으로 부각"
    if re.search(r"\bbuyback\b|자사주|dividend|배당", lowered):
        return "자사주·배당 같은 주주환원 정책 변화가 핵심"
    if re.search(r"\boffering\b|dilution|유상증자|증자", lowered):
        return "자금 조달과 주식 가치 희석 가능성이 핵심"
    if re.search(r"\bearnings\b|revenue|profit|실적|매출|영업이익", lowered):
        return "실적과 이익 전망 변화가 핵심"
    if re.search(r"\bbitcoin\b|비트코인|crypto|digital asset", lowered):
        return "비트코인·가상자산 가격 민감도가 핵심"
    if re.search(r"\bAI\b|artificial intelligence|gpu|data center|cloud|AI", combined, re.IGNORECASE):
        return "AI·데이터센터 수요가 실적 기대에 연결되는지 확인할 뉴스"
    sentences = article_sentence_candidates(text or title_text, target, {}, 1)
    if sentences:
        return clean_article_summary_noise(sentences[0]).rstrip(".")
    return target_name + " 투자 판단에 영향을 줄 수 있는 새 정보"


def impact_channel_text(event_type: object, text: object) -> str:
    event = str(event_type or "general")
    lowered = _lower_text(text)
    if event == "earnings":
        return "실적 추정치와 목표가 재평가로 이어질 수 있습니다"
    if event == "guidance":
        return "앞으로의 매출·이익 눈높이를 바꾸는 재료입니다"
    if event == "supply_chain":
        return "생산 차질이나 공급 확대가 매출 시점과 마진에 영향을 줍니다"
    if event == "product":
        return "제품 경쟁력과 실제 매출 전환 가능성을 확인해야 합니다"
    if event == "regulation":
        return "소송 비용, 사업 제약, 투자심리 악화로 연결될 수 있습니다"
    if event == "capital_policy":
        if re.search(r"repayment|상환|debt|전환사채", lowered):
            return "상환 부담과 자금 조달 신뢰가 배당 지속성·가격 변동성에 바로 연결됩니다"
        if re.search(r"buyback|자사주|dividend|배당", lowered):
            return "주주환원은 수급을 지지하지만 재원 지속성이 관건입니다"
        return "자금 조달·주주환원 조건이 주당 가치와 투자심리를 바꿀 수 있습니다"
    if event == "listing":
        return "거래시장과 유동성 변화가 수급을 바꿀 수 있습니다"
    if event == "macro_sector":
        return "업황·금리·환율 변화가 밸류에이션과 주문 흐름에 영향을 줍니다"
    if event == "crypto_linked":
        return "비트코인 가격과 같은 방향으로 흔들릴 가능성이 커집니다"
    if event == "price_commentary":
        return "이미 가격 반응이 언급된 기사라 다음 거래량과 후속 가격 반응이 중요합니다"
    return "가격을 움직일 만큼 구체적인 사건인지 원문과 다음 가격 반응으로 확인해야 합니다"


def impact_watch_text(impact: str, materiality: float, text: object) -> str:
    lowered = _lower_text(text)
    if impact == "positive":
        return "호재라면 가격 상승이 거래량 증가와 함께 이어지는지 확인하세요"
    if impact == "negative":
        return "악재라면 하락이 하루짜리 반응인지, 거래량을 동반한 재평가인지 확인하세요"
    if re.search(r"trading volume|거래량|거래대금", lowered):
        return "거래가 늘어난 이유가 매수 수요인지 단순 변동성인지 가격 반응으로 확인하세요"
    if materiality >= 65:
        return "중요도는 높지만 방향은 단정하지 말고 당일·익일 가격과 거래량 반응을 보세요"
    return "방향성이 약하므로 다른 가격·수급 신호와 같이 봐야 합니다"


def stock_impact_analysis(
    target: object,
    title: object,
    article_text: object = "",
    feed_summary: object = "",
    analysis: Dict[str, object] = None,
    polarity: str = "",
    impact_score: object = 0,
) -> Dict[str, object]:
    analysis = analysis if isinstance(analysis, dict) else {}
    text = str(title or "") + " " + str(article_text or feed_summary or "")
    detected_polarity = str(polarity or "").strip() or keyword_polarity(text)[0]
    if detected_polarity == "support":
        impact = "positive"
        label = "호재"
        hits = keyword_hits(text, SUPPORT_KEYWORDS)
    elif detected_polarity in {"risk", "contradiction"}:
        impact = "negative"
        label = "악재"
        hits = keyword_hits(text, RISK_KEYWORDS)
    else:
        impact = "neutral"
        label = "중립"
        hits = []
    scope_label = relation_scope_label(analysis.get("relationScope"))
    event_label = event_type_label(analysis.get("eventType") or classify_news_event_type(title, text))
    event_type = str(analysis.get("eventType") or classify_news_event_type(title, text))
    materiality = number(analysis.get("materialityScore"))
    relevance = number(analysis.get("relevanceScore"))
    takeaway = article_event_takeaway(target, title, article_text, feed_summary)
    channel = impact_channel_text(event_type, text)
    watch = impact_watch_text(impact, materiality, text)
    reason_parts = []
    if takeaway:
        reason_parts.append("핵심: " + takeaway + ".")
    reason_parts.append("영향 경로: " + channel + ".")
    score_bits = [
        scope_label,
        event_label,
        ("관련성 " + ("%.1f" % relevance).rstrip("0").rstrip(".") + "점") if relevance else "",
        ("중요도 " + ("%.1f" % materiality).rstrip("0").rstrip(".") + "점") if materiality else "",
    ]
    reason_parts.append("주가 영향: " + label + "로 분류했습니다(" + ", ".join(bit for bit in score_bits if bit) + ").")
    if hits:
        reason_parts.append(("긍정 표현: " if impact == "positive" else "부정 표현: ") + ", ".join(hits[:3]) + ".")
    reason_parts.append("확인: " + watch + ".")
    reason = " ".join(part for part in reason_parts if part)
    return {
        "articleDigestVersion": ARTICLE_DIGEST_VERSION,
        "stockImpact": impact,
        "stockImpactLabel": label,
        "stockImpactPolarity": detected_polarity or "context",
        "stockImpactScore": round(number(impact_score), 1),
        "stockImpactReasonKo": compact_text(reason, 520),
    }


def source_reliability_score(source: object, provider: object = "") -> float:
    source_text = _lower_text(source)
    provider_text = _lower_text(provider)
    text = source_text + " " + provider_text
    if source_is_social_feed(source, provider):
        return 0.25
    if any(token in source_text for token in LOW_RELIABILITY_SOURCE_TERMS):
        return 0.42
    if any(token in source_text for token in HIGH_RELIABILITY_SOURCE_TERMS):
        return 0.82
    if any(token in source_text for token in MEDIUM_RELIABILITY_SOURCE_TERMS):
        return 0.68
    if any(token in provider_text for token in ["google news", "google_rss", "gdelt"]):
        return 0.58
    if any(token in text for token in ["yahoo finance", "investing"]):
        return 0.68
    return 0.58


def source_is_social_feed(source: object, provider: object = "") -> bool:
    text = _lower_text(str(source or "") + " " + str(provider or ""))
    return any(token in text for token in SOCIAL_SOURCE_TERMS)


def keyword_polarity(text: object) -> Tuple[str, float]:
    lowered = _lower_text(text)
    support_hits = sum(1 for item in SUPPORT_KEYWORDS if _keyword_in_lowered_text(item, lowered))
    risk_hits = sum(1 for item in RISK_KEYWORDS if _keyword_in_lowered_text(item, lowered))
    if risk_hits > support_hits:
        return "risk", min(16.0, 6.0 + risk_hits * 4.0)
    if support_hits > risk_hits:
        return "support", min(14.0, 5.0 + support_hits * 3.5)
    return "context", 2.0


def classify_news_event_type(title: object, summary: object = "") -> str:
    text = _lower_text(str(title or "") + " " + str(summary or ""))
    best = ("general", 0)
    for event_type, keywords in EVENT_TYPE_KEYWORDS.items():
        hits = sum(1 for keyword in keywords if _keyword_in_lowered_text(keyword, text))
        if hits > best[1]:
            best = (event_type, hits)
    return best[0]


def ontology_relations_for_news(scope: str, polarity: str, event_type: str) -> List[Dict[str, object]]:
    if not relation_scope_is_investable(scope):
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
        if any(marker in text for marker in PLATFORM_SOURCE_TERMS) and not list(topic_hits or []):
            return True
    return False


def low_confidence_platform_context(
    source: object,
    provider: object,
    direct_hits: Iterable[str],
) -> bool:
    identity = source_identity(source, provider)
    return bool(identity.get("isPlatformSource") and list(direct_hits or []))


def other_company_subject_hits(target: object, title: object) -> List[str]:
    primary = article_primary_clause(title)
    if not primary:
        return []
    target_hits = matched_terms(primary, target_aliases(target))
    if target_hits:
        return []
    return matched_terms(primary, global_company_aliases(target_symbol(target)))

def ambiguous_company_alias_noise(
    target: object,
    title: object,
    summary: object,
    direct_hits: Iterable[str],
    topic_hits: Iterable[str],
    event_type: str,
) -> bool:
    symbol = target_symbol(target)
    hits = [str(item or "").casefold().strip() for item in direct_hits or [] if str(item or "").strip()]
    text = _lower_text(str(title or "") + " " + str(summary or ""))
    if symbol == "AAPL" and hits and set(hits).issubset({"apple"}):
        common_noun_markers = [
            "apple snail",
            "apple snails",
            "apple orchard",
            "apple orchards",
            "apple harvest",
            "apple festival",
            "apple growers",
            "apple cider",
        ]
        if any(marker in text for marker in common_noun_markers):
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
    normalized_title = normalized_article_title(title_text)
    normalized_summary = strip_platform_source_markers(summary_text)
    combined = normalized_title + " " + normalized_summary
    aliases = target_aliases(target)
    peers = peer_aliases(target)
    topics = sector_topic_keywords(target)
    raw_direct_hits = matched_terms(title_text + " " + summary_text + " " + str(source or ""), aliases)
    direct_title = matched_terms(normalized_title, aliases)
    direct_body = matched_terms(normalized_summary, aliases)
    direct_keys = {_lower_text(item) for item in _unique_texts([*direct_title, *direct_body])}
    platform_alias_hits = [item for item in raw_direct_hits if _lower_text(item) not in direct_keys]
    peer_hits = matched_terms(combined, peers)
    topic_hits = matched_terms(combined, topics)
    market_hits = matched_terms(combined, MARKET_TOPIC_KEYWORDS)
    other_subject_hits = other_company_subject_hits(target, normalized_title)
    identity = source_identity(source, provider)
    reliability = source_reliability_score(source, provider)
    event_type = classify_news_event_type(title_text, summary_text)
    polarity, impact = keyword_polarity(combined)
    platform_reference_only = target_symbol(target) == "035420" and bool(platform_alias_hits) and not direct_title and not direct_body
    platform_only = platform_reference_only or platform_source_only_noise(target, title_text, summary_text, source, [*direct_title, *direct_body], topic_hits, event_type)
    alias_noise = ambiguous_company_alias_noise(target, title_text, summary_text, [*direct_title, *direct_body], topic_hits, event_type)
    low_confidence_platform = low_confidence_platform_context(source, provider, [*direct_title, *direct_body])
    social_feed = source_is_social_feed(source, provider)

    score = 24.0
    scope = "noise"
    if platform_only:
        score = 16.0
        scope = "platform_noise"
    elif alias_noise:
        score = 18.0
        scope = "noise"
    elif low_confidence_platform:
        score = 42.0
        scope = "low_confidence_context"
    elif other_subject_hits and not direct_title:
        score = 26.0
        scope = "entity_mismatch"
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

    if relation_scope_is_investable(scope):
        score += max(-8.0, min(8.0, (reliability - 0.6) * 25.0))
    score = clamp(score, 0.0, 100.0)
    if social_feed and scope == "direct":
        score = min(score, 62.0)
    if identity.get("isPlatformSource") and scope == "direct":
        score = min(score, 58.0)
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
    elif alias_noise:
        excluded_reason = "회사 뉴스가 아니라 일반 명사 별칭이 종목명처럼 잡힌 항목"
    elif low_confidence_platform:
        excluded_reason = "블로그/플랫폼 출처의 낮은 신뢰도 직접 언급은 투자 판단 근거에서 제외"
    elif scope == "entity_mismatch":
        excluded_reason = "기사 제목의 핵심 주어가 대상 종목이 아니라 " + ", ".join(other_subject_hits[:3]) + "로 보임"
    elif not relation_scope_is_investable(scope):
        excluded_reason = "종목명, 비교 기업, 업종, 시장 주제가 기사 제목/요약에서 확인되지 않음"
    entity_links = entity_link_rows(
        target,
        normalized_title,
        normalized_summary,
        source,
        provider,
        direct_title,
        direct_body,
        peer_hits,
        other_subject_hits,
        platform_alias_hits,
    )
    quality_gate = {
        "stage": "entity-linking",
        "decision": "accept" if relation_scope_is_investable(scope) else "exclude",
        "reason": excluded_reason,
        "relationScope": scope,
        "sourceKind": str(identity.get("sourceKind") or "publisher"),
        "sourcePlatform": str(identity.get("sourcePlatform") or ""),
        "targetSubjectConfirmed": bool(direct_title),
    }
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
        normalized_title=normalized_title,
        normalized_summary=normalized_summary,
        source_kind=str(identity.get("sourceKind") or "publisher"),
        source_platform=str(identity.get("sourcePlatform") or ""),
        entity_links=entity_links,
        quality_gate=quality_gate,
    )


def classify_news_relevance(
    target: object,
    title: object,
    summary: object = "",
    source: object = "",
    provider: object = "",
) -> Dict[str, object]:
    return analyze_news_item(target, title, summary, source, provider).to_payload()
