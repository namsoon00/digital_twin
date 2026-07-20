from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Dict, Iterable, List, Tuple

from .investment_research import NewsCollectionTarget, ResearchEvidence
from . import news_analysis as news_domain


NEWS_AI_ANALYSIS_VERSION = "news-ai-analysis-v4"
NEWS_AI_PROMPT_VERSION = "news-ai-prompt-v4"

IMPACT_LABELS = {
    "support": "호재",
    "risk": "악재",
    "mixed": "혼재",
    "neutral": "중립",
    "context": "중립",
    "unknown": "미확인",
}

STOCK_IMPACT_VALUES = {
    "support": "positive",
    "risk": "negative",
    "mixed": "neutral",
    "neutral": "neutral",
    "unknown": "neutral",
}

RISK_PHRASES = [
    "실적 우려",
    "전망 우려",
    "이익 우려",
    "수요 우려",
    "우려",
    "붕괴",
    "급락",
    "하락",
    "약세",
    "부담",
    "부진",
    "하회",
    "덮은",
    "불구",
    "매도",
    "목표가 하향",
    "하향",
    "적자",
    "손실",
    "소송",
    "규제",
    "조사",
    "downgrade",
    "miss",
    "missed",
    "lawsuit",
    "sue",
    "sues",
    "sued",
    "accuse",
    "accuses",
    "accused",
    "steal",
    "steals",
    "stealing",
    "stolen",
    "trade secret",
    "trade secrets",
    "core tech secrets",
    "legal",
    "litigation",
    "antitrust",
    "plunge",
    "falls",
    "fell",
    "drop",
    "drops",
    "weak",
    "concern",
    "concerns",
    "under pressure",
    "profit warning",
    "slide",
    "slides",
    "slid",
    "selloff",
    "decline",
    "declines",
    "down",
    "valuation debate",
]

SUPPORT_PHRASES = [
    "adr 호재",
    "호재",
    "급등",
    "상승",
    "강세",
    "상향",
    "수주",
    "계약",
    "승인",
    "개선",
    "최대",
    "흑자",
    "buyback",
    "dividend",
    "upgrade",
    "beat",
    "beats",
    "raised guidance",
    "strong demand",
    "surge",
    "record revenue",
    "record",
    "bargain",
    "cheap",
    "undervalued",
    "value score",
    "lean cheap",
]

CONTRAST_PHRASES = ["덮은", "불구", "에도", "despite", "but", "however", "yet"]


def compact_text(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def unique_texts(values: Iterable[object], limit: int = 6) -> List[str]:
    rows: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            rows.append(text)
            seen.add(key)
        if len(rows) >= limit:
            break
    return rows


SUMMARY_PREFIX_PATTERN = re.compile(
    r"^(?:(?:전체\s*)?본문|RSS/제공|RSS|제공|기사|AI\s*기사)\s*(?:요약|분석)\s*:\s*|^요약\s*:\s*",
    re.IGNORECASE,
)

SUMMARY_STOP_WORDS = {
    "관련", "기사", "내용", "핵심", "요약", "분석", "확인", "대상", "종목",
    "있습니다", "합니다", "됩니다", "입니다", "그리고", "하지만", "대한", "위한",
    "with", "from", "that", "this", "into", "after", "before", "about", "stock", "shares",
}


def clean_summary_text(value: object, limit: int = 760) -> str:
    text = news_domain.clean_article_summary_noise(compact_text(value, limit + 120))
    previous = ""
    while text and text != previous:
        previous = text
        text = SUMMARY_PREFIX_PATTERN.sub("", text).strip()
    text = re.sub(r"\s*/\s*(?=[가-힣A-Za-z])", ". ", text)
    text = re.sub(r"\s+", " ", text).strip(" .·;:-")
    return compact_text(text, limit)


def summary_sentence_candidates(value: object) -> List[str]:
    text = clean_summary_text(value, 1200)
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|[\r\n]+|\s*[•·]\s+", text)
    return [clean_summary_text(part, 520) for part in parts if clean_summary_text(part, 520)]


def summary_tokens(value: object) -> set:
    text = clean_summary_text(value, 1200).casefold()
    tokens = re.findall(r"[가-힣]{2,}|[a-z][a-z0-9'-]{2,}|[$€£]?\d[\d,.]*(?:%|점|주|원|달러)?", text)
    return {token for token in tokens if token not in SUMMARY_STOP_WORDS}


def summary_texts_similar(left: object, right: object) -> bool:
    left_text = re.sub(r"[^0-9a-z가-힣]+", "", clean_summary_text(left, 1200).casefold())
    right_text = re.sub(r"[^0-9a-z가-힣]+", "", clean_summary_text(right, 1200).casefold())
    if not left_text or not right_text:
        return False
    shorter, longer = sorted([left_text, right_text], key=len)
    if len(shorter) >= 16 and shorter in longer:
        return True
    left_tokens = summary_tokens(left)
    right_tokens = summary_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens.intersection(right_tokens)) / max(1, min(len(left_tokens), len(right_tokens)))
    return overlap >= 0.78 and len(left_tokens.intersection(right_tokens)) >= 3


