import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Tuple

from .investment_calendar import utc_iso
from .portfolio import utc_now_iso


KST = timezone(timedelta(hours=9))
EVENT_PATTERNS = [
    {
        "eventType": "adrListing",
        "label": "ADR/GDR 상장",
        "keywords": [
            "adr",
            "gdr",
            "american depositary receipt",
            "global depositary receipt",
            "depositary receipt",
            "f-6",
            "f6",
            "예탁증서",
            "미국예탁증서",
            "주식예탁증서",
        ],
        "importance": 92,
        "markets": ["US"],
    },
    {
        "eventType": "indexInclusion",
        "label": "지수 편입",
        "keywords": [
            "index inclusion",
            "included in",
            "adds",
            "added to",
            "msci",
            "s&p 500",
            "s&p500",
            "nasdaq-100",
            "nasdaq 100",
            "russell",
            "kospi200",
            "kosdaq150",
            "지수 편입",
            "편입 예정",
            "정기변경",
        ],
        "importance": 88,
        "markets": [],
    },
    {
        "eventType": "spinoff",
        "label": "분할/스핀오프",
        "keywords": [
            "spin-off",
            "spinoff",
            "split-off",
            "carve-out",
            "회사분할",
            "인적분할",
            "물적분할",
            "분할상장",
            "스핀오프",
        ],
        "importance": 86,
        "markets": [],
    },
    {
        "eventType": "capitalRaise",
        "label": "증자/자금조달",
        "keywords": [
            "capital raise",
            "share offering",
            "secondary offering",
            "follow-on offering",
            "rights offering",
            "at-the-market",
            "atm offering",
            "convertible bond",
            "convertible notes",
            "cb 발행",
            "bw 발행",
            "유상증자",
            "무상증자",
            "전환사채",
            "신주인수권부사채",
            "자금조달",
        ],
        "importance": 84,
        "markets": [],
    },
    {
        "eventType": "listing",
        "label": "상장/이전상장",
        "keywords": [
            "ipo",
            "listing",
            "direct listing",
            "uplisting",
            "dual listing",
            "new listing",
            "listed on",
            "상장",
            "신규상장",
            "이전상장",
            "재상장",
            "기업공개",
        ],
        "importance": 86,
        "markets": [],
    },
    {
        "eventType": "capitalMarketEvent",
        "label": "자본시장 이벤트",
        "keywords": [
            "stock split",
            "reverse split",
            "share split",
            "tender offer",
            "exchange offer",
            "buyback",
            "repurchase",
            "액면분할",
            "주식분할",
            "감자",
            "공개매수",
            "자사주",
        ],
        "importance": 82,
        "markets": [],
    },
]
OFFICIAL_SOURCE_TERMS = {"dart", "kind", "krx", "sec", "edgar", "nasdaq", "nyse", "investor relations", "ir"}
EVENT_PATTERN_BY_TYPE = {str(pattern["eventType"]): pattern for pattern in EVENT_PATTERNS}
STRUCTURED_DATE_KEYS = [
    "eventDate",
    "event_date",
    "listingDate",
    "listing_date",
    "expectedListingDate",
    "firstTradeDate",
    "effectiveDate",
    "recordDate",
    "exDate",
    "splitDate",
    "paymentDate",
    "payableDate",
    "rightsOfferingDate",
    "subscriptionDate",
]
STRUCTURED_EVENT_TYPE_KEYS = ["calendarEventType", "investmentCalendarEventType", "eventType", "event_type"]


def clean_text(value: object, limit: int = 1000) -> str:
    return " ".join(str(value or "").split()).strip()[:limit].rstrip()


def lower_text(value: object) -> str:
    return clean_text(value, 4000).casefold()


def unique_texts(values: Iterable[object], limit: int = 50) -> List[str]:
    result = []
    for value in values or []:
        text = clean_text(value, 191)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def evidence_payload(item: Dict[str, object]) -> Dict[str, object]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return dict(payload or {})


def nested_dicts(item: Dict[str, object]) -> List[Dict[str, object]]:
    payload = evidence_payload(item)
    values = [item, payload]
    for key in ["articleFacts", "filing", "disclosure", "event", "metadata"]:
        value = payload.get(key)
        if isinstance(value, dict):
            values.append(value)
    return values


def article_facts_text(payload: Dict[str, object]) -> str:
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    values = []
    for key in ["eventTakeaway", "numbers", "topics", "keySentences"]:
        value = facts.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return " ".join(values)


