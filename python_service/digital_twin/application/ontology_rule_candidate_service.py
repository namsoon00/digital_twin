from typing import Dict, Iterable, List


def int_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 1, upper: int = 1000) -> int:
    try:
        parsed = int(float(str((settings or {}).get(key) or "").strip()))
    except ValueError:
        parsed = fallback
    return max(lower, min(upper, parsed))


class RuleChangeCandidateProposalService:
    def __init__(
        self,
        ontology_repository,
        advisor,
        event_reader=None,
        settings: Dict[str, object] = None,
        strategy_proposal_service=None,
    ):
        self.ontology_repository = ontology_repository
        self.advisor = advisor
        self.event_reader = event_reader
        self.settings = dict(settings or {})
        self.strategy_proposal_service = strategy_proposal_service

    def propose(
        self,
        symbols: Iterable[str] = None,
        trigger: str = "manual",
        requests: Iterable[object] = None,
        alerts: Iterable[object] = None,
    ) -> Dict[str, object]:
        if not self.ontology_repository or not self.advisor:
            return {"status": "disabled", "reason": "Rule candidate advisor is not configured.", "candidateCount": 0, "savedCount": 0}
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        context = self.build_context(clean_symbols, trigger, requests, alerts)
        candidates = self.advisor.propose(context)
        candidates = list(candidates or [])[: self.max_candidates()]
        save_result = self.ontology_repository.save_rule_change_candidates(candidates, {
            "trigger": trigger,
            "symbols": clean_symbols,
            "promptContext": context,
        }) if candidates and hasattr(self.ontology_repository, "save_rule_change_candidates") else {
            "status": "skipped",
            "savedCount": 0,
            "reason": "No candidates to save.",
        }
        result = {
            "status": "ok" if candidates else "no-candidates",
            "trigger": trigger,
            "symbols": clean_symbols,
            "candidateCount": len(candidates),
            "savedCount": int(save_result.get("savedCount") or 0),
            "candidates": candidates,
            "saveResult": save_result,
            "advisor": self.advisor_metadata(),
            "contextSummary": {
                "recentEventCount": len(context.get("recentEvents") or []),
                "alertCount": len(context.get("alerts") or []),
                "ruleCount": ((context.get("ruleBox") or {}).get("ruleCount") if isinstance(context.get("ruleBox"), dict) else 0),
                "inferenceRelationCount": ((context.get("inferenceBox") or {}).get("relationCount") if isinstance(context.get("inferenceBox"), dict) else 0),
            },
        }
        if self.strategy_proposal_service and hasattr(self.strategy_proposal_service, "propose_from_rule_candidates"):
            result["strategyProposalResult"] = self.strategy_proposal_service.propose_from_rule_candidates(result, context)
        return result

    def build_context(
        self,
        symbols: List[str],
        trigger: str,
        requests: Iterable[object] = None,
        alerts: Iterable[object] = None,
    ) -> Dict[str, object]:
        rulebox = self.ontology_repository.rulebox_snapshot() if hasattr(self.ontology_repository, "rulebox_snapshot") else {}
        inferencebox = self.ontology_repository.inferencebox_snapshot(symbols, limit=80) if hasattr(self.ontology_repository, "inferencebox_snapshot") else {}
        request_items = [self.event_payload(event) for event in (requests or [])]
        recent_events = request_items or self.recent_events()
        return {
            "trigger": trigger,
            "symbols": symbols,
            "ruleBox": rulebox,
            "inferenceBox": inferencebox,
            "recentEvents": recent_events,
            "alerts": [self.alert_payload(item) for item in (alerts or [])],
            "materialityAssessments": self.materiality_assessments(request_items or recent_events),
        }

    def max_candidates(self) -> int:
        return int_setting(self.settings, "ontologyRuleCandidateAiMaxCandidates", 3, 1, 10)

    def advisor_metadata(self) -> Dict[str, object]:
        if self.advisor and hasattr(self.advisor, "metadata"):
            metadata = self.advisor.metadata()
            return metadata if isinstance(metadata, dict) else {}
        return {"configured": bool(self.advisor), "mode": self.advisor.__class__.__name__ if self.advisor else "disabled"}

    def recent_events(self) -> List[Dict[str, object]]:
        if not self.event_reader or not hasattr(self.event_reader, "latest_events"):
            return []
        return [self.event_payload(event) for event in self.event_reader.latest_events(20)]

    def event_payload(self, event) -> Dict[str, object]:
        payload = dict(getattr(event, "payload", {}) or {})
        return {
            "eventId": str(getattr(event, "event_id", "") or ""),
            "name": str(getattr(event, "name", "") or ""),
            "aggregateId": str(getattr(event, "aggregate_id", "") or ""),
            "occurredAt": str(getattr(event, "occurred_at", "") or ""),
            "symbols": list(payload.get("symbols") or payload.get("changedSymbols") or [])[:40],
            "changedCount": int(payload.get("changedCount") or 0),
            "materialChangedCount": int(payload.get("materialChangedCount") or 0),
            "factTypes": list(payload.get("factTypes") or [])[:12],
            "reason": str(payload.get("reason") or ""),
            "sourceEventName": str(payload.get("sourceEventName") or ""),
            "materialityAssessments": payload.get("materialityAssessments") if isinstance(payload.get("materialityAssessments"), list) else [],
        }

    def alert_payload(self, event) -> Dict[str, object]:
        metadata = dict(getattr(event, "metadata", {}) or {})
        return {
            "rule": str(getattr(event, "rule", "") or ""),
            "title": str(getattr(event, "title", "") or ""),
            "symbol": str(getattr(event, "symbol", "") or ""),
            "severity": str(getattr(event, "severity", "") or ""),
            "sourceRules": [
                str(item.get("rule") or "")
                for item in (metadata.get("sourceAlertEvents") or [])
                if isinstance(item, dict)
            ][:12],
            "relationRuleScore": metadata.get("relationRuleScore"),
            "insightType": metadata.get("insightType"),
        }

    def materiality_assessments(self, events: Iterable[Dict[str, object]]) -> List[object]:
        rows = []
        for event in events or []:
            assessments = event.get("materialityAssessments")
            if isinstance(assessments, list):
                rows.extend(assessments[:20])
            elif isinstance(assessments, dict):
                rows.append(assessments)
        return rows[:40]