def semantically_unique_texts(
    values: Iterable[object],
    limit: int = 6,
    against: Iterable[object] = (),
) -> List[str]:
    rows: List[str] = []
    comparison = [clean_summary_text(value, 760) for value in against or [] if clean_summary_text(value, 760)]
    for value in values or []:
        candidates = summary_sentence_candidates(value) or [clean_summary_text(value, 520)]
        for candidate in candidates:
            if not candidate or any(summary_texts_similar(candidate, existing) for existing in comparison + rows):
                continue
            rows.append(candidate)
            if len(rows) >= limit:
                return rows
    return rows


def normalized_summary_payload(summary: Dict[str, object], fallback_summary: Dict[str, object]) -> Dict[str, object]:
    summary = summary if isinstance(summary, dict) else {}
    fallback_summary = fallback_summary if isinstance(fallback_summary, dict) else {}
    raw_one_line = (
        summary.get("oneLineKo")
        or summary.get("one_line_ko")
        or fallback_summary.get("oneLineKo")
        or ""
    )
    raw_brief = (
        summary.get("briefKo")
        or summary.get("brief_ko")
        or fallback_summary.get("briefKo")
        or raw_one_line
    )
    brief_rows = semantically_unique_texts(summary_sentence_candidates(raw_brief), 3)
    brief = compact_text(". ".join(row.rstrip(". ") for row in brief_rows), 520)
    one_line = clean_summary_text(raw_one_line, 220) or (brief_rows[0] if brief_rows else "")
    takeaways = semantically_unique_texts(
        summary.get("keyTakeaways")
        or summary.get("key_takeaways")
        or fallback_summary.get("keyTakeaways")
        or [],
        4,
        against=[one_line, brief],
    )
    why_it_matters = clean_summary_text(
        summary.get("whyItMatters")
        or summary.get("why_it_matters")
        or fallback_summary.get("whyItMatters")
        or "",
        360,
    )
    if summary_texts_similar(why_it_matters, brief):
        fallback_why = clean_summary_text(fallback_summary.get("whyItMatters"), 360)
        why_it_matters = "" if summary_texts_similar(fallback_why, brief) else fallback_why
    watch_points = semantically_unique_texts(
        summary.get("watchPoints")
        or summary.get("watch_points")
        or fallback_summary.get("watchPoints")
        or [],
        4,
        against=[one_line, brief, why_it_matters],
    )
    return {
        "oneLineKo": one_line,
        "briefKo": brief or one_line,
        "keyTakeaways": takeaways,
        "whyItMatters": why_it_matters,
        "watchPoints": watch_points,
    }


def keyword_hits(text: object, phrases: Iterable[str], limit: int = 6) -> List[str]:
    lowered = str(text or "").casefold()
    rows: List[str] = []
    for phrase in phrases:
        term = str(phrase or "").strip()
        if not term:
            continue
        term_lower = term.casefold()
        if re.fullmatch(r"[a-z0-9][a-z0-9 .&+/'-]*", term_lower):
            pattern = r"(?<![a-z0-9])" + re.escape(term_lower) + r"(?![a-z0-9])"
            matched = any(
                not keyword_match_is_boilerplate(term_lower, lowered[max(0, match.start() - 32): match.end() + 42])
                for match in re.finditer(pattern, lowered)
            )
        else:
            matched = term_lower in lowered
        if matched and term not in rows:
            rows.append(term)
        if len(rows) >= limit:
            break
    return rows


def keyword_match_is_boilerplate(term: str, snippet: str) -> bool:
    if term in {"miss", "missed"}:
        return bool(re.search(r"\b(?:never|don't|dont|do\s+not|not\s+to)\s+\w{0,12}\s*miss(?:ed)?\b", snippet)) or bool(
            re.search(r"\bmiss(?:ed)?\s+important\s+updates?\b", snippet)
        )
    if term == "record":
        return "recorded for your portfolio" in snippet
    return False


def source_text_hash(*values: object) -> str:
    text = "\n".join(str(value or "").strip() for value in values if str(value or "").strip())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def analysis_payload_from_evidence(evidence: ResearchEvidence) -> Dict[str, object]:
    return evidence.raw_payload if isinstance(evidence.raw_payload, dict) else {}


def article_facts(payload: Dict[str, object]) -> Dict[str, object]:
    facts = payload.get("articleFacts") if isinstance(payload, dict) else {}
    return facts if isinstance(facts, dict) else {}


def normalized_impact_polarity(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"support", "positive", "bullish", "호재", "긍정", "positive_news"}:
        return "support"
    if text in {"risk", "negative", "bearish", "악재", "부정", "negative_news"}:
        return "risk"
    if text in {"mixed", "혼재"}:
        return "mixed"
    if text in {"context", "neutral", "중립"}:
        return "context"
    return ""


