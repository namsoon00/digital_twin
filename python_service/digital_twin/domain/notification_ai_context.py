import re
from typing import Dict, List

from .ontology_rulebox_contracts import WATCHLIST_ACTION_POLICY


def context_raw_lines(context: Dict[str, object]) -> List[str]:
    raw = context.get("rawLines") if isinstance(context, dict) else ""
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    if raw:
        return [line.strip() for line in str(raw or "").splitlines() if line.strip()]
    lines = context.get("lines") if isinstance(context, dict) else ""
    if isinstance(lines, list):
        return [str(item or "").strip() for item in lines if str(item or "").strip()]
    return [
        line.strip().lstrip("-").strip()
        for line in str(lines or "").splitlines()
        if line.strip()
    ]


def criterion_lines(context: Dict[str, object]) -> List[str]:
    raw = context.get("criterionLines") if isinstance(context, dict) else ""
    if isinstance(raw, list):
        return [str(item or "").strip() for item in raw if str(item or "").strip()]
    return [line.strip() for line in str(raw or "").splitlines() if line.strip()]


def line_value(lines: List[str], label: str) -> str:
    prefix = str(label or "").strip()
    if not prefix:
        return ""
    for index, raw in enumerate(lines):
        line = str(raw or "").strip()
        if line.startswith(prefix + ":"):
            value = line.split(":", 1)[1].strip()
            if prefix == "투자자":
                rows = [value] if value else []
                for next_line in lines[index + 1 :]:
                    stripped = str(next_line or "").strip()
                    if stripped.startswith(("외국인:", "기관:", "개인:")):
                        rows.append(stripped)
                        continue
                    break
                return " / ".join(row for row in rows if row)
            return value
        if line.startswith(prefix + " "):
            return line[len(prefix):].strip()
    return ""


def split_label_value_text(line: object):
    label, _, value = str(line or "").partition(":")
    return label.strip(), value.strip()


def normalized_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def first_line_with(lines: List[str], *labels: str) -> str:
    for label in labels:
        value = line_value(lines, label)
        if value:
            return label + " " + value
    for line in lines:
        if str(line or "").strip():
            return str(line).strip()
    return ""


def relation_labels(context: Dict[str, object]) -> List[str]:
    relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    if not relation_context:
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        relation_context = metadata.get("ontologyRelationContext") if isinstance(metadata.get("ontologyRelationContext"), dict) else {}
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    labels: List[str] = []
    for item in rules:
        if not isinstance(item, dict) or item.get("referenceOnly") or item.get("reference_only"):
            continue
        label = str(item.get("label") or item.get("ruleId") or item.get("rule_id") or "").strip()
        if label:
            labels.append(label)
    return labels


def missing_data_labels(context: Dict[str, object]) -> List[str]:
    relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    if not relation_context:
        metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
        relation_context = metadata.get("ontologyRelationContext") if isinstance(metadata.get("ontologyRelationContext"), dict) else {}
    missing = relation_context.get("missingData") if isinstance(relation_context, dict) else []
    labels: List[str] = []
    if isinstance(missing, list):
        for item in missing:
            if isinstance(item, dict):
                label = str(item.get("label") or item.get("key") or "").strip()
            else:
                label = str(item or "").strip()
            if label:
                labels.append(label)
    return labels


def relation_context_value(context: Dict[str, object]) -> Dict[str, object]:
    relation_context = context.get("ontologyRelationContext") if isinstance(context.get("ontologyRelationContext"), dict) else {}
    if relation_context:
        return relation_context
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    relation_context = metadata.get("ontologyRelationContext") if isinstance(metadata.get("ontologyRelationContext"), dict) else {}
    return relation_context if isinstance(relation_context, dict) else {}


