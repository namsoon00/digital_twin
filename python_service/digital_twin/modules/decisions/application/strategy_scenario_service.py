"""Save native strategy observations before notification eligibility is checked."""
from copy import deepcopy

from digital_twin.modules.model_registry.contracts import GraphInferenceRule, parse_strategy_contract
from digital_twin.modules.decisions.domain.strategy_scenario import observation_input


class StrategyScenarioService:
    def __init__(self, store, deployment_id, rules):
        self.store, self.deployment_id = store, deployment_id
        self.contracts = {}
        for raw in rules:
            rule = raw if isinstance(raw, GraphInferenceRule) else GraphInferenceRule.from_dict(raw)
            contract = rule.resolved_claim_contract.strategy_contract or {}
            if rule.enabled and contract.get("version") == "rule-strategy-contract-v3":
                self.contracts[contract["contractFingerprint"]] = parse_strategy_contract(contract)

    def observe(self, judgment, assessed_at):
        identity = {**judgment, "deploymentId": self.deployment_id}
        result = []
        for native in judgment.get("strategyObservations") or []:
            contract = self.contracts.get(native.get("contractFingerprint"))
            if not contract:
                continue
            value = observation_input(contract, identity, native, assessed_at)
            if value:
                result.append(self.store.record(contract, value))
            else:
                # Keep the diagnostic on the immutable subject case without
                # overwriting the last proven state or creating a customer alert.
                result.append({"version": "strategy-scenario-observation-v1",
                    **{k: identity.get(k, "") for k in ("deploymentId", "accountId", "symbol", "worldId",
                        "inferenceGenerationId", "sourceAboxSnapshotId")},
                    "contractFingerprint": contract["contractFingerprint"], "strategyId": contract["strategyId"],
                    "status": "source-clock-or-proof-unconfirmed", "notifyEligible": False,
                    "orderAuthorized": False, "tradeTimingQualified": False})
        return deepcopy(result)
