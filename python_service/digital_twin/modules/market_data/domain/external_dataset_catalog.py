"""Owned semantics for registered external datasets.

Provider adapters describe transport policy.  This catalog describes the
business-neutral data they publish, so scheduling and downstream consumers do
not branch on vendor response fields.
"""

from __future__ import annotations

from typing import Dict

from digital_twin.modules.market_data.domain.external_data_contracts import DatasetSemantics


def _spec(
    dataset_id: str,
    categories,
    output: str,
    purposes=(),
    markets=(),
    assets=(),
    *,
    basis: str = "reported",
    empty: str = "missing",
) -> DatasetSemantics:
    return DatasetSemantics(
        dataset_id=dataset_id,
        category_ids=tuple(categories),
        output_contract=output,
        purpose_ids=tuple(purposes),
        supported_markets=tuple(markets),
        asset_kinds=tuple(assets),
        evidence_basis=basis,
        source_schema_version=dataset_id + "-source-v1",
        normalizer_id=dataset_id + "-normalizer",
        normalizer_version="v1",
        empty_result_semantics=empty,
    )


_CATALOG = {
    "coingecko.market": _spec("coingecko.market", ("price_trade", "market_context"), "crypto-market-observation-v1", ("crypto-market",), ("GLOBAL",), ("crypto",)),
    "fred.macro": _spec("fred.macro", ("market_context",), "macro-series-observation-v1", ("macro-regime",), ("GLOBAL",), ("macro-series",)),
    "official.bls-release": _spec("official.bls-release", ("calendar", "market_context"), "official-release-calendar-v1", (), ("GLOBAL",), ("economic-release",)),
    "official.fomc-release": _spec("official.fomc-release", ("calendar", "market_context"), "official-release-calendar-v1", (), ("GLOBAL",), ("economic-release",)),
    "official.bok-release": _spec("official.bok-release", ("calendar", "market_context"), "official-release-calendar-v1", (), ("GLOBAL",), ("economic-release",)),
    "official.bls-statistics": _spec("official.bls-statistics", ("market_context",), "official-statistical-vintage-v1", ("macro-regime",), ("GLOBAL",), ("macro-series",)),
    "opendart.disclosures": _spec("opendart.disclosures", ("company_event",), "official-disclosure-index-v1", ("disclosure",), ("KR",), ("issuer",)),
    "opendart.document": _spec("opendart.document", ("company_event", "financial"), "official-disclosure-document-v1", ("disclosure", "valuation"), ("KR",), ("issuer",)),
    "opendart.company_facts": _spec("opendart.company_facts", ("identity", "financial"), "company-financial-facts-v1", ("issuer-identity", "valuation"), ("KR",), ("issuer",)),
    "public-data.kr-stock-daily": _spec("public-data.kr-stock-daily", ("price_trade",), "daily-price-observation-v1", ("market-price",), ("KR",), ("listing",)),
    "public-data.kr-security-master": _spec("public-data.kr-security-master", ("identity",), "security-master-v1", ("issuer-identity",), ("KR",), ("listing",)),
    "public-data.kr-market-index-daily": _spec("public-data.kr-market-index-daily", ("price_trade", "market_context"), "market-index-observation-v1", ("market-price",), ("KR",), ("market-index",)),
    "public-data.kr-company-profile": _spec("public-data.kr-company-profile", ("identity",), "company-profile-v1", ("issuer-identity",), ("KR",), ("issuer",)),
    "public-data.kr-company-financials": _spec("public-data.kr-company-financials", ("financial",), "company-financial-facts-v1", ("valuation",), ("KR",), ("issuer",)),
    "public-data.kr-dividends": _spec("public-data.kr-dividends", ("company_event", "calendar"), "corporate-action-v1", ("disclosure",), ("KR",), ("security",)),
    "public-data.kr-capital-events": _spec("public-data.kr-capital-events", ("company_event", "calendar"), "corporate-action-v1", ("disclosure",), ("KR",), ("security",)),
    "public-data.kr-shareholder-rights": _spec("public-data.kr-shareholder-rights", ("company_event", "calendar"), "shareholder-rights-event-v1", ("disclosure",), ("KR",), ("security",)),
    "sec.submissions": _spec("sec.submissions", ("company_event", "identity"), "official-filing-index-v1", ("disclosure", "issuer-identity"), ("US",), ("issuer",)),
    "sec.document": _spec("sec.document", ("company_event", "financial"), "official-filing-document-v1", ("disclosure", "valuation"), ("US",), ("issuer",)),
    "sec.company_facts": _spec("sec.company_facts", ("financial",), "company-financial-facts-v1", ("valuation",), ("US",), ("issuer",)),
    "yfinance.price": _spec("yfinance.price", ("price_trade",), "secondary-market-observation-v1", ("market-price",), ("KR", "US"), ("listing",), basis="estimated"),
    "yfinance.options": _spec("yfinance.options", ("price_trade", "market_context"), "derivatives-observation-v1", ("derivatives",), ("US",), ("option-chain",), basis="estimated", empty="unsupported"),
    "yfinance.news": _spec("yfinance.news", ("company_event",), "news-metadata-v1", ("news",), ("KR", "US"), ("issuer",), basis="estimated", empty="unsupported"),
    "yfinance.analyst": _spec("yfinance.analyst", ("expectation_valuation",), "analyst-consensus-v1", ("analyst-consensus",), ("KR", "US"), ("issuer",), basis="estimated", empty="unsupported"),
    "yfinance.fundamental": _spec("yfinance.fundamental", ("financial", "expectation_valuation"), "secondary-company-facts-v1", ("valuation",), ("KR", "US"), ("issuer",), basis="estimated"),
    "alpha.quote": _spec("alpha.quote", ("price_trade",), "secondary-market-observation-v1", ("market-price",), ("US",), ("listing",), basis="estimated"),
}


def external_dataset_semantics(dataset_id: str) -> DatasetSemantics:
    key = str(dataset_id or "").strip()
    if key not in _CATALOG:
        raise KeyError("External dataset semantics are not registered: " + key)
    return _CATALOG[key]


def external_dataset_catalog() -> Dict[str, DatasetSemantics]:
    return dict(_CATALOG)