def news_analysis_conflict_payload(
    payload: Dict[str, object],
    facts_payload: Dict[str, object],
    ai_impact_polarity: object,
) -> Dict[str, object]:
    ai_polarity = normalized_impact_polarity(ai_impact_polarity)
    if ai_polarity not in {"support", "risk", "context"}:
        return {}
    candidates = [
        ("기존 주가 영향", payload.get("stockImpactPolarity")),
        ("기존 주가 영향", payload.get("stockImpact")),
        ("기사 사실", facts_payload.get("stockImpactPolarity")),
        ("기사 사실", facts_payload.get("stockImpact")),
        ("기사 사실", facts_payload.get("impactPolarity")),
    ]
    for source, value in candidates:
        existing_polarity = normalized_impact_polarity(value)
        if existing_polarity not in {"support", "risk"}:
            continue
        if existing_polarity == ai_polarity:
            return {}
        return {
            "analysisConflict": True,
            "analysisConflictSource": source,
            "analysisConflictExistingPolarity": existing_polarity,
            "analysisConflictAiPolarity": ai_polarity,
            "analysisConflictReasonKo": (
                source
                + "은 "
                + IMPACT_LABELS.get(existing_polarity, existing_polarity)
                + "로 표시됐지만 기사 AI 분석은 "
                + IMPACT_LABELS.get(ai_polarity, ai_polarity)
                + "로 판단했습니다."
            ),
            "dataQualityRisk": "article-ai-impact-conflict",
            "dataState": "partial",
            "validationState": "conditional",
        }
    return {}


def article_text_parts(evidence: ResearchEvidence) -> Tuple[str, str, str, str]:
    payload = analysis_payload_from_evidence(evidence)
    facts = article_facts(payload)
    title = compact_text(evidence.title, 360)
    body = compact_text(payload.get("articleTextPreview") or facts.get("bodyPreview") or "", 5000)
    feed_summary = compact_text(
        facts.get("feedSummaryPreview")
        or payload.get("normalizedSummary")
        or evidence.summary
        or payload.get("articleSummaryKo")
        or "",
        1600,
    )
    read_scope = "body" if body and bool(facts.get("bodyAvailable")) else "title+rss-summary"
    return title, body, feed_summary, read_scope


@dataclass(frozen=True)
class NewsAiAnalysis:
    status: str = "ok"
    version: str = NEWS_AI_ANALYSIS_VERSION
    prompt_version: str = NEWS_AI_PROMPT_VERSION
    model: str = "local-news-semantic-analyzer-v1"
    read_scope: str = "title+rss-summary"
    source_text_hash: str = ""
    relation_scope: str = ""
    event_type: str = "general"
    impact_polarity: str = "neutral"
    impact_label_ko: str = "중립"
    relevance_state: str = "context"
    source_trust_state: str = "unknown"
    materiality_state: str = "context"
    data_state: str = "partial"
    validation_state: str = "conditional"
    summary: Dict[str, object] = field(default_factory=dict)
    risk_signals: List[str] = field(default_factory=list)
    support_signals: List[str] = field(default_factory=list)
    contrast_signals: List[str] = field(default_factory=list)
    key_numbers: List[str] = field(default_factory=list)
    rationale_ko: str = ""
    impact_reason_ko: str = ""
    portfolio_implication_ko: str = ""
    action_boundary_ko: str = ""
    validation_reason_ko: str = ""
    needs_review: bool = False
    reasoning_limitations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "version": self.version,
            "promptVersion": self.prompt_version,
            "model": self.model,
            "readScope": self.read_scope,
            "sourceTextHash": self.source_text_hash,
            "relationScope": self.relation_scope,
            "eventType": self.event_type,
            "impactPolarity": self.impact_polarity,
            "impactLabelKo": self.impact_label_ko,
            "relevanceState": self.relevance_state,
            "sourceTrustState": self.source_trust_state,
            "materialityState": self.materiality_state,
            "dataState": self.data_state,
            "validationState": self.validation_state,
            "summary": dict(self.summary or {}),
            "riskSignals": list(self.risk_signals or []),
            "supportSignals": list(self.support_signals or []),
            "contrastSignals": list(self.contrast_signals or []),
            "keyNumbers": list(self.key_numbers or []),
            "rationaleKo": compact_text(self.rationale_ko, 760),
            "impactReasonKo": compact_text(self.impact_reason_ko, 520),
            "portfolioImplicationKo": compact_text(self.portfolio_implication_ko, 520),
            "actionBoundaryKo": compact_text(self.action_boundary_ko, 360),
            "validationReasonKo": compact_text(self.validation_reason_ko, 360),
            "needsReview": bool(self.needs_review),
            "reasoningLimitations": list(self.reasoning_limitations or []),
        }


