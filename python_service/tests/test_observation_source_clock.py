"""Regressions for immutable old quotes recaptured as apparently current input."""
import copy
import hashlib
import json
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock

from digital_twin.modules.reasoning.domain.observation_evidence import (
    EvidenceReadError, evidence_change_identity, quote_clock_assessment, validate_evidence_packet,
)
from digital_twin.modules.reasoning.application.observation_evidence import ObservationEvidenceReader
from digital_twin.modules.ai_orchestration.domain.execution_input import (
    AGENDA_PROMPT_VERSION, AGENDA_REPAIR_PROMPT_VERSION, LEGACY_REVIEW_PROMPT_VERSION,
    freeze_execution_input, freeze_repair_input, freeze_review_input, validate_execution_input,
)
from digital_twin.modules.ai_orchestration.domain.brain_management import management_prompt
from digital_twin.modules.ai_orchestration.domain.brain_management import management_schema
from digital_twin.modules.ai_orchestration.domain.insight_quality import review_prompt
from digital_twin.modules.ai_orchestration.domain.insight_repair import repair_prompt
from digital_twin.modules.ai_orchestration.domain.publication import publication_block
from digital_twin.modules.notifications.application.ai_observation_message import render_ai_observation
from ai_insight_fixtures import observation, packet
from test_ai_control import SUBJECT
import test_ai_control as control_helpers


CAPTURE = "2026-10-01T23:14:08Z"
SOURCE = "2026-10-02T04:57:36+09:00"


def aged_packet():
    value = packet()
    value["capturedAt"] = CAPTURE
    value["facts"][0].update(sourceAsOf=SOURCE, sourceFetchedAt=CAPTURE, maxAgeMinutes=10,
                             freshnessStatus="fresh", judgementEvidenceUsable=True)
    value["quoteAssessment"] = quote_clock_assessment(value["facts"], CAPTURE)
    return value


