import json
from typing import Callable, Dict, Iterable, List

from digital_twin.modules.model_registry.domain.hypothesis_compilation import ranked_authoring_rules, rule_design_context
from digital_twin.modules.reasoning.contracts import portfolio_world_id


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
        model_signal_store=None,
    ):
        self.ontology_repository = ontology_repository
        self.advisor = advisor
        self.event_reader = event_reader
        self.settings = dict(settings or {})
        self.strategy_proposal_service = strategy_proposal_service
        self.model_signal_store = model_signal_store

    def propose(
        self,
        symbols: Iterable[str] = None,
        trigger: str = "manual",
        requests: Iterable[object] = None,
        alerts: Iterable[object] = None,
        account_id: str = "",
        tenant_id: str = "",
        hypothesis_proposal: Dict[str, object] = None,
        context_observer: Callable[[Dict[str, object]], None] = None,
    ) -> Dict[str, object]:
        if not self.ontology_repository or not self.advisor:
            return {"status": "disabled", "reason": "Rule candidate advisor is not configured.", "candidateCount": 0, "savedCount": 0}
        clean_symbols = sorted(set(str(item or "").upper().strip() for item in (symbols or []) if str(item or "").strip()))
        world_id = portfolio_world_id(account_id, tenant_id) if str(account_id or "").strip() else ""
        context = self.build_context(clean_symbols, trigger, requests, alerts, world_id=world_id,
                                     read_inference=not bool(hypothesis_proposal))
        for box in ("ruleBox", "inferenceBox"):
            if str((context.get(box) or {}).get("status") or "") in {"error", "unavailable", "disabled"}:
                raise RuntimeError(box + " unavailable: " + str((context.get(box) or {}).get("reason") or ""))
        if isinstance(hypothesis_proposal, dict) and hypothesis_proposal:
            context["hypothesisProposal"] = dict(hypothesis_proposal)
            context["modelAssessmentContext"] = self.model_assessment_context(context, account_id)
        design = rule_design_context(context) if hypothesis_proposal else {}
        context_summary = {
            "designVersion": design.get("version"),
            "ruleboxSnapshotId": design.get("ruleboxSnapshotId"),
            "ruleboxRulesHash": design.get("rulesHash"),
            "observationState": design.get("observationState"),
            "exampleRuleIds": [item.get("rule_id") or item.get("ruleId") for item in design.get("examples") or []],
            "capabilityIndexCoverage": (design.get("capabilityIndex") or {}).get("coverage"),
            "capabilityIndexRuleCount": (design.get("capabilityIndex") or {}).get("includedRuleCount"),
            "modelAssessmentContext": context.get("modelAssessmentContext") or {},
            "recentEventCount": len(context.get("recentEvents") or []),
            "alertCount": len(context.get("alerts") or []),
            "ruleCount": (context.get("ruleBox") or {}).get("ruleCount", 0),
            "inferenceRelationCount": (context.get("inferenceBox") or {}).get("relationCount"),
        }
        if context_observer:
            context_observer(context_summary)
        candidates = list(self.advisor.propose(context) or [])[: self.max_candidates()]
        persist_as_general_candidate = not bool(hypothesis_proposal)
        save_result = self.ontology_repository.save_rule_change_candidates(candidates, {
            "trigger": trigger,
            "symbols": clean_symbols,
            "worldId": world_id,
            "promptContext": context,
        }) if candidates and persist_as_general_candidate and hasattr(self.ontology_repository, "save_rule_change_candidates") else {
            "status": "skipped",
            "savedCount": 0,
            "reason": "Hypothesis candidates are persisted only after development governance." if hypothesis_proposal else "No candidates to save.",
        }
        result = {
            "status": "ok" if candidates else "no-candidates",
            "trigger": trigger,
            "symbols": clean_symbols,
            "worldId": world_id,
            "candidateCount": len(candidates),
            "savedCount": int(save_result.get("savedCount") or 0),
            "candidates": candidates,
            "saveResult": save_result,
            "advisor": self.advisor_metadata(),
            "contextSummary": context_summary,
        }
        if not hypothesis_proposal and self.strategy_proposal_service and hasattr(self.strategy_proposal_service, "propose_from_rule_candidates"):
            result["strategyProposalResult"] = self.strategy_proposal_service.propose_from_rule_candidates(result, context)
        return result

    def model_assessment_context(self, context, account_id):
        """Read bounded, subject-specific evaluation receipts; never re-score facts."""
        symbols = context.get("symbols") or []
        if not self.model_signal_store or not account_id or len(symbols) != 1:
            return {"status": "not-queried", "snapshots": []}
        symbol = symbols[0]
        rules = ranked_authoring_rules(context)[:8]
        rule_ids = {str(row.get("rule_id") or row.get("ruleId") or "") for row in rules}
        labels = {str(row.get("rule_id") or row.get("ruleId") or ""): str(row.get("label") or "") for row in rules}
        release_ids = []
        for rule in rules:
            for condition in rule.get("conditions") or []:
                if not isinstance(condition, dict) or (condition.get("relation_type") or condition.get("relationType")) != "HAS_MODEL_SIGNAL":
                    continue
                filters = condition.get("target_property_filters") or condition.get("targetPropertyFilters") or {}
                release_id = filters.get("releaseId") if isinstance(filters, dict) else None
                if isinstance(release_id, str) and release_id and release_id not in release_ids:
                    release_ids.append(release_id)
        snapshots = []
        errors = []
        remaining_bytes = 12000
        omitted_assessments = 0
        for release_id in release_ids[:3]:
            try:
                payload = self.model_signal_store.latest(account_id, subject_id=symbol, model_release_id=release_id) or {}
                if not payload:
                    continue
                if payload.get("accountId") != account_id or payload.get("modelReleaseId") != release_id:
                    raise ValueError("Model assessment scope mismatch")
                assessments = []
                for item in payload.get("assessments") or []:
                    if not isinstance(item, dict) or item.get("subjectId") != symbol or item.get("hypothesisContractId") not in rule_ids:
                        continue
                    receipt = {key: item.get(key) for key in (
                        "assessmentId", "subjectId", "hypothesisContractId", "status", "decisionEligibility",
                        "matchedConditionIds", "failedConditionIds", "unknownConditionIds", "evidenceIds",
                        "sourceFeatureSnapshotId", "observedAt", "knowledgeCutoffAt", "scorerVersion",
                    )}
                    receipt["ruleLabel"] = labels.get(str(item.get("hypothesisContractId") or ""), "")
                    size = len(json.dumps(receipt, ensure_ascii=False).encode("utf-8"))
                    if size > remaining_bytes or len(assessments) >= 8:
                        omitted_assessments += 1
                        continue
                    assessments.append(receipt)
                    remaining_bytes -= size
                snapshots.append({
                    "snapshotId": payload.get("snapshotId"), "asOf": payload.get("asOf"),
                    "modelReleaseId": release_id, "sourceFeatureSnapshotId": payload.get("sourceFeatureSnapshotId"),
                    "assessments": assessments,
                })
            except Exception as error:  # noqa: BLE001 - authoring can proceed with explicitly unknown observations.
                errors.append({"modelReleaseId": release_id, "reason": str(error)[:250]})
        return {
            "status": "partial" if errors or omitted_assessments or len(release_ids) > 3 else ("available" if snapshots else "not-recorded"),
            "purpose": "authoring-reference-not-historical-replay-or-current-inference-proof",
            "accountId": account_id, "symbol": symbol, "snapshots": snapshots, "errors": errors,
            "omittedReleaseCount": max(0, len(release_ids) - 3),
            "omittedAssessmentCount": omitted_assessments,
        }

    def propose_hypothesis(
        self,
        proposal: Dict[str, object],
        account_id: str = "",
        tenant_id: str = "",
        context_observer: Callable[[Dict[str, object]], None] = None,
    ) -> Dict[str, object]:
        proposal = dict(proposal or {})
        symbol = str(proposal.get("symbol") or "").upper().strip()
        return self.propose(
            symbols=[symbol] if symbol else [],
            trigger="novel-hypothesis-auto-promotion",
            account_id=str(account_id or proposal.get("accountId") or ""),
            tenant_id=str(tenant_id or ""),
            hypothesis_proposal=proposal,
            context_observer=context_observer,
        )

    def build_context(
        self,
        symbols: List[str],
        trigger: str,
        requests: Iterable[object] = None,
        alerts: Iterable[object] = None,
        world_id: str = "",
        read_inference: bool = True,
    ) -> Dict[str, object]:
        rulebox = self.ontology_repository.rulebox_snapshot() if hasattr(self.ontology_repository, "rulebox_snapshot") else {}
        # Authoring uses the stored proposal and rule syntax. Only a runnable
        # candidate needs the live TypeDB validation performed by development.
        inferencebox = {} if read_inference else {
            "status": "deferred-validation", "relations": [],
            "reason": "후보 명세 생성 단계입니다. 원본 제안의 근거 ID는 출처 참조이며 현재 사실 확인은 후보 생성 후 TypeDB 검증에서 수행합니다.",
            "decisionEligibility": "authoring-only",
        }
        if read_inference and hasattr(self.ontology_repository, "inferencebox_snapshot"):
            try:
                inferencebox = self.ontology_repository.inferencebox_snapshot(symbols, limit=80, world_id=world_id)
            except TypeError as error:
                if "unexpected keyword" not in str(error) and "world_id" not in str(error):
                    raise
                if world_id:
                    raise RuntimeError("Scoped InferenceBox reads are required for hypothesis compilation") from error
                inferencebox = self.ontology_repository.inferencebox_snapshot(symbols, limit=80)
        request_items = [self.event_payload(event) for event in (requests or [])]
        recent_events = request_items or (self.recent_events() if read_inference else [])
        if symbols:
            recent_events = [item for item in recent_events if set(item.get("symbols") or []) & set(symbols)]
        return {
            "trigger": trigger,
            "symbols": symbols,
            "worldId": world_id,
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
            "reviewLevel": metadata.get("reviewLevel"),
            "dataState": metadata.get("dataState"),
            "changeState": metadata.get("changeState"),
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
