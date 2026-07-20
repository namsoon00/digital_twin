from typing import Dict, List

from ..domain.events import psychology_shadow_evaluated_event
from ..domain.market_psychology import market_psychology_snapshot, psychology_policy_from_settings
from ..domain.ontology_inference_context import inferencebox_from_snapshot, relation_contexts_from_snapshot
from ..domain.ontology_observation_quality import position_observation_profiles
from ..domain.portfolio import AccountSnapshot


class PsychologyShadowService:
    def __init__(self, settings: Dict[str, object] = None):
        self.settings = dict(settings or {})

    def evaluate(self, snapshot: AccountSnapshot):
        policy = psychology_policy_from_settings(self.settings)
        payload = {
            "version": "market-psychology-shadow-v2",
            "enabled": policy.enabled,
            "mode": "shadow",
            "generatedAt": str(snapshot.generated_at or ""),
            "accountId": str(snapshot.account_id or ""),
            "policy": policy.to_dict(),
            "symbols": {},
            "summary": {
                "symbolCount": 0,
                "usableCount": 0,
                "changedCount": 0,
                "typeDbConfirmedCount": 0,
                "decisionImpactApplied": False,
            },
        }
        snapshot.metadata["psychologyShadow"] = payload
        if not policy.enabled:
            return []

        previous_rows = previous_psychology_rows(snapshot)
        inference_contexts = relation_contexts_from_snapshot(snapshot, self.settings)
        inferencebox = inferencebox_from_snapshot(snapshot)
        shadow_confirmed_symbols = typedb_shadow_confirmed_symbols(inferencebox)
        holding_symbols = {
            str(item.symbol or "").upper().strip()
            for item in snapshot.positions or []
            if getattr(item, "symbol", "") and not item.is_cash()
        }
        seen = set()
        changed_symbols: List[str] = []
        for position in list(snapshot.positions or []) + list(snapshot.watchlist or []):
            symbol = str(getattr(position, "symbol", "") or "").upper().strip()
            if not symbol or symbol in seen or position.is_cash():
                continue
            seen.add(symbol)
            profiles = position_observation_profiles(
                position,
                {"settings": self.settings, "asOf": snapshot.generated_at},
            )
            assessment = market_psychology_snapshot(
                position,
                external_signals=snapshot.external_signals,
                observation_profiles=profiles,
                settings=self.settings,
                observed_at=snapshot.generated_at,
            ).to_dict()
            baseline = baseline_decision(inference_contexts.get(symbol) or {})
            previous = previous_rows.get(symbol) if isinstance(previous_rows.get(symbol), dict) else {}
            comparison = shadow_comparison(assessment, baseline, previous)
            row = {
                **assessment,
                "source": "holding" if symbol in holding_symbols else "watchlist",
                "baseline": baseline,
                "comparison": comparison,
                "typeDbShadowConfirmed": symbol in shadow_confirmed_symbols,
                "decisionImpactApplied": False,
            }
            payload["symbols"][symbol] = row
            payload["summary"]["symbolCount"] += 1
            if assessment.get("state") != "insufficient":
                payload["summary"]["usableCount"] += 1
            if comparison.get("materialChange"):
                payload["summary"]["changedCount"] += 1
                changed_symbols.append(symbol)
            if row["typeDbShadowConfirmed"]:
                payload["summary"]["typeDbConfirmedCount"] += 1
        snapshot.metadata["psychologyShadow"] = payload
        return [psychology_shadow_evaluated_event(payload, changed_symbols)]


def previous_psychology_rows(snapshot: AccountSnapshot) -> Dict[str, object]:
    previous = (snapshot.metadata or {}).get("previousMonitorState")
    previous = previous if isinstance(previous, dict) else {}
    metadata = previous.get("metadata") if isinstance(previous.get("metadata"), dict) else {}
    psychology = metadata.get("psychologyShadow") if isinstance(metadata.get("psychologyShadow"), dict) else {}
    return dict(psychology.get("symbols") or {}) if isinstance(psychology.get("symbols"), dict) else {}


