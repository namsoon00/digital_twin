"""Bounded, issuer-owned earnings schedules; never infer next quarter's date."""

import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from digital_twin.infrastructure.external_signal_utils import default_text_fetcher, external_call_target, guarded_external_call
from digital_twin.modules.investment_calendar.domain.investment_calendar import utc_iso
from digital_twin.modules.investment_calendar.domain.official_calendar import OfficialCalendarEvent


ISSUER_PAGES = {
    "AAPL": "https://investor.apple.com/investor-relations/default.aspx",
    "PLTR": "https://investors.palantir.com/events.html",
    # Use the newsroom's public search form; unrelated product news must not
    # push the still-upcoming earnings announcement out of the first page.
    "NVDA": "https://nvidianews.nvidia.com/news?q=sets+conference+call",
}
QUARTERS = {"first": 1, "second": 2, "third": 3, "fourth": 4}
ZONES = {"PT": "America/Los_Angeles", "ET": "America/New_York"}


def plain_text(markup):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(markup or ""))).split())


def official_event(symbol, year, quarter, local_start, source_url, excerpt, *, date_only=False):
    identity = f"{symbol}:earnings:{year}-q{quarter}:conference-call"
    return OfficialCalendarEvent(
        event_id=f"official-us-ir-{symbol.lower()}-{year}-q{quarter}",
        title=f"{symbol} FY{year} {quarter}분기 실적 발표",
        event_type="earnings", starts_at=utc_iso(local_start), timezone=str(local_start.tzinfo),
        all_day=date_only, importance=95, symbols=[symbol], markets=["US"],
        source=f"{symbol} Investor Relations", source_url=source_url,
        notes="공식 IR의 실적 콘퍼런스콜 일정입니다." + (" 현지 발표일만 확인됐으며 시각은 미확인입니다." if date_only else ""),
        reminder_offsets_minutes=[] if date_only else [1440, 180, 60, 0],
        payload={
            "autoDetected": True, "officialSource": True, "scheduleState": "confirmed",
            "reviewRequired": False, "sourceProvider": f"{symbol} IR", "sourceTrustState": "trusted",
            "calendarIdentity": identity, "fiscalYear": year, "fiscalQuarter": quarter,
            "schedulePhase": "conference-call", "dateSource": "official-us-issuer-calendar",
            "timeState": "dateOnly" if date_only else "sourceProvided",
            "timePrecision": "date" if date_only else "minute",
            "eventLocalDate": local_start.date().isoformat(),
            "eventLocalTime": "" if date_only else local_start.strftime("%H:%M"),
            "sourceExcerpt": excerpt, "sourceHash": hashlib.sha256(excerpt.encode()).hexdigest(),
            "detector": "official-us-issuer-calendar-v1",
        },
    )


def parse_apple_events(data):
    events = []
    for row in data.get("GetEventListResult", []):
        title = plain_text(row.get("Title"))
        match = re.fullmatch(r"FY (\d{2}|20\d{2}) (First|Second|Third|Fourth) Quarter Results", title, re.I)
        zone = ZONES.get(str(row.get("TimeZone") or "").upper())
        if not match or not zone:
            continue
        try:
            starts = datetime.strptime(row["StartDate"], "%m/%d/%Y %H:%M:%S").replace(tzinfo=ZoneInfo(zone))
        except (ValueError, KeyError, TypeError):
            continue
        url = urljoin(ISSUER_PAGES["AAPL"], row.get("LinkToDetailPage") or "")
        if urlsplit(url).hostname != "investor.apple.com" or urlsplit(url).scheme != "https":
            continue
        year = int(match[1]) + (2000 if len(match[1]) == 2 else 0)
        events.append(official_event("AAPL", year, QUARTERS[match[2].lower()], starts, url,
                                     f"{title} | {row['StartDate']} {row['TimeZone']}"))
    return events


def parse_palantir_events(markup):
    events = []
    pattern = r'<p class="report-date[^\"]*">([^<]+)</p>\s*<p class="report-name[^\"]*">(Q([1-4]) (20\d{2}) Earnings)</p>'
    for match in re.finditer(pattern, markup, re.I):
        try:
            starts = datetime.strptime(re.sub(r"\s+", "", match[1]), "%m/%d/%y").replace(tzinfo=ZoneInfo("America/New_York"))
        except ValueError:
            continue
        events.append(official_event("PLTR", int(match[4]), int(match[3]), starts, ISSUER_PAGES["PLTR"],
                                     plain_text(match[0]), date_only=True))
    return events


