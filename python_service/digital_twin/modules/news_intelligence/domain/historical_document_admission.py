"""Dated original-source references do not change current investment admission."""


def age_only_rejection(raw):
    governance = raw.get("evidenceGovernance") or {}
    return (governance.get("verificationStatus") == "rejected"
            and governance.get("claimState") == "expired"
            and set(governance.get("reasons") or []) == {"evidence-stale"}
            and governance.get("sourceTrustState") == "trusted"
            and governance.get("validationState") == "ready")


def current_or_dated_source(raw):
    governance = raw.get("evidenceGovernance") or {}
    return ((governance.get("verificationStatus") == "verified-primary"
             and governance.get("investmentJudgmentEligible") is True)
            or age_only_rejection(raw))


def allowed_reference_reasons(raw):
    return {"evidence-stale", "claim-governance-not-eligible"} if age_only_rejection(raw) else {"evidence-stale"}
