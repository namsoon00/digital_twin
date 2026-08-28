import json
import re
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple


SOURCE_REGISTRY_VERSION = "news-source-registry-v3-canonical-publisher"
SOURCE_TIERS = {"A", "B", "C", "D", "DISCOVERY_ONLY"}


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _slug(value: object) -> str:
    return re.sub(r"[^0-9a-z가-힣.]+", "-", _clean(value).casefold()).strip("-")


def _tier(value: object, primary: bool = False) -> str:
    text = _clean(value).upper().replace("-", "_")
    aliases = {
        "TRUSTED": "B",
        "STANDARD": "C",
        "LIMITED": "D",
        "UNKNOWN": "D",
        "DISCOVERY": "DISCOVERY_ONLY",
    }
    normalized = aliases.get(text, text)
    if primary and normalized not in SOURCE_TIERS:
        return "A"
    return normalized if normalized in SOURCE_TIERS else "D"


def trust_state_for_tier(tier: object) -> str:
    return {
        "A": "trusted",
        "B": "trusted",
        "C": "standard",
        "D": "limited",
        "DISCOVERY_ONLY": "unknown",
    }.get(_tier(tier), "unknown")


@dataclass(frozen=True)
class SourceRegistryEntry:
    publisher_id: str
    name: str
    domains: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    tier: str = "D"
    publisher_type: str = "publisher"
    default_content_type: str = "reporting"

    @property
    def primary(self) -> bool:
        return self.publisher_type == "official"

    @property
    def source_trust_state(self) -> str:
        return trust_state_for_tier(self.tier)

    def to_dict(self) -> Dict[str, object]:
        return {
            "publisherId": self.publisher_id,
            "name": self.name,
            "domains": list(self.domains),
            "aliases": list(self.aliases),
            "tier": self.tier,
            "sourceTrustState": self.source_trust_state,
            "publisherType": self.publisher_type,
            "defaultContentType": self.default_content_type,
            "primary": self.primary,
        }


def _entry(
    publisher_id: str,
    name: str,
    domains: Iterable[str],
    aliases: Iterable[str],
    tier: str,
    publisher_type: str = "publisher",
    content_type: str = "reporting",
) -> SourceRegistryEntry:
    return SourceRegistryEntry(
        publisher_id,
        name,
        tuple(_clean(value).casefold().removeprefix("www.") for value in domains if _clean(value)),
        tuple(_clean(value).casefold() for value in aliases if _clean(value)),
        _tier(tier, publisher_type == "official"),
        publisher_type,
        content_type,
    )


