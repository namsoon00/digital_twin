import html
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Dict, Iterable, List

from ..domain.official_calendar import (
    DEFAULT_BOK_POLICY_DECISION_TIME_KST,
    OfficialCalendarEvent,
    bok_policy_decision_event,
)
from .external_signal_utils import (
    DISABLED_SETTING_VALUES,
    external_call_target,
    guarded_external_call,
    guarded_int_setting,
)


BOK_POLICY_DECISION_URL = "https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do"


def truthy(value: object, default: bool = True) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    return text not in DISABLED_SETTING_VALUES


def number_setting(settings: Dict[str, object], key: str, fallback: int, lower: int = 0, upper: int = 10) -> int:
    try:
        value = int(float(str((settings or {}).get(key) or "").strip()))
    except (TypeError, ValueError):
        value = fallback
    return max(lower, min(upper, value))


def default_text_fetcher(url: str, headers: Dict[str, str] = None, timeout: float = 8.0) -> str:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=max(0.5, float(timeout or 8.0))) as response:
        raw = response.read()
        encoding = response.headers.get_content_charset() or "utf-8"
        return raw.decode(encoding, errors="replace")


def bok_policy_decision_url(year: int = 0) -> str:
    params = {"mtgSe": "A", "menuNo": "200755"}
    if int(year or 0):
        params["pYear"] = str(int(year))
    return BOK_POLICY_DECISION_URL + "?" + urllib.parse.urlencode(params)


def selected_bok_year(markup: str, fallback: int) -> int:
    match = re.search(r"<h3>\s*(20\d{2})년\s*</h3>", markup or "")
    if match:
        return int(match.group(1))
    return int(fallback or datetime.now().year)


def parse_bok_policy_decision_events(
    markup: str,
    year: int = 0,
    source_url: str = "",
    time_kst: object = DEFAULT_BOK_POLICY_DECISION_TIME_KST,
) -> List[OfficialCalendarEvent]:
    content = html.unescape(str(markup or ""))
    event_year = selected_bok_year(content, int(year or datetime.now().year))
    events: List[OfficialCalendarEvent] = []
    seen = set()
    for match in re.finditer(
        r"<th\b[^>]*scope=[\"']row[\"'][^>]*>\s*(\d{1,2})월\s*(\d{1,2})일(?:\(([^)]*)\))?\s*</th>",
        content,
        flags=re.IGNORECASE,
    ):
        month = int(match.group(1))
        day = int(match.group(2))
        key = (event_year, month, day)
        if key in seen:
            continue
        seen.add(key)
        events.append(
            bok_policy_decision_event(
                event_year,
                month,
                day,
                source_url=source_url or bok_policy_decision_url(event_year),
                time_kst=time_kst,
                weekday=match.group(3) or "",
            )
        )
    return events


class BokPolicyDecisionCalendarSource:
    def __init__(
        self,
        settings: Dict[str, object] = None,
        fetch_text: Callable[[str, Dict[str, str], float], str] = None,
        now: Callable[[], datetime] = None,
        guard_state: Dict[str, object] = None,
    ):
        self.settings = dict(settings or {})
        self.fetch_text = fetch_text or default_text_fetcher
        self.now = now or datetime.now
        self.guard_state = guard_state

    def enabled(self) -> bool:
        return truthy(self.settings.get("investmentCalendarOfficialMacroSyncEnabled"), True) and truthy(
            self.settings.get("investmentCalendarBokPolicyDecisionEnabled"),
            True,
        )

    def years(self) -> List[int]:
        current_year = int(self.now().year)
        lookahead = number_setting(self.settings, "investmentCalendarBokPolicyDecisionLookaheadYears", 1, 0, 3)
        return list(range(current_year, current_year + lookahead + 1))

    def timeout_seconds(self) -> float:
        try:
            return max(1.0, min(float(self.settings.get("investmentCalendarOfficialMacroSyncTimeoutSeconds") or 8), 30.0))
        except (TypeError, ValueError):
            return 8.0

    def fetch_year(self, year: int) -> str:
        url = bok_policy_decision_url(year)
        headers = {"Accept": "text/html", "User-Agent": "DigitalTwin/1.0"}
        return guarded_external_call(
            self.settings,
            "BOK Calendar",
            external_call_target(url),
            lambda: self.fetch_text(url, headers, self.timeout_seconds()),
            state=self.guard_state,
            rate_limit_seconds=guarded_int_setting(
                self.settings,
                "investmentCalendarOfficialMacroSyncRateLimitSeconds",
                600,
                0,
                86400,
            ),
        )

    def events(self, years: Iterable[int] = None) -> List[OfficialCalendarEvent]:
        if not self.enabled():
            return []
        selected_years = list(years or self.years())
        time_kst = self.settings.get("investmentCalendarBokPolicyDecisionTimeKst") or DEFAULT_BOK_POLICY_DECISION_TIME_KST
        events: List[OfficialCalendarEvent] = []
        seen = set()
        for year in selected_years:
            source_url = bok_policy_decision_url(int(year))
            for event in parse_bok_policy_decision_events(
                self.fetch_year(int(year)),
                year=int(year),
                source_url=source_url,
                time_kst=time_kst,
            ):
                if event.event_id in seen:
                    continue
                seen.add(event.event_id)
                events.append(event)
        return sorted(events, key=lambda item: item.starts_at)
