import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from digital_twin.application.investment_research_orchestration_service import (
    InvestmentResearchOrchestrationService,
)
from digital_twin.application.ontology_reasoning_service import OntologyReasoningRunner
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.investment_brain import InvestmentQuestion, utc_now_iso
from digital_twin.domain.investment_evidence_governance import (
    ReasoningGeneration,
    ResearchReasoningHandoff,
    ResearchRun,
)
from digital_twin.domain.investment_research import NewsCollectionTarget, ResearchEvidence
from digital_twin.infrastructure.event_bus import EventBus


WORLD_ID = "portfolio:local:account-1"


class MemoryEvidenceStore:
    def __init__(self):
        self.saved = []
        self.last_changed_items = []

    def latest(self, symbol="", limit=50):
        return []

    def upsert_many(self, items):
        self.saved.extend(items)
        self.last_changed_items = list(items)
        return len(self.last_changed_items)


class MemoryResearchStore:
    def __init__(self):
        self.runs = []
        self.refreshes = []

    def save_run(self, run):
        self.runs.append(run)
        return run

    def mark_reasoning_refreshed(self, run_id, refreshed=True, reasoning_handoff=None):
        self.refreshes.append((run_id, bool(refreshed), dict(reasoning_handoff or {})))
        return {"runId": run_id, "reasoningRefreshed": bool(refreshed)}


class StaticGateway:
    def __init__(self, items):
        self.items = list(items)

    def collect_for_target(self, _target, source_types=None):
        return list(self.items), [{"provider": "test", "status": "ok", "sourceTypes": list(source_types or [])}]


class Cursor:
    def __init__(self):
        self.payload = {"processedEventIds": []}

    def processed_event_ids(self):
        return list(self.payload.get("processedEventIds") or [])

    def mark_processed(self, event_ids):
        self.payload["processedEventIds"] = list(self.payload.get("processedEventIds") or []) + list(event_ids or [])

    def load(self):
        return dict(self.payload)

    def save(self, payload):
        self.payload = dict(payload or {})


def source_generation():
    return ReasoningGeneration(
        inference_generation_id="inference:old",
        source_abox_snapshot_id="abox:old",
        world_id=WORLD_ID,
        generation_aligned=True,
        observed_at="2026-07-22T00:00:00Z",
    )


def research_handoff():
    return ResearchReasoningHandoff(
        request_id="research-handoff-1",
        source_generation=source_generation(),
    ).requested(["evidence-1"])


def projection(abox_id, inference_id):
    return {
        "status": "ok",
        "aboxSnapshotId": abox_id,
        "ontologyWorld": {"accountId": "account-1", "worldId": WORLD_ID},
        "inferenceBox": {
            "status": "ok",
            "nativeTypeDbReasoningUsed": True,
            "generationAligned": True,
            "sourceAboxSnapshotId": abox_id,
            "inferenceGenerationId": inference_id,
            "worldId": WORLD_ID,
            "inferenceGenerationAt": "2026-07-22T00:05:00Z",
        },
    }


