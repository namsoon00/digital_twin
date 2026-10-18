"""Independent cash/weight conservation checks for bounded proposal amounts."""
from dataclasses import replace
from unittest.mock import Mock


def check_rebalance_arithmetic(test):
    from digital_twin.modules.portfolio.application.portfolio_lifecycle_service import PortfolioAccountingService
    from digital_twin.modules.portfolio.domain.portfolio import AccountSnapshot, PortfolioSummary, Position
    from digital_twin.modules.portfolio.domain.investment_mandate import InvestmentMandate
    service = PortfolioAccountingService(Mock())
    def run(weights, limit=100, targets=None, cash_pct=0, cap=20, fees=15, floor=0):
        total = 1_000_000
        positions = [Position(symbol=key, name=key, market="KR", currency="KRW", quantity=value,
            current_price=10000, market_value=value * 10000, market_value_krw=value * 10000,
            source="holding") for key, value in weights.items()]
        snapshot = AccountSnapshot(account_id="synthetic", account_label="Synthetic", provider="fixture", mode="live", status="complete",
            generated_at="2026-10-19T00:00:00Z", positions=positions,
            portfolio=PortfolioSummary(total=total, invested=total * (100-cash_pct)/100, cash=total*cash_pct/100,
                                       markets=[], sectors=[], concentration=max(weights.values())))
        mandate = InvestmentMandate.from_profile(snapshot.account_id, "portfolio:synthetic", {}, snapshot.generated_at)
        mandate = replace(mandate, max_position_weight_pct=limit, max_sector_weight_pct=100, min_cash_weight_pct=floor,
            max_rebalance_turnover_pct=cap, estimated_transaction_cost_bps=fees, allocation_band_pct=0, target_allocations=targets or {})
        exposure = service.exposure_snapshot(snapshot, mandate.portfolio_id, mandate)
        proposal = service.rebalance_proposal(snapshot, mandate, exposure)
        if not proposal:
            return None
        test.assertEqual([], proposal.validate())
        test.assertEqual("review-required", proposal.status)
        notionals = sum(row.maximum_notional for row in proposal.legs)
        cost = notionals * fees / 10000
        after_total = total-cost
        after_cash = snapshot.portfolio.cash + sum(row.maximum_notional * (1 if row.side == "DECREASE" else -1)
                                                  for row in proposal.legs) - cost
        test.assertLessEqual(notionals / total * 100, cap + 1e-6)
        test.assertGreaterEqual(after_cash, -1e-6)
        after_positions = {key: value*10000 for key, value in weights.items()}
        for row in proposal.legs:
            after_positions[row.symbol] = after_positions.get(row.symbol, 0) + row.maximum_notional * (1 if row.side == "INCREASE" else -1)
            test.assertAlmostEqual(after_positions[row.symbol] / after_total * 100, row.after_weight_pct, places=7)
        test.assertAlmostEqual(sum(after_positions.values()) + after_cash, after_total, places=6)
        for scenario in proposal.scenarios:
            if scenario.scenario_type == "NO_ACTION":
                continue
            test.assertIsNone(scenario.after_metrics["annualizedVolatilityPct"])
            test.assertEqual("not-simulated", scenario.after_metrics["riskSimulationState"])
            test.assertEqual("partial", scenario.data_state)
        return proposal
    capped = run({"A": 100}, limit=25)
    test.assertEqual(200000, capped.legs[0].maximum_notional)
    test.assertGreater(capped.legs[0].after_weight_pct, 80)
    test.assertFalse(capped.scenarios[1].after_metrics["allocationBandsRestored"])
    bad_leg = replace(capped.legs[0], after_weight_pct=25)
    forged = replace(capped, scenarios=[replace(capped.scenarios[1], legs=[bad_leg])])
    test.assertIn("rebalance-after-weight-mismatch", forged.validate())
    forged = replace(capped, scenarios=[replace(capped.scenarios[1], after_metrics={
        **capped.scenarios[1].after_metrics, "allocationBandsRestored": True})])
    test.assertIn("rebalance-restoration-unproven", forged.validate())
    shared = run({"A": 50, "B": 50}, limit=10)
    test.assertAlmostEqual(200000, sum(row.maximum_notional for row in shared.legs))
    joint = run({"A": 100}, targets={"position:A": 40, "position:B": 30, "position:C": 30})
    test.assertEqual(3, len(joint.legs))
    test.assertFalse(joint.scenarios[1].after_metrics["allocationBandsRestored"])
    restored = run({"A": 100}, limit=25, cap=100, fees=0)
    test.assertTrue(restored.scenarios[1].after_metrics["allocationBandsRestored"])
    stopped = run({"A": 100}, limit=25, cap=0)
    test.assertEqual([], stopped.legs)
    test.assertEqual(["NO_ACTION"], [row.scenario_type for row in stopped.scenarios])
    conflicting = run({"A": 100}, limit=25, targets={"position:A": 40, "position:B": 30})
    test.assertEqual([], conflicting.legs)
    test.assertEqual("blocked-conflicting-allocation-policy", conflicting.scenarios[0].after_metrics["sizingState"])
    reserved = run({"A": 90}, targets={"position:A": 40, "position:B": 30}, cash_pct=10, floor=10)
    test.assertGreaterEqual(reserved.scenarios[1].after_metrics["cashWeightPct"], 10-1e-6)
    # Different fees/limits/balances check the invariants independently of the implementation.
    for fees in (0, 15, 250):
        for cap in (1, 20, 100):
            for cash in (0, 10, 50):
                run({"A": 100-cash}, targets={"position:A": 30, "position:B": 30, "position:C": 20},
                    cash_pct=cash, cap=cap, fees=fees, floor=10)