def source_signal_types(context: Dict[str, object]) -> List[str]:
    if not isinstance(context, dict):
        return []
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    ontology_insight = context.get("ontologyInsight") if isinstance(context.get("ontologyInsight"), dict) else {}
    metadata_insight = metadata.get("ontologyInsight") if isinstance(metadata.get("ontologyInsight"), dict) else {}
    values: List[str] = []
    for container in [context, metadata, ontology_insight, metadata_insight]:
        raw = container.get("sourceSignalTypes") if isinstance(container, dict) else []
        if isinstance(raw, list):
            values.extend(str(item or "").strip() for item in raw)
        elif str(raw or "").strip():
            values.extend(part.strip() for part in str(raw or "").replace("\n", ",").split(","))
    for event in source_alert_event_items(context):
        rule = str(event.get("rule") or "").strip()
        if rule:
            values.append(rule)
        event_metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        raw = event_metadata.get("sourceSignalTypes") if isinstance(event_metadata, dict) else []
        if isinstance(raw, list):
            values.extend(str(item or "").strip() for item in raw)
    return [item for item in values if item]


def is_watchlist_context(context: Dict[str, object]) -> bool:
    relation_context = relation_context_value(context or {})
    facts = relation_context.get("facts") if isinstance(relation_context.get("facts"), dict) else {}
    if facts.get("isWatchlist") is True:
        return True
    if str(facts.get("source") or "").strip().lower() == "watchlist":
        return True
    if str(relation_context.get("targetRole") or relation_context.get("sourceRole") or "").strip().lower() == "watchlist":
        return True
    if str(relation_context.get("actionPolicy") or "").strip() == WATCHLIST_ACTION_POLICY:
        return True
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    plan = relation_context.get("executionPlan") if isinstance(relation_context.get("executionPlan"), dict) else {}
    if str(decision.get("actionPolicy") or plan.get("actionPolicy") or "").strip() == WATCHLIST_ACTION_POLICY:
        return True
    insight = (context or {}).get("ontologyInsight") if isinstance((context or {}).get("ontologyInsight"), dict) else {}
    metadata = (context or {}).get("metadata") if isinstance((context or {}).get("metadata"), dict) else {}
    metadata_insight = metadata.get("ontologyInsight") if isinstance(metadata.get("ontologyInsight"), dict) else {}
    for container in [context or {}, metadata, insight, metadata_insight]:
        if str((container or {}).get("portfolioBucket") or "").strip() == "관심":
            return True
        if str((container or {}).get("positionRole") or (container or {}).get("targetPositionRole") or "").strip().lower() == "watchlist":
            return True
    signal_types = {item for item in source_signal_types(context or {})}
    if "watchlistOntologySignal" in signal_types and "holdingTiming" not in signal_types:
        return True
    return False


def target_position_role(context: Dict[str, object]) -> str:
    return "watchlist" if is_watchlist_context(context or {}) else "holding"


def is_graph_backed_relation_context(relation_context: Dict[str, object]) -> bool:
    if not isinstance(relation_context, dict) or not relation_context:
        return False
    decision = relation_context.get("decision") if isinstance(relation_context.get("decision"), dict) else {}
    source = str(relation_context.get("source") or "")
    basis = str(decision.get("basis") or "")
    return (
        bool(relation_context.get("graphStoreUsed"))
        and not bool(relation_context.get("fallbackUsed"))
        and is_graph_store_inference_source(source)
        and is_graph_store_inference_source(basis)
    )


def is_graph_store_inference_source(value: str) -> bool:
    return str(value or "") in {"typedbInferenceBox", "graphStoreInferenceBox"}


def has_graph_backed_relation_context(context: Dict[str, object]) -> bool:
    return is_graph_backed_relation_context(relation_context_value(context or {}))


def source_alert_event_items(context: Dict[str, object]) -> List[Dict[str, object]]:
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    raw_items = metadata.get("sourceAlertEvents") or context.get("sourceAlertEvents") or []
    if not isinstance(raw_items, list):
        return []
    return [item for item in raw_items if isinstance(item, dict)]


