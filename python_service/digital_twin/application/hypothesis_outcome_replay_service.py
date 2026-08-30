"""Read-only replay of persisted hypothesis outcome observations."""

from typing import Dict, Iterable, Mapping

from ..domain.investment_brain import parse_investment_timestamp


def as_dict(value: object) -> Dict[str, object]:
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return dict(payload or {}) if isinstance(payload, dict) else {}
    return dict(value or {}) if isinstance(value, Mapping) else {}


class HypothesisOutcomeReplayService:
    """Verifies stored actual observations without rerunning investment judgement."""

    def __init__(self, decision_episode_store=None, hypothesis_review_service=None, quality_review_service=None):
        self.decision_episode_store = decision_episode_store
        self.hypothesis_review_service = hypothesis_review_service
        self.quality_review_service = quality_review_service

    def run(self, account_id: str = "", symbol: str = "", limit: int = 500) -> Dict[str, object]:
        if not self.decision_episode_store or not hasattr(self.decision_episode_store, "list"):
            return {
                "status": "unavailable",
                "reason": "결정 에피소드 저장소가 구성되지 않았습니다.",
                "mutated": False,
            }
        rows = self.decision_episode_store.list(account_id=account_id, symbol=symbol, limit=max(1, min(2000, int(limit or 500))))
        episodes = [as_dict(item) for item in rows]
        integrity = self.integrity(episodes)
        # Episode integrity can scan a broad history, while graph-backed
        # hypothesis details stay bounded so the replay queue remains finite.
        assessment_limit = min(25, max(1, int(limit or 500)))
        workspace = self.hypothesis_review_service.workspace(
            account_id=account_id,
            symbol=symbol,
            limit=assessment_limit,
            event_limit=0,
        ) if self.hypothesis_review_service else {"items": []}
        quality = self.quality_review_service.assess(workspace) if self.quality_review_service else {}
        observed = integrity["outcomeCount"]
        performance = self.performance(episodes)
        return {
            "status": "completed" if episodes else "no-history",
            "source": "persisted-decision-episode+observed-outcome-replay",
            "mutated": False,
            "automaticDeployment": False,
            "decisionEligibility": "historical-replay-only",
            "accountId": account_id,
            "symbol": str(symbol or "").upper(),
            "episodeCount": len(episodes),
            "outcomeCount": observed,
            "integrity": integrity,
            "hypothesisAssessments": list(workspace.get("items") or [])[:100],
            "hypothesisAssessmentLimit": assessment_limit,
            "qualityReview": quality,
            "performanceByHypothesis": performance["hypotheses"],
            "performanceByRule": performance["rules"],
            "performanceSummary": performance["summary"],
            "summary": self.summary(len(episodes), integrity, quality),
        }

    def performance(self, episodes: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        """Aggregate observed outcomes without inventing deployment thresholds."""

        hypotheses: Dict[str, Dict[str, object]] = {}
        rules: Dict[str, Dict[str, object]] = {}

        def observe(bucket: Dict[str, Dict[str, object]], key: str, status: str, eligible: bool):
            if not key:
                return
            row = bucket.setdefault(key, {
                "id": key,
                "observedCount": 0,
                "eligibleCount": 0,
                "corroboratedCount": 0,
                "contradictedCount": 0,
                "inconclusiveCount": 0,
            })
            row["observedCount"] += 1
            if eligible:
                row["eligibleCount"] += 1
            if "corroborated" in status:
                row["corroboratedCount"] += 1
            elif "contradicted" in status:
                row["contradictedCount"] += 1
            else:
                row["inconclusiveCount"] += 1

        for raw in episodes or []:
            episode = as_dict(raw)
            hypothesis_id = str(episode.get("selectedHypothesisId") or "").strip()
            facts = episode.get("factsAtDecision") if isinstance(episode.get("factsAtDecision"), Mapping) else {}
            contract = facts.get("hypothesisOutcomeContract") if isinstance(facts.get("hypothesisOutcomeContract"), Mapping) else {}
            rule_ids = [str(item or "").strip() for item in contract.get("sourceRuleIds") or [] if str(item or "").strip()]
            for raw_outcome in episode.get("outcomes") or []:
                outcome = as_dict(raw_outcome)
                payload = outcome.get("payload") if isinstance(outcome.get("payload"), Mapping) else {}
                status = str(
                    outcome.get("selectedHypothesisStatus")
                    or payload.get("selectedHypothesisStatus")
                    or "inconclusive"
                ).lower()
                eligible = str(payload.get("calibrationEligibility") or "") == "eligible"
                observe(hypotheses, hypothesis_id, status, eligible)
                for rule_id in rule_ids:
                    observe(rules, rule_id, status, eligible)

        def rows(bucket: Dict[str, Dict[str, object]]):
            values = []
            for row in bucket.values():
                conclusive = int(row["corroboratedCount"]) + int(row["contradictedCount"])
                values.append({
                    **row,
                    "conclusiveCount": conclusive,
                    "corroborationRate": (
                        round(int(row["corroboratedCount"]) / conclusive, 4)
                        if conclusive else None
                    ),
                    "automaticDeployment": False,
                })
            return sorted(
                values,
                key=lambda item: (-int(item["eligibleCount"]), -int(item["conclusiveCount"]), item["id"]),
            )[:100]

        hypothesis_rows = rows(hypotheses)
        rule_rows = rows(rules)
        return {
            "hypotheses": hypothesis_rows,
            "rules": rule_rows,
            "summary": {
                "hypothesisCount": len(hypotheses),
                "ruleCount": len(rules),
                "observedHypothesisCount": len(hypothesis_rows),
                "observedRuleCount": len(rule_rows),
                "thresholdPolicyApplied": False,
                "automaticDeployment": False,
            },
        }

    def integrity(self, episodes: Iterable[Mapping[str, object]]) -> Dict[str, object]:
        outcome_count = 0
        eligible_count = 0
        excluded_count = 0
        contract_snapshot_count = 0
        legacy_contract_count = 0
        structured_contract_count = 0
        fingerprint_contract_count = 0
        legacy_directional_outcome_count = 0
        criterion_data_gap_outcome_count = 0
        duplicate_keys = []
        duplicate_independence_keys = []
        invalid_time_episode_ids = []
        seen = set()
        seen_independence = set()
        exclusion_reasons: Dict[str, int] = {}
        for raw in episodes or []:
            episode = as_dict(raw)
            episode_id = str(episode.get("episodeId") or "")
            facts = episode.get("factsAtDecision") if isinstance(episode.get("factsAtDecision"), Mapping) else {}
            contract = facts.get("hypothesisOutcomeContract") if isinstance(facts.get("hypothesisOutcomeContract"), Mapping) else {}
            if contract:
                contract_snapshot_count += 1
                if contract.get("criteria"):
                    structured_contract_count += 1
                if contract.get("contractFingerprint"):
                    fingerprint_contract_count += 1
            else:
                legacy_contract_count += 1
            decided_at = parse_investment_timestamp(episode.get("decidedAt"))
            for raw_outcome in episode.get("outcomes") or []:
                outcome = as_dict(raw_outcome)
                payload = outcome.get("payload") if isinstance(outcome.get("payload"), Mapping) else {}
                outcome_count += 1
                horizon = str(payload.get("horizonMinutes") or "")
                key = episode_id + "|" + horizon
                if episode_id and horizon and key in seen:
                    duplicate_keys.append(key)
                seen.add(key)
                observed_at = parse_investment_timestamp(outcome.get("observedAt"))
                if decided_at and observed_at and observed_at < decided_at and episode_id not in invalid_time_episode_ids:
                    invalid_time_episode_ids.append(episode_id)
                eligibility = str(payload.get("calibrationEligibility") or "")
                if str(payload.get("mode") or "legacy-directional-fallback") == "legacy-directional-fallback":
                    legacy_directional_outcome_count += 1
                if payload.get("missingRequiredMetricIds"):
                    criterion_data_gap_outcome_count += 1
                independence_key = str(
                    payload.get("accountIndependenceKey")
                    or payload.get("marketIndependenceKey")
                    or episode_id
                )
                independence_horizon_key = independence_key + "|" + horizon
                if independence_key and horizon and independence_horizon_key in seen_independence:
                    duplicate_independence_keys.append(independence_horizon_key)
                seen_independence.add(independence_horizon_key)
                if eligibility == "eligible":
                    eligible_count += 1
                else:
                    excluded_count += 1
                    reason = eligibility or "legacy-eligibility-not-recorded"
                    exclusion_reasons[reason] = int(exclusion_reasons.get(reason) or 0) + 1
        return {
            "outcomeCount": outcome_count,
            "eligibleOutcomeCount": eligible_count,
            "excludedOutcomeCount": excluded_count,
            "exclusionReasons": exclusion_reasons,
            "contractSnapshotEpisodeCount": contract_snapshot_count,
            "legacyContractEpisodeCount": legacy_contract_count,
            "structuredContractEpisodeCount": structured_contract_count,
            "fingerprintedContractEpisodeCount": fingerprint_contract_count,
            "legacyDirectionalOutcomeCount": legacy_directional_outcome_count,
            "criterionDataGapOutcomeCount": criterion_data_gap_outcome_count,
            "duplicateEpisodeHorizonKeys": duplicate_keys[:100],
            "repeatedIndependenceHorizonKeys": duplicate_independence_keys[:100],
            "futureOrInvalidObservationEpisodeIds": invalid_time_episode_ids[:100],
            "scopeSeparation": "market-and-account-lifecycles-reviewed-separately",
            "passed": not duplicate_keys and not invalid_time_episode_ids,
            "migrationState": (
                "structured-contract-ready"
                if contract_snapshot_count and structured_contract_count == contract_snapshot_count
                else "legacy-contracts-retained"
            ),
        }

    def summary(self, episode_count: int, integrity: Mapping[str, object], quality: Mapping[str, object]) -> str:
        if not episode_count:
            return "재생할 결정 에피소드가 아직 없습니다."
        if not bool(integrity.get("passed")):
            return "저장된 관측의 중복 또는 시각 순서를 확인해야 합니다. 투자 판단에는 사용하지 않습니다."
        required = ((quality.get("summary") or {}).get("reviewRequiredCount") if isinstance(quality, Mapping) else 0) or 0
        if required:
            return "저장된 실제 관측은 일관되지만, 가설 품질 검토가 필요한 항목이 " + str(required) + "건 있습니다."
        return "저장된 실제 관측의 시각·중복·계정/시장 분리 검사를 통과했습니다."
