import unittest
from unittest.mock import Mock

from digital_twin.domain.ontology_subject_fanout import (
    evaluate_subject_fanout_comparison,
    native_match_signatures,
)
from digital_twin.infrastructure.typedb_ontology import TypeDBOntologyGraphRepository


def match(rule_id, source_id, evidence_id):
    return {
        "ruleId": rule_id,
        "sourceId": source_id,
        "queryMode": "ignored-execution-detail",
        "matchedConditions": [{"conditionId": "condition-1"}],
        "evidenceRelationIds": [evidence_id],
    }


def result(matches, candidates):
    return {
        "status": "ok",
        "coreNativeInferenceEvaluationComplete": True,
        "nativeInferenceEvaluationComplete": True,
        "nativeCoverageStatus": "complete",
        "matches": matches,
        "executedRules": [{
            "ruleId": "graph.test.v1",
            "candidateSymbols": candidates,
        }],
        "skippedRules": [],
    }


class OntologySubjectFanoutTests(unittest.TestCase):
    def test_accepts_semantically_equal_fanout_only_with_measured_gain(self):
        combined = result([
            match("graph.test.v1", "instrument:AAA", "evidence:AAA"),
            match("graph.test.v1", "instrument:BBB", "evidence:BBB"),
        ], ["AAA", "BBB"])
        subjects = [
            result([match("graph.test.v1", "instrument:AAA", "evidence:AAA")], ["AAA"]),
            result([match("graph.test.v1", "instrument:BBB", "evidence:BBB")], ["BBB"]),
        ]

        comparison = evaluate_subject_fanout_comparison(
            combined,
            subjects,
            combined_duration_ms=100_000,
            fanout_duration_ms=55_000,
            generation_unchanged=True,
        )

        self.assertEqual("accepted", comparison["status"])
        self.assertTrue(comparison["semanticMatch"])
        self.assertTrue(comparison["ruleCoverageMatch"])
        self.assertEqual(45.0, comparison["durationReductionPct"])

    def test_rejects_fast_fanout_when_one_inference_result_is_missing(self):
        combined = result([
            match("graph.test.v1", "instrument:AAA", "evidence:AAA"),
            match("graph.test.v1", "instrument:BBB", "evidence:BBB"),
        ], ["AAA", "BBB"])
        subjects = [
            result([match("graph.test.v1", "instrument:AAA", "evidence:AAA")], ["AAA"]),
            result([], ["BBB"]),
        ]

        comparison = evaluate_subject_fanout_comparison(
            combined,
            subjects,
            combined_duration_ms=100_000,
            fanout_duration_ms=20_000,
            generation_unchanged=True,
        )

        self.assertEqual("rejected", comparison["status"])
        self.assertIn("inference-match-mismatch", comparison["reasonCodes"])

    def test_signatures_ignore_query_transport_but_keep_evidence(self):
        first = match("graph.test.v1", "instrument:AAA", "evidence:1")
        second = dict(first, queryMode="typedb-manifest-evidence-index")
        changed = match("graph.test.v1", "instrument:AAA", "evidence:2")

        self.assertEqual(native_match_signatures([first]), native_match_signatures([second]))
        self.assertNotEqual(native_match_signatures([first]), native_match_signatures([changed]))

    def test_repository_merges_two_complete_subject_reads(self):
        repository = TypeDBOntologyGraphRepository(
            address="127.0.0.1:1729",
            native_rule_parallelism=2,
            native_rule_subject_fanout_enabled=True,
            native_rule_subject_parallelism=2,
            native_rule_total_read_parallelism=4,
            persistent_driver_enabled=False,
        )

        def subject_result(*_args, **kwargs):
            symbol = list(kwargs.get("target_symbols") or [""])[0]
            return result(
                [match("graph.test.v1", "instrument:" + symbol, "evidence:" + symbol)],
                [symbol],
            ) | {
                "matchedCount": 1,
                "readTransactionCount": 1,
                "readQueryCount": 1,
                "executedRuleCount": 1,
                "executedRuleWorkCount": 1,
                "skippedRuleCount": 0,
                "skippedRuleWorkCount": 0,
            }

        repository.match_typedb_native_rules = Mock(side_effect=subject_result)

        merged = TypeDBOntologyGraphRepository.match_typedb_native_rules_by_subject(
            repository,
            [],
            ["AAA", "BBB"],
            world_id="portfolio:local:main",
        )

        self.assertEqual("ok", merged["status"])
        self.assertTrue(merged["subjectFanoutUsed"])
        self.assertEqual(2, merged["matchedCount"])
        self.assertEqual(2, merged["readQueryCount"])
        self.assertEqual(2, repository.match_typedb_native_rules.call_count)
        self.assertTrue(all(
            call.kwargs.get("stable_abox_write_lease_held") is True
            for call in repository.match_typedb_native_rules.call_args_list
        ))
        self.assertTrue(all(
            call.kwargs.get("native_rule_parallelism") == 2
            for call in repository.match_typedb_native_rules.call_args_list
        ))
        self.assertEqual(2, merged["subjectRuleParallelism"])
        self.assertEqual(4, merged["totalReadParallelismCap"])
        self.assertEqual(4, merged["effectiveTotalReadParallelism"])

    def test_repository_fanout_divides_global_read_cap_across_subjects(self):
        repository = TypeDBOntologyGraphRepository(
            address="127.0.0.1:1729",
            native_rule_parallelism=4,
            native_rule_subject_fanout_enabled=True,
            native_rule_subject_parallelism=2,
            native_rule_total_read_parallelism=3,
            persistent_driver_enabled=False,
        )
        repository.match_typedb_native_rules = Mock(return_value=result([], []))

        merged = TypeDBOntologyGraphRepository.match_typedb_native_rules_by_subject(
            repository,
            [],
            ["AAA", "BBB"],
            world_id="portfolio:local:main",
        )

        self.assertEqual(1, merged["subjectRuleParallelism"])
        self.assertEqual(3, merged["totalReadParallelismCap"])
        self.assertEqual(2, merged["effectiveTotalReadParallelism"])
        self.assertTrue(all(
            call.kwargs.get("native_rule_parallelism") == 1
            for call in repository.match_typedb_native_rules.call_args_list
        ))

    def test_repository_fanout_fails_whole_generation_when_one_subject_fails(self):
        repository = TypeDBOntologyGraphRepository(
            address="127.0.0.1:1729",
            native_rule_subject_fanout_enabled=True,
            native_rule_subject_parallelism=2,
            persistent_driver_enabled=False,
        )

        def subject_result(*_args, **kwargs):
            symbol = list(kwargs.get("target_symbols") or [""])[0]
            if symbol == "BBB":
                return {
                    "status": "partial",
                    "coreNativeInferenceEvaluationComplete": False,
                    "nativeInferenceEvaluationComplete": False,
                    "matches": [],
                    "executedRules": [],
                    "skippedRules": [],
                }
            return result([match("graph.test.v1", "instrument:AAA", "evidence:AAA")], ["AAA"])

        repository.match_typedb_native_rules = Mock(side_effect=subject_result)

        merged = TypeDBOntologyGraphRepository.match_typedb_native_rules_by_subject(
            repository,
            [],
            ["AAA", "BBB"],
            world_id="portfolio:local:main",
        )

        self.assertEqual("partial", merged["status"])
        self.assertFalse(merged["coreNativeInferenceEvaluationComplete"])
        self.assertEqual(1, merged["subjectFanoutFailureCount"])


if __name__ == "__main__":
    unittest.main()
