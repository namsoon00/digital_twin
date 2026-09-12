"""Compose immutable TypeDB candidates, outcome ledgers and atomic release switches."""

from digital_twin.modules.model_registry.contracts import (
    GraphInferenceRule, evolution_fingerprint, validate_evolution_plan,
)
from digital_twin.modules.outcomes.contracts import claim_validation_fingerprint
from digital_twin.modules.reasoning.public import append_rule_to_release_artifact
from digital_twin.modules.reasoning.contracts import portfolio_world_id
from digital_twin.modules.portfolio.contracts import utc_now_iso


def claim_binding(rule):
    claim = GraphInferenceRule.from_dict(rule).resolved_claim_contract.to_dict()
    return {"claimContractId": claim["claimContractId"],
            "validationFingerprint": claim_validation_fingerprint(claim)}


def comparison_measurement(rule):
    claim = GraphInferenceRule.from_dict(rule).resolved_claim_contract
    contract = claim.to_dict().get("outcomeContract") or {}
    criteria = contract.get("criteria") or []
    measurements = sorted((str(row.get("metric") or ""), str(row.get("unit") or ""),
                           str(row.get("benchmarkSymbol") or ""), abs(float(row.get("threshold") or 0)),
                           str(row.get("role") or ""), bool(row.get("required")),
                           int(row.get("horizonMinutes") or 0), str(row.get("failureOutcome") or ""),
                           tuple(sorted(row.get("sourcePolicy") or [])),
                           tuple(sorted(row.get("requiredObservationDomains") or [])),
                           "strict" if row.get("operator") in {"<", ">"} else
                           "inclusive" if row.get("operator") in {"<=", ">="} else str(row.get("operator")))
                          for row in criteria)
    return (claim.prediction_target, claim.outcome_metric,
            tuple(contract.get("outcomeHorizonMinutes") or []), tuple(measurements))