def normalize_ai_analysis(payload: Dict[str, object], fallback: NewsAiAnalysis = None) -> NewsAiAnalysis:
    fallback = fallback or NewsAiAnalysis()
    payload = payload if isinstance(payload, dict) else {}
    polarity = str(payload.get("impactPolarity") or payload.get("impact_polarity") or fallback.impact_polarity).strip().lower()
    if polarity not in IMPACT_LABELS:
        polarity = "unknown"
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    fallback_summary = fallback.summary if isinstance(fallback.summary, dict) else {}
    normalized_summary = normalized_summary_payload(summary, fallback_summary)
    state_payload = dict(fallback.to_dict())
    state_payload.update(payload)
    states = news_domain.news_state_payload(state_payload)
    return NewsAiAnalysis(
        status=str(payload.get("status") or fallback.status or "ok"),
        version=str(payload.get("version") or fallback.version or NEWS_AI_ANALYSIS_VERSION),
        prompt_version=str(payload.get("promptVersion") or payload.get("prompt_version") or fallback.prompt_version or NEWS_AI_PROMPT_VERSION),
        model=str(payload.get("model") or fallback.model or "local-news-semantic-analyzer-v1"),
        read_scope=str(payload.get("readScope") or payload.get("read_scope") or fallback.read_scope or "title+rss-summary"),
        source_text_hash=str(payload.get("sourceTextHash") or payload.get("source_text_hash") or fallback.source_text_hash or ""),
        relation_scope=str(payload.get("relationScope") or payload.get("relation_scope") or fallback.relation_scope or ""),
        event_type=str(payload.get("eventType") or payload.get("event_type") or fallback.event_type or "general"),
        impact_polarity=polarity,
        impact_label_ko=str(payload.get("impactLabelKo") or payload.get("impact_label_ko") or IMPACT_LABELS.get(polarity, "미확인")),
        relevance_state=states["relevanceState"],
        source_trust_state=states["sourceTrustState"],
        materiality_state=states["materialityState"],
        data_state=states["dataState"],
        validation_state=states["validationState"],
        summary=normalized_summary,
        risk_signals=unique_texts(payload.get("riskSignals") or payload.get("risk_signals") or fallback.risk_signals, 6),
        support_signals=unique_texts(payload.get("supportSignals") or payload.get("support_signals") or fallback.support_signals, 6),
        contrast_signals=unique_texts(payload.get("contrastSignals") or payload.get("contrast_signals") or fallback.contrast_signals, 6),
        key_numbers=unique_texts(payload.get("keyNumbers") or payload.get("key_numbers") or fallback.key_numbers, 6),
        rationale_ko=compact_text(payload.get("rationaleKo") or payload.get("rationale_ko") or fallback.rationale_ko, 760),
        impact_reason_ko=compact_text(payload.get("impactReasonKo") or payload.get("impact_reason_ko") or fallback.impact_reason_ko, 520),
        portfolio_implication_ko=compact_text(payload.get("portfolioImplicationKo") or payload.get("portfolio_implication_ko") or fallback.portfolio_implication_ko, 520),
        action_boundary_ko=compact_text(payload.get("actionBoundaryKo") or payload.get("action_boundary_ko") or fallback.action_boundary_ko, 360),
        validation_reason_ko=compact_text(
            payload.get("validationReasonKo")
            or payload.get("validation_reason_ko")
            or payload.get("confidenceReasonKo")
            or payload.get("confidence_reason_ko")
            or fallback.validation_reason_ko,
            360,
        ),
        needs_review=bool(payload.get("needsReview") if "needsReview" in payload else fallback.needs_review),
        reasoning_limitations=unique_texts(payload.get("reasoningLimitations") or payload.get("reasoning_limitations") or fallback.reasoning_limitations, 5),
    )


def infer_impact_polarity(text: object) -> Tuple[str, List[str], List[str], List[str]]:
    risk_hits = keyword_hits(text, RISK_PHRASES)
    support_hits = keyword_hits(text, SUPPORT_PHRASES)
    contrast_hits = keyword_hits(text, CONTRAST_PHRASES)
    if risk_hits and support_hits:
        lowered = str(text or "").casefold()
        markers = [
            (lowered.rfind(str(phrase).casefold()), str(phrase))
            for phrase in CONTRAST_PHRASES
            if lowered.rfind(str(phrase).casefold()) >= 0
        ]
        if markers:
            marker_index, marker = max(markers, key=lambda item: item[0])
            trailing_text = lowered[marker_index + len(marker):]
            trailing_risk = keyword_hits(trailing_text, RISK_PHRASES)
            trailing_support = keyword_hits(trailing_text, SUPPORT_PHRASES)
            if trailing_risk and not trailing_support:
                return "risk", risk_hits, support_hits, contrast_hits
            if trailing_support and not trailing_risk:
                return "support", risk_hits, support_hits, contrast_hits
        return "mixed", risk_hits, support_hits, contrast_hits
    if risk_hits:
        return "risk", risk_hits, support_hits, contrast_hits
    if support_hits:
        return "support", risk_hits, support_hits, contrast_hits
    return "neutral", risk_hits, support_hits, contrast_hits


def signal_summary_text(risk_hits: Iterable[str], support_hits: Iterable[str], contrast_hits: Iterable[str]) -> str:
    parts = []
    risk = unique_texts(risk_hits, 3)
    support = unique_texts(support_hits, 3)
    contrast = unique_texts(contrast_hits, 2)
    if risk:
        parts.append("위험 신호 " + ", ".join(risk))
    if support:
        parts.append("우호 신호 " + ", ".join(support))
    if contrast:
        parts.append("상반 문맥 " + ", ".join(contrast))
    return " · ".join(parts) if parts else "명시적 방향 신호 없음"


