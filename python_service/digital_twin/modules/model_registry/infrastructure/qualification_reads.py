"""Read empirical comparison receipts without rewriting frozen decision facts."""
from digital_twin.infrastructure.mysql_operational_helpers import _json_loads
from digital_twin.modules.model_registry.domain.ontology_evolution import validate_plan, timestamp
from digital_twin.modules.outcomes.contracts import claim_validation_fingerprint


def qualification_receipts(connection, scopes, as_of):
    results = {}
    cutoff = timestamp(as_of)
    if not cutoff:
        return results
    # Each exact subject has a bounded local development catalog. Keep only
    # adopted candidates and their immutable pre-adoption comparison report.
    for account, symbol in sorted(scopes):
        rows = connection.execute(
            "SELECT payload_json FROM hypothesis_development_cases WHERE account_id = %s AND symbol = %s "
            "AND status IN ('evolution-monitoring', 'strengthened') ORDER BY updated_at DESC LIMIT 100",
            (account, symbol)).fetchall()
        for row in rows:
            case = _json_loads(row.get("payload_json"), {})
            evolution = case.get("evolution") or {}
            plan, assessment = evolution.get("plan") or {}, evolution.get("assessment") or {}
            try:
                validate_plan(plan)
            except (ValueError, KeyError, TypeError):
                continue
            evaluated = timestamp(assessment.get("evaluatedAt"))
            if (plan.get("accountId") != account or plan.get("symbol") != symbol
                    or assessment.get("status") != "qualified" or not evaluated or evaluated > cutoff
                    or assessment.get("candidateFingerprint") != plan.get("fingerprint")
                    or assessment.get("policyFingerprint") != plan.get("policyFingerprint")
                    or not assessment.get("evidenceIds")):
                continue
            fingerprint = claim_validation_fingerprint((plan.get("candidateRule") or {}).get("claim_contract") or {})
            if not fingerprint or fingerprint != (plan.get("baseline", {}).get("candidateClaim") or {}).get("validationFingerprint"):
                continue
            results[(account, symbol, fingerprint)] = {
                "baselineComparisonState": "qualified", "baselineComparisonClaimFingerprint": fingerprint,
                "comparisonPlanFingerprint": plan["fingerprint"], "comparisonEvaluatedAt": assessment["evaluatedAt"],
                "comparisonEvidenceIds": assessment["evidenceIds"], "comparisonImprovement": assessment.get("improvement")}
    return results
