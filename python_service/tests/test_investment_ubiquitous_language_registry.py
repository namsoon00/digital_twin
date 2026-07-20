import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from digital_twin.domain.investment_ubiquitous_language import (
    LANGUAGE_GOVERNANCE_BOX,
    LANGUAGE_REGISTRY_SETTING_KEY,
    audit_user_facing_investment_text,
    default_investment_language_registry,
    investment_archetype_label,
    investment_language_registry,
    normalize_investment_language_registry,
    propose_investment_language_changes,
    user_facing_investment_language,
    validate_investment_language_registry,
)
from digital_twin.application.notification_ai_gate_audit import context_with_validated_ai_response
from digital_twin.domain.notification_ai_gate_contracts import NotificationAIValidatedResponse
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.domain.ontology_tbox import (
    tbox_class_def,
    tbox_class_materialization_policy,
    tbox_materialization_box,
    tbox_relation_def,
)
from digital_twin.infrastructure.graph_store_lifecycle import ontology_seed_graph
from digital_twin.infrastructure.graph_store_rulebox import rulebox_graph_from_rules
from digital_twin.infrastructure.web_server import (
    ontology_language_payload,
    preview_ontology_language_payload,
    save_ontology_language_payload,
)


class InvestmentUbiquitousLanguageRegistryTests(unittest.TestCase):
    def test_default_registry_covers_tbox_archetypes_and_position_intents(self):
        validation = validate_investment_language_registry(default_investment_language_registry())

        self.assertTrue(validation["valid"])
        self.assertEqual([], validation["coverage"]["missingTermIds"])
        self.assertEqual(
            validation["coverage"]["requiredCount"],
            validation["coverage"]["coveredCount"],
        )

    def test_validation_rejects_duplicate_raw_term_ids(self):
        registry = default_investment_language_registry()
        duplicate = dict(next(item for item in registry["terms"] if item["termId"] == "PlatformGrowth"))
        registry["terms"].append(duplicate)

        validation = validate_investment_language_registry(registry)

        self.assertFalse(validation["valid"])
        self.assertTrue(any(
            item["termId"] == "PlatformGrowth" and item["field"] == "termId"
            for item in validation["errors"]
        ))

    def test_registry_override_changes_level_specific_archetype_label(self):
        registry = default_investment_language_registry()
        platform = next(item for item in registry["terms"] if item["termId"] == "PlatformGrowth")
        platform["renderings"]["absoluteBeginner"] = "온라인 서비스를 키우는 성장주"
        settings = {LANGUAGE_REGISTRY_SETTING_KEY: json.dumps(registry, ensure_ascii=False)}

        self.assertEqual(
            "온라인 서비스를 키우는 성장주",
            investment_archetype_label("PlatformGrowth", settings, "absoluteBeginner"),
        )

    def test_draft_override_does_not_replace_approved_tbox_label(self):
        registry = default_investment_language_registry()
        platform = next(item for item in registry["terms"] if item["termId"] == "PlatformGrowth")
        platform["status"] = "draft"
        platform["preferredLabel"] = "검토 중 표현"
        settings = {LANGUAGE_REGISTRY_SETTING_KEY: registry}

        self.assertEqual("플랫폼 성장주", investment_archetype_label("PlatformGrowth", settings))

    def test_text_audit_finds_internal_and_forbidden_expressions(self):
        source = "종목 타입: PlatformGrowth. feature 기여도와 thesis를 확인합니다."
        audit = audit_user_facing_investment_text(source, level="absoluteBeginner")

        self.assertFalse(audit["valid"])
        self.assertIn("종목 성격", audit["renderedText"])
        self.assertIn("플랫폼 성장주", audit["renderedText"])
        self.assertIn("판단에 미친 영향", audit["renderedText"])
        self.assertIn("보유 이유", audit["renderedText"])
        self.assertNotIn("PlatformGrowth", audit["renderedText"])
        self.assertNotIn("feature 기여도", audit["renderedText"])
        self.assertTrue(propose_investment_language_changes(source)["proposals"])

    def test_short_position_intent_id_does_not_corrupt_other_words(self):
        audit = audit_user_facing_investment_text("relation score와 core 역할을 봅니다.")

        self.assertIn("확인 필요 점수", audit["renderedText"])
        self.assertIn("핵심 보유", audit["renderedText"])
        self.assertNotIn("s핵심 보유", audit["renderedText"])

    def test_replacement_adjusts_korean_particles(self):
        rendered = user_facing_investment_language(
            "PlatformGrowth 종목의 feature 기여도와 thesis를 확인합니다.",
            level="absoluteBeginner",
        )

        self.assertEqual(
            "플랫폼 성장주 종목의 판단에 미친 영향과 보유 이유를 확인합니다.",
            rendered,
        )

    def test_validated_notification_uses_current_registry(self):
        registry = default_investment_language_registry()
        feature = next(item for item in registry["terms"] if item["termId"] == "feature-contribution")
        feature["renderings"]["absoluteBeginner"] = "판단에 준 영향"
        settings = {LANGUAGE_REGISTRY_SETTING_KEY: json.dumps(registry, ensure_ascii=False)}
        response = NotificationAIValidatedResponse(
            action="HOLD",
            action_label="보유",
            confidence=60,
            summary="feature 기여도와 thesis를 확인합니다.",
            opinion="feature 기여도와 thesis를 확인합니다.",
            source="test",
        )

        result = context_with_validated_ai_response(
            {
                "messageType": "investmentInsight",
                "messageDeliveryLevel": "absoluteBeginner",
                "displayTarget": "테스트 종목 / TEST",
            },
            response,
            settings,
        )

        self.assertIn("판단에 준 영향과 보유 이유를 확인합니다.", result["telegramMessage"])
        self.assertNotIn("feature 기여도", result["telegramMessage"])

    def test_language_governance_is_projected_separately_from_rulebox(self):
        registry = normalize_investment_language_registry()
        graph = rulebox_graph_from_rules(
            default_graph_inference_rules(),
            include_tbox=False,
            language_registry=registry,
        )

        language_entities = [
            item for item in graph.entities
            if (item.properties or {}).get("ontologyBox") == LANGUAGE_GOVERNANCE_BOX
        ]
        language_relations = [
            item for item in graph.relations
            if (item.properties or {}).get("ontologyBox") == LANGUAGE_GOVERNANCE_BOX
        ]
        self.assertTrue(any(item.kind == "language-registry-version" for item in language_entities))
        self.assertTrue(any(item.kind == "domain-term" for item in language_entities))
        self.assertTrue(any(item.kind == "term-rendering" for item in language_entities))
        self.assertTrue(any(item.relation_type == "GOVERNS_TERM" for item in language_relations))
        self.assertTrue(any(item.relation_type == "HAS_TERM_RENDERING" for item in language_relations))
        self.assertTrue(all((item.properties or {}).get("ontologyBox") != "RuleBoxGovernance" for item in language_entities))

    def test_seed_graph_contains_tbox_language_contract_and_registry(self):
        graph = ontology_seed_graph(language_registry=default_investment_language_registry())

        self.assertIsNotNone(tbox_class_def("DomainTerm"))
        self.assertIsNotNone(tbox_relation_def("HAS_TERM_RENDERING"))
        self.assertEqual("language-governance", tbox_class_materialization_policy("DomainTerm"))
        self.assertEqual("LanguageGovernance", tbox_materialization_box("language-governance"))
        self.assertTrue(any(
            (item.properties or {}).get("ontologyBox") == LANGUAGE_GOVERNANCE_BOX
            for item in graph.entities
        ))

    def test_missing_required_tbox_term_is_restored_during_normalization(self):
        registry = default_investment_language_registry()
        registry["terms"] = [item for item in registry["terms"] if item["termId"] != "PlatformGrowth"]

        normalized = investment_language_registry({LANGUAGE_REGISTRY_SETTING_KEY: registry})

        self.assertTrue(any(item["termId"] == "PlatformGrowth" for item in normalized["terms"]))

    def test_language_api_saves_registry_and_resyncs_active_rulebox(self):
        settings = {"typedbAddress": "127.0.0.1:1729", LANGUAGE_REGISTRY_SETTING_KEY: ""}
        saved_rulebox_payloads = []

        class FakeRepository:
            def rulebox_snapshot(self):
                return {"status": "ok", "rules": [{"id": "active-rule"}]}

            def save_rulebox(self, payload):
                saved_rulebox_payloads.append(payload)
                return {"status": "ok", "saved": True, "languageGovernanceEntityCount": 12}

        def save_settings(payload):
            settings.update(payload)
            return dict(settings)

        registry = default_investment_language_registry()
        with mock.patch("digital_twin.infrastructure.web_server.runtime_settings", side_effect=lambda: dict(settings)), \
                mock.patch("digital_twin.infrastructure.web_server.save_runtime_settings", side_effect=save_settings), \
                mock.patch("digital_twin.infrastructure.web_server.ontology_repository_from_settings", return_value=FakeRepository()), \
                mock.patch("digital_twin.infrastructure.web_server.new_domain_event"):
            result = save_ontology_language_payload({"registry": registry})
            current = ontology_language_payload()

        self.assertTrue(result["saved"])
        self.assertEqual("ok", result["typeDbSync"]["status"])
        self.assertEqual([{"rules": [{"id": "active-rule"}]}], saved_rulebox_payloads)
        self.assertTrue(current["validation"]["valid"])
        self.assertTrue(str(settings[LANGUAGE_REGISTRY_SETTING_KEY]).startswith("{"))

    def test_language_preview_uses_requested_delivery_level(self):
        registry = default_investment_language_registry()
        term = next(item for item in registry["terms"] if item["termId"] == "PlatformGrowth")
        term["renderings"]["absoluteBeginner"] = "온라인 서비스를 키우는 성장주"
        with mock.patch("digital_twin.infrastructure.web_server.runtime_settings", return_value={}):
            result = preview_ontology_language_payload({
                "registry": registry,
                "level": "absoluteBeginner",
                "text": "PlatformGrowth",
            })

        self.assertEqual("온라인 서비스를 키우는 성장주", result["renderedText"])


if __name__ == "__main__":
    unittest.main()
