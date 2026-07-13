from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Dict, Iterable, List, Tuple

from .investment_research import NewsCollectionTarget, ResearchEvidence
from .market_data import clamp, number
from . import news_analysis as news_domain


NEWS_AI_ANALYSIS_VERSION = "news-ai-analysis-v1"
NEWS_AI_PROMPT_VERSION = "news-ai-prompt-v1"

IMPACT_LABELS = {
    "support": "호재",
    "risk": "악재",
    "mixed": "혼재",
    "neutral": "중립",
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


def keyword_hits(text: object, phrases: Iterable[str], limit: int = 6) -> List[str]:
    lowered = str(text or "").casefold()
    rows: List[str] = []
    for phrase in phrases:
        term = str(phrase or "").strip()
        if not term:
            continue
        term_lower = term.casefold()
        if re.fullmatch(r"[a-z0-9][a-z0-9 .&+/'-]*", term_lower):
            matched = bool(re.search(r"(?<![a-z0-9])" + re.escape(term_lower) + r"(?![a-z0-9])", lowered))
        else:
            matched = term_lower in lowered
        if matched and term not in rows:
            rows.append(term)
        if len(rows) >= limit:
            break
    return rows


def source_text_hash(*values: object) -> str:
    text = "\n".join(str(value or "").strip() for value in values if str(value or "").strip())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def analysis_payload_from_evidence(evidence: ResearchEvidence) -> Dict[str, object]:
    return evidence.raw_payload if isinstance(evidence.raw_payload, dict) else {}


def article_facts(payload: Dict[str, object]) -> Dict[str, object]:
    facts = payload.get("articleFacts") if isinstance(payload, dict) else {}
    return facts if isinstance(facts, dict) else {}


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
    confidence: float = 0.5
    materiality_score: float = 0.0
    relevance_score: float = 0.0
    summary: Dict[str, object] = field(default_factory=dict)
    risk_signals: List[str] = field(default_factory=list)
    support_signals: List[str] = field(default_factory=list)
    contrast_signals: List[str] = field(default_factory=list)
    key_numbers: List[str] = field(default_factory=list)
    rationale_ko: str = ""
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
            "confidence": round(clamp(number(self.confidence), 0.0, 1.0), 2),
            "materialityScore": round(clamp(number(self.materiality_score), 0.0, 100.0), 1),
            "relevanceScore": round(clamp(number(self.relevance_score), 0.0, 100.0), 1),
            "summary": dict(self.summary or {}),
            "riskSignals": list(self.risk_signals or []),
            "supportSignals": list(self.support_signals or []),
            "contrastSignals": list(self.contrast_signals or []),
            "keyNumbers": list(self.key_numbers or []),
            "rationaleKo": compact_text(self.rationale_ko, 760),
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
        confidence=clamp(number(payload.get("confidence")) or fallback.confidence, 0.0, 1.0),
        materiality_score=clamp(number(payload.get("materialityScore") or payload.get("materiality_score")) or fallback.materiality_score, 0.0, 100.0),
        relevance_score=clamp(number(payload.get("relevanceScore") or payload.get("relevance_score")) or fallback.relevance_score, 0.0, 100.0),
        summary={
            "oneLineKo": compact_text(summary.get("oneLineKo") or summary.get("one_line_ko") or fallback_summary.get("oneLineKo") or "", 220),
            "briefKo": compact_text(summary.get("briefKo") or summary.get("brief_ko") or fallback_summary.get("briefKo") or "", 520),
            "keyTakeaways": unique_texts(summary.get("keyTakeaways") or summary.get("key_takeaways") or fallback_summary.get("keyTakeaways") or [], 5),
            "whyItMatters": compact_text(summary.get("whyItMatters") or summary.get("why_it_matters") or fallback_summary.get("whyItMatters") or "", 360),
            "watchPoints": unique_texts(summary.get("watchPoints") or summary.get("watch_points") or fallback_summary.get("watchPoints") or [], 5),
        },
        risk_signals=unique_texts(payload.get("riskSignals") or payload.get("risk_signals") or fallback.risk_signals, 6),
        support_signals=unique_texts(payload.get("supportSignals") or payload.get("support_signals") or fallback.support_signals, 6),
        contrast_signals=unique_texts(payload.get("contrastSignals") or payload.get("contrast_signals") or fallback.contrast_signals, 6),
        key_numbers=unique_texts(payload.get("keyNumbers") or payload.get("key_numbers") or fallback.key_numbers, 6),
        rationale_ko=compact_text(payload.get("rationaleKo") or payload.get("rationale_ko") or fallback.rationale_ko, 760),
        needs_review=bool(payload.get("needsReview") if "needsReview" in payload else fallback.needs_review),
        reasoning_limitations=unique_texts(payload.get("reasoningLimitations") or payload.get("reasoning_limitations") or fallback.reasoning_limitations, 5),
    )


