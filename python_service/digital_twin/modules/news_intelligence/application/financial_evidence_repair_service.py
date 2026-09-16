"""Rebuild derived facts from cached sources; never rewrite past decisions."""

from digital_twin.modules.news_intelligence.domain.company_knowledge import (
    COMPANY_KNOWLEDGE_CACHE_VERSION, company_knowledge_by_symbol, merge_company_knowledge_rows,
)
from digital_twin.modules.news_intelligence.domain.financial_reporting import compact_financial_evidence


def financial_repair_plan(cache, signals):
    old = cache.get("symbols") or {}
    symbols = set(old)
    for field in ("yfinanceData", "dartDisclosures", "secFilings", "companyKnowledge"):
        symbols.update((signals.get(field) or {}).keys())
    rebuilt = company_knowledge_by_symbol(signals, symbols)
    result, changes = dict(old), []
    for symbol, row in rebuilt.items():
        row = merge_company_knowledge_rows(old.get(symbol, {}), (signals.get("companyKnowledge") or {}).get(symbol, {}), row)
        before, after = compact_financial_evidence(old.get(symbol, {})), compact_financial_evidence(row)
        result[symbol] = row
        if before != after:
            changes.append({"symbol": symbol, "previousPeriod": before.get("period"), "currentPeriod": after.get("period"),
                            "previousFingerprint": before.get("fingerprint"), "currentFingerprint": after.get("fingerprint"),
                            "integrity": row.get("financialIntegrity"), "comparisons": after.get("comparisons")})
    return {"schemaVersion": COMPANY_KNOWLEDGE_CACHE_VERSION, "symbols": result}, sorted(changes, key=lambda row: row["symbol"])


def financial_input_requires_revalidation(company, rules):
    """Legacy derived comparisons have no reproducible period/source contract."""
    if not company or not any("graph.company." in str(rule) for rule in rules or []):
        return False
    packet = compact_financial_evidence(company)
    return packet.get("version") in (None, "legacy-unverified")
