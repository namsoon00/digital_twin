import json
import re
from difflib import SequenceMatcher
from typing import Dict, Iterable, List

from ..domain.provenance import annotate_source_provenance, normalized_content
from ..domain.story import event_cluster_identity, same_story_event, story_event_features, story_identity


def _payload(item) -> Dict[str, object]:
    value = getattr(item, "raw_payload", {})
    return dict(value or {}) if isinstance(value, dict) else {}


def _context(item, registry: object = "") -> Dict[str, object]:
    payload = _payload(item)
    return {
        "title": getattr(item, "title", ""),
        "summary": getattr(item, "summary", ""),
        "source": getattr(item, "source", ""),
        "provider": payload.get("provider") or "",
        "url": getattr(item, "url", ""),
        "published_at": getattr(item, "published_at", "") or getattr(item, "observed_at", ""),
        "registry": registry,
    }


def _item_story_identity(item) -> str:
    payload = _payload(item)
    context = {
        "symbol": getattr(item, "symbol", ""),
        "title": getattr(item, "title", ""),
        "publishedAt": getattr(item, "published_at", "") or getattr(item, "observed_at", ""),
        "url": getattr(item, "url", ""),
        "payload": payload,
    }
    return event_cluster_identity(context) or story_identity(context)


def _story_context(item) -> Dict[str, object]:
    return {
        "symbol": getattr(item, "symbol", ""),
        "title": getattr(item, "title", ""),
        "publishedAt": getattr(item, "published_at", "") or getattr(item, "observed_at", ""),
        "payload": _payload(item),
    }


def _body_tokens(item) -> set:
    payload = _payload(item)
    facts = payload.get("articleFacts") if isinstance(payload.get("articleFacts"), dict) else {}
    text = payload.get("articleText") or facts.get("bodyText") or facts.get("bodyPreview") or getattr(item, "summary", "")
    return set(normalized_content(text).split())


def _body_similarity(left, right) -> float:
    left_tokens = _body_tokens(left)
    right_tokens = _body_tokens(right)
    if len(left_tokens) < 20 or len(right_tokens) < 20:
        return 0.0
    return len(left_tokens.intersection(right_tokens)) / max(1, len(left_tokens.union(right_tokens)))


def _title_similarity(left, right) -> float:
    left_title = normalized_content(getattr(left, "title", ""))
    right_title = normalized_content(getattr(right, "title", ""))
    if len(left_title) < 20 or len(right_title) < 20:
        return 0.0
    return SequenceMatcher(None, left_title, right_title).ratio()


def _ordered(items: Iterable[object]) -> List[object]:
    return sorted(
        list(items or []),
        key=lambda item: (
            str(getattr(item, "published_at", "") or getattr(item, "observed_at", "") or ""),
            str(getattr(item, "evidence_id", "") or ""),
        ),
    )


