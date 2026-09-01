import unittest

from digital_twin.application.investment_calendar_service import InvestmentCalendarService
from digital_twin.domain.events import (
    ONTOLOGY_REASONING_REQUESTED,
    DomainEvent,
    ontology_reasoning_requested_event,
)
from digital_twin.domain.independent_reasoning import independent_reasoning_request
from digital_twin.domain.investment_calendar import InvestmentCalendarEvent
from digital_twin.domain.portfolio import Position
from digital_twin.domain.portfolio_calculations import portfolio_summary
from digital_twin.domain.portfolio_ontology_builder import build_portfolio_ontology
from digital_twin.domain.reasoning_source_facts import investment_calendar_source_fact
from digital_twin.domain.reasoning_source_facts import reasoning_source_facts_runtime_eligibility
from digital_twin.domain.ontology_change_impact import unpack_semantic_dependency_fingerprints
from digital_twin.domain.ontology_scopes import apply_scoped_abox_identity
from digital_twin.domain.ontology_projection_audit import compact_reasoning_request_context
from digital_twin.domain.ontology_execution_trace import reasoning_stage_records
from digital_twin.domain.ontology_rulebox_catalog import default_graph_inference_rules
from digital_twin.infrastructure.typedb_ontology import typedb_native_rule_profile


def calendar_payload(updated_at="2026-08-28T00:00:00Z"):
    return {
        "eventId": "official-earnings-035420-2026-q3",
        "title": "NAVER 3분기 실적 발표",
        "eventType": "earnings",
        "startsAt": "2026-09-03T00:00:00Z",
        "status": "active",
        "importance": 85,
        "symbols": ["035420"],
        "markets": ["KR"],
        "source": "OpenDART",
        "payload": {"officialSource": True, "fetchedAt": updated_at},
        "updatedAt": updated_at,
    }