DEFAULT_SOURCE_ENTRIES = (
    _entry("opendart", "OpenDART", ("dart.fss.or.kr", "opendart.fss.or.kr"), ("opendart", "dart"), "A", "official", "official-filing"),
    _entry("sec-edgar", "SEC EDGAR", ("sec.gov",), ("sec", "edgar"), "A", "official", "official-filing"),
    _entry("federal-reserve", "Federal Reserve", ("federalreserve.gov",), ("federal reserve", "fomc"), "A", "official", "official-release"),
    _entry("bank-of-korea", "한국은행", ("bok.or.kr",), ("한국은행", "bank of korea"), "A", "official", "official-release"),
    _entry("reuters", "Reuters", ("reuters.com",), ("reuters", "로이터"), "B"),
    _entry("bloomberg", "Bloomberg", ("bloomberg.com",), ("bloomberg", "블룸버그"), "B"),
    _entry("bloomberg-law", "Bloomberg Law", ("bloomberglaw.com",), ("bloomberg law",), "B"),
    _entry("yonhap", "연합뉴스", ("yna.co.kr",), ("연합뉴스", "yonhap"), "B"),
    _entry("wall-street-journal", "The Wall Street Journal", ("wsj.com",), ("wall street journal", "wsj"), "B"),
    _entry("cnbc", "CNBC", ("cnbc.com",), ("cnbc",), "B"),
    _entry("barrons", "Barron's", ("barrons.com",), ("barrons", "barron's", "barrons.com"), "B"),
    _entry("axios", "Axios", ("axios.com",), ("axios",), "B"),
    _entry("ytn", "YTN", ("ytn.co.kr",), ("ytn", "www.ytn.co.kr"), "C"),
    _entry("marketwatch", "MarketWatch", ("marketwatch.com",), ("marketwatch",), "C"),
    _entry("yahoo-finance", "Yahoo Finance", ("finance.yahoo.com",), ("yahoo finance", "yahoo finance uk"), "C"),
    _entry("quartz", "Quartz", ("qz.com",), ("quartz",), "C"),
    _entry("investors-business-daily", "Investor's Business Daily", ("investors.com",), ("investor's business daily", "investors business daily", "ibd"), "C"),
    _entry("marketbeat", "MarketBeat", ("marketbeat.com",), ("marketbeat",), "C", content_type="analysis"),
    _entry("the-street", "TheStreet", ("thestreet.com",), ("thestreet", "the street"), "C", content_type="analysis"),
    _entry("zacks", "Zacks", ("zacks.com",), ("zacks",), "C", content_type="analysis"),
    _entry("trefis", "Trefis", ("trefis.com",), ("trefis",), "C", content_type="analysis"),
    _entry("verdict", "Verdict", ("verdict.co.uk",), ("verdict",), "C"),
    _entry("guru-focus", "GuruFocus", ("gurufocus.com",), ("gurufocus", "guru focus"), "C", content_type="analysis"),
    _entry("coin-desk", "CoinDesk", ("coindesk.com", "videos.coindesk.com"), ("coindesk",), "C"),
    _entry("decrypt", "Decrypt", ("decrypt.co",), ("decrypt",), "C"),
    _entry("seoul-economic-daily", "서울경제", ("sedaily.com",), ("서울경제",), "C"),
    _entry("seoul-economic-tv", "서울경제TV", ("sentv.co.kr",), ("서울경제tv", "서울경제티브이", "(주) 서울경제티브이"), "C"),
    _entry("financial-news", "파이낸셜뉴스", ("fnnews.com",), ("파이낸셜뉴스",), "C"),
    _entry("maeil-business", "매일경제", ("mk.co.kr",), ("매일경제",), "C"),
    _entry("korea-economic-daily", "한국경제", ("hankyung.com",), ("한국경제",), "C"),
    _entry("electronic-times", "전자신문", ("etnews.com",), ("전자신문",), "C"),
    _entry("newsis", "뉴시스", ("newsis.com",), ("뉴시스",), "C"),
    _entry("money-today", "머니투데이", ("mt.co.kr",), ("머니투데이", "moneytoday"), "C"),
    _entry("mtn-news", "MTN NEWS", ("news.mtn.co.kr", "mtn.co.kr"), ("mtn news", "머니투데이방송"), "C"),
    _entry("yonhap-infomax", "연합인포맥스", ("news.einfomax.co.kr", "einfomax.co.kr"), ("연합인포맥스", "yonhap infomax"), "B"),
    _entry("newspim", "뉴스핌", ("newspim.com",), ("뉴스핌", "newspim"), "C"),
    _entry("mydaily", "마이데일리", ("mydaily.co.kr",), ("마이데일리", "mydaily"), "C"),
    _entry("twenty-four-seven-wall-st", "24/7 Wall St.", ("247wallst.com",), ("24/7 wall st", "247 wall st"), "C", content_type="analysis"),
    _entry("proactive-investors", "Proactive Investors", ("proactiveinvestors.com", "proactiveinvestors.co.uk", "proactiveinvestors.com.au"), ("proactive investors",), "C"),
    _entry("motley-fool", "The Motley Fool", ("fool.com",), ("motley fool", "the motley fool"), "C", content_type="analysis"),
    _entry("seeking-alpha", "Seeking Alpha", ("seekingalpha.com",), ("seeking alpha",), "C", content_type="analysis"),
    _entry("beincrypto", "BeInCrypto", ("beincrypto.com",), ("beincrypto",), "C"),
    _entry("investors-hub", "InvestorsHub", ("investorshub.advfn.com", "advfn.com"), ("investorshub", "advfn"), "D"),
    _entry("stocktwits", "Stocktwits", ("stocktwits.com",), ("stocktwits",), "D", content_type="social-reporting"),
    _entry("moby", "Moby", ("moby.co", "app.moby.co"), ("moby",), "D", content_type="analysis"),
    _entry("crypto-prowl", "CryptoProwl", ("cryptoprowl.com",), ("cryptoprowl", "crypto prowl"), "D"),
    _entry("google-news", "Google News", ("news.google.com",), ("google news", "google news kr", "google news us", "google_rss"), "DISCOVERY_ONLY", "discovery", "aggregation"),
    _entry("gdelt", "GDELT", ("gdeltproject.org",), ("gdelt",), "DISCOVERY_ONLY", "discovery", "aggregation"),
)