def impact_reason_text(
    target_name: str,
    polarity: str,
    event_type: str,
    risk_hits: Iterable[str],
    support_hits: Iterable[str],
    contrast_hits: Iterable[str],
    key_numbers: Iterable[str],
) -> str:
    event_label = news_domain.event_type_label(event_type)
    signals = signal_summary_text(risk_hits, support_hits, contrast_hits)
    numbers = unique_texts(key_numbers, 3)
    number_text = (" 확인 수치: " + ", ".join(numbers) + ".") if numbers else ""
    if polarity == "risk":
        risk_rows = {str(item or "").casefold() for item in risk_hits or []}
        price_drop_prefix = "주가 하락과 " if risk_rows.intersection({"slide", "slides", "slid", "decline", "declines", "down", "drop", "drops", "falls", "fell", "plunge"}) else ""
        return compact_text(target_name + "에는 " + price_drop_prefix + event_label + " 관련 부담이 우세합니다. " + signals + "가 확인돼 단기 투자심리와 가격 반응을 낮출 수 있습니다." + number_text, 520)
    if polarity == "support":
        return compact_text(target_name + "에는 " + event_label + " 관련 우호 재료가 확인됩니다. " + signals + "가 실제 가격·거래량 반응으로 이어지는지 봐야 합니다." + number_text, 520)
    if polarity == "mixed":
        return compact_text(target_name + "에는 우호 논리와 위험 신호가 함께 있습니다. " + signals + "가 충돌해 다음 가격 반응 전까지 방향을 단정하기 어렵습니다." + number_text, 520)
    return compact_text(target_name + " 관련 새 정보지만 기사 안의 가격 방향성은 제한적입니다. " + event_label + " 이슈가 실제 수급 변화로 이어지는지 확인하는 근거로 봅니다." + number_text, 520)


def portfolio_implication_text(target_name: str, polarity: str, event_type: str) -> str:
    event_label = news_domain.event_type_label(event_type)
    if polarity == "risk":
        return target_name + " 보유·관심 기준으로는 " + event_label + " 부담이 가격 하락이나 변동성 확대로 이어지는지 먼저 확인해야 합니다."
    if polarity == "support":
        return target_name + " 보유·관심 기준으로는 우호 재료지만, 가격 상승이 거래량을 동반하는지 확인해야 의미가 커집니다."
    if polarity == "mixed":
        return target_name + " 보유·관심 기준으로는 저가 매수 논리와 추가 하락 위험이 동시에 있어 실적·거래량 확인 전 판단 강도를 낮춥니다."
    return target_name + " 보유·관심 기준으로는 당장 방향성 근거보다 이벤트 확인용 정보에 가깝습니다."


def action_boundary_text(polarity: str, read_scope: str) -> str:
    scope_note = "본문 기반" if read_scope == "body" else "제목/RSS 기반"
    if polarity == "risk":
        return scope_note + " 경계 신호입니다. 자동 매매 판단이 아니라 다음 장 가격, 거래량, 반대 뉴스 확인 조건입니다."
    if polarity == "support":
        return scope_note + " 우호 신호입니다. 자동 진입 판단이 아니라 가격 반응과 거래량 동반 여부 확인 조건입니다."
    if polarity == "mixed":
        return scope_note + " 혼재 신호입니다. 방향을 정하기보다 상반 근거와 실적 반응을 분리해 확인해야 합니다."
    return scope_note + " 확인 신호입니다. 투자 방향을 단정하지 않고 후속 가격·거래량 반응만 점검합니다."


def validation_reason_text(read_scope: str, relation_scope: str, risk_hits: Iterable[str], support_hits: Iterable[str]) -> str:
    parts = []
    parts.append("본문을 읽음" if read_scope == "body" else "본문 미확보")
    if relation_scope:
        parts.append("관계 범위 " + relation_scope)
    if list(risk_hits or []) or list(support_hits or []):
        parts.append("방향 키워드 확인")
    else:
        parts.append("방향 키워드 약함")
    return ", ".join(parts)


