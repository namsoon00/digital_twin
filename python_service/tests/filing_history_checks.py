"""Boundary scenarios for bounded source receipts, without a live provider."""
from copy import deepcopy
from unittest.mock import Mock, patch

from digital_twin.modules.news_intelligence.domain.filing_history import build_filing_history, verified_filing_history

CIK = "0001045810"
SYMBOL = "NVDA"
DATE = "2026-10-20T10:00:00Z"


def submissions():
    return {"cik": int(CIK), "tickers": [SYMBOL], "filings": {"files": [], "recent": {
        "form": ["10-Q/A", "10-Q", "10-Q"],
        "filingDate": ["2026-09-10", "2026-08-26", "2026-05-20"],
        "reportDate": ["2026-07-26", "2026-07-26", "2026-04-26"],
        "accessionNumber": ["0001045810-26-000003", "0001045810-26-000002", "0001045810-26-000001"],
        "primaryDocument": ["amend.htm", "report.htm", "prior.htm"]}}}


def check_history_coverage(test):
    from digital_twin.modules.news_intelligence.domain.research_material import research_material_fingerprint
    from digital_twin.modules.news_intelligence.domain.evidence_delta import evidence_content_signature
    from digital_twin.modules.news_intelligence.domain.investment_research import ResearchEvidence
    source = submissions()
    history = build_filing_history(source, SYMBOL, CIK, "2026-08-26", DATE)
    test.assertTrue(history["complete"])
    test.assertEqual(["10-Q/A", "10-Q"], [row["form"] for row in history["rows"]])
    test.assertEqual(history, verified_filing_history(history, SYMBOL, CIK))
    test.assertFalse(verified_filing_history(history, "OTHER", CIK))
    altered = deepcopy(history); altered["rows"].clear()
    test.assertFalse(verified_filing_history(altered, SYMBOL, CIK))
    for mutation in ("wrong-issuer", "missing-column", "bad-date", "duplicate"):
        value = deepcopy(source)
        if mutation == "wrong-issuer": value["cik"] = 1
        if mutation == "missing-column": value["filings"]["recent"]["filingDate"].pop()
        if mutation == "bad-date": value["filings"]["recent"]["filingDate"][0] = "invalid"
        if mutation == "duplicate": value["filings"]["recent"]["accessionNumber"][0] = value["filings"]["recent"]["accessionNumber"][1]
        with test.subTest(mutation=mutation), test.assertRaises(ValueError):
            build_filing_history(value, SYMBOL, CIK, "2026-08-26", DATE)
    for archive in (None, [{"filingFrom": "2026-01-01", "filingTo": "2026-09-01"}], [{"filingTo": "invalid"}]):
        value = deepcopy(source); value["filings"]["files"] = archive
        partial = build_filing_history(value, SYMBOL, CIK, "2026-08-26", DATE)
        test.assertFalse(partial["complete"])
        test.assertTrue(partial["coverageGaps"])
    test.assertFalse(build_filing_history(source, SYMBOL, CIK, "2020-01-01", DATE)["complete"])
    value = deepcopy(source)
    recent = value["filings"]["recent"]
    for index in range(30):
        for key, field in {"form": "8-K", "filingDate": "2026-09-01", "reportDate": "2026-09-01",
                           "accessionNumber": f"0001045810-26-{index+100:06d}", "primaryDocument": "event.htm"}.items():
            recent[key].append(field)
    capped = build_filing_history(value, SYMBOL, CIK, "2026-08-26", DATE)
    test.assertEqual(24, len(capped["rows"]))
    test.assertFalse(capped["complete"])
    packet = {"symbol": SYMBOL, "sourceTextHash": "unchanged-body", "filingHistory": history}
    later = build_filing_history(source, SYMBOL, CIK, "2026-08-26", "2026-10-21T10:00:00Z")
    test.assertEqual(research_material_fingerprint(packet),
                     research_material_fingerprint({**packet, "filingHistory": later}))
    original = ResearchEvidence("original", SYMBOL, "filing", "SEC EDGAR", "10-Q",
                                raw_payload={"filingHistory": history})
    polled = deepcopy(original); polled.raw_payload["filingHistory"] = later
    test.assertEqual(evidence_content_signature(original), evidence_content_signature(polled))
    changed = deepcopy(source); changed["filings"]["recent"]["form"][0] = "8-K"
    test.assertNotEqual(research_material_fingerprint(packet), research_material_fingerprint({**packet,
        "filingHistory": build_filing_history(changed, SYMBOL, CIK, "2026-08-26", DATE)}))


