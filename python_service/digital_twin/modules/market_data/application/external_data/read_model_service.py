from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping

from digital_twin.modules.news_intelligence.contracts import (
    bind_company_event_contract,
    merge_company_knowledge_rows,
    source_reference_from_fact_row,
)
from digital_twin.modules.market_data.domain.external_data_fitness import evaluate_external_data_fitness


EXTERNAL_SIGNAL_MAP_FIELDS = {
    "equityQuotes",
    "officialDailyPrices",
    "securityMaster",
    "marketIndices",
    "corporateActions",
    "cryptoMarkets",
    "fxRates",
    "secFilings",
    "dartDisclosures",
    "newsHeadlines",
    "companyOverviews",
    "earningsReports",
    "yfinanceData",
    "researchEvidence",
    "companyKnowledge",
    "externalDataLineage",
}

EXTERNAL_SIGNAL_ARCHIVE_FIELDS = {"sourceArchive"}
CALENDAR_REFERENCE_DATASETS = {"official.bls-release", "official.fomc-release", "official.bok-release", "official.bls-statistics"}
COMPANY_EVENT_DATASETS = {
    "opendart.disclosures", "opendart.document", "sec.submissions", "sec.document",
    "public-data.kr-dividends", "public-data.kr-capital-events",
    "public-data.kr-shareholder-rights", "yfinance.news",
}


def bind_external_event_lineage(fragment: Dict[str, object], fact_row: Dict[str, object]) -> Dict[str, object]:
    """Attach one exact fact revision to each event represented by that fact."""

    source = dict(fragment or {})
    if str(fact_row.get("datasetId") or "") not in COMPANY_EVENT_DATASETS:
        return source
    result = dict(source)
    source_reference = source_reference_from_fact_row(fact_row)
    if not source_reference:
        return result
    subject = str(fact_row.get("subjectKey") or "").upper().strip()

    corporate_groups = {
        symbol: dict(events) if isinstance(events, dict) else events
        for symbol, events in (result.get("corporateActions") or {}).items()
    } if isinstance(result.get("corporateActions"), dict) else {}
    if corporate_groups:
        result["corporateActions"] = corporate_groups
    for symbol, events in list(corporate_groups.items()):
        if not isinstance(events, dict):
            continue
        corporate_groups[symbol] = {
            event_id: bind_company_event_contract(
                event,
                symbol=symbol or subject,
                kind="corporate-action",
                title=event.get("eventType") if isinstance(event, dict) else "",
                published_at=(event.get("publishedAt") or event.get("announcedAt")) if isinstance(event, dict) else "",
                source_references=[source_reference],
            ) if isinstance(event, dict) else event
            for event_id, event in events.items()
        }

    dart_groups = {
        symbol: dict(disclosure) if isinstance(disclosure, dict) else disclosure
        for symbol, disclosure in (result.get("dartDisclosures") or {}).items()
    } if isinstance(result.get("dartDisclosures"), dict) else {}
    if dart_groups:
        result["dartDisclosures"] = dart_groups
    for symbol, disclosure in list(dart_groups.items()):
        if not isinstance(disclosure, dict):
            continue
        items = disclosure.get("items") if isinstance(disclosure.get("items"), list) else []
        disclosure["items"] = [
            bind_company_event_contract(
                item,
                symbol=symbol or subject,
                kind="disclosure",
                title=item.get("reportName") or item.get("report_name"),
                published_at=item.get("receiptDate") or item.get("receipt_date"),
                source_references=[source_reference],
            ) if isinstance(item, dict) else item
            for item in items
        ]
        dart_groups[symbol] = bind_company_event_contract(
            disclosure,
            symbol=symbol or subject,
            kind="disclosure",
            title=disclosure.get("reportName") or disclosure.get("report_name"),
            published_at=disclosure.get("receiptDate") or disclosure.get("receipt_date"),
            source_references=[source_reference],
        )

    sec_groups = {
        symbol: dict(filing) if isinstance(filing, dict) else filing
        for symbol, filing in (result.get("secFilings") or {}).items()
    } if isinstance(result.get("secFilings"), dict) else {}
    if sec_groups:
        result["secFilings"] = sec_groups
    for symbol, filing_group in list(sec_groups.items()):
        if not isinstance(filing_group, dict):
            continue
        latest = filing_group.get("latestFiling") if isinstance(filing_group.get("latestFiling"), dict) else {}
        if latest:
            filing_group["latestFiling"] = bind_company_event_contract(
                latest,
                symbol=symbol or subject,
                kind="filing",
                title=latest.get("form"),
                published_at=latest.get("filingDate") or latest.get("filed"),
                source_references=[source_reference],
            )
        recent = filing_group.get("recentFilings") if isinstance(filing_group.get("recentFilings"), list) else []
        filing_group["recentFilings"] = [
            bind_company_event_contract(
                item,
                symbol=symbol or subject,
                kind="filing",
                title=item.get("form"),
                published_at=item.get("filingDate") or item.get("filed"),
                source_references=[source_reference],
            ) if isinstance(item, dict) else item
            for item in recent
        ]

    news_groups = {
        symbol: dict(headlines) if isinstance(headlines, dict) else headlines
        for symbol, headlines in (result.get("newsHeadlines") or {}).items()
    } if isinstance(result.get("newsHeadlines"), dict) else {}
    if news_groups:
        result["newsHeadlines"] = news_groups
    for symbol, headlines in list(news_groups.items()):
        if not isinstance(headlines, dict):
            continue
        items = headlines.get("items") if isinstance(headlines.get("items"), list) else []
        headlines["items"] = [
            bind_company_event_contract(
                item,
                symbol=symbol or subject,
                kind="news",
                title=item.get("title"),
                published_at=item.get("publishedAt") or item.get("seenDate") or item.get("seendate"),
                source_references=[source_reference],
            ) if isinstance(item, dict) else item
            for item in items
        ]
    return result


