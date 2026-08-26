import hashlib
from datetime import datetime, timezone
from typing import Dict, Iterable, List

from ..domain.events import (
    HYPOTHESIS_DEVELOPMENT_DEPLOYED,
    HYPOTHESIS_DEVELOPMENT_TRANSITIONED,
    HYPOTHESIS_DEVELOPMENT_VALIDATED,
    hypothesis_development_event,
)
from ..domain.hypothesis_development import (
    HypothesisDevelopmentCase,
    TERMINAL_HYPOTHESIS_DEVELOPMENT_STATUSES,
    hypothesis_decision_impact,
    screen_hypothesis_case,
    validation_gate,
)
from ..domain.ontology_experiments import OntologyExperiment, normalize_candidate_rules, rulebox_metrics
from ..domain.ontology_rulebox_contracts import GraphInferenceRule
from ..domain.ontology_rulebox_governance import rulebox_semantic_violations
from ..domain.ontology_worlds import portfolio_world_id
from ..domain.portfolio import utc_now_iso


class HypothesisDevelopmentService:
    """Automatically validates novel hypotheses without mutating the operational RuleBox."""

    def __init__(
        self,
        case_store,
        proposal_store,
        experiment_store,
        rule_candidate_service,
        ontology_repository,
        monitor_store=None,
        event_publisher=None,
        settings: Dict[str, object] = None,
    ):
        self.case_store = case_store
        self.proposal_store = proposal_store
        self.experiment_store = experiment_store
        self.rule_candidate_service = rule_candidate_service
        self.ontology_repository = ontology_repository
        self.monitor_store = monitor_store
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})

    def ingest_proposal(self, proposal: Dict[str, object], inference_generation_id: str = "") -> Dict[str, object]:
        incoming = HypothesisDevelopmentCase.from_proposal(proposal, inference_generation_id)
        existing = self.case_store.get_by_fingerprint(incoming.fingerprint) if self.case_store else None
        case = existing or incoming
        if existing:
            case.merge_proposal(proposal, inference_generation_id)
        self.persist(case, "proposal-merged" if existing else "proposal-ingested")
        if case.status in TERMINAL_HYPOTHESIS_DEVELOPMENT_STATUSES or case.status in {"approval-required", "deployed", "observing"}:
            return {"status": case.status, "case": case.to_dict(), "merged": bool(existing)}
        result = self.process(case.case_id)
        result["merged"] = bool(existing)
        return result

    def process(self, case_id: str) -> Dict[str, object]:
        case = self.case_store.get(case_id) if self.case_store else None
        if not case:
            return {"status": "not-found", "caseId": str(case_id or "")}
        if case.candidate_rule and case.experiment_id and case.status in {"needs-data", "compiled", "validating"}:
            experiment = self.experiment_store.get(case.experiment_id) if self.experiment_store else None
            if experiment:
                case.transition("validating", "validation")
                self.persist(case, "validation-resumed")
                return self.validate(case, experiment)
        case.transition("screening", "screening")
        self.persist(case, "screening-started")
        screening = screen_hypothesis_case(case)
        case.classification = str(screening.get("classification") or case.classification)
        evidence_status = "passed" if case.supporting_evidence_ids else "needs-data"
        case.update_gates([
            screening.get("gate") or {},
            validation_gate(
                "evidence",
                "근거 품질",
                evidence_status,
                (str(len(case.supporting_evidence_ids)) + "개 등록 근거 ID를 확인했습니다.") if case.supporting_evidence_ids else "등록된 지지 근거 ID가 없습니다.",
                True,
                {
                    "supportingEvidenceCount": len(case.supporting_evidence_ids),
                    "counterEvidenceCount": len(case.counter_evidence_ids),
                },
            ),
            validation_gate(
                "counterevidence",
                "반증 가능성",
                "passed" if case.invalidation_conditions else "blocked",
                str(len(case.invalidation_conditions)) + "개 무효화 조건과 " + str(len(case.counter_evidence_ids)) + "개 반대 근거를 기록했습니다.",
                True,
            ),
        ])
        screen_status = str(screening.get("status") or "needs-revision")
        if screen_status != "passed":
            case.transition(screen_status, "screening", ", ".join(screening.get("issues") or screening.get("needsData") or []))
            self.persist(case, "screening-stopped", case.blocked_reason)
            return {"status": case.status, "case": case.to_dict()}
        candidate_result = self.compile_candidate(case)
        candidates = [dict(item) for item in candidate_result.get("candidates") or [] if isinstance(item, dict)]
        candidate = next((item for item in candidates if isinstance(item.get("proposedRule"), dict)), None)
        if not candidate:
            needs_data = sorted({str(value) for item in candidates for value in (item.get("requiresData") or []) if str(value)})
            status = "needs-data" if needs_data else "needs-revision"
            reason = ", ".join(needs_data) or str(candidate_result.get("reason") or "AI가 실행 가능한 후보 규칙을 만들지 못했습니다.")
            case.transition(status, "compilation", reason)
            self.persist(case, "compilation-stopped", reason)
            return {"status": case.status, "case": case.to_dict(), "candidateResult": self.compact_candidate_result(candidate_result)}
        prepared_rule = self.governed_candidate_rule(case, candidate.get("proposedRule") or {})
        candidate["proposedRule"] = prepared_rule
        case.candidate_id = str(candidate.get("id") or "")
        case.candidate_rule = prepared_rule
        case.decision_impact = hypothesis_decision_impact(prepared_rule)
        rule_conflict = self.existing_rule_id_conflict(prepared_rule)
        case.update_gates([
            validation_gate(
                "deduplication",
                "중복·기존 규칙",
                "blocked" if rule_conflict else "passed",
                rule_conflict or "동일 가설은 개발 케이스에 병합하고 기존 rule_id와 충돌하지 않습니다.",
                True,
            ),
            validation_gate("decision-impact", "판단 영향", "passed", str(case.decision_impact.get("influence") or "explanation-only"), False, case.decision_impact),
        ])
        if rule_conflict:
            case.transition("needs-revision", "compilation", rule_conflict)
            self.persist(case, "candidate-duplicate-blocked", rule_conflict)
            return {"status": case.status, "case": case.to_dict(), "violations": [rule_conflict]}
        structural = self.validate_rule_structure(prepared_rule)
        if structural:
            case.update_gates([
                validation_gate("structure", "가설 구조", "blocked", " | ".join(structural[:8]), True)
            ])
            case.transition("needs-revision", "compilation", " | ".join(structural[:8]))
            self.persist(case, "candidate-structure-blocked", case.blocked_reason)
            return {"status": case.status, "case": case.to_dict(), "violations": structural}
        case.transition("compiled", "compilation")
        self.persist(case, "candidate-compiled")
        experiment = self.create_experiment(case, candidate)
        case.experiment_id = experiment.experiment_id
        case.transition("validating", "validation")
        self.persist(case, "validation-started")
        return self.validate(case, experiment)

    def process_pending(self, limit: int = 5) -> Dict[str, object]:
        statuses = {"proposed", "screening", "compiled", "validating", "needs-data"}
        rows = [
            item
            for item in (self.case_store.list(limit=max(1, min(50, int(limit or 5)))) if self.case_store else [])
            if item.status in statuses
            and (item.status != "needs-data" or (item.candidate_rule and item.experiment_id))
        ]
        results = []
        for item in rows[: max(1, int(limit or 5))]:
            try:
                results.append(self.process(item.case_id))
            except Exception as error:  # noqa: BLE001 - one unavailable dependency must not stop the batch.
                results.append({"status": "error", "caseId": item.case_id, "reason": str(error)[:500]})
        return {
            "status": "processed" if results else "idle",
            "processedCount": len(results),
            "results": results,
        }

    def reconcile_proposal_backlog(self, limit: int = 5) -> Dict[str, object]:
        """Create missing development cases for proposals persisted before automation."""

        if (
            not self.proposal_store
            or not hasattr(self.proposal_store, "list_hypothesis_proposals")
            or not self.case_store
        ):
            return {"status": "unavailable", "processedCount": 0, "results": []}
        proposals = self.proposal_store.list_hypothesis_proposals("", "", 500) or []
        results = []
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            status = str(proposal.get("status") or "review-required").strip().lower()
            if status in {"rejected"}:
                continue
            incoming = HypothesisDevelopmentCase.from_proposal(proposal)
            existing = self.case_store.get_by_fingerprint(incoming.fingerprint)
            if existing:
                continue
            try:
                results.append(self.ingest_proposal(proposal))
            except Exception as error:  # noqa: BLE001 - one legacy proposal cannot block recovery.
                results.append({
                    "status": "error",
                    "proposalId": str(proposal.get("proposalId") or ""),
                    "reason": str(error)[:500],
                })
            if len(results) >= max(1, min(50, int(limit or 5))):
                break
        return {
            "status": "reconciled" if results else "idle",
            "processedCount": len(results),
            "results": results,
        }

    def compile_candidate(self, case: HypothesisDevelopmentCase) -> Dict[str, object]:
        if not self.rule_candidate_service or not hasattr(self.rule_candidate_service, "propose_hypothesis"):
            return {"status": "disabled", "reason": "가설 규칙 후보 서비스가 구성되지 않았습니다.", "candidates": []}
        return self.rule_candidate_service.propose_hypothesis(
            case.to_dict(),
            account_id=case.account_id,
            tenant_id=str(self.settings.get("ontologyTenantId") or self.settings.get("tenantId") or ""),
        )

    def governed_candidate_rule(self, case: HypothesisDevelopmentCase, rule: Dict[str, object]) -> Dict[str, object]:
        prepared = dict(rule or {})
        prepared["enabled"] = False
        prepared["hypothesis_family_key"] = str(prepared.get("hypothesis_family_key") or prepared.get("hypothesisFamilyKey") or "ai-hypothesis." + case.fingerprint[:16])
        conditions = []
        for index, raw in enumerate(prepared.get("conditions") or []):
            item = dict(raw) if isinstance(raw, dict) else {}
            condition_id = str(item.get("condition_id") or item.get("conditionId") or "condition-" + str(index + 1))
            item["condition_id"] = condition_id
            item["hypothesis_scope"] = str(item.get("hypothesis_scope") or item.get("hypothesisScope") or ("account" if case.account_id else "market"))
            item["evidence_group_key"] = str(item.get("evidence_group_key") or item.get("evidenceGroupKey") or condition_id)
            conditions.append(item)
        prepared["conditions"] = conditions
        formation = [str(item.get("condition_id") or "") for item in conditions if str(item.get("role") or "required").lower() not in {"optional", "negative", "exclude", "not"}]
        prepared["hypothesis_lifecycle"] = {
            **dict(prepared.get("hypothesis_lifecycle") or prepared.get("hypothesisLifecycle") or {}),
            "formationConditionIds": formation,
            "invalidationConditionIds": case.invalidation_conditions,
            "validityMinutes": int(self.settings.get("hypothesisDevelopmentDefaultValidityMinutes") or 1440),
            "requiredFreshnessDomains": list(case.required_evidence_types or ["research"]),
            "nextDataRequirements": list(case.required_evidence_types or []),
            "invalidationMode": "typedb-rule-not-materialized-or-condition-invalidated",
        }
        derivations = []
        for raw in prepared.get("derivations") or []:
            item = dict(raw) if isinstance(raw, dict) else {}
            item["evidence_role"] = str(item.get("evidence_role") or item.get("evidenceRole") or item.get("polarity") or "context")
            effect = str(item.get("decision_effect") or item.get("decisionEffect") or "defer").lower()
            item["decision_effect"] = effect if effect in {"defer", "constrain"} else "defer"
            item["candidate_action"] = "HOLD"
            item["candidate_action_label"] = str(item.get("candidate_action_label") or item.get("candidateActionLabel") or "관찰 유지")
            derivations.append(item)
        prepared["derivations"] = derivations
        return GraphInferenceRule.from_dict(prepared).to_dict()

    def validate_rule_structure(self, rule: Dict[str, object]) -> List[str]:
        try:
            enabled = GraphInferenceRule.from_dict({**dict(rule or {}), "enabled": True})
        except ValueError as error:
            return [str(error)]
        return rulebox_semantic_violations([enabled])

    def existing_rule_id_conflict(self, rule: Dict[str, object]) -> str:
        if not self.ontology_repository or not hasattr(self.ontology_repository, "rulebox_snapshot"):
            return ""
        proposed_id = str((rule or {}).get("rule_id") or (rule or {}).get("ruleId") or "").strip()
        if not proposed_id:
            return "candidate-rule-id-missing"
        snapshot = self.ontology_repository.rulebox_snapshot() or {}
        existing_ids = {
            str(item.get("rule_id") or item.get("ruleId") or "").strip()
            for item in snapshot.get("rules") or []
            if isinstance(item, dict)
        }
        return "기존 운영 RuleBox의 rule_id와 충돌합니다: " + proposed_id if proposed_id in existing_ids else ""

    def create_experiment(self, case: HypothesisDevelopmentCase, candidate: Dict[str, object]) -> OntologyExperiment:
        experiment_id = "ontology-exp-hypothesis-" + case.fingerprint[:16]
        existing = self.experiment_store.get(experiment_id) if self.experiment_store else None
        if existing:
            return existing
        rulebox = self.ontology_repository.rulebox_snapshot() if self.ontology_repository and hasattr(self.ontology_repository, "rulebox_snapshot") else {}
        rules, warnings = normalize_candidate_rules({"rules": [candidate.get("proposedRule") or {}]}, rulebox)
        stamp = utc_now_iso()
        experiment = OntologyExperiment(
            experiment_id=experiment_id,
            title="AI 가설 검증: " + case.title,
            hypothesis=case.claim,
            symbols=[case.symbol] if case.symbol else [],
            candidate_rules=rules,
            baseline_rulebox={
                "status": str(rulebox.get("status") or ""),
                "ruleCount": int(rulebox.get("ruleCount") or len(rulebox.get("rules") or [])),
                "rulesHash": str(rulebox.get("rulesHash") or rulebox.get("ruleboxRulesHash") or ""),
            },
            target_world=self.world_context(case),
            status="active",
            created_at=stamp,
            updated_at=stamp,
            active_since=stamp,
            validation_warnings=warnings,
            source_proposal_id=case.source_proposal_ids[-1] if case.source_proposal_ids else "",
            source_case_id=case.case_id,
            validation_contract={
                "contract": "automatic-hypothesis-validation-v1",
                "gates": [item.get("id") for item in case.validation_gates],
                "operationalDeploymentRequiresApproval": True,
            },
        )
        if self.experiment_store:
            self.experiment_store.save(experiment)
        return experiment

    def validate(self, case: HypothesisDevelopmentCase, experiment: OntologyExperiment) -> Dict[str, object]:
        world = self.world_context(case)
        try:
            preview = self.ontology_repository.validate_rulebox_materialization({
                "rules": experiment.candidate_rules,
                "symbols": experiment.symbols,
                "worldId": world.get("worldId") or "",
            }) if self.ontology_repository and hasattr(self.ontology_repository, "validate_rulebox_materialization") else {
                "status": "unavailable",
                "reason": "TypeDB 후보 검증 기능이 구성되지 않았습니다.",
            }
        except Exception as error:  # noqa: BLE001 - keep the candidate pending for the next scheduled retry.
            preview = {"status": "error", "reason": "TypeDB 후보 검증 실패: " + str(error)[:500]}
        preview_status = str(preview.get("status") or "")
        matched_count = int(preview.get("matchedCount") or 0)
        type_status = "passed" if preview_status == "ok" else ("needs-data" if preview_status in {"missing-abox", "incomplete-abox", "provisioning", "unavailable", "error", "typedb-error"} else "blocked")
        replay_status = "passed" if preview_status == "ok" and matched_count > 0 else ("needs-data" if type_status == "needs-data" or (preview_status == "ok" and matched_count == 0) else "blocked")
        history = self.history_for(case)
        minimum_history = max(1, int(self.settings.get("hypothesisDevelopmentMinimumHistoricalSnapshots") or 3))
        history_status = "passed" if len(history) >= minimum_history else "needs-data"
        holdout = [item for item in history if self.timestamp_after(self.snapshot_generated_at(item), case.created_at)]
        minimum_holdout = max(1, int(self.settings.get("hypothesisDevelopmentMinimumHoldoutSnapshots") or 1))
        holdout_status = "passed" if len(holdout) >= minimum_holdout else "needs-data"
        derivations = [dict(item) for item in case.candidate_rule.get("derivations") or [] if isinstance(item, dict)]
        policy_safe = bool(derivations) and all(
            str(item.get("candidate_action") or "").upper() == "HOLD"
            and str(item.get("decision_effect") or "").lower() in {"defer", "constrain"}
            for item in derivations
        )
        policy_status = "passed" if policy_safe else "blocked"
        case.update_gates([
            validation_gate("typedb-preview", "TypeDB 후보 실행", type_status, str(preview.get("reason") or preview_status), True, self.compact_preview(preview)),
            validation_gate("current-replay", "현재 ABox 재생", replay_status, "후보 규칙 일치 " + str(matched_count) + "건", True, {"matchedCount": matched_count}),
            validation_gate("historical-coverage", "과거 자료 범위", history_status, str(len(history)) + "개 독립 스냅샷 중 최소 " + str(minimum_history) + "개 필요", True, {"snapshotCount": len(history), "minimumSnapshotCount": minimum_history}),
            validation_gate("holdout-observation", "제안 후 관측", holdout_status, "가설 제안 뒤 생성된 " + str(len(holdout)) + "개 스냅샷 중 최소 " + str(minimum_holdout) + "개 필요", True, {"snapshotCount": len(holdout), "minimumSnapshotCount": minimum_holdout, "after": case.created_at}),
            validation_gate("policy-safety", "정책 안전", policy_status, "후보 행동은 HOLD, 판단 효과는 defer/constrain으로 제한되며 운영 배포에는 승인이 필요합니다.", True, {"operationalDeploymentRequiresApproval": True, "candidateActions": [item.get("candidate_action") for item in derivations], "decisionEffects": [item.get("decision_effect") for item in derivations]}),
        ])
        summary = dict(case.validation_summary_payload)
        if summary.get("status") == "validated":
            case.transition("approval-required", "approval")
            event_name = HYPOTHESIS_DEVELOPMENT_VALIDATED
            event_type = "validation-confirmed"
        elif summary.get("blockedCount"):
            case.transition("blocked", "validation", ", ".join(summary.get("blockedGateIds") or []))
            event_name = HYPOTHESIS_DEVELOPMENT_TRANSITIONED
            event_type = "validation-blocked"
        else:
            case.transition("needs-data", "validation", ", ".join(summary.get("pendingGateIds") or []))
            event_name = HYPOTHESIS_DEVELOPMENT_TRANSITIONED
            event_type = "validation-needs-data"
        self.complete_experiment(experiment, case, preview, history, holdout)
        self.persist(case, event_type, case.blocked_reason, event_name=event_name)
        return {
            "status": case.status,
            "case": case.to_dict(),
            "experiment": experiment.to_dict(),
            "preview": self.compact_preview(preview),
        }

    def complete_experiment(
        self,
        experiment: OntologyExperiment,
        case: HypothesisDevelopmentCase,
        preview: Dict[str, object],
        history: List[Dict[str, object]],
        holdout: List[Dict[str, object]],
    ) -> None:
        stamp = utc_now_iso()
        matched_count = int(preview.get("matchedCount") or 0)
        candidate_metrics = rulebox_metrics(experiment.candidate_rules)
        summary = dict(case.validation_summary_payload)
        promotion_status = "promote-candidate" if summary.get("status") == "validated" else ("needs-data" if summary.get("pendingCount") else "needs-review")
        result = {
            "status": "completed",
            "experimentId": experiment.experiment_id,
            "hypothesis": experiment.hypothesis,
            "symbols": experiment.symbols,
            "sandbox": {
                "mutatedOperationalRuleBox": False,
                "mutatedTypeDB": False,
                "graphRunCount": 1,
                "validationOnly": True,
            },
            "typeDbPreview": self.compact_preview(preview),
            "historicalCoverage": {"snapshotCount": len(history), "holdoutSnapshotCount": len(holdout)},
            "validationGates": list(case.validation_gates),
            "validationSummary": summary,
            "decisionImpact": dict(case.decision_impact),
            "inference": {
                "aggregateDelta": {
                    "derivedRelationCount": matched_count,
                    "newRuleIds": list(candidate_metrics.get("ruleIds") or []),
                    "newRelationTypes": list(candidate_metrics.get("relationTypes") or []),
                    "newDecisionStages": list(candidate_metrics.get("decisionStages") or []),
                }
            },
            "proposedOntologyChanges": {
                "ruleIds": list(candidate_metrics.get("ruleIds") or []),
                "relationTypes": list(candidate_metrics.get("relationTypes") or []),
                "newRelationTypes": list(candidate_metrics.get("relationTypes") or []),
                "decisionStages": list(candidate_metrics.get("decisionStages") or []),
                "newDecisionStages": list(candidate_metrics.get("decisionStages") or []),
                "tboxClasses": list(candidate_metrics.get("tboxClasses") or []),
            },
            "promotionReadiness": {
                "status": promotion_status,
                "validationState": "ready" if summary.get("status") == "validated" else ("blocked" if summary.get("blockedCount") else "conditional"),
                "dataState": "sufficient" if summary.get("status") == "validated" else "insufficient",
                "reason": "모든 자동 검증 게이트를 통과했습니다. 운영 반영에는 사용자 승인이 필요합니다." if summary.get("status") == "validated" else "자동 검증 게이트가 아직 완료되지 않았습니다.",
            },
            "recommendations": [{
                "id": "hypothesis-development-approval:" + case.case_id,
                "type": "promote-rule",
                "priority": "high",
                "title": "검증된 가설의 운영 반영 승인",
                "reason": "자동 검증은 완료됐지만 운영 RuleBox는 변경하지 않았습니다.",
                "action": "사용자가 검증 탭에서 운영 반영을 승인합니다.",
                "proposal": {"ruleIds": list(candidate_metrics.get("ruleIds") or [])},
                "evidence": {"caseId": case.case_id, "validationSummary": summary},
            }] if summary.get("status") == "validated" else [],
            "findings": [str(item.get("label") or item.get("id")) + ": " + str(item.get("status")) for item in case.validation_gates],
            "completedAt": stamp,
        }
        run_seed = experiment.experiment_id + "|" + stamp
        run = {
            "runId": "ontology-lab-run-" + hashlib.sha256(run_seed.encode("utf-8")).hexdigest()[:12],
            "runKind": "hypothesis-development",
            "status": "completed",
            "completedAt": stamp,
            "promotionStatus": promotion_status,
            "validationState": result["promotionReadiness"]["validationState"],
            "dataState": result["promotionReadiness"]["dataState"],
            "derivedRelationDelta": matched_count,
            "validationSummary": summary,
        }
        experiment.status = "completed" if summary.get("status") == "validated" else "paused"
        experiment.last_result = result
        experiment.run_history = [run] + [dict(item) for item in experiment.run_history or [] if isinstance(item, dict) and item.get("runId") != run["runId"]]
        experiment.updated_at = stamp
        if self.experiment_store:
            self.experiment_store.save(experiment)

    def mark_deployed(self, case_id: str, application: Dict[str, object]) -> Dict[str, object]:
        case = self.case_store.get(case_id) if self.case_store else None
        if not case:
            return {"status": "not-found", "caseId": case_id}
        status = str((application or {}).get("status") or "")
        if status not in {"applied", "already-applied"}:
            case.transition("rolled-back" if status in {"rollback", "rolled-back"} else "blocked", "deployment", status or "deployment-failed")
        else:
            case.deployment = dict(application or {})
            case.transition("deployed", "observation")
        self.persist(
            case,
            "deployed" if case.status == "deployed" else "deployment-failed",
            case.blocked_reason,
            event_name=HYPOTHESIS_DEVELOPMENT_DEPLOYED if case.status == "deployed" else HYPOTHESIS_DEVELOPMENT_TRANSITIONED,
        )
        return {"status": case.status, "case": case.to_dict()}

    def list(self, status: str = "", symbol: str = "", limit: int = 100) -> Dict[str, object]:
        rows = self.case_store.list(status=status, symbol=symbol, limit=limit) if self.case_store else []
        statuses: Dict[str, int] = {}
        for item in rows:
            statuses[item.status] = statuses.get(item.status, 0) + 1
        return {
            "status": "ok",
            "count": len(rows),
            "summary": {"statuses": statuses, "approvalRequiredCount": statuses.get("approval-required", 0)},
            "cases": [item.to_dict() for item in rows],
            "events": self.case_store.events(limit=100) if self.case_store and hasattr(self.case_store, "events") else [],
            "governance": "automatic-validation-human-deployment-approval",
        }

    def report(self, case_id: str) -> Dict[str, object]:
        case = self.case_store.get(case_id) if self.case_store else None
        if not case:
            return {"status": "not-found", "caseId": case_id}
        experiment = self.experiment_store.get(case.experiment_id) if self.experiment_store and case.experiment_id else None
        return {
            "status": "ok",
            "case": case.to_dict(),
            "experiment": experiment.to_dict() if experiment else {},
            "events": self.case_store.events(case.case_id, 200) if hasattr(self.case_store, "events") else [],
        }

    def history_for(self, case: HypothesisDevelopmentCase) -> List[Dict[str, object]]:
        if not self.monitor_store or not case.account_id or not hasattr(self.monitor_store, "load_history"):
            return []
        try:
            rows = self.monitor_store.load_history(case.account_id, limit=12)
        except Exception:
            return []
        return [item for item in rows if self.snapshot_has_symbol(item, case.symbol)]

    @staticmethod
    def snapshot_has_symbol(snapshot: Dict[str, object], symbol: str) -> bool:
        target = str(symbol or "").upper()
        for key in ["positions", "watchlist"]:
            for item in (snapshot or {}).get(key) or []:
                if isinstance(item, dict) and str(item.get("symbol") or "").upper() == target:
                    return True
        return False

    @staticmethod
    def snapshot_generated_at(snapshot: Dict[str, object]) -> str:
        return str((snapshot or {}).get("generatedAt") or (snapshot or {}).get("generated_at") or "")

    @staticmethod
    def timestamp_after(candidate: object, baseline: object) -> bool:
        def parsed(value: object):
            text = str(value or "").strip()
            if not text:
                return None
            try:
                value = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(timezone.utc)
            except ValueError:
                return None

        candidate_time = parsed(candidate)
        baseline_time = parsed(baseline)
        return bool(candidate_time and baseline_time and candidate_time > baseline_time)

    def world_context(self, case: HypothesisDevelopmentCase) -> Dict[str, str]:
        tenant = str(self.settings.get("ontologyTenantId") or self.settings.get("tenantId") or "")
        if not case.account_id:
            return {}
        return {
            "worldId": portfolio_world_id(case.account_id, tenant),
            "worldType": "PortfolioWorld",
            "accountId": case.account_id,
            "tenantId": tenant,
        }

    def persist(
        self,
        case: HypothesisDevelopmentCase,
        event_type: str,
        reason: str = "",
        event_name: str = HYPOTHESIS_DEVELOPMENT_TRANSITIONED,
    ) -> None:
        if self.case_store:
            self.case_store.save(case, event_type, reason)
        self.publish(hypothesis_development_event(case.to_dict(), event_name))

    def publish(self, event) -> None:
        if not self.event_publisher:
            return
        if hasattr(self.event_publisher, "publish"):
            self.event_publisher.publish(event)
        else:
            self.event_publisher.handle(event)

    @staticmethod
    def compact_preview(preview: Dict[str, object]) -> Dict[str, object]:
        return {
            key: preview.get(key)
            for key in [
                "status", "reason", "reasonCode", "validationOnly",
                "mutatedOperationalRuleBox", "wroteInferenceBox", "candidateRuleCount",
                "matchedCount", "nativeTypeDbReasoningUsed", "typedbNativeFunctionReasoningUsed",
                "targetSymbols", "worldId", "diff",
            ]
            if key in (preview or {})
        }

    @staticmethod
    def compact_candidate_result(result: Dict[str, object]) -> Dict[str, object]:
        return {
            "status": str((result or {}).get("status") or ""),
            "reason": str((result or {}).get("reason") or ""),
            "candidateCount": int((result or {}).get("candidateCount") or len((result or {}).get("candidates") or [])),
            "savedCount": int((result or {}).get("savedCount") or 0),
        }
