import json
import unittest

from digital_twin.application.flow_lens_service import position_payload
from digital_twin.application.notification_ai_gate_message import (
    _investor_text_from_relation_facts,
    compact_investor_flow_line,
)
from digital_twin.domain.investor_flow_psychology import (
    investor_flow_contract,
    investor_flow_observation,
)
from digital_twin.domain.ontology_relation_facts import position_signal_facts
from digital_twin.domain.market_time_series import MarketTimeSeriesObservation
from digital_twin.domain.portfolio import PortfolioSummary, Position
from digital_twin.domain.portfolio_ontology_temporal_concepts import (
    has_smart_money_flow_observation,
)
from digital_twin.infrastructure.mysql_market_time_series import MySQLMarketTimeSeriesStore


def portfolio_summary():
    return PortfolioSummary(1000, 1000, 0, [], [], 1)


def investor_coverage(fields, participant_status=None, status="available"):
    return {
        "investor": {
            "status": status,
            "fields": list(fields),
            "observedFields": list(fields),
            "participantStatus": participant_status or {},
            "measurementType": "intraday-estimate",
            "providerUpdateSlot": "09:30",
            "sourceAsOf": "2026-08-13T09:30:00+09:00",
            "judgementEvidenceUsable": status == "available",
        }
    }


class InvestorFlowContractTests(unittest.TestCase):
    def test_foreign_only_estimate_does_not_create_institution_or_smart_money_fact(self):
        position = Position(
            symbol="035720",
            name="카카오",
            market="KR",
            currency="KRW",
            foreign_net_volume=236000,
            institution_net_volume=0,
            market_signal_coverage=investor_coverage(
                ["foreignNetVolume"],
                {
                    "foreign": "available",
                    "institution": "not-yet-published",
                    "individual": "not-yet-published",
                },
            ),
        )

        contract = investor_flow_contract(position)
        observation = investor_flow_observation(position)
        facts = position_signal_facts(position, portfolio_summary())

        self.assertEqual(236000, contract["values"]["foreign"])
        self.assertIsNone(contract["values"]["institution"])
        self.assertFalse(contract["smartMoneyAvailable"])
        self.assertEqual(236000, observation["foreignNetVolume"])
        self.assertNotIn("institutionNetVolume", observation)
        self.assertNotIn("smartMoneyNetVolume", observation)
        self.assertNotIn("institutionNetVolume", facts)
        self.assertNotIn("smartMoneyNetVolume", facts)

    def test_observed_zero_is_kept_as_a_real_institution_value(self):
        position = Position(
            symbol="035720",
            name="카카오",
            market="KR",
            currency="KRW",
            foreign_net_volume=12000,
            institution_net_volume=0,
            market_signal_coverage=investor_coverage(
                ["foreignNetVolume", "institutionNetVolume"],
                {
                    "foreign": "available",
                    "institution": "available",
                    "individual": "not-yet-published",
                },
            ),
        )

        contract = investor_flow_contract(position)
        observation = investor_flow_observation(position)

        self.assertEqual(0, contract["values"]["institution"])
        self.assertTrue(contract["smartMoneyAvailable"])
        self.assertEqual(0, observation["institutionNetVolume"])
        self.assertEqual(12000, observation["smartMoneyNetVolume"])
        self.assertFalse(observation["jointSmartMoneyInflow"])

    def test_stale_flow_keeps_quality_metadata_without_directional_facts(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            foreign_net_volume=100,
            institution_net_volume=200,
            market_signal_coverage=investor_coverage(
                ["foreignNetVolume", "institutionNetVolume"],
                {"foreign": "stale", "institution": "stale", "individual": "missing"},
                status="stale",
            ),
        )

        observation = investor_flow_observation(position)

        self.assertFalse(observation["available"])
        self.assertNotIn("foreignNetVolume", observation)
        self.assertNotIn("institutionNetVolume", observation)
        self.assertNotIn("smartMoneyNetVolume", observation)

    def test_flow_lens_payload_omits_unobserved_numeric_defaults(self):
        position = Position(
            symbol="035420",
            name="NAVER",
            market="KR",
            currency="KRW",
            foreign_net_volume=-32000,
            market_signal_coverage=investor_coverage(
                ["foreignNetVolume"],
                {"foreign": "available", "institution": "not-yet-published", "individual": "not-yet-published"},
            ),
        )

        payload = position_payload(position)

        self.assertEqual(-32000, payload["foreignNetVolume"])
        self.assertNotIn("institutionNetVolume", payload)
        self.assertNotIn("individualNetVolume", payload)

    def test_temporal_smart_money_requires_both_observed_fields(self):
        foreign_only = {
            "foreignNetVolume": 100,
            "institutionNetVolume": 0,
            "marketSignalCoverage": investor_coverage(["foreignNetVolume"]),
        }
        both_observed = {
            "foreignNetVolume": 100,
            "institutionNetVolume": 0,
            "marketSignalCoverage": investor_coverage(["foreignNetVolume", "institutionNetVolume"]),
        }

        self.assertFalse(has_smart_money_flow_observation(foreign_only))
        self.assertTrue(has_smart_money_flow_observation(both_observed))

    def test_notification_explains_partial_provider_schedule_without_zero_fill(self):
        coverage = investor_coverage(
            ["foreignNetVolume"],
            {
                "foreign": "available",
                "institution": "not-yet-published",
                "individual": "not-yet-published",
            },
        )
        coverage["investor"]["nextProviderUpdateAt"] = "2026-08-13T10:00:00+09:00"
        context = {
            "ontologyRelationContext": {
                "facts": {
                    "foreignNetVolume": 236000,
                    "marketSignalCoverage": coverage,
                }
            }
        }

        detail = _investor_text_from_relation_facts(context)
        compact = compact_investor_flow_line(context)

        self.assertIn("외국인: 순매수 236,000주", detail)
        self.assertIn("기관: 아직 제공 전 · 10:00 갱신 예정", detail)
        self.assertIn("개인: 아직 제공 전 · 장 마감 후 제공", detail)
        self.assertNotIn("기관: 매수·매도 균형 0주", detail)
        self.assertIn("기관 10:00 갱신 예정", compact)

    def test_time_series_round_trip_preserves_observed_fields(self):
        position = Position(
            symbol="000660",
            name="SK하이닉스",
            market="KR",
            currency="KRW",
            current_price=200000,
            foreign_net_volume=236000,
            market_signal_coverage=investor_coverage(
                ["foreignNetVolume"],
                {"foreign": "available", "institution": "not-yet-published", "individual": "not-yet-published"},
            ),
        )
        observation = MarketTimeSeriesObservation.from_position(
            "main",
            position,
            "2026-08-13T00:35:00Z",
            provider="KIS",
        )
        stored = json.loads(observation.investor_coverage_json)
        store = MySQLMarketTimeSeriesStore.__new__(MySQLMarketTimeSeriesStore)
        payload = store.observation_payload(observation.to_row())

        self.assertEqual(["foreignNetVolume"], stored["observedFields"])
        self.assertEqual(
            "not-yet-published",
            payload["marketSignalCoverage"]["investor"]["participantStatus"]["institution"],
        )


if __name__ == "__main__":
    unittest.main()
