import io
import re
import urllib.parse
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from xml.etree import ElementTree

from ..domain.official_calendar import OfficialCalendarEvent, kst_datetime
from ..domain.investment_calendar import utc_iso
from .external_signal_utils import (
    DISABLED_SETTING_VALUES,
    dart_document_text,
    default_bytes_fetcher,
    default_json_fetcher,
    external_call_target,
    guarded_external_call,
    guarded_int_setting,
    symbol_assignments,
)


OPENDART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
OPENDART_DOCUMENT_URL = "https://opendart.fss.or.kr/api/document.xml"
OPENDART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do"


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_SETTING_VALUES


def bounded_int(value: object, fallback: int, lower: int, upper: int) -> int:
    try:
        parsed = int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        parsed = fallback
    return max(lower, min(parsed, upper))


def opendart_document_url(receipt_no: str) -> str:
    return DART_VIEWER_URL + "?" + urllib.parse.urlencode({"rcpNo": str(receipt_no or "").strip()})


def parse_opendart_corp_codes(raw: object, target_symbols: List[str] = None) -> Dict[str, str]:
    data = bytes(raw or b"")
    if not data:
        return {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
            data = archive.read(names[0]) if names else b""
    except (OSError, zipfile.BadZipFile):
        pass
    if not data:
        return {}
    wanted = {str(symbol or "").zfill(6) for symbol in target_symbols or [] if str(symbol or "").strip()}
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return {}
    assignments = {}
    for row in root.findall(".//list"):
        stock_code = str(row.findtext("stock_code") or "").strip().zfill(6)
        corp_code = str(row.findtext("corp_code") or "").strip().zfill(8)
        if (
            stock_code != "000000"
            and corp_code != "00000000"
            and (not wanted or stock_code in wanted)
        ):
            assignments[stock_code] = corp_code
    return assignments


def is_earnings_ir_report(row: Dict[str, object]) -> bool:
    name = re.sub(r"\s+", "", str((row or {}).get("report_nm") or ""))
    return "기업설명회" in name and ("IR" in name.upper() or "안내공시" in name)


def parse_opendart_earnings_event(
    document_text: str,
    symbol: str,
    company_name: str,
    receipt_no: str,
) -> Optional[OfficialCalendarEvent]:
    text = " ".join(str(document_text or "").split())
    lowered = text.casefold()
    if not text or not any(term in lowered for term in ("경영실적", "실적발표", "earnings")):
        return None
    when = re.search(
        r"일시\s*(?:및\s*장소\s*)?일시\s*(20\d{2})\s*(?:[-./년])\s*(\d{1,2})\s*(?:[-./월])\s*(\d{1,2})(?:일)?\s*(\d{1,2}:\d{2})",
        text,
    )
    if not when:
        return None
    fiscal = re.search(r"(20\d{2})년\s*([1-4])\s*분기", text)
    year = int(when.group(1))
    month = int(when.group(2))
    day = int(when.group(3))
    time_kst = when.group(4)
    fiscal_year = int(fiscal.group(1)) if fiscal else year
    quarter = int(fiscal.group(2)) if fiscal else max(1, min(4, (month - 1) // 3 + 1))
    starts = kst_datetime(year, month, day, time_kst)
    normalized_symbol = str(symbol or "").upper().strip()
    company = str(company_name or normalized_symbol).strip() or normalized_symbol
    event_id = "official-opendart-earnings-{}-{}-q{}".format(normalized_symbol, fiscal_year, quarter)
    return OfficialCalendarEvent(
        event_id=event_id,
        title="{} {}년 {}분기 실적 발표".format(company, fiscal_year, quarter),
        event_type="earnings",
        starts_at=utc_iso(starts),
        timezone="Asia/Seoul",
        all_day=False,
        status="active",
        importance=90,
        symbols=[normalized_symbol],
        markets=["KOSPI"] if re.fullmatch(r"\d{6}", normalized_symbol) else [],
        source="OpenDART",
        source_url=opendart_document_url(receipt_no),
        notes="OpenDART 기업설명회(IR) 개최 공시에서 확인한 실적 발표 일정입니다.",
        reminder_offsets_minutes=[1440, 180, 60, 0],
        payload={
            "autoDetected": True,
            "officialSource": True,
            "scheduleState": "confirmed",
            "reviewRequired": False,
            "sourceProvider": "OpenDART",
            "sourceTrustState": "trusted",
            "receiptNo": str(receipt_no or "").strip(),
            "calendarIdentity": "{}:earnings:{}-q{}".format(normalized_symbol, fiscal_year, quarter),
            "fiscalYear": fiscal_year,
            "fiscalQuarter": quarter,
            "dateSource": "opendart-ir-announcement",
            "detector": "official-opendart-calendar-sync-v1",
        },
    )


class OpenDartEarningsCalendarSource:
    """Fetch issuer-confirmed Korean earnings schedules from OpenDART."""

    def __init__(
        self,
        settings: Dict[str, object] = None,
        fetch_json: Callable[[str, Dict[str, str], float], Dict[str, object]] = None,
        fetch_bytes: Callable[[str, Dict[str, str], float], bytes] = None,
        now: Callable[[], datetime] = None,
        guard_state: Dict[str, object] = None,
        target_symbols: List[str] = None,
    ):
        self.settings = dict(settings or {})
        self.fetch_json = fetch_json or default_json_fetcher
        self.fetch_bytes = fetch_bytes or default_bytes_fetcher
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.guard_state = guard_state
        self.target_symbols = []
        for raw in target_symbols or []:
            symbol = str(raw or "").upper().strip()
            if re.fullmatch(r"\d{6}", symbol) and symbol not in self.target_symbols:
                self.target_symbols.append(symbol)

    def enabled(self) -> bool:
        return bool(
            truthy(self.settings.get("investmentCalendarOfficialMacroSyncEnabled"), True)
            and truthy(self.settings.get("investmentCalendarOfficialEarningsSyncEnabled"), True)
            and str(self.settings.get("opendartApiKey") or "").strip()
            and (symbol_assignments(self.settings.get("externalDartCorpCodes") or "") or self.target_symbols)
        )

    def timeout_seconds(self) -> float:
        try:
            return max(1.0, min(float(self.settings.get("investmentCalendarOfficialMacroSyncTimeoutSeconds") or 8), 30.0))
        except (TypeError, ValueError):
            return 8.0

    def lookback_days(self) -> int:
        return bounded_int(self.settings.get("investmentCalendarOfficialEarningsLookbackDays"), 180, 14, 365)

    def max_symbols(self) -> int:
        return bounded_int(self.settings.get("investmentCalendarOfficialEarningsMaxSymbols"), 20, 1, 100)

    def fetch(self, url: str, fetcher: Callable, headers: Dict[str, str]):
        return guarded_external_call(
            self.settings,
            "OpenDART calendar",
            external_call_target(url),
            lambda: fetcher(url, headers, self.timeout_seconds()),
            state=self.guard_state,
            rate_limit_seconds=guarded_int_setting(
                self.settings,
                "investmentCalendarOfficialEarningsRateLimitSeconds",
                0,
                0,
                86400,
            ),
        )

    def list_rows(self, corp_code: str) -> List[Dict[str, object]]:
        now_at = self.now().astimezone(timezone.utc)
        params = {
            "crtfc_key": str(self.settings.get("opendartApiKey") or "").strip(),
            "corp_code": str(corp_code or "").zfill(8),
            "bgn_de": (now_at - timedelta(days=self.lookback_days())).strftime("%Y%m%d"),
            "end_de": now_at.strftime("%Y%m%d"),
            "page_no": "1",
            "page_count": "100",
        }
        url = OPENDART_LIST_URL + "?" + urllib.parse.urlencode(params)
        result = self.fetch(url, self.fetch_json, {"Accept": "application/json"})
        if not isinstance(result, dict) or str(result.get("status") or "") not in {"", "000"}:
            raise RuntimeError(str((result or {}).get("message") or "OpenDART 목록 응답 오류"))
        return [row for row in result.get("list") or [] if isinstance(row, dict) and is_earnings_ir_report(row)]

    def corp_code_assignments(self) -> Dict[str, str]:
        configured = symbol_assignments(self.settings.get("externalDartCorpCodes") or "")
        targets = []
        for symbol in list(configured) + self.target_symbols:
            if re.fullmatch(r"\d{6}", str(symbol or "")) and symbol not in targets:
                targets.append(symbol)
        missing = [symbol for symbol in targets if symbol not in configured]
        if not missing:
            return {symbol: configured[symbol] for symbol in targets[: self.max_symbols()]}
        params = {"crtfc_key": str(self.settings.get("opendartApiKey") or "").strip()}
        url = OPENDART_CORP_CODE_URL + "?" + urllib.parse.urlencode(params)
        try:
            raw = self.fetch(url, self.fetch_bytes, {"Accept": "application/zip,application/xml"})
            configured.update(parse_opendart_corp_codes(raw, missing))
        except Exception:
            if not configured:
                raise
        return {
            symbol: configured[symbol]
            for symbol in targets[: self.max_symbols()]
            if symbol in configured
        }

    def document_text(self, receipt_no: str) -> str:
        params = {
            "crtfc_key": str(self.settings.get("opendartApiKey") or "").strip(),
            "rcept_no": str(receipt_no or "").strip(),
        }
        url = OPENDART_DOCUMENT_URL + "?" + urllib.parse.urlencode(params)
        raw = self.fetch(url, self.fetch_bytes, {"Accept": "application/zip,application/xml"})
        return dart_document_text(raw, 12000)

    def events(self) -> List[OfficialCalendarEvent]:
        if not self.enabled():
            return []
        assignments = self.corp_code_assignments()
        events: List[OfficialCalendarEvent] = []
        seen = set()
        for symbol, corp_code in list(assignments.items())[: self.max_symbols()]:
            for row in self.list_rows(corp_code):
                receipt_no = str(row.get("rcept_no") or "").strip()
                if not receipt_no:
                    continue
                event = parse_opendart_earnings_event(
                    self.document_text(receipt_no),
                    symbol,
                    str(row.get("corp_name") or symbol),
                    receipt_no,
                )
                if not event or event.event_id in seen:
                    continue
                seen.add(event.event_id)
                events.append(event)
        return sorted(events, key=lambda event: event.starts_at)
