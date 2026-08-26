from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

from .data_freshness import parse_datetime


PROMPT_EVIDENCE_ADMISSION_VERSION = "prompt-evidence-admission-v1"
NEWS_KINDS = {"news"}
OFFICIAL_KINDS = {"disclosure", "filing", "sec-filing"}
DEFAULT_MAX_AGE_MINUTES = {
    "news": 3 * 24 * 60,
    "official": 7 * 24 * 60,
    "research": 7 * 24 * 60,
}


def _mapping(value: object) -> Dict[str, object]:
    return value if isinstance(value, dict) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _explicit_bool(*values: object):
    for value in values:
        if isinstance(value, bool):
            return value
        text = _text(value).lower()
        if text in {"1", "true", "yes", "on", "ready"}:
            return True
        if text in {"0", "false", "no", "off", "blocked"}:
            return False
    return None


def _reference_time(value: object) -> datetime:
    parsed = value if isinstance(value, datetime) else parse_datetime(value)
    current = parsed or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _source_time(payload: Dict[str, object], published_at: object, observed_at: object):
    for value in (
        published_at,
        payload.get("publishedAt"),
        payload.get("published_at"),
        observed_at,
        payload.get("observedAt"),
        payload.get("observed_at"),
        payload.get("receiptDate"),
    ):
        parsed = parse_datetime(value)
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc), _text(value)
    return None, ""


@dataclass(frozen=True)
class PromptEvidenceAdmission:
    usage: str
    display_eligible: bool
    alert_eligible: bool
    reference_eligible: bool
    decision_eligible: bool
    prompt_eligible: bool
    freshness_state: str
    age_minutes: float = None
    max_age_minutes: int = 0
    source_as_of: str = ""
    checked_at: str = ""
    reason_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "version": PROMPT_EVIDENCE_ADMISSION_VERSION,
            "usage": self.usage,
            "displayEligible": bool(self.display_eligible),
            "alertEligible": bool(self.alert_eligible),
            "referenceEligible": bool(self.reference_eligible),
            "decisionEligible": bool(self.decision_eligible),
            "promptEligible": bool(self.prompt_eligible),
            "freshnessState": self.freshness_state,
            "ageMinutes": None if self.age_minutes is None else round(self.age_minutes, 2),
            "maxAgeMinutes": int(self.max_age_minutes),
            "sourceAsOf": self.source_as_of,
            "checkedAt": self.checked_at,
            "reasonCodes": list(self.reason_codes),
        }