class ResearchReasoningHandoffTests(unittest.TestCase):
    def test_research_event_carries_original_generation_and_changed_evidence(self):
        evidence = ResearchEvidence(
            evidence_id="evidence-1",
            symbol="005930",
            kind="news",
            source="Reuters",
            title="삼성전자 공급 계약 확인",
            summary="삼성전자에 직접 관련된 공급 계약 사실을 확인했습니다.",
            url="https://example.test/evidence-1",
            published_at=utc_now_iso(),
            observed_at=utc_now_iso(),
            raw_payload={
                "directMention": True,
                "sourceTrustState": "trusted",
                "dataState": "sufficient",
                "validationState": "ready",
            },
        )
        events = EventBus()
        store = MemoryResearchStore()
        service = InvestmentResearchOrchestrationService(
            MemoryEvidenceStore(),
            StaticGateway([evidence]),
            research_store=store,
            event_publisher=events,
            settings={"investmentBrainResearchMinimumVerifiedCount": "2"},
        )
        question = InvestmentQuestion.create("삼성전자 위험 근거를 확인해줘", "005930", "삼성전자", "account-1")
        target = NewsCollectionTarget("005930", "삼성전자", "KR", "KRW", "반도체")
        brain = {
            "reasoningGeneration": source_generation().to_dict(),
            "epistemicState": {"status": "contested"},
            "hypothesisSet": {"hypotheses": []},
            "researchPlan": {"tasks": []},
        }

        run = service.run(question, target, brain, account_id="account-1", force=True)

        self.assertEqual("pending", run.reasoning_handoff.status)
        self.assertEqual(["evidence-1"], run.reasoning_handoff.changed_evidence_ids)
        restored = ResearchRun.from_dict(run.to_dict())
        self.assertEqual("abox:old", restored.reasoning_handoff.source_generation.source_abox_snapshot_id)
        request = next(event for event in events.published if event.name == ONTOLOGY_REASONING_REQUESTED)
        handoff = request.payload["reasoningHandoff"]
        self.assertEqual("inference:old", handoff["sourceGeneration"]["inferenceGenerationId"])
        self.assertEqual(["evidence-1"], request.payload["changedEvidenceIds"])
        self.assertEqual("account-1", request.payload["accountId"])

    def test_runner_keeps_request_pending_until_a_new_aligned_generation_exists(self):
        handoff = research_handoff()
        request = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="ontology:005930",
            event_id="reasoning-event-1",
            payload={
                "researchRunId": "research-run-1",
                "accountId": "account-1",
                "symbols": ["005930"],
                "changedCount": 1,
                "reasoningHandoff": handoff.to_dict(),
            },
        )
        store = MemoryResearchStore()
        runner = OntologyReasoningRunner(None, None, lambda: None, research_store=store)

        stale = runner.research_generation_refresh_results(
            [request],
            {"account-1": projection("abox:old", "inference:old")},
        )

        self.assertEqual(["research-run-1"], stale["blockedRunIds"])
        self.assertEqual(["reasoning-event-1"], stale["blockedRequestEventIds"])
        self.assertFalse(store.refreshes[-1][1])
        self.assertEqual("blocked", store.refreshes[-1][2]["status"])

        advanced = runner.research_generation_refresh_results(
            [request],
            {"account-1": projection("abox:new", "inference:new")},
        )

        self.assertEqual(["research-run-1"], advanced["refreshedRunIds"])
        self.assertTrue(store.refreshes[-1][1])
        self.assertEqual("applied", store.refreshes[-1][2]["status"])
        self.assertEqual("abox:new", store.refreshes[-1][2]["appliedGeneration"]["sourceAboxSnapshotId"])

    def test_runner_does_not_advance_event_cursor_when_generation_is_not_new(self):
        handoff = research_handoff()
        request = DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="ontology:005930",
            event_id="reasoning-event-cursor",
            occurred_at="2026-07-22T00:00:00Z",
            payload={
                "researchRunId": "research-run-cursor",
                "accountId": "account-1",
                "symbols": ["005930"],
                "changedCount": 1,
                "reasoningHandoff": handoff.to_dict(),
            },
        )

        class Reader:
            def events(self, name="", **_kwargs):
                return [request] if name == ONTOLOGY_REASONING_REQUESTED else []

        class Monitor:
            def __init__(self):
                self.accounts = [SimpleNamespace(account_id="account-1")]
                self.last_ontology_projection_results = {"account-1": projection("abox:old", "inference:old")}

            def run_once(self, **_kwargs):
                return []

        cursor = Cursor()
        store = MemoryResearchStore()
        monitor = Monitor()
        runner = OntologyReasoningRunner(
            Reader(),
            cursor,
            monitor_runner_factory=lambda: monitor,
            research_store=store,
            settings={"ontologyRuleCandidateAiEnabled": "0"},
            now_provider=lambda: datetime(2026, 7, 22, tzinfo=timezone.utc),
        )

        blocked = runner.run_once()

        self.assertEqual("partial", blocked["status"])
        self.assertEqual([], cursor.processed_event_ids())
        monitor.last_ontology_projection_results = {"account-1": projection("abox:new", "inference:new")}
        completed = runner.run_once(force=True)
        self.assertEqual("ok", completed["status"])
        self.assertEqual(["reasoning-event-cursor"], cursor.processed_event_ids())


if __name__ == "__main__":
    unittest.main()