def check_source_to_graph(test):
    from digital_twin.infrastructure.official_question_research import collect_question_documents
    from digital_twin.modules.news_intelligence.contracts import NewsCollectionTarget
    from digital_twin.modules.news_intelligence.domain.source_focus import source_research_focus
    from digital_twin.modules.news_intelligence.domain.investment_evidence_governance import governed_evidence
    from digital_twin.modules.news_intelligence.domain.research_progress import evidence_packets
    from digital_twin.modules.news_intelligence.domain.research_document_reference import research_document_reference
    from digital_twin.modules.reasoning.domain.observation_document_view import document_view
    from digital_twin.modules.market_data.contracts import ExternalCallDeferred
    target = NewsCollectionTarget(SYMBOL, "NVIDIA", market="US")
    source = submissions()
    url = "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000002/report.htm"
    filing = {"form": "10-Q", "filingDate": "2026-08-26", "reportDate": "2026-07-26",
              "accessionNumber": "0001045810-26-000002", "primaryDocument": "report.htm", "url": url}
    signals = {"secFilings": {SYMBOL: {"cik": CIK, "companyName": "NVIDIA", "latestFiling": filing}}}
    missing = ["부문 정의와 정정 이력 원문이 필요합니다."]
    task = {"taskId": "question", "question": "매출과 순이익을 비교하고 정정 사항을 확인합니다.",
            "queryTerms": ["2026-08-26 segment information amendment"],
            "sourceFocus": source_research_focus(missing)}
    paragraph = "Segment information: NVIDIA has two reportable segments. Data Center is a platform, not a separately reported operating segment. This amendment changes only exhibit references."
    provider = Mock()
    provider.guarded_call.side_effect = lambda _source, _key, fetch: fetch()
    provider.sec_document_access_configured.return_value = True
    provider.sec_document_text_max_chars.return_value = 6000
    provider.fetch_json.return_value = source
    provider.fetch_text.return_value = "<p>" + paragraph + "</p>"
    items, statuses = collect_question_documents(provider, target, signals, [task])
    test.assertEqual(1, provider.fetch_json.call_count)
    test.assertLessEqual(provider.fetch_text.call_count, 3)
    test.assertTrue(provider.fetch_text.call_args_list[0].args[0].endswith("amend.htm"))
    test.assertTrue(any(row["status"] == "filing-history-collected" for row in statuses))
    accepted, claims, _ = governed_evidence(items, target, 527040, "standard")
    test.assertTrue(claims)
    # The evidence repository marks an admitted, stored revision active.
    for item in accepted:
        item.raw_payload["evidenceLifecycleState"] = "active"
    packets = evidence_packets(accepted, claims)
    test.assertTrue(packets)
    test.assertTrue(packets[0]["filingHistory"]["complete"])
    reference = research_document_reference(accepted[0])
    test.assertTrue(reference, accepted[0].raw_payload.get("evidenceGovernance"))
    fact = {"id": "captured-document", "kind": "research-evidence", "symbol": SYMBOL,
            "url": accepted[0].url, "historicalDocumentReference": reference}
    viewed = document_view(fact)
    test.assertEqual(reference["text"], viewed["historicalDocumentReference"]["text"])
    test.assertEqual(reference["filingHistory"], viewed["historicalDocumentReference"]["filingHistory"])
    import json
    without_history = deepcopy(accepted[0]); without_history.raw_payload.pop("filingHistory")
    original = research_document_reference(without_history)
    byte_limit = len(json.dumps(original, ensure_ascii=False).encode())
    with patch("digital_twin.modules.news_intelligence.domain.research_document_reference.MAX_BYTES", byte_limit):
        bounded = research_document_reference(accepted[0])
    test.assertEqual(original["text"], bounded["text"])
    test.assertNotIn("filingHistory", bounded)
    test.assertIn("제출 이력은 크기 제한으로 생략", bounded["limitation"])
    test.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False).encode()), byte_limit)
    from digital_twin.modules.reasoning.domain.observation_session import ObservationEvidenceSession
    session = object.__new__(ObservationEvidenceSession); session._facts = {fact["id"]: fact}
    key = session.question_work_inventory()
    polled = deepcopy(fact)
    polled["historicalDocumentReference"]["filingHistory"] = build_filing_history(
        source, SYMBOL, CIK, reference["filingHistory"]["fromDate"], "2026-10-21T10:00:00Z")
    session._facts = {polled["id"]: polled}
    test.assertEqual(key, session.question_work_inventory())
    # A failed listing keeps the source document usable but never supplies an
    # empty "complete" history or consumes another paid research attempt.
    provider.fetch_json.side_effect = ExternalCallDeferred("rate-limited", DATE, "cooldown")
    items, statuses = collect_question_documents(provider, target, signals, [task])
    test.assertTrue(items)
    test.assertTrue(any(row["status"] == "filing-history-deferred" for row in statuses))
    test.assertTrue(all(not row.raw_payload.get("filingHistory") for row in items))
