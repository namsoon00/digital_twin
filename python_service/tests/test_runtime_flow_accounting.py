from datetime import datetime, timedelta, timezone
import unittest

from runtime_flow_accounting import request_evidence, outcome_evidence, summarize_cohort
from verify_runtime_continuity import Redactor


NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def successful_request():
    return dict(request_id="request", ai_status="completed", result_id="result", ai_authored=1,
                publication_contract_passed=1, publication_mode="ai-authored", validation_state="ready",
                subject_case_id="subject", scope_match=1, publication_id="publication", receipt_verified=1)


class RuntimeFlowAccountingTests(unittest.TestCase):
    def test_repeated_poll_is_one_case_and_one_receipt(self):
        row = request_evidence(successful_request(), Redactor())
        report = summarize_cohort([{"requests": [row]}] * 5, 1)
        self.assertEqual("pass", report["status"])
        self.assertEqual(1, report["sampledRequests"])
        self.assertEqual({"verified-telegram-receipt": 1}, report["delivery"])
        self.assertEqual("inconclusive", report["outcomeClosure"]["status"])

    def test_one_success_cannot_hide_fallback_or_missing_result(self):
        for change in ({"publication_mode": "typedb-fallback"}, {"result_id": None},
                       {"validation_state": "blocked"}, {"scope_match": 0},
                       {"publication_id": None}, {"receipt_verified": 0}):
            with self.subTest(change=change):
                redactor = Redactor()
                good = request_evidence(successful_request(), redactor)
                bad = request_evidence(dict(successful_request(), request_id="bad", **change), redactor)
                report = summarize_cohort([{"requests": [good, bad]}], 1)
                self.assertEqual("degraded", report["status"])

    def test_only_explained_withholding_is_accounted(self):
        row = successful_request()
        row.update(receipt_verified=0, delivery_state="suppressed", delivery_reason_code="cooldown")
        self.assertEqual("explained-withheld", request_evidence(row, Redactor())["delivery"])
        row["delivery_reason_code"] = ""
        self.assertEqual("unresolved-delivery", request_evidence(row, Redactor())["delivery"])

    def test_small_truncated_missing_or_mixed_release_samples_are_inconclusive(self):
        redactor = Redactor()
        first = request_evidence(successful_request(), redactor)
        second = request_evidence(dict(successful_request(), request_id="other", prompt_version="other"), redactor)
        for samples, minimum in (([{"requests": [first]}], 30), ([{"requests": [first], "truncated": True}], 1),
                                 ([None, {"requests": [first]}], 1), ([{"requests": [first, second]}], 1)):
            with self.subTest(samples=samples):
                self.assertEqual("inconclusive", summarize_cohort(samples, minimum)["status"])

    def test_outcomes_distinguish_future_data_gaps_and_broken_links(self):
        redactor = Redactor()
        base = dict(target_id="target", status="pending", target_at=(NOW + timedelta(days=1)).isoformat())
        self.assertEqual("not-due", outcome_evidence(base, NOW, redactor)["state"])
        base.update(target_at=(NOW - timedelta(days=1)).isoformat())
        self.assertEqual("overdue-or-unexplained", outcome_evidence(base, NOW, redactor)["state"])
        base.update(status="needs-data", exclusion_reason="missing-benchmark")
        self.assertEqual("explained-data-gap", outcome_evidence(base, NOW, redactor)["state"])
        base.update(status="observed", outcome_id="outcome")
        self.assertEqual("invalid-outcome-link", outcome_evidence(base, NOW, redactor)["state"])
        base.update(stored_outcome_id="outcome", scope_match=1)
        self.assertEqual("observed", outcome_evidence(base, NOW, redactor)["state"])
        observed = outcome_evidence(base, NOW, redactor)
        future = outcome_evidence(dict(base, target_id="future", status="pending", target_at=(NOW + timedelta(days=1)).isoformat()), NOW, redactor)
        self.assertEqual("inconclusive", summarize_cohort([{"outcomes": [observed, future]}], 1)["outcomeClosure"]["status"])
        self.assertEqual("pass", summarize_cohort([{"outcomes": [observed]}], 1)["outcomeClosure"]["status"])

    def test_latest_request_state_replaces_prior_pending(self):
        redactor = Redactor()
        pending = request_evidence(dict(successful_request(), ai_status="processing"), redactor)
        done = request_evidence(successful_request(), redactor)
        other = request_evidence(dict(successful_request(), request_id="other"), redactor)
        self.assertIn("ai-still-pending", summarize_cohort([{"requests": [pending, other]}], 1)["gaps"])
        report = summarize_cohort([{"requests": [pending]}, {"requests": [done]}], 1)
        self.assertEqual({"ai-authored": 1}, report["dispositions"])

    def test_private_errors_are_not_copied_to_reports(self):
        row = request_evidence(dict(successful_request(), contract_failure_code="secret account url https://private"), Redactor())
        self.assertEqual("unknown", row["reasonCode"])


if __name__ == "__main__":
    unittest.main()