class ObservationSourceClockTests(unittest.TestCase):
    def test_clock_metadata_is_citable_without_fabricating_a_native_fact(self):
        from digital_twin.modules.ai_orchestration.domain.insight_contract import resolve_ref, insight_errors
        from digital_twin.modules.ai_orchestration.domain.observation_clock import citable_management_schema
        value = aged_packet()
        original = copy.deepcopy(value)
        result = {**observation(), "input": value}
        result["counterEvidence"] = "재대조한 원천 시세는 갱신 기준을 넘었으므로 현재 상황은 추가 확인이 필요합니다."
        result["claimEvidence"]["counterEvidence"] = [
            {"factId": "quote-1", "period": "assessment", "field": field}
            for field in ("status", "checkedAt", "ageMinutes", "maxAgeMinutes")]
        self.assertEqual([], insight_errors(result, value))
        self.assertEqual("stale", resolve_ref(value, result["claimEvidence"]["counterEvidence"][0])[1])
        self.assertEqual(CAPTURE, resolve_ref(value, result["claimEvidence"]["counterEvidence"][1])[1])
        schema = citable_management_schema(value, [])
        self.assertIn('"assessment"', json.dumps(schema["$defs"]["limitation"]))
        self.assertNotIn('"assessment"', json.dumps(schema["$defs"]["numeric"]))
        self.assertEqual(original, value)
        for invalid in ("sourceAsOf", "capturedAt", "status"):
            corrupt = copy.deepcopy(value)
            if invalid == "sourceAsOf":
                corrupt["facts"][0][invalid] = CAPTURE
            elif invalid == "capturedAt":
                corrupt[invalid] = "2026-10-01T23:15:08Z"
            else:
                corrupt["quoteAssessment"]["quotes"][0][invalid] = "fresh"
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                resolve_ref(corrupt, result["claimEvidence"]["counterEvidence"][0])
        result["claimEvidence"]["hypothesis"] = result["claimEvidence"]["counterEvidence"]
        self.assertTrue(insight_errors(result, value), "clock metadata cannot support a market hypothesis")

    def test_prior_source_clock_prompt_schema_and_hash_remain_replayable(self):
        from digital_twin.modules.ai_orchestration.domain.execution_input import (
            SOURCE_CLOCK_PROMPT_VERSION, SOURCE_CLOCK_REPAIR_PROMPT_VERSION, SOURCE_CLOCK_REVIEW_PROMPT_VERSION,
        )
        from digital_twin.modules.ai_orchestration.domain.observation_clock import clocked_management_prompt, clocked_review_prompt
        value = aged_packet()
        author = freeze_execution_input(value, [], [])
        correction = freeze_repair_input(author, {}, ["시점 확인"], "original")
        reviewer = freeze_review_input({**observation(), "input": value})
        for envelope, version in ((author, SOURCE_CLOCK_PROMPT_VERSION), (correction, SOURCE_CLOCK_REPAIR_PROMPT_VERSION),
                                  (reviewer, SOURCE_CLOCK_REVIEW_PROMPT_VERSION)):
            old = copy.deepcopy(envelope)
            old["promptVersion"] = version
            if version == SOURCE_CLOCK_REVIEW_PROMPT_VERSION:
                prompt = clocked_review_prompt(value, old["draft"])
            else:
                prompt = clocked_management_prompt(value, old["previousAnalyses"], old["researchResults"])
                old["outputSchema"] = management_schema(value, old["researchResults"])
                if version == SOURCE_CLOCK_REPAIR_PROMPT_VERSION:
                    prompt = repair_prompt(prompt, old["repair"])
            old.update(prompt=prompt, promptHash=hashlib.sha256(prompt.encode()).hexdigest())
            validate_execution_input(old)
            self.assertNotIn("시세 시점 근거 인용 확장 계약", prompt)
            validate_execution_input(envelope)

    def test_new_capture_does_not_renew_quote_or_rewrite_native_fact(self):
        original = aged_packet()["facts"][0]
        source = Mock()
        source.metadata.return_value = {"status": "ok", "aboxSnapshotId": "snapshot-1", "accountId": SUBJECT["accountId"]}
        source.snapshot_id.return_value = "snapshot-1"
        source.candidates.return_value = [original]
        before = copy.deepcopy(original)
        result = ObservationEvidenceReader(source, clock=lambda: datetime.fromisoformat(CAPTURE.replace("Z", "+00:00")))(SUBJECT)
        validate_evidence_packet(result)
        measured = result["quoteAssessment"]["quotes"][0]
        self.assertEqual("stale", measured["status"])
        self.assertAlmostEqual(196.533, measured["ageMinutes"])
        self.assertEqual("fresh", measured["sourceFreshnessStatus"])
        self.assertEqual(before, original)
        self.assertEqual(SOURCE, result["facts"][0]["sourceAsOf"])
        self.assertTrue(result["facts"][0]["judgementEvidenceUsable"])
        self.assertEqual("advisory", result["quoteAssessment"]["policy"])

    def test_bad_clocks_and_budget_cannot_be_presented_as_fresh(self):
        from ai_execution_resilience_checks import ExecutionResilienceChecks
        ExecutionResilienceChecks().check_reference_prices_never_become_current_from_freshness_alone()
        base = {"id": "q", "kind": "stock", "sourceFetchedAt": CAPTURE, "maxAgeMinutes": 10}
        for source, status in (("", "missing-time"), ("bad", "invalid-time"),
                               ("2026-10-01T23:14:08", "invalid-time"),
                               ("2026-10-01T23:15:00Z", "invalid-time")):
            with self.subTest(source=source):
                row = quote_clock_assessment([{**base, "sourceAsOf": source}], CAPTURE)["quotes"][0]
                self.assertEqual(status, row["status"])
        for maximum in (None, 0, -1, True, "inf", "nan"):
            row = quote_clock_assessment([{**base, "sourceAsOf": SOURCE, "maxAgeMinutes": maximum}], CAPTURE)["quotes"][0]
            self.assertEqual("unknown-budget", row["status"])
        for checked, status in (("2026-10-02T05:07:36+09:00", "fresh"), ("2026-10-02T05:07:37+09:00", "stale")):
            row = quote_clock_assessment([{**base, "sourceAsOf": SOURCE}], checked)["quotes"][0]
            self.assertEqual(status, row["status"])

    def test_clock_assessment_is_bound_to_facts_and_capture(self):
        value = aged_packet()
        validate_evidence_packet(value)
        for changed in ("sourceAsOf", "capturedAt", "status"):
            broken = copy.deepcopy(value)
            if changed == "sourceAsOf":
                broken["facts"][0][changed] = CAPTURE
            elif changed == "capturedAt":
                broken[changed] = "2026-10-01T23:17:08Z"
            else:
                broken["quoteAssessment"]["quotes"][0][changed] = "fresh"
            with self.subTest(field=changed), self.assertRaises(ValueError):
                validate_evidence_packet(broken)

    def test_only_state_transition_causes_material_change_not_each_minute(self):
        value = aged_packet()
        original = evidence_change_identity(value, [])
        value["capturedAt"] = "2026-10-01T23:17:08Z"
        value["quoteAssessment"] = quote_clock_assessment(value["facts"], value["capturedAt"])
        self.assertEqual(original, evidence_change_identity(value, []))
        value["capturedAt"] = "2026-10-01T19:58:00Z"
        value["quoteAssessment"] = quote_clock_assessment(value["facts"], value["capturedAt"])
        self.assertNotEqual(original, evidence_change_identity(value, []))

    def test_new_author_review_and_repair_share_assessment_and_legacy_inputs_replay(self):
        value = aged_packet()
        result = {**observation(), "input": value}
        author = freeze_execution_input(value, [], [])
        correction = freeze_repair_input(author, {}, ["시점 확인"], "original")
        review = freeze_review_input(result)
        for envelope in (author, correction, review):
            self.assertIn("시세 시점 계약", envelope["prompt"])
            self.assertEqual(value["quoteAssessment"], envelope["current"]["quoteAssessment"])
            validate_execution_input(envelope)
        for envelope, version in ((author, AGENDA_PROMPT_VERSION), (correction, AGENDA_REPAIR_PROMPT_VERSION),
                                  (review, LEGACY_REVIEW_PROMPT_VERSION)):
            old = copy.deepcopy(envelope)
            old["current"].pop("quoteAssessment")
            old["promptVersion"] = version
            if version == LEGACY_REVIEW_PROMPT_VERSION:
                prompt = review_prompt(old["current"], old["draft"])
            else:
                prompt = management_prompt(old["current"], old["previousAnalyses"], old["researchResults"])
                old["outputSchema"] = management_schema(old["current"], old["researchResults"])
                if version == AGENDA_REPAIR_PROMPT_VERSION:
                    prompt = repair_prompt(prompt, old["repair"])
            old.update(prompt=prompt, promptHash=hashlib.sha256(prompt.encode()).hexdigest())
            validate_execution_input(old)

    def test_delayed_delivery_rechecks_age_without_blocking_dated_analysis(self):
        from ai_execution_resilience_checks import ExecutionResilienceChecks
        ExecutionResilienceChecks().check_new_price_presentation_is_explicit_and_legacy_body_is_stable()
        value = aged_packet()
        value["capturedAt"] = "2026-10-01T19:58:00Z"
        value["quoteAssessment"] = quote_clock_assessment(value["facts"], value["capturedAt"])
        result = {**observation(), "input": value, "observedAt": value["capturedAt"]}
        before = copy.deepcopy(result)
        at = "2026-10-01T20:09:36Z"
        self.assertEqual("fresh", value["quoteAssessment"]["quotes"][0]["status"])
        self.assertIn("갱신 기준 이내", render_ai_observation(result))
        body = render_ai_observation(result, sent_at=at)
        self.assertIn("시세 경과 12분", body)
        self.assertIn("기준 시점 가격: 100원", body)
        self.assertIn("과거 시점 참고 자료", body)
        self.assertNotIn("확인한 현재 데이터", body)
        self.assertEqual(before, result)
        self.assertEqual("", publication_block(result, datetime.fromisoformat(at.replace("Z", "+00:00"))))

    def test_closed_reference_does_not_masquerade_as_live_or_close_current_session(self):
        value = aged_packet()
        value["facts"][0].update(freshnessReferenceState="last-close", marketSessionStatus="closed")
        value["quoteAssessment"] = quote_clock_assessment(value["facts"], CAPTURE)
        result = {**observation(), "input": value, "observedAt": CAPTURE}
        body = render_ai_observation(result)
        self.assertIn("출처의 마감 참고값", body)
        self.assertIn("과거 시점 참고 자료", body)
        self.assertNotIn("현재가:", body)

    def test_read_failure_retains_stage_and_never_calls_model_or_leaks_provider_message(self):
        from digital_twin.modules.reasoning.infrastructure.observation_evidence import TypeDBObservationEvidenceSource
        repository = Mock()
        repository.active_abox_members_clause.return_value = ""
        quote = {"id": "quote", "kind": "stock", "label": "quote", "json": json.dumps({"symbol": "TEST", "currentPrice": 100})}
        repository.read_rows.side_effect = [[quote], [], [{"id": "quote", "storageId": "active-quote"}], TimeoutError("secret credential / private query")]
        source = TypeDBObservationEvidenceSource(repository)
        with self.assertRaises(EvidenceReadError) as failure:
            source.candidates(SUBJECT["worldId"], "TEST")
        self.assertEqual("evidence-read:linked-outgoing:TimeoutError", str(failure.exception))
        service, store, planner = control_helpers.AIControlTests().runner(evidence=Mock(side_effect=failure.exception))
        outcome = service.run_once()
        self.assertEqual("deferred", outcome["status"])
        self.assertEqual("evidence-read:linked-outgoing:TimeoutError", outcome["reason"])
        self.assertNotIn("secret", repr(store.fail.call_args))
        planner.assert_not_called()
        store.save_execution_input.assert_not_called()
        store.complete.assert_not_called()

    def test_split_inventory_preserves_union_bounds_and_detects_conflicting_values(self):
        from digital_twin.modules.reasoning.infrastructure.observation_evidence import TypeDBObservationEvidenceSource
        repository = Mock()
        repository.active_abox_members_clause.return_value = ""
        quote = {"id": "quote", "kind": "stock", "label": "quote", "json": json.dumps({"currentPrice": 100, "symbol": "TEST"})}
        macro = {"id": "macro", "kind": "interest-rate", "label": "rate", "json": json.dumps({"rate": 4})}
        repository.read_rows.side_effect = [[quote, macro], [macro], [{"id": "quote", "storageId": "active-quote"}],
                                           [{"storageId": "active-quote"}], [], [quote]]
        source = TypeDBObservationEvidenceSource(repository)
        result = source.candidates(SUBJECT["worldId"], "TEST")
        self.assertEqual({"quote", "macro"}, {row["id"] for row in result})
        self.assertEqual(6, repository.read_rows.call_count)
        repository.read_rows.side_effect = [[macro], [{**macro, "json": json.dumps({"rate": 5})}]]
        with self.assertRaisesRegex(ValueError, "conflicting observation inventory"):
            source.candidates(SUBJECT["worldId"], "TEST")
        repository.read_rows.side_effect = [[{**quote, "id": str(i)} for i in range(2000)], [macro]]
        with self.assertRaisesRegex(ValueError, "bounded read contract"):
            source.candidates(SUBJECT["worldId"], "TEST")
        repository.read_rows.side_effect = [[quote], TimeoutError("private macro query")]
        with self.assertRaisesRegex(EvidenceReadError, "evidence-read:inventory-macro:TimeoutError"):
            source.candidates(SUBJECT["worldId"], "TEST")
