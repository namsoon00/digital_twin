"""Source-bound BOK policy statements. No inferred publication clock."""

import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from zoneinfo import ZoneInfo

from .official_release import parse_html


BOK_HOME = "https://www.bok.or.kr/portal/main/main.do"
BOK_PATH = "/portal/bbs/P0000559/view.do"
SEOUL = ZoneInfo("Asia/Seoul")
TITLE_DATE = r"통화정책방향\s*\(\s*(20\d{2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*\)"


class StatementLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.href = ""
        self.parts = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.href = dict(attrs).get("href", "")
            self.parts = []

    def handle_data(self, data):
        if self.href:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.href:
            self.links.append((self.href, " ".join(self.parts)))
            self.href = ""


def bok_statement_url(value):
    parts = urlsplit(urljoin(BOK_HOME, value))
    identity = parse_qs(parts.query).get("nttId", [])
    if parts.scheme != "https" or parts.hostname != "www.bok.or.kr" or parts.path != BOK_PATH or parts.username or parts.password or len(identity) != 1 or not re.fullmatch(r"\d+", identity[0]):
        raise ValueError("Invalid BOK statement URL")
    return "https://www.bok.or.kr" + BOK_PATH + "?" + urlencode({"nttId": identity[0], "menuNo": "200690"})


def latest_bok_statement_url(markup, now=None):
    if len(str(markup)) > 2_000_000:
        raise ValueError("BOK page exceeds size limit")
    parser = StatementLinks()
    parser.feed(str(markup))
    today = (now or datetime.now(timezone.utc)).astimezone(SEOUL).date()
    links = []
    for href, title in parser.links:
        match = re.search(TITLE_DATE, title)
        if not match:
            continue
        try:
            date = datetime(*map(int, match.groups())).date()
            url = bok_statement_url(href)
        except ValueError:
            continue
        if date <= today:
            links.append((date, url))
    if not links:
        raise ValueError("No published BOK policy statement link found")
    return max(links)[1]


def parse_bok_statement(markup, url, now=None):
    url = bok_statement_url(url)
    text = parse_html(markup).text
    title = re.search(TITLE_DATE, text)
    registered = re.search(r"등록일\s*(20\d{2})\.(\d{2})\.(\d{2})", text)
    if not title or not registered or tuple(map(int, title.groups())) != tuple(map(int, registered.groups())):
        raise ValueError("BOK statement title and publication date must agree")
    date = datetime(*map(int, title.groups()), tzinfo=SEOUL).date()
    if date > (now or datetime.now(timezone.utc)).astimezone(SEOUL).date():
        raise ValueError("BOK statement is dated in the future")
    opening = re.search(r"금융통화위원회는\s+다음\s*통화정책방향\s*결정시까지", text)
    if not opening:
        raise ValueError("BOK policy decision paragraph not found")
    body = text[opening.start():opening.start() + 12000]
    match = re.search(r"한국은행\s*기준금리를\s*현재의\s*(\d+(?:\.\d+)?)%\s*(?:수준)?에서\s*(\d+(?:\.\d+)?)%로\s*(상향|하향)\s*조정", body)
    if match:
        previous, actual = float(match[1]), float(match[2])
        if (match[3] == "상향" and actual <= previous) or (match[3] == "하향" and actual >= previous):
            raise ValueError("BOK rate direction and reported values disagree")
    else:
        match = re.search(r"한국은행\s*기준금리를\s*현재의\s*(\d+(?:\.\d+)?)%\s*수준에서\s*유지", body)
        if not match:
            raise ValueError("BOK rate value could not be verified")
        previous = actual = float(match[1])
    if not 0 <= min(previous, actual) <= max(previous, actual) <= 100:
        raise ValueError("Invalid BOK policy rate")
    return {"version": "official-release-v1", "indicator": "bok", "country": "KR", "source": "한국은행",
            "sourceUrl": url, "sourceHash": hashlib.sha256(body.encode()).hexdigest(), "sourceText": body,
            "releasedDate": date.isoformat(), "releasedAt": "", "timePrecision": "date", "referencePeriod": date.isoformat(),
            "metrics": [{"key": "bok-base-rate", "label": "한국은행 기준금리", "actual": actual, "previous": previous,
                         "unit": "%", "basis": "policy-rate", "changePercentagePoints": round(actual - previous, 4),
                         "consensus": None, "excerpt": match[0], "evidenceRole": "official-reported"}],
            "consensusState": "not-collected", "decisionAuthority": False}
