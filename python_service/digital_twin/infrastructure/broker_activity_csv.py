"""CSV adapter for normalized broker activity imports."""

import csv
from io import StringIO
from typing import Dict, List

from ..domain.broker_activity import BrokerActivity


HEADER_ALIASES = {
    "type": ("type", "activity_type", "activitytype", "구분", "유형", "거래유형"),
    "occurred_at": ("occurred_at", "occurredat", "executed_at", "date", "일시", "거래일시", "체결일시"),
    "source_reference": ("source_reference", "sourcereference", "execution_id", "id", "거래번호", "체결번호"),
    "symbol": ("symbol", "ticker", "code", "종목코드", "종목"),
    "currency": ("currency", "통화"),
    "quantity": ("quantity", "qty", "수량"),
    "unit_price": ("unit_price", "unitprice", "price", "단가", "체결가"),
    "amount": ("amount", "금액", "거래금액"),
    "fee": ("fee", "commission", "수수료"),
}

TYPE_ALIASES = {
    "매수": "BUY",
    "BUY": "BUY",
    "매도": "SELL",
    "SELL": "SELL",
    "입금": "CASH_DEPOSIT",
    "CASH_DEPOSIT": "CASH_DEPOSIT",
    "출금": "CASH_WITHDRAWAL",
    "CASH_WITHDRAWAL": "CASH_WITHDRAWAL",
    "배당": "DIVIDEND",
    "DIVIDEND": "DIVIDEND",
    "수수료": "FEE",
    "FEE": "FEE",
    "분할": "SPLIT",
    "SPLIT": "SPLIT",
}


def normalized_header(value: object) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("-", "_")


def header_map(fieldnames) -> Dict[str, str]:
    available = {normalized_header(item): str(item) for item in fieldnames or []}
    result = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            key = normalized_header(alias)
            if key in available:
                result[canonical] = available[key]
                break
    return result


def row_value(row: Dict[str, object], mapping: Dict[str, str], key: str, fallback=""):
    source = mapping.get(key)
    return row.get(source, fallback) if source else fallback


def parse_broker_activity_csv(account_id: str, provider: str, content: str) -> Dict[str, object]:
    reader = csv.DictReader(StringIO(str(content or "").lstrip("\ufeff")))
    mapping = header_map(reader.fieldnames)
    missing_headers = [key for key in ("type", "occurred_at") if key not in mapping]
    if missing_headers:
        return {
            "activities": [],
            "rejected": [{"row": 1, "reason": "missing-required-headers", "fields": missing_headers}],
            "headers": list(reader.fieldnames or []),
        }
    activities: List[BrokerActivity] = []
    rejected = []
    for row_number, row in enumerate(reader, start=2):
        raw_type = str(row_value(row, mapping, "type") or "").strip().upper()
        activity_type = TYPE_ALIASES.get(raw_type, raw_type)
        try:
            activities.append(BrokerActivity.create(
                account_id,
                provider,
                activity_type,
                str(row_value(row, mapping, "occurred_at") or "").strip(),
                source_reference=str(row_value(row, mapping, "source_reference") or "").strip(),
                symbol=str(row_value(row, mapping, "symbol") or "").strip(),
                currency=str(row_value(row, mapping, "currency") or "KRW").strip(),
                quantity=row_value(row, mapping, "quantity", 0),
                unit_price=row_value(row, mapping, "unit_price", 0),
                amount=row_value(row, mapping, "amount", 0),
                fee=row_value(row, mapping, "fee", 0),
                payload={"importRow": row_number, "importSource": "csv"},
            ))
        except (TypeError, ValueError) as error:
            rejected.append({"row": row_number, "reason": str(error)[:220]})
    return {"activities": activities, "rejected": rejected, "headers": list(reader.fieldnames or [])}