def active_investment_opinion_value(context: Dict[str, object]) -> Dict[str, object]:
    if not isinstance(context, dict):
        return {}
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    containers = [
        context,
        metadata,
        context.get("ontologyReviewContext") if isinstance(context.get("ontologyReviewContext"), dict) else {},
        metadata.get("ontologyReviewContext") if isinstance(metadata.get("ontologyReviewContext"), dict) else {},
        context.get("aiContext") if isinstance(context.get("aiContext"), dict) else {},
        metadata.get("aiContext") if isinstance(metadata.get("aiContext"), dict) else {},
    ]
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("activeInvestmentOpinion", "active_investment_opinion"):
            value = container.get(key)
            if isinstance(value, dict) and value:
                return value
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and item:
                        return item
    for event in source_alert_event_items(context):
        event_metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        for container in (event, event_metadata):
            value = container.get("activeInvestmentOpinion") if isinstance(container, dict) else {}
            if isinstance(value, dict) and value:
                return value
    return {}


def execution_plan_value(context: Dict[str, object]) -> Dict[str, object]:
    opinion = active_investment_opinion_value(context)
    if isinstance(opinion.get("executionPlan"), dict) and opinion.get("executionPlan"):
        return dict(opinion.get("executionPlan") or {})
    relation_context = relation_context_value(context)
    plan = relation_context.get("executionPlan") if isinstance(relation_context, dict) else {}
    if isinstance(plan, dict) and plan:
        return dict(plan)
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    plan = metadata.get("executionPlan") if isinstance(metadata.get("executionPlan"), dict) else {}
    if plan:
        return dict(plan)
    for event in source_alert_event_items(context):
        event_metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        for container in (event, event_metadata):
            plan = container.get("executionPlan") if isinstance(container, dict) and isinstance(container.get("executionPlan"), dict) else {}
            if plan:
                return dict(plan)
            nested_opinion = container.get("activeInvestmentOpinion") if isinstance(container, dict) and isinstance(container.get("activeInvestmentOpinion"), dict) else {}
            plan = nested_opinion.get("executionPlan") if isinstance(nested_opinion.get("executionPlan"), dict) else {}
            if plan:
                return dict(plan)
    return {}


def relation_facts(context: Dict[str, object]) -> Dict[str, object]:
    facts = relation_context_value(context).get("facts")
    return facts if isinstance(facts, dict) else {}


def relation_trend_dynamics(context: Dict[str, object]) -> Dict[str, object]:
    facts = relation_facts(context)
    dynamics = facts.get("trendDynamics") if isinstance(facts.get("trendDynamics"), dict) else {}
    return dynamics if isinstance(dynamics, dict) else {}


def active_rule_items(context: Dict[str, object]) -> List[Dict[str, object]]:
    relation_context = relation_context_value(context)
    rules = relation_context.get("activeRules") or relation_context.get("matchedRules") or []
    return [
        item for item in rules
        if isinstance(item, dict) and not item.get("referenceOnly") and not item.get("reference_only")
    ]


def active_rule_evidence(context: Dict[str, object], limit: int = 5) -> List[str]:
    evidence: List[str] = []
    for item in active_rule_items(context):
        raw = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        for value in raw:
            text = str(value or "").strip()
            if text and text not in evidence:
                evidence.append(text)
            if len(evidence) >= limit:
                return evidence
    return evidence


def disclosure_context(context: Dict[str, object]) -> Dict[str, object]:
    facts = relation_facts(context)
    disclosure = facts.get("dartDisclosure") if isinstance(facts.get("dartDisclosure"), dict) else {}
    if disclosure:
        return disclosure
    metadata = context.get("metadata") if isinstance(context.get("metadata"), dict) else {}
    for key in ["dartDisclosure", "disclosure"]:
        value = metadata.get(key) if isinstance(metadata.get(key), dict) else {}
        if value:
            return value
    return {}