def infer_impact_polarity(text: object) -> Tuple[str, List[str], List[str], List[str]]:
    risk_hits = keyword_hits(text, RISK_PHRASES)
    support_hits = keyword_hits(text, SUPPORT_PHRASES)
    contrast_hits = keyword_hits(text, CONTRAST_PHRASES)
    risk_score = len(risk_hits) * 18
    support_score = len(support_hits) * 15
    if "실적 우려" in risk_hits or "전망 우려" in risk_hits:
        risk_score += 22
    if "붕괴" in risk_hits or "plunge" in risk_hits:
        risk_score += 18
    if contrast_hits and risk_hits and support_hits:
        risk_score += 15
    if risk_score >= support_score + 8 and risk_score > 0:
        return "risk", risk_hits, support_hits, contrast_hits
    if support_score >= risk_score + 8 and support_score > 0:
        return "support", risk_hits, support_hits, contrast_hits
    if risk_hits and support_hits:
        return "mixed", risk_hits, support_hits, contrast_hits
    if risk_hits:
        return "risk", risk_hits, support_hits, contrast_hits
    if support_hits:
        return "support", risk_hits, support_hits, contrast_hits
    return "neutral", risk_hits, support_hits, contrast_hits


def local_news_ai_analysis(target: NewsCollectionTarget, evidence: ResearchEvidence) -> NewsAiAnalysis:
    payload = analysis_payload_from_evidence(evidence)
    facts = article_facts(payload)
    title, body, feed_summary, read_scope = article_text_parts(evidence)
    source_text = " ".join(part for part in [title, body or feed_summary] if part)
    polarity, risk_hits, support_hits, contrast_hits = infer_impact_polarity(source_text)
    event_type = str(payload.get("eventType") or facts.get("eventType") or news_domain.classify_news_event_type(title, source_text) or "general")
    relation_scope = str(payload.get("relationScope") or facts.get("relationScope") or "").strip()
    relevance = number(payload.get("relevanceScore") or facts.get("relevanceScore"))
    materiality_base = number(payload.get("materialityScore") or facts.get("materialityScore"))
    hit_boost = min(22.0, len(risk_hits) * 5 + len(support_hits) * 4 + len(contrast_hits) * 3)
    materiality = clamp(max(materiality_base, 50.0 + hit_boost if polarity != "neutral" else materiality_base), 0.0, 100.0)
    confidence_base = 0.58
    if read_scope == "body":
        confidence_base += 0.12
    if relation_scope == "direct":
        confidence_base += 0.08
    if risk_hits or support_hits:
        confidence_base += 0.07
    if polarity == "mixed":
        confidence_base -= 0.08
    if read_scope != "body":
        confidence_base -= 0.06
    confidence = clamp(confidence_base, 0.3, 0.9)
    key_numbers = news_domain.numeric_highlights(source_text, 6)
    target_name = target.name or evidence.symbol or "대상 종목"
    label = IMPACT_LABELS.get(polarity, "중립")
    evidence_scope = "본문" if read_scope == "body" else "제목/RSS 요약"
    analysis_context = {
        "relationScope": relation_scope,
        "eventType": event_type,
        "relevanceScore": relevance,
        "materialityScore": materiality,
    }
    article_summary = news_domain.korean_article_summary(target, title, body, feed_summary, analysis_context)
    article_takeaway = news_domain.article_event_takeaway(target, title, body, feed_summary)
    signal_text = ", ".join(unique_texts(risk_hits + support_hits + contrast_hits, 5)) or "명시적 방향 신호 없음"
    if polarity == "risk":
        one_line = article_takeaway or target_name + " 기사에서 위험 신호가 더 강하게 확인됩니다."
        fallback_brief = target_name + " 관련 기사에서 " + signal_text + "가 확인돼 단기 가격 부담과 거래량 반응을 먼저 봐야 합니다."
    elif polarity == "support":
        one_line = article_takeaway or target_name + " 기사에서 우호 신호가 확인됩니다."
        fallback_brief = target_name + " 관련 기사에서 " + signal_text + "가 확인돼 가격 반응이 이어지는지 확인할 필요가 있습니다."
    elif polarity == "mixed":
        one_line = article_takeaway or target_name + " 기사에 우호·위험 신호가 함께 있습니다."
        fallback_brief = "우호 표현과 위험 표현이 함께 있어 방향을 단정하지 말고 원문과 다음 가격 반응을 함께 확인해야 합니다."
    else:
        one_line = article_takeaway or target_name + " 관련 새 정보지만 방향성은 중립입니다."
        fallback_brief = "기사에서 명확한 호재·악재 방향은 약해 가격·거래량 확인용 근거로 다룹니다."
    brief = article_summary or fallback_brief
    takeaways = [
        "기사 요약: " + compact_text(article_takeaway or article_summary, 140),
        "영향 방향: " + label,
        "분석 범위: " + evidence_scope,
        "이벤트 유형: " + news_domain.event_type_label(event_type),
    ] if (article_takeaway or article_summary) else [
        "영향 방향: " + label,
        "분석 범위: " + evidence_scope,
        "이벤트 유형: " + news_domain.event_type_label(event_type),
    ]
    if risk_hits:
        takeaways.append("위험 신호: " + ", ".join(risk_hits[:3]))
    if support_hits:
        takeaways.append("우호 신호: " + ", ".join(support_hits[:3]))
    watch_points = ["다음 장 가격 반응", "거래량 동반 여부"]
    if read_scope != "body":
        watch_points.insert(0, "원문 본문 확보")
    if event_type in {"earnings", "guidance"}:
        watch_points.append("실적 전망 변화")
    limitations = [] if read_scope == "body" else ["본문 원문 미수집으로 제목/RSS 요약 기반 분석"]
    if contrast_hits:
        limitations.append("상반된 표현이 있어 문맥 확인 필요")
    return NewsAiAnalysis(
        read_scope=read_scope,
        source_text_hash=source_text_hash(title, body, feed_summary),
        relation_scope=relation_scope,
        event_type=event_type,
        impact_polarity=polarity,
        impact_label_ko=label,
        confidence=confidence,
        materiality_score=materiality,
        relevance_score=relevance,
        summary={
            "oneLineKo": compact_text(one_line, 220),
            "briefKo": compact_text(brief, 520),
            "keyTakeaways": unique_texts(takeaways, 5),
            "whyItMatters": compact_text(news_domain.impact_channel_text(event_type, source_text), 360),
            "watchPoints": unique_texts(watch_points, 5),
        },
        risk_signals=risk_hits,
        support_signals=support_hits,
        contrast_signals=contrast_hits,
        key_numbers=key_numbers,
        rationale_ko=compact_text(
            "AI 기사 분석: "
            + evidence_scope
            + "에서 "
            + signal_text
            + "를 근거로 "
            + label
            + "로 분류했습니다.",
            760,
        ),
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
    stock_impact_score = number(analysis_dict.get("materialityScore")) or number(evidence.impact_score)
    article_facts_payload = article_facts(payload)
    if article_facts_payload and not article_facts_payload.get("bodyAvailable") and payload.get("articleReadStatus") == "body":
        payload["articleReadStatus"] = "feed-summary"
        article_facts_payload["readStatus"] = "feed-summary"
        article_facts_payload["readStatusLabel"] = news_domain.article_read_status_label("feed-summary")
        article_facts_payload["missingBodyReason"] = article_facts_payload.get("missingBodyReason") or news_domain.article_missing_body_reason("feed-summary", "")
        payload["articleFacts"] = article_facts_payload
    payload["aiAnalysis"] = analysis_dict
    payload["articleAiAnalysisVersion"] = NEWS_AI_ANALYSIS_VERSION
    payload["articleSummaryKo"] = summary.get("briefKo") or summary.get("oneLineKo") or payload.get("articleSummaryKo") or evidence.summary
    payload["stockImpact"] = STOCK_IMPACT_VALUES.get(impact_polarity, "neutral")
    payload["stockImpactLabel"] = analysis_dict.get("impactLabelKo") or IMPACT_LABELS.get(impact_polarity, "중립")
    payload["stockImpactPolarity"] = impact_polarity if impact_polarity in {"support", "risk"} else "context"
    payload["stockImpactScore"] = round(clamp(stock_impact_score, 0.0, 100.0), 1)
    payload["stockImpactReasonKo"] = analysis_dict.get("rationaleKo") or payload.get("stockImpactReasonKo") or ""
    if analysis_dict.get("materialityScore"):
        payload["materialityScore"] = max(number(payload.get("materialityScore")), number(analysis_dict.get("materialityScore")))
    if analysis_dict.get("relationScope") and not payload.get("relationScope"):
        payload["relationScope"] = analysis_dict.get("relationScope")
    if analysis_dict.get("eventType") and not payload.get("eventType"):
        payload["eventType"] = analysis_dict.get("eventType")
    evidence_polarity = impact_polarity if impact_polarity in {"support", "risk"} else "context"
    return ResearchEvidence(
        evidence.evidence_id,
        evidence.symbol,
        evidence.kind,
        evidence.source,
        evidence.title,
        compact_text(payload.get("articleSummaryKo") or evidence.summary, 520),
        evidence.url,
        evidence.observed_at,
        evidence_polarity,
        payload["stockImpactScore"],
        max(number(evidence.confidence), number(analysis_dict.get("confidence"))),
        evidence.published_at,
        payload,
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
            "confidence": "0.0-1.0",
            "materialityScore": "0-100",
            "summary": {
                "oneLineKo": "one sentence",
                "briefKo": "2-3 sentences",
                "keyTakeaways": ["fact"],
                "whyItMatters": "investment relevance path",
                "watchPoints": ["next check"],
            },
            "riskSignals": ["phrase"],
            "supportSignals": ["phrase"],
            "contrastSignals": ["phrase"],
            "keyNumbers": ["number"],
            "rationaleKo": "short evidence-based rationale",
            "needsReview": True,
            "reasoningLimitations": ["missing data"],
        },
        "guardrails": [
            "Do not create buy, sell, add, trim, or hold decisions.",
            "Use only the provided title, feed summary, body preview, and existing metadata.",
            "summary.oneLineKo and summary.briefKo must summarize article facts first; keep stock impact reasoning in rationaleKo.",
            "Do not use generic sector templates such as AI/data-center demand unless that fact is present in the title, feed summary, or body preview.",
            "If the body is missing, state that limitation and lower confidence.",
            "A phrase such as 실적 by itself is not positive; 실적 우려, 붕괴, 하락, 덮은 are risk context.",
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
                "relevanceScore": payload.get("relevanceScore"),
                "materialityScore": payload.get("materialityScore"),
                "sourceReliability": payload.get("sourceReliability"),
            },
        },
    }
    return json.dumps(prompt_payload, ensure_ascii=False, indent=2)
