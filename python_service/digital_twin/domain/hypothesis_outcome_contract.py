"""Review-only contracts for observing a hypothesis after a decision.

The contract belongs to the TypeDB-managed RuleBox policy.  It controls which
later facts are required to review a decision episode; it never selects an
investment action or changes an active inference result.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping


DEFAULT_OUTCOME_HORIZON_MINUTES = (60, 1440, 10080)
DEFAULT_MINIMUM_INDEPENDENT_EPISODES = 3
DEFAULT_MAXIMUM_OBSERVATION_DELAY_MINUTES = 180
DEFAULT_REQUIRED_OBSERVATION_DOMAINS = ("quote",)
SUPPORTED_OBSERVATION_DOMAINS = (
    "quote",
    "trend",
    "flow",
    "research",
    "portfolio",
    "static",
)


def text(value: object) -> str:
    return str(value or "").strip()


def unique_strings(values: Iterable[object], limit: int = 64) -> List[str]:
    rows: List[str] = []
    for value in values or []:
        item = text(value)
        if item and item not in rows:
            rows.append(item)
        if len(rows) >= limit:
            break
    return rows


def list_values(value: object) -> List[object]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if value in (None, ""):
        return []
    return [item.strip() for item in str(value).replace("\n", ",").split(",") if item.strip()]


def bounded_int(value: object, fallback: int, lower: int, upper: int) -> int:
    try:
        parsed = int(float(str(value if value is not None else fallback)))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(upper, parsed))


def normalized_horizons(value: object, fallback: Iterable[object] = None) -> List[int]:
    result: List[int] = []
    for raw in list_values(value) or list_values(fallback):
        try:
            minutes = int(float(str(raw)))
        except (TypeError, ValueError):
            continue
        if 0 < minutes <= 60 * 24 * 365 and minutes not in result:
            result.append(minutes)
    return sorted(result)


def normalized_domains(value: object) -> List[str]:
    return [
        domain
        for domain in unique_strings(str(item or "").strip().lower() for item in list_values(value))
        if domain in SUPPORTED_OBSERVATION_DOMAINS
    ]


@dataclass(frozen=True)
class HypothesisOutcomeContract:
    """A RuleBox-owned observation contract captured with a decision episode."""

    outcome_horizon_minutes: List[int] = field(default_factory=list)
    required_observation_domains: List[str] = field(default_factory=list)
    minimum_independent_episodes: int = 0
    maximum_observation_delay_minutes: int = 0
    verification_focus: List[str] = field(default_factory=list)
    evaluation_scope: str = "market-and-account-separated"

    def to_dict(self) -> Dict[str, object]:
        return {
            "outcomeHorizonMinutes": list(self.outcome_horizon_minutes or []),
            "requiredObservationDomains": list(self.required_observation_domains or []),
            "minimumIndependentEpisodes": int(self.minimum_independent_episodes or 0),
            "maximumObservationDelayMinutes": int(self.maximum_observation_delay_minutes or 0),
            "verificationFocus": list(self.verification_focus or []),
            "evaluationScope": text(self.evaluation_scope) or "market-and-account-separated",
        }

    @staticmethod
    def from_dict(payload: Mapping[str, object] = None):
        source = dict(payload or {}) if isinstance(payload, Mapping) else {}
        return HypothesisOutcomeContract(
            outcome_horizon_minutes=normalized_horizons(
                source.get("outcome_horizon_minutes", source.get("outcomeHorizonMinutes")),
            ),
            required_observation_domains=normalized_domains(
                source.get("required_observation_domains", source.get("requiredObservationDomains"))
            ),
            minimum_independent_episodes=bounded_int(
                source.get("minimum_independent_episodes", source.get("minimumIndependentEpisodes")),
                0,
                0,
                1000,
            ),
            maximum_observation_delay_minutes=bounded_int(
                source.get("maximum_observation_delay_minutes", source.get("maximumObservationDelayMinutes")),
                0,
                0,
                60 * 24 * 14,
            ),
            verification_focus=unique_strings(
                list_values(source.get("verification_focus", source.get("verificationFocus")))
            ),
            evaluation_scope=text(
                source.get("evaluation_scope", source.get("evaluationScope"))
            ) or "market-and-account-separated",
        )

    def resolved(
        self,
        fallback_horizons: Iterable[object] = None,
        fallback_minimum_samples: int = DEFAULT_MINIMUM_INDEPENDENT_EPISODES,
        fallback_maximum_delay_minutes: int = DEFAULT_MAXIMUM_OBSERVATION_DELAY_MINUTES,
        fallback_required_domains: Iterable[object] = DEFAULT_REQUIRED_OBSERVATION_DOMAINS,
    ) -> "HypothesisOutcomeContract":
        return HypothesisOutcomeContract(
            outcome_horizon_minutes=normalized_horizons(
                self.outcome_horizon_minutes,
                fallback_horizons or DEFAULT_OUTCOME_HORIZON_MINUTES,
            ),
            required_observation_domains=(
                normalized_domains(self.required_observation_domains)
                or normalized_domains(fallback_required_domains)
                or list(DEFAULT_REQUIRED_OBSERVATION_DOMAINS)
            ),
            minimum_independent_episodes=bounded_int(
                self.minimum_independent_episodes or fallback_minimum_samples,
                DEFAULT_MINIMUM_INDEPENDENT_EPISODES,
                1,
                1000,
            ),
            maximum_observation_delay_minutes=bounded_int(
                self.maximum_observation_delay_minutes or fallback_maximum_delay_minutes,
                DEFAULT_MAXIMUM_OBSERVATION_DELAY_MINUTES,
                1,
                60 * 24 * 14,
            ),
            verification_focus=unique_strings(self.verification_focus),
            evaluation_scope="market-and-account-separated",
        )


def resolved_outcome_contract(
    value: Mapping[str, object] = None,
    fallback_horizons: Iterable[object] = None,
    fallback_minimum_samples: int = DEFAULT_MINIMUM_INDEPENDENT_EPISODES,
    fallback_maximum_delay_minutes: int = DEFAULT_MAXIMUM_OBSERVATION_DELAY_MINUTES,
    fallback_required_domains: Iterable[object] = DEFAULT_REQUIRED_OBSERVATION_DOMAINS,
) -> Dict[str, object]:
    return HypothesisOutcomeContract.from_dict(value).resolved(
        fallback_horizons=fallback_horizons,
        fallback_minimum_samples=fallback_minimum_samples,
        fallback_maximum_delay_minutes=fallback_maximum_delay_minutes,
        fallback_required_domains=fallback_required_domains,
    ).to_dict()


def merge_outcome_contracts(
    contracts: Iterable[Mapping[str, object]],
    fallback_horizons: Iterable[object] = None,
    fallback_minimum_samples: int = DEFAULT_MINIMUM_INDEPENDENT_EPISODES,
    fallback_maximum_delay_minutes: int = DEFAULT_MAXIMUM_OBSERVATION_DELAY_MINUTES,
    fallback_required_domains: Iterable[object] = DEFAULT_REQUIRED_OBSERVATION_DOMAINS,
) -> Dict[str, object]:
    rows = [
        HypothesisOutcomeContract.from_dict(item)
        for item in contracts or []
        if isinstance(item, Mapping)
    ]
    if not rows:
        return resolved_outcome_contract(
            {},
            fallback_horizons,
            fallback_minimum_samples,
            fallback_maximum_delay_minutes,
            fallback_required_domains,
        )
    horizons = normalized_horizons(
        [minutes for row in rows for minutes in row.outcome_horizon_minutes],
        fallback_horizons or DEFAULT_OUTCOME_HORIZON_MINUTES,
    )
    domains = normalized_domains([
        domain
        for row in rows
        for domain in row.required_observation_domains
    ]) or normalized_domains(fallback_required_domains) or list(DEFAULT_REQUIRED_OBSERVATION_DOMAINS)
    minimums = [row.minimum_independent_episodes for row in rows if row.minimum_independent_episodes > 0]
    delays = [row.maximum_observation_delay_minutes for row in rows if row.maximum_observation_delay_minutes > 0]
    focus = unique_strings(value for row in rows for value in row.verification_focus)
    return HypothesisOutcomeContract(
        outcome_horizon_minutes=horizons,
        required_observation_domains=domains,
        minimum_independent_episodes=max(minimums or [fallback_minimum_samples]),
        maximum_observation_delay_minutes=min(delays or [fallback_maximum_delay_minutes]),
        verification_focus=focus,
        evaluation_scope="market-and-account-separated",
    ).resolved(
        fallback_horizons=fallback_horizons,
        fallback_minimum_samples=fallback_minimum_samples,
        fallback_maximum_delay_minutes=fallback_maximum_delay_minutes,
        fallback_required_domains=fallback_required_domains,
    ).to_dict()


def observation_domain_status(
    facts: Mapping[str, object],
    contract: Mapping[str, object],
) -> Dict[str, object]:
    source = dict(facts or {}) if isinstance(facts, Mapping) else {}
    resolved = resolved_outcome_contract(contract)
    required = list(resolved.get("requiredObservationDomains") or [])

    def has_value(*keys: str) -> bool:
        return any(key in source and source.get(key) not in (None, "") for key in keys)

    availability = {
        "quote": bool(number(source.get("currentPrice")) > 0),
        "trend": has_value("ma5", "ma20", "ma60", "ma20Slope", "ma60Slope"),
        "flow": has_value("volume", "tradeStrength", "foreignNetVolume", "institutionNetVolume", "individualNetVolume"),
        "research": has_value("researchEvidence", "verifiedClaims", "disclosureIds"),
        "portfolio": has_value("profitLossRate", "quantity", "averagePrice"),
        "static": True,
    }
    missing = [domain for domain in required if not availability.get(domain, False)]
    return {
        "requiredObservationDomains": required,
        "availableObservationDomains": [domain for domain in required if availability.get(domain, False)],
        "missingObservationDomains": missing,
        "observationDomainAvailability": availability,
        "observationDataState": "sufficient" if not missing else "partial",
    }


def number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
