from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from .market_data import clamp, number
from .portfolio import Position


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

    def to_dict(self) -> Dict[str, object]:
        return {
            "evidenceId": self.evidence_id,
            "symbol": self.symbol,
            "kind": self.kind,
            "source": self.source,
            "title": self.title,
            "summary": self.summary,
            "url": self.url,
            "observedAt": self.observed_at,
            "polarity": self.polarity,
            "impactScore": round(number(self.impact_score), 1),
            "confidence": round(number(self.confidence), 2),
        }


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


def keyword_polarity(text: object) -> Tuple[str, float]:
    lowered = str(text or "").lower()
    support_hits = sum(1 for item in SUPPORT_KEYWORDS if item.lower() in lowered)
    risk_hits = sum(1 for item in RISK_KEYWORDS if item.lower() in lowered)
    if risk_hits > support_hits:
        return "risk", min(16.0, 6.0 + risk_hits * 4.0)
    if support_hits > risk_hits:
        return "support", min(14.0, 5.0 + support_hits * 3.5)
    return "context", 2.0


def source_urls(items: Iterable[ResearchEvidence]) -> List[str]:
    urls: List[str] = []
    seen = set()
    for item in items or []:
        url = str(getattr(item, "url", "") or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls[:8]


def research_evidence_from_facts(symbol: str, facts: Dict[str, object]) -> List[ResearchEvidence]:
    facts = facts or {}
    normalized_symbol = str(symbol or facts.get("symbol") or "").upper()
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
    for index, item in enumerate(news.get("items") if isinstance(news.get("items"), list) else []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        polarity, impact = keyword_polarity(title)
        evidence.append(ResearchEvidence(
            "research:" + normalized_symbol + ":news:" + str(index),
            normalized_symbol,
            "news",
            str(item.get("domain") or news.get("provider") or "GDELT"),
            title,
            compact_text(item.get("summary") or title),
            str(item.get("url") or ""),
            str(item.get("seenDate") or item.get("seendate") or ""),
            polarity,
            impact,
            0.62,
        ))
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
    if isinstance(sec, dict) and sec:
        latest = sec.get("latestFiling") if isinstance(sec.get("latestFiling"), dict) else {}
        form = str(latest.get("form") or "SEC filing").strip()
        filing_date = str(latest.get("filingDate") or latest.get("filed") or "")
        polarity, impact = keyword_polarity(form + " " + str(sec.get("companyName") or ""))
        evidence.append(ResearchEvidence(
            "research:" + normalized_symbol + ":sec:" + (str(latest.get("accessionNumber") or form)),
            normalized_symbol,
            "filing",
            str(sec.get("provider") or "SEC EDGAR"),
            form,
            "제출일 " + (filing_date or "-"),
            str(latest.get("url") or ""),
            filing_date,
            polarity,
            impact,
            0.72,
        ))
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
    support = sum(number(item.impact_score) for item in evidence if item.polarity == "support")
    risk = sum(number(item.impact_score) for item in evidence if item.polarity in {"risk", "contradiction"})
    for item in relation_context.get("activeRules") or []:
        if not isinstance(item, dict):
            continue
        score = number(item.get("strengthScore") or item.get("strength_score"))
        relation_type = str(item.get("relationType") or item.get("relation_type") or "").upper()
        label = str(item.get("label") or "")
        if any(token in relation_type + " " + label for token in ["ENTRY", "SUPPORT", "CONFIRM", "기회", "매수"]):
            support += min(18.0, score * 0.22)
        if any(token in relation_type + " " + label for token in ["LOSS", "RISK", "DISCLOSURE", "CONCENTRATION", "리스크", "손실", "매도", "하락"]):
            risk += min(22.0, score * 0.28)
    return support, risk


def choose_action(position: Position, relation_context: Dict[str, object], support_score: float, risk_score: float) -> str:
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    action_group = str(decision.get("actionGroup") or "")
    action_level = str(decision.get("actionLevel") or "")
    relation_score = number(decision.get("score") or relation_context.get("signalStrength"))
    is_watchlist = str(position.source or "") == "watchlist"
    if is_watchlist:
        if risk_score >= support_score + 10 or action_group in {"entryRisk", "lossControl"}:
            return "AVOID"
        if action_group == "entry" and relation_score >= 70:
            return "BUY"
        if action_group == "entry" and relation_score >= 55:
            return "BUY"
        return "AVOID" if relation_score < 45 else "BUY"
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
            "legacyExitPressure": round(number(legacy_model.get("exitPressure")), 1),
            "ontologyPressure": round(number(ontology_opinion.get("ontology_pressure") or ontology_opinion.get("ontologyPressure")), 1),
            "evidenceCount": len(evidence),
        },
        execution_plan=dict(execution_plan or {}),
    )