class OntologyEvolutionRuntime:
    def __init__(self, platform, outcome_store, lock_store):
        self.platform, self.registry = platform, platform.registry
        self.outcomes, self.locks = outcome_store, lock_store

    def baseline(self, candidate, policy):
        control = self.registry.control()
        saved = self.registry.release_artifact(control.active_deployment_id)
        if not saved.get("valid"):
            raise RuntimeError("immutable-baseline-artifact-unavailable")
        if control.active_deployment_id != control.delivery_deployment_id:
            raise RuntimeError("active-delivery-release-mismatch")
        rules = saved["artifact"]["rules"]
        measurement = comparison_measurement(candidate)
        comparable = [row for row in rules
                      if GraphInferenceRule.from_dict(row).resolved_claim_contract.is_predictive
                      and comparison_measurement(row) == measurement
                      and (not policy["baselineRuleIds"] or row["rule_id"] in policy["baselineRuleIds"])]
        if not comparable:
            raise ValueError("comparable-baseline-outcome-contract-required")
        authored_baseline = (candidate.get("model_input_contract") or {}).get("comparisonBaselineRuleId")
        if authored_baseline and not policy["baselineRuleIds"]:
            comparable = [row for row in comparable if row["rule_id"] == authored_baseline]
        else:
            # Prefer the same thesis before observing any candidate result.
            thesis = GraphInferenceRule.from_dict(candidate).resolved_claim_contract.thesis_family
            same_thesis = [row for row in comparable if GraphInferenceRule.from_dict(row).resolved_claim_contract.thesis_family == thesis]
            comparable = same_thesis or comparable
        if len(comparable) != 1:
            raise ValueError("ambiguous-baseline-author-comparisonBaselineRuleId-or-configure-policy")
        horizon = min(measurement[2])
        minimum_window = max(horizon, policy["independenceMinutes"]) * policy["minimumIndependentPairs"]
        if minimum_window > policy["maximumShadowDays"] * 1440:
            raise ValueError("comparison-horizon-cannot-fit-configured-observation-window")
        return {"deploymentId": control.active_deployment_id,
                "artifactFingerprint": saved["artifactFingerprint"],
                "comparisonRuleId": comparable[0]["rule_id"],
                "comparisonHorizonMinutes": horizon,
                "comparisonClaim": claim_binding(comparable[0]),
                "candidateClaim": claim_binding(candidate)}

    def stage(self, plan):
        validate_evolution_plan(plan)
        scope = (plan["candidateRule"].get("model_input_contract") or {}).get("evolutionScope")
        if scope != {"worldId": portfolio_world_id(plan["accountId"]), "symbol": plan["symbol"]}:
            raise ValueError("Evolution candidate cannot escape its validated account and symbol")
        deployment_id = "evolution-" + plan["fingerprint"][:20]
        with self.locks.processing_lock("ontology-evolution-control") as acquired:
            if not acquired:
                return {"status": "waiting", "reason": "evolution-control-busy"}
            control = self.registry.control()
            saved = self.registry.release_artifact(plan["baseline"]["deploymentId"])
            if control.active_deployment_id != plan["baseline"]["deploymentId"] or saved.get("artifactFingerprint") != plan["baseline"]["artifactFingerprint"]:
                return {"status": "superseded", "reason": "evolution-baseline-changed"}
            existing = self.registry.get(deployment_id) or {}
            if existing and control.candidate_deployment_id == deployment_id:
                artifact = self.registry.release_artifact(deployment_id)
                if not artifact.get("valid") or artifact["artifact"].get("evolutionPlanFingerprint") != plan["fingerprint"]:
                    raise RuntimeError("candidate-release-plan-mismatch")
                return {"status": "staged", "deploymentId": deployment_id, "artifactFingerprint": artifact["artifactFingerprint"]}
            other = self.registry.get(control.candidate_deployment_id) if control.candidate_deployment_id else {}
            if other and other.get("status") not in {"retired", "blocked"}:
                return {"status": "waiting", "reason": "another-candidate-or-rollback-release-in-use"}
            if existing:
                artifact = self.registry.release_artifact(deployment_id)
                if artifact.get("valid") and artifact["artifact"].get("evolutionPlanFingerprint") == plan["fingerprint"]:
                    if existing.get("status") in {"retired", "blocked"}:
                        return {"status": "superseded", "reason": "candidate-release-terminated"}
                    self.registry.set_control(control.active_deployment_id, control.delivery_deployment_id,
                                              deployment_id, expected_version=control.version)
                    return {"status": "staged", "deploymentId": deployment_id, "artifactFingerprint": artifact["artifactFingerprint"]}
            artifact = append_rule_to_release_artifact(saved["artifact"], plan["candidateRule"])
            artifact["evolutionPlanFingerprint"] = plan["fingerprint"]
            result = self.platform.register_v2_release(
                deployment_id, "ontology-evolution-" + plan["fingerprint"],
                graph_database="orbit_evolution_" + plan["fingerprint"][:20],
                release_seed_artifact=artifact,
                expected_baseline_deployment_id=plan["baseline"]["deploymentId"],
            )
            if result.get("status") != "registered":
                return {"status": "waiting", "reason": "candidate-registration-blocked", "result": result}
            return {"status": "staged", "deploymentId": deployment_id,
                    "artifactFingerprint": result["releaseSeedArtifact"]["artifactFingerprint"]}

    def state(self, plan, deployment):
        control = self.registry.control()
        row = self.registry.get(deployment["deploymentId"]) or {}
        ownership = (row.get("health") or {}).get("ontologyEvolution") or {}
        if ownership.get("state") == "rolled-back" and control.active_deployment_id == plan["baseline"]["deploymentId"]:
            return {"status": "rolled-back"}
        if control.active_deployment_id == deployment["deploymentId"]:
            if ownership.get("planFingerprint") != plan["fingerprint"] or not ownership.get("adoptedAt"):
                raise RuntimeError("active-evolution-receipt-missing")
            return {"status": "active", "adoptedAt": ownership["adoptedAt"],
                    "criticalFailure": (row.get("health") or {}).get("status") in {"corrupt", "invalid-contract"}}
        if (control.active_deployment_id != plan["baseline"]["deploymentId"]
                or control.candidate_deployment_id != deployment["deploymentId"]):
            return {"status": "superseded"}
        return {"status": "shadow"}

    def comparison(self, plan, deployment, observed_after=""):
        artifact = self.registry.release_artifact(deployment["deploymentId"])
        if (not artifact.get("valid") or artifact.get("artifactFingerprint") != deployment.get("artifactFingerprint")
                or artifact["artifact"].get("evolutionPlanFingerprint") != plan["fingerprint"]):
            return {"status": "unavailable", "reason": "candidate-artifact-mismatch"}
        return self.outcomes.ontology_evolution_comparison(plan, observed_after=observed_after)

    def adopt(self, plan, deployment, assessment):
        validate_evolution_plan(plan)
        if (assessment.get("status") != "qualified" or assessment.get("candidateFingerprint") != plan["fingerprint"]
                or assessment.get("policyFingerprint") != plan["policyFingerprint"]):
            raise ValueError("Exact qualified comparison is required")
        readiness = self.platform.mark_candidate(deployment["deploymentId"])
        if readiness.get("status") != "candidate":
            return {"status": "waiting", "reason": "runtime-readiness-required", "readiness": readiness}
        with self.locks.processing_lock("ontology-evolution-control") as acquired:
            if not acquired:
                return {"status": "waiting", "reason": "evolution-control-busy"}
            if self.state(plan, deployment)["status"] != "shadow":
                return {"status": "waiting", "reason": "baseline-changed-before-adoption"}
            receipt = {"planFingerprint": plan["fingerprint"], "policyFingerprint": plan["policyFingerprint"],
                       "assessmentFingerprint": evolution_fingerprint(assessment), "state": "adopted", "adoptedAt": utc_now_iso()}
            control = self.registry.switch_evolution_release(
                plan["baseline"]["deploymentId"], deployment["deploymentId"], receipt,
                expected_version=self.registry.control().version,
            )
            self.platform.synchronize_control_capabilities(control)
            return {"status": "promoted", "adoptedAt": receipt["adoptedAt"], "control": control.to_dict()}

    def rollback(self, plan, deployment, assessment):
        with self.locks.processing_lock("ontology-evolution-control") as acquired:
            if not acquired:
                return {"status": "waiting"}
            control = self.registry.control()
            if (control.active_deployment_id != deployment["deploymentId"]
                    or control.candidate_deployment_id != plan["baseline"]["deploymentId"]):
                return {"status": "blocked", "reason": "rollback-ownership-changed"}
            receipt = {"planFingerprint": plan["fingerprint"], "state": "rolled-back", "rolledBackAt": utc_now_iso(),
                       "assessmentFingerprint": evolution_fingerprint(assessment)}
            result = self.registry.switch_evolution_release(
                deployment["deploymentId"], plan["baseline"]["deploymentId"], receipt,
                expected_version=control.version, rollback=True,
            )
            self.platform.synchronize_control_capabilities(result)
            return {"status": "rolled-back", "control": result.to_dict()}

    def retire(self, plan, deployment):
        with self.locks.processing_lock("ontology-evolution-control") as acquired:
            if not acquired:
                return {"status": "waiting"}
            control = self.registry.control()
            if deployment["deploymentId"] in {control.active_deployment_id, control.delivery_deployment_id}:
                return {"status": "blocked", "reason": "cannot-retire-active-release"}
            if control.candidate_deployment_id == deployment["deploymentId"]:
                self.registry.set_control(control.active_deployment_id, control.delivery_deployment_id, "", expected_version=control.version)
            self.registry.transition(deployment["deploymentId"], "retired")
            return {"status": "retired"}

    def finish_monitoring(self, plan, deployment):
        with self.locks.processing_lock("ontology-evolution-control") as acquired:
            if not acquired:
                return {"status": "waiting"}
            control = self.registry.control()
            if control.active_deployment_id != deployment["deploymentId"]:
                return {"status": "blocked", "reason": "monitoring-ownership-changed"}
            if control.candidate_deployment_id == plan["baseline"]["deploymentId"]:
                self.registry.set_control(control.active_deployment_id, control.delivery_deployment_id, "", expected_version=control.version)
            # The old immutable artifact remains available for an explicit recovery.
            return {"status": "completed"}
