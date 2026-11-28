"""Register and reproduce bounded studies against one immutable RuleBox release."""

from datetime import date
import re

from digital_twin.modules.decisions.contracts import strategy_scenario_key
from digital_twin.modules.model_registry.contracts import GraphInferenceRule, parse_strategy_contract
from digital_twin.modules.reasoning.contracts import portfolio_world_id
from digital_twin.shared_kernel.clock import utc_now_iso

from ..domain.price_recovery_paper import (
    HOLDING_WINDOW, MARKETS, QUOTE_WINDOW, VERSION, clock, comparison,
    measured_signal, session_trials, settle_trial, study_window, utc,
)


MAX_OBSERVATIONS = 3000


class PriceRecoveryPaperService:
    def __init__(self, releases, scenarios=None, quotes=None, now=utc_now_iso):
        self.releases, self.scenarios, self.quotes, self.now = releases, scenarios, quotes, now

    def prepare(self, request):
        """Freeze the strategy, research assumptions and observation dates at registration."""
        account = str(request.get("accountId") or "").strip()
        symbol = str(request.get("symbol") or "").strip().upper()
        market = str(request.get("market") or "").strip().upper()
        mode = request.get("mode", "historical")
        cost = request.get("costBpsPerSide")
        if not account or len(account) > 191 or not re.fullmatch(r"[A-Z0-9^.:_-]{1,64}", symbol) or market not in MARKETS:
            raise ValueError("accountId, symbol and market (KR or US) are required.")
        if mode not in ("historical", "forward") or type(cost) is not int or not 0 <= cost <= 500:
            raise ValueError("Supply a study mode and costBpsPerSide (integer 0–500).")
        try:
            start_date = date.fromisoformat(str(request.get("startDate") or "")).isoformat()
            end_date = date.fromisoformat(str(request.get("endDate") or "")).isoformat()
        except ValueError as error:
            raise ValueError("Supply startDate and endDate as YYYY-MM-DD in the market calendar.") from error
        start, end = study_window(market, start_date, end_date)
        registered = clock(self.now())
        if mode == "forward" and start <= registered:
            raise ValueError("Forward studies must be registered before their first market date.")
        if mode == "historical" and end > registered:
            raise ValueError("Historical studies require completed market dates.")
        world = str(request.get("worldId") or portfolio_world_id(account)).strip()
        deployment = str(request.get("deploymentId") or "").strip() or self.releases.control().delivery_deployment_id
        if not deployment or not world or len(world) > 191 or len(deployment) > 191:
            raise ValueError("An exact world and registered RuleBox deployment are required.")
        receipt = self.releases.release_artifact(deployment)
        if not receipt.get("valid") or receipt.get("deploymentId") != deployment:
            raise ValueError("The requested deployment has no verified RuleBox artifact.")
        contract, rules = self._price_recovery_strategy(receipt["artifact"]["rules"], market)
        scope = {"accountId": account, "symbol": symbol, "worldId": world,
                 "deploymentId": deployment, "contractFingerprint": contract["contractFingerprint"]}
        return {
            **scope, "market": market, "mode": mode, "startDate": start_date, "endDate": end_date,
            "costBpsPerSide": cost, "runNotBefore": utc(end), "protocol": VERSION,
            "scenarioKey": strategy_scenario_key(scope),
            "strategyId": contract["strategyId"], "strategyLabel": contract["label"], "phaseRuleIds": rules,
            "signalValiditySeconds": contract["signalValiditySeconds"],
            "artifactFingerprint": receipt["artifactFingerprint"], "ruleboxFingerprint": receipt["ruleboxFingerprint"],
            "holdingWindowMinutes": int(HOLDING_WINDOW.total_seconds() / 60),
            "quoteWindowMinutes": int(QUOTE_WINDOW.total_seconds() / 60),
        }

    @staticmethod
    def _price_recovery_strategy(rows, market):
        contracts = {}
        for raw in rows:
            if not str(raw.get("rule_id") or raw.get("ruleId") or "").startswith("graph.strategy."):
                continue
            rule = GraphInferenceRule.from_dict(raw)
            claim = rule.resolved_claim_contract
            contract = claim.strategy_contract or {}
            if not (rule.enabled and contract.get("market") == market and contract.get("mode") == "live-observation"
                    and contract.get("version") == "rule-strategy-contract-v3"
                    and (contract.get("scenarioContract") or {}).get("version") == "strategy-scenario-continuity-v1"):
                continue
            _, phases = contracts.setdefault(contract["contractFingerprint"], (contract, {}))
            phases[claim.strategy_phase] = rule.rule_id
        if len(contracts) != 1:
            raise ValueError("The release must contain exactly one price-recovery scenario for the market.")
        contract, rules = next(iter(contracts.values()))
        if set(rules) != {"setup", "entry", "invalidation", "exit"}:
            raise ValueError("The frozen price-recovery strategy is missing enabled phases.")
        return parse_strategy_contract(contract), rules

    def run(self, study, registered_at):
        start, end = study_window(study["market"], study["startDate"], study["endDate"])
        if (study["protocol"] != VERSION or clock(self.now()) < end
                or (study["mode"] == "forward" and clock(registered_at) >= start)):
            raise ValueError("The study protocol or registration window is invalid.")
        rows = self.scenarios.study_observations(study["scenarioKey"], start, end, MAX_OBSERVATIONS + 1)
        if len(rows) > MAX_OBSERVATIONS:
            raise ValueError("Too many scenario observations. Use a shorter study window.")
        signals = []
        for row in rows:
            signal = measured_signal(row, study)
            if signal and start <= clock(signal["knownAt"]) < end:
                signals.append(signal)
        trials = []
        for trial in session_trials(signals):
            first = clock(trial["setup"]["knownAt"])
            last = min(clock(trial["exitAt"]) + QUOTE_WINDOW, end)
            quotes = self.quotes.load_paper_quote_window(study["accountId"], study["symbol"], first, last)
            trials.append(settle_trial(trial, study, quotes))
        summary = comparison(trials)
        if not signals:
            gap = "no-verified-strategy-observations"
        elif not trials:
            gap = "no-observed-setup-in-window"
        else:
            gap = "" if summary["pairedCount"] else "incomplete-quote-coverage"
        return {
            "status": "measured" if summary["pairedCount"] else "no-comparable-results",
            "coverageGap": gap,
            "study": study, "registeredAt": registered_at, "observationCount": len(rows),
            "usableSignalCount": len(signals), "summary": summary, "ledger": trials,
            "executionBasis": "stored-account-quote-proxy",
            "decisionEligibility": "research-only", "tradeTimingQualified": False, "orderAuthorized": False,
        }
