"""Official Korean issuer, financial, and corporate-action adapters.

The public provider rows remain in the durable external-fact archive.  Each
adapter also emits a bounded canonical fragment for CompanyKnowledge or the
current corporate-action ABox; no investment opinion is calculated here.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

from zoneinfo import ZoneInfo

from ....application.external_data.contracts import (
    CollectionJob,
    CollectionPartition,
    DatasetDescriptor,
    ExternalSubject,
)
from ....domain.company_knowledge import (
    enrich_financial_periods,
    merge_company_knowledge_rows,
)
from ....domain.portfolio import utc_now_iso
from ...external_signal_utils import default_json_fetcher
from .base import observation
from .public_data_portal import (
    PUBLIC_DATA_PROVIDER_LABEL,
    fetch_public_data_rows,
    latest_rows,
    numeric_value,
    source_as_of_for_base_date,
    stable_revision,
)


SEOUL = ZoneInfo("Asia/Seoul")
COMPANY_BASIC_ROOT = "https://apis.data.go.kr/1160100/service/GetCorpBasicInfoService_V2/"
FINANCIAL_ROOT = "https://apis.data.go.kr/1160100/service/GetFinaStatInfoService_V2/"
DIVIDEND_ENDPOINT = "https://apis.data.go.kr/1160100/GetStocDiviInfoService_V2/getDiviInfo_V2"
ISSUANCE_ROOT = "https://apis.data.go.kr/1160100/GetStocIssuInfoService_V3/"
RIGHTS_ENDPOINT = "https://apis.data.go.kr/1160100/GetStocRighScheService_V2/getRighExerReasSche_V2"

COMPANY_BASIC_PAGE = "https://www.data.go.kr/dataset/15043184/openapi.do"
FINANCIAL_PAGE = "https://www.data.go.kr/dataset/15043459/openapi.do"
DIVIDEND_PAGE = "https://www.data.go.kr/data/15043284/openapi.do"
ISSUANCE_PAGE = "https://www.data.go.kr/data/15043423/openapi.do"
RIGHTS_PAGE = "https://www.data.go.kr/data/15059609/openapi.do"


def _text(value: object) -> str:
    return " ".join(str(value or "").split()).strip()


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9가-힣]+", "", _text(value).lower())


def _crno(job: CollectionJob) -> str:
    return _text(job.watermark.get("crno"))


def _symbol(job: CollectionJob) -> str:
    return _text(job.subject.symbol or job.subject.subject_key).upper()


def _exact_crno(rows: Iterable[Dict[str, object]], crno: str) -> List[Dict[str, object]]:
    return [dict(row) for row in rows or [] if _text(row.get("crno")) == crno]


def _latest_distinct(
    rows: Iterable[Dict[str, object]],
    fields: Sequence[str],
    *,
    limit: int,
) -> List[Dict[str, object]]:
    selected = latest_rows(rows)
    result = []
    seen = set()
    for row in selected:
        key = tuple(_text(row.get(field)) for field in fields)
        if not any(key) or key in seen:
            continue
        seen.add(key)
        result.append(dict(row))
        if len(result) >= limit:
            break
    return result


def _latest_date(*groups: Iterable[Mapping[str, object]]) -> str:
    return max(
        (
            _text(row.get("basDt"))
            for group in groups
            for row in group or []
            if isinstance(row, Mapping)
        ),
        default="",
    )


def _source(provider_scope: str, as_of: str, url: str) -> Dict[str, object]:
    return {
        "provider": PUBLIC_DATA_PROVIDER_LABEL,
        "asOf": as_of,
        "scope": provider_scope,
        "sourceUrl": url,
        "officialSource": True,
    }


def _empty_observation(
    descriptor: DatasetDescriptor,
    symbol: str,
    payload: Dict[str, object],
    message: str,
):
    return observation(
        descriptor,
        symbol,
        payload,
        preferred_revision=message,
        preferred_source_as_of=utc_now_iso(),
        watermark={"emptyResult": True, "symbol": symbol},
        quality={
            "dataUsable": True,
            "provider": descriptor.provider_id,
            "officialSource": True,
            "emptyResult": True,
        },
        empty_result=True,
        retain_previous=True,
    )


def _followup_descriptor(
    dataset_id: str,
    capability: str,
    priority: int,
    materiality_policy: str,
) -> DatasetDescriptor:
    return DatasetDescriptor(
        dataset_id=dataset_id,
        provider_id="data-go-kr-fsc",
        capability=capability,
        cadence_seconds=86400,
        freshness_seconds=90 * 86400,
        priority=priority,
        rate_limit_seconds=1,
        enabled_setting="externalPublicDataReferenceEnabled",
        max_partitions=5000,
        revision_mode="immutable",
        materiality_policy=materiality_policy,
        partition_strategy="followup",
        completion_mode="once",
    )


class PublicDataPortalCompanyProfileAdapter:
    descriptor = _followup_descriptor(
        "public-data.kr-company-profile",
        "official-company-profile-and-relationships",
        53,
        "official-company-knowledge",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(self, _subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return []

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        symbol, crno = _symbol(job), _crno(job)
        if not crno:
            raise RuntimeError("공공데이터 기업개요 작업에 법인등록번호가 없습니다.")
        outline_rows = _exact_crno(fetch_public_data_rows(
            self.json_fetcher,
            COMPANY_BASIC_ROOT + "getCorpOutline_V2",
            settings,
            {"crno": crno},
            label="금융위원회 기업기본정보",
            max_rows=200,
        ), crno)
        affiliate_rows = _exact_crno(fetch_public_data_rows(
            self.json_fetcher,
            COMPANY_BASIC_ROOT + "getAffiliate_V2",
            settings,
            {"crno": crno},
            label="금융위원회 계열회사정보",
            max_rows=500,
        ), crno)
        subsidiary_rows = _exact_crno(fetch_public_data_rows(
            self.json_fetcher,
            COMPANY_BASIC_ROOT + "getConsSubsComp_V2",
            settings,
            {"crno": crno},
            label="금융위원회 연결대상종속회사정보",
            max_rows=500,
        ), crno)
        outline_rows.sort(key=lambda row: _text(row.get("basDt")), reverse=True)
        if not outline_rows:
            return _empty_observation(
                self.descriptor,
                symbol,
                {"companyKnowledge": {}},
                "no-company-profile",
            )
        outline = outline_rows[0]
        affiliates = _latest_distinct(affiliate_rows, ("afilCmpyCrno", "afilCmpyNm"), limit=20)
        subsidiaries = _latest_distinct(subsidiary_rows, ("sbrdEnpNm",), limit=20)
        base_date = _latest_date(outline_rows[:1], affiliates, subsidiaries)
        source_as_of = source_as_of_for_base_date(base_date)
        profile = {
            "legalName": _text(outline.get("corpNm")),
            "englishName": _text(outline.get("corpEnsnNm")),
            "publicName": _text(outline.get("enpPbanCmpyNm")),
            "ceoName": _text(outline.get("enpRprFnm")),
            "industry": _text(outline.get("sicNm")),
            "mainBusiness": _text(outline.get("enpMainBizNm")),
            "website": _text(outline.get("enpHmpgUrl")),
            "headOfficeAddress": _text(outline.get("enpBsadr")),
            "detailedAddress": _text(outline.get("enpDtadr")),
            "establishedDate": _text(outline.get("enpEstbDt")),
            "fiscalYearEndMonth": _text(outline.get("enpStacMm")),
            "employeeCount": numeric_value(outline.get("enpEmpeCnt"), integer=True),
            "averageTenure": _text(outline.get("empeAvgCnwkTermCtt")),
            "averageSalary": numeric_value(outline.get("enpPn1AvgSlryAmt"), integer=True),
            "auditFirm": _text(outline.get("actnAudpnNm")),
            "auditOpinion": _text(outline.get("audtRptOpnnCtt")),
            "mainBank": _text(outline.get("enpMntrBnkNm")),
        }
        listing = {
            "marketRegistrationCode": _text(outline.get("corpRegMrktDcd")),
            "marketRegistrationName": _text(outline.get("corpRegMrktDcdNm")),
            "krxListingDate": _text(outline.get("enpKrxLstgDt")),
            "krxDelistingDate": _text(outline.get("enpKrxLstgAbolDt")),
            "kosdaqListingDate": _text(outline.get("enpKosdaqLstgDt")),
            "kosdaqDelistingDate": _text(outline.get("enpKosdaqLstgAbolDt")),
            "exchangeListingDate": _text(outline.get("enpXchgLstgDt")),
            "exchangeDelistingDate": _text(outline.get("enpXchgLstgAbolDt")),
        }
        fragment = {
            "symbol": symbol,
            "companyName": _text(outline.get("corpNm") or job.watermark.get("companyName") or job.subject.name or symbol),
            "identifiers": {
                "corporateRegistrationNumber": crno,
                "businessRegistrationNumber": _text(outline.get("bzno")),
                "fssCorporateNumber": _text(outline.get("fssCorpUnqNo")),
                "isin": _text(job.watermark.get("isin")),
            },
            "profile": {key: value for key, value in profile.items() if value not in (None, "")},
            "listing": {key: value for key, value in listing.items() if value not in (None, "")},
            "relationships": {
                "affiliates": [
                    {
                        "companyName": _text(row.get("afilCmpyNm")),
                        "corporateRegistrationNumber": _text(row.get("afilCmpyCrno")),
                        "listed": _text(row.get("lstgYn")),
                        "baseDate": _text(row.get("basDt")),
                        "provider": PUBLIC_DATA_PROVIDER_LABEL,
                    }
                    for row in affiliates
                    if _text(row.get("afilCmpyNm"))
                ],
                "subsidiaries": [
                    {
                        "companyName": _text(row.get("sbrdEnpNm")),
                        "establishedDate": _text(row.get("sbrdEnpEstbDt")),
                        "address": _text(row.get("sbrdEnpadr")),
                        "mainBusiness": _text(row.get("sbrdEnpMainBizCtt")),
                        "totalAssets": numeric_value(row.get("sbrdEnpLtstEbzyrTastAmt")),
                        "controlBasis": _text(row.get("dntRltBsisCtt")),
                        "materialSubsidiary": _text(row.get("mainSbrdEnpYnCtt")),
                        "baseDate": _text(row.get("basDt")),
                        "provider": PUBLIC_DATA_PROVIDER_LABEL,
                    }
                    for row in subsidiaries
                    if _text(row.get("sbrdEnpNm"))
                ],
            },
            "provenance": [_source("official-company-profile", source_as_of, COMPANY_BASIC_PAGE)],
        }
        knowledge = merge_company_knowledge_rows(fragment)
        archive = {
            "dataset": self.descriptor.dataset_id,
            "outline": outline_rows,
            "affiliates": affiliate_rows,
            "subsidiaries": subsidiary_rows,
        }
        fetched_at = utc_now_iso()
        return observation(
            self.descriptor,
            symbol,
            {"companyKnowledge": {symbol: knowledge}, "sourceArchive": archive},
            preferred_revision=knowledge.get("materialRevision") or stable_revision(fragment),
            preferred_source_as_of=source_as_of or fetched_at,
            watermark={"symbol": symbol, "crno": crno, "baseDate": base_date},
            quality={
                "dataUsable": bool(knowledge),
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "decisionEligibility": "company-context",
            },
        )


FINANCIAL_ACCOUNT_ALIASES = {
    "revenue": ("ifrsfullrevenue", "ifrsrevenue", "매출액", "영업수익"),
    "operatingIncome": ("dartoperatingincomeloss", "영업이익손실", "영업이익"),
    "netIncome": ("ifrsprofitloss", "당기순이익손실", "당기순이익"),
    "totalAssets": ("ifrsassets", "자산총계"),
    "totalLiabilities": ("ifrsliabilities", "부채총계"),
    "equity": ("ifrsequity", "자본총계"),
    "cash": ("ifrscashandcashequivalents", "현금및현금성자산"),
}


def _scope_priority(row: Mapping[str, object]) -> int:
    text = _text(row.get("fnclDcdNm")).lower()
    return 2 if "연결" in text or "consolidated" in text else 1


def _best_rows_by_period(rows: Iterable[Dict[str, object]]) -> List[Dict[str, object]]:
    result: Dict[str, Dict[str, object]] = {}
    for row in rows or []:
        period = _text(row.get("basDt") or row.get("bizYear"))
        if not period:
            continue
        current = result.get(period)
        if current is None or _scope_priority(row) >= _scope_priority(current):
            result[period] = dict(row)
    return [result[key] for key in sorted(result, reverse=True)[:4]]


def _statement_values(rows: Iterable[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    grouped: Dict[Tuple[str, str, str], List[Dict[str, object]]] = {}
    for row in rows or []:
        period = _text(row.get("basDt"))
        scope = _text(row.get("fnclDcd"))
        if period:
            statement_type = scope.split("_", 1)[0].lower() or "statement"
            grouped.setdefault((period, statement_type, scope), []).append(dict(row))
    selected: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
    for (period, statement_type, _scope), values in grouped.items():
        key = (period, statement_type)
        current = selected.get(key)
        if current is None or _scope_priority(values[0]) >= _scope_priority(current[0]):
            selected[key] = values
    by_period: Dict[str, List[Dict[str, object]]] = {}
    for (period, _statement_type), values in selected.items():
        by_period.setdefault(period, []).extend(values)
    result: Dict[str, Dict[str, object]] = {}
    for period, values in by_period.items():
        facts: Dict[str, object] = {}
        for row in values:
            account_keys = {_key(row.get("acitId")), _key(row.get("acitNm"))}
            for field, aliases in FINANCIAL_ACCOUNT_ALIASES.items():
                if any(alias == account or alias in account for alias in aliases for account in account_keys if account):
                    amount = numeric_value(row.get("crtmAcitAmt"))
                    if amount is not None:
                        facts[field] = amount
                    break
        result[period] = facts
    return result


class PublicDataPortalCompanyFinancialAdapter:
    descriptor = _followup_descriptor(
        "public-data.kr-company-financials",
        "official-company-financial-statements",
        52,
        "official-company-financial-revision",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(self, _subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return []

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        symbol, crno = _symbol(job), _crno(job)
        if not crno:
            raise RuntimeError("공공데이터 재무 작업에 법인등록번호가 없습니다.")
        groups = {}
        for name, operation, limit in (
            ("summary", "getSummFinaStat_V2", 300),
            ("balanceSheet", "getBs_V2", 600),
            ("incomeStatement", "getIncoStat_V2", 600),
        ):
            groups[name] = _exact_crno(fetch_public_data_rows(
                self.json_fetcher,
                FINANCIAL_ROOT + operation,
                settings,
                {"crno": crno},
                label="금융위원회 기업재무정보 " + name,
                max_rows=limit,
            ), crno)
        summaries = _best_rows_by_period(groups["summary"])
        if not summaries:
            return _empty_observation(
                self.descriptor,
                symbol,
                {"companyKnowledge": {}},
                "no-company-financials",
            )
        statement_facts = _statement_values([*groups["balanceSheet"], *groups["incomeStatement"]])
        periods = []
        for row in summaries:
            period = _text(row.get("basDt") or row.get("bizYear"))
            facts = {
                "period": period,
                "businessYear": _text(row.get("bizYear")),
                "currency": _text(row.get("curCd") or "KRW"),
                "accountingScopeCode": _text(row.get("fnclDcd")),
                "accountingScope": _text(row.get("fnclDcdNm")),
                "provider": PUBLIC_DATA_PROVIDER_LABEL,
                "revenue": numeric_value(row.get("enpSaleAmt")),
                "operatingIncome": numeric_value(row.get("enpBzopPft")),
                "netIncome": numeric_value(row.get("enpCrtmNpf")),
                "totalAssets": numeric_value(row.get("enpTastAmt")),
                "totalLiabilities": numeric_value(row.get("enpTdbtAmt")),
                "equity": numeric_value(row.get("enpTcptAmt")),
                "paidInCapital": numeric_value(row.get("enpCptlAmt")),
                "debtToEquityPct": numeric_value(row.get("fnclDebtRto")),
                **statement_facts.get(period, {}),
            }
            periods.append({key: value for key, value in facts.items() if value not in (None, "")})
        periods = enrich_financial_periods(periods)
        base_date = _latest_date(summaries)
        source_as_of = source_as_of_for_base_date(base_date)
        latest = periods[0] if periods else {}
        fragment = {
            "symbol": symbol,
            "companyName": _text(job.watermark.get("companyName") or job.subject.name or symbol),
            "identifiers": {
                "corporateRegistrationNumber": crno,
                "isin": _text(job.watermark.get("isin")),
            },
            "financials": {"annual": periods},
            "capital": {
                key: latest.get(key)
                for key in ("paidInCapital", "totalLiabilities", "equity")
                if latest.get(key) not in (None, "")
            },
            "provenance": [_source("official-financial-statements", source_as_of, FINANCIAL_PAGE)],
        }
        knowledge = merge_company_knowledge_rows(fragment)
        fetched_at = utc_now_iso()
        return observation(
            self.descriptor,
            symbol,
            {"companyKnowledge": {symbol: knowledge}, "sourceArchive": {"dataset": self.descriptor.dataset_id, **groups}},
            preferred_revision=knowledge.get("materialRevision") or stable_revision(fragment),
            preferred_source_as_of=source_as_of or fetched_at,
            watermark={"symbol": symbol, "crno": crno, "baseDate": base_date},
            quality={
                "dataUsable": bool(periods),
                "provider": self.descriptor.provider_id,
                "officialSource": True,
                "reportingPeriodCount": len(periods),
                "decisionEligibility": "fundamental-context",
            },
        )


def _event_state(start_date: object, end_date: object = "") -> str:
    today = datetime.now(SEOUL).strftime("%Y%m%d")
    start = _text(start_date)
    end = _text(end_date)
    if end and end < today:
        return "completed"
    if start and start > today:
        return "upcoming"
    if start and (not end or end >= today):
        return "active"
    return "announced"


def _event_common(symbol: str, row: Mapping[str, object], source_url: str) -> Dict[str, object]:
    return {
        "symbol": symbol,
        "companyName": _text(row.get("stckIssuCmpyNm")),
        "corporateRegistrationNumber": _text(row.get("crno")),
        "isin": _text(row.get("isinCd")),
        "baseDate": _text(row.get("basDt")),
        "provider": PUBLIC_DATA_PROVIDER_LABEL,
        "sourceUrl": source_url,
        "officialSource": True,
        "decisionEligibility": "corporate-action-context",
    }


class PublicDataPortalDividendAdapter:
    descriptor = _followup_descriptor(
        "public-data.kr-dividends",
        "official-dividend-events",
        57,
        "official-corporate-action-revision",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(self, _subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return []

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        symbol, crno = _symbol(job), _crno(job)
        rows = _exact_crno(fetch_public_data_rows(
            self.json_fetcher,
            DIVIDEND_ENDPOINT,
            settings,
            {"crno": crno},
            label="금융위원회 주식배당정보",
            max_rows=500,
        ), crno)
        rows.sort(key=lambda row: (_text(row.get("dvdnBasDt")), _text(row.get("basDt"))), reverse=True)
        events = {}
        for row in rows:
            record_date = _text(row.get("dvdnBasDt"))
            share_class = _text(row.get("scrsItmsKcd"))
            event_id = "dividend:" + (_text(row.get("isinCd")) or symbol) + ":" + (record_date or _text(row.get("basDt"))) + ":" + share_class
            if event_id in events:
                continue
            events[event_id] = {
                **_event_common(symbol, row, DIVIDEND_PAGE),
                "eventId": event_id,
                "eventType": "dividend",
                "tboxClass": "DividendEvent",
                "shareClassCode": share_class,
                "shareClassName": _text(row.get("scrsItmsKcdNm")),
                "recordDate": record_date,
                "cashPaymentDate": _text(row.get("cashDvdnPayDt")),
                "stockDeliveryDate": _text(row.get("stckHndvDt")),
                "cashDividendPerCommonShare": numeric_value(row.get("stckGenrDvdnAmt")),
                "cashDividendPerPreferredShare": numeric_value(row.get("stckGrdnDvdnAmt")),
                "commonCashDividendRatePct": numeric_value(row.get("stckGenrCashDvdnRt")),
                "commonStockDividendRatePct": numeric_value(row.get("stckGenrDvdnRt")),
                "preferredStockDividendRatePct": numeric_value(row.get("stckGrdnDvdnRt")),
                "parValue": numeric_value(row.get("stckParPrc")),
                "fiscalClosingMonthDay": _text(row.get("stckStacMd")),
                "eventLifecycleState": _event_state(record_date, row.get("cashDvdnPayDt")),
            }
            if len(events) >= 12:
                break
        if not events:
            return _empty_observation(self.descriptor, symbol, {"corporateActions": {}}, "no-dividends")
        base_date = _latest_date(rows)
        fetched_at = utc_now_iso()
        return observation(
            self.descriptor,
            symbol,
            {"corporateActions": {symbol: events}, "sourceArchive": {"dataset": self.descriptor.dataset_id, "rows": rows}},
            preferred_revision=stable_revision(events),
            preferred_source_as_of=source_as_of_for_base_date(base_date) or fetched_at,
            watermark={"symbol": symbol, "crno": crno, "baseDate": base_date},
            quality={"dataUsable": True, "officialSource": True, "eventCount": len(events)},
        )


class PublicDataPortalCapitalEventAdapter:
    descriptor = _followup_descriptor(
        "public-data.kr-capital-events",
        "official-share-issuance-and-lockup-events",
        59,
        "official-corporate-action-revision",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(self, _subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return []

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        symbol, crno = _symbol(job), _crno(job)
        groups = {}
        for name, operation in (
            ("security", "getItemBasiInfo_V3"),
            ("issuance", "getStocIssuInfo_V3"),
            ("lockup", "getLockUpRetuInfo_V3"),
            ("statistics", "getStocIssuStat_V3"),
        ):
            groups[name] = _exact_crno(fetch_public_data_rows(
                self.json_fetcher,
                ISSUANCE_ROOT + operation,
                settings,
                {"crno": crno},
                label="금융위원회 주식발행정보 " + name,
                max_rows=500,
            ), crno)
        security_rows = sorted(groups["security"], key=lambda row: _text(row.get("basDt")), reverse=True)
        stat_rows = sorted(groups["statistics"], key=lambda row: _text(row.get("basDt")), reverse=True)
        events = {}
        issuance_rows = sorted(groups["issuance"], key=lambda row: (_text(row.get("stckIssuDt")), _text(row.get("basDt"))), reverse=True)
        for row in issuance_rows:
            issue_date = _text(row.get("stckIssuDt"))
            sequence = _text(row.get("stckIssuSqno"))
            event_id = "issuance:" + (_text(row.get("isinCd")) or symbol) + ":" + (issue_date or _text(row.get("basDt"))) + ":" + sequence
            if event_id in events:
                continue
            events[event_id] = {
                **_event_common(symbol, row, ISSUANCE_PAGE),
                "eventId": event_id,
                "eventType": "equity-issuance",
                "tboxClass": "EquityIssuanceEvent",
                "issueDate": issue_date,
                "listingDate": _text(row.get("lstgDt")),
                "issueReasonCode": _text(row.get("stckIssuRcd")),
                "issueReason": _text(row.get("stckIssuRcdNm")),
                "issuedShareCount": numeric_value(row.get("issuStckCnt"), integer=True),
                "issueSequence": sequence,
                "shareClassCode": _text(row.get("scrsItmsKcd")),
                "shareClassName": _text(row.get("scrsItmsKcdNm")),
                "eventLifecycleState": _event_state(issue_date, row.get("lstgDt")),
            }
            if len([item for item in events.values() if item.get("eventType") == "equity-issuance"]) >= 12:
                break
        lockup_rows = sorted(groups["lockup"], key=lambda row: (_text(row.get("protEndDt")), _text(row.get("basDt"))), reverse=True)
        for row in lockup_rows[:12]:
            release_date = _text(row.get("protEndDt") or row.get("lckupRelDt") or row.get("rtnDt"))
            registration_date = _text(row.get("protRegDt") or row.get("lckupRegDt"))
            reason = _text(row.get("protRcdNm") or row.get("lckupRcdNm"))
            registered_count = numeric_value(row.get("protRegStckCnt") or row.get("lckupRegStckCnt"), integer=True)
            event_id = "lockup:" + (_text(row.get("isinCd")) or symbol) + ":" + stable_revision({
                "releaseDate": release_date or _text(row.get("basDt")),
                "registrationDate": registration_date,
                "reason": reason,
                "registeredShareCount": registered_count,
            })
            events[event_id] = {
                **_event_common(symbol, row, ISSUANCE_PAGE),
                "eventId": event_id,
                "eventType": "lockup-release",
                "tboxClass": "LockupReleaseEvent",
                "registrationDate": registration_date,
                "registeredShareCount": registered_count,
                "releaseDate": release_date,
                "releasedShareCount": numeric_value(row.get("protEndStckCnt") or row.get("lckupRelStckCnt"), integer=True),
                "remainingShareCount": numeric_value(row.get("remnStckCnt"), integer=True),
                "reason": reason,
                "eventLifecycleState": _event_state(release_date, release_date),
            }
        security = security_rows[0] if security_rows else {}
        stats = stat_rows[0] if stat_rows else {}
        ordinary = numeric_value(stats.get("onskTisuCnt"), integer=True)
        preferred = numeric_value(stats.get("pfstTisuCnt"), integer=True)
        total_shares = (ordinary or 0) + (preferred or 0) if ordinary is not None or preferred is not None else None
        base_date = _latest_date(*groups.values())
        source_as_of = source_as_of_for_base_date(base_date)
        fragment = {
            "symbol": symbol,
            "companyName": _text(security.get("stckIssuCmpyNm") or job.watermark.get("companyName") or job.subject.name or symbol),
            "identifiers": {
                "corporateRegistrationNumber": crno,
                "isin": _text(security.get("isinCd") or job.watermark.get("isin")),
                "shortCode": _text(security.get("itmsShrtnCd") or symbol),
            },
            "listing": {
                "listingDate": _text(security.get("lstgDt")),
                "delistingDate": _text(security.get("lstgAbolDt")),
                "shareClassCode": _text(security.get("scrsItmsKcd")),
                "shareClassName": _text(security.get("scrsItmsKcdNm")),
                "issueForm": _text(security.get("issuFrmtClsfNm")),
                "parValue": numeric_value(security.get("stckParPrc")),
            },
            "capital": {
                key: value
                for key, value in {
                    "sharesOutstanding": total_shares,
                    "ordinarySharesOutstanding": ordinary,
                    "preferredSharesOutstanding": preferred,
                    "issuedShares": numeric_value(security.get("issuStckCnt"), integer=True),
                }.items()
                if value is not None
            },
            "provenance": [_source("official-security-capital", source_as_of, ISSUANCE_PAGE)],
        }
        knowledge = merge_company_knowledge_rows(fragment) if security or stats else {}
        if not events and not knowledge:
            return _empty_observation(
                self.descriptor,
                symbol,
                {"corporateActions": {}, "companyKnowledge": {}},
                "no-capital-events",
            )
        payload = {
            "corporateActions": {symbol: events} if events else {},
            "companyKnowledge": {symbol: knowledge} if knowledge else {},
            "sourceArchive": {"dataset": self.descriptor.dataset_id, **groups},
        }
        fetched_at = utc_now_iso()
        return observation(
            self.descriptor,
            symbol,
            payload,
            preferred_revision=stable_revision({"events": events, "knowledge": knowledge}),
            preferred_source_as_of=source_as_of or fetched_at,
            watermark={"symbol": symbol, "crno": crno, "baseDate": base_date},
            quality={
                "dataUsable": bool(events or knowledge),
                "officialSource": True,
                "eventCount": len(events),
                "decisionEligibility": "capital-context",
            },
        )


class PublicDataPortalShareholderRightsAdapter:
    descriptor = _followup_descriptor(
        "public-data.kr-shareholder-rights",
        "official-shareholder-right-schedules",
        58,
        "official-corporate-action-revision",
    )

    def __init__(self, json_fetcher: Callable = None):
        self.json_fetcher = json_fetcher or default_json_fetcher

    def partitions(self, _subjects: Iterable[ExternalSubject], _settings: Dict[str, object]) -> List[CollectionPartition]:
        return []

    def fetch(self, job: CollectionJob, settings: Dict[str, object]):
        symbol, crno = _symbol(job), _crno(job)
        rows = _exact_crno(fetch_public_data_rows(
            self.json_fetcher,
            RIGHTS_ENDPOINT,
            settings,
            {"crno": crno},
            label="금융위원회 주식권리일정정보",
            max_rows=500,
        ), crno)
        rows.sort(key=lambda row: (_text(row.get("rgtExertSttgDt")), _text(row.get("basDt"))), reverse=True)
        events = {}
        for row in rows:
            start = _text(row.get("rgtExertSttgDt"))
            end = _text(row.get("rgtExertEdDt"))
            reason_code = _text(row.get("rgtExertRcd"))
            event_id = "shareholder-right:" + (_text(row.get("isinCd")) or symbol) + ":" + (start or _text(row.get("basDt"))) + ":" + reason_code
            if event_id in events:
                continue
            events[event_id] = {
                **_event_common(symbol, row, RIGHTS_PAGE),
                "companyName": _text(row.get("stckIssuCmpyNm") or job.watermark.get("companyName") or job.subject.name),
                "eventId": event_id,
                "eventType": "shareholder-right",
                "tboxClass": "ShareholderRightEvent",
                "rightReasonCode": reason_code,
                "rightReason": _text(row.get("rgtExertRcdNm")),
                "issuanceReasonCode": _text(row.get("stckIssuRcd")),
                "issuanceReason": _text(row.get("stckIssuRcdNm")),
                "exerciseStartDate": start,
                "exerciseEndDate": end,
                "nameLockStartDate": _text(row.get("nmlsLckSttgDt")),
                "nameLockEndDate": _text(row.get("nmlsLckEdDt")),
                "transferAgent": _text(row.get("trsnmDptyDcdNm")),
                "parValue": numeric_value(row.get("stckParPrc")),
                "eventLifecycleState": _event_state(start, end),
            }
            if len(events) >= 12:
                break
        if not events:
            return _empty_observation(self.descriptor, symbol, {"corporateActions": {}}, "no-shareholder-rights")
        base_date = _latest_date(rows)
        fetched_at = utc_now_iso()
        return observation(
            self.descriptor,
            symbol,
            {"corporateActions": {symbol: events}, "sourceArchive": {"dataset": self.descriptor.dataset_id, "rows": rows}},
            preferred_revision=stable_revision(events),
            preferred_source_as_of=source_as_of_for_base_date(base_date) or fetched_at,
            watermark={"symbol": symbol, "crno": crno, "baseDate": base_date},
            quality={"dataUsable": True, "officialSource": True, "eventCount": len(events)},
        )


__all__ = [
    "PublicDataPortalCapitalEventAdapter",
    "PublicDataPortalCompanyFinancialAdapter",
    "PublicDataPortalCompanyProfileAdapter",
    "PublicDataPortalDividendAdapter",
    "PublicDataPortalShareholderRightsAdapter",
]
