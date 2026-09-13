"""Durable experimental lifecycle, independent of investment delivery."""

from datetime import timedelta

from digital_twin.modules.model_registry.domain.ontology_evolution import (
    create_plan, evaluate_comparison, timestamp, validate_plan,
)


EVOLUTION_STATUSES = {"shadow-observing", "adoption-ready", "evolution-monitoring"}


class OntologyEvolutionService:
    def __init__(self, runtime, policy, clock):
        self.runtime, self.policy, self.clock = runtime, policy, clock

    def start(self, case, persist):
        if self.policy["mode"] == "disabled":
            return {"status": "disabled", "reason": "ontology-evolution-disabled"}
        if not case.evolution.get("plan"):
            baseline = self.runtime.baseline(case.candidate_rule, self.policy)
            plan = create_plan(case, case.candidate_rule, baseline, self.policy, self.clock())
            case.evolution = {"plan": plan, "state": "planned", "reason": "isolated-release-required"}
            case.decision_impact.update({"requiresDeploymentApproval": False,
                                         "deploymentAuthority": "versioned-evolution-policy"})
            case.transition("shadow-observing", "evolution")
            self.schedule(case)
            persist(case, "evolution-plan-frozen")
        return self.advance(case, persist)

    def schedule(self, case):
        policy = validate_plan(case.evolution["plan"])
        case.retry.update({
            "nextCheckAt": (timestamp(self.clock()) + timedelta(minutes=policy["retryMinutes"])).isoformat(),
            "lastCheckedAt": self.clock(), "state": "evolution-waiting", "owner": "ontology-evolution",
        })

    def advance(self, case, persist):
        plan = case.evolution["plan"]
        policy = validate_plan(plan)
        self.schedule(case)
        try:
            if self.policy["mode"] == "disabled":
                return self.wait(case, persist, "evolution-disabled-by-operator")
            deployment = case.evolution.get("deployment") or {}
            if not deployment.get("deploymentId"):
                if timestamp(self.clock()) >= timestamp(plan["createdAt"]) + timedelta(days=policy["maximumShadowDays"]):
                    case.transition("retired", "evolution")
                    return self.finish(case, persist, "candidate-start-window-expired")
                deployment = self.runtime.stage(plan)
                if deployment.get("dataReadiness"):
                    case.evolution["dataReadiness"] = deployment["dataReadiness"]
                if deployment.get("status") == "superseded":
                    case.transition("superseded", "evolution")
                    return self.finish(case, persist, "baseline-or-candidate-replaced")
                if deployment.get("status") != "staged":
                    return self.wait(case, persist, deployment.get("reason") or "candidate-slot-unavailable", deployment)
                case.evolution["deployment"] = deployment
                persist(case, "evolution-shadow-staged")
            state = self.runtime.state(plan, deployment)
            if state["status"] == "superseded":
                case.transition("superseded", "evolution")
                return self.finish(case, persist, "baseline-or-candidate-replaced")
            # Recover a completed atomic switch even if the worker died before saving the case.
            if state["status"] == "active" and not case.evolution.get("adoptedAt"):
                case.evolution["adoptedAt"] = state["adoptedAt"]
                case.transition("evolution-monitoring", "evolution")
                persist(case, "evolution-adoption-recovered")
            if state["status"] == "rolled-back":
                case.transition("rolled-back", "evolution")
                return self.finish(case, persist, "rollback-recovered")
            monitoring = bool(case.evolution.get("adoptedAt"))
            if monitoring and state.get("criticalFailure"):
                return self.rollback(case, persist, {"reason": "runtime-contract-failure"}, "operational-failure")
            if monitoring and timestamp(self.clock()) >= timestamp(case.evolution["adoptedAt"]) + timedelta(days=policy["maximumShadowDays"]):
                return self.rollback(case, persist, {"reason": "monitoring-window-expired"}, "post-adoption-window-expired")
            if not monitoring and timestamp(self.clock()) >= timestamp(plan["createdAt"]) + timedelta(days=policy["maximumShadowDays"]):
                result = self.runtime.retire(plan, deployment)
                if result.get("status") == "retired":
                    case.transition("retired", "evolution")
                    return self.finish(case, persist, "observation-window-expired")
                return self.wait(case, persist, "candidate-retirement-pending", result)
            evidence = self.runtime.comparison(plan, deployment, observed_after=case.evolution.get("adoptedAt") or "")
            case.evolution["dataSummary"] = dict(evidence.get("dataSummary") or {})
            assessment = evaluate_comparison(plan, evidence, now=self.clock(),
                                             observed_after=case.evolution.get("adoptedAt") or "")
            case.evolution["monitoring" if monitoring else "assessment"] = assessment
            if monitoring:
                regression = (assessment["status"] == "not-better"
                              and assessment.get("improvement", 0) < -policy["minimumImprovement"]
                              and assessment.get("contradictionRate", 0) > policy["maximumContradictionRate"])
                if regression:
                    return self.rollback(case, persist, assessment, "forward-regression")
                if assessment["status"] in {"qualified", "not-better"}:
                    result = self.runtime.finish_monitoring(plan, deployment)
                    if result.get("status") == "completed":
                        case.transition("strengthened", "evolution")
                        return self.finish(case, persist, "post-adoption-cohort-no-regression")
                return self.wait(case, persist, "adopted-release-under-observation")
            external_review = any(row.get("check") == "review" for row in plan.get("validationRequirements") or [])
            if assessment["status"] == "qualified" and not external_review:
                case.transition("adoption-ready", "evolution")
                if policy["mode"] != "automatic" or self.policy["mode"] != "automatic":
                    return self.wait(case, persist, "shadow-policy-no-deployment")
                persist(case, "evolution-qualified")
                result = self.runtime.adopt(plan, deployment, assessment)
                case.evolution["adoption"] = result
                if result.get("status") == "promoted":
                    case.evolution["adoptedAt"] = result["adoptedAt"]
                    case.transition("evolution-monitoring", "evolution")
                    return self.finish(case, persist, "paired-holdout-and-runtime-checks-passed", terminal=False)
                return self.wait(case, persist, result.get("reason") or "runtime-readiness-required", result)
            if assessment["status"] == "not-better":
                result = self.runtime.retire(plan, deployment)
                if result.get("status") == "retired":
                    case.transition("retired", "evolution")
                    return self.finish(case, persist, "not-better-than-baseline")
                return self.wait(case, persist, "candidate-retirement-pending", result)
            return self.wait(case, persist, "external-validation-required" if external_review else assessment["reason"])
        except Exception as error:
            # Preserve plan, deployment and queued retry. Never turn an outage into no change.
            return self.wait(case, persist, "evolution-dependency-error", {"error": str(error)[:500]})

    def rollback(self, case, persist, assessment, reason):
        result = self.runtime.rollback(case.evolution["plan"], case.evolution["deployment"], assessment)
        case.evolution["rollback"] = result
        if result.get("status") == "rolled-back":
            case.transition("rolled-back", "evolution")
            return self.finish(case, persist, reason)
        return self.wait(case, persist, "rollback-pending", result)

    def wait(self, case, persist, reason, details=None):
        case.evolution.update({"state": case.status, "reason": reason, "details": details or {}})
        persist(case, "evolution-waiting", reason)
        return {"status": case.status, "case": case.to_dict(), "reason": reason}

    def finish(self, case, persist, reason, terminal=True):
        case.evolution.update({"state": case.status, "reason": reason, "details": {}})
        if terminal:
            case.retry["nextCheckAt"] = ""
        persist(case, "evolution-" + case.status, reason)
        return {"status": case.status, "case": case.to_dict(), "reason": reason}
