"""Decision-purpose fitness for durable external facts.

Collection success and investment usability are different questions.  This
module keeps that distinction explicit without deciding an investment action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Tuple


FITNESS_CONTRACT_VERSION = "external-data-fitness-v1"
FITNESS_STATES = frozenset({
    "fresh",
    "stale",
    "partial",
    "unsupported",
    "failed",
    "not-collected",
})


def _text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _upper(value: object) -> str:
    return _text(value).upper()


def _mapping(value: object) -> Dict[str, object]:
    return dict(value or {}) if isinstance(value, Mapping) else {}


def subject_market(subject_key: str) -> str:
    key = _upper(subject_key)
    if key == "GLOBAL":
        return "GLOBAL"
    if len(key) == 6 and key.isdigit():
        return "KR"
    return "US"


@dataclass(frozen=True)
class DataPurposeContract:
    purpose: str
    label: str
    markets: Tuple[str, ...]
    datasets_by_market: Mapping[str, Tuple[str, ...]]
    minimum_sources: int = 1
    ideal_sources: int = 1

    def datasets(self, market: str) -> Tuple[str, ...]:
        return tuple(self.datasets_by_market.get(market) or self.datasets_by_market.get("ALL") or ())


PURPOSE_CONTRACTS = (
    DataPurposeContract(
        "market-price",
        "현재가·가격 흐름",
        ("KR", "US"),
        {
            "KR": ("yfinance.price", "public-data.kr-stock-daily", "alpha.quote"),
            "US": ("yfinance.price", "alpha.quote"),
        },
    ),
    DataPurposeContract(
        "valuation",
        "재무·가치평가",
        ("KR", "US"),
        {
            "KR": ("opendart.company_facts", "public-data.kr-company-financials", "yfinance.fundamental"),
            "US": ("sec.company_facts", "yfinance.fundamental"),
        },
        ideal_sources=2,
    ),
    DataPurposeContract(
        "disclosure",
        "공시·신고 사건",
        ("KR", "US"),
        {
            "KR": ("opendart.disclosures",),
            "US": ("sec.submissions",),
        },
    ),
    DataPurposeContract(
        "issuer-identity",
        "기업 식별·프로필",
        ("KR", "US"),
        {
            "KR": ("public-data.kr-security-master", "opendart.company_facts"),
            "US": ("sec.submissions", "sec.company_facts"),
        },
        ideal_sources=2,
    ),
    DataPurposeContract(
        "news",
        "기업 뉴스",
        ("KR", "US"),
        {"ALL": ("yfinance.news",)},
    ),
    DataPurposeContract(
        "analyst-consensus",
        "애널리스트 전망",
        ("KR", "US"),
        {"ALL": ("yfinance.analyst",)},
    ),
    DataPurposeContract(
        "derivatives",
        "옵션·파생시장",
        ("US",),
        {"US": ("yfinance.options",)},
    ),
    DataPurposeContract(
        "macro-regime",
        "거시 환경",
        ("GLOBAL",),
        {
            "GLOBAL": ("fred.macro", "ecos.macro", "kosis.indicators"),
        },
        ideal_sources=2,
    ),
    DataPurposeContract(
        "crypto-market",
        "가상자산 시장",
        ("GLOBAL",),
        {"GLOBAL": ("coingecko.market",)},
    ),
)


def _fact_usable(row: Mapping[str, object]) -> bool:
    quality = _mapping(row.get("quality"))
    payload_present = row.get("payloadPresent")
    if not isinstance(payload_present, bool):
        payload_present = bool(_mapping(row.get("payload")))
    return quality.get("dataUsable") is not False and payload_present


def _purpose_fitness(
    contract: DataPurposeContract,
    subject_key: str,
    facts: Iterable[Mapping[str, object]],
    provider_states: Mapping[str, Mapping[str, object]],
    enabled_datasets: set,
) -> Dict[str, object]:
    market = subject_market(subject_key)
    expected = contract.datasets(market)
    if market not in contract.markets or not expected:
        return {
            "purpose": contract.purpose,
            "label": contract.label,
            "state": "unsupported",
            "usable": False,
            "subjectKey": _upper(subject_key),
            "market": market,
            "expectedDatasets": [],
            "availableDatasets": [],
            "reason": "이 시장과 판단 목적을 지원하는 데이터 계약이 없습니다.",
        }
    enabled_expected = tuple(dataset for dataset in expected if dataset in enabled_datasets)
    matching = [
        dict(row)
        for row in facts
        if _text(row.get("datasetId")) in expected
        and _upper(row.get("subjectKey")) == _upper(subject_key)
    ]
    usable = [row for row in matching if _fact_usable(row)]
    fresh = [row for row in usable if _text(row.get("freshnessState")).lower() == "fresh"]
    stale = [row for row in usable if _text(row.get("freshnessState")).lower() == "stale"]
    failed_datasets = sorted({
        dataset
        for dataset in expected
        if _text(_mapping(provider_states.get(dataset)).get("state")).lower()
        in {"failed", "circuit_open"}
    })
    fresh_datasets = sorted({_text(row.get("datasetId")) for row in fresh})
    available_datasets = sorted({_text(row.get("datasetId")) for row in usable})
    if len(fresh_datasets) >= contract.minimum_sources:
        state = "fresh" if len(fresh_datasets) >= contract.ideal_sources else "partial"
        reason = (
            "판단에 필요한 최신 원천이 충족됐습니다."
            if state == "fresh"
            else "최소 판단 원천은 최신이지만 교차검증 원천이 부족합니다."
        )
    elif fresh:
        state = "partial"
        reason = "최신 원천이 있으나 최소 원천 수를 충족하지 못했습니다."
    elif stale:
        state = "stale"
        reason = "수집 이력은 있지만 신선도 한도를 넘었습니다."
    elif failed_datasets:
        state = "failed"
        reason = "필요한 공급자 호출이 실패했고 사용할 수 있는 현재값이 없습니다."
    else:
        state = "not-collected"
        reason = (
            "지원되는 데이터셋이 비활성화됐거나 아직 수집되지 않았습니다."
            if not enabled_expected
            else "지원되는 데이터셋이 아직 수집되지 않았습니다."
        )
    return {
        "purpose": contract.purpose,
        "label": contract.label,
        "state": state,
        "usable": state in {"fresh", "partial"},
        "subjectKey": _upper(subject_key),
        "market": market,
        "minimumSources": contract.minimum_sources,
        "idealSources": contract.ideal_sources,
        "expectedDatasets": list(expected),
        "enabledDatasets": list(enabled_expected),
        "availableDatasets": available_datasets,
        "freshDatasets": fresh_datasets,
        "staleDatasets": sorted({_text(row.get("datasetId")) for row in stale}),
        "failedDatasets": failed_datasets,
        "sourceAsOf": max((_text(row.get("sourceAsOf")) for row in usable), default=""),
        "fetchedAt": max((_text(row.get("fetchedAt")) for row in usable), default=""),
        "reason": reason,
    }


def evaluate_external_data_fitness(
    facts: Iterable[Mapping[str, object]],
    provider_statuses: Iterable[Mapping[str, object]],
    descriptors: Iterable[Mapping[str, object]],
    subject_keys: Iterable[str] = None,
    include_subjects: bool = True,
) -> Dict[str, object]:
    rows = [dict(row or {}) for row in facts or []]
    requested = sorted({_upper(item) for item in subject_keys or [] if _upper(item)})
    if not requested:
        requested = sorted({
            _upper(row.get("subjectKey"))
            for row in rows
            if _upper(row.get("subjectKey")) and _upper(row.get("subjectKey")) != "GLOBAL"
        })
    subjects = [*requested, "GLOBAL"]
    enabled_datasets = {
        _text(row.get("datasetId"))
        for row in descriptors or []
        if bool(row.get("enabled", True)) and _text(row.get("datasetId"))
    }
    provider_states = {
        _text(row.get("datasetId")): dict(row)
        for row in provider_statuses or []
        if _text(row.get("datasetId"))
    }
    by_subject: Dict[str, Dict[str, object]] = {}
    state_counts = {state: 0 for state in sorted(FITNESS_STATES)}
    for subject_key in subjects:
        market = subject_market(subject_key)
        purpose_rows = {}
        for contract in PURPOSE_CONTRACTS:
            if market == "GLOBAL" and "GLOBAL" not in contract.markets:
                continue
            if market != "GLOBAL" and "GLOBAL" in contract.markets:
                continue
            assessment = _purpose_fitness(
                contract,
                subject_key,
                rows,
                provider_states,
                enabled_datasets,
            )
            purpose_rows[contract.purpose] = assessment
            state_counts[assessment["state"]] += 1
        by_subject[subject_key] = {
            "subjectKey": subject_key,
            "market": market,
            "purposes": purpose_rows,
            "decisionReadyPurposes": sorted([
                purpose for purpose, item in purpose_rows.items() if item.get("usable")
            ]),
        }
    attention = [
        {
            "subjectKey": subject_key,
            "purpose": purpose,
            "label": item.get("label"),
            "state": item.get("state"),
            "reason": item.get("reason"),
            "expectedDatasets": item.get("expectedDatasets") or [],
        }
        for subject_key, subject in by_subject.items()
        for purpose, item in _mapping(subject.get("purposes")).items()
        if _text(item.get("state")) in {"partial", "stale", "failed", "not-collected"}
    ]
    result = {
        "contractVersion": FITNESS_CONTRACT_VERSION,
        "states": sorted(FITNESS_STATES),
        "subjectCount": len(requested),
        "stateCounts": state_counts,
        "attentionCount": len(attention),
        "attention": attention[:100],
    }
    if include_subjects:
        result["subjects"] = by_subject
    return result
