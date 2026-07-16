import json
import re
from typing import Dict, Iterable, List

from ..domain.ontology_contracts import PortfolioOntology
from ..domain.ontology_decision_policy import decision_stage_from_action, relation_stage_priority
from ..domain.ontology_schema import default_tbox_metadata


def safe_relation_type(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9_]+", "_", str(value or "RELATED_TO").upper()).strip("_")
    if not normalized:
        return "RELATED_TO"
    if not re.match(r"^[A-Z_]", normalized):
        normalized = "R_" + normalized
    return normalized[:60]


def symbol_from_graph_reference(*values: object) -> str:
    symbol_prefixes = {
        "action-candidate",
        "blocked-action",
        "confidence-assessment",
        "execution-capacity",
        "inference-trace",
        "investment-thesis",
        "loss-defense-evidence",
        "next-check",
        "risk",
        "stock",
    }
    for value in values:
        parts = str(value or "").split(":")
        for index, part in enumerate(parts[:-1]):
            if part not in symbol_prefixes:
                continue
            candidate = str(parts[index + 1] or "").upper().strip()
            if re.match(r"^[A-Z0-9.]{1,12}$", candidate):
                return candidate
    return ""


def group_relation_rows(rows: Iterable[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    grouped: Dict[str, List[Dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("type") or "RELATED_TO"), []).append(row)
    return grouped

def number_or_none(value: object):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def derivation_decision_stage(derivation: Dict[str, object]) -> str:
    explicit = str(derivation.get("decision_stage") or derivation.get("decisionStage") or "").strip()
    if explicit:
        return explicit
    return decision_stage_from_action(
        str(derivation.get("action_group") or derivation.get("actionGroup") or ""),
        str(derivation.get("action_level") or derivation.get("actionLevel") or ""),
    )

def derivation_stage_priority(derivation: Dict[str, object]):
    explicit = number_or_none(derivation.get("stage_priority") or derivation.get("stagePriority"))
    if explicit:
        return explicit
    stage = derivation_decision_stage(derivation)
    return float(relation_stage_priority({
        "decisionStage": stage,
        "actionGroup": derivation.get("action_group") or derivation.get("actionGroup"),
        "actionLevel": derivation.get("action_level") or derivation.get("actionLevel"),
        "riskImpact": derivation.get("risk_impact") or derivation.get("riskImpact"),
        "supportImpact": derivation.get("support_impact") or derivation.get("supportImpact"),
    }))

def list_of_strings(value: object) -> List[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item or "").strip()]
    if value is None or value == "":
        return []
    return [str(value)]

def condition_target_level_types(condition: Dict[str, object]) -> List[str]:
    filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    return list_of_strings(filters.get("levelType"))

KNOWN_TARGET_FILTER_KEYS = {
    "field",
    "levelType",
    "dataScope",
    "domainScope",
    "relationScope",
    "group",
    "scope",
    "eventType",
    "polarity",
    "materialityPassed",
    "minMaterialityScore",
    "minValue",
    "maxValue",
    "tboxClass",
    "tboxClasses",
}

PROMOTED_NUMERIC_ENTITY_FIELDS = [
    "currentPrice",
    "averagePrice",
    "marketValue",
    "quantity",
    "sellableQuantity",
    "positionWeight",
    "positionAccountWeight",
    "changeRate",
    "priceChangeRate",
    "ma5",
    "ma20",
    "ma60",
    "ma5Distance",
    "ma20Distance",
    "ma60Distance",
    "ma20Slope",
    "ma60Slope",
    "trendCurve",
    "volume",
    "volumeRatio",
    "rawVolumeRatio",
    "timeAdjustedVolumeRatio",
    "expectedVolumeRatioNow",
    "tradeStrength",
    "tradingValue",
    "reportedTradingValue",
    "estimatedTradingValue",
    "tradingValueMismatchPct",
    "bidAskImbalance",
    "foreignNetVolume",
    "foreignNetAmount",
    "institutionNetVolume",
    "institutionNetAmount",
    "individualNetVolume",
    "individualNetAmount",
    "smartMoneyNetVolume",
    "investorFlowScore",
    "adrRatio",
    "adrPriceUsd",
    "adrVolume",
    "usdKrwRate",
    "localPriceKrw",
    "localEquivalentKrw",
    "leverageFactor",
    "price",
    "fairValue",
    "fairValuePrice",
    "marginOfSafetyPct",
    "expensivePremiumPct",
    "minimumMarginOfSafetyPct",
    "expectedEPS",
    "reportedEPS",
    "estimatedEPS",
    "targetPER",
    "forwardPE",
    "pegRatio",
    "dividendYield",
    "peerPER",
    "historicalMedianPER",
    "lookbackDays",
    "requiredSampleCount",
    "sampleCount",
    "coverageRatio",
    "elapsedHours",
    "startPrice",
    "priceChangePct",
    "profitLossRateStart",
    "profitLossRateEnd",
    "profitLossRateChangePct",
    "ma20DistanceStart",
    "ma20DistanceEnd",
    "ma20DistanceChange",
    "ma60DistanceStart",
    "ma60DistanceEnd",
    "volumeRatioEnd",
    "tradeStrengthEnd",
    "bidAskImbalanceEnd",
    "smartMoneyNetLatest",
    "smartMoneyNetChange",
    "individualNetLatest",
    "eventCount",
    "riskEventCount",
    "supportEventCount",
    "temporalRiskScore",
    "temporalSupportScore",
]

PROMOTED_TEXT_ENTITY_FIELDS = [
    "investmentStrategyProfile",
    "investmentStrategyProfileLabel",
    "positionRole",
    "targetPositionRole",
    "instrumentArchetype",
    "instrumentArchetypes",
    "positionIntent",
    "factor",
    "sensitivityLevel",
    "cryptoSymbol",
    "actionPolicy",
    "tradingValueQuality",
    "tradingValueBasis",
    "securityLineRole",
    "localSymbol",
    "companyName",
    "market",
    "currency",
    "exchange",
    "adrSymbol",
    "etfSymbol",
    "underlyingSymbol",
    "conversionStartDate",
    "listingDate",
    "sourceUrl",
    "valuationMethod",
    "formula",
    "windowKey",
    "hasSufficientHistory",
    "pricePathPattern",
    "flowPattern",
    "eventClusterType",
    "trendEpisodeType",
]

def condition_target_filter_values(condition: Dict[str, object], key: str) -> List[str]:
    filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    values = list_of_strings(filters.get(key))
    if values or key != "field":
        return values
    return [
        str(filter_key)
        for filter_key, value in filters.items()
        if str(filter_key or "") not in KNOWN_TARGET_FILTER_KEYS and value not in (None, "", [], {})
    ]

def condition_target_filter_bool(condition: Dict[str, object], key: str):
    filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    return bool_or_none(filters.get(key))

def condition_target_filter_number(condition: Dict[str, object], key: str):
    filters = condition.get("target_property_filters") if isinstance(condition.get("target_property_filters"), dict) else {}
    return number_or_none(filters.get(key))

def condition_relation_filter_values(condition: Dict[str, object], key: str) -> List[str]:
    filters = condition.get("relation_property_filters") if isinstance(condition.get("relation_property_filters"), dict) else {}
    return list_of_strings(filters.get(key))

def condition_relation_filter_bool(condition: Dict[str, object], key: str):
    filters = condition.get("relation_property_filters") if isinstance(condition.get("relation_property_filters"), dict) else {}
    return bool_or_none(filters.get(key))

def condition_relation_filter_number(condition: Dict[str, object], key: str):
    filters = condition.get("relation_property_filters") if isinstance(condition.get("relation_property_filters"), dict) else {}
    return number_or_none(filters.get(key))

def bool_or_none(value: object):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return None



class GraphStoreOntologyRowMapperMixin:
    def rows_for_entities(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for item in graph.entities:
            properties = item.properties or {}
            condition = properties.get("condition") if isinstance(properties.get("condition"), dict) else {}
            derivation = properties.get("derivation") if isinstance(properties.get("derivation"), dict) else {}
            rows.append({
                "id": item.entity_id,
                "label": item.label,
                "kind": item.kind,
                "ontologyBox": str(properties.get("ontologyBox") or "ABox"),
                "symbol": str(properties.get("symbol") or ""),
                "ruleId": str(properties.get("ruleId") or ""),
                "version": str(properties.get("version") or ""),
                "sourceKind": str(properties.get("sourceKind") or ""),
                "actionGroup": str(properties.get("actionGroup") or ""),
                "actionLevel": str(properties.get("actionLevel") or ""),
                "promptHint": str(properties.get("promptHint") or ""),
                "anyConditionMinCount": int(properties.get("anyConditionMinCount") or 1),
                "tboxClass": str(properties.get("tboxClass") or properties.get("className") or ""),
                "tboxClasses": list_of_strings(properties.get("tboxClasses")),
                "className": str(properties.get("className") or ""),
                "parentClass": str(properties.get("parentClass") or ""),
                "relationTypeName": str(properties.get("relationType") or ""),
                "boundedContext": str(properties.get("boundedContext") or ""),
                "box": str(properties.get("box") or properties.get("ontologyBox") or ""),
                "scope": str(properties.get("scope") or ""),
                "dataScope": str(properties.get("dataScope") or ""),
                "domainScope": str(properties.get("domainScope") or ""),
                "sourceContext": str(properties.get("sourceContext") or ""),
                "targetContext": str(properties.get("targetContext") or ""),
                "sourceValue": str(properties.get("source") or ""),
                "accountId": str(properties.get("accountId") or ""),
                "aboxSnapshotId": str(properties.get("aboxSnapshotId") or properties.get("snapshotId") or ""),
                "snapshotId": str(properties.get("snapshotId") or properties.get("aboxSnapshotId") or ""),
                "asOf": str(properties.get("asOf") or ""),
                "isCurrent": bool(properties.get("isCurrent")) if "isCurrent" in properties else False,
                "tboxVersion": str(properties.get("tboxVersion") or properties.get("version") or (default_tbox_metadata()["version"] if str(properties.get("ontologyBox") or "") in {"TBox", "RuleBox", "InferenceBox"} else "")),
                "activeTboxVersion": str(properties.get("activeTboxVersion") or properties.get("tboxVersion") or properties.get("version") or (default_tbox_metadata()["version"] if str(properties.get("ontologyBox") or "") in {"TBox", "RuleBox", "InferenceBox"} else "")),
                "tboxFingerprint": str(properties.get("tboxFingerprint") or (default_tbox_metadata()["fingerprint"] if str(properties.get("ontologyBox") or "") in {"TBox", "RuleBox", "InferenceBox"} else "")),
                "activeTboxSource": str(properties.get("activeTboxSource") or ""),
                "profitLossRate": number_or_none(properties.get("profitLossRate")),
                "levelType": str(properties.get("levelType") or ""),
                "field": str(properties.get("field") or ""),
                "valueNumber": number_or_none(properties.get("value")),
                "polarity": str(properties.get("polarity") or ""),
                "transitionType": str(properties.get("transitionType") or ""),
                "group": str(properties.get("group") or ""),
                "relationScope": str(properties.get("relationScope") or ""),
                "eventType": str(properties.get("eventType") or ""),
                "title": str(properties.get("title") or ""),
                "url": str(properties.get("url") or ""),
                "publishedAt": str(properties.get("publishedAt") or ""),
                "observedAt": str(properties.get("observedAt") or properties.get("updatedAt") or ""),
                "materialityScore": number_or_none(properties.get("materialityScore")),
                "materialityPassed": bool(properties.get("materialityPassed")) if "materialityPassed" in properties else None,
                "relevanceScore": number_or_none(properties.get("relevanceScore")),
                "sourceReliability": number_or_none(properties.get("sourceReliability")),
                "impactScore": number_or_none(properties.get("impactScore")),
                "confidence": number_or_none(properties.get("confidence")),
                "allowAddOnStrength": bool(properties.get("allowAddOnStrength")) if "allowAddOnStrength" in properties else None,
                "trimOnTrendBreak": bool(properties.get("trimOnTrendBreak")) if "trimOnTrendBreak" in properties else None,
                "avoidAveragingDown": bool(properties.get("avoidAveragingDown")) if "avoidAveragingDown" in properties else None,
                "impactPolarity": str(properties.get("impactPolarity") or ""),
                "needsReview": bool(properties.get("needsReview")) if "needsReview" in properties else None,
                "readScope": str(properties.get("readScope") or ""),
                "peRatio": number_or_none(properties.get("peRatio")),
                "beta": number_or_none(properties.get("beta")),
                **{
                    field: number_or_none(properties.get(field))
                    for field in PROMOTED_NUMERIC_ENTITY_FIELDS
                },
                **{
                    field: (
                        ", ".join(list_of_strings(properties.get(field)))
                        if isinstance(properties.get(field), list)
                        else "true" if properties.get(field) is True
                        else "false" if properties.get(field) is False
                        else str(properties.get(field) or "")
                    )
                    for field in PROMOTED_TEXT_ENTITY_FIELDS
                },
                "nativeTypeDbReasoned": bool(properties.get("nativeTypeDbReasoned")),
                "enabled": bool(properties.get("enabled")) if "enabled" in properties else False,
                "conditionId": str(properties.get("conditionId") or condition.get("condition_id") or ""),
                "conditionIndex": int(properties.get("conditionIndex") or 0),
                "conditionKind": str(condition.get("kind") or ""),
                "conditionField": str(condition.get("field") or ""),
                "conditionOperator": str(condition.get("operator") or ""),
                "conditionRole": str(condition.get("role") or "required"),
                "conditionValueString": str(condition.get("value") or ""),
                "conditionValueNumber": number_or_none(condition.get("value")),
                "conditionRelationType": str(condition.get("relation_type") or "").upper(),
                "conditionDirection": str(condition.get("direction") or "out"),
                "conditionTargetKind": str(condition.get("target_kind") or ""),
                "conditionTargetLevelTypes": condition_target_level_types(condition),
                "conditionTargetFields": condition_target_filter_values(condition, "field"),
                "conditionTargetTboxClasses": condition_target_filter_values(condition, "tboxClass") + condition_target_filter_values(condition, "tboxClasses"),
                "conditionTargetInstrumentArchetypes": condition_target_filter_values(condition, "instrumentArchetype") + condition_target_filter_values(condition, "instrumentArchetypes"),
                "conditionTargetFactors": condition_target_filter_values(condition, "factor"),
                "conditionTargetSensitivityLevels": condition_target_filter_values(condition, "sensitivityLevel"),
                "conditionTargetCryptoSymbols": condition_target_filter_values(condition, "cryptoSymbol"),
                "conditionTargetPositionIntents": condition_target_filter_values(condition, "positionIntent"),
                "conditionTargetGroups": condition_target_filter_values(condition, "group"),
                "conditionTargetScopes": condition_target_filter_values(condition, "scope"),
                "conditionTargetDataScopes": condition_target_filter_values(condition, "dataScope"),
                "conditionTargetDomainScopes": condition_target_filter_values(condition, "domainScope"),
                "conditionTargetRelationScopes": condition_target_filter_values(condition, "relationScope"),
                "conditionTargetEventTypes": condition_target_filter_values(condition, "eventType"),
                "conditionTargetPolarities": condition_target_filter_values(condition, "polarity"),
                "conditionTargetMaterialityPassed": condition_target_filter_bool(condition, "materialityPassed"),
                "conditionTargetMinMaterialityScore": condition_target_filter_number(condition, "minMaterialityScore"),
                "conditionTargetMinValue": condition_target_filter_number(condition, "minValue"),
                "conditionTargetMaxValue": condition_target_filter_number(condition, "maxValue"),
                "conditionRelationPolarities": condition_relation_filter_values(condition, "polarity"),
                "conditionRelationTransitionTypes": condition_relation_filter_values(condition, "transitionType"),
                "conditionRelationFields": condition_relation_filter_values(condition, "field"),
                "conditionRelationSignalGroups": condition_relation_filter_values(condition, "signalGroup"),
                "conditionRelationMaterialityPassed": condition_relation_filter_bool(condition, "materialityPassed"),
                "conditionRelationMinRiskImpact": condition_relation_filter_number(condition, "minRiskImpact"),
                "conditionRelationMinSupportImpact": condition_relation_filter_number(condition, "minSupportImpact"),
                "conditionMinWeight": float(condition.get("min_weight") or 0),
                "derivationRelationType": str(derivation.get("relation_type") or "").upper(),
                "derivationIndex": int(properties.get("derivationIndex") or 0),
                "derivationTargetKind": str(derivation.get("target_kind") or ""),
                "derivationTargetKey": str(derivation.get("target_key") or ""),
                "derivationTargetLabel": str(derivation.get("target_label") or ""),
                "derivationTboxClass": str(derivation.get("tbox_class") or ""),
                "derivationTboxClasses": list_of_strings(derivation.get("tbox_classes")),
                "derivationPolarity": str(derivation.get("polarity") or ""),
                "derivationRiskImpact": float(derivation.get("risk_impact") or 0),
                "derivationSupportImpact": float(derivation.get("support_impact") or 0),
                "derivationWeight": float(derivation.get("weight") or 0),
                "derivationBeliefLabel": str(derivation.get("belief_label") or ""),
                "derivationAiInfluenceLabel": str(derivation.get("ai_influence_label") or ""),
                "derivationActionGroup": str(derivation.get("action_group") or ""),
                "derivationActionLevel": str(derivation.get("action_level") or ""),
                "derivationDecisionStage": derivation_decision_stage(derivation),
                "derivationStagePriority": derivation_stage_priority(derivation),
                "derivationTargetRole": str(derivation.get("target_role") or derivation.get("targetRole") or ""),
                "derivationActionPolicy": str(derivation.get("action_policy") or derivation.get("actionPolicy") or ""),
                "derivationAllowedActions": list_of_strings(derivation.get("allowed_actions") or derivation.get("allowedActions")),
                "derivationBlockedActions": list_of_strings(derivation.get("blocked_actions") or derivation.get("blockedActions")),
                "propertiesJson": json.dumps(properties, ensure_ascii=False, sort_keys=True),
            })
        return rows

    def rows_for_relations(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for item in graph.relations:
            properties = dict(item.properties or {})
            symbol = str(properties.get("symbol") or symbol_from_graph_reference(item.source, item.target))
            if symbol:
                properties.setdefault("symbol", symbol)
            rows.append({
                "source": item.source,
                "target": item.target,
                "type": safe_relation_type(item.relation_type),
                "weight": float(item.weight or 0),
                "symbol": symbol,
                "ontologyBox": str(properties.get("ontologyBox") or "ABox"),
                "accountId": str(properties.get("accountId") or ""),
                "aboxSnapshotId": str(properties.get("aboxSnapshotId") or properties.get("snapshotId") or ""),
                "snapshotId": str(properties.get("snapshotId") or properties.get("aboxSnapshotId") or ""),
                "asOf": str(properties.get("asOf") or ""),
                "isCurrent": bool(properties.get("isCurrent")) if "isCurrent" in properties else False,
                "tboxVersion": str(properties.get("tboxVersion") or (default_tbox_metadata()["version"] if str(properties.get("ontologyBox") or "") in {"TBox", "RuleBox", "InferenceBox"} else "")),
                "activeTboxVersion": str(properties.get("activeTboxVersion") or properties.get("tboxVersion") or (default_tbox_metadata()["version"] if str(properties.get("ontologyBox") or "") in {"TBox", "RuleBox", "InferenceBox"} else "")),
                "tboxFingerprint": str(properties.get("tboxFingerprint") or (default_tbox_metadata()["fingerprint"] if str(properties.get("ontologyBox") or "") in {"TBox", "RuleBox", "InferenceBox"} else "")),
                "boundedContext": str(properties.get("boundedContext") or ""),
                "ruleId": str(properties.get("ruleId") or ""),
                "polarity": str(properties.get("polarity") or ""),
                "transitionType": str(properties.get("transitionType") or ""),
                "field": str(properties.get("field") or properties.get("observationField") or ""),
                "signalGroup": str(properties.get("signalGroup") or ""),
                "materialityPassed": bool(properties.get("materialityPassed")) if "materialityPassed" in properties else None,
                "materialityScore": number_or_none(properties.get("materialityScore")),
                "riskImpact": number_or_none(properties.get("riskImpact") or properties.get("opinionImpact")),
                "supportImpact": number_or_none(properties.get("supportImpact")),
                "decisionStage": str(properties.get("decisionStage") or ""),
                "stagePriority": number_or_none(properties.get("stagePriority")),
                "targetRole": str(properties.get("targetRole") or ""),
                "actionPolicy": str(properties.get("actionPolicy") or ""),
                "allowedActions": list_of_strings(properties.get("allowedActions")),
                "blockedActionCodes": list_of_strings(properties.get("blockedActionCodes")),
                "blockedActionPolicyCodes": list_of_strings(properties.get("blockedActions")),
                "aiInfluenceLabel": str(properties.get("aiInfluenceLabel") or ""),
                "nativeTypeDbReasoned": bool(properties.get("nativeTypeDbReasoned")),
                "evidenceIds": [str(value) for value in item.evidence_ids],
                "propertiesJson": json.dumps(properties, ensure_ascii=False, sort_keys=True),
            })
        return rows

    def rows_for_evidence(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": item.evidence_id,
                "subject": item.subject,
                "kind": item.kind,
                "source": item.source,
                "summary": item.summary,
                "ontologyBox": str((item.value or {}).get("ontologyBox") or ("InferenceBox" if item.kind == "inference-trace" else "ABox")),
                "accountId": str((item.value or {}).get("accountId") or ""),
                "aboxSnapshotId": str((item.value or {}).get("aboxSnapshotId") or (item.value or {}).get("snapshotId") or ""),
                "snapshotId": str((item.value or {}).get("snapshotId") or (item.value or {}).get("aboxSnapshotId") or ""),
                "asOf": str((item.value or {}).get("asOf") or ""),
                "isCurrent": bool((item.value or {}).get("isCurrent")) if "isCurrent" in (item.value or {}) else False,
                "tboxVersion": str((item.value or {}).get("tboxVersion") or ""),
                "valueJson": json.dumps(item.value or {}, ensure_ascii=False, sort_keys=True),
                "confidence": float(item.confidence or 0),
            }
            for item in graph.evidence
        ]

    def rows_for_beliefs(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": item.belief_id,
                "subject": item.subject,
                "label": item.label,
                "polarity": item.polarity,
                "confidence": float(item.confidence or 0),
                "ontologyBox": "InferenceBox" if str(item.belief_id or "").startswith("belief:inference:") else "ABox",
                "accountId": "",
                "aboxSnapshotId": "",
                "snapshotId": "",
                "asOf": "",
                "isCurrent": False,
                "tboxVersion": "",
                "evidenceIds": [str(value) for value in item.evidence_ids],
            }
            for item in graph.beliefs
        ]

    def rows_for_opinions(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": "opinion:" + item.symbol,
                "symbol": item.symbol,
                "action": item.action,
                "tone": item.tone,
                "conviction": float(item.conviction or 0),
                "ontologyPressure": float(item.ontology_pressure or 0),
                "ontologyBox": "ABox",
                "accountId": str((item.legacy_model or {}).get("accountId") or ""),
                "aboxSnapshotId": str((item.legacy_model or {}).get("aboxSnapshotId") or ""),
                "snapshotId": str((item.legacy_model or {}).get("snapshotId") or ""),
                "asOf": str((item.legacy_model or {}).get("asOf") or ""),
                "isCurrent": bool((item.legacy_model or {}).get("isCurrent")) if "isCurrent" in (item.legacy_model or {}) else False,
                "tboxVersion": str((item.legacy_model or {}).get("tboxVersion") or ""),
                "payloadJson": json.dumps(item.to_dict(), ensure_ascii=False, sort_keys=True),
            }
            for item in graph.opinions
        ]

    def rows_for_reasoning_cards(self, graph: PortfolioOntology) -> List[Dict[str, object]]:
        return [
            {
                "id": str(item.get("id") or ""),
                "symbol": str(item.get("symbol") or "").upper(),
                "companyName": str(item.get("companyName") or item.get("displayName") or item.get("symbol") or ""),
                "source": str(item.get("source") or ""),
                "portfolioRelation": str(item.get("portfolioRelation") or ""),
                "status": str(item.get("status") or ""),
                "ontologyBox": "ABox",
                "accountId": str(item.get("accountId") or ""),
                "aboxSnapshotId": str(item.get("aboxSnapshotId") or item.get("snapshotId") or ""),
                "snapshotId": str(item.get("snapshotId") or item.get("aboxSnapshotId") or ""),
                "asOf": str(item.get("asOf") or ""),
                "isCurrent": bool(item.get("isCurrent")) if "isCurrent" in item else False,
                "tboxVersion": str(item.get("tboxVersion") or ""),
                "payloadJson": json.dumps(item, ensure_ascii=False, sort_keys=True),
            }
            for item in (getattr(graph, "reasoning_cards", []) or [])
            if isinstance(item, dict) and item.get("id") and item.get("symbol")
        ]
