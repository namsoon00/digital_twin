"""Plan bounded financial-evidence repair without mutating source history."""

from __future__ import annotations

from typing import Iterable, Mapping

from digital_twin.modules.news_intelligence.domain.company_knowledge import (
    COMPANY_KNOWLEDGE_CACHE_VERSION,
    company_knowledge_by_symbol,
    merge_company_knowledge_rows,
)
from digital_twin.modules.news_intelligence.domain.financial_reporting import (
    FINANCIAL_REPORTING_VERSION,
    compact_financial_evidence,
    financial_report_contract_assessment,
)


FINANCIAL_REPAIR_PLAN_VERSION = "financial-repair-plan-v2-scoped-manifest"


def normalized_repair_symbols(values: Iterable[object] = ()):
    return sorted({
        str(value or "").upper().strip()
        for value in values or []
        if str(value or "").strip()
    })


def _rule_id(value: object) -> str:
    if isinstance(value, Mapping):
        return str(value.get("ruleId") or value.get("rule_id") or "")
    return str(value or "")


def _company_rule_present(rules: Iterable[object]) -> bool:
    return any("graph.company." in _rule_id(rule) for rule in rules or [])


def _packet_contract_assessment(packet: Mapping[str, object]):
    report = packet.get("report") if isinstance(packet.get("report"), Mapping) else {}
    frequency = str(report.get("frequency") or "").strip().lower()
    row = {
        "period": packet.get("period") or report.get("periodEnd"),
        "periodEnd": report.get("periodEnd"),
        "frequency": frequency,
        "provider": report.get("provider"),
        "financialReportingVersion": packet.get("version"),
        "reportContract": report,
    }
    for comparison in packet.get("comparisons") or []:
        if not isinstance(comparison, Mapping):
            continue
        metric = str(comparison.get("metric") or "")
        if metric:
            row[metric] = comparison.get("currentValue")
    return financial_report_contract_assessment(row, frequency)


def financial_input_revalidation_assessment(company, rules):
    """Explain whether a company-rule input used unverifiable finance."""

    if not isinstance(company, Mapping) or not company:
        return {"requiresRevalidation": False, "reasonCodes": ["missing-company-context"]}
    if not _company_rule_present(rules):
        return {"requiresRevalidation": False, "reasonCodes": ["no-company-rule"]}

    packet = compact_financial_evidence(company)
    if packet:
        version = str(packet.get("version") or "")
        if version != FINANCIAL_REPORTING_VERSION:
            return {
                "requiresRevalidation": True,
                "reasonCodes": ["legacy-financial-reporting-version"],
                "financialVersion": version or "missing",
                "period": str(packet.get("period") or ""),
            }
        assessment = _packet_contract_assessment(packet)
        if not assessment.get("eligible"):
            return {
                "requiresRevalidation": True,
                "reasonCodes": [str(assessment.get("reason") or "invalid-financial-report-contract")],
                "financialVersion": version,
                "period": str(packet.get("period") or ""),
            }
        return {
            "requiresRevalidation": False,
            "reasonCodes": [],
            "financialVersion": version,
            "period": str(packet.get("period") or ""),
            "observationId": str((packet.get("report") or {}).get("observationId") or ""),
        }

    integrity = company.get("financialIntegrity") if isinstance(company.get("financialIntegrity"), Mapping) else {}
    exclusions = integrity.get("excludedPeriods") if isinstance(integrity.get("excludedPeriods"), list) else []
    if exclusions:
        return {
            "requiresRevalidation": False,
            "reasonCodes": ["invalid-financial-periods-already-excluded"],
            "excludedPeriodCount": len(exclusions),
        }
    return {"requiresRevalidation": False, "reasonCodes": ["no-financial-evidence-used"]}


def financial_input_requires_revalidation(company, rules):
    return bool(financial_input_revalidation_assessment(company, rules).get("requiresRevalidation"))


def _repair_comparison_payload(company: Mapping[str, object]):
    integrity = company.get("financialIntegrity") if isinstance(company.get("financialIntegrity"), Mapping) else {}
    packet = compact_financial_evidence(company)
    return {
        "financialEvidence": packet,
        "decisionSemantics": {
            "version": str(packet.get("version") or ""),
            "period": str(packet.get("period") or ""),
            "decisionFingerprint": str(packet.get("decisionFingerprint") or packet.get("fingerprint") or ""),
        },
        "selectionVersion": str(integrity.get("selectionVersion") or ""),
        "integrityStatus": str(integrity.get("status") or ""),
        "excludedPeriods": list(integrity.get("excludedPeriods") or []),
    }


def financial_repair_plan(cache, signals, symbols: Iterable[object] = ()):
    """Rebuild only the requested symbols and preserve every non-target row."""

    old = cache.get("symbols") if isinstance(cache, Mapping) and isinstance(cache.get("symbols"), Mapping) else {}
    requested = normalized_repair_symbols(symbols)
    if not requested:
        requested = normalized_repair_symbols(old)
    rebuilt = company_knowledge_by_symbol(signals, requested)
    signal_rows = signals.get("companyKnowledge") if isinstance(signals, Mapping) and isinstance(signals.get("companyKnowledge"), Mapping) else {}
    result, changes = dict(old), []
    for symbol in requested:
        previous = old.get(symbol) if isinstance(old.get(symbol), Mapping) else {}
        current_signal = signal_rows.get(symbol) if isinstance(signal_rows.get(symbol), Mapping) else {}
        normalized = rebuilt.get(symbol) if isinstance(rebuilt.get(symbol), Mapping) else {}
        if not any((previous, current_signal, normalized)):
            continue
        row = merge_company_knowledge_rows(previous, current_signal, normalized)
        before, after = _repair_comparison_payload(previous), _repair_comparison_payload(row)
        result[symbol] = row
        effective_before = {key: value for key, value in before.items() if key != "financialEvidence"}
        effective_after = {key: value for key, value in after.items() if key != "financialEvidence"}
        if effective_before != effective_after:
            before_packet = before["financialEvidence"]
            after_packet = after["financialEvidence"]
            changes.append({
                "symbol": symbol,
                "previousPeriod": before_packet.get("period"),
                "currentPeriod": after_packet.get("period"),
                "previousFingerprint": before_packet.get("fingerprint"),
                "currentFingerprint": after_packet.get("fingerprint"),
                "previousIntegrityStatus": before.get("integrityStatus"),
                "currentIntegrityStatus": after.get("integrityStatus"),
                "excludedPeriods": after.get("excludedPeriods"),
                "comparisons": after_packet.get("comparisons"),
            })

    all_cached_symbols = set(normalized_repair_symbols(old))
    full_scope = bool(all_cached_symbols) and all_cached_symbols.issubset(set(requested))
    schema_version = (
        COMPANY_KNOWLEDGE_CACHE_VERSION
        if full_scope
        else str((cache or {}).get("schemaVersion") or "company-knowledge-cache-partial")
    )
    replacement = {
        **dict(cache or {}),
        "schemaVersion": schema_version,
        "symbols": result,
        "financialRepairCoverage": {
            "version": FINANCIAL_REPAIR_PLAN_VERSION,
            "repairedSymbols": requested,
            "fullCacheScope": full_scope,
        },
    }
    replacement.pop("updatedAt", None)
    return replacement, sorted(changes, key=lambda row: row["symbol"])
