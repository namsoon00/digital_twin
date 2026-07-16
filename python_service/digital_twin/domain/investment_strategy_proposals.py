import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List

from .portfolio import utc_now_iso


STRATEGY_STATUS_PROPOSED = "proposed"
STRATEGY_STATUS_VALIDATED = "validated"
STRATEGY_STATUS_APPROVED = "approved"
STRATEGY_STATUS_DEPLOYED = "deployed"
STRATEGY_STATUS_RETIRED = "retired"

PROMOTABLE_STRATEGY_STATUSES = {
    STRATEGY_STATUS_PROPOSED,
    STRATEGY_STATUS_VALIDATED,
    STRATEGY_STATUS_APPROVED,
}


@dataclass
class InvestmentStrategyProposal:
    proposal_id: str
    title: str
    thesis: str
    symbols: List[str] = field(default_factory=list)
    target_universe: List[str] = field(default_factory=list)
    entry_conditions: List[str] = field(default_factory=list)
    exit_conditions: List[str] = field(default_factory=list)
    risk_controls: List[str] = field(default_factory=list)
    position_sizing: List[str] = field(default_factory=list)
    rebalance_policy: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    rule_ids: List[str] = field(default_factory=list)
    source_candidate_ids: List[str] = field(default_factory=list)
    source_experiment_id: str = ""
    source_trigger: str = ""
    rulebox_hash: str = ""
    inference_generation_id: str = ""
    status: str = STRATEGY_STATUS_PROPOSED
    validation: Dict[str, object] = field(default_factory=dict)
    lifecycle: Dict[str, object] = field(default_factory=dict)
    metadata: Dict[str, object] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    approved_at: str = ""
    deployed_at: str = ""
    retired_at: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["id"] = payload.pop("proposal_id")
        payload["targetUniverse"] = payload.pop("target_universe")
        payload["entryConditions"] = payload.pop("entry_conditions")
        payload["exitConditions"] = payload.pop("exit_conditions")
        payload["riskControls"] = payload.pop("risk_controls")
        payload["positionSizing"] = payload.pop("position_sizing")
        payload["rebalancePolicy"] = payload.pop("rebalance_policy")
        payload["evidenceRefs"] = payload.pop("evidence_refs")
        payload["ruleIds"] = payload.pop("rule_ids")
        payload["sourceCandidateIds"] = payload.pop("source_candidate_ids")
        payload["sourceExperimentId"] = payload.pop("source_experiment_id")
        payload["sourceTrigger"] = payload.pop("source_trigger")
        payload["ruleboxHash"] = payload.pop("rulebox_hash")
        payload["inferenceGenerationId"] = payload.pop("inference_generation_id")
        payload["createdAt"] = payload.pop("created_at")
        payload["updatedAt"] = payload.pop("updated_at")
        payload["approvedAt"] = payload.pop("approved_at")
        payload["deployedAt"] = payload.pop("deployed_at")
        payload["retiredAt"] = payload.pop("retired_at")
        payload["contract"] = "investment-strategy-proposal-v1"
        return payload

    @staticmethod
    def from_dict(payload: Dict[str, object]):
        payload = dict(payload or {})
        return InvestmentStrategyProposal(
            proposal_id=str(payload.get("id") or payload.get("proposalId") or payload.get("proposal_id") or ""),
            title=clean_text(payload.get("title") or "Investment strategy proposal"),
            thesis=clean_text(payload.get("thesis")),
            symbols=clean_list(payload.get("symbols")),
            target_universe=clean_list(payload.get("targetUniverse") or payload.get("target_universe")),
            entry_conditions=clean_list(payload.get("entryConditions") or payload.get("entry_conditions")),
            exit_conditions=clean_list(payload.get("exitConditions") or payload.get("exit_conditions")),
            risk_controls=clean_list(payload.get("riskControls") or payload.get("risk_controls")),
            position_sizing=clean_list(payload.get("positionSizing") or payload.get("position_sizing")),
            rebalance_policy=clean_list(payload.get("rebalancePolicy") or payload.get("rebalance_policy")),
            evidence_refs=clean_list(payload.get("evidenceRefs") or payload.get("evidence_refs")),
            rule_ids=clean_list(payload.get("ruleIds") or payload.get("rule_ids")),
            source_candidate_ids=clean_list(payload.get("sourceCandidateIds") or payload.get("source_candidate_ids")),
            source_experiment_id=clean_text(payload.get("sourceExperimentId") or payload.get("source_experiment_id")),
            source_trigger=clean_text(payload.get("sourceTrigger") or payload.get("source_trigger")),
            rulebox_hash=clean_text(payload.get("ruleboxHash") or payload.get("rulebox_hash")),
            inference_generation_id=clean_text(payload.get("inferenceGenerationId") or payload.get("inference_generation_id")),
            status=clean_text(payload.get("status") or STRATEGY_STATUS_PROPOSED, 64),
            validation=dict(payload.get("validation") or {}),
            lifecycle=dict(payload.get("lifecycle") or {}),
            metadata=dict(payload.get("metadata") or {}),
            created_at=clean_text(payload.get("createdAt") or payload.get("created_at")),
            updated_at=clean_text(payload.get("updatedAt") or payload.get("updated_at")),
            approved_at=clean_text(payload.get("approvedAt") or payload.get("approved_at")),
            deployed_at=clean_text(payload.get("deployedAt") or payload.get("deployed_at")),
            retired_at=clean_text(payload.get("retiredAt") or payload.get("retired_at")),
        )


