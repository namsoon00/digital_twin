"""Bounded retained evidence history, without new claims or decision authority."""

import re

from .information_brief import _mapping, _time, source_url


EVENT_HISTORY_VERSION = "retained-event-history-v1"


def event_history_identity(item):
    raw = _mapping(item.raw_payload)
    if raw.get("eventEpisodeId"):
        period = str(_mapping(raw.get("eventFingerprint")).get("reportingPeriod") or "")
        # Legacy quarter-only episode IDs can recur across years. The current
        # episode producer groups earnings only; require its bounded period.
        if not re.fullmatch(r"(?:20\d{2}-Q[1-4]|FY20\d{2})", period):
            return "evidenceId", str(item.evidence_id)
    for key in ("eventEpisodeId", "storyClusterId"):
        if raw.get(key):
            return key, str(raw[key])
    return "evidenceId", str(item.evidence_id)


def build_event_history(item, peers=(), limit=12):
    """Use only explicit same-subject identities; retained rows are not full history.

    Publication is the display order, never a replacement for effective/known time.
    Corrections and conflicts come from recorded lineage, not adjacent titles.
    """
    key, identity = event_history_identity(item)
    matched = {}
    for peer in [*peers, item]:
        candidate_key, candidate = event_history_identity(peer)
        if peer.symbol == item.symbol and peer.kind == item.kind and candidate_key == key and candidate == identity:
            matched[str(peer.evidence_id)] = peer
    def order(peer):
        stamp = _time(peer.published_at)
        return (stamp is None, stamp.timestamp() if stamp else 0, str(peer.evidence_id))
    ordered = sorted(matched.values(), key=order)
    limit = max(1, min(20, int(limit)))
    # Retain the selected source even when it is older than the latest window.
    selected = ordered[-limit:]
    if item.evidence_id not in {peer.evidence_id for peer in selected}:
        selected = sorted([item, *(selected[-(limit - 1):] if limit > 1 else [])], key=order)
    entries = []
    for peer in selected:
        raw = _mapping(peer.raw_payload)
        governance = _mapping(raw.get("evidenceGovernance"))
        event = _mapping(raw.get("companyEventContract"))
        provenance = _mapping(raw.get("sourceProvenance"))
        conflicts = governance.get("conflictingEvidenceIds")
        state = str(peer.lifecycle_state or "unknown")
        entries.append({
            "evidenceId": peer.evidence_id,
            "title": str(raw.get("translatedTitleKo") or peer.title),
            "source": peer.source,
            "url": source_url(peer.url),
            "state": state,
            "selected": peer.evidence_id == item.evidence_id,
            "publishedAt": str(peer.published_at or ""),
            "observedAt": str(peer.observed_at or ""),
            "announcedAt": str(event.get("announcedAt") or raw.get("announcedAt") or ""),
            "effectiveAt": str(event.get("effectiveAt") or event.get("effectiveFrom") or raw.get("effectiveAt") or raw.get("effectiveFrom") or ""),
            "reportingPeriod": str(event.get("reportingPeriod") or raw.get("reportingPeriod") or _mapping(raw.get("eventFingerprint")).get("reportingPeriod") or ""),
            "sourceRevision": str(raw.get("sourceRevision") or raw.get("receiptNo") or raw.get("accessionNumber") or ""),
            "revisionState": str(event.get("revisionState") or ""),
            "correctsSourceDocumentId": str(event.get("correctsSourceDocumentId") or ""),
            "relationship": str(provenance.get("evidenceRelationship") or raw.get("evidenceRelationship") or ""),
            "claimState": str(governance.get("claimState") or "unknown"),
            "supersededByEvidenceId": str(governance.get("supersededByEvidenceId") or ""),
            "conflictingEvidenceIds": [str(value) for value in conflicts[:20]] if isinstance(conflicts, list) else [],
        })
    return {
        "version": EVENT_HISTORY_VERSION,
        "identityKind": key,
        "identity": identity,
        "symbol": item.symbol,
        "entries": entries,
        "ordering": "publishedAt-ascending-unknown-last",
        "coverage": "retained-sample",
        "historyComplete": False,
        "omittedFromSample": max(0, len(ordered) - len(entries)),
        "decisionAuthority": False,
        "note": "현재 보관된 관련 근거의 일부입니다. 최초 발생부터의 전체 이력이나 독립 출처 검증 완료를 뜻하지 않습니다.",
    }
