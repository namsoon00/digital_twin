"""Forward observation ledger for predictive hypotheses not yet action-qualified."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone
from typing import Dict, Mapping

from digital_twin.modules.decisions.contracts import canonical_investment_timestamp, parse_investment_timestamp, stable_id


SHADOW_HYPOTHESIS_OBSERVATION_VERSION = "shadow-hypothesis-observation-v1"


def hypothesis_observation_bucket(observed_at: str, minimum_horizon_minutes: int) -> str:
    """Cap repeated snapshots to one independent claim sample per time window."""

    observed = parse_investment_timestamp(observed_at)
    if not observed:
        return ""
    width_minutes = max(60, min(1440, int(minimum_horizon_minutes or 60)))
    utc = observed.astimezone(timezone.utc)
    minute_of_day = utc.hour * 60 + utc.minute
    bucket_minute = (minute_of_day // width_minutes) * width_minutes
    bucket_hour, bucket_minute = divmod(bucket_minute, 60)
    bucket = utc.replace(
        hour=bucket_hour,
        minute=bucket_minute,
        second=0,
        microsecond=0,
    )
    return bucket.isoformat().replace("+00:00", "Z") + "/" + str(width_minutes) + "m"


@dataclass(frozen=True)
class ShadowHypothesisObservationEpisode:
    """A research-only prediction anchor that can never authorize an action."""

    episode_id: str
    candidate_set_id: str
    account_id: str
    symbol: str
    hypothesis_id: str
    claim_identity: str
    family_id: str = ""
    claim_contract_id: str = ""
    source_abox_snapshot_id: str = ""
    inference_generation_id: str = ""
    observed_from_at: str = ""
    independence_bucket: str = ""
    market_independence_key: str = ""
    account_independence_key: str = ""
    candidate_action: str = "NO_ACTION"
    stance: str = "context"
    market: str = ""
    currency: str = ""
    outcome_contract: Dict[str, object] = field(default_factory=dict)
    hypothesis: Dict[str, object] = field(default_factory=dict)
    readiness: Dict[str, object] = field(default_factory=dict)
    input_provenance: Dict[str, object] = field(default_factory=dict)
    status: str = "scheduled"
    version: str = SHADOW_HYPOTHESIS_OBSERVATION_VERSION

    @property
    def observation_eligible(self) -> bool:
        return bool(
            self.episode_id
            and self.account_id
            and self.symbol
            and self.hypothesis_id
            and self.claim_identity
            and self.observed_from_at
            and self.independence_bucket
            and self.readiness.get("eligible") is True
            and self.outcome_contract
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "episodeKind": "shadow-hypothesis",
            "episodeId": self.episode_id,
            "candidateSetId": self.candidate_set_id,
            "accountId": self.account_id,
            "symbol": self.symbol,
            "hypothesisId": self.hypothesis_id,
            "claimIdentity": self.claim_identity,
            "familyId": self.family_id,
            "claimContractId": self.claim_contract_id,
            "sourceAboxSnapshotId": self.source_abox_snapshot_id,
            "inferenceGenerationId": self.inference_generation_id,
            "observedFromAt": self.observed_from_at,
            "independenceBucket": self.independence_bucket,
            "marketIndependenceKey": self.market_independence_key,
            "accountIndependenceKey": self.account_independence_key,
            "candidateAction": self.candidate_action,
            "stance": self.stance,
            "market": self.market,
            "currency": self.currency,
            "outcomeContract": dict(self.outcome_contract),
            "hypothesis": dict(self.hypothesis),
            "readiness": dict(self.readiness),
            "inputProvenance": dict(self.input_provenance),
            "status": self.status,
            "version": self.version,
        }

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
    ) -> "ShadowHypothesisObservationEpisode":
        payload = dict(value or {})
        outcome_contract = dict(
            payload.get("outcomeContract")
            or payload.get("outcome_contract")
            or {}
        )
        hypothesis = dict(payload.get("hypothesis") or {})
        hypothesis_id = str(
            payload.get("hypothesisId")
            or payload.get("hypothesis_id")
            or hypothesis.get("hypothesisId")
            or ""
        )
        claim_identity = str(
            payload.get("claimIdentity")
            or payload.get("claim_identity")
            or payload.get("claimContractId")
            or payload.get("claim_contract_id")
            or payload.get("familyId")
            or payload.get("family_id")
            or hypothesis_id
        )
        observed_from_at = canonical_investment_timestamp(
            payload.get("observedFromAt") or payload.get("observed_from_at")
        )
        independence_bucket = str(
            payload.get("independenceBucket")
            or payload.get("independence_bucket")
            or ""
        )
        account_id = str(payload.get("accountId") or payload.get("account_id") or "")
        symbol = str(payload.get("symbol") or "").upper()
        episode_id = str(payload.get("episodeId") or payload.get("episode_id") or "")
        if not episode_id and account_id and symbol and claim_identity and independence_bucket:
            episode_id = stable_id(
                "shadow-hypothesis-observation",
                account_id,
                symbol,
                claim_identity,
                independence_bucket,
            )
        return cls(
            episode_id=episode_id,
            candidate_set_id=str(
                payload.get("candidateSetId") or payload.get("candidate_set_id") or ""
            ),
            account_id=account_id,
            symbol=symbol,
            hypothesis_id=hypothesis_id,
            claim_identity=claim_identity,
            family_id=str(payload.get("familyId") or payload.get("family_id") or ""),
            claim_contract_id=str(
                payload.get("claimContractId") or payload.get("claim_contract_id") or ""
            ),
            source_abox_snapshot_id=str(
                payload.get("sourceAboxSnapshotId")
                or payload.get("source_abox_snapshot_id")
                or ""
            ),
            inference_generation_id=str(
                payload.get("inferenceGenerationId")
                or payload.get("inference_generation_id")
                or ""
            ),
            observed_from_at=observed_from_at,
            independence_bucket=independence_bucket,
            market_independence_key=str(
                payload.get("marketIndependenceKey")
                or payload.get("market_independence_key")
                or outcome_contract.get("marketIndependenceKey")
                or ""
            ),
            account_independence_key=str(
                payload.get("accountIndependenceKey")
                or payload.get("account_independence_key")
                or outcome_contract.get("accountIndependenceKey")
                or ""
            ),
            candidate_action=str(
                payload.get("candidateAction") or payload.get("candidate_action") or "NO_ACTION"
            ).upper(),
            stance=str(payload.get("stance") or hypothesis.get("stance") or "context").lower(),
            market=str(payload.get("market") or "").upper(),
            currency=str(payload.get("currency") or "").upper(),
            outcome_contract=outcome_contract,
            hypothesis=hypothesis,
            readiness=dict(payload.get("readiness") or {}),
            input_provenance=dict(payload.get("inputProvenance") or {}),
            status=str(payload.get("status") or "scheduled").lower(),
            version=str(payload.get("version") or SHADOW_HYPOTHESIS_OBSERVATION_VERSION),
        )
