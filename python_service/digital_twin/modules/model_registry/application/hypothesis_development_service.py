import hashlib
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List

from digital_twin.modules.model_registry.application.hypothesis_candidate_compilation import capture_compilation, reusable_compilation
from digital_twin.modules.model_registry.domain.event_types import HYPOTHESIS_DEVELOPMENT_DEPLOYED, HYPOTHESIS_DEVELOPMENT_TRANSITIONED, HYPOTHESIS_DEVELOPMENT_VALIDATED
from digital_twin.modules.model_registry.domain.events import hypothesis_development_event
from digital_twin.modules.model_registry.domain.hypothesis_development import HypothesisDevelopmentCase, TERMINAL_HYPOTHESIS_DEVELOPMENT_STATUSES, default_validation_gates, hypothesis_decision_impact, screen_hypothesis_case, validation_gate
from digital_twin.modules.model_registry.domain.ontology_experiments import OntologyExperiment, normalize_candidate_rules, rulebox_metrics
from digital_twin.modules.model_registry.domain.ontology_rulebox_contracts import GraphInferenceRule
from digital_twin.modules.model_registry.domain.ontology_rulebox_governance import rulebox_semantic_violations
from digital_twin.modules.model_registry.domain.hypothesis_compilation import RULE_DESIGN_VERSION, blocker_state, compilation_blockers, compilation_fingerprint, validation_requirements
from digital_twin.modules.model_registry.domain.hypothesis_validation import additional_validation_gate, preview_states
from digital_twin.modules.reasoning.contracts import portfolio_world_id
from digital_twin.modules.portfolio.contracts import utc_now_iso
from .ontology_evolution_service import EVOLUTION_STATUSES


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
        evolution_service=None,
    ):
        self.case_store = case_store
        self.proposal_store = proposal_store
        self.experiment_store = experiment_store
        self.rule_candidate_service = rule_candidate_service
        self.ontology_repository = ontology_repository
        self.monitor_store = monitor_store
        self.event_publisher = event_publisher
        self.settings = dict(settings or {})
        self.evolution_service = evolution_service

    def ingest_proposal(self, proposal: Dict[str, object], inference_generation_id: str = "") -> Dict[str, object]:
        incoming = HypothesisDevelopmentCase.from_proposal(proposal, inference_generation_id)
        lock = getattr(self.case_store, "processing_lock", None)
        with (lock(incoming.case_id) if callable(lock) else nullcontext(True)) as acquired:
            if not acquired:
                return {"status": "deferred-busy", "caseId": incoming.case_id}
            existing = self.case_store.get_by_fingerprint(incoming.fingerprint) if self.case_store else None
            case = existing or incoming
            if existing:
                case.merge_proposal(proposal, inference_generation_id)
            self.persist(case, "proposal-merged" if existing else "proposal-ingested")
        if case.status in TERMINAL_HYPOTHESIS_DEVELOPMENT_STATUSES or case.status in {"approval-required", "deployed", "observing"} or (case.evolution.get("plan") and case.status == "strengthened"):
            return {"status": case.status, "case": case.to_dict(), "merged": bool(existing)}
        result = self.process(case.case_id, force=not bool(existing))
        result["merged"] = bool(existing)
        return result

    def process(self, case_id: str, force: bool = True) -> Dict[str, object]:
        lock = getattr(self.case_store, "processing_lock", None)
        with (lock(case_id) if callable(lock) else nullcontext(True)) as acquired:
            if not acquired:
                return {"status": "already-processing", "caseId": case_id}
            case = self.case_store.get(case_id) if self.case_store else None
            if not case:
                return {"status": "not-found", "caseId": case_id}
            if self.evolution_service and case.status in EVOLUTION_STATUSES:
                ready = self.parse_timestamp(case.retry.get("nextCheckAt"))
                if not force and ready and ready > datetime.now(timezone.utc):
                    return {"status": "deferred-unchanged", "caseId": case_id}
                return self.evolution_service.advance(case, self.persist)
            if case.status in TERMINAL_HYPOTHESIS_DEVELOPMENT_STATUSES | {"approval-required", "deployed", "observing"} or (case.evolution.get("plan") and case.status == "strengthened"):
                return {"status": case.status, "case": case.to_dict()}
            if not force and case.status in {"needs-revision", "blocked"}:
                if not self.evolution_service or int(case.retry.get("authoringAttempts") or 0) >= self.evolution_service.policy["maximumAuthoringAttempts"]:
                    return {"status": "development-required", "caseId": case_id}
            if not force and not self.validation_retry_due(case):
                attempted = self.parse_timestamp(case.retry.get("lastAttemptAt") or case.validation_attempted_at)
                now = datetime.now(timezone.utc)
                minimum = max(1, int(self.settings.get("hypothesisDevelopmentChangedRetryMinutes") or 15))
                ready = attempted + timedelta(minutes=minimum)
                if ready <= now:
                    ready = attempted + timedelta(minutes=self.retry_minutes())
                case.retry.update({"nextCheckAt": ready.isoformat(), "lastCheckedAt": now.isoformat()})
                self.persist(case, "retry-deferred")
                return {"status": "deferred-unchanged", "caseId": case_id}
            attempted = datetime.now(timezone.utc)
            case.retry = {
                **case.retry,
                "attemptCount": int(case.retry.get("attemptCount") or 0) + 1,
                "lastAttemptAt": attempted.isoformat(),
                "lastInputFingerprint": self.validation_input_fingerprint(case),
                "nextCheckAt": (attempted + timedelta(minutes=self.retry_minutes())).isoformat(),
                "state": "processing",
                "designVersion": RULE_DESIGN_VERSION,
                "lastCheckedAt": attempted.isoformat(),
                "owner": "hypothesis-development",
            }
            self.persist(case, "retry-started")
            try:
                result = self._process_case(case_id)
            except Exception as error:
                case = self.case_store.get(case_id) or case
                invalid = isinstance(error, (ValueError, TypeError))
                case.transition("needs-revision" if invalid else "needs-data", case.stage, str(error)[:500])
                case.retry["state"] = "development-required" if invalid else "dependency-error"
                case.retry["blockers"] = [{"kind": "schema-mismatch" if invalid else "dependency-error",
                                           "requirement": case.blocked_reason,
                                           "owner": "development" if invalid else "runtime",
                                           "dependencyKey": "rule-candidate-runtime"}]
                case.retry["requirements"] = [case.blocked_reason]
                if invalid:
                    case.retry["nextCheckAt"] = ""
                    self.schedule_authoring_retry(case)
                self.persist(case, "retry-failed", case.blocked_reason)
                return {"status": "error", "caseId": case_id, "reason": case.blocked_reason, "case": case.to_dict()}
            case = self.case_store.get(case_id) or case
            if case.status in EVOLUTION_STATUSES:
                result["case"] = case.to_dict()
                return result
            if case.status == "needs-data":
                case.retry["state"] = blocker_state(case.retry.get("blockers") or [
                    {"kind": "missing-observation"}
                ])[1]
            else:
                case.retry["state"] = "development-required" if case.status in {"needs-revision", "blocked"} else "completed"
            if case.status != "needs-data":
                case.retry["nextCheckAt"] = ""
            if case.status in {"needs-revision", "blocked"}:
                self.schedule_authoring_retry(case)
            self.persist(case, "retry-finished", case.blocked_reason)
            result["case"] = case.to_dict()
            return result

    def schedule_authoring_retry(self, case):
        if (self.evolution_service and int(case.retry.get("authoringAttempts") or 0)
                < self.evolution_service.policy["maximumAuthoringAttempts"]):
            case.retry["state"] = "authoring-retry"
            case.retry["nextCheckAt"] = (datetime.now(timezone.utc) + timedelta(
                minutes=self.evolution_service.policy["retryMinutes"])).isoformat()
            case.compilation_draft["rejectedReason"] = case.blocked_reason or "candidate-needs-revision"

    def _process_case(self, case_id: str) -> Dict[str, object]:
        case = self.case_store.get(case_id) if self.case_store else None
        if not case:
            return {"status": "not-found", "caseId": str(case_id or "")}
        case.transition("screening", "screening")
        case.validation_gates = default_validation_gates()
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
            case.retry["blockers"] = compilation_blockers([{"blockers": [
                {"kind": "missing-observation" if screen_status == "needs-data" else "schema-mismatch",
                 "requirement": case.blocked_reason, "dependencyKey": "hypothesis-screening"}
            ]}])
            case.retry["requirements"] = [item["requirement"] for item in case.retry["blockers"]]
            self.persist(case, "screening-stopped", case.blocked_reason)
            return {"status": case.status, "case": case.to_dict()}
        case.transition("screening", "compilation")
        self.persist(case, "compilation-started")
        candidate_result = self.compile_candidate(case)
        case.retry["compilationContext"] = dict(candidate_result.get("contextSummary") or {})
        candidates = [dict(item) for item in candidate_result.get("candidates") or [] if isinstance(item, dict)]
        candidate = next((item for item in candidates if isinstance(item.get("proposedRule"), dict) and not compilation_blockers([item])), None)
        if not candidate or compilation_blockers([candidate]):
            blockers = compilation_blockers(candidates)
            needs_data = sorted({item["requirement"] for item in blockers})
            case.retry["requirements"] = needs_data[:40]
            case.retry["blockers"] = blockers
            status, case.retry["state"] = blocker_state(blockers)
            reason = ", ".join(needs_data) or str(candidate_result.get("reason") or "AI가 실행 가능한 후보 규칙을 만들지 못했습니다.")
            case.transition(status, "compilation", reason)
            self.persist(case, "compilation-stopped", reason)
            return {"status": case.status, "case": case.to_dict(), "candidateResult": self.compact_candidate_result(candidate_result)}
        case.retry["blockers"] = []
        case.retry["requirements"] = []
        case.validation_requirements = validation_requirements(candidate)
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
            case.compilation_draft["rejectedReason"] = rule_conflict
            case.transition("needs-revision", "compilation", rule_conflict)
            self.persist(case, "candidate-duplicate-blocked", rule_conflict)
            return {"status": case.status, "case": case.to_dict(), "violations": [rule_conflict]}
        structural = self.validate_rule_structure(prepared_rule)
        if structural:
            case.compilation_draft["rejectedReason"] = " | ".join(structural[:8])
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
        self.persist(case, "validation-resumed" if candidate_result.get("reused") else "validation-started")
        return self.validate(case, experiment)

    def process_pending(self, limit: int = 5) -> Dict[str, object]:
        cleanup = getattr(getattr(self.evolution_service, "runtime", None), "cleanup_observations", None)
        if callable(cleanup):
            cleanup()
        statuses = {"proposed", "screening", "compiled", "validating", "needs-data"} | EVOLUTION_STATUSES
        if self.evolution_service:
            statuses |= {"needs-revision", "blocked"}
        reader = getattr(self.case_store, "pending", None)
        scanned = reader(limit=50) if callable(reader) else (self.case_store.list(limit=500) if self.case_store else [])
        candidates = [
            item
            for item in scanned
            if item.status in statuses
        ]
        candidates.sort(key=lambda item: (
            self.parse_timestamp(item.retry.get("nextCheckAt") or item.created_at) or datetime.min.replace(tzinfo=timezone.utc),
            item.case_id,
        ))
        deferred = []
        results = []
        for item in candidates:
            try:
                result = self.process(item.case_id, force=False)
                if result.get("status") in {"deferred-unchanged", "already-processing", "development-required"}:
                    deferred.append(item.case_id)
                    continue
                results.append(result)
            except Exception as error:  # noqa: BLE001 - one unavailable dependency must not stop the batch.
                results.append({"status": "error", "caseId": item.case_id, "reason": str(error)[:500]})
            if len(results) >= max(1, int(limit or 5)):
                break
        return {
            "status": "processed" if results else "idle",
            "processedCount": len(results),
            "deferredUnchangedCount": len(deferred),
            "deferredCaseIds": deferred[:20],
            "results": results,
            "scannedCount": len(scanned),
        }

    def ready_wait_minutes(self) -> float:
        reader = getattr(self.case_store, "oldest_ready_at", None)
        ready = self.parse_timestamp(reader()) if callable(reader) else None
        return max(0.0, (datetime.now(timezone.utc) - ready).total_seconds() / 60) if ready else 0.0

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
            if existing and (not proposal.get("proposalId") or str(proposal["proposalId"]) in existing.source_proposal_ids):
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
        if case.compilation_draft and self.ontology_repository:
            rulebox = self.ontology_repository.rulebox_snapshot()
            reused = reusable_compilation(case, rulebox, self.world_context(case))
            if reused:
                self.persist(case, "compilation-reused")
                return reused
        if not self.rule_candidate_service or not hasattr(self.rule_candidate_service, "propose_hypothesis"):
            return {"status": "disabled", "reason": "가설 규칙 후보 서비스가 구성되지 않았습니다.", "candidates": []}
        case.retry.pop("compilationContext", None)
        attempts = int(case.retry.get("authoringAttempts") or 0)
        if self.evolution_service and attempts >= self.evolution_service.policy["maximumAuthoringAttempts"]:
            raise ValueError("candidate-authoring-budget-exhausted")
        case.retry["authoringAttempts"] = attempts + 1
        case.candidate_rule = {}
        case.candidate_id = ""
        case.experiment_id = ""
        case.validation_requirements = []
        def record_input(summary):
            case.retry["compilationContext"] = dict(summary)
            self.persist(case, "compilation-input-captured")

        result = self.rule_candidate_service.propose_hypothesis(
            case.to_dict(),
            account_id=case.account_id,
            tenant_id=str(self.settings.get("ontologyTenantId") or self.settings.get("tenantId") or ""),
            context_observer=record_input,
        )
        capture_compilation(case, result, self.world_context(case))
        self.persist(case, "compilation-candidate-captured")
        return result

    def governed_candidate_rule(self, case: HypothesisDevelopmentCase, rule: Dict[str, object]) -> Dict[str, object]:
        prepared = dict(rule or {})
        prepared["enabled"] = False
        if self.evolution_service:
            if prepared.get("source_kind", prepared.get("sourceKind")) != "stock":
                raise ValueError("Automatic evolution currently requires a stock-scoped source")
            prepared["model_input_contract"] = {
                **dict(prepared.get("model_input_contract") or prepared.get("modelInputContract") or {}),
                "evolutionScope": {"worldId": portfolio_world_id(case.account_id), "symbol": case.symbol},
            }
        prepared["hypothesis_family_key"] = str(prepared.get("hypothesis_family_key") or prepared.get("hypothesisFamilyKey") or "ai-hypothesis." + case.fingerprint[:16])
        conditions = []
        for index, raw in enumerate(prepared.get("conditions") or []):
            item = dict(raw) if isinstance(raw, dict) else {}
            condition_id = str(item.get("condition_id") or item.get("conditionId") or "condition-" + str(index + 1))
            item["condition_id"] = condition_id
            item["hypothesis_scope"] = str(item.get("hypothesis_scope") or item.get("hypothesisScope") or ("account" if case.account_id else "market"))
            item["evidence_group_key"] = str(item.get("evidence_group_key") or item.get("evidenceGroupKey") or condition_id)
            conditions.append(item)
        if self.evolution_service:
            # Keep the entire scoped experiment in its private overlay, not a shared premise.
            for condition in conditions:
                condition["hypothesis_scope"] = "account"
        prepared["conditions"] = conditions
        formation = [str(item.get("condition_id") or "") for item in conditions if str(item.get("role") or "required").lower() not in {"optional", "negative", "exclude", "not"}]
        lifecycle = dict(prepared.get("hypothesis_lifecycle") or prepared.get("hypothesisLifecycle") or {})
        prepared["hypothesis_lifecycle"] = {
            "formationConditionIds": formation,
            "invalidationConditionIds": case.invalidation_conditions,
            "validityMinutes": int(self.settings.get("hypothesisDevelopmentDefaultValidityMinutes") or 1440),
            "requiredFreshnessDomains": list(case.required_evidence_types or ["research"]),
            "nextDataRequirements": list(case.required_evidence_types or []),
            "invalidationMode": "typedb-rule-not-materialized-or-condition-invalidated",
            **lifecycle,
        }
        predictive = GraphInferenceRule.from_dict(prepared).resolved_claim_contract.is_predictive
        derivations = []
        for raw in prepared.get("derivations") or []:
            item = dict(raw) if isinstance(raw, dict) else {}
            item["evidence_role"] = str(item.get("evidence_role") or item.get("evidenceRole") or item.get("polarity") or "context")
            if not predictive:
                item["candidate_action"] = ""
                item["candidate_action_label"] = ""
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
        experiment_id = "ontology-exp-hypothesis-" + compilation_fingerprint({
            "caseId": case.case_id, "candidateRule": candidate.get("proposedRule"),
            "compilationFingerprint": case.compilation_draft.get("contentFingerprint"),
            "world": self.world_context(case),
        })[:24]
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
                "operationalDeploymentRequiresApproval": not bool(self.evolution_service),
                "compilationFingerprint": case.compilation_draft.get("contentFingerprint"),
                "validationRequirements": case.validation_requirements,
            },
        )
        if self.experiment_store:
            self.experiment_store.save(experiment)
        return experiment

    def validate(self, case: HypothesisDevelopmentCase, experiment: OntologyExperiment) -> Dict[str, object]:
        world = self.world_context(case)
        history = self.history_for(case)
        case.validation_input_fingerprint = self.validation_input_fingerprint(
            case,
            history=history,
        )
        case.validation_attempted_at = utc_now_iso()
        try:
            preview = self.ontology_repository.validate_rulebox_materialization({
                "rules": experiment.candidate_rules,
                "symbols": experiment.symbols,
                "worldId": world.get("worldId") or "",
                "includeBaseline": False,
            }) if self.ontology_repository and hasattr(self.ontology_repository, "validate_rulebox_materialization") else {
                "status": "unavailable",
                "reason": "TypeDB 후보 검증 기능이 구성되지 않았습니다.",
            }
        except Exception as error:  # noqa: BLE001 - keep the candidate pending for the next scheduled retry.
            preview = {"status": "error", "reason": "TypeDB 후보 검증 실패: " + str(error)[:500]}
        preview_status = str(preview.get("status") or "")
        matched_count = int(preview.get("matchedCount") or 0)
        type_status, replay_status = preview_states(
            preview, [item.get("rule_id") for item in experiment.candidate_rules],
            experiment.symbols, world.get("worldId") or "",
        )
        if preview_status == "ok" and type_status == "blocked":
            preview["reason"] = "현재 계정·종목·후보 규칙의 읽기 전용 TypeDB 실행 증명이 불완전합니다."
        minimum_history = max(1, int(self.settings.get("hypothesisDevelopmentMinimumHistoricalSnapshots") or 3))
        history_status = "passed" if len(history) >= minimum_history else "needs-data"
        authored_at = str(case.compilation_draft.get("capturedAt") or case.updated_at)
        holdout = [item for item in history if self.timestamp_after(self.snapshot_generated_at(item), authored_at)]
        minimum_holdout = max(1, int(self.settings.get("hypothesisDevelopmentMinimumHoldoutSnapshots") or 1))
        holdout_status = "passed" if len(holdout) >= minimum_holdout else "needs-data"
        derivations = [dict(item) for item in case.candidate_rule.get("derivations") or [] if isinstance(item, dict)]
        predictive = GraphInferenceRule.from_dict(case.candidate_rule).resolved_claim_contract.is_predictive
        policy_safe = bool(derivations) and case.candidate_rule.get("enabled") is False and not self.validate_rule_structure(case.candidate_rule)
        policy_status = "passed" if policy_safe else "blocked"
        case.update_gates([
            validation_gate("typedb-preview", "TypeDB 후보 실행", type_status, str(preview.get("reason") or preview_status), True, self.compact_preview(preview)),
            validation_gate("current-replay", "현재 ABox 재생", replay_status, ("조회 성공, 현재 후보 조건은 성립하지 않습니다." if type_status == "passed" and matched_count == 0 else "후보 규칙 일치 " + str(matched_count) + "건"), True, {"matchedCount": matched_count, "conditionState": "not-met" if type_status == "passed" and matched_count == 0 else "matched" if replay_status == "passed" else "unknown"}),
            validation_gate("historical-coverage", "과거 자료 범위", history_status, str(len(history)) + "개 스냅샷 중 최소 " + str(minimum_history) + "개 필요", True, {"snapshotCount": len(history), "minimumSnapshotCount": minimum_history}),
            validation_gate("holdout-observation", "후보 작성 후 관측", holdout_status, "현재 후보 작성 뒤 생성된 " + str(len(holdout)) + "개 스냅샷 중 최소 " + str(minimum_holdout) + "개 필요. 독립 사건 결과 검증과는 별개입니다.", True, {"snapshotCount": len(holdout), "minimumSnapshotCount": minimum_holdout, "after": authored_at}),
            validation_gate("policy-safety", "실험 격리", policy_status, "후보의 원래 행동과 반증 조건을 유지하되 운영 규칙은 비활성으로 보관합니다. 별도 실험 버전은 알림 권한이 없습니다.", True, {"candidateEnabled": False, "candidateActions": [item.get("candidate_action") for item in derivations], "decisionEffects": [item.get("decision_effect") for item in derivations]}),
            validation_gate("causal-hypothesis", "원래 인과 가설의 검증 계약", "passed" if predictive else "blocked", "예측 가설 계약을 확인했습니다. 사후 결과 검증은 별도로 필요합니다." if predictive else "생성된 후보는 추가 확인용 관계이며 원래 인과 가설을 검증하는 예측 모델·결과 계약이 아닙니다. 참고 조회 성공을 가설 검증 성공으로 바꾸지 않습니다.", True, {"predictiveClaim": predictive, "claimType": GraphInferenceRule.from_dict(case.candidate_rule).resolved_claim_contract.claim_type}),
            additional_validation_gate(case.validation_requirements, type_status, replay_status),
        ])
        summary = dict(case.validation_summary_payload)
        if (self.evolution_service and self.evolution_service.policy["mode"] != "disabled"
                and type_status == "passed" and predictive and policy_safe
                and not summary.get("blockedCount")):
            self.complete_experiment(experiment, case, preview, history, holdout)
            self.persist(case, "technical-validation-completed")
            return self.evolution_service.start(case, self.persist)
        if summary.get("status") == "validated":
            case.transition("approval-required", "approval")
            event_name = HYPOTHESIS_DEVELOPMENT_VALIDATED
            event_type = "validation-confirmed"
        elif summary.get("blockedCount"):
            case.transition("blocked", "validation", ", ".join(summary.get("blockedGateIds") or []))
            case.retry["blockers"] = compilation_blockers([{"blockers": [
                {"kind": "unsupported-capability" if item.get("id") == "causal-hypothesis" else "schema-mismatch",
                 "requirement": str(item.get("detail") or item.get("label") or ""),
                 "dependencyKey": str(item.get("id") or "")}
                for item in case.validation_gates if item.get("id") in summary.get("blockedGateIds", [])
            ]}])
            case.retry["requirements"] = [item["requirement"] for item in case.retry["blockers"]]
            event_name = HYPOTHESIS_DEVELOPMENT_TRANSITIONED
            event_type = "validation-blocked"
        else:
            case.transition("needs-data", "validation", ", ".join(summary.get("pendingGateIds") or []))
            event_name = HYPOTHESIS_DEVELOPMENT_TRANSITIONED
            event_type = "validation-needs-data"
        if case.status == "needs-data":
            blockers = []
            for item in case.validation_gates:
                if item.get("id") not in (summary.get("pendingGateIds") or []):
                    continue
                kind = "observation-window"
                if item.get("id") in {"typedb-preview", "current-replay"}:
                    kind = "condition-not-met" if type_status == "passed" else ("dependency-error" if preview_status in {"error", "typedb-error", "unavailable", "provisioning"} else "missing-observation")
                elif item.get("id") == "additional-validation":
                    kind = "validation-review" if any(row.get("check") == "review" for row in case.validation_requirements) else ("condition-not-met" if type_status == "passed" else "dependency-error")
                blockers.append({"kind": kind,
                                 "requirement": str(item.get("detail") or item.get("label") or ""),
                                 "dependencyKey": str(item.get("id") or "")})
            case.retry["blockers"] = compilation_blockers([{"blockers": blockers}])
            case.retry["requirements"] = [item["requirement"] for item in case.retry["blockers"]]
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
            "governance": "policy-governed-shadow-evolution" if self.evolution_service else "automatic-validation-human-deployment-approval",
            "evolutionPolicy": dict(self.evolution_service.policy) if self.evolution_service else {},
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
        history = [item for item in rows if isinstance(item, dict)
                   and str(item.get("accountId") or item.get("account_id") or case.account_id) == case.account_id
                   and self.snapshot_has_symbol(item, case.symbol)]
        return sorted(history, key=lambda item: self.parse_timestamp(self.snapshot_generated_at(item))
                      or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    def retry_minutes(self) -> int:
        return max(15, min(24 * 60, int(self.settings.get("hypothesisDevelopmentUnchangedRetryMinutes") or 180)))

    def validation_retry_due(self, case: HypothesisDevelopmentCase) -> bool:
        """Retry a needs-data experiment only after new input or a slow health retry."""

        attempted = self.parse_timestamp(case.retry.get("lastAttemptAt") or case.validation_attempted_at)
        if attempted is None or case.retry.get("designVersion") != RULE_DESIGN_VERSION:
            return True
        elapsed = (datetime.now(timezone.utc) - attempted).total_seconds()
        # Price ticks must not turn a missing-data hypothesis into an AI loop.
        minimum_minutes = max(1, int(self.settings.get("hypothesisDevelopmentChangedRetryMinutes") or 15))
        if elapsed < minimum_minutes * 60:
            return False
        current = self.validation_input_fingerprint(case)
        previous = str(case.retry.get("lastInputFingerprint") or case.validation_input_fingerprint)
        return not previous or current != previous or elapsed >= self.retry_minutes() * 60

    def validation_input_fingerprint(
        self,
        case: HypothesisDevelopmentCase,
        history: List[Dict[str, object]] = None,
    ) -> str:
        rows = list(history if history is not None else self.history_for(case))
        latest_state: Dict[str, object] = {}
        if rows:
            latest = max((row for row in rows if isinstance(row, dict)),
                         key=lambda row: self.parse_timestamp(self.snapshot_generated_at(row))
                         or datetime.min.replace(tzinfo=timezone.utc), default={})
            target = str(case.symbol or "").upper()
            aliases = {
                "symbol": "symbol", "market": "market", "currentPrice": "current_price",
                "changeRate": "change_rate", "volume": "volume", "volumeRatio": "volume_ratio",
                "tradeStrength": "trade_strength", "bidAskImbalance": "bid_ask_imbalance",
                "foreignNetVolume": "foreign_net_volume", "institutionNetVolume": "institution_net_volume",
                "individualNetVolume": "individual_net_volume", "ma5": "ma5", "ma20": "ma20", "ma60": "ma60",
                "ma20Slope": "ma20_slope", "ma60Slope": "ma60_slope", "profitLossRate": "profit_loss_rate",
                "quantity": "quantity", "averagePrice": "average_price",
            }
            for item in self.snapshot_symbol_items(latest):
                if str(item.get("symbol") or "").upper() == target:
                    latest_state = {name: item[name] if name in item else item[alias]
                                    for name, alias in aliases.items() if name in item or alias in item}
                    break
        payload = {
            "caseFingerprint": case.fingerprint,
            "supportingEvidenceIds": sorted(case.supporting_evidence_ids),
            "counterEvidenceIds": sorted(case.counter_evidence_ids),
            "requiredEvidenceTypes": sorted(case.required_evidence_types),
            "candidateRule": case.candidate_rule,
            "historyCoverageCount": len(rows),
            "latestSymbolState": latest_state if case.candidate_rule else {},
            "designVersion": RULE_DESIGN_VERSION,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def snapshot_symbol_items(snapshot: Dict[str, object]) -> List[Dict[str, object]]:
        # Persisted temporal projections are symbol-keyed maps; older API
        # snapshots can be lists. Read both without altering stored facts.
        result = []
        for key in ["positions", "watchlist"]:
            values = (snapshot or {}).get(key) or []
            entries = values.items() if isinstance(values, dict) else (("", item) for item in values)
            for symbol, item in entries:
                if not isinstance(item, dict):
                    continue
                item_symbol = str(item.get("symbol") or symbol or "").upper()
                if symbol and str(symbol).upper() != item_symbol:
                    continue
                if item_symbol:
                    result.append({**item, "symbol": item_symbol})
        return result

    @classmethod
    def snapshot_has_symbol(cls, snapshot: Dict[str, object], symbol: str) -> bool:
        target = str(symbol or "").upper()
        return any(item["symbol"] == target for item in cls.snapshot_symbol_items(snapshot))

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

    @staticmethod
    def parse_timestamp(value: object):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

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
                "typedbDirectTypeqlUsed", "nativeMatchResult", "candidateRuleIds",
                "targetSymbols", "worldId", "baselineRequested", "diff",
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