def merge_dict(base: Dict[str, object], incoming: Dict[str, object]) -> Dict[str, object]:
    result = dict(base or {})
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _estimate_identity(value: Mapping[str, object]) -> tuple:
    row = dict(value or {})
    return (
        str(row.get("upstreamOrigin") or row.get("provider") or "").casefold().strip(),
        str(row.get("horizon") or row.get("period") or "").casefold().strip(),
        str(row.get("targetPeriodEnd") or "").strip(),
    )


def _estimate_clock(value: Mapping[str, object]) -> tuple:
    row = dict(value or {})
    return (
        str(row.get("sourceAsOf") or "").strip(),
        str(row.get("fetchedAt") or row.get("asOf") or "").strip(),
        str(row.get("observationId") or "").strip(),
    )


def merge_earnings_estimates(base: object, incoming: object) -> list:
    """Select one complete revision per upstream/horizon without field splicing."""

    selected = {}
    for raw in [*(base if isinstance(base, list) else []), *(incoming if isinstance(incoming, list) else [])]:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        identity = _estimate_identity(row)
        if not identity[0] or not identity[1]:
            continue
        current = selected.get(identity)
        if current is None or _estimate_clock(row) >= _estimate_clock(current):
            selected[identity] = row
    return [selected[key] for key in sorted(selected)]


def _merge_non_empty(base: Mapping[str, object], incoming: Mapping[str, object]) -> Dict[str, object]:
    result = dict(base or {})
    for key, value in dict(incoming or {}).items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_non_empty(result[key], value)
        elif value in (None, "", [], {}) and result.get(key) not in (None, "", [], {}):
            continue
        else:
            result[key] = value
    return result


def merge_financial_summary(base: Mapping[str, object], incoming: Mapping[str, object]) -> Dict[str, object]:
    """Merge provider summaries while consensus owns its structured estimate fields."""

    result = _merge_non_empty(dict(base or {}), dict(incoming or {}))
    estimates = merge_earnings_estimates(
        dict(base or {}).get("earningsEstimates"),
        dict(incoming or {}).get("earningsEstimates"),
    )
    if estimates:
        result["earningsEstimates"] = estimates
        fy1 = next((row for row in estimates if str(row.get("horizon") or row.get("period") or "") == "fy1"), None)
        if fy1 is not None and fy1.get("base") is not None:
            result["forwardEPS"] = fy1.get("base")
            result["epsPeriod"] = "fy1"
    elif "earningsEstimates" in result:
        result["earningsEstimates"] = []
    result["fetchedAt"] = max_timestamp(
        str(dict(base or {}).get("fetchedAt") or ""),
        str(dict(incoming or {}).get("fetchedAt") or ""),
    )
    return result


def merge_financial_summary_maps(base: Mapping[str, object], incoming: Mapping[str, object]) -> Dict[str, object]:
    result = {
        str(symbol or "").upper().strip(): dict(row)
        for symbol, row in dict(base or {}).items()
        if str(symbol or "").strip() and isinstance(row, Mapping)
    }
    for symbol, row in dict(incoming or {}).items():
        normalized = str(symbol or "").upper().strip()
        if normalized and isinstance(row, Mapping):
            result[normalized] = merge_financial_summary(result.get(normalized, {}), row)
    return result


