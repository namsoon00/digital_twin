import unittest

from digital_twin.application.ontology_catalog_query_service import OntologyCatalogQueryService


def tbox_payload():
    return {
        "version": "tbox-v1",
        "domainModelVersion": "domain-v1",
        "fingerprint": "fingerprint-1",
        "boundedContexts": [
            {"key": "core", "label": "핵심", "description": ""},
            {"key": "reasoning", "label": "추론", "description": ""},
        ],
        "classDefinitions": [
            {"name": "Subject", "label": "대상", "bounded_context": "core", "parent": ""},
            {"name": "Signal", "label": "신호", "bounded_context": "reasoning", "parent": "Subject"},
        ],
        "relationDefinitions": [
            {
                "name": "HAS_SIGNAL",
                "bounded_context": "reasoning",
                "source_context": "core",
                "target_context": "reasoning",
            }
        ],
        "reasoningRuleDefinitions": [{"text": "a signal supports a decision", "bounded_context": "reasoning"}],
    }


RULE = {
    "rule_id": "rule.signal.v1",
    "label": "신호 확인 규칙",
    "version": "v1",
    "enabled": True,
    "conditions": [
        {"condition_id": "signal", "kind": "relation", "relation_type": "HAS_SIGNAL", "role": "required"}
    ],
    "derivations": [
        {
            "relation_type": "HAS_SIGNAL",
            "tbox_class": "Signal",
            "decision_stage": "RELATION_WATCH",
        }
    ],
    "hypothesis_family_key": "signal.family",
}


class FakeRepository:
    def __init__(self, fallback=False, invalid_rule=False):
        self.fallback = fallback
        self.invalid_rule = invalid_rule

    def active_tbox_metadata(self):
        return {
            "configured": True,
            "status": "ok",
            "source": "typedb",
            "version": "tbox-v1",
            "fingerprint": "fingerprint-1",
        }

    def rulebox_snapshot(self):
        rule = dict(RULE)
        if self.invalid_rule:
            rule["conditions"] = [{"kind": "relation", "relation_type": "UNKNOWN_RELATION"}]
        return {
            "configured": not self.fallback,
            "status": "disabled" if self.fallback else "ok",
            "source": "typedb-defaults" if self.fallback else "typedb-typeql",
            "defaultsFallbackUsed": self.fallback,
            "ruleboxSnapshotId": "rulebox:1",
            "rules": [rule],
        }

    def inferencebox_recovery_metadata(self, world_id):
        return {
            "configured": True,
            "status": "ok",
            "worldId": world_id,
            "inferenceGenerationId": "generation:1",
        }

    def inferencebox_snapshot(self, symbols=None, limit=80, world_id=""):
        return {
            "configured": True,
            "status": "ok",
            "worldId": world_id,
            "inferenceGenerationId": "generation:1",
            "nativeTypeDbReasoningUsed": True,
            "generationAligned": True,
            "entities": [],
            "relations": [
                {
                    "type": "HAS_SIGNAL",
                    "source": "stock:005930",
                    "target": "signal:1",
                    "sourceRuleId": "rule.signal.v1",
                    "inferenceTraceId": "trace:1",
                    "decisionStage": "RELATION_WATCH",
                }
            ],
            "traces": [
                {
                    "id": "trace:1",
                    "label": "삼성전자 신호 추론",
                    "symbol": "005930",
                    "sourceRuleId": "rule.signal.v1",
                    "matchedConditionIds": ["signal"],
                    "requiredConditionCount": 1,
                    "groundedConditionCount": 1,
                    "validationState": "verified",
                }
            ],
        }


HYPOTHESIS = {
    "lifecycleKey": "v2:account:hypothesis-1",
    "lifecycleId": "account-hypothesis-overlay:1",
    "scope": "account",
    "accountId": "default",
    "marketId": "KR",
    "symbol": "005930",
    "familyId": "family:1",
    "state": "observed",
    "transitionReason": "현재 추론 세대에서 관찰됨",
    "materialChange": True,
    "inferenceGenerationId": "generation:1",
    "sourceRuleIds": ["rule.signal.v1"],
}


class FakeHypothesisStore:
    def __init__(self):
        self.rows = [dict(HYPOTHESIS)]

    def count_current(self, account_id="", symbol="", market_id="", scope="", search="", state=""):
        return len(self._filtered(account_id, symbol, market_id, scope, search, state))

    def list_current_summary(
        self,
        account_id="",
        symbol="",
        market_id="",
        scope="",
        limit=100,
        offset=0,
        search="",
        state="",
    ):
        return self._filtered(account_id, symbol, market_id, scope, search, state)[offset:offset + limit]

    def list_current(self, account_id="", symbol="", market_id="", scope="", limit=100):
        return self._filtered(account_id, symbol, market_id, scope, "", "")[:limit]

    def current_for_keys(self, keys):
        return {row["lifecycleKey"]: row for row in self.rows if row["lifecycleKey"] in keys}

    def _filtered(self, account_id, symbol, market_id, scope, search, state):
        rows = list(self.rows)
        if account_id:
            rows = [row for row in rows if row["accountId"] == account_id or row["scope"] == "market"]
        if symbol:
            rows = [row for row in rows if row["symbol"] == symbol]
        if market_id:
            rows = [row for row in rows if row["marketId"] == market_id]
        if scope:
            rows = [row for row in rows if row["scope"] == scope]
        if state:
            rows = [row for row in rows if row["state"] == state]
        if search:
            rows = [row for row in rows if search.lower() in str(row).lower()]
        return rows


