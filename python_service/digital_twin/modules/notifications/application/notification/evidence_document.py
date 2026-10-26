"""Check the final policy document against its frozen event, not cached prose."""

from dataclasses import replace

from digital_twin.modules.read_models.contracts import (
    CustomerInvestmentDocument, CustomerInvestmentSection,
    normalized_customer_investment_document, customer_investment_document_quality,
)
from digital_twin.modules.notifications.domain.rulebox_notification_summary import compact_rulebox_summary


def enforce_policy_evidence_document(context, document):
    packet = context.get("relationChangeEvidence") or {}
    if not any(isinstance(e, dict) and e.get("policyState") for e in packet.get("customerEvents") or []):
        return document
    summary = compact_rulebox_summary(packet)
    contract = summary.get("evidenceContract") or {}
    if not contract:
        return document
    source = document or CustomerInvestmentDocument(role="typedb-observation", headline="🔗 관리 기준 점검")
    expected = normalized_customer_investment_document(replace(source, layout="compact-rulebox-v1",
        lead=summary["lead"], sections=tuple(CustomerInvestmentSection(key, title, tuple(rows))
                                           for key, title, rows in summary["sections"])))
    # Normalize each mandatory row independently. The generic preview's
    # eight-row limit and punctuation-insensitive dedupe cannot erase a sign,
    # a second rule's threshold, or the ninth required comparison.
    mandatory = []
    for row in contract["measurements"]:
        one = normalized_customer_investment_document(replace(source,
            sections=(CustomerInvestmentSection("decision-impact", "핵심 수치", (row["text"],)),)))
        mandatory.extend(one.sections[0].rows if one.sections else ())
    if mandatory:
        extras = next((rows for key, _, rows in summary['sections'] if key == 'decision-impact'), [])
        reference = [row for row in extras if row.startswith('함께 바뀐 근거: ')]
        for text in reference:
            one = normalized_customer_investment_document(replace(source,
                sections=(CustomerInvestmentSection("decision-impact", "핵심 수치", (text,)),)))
            mandatory.extend(one.sections[0].rows if one.sections else ())
        expected = replace(expected, sections=tuple(replace(s, rows=tuple(mandatory))
            if s.key == "decision-impact" else s for s in expected.sections))
    issues = []
    if not document or document.lead != expected.lead:
        issues.append("policy-conclusion-mismatch")
    actual = {s.key: s.rows for s in document.sections} if document else {}
    for section in expected.sections:
        if actual.get(section.key) != section.rows:
            issues.append("policy-section-mismatch:" + section.key)
    quality = {"version": "rule-notice-semantic-quality-v1",
               "status": "partial" if contract["issues"] else "repaired" if issues else "passed",
               "issues": contract["issues"], "originalIssues": issues,
               "sourceIdentity": contract["sourceIdentity"],
               "eventIds": [e["eventId"] for e in contract["events"]],
               "requiredMeasurementCount": len(contract["measurements"])}
    context["ruleboxNotificationSummary"] = summary
    context["ruleNoticeEvidenceQuality"] = quality
    context["customerInvestmentDocument"] = expected.to_dict()
    document_quality = customer_investment_document_quality(expected)
    document_quality["evidenceStatus"] = quality["status"]
    if contract["issues"]:
        document_quality["status"] = "failed"
        document_quality["issues"] += contract["issues"]
    context["customerInvestmentDocumentQuality"] = document_quality
    return expected