def bind_consensus_lineage(fragment: Dict[str, object], fact_row: Mapping[str, object]) -> Dict[str, object]:
    if str(fact_row.get("datasetId") or "") != "yfinance.analyst":
        return dict(fragment or {})
    reference = source_reference_from_fact_row(fact_row)
    if not reference:
        return dict(fragment or {})
    result = dict(fragment or {})
    for group_name in ("companyOverviews", "earningsReports"):
        group = {
            str(symbol): dict(summary) if isinstance(summary, Mapping) else summary
            for symbol, summary in dict(result.get(group_name) or {}).items()
        }
        for symbol, summary in list(group.items()):
            if not isinstance(summary, dict):
                continue
            rows = []
            for raw in summary.get("earningsEstimates") or []:
                if not isinstance(raw, Mapping):
                    continue
                row = dict(raw)
                references = {
                    (str(item.get("datasetId") or ""), str(item.get("revisionId") or "")): dict(item)
                    for item in row.get("sourceReferences") or []
                    if isinstance(item, Mapping) and item.get("datasetId") and item.get("revisionId")
                }
                references[(reference["datasetId"], reference["revisionId"])] = reference
                row["sourceReferences"] = [references[key] for key in sorted(references)]
                row["validationState"] = (
                    "observed" if row.get("targetPeriodEnd") else "observed-partial-period"
                )
                row["revisionState"] = "exact-source-revision"
                rows.append(row)
            summary["earningsEstimates"] = rows
            group[symbol] = summary
        result[group_name] = group
    return result


def merge_company_knowledge_maps(base: Dict[str, object], incoming: Dict[str, object]) -> Dict[str, object]:
    result = {
        str(symbol or "").upper().strip(): dict(row)
        for symbol, row in dict(base or {}).items()
        if str(symbol or "").strip() and isinstance(row, dict)
    }
    for raw_symbol, row in dict(incoming or {}).items():
        symbol = str(raw_symbol or "").upper().strip()
        if symbol and isinstance(row, dict):
            result[symbol] = merge_company_knowledge_rows(result.get(symbol, {}), row)
    return result


def max_timestamp(*values: str) -> str:
    parsed = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        parsed.append((stamp.astimezone(timezone.utc), text))
    return max(parsed, default=(None, ""), key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))[1]


def merge_external_signal_read_models(
    base: Dict[str, object],
    incoming: Dict[str, object],
) -> Dict[str, object]:
    result = dict(base or {})
    for key, value in (incoming or {}).items():
        if key == "statuses" and isinstance(value, list):
            existing = [dict(item) for item in result.get("statuses") or [] if isinstance(item, dict)]
            replacement_sources = {
                (str(item.get("source") or ""), str(item.get("datasetId") or ""))
                for item in value
                if isinstance(item, dict)
            }
            existing = [
                item for item in existing
                if (str(item.get("source") or ""), str(item.get("datasetId") or "")) not in replacement_sources
            ]
            result["statuses"] = existing + [dict(item) for item in value if isinstance(item, dict)]
        elif key == "companyKnowledge" and isinstance(value, dict):
            result[key] = merge_company_knowledge_maps(result.get(key) or {}, value)
        elif key in {"companyOverviews", "earningsReports"} and isinstance(value, dict):
            result[key] = merge_financial_summary_maps(result.get(key) or {}, value)
        elif key in EXTERNAL_SIGNAL_MAP_FIELDS and isinstance(value, dict):
            result[key] = merge_dict(result.get(key) or {}, value)
        elif key == "macro" and isinstance(value, dict):
            result["macro"] = merge_dict(result.get("macro") or {}, value)
        elif key == "fetchedAt":
            result["fetchedAt"] = max_timestamp(result.get("fetchedAt"), value)
        elif key not in EXTERNAL_SIGNAL_ARCHIVE_FIELDS:
            result[key] = value
    return result


