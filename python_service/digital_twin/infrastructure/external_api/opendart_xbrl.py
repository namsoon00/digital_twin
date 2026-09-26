"""Bounded OpenDART XBRL extraction for official annual valuation facts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import io
import re
from typing import Dict, List, Mapping
from xml.etree import ElementTree
import zipfile


XBRL_INSTANCE_VERSION = "opendart-xbrl-candidate-facts-v1"
XBRL_INSTANCE_NAMESPACE = "http://www.xbrl.org/2003/instance"
XBRL_DIMENSION_NAMESPACE = "http://xbrl.org/2006/xbrldi"
XSI_NAMESPACE = "http://www.w3.org/2001/XMLSchema-instance"
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_INSTANCE_BYTES = 20 * 1024 * 1024
MAX_CANDIDATE_FACTS = 500

DCF_TAG_PATTERNS = tuple(re.compile(value, re.IGNORECASE) for value in (
    r"interestexpense",
    r"depreciation",
    r"amortisation",
    r"amortization",
    r"sharebased",
    r"weightedaverageshares",
    r"dilutedearnings",
    r"adjustmentsfordecreaseincrease",
    r"adjustmentsforincreasedecrease",
))


def _local_name(value: object) -> str:
    return str(value or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _annual_duration(start: str, end: str) -> bool:
    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except (TypeError, ValueError):
        return False
    return 270 <= days <= 430


def _number(value: object):
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)


def _contexts(root) -> Dict[str, Dict[str, object]]:
    xbrli = "{" + XBRL_INSTANCE_NAMESPACE + "}"
    xbrldi = "{" + XBRL_DIMENSION_NAMESPACE + "}"
    result = {}
    for context in root.findall(xbrli + "context"):
        context_id = str(context.attrib.get("id") or "").strip()
        if not context_id:
            continue
        start = context.find(".//" + xbrli + "startDate")
        end = context.find(".//" + xbrli + "endDate")
        instant = context.find(".//" + xbrli + "instant")
        dimensions = [
            {
                "axis": str(item.attrib.get("dimension") or ""),
                "member": str(item.text or "").strip(),
            }
            for item in context.findall(".//" + xbrldi + "explicitMember")
            if str(item.text or "").strip()
        ]
        result[context_id] = {
            "start": str(start.text or "").strip() if start is not None else "",
            "end": str(end.text or "").strip() if end is not None else (
                str(instant.text or "").strip() if instant is not None else ""
            ),
            "dimensions": dimensions,
        }
    return result


def _units(root) -> Dict[str, List[str]]:
    xbrli = "{" + XBRL_INSTANCE_NAMESPACE + "}"
    result = {}
    for unit in root.findall(xbrli + "unit"):
        unit_id = str(unit.attrib.get("id") or "").strip()
        if not unit_id:
            continue
        result[unit_id] = [
            str(item.text or "").strip()
            for item in unit.findall(".//" + xbrli + "measure")
            if str(item.text or "").strip()
        ]
    return result


def opendart_xbrl_error_response(raw: object) -> Dict[str, str]:
    data = bytes(raw or b"")
    if not data or zipfile.is_zipfile(io.BytesIO(data)):
        return {}
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return {}
    status = str(root.findtext("status") or "").strip()
    message = str(root.findtext("message") or "").strip()
    return {key: value for key, value in {"status": status, "message": message}.items() if value}


def parse_opendart_xbrl_archive(
    raw: object,
    *,
    receipt_no: str,
    symbol: str,
    business_year: str = "",
) -> Dict[str, object]:
    """Extract bounded numeric candidates from one official XBRL archive.

    The parser keeps transport parsing separate from metric selection. It only
    admits annual consolidated duration contexts and retains their exact XBRL
    tag, dimensions, unit and report identity for domain normalization.
    """

    data = bytes(raw or b"")
    if not data or len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("OpenDART XBRL archive is empty or exceeds the bounded size")
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ValueError("OpenDART XBRL response is not a ZIP archive")
    archive_hash = hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        candidates = [
            info for info in archive.infolist()
            if not info.is_dir() and info.filename.lower().endswith(".xbrl")
            and 0 < info.file_size <= MAX_INSTANCE_BYTES
        ]
        if not candidates:
            raise ValueError("OpenDART XBRL archive has no bounded instance document")
        instance = max(candidates, key=lambda item: item.file_size)
        instance_raw = archive.read(instance)
    try:
        root = ElementTree.fromstring(instance_raw)
    except ElementTree.ParseError as error:
        raise ValueError("OpenDART XBRL instance is malformed") from error

    contexts = _contexts(root)
    units = _units(root)
    facts = []
    duplicate_guard = set()
    for element in root.iter():
        context_id = str(element.attrib.get("contextRef") or "").strip()
        unit_id = str(element.attrib.get("unitRef") or "").strip()
        context = contexts.get(context_id)
        if not context or not unit_id:
            continue
        if str(element.attrib.get("{" + XSI_NAMESPACE + "}nil") or "").lower() == "true":
            continue
        start, end = str(context.get("start") or ""), str(context.get("end") or "")
        if not _annual_duration(start, end):
            continue
        dimensions = context.get("dimensions") if isinstance(context.get("dimensions"), list) else []
        members = [_local_name(item.get("member")) for item in dimensions if isinstance(item, Mapping)]
        if "ConsolidatedMember" not in members:
            continue
        tag = _local_name(element.tag)
        if not any(pattern.search(tag) for pattern in DCF_TAG_PATTERNS):
            continue
        value = _number(element.text)
        if value is None:
            continue
        row = {
            "tag": tag,
            "namespace": str(element.tag).split("}", 1)[0].lstrip("{") if "}" in str(element.tag) else "",
            "value": value,
            "unitId": unit_id,
            "unitMeasures": list(units.get(unit_id) or []),
            "decimals": str(element.attrib.get("decimals") or ""),
            "contextId": context_id,
            "periodStart": start,
            "periodEnd": end,
            "dimensions": [dict(item) for item in dimensions if isinstance(item, Mapping)],
        }
        identity = (
            row["tag"], row["value"], row["unitId"], row["contextId"],
        )
        if identity in duplicate_guard:
            continue
        duplicate_guard.add(identity)
        facts.append(row)
        if len(facts) >= MAX_CANDIDATE_FACTS:
            break

    periods = sorted({str(item.get("periodEnd") or "") for item in facts if item.get("periodEnd")})
    return {
        "schemaVersion": XBRL_INSTANCE_VERSION,
        "provider": "OpenDART XBRL",
        "symbol": str(symbol or "").upper().strip(),
        "receiptNo": str(receipt_no or "").strip(),
        "businessYear": str(business_year or "").strip(),
        "archiveHash": archive_hash,
        "instanceFile": instance.filename,
        "instanceHash": hashlib.sha256(instance_raw).hexdigest(),
        "periods": periods,
        "candidateFacts": facts,
        "candidateFactCount": len(facts),
        "sourceUrl": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + str(receipt_no or "").strip(),
    }
