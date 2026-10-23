from typing import Dict, Iterable, List

from digital_twin.modules.model_registry.domain.events import investment_strategy_approved_event, investment_strategy_deployed_event, investment_strategy_performance_recorded_event, investment_strategy_proposed_event, investment_strategy_validated_event
from digital_twin.modules.model_registry.domain.investment_strategy_proposals import InvestmentStrategyProposal, PROMOTABLE_STRATEGY_STATUSES, STRATEGY_STATUS_APPROVED, STRATEGY_STATUS_DEPLOYED, STRATEGY_STATUS_PROPOSED, STRATEGY_STATUS_RETIRED, STRATEGY_STATUS_VALIDATED, clean_list, proposal_matches_rule_ids, rule_id_from_payload, strategy_proposals_from_rule_candidates
from digital_twin.modules.model_registry.domain.investment_strategy_lifecycle import append_strategy_review_log, merge_strategy_performance, strategy_performance_sample
from digital_twin.modules.portfolio.contracts import utc_now_iso


class InvestmentStrategyProposalService:
    def __init__(
        self,
        proposal_store,
        ontology_repository=None,
        event_publisher=None,
        settings: Dict[str, object] = None,
        strategy_repository_provider=None,
    ):
        self.proposal_store = proposal_store
        self.ontology_repository = ontology_repository
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})
        self.strategy_repository_provider = strategy_repository_provider

    def strategy_runtime(self):
        if self.strategy_repository_provider:
            return self.strategy_repository_provider()
        return self.ontology_repository, self.settings.get("_strategySourceDeployment") or {}

    def design_catalog(self):
        from ..domain.rule_strategy_contract import strategy_authoring_catalog
        repository, binding = self.strategy_runtime()
        if not repository:
            raise RuntimeError("A frozen RuleBox is required for strategy authoring")
        rulebox = repository.rulebox_snapshot()
        if rulebox.get("status") != "ok" or not rulebox.get("rules"):
            raise RuntimeError("RuleBox is unavailable for strategy authoring")
        return strategy_authoring_catalog(rulebox, binding)

    def create_design(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("strategy design must be an object")
        from ..domain.rule_strategy_compilation import assemble_strategy_design
        from ..domain.ontology_rulebox_governance import normalize_rule_change_candidate
        catalog = self.design_catalog()
        if payload.get("sourceRuleboxFingerprint") != catalog["sourceRuleboxFingerprint"]:
            raise ValueError("RuleBox changed; reload the strategy design catalog")
        raw = assemble_strategy_design({"title": payload.get("title"), "rationale": payload.get("rationale", ""),
                                       "strategyDesign": payload.get("strategyDesign")}, catalog)
        candidate = normalize_rule_change_candidate(raw, source="rulebox-strategy-design")
        return self.propose_from_rule_candidates({"candidates": [candidate]}, {
            "trigger": "short-term-strategy-design", "symbols": payload.get("symbols") or [],
            "ruleBox": {"rulesHash": catalog["sourceRuleboxFingerprint"]}})

    def list(self, limit: int = 100, detail: str = "full") -> Dict[str, object]:
        safe_limit = max(1, min(500, int(limit or 100)))
        proposals = self.proposal_store.list(limit=safe_limit) if self.proposal_store else []
        summary_only = str(detail or "full").lower() == "summary"
        return {
            "proposals": [item.to_summary_dict() if summary_only else item.to_dict() for item in proposals],
            "count": len(proposals),
            "detailLevel": "summary" if summary_only else "full",
            "limit": safe_limit,
        }

    def status(self) -> Dict[str, object]:
        counter = getattr(self.proposal_store, "status_counts", None) if self.proposal_store else None
        if callable(counter):
            statuses = dict(counter() or {})
        else:
            proposals = self.proposal_store.list() if self.proposal_store else []
            statuses: Dict[str, int] = {}
            for proposal in proposals:
                statuses[proposal.status] = statuses.get(proposal.status, 0) + 1
        return {
            "count": sum(int(value or 0) for value in statuses.values()),
            "proposedCount": statuses.get(STRATEGY_STATUS_PROPOSED, 0),
            "validatedCount": statuses.get(STRATEGY_STATUS_VALIDATED, 0),
            "approvedCount": statuses.get(STRATEGY_STATUS_APPROVED, 0),
            "deployedCount": statuses.get(STRATEGY_STATUS_DEPLOYED, 0),
            "retiredCount": statuses.get(STRATEGY_STATUS_RETIRED, 0),
            "statuses": statuses,
        }

    def get(self, proposal_id: str) -> Dict[str, object]:
        proposal = self.proposal_store.get(proposal_id) if self.proposal_store else None
        if not proposal:
            return {"status": "not-found", "id": proposal_id}
        return {"status": "ok", "proposal": proposal.to_dict()}

    def propose_from_rule_candidates(
        self,
        candidate_result: Dict[str, object],
        context: Dict[str, object] = None,
    ) -> Dict[str, object]:
        if not self.proposal_store:
            return {"status": "disabled", "proposalCount": 0, "savedCount": 0, "reason": "Strategy proposal store is not configured."}
        candidate_result = dict(candidate_result or {})
        context = {
            **dict(context or {}),
            "trigger": (context or {}).get("trigger") or candidate_result.get("trigger"),
            "symbols": (context or {}).get("symbols") or candidate_result.get("symbols") or [],
        }
        if isinstance(candidate_result.get("contextSummary"), dict):
            context.setdefault("candidateContextSummary", candidate_result.get("contextSummary"))
        candidates = [dict(item) for item in (candidate_result.get("candidates") or []) if isinstance(item, dict)]
        proposals = strategy_proposals_from_rule_candidates(candidates, context)
        created = []
        updated = []
        skipped = []
        for proposal in proposals:
            existing = self.proposal_store.get(proposal.proposal_id)
            if existing and existing.status in {STRATEGY_STATUS_DEPLOYED, STRATEGY_STATUS_RETIRED}:
                skipped.append({"id": proposal.proposal_id, "reason": "terminal-status", "status": existing.status})
                continue
            if existing:
                merged = merge_strategy_proposal(existing, proposal)
                append_strategy_review_log(merged, "updated", {
                    "trigger": context.get("trigger"),
                    "source": "rule-change-candidate",
                    "ruleIds": proposal.rule_ids,
                })
                self.proposal_store.save(merged)
                updated.append(merged.to_dict())
            else:
                append_strategy_review_log(proposal, "proposed", {
                    "trigger": context.get("trigger"),
                    "source": "rule-change-candidate",
                    "ruleIds": proposal.rule_ids,
                })
                self.proposal_store.save(proposal)
                created.append(proposal.to_dict())
                self.publish(investment_strategy_proposed_event(proposal))
        return {
            "status": "created" if created else ("updated" if updated else ("no-candidates" if not proposals else "skipped")),
            "proposalCount": len(proposals),
            "createdCount": len(created),
            "updatedCount": len(updated),
            "skippedCount": len(skipped),
            "savedCount": len(created) + len(updated),
            "proposals": created + updated,
            "skipped": skipped,
        }

    def record_experiment_validation(self, experiment, result: Dict[str, object]) -> Dict[str, object]:
        if not self.proposal_store:
            return {"status": "disabled", "updatedCount": 0}
        result = dict(result or {})
        rule_ids = experiment_rule_ids(getattr(experiment, "candidate_rules", []) or [])
        if not rule_ids:
            return {"status": "no-rules", "updatedCount": 0}
        proposals = self.find_by_rule_ids(rule_ids)
        if not proposals:
            proposal = proposal_from_experiment(experiment, result)
            self.proposal_store.save(proposal)
            self.publish(investment_strategy_proposed_event(proposal))
            proposals = [proposal]
        updated = []
        for proposal in proposals:
            proposal.source_experiment_id = getattr(experiment, "experiment_id", "") or proposal.source_experiment_id
            proposal.validation = strategy_validation_payload(result)
            proposal.lifecycle = {
                **dict(proposal.lifecycle or {}),
                "lastExperimentRunAt": result.get("completedAt") or utc_now_iso(),
                "lastExperimentId": getattr(experiment, "experiment_id", ""),
                "promotionReadiness": result.get("promotionReadiness") if isinstance(result.get("promotionReadiness"), dict) else {},
            }
            proposal.status = (STRATEGY_STATUS_VALIDATED if validation_is_complete(proposal.validation)
                               and not proposal.metadata.get("strategyContract") else proposal.status)
            append_strategy_review_log(proposal, "validated" if proposal.status == STRATEGY_STATUS_VALIDATED else "validation-recorded", {
                "experimentId": getattr(experiment, "experiment_id", ""),
                "validationStatus": proposal.validation.get("status"),
                "promotionReadiness": proposal.validation.get("promotionReadiness") or {},
            })
            proposal.updated_at = utc_now_iso()
            self.proposal_store.save(proposal)
            updated.append(proposal.to_dict())
            if proposal.status == STRATEGY_STATUS_VALIDATED:
                self.publish(investment_strategy_validated_event(proposal))
        return {
            "status": "updated" if updated else "no-proposals",
            "updatedCount": len(updated),
            "ruleIds": rule_ids,
            "proposals": updated,
        }

    def validate_materialization(self, proposal_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        proposal = self.proposal_store.get(proposal_id) if self.proposal_store else None
        if not proposal:
            return {"status": "not-found", "id": proposal_id}
        rule_payloads = proposal_rule_payloads(proposal)
        research_strategy = bool((proposal.metadata or {}).get("strategyContract"))
        repository, binding = self.strategy_runtime() if research_strategy else (self.ontology_repository, {})
        if research_strategy and proposal.metadata["strategyContract"]["sourceDeployment"] != binding:
            validation = {"status": "source-deployment-changed", "reason": "전략을 작성한 배포와 현재 알림 담당 배포가 다릅니다. 새 배포의 RuleBox에서 전략 버전을 다시 작성해야 합니다.",
                          "mutatedOperationalRuleBox": False, "wroteInferenceBox": False}
        elif not repository or not hasattr(repository, "validate_rulebox_materialization"):
            validation = {
                "status": "requires-typedb",
                "reason": "Ontology repository does not support candidate materialization preview.",
                "validatedAt": utc_now_iso(),
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
            }
        elif not rule_payloads:
            validation = {
                "status": "missing-rules",
                "reason": "Strategy proposal has no proposed RuleBox payload.",
                "validatedAt": utc_now_iso(),
                "mutatedOperationalRuleBox": False,
                "wroteInferenceBox": False,
            }
        else:
            validation = repository.validate_rulebox_materialization({
                **dict(payload or {}),
                "rules": rule_payloads,
                "symbols": proposal.symbols,
                "proposalId": proposal.proposal_id,
            })
            validation = validation if isinstance(validation, dict) else {"status": "unknown"}
        validation = materialization_validation_with_diff(validation)
        proposal.validation = {**dict(proposal.validation or {}), "materialization": validation}
        if research_strategy:
            from ..domain.rule_strategy_compilation import strategy_phase_evaluation
            validation["decisionEligibility"] = "research-only"
            validation["tradeTimingQualified"] = False
            validation["remainingChecks"] = list(proposal.metadata["strategyContract"]["validationRequirements"])
            validation["strategyPhaseEvaluation"] = strategy_phase_evaluation(proposal.metadata["strategyContract"], validation)
        if str(validation.get("status") or "") in {"ok", "empty"} and not research_strategy:
            proposal.status = STRATEGY_STATUS_VALIDATED
            self.publish(investment_strategy_validated_event(proposal))
        append_strategy_review_log(proposal, "materialization-validated", {
            "validationStatus": validation.get("status"),
            "candidateRuleCount": validation.get("candidateRuleCount"),
            "matchedCount": validation.get("matchedCount"),
            "diff": validation.get("diff") or {},
        })
        proposal.updated_at = utc_now_iso()
        self.proposal_store.save(proposal)
        return {"status": str(validation.get("status") or "unknown"), "proposal": proposal.to_dict(), "validation": validation}

    def approve(self, proposal_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        payload = dict(payload or {})
        proposal = self.proposal_store.get(proposal_id) if self.proposal_store else None
        if not proposal:
            return {"status": "not-found", "id": proposal_id}
        if (proposal.metadata or {}).get("strategyContract"):
            return {"status": "not-ready", "id": proposal_id, "reason": "strategy-v1-research-only",
                    "remainingChecks": proposal.metadata["strategyContract"]["validationRequirements"],
                    "proposal": proposal.to_dict()}
        if proposal.status not in PROMOTABLE_STRATEGY_STATUSES and not truthy(payload.get("forceApproved"), False):
            return {"status": "not-ready", "id": proposal_id, "reason": "proposal-status-not-approvable", "proposal": proposal.to_dict()}
        stamp = utc_now_iso()
        reviewer = str(payload.get("approvedBy") or payload.get("reviewedBy") or "local-user")
        reason = str(payload.get("approvalReason") or payload.get("reviewReason") or "strategy proposal approved")
        proposal.status = STRATEGY_STATUS_APPROVED
        proposal.approved_at = stamp
        proposal.updated_at = stamp
        proposal.lifecycle = {
            **dict(proposal.lifecycle or {}),
            "approvedBy": reviewer,
            "approvalReason": reason,
        }
        append_strategy_review_log(proposal, "approved", {
            "reviewedBy": reviewer,
            "reviewReason": reason,
            "forceApproved": truthy(payload.get("forceApproved"), False),
        })
        self.proposal_store.save(proposal)
        self.publish(investment_strategy_approved_event(proposal))
        return {"status": "approved", "proposal": proposal.to_dict()}

    def mark_deployed_by_experiment(self, experiment, application: Dict[str, object]) -> Dict[str, object]:
        rule_ids = clean_list((application or {}).get("ruleIds") or experiment_rule_ids(getattr(experiment, "candidate_rules", []) or []), 64)
        proposals = self.find_by_rule_ids(rule_ids)
        updated = []
        for proposal in proposals:
            if proposal.metadata.get("strategyContract"):
                continue  # Research phase materialization is not a strategy trading deployment.
            stamp = str((application or {}).get("appliedAt") or utc_now_iso())
            proposal.status = STRATEGY_STATUS_DEPLOYED
            proposal.deployed_at = stamp
            proposal.updated_at = stamp
            proposal.source_experiment_id = getattr(experiment, "experiment_id", "") or proposal.source_experiment_id
            proposal.lifecycle = {
                **dict(proposal.lifecycle or {}),
                "deployment": dict(application or {}),
            }
            append_strategy_review_log(proposal, "deployed", {
                "experimentId": getattr(experiment, "experiment_id", ""),
                "ruleIds": rule_ids,
                "appliedAt": stamp,
            })
            self.proposal_store.save(proposal)
            updated.append(proposal.to_dict())
            self.publish(investment_strategy_deployed_event(proposal))
        return {"status": "updated" if updated else "no-proposals", "updatedCount": len(updated), "ruleIds": rule_ids, "proposals": updated}

    def performance(self, proposal_id: str) -> Dict[str, object]:
        proposal = self.proposal_store.get(proposal_id) if self.proposal_store else None
        if not proposal:
            return {"status": "not-found", "id": proposal_id}
        return {
            "status": "ok",
            "proposalId": proposal.proposal_id,
            "performance": dict(proposal.performance or {}),
        }

    def record_performance_sample(self, proposal_id: str, payload: Dict[str, object] = None) -> Dict[str, object]:
        proposal = self.proposal_store.get(proposal_id) if self.proposal_store else None
        if not proposal:
            return {"status": "not-found", "id": proposal_id}
        sample = strategy_performance_sample(payload or {}, proposal.symbols)
        proposal.performance = merge_strategy_performance(proposal.performance, sample)
        append_strategy_review_log(proposal, "performance-recorded", {
            "observedAt": sample.get("observedAt"),
            "portfolioReturnPct": sample.get("portfolioReturnPct"),
            "benchmarkReturnPct": sample.get("benchmarkReturnPct"),
        })
        proposal.updated_at = utc_now_iso()
        self.proposal_store.save(proposal)
        self.publish(investment_strategy_performance_recorded_event(proposal, sample))
        return {
            "status": "recorded",
            "proposal": proposal.to_dict(),
            "sample": sample,
            "performance": dict(proposal.performance or {}),
        }

    def find_by_rule_ids(self, rule_ids: Iterable[object]) -> List[InvestmentStrategyProposal]:
        proposals = self.proposal_store.list() if self.proposal_store else []
        return [proposal for proposal in proposals if proposal_matches_rule_ids(proposal, rule_ids)]

    def publish(self, event) -> None:
        if self.event_publisher and hasattr(self.event_publisher, "publish"):
            self.event_publisher.publish(event)


def merge_strategy_proposal(existing: InvestmentStrategyProposal, incoming: InvestmentStrategyProposal) -> InvestmentStrategyProposal:
    existing.title = incoming.title or existing.title
    existing.thesis = incoming.thesis or existing.thesis
    existing.symbols = clean_list(list(existing.symbols or []) + list(incoming.symbols or []), 64)
    existing.target_universe = clean_list(list(existing.target_universe or []) + list(incoming.target_universe or []), 64)
    existing.entry_conditions = clean_list(list(existing.entry_conditions or []) + list(incoming.entry_conditions or []), 24)
    existing.exit_conditions = clean_list(list(existing.exit_conditions or []) + list(incoming.exit_conditions or []), 24)
    existing.risk_controls = clean_list(list(existing.risk_controls or []) + list(incoming.risk_controls or []), 24)
    existing.position_sizing = clean_list(list(existing.position_sizing or []) + list(incoming.position_sizing or []), 16)
    existing.rebalance_policy = clean_list(list(existing.rebalance_policy or []) + list(incoming.rebalance_policy or []), 16)
    existing.evidence_refs = clean_list(list(existing.evidence_refs or []) + list(incoming.evidence_refs or []), 64)
    existing.rule_ids = clean_list(list(existing.rule_ids or []) + list(incoming.rule_ids or []), 64)
    existing.source_candidate_ids = clean_list(list(existing.source_candidate_ids or []) + list(incoming.source_candidate_ids or []), 64)
    existing.source_trigger = incoming.source_trigger or existing.source_trigger
    existing.rulebox_hash = incoming.rulebox_hash or existing.rulebox_hash
    existing.inference_generation_id = incoming.inference_generation_id or existing.inference_generation_id
    existing.metadata = {**dict(existing.metadata or {}), **dict(incoming.metadata or {})}
    existing.updated_at = utc_now_iso()
    return existing


def experiment_rule_ids(candidate_rules: Iterable[Dict[str, object]]) -> List[str]:
    return clean_list([rule_id_from_payload(item) for item in candidate_rules or [] if isinstance(item, dict)], 64)


def proposal_from_experiment(experiment, result: Dict[str, object]) -> InvestmentStrategyProposal:
    stamp = utc_now_iso()
    rule_ids = experiment_rule_ids(getattr(experiment, "candidate_rules", []) or [])
    proposed = result.get("proposedOntologyChanges") if isinstance(result.get("proposedOntologyChanges"), dict) else {}
    return InvestmentStrategyProposal(
        proposal_id="strategy-proposal-" + str(getattr(experiment, "experiment_id", "") or "experiment").replace("ontology-exp-", "")[:20],
        title=str(getattr(experiment, "title", "") or "Ontology lab strategy proposal"),
        thesis=str(getattr(experiment, "hypothesis", "") or "Ontology lab candidate strategy"),
        symbols=clean_list(getattr(experiment, "symbols", []) or [], 64),
        target_universe=clean_list(getattr(experiment, "symbols", []) or [], 64),
        entry_conditions=clean_list(proposed.get("decisionStages") or [], 24),
        risk_controls=clean_list(proposed.get("relationTypes") or [], 24),
        rule_ids=rule_ids,
        source_experiment_id=str(getattr(experiment, "experiment_id", "") or ""),
        source_trigger="ontology-lab",
        status=STRATEGY_STATUS_PROPOSED,
        validation=strategy_validation_payload(result),
        metadata={"source": "ontology-lab-experiment"},
        created_at=stamp,
        updated_at=stamp,
    )


def strategy_validation_payload(result: Dict[str, object]) -> Dict[str, object]:
    result = dict(result or {})
    return {
        "status": str(result.get("status") or ""),
        "validatedAt": str(result.get("completedAt") or utc_now_iso()),
        "promotionReadiness": dict(result.get("promotionReadiness") or {}),
        "sandbox": dict(result.get("sandbox") or {}),
        "inference": {
            "aggregateDelta": dict((result.get("inference") or {}).get("aggregateDelta") or {})
            if isinstance(result.get("inference"), dict)
            else {}
        },
        "recommendations": [dict(item) for item in (result.get("recommendations") or []) if isinstance(item, dict)][:6],
        "mutatedOperationalRuleBox": bool((result.get("sandbox") or {}).get("mutatedOperationalRuleBox")) if isinstance(result.get("sandbox"), dict) else False,
        "mutatedTypeDB": bool((result.get("sandbox") or {}).get("mutatedTypeDB")) if isinstance(result.get("sandbox"), dict) else False,
    }


def validation_is_complete(validation: Dict[str, object]) -> bool:
    if str((validation or {}).get("status") or "") != "completed":
        return False
    sandbox = (validation or {}).get("sandbox") if isinstance((validation or {}).get("sandbox"), dict) else {}
    return int(sandbox.get("graphRunCount") or 0) > 0


def proposal_rule_payloads(proposal: InvestmentStrategyProposal) -> List[Dict[str, object]]:
    metadata = proposal.metadata if isinstance(proposal.metadata, dict) else {}
    rule = metadata.get("proposedRule") if isinstance(metadata.get("proposedRule"), dict) else {}
    contract = (rule.get("claim_contract") or {}).get("strategyContract")
    if contract:
        from ..domain.rule_strategy_compilation import compile_strategy_rules
        return [r.to_dict() for r in compile_strategy_rules(contract)]
    return [dict(rule)] if rule else []


def materialization_validation_with_diff(validation: Dict[str, object]) -> Dict[str, object]:
    validation = dict(validation or {})
    if "diff" in validation and isinstance(validation.get("diff"), dict):
        return validation
    baseline = validation.get("baselineInferenceBox") if isinstance(validation.get("baselineInferenceBox"), dict) else {}
    matched_count = int(number_or_none(validation.get("matchedCount")) or 0)
    baseline_relations = int(number_or_none(baseline.get("relationCount")) or 0)
    baseline_traces = int(number_or_none(baseline.get("traceCount")) or 0)
    validation["diff"] = {
        "baselineRelationCount": baseline_relations,
        "baselineTraceCount": baseline_traces,
        "candidateMatchedCount": matched_count,
        "matchedMinusBaselineRelations": matched_count - baseline_relations,
        "validationOnly": bool(validation.get("validationOnly", True)),
        "wroteInferenceBox": bool(validation.get("wroteInferenceBox")),
    }
    return validation


def number_or_none(value: object):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in {"0", "false", "no", "off", "disabled"}
