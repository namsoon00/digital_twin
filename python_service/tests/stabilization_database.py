"""Credential-free lifecycle fixtures using only the isolated test schema."""

from datetime import datetime, timezone
import tempfile
import unittest
import uuid

from mysql_fixtures import reset_mysql_test_database
from digital_twin.domain.events import DomainEvent, ONTOLOGY_REASONING_REQUESTED
from digital_twin.domain.investment_brain import DecisionEpisode
from digital_twin.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
from digital_twin.domain.reasoning_engine_versions import (
    EngineReleaseBundle,
    ReasoningEngineDescriptor,
)
from digital_twin.infrastructure.transactions.ai_publication import (
    MySQLAIInferenceQueueStore,
)
from digital_twin.infrastructure.transactions.decision_history import (
    MySQLInvestmentDecisionEpisodeStore,
)
from digital_twin.infrastructure.transactions.monitoring import (
    MySQLEventLog,
    MySQLMonitorStore,
    MySQLMonitoringCycleRecorder,
)
from digital_twin.infrastructure.transactions.portfolio import (
    MySQLInvestmentDomainStore,
)
from digital_twin.modules.notifications.infrastructure.mysql_notification_jobs import (
    MySQLNotificationJobStore,
)
from digital_twin.modules.reasoning.infrastructure.mysql_engine_runtime import (
    MySQLReasoningEngineJobStore,
    MySQLReasoningEngineRegistryStore,
)


def fixture_snapshot(account="stabilization", stamp="2026-09-10T01:00:00Z"):
    return AccountSnapshot(
        account,
        "Fixture account",
        "fixture",
        "live",
        "ok",
        stamp,
        PortfolioSummary(
            total=1100,
            invested=1000,
            cash=100,
            markets=[],
            sectors=[],
            concentration=90,
        ),
        positions=[
            Position(
                "AAPL",
                "Fixture company",
                market="US",
                currency="USD",
                quantity=10,
                current_price=100,
                market_value=1000,
                market_value_krw=1000,
            )
        ],
        metadata={
            "accountSnapshotCompleteness": {
                "holdings": "complete",
                "cash": "complete",
                "source": "test-fixture",
            }
        },
    )


def fixture_episode(
    account="stabilization", stamp="2026-09-10T01:00:00Z", episode_id="episode:fixture"
):
    return DecisionEpisode.from_dict(
        {
            "episodeId": episode_id,
            "accountId": account,
            "symbol": "AAPL",
            "subjectName": "Fixture company",
            "action": "HOLD",
            "reviewLevel": "check",
            "dataState": "sufficient",
            "validationState": "ready",
            "source": "storage-contract-fixture",
            "decidedAt": stamp,
            "question": {"questionId": "question:fixture"},
            "hypothesisSet": {"hypothesisSetId": "hypotheses:fixture"},
            "selectedHypothesisId": "hypothesis:fixture",
            "sourceAboxSnapshotId": "abox:fixture",
            "inferenceGenerationId": "generation:fixture",
            "followUpConditions": [
                {
                    "conditionId": "condition:fixture",
                    "field": "currentPrice",
                    "operator": ">",
                    "threshold": 110,
                }
            ],
        }
    )


class StabilizationDatabaseCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.settings = reset_mysql_test_database(cls.temp.name)
        cls.jobs = MySQLReasoningEngineJobStore(cls.settings)
        cls.registry = MySQLReasoningEngineRegistryStore(cls.settings)
        cls.notifications = MySQLNotificationJobStore(cls.settings)
        cls.ai = MySQLAIInferenceQueueStore(cls.settings)
        cls.decisions = MySQLInvestmentDecisionEpisodeStore(cls.settings)
        cls.portfolio = MySQLInvestmentDomainStore(cls.settings)
        cls.events = MySQLEventLog(cls.settings)

    def setUp(self):
        self.deployment = "fixture-deployment-" + uuid.uuid4().hex[:12]
        self.registry.upsert(
            ReasoningEngineDescriptor(
                engine_family="fixture",
                engine_version="v2",
                deployment_id=self.deployment,
                status="active",
                graph_store_binding="test-fixture",
                time_series_backend_id="test-fixture",
                release_bundle=EngineReleaseBundle(
                    "fixture-tbox",
                    "fixture-rulebox",
                    "fixture-prompt",
                    "fixture-features",
                ),
            )
        )
        self.registry.set_control(self.deployment, self.deployment)

    def source(self, symbol="AAPL", account="stabilization", suffix="", stamp=""):
        now = stamp or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return DomainEvent(
            name=ONTOLOGY_REASONING_REQUESTED,
            aggregate_id="market-observation:" + symbol,
            occurred_at=now,
            event_id="fixture-event:" + (suffix or uuid.uuid4().hex),
            correlation_id="fixture-correlation",
            payload={
                "accountIds": [account],
                "affectedSymbols": [symbol],
                "factTypes": ["PRICE_OBSERVATION"],
                "sourceObservedAt": now,
                "workClass": "MARKET",
                "verifiedSourceSnapshot": {
                    "accountId": account,
                    "snapshotId": "snapshot:" + now,
                    "generatedAt": now,
                },
            },
        )

    def claimed_job(
        self, worker="fixture-worker", symbol="AAPL", account="stabilization"
    ):
        event = self.source(symbol, account)
        self.events.handle(event)
        outcome = self.jobs.ingress_event(event)
        self.assertFalse(
            outcome["saved"],
            "The event log already persisted its queue ingress atomically.",
        )
        rows = self.jobs.claim(self.deployment, worker, 1, 60)
        self.assertEqual(1, len(rows))
        self.assertEqual("processing", rows[0]["status"])
        self.assertTrue(rows[0]["claimedAt"])
        return rows[0]

    def sql(self, statement, params=(), all_rows=False):
        with self.jobs.connect() as connection:
            cursor = connection.execute(statement, params)
            if statement.lstrip().upper().startswith("SELECT"):
                return cursor.fetchall() if all_rows else cursor.fetchone()
            return cursor.rowcount
