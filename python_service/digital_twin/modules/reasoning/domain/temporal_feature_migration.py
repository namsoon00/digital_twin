"""Bounded proof of a v2 -> valid-price-path feature migration.

Raw parity failures remain failures in the original comparison. A separately
reviewed migration can explain calculated facts, never changed source facts,
unrelated rules, a new investment action, or shadow delivery.
"""

from copy import deepcopy

from digital_twin.modules.market_data.contracts import VALID_PRICE_PATH_FEATURE_VERSION, payload_fingerprint
from digital_twin.modules.model_registry.contracts import GraphInferenceRule, rulebox_rules_hash


LEGACY_FEATURE_VERSION = "temporal-features-v2"
MIGRATION_VERSION = "valid-price-path-migration-v1"
RECEIPT_VERSION = "temporal-feature-source-proof-v1"
REVIEW_VERSION = "temporal-feature-migration-review-v1"
TEMPORAL_KINDS = frozenset({"temporal-window", "temporal-observation", "temporal-coverage-gap"})


def transformed_rule_payloads(rows):
    from digital_twin.modules.model_registry.contracts import with_valid_temporal_price_paths
    result = []
    for row in rows:
        before = GraphInferenceRule.from_dict(row)
        after = with_valid_temporal_price_paths([before])[0]
        # Preserve every unmodified authored byte, including older manifests.
        result.append(after.to_dict() if before != after else deepcopy(row))
    return result


def _graph_facts(graph, omit_temporal=False):
    omitted = {item.entity_id for item in graph.entities if item.kind in TEMPORAL_KINDS} if omit_temporal else set()
    entities = [item.to_dict() for item in graph.entities if item.entity_id not in omitted]
    relations = [item.to_dict() for item in graph.relations if item.source not in omitted and item.target not in omitted]
    return {"entities": sorted(entities, key=payload_fingerprint), "relations": sorted(relations, key=payload_fingerprint)}


def feature_source_proof(graph, prepared, snapshot):
    """Only paired validation pays for a second *factual* graph calculation.

    The legacy calculation is a common reference on the exact captured input.
    No TypeDB decisions or model results are fabricated by this calculation.
    """
    from digital_twin.modules.reasoning.domain.projection_facts import build_factual_graph
    version = str((prepared.runtime_context.get("settings") or {}).get("_reasoningFeatureSetVersion") or "")
    if version not in {LEGACY_FEATURE_VERSION, VALID_PRICE_PATH_FEATURE_VERSION}:
        return {}
    legacy = graph
    if version != LEGACY_FEATURE_VERSION:
        context = {**prepared.runtime_context, "settings": {
            **dict(prepared.runtime_context.get("settings") or {}), "_reasoningFeatureSetVersion": LEGACY_FEATURE_VERSION}}
        legacy = build_factual_graph(snapshot, prepared.observation_input, prepared.projection_external_signals,
                                     context, prepared.active_tbox, {})
    original = payload_fingerprint(_graph_facts(legacy, omit_temporal=True))
    current = payload_fingerprint(_graph_facts(graph, omit_temporal=True))
    return {
        "version": RECEIPT_VERSION, "featureSetVersion": version,
        "accountId": snapshot.account_id, "asOf": snapshot.generated_at,
        "legacyFactualFingerprint": payload_fingerprint(_graph_facts(legacy)),
        "actualFactualFingerprint": payload_fingerprint(_graph_facts(graph)),
        "nonTemporalFingerprint": current, "nonTemporalUnchanged": current == original,
        "rawWindowsFingerprint": payload_fingerprint(prepared.runtime_context.get("temporalObservationWindows") or {}),
    }


