"""Lifecycle-aware changes to the inference-eligible evidence set.

Evidence rows are durable audit records.  A TypeDB generation, however,
must only receive evidence that is currently eligible for investment
inference.  This module describes the boundary between those two concerns
without making an investment judgement itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

from .fact_changes import fact_revision_id, fact_signature


ACTIVE_EVIDENCE_LIFECYCLE_STATES = {"active"}
EVIDENCE_DELTA_TRANSITIONS = {
    "added",
    "modified",
    "promotion",
    "demotion",
    "expiration",
    "retraction",
    "supersession",
    "reactivation",
}

# Provider polls and AI retries attach operational clocks to otherwise
# identical evidence.  Those clocks are useful for diagnostics and retention,
# but do not change the fact that TypeDB evaluates.  Keep the list normalized
# so snake_case and camelCase transports receive the same treatment.
VOLATILE_PAYLOAD_KEYS = {
    "age", "ageminutes", "ageseconds", "cacheage", "cacheageminutes",
    "checkedat", "collectedat", "collectionid", "collectionrunid",
    "elapsedminutes", "elapsedseconds", "externalcompletedat",
    "fetchduration", "fetchdurationms", "fetchedat", "firstseenat",
    "generatedat", "lastattemptat", "lastcheckedat", "lastexternalattemptat",
    "lastfetchedat", "lastpolledat", "lastseenat", "lastsuccessat",
    "latency", "latencyms", "nextrefreshat", "nextretryafterminutes",
    "observedat", "pollid", "refreshedat", "requestid", "retriedat",
    "retrycount", "runid", "sourcefetchedat", "sourceobservedat",
    "traceid", "updatedat",
}

# These fields alter the graph's relations, evidence quality, or policy
# admission. Presentation-only text such as a translated headline remains in
# the audit record, but cannot by itself cause a new investment inference.
INFERENCE_PAYLOAD_KEYS = {
    "articlefacts", "articlereadstatus", "bodyqualitypassed", "bodyqualitystate",
    "claimledger", "datastate", "dataqualityrisk", "dataqualityriskscore",
    "directmention", "evidencegovernance", "evidencerole", "eventtype",
    "excludedreason", "impactpolarity", "matchedaliases", "materialitypassed",
    "materialitystate", "mentionedpeers", "ontologyrelations", "entitylinks",
    "qualitygate", "readscope", "relationscope", "relevancestate",
    "sourcetexthash", "sourcekind", "sourceorigin", "sourceplatform",
    "sourcepublisher", "sourcereliability", "sourcetruststate", "stockimpact",
    "stockimpactlabel", "stockimpactpolarity", "stockimpactreasonko",
    "stockimpactscore", "topictags", "markettopics", "validationstate",
    "aianalysis", "analysisconflict", "analysisconflictaipolarity",
    "analysisconflictexistingpolarity", "analysisconflictreasonko",
    "analysisconflictsource", "correctionstate", "documenttype", "filingtype",
    "form", "receiptno", "reportname", "regulatoryeventtype",
    "promptevidenceadmission", "newseligibility", "officialdocumentstate",
    "documentverified", "analysisready", "storyidentityversion",
}

PRESENTATION_ONLY_PAYLOAD_KEYS = {
    "actionboundaryko", "analysissummary", "articlesummaryko", "articletextpreview",
    "normalizedsummary", "rationaleko", "translatedtitleko", "translationstatus",
    "validationreasonko",
}


def _normalized_key(value: object) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _stable_payload(value: object) -> object:
    """Drop operational refresh noise while retaining the persisted fact body."""
    if isinstance(value, Mapping):
        return {
            str(key): _stable_payload(candidate)
            for key, candidate in value.items()
            if _normalized_key(key) not in VOLATILE_PAYLOAD_KEYS
        }
    if isinstance(value, (list, tuple, set)):
        return [_stable_payload(candidate) for candidate in value]
    return value


def _semantic_payload(value: object) -> Dict[str, object]:
    """Return the graph-relevant portion of an evidence payload.

    The collector can refresh translations, summaries, and retry metadata many
    times.  The inference set must change only when an input that can affect
    the ABox/RuleBox changes.
    """
    source = value if isinstance(value, Mapping) else {}
    result: Dict[str, object] = {}
    for key, candidate in source.items():
        normalized = _normalized_key(key)
        if normalized not in INFERENCE_PAYLOAD_KEYS:
            continue
        stable = _stable_payload(candidate)
        if normalized == "aianalysis" and isinstance(stable, Mapping):
            stable = {
                str(nested_key): nested_value
                for nested_key, nested_value in stable.items()
                if _normalized_key(nested_key) not in PRESENTATION_ONLY_PAYLOAD_KEYS
            }
        if stable not in (None, "", [], {}):
            result[str(key)] = stable
    return result


def _normalized_title(evidence) -> str:
    payload = getattr(evidence, "raw_payload", {})
    payload = payload if isinstance(payload, Mapping) else {}
    value = payload.get("normalizedTitle") or getattr(evidence, "title", "") or ""
    return " ".join(str(value).casefold().split())


def clean_symbol(value: object) -> str:
    return str(value or "").strip().upper()


def clean_lifecycle_state(value: object, fallback: str = "active") -> str:
    state = str(value or fallback).strip().lower()
    return state or fallback


def evidence_is_active(lifecycle_state: object) -> bool:
    return clean_lifecycle_state(lifecycle_state) in ACTIVE_EVIDENCE_LIFECYCLE_STATES


def evidence_content_signature(evidence) -> str:
    """Return a stable signature for one evidence fact, excluding collection clocks."""
    if evidence is None:
        return ""
    payload = getattr(evidence, "raw_payload", {})
    payload = dict(payload or {}) if isinstance(payload, Mapping) else {}
    # Lifecycle is carried by the transition itself.  Its audit timestamp must
    # not make an unchanged active fact look like a new inference input.
    payload.pop("evidenceLifecycleState", None)
    payload.pop("evidenceLifecycleChangedAt", None)
    return fact_signature({
        "evidenceId": str(getattr(evidence, "evidence_id", "") or "").strip(),
        "symbol": clean_symbol(getattr(evidence, "symbol", "")),
        "kind": str(getattr(evidence, "kind", "") or "").strip(),
        "source": str(getattr(evidence, "source", "") or "").strip(),
        "title": str(getattr(evidence, "title", "") or "").strip(),
        "summary": str(getattr(evidence, "summary", "") or "").strip(),
        "url": str(getattr(evidence, "url", "") or "").strip(),
        # The storage contract normalizes an omitted publication timestamp to
        # the observation timestamp. Mirror that invariant before comparing an
        # incoming object with the persisted row.
        "publishedAt": str(
            getattr(evidence, "published_at", "")
            or getattr(evidence, "observed_at", "")
            or ""
        ).strip(),
        "polarity": str(getattr(evidence, "polarity", "context") or "context").strip(),
        "sourceTrustState": str(getattr(evidence, "source_trust_state", "unknown") or "unknown").strip(),
        "materialityState": str(getattr(evidence, "materiality_state", "context") or "context").strip(),
        "dataState": str(getattr(evidence, "data_state", "partial") or "partial").strip(),
        "validationState": str(getattr(evidence, "validation_state", "conditional") or "conditional").strip(),
        "payload": _stable_payload(payload),
    })


def evidence_story_key(evidence) -> str:
    """Return a provider-independent identity for one reported fact.

    The key intentionally excludes source and URL.  Syndicated reports often
    differ only by transport URL or publisher while representing one factual
    change.  Provenance remains in the durable evidence records and the ABox;
    this key only prevents duplicate scheduling work.
    """
    if evidence is None:
        return ""
    payload = getattr(evidence, "raw_payload", {})
    payload = payload if isinstance(payload, Mapping) else {}
    published = str(
        getattr(evidence, "published_at", "")
        or getattr(evidence, "observed_at", "")
        or ""
    ).strip()
    return fact_signature({
        "symbol": clean_symbol(getattr(evidence, "symbol", "")),
        "kind": str(getattr(evidence, "kind", "") or "").strip().lower(),
        "title": _normalized_title(evidence),
        # Providers frequently disagree on seconds/time zones for the same
        # article. The publication day is stable enough to collapse those
        # duplicate transports without combining separate dated updates.
        "publishedDay": published[:10],
        "eventType": str(payload.get("eventType") or "").strip().lower(),
        "fallbackEvidenceId": (
            str(getattr(evidence, "evidence_id", "") or "").strip()
            if not _normalized_title(evidence)
            else ""
        ),
    })


def evidence_inference_signature(evidence) -> str:
    """Return the stable, graph-relevant signature for one evidence item."""
    if evidence is None:
        return ""
    payload = getattr(evidence, "raw_payload", {})
    payload = dict(payload or {}) if isinstance(payload, Mapping) else {}
    payload.pop("evidenceLifecycleState", None)
    payload.pop("evidenceLifecycleChangedAt", None)
    return fact_signature({
        "storyKey": evidence_story_key(evidence),
        "symbol": clean_symbol(getattr(evidence, "symbol", "")),
        "kind": str(getattr(evidence, "kind", "") or "").strip(),
        "title": _normalized_title(evidence),
        "publishedAt": str(
            getattr(evidence, "published_at", "")
            or getattr(evidence, "observed_at", "")
            or ""
        ).strip()[:10],
        "polarity": str(getattr(evidence, "polarity", "context") or "context").strip(),
        "sourceTrustState": str(getattr(evidence, "source_trust_state", "unknown") or "unknown").strip(),
        "materialityState": str(getattr(evidence, "materiality_state", "context") or "context").strip(),
        "dataState": str(getattr(evidence, "data_state", "partial") or "partial").strip(),
        "validationState": str(getattr(evidence, "validation_state", "conditional") or "conditional").strip(),
        "payload": _semantic_payload(payload),
    })


def inference_eligible(evidence, lifecycle_state: object = "active", settings: Dict[str, object] = None) -> bool:
    """Whether an evidence item belongs to the active investment fact set.

    Materiality only decides admission to the inference fact set.  It does not
    determine the final investment action; direct TypeQL rules remain the
    only judgement path.
    """
    if evidence is None or not evidence_is_active(lifecycle_state):
        return False
    # Imported lazily to keep the lifecycle contract independent from the
    # evidence collection/application layer.
    from .materiality import evidence_materiality

    return bool(evidence_materiality(evidence, settings or {}).passed)


def eligible_evidence_set_revision(symbol: object, evidence_signatures: Iterable[object]) -> str:
    """Return the revision of the *current* eligible fact set for one symbol."""
    clean_signatures = sorted({str(value or "").strip() for value in evidence_signatures or [] if str(value or "").strip()})
    return fact_revision_id(
        "EligibleResearchEvidenceSet",
        clean_symbol(symbol),
        {"eligibleEvidenceSignatures": clean_signatures},
    )


@dataclass(frozen=True)
class EvidenceDelta:
    evidence_id: str
    symbol: str
    transition: str
    previous_lifecycle_state: str = ""
    lifecycle_state: str = "active"
    previous_eligible: bool = False
    eligible: bool = False
    previous_signature: str = ""
    signature: str = ""
    previous_inference_signature: str = ""
    inference_signature: str = ""
    story_key: str = ""
    occurred_at: str = ""
    reason: str = ""
    eligible_set_revision: str = ""
    eligible_set_changed: Optional[bool] = None

    @property
    def changes_inference_eligible_set(self) -> bool:
        if self.eligible_set_changed is not None:
            return bool(self.eligible_set_changed)
        if self.previous_eligible != self.eligible:
            return True
        return bool(self.eligible and self.previous_inference_signature != self.inference_signature)

    def with_eligible_set_revision(
        self,
        revision: str,
        eligible_set_changed: Optional[bool] = None,
    ) -> "EvidenceDelta":
        return EvidenceDelta(
            evidence_id=self.evidence_id,
            symbol=self.symbol,
            transition=self.transition,
            previous_lifecycle_state=self.previous_lifecycle_state,
            lifecycle_state=self.lifecycle_state,
            previous_eligible=self.previous_eligible,
            eligible=self.eligible,
            previous_signature=self.previous_signature,
            signature=self.signature,
            previous_inference_signature=self.previous_inference_signature,
            inference_signature=self.inference_signature,
            story_key=self.story_key,
            occurred_at=self.occurred_at,
            reason=self.reason,
            eligible_set_revision=str(revision or ""),
            eligible_set_changed=eligible_set_changed,
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "evidenceId": self.evidence_id,
            "symbol": self.symbol,
            "transition": self.transition,
            "previousLifecycleState": self.previous_lifecycle_state,
            "lifecycleState": self.lifecycle_state,
            "previousEligible": self.previous_eligible,
            "eligible": self.eligible,
            "previousSignature": self.previous_signature,
            "signature": self.signature,
            "previousInferenceSignature": self.previous_inference_signature,
            "inferenceSignature": self.inference_signature,
            "storyKey": self.story_key,
            "occurredAt": self.occurred_at,
            "reason": self.reason,
            "changesInferenceEligibleSet": self.changes_inference_eligible_set,
            "eligibleEvidenceSetRevision": self.eligible_set_revision,
            "factFamilies": ["evidence"],
        }


def evidence_delta(
    previous,
    current,
    *,
    previous_lifecycle_state: object = "",
    lifecycle_state: object = "active",
    transition: str = "",
    occurred_at: str = "",
    reason: str = "",
    settings: Dict[str, object] = None,
) -> EvidenceDelta:
    """Build a lifecycle transition while preserving the prior fact identity."""
    before_state = clean_lifecycle_state(previous_lifecycle_state or ("active" if previous is not None else ""), "")
    after_state = clean_lifecycle_state(lifecycle_state, "active")
    before_signature = evidence_content_signature(previous)
    signature = evidence_content_signature(current)
    before_inference_signature = evidence_inference_signature(previous)
    inference_signature = evidence_inference_signature(current)
    subject = current if current is not None else previous
    evidence_id = str(getattr(subject, "evidence_id", "") or "").strip()
    symbol = clean_symbol(getattr(subject, "symbol", ""))
    previous_eligible = inference_eligible(previous, before_state, settings) if previous is not None else False
    eligible = inference_eligible(current, after_state, settings) if current is not None else False

    if not transition:
        if previous is None and current is not None:
            transition = "added"
        elif previous is not None and current is None:
            transition = "retraction"
        elif before_state and before_state != "active" and after_state == "active":
            transition = "reactivation"
        elif not previous_eligible and eligible:
            transition = "promotion" if before_state else "added"
        elif previous_eligible and not eligible:
            transition = "demotion"
        else:
            transition = "modified"
    transition = str(transition or "modified").strip().lower()
    if transition not in EVIDENCE_DELTA_TRANSITIONS:
        transition = "modified"
    return EvidenceDelta(
        evidence_id=evidence_id,
        symbol=symbol,
        transition=transition,
        previous_lifecycle_state=before_state,
        lifecycle_state=after_state,
        previous_eligible=previous_eligible,
        eligible=eligible,
        previous_signature=before_signature,
        signature=signature,
        previous_inference_signature=before_inference_signature,
        inference_signature=inference_signature,
        story_key=evidence_story_key(subject),
        occurred_at=str(occurred_at or ""),
        reason=str(reason or ""),
    )


def eligible_set_revisions_for_deltas(
    deltas: Iterable[EvidenceDelta],
    eligible_signatures_by_symbol: Mapping[str, Iterable[object]],
) -> Dict[str, str]:
    affected_symbols = {
        clean_symbol(delta.symbol)
        for delta in deltas or []
        if clean_symbol(delta.symbol) and delta.changes_inference_eligible_set
    }
    return {
        symbol: eligible_evidence_set_revision(symbol, eligible_signatures_by_symbol.get(symbol) or [])
        for symbol in sorted(affected_symbols)
    }


@dataclass
class EvidenceMutation:
    """One transactional source mutation and its inference-relevant delta."""

    written_count: int = 0
    expired_count: int = 0
    retracted_count: int = 0
    changed_symbols: List[str] = field(default_factory=list)
    changed_items: List[object] = field(default_factory=list)
    deltas: List[EvidenceDelta] = field(default_factory=list)
    eligible_set_revisions: Dict[str, str] = field(default_factory=dict)
    previous_eligible_set_revisions: Dict[str, str] = field(default_factory=dict)
    inference_changed_symbols_override: Optional[List[str]] = None

    @property
    def lifecycle_changed_count(self) -> int:
        return int(self.expired_count or 0) + int(self.retracted_count or 0)

    @property
    def inference_changed_symbols(self) -> List[str]:
        if self.inference_changed_symbols_override is not None:
            return sorted({
                clean_symbol(symbol)
                for symbol in self.inference_changed_symbols_override
                if clean_symbol(symbol)
            })
        return sorted({
            clean_symbol(delta.symbol)
            for delta in self.deltas
            if clean_symbol(delta.symbol) and delta.changes_inference_eligible_set
        })

    def with_revisions(self) -> "EvidenceMutation":
        revisions = dict(self.eligible_set_revisions or {})
        changed_symbols = set(self.inference_changed_symbols)
        has_explicit_change_set = self.inference_changed_symbols_override is not None
        self.deltas = [
            delta.with_eligible_set_revision(
                revisions.get(clean_symbol(delta.symbol), ""),
                clean_symbol(delta.symbol) in changed_symbols if has_explicit_change_set else None,
            )
            for delta in self.deltas
        ]
        return self

    def to_dict(self) -> Dict[str, object]:
        return {
            "writtenCount": int(self.written_count or 0),
            "expiredCount": int(self.expired_count or 0),
            "retractedCount": int(self.retracted_count or 0),
            "changedSymbols": list(self.changed_symbols or []),
            "inferenceChangedSymbols": self.inference_changed_symbols,
            "inferenceChangedCount": len(self.inference_changed_symbols),
            "evidenceDeltas": [delta.to_dict() for delta in self.deltas],
            "factRevisionsBySymbol": dict(self.eligible_set_revisions or {}),
        }