def evidence_text(item: Dict[str, object]) -> str:
    payload = evidence_payload(item)
    ai_analysis = payload.get("aiAnalysis") if isinstance(payload.get("aiAnalysis"), dict) else {}
    summary = ai_analysis.get("summary") if isinstance(ai_analysis.get("summary"), dict) else {}
    structured_values = []
    for container in nested_dicts(item):
        for key in [
            "form",
            "formType",
            "reportName",
            "report_nm",
            "market",
            "exchange",
            "calendarEventType",
            "investmentCalendarEventType",
        ]:
            structured_values.append(container.get(key))
    parts = [
        item.get("title"),
        item.get("summary"),
        item.get("articleSummaryKo"),
        payload.get("articleSummaryKo"),
        payload.get("analysisSummary"),
        payload.get("stockImpactReasonKo"),
        article_facts_text(payload),
        summary.get("briefKo"),
        " ".join(str(value) for value in summary.get("watchPoints") or []),
        " ".join(clean_text(value, 300) for value in structured_values if clean_text(value, 300)),
    ]
    return "\n".join(clean_text(part, 1500) for part in parts if clean_text(part))


def matched_event_pattern(text: str):
    lowered = lower_text(text)
    best = None
    best_matches = []
    for pattern in EVENT_PATTERNS:
        matches = [keyword for keyword in pattern["keywords"] if keyword.casefold() in lowered]
        if matches and len(matches) > len(best_matches):
            best = pattern
            best_matches = matches
    return best, best_matches


def known_event_type(value: object) -> str:
    event_type = clean_text(value, 64)
    return event_type if event_type in EVENT_PATTERN_BY_TYPE else ""


def source_parser(item: Dict[str, object]) -> str:
    payload = evidence_payload(item)
    haystack = lower_text(" ".join([
        str(item.get("source") or ""),
        str(item.get("url") or ""),
        str(item.get("kind") or ""),
        str(payload.get("sourcePlatform") or ""),
        str(payload.get("provider") or ""),
        str(payload.get("sourceKind") or ""),
    ]))
    if "sec" in haystack or "edgar" in haystack or "sec.gov" in haystack:
        return "sec-edgar"
    if "dart" in haystack or "opendart" in haystack:
        return "dart"
    if "krx" in haystack or "kind" in haystack:
        return "krx-kind"
    if "nasdaq" in haystack or "nyse" in haystack:
        return "exchange"
    if "investor relations" in haystack or re.search(r"\bir\b", haystack):
        return "issuer-ir"
    return "generic"


def structured_event_type(item: Dict[str, object], text: str) -> Tuple[str, List[str]]:
    lowered = lower_text(text)
    for container in nested_dicts(item):
        for key in STRUCTURED_EVENT_TYPE_KEYS:
            event_type = known_event_type(container.get(key))
            if event_type:
                return event_type, [key + "=" + event_type]
    parser = source_parser(item)
    form_text = lower_text(" ".join(
        clean_text(container.get(key), 120)
        for container in nested_dicts(item)
        for key in ["form", "formType", "filingType", "sourceKind", "reportName", "report_nm"]
        if clean_text(container.get(key), 120)
    ))
    if parser == "sec-edgar":
        if "f-6" in form_text or re.search(r"\bf6\b", form_text) or " f-6" in lowered:
            return "adrListing", ["SEC F-6"]
        if any(form in form_text for form in ["s-1", "f-1", "424b", "424 b", "20-f ipo"]):
            return "listing", ["SEC registration"]
        if any(term in lowered for term in ["stock split", "reverse split", "tender offer", "buyback", "repurchase"]):
            return "capitalMarketEvent", ["SEC capital-market action"]
    if parser in {"dart", "krx-kind"}:
        if any(term in lowered for term in ["예탁증서", "gdr", "adr"]):
            return "adrListing", [parser + " depositary receipt"]
        if any(term in lowered for term in ["유상증자", "무상증자", "전환사채", "신주인수권부사채", "cb 발행", "bw 발행"]):
            return "capitalRaise", [parser + " capital raise"]
        if any(term in lowered for term in ["회사분할", "인적분할", "물적분할", "분할상장", "스핀오프"]):
            return "spinoff", [parser + " spinoff"]
        if any(term in lowered for term in ["신규상장", "이전상장", "재상장", "상장예정", "상장 예정"]):
            return "listing", [parser + " listing"]
        if any(term in lowered for term in ["액면분할", "주식분할", "감자", "공개매수", "자사주"]):
            return "capitalMarketEvent", [parser + " capital-market action"]
    return "", []


def parse_year_month_day(year: int, month: int, day: int):
    try:
        return datetime(year, month, day, 9, 0, tzinfo=KST)
    except ValueError:
        return None


