"""RuleBox-owned notification purposes; no market predicates or action scoring.

Older immutable releases already own typed claims, outcomes and falsification.
Their read adapter binds a purpose to that exact frozen claim. It never consults
the current catalog or rewrites a release. Explicit overrides travel inside the
claim contract and are validated before RuleBox persistence.
"""

from copy import deepcopy
from hashlib import sha256
import json


VERSION = "rule-notification-intent-v1"
PURPOSES = {
    "market-hypothesis": "hypothesis-evidence",
    "risk-invariant": "management-policy",
    "execution-feasibility": "execution-constraint",
    "data-reliability": "data-continuity",
    "causal-context": "context-evidence",
}
CHANGES = {"observed", "reactivated", "support-added", "support-removed",
           "counter-added", "counter-removed", "mixed-evidence", "invalidated",
           "policy-changed", "data-unavailable", "data-restored",
           "outcome-confirmed", "outcome-contradicted", "outcome-inconclusive"}


def intent_violations(value, claim):
    if not isinstance(value, dict):
        return ["notificationIntent must be an object"]
    errors = []
    if value.get("version") != VERSION:
        errors.append("notificationIntent version is unsupported")
    purpose = value.get("purpose")
    if not isinstance(purpose, str) or purpose not in {PURPOSES.get(claim.get("claimType")), "internal-only"}:
        errors.append("notificationIntent purpose must match its claim type")
    if not isinstance(value.get("customerQuestion"), str) or not value["customerQuestion"].strip():
        errors.append("notificationIntent requires a customerQuestion")
    notify = value.get("notifyOn")
    if not isinstance(notify, list) or any(not isinstance(item, str) or item not in CHANGES for item in notify):
        errors.append("notificationIntent notifyOn contains an unsupported change")
    elif purpose != "internal-only" and not notify:
        errors.append("notificationIntent requires an observable change")
    if value.get("comparisonBasis") != "last-successful-delivery":
        errors.append("notificationIntent requires a successful delivery baseline")
    if value.get("nextCheckSource") != "rule-claim-contract":
        errors.append("notificationIntent next checks must use the authored claim")
    return errors


def parse_notification_intent(claim):
    value = claim.get("notificationIntent", claim.get("notification_intent"))
    if value is not None and not isinstance(value, dict):
        raise ValueError("notificationIntent must be an object")
    return deepcopy(value)


def rule_notification_intent(claim):
    """Normalize only the claim captured with the native TypeDB proof."""
    claim = claim if isinstance(claim, dict) else {}
    purpose = PURPOSES.get(claim.get("claimType"))
    if not purpose or not all(claim.get(key) for key in (
        "ruleId", "claimContractId", "statement", "validationMode", "decisionAuthority",
    )):
        return {}
    from .rule_claim_contract import CLAIM_VALIDATION_MODES, CLAIM_DECISION_AUTHORITIES
    if (claim["validationMode"] != CLAIM_VALIDATION_MODES.get(claim["claimType"])
            or claim["decisionAuthority"] != CLAIM_DECISION_AUTHORITIES.get(claim["claimType"])):
        return {}
    if purpose == "hypothesis-evidence" and not all(claim.get(key) for key in (
        "expectedOutcome", "falsificationContract", "outcomeContract",
    )):
        return {}
    explicit = claim.get("notificationIntent")
    if explicit is not None:
        if intent_violations(explicit, claim):
            return {}
        contract = deepcopy(explicit)
    else:
        question = {
            "hypothesis-evidence": "이 가설을 지지하거나 반박하는 근거가 어떻게 달라졌나요?",
            "management-policy": "내 관리 기준의 적용 상태가 어떻게 달라졌나요?",
            "execution-constraint": "거래를 검토할 때 확인할 제약이 어떻게 달라졌나요?",
            "data-continuity": "앞서 알린 가설을 계속 확인할 자료가 있나요?",
            "context-evidence": "이 종목을 이해하는 데 필요한 새 근거는 무엇인가요?",
        }[purpose]
        contract = {"version": VERSION, "purpose": purpose, "customerQuestion": question,
                    "notifyOn": sorted(CHANGES), "comparisonBasis": "last-successful-delivery",
                    "nextCheckSource": "rule-claim-contract"}
    source = {key: deepcopy(claim.get(key)) for key in (
        "claimContractId", "ruleId", "claimType", "statement", "expectedOutcome",
        "falsificationContract", "outcomeContract", "ruleFingerprint", "version",
    )}
    fingerprint = sha256(json.dumps({"contract": contract, "claim": source}, sort_keys=True,
                                   ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {**contract, "claimContractId": claim["claimContractId"], "ruleId": claim["ruleId"],
            "source": "authored-rulebox" if explicit is not None else "frozen-rule-claim-adapter-v1",
            "claimFingerprint": fingerprint, "expectedOutcome": claim.get("expectedOutcome") or claim["statement"],
            "falsificationContract": claim.get("falsificationContract") or "",
            "outcomeContract": deepcopy(claim.get("outcomeContract") or {})}