def local_news_ai_analysis(target: NewsCollectionTarget, evidence: ResearchEvidence) -> NewsAiAnalysis:
    payload = analysis_payload_from_evidence(evidence)
    facts = article_facts(payload)
    title, body, feed_summary, read_scope = article_text_parts(evidence)
    source_text = " ".join(part for part in [title, body or feed_summary] if part)
    polarity, risk_hits, support_hits, contrast_hits = infer_impact_polarity(source_text)
    event_type = str(payload.get("eventType") or facts.get("eventType") or news_domain.classify_news_event_type(title, source_text) or "general")
    relation_scope = str(payload.get("relationScope") or facts.get("relationScope") or "").strip()
    state_source = {
        **payload,
        **facts,
        "relationScope": relation_scope,
        "eventType": event_type,
        "impactPolarity": polarity,
        "articleReadStatus": "body" if read_scope == "body" else "feed-summary",
    }
    states = news_domain.news_state_payload(state_source)
    if polarity in {"risk", "support", "mixed"} and states["materialityState"] == "context":
        states["materialityState"] = news_domain.news_materiality_state(
            event_type,
            relation_scope=relation_scope,
            impact_polarity=polarity,
            source_trust_state=states["sourceTrustState"],
        )
    if read_scope != "body" and states["dataState"] == "sufficient":
        states["dataState"] = "partial"
    if read_scope != "body" or polarity in {"mixed", "unknown"}:
        states["validationState"] = "conditional"
    key_numbers = news_domain.numeric_highlights(source_text, 6)
    target_name = target.name or evidence.symbol or "대상 종목"
    label = IMPACT_LABELS.get(polarity, "중립")
    evidence_scope = "본문" if read_scope == "body" else "제목/RSS 요약"
    analysis_context = {
        "relationScope": relation_scope,
        "eventType": event_type,
        **states,
    }
    article_summary = news_domain.korean_article_summary(target, title, body, feed_summary, analysis_context)
    article_takeaway = news_domain.article_event_takeaway(target, title, body, feed_summary)
    signal_text = signal_summary_text(risk_hits, support_hits, contrast_hits)
    impact_reason = impact_reason_text(target_name, polarity, event_type, risk_hits, support_hits, contrast_hits, key_numbers)
    portfolio_implication = portfolio_implication_text(target_name, polarity, event_type)
    action_boundary = action_boundary_text(polarity, read_scope)
    validation_reason = validation_reason_text(read_scope, relation_scope, risk_hits, support_hits)
    if polarity == "risk":
        one_line = article_takeaway or target_name + " 기사에서 위험 신호가 더 강하게 확인됩니다."
        fallback_brief = impact_reason
    elif polarity == "support":
        one_line = article_takeaway or target_name + " 기사에서 우호 신호가 확인됩니다."
        fallback_brief = impact_reason
    elif polarity == "mixed":
        one_line = article_takeaway or target_name + " 기사에 우호·위험 신호가 함께 있습니다."
        fallback_brief = impact_reason
    else:
        one_line = article_takeaway or target_name + " 관련 새 정보지만 방향성은 중립입니다."
        fallback_brief = impact_reason
    source_is_korean = news_domain.contains_hangul(body or feed_summary or title)
    brief_source = (article_summary if source_is_korean else article_takeaway) or article_summary
    brief = compact_text(brief_source, 520) or fallback_brief
    takeaways = summary_sentence_candidates(article_summary)[1:4] if source_is_korean else []
    watch_points = [news_domain.impact_watch_text(
        STOCK_IMPACT_VALUES.get(polarity, "neutral"),
        states["materialityState"],
        source_text,
    )]
    if read_scope != "body":
        watch_points.insert(0, "원문 본문 확보")
    if event_type in {"earnings", "guidance"}:
        watch_points.append("실적 전망 변화")
    limitations = [] if read_scope == "body" else ["본문 원문 미수집으로 제목/RSS 요약 기반 분석"]
    if contrast_hits:
        limitations.append("상반된 표현이 있어 문맥 확인 필요")
    normalized_summary = normalized_summary_payload({
        "oneLineKo": compact_text(one_line, 220),
        "briefKo": compact_text(brief, 520),
        "keyTakeaways": takeaways,
        "whyItMatters": compact_text(news_domain.impact_channel_text(event_type, source_text), 360),
        "watchPoints": watch_points,
    }, {})
    return NewsAiAnalysis(
        read_scope=read_scope,
        source_text_hash=source_text_hash(title, body, feed_summary),
        relation_scope=relation_scope,
        event_type=event_type,
        impact_polarity=polarity,
        impact_label_ko=label,
        relevance_state=states["relevanceState"],
        source_trust_state=states["sourceTrustState"],
        materiality_state=states["materialityState"],
        data_state=states["dataState"],
        validation_state=states["validationState"],
        summary=normalized_summary,
        risk_signals=risk_hits,
        support_signals=support_hits,
        contrast_signals=contrast_hits,
        key_numbers=key_numbers,
        rationale_ko=compact_text(
            "AI 기사 분석: "
            + evidence_scope
            + "에서 "
            + signal_text
            + "을 근거로 "
            + label
            + "로 분류했습니다.",
            760,
        ),
        impact_reason_ko=impact_reason,
        portfolio_implication_ko=portfolio_implication,
        action_boundary_ko=action_boundary,
        validation_reason_ko=validation_reason,
        needs_review=read_scope != "body" or polarity in {"mixed", "unknown"},
        reasoning_limitations=unique_texts(limitations, 5),
    )


def ai_analysis_existing_hash(payload: Dict[str, object]) -> str:
    analysis = payload.get("aiAnalysis") if isinstance(payload, dict) else {}
    if not isinstance(analysis, dict):
        return ""
    return str(analysis.get("sourceTextHash") or "").strip()


def news_ai_analysis_is_current(evidence: ResearchEvidence) -> bool:
    payload = analysis_payload_from_evidence(evidence)
    analysis = payload.get("aiAnalysis") if isinstance(payload, dict) else {}
    if not isinstance(analysis, dict):
        return False
    title, body, feed_summary, _read_scope = article_text_parts(evidence)
    return (
        str(analysis.get("version") or "") == NEWS_AI_ANALYSIS_VERSION
        and str(analysis.get("sourceTextHash") or "") == source_text_hash(title, body, feed_summary)
    )