def typedb_shadow_confirmed_symbols(inferencebox: Dict[str, object]) -> set:
    symbols = set()
    for relation in (inferencebox or {}).get("relations") or []:
        if not isinstance(relation, dict):
            continue
        if str(relation.get("type") or relation.get("relationType") or "").upper() != "HAS_PSYCHOLOGY_SHADOW":
            continue
        symbol = str(relation.get("symbol") or "").upper().strip()
        if symbol:
            symbols.add(symbol)
    return symbols


def baseline_decision(context: Dict[str, object]) -> Dict[str, object]:
    decision = context.get("decision") if isinstance(context.get("decision"), dict) else {}
    return {
        "available": bool(decision),
        "action": str(decision.get("action") or decision.get("actionLabel") or ""),
        "actionLabel": str(decision.get("actionLabel") or decision.get("action") or ""),
        "reviewLevel": str(decision.get("reviewLevel") or context.get("reviewLevel") or "normal"),
        "dataState": str(decision.get("dataState") or context.get("dataState") or "unavailable"),
        "validationState": str(decision.get("validationState") or context.get("validationState") or "conditional"),
        "decisionStage": str(decision.get("decisionStage") or ""),
        "source": str(context.get("source") or ""),
    }


def shadow_comparison(
    assessment: Dict[str, object],
    baseline: Dict[str, object],
    previous: Dict[str, object],
) -> Dict[str, object]:
    previous_state = str(previous.get("state") or "")
    current_state = str(assessment.get("state") or "insufficient")
    previous_review = str(previous.get("reviewLevel") or "")
    current_review = str(assessment.get("reviewLevel") or "blocked")
    previous_data = str(previous.get("dataState") or "")
    current_data = str(assessment.get("dataState") or "unavailable")
    previous_conflict = str(previous.get("conflictState") or "")
    current_conflict = str(assessment.get("conflictState") or "context-only")
    state_changed = bool(previous_state) and previous_state != current_state
    review_changed = bool(previous_review) and previous_review != current_review
    data_changed = bool(previous_data) and previous_data != current_data
    conflict_changed = bool(previous_conflict) and previous_conflict != current_conflict
    material_change = not previous or state_changed or review_changed or data_changed or conflict_changed
    candidate_direction = {
        "risk-only": "risk",
        "support-only": "support",
        "mixed": "mixed",
    }.get(current_conflict, "context")
    return {
        "previousState": previous_state,
        "previousReviewLevel": previous_review,
        "previousDataState": previous_data,
        "previousConflictState": previous_conflict,
        "stateChanged": state_changed,
        "reviewLevelChanged": review_changed,
        "dataStateChanged": data_changed,
        "conflictStateChanged": conflict_changed,
        "materialChange": material_change,
        "baselineAction": str(baseline.get("action") or ""),
        "baselineReviewLevel": str(baseline.get("reviewLevel") or "normal"),
        "candidateDirection": candidate_direction,
        "decisionImpactApplied": False,
        "actionChanged": False,
        "dispatchEligible": False,
        "explanation": shadow_comparison_explanation(candidate_direction),
    }


def shadow_comparison_explanation(direction: str) -> str:
    if direction == "risk":
        return "심리 근거는 경계 쪽이지만 현재 투자 판단에는 직접 반영하지 않고 비교 자료로만 남겼습니다."
    if direction == "support":
        return "심리 근거는 우호 쪽이지만 현재 투자 판단에는 직접 반영하지 않고 비교 자료로만 남겼습니다."
    if direction == "mixed":
        return "우호 근거와 위험 근거가 함께 있어 방향을 단정하지 않고 비교 자료로만 남겼습니다."
    return "심리 근거의 방향이 뚜렷하지 않아 현재 투자 판단에는 직접 반영하지 않았습니다."
