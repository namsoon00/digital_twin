"""Strict extraction of reported values, not forecasts or investment signals."""

import hashlib
import re
from datetime import datetime, timezone
from fractions import Fraction
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo


MONTHS = {name.lower(): index for index, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}
MONTH_PATTERN = "(?:" + "|".join(MONTHS) + ")"
DATE_PATTERN = "(" + MONTH_PATTERN + r")\s+(\d{1,2}),\s*(20\d{2})"
EASTERN = ZoneInfo("America/New_York")
RELEASE_URLS = {
    "cpi": "https://www.bls.gov/news.release/cpi.nr0.htm",
    "employment": "https://www.bls.gov/news.release/empsit.nr0.htm",
    "fomc": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
}


class ReleaseHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.links = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))
        if tag in {"p", "br", "div", "h1", "h2", "h3", "td", "tr"}:
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "h1", "h2", "h3", "td", "tr"}:
            self.chunks.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)

    @property
    def text(self):
        return re.sub(r"\s+", " ", "".join(self.chunks)).strip()


def parse_html(markup):
    if len(str(markup)) > 2_000_000:
        raise ValueError("Official release exceeds size limit")
    document = ReleaseHTML()
    document.feed(str(markup or ""))
    return document


def latest_fomc_statement_url(markup, now=None):
    today = (now or datetime.now(timezone.utc)).astimezone(EASTERN).date()
    candidates = []
    for href in parse_html(markup).links:
        url = urljoin(RELEASE_URLS["fomc"], href)
        parts = urlsplit(url)
        match = re.fullmatch(r"/newsevents/pressreleases/monetary(20\d{6})a\.htm", parts.path)
        if parts.scheme == "https" and parts.hostname == "www.federalreserve.gov" and match and not parts.query and not parts.username and not parts.password:
            released = datetime.strptime(match[1], "%Y%m%d").date()
            if released <= today:
                candidates.append((released, url))
    if not candidates:
        raise ValueError("No published FOMC statement link on official calendar")
    return max(candidates)[1]


def _metric(key, label, match, value, unit, basis, previous=None):
    return {"key": key, "label": label, "actual": value, "unit": unit, "basis": basis,
            "previous": previous, "consensus": None, "excerpt": match[0], "evidenceRole": "official-reported"}


def _signed(verb, value):
    number = float(value.replace(",", ""))
    return -number if verb.lower() in {"decreased", "declined", "fell", "falling"} else number