def apply_news_ai_analysis(evidence: ResearchEvidence, analysis_payload: Dict[str, object]) -> ResearchEvidence:
    payload = dict(evidence.raw_payload or {})
    fallback = local_news_ai_analysis(
        NewsCollectionTarget(evidence.symbol, evidence.symbol),
        evidence,
    )
    analysis = normalize_ai_analysis(analysis_payload, fallback)
    analysis_dict = analysis.to_dict()
    summary = analysis_dict.get("summary") if isinstance(analysis_dict.get("summary"), dict) else {}
    impact_polarity = str(analysis_dict.get("impactPolarity") or "neutral")
    article_facts_payload = article_facts(payload)
    if article_facts_payload and not article_facts_payload.get("bodyAvailable") and payload.get("articleReadStatus") == "body":
        payload["articleReadStatus"] = "feed-summary"
        article_facts_payload["readStatus"] = "feed-summary"
        article_facts_payload["readStatusLabel"] = news_domain.article_read_status_label("feed-summary")
        article_facts_payload["missingBodyReason"] = article_facts_payload.get("missingBodyReason") or news_domain.article_missing_body_reason("feed-summary", "")
        payload["articleFacts"] = article_facts_payload
    conflict_payload = news_analysis_conflict_payload(payload, article_facts_payload, impact_polarity)
    for key in [
        "analysisConflict",
        "analysisConflictSource",
        "analysisConflictExistingPolarity",
        "analysisConflictAiPolarity",
        "analysisConflictReasonKo",
        "dataQualityRisk",
        "dataQualityRiskScore",
        "confidenceReasonKo",
        "stockImpactScore",
        "materialityScore",
        "relevanceScore",
        "sourceReliability",
    ]:
        payload.pop(key, None)
    if conflict_payload:
        payload.update(conflict_payload)
    payload["aiAnalysis"] = analysis_dict
    payload["articleAiAnalysisVersion"] = NEWS_AI_ANALYSIS_VERSION
    payload["articleSummaryKo"] = summary.get("briefKo") or summary.get("oneLineKo") or payload.get("articleSummaryKo") or evidence.summary
    payload["stockImpact"] = STOCK_IMPACT_VALUES.get(impact_polarity, "neutral")
    payload["stockImpactLabel"] = analysis_dict.get("impactLabelKo") or IMPACT_LABELS.get(impact_polarity, "중립")
    payload["stockImpactPolarity"] = impact_polarity if impact_polarity in {"support", "risk"} else "context"
    payload["stockImpactReasonKo"] = analysis_dict.get("impactReasonKo") or analysis_dict.get("rationaleKo") or payload.get("stockImpactReasonKo") or ""
    payload["portfolioImplicationKo"] = analysis_dict.get("portfolioImplicationKo") or payload.get("portfolioImplicationKo") or ""
    payload["actionBoundaryKo"] = analysis_dict.get("actionBoundaryKo") or payload.get("actionBoundaryKo") or ""
    payload["validationReasonKo"] = analysis_dict.get("validationReasonKo") or payload.get("validationReasonKo") or ""
    for key in ["relevanceState", "sourceTrustState", "materialityState", "dataState", "validationState"]:
        payload[key] = analysis_dict.get(key) or payload.get(key) or ""
    if analysis_dict.get("relationScope") and not payload.get("relationScope"):
        payload["relationScope"] = analysis_dict.get("relationScope")
    if analysis_dict.get("eventType") and not payload.get("eventType"):
        payload["eventType"] = analysis_dict.get("eventType")
    if article_facts_payload:
        article_facts_payload.setdefault("preAiStockImpact", article_facts_payload.get("stockImpact"))
        article_facts_payload.setdefault("preAiStockImpactPolarity", article_facts_payload.get("stockImpactPolarity") or article_facts_payload.get("impactPolarity"))
        article_facts_payload.setdefault("preAiStockImpactLabel", article_facts_payload.get("stockImpactLabel"))
        article_facts_payload.update({
            "summaryKo": payload["articleSummaryKo"],
            "eventTakeaway": summary.get("oneLineKo") or article_facts_payload.get("eventTakeaway") or "",
            "impactReasonKo": payload["stockImpactReasonKo"],
            "stockImpact": payload["stockImpact"],
            "stockImpactPolarity": payload["stockImpactPolarity"],
            "stockImpactLabel": payload["stockImpactLabel"],
            "stockImpactReasonKo": payload["stockImpactReasonKo"],
            "analysisConflict": bool(conflict_payload),
        })
        if conflict_payload:
            article_facts_payload.update(conflict_payload)
        payload["articleFacts"] = article_facts_payload
    payload = news_domain.public_news_payload(payload)
    evidence_polarity = impact_polarity if impact_polarity in {"support", "risk"} else "context"
    states = news_domain.news_state_payload(payload)
    return ResearchEvidence(
        evidence_id=evidence.evidence_id,
        symbol=evidence.symbol,
        kind=evidence.kind,
        source=evidence.source,
        title=evidence.title,
        summary=compact_text(payload.get("articleSummaryKo") or evidence.summary, 520),
        url=evidence.url,
        observed_at=evidence.observed_at,
        polarity=evidence_polarity,
        published_at=evidence.published_at,
        raw_payload=payload,
        source_trust_state=states["sourceTrustState"],
        materiality_state=states["materialityState"],
        data_state=states["dataState"],
        validation_state=states["validationState"],
    )


