"""Review-only contracts for observing a hypothesis after a decision.

The contract belongs to the TypeDB-managed RuleBox policy.  It controls which
later facts are required to review a decision episode; it never selects an
investment action or changes an active inference result.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping


DEFAULT_OUTCOME_HORIZON_MINUTES = (60, 1440, 10080)
DEFAULT_MINIMUM_INDEPENDENT_EPISODES = 3
DEFAULT_MAXIMUM_OBSERVATION_DELAY_MINUTES = 180
DEFAULT_REQUIRED_OBSERVATION_DOMAINS = ("quote",)
HYPOTHESIS_OUTCOME_CONTRACT_VERSION = "rulebox-hypothesis-outcome-contract-v2"
SUPPORTED_OBSERVATION_DOMAINS = (
    "quote",
    "trend",
    "flow",
    "research",
    "portfolio",
    "static",
)
SUPPORTED_OUTCOME_CRITERION_ROLES = ("cause", "result", "invalidation", "context")
SUPPORTED_OUTCOME_CRITERION_OPERATORS = (">", ">=", "<", "<=", "==", "!=")
SUPPORTED_OUTCOME_CRITERION_FAILURE_OUTCOMES = ("contradicted", "inconclusive")
SUPPORTED_OUTCOME_CRITERION_METRICS = (
    "instrumentReturnPct",
    "benchmarkReturnPct",
    "excessReturnPct",
    "profitLossRate",
    "volumeRatio",
    "tradeStrength",
    "foreignNetVolume",
    "institutionNetVolume",
    "individualNetVolume",
    "shareCountChangePct",
    "freeCashFlowChangePct",
    "verifiedEventCount",
    "counterEvidenceCount",
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


def number(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else fallback)
    except (TypeError, ValueError):
        return fallback


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
class HypothesisOutcomeCriterion:
    """One falsifiable check captured before an outcome is observed."""

    criterion_id: str
    label: str
    role: str = "result"
    metric: str = "instrumentReturnPct"
    operator: str = ">="
    threshold: float = 0.0
    unit: str = "%"
    horizon_minutes: int = 0
    required: bool = True
    benchmark_symbol: str = ""
    required_observation_domains: List[str] = field(default_factory=list)
    source_policy: List[str] = field(default_factory=list)
    failure_outcome: str = "contradicted"

    def to_dict(self) -> Dict[str, object]:
        return {
            "criterionId": text(self.criterion_id),
            "label": text(self.label),
            "role": text(self.role) or "result",
            "metric": text(self.metric) or "instrumentReturnPct",
            "operator": text(self.operator) or ">=",
            "threshold": float(self.threshold or 0.0),
            "unit": text(self.unit) or "%",
            "horizonMinutes": int(self.horizon_minutes or 0),
            "required": bool(self.required),
            "benchmarkSymbol": text(self.benchmark_symbol).upper(),
            "requiredObservationDomains": list(self.required_observation_domains or []),
            "sourcePolicy": list(self.source_policy or []),
            "failureOutcome": text(self.failure_outcome) or "contradicted",
        }

    @staticmethod
    def from_dict(payload: Mapping[str, object], index: int = 0):
        source = dict(payload or {}) if isinstance(payload, Mapping) else {}
        metric = text(source.get("metric"))
        if metric not in SUPPORTED_OUTCOME_CRITERION_METRICS:
            metric = ""
        role = text(source.get("role")).lower()
        if role not in SUPPORTED_OUTCOME_CRITERION_ROLES:
            role = "result"
        operator = text(source.get("operator"))
        if operator not in SUPPORTED_OUTCOME_CRITERION_OPERATORS:
            operator = ">="
        criterion_id = text(source.get("criterionId") or source.get("criterion_id"))
        failure_outcome = text(
            source.get("failureOutcome") or source.get("failure_outcome")
        ).lower()
        if failure_outcome not in SUPPORTED_OUTCOME_CRITERION_FAILURE_OUTCOMES:
            failure_outcome = "contradicted"
        return HypothesisOutcomeCriterion(
            criterion_id=criterion_id or ("criterion-" + str(index + 1)),
            label=text(source.get("label")) or metric or "사후 검증 기준",
            role=role,
            metric=metric,
            operator=operator,
            threshold=number(source.get("threshold")),
            unit=text(source.get("unit")) or "%",
            horizon_minutes=bounded_int(
                source.get("horizonMinutes", source.get("horizon_minutes")),
                0,
                0,
                60 * 24 * 365,
            ),
            required=bool(source.get("required", True)),
            benchmark_symbol=text(
                source.get("benchmarkSymbol") or source.get("benchmark_symbol")
            ).upper(),
            required_observation_domains=normalized_domains(
                source.get("requiredObservationDomains", source.get("required_observation_domains"))
            ),
            source_policy=unique_strings(list_values(
                source.get("sourcePolicy", source.get("source_policy"))
            )),
            failure_outcome=failure_outcome,
        )


def normalized_criteria(value: object) -> List[HypothesisOutcomeCriterion]:
    source = value if isinstance(value, (list, tuple)) else []
    result: List[HypothesisOutcomeCriterion] = []
    ids = set()
    for index, raw in enumerate(source[:64]):
        if not isinstance(raw, Mapping):
            continue
        criterion = HypothesisOutcomeCriterion.from_dict(raw, index)
        if not criterion.metric:
            continue
        base = criterion.criterion_id
        candidate = base
        suffix = 2
        while candidate in ids:
            candidate = base + "-" + str(suffix)
            suffix += 1
        if candidate != criterion.criterion_id:
            criterion = HypothesisOutcomeCriterion(
                **{**criterion.__dict__, "criterion_id": candidate}
            )
        ids.add(candidate)
        result.append(criterion)
    return result


def default_directional_criteria(stance: str, material_move_pct: float = 0.5) -> List[Dict[str, object]]:
    """Create an explicit conservative fallback for a newly frozen contract."""

    normalized_stance = text(stance).lower()
    if normalized_stance not in {"support", "risk"}:
        return []
    risk = normalized_stance == "risk"
    return [
        HypothesisOutcomeCriterion(
            criterion_id="default-material-direction",
            label="판단 이후 유의한 가격 " + ("하락" if risk else "상승"),
            role="result",
            metric="instrumentReturnPct",
            operator="<=" if risk else ">=",
            threshold=-abs(material_move_pct) if risk else abs(material_move_pct),
            unit="%",
            required=True,
            required_observation_domains=["quote"],
            failure_outcome="inconclusive",
        ).to_dict(),
        HypothesisOutcomeCriterion(
            criterion_id="default-material-opposite-direction",
            label="가설과 반대인 유의한 가격 " + ("상승" if risk else "하락"),
            role="invalidation",
            metric="instrumentReturnPct",
            operator=">=" if risk else "<=",
            threshold=abs(material_move_pct) if risk else -abs(material_move_pct),
            unit="%",
            required=True,
            required_observation_domains=["quote"],
        ).to_dict(),
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
    criteria: List[HypothesisOutcomeCriterion] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "outcomeHorizonMinutes": list(self.outcome_horizon_minutes or []),
            "requiredObservationDomains": list(self.required_observation_domains or []),
            "minimumIndependentEpisodes": int(self.minimum_independent_episodes or 0),
            "maximumObservationDelayMinutes": int(self.maximum_observation_delay_minutes or 0),
            "verificationFocus": list(self.verification_focus or []),
            "evaluationScope": text(self.evaluation_scope) or "market-and-account-separated",
            "criteria": [item.to_dict() for item in self.criteria or []],
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
            criteria=normalized_criteria(source.get("criteria")),
        )

    def resolved(
        self,
        fallback_horizons: Iterable[object] = None,
        fallback_minimum_samples: int = DEFAULT_MINIMUM_INDEPENDENT_EPISODES,
        fallback_maximum_delay_minutes: int = DEFAULT_MAXIMUM_OBSERVATION_DELAY_MINUTES,
        fallback_required_domains: Iterable[object] = DEFAULT_REQUIRED_OBSERVATION_DOMAINS,
    ) -> "HypothesisOutcomeContract":
        criteria = list(self.criteria or [])
        criterion_domains = normalized_domains([
            domain
            for criterion in criteria
            for domain in criterion.required_observation_domains
        ])
        return HypothesisOutcomeContract(
            outcome_horizon_minutes=normalized_horizons(
                self.outcome_horizon_minutes,
                fallback_horizons or DEFAULT_OUTCOME_HORIZON_MINUTES,
            ),
            required_observation_domains=(
                normalized_domains([
                    *list(self.required_observation_domains or []),
                    *criterion_domains,
                ])
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
            criteria=criteria,
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


def outcome_contract_completeness(value: Mapping[str, object] = None) -> Dict[str, object]:
    """Validate an authored prediction contract without applying fallbacks."""

    source = dict(value or {}) if isinstance(value, Mapping) else {}
    parsed = HypothesisOutcomeContract.from_dict(source)
    missing = []
    if text(source.get("criteriaOrigin")) != "rulebox":
        missing.append("rulebox-criteria-origin")
    if not text(source.get("contractVersion")):
        missing.append("contract-version")
    if not text(source.get("contractFingerprint")):
        missing.append("contract-fingerprint")
    if not text(source.get("selectedHypothesisId")):
        missing.append("selected-hypothesis")
    if not unique_strings(list_values(source.get("sourceRuleIds"))):
        missing.append("source-rule")
    if not text(source.get("inferenceGenerationId")):
        missing.append("inference-generation")
    if not parsed.outcome_horizon_minutes:
        missing.append("outcome-horizon")
    if not parsed.required_observation_domains:
        missing.append("observation-domain")
    if parsed.minimum_independent_episodes <= 0:
        missing.append("minimum-independent-episodes")
    if parsed.maximum_observation_delay_minutes <= 0:
        missing.append("maximum-observation-delay")
    if not parsed.criteria:
        missing.append("outcome-criteria")
    directional = [
        criterion
        for criterion in parsed.criteria
        if criterion.required and criterion.role in {"result", "invalidation"}
    ]
    if not directional:
        missing.append("directional-result-or-invalidation-criterion")
    for field_name, key in [
        ("prediction-target", "predictionTarget"),
        ("expected-direction", "expectedDirection"),
        ("outcome-metric", "outcomeMetric"),
        ("falsification-contract", "falsificationContract"),
    ]:
        if not text(source.get(key)):
            missing.append(field_name)
    return {
        "complete": not missing,
        "missing": missing,
        "criteriaCount": len(parsed.criteria),
        "directionalCriterionCount": len(directional),
    }


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
    criteria = normalized_criteria([
        criterion.to_dict()
        for row in rows
        for criterion in row.criteria
    ])
    return HypothesisOutcomeContract(
        outcome_horizon_minutes=horizons,
        required_observation_domains=domains,
        minimum_independent_episodes=max(minimums or [fallback_minimum_samples]),
        maximum_observation_delay_minutes=min(delays or [fallback_maximum_delay_minutes]),
        verification_focus=focus,
        evaluation_scope="market-and-account-separated",
        criteria=criteria,
    ).resolved(
        fallback_horizons=fallback_horizons,
        fallback_minimum_samples=fallback_minimum_samples,
        fallback_maximum_delay_minutes=fallback_maximum_delay_minutes,
        fallback_required_domains=fallback_required_domains,
    ).to_dict()


def outcome_contract_fingerprint(value: Mapping[str, object]) -> str:
    """Hash only immutable evaluation semantics, excluding episode metadata."""

    resolved = resolved_outcome_contract(value)
    canonical = {
        "outcomeHorizonMinutes": resolved.get("outcomeHorizonMinutes") or [],
        "requiredObservationDomains": resolved.get("requiredObservationDomains") or [],
        "minimumIndependentEpisodes": resolved.get("minimumIndependentEpisodes"),
        "maximumObservationDelayMinutes": resolved.get("maximumObservationDelayMinutes"),
        "verificationFocus": resolved.get("verificationFocus") or [],
        "evaluationScope": resolved.get("evaluationScope"),
        "criteria": resolved.get("criteria") or [],
    }
    body = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


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