def clean_text(value: object, limit: int = 1000) -> str:
    text = " ".join(str(value or "").split())
    if limit > 3 and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def clean_list(value: object, limit: int = 24) -> List[str]:
    values = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    result: List[str] = []
    seen = set()
    for item in values or []:
        text = clean_text(item, 260)
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
        if len(result) >= limit:
            break
    return result


def clean_symbols(values: Iterable[object]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").upper().strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def rule_id_from_payload(payload: Dict[str, object]) -> str:
    return clean_text((payload or {}).get("rule_id") or (payload or {}).get("ruleId"), 160)


def proposal_id_for(rule_id: str, candidate_id: str = "", symbols: Iterable[object] = None) -> str:
    seed = json.dumps(
        {
            "ruleId": rule_id,
            "candidateId": candidate_id,
            "symbols": clean_symbols(symbols or []),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "strategy-proposal-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def strategy_proposals_from_rule_candidates(
    candidates: Iterable[Dict[str, object]],
    context: Dict[str, object] = None,
) -> List[InvestmentStrategyProposal]:
    context = dict(context or {})
    symbols = clean_symbols(context.get("symbols") or [])
    proposals = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        proposal = strategy_proposal_from_rule_candidate(candidate, context, symbols)
        if proposal:
            proposals.append(proposal)
    return proposals


def strategy_proposal_from_rule_candidate(
    candidate: Dict[str, object],
    context: Dict[str, object],
    symbols: List[str],
) -> InvestmentStrategyProposal:
    proposed_rule = candidate.get("proposedRule") if isinstance(candidate.get("proposedRule"), dict) else {}
    rule_id = rule_id_from_payload(proposed_rule)
    if not rule_id:
        return None
    now = utc_now_iso()
    candidate_id = clean_text(candidate.get("id") or candidate.get("candidateId") or rule_id, 160)
    rulebox = context.get("ruleBox") if isinstance(context.get("ruleBox"), dict) else {}
    inferencebox = context.get("inferenceBox") if isinstance(context.get("inferenceBox"), dict) else {}
    return InvestmentStrategyProposal(
        proposal_id=proposal_id_for(rule_id, candidate_id, symbols),
        title=clean_text(candidate.get("title") or proposed_rule.get("label") or rule_id, 180),
        thesis=strategy_thesis(candidate, proposed_rule),
        symbols=symbols,
        target_universe=strategy_target_universe(candidate, proposed_rule, symbols),
        entry_conditions=strategy_entry_conditions(proposed_rule),
        exit_conditions=strategy_exit_conditions(proposed_rule),
        risk_controls=strategy_risk_controls(candidate, proposed_rule),
        position_sizing=strategy_position_sizing(proposed_rule),
        rebalance_policy=strategy_rebalance_policy(proposed_rule),
        evidence_refs=strategy_evidence_refs(candidate, context),
        rule_ids=[rule_id],
        source_candidate_ids=[candidate_id],
        source_trigger=clean_text(context.get("trigger") or candidate.get("trigger"), 80),
        rulebox_hash=clean_text(rulebox.get("ruleboxRulesHash") or rulebox.get("rulesHash"), 120),
        inference_generation_id=clean_text(inferencebox.get("inferenceGenerationId"), 120),
        status=STRATEGY_STATUS_PROPOSED,
        metadata={
            "source": "rule-change-candidate",
            "candidatePriority": candidate.get("priority"),
            "candidateStatus": candidate.get("status"),
            "expectedEffect": clean_text(candidate.get("expectedEffect"), 1000),
            "risk": clean_text(candidate.get("risk"), 1000),
            "proposedRule": proposed_rule,
        },
        created_at=now,
        updated_at=now,
    )


def strategy_thesis(candidate: Dict[str, object], rule: Dict[str, object]) -> str:
    parts = [
        clean_text(candidate.get("rationale"), 500),
        clean_text(candidate.get("expectedEffect"), 500),
        clean_text(rule.get("prompt_hint") or rule.get("promptHint"), 500),
    ]
    return " ".join(item for item in parts if item) or clean_text(rule.get("label") or "Rule-backed strategy proposal")


def strategy_target_universe(candidate: Dict[str, object], rule: Dict[str, object], symbols: List[str]) -> List[str]:
    values = list(symbols or [])
    source_kind = clean_text(rule.get("source_kind") or rule.get("sourceKind"), 120)
    if source_kind:
        values.append(source_kind)
    for item in candidate.get("requiresData") or []:
        if "universe" in str(item).lower() or "symbol" in str(item).lower():
            values.append(item)
    return clean_list(values, 24)


def strategy_entry_conditions(rule: Dict[str, object]) -> List[str]:
    return strategy_conditions_for(rule, {"entry", "buy", "addbuy", "opportunity", "support"})


def strategy_exit_conditions(rule: Dict[str, object]) -> List[str]:
    return strategy_conditions_for(rule, {"exit", "sell", "trim", "loss", "risk", "profit"})


def strategy_conditions_for(rule: Dict[str, object], keywords: set) -> List[str]:
    rows = []
    action_group = clean_text(rule.get("action_group") or rule.get("actionGroup"), 120).lower()
    if any(keyword in action_group for keyword in keywords):
        rows.extend(condition_descriptions(rule.get("conditions") or []))
    for derivation in rule.get("derivations") or []:
        if not isinstance(derivation, dict):
            continue
        blob = " ".join(str(derivation.get(key) or "") for key in [
            "relation_type",
            "relationType",
            "action_group",
            "actionGroup",
            "decision_stage",
            "decisionStage",
            "polarity",
            "target_label",
            "targetLabel",
        ]).lower()
        if any(keyword in blob for keyword in keywords):
            rows.append(clean_text(derivation.get("target_label") or derivation.get("targetLabel") or blob, 220))
    return clean_list(rows, 12)


def condition_descriptions(conditions: Iterable[Dict[str, object]]) -> List[str]:
    rows = []
    for condition in conditions or []:
        if not isinstance(condition, dict):
            continue
        description = clean_text(condition.get("description"), 220)
        field = clean_text(condition.get("field"), 80)
        operator = clean_text(condition.get("operator"), 16)
        if description:
            rows.append(description)
        elif field:
            rows.append(" ".join(item for item in [field, operator, clean_text(condition.get("value"), 80)] if item))
    return rows


def strategy_risk_controls(candidate: Dict[str, object], rule: Dict[str, object]) -> List[str]:
    rows = []
    if candidate.get("risk"):
        rows.append(candidate.get("risk"))
    rows.extend(strategy_conditions_for(rule, {"risk", "loss", "blocked", "avoid", "invalid"}))
    rows.extend(candidate.get("requiresData") or [])
    return clean_list(rows, 12)


def strategy_position_sizing(rule: Dict[str, object]) -> List[str]:
    rows = []
    for derivation in rule.get("derivations") or []:
        if not isinstance(derivation, dict):
            continue
        action = clean_text(derivation.get("action_level") or derivation.get("actionLevel"), 80)
        if action:
            rows.append("action_level=" + action)
    return clean_list(rows or ["position sizing requires account risk budget confirmation"], 8)


def strategy_rebalance_policy(rule: Dict[str, object]) -> List[str]:
    rows = []
    for derivation in rule.get("derivations") or []:
        if not isinstance(derivation, dict):
            continue
        stage = clean_text(derivation.get("decision_stage") or derivation.get("decisionStage"), 120)
        if stage:
            rows.append("decision_stage=" + stage)
    return clean_list(rows or ["rebalance after TypeDB InferenceBox validation"], 8)


def strategy_evidence_refs(candidate: Dict[str, object], context: Dict[str, object]) -> List[str]:
    refs = []
    refs.extend(candidate.get("requiresData") or [])
    for item in context.get("recentEvents") or []:
        if isinstance(item, dict):
            refs.append(item.get("eventId") or item.get("name") or item.get("aggregateId"))
    for item in context.get("alerts") or []:
        if isinstance(item, dict):
            refs.append(item.get("key") or item.get("rule") or item.get("title"))
    return clean_list(refs, 24)


def proposal_matches_rule_ids(proposal: InvestmentStrategyProposal, rule_ids: Iterable[object]) -> bool:
    targets = {str(item or "").strip() for item in rule_ids or [] if str(item or "").strip()}
    return bool(targets.intersection(set(proposal.rule_ids or [])))