def parse_date_from_text(text: str, reference: datetime = None):
    reference = reference or datetime.now(KST)
    normalized = clean_text(text, 5000)
    match = re.search(r"(20\d{2})[-./년]\s*(\d{1,2})[-./월]\s*(\d{1,2})", normalized)
    if match:
        return parse_year_month_day(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", normalized)
    if match:
        parsed = parse_year_month_day(reference.year, int(match.group(1)), int(match.group(2)))
        if parsed and parsed < reference - timedelta(days=2):
            parsed = parse_year_month_day(reference.year + 1, int(match.group(1)), int(match.group(2)))
        return parsed
    match = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", normalized)
    if match:
        parsed = parse_year_month_day(reference.year, int(match.group(1)), int(match.group(2)))
        if parsed and parsed < reference - timedelta(days=2):
            parsed = parse_year_month_day(reference.year + 1, int(match.group(1)), int(match.group(2)))
        return parsed
    quarter = re.search(r"(20\d{2})\s*(?:년)?\s*(?:q([1-4])|([1-4])분기)", normalized, re.IGNORECASE)
    if quarter:
        q = int(quarter.group(2) or quarter.group(3))
        month = {1: 3, 2: 6, 3: 9, 4: 12}[q]
        return parse_year_month_day(int(quarter.group(1)), month, 30)
    return None


def parse_structured_date(value: object, reference: datetime = None):
    text = clean_text(value, 120)
    if not text:
        return None
    if re.fullmatch(r"\d{8}", text):
        return parse_year_month_day(int(text[:4]), int(text[4:6]), int(text[6:8]))
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return parse_date_from_text(text, reference)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=KST)
        return parsed.astimezone(KST)
    except ValueError:
        return parse_date_from_text(text, reference)


def event_date_from_item(item: Dict[str, object], text: str, reference: datetime = None):
    reference = reference or parse_reference_datetime(item)
    for container in nested_dicts(item):
        for key in STRUCTURED_DATE_KEYS:
            parsed = parse_structured_date(container.get(key), reference)
            if parsed:
                return parsed, key
    parsed = parse_date_from_text(text, reference)
    return parsed, "text" if parsed else ""


def parse_reference_datetime(item: Dict[str, object]):
    for key in ["publishedAt", "observedAt"]:
        text = clean_text(item.get(key), 80)
        if not text:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(KST)
        except ValueError:
            continue
    return datetime.now(KST)


def official_source(item: Dict[str, object]) -> bool:
    payload = evidence_payload(item)
    haystack = lower_text(" ".join([
        str(item.get("source") or ""),
        str(item.get("url") or ""),
        str(payload.get("sourcePlatform") or ""),
        str(payload.get("provider") or ""),
    ]))
    return any(term in haystack for term in OFFICIAL_SOURCE_TERMS)


@dataclass
class CalendarEventCandidate:
    event_id: str
    title: str
    event_type: str
    starts_at: str
    status: str
    importance: int
    symbols: List[str] = field(default_factory=list)
    markets: List[str] = field(default_factory=list)
    source: str = "research-evidence"
    source_url: str = ""
    notes: str = ""
    confidence: float = 0.0
    matched_keywords: List[str] = field(default_factory=list)
    source_evidence_id: str = ""
    review_candidate_id: str = ""
    review_reason: str = ""
    payload: Dict[str, object] = field(default_factory=dict)

    def to_calendar_payload(self, account_ids: Iterable[str] = None) -> Dict[str, object]:
        return {
            "eventId": self.event_id,
            "title": self.title,
            "eventType": self.event_type,
            "startsAt": self.starts_at,
            "timezone": "Asia/Seoul",
            "allDay": True,
            "status": self.status,
            "importance": self.importance,
            "symbols": list(self.symbols or []),
            "markets": list(self.markets or []),
            "accountIds": unique_texts(account_ids or [], 50),
            "source": self.source,
            "sourceUrl": self.source_url,
            "notes": self.notes,
            "reminderOffsetsMinutes": [1440, 180, 60, 0],
            "payload": dict(self.payload or {}),
        }

    def review_required(self) -> bool:
        return bool(self.review_reason)

    def to_review_payload(self, account_ids: Iterable[str] = None) -> Dict[str, object]:
        return {
            "candidateId": self.review_candidate_id,
            "proposedEventId": self.event_id,
            "title": self.title,
            "eventType": self.event_type,
            "startsAt": self.starts_at,
            "timezone": "Asia/Seoul",
            "allDay": True,
            "status": "pending",
            "reviewReason": self.review_reason or "needsReview",
            "importance": self.importance,
            "confidence": self.confidence,
            "symbols": list(self.symbols or []),
            "markets": list(self.markets or []),
            "accountIds": unique_texts(account_ids or [], 50),
            "source": self.source,
            "sourceUrl": self.source_url,
            "notes": self.notes,
            "reminderOffsetsMinutes": [1440, 180, 60, 0],
            "sourceEvidenceId": self.source_evidence_id,
            "payload": dict(self.payload or {}),
            "createdAt": utc_now_iso(),
            "updatedAt": "",
        }