def build_news_ai_analysis_prompt(target: NewsCollectionTarget, evidence: ResearchEvidence) -> str:
    payload = analysis_payload_from_evidence(evidence)
    facts = article_facts(payload)
    title, body, feed_summary, read_scope = article_text_parts(evidence)
    prompt_payload = {
        "task": "Analyze a collected investment news article as metadata, not as a buy/sell recommendation.",
        "outputLanguage": "ko",
        "requiredJsonOnly": True,
        "schema": {
            "status": "ok|error",
            "impactPolarity": "support|risk|neutral|mixed|unknown",
            "impactLabelKo": "호재|악재|중립|혼재|미확인",
            "relevanceState": "direct|related|context|unrelated",
            "sourceTrustState": "trusted|standard|limited|unknown",
            "materialityState": "material|notable|context",
            "dataState": "sufficient|partial|insufficient|unavailable",
            "validationState": "ready|conditional|blocked",
            "summary": {
                "oneLineKo": "기사에서 실제로 일어난 일과 종목 관련성을 담은 한 문장",
                "briefKo": "핵심 사실만 담은 자연스러운 한국어 2-3문장",
                "keyTakeaways": ["briefKo에 없는 보조 사실"],
                "whyItMatters": "사실을 반복하지 않고 설명한 종목 영향 경로",
                "watchPoints": ["불확실성을 해소할 구체적인 다음 확인 항목"],
            },
            "riskSignals": ["phrase"],
            "supportSignals": ["phrase"],
            "contrastSignals": ["phrase"],
            "keyNumbers": ["number"],
            "rationaleKo": "short evidence-based rationale",
            "impactReasonKo": "why this article is support/risk/mixed/neutral for the stock in Korean",
            "portfolioImplicationKo": "what this means for a holding/watchlist user without trading instruction",
            "actionBoundaryKo": "what to check next and what not to conclude",
            "validationReasonKo": "which source or data condition limits use of this analysis",
            "needsReview": True,
            "reasoningLimitations": ["missing data"],
        },
        "guardrails": [
            "Do not create buy, sell, add, trim, or hold decisions.",
            "Use only the provided title, feed summary, body preview, and existing metadata.",
            "summary.oneLineKo and summary.briefKo must summarize article facts first; keep stock impact reasoning in rationaleKo.",
            "summary.briefKo must state who did what, the material number or condition when present, and why the event matters; do not merely name an event category.",
            "Do not repeat the same fact across oneLineKo, briefKo, keyTakeaways, whyItMatters, and watchPoints. Each field has a distinct role: core fact, supporting facts, investment impact, and verification condition.",
            "whyItMatters must explain the causal path to revenue, cost, valuation, regulation, liquidity, or investor sentiment. Do not restate the headline.",
            "watchPoints must name a measurable follow-up such as an official filing, guidance number, price reaction, or volume confirmation. Avoid generic phrases when a specific condition is available.",
            "Write the Korean summary as complete natural sentences. Do not repeat the title, source name, relation status, or phrases such as 확인할 뉴스, 관련 뉴스입니다, 핵심 내용은.",
            "Do not use generic sector templates such as AI/data-center demand unless that fact is present in the title, feed summary, or body preview.",
            "If the body is missing, set dataState to partial and validationState to conditional.",
            "A phrase such as 실적 by itself is not positive; 실적 우려, 붕괴, 하락, 덮은 are risk context.",
            "Ignore newsletter or CTA boilerplate such as Never miss important updates, Simply Wall St tools, make better investment decisions, and cut through noise.",
            "The word miss is a risk signal only in earnings or estimate-miss context, not in Never miss important updates.",
            "impactReasonKo and portfolioImplicationKo must explain the investment impact plainly before any generic summary.",
        ],
        "target": {
            "symbol": target.symbol,
            "name": target.name,
            "market": target.market,
            "sector": target.sector,
        },
        "article": {
            "title": title,
            "feedSummary": feed_summary,
            "bodyPreview": body,
            "readScope": read_scope,
            "source": evidence.source,
            "url": evidence.url,
            "publishedAt": evidence.published_at,
            "articleFacts": facts,
            "existingAnalysis": {
                "relationScope": payload.get("relationScope"),
                "eventType": payload.get("eventType"),
                "relevanceState": payload.get("relevanceState"),
                "materialityState": payload.get("materialityState"),
                "sourceTrustState": payload.get("sourceTrustState"),
                "dataState": payload.get("dataState"),
                "validationState": payload.get("validationState"),
            },
        },
    }
    return json.dumps(prompt_payload, ensure_ascii=False, indent=2)
