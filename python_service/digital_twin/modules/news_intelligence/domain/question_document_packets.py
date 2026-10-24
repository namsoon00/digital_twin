"""Explicit historical questions may read proven originals excluded only by age."""
from .collection_scope import required_report_periods
from .historical_document_admission import age_only_rejection
from .research_document_reference import research_document_reference
from .research_progress import evidence_packets, fingerprint

VERSION = "question-historical-original-v1"


def historical_packets(items, rejected, tasks):
    periods = set(required_report_periods(tasks))
    if not periods:
        return []
    claims = {row.evidence_id: row for row in rejected}
    result = []
    for item in items:
        claim = claims.get(item.evidence_id)
        if not claim or not age_only_rejection(item.raw_payload):
            continue
        reference = research_document_reference(item)
        if not reference or reference["periodEnd"] not in periods:
            continue
        packet = evidence_packets([item], [claim])[0]
        packet["historicalOriginal"] = {"version": VERSION, "periodEnd": reference["periodEnd"],
            "sourceDocumentHash": reference["sourceDocumentHash"], "documentHash": reference["documentHash"],
            "purpose": "original-period-comparison", "sourceVerification": "verified-original-document",
            "currentInvestmentEligible": False}
        packet.pop("inputFingerprint", None)
        packet["inputFingerprint"] = fingerprint(packet)
        result.append(packet)
    return result


def matches_historical_question(task, packet):
    reference = packet.get("historicalOriginal") or {}
    return (reference.get("version") == VERSION and reference.get("purpose") == "original-period-comparison"
            and reference.get("sourceVerification") == "verified-original-document"
            and reference.get("currentInvestmentEligible") is False
            and reference.get("periodEnd") == packet.get("periodEnd")
            and reference["periodEnd"] in required_report_periods([task]))