def candidate_id(item: Dict[str, object], event_type: str, starts_at: str) -> str:
    token = "|".join([
        str(event_type or ""),
        str(item.get("symbol") or "").upper(),
        str(starts_at or ""),
        str(item.get("url") or ""),
        str(item.get("title") or ""),
    ])
    return "auto-special-event-" + hashlib.sha1(token.encode("utf-8")).hexdigest()[:24]


def review_candidate_id(item: Dict[str, object], event_type: str, starts_at: str) -> str:
    token = "|".join([
        str(event_type or ""),
        str(item.get("symbol") or "").upper(),
        str(starts_at or ""),
        str(item.get("url") or ""),
        str(item.get("title") or ""),
        str(item.get("evidenceId") or item.get("id") or ""),
    ])
    return "calendar-review-" + hashlib.sha1(token.encode("utf-8")).hexdigest()[:24]


def feedback_adjustment(event_type: str, feedback: Dict[str, object] = None) -> float:
    entry = (feedback or {}).get(event_type) if isinstance(feedback, dict) else None
    if not isinstance(entry, dict):
        return 0.0
    accepted = int(entry.get("accepted") or 0)
    rejected = int(entry.get("rejected") or 0)
    total = accepted + rejected
    if total < 3:
        return 0.0
    rate = accepted / total
    return max(-0.10, min(0.10, (rate - 0.5) * 0.18))


def confidence_for(
    item: Dict[str, object],
    matched_keywords: List[str],
    starts_at,
    official: bool,
    event_type: str = "",
    parser: str = "generic",
    date_source: str = "",
    structured_match: bool = False,
    feedback: Dict[str, object] = None,
) -> float:
    payload = evidence_payload(item)
    score = 0.35
    score += min(0.25, len(matched_keywords) * 0.08)
    if starts_at:
        score += 0.22
    if official:
        score += 0.18
    if parser != "generic":
        score += 0.06
    if structured_match:
        score += 0.06
    if date_source and date_source != "text":
        score += 0.04
    try:
        materiality = float(item.get("materialityScore") or payload.get("materialityScore") or 0)
    except (TypeError, ValueError):
        materiality = 0
    if materiality >= 80:
        score += 0.08
    score += feedback_adjustment(event_type, feedback)
    return round(max(0.01, min(score, 0.99)), 2)


def markets_for_candidate(item: Dict[str, object], pattern: Dict[str, object], text: str) -> List[str]:
    markets = list(pattern.get("markets") or [])
    lowered = lower_text(text)
    for token, market in [
        ("nasdaq", "NASDAQ"),
        ("nyse", "NYSE"),
        ("new york stock exchange", "NYSE"),
        ("krx", "KRX"),
        ("kospi", "KOSPI"),
        ("kosdaq", "KOSDAQ"),
        ("msci", "MSCI"),
        ("s&p", "S&P"),
        ("russell", "RUSSELL"),
    ]:
        if token in lowered and market not in markets:
            markets.append(market)
    market = clean_text(item.get("market"), 32).upper()
    if market and market not in markets:
        markets.append(market)
    return markets[:8]


