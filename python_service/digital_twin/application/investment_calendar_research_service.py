from typing import Dict, Iterable, List

from ..domain.investment_calendar import clean_text
from ..domain.investment_calendar_candidates import (
    CANDIDATE_STATUS_PENDING,
    InvestmentCalendarReviewCandidate,
)
from ..domain.investment_calendar_extraction import calendar_candidate_sets_from_research_items
from ..domain.investment_strategy_guidance import event_type_guidance, target_text
from ..domain.market_data import number
from ..domain.portfolio import utc_now_iso


DISABLED_VALUES = {"0", "false", "no", "off", "disabled"}
AI_RESEARCH_DETECTOR = "ai-research-calendar-recommender-v1"


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_VALUES


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 1000) -> int:
    raw = settings.get(key) if isinstance(settings, dict) else None
    parsed = number(raw)
    if parsed == 0 and raw in (None, ""):
        parsed = fallback
    return max(lower, min(upper, int(parsed or fallback)))


def source_item_id(item: Dict[str, object]) -> str:
    return clean_text(item.get("evidenceId") or item.get("id"), 191)


def evidence_to_dict(item) -> Dict[str, object]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "to_dict"):
        return item.to_dict()
    return {}


def compact_collection_result(result: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(result, dict):
        return {}
    return {
        "status": clean_text(result.get("status"), 40),
        "targetCount": int(result.get("targetCount") or 0),
        "fetchedCount": int(result.get("fetchedCount") or 0),
        "savedCount": int(result.get("savedCount") or 0),
        "changedCount": int(result.get("changedCount") or 0),
        "materialChangedCount": int(result.get("materialChangedCount") or 0),
        "symbols": list(result.get("symbols") or [])[:40],
        "providers": list(result.get("providers") or [])[:20],
        "articleAnalysisHealth": dict(result.get("articleAnalysisHealth") or {}),
    }


def recommendation_scenarios(event_type: str, symbols: Iterable[object] = None) -> Dict[str, str]:
    target = target_text(symbols, [])
    if event_type == "adrListing":
        return {
            "positive": target + "의 해외 접근성과 유동성이 개선되면 밸류에이션 재평가와 단기 수급 유입이 붙을 수 있습니다.",
            "negative": "상장 조건, 교환비율, 원주와 ADR 가격차가 불리하면 차익거래와 변동성 부담이 커질 수 있습니다.",
        }
    if event_type == "indexInclusion":
        return {
            "positive": "편입 확정과 리밸런싱 수요가 확인되면 패시브 매수 기대와 거래대금 증가가 붙을 수 있습니다.",
            "negative": "이미 선반영됐거나 편입 실패/비중 축소가 나오면 이벤트 소멸 매물이 나올 수 있습니다.",
        }
    if event_type == "capitalRaise":
        return {
            "positive": "조달 자금의 사용처가 성장 투자나 재무 안정성 개선으로 확인되면 중장기 근거가 보강됩니다.",
            "negative": "발행가, 전환가, 물량 부담이 크면 희석과 단기 수급 압박이 우선 반영될 수 있습니다.",
        }
    if event_type == "spinoff":
        return {
            "positive": "분할 후 사업 가치가 더 명확해지면 밸류에이션 재평가와 사업별 수급 분리가 가능합니다.",
            "negative": "거래정지, 재상장 기준가, 지배구조 불확실성이 커지면 이벤트 전후 변동성이 확대될 수 있습니다.",
        }
    if event_type == "listing":
        return {
            "positive": "상장 시장 변경이나 신규 상장이 투자자 접근성과 거래 유동성을 넓힐 수 있습니다.",
            "negative": "첫 거래 물량, 보호예수 해제, 기준가 부담이 커지면 단기 매도 압력이 생길 수 있습니다.",
        }
    return {
        "positive": "공식 일정과 실제 결과가 투자 가정을 강화하면 관심 종목의 수급과 재평가 근거가 생길 수 있습니다.",
        "negative": "일정이 지연되거나 조건이 기대보다 약하면 이벤트 기대가 빠지며 변동성이 커질 수 있습니다.",
    }


class InvestmentCalendarResearchRecommendationService:
    def __init__(
        self,
        candidate_repository,
        evidence_repository,
        account_repository=None,
        news_collection_runner_factory=None,
        settings: Dict[str, object] = None,
    ):
        self.candidate_repository = candidate_repository
        self.evidence_repository = evidence_repository
        self.account_repository = account_repository
        self.news_collection_runner_factory = news_collection_runner_factory
        self.settings = dict(settings or {})

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarAiResearchEnabled"), True)

    def default_run_collection(self) -> bool:
        return truthy(self.settings.get("investmentCalendarAiResearchRunCollection"), True)

    def evidence_limit(self, payload: Dict[str, object]) -> int:
        value = dict(self.settings)
        if isinstance(payload, dict) and payload.get("limit") not in (None, ""):
            value["investmentCalendarAiResearchEvidenceLimit"] = payload.get("limit")
        return int_setting(value, "investmentCalendarAiResearchEvidenceLimit", 120, 1, 500)

    def candidate_limit(self) -> int:
        return int_setting(self.settings, "investmentCalendarAiResearchCandidateLimit", 50, 1, 200)

    def feedback(self) -> Dict[str, object]:
        if not self.candidate_repository or not hasattr(self.candidate_repository, "feedback_summary"):
            return {}
        try:
            return self.candidate_repository.feedback_summary()
        except Exception:  # noqa: BLE001 - recommendation should still work without feedback.
            return {}

    def accounts(self) -> List[object]:
        if not self.account_repository:
            return []
        try:
            accounts = self.account_repository.load_all() if hasattr(self.account_repository, "load_all") else self.account_repository.load()
        except Exception:  # noqa: BLE001 - account lookup must not block research recommendations.
            accounts = []
        return [account for account in accounts or [] if getattr(account, "enabled", True)]

    def account_ids_for_candidate(self, candidate) -> List[str]:
        accounts = self.accounts()
        if not accounts:
            return []
        symbols = {str(symbol or "").upper() for symbol in getattr(candidate, "symbols", []) or [] if str(symbol or "").strip()}
        if symbols:
            selected = [
                account
                for account in accounts
                if symbols.intersection({str(symbol or "").upper() for symbol in getattr(account, "watchlist_symbols", []) or []})
            ]
            if selected:
                return [str(getattr(account, "account_id", "") or "") for account in selected if getattr(account, "account_id", "")]
        return [str(getattr(account, "account_id", "") or "") for account in accounts if getattr(account, "account_id", "")]

    def latest_evidence_items(self, payload: Dict[str, object]) -> List[Dict[str, object]]:
        if not self.evidence_repository or not hasattr(self.evidence_repository, "latest"):
            return []
        symbol = clean_text((payload or {}).get("symbol"), 24).upper()
        kind = clean_text((payload or {}).get("kind"), 40)
        items = self.evidence_repository.latest(symbol=symbol, kind=kind, limit=self.evidence_limit(payload))
        return [item for item in [evidence_to_dict(item) for item in items] if item]

    def run_collection(self, payload: Dict[str, object]) -> Dict[str, object]:
        should_run = truthy((payload or {}).get("runCollection"), self.default_run_collection())
        if not should_run or not self.news_collection_runner_factory:
            return {"status": "skipped"}
        runner = self.news_collection_runner_factory()
        if not runner or not hasattr(runner, "run_once"):
            return {"status": "unavailable"}
        return runner.run_once(force=True)

    def recommendation_payload(self, candidate, source_item: Dict[str, object], collection_result: Dict[str, object]) -> Dict[str, object]:
        guidance = event_type_guidance(candidate.event_type, target_text(candidate.symbols, candidate.markets), candidate.symbols)
        scenarios = recommendation_scenarios(candidate.event_type, candidate.symbols)
        payload = candidate.to_review_payload(self.account_ids_for_candidate(candidate))
        payload["reviewReason"] = "missingDate" if not candidate.starts_at else "aiResearchRecommended"
        payload["notes"] = (
            clean_text(candidate.notes, 1200)
            + " AI 리서치 추천 후보입니다. 캘린더 등록 전 날짜, 출처, 이벤트 조건을 확인하세요."
        ).strip()
        body = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        body.update({
            "aiResearchRecommended": True,
            "detector": AI_RESEARCH_DETECTOR,
            "recommendationMode": "review-first",
            "recommendedAt": utc_now_iso(),
            "investmentImpact": guidance["impact"],
            "watchItems": list(guidance["watchItems"]),
            "positiveScenario": scenarios["positive"],
            "negativeScenario": scenarios["negative"],
            "sourceEvidenceTitle": clean_text(source_item.get("title"), 255),
            "sourceEvidenceSummary": clean_text(source_item.get("summary") or source_item.get("analysisSummary") or source_item.get("articleSummaryKo"), 700),
            "sourcePublishedAt": clean_text(source_item.get("publishedAt"), 80),
            "sourceObservedAt": clean_text(source_item.get("observedAt"), 80),
            "sourceTrustState": source_item.get("sourceTrustState"),
            "materialityState": source_item.get("materialityState"),
            "dataState": source_item.get("dataState"),
            "validationState": source_item.get("validationState"),
            "collection": compact_collection_result(collection_result),
        })
        payload["payload"] = body
        return payload

    def recommend(self, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = payload if isinstance(payload, dict) else {}
        if not self.enabled() and not truthy(payload.get("force"), False):
            return {"status": "disabled", "candidateCount": 0, "storedCandidateCount": 0}
        errors: List[str] = []
        collection_result: Dict[str, object] = {}
        try:
            collection_result = self.run_collection(payload)
        except Exception as error:  # noqa: BLE001 - existing evidence scan can still produce candidates.
            errors.append("newsCollection: " + str(error)[:180])
            collection_result = {"status": "error", "message": str(error)[:180]}
        items = self.latest_evidence_items(payload)
        item_by_id = {source_item_id(item): item for item in items if source_item_id(item)}
        candidate_sets = calendar_candidate_sets_from_research_items(
            items,
            register_undated=False,
            force_review=True,
            feedback=self.feedback(),
        )
        candidates = list(candidate_sets.get("review") or [])[: self.candidate_limit()]
        stored = 0
        skipped_final = 0
        saved_candidates = []
        for candidate in candidates:
            existing = self.candidate_repository.get(candidate.review_candidate_id) if hasattr(self.candidate_repository, "get") else None
            if existing and getattr(existing, "status", "") != CANDIDATE_STATUS_PENDING:
                skipped_final += 1
                continue
            source_item = item_by_id.get(candidate.source_evidence_id, {})
            candidate_payload = self.recommendation_payload(candidate, source_item, collection_result)
            if self.candidate_repository.upsert(candidate_payload):
                stored += 1
            saved = self.candidate_repository.get(candidate_payload["candidateId"]) if hasattr(self.candidate_repository, "get") else None
            if isinstance(saved, InvestmentCalendarReviewCandidate):
                saved_candidates.append(saved.to_dict())
            else:
                saved_candidates.append(candidate_payload)
        return {
            "status": "ok",
            "generatedAt": utc_now_iso(),
            "mode": "aiResearchReview",
            "detector": AI_RESEARCH_DETECTOR,
            "evidenceCount": len(items),
            "candidateCount": len(candidates),
            "storedCandidateCount": stored,
            "skippedFinalCandidateCount": skipped_final,
            "collection": compact_collection_result(collection_result),
            "errors": errors,
            "candidates": saved_candidates,
        }