class FakeDecisionStore:
    def list(self, account_id="", symbol="", limit=50):
        return [{
            "episodeId": "episode:1",
            "accountId": "default",
            "symbol": "005930",
            "action": "HOLD",
            "reviewLevel": "observe",
            "validationState": "verified",
            "selectedHypothesisId": "account-hypothesis-overlay:1",
            "inferenceGenerationId": "generation:1",
            "decisionSummary": "신호를 확인하고 보유",
            "decidedAt": "2026-08-14T00:00:00Z",
            "hypothesisSet": {
                "hypotheses": [{
                    "hypothesisId": "account-hypothesis-overlay:1",
                    "supportingRuleIds": ["rule.signal.v1"],
                }]
            },
        }]


class FakeNotificationStore:
    def recent_page(self, limit=40, offset=0, message_type="", status="", query="", scope="all"):
        rows = [{
            "jobId": "job:1",
            "decisionEpisodeId": "episode:1",
            "messageType": "investmentInsight",
            "symbol": "005930",
            "status": "sent",
            "createdAt": "2026-08-14T00:01:00Z",
        }]
        return ([row for row in rows if not query or query == row["decisionEpisodeId"]], len(rows))


class OntologyCatalogQueryServiceTests(unittest.TestCase):
    def service(self, repository=None):
        return OntologyCatalogQueryService(
            ontology_repository=repository or FakeRepository(),
            hypothesis_lifecycle_store=FakeHypothesisStore(),
            decision_episode_store=FakeDecisionStore(),
            notification_job_store=FakeNotificationStore(),
            tbox_provider=tbox_payload,
        )

    def test_summary_reports_canonical_counts_and_deployment_alignment(self):
        payload = self.service().summary(world_id="portfolio:local:default", account_id="default")

        self.assertEqual("ok", payload["status"])
        self.assertEqual(2, payload["counts"]["classes"])
        self.assertEqual(1, payload["counts"]["relations"])
        self.assertEqual(1, payload["counts"]["executableRules"])
        self.assertEqual(1, payload["counts"]["hypotheses"])
        self.assertEqual("aligned", payload["deployedTBox"]["alignment"])
        self.assertEqual(1, payload["ruleKnowledge"]["hypothesisRuleCount"])
        self.assertTrue(all(item["status"] == "ok" for item in payload["diagnostics"]))

    def test_rule_catalog_exposes_and_filters_theory_governance(self):
        service = self.service()
        all_rules = service.list_section("rules")
        rule = all_rules["items"][0]

        filtered = service.list_section(
            "rules",
            rule_kind=rule["ruleKind"],
            theory_family=rule["theoryFamily"],
            validation_status=rule["knowledgeValidationStatus"],
        )

        self.assertEqual("predictive-hypothesis", rule["ruleKind"])
        self.assertTrue(rule["knowledgeBasis"]["requiresHypothesis"])
        self.assertTrue(rule["detailRequired"])
        self.assertNotIn("conditions", rule)
        self.assertNotIn("references", rule["knowledgeBasis"])
        self.assertEqual(["rule.signal.v1"], [item["ruleId"] for item in filtered["items"]])

    def test_rulebox_defaults_are_blocked_when_typedb_is_unavailable(self):
        payload = self.service(FakeRepository(fallback=True)).list_section("rules")

        self.assertEqual("unavailable", payload["status"])
        self.assertEqual([], payload["items"])
        self.assertTrue(payload["source"]["fallbackBlocked"])

    def test_catalog_pages_use_stable_ids_and_cursor_metadata(self):
        payload = self.service().list_section("hypotheses", query="005930", account_id="default", limit=1)

        self.assertEqual("v2:account:hypothesis-1", payload["items"][0]["id"])
        self.assertEqual("offset:0", payload["page"]["cursor"])
        self.assertEqual(1, payload["page"]["total"])

    def test_rule_lineage_connects_exact_runtime_identifiers_to_alert(self):
        payload = self.service().lineage(
            "rule",
            "rule.signal.v1",
            world_id="portfolio:local:default",
            account_id="default",
            symbol="005930",
        )

        lineage = payload["lineage"]
        self.assertEqual("ok", payload["status"])
        self.assertEqual(["HAS_SIGNAL"], [item["id"] for item in lineage["relations"]])
        self.assertEqual(["v2:account:hypothesis-1"], [item["id"] for item in lineage["hypotheses"]])
        self.assertEqual(["trace:1"], [item["id"] for item in lineage["inferences"]])
        self.assertEqual(["episode:1"], [item["id"] for item in lineage["decisions"]])
        self.assertEqual(["job:1"], [item["id"] for item in lineage["notifications"]])
        self.assertEqual([], payload["gaps"])

    def test_inference_listing_requires_an_explicit_portfolio_world(self):
        payload = self.service().list_section("inferences")

        self.assertEqual("world-required", payload["status"])
        self.assertEqual([], payload["items"])

    def test_summary_warns_when_rule_references_an_undefined_relation(self):
        payload = self.service(FakeRepository(invalid_rule=True)).summary(
            world_id="portfolio:local:default",
            account_id="default",
        )
        diagnostic = next(item for item in payload["diagnostics"] if item["id"] == "rulebox.references")

        self.assertEqual("warning", diagnostic["status"])
        self.assertEqual(["UNKNOWN_RELATION"], diagnostic["undefinedRelationTypes"])


if __name__ == "__main__":
    unittest.main()