def parse_official_release(indicator, markup, source_url, now=None):
    if indicator not in RELEASE_URLS:
        raise ValueError("Unsupported official release indicator")
    parts = urlsplit(source_url)
    expected_host = "www.federalreserve.gov" if indicator == "fomc" else "www.bls.gov"
    if parts.scheme != "https" or parts.hostname != expected_host or parts.username or parts.password:
        raise ValueError("Official release source host mismatch")
    text = parse_html(markup).text
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    metrics = []
    released_at = ""
    period = ""
    if indicator in {"cpi", "employment"}:
        embargo = re.search(r"Transmission of material.*?embargoed until\s*(\d{1,2}):(\d{2})\s*a\.m\.\s*\(ET\).*?" + DATE_PATTERN, text, re.I)
        if not embargo:
            raise ValueError("Official release publication time not found")
        released = datetime(int(embargo[5]), MONTHS[embargo[3].lower()], int(embargo[4]), int(embargo[1]), int(embargo[2]), tzinfo=EASTERN)
        released_at = released.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        heading = "CONSUMER PRICE INDEX" if indicator == "cpi" else "THE EMPLOYMENT SITUATION"
        header = re.search(re.escape(heading) + r"\s*[-\u2013\u2014]\s*(" + MONTH_PATTERN + r")\s+(20\d{2})", text[embargo.end():], re.I)
        if not header:
            raise ValueError("Official release reference period not found")
        period = header[2] + "-" + str(MONTHS[header[1].lower()]).zfill(2)
        start = embargo.end() + header.end()
        end = re.search(r"Table A\.|Household Survey Data|Technical Note", text[start:], re.I)
        body = text[start:start + end.start()] if end else text[start:start + 5000]
        if indicator == "cpi":
            match = re.search(r"The Consumer Price Index for All Urban Consumers \(CPI-U\) (increased|rose|decreased|declined|fell) ([\d.]+) percent on a seasonally adjusted basis in " + MONTH_PATTERN + r"(?: after (rising|increasing|falling|decreasing) ([\d.]+) percent in " + MONTH_PATTERN + r")?", body, re.I)
            if match:
                previous = _signed("fell" if (match[3] or "").lower() in {"falling", "decreasing"} else "rose", match[4]) if match[4] else None
                metrics.append(_metric("cpi-mom", "소비자물가 전월 대비", match, _signed(match[1], match[2]), "%", "seasonally-adjusted-monthly-change", previous))
            match = re.search(r"Over the last 12 months, the all items index (increased|rose|decreased|declined|fell) ([\d.]+) percent before seasonal adjustment", body, re.I)
            if match:
                metrics.append(_metric("cpi-yoy", "소비자물가 전년 동월 대비", match, _signed(match[1], match[2]), "%", "unadjusted-yearly-change"))
        else:
            match = re.search(r"Total nonfarm payroll employment (increased|rose|decreased|declined|fell)(?: by)? ([\d,]+) in " + MONTH_PATTERN, body, re.I)
            if match:
                metrics.append(_metric("payrolls", "비농업 고용 전월 대비", match, int(_signed(match[1], match[2])), "명", "seasonally-adjusted-monthly-change"))
            match = re.search(r"(?:the )?unemployment rate (?:changed little at|was unchanged at|remained at|held (?:steady )?at|rose to|increased to|declined to|fell to|edged (?:up|down) to) ([\d.]+) percent", body, re.I)
            if match:
                metrics.append(_metric("unemployment-rate", "실업률", match, float(match[1]), "%", "seasonally-adjusted-level"))
    else:
        date_match = re.search(DATE_PATTERN + r"\s*(?:Federal Reserve issues FOMC statement|For release at)", text, re.I)
        if not date_match:
            raise ValueError("FOMC statement publication date not found")
        released = datetime(int(date_match[3]), MONTHS[date_match[1].lower()], int(date_match[2]), tzinfo=EASTERN)
        path_date = re.search(r"monetary(20\d{6})a\.htm$", parts.path)
        if not path_date or path_date[1] != released.strftime("%Y%m%d"):
            raise ValueError("FOMC statement URL and publication date disagree")
        clock = re.search(r"For release at (\d{1,2}):(\d{2}) p\.m\.\s*(?:EDT|EST)", text, re.I)
        if clock:
            released = released.replace(hour=int(clock[1]) % 12 + 12, minute=int(clock[2]))
            released_at = released.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        body = text[date_match.start():]
        body = re.split(r"For media inquiries|Voting for the", body, maxsplit=1, flags=re.I)[0]
        numeric = r"(\d+(?:\.\d+)?(?:[\s-]+\d/\d)?)"
        match = re.search(r"target range for the federal funds rate (?:at|to) " + numeric + r" to " + numeric + r" percent", body, re.I)
        if match:
            values = [sum(float(Fraction(piece)) for piece in value.replace("-", " ").split()) for value in (match[1], match[2])]
            if not 0 <= values[0] <= values[1] <= 100:
                raise ValueError("Invalid federal funds target range")
            metrics.append(_metric("federal-funds-target", "정책금리 목표 범위", match, values, "%", "target-range"))
        period = released.date().isoformat()
    if released > now:
        raise ValueError("Official release is dated in the future")
    if not metrics:
        raise ValueError("Official release format changed: no supported value could be verified")
    body = body[:12000]
    if any(row["excerpt"] not in body for row in metrics):
        raise ValueError("Official value excerpt is outside retained source text")
    source_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return {"version": "official-release-v1", "indicator": indicator, "country": "US", "source": "Federal Reserve" if indicator == "fomc" else "BLS",
            "sourceUrl": source_url, "sourceHash": source_hash, "releasedDate": released.date().isoformat(),
            "releasedAt": released_at, "referencePeriod": period, "metrics": metrics, "sourceText": body[:12000],
            "consensusState": "not-collected", "decisionAuthority": False}