def parse_nvidia_events(markup):
    events = []
    for article in re.findall(r"<article\b[^>]*>(.*?)</article>", markup, re.I | re.S):
        link = re.search(r'href="([^\"]*nvidia-sets-conference-call[^\"]*)"', article, re.I)
        published = re.search(r'class="index-item-text-info-date">([^<]+)', article)
        text = plain_text(article)
        schedule = re.search(
            r"will host a conference call on \w+, (\w+ \d{1,2})(?:, (20\d{2}))?, at (\d{1,2})(?::(\d{2}))? ([ap])\.m\. PT", text, re.I)
        fiscal = re.search(r"(first|second|third|fourth)[- ]quarter of fiscal year (20\d{2})", text, re.I)
        if not all((link, published, schedule, fiscal)):
            continue
        try:
            published_date = datetime.strptime(published[1].strip(), "%B %d, %Y")
            year = int(schedule[2] or published_date.year)
            starts = datetime.strptime(f"{schedule[1]} {year}", "%B %d %Y")
            # An announcement in December may name a January call without its year.
            if not schedule[2] and starts < published_date - timedelta(days=7):
                starts = starts.replace(year=year + 1)
            if not published_date - timedelta(days=7) <= starts <= published_date + timedelta(days=120):
                continue
            hour = int(schedule[3])
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if schedule[5].lower() == "p" else 0)
            starts = starts.replace(hour=hour, minute=int(schedule[4] or 0), tzinfo=ZoneInfo("America/Los_Angeles"))
        except ValueError:
            continue
        url = urljoin(ISSUER_PAGES["NVDA"], html.unescape(link[1]))
        if urlsplit(url).hostname != "nvidianews.nvidia.com" or urlsplit(url).scheme != "https":
            continue
        events.append(official_event("NVDA", int(fiscal[2]), QUARTERS[fiscal[1].lower()], starts, url, text))
    return events


class UsIssuerEarningsCalendarSource:
    def __init__(self, symbol, settings=None, fetch_text=None, now=None):
        self.symbol = symbol
        self.label = f"OfficialIssuerIR/{symbol}"
        self.settings = dict(settings or {})
        self.fetch_text = fetch_text or default_text_fetcher
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _fetch(self, url):
        # Public Q4 feed credentials must never enter exception logs/checkpoints.
        def fetch():
            try:
                return self.fetch_text(url, {"Accept": "text/html,application/json", "User-Agent": "Mozilla/5.0"}, 10.0)
            except Exception as error:
                code = getattr(error, "code", None)
                raise RuntimeError(f"official source unavailable ({type(error).__name__}{':' + str(code) if code else ''})") from None
        return guarded_external_call(self.settings, self.label, external_call_target(ISSUER_PAGES[self.symbol]), fetch)

    def events(self):
        if str(self.settings.get("investmentCalendarOfficialEarningsSyncEnabled", "1")).lower() in {"0", "false", "off", "no", "disabled"}:
            return []
        markup = self._fetch(ISSUER_PAGES[self.symbol])
        if self.symbol == "AAPL":
            key = re.search(r'Q4ApiKey\s*=\s*[\"\x27]([^\"\x27]+)', markup)
            if not key:
                raise ValueError("official public calendar feed not found")
            params = {"apiKey": key[1], "LanguageId": 1, "eventSelection": 3, "eventDateFilter": 3,
                      "sortOperator": 1, "pageSize": 16, "pageNumber": 0, "year": -1}
            data = json.loads(self._fetch("https://investor.apple.com/feed/Event.svc/GetEventList?" + urlencode(params)))
            events = parse_apple_events(data)
        elif self.symbol == "PLTR":
            events = parse_palantir_events(markup)
        else:
            events = parse_nvidia_events(markup)
        if not events:
            raise ValueError("official page contains no recognized earnings schedules; coverage unverified")
        cutoff = self.now() - timedelta(days=180)
        horizon = self.now() + timedelta(days=366)
        return [event for event in events if cutoff <= datetime.fromisoformat(event.starts_at.replace("Z", "+00:00")) <= horizon]