class ExternalSignalsReadModelService:
    """Build the compact legacy read model from independently stored facts."""

    def __init__(self, fact_store):
        self.fact_store = fact_store

    def signals_for_subjects(self, subject_keys: Iterable[str]) -> Dict[str, object]:
        result: Dict[str, object] = {
            "fetchedAt": "",
            "cryptoFetchedAt": "",
            "cryptoLastAttemptAt": "",
            "equityQuotes": {},
            "officialDailyPrices": {},
            "securityMaster": {},
            "marketIndices": {},
            "corporateActions": {},
            "cryptoMarkets": {},
            "macro": {},
            "fxRates": {},
            "secFilings": {},
            "dartDisclosures": {},
            "newsHeadlines": {},
            "companyOverviews": {},
            "earningsReports": {},
            "yfinanceData": {},
            "researchEvidence": {},
            "statuses": [],
            "externalDataLineage": {},
            "externalDataPlatform": {
                "enabled": True,
                "factCount": 0,
                "datasets": [],
                "staleDatasets": [],
            },
        }
        datasets = set()
        stale = set()
        requested_subjects = [str(item or "").upper().strip() for item in subject_keys or [] if str(item or "").strip()]
        rows = [row for row in self.fact_store.list_current(requested_subjects) if row.get("datasetId") not in CALENDAR_REFERENCE_DATASETS]
        for row in rows:
            fragment = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            fragment = bind_external_event_lineage(fragment, row)
            fragment = bind_consensus_lineage(fragment, row)
            for key, value in fragment.items():
                if key == "statuses" and isinstance(value, list):
                    result["statuses"].extend([dict(item) for item in value if isinstance(item, dict)])
                elif key == "companyKnowledge" and isinstance(value, dict):
                    result[key] = merge_company_knowledge_maps(result.get(key) or {}, value)
                elif key in {"companyOverviews", "earningsReports"} and isinstance(value, dict):
                    result[key] = merge_financial_summary_maps(result.get(key) or {}, value)
                elif key in EXTERNAL_SIGNAL_MAP_FIELDS and isinstance(value, dict):
                    result[key] = merge_dict(result.get(key) or {}, value)
                elif key == "macro" and isinstance(value, dict):
                    result["macro"] = merge_dict(result.get("macro") or {}, value)
                elif key not in {"fetchedAt", "externalDataPlatform", *EXTERNAL_SIGNAL_ARCHIVE_FIELDS}:
                    result[key] = value
            dataset_id = str(row.get("datasetId") or "")
            subject_key = str(row.get("subjectKey") or "").upper().strip()
            if dataset_id:
                datasets.add(dataset_id)
                lineage_key = dataset_id + ":" + subject_key
                result["externalDataLineage"][lineage_key] = {
                    "datasetId": dataset_id,
                    "subjectKey": subject_key,
                    "revisionId": str(row.get("revisionId") or ""),
                    "providerRevision": str(row.get("sourceRevision") or ""),
                    "payloadHash": str(row.get("payloadHash") or ""),
                    "sourceSchemaVersion": str(row.get("sourceSchemaVersion") or ""),
                    "sourceAsOf": str(row.get("sourceAsOf") or ""),
                    "fetchedAt": str(row.get("fetchedAt") or ""),
                    "availability": str(row.get("availability") or "unknown"),
                    "freshnessState": str(row.get("freshnessState") or "unknown"),
                }
            if str(row.get("freshnessState") or "") == "stale":
                stale.add(dataset_id)
            result["fetchedAt"] = max_timestamp(result.get("fetchedAt"), row.get("fetchedAt"))
            if dataset_id == "coingecko.market":
                result["cryptoFetchedAt"] = str(row.get("fetchedAt") or "")
                result["cryptoLastAttemptAt"] = str(row.get("updatedAt") or row.get("fetchedAt") or "")
        # Document extraction may refer to an older filing. It does not own
        # the current full-statement response or its reporting basis.
        for row in rows:
            if row.get("datasetId") != "opendart.company_facts":
                continue
            for symbol, source in (row.get("payload", {}).get("dartDisclosures") or {}).items():
                target = result["dartDisclosures"].setdefault(symbol, {})
                for field in ("financialStatements", "financialStatementBasis"):
                    if field in source:
                        target[field] = source[field]
        for status in self.fact_store.provider_statuses():
            if status.get("datasetId") in CALENDAR_REFERENCE_DATASETS:
                continue
            state = str(status.get("state") or "unknown")
            if state in {"failed", "circuit_open"}:
                result["statuses"].append({
                    "source": str(status.get("providerId") or "External API"),
                    "datasetId": str(status.get("datasetId") or ""),
                    "ok": False,
                    "message": str(status.get("lastError") or state),
                    "state": state,
                    "lastAttemptAt": str(status.get("lastAttemptAt") or ""),
                    "lastSuccessAt": str(status.get("lastSuccessAt") or ""),
                    "circuitOpenUntil": str(status.get("circuitOpenUntil") or ""),
                })
        for dataset_id in sorted(stale):
            result["statuses"].append({
                "source": dataset_id,
                "datasetId": dataset_id,
                "ok": True,
                "deferred": True,
                "dataUsable": False,
                "message": "source fact is stale; collection refresh is pending",
            })
        result["externalDataPlatform"] = {
            "enabled": True,
            "factCount": len(rows),
            "datasets": sorted(datasets),
            "staleDatasets": sorted(stale),
            "fitness": evaluate_external_data_fitness(
                rows,
                self.fact_store.provider_statuses(),
                [
                    {"datasetId": row.get("datasetId"), "enabled": True}
                    for row in rows
                ],
                requested_subjects,
            ),
        }
        return result
