"""Project compact statistical-signal packets into the investment ABox."""

from typing import Dict, Mapping

from .ontology_contracts import PortfolioOntology
from .ontology_schema import add_entity, add_relation
from .statistical_signals.registry import model_release
from .hypothesis_catalog import hypothesis_family_definition


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _feature_summary(signal: Mapping[str, object]) -> Dict[str, object]:
    features = _mapping(signal.get("inputFeatures"))
    return {
        key: round(_number(features.get(key)), 8)
        for key in (
            "priceReturn",
            "currentPrice",
            "recentReturn",
            "velocityChange",
            "realizedVolatility",
            "slopeRatio",
            "drawdown",
            "rebound",
            "ma20Distance",
            "ma60Distance",
            "latestSmartMoneyVolumeRatio",
            "meanSmartMoneyVolumeRatio",
            "flowSignPersistence",
            "tradeStrength",
            "bidAskImbalance",
            "volumeRatio",
        )
        if key in features
    }


def statistical_signal_rows_for_symbol(
    runtime_context: Mapping[str, object],
    symbol: object,
):
    snapshot = _mapping(runtime_context.get("statisticalSignalSnapshot"))
    normalized = str(symbol or "").upper().strip()
    return snapshot, [
        dict(item)
        for item in snapshot.get("signals") or []
        if isinstance(item, Mapping)
        and str(item.get("subjectId") or "").upper().strip() == normalized
    ]


def statistical_assessment_rows_for_symbol(
    runtime_context: Mapping[str, object],
    symbol: object,
):
    snapshot = _mapping(runtime_context.get("statisticalSignalSnapshot"))
    normalized = str(symbol or "").upper().strip()
    return snapshot, [
        dict(item)
        for item in snapshot.get("assessments") or []
        if isinstance(item, Mapping)
        and str(item.get("subjectId") or "").upper().strip() == normalized
    ]


def _ensure_hypothesis_family_node(
    graph: PortfolioOntology,
    family_nodes: Dict[str, str],
    family_id: str,
) -> str:
    if family_id in family_nodes:
        return family_nodes[family_id]
    family = hypothesis_family_definition(family_id)
    family_nodes[family_id] = add_entity(
        graph,
        "hypothesis-family-definition",
        family_id,
        family.label if family else family_id,
        {
            "tboxClass": "HypothesisFamily",
            "tboxClasses": ["InvestmentThesis", "HypothesisFamily"],
            "familyId": family_id,
            "predictionTarget": family.prediction_target if family else "",
            "expectedOutcome": family.expected_outcome if family else "",
            "expectedDirection": family.expected_direction if family else "",
            "defaultHorizon": family.default_horizon if family else "",
            "outcomeMetric": family.outcome_metric if family else "",
            "falsificationContract": family.falsification_contract if family else "",
            "competingFamilyIds": list(family.competing_family_ids) if family else [],
            "source": "investment-hypothesis-catalog",
        },
    )
    return family_nodes[family_id]


def statistical_signal_packet_can_replace_temporal_anchors(
    runtime_context: Mapping[str, object],
    symbol: object,
) -> bool:
    _, signals = statistical_signal_rows_for_symbol(runtime_context, symbol)
    if not signals:
        return False
    for signal in signals:
        try:
            release = model_release(signal.get("modelReleaseId"))
        except ValueError:
            return False
        eligibility = _mapping(signal.get("eligibility"))
        if release.status != "production" or release.validation_status != "calibrated":
            return False
        if (
            str(eligibility.get("decisionEligibility") or "") != "eligible"
            or str(eligibility.get("validationStatus") or "") != "calibrated"
        ):
            return False
    return True