def calendar_candidate_from_research_item(
    item: Dict[str, object],
    register_undated: bool = False,
    min_confidence: float = 0.45,
    include_review: bool = False,
    review_min_confidence: float = 0.35,
    feedback: Dict[str, object] = None,
):
    if not isinstance(item, dict):
        return None
    text = evidence_text(item)
    parser = source_parser(item)
    structured_type, structured_matches = structured_event_type(item, text)
    pattern, matched_keywords = matched_event_pattern(text)
    if structured_type:
        pattern = EVENT_PATTERN_BY_TYPE.get(structured_type)
        matched_keywords = unique_texts(list(structured_matches or []) + list(matched_keywords or []), 20)
    if not pattern:
        return None
    reference = parse_reference_datetime(item)
    event_type = str(pattern["eventType"])
    event_date, date_source = event_date_from_item(item, text, reference)
    missing_date = not event_date
    if missing_date and not register_undated:
        event_date_utc = ""
    elif not event_date:
        event_date = reference
        event_date_utc = utc_iso(event_date)
        date_source = "reference"
    else:
        event_date_utc = utc_iso(event_date)
    official = official_source(item)
    confidence = confidence_for(
        item,
        matched_keywords,
        event_date,
        official,
        event_type=event_type,
        parser=parser,
        date_source=date_source,
        structured_match=bool(structured_type),
        feedback=feedback,
    )
    review_reason = ""
    if missing_date and not register_undated:
        review_reason = "missingDate"
    elif confidence < float(min_confidence or 0):
        review_reason = "lowConfidence"
    if review_reason:
        if not include_review or confidence < float(review_min_confidence or 0):
            return None
    if not review_reason and confidence < float(min_confidence or 0):
        return None
    title = clean_text(item.get("title"), 180) or str(pattern["label"])
    symbol = clean_text(item.get("symbol"), 24).upper()
    symbols = [symbol] if symbol else []
    markets = markets_for_candidate(item, pattern, text)
    status = "active" if event_date and official and event_date >= reference - timedelta(days=2) else "tentative"
    if review_reason:
        status = "needsReview"
    source = clean_text(item.get("source") or "research-evidence", 120)
    evidence_id = clean_text(item.get("evidenceId") or item.get("id"), 191)
    notes = (
        str(pattern["label"])
        + " 후보를 뉴스/공시 근거에서 자동 추출했습니다. 확인 키워드: "
        + ", ".join(matched_keywords[:6])
        + ". 원문과 일정 확정 여부를 확인하세요."
    )
    if review_reason == "missingDate":
        notes += " 날짜가 명확하지 않아 후보함에서 검토해야 합니다."
    elif review_reason == "lowConfidence":
        notes += " 신뢰도가 자동 등록 기준보다 낮아 후보함에서 검토해야 합니다."
    payload = {
        "autoDetected": True,
        "detector": "research-evidence-calendar-extractor-v1",
        "confidence": confidence,
        "matchedKeywords": matched_keywords[:12],
        "sourceEvidenceId": evidence_id,
        "sourcePublishedAt": clean_text(item.get("publishedAt"), 80),
        "sourceObservedAt": clean_text(item.get("observedAt"), 80),
        "sourceTitle": title,
        "sourceKind": clean_text(item.get("kind"), 80),
        "officialSource": official,
        "sourceParser": parser,
        "dateSource": date_source,
        "structuredEventType": structured_type,
        "reviewRequired": bool(review_reason),
        "reviewReason": review_reason,
        "needsSourceRefresh": status != "active",
    }
    return CalendarEventCandidate(
        event_id=candidate_id(item, event_type, event_date_utc),
        review_candidate_id=review_candidate_id(item, event_type, event_date_utc),
        title=str(pattern["label"]) + ": " + title,
        event_type=event_type,
        starts_at=event_date_utc,
        status=status,
        importance=int(pattern["importance"]),
        symbols=symbols,
        markets=markets,
        source=source,
        source_url=clean_text(item.get("url"), 1000),
        notes=notes,
        confidence=confidence,
        matched_keywords=matched_keywords,
        source_evidence_id=evidence_id,
        review_reason=review_reason,
        payload=payload,
    )


def calendar_candidates_from_research_items(
    items: Iterable[Dict[str, object]],
    register_undated: bool = False,
    min_confidence: float = 0.45,
    feedback: Dict[str, object] = None,
) -> List[CalendarEventCandidate]:
    candidates = []
    seen = set()
    for item in items or []:
        candidate = calendar_candidate_from_research_item(
            item,
            register_undated,
            min_confidence,
            feedback=feedback,
        )
        if not candidate or candidate.review_required() or candidate.event_id in seen:
            continue
        seen.add(candidate.event_id)
        candidates.append(candidate)
    return candidates


def calendar_candidate_sets_from_research_items(
    items: Iterable[Dict[str, object]],
    register_undated: bool = False,
    min_confidence: float = 0.45,
    review_min_confidence: float = 0.35,
    feedback: Dict[str, object] = None,
) -> Dict[str, List[CalendarEventCandidate]]:
    ready = []
    review = []
    seen_ready = set()
    seen_review = set()
    for item in items or []:
        candidate = calendar_candidate_from_research_item(
            item,
            register_undated=register_undated,
            min_confidence=min_confidence,
            include_review=True,
            review_min_confidence=review_min_confidence,
            feedback=feedback,
        )
        if not candidate:
            continue
        if candidate.review_required():
            if candidate.review_candidate_id in seen_review:
                continue
            seen_review.add(candidate.review_candidate_id)
            review.append(candidate)
            continue
        if candidate.event_id in seen_ready:
            continue
        seen_ready.add(candidate.event_id)
        ready.append(candidate)
    return {"ready": ready, "review": review}