def validate_migration_artifacts(baseline_record, candidate, baseline_id):
    """Exact authored successor; a changed threshold/strategy is not this upgrade."""
    baseline = baseline_record.get("artifact") or {}
    declaration = candidate.get("temporalFeatureMigration") or {}
    if (baseline_record.get("valid") is not True
            or payload_fingerprint(baseline) != baseline_record.get("artifactFingerprint")
            or declaration.get("version") != MIGRATION_VERSION
            or declaration.get("baselineDeploymentId") != baseline_id
            or declaration.get("baselineArtifactFingerprint") != baseline_record.get("artifactFingerprint")
            or declaration.get("baselineFeatureSetVersion") != LEGACY_FEATURE_VERSION
            or declaration.get("candidateFeatureSetVersion") != VALID_PRICE_PATH_FEATURE_VERSION
            or baseline.get("releaseBundle", {}).get("feature_set_version") != LEGACY_FEATURE_VERSION
            or baseline.get("tboxMetadata") != candidate.get("tboxMetadata")
            or baseline.get("tboxFingerprint") != candidate.get("tboxFingerprint")
            or declaration.get("baselineRuleboxFingerprint") != baseline.get("ruleboxFingerprint")
            or declaration.get("candidateRuleboxFingerprint") != candidate.get("ruleboxFingerprint")
            or rulebox_rules_hash(baseline.get("rules") or []) != baseline.get("ruleboxFingerprint")
            or rulebox_rules_hash(candidate.get("rules") or []) != candidate.get("ruleboxFingerprint")
            or candidate.get("rules") != transformed_rule_payloads(baseline.get("rules") or [])):
        raise ValueError("temporal-feature-migration-artifact-mismatch")
    return declaration


def temporal_migration_review_valid(comparison):
    from digital_twin.modules.reasoning.domain.management_upgrade_review import management_review_fingerprint
    review = comparison.get("temporalFeatureMigrationReview") or {}
    return bool(review.get("version") == REVIEW_VERSION and review.get("status") == "explained-feature-migration"
                and review.get("rawComparisonFingerprint") == management_review_fingerprint(comparison)
                and review.get("baselineArtifactFingerprint") and review.get("candidateArtifactFingerprint")
                and review.get("sourceParity") == "equivalent" and review.get("investmentActions") == "unchanged"
                and review.get("reviewedAccounts") and review.get("nativeRuleSlotCoveragePct") == 100.0
                and comparison.get("shadowDeliveryCount") == 0)