class InvestmentCalendarReasoningFactsTest(unittest.TestCase):
    def test_revision_ignores_collection_timestamps(self):
        source_event = DomainEvent(
            name="investment_calendar.event_saved",
            aggregate_id="official-earnings-035420-2026-q3",
            occurred_at="2026-08-28T00:00:00Z",
            event_id="source-event-1",
        )
        first = investment_calendar_source_fact(
            InvestmentCalendarEvent.from_payload(calendar_payload("2026-08-28T00:00:00Z")),
            source_event,
        )
        second = investment_calendar_source_fact(
            InvestmentCalendarEvent.from_payload(calendar_payload("2026-08-28T00:05:00Z")),
            source_event,
        )

        self.assertEqual(first.revision, second.revision)
        self.assertEqual(first.fact_id, second.fact_id)

    def test_request_and_v2_context_keep_exact_source_fact(self):
        calendar = InvestmentCalendarEvent.from_payload(calendar_payload())
        source_event = DomainEvent(
            name="investment_calendar.event_saved",
            aggregate_id=calendar.event_id,
            occurred_at="2026-08-28T00:00:00Z",
            event_id="source-event-2",
        )
        fact = investment_calendar_source_fact(calendar, source_event)
        self.assertEqual("EarningsCalendarEvent", fact.fact_type)
        requested = ontology_reasoning_requested_event(
            source_event,
            "investment-calendar-update",
            calendar.symbols,
            fact_types=[fact.fact_type],
            fact_revisions_by_symbol={"035420": fact.revision},
            changed_fields_by_symbol={"035420": ["external.investmentCalendarEvent"]},
            source_facts=[fact.request_payload()],
        )

        self.assertEqual(fact.fact_id, requested.payload["sourceFacts"][0]["factId"])
        request = independent_reasoning_request("test-deployment", [requested])
        self.assertEqual(fact.fact_id, request.context["sourceFacts"][0]["factId"])
        self.assertTrue(request.context["eventDependencyBoundaryAuthoritative"])

    def test_non_earnings_event_uses_generic_calendar_dependency(self):
        payload = calendar_payload()
        payload.update({"eventType": "shareholderMeeting", "title": "NAVER 주주총회"})
        calendar = InvestmentCalendarEvent.from_payload(payload)
        source_event = DomainEvent(
            name="investment_calendar.event_saved",
            aggregate_id=calendar.event_id,
            occurred_at="2026-08-28T00:00:00Z",
            event_id="source-event-generic",
        )

        fact = investment_calendar_source_fact(calendar, source_event)
        requested = ontology_reasoning_requested_event(
            source_event,
            "investment-calendar-update",
            calendar.symbols,
            fact_types=[fact.fact_type],
            source_facts=[fact.request_payload()],
        )

        self.assertEqual("InvestmentCalendarEvent", fact.fact_type)
        self.assertEqual(
            ["kind:investment-calendar-event"],
            requested.payload["factChangeContract"]["dependencyKeys"],
        )

    def test_calendar_fact_becomes_symbol_scoped_abox_entity(self):
        position = Position(
            symbol="035420", name="NAVER", market="KR", currency="KRW",
            quantity=1, sellable_quantity=1, average_price=220000,
            current_price=223000, market_value=223000, sector="인터넷",
        )
        calendar = InvestmentCalendarEvent.from_payload(calendar_payload())
        source_event = DomainEvent(
            name="investment_calendar.event_saved",
            aggregate_id=calendar.event_id,
            occurred_at="2026-08-28T00:00:00Z",
            event_id="source-event-3",
        )
        fact = investment_calendar_source_fact(calendar, source_event)
        graph = build_portfolio_ontology(
            [position], portfolio_summary([position]),
            runtime_context={
                "asOf": "2026-08-28T00:00:00Z",
                "reasoningSourceFacts": [fact.request_payload()],
            },
            include_tbox=False,
            include_presentation=False,
        )

        event = next(item for item in graph.entities if item.kind == "earnings-calendar-event")
        self.assertEqual(calendar.event_id, event.properties["eventId"])
        self.assertEqual(fact.revision, event.properties["sourceFactRevision"])
        self.assertTrue(event.properties["calendarScheduleEligible"])
        self.assertEqual(6.0, event.properties["eventDaysUntil"])
        self.assertTrue(event.properties["eventWithinReviewWindow"])
        self.assertTrue(any(
            relation.source == "stock:035420"
            and relation.target == event.entity_id
            and relation.relation_type == "HAS_EXTERNAL_SIGNAL"
            for relation in graph.relations
        ))
        identity = apply_scoped_abox_identity(graph, account_id="default")
        event_scopes = [
            item for item in identity["scopePlan"]
            if "kind:earnings-calendar-event"
            in unpack_semantic_dependency_fingerprints(item)
        ]
        self.assertTrue(event_scopes)

    def test_projection_context_retains_only_targeted_immutable_fact(self):
        facts = [
            {"factId": "fact-naver", "factType": "InvestmentCalendarEvent", "subjectIds": ["035420"], "payload": {"eventId": "naver"}},
            {"factId": "fact-lg", "factType": "InvestmentCalendarEvent", "subjectIds": ["066570"], "payload": {"eventId": "lg"}},
        ]
        compact = compact_reasoning_request_context(
            {"sourceFacts": facts}, target_symbols=["035420"],
        )
        self.assertEqual(["fact-naver"], [item["factId"] for item in compact["sourceFacts"]])

    def test_execution_trace_exposes_source_fact_identity_without_copying_body(self):
        records = reasoning_stage_records(
            {
                "run_id": "run-1", "world_id": "world-1", "account_id": "default",
                "source_symbols": ["035420"],
                "context_payload": {"reasoningRequest": {
                    "factTypes": ["InvestmentCalendarEvent"],
                    "sourceFacts": [{
                        "factId": "fact-naver", "factType": "InvestmentCalendarEvent",
                        "aggregateId": "earnings-naver", "subjectIds": ["035420"],
                        "revision": "r1", "validFrom": "2026-09-03T00:00:00Z",
                        "qualityState": "verified-source-boundary",
                        "payload": {"notes": "must-not-enter-trace"},
                    }],
                }},
            },
            {"status": "ok"},
        )
        capture = next(item for item in records if item["stageKey"] == "source-fact-capture")
        self.assertEqual("fact-naver", capture["detail"]["sourceFacts"][0]["factId"])
        self.assertNotIn("payload", capture["detail"]["sourceFacts"][0])

    def test_same_semantic_revision_does_not_enqueue_again(self):
        class Repository:
            def upsert(self, event):
                return event

        class FactStore:
            def __init__(self):
                self.ids = set()

            def append(self, fact):
                inserted = fact.fact_id not in self.ids
                self.ids.add(fact.fact_id)
                return {"inserted": inserted, "fact": fact}

        class Publisher:
            def __init__(self):
                self.events = []

            def publish(self, event):
                self.events.append(event)

        publisher = Publisher()
        service = InvestmentCalendarService(
            Repository(), event_publisher=publisher,
            reasoning_source_fact_store=FactStore(),
        )
        first = service.save_event(calendar_payload("2026-08-28T00:00:00Z"))
        second = service.save_event(calendar_payload("2026-08-28T00:05:00Z"))

        self.assertTrue(first["reasoningRequested"])
        self.assertFalse(second["reasoningRequested"])
        self.assertEqual(
            1,
            len([event for event in publisher.events if event.name == ONTOLOGY_REASONING_REQUESTED]),
        )

    def test_expired_calendar_revision_is_stored_without_reasoning_request(self):
        class Repository:
            def upsert(self, event):
                return event

        class FactStore:
            def append(self, fact):
                return {"inserted": True, "fact": fact}

        class Publisher:
            def __init__(self):
                self.events = []

            def publish(self, event):
                self.events.append(event)

        publisher = Publisher()
        service = InvestmentCalendarService(
            Repository(), event_publisher=publisher,
            reasoning_source_fact_store=FactStore(),
        )
        payload = calendar_payload()
        payload["startsAt"] = "2020-01-01T00:00:00Z"

        result = service.save_event(payload)

        self.assertFalse(result["reasoningRequested"])
        self.assertEqual(
            0,
            len([event for event in publisher.events if event.name == ONTOLOGY_REASONING_REQUESTED]),
        )

    def test_distant_calendar_revision_waits_until_review_window(self):
        calendar = InvestmentCalendarEvent.from_payload({
            **calendar_payload(),
            "startsAt": "2035-01-01T00:00:00Z",
        })
        self.assertFalse(calendar.reasoning_eligible())

    def test_calendar_rule_is_fully_native_typedb_compatible(self):
        rule = next(
            item for item in default_graph_inference_rules()
            if item.rule_id == "graph.earnings.calendar.review.v1"
        )
        profile = typedb_native_rule_profile(rule.to_dict())

        self.assertEqual("ready", profile["status"])
        self.assertEqual([], profile["blockers"])

    def test_expired_calendar_source_fact_is_history_only_during_release_replay(self):
        payload = calendar_payload()
        payload["startsAt"] = "2020-01-01T00:00:00Z"

        result = reasoning_source_facts_runtime_eligibility([{
            "factType": "EarningsCalendarEvent",
            "aggregateId": payload["eventId"],
            "payload": payload,
        }])

        self.assertFalse(result["eligible"])
        self.assertEqual("expired-calendar-history", result["reasonCode"])

    def test_calendar_removal_revision_remains_reasoning_eligible(self):
        payload = calendar_payload()
        payload.update({"startsAt": "2020-01-01T00:00:00Z", "status": "deleted"})

        result = reasoning_source_facts_runtime_eligibility([{
            "factType": "EarningsCalendarEvent",
            "aggregateId": payload["eventId"],
            "payload": payload,
        }])

        self.assertTrue(result["eligible"])
        self.assertEqual("calendar-removal-revision", result["status"])


if __name__ == "__main__":
    unittest.main()