def assess_prompt_evidence(
    payload: Dict[str, object],
    *,
    kind: object = "",
    published_at: object = "",
    observed_at: object = "",
    now: object = None,
    directly_linked: bool = False,
) -> PromptEvidenceAdmission:
    """Classify one evidence row at the last boundary before an AI prompt.

    Collection, display, notification and investment reasoning have different
    requirements. This contract preserves those differences while ensuring a
    stale or unverified row cannot re-enter the final decision prompt through
    a compatibility read model.
    """

    row = dict(payload or {})
    evidence_kind = _text(kind or row.get("kind") or "research").lower()
    kind_group = "news" if evidence_kind in NEWS_KINDS else "official" if evidence_kind in OFFICIAL_KINDS else "research"
    max_age = DEFAULT_MAX_AGE_MINUTES[kind_group]
    current = _reference_time(now)
    source_time, source_as_of = _source_time(row, published_at, observed_at)
    reasons: List[str] = []
    if source_time is None:
        freshness_state = "unknown"
        age = None
        reasons.append("source-time-missing")
    else:
        age = (current - source_time).total_seconds() / 60.0
        if age < -10:
            freshness_state = "future"
            reasons.append("source-time-in-future")
        elif age > max_age:
            freshness_state = "stale"
            reasons.append("evidence-stale")
        else:
            freshness_state = "fresh"
            age = max(0.0, age)

    lifecycle = _text(row.get("evidenceLifecycleState") or row.get("lifecycleState") or "active").lower()
    active = lifecycle == "active"
    if not active:
        reasons.append("lifecycle-inactive")

    governance = _mapping(row.get("evidenceGovernance"))
    news_eligibility = _mapping(row.get("newsEligibility"))
    analysis = _mapping(row.get("aiAnalysis"))
    governance_eligible = _explicit_bool(
        governance.get("investmentJudgmentEligible"),
        row.get("investmentJudgmentEligible"),
    ) is True
    validation = _text(row.get("validationState") or analysis.get("validationState") or governance.get("validationState")).lower()
    data_state = _text(row.get("dataState") or analysis.get("dataState") or governance.get("dataState")).lower()
    fresh = freshness_state == "fresh"

    display_eligible = active
    alert_eligible = False
    prompt_ready = False

    if kind_group == "news":
        display_state = _explicit_bool(news_eligibility.get("displayEligible"), row.get("displayEligible"))
        alert_state = _explicit_bool(news_eligibility.get("alertEligible"), row.get("alertEligible"))
        reasoning_state = _explicit_bool(news_eligibility.get("reasoningEligible"), row.get("reasoningEligible"))
        inline_state = _explicit_bool(
            analysis.get("decisionInlineEligible"),
            row.get("decisionInlineEligible"),
        )
        if display_state is not None:
            display_eligible = active and display_state
        alert_eligible = bool(active and fresh and alert_state is True)
        if reasoning_state is not True:
            reasons.append("news-reasoning-not-eligible")
        if inline_state is not True:
            reasons.append("news-decision-inline-not-eligible")
        if not governance_eligible:
            reasons.append("claim-governance-not-eligible")
        prompt_ready = bool(
            active
            and fresh
            and reasoning_state is True
            and inline_state is True
            and governance_eligible
            and validation not in {"blocked", ""}
            and data_state not in {"insufficient", "unavailable", ""}
        )
    elif kind_group == "official":
        document_verified = _explicit_bool(row.get("documentVerified")) is True
        analysis_ready = _explicit_bool(row.get("analysisReady")) is True
        document_hash = _text(row.get("documentHash"))
        document_state = _text(row.get("officialDocumentState")).lower()
        metadata_only = (
            document_state != "document-verified"
            or not document_verified
            or not analysis_ready
            or not document_hash
        )
        alert_eligible = bool(active and fresh and _text(row.get("title") or row.get("reportName")))
        if metadata_only:
            reasons.append("official-document-not-verified")
        if not governance_eligible:
            reasons.append("claim-governance-not-eligible")
        prompt_ready = bool(
            active
            and fresh
            and not metadata_only
            and governance_eligible
            and validation not in {"blocked", ""}
            and data_state not in {"insufficient", "unavailable", ""}
        )
    else:
        alert_eligible = False
        if not governance_eligible:
            reasons.append("claim-governance-not-eligible")
        prompt_ready = bool(
            active
            and fresh
            and governance_eligible
            and validation not in {"blocked", ""}
            and data_state not in {"insufficient", "unavailable", ""}
        )

    reference_eligible = prompt_ready
    decision_eligible = prompt_ready
    prompt_eligible = prompt_ready
    if prompt_ready:
        usage = "decision" if directly_linked else "reference"
    elif alert_eligible:
        usage = "alert"
    elif display_eligible:
        usage = "display"
    else:
        usage = "blocked"
    return PromptEvidenceAdmission(
        usage=usage,
        display_eligible=display_eligible,
        alert_eligible=alert_eligible,
        reference_eligible=reference_eligible,
        decision_eligible=decision_eligible,
        prompt_eligible=prompt_eligible,
        freshness_state=freshness_state,
        age_minutes=age,
        max_age_minutes=max_age,
        source_as_of=source_as_of,
        checked_at=current.isoformat(),
        reason_codes=list(dict.fromkeys(reasons)),
    )


def attach_prompt_evidence_admission(
    payload: Dict[str, object],
    **context: object,
) -> Dict[str, object]:
    result = dict(payload or {})
    result["promptEvidenceAdmission"] = assess_prompt_evidence(result, **context).to_dict()
    return result