def normalize_evidence_sources(items: Iterable[object], source_registry: object = "") -> List[object]:
    """Normalize provenance and classify copies without importing legacy types."""
    rows = _ordered(items)
    for item in rows:
        item.raw_payload = annotate_source_provenance(_payload(item), **_context(item, source_registry))
        payload = _payload(item)
        payload["storyClusterId"] = _item_story_identity(item)
        item.raw_payload = payload

    roots: List[object] = []
    for item in rows:
        payload = _payload(item)
        relationship = "original"
        root = item
        corroborating = set()
        governance = payload.get("evidenceGovernance") if isinstance(payload.get("evidenceGovernance"), dict) else {}
        corroborating.update(str(value or "") for value in governance.get("corroboratingEvidenceIds") or [])
        for candidate in roots:
            other = _payload(candidate)
            same_url = bool(payload.get("documentIdentity") and payload.get("documentIdentity") == other.get("documentIdentity"))
            same_content = bool(payload.get("articleBodyFingerprint") and payload.get("articleBodyFingerprint") == other.get("articleBodyFingerprint"))
            near_copy = _title_similarity(item, candidate) >= 0.9 and _body_similarity(item, candidate) >= 0.82
            if same_url:
                relationship, root = "exact-duplicate", candidate
                break
            if same_content or near_copy:
                relationship, root = "syndicated-copy", candidate
                break
            if str(getattr(candidate, "evidence_id", "")) in corroborating:
                relationship, root = "independent-confirmation", candidate
                break
            same_event = bool(
                payload.get("storyClusterId")
                and payload.get("storyClusterId") == other.get("storyClusterId")
            ) or same_story_event(_story_context(item), _story_context(candidate))
            if same_event:
                current_origin = str(payload.get("sourceOrigin") or "")
                candidate_origin = str(other.get("sourceOrigin") or "")
                relationship = "independent-confirmation" if current_origin and candidate_origin and current_origin != candidate_origin else "same-story"
                current_numbers = set(story_event_features(_story_context(item)).get("numbers") or [])
                candidate_numbers = set(story_event_features(_story_context(candidate)).get("numbers") or [])
                if current_numbers.difference(candidate_numbers):
                    relationship = "follow-up"
                    payload["storyUpdate"] = True
                root = candidate
                break
        if relationship == "original":
            roots.append(item)
        elif relationship in {"same-story", "independent-confirmation", "follow-up"}:
            roots.append(item)
        if payload.get("storyUpdate"):
            relationship = "follow-up"
        story_root_id = str(getattr(root, "evidence_id", "") or "")
        root_id = (
            story_root_id
            if relationship in {"exact-duplicate", "syndicated-copy"}
            else str(getattr(item, "evidence_id", "") or "")
        )
        if relationship in {"exact-duplicate", "syndicated-copy", "same-story", "independent-confirmation", "follow-up"}:
            root_payload = _payload(root)
            if root_payload.get("storyClusterId"):
                payload["storyClusterId"] = root_payload.get("storyClusterId")
        provenance = dict(payload.get("sourceProvenance") or {})
        if relationship in {"exact-duplicate", "syndicated-copy"}:
            provenance["syndicationState"] = relationship
            payload["syndicationState"] = relationship
        provenance["evidenceRelationship"] = relationship
        provenance["syndicationRootEvidenceId"] = root_id
        root_provenance = _payload(root).get("sourceProvenance") if isinstance(_payload(root).get("sourceProvenance"), dict) else {}
        provenance["storyRootEvidenceId"] = str(root_provenance.get("storyRootEvidenceId") or story_root_id)
        provenance["independentEvidenceKey"] = (
            root_id if relationship in {"exact-duplicate", "syndicated-copy"}
            else str(payload.get("sourceOrigin") or "unknown") + "|" + str(payload.get("documentIdentity") or getattr(item, "evidence_id", ""))
        )
        payload["sourceProvenance"] = provenance
        payload["evidenceRelationship"] = relationship
        payload["syndicationRootEvidenceId"] = root_id
        payload["storyRootEvidenceId"] = story_root_id
        governance = dict(payload.get("evidenceGovernance") or {}) if isinstance(payload.get("evidenceGovernance"), dict) else {}
        if governance:
            governance.update({
                "sourcePublisher": payload.get("sourcePublisher"),
                "sourceOrigin": payload.get("sourceOrigin"),
                "canonicalUrl": payload.get("articleCanonicalUrl"),
                "publisherIdentity": payload.get("publisherIdentity"),
            })
            if relationship in {"exact-duplicate", "syndicated-copy"}:
                governance["investmentJudgmentEligible"] = False
                governance["reasons"] = list(dict.fromkeys(list(governance.get("reasons") or []) + ["syndicated-duplicate"]))
            payload["evidenceGovernance"] = governance
        claim_ledger = dict(payload.get("claimLedger") or {}) if isinstance(payload.get("claimLedger"), dict) else {}
        claims = list(claim_ledger.get("claims") or [])
        if claims:
            normalized_claims = []
            for raw_claim in claims:
                claim = dict(raw_claim) if isinstance(raw_claim, dict) else {}
                claim.update({
                    "source": payload.get("sourcePublisher"),
                    "sourceOrigin": payload.get("sourceOrigin"),
                    "publisherIdentity": payload.get("publisherIdentity"),
                    "canonicalUrl": payload.get("articleCanonicalUrl"),
                    "evidenceRelationship": relationship,
                    "syndicationRootEvidenceId": root_id,
                })
                if relationship in {"exact-duplicate", "syndicated-copy"}:
                    claim["investmentJudgmentEligible"] = False
                    claim["reasons"] = list(dict.fromkeys(list(claim.get("reasons") or []) + ["syndicated-duplicate"]))
                normalized_claims.append(claim)
            claim_ledger["claims"] = normalized_claims
            summary = dict(claim_ledger.get("summary") or {}) if isinstance(claim_ledger.get("summary"), dict) else {}
            summary["claimCount"] = len(normalized_claims)
            summary["eligibleClaimCount"] = len([claim for claim in normalized_claims if claim.get("investmentJudgmentEligible")])
            summary["syndicatedDuplicateCount"] = len([
                claim for claim in normalized_claims if "syndicated-duplicate" in list(claim.get("reasons") or [])
            ])
            claim_ledger["summary"] = summary
            payload["claimLedger"] = claim_ledger
        item.raw_payload = payload
    return rows


def provenance_signature(item) -> str:
    payload = _payload(item)
    return json.dumps(payload.get("sourceProvenance") or {}, ensure_ascii=False, sort_keys=True, default=str)