def add_position_statistical_signal_concepts(
    graph: PortfolioOntology,
    stock_id: str,
    symbol: str,
    runtime_context: Dict[str, object] = None,
) -> None:
    runtime_context = runtime_context if isinstance(runtime_context, dict) else {}
    snapshot, signals = statistical_signal_rows_for_symbol(runtime_context, symbol)
    _, assessments = statistical_assessment_rows_for_symbol(runtime_context, symbol)
    if not signals and not assessments:
        return
    feature_reference = str(
        snapshot.get("sourceFeatureSnapshotId")
        or snapshot.get("sharedMaterialHash")
        or snapshot.get("materialHash")
        or ""
    )
    feature_node_id = add_entity(
        graph,
        "temporal-feature-snapshot-reference",
        feature_reference,
        str(symbol or "") + " 기간 특징 스냅샷",
        {
            "tboxClass": "TemporalFeatureSnapshotReference",
            "tboxClasses": ["Observation", "TemporalFeatureSnapshotReference"],
            "featureSetVersion": str(snapshot.get("featureSetVersion") or ""),
            "featureMaterialHash": feature_reference,
            "observedAt": str(snapshot.get("asOf") or ""),
            "source": "temporal-feature-snapshot-store",
        },
    )
    release_nodes = {}
    hypothesis_family_nodes = {}
    for signal in signals:
        release_id = str(signal.get("modelReleaseId") or snapshot.get("modelReleaseId") or "").strip()
        if release_id not in release_nodes:
            try:
                release = model_release(release_id)
                release_status = release.status
                release_validation = release.validation_status
                release_eligibility = release.decision_eligibility
            except ValueError:
                release_status = "unknown"
                release_validation = "unknown"
                release_eligibility = "reference-only"
            release_nodes[release_id] = add_entity(
                graph,
                "statistical-model-release",
                release_id,
                release_id,
                {
                    "tboxClass": "StatisticalModelRelease",
                    "tboxClasses": ["StatisticalModelRelease"],
                    "releaseId": release_id,
                    "featureSetVersion": str(signal.get("featureSetVersion") or snapshot.get("featureSetVersion") or ""),
                    "releaseStatus": release_status,
                    "validationStatus": release_validation,
                    "decisionEligibility": release_eligibility,
                    "source": "statistical-signal-registry",
                },
            )
        release_node_id = release_nodes[release_id]
        eligibility = _mapping(signal.get("eligibility"))
        signal_type = str(signal.get("signalType") or "").strip()
        signal_id = add_entity(
            graph,
            "statistical-model-signal",
            str(signal.get("signalId") or signal_type),
            str(symbol or "") + " " + signal_type,
            {
                "tboxClass": "ModelSignalObservation",
                "tboxClasses": ["Observation", "ModelSignalObservation"],
                "symbol": str(symbol or "").upper(),
                "field": "modelSignal",
                "signalType": signal_type,
                "signalFamily": str(signal.get("signalFamily") or ""),
                "horizon": str(signal.get("horizon") or ""),
                "polarity": str(signal.get("polarity") or "context"),
                "score": round(_number(signal.get("score")), 8),
                "strengthBand": str(signal.get("strengthBand") or "weak"),
                "contractMatched": bool(signal.get("contractMatched")),
                "confidence": round(_number(signal.get("confidence")), 8),
                "probability": signal.get("probability"),
                "probabilityLower": signal.get("probabilityLower"),
                "probabilityUpper": signal.get("probabilityUpper"),
                "hypothesisFamilyId": str(signal.get("hypothesisFamilyId") or ""),
                "hypothesisContractCount": len(signal.get("hypothesisContractIds") or []),
                "outcomeMetric": str(signal.get("outcomeMetric") or ""),
                "knowledgeCutoffAt": str(signal.get("knowledgeCutoffAt") or signal.get("observedAt") or ""),
                "marketSession": str(signal.get("marketSession") or ""),
                "sourceAgeSeconds": signal.get("sourceAgeSeconds"),
                "freshnessCompatible": bool(signal.get("freshnessCompatible", True)),
                "uncertaintyStatus": str(signal.get("uncertaintyStatus") or "uncalibrated"),
                "sampleCount": int(_number(signal.get("sampleCount"))),
                "coverageRatio": round(_number(signal.get("coverageRatio")), 8),
                "observedAt": str(signal.get("observedAt") or ""),
                "featureSetVersion": str(signal.get("featureSetVersion") or ""),
                "releaseId": str(signal.get("modelReleaseId") or release_id),
                "materialHash": str(signal.get("materialHash") or ""),
                "validationStatus": str(eligibility.get("validationStatus") or "replay-required"),
                "decisionEligibility": str(eligibility.get("decisionEligibility") or "reference-only"),
                "eligibilityStatus": str(eligibility.get("status") or "ineligible"),
                "dataState": str(eligibility.get("dataQuality") or "unknown"),
                "source": "statistical-signal-pipeline",
                **_feature_summary(signal),
            },
        )
        assessment_id = add_entity(
            graph,
            "signal-eligibility-assessment",
            str(signal.get("signalId") or signal_type),
            str(symbol or "") + " " + signal_type + " 적격성",
            {
                "tboxClass": "SignalEligibilityAssessment",
                "tboxClasses": ["ActionabilityAssessment", "SignalEligibilityAssessment"],
                "symbol": str(symbol or "").upper(),
                "signalType": signal_type,
                "eligibilityStatus": str(eligibility.get("status") or "ineligible"),
                "eligibilityReasons": list(eligibility.get("reasons") or []),
                "dataState": str(eligibility.get("dataQuality") or "unknown"),
                "validationStatus": str(eligibility.get("validationStatus") or "replay-required"),
                "decisionEligibility": str(eligibility.get("decisionEligibility") or "reference-only"),
                "source": "statistical-signal-policy",
            },
        )
        relation_properties = {
            "source": "statistical-signal-pipeline",
            "field": "modelSignal",
            "signalType": signal_type,
            "polarity": str(signal.get("polarity") or "context"),
            "evidenceRole": "reference",
            "decisionEligibility": str(eligibility.get("decisionEligibility") or "reference-only"),
            "validationStatus": str(eligibility.get("validationStatus") or "replay-required"),
            "dataState": str(eligibility.get("dataQuality") or "unknown"),
            "aiInfluenceLabel": signal_type,
        }
        add_relation(graph, stock_id, signal_id, "HAS_MODEL_SIGNAL", properties=relation_properties)
        add_relation(graph, signal_id, release_node_id, "GENERATED_BY_MODEL_RELEASE", properties=relation_properties)
        add_relation(graph, signal_id, feature_node_id, "BASED_ON_FEATURE_SNAPSHOT", properties=relation_properties)
        add_relation(graph, signal_id, assessment_id, "HAS_SIGNAL_ELIGIBILITY", properties=relation_properties)
        for contract_id in sorted({
            str(value or "").strip()
            for value in signal.get("hypothesisContractIds") or []
            if str(value or "").strip()
        }):
            contract_properties = {
                **relation_properties,
                "tboxClass": "ModelHypothesisEvidence",
                "tboxClasses": ["Observation", "ModelHypothesisEvidence"],
                "symbol": str(symbol or "").upper(),
                "hypothesisContractId": contract_id,
                "hypothesisFamilyId": str(signal.get("hypothesisFamilyId") or ""),
                "score": round(_number(signal.get("score")), 8),
                "strengthBand": str(signal.get("strengthBand") or "weak"),
                "contractMatched": bool(signal.get("contractMatched")),
                "confidence": round(_number(signal.get("confidence")), 8),
                "releaseId": str(signal.get("modelReleaseId") or release_id),
                "eligibilityStatus": str(eligibility.get("status") or "ineligible"),
                "observedAt": str(signal.get("observedAt") or ""),
                "knowledgeCutoffAt": str(
                    signal.get("knowledgeCutoffAt") or signal.get("observedAt") or ""
                ),
                "marketSession": str(signal.get("marketSession") or ""),
                "sourceAgeSeconds": signal.get("sourceAgeSeconds"),
                "freshnessCompatible": bool(signal.get("freshnessCompatible", True)),
                "materialHash": str(signal.get("materialHash") or ""),
                "modelEvidenceIds": list(
                    (_mapping(signal.get("inputFeatures"))).get("evidenceIds") or []
                )[:64],
            }
            contract_node_id = add_entity(
                graph,
                "statistical-model-hypothesis-evidence",
                str(signal.get("signalId") or signal_type) + ":" + contract_id,
                str(symbol or "") + " " + contract_id + " 모델 근거",
                contract_properties,
            )
            add_relation(
                graph,
                stock_id,
                contract_node_id,
                "HAS_MODEL_SIGNAL",
                properties=contract_properties,
            )
            add_relation(
                graph,
                contract_node_id,
                signal_id,
                "DERIVED_FROM_MODEL_SIGNAL",
                properties=contract_properties,
            )
            add_relation(
                graph,
                contract_node_id,
                release_node_id,
                "GENERATED_BY_MODEL_RELEASE",
                properties=contract_properties,
            )
            add_relation(
                graph,
                contract_node_id,
                assessment_id,
                "HAS_SIGNAL_ELIGIBILITY",
                properties=contract_properties,
            )
        hypothesis_family_id = str(signal.get("hypothesisFamilyId") or "").strip()
        if hypothesis_family_id:
            family_node_id = _ensure_hypothesis_family_node(
                graph,
                hypothesis_family_nodes,
                hypothesis_family_id,
            )
            add_relation(
                graph,
                signal_id,
                family_node_id,
                "SUPPORTS_HYPOTHESIS_FAMILY",
                properties={
                    **relation_properties,
                    "evidenceRole": "reference" if str(eligibility.get("decisionEligibility") or "") != "eligible" else "support",
                },
            )

    assessments_by_family = {}
    for assessment in assessments:
        family_id = str(assessment.get("hypothesisFamilyId") or "").strip()
        if family_id:
            assessments_by_family.setdefault(family_id, []).append(assessment)
    for family_id, rows in sorted(assessments_by_family.items()):
        status_contracts = {
            status: sorted({
                str(item.get("hypothesisContractId") or "").strip()
                for item in rows
                if str(item.get("status") or "") == status
                and str(item.get("hypothesisContractId") or "").strip()
            })
            for status in ("supported", "not-supported", "untested")
        }
        assessment_hashes = sorted({
            str(item.get("materialHash") or "").strip()
            for item in rows
            if str(item.get("materialHash") or "").strip()
        })
        summary_key = ":".join([
            str(snapshot.get("sourceFeatureSnapshotId") or snapshot.get("materialHash") or ""),
            str(symbol or "").upper(),
            family_id,
        ])
        summary_node_id = add_entity(
            graph,
            "model-hypothesis-assessment",
            summary_key,
            str(symbol or "").upper() + " " + family_id + " 가설 평가",
            {
                "tboxClass": "ModelHypothesisAssessment",
                "tboxClasses": ["ActionabilityAssessment", "ModelHypothesisAssessment"],
                "symbol": str(symbol or "").upper(),
                "hypothesisFamilyId": family_id,
                "assessmentCount": len(rows),
                "supportedCount": len(status_contracts["supported"]),
                "notSupportedCount": len(status_contracts["not-supported"]),
                "untestedCount": len(status_contracts["untested"]),
                "supportedContractIds": status_contracts["supported"][:24],
                "notSupportedContractIds": status_contracts["not-supported"][:24],
                "untestedContractIds": status_contracts["untested"][:24],
                "decisionEligibleSupportedCount": sum(
                    1 for item in rows
                    if str(item.get("status") or "") == "supported"
                    and str(item.get("decisionEligibility") or "") in {"eligible", "conditional"}
                ),
                "observedAt": str(snapshot.get("asOf") or ""),
                "sourceFeatureSnapshotId": str(snapshot.get("sourceFeatureSnapshotId") or ""),
                "assessmentMaterialHashes": assessment_hashes[:24],
                "source": "statistical-hypothesis-assessment",
            },
        )
        relation_properties = {
            "source": "statistical-hypothesis-assessment",
            "hypothesisFamilyId": family_id,
            "evidenceRole": "diagnostic",
            "observedAt": str(snapshot.get("asOf") or ""),
        }
        add_relation(
            graph,
            stock_id,
            summary_node_id,
            "HAS_HYPOTHESIS_ASSESSMENT",
            properties=relation_properties,
        )
        add_relation(
            graph,
            summary_node_id,
            _ensure_hypothesis_family_node(graph, hypothesis_family_nodes, family_id),
            "ASSESSES_HYPOTHESIS_FAMILY",
            properties=relation_properties,
        )
        add_relation(
            graph,
            summary_node_id,
            feature_node_id,
            "BASED_ON_FEATURE_SNAPSHOT",
            properties=relation_properties,
        )
