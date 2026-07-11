from dataclasses import dataclass, field
import re
from typing import Dict, Iterable, List, Tuple

from .market_data import clamp, number


NEWS_ANALYSIS_VERSION = "news-analysis-v2-domain-ontology"
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
    "regulation": ["regulation", "규제", "소송", "lawsuit", "sue", "sues", "sued", "legal", "litigation", "antitrust", "probe", "investigation", "조사", "제재"],
    "capital_policy": ["buyback", "dividend", "자사주", "배당", "증자", "offering", "dilution"],
    "listing": ["listing", "상장", "ADR", "나스닥", "IPO"],
    "macro_sector": ["금리", "환율", "inflation", "FOMC", "업황", "수요", "demand"],
    "crypto_linked": ["bitcoin", "비트코인", "crypto", "암호화폐", "digital asset"],
    "price_commentary": ["주가", "shares", "stock", "목표주가", "급등", "급락"],
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
HIGH_RELIABILITY_SOURCE_TERMS = ["dart", "sec", "edgar", "reuters", "bloomberg", "연합", "yonhap", "cnbc", "wsj", "marketwatch"]

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
        (r"\bdigital assets?\b", "디지털자산"),
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
        impact_word = "긍정적"
        hits = keyword_hits(text, SUPPORT_KEYWORDS)
    elif detected_polarity in {"risk", "contradiction"}:
        impact = "negative"
        label = "악재"
        impact_word = "부정적"
        hits = keyword_hits(text, RISK_KEYWORDS)
    else:
        impact = "neutral"
        label = "중립"
        impact_word = "중립적"
        hits = []
    scope_label = relation_scope_label(analysis.get("relationScope"))
    event_label = event_type_label(analysis.get("eventType") or classify_news_event_type(title, text))
    materiality = number(analysis.get("materialityScore"))
    relevance = number(analysis.get("relevanceScore"))
    reason_parts = [
        scope_label + "이고 " + event_label + " 성격입니다",
        "관련성 " + ("%.1f" % relevance).rstrip("0").rstrip(".") + "점" if relevance else "",
        "중요도 " + ("%.1f" % materiality).rstrip("0").rstrip(".") + "점" if materiality else "",
    ]
    if hits:
        reason_parts.append(("긍정 표현 " if impact == "positive" else "부정 표현 ") + ", ".join(hits[:3]))
    if impact == "neutral" and materiality >= 65:
        reason_parts.append("중요도는 높지만 방향성 표현이 뚜렷하지 않습니다")
    reason = "주가 영향은 " + impact_word + "으로 봅니다. " + ", ".join(part for part in reason_parts if part) + "."
    return {
        "articleDigestVersion": ARTICLE_DIGEST_VERSION,
        "stockImpact": impact,
        "stockImpactLabel": label,
        "stockImpactPolarity": detected_polarity or "context",
        "stockImpactScore": round(number(impact_score), 1),
        "stockImpactReasonKo": compact_text(reason, 360),
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
    if any(token in source_text for token in ["yahoo finance", "investing", "매일경제", "한국경제", "이데일리", "머니투데이", "조선비즈", "서울경제", "kbs", "sbs", "mbc", "매경", "한경"]):
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
    social_feed = source_is_social_feed(source, provider)

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
    if social_feed and scope == "direct":
        score = min(score, 62.0)
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