def _custom_entries(value: object) -> Tuple[SourceRegistryEntry, ...]:
    decoded = value if isinstance(value, dict) else None
    raw = _clean(value) if not isinstance(value, dict) else ""
    if not raw and not isinstance(decoded, dict):
        return ()
    if decoded is None and raw.startswith("{"):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            decoded = None
    rows = []
    if isinstance(decoded, dict):
        source_rows = decoded.items()
    else:
        source_rows = []
        for raw_line in str(value or "").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, encoded = line.split("=", 1)
                profile: Dict[str, object] = {}
                parts = [part.strip() for part in encoded.split(",") if part.strip()]
                if parts:
                    profile["tier"] = parts[0]
                for part in parts[1:]:
                    if "=" in part:
                        field, field_value = part.split("=", 1)
                        profile[field.strip()] = field_value.strip()
                    elif part.casefold() == "primary":
                        profile["primary"] = True
                source_rows.append((key, profile))
    for matcher, raw_profile in source_rows:
        profile = dict(raw_profile) if isinstance(raw_profile, dict) else {"tier": raw_profile}
        publisher_id = _slug(profile.get("publisherId") or profile.get("origin") or matcher)
        name = _clean(profile.get("name") or matcher)
        domains = profile.get("domains") or ([matcher] if "." in str(matcher) else [])
        aliases = profile.get("aliases") or [matcher]
        if isinstance(domains, str):
            domains = [part.strip() for part in domains.split(",")]
        if isinstance(aliases, str):
            aliases = [part.strip() for part in aliases.split(",")]
        primary = str(profile.get("primary") or "").casefold() in {"1", "true", "yes", "on"} or profile.get("primary") is True
        publisher_type = _clean(profile.get("publisherType") or ("official" if primary else "publisher"))
        rows.append(_entry(
            publisher_id,
            name,
            domains,
            aliases,
            _tier(profile.get("tier"), primary),
            publisher_type,
            _clean(profile.get("contentType") or ("official-filing" if primary else "reporting")),
        ))
    return tuple(rows)


class SourceRegistry:
    def __init__(self, custom: object = ""):
        custom_entries = _custom_entries(custom)
        custom_ids = {entry.publisher_id for entry in custom_entries}
        self.entries = custom_entries + tuple(entry for entry in DEFAULT_SOURCE_ENTRIES if entry.publisher_id not in custom_ids)

    def by_host(self, host: object) -> Optional[SourceRegistryEntry]:
        normalized = _clean(host).casefold().split(":")[0].removeprefix("www.").removeprefix("m.")
        matches = [
            (len(domain), entry)
            for entry in self.entries
            for domain in entry.domains
            if normalized == domain or normalized.endswith("." + domain)
        ]
        return max(matches, default=(0, None), key=lambda row: row[0])[1]

    def by_name(self, value: object) -> Optional[SourceRegistryEntry]:
        normalized = _clean(value).casefold()
        if not normalized:
            return None
        exact = [entry for entry in self.entries if normalized == entry.name.casefold() or normalized in entry.aliases]
        if exact:
            return exact[0]

        def contains_alias(alias: str) -> bool:
            # Publisher aliases are names, not arbitrary substrings. In
            # particular, the SEC alias must never match "SecurityWeek".
            return bool(re.search(
                r"(?<![0-9a-z가-힣])" + re.escape(alias) + r"(?![0-9a-z가-힣])",
                normalized,
                re.IGNORECASE,
            ))

        matches = [
            (len(alias), entry)
            for entry in self.entries
            for alias in entry.aliases
            if alias and contains_alias(alias)
        ]
        if matches:
            return max(matches, key=lambda row: row[0])[1]
        return self.by_host(normalized)


def unknown_entry(host: object = "", name: object = "") -> SourceRegistryEntry:
    normalized_host = _clean(host).casefold().removeprefix("www.")
    label = _clean(name)
    if not label and normalized_host:
        label = normalized_host.split(".")[0].replace("-", " ").title()
    label = label or "Unknown"
    return _entry(_slug(normalized_host or label) or "unknown", label, (normalized_host,) if normalized_host else (), (label,), "D")