def review_temporal_feature_migration(baseline_job, candidate_job, comparison, baseline_record, candidate_record):
    from digital_twin.modules.reasoning.domain.reasoning_comparison_identity import comparison_input_contract
    from digital_twin.modules.reasoning.domain.reasoning_shadow import independent_reasoning_outcome_packet, compare_engine_outcomes
    from digital_twin.modules.reasoning.domain.management_upgrade_review import management_review_fingerprint
    receipt = {"version": REVIEW_VERSION, "status": "not-explained"}
    try:
        if (candidate_record.get("valid") is not True
                or payload_fingerprint(candidate_record.get("artifact")) != candidate_record.get("artifactFingerprint")):
            raise ValueError("candidate-artifact-unverified")
        validate_migration_artifacts(baseline_record, candidate_record["artifact"], baseline_job.get("deploymentId"))
        source = comparison_input_contract(baseline_job, candidate_job)
        if any(source.get(k) != (comparison.get("sourceInput") or {}).get(k) for k in source):
            raise ValueError("input-receipt-mismatch")
        if any(not valid for key, valid in source["checks"].items() if key not in {"temporalFeatureSemantics", "aboxInputs"}):
            raise ValueError("source-input-changed")
        if not all(source[side].get("aboxInputs") for side in ("baseline", "candidate")):
            raise ValueError("complete-native-input-manifest-required")
        projections = [(job.get("result") or {}).get("projection_results") or (job.get("result") or {}).get("projectionResults") or {}
                       for job in (baseline_job, candidate_job)]
        if not projections[0] or projections[0].keys() != projections[1].keys():
            raise ValueError("projection-account-mismatch")
        for account in projections[0]:
            proofs = [p[account].get("temporalFeatureMigrationInput") or {} for p in projections]
            for proof, version, p in zip(proofs, (LEGACY_FEATURE_VERSION, VALID_PRICE_PATH_FEATURE_VERSION), projections):
                projection = p[account]
                if (proof.get("version") != RECEIPT_VERSION or proof.get("featureSetVersion") != version
                        or proof.get("accountId") != account or proof.get("asOf") != source["baseline"]["sourceSnapshotAt"]
                        or proof.get("nonTemporalUnchanged") is not True
                        or not proof.get("actualFactualFingerprint")
                        or (projection.get("temporalFeatureInput") or {}).get("featureSetVersion") != version
                        or not projection.get("nativeTypeDbReasoningCompleted") or not projection.get("generationAligned")
                        or projection["comparisonScope"]["fingerprint"] != projection["persistedComparisonScope"]["fingerprint"]):
                    raise ValueError("factual-source-proof-incomplete")
            for key in ("legacyFactualFingerprint", "nonTemporalFingerprint", "rawWindowsFingerprint"):
                if not proofs[0].get(key) or proofs[0][key] != proofs[1].get(key):
                    raise ValueError("non-feature-facts-changed:" + key)
        packets = [independent_reasoning_outcome_packet(job) for job in (baseline_job, candidate_job)]
        raw = compare_engine_outcomes(*packets, [source]).to_dict()
        if (raw.get("ruleSlotCoveragePct") != 100.0 or comparison.get("shadowDeliveryCount") != 0
                or raw.get("status") in {"candidate-failed", "delivery-violation"}):
            raise ValueError("native-rule-coverage-or-zero-delivery-unverified")
        # A feature correction may change evidence availability. This narrow
        # migration never explains a different order, account management, or
        # chosen action; those require their own reviewed investment change.
        action_keys = ("accountId", "symbol", "candidateAction", "selectedRuleId", "decisionStage",
                       "decisionEffect", "actionGroup", "judgementBlocked", "managementDecision")
        actions = [[{key: row.get(key) for key in action_keys} for row in p.get("candidates") or []] for p in packets]
        if not actions[0] or actions[0] != actions[1]:
            raise ValueError("investment-actions-changed-or-missing")
        action_contracts = []
        for job in (baseline_job, candidate_job):
            rows = (job.get("result") or {}).get("decision_syntheses") or (job.get("result") or {}).get("decisionSyntheses") or []
            action_contracts.append([{key: row.get(key) for key in (
                "account_id", "symbol", "allowed_actions", "blocked_actions", "execution_action",
                "allowedActions", "blockedActions", "executionAction",
            )} for row in rows])
        if action_contracts[0] != action_contracts[1]:
            raise ValueError("investment-action-authority-changed")
        from digital_twin.modules.model_registry.contracts import governed_graph_inference_rules, with_valid_temporal_price_paths
        raw_rules = governed_graph_inference_rules(filtered_temporal_paths=False)
        allowed = {r.rule_id for r, upgraded in zip(raw_rules, with_valid_temporal_price_paths(raw_rules)) if r != upgraded}
        for before, after in zip(baseline_record["artifact"]["rules"], candidate_record["artifact"]["rules"]):
            if before != after:
                allowed.add(before["rule_id"])
        matched = [{rid for p in packet.get("projections") or [] for rid in p.get("nativeMatchedRuleIds") or []} for packet in packets]
        if (matched[0] ^ matched[1]) - allowed:
            raise ValueError("unrelated-native-rule-result-changed")
        return {**receipt, "status": "explained-feature-migration", "sourceParity": "equivalent",
                "investmentActions": "unchanged", "nativeRuleSlotCoveragePct": raw["ruleSlotCoveragePct"],
                "reviewedAccounts": sorted(projections[0]),
                "rawComparisonFingerprint": management_review_fingerprint(comparison),
                "baselineArtifactFingerprint": baseline_record["artifactFingerprint"],
                "candidateArtifactFingerprint": candidate_record["artifactFingerprint"]}
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        return {**receipt, "reasonCode": str(error)[:160]}
