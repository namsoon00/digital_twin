import re
from dataclasses import dataclass, field
from typing import Iterable, List

from .entity import other_company_aliases, target_aliases, unique_aliases


ENTITY_RESOLUTION_VERSION = "news-entity-resolution-v1"
WORD_BOUNDARY = r"A-Za-z0-9_가-힣"
BROKER_SUFFIXES = ("증권", "투자증권", "자산운용", "보험", "카드")
ROUNDUP_MARKERS = (
    "stocks to watch",
    "stocks on the move",
    "stock market today",
    "rally",
    "surge as market",
    "종목 주목",
    "특징주",
    "급등 종목",
)
LEADING_SUBJECT_RE = re.compile(
    r"^\s*(?:\[[^\]]{1,40}\]\s*)?(?P<subject>[A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,4})\s+"
    r"(?P<verb>stock|shares|launches|launched|reports|reported|raises|raised|wins|won|unveils|unveiled|"
    r"acquires|acquired|secures|secured|announces|announced|partners|partnered|files|filed|jumps|jumped|"
    r"rises|rose|falls|fell|gains|gained|slides|slid|surges|surged|tumbles|tumbled)\b",
    re.IGNORECASE,
)
COMPARISON_SUBJECT_RE = re.compile(
    r"^\s*(?P<subject>.{2,70}?)\s*(?:\([A-Z.]{1,8}\))?\s+(?:vs\.?|versus)\s+",
    re.IGNORECASE,
)


def alias_pattern(alias: object) -> re.Pattern:
    term = str(alias or "").strip()
    if re.search(r"[가-힣]", term):
        # Korean company names are commonly followed by a grammatical particle.
        # Permit those particles while rejecting another corporate-name token,
        # such as 현대차증권 being interpreted as 현대차.
        return re.compile(
            r"(?<![" + WORD_BOUNDARY + r"])(?:" + re.escape(term) + r")(?=$|[^" + WORD_BOUNDARY + r"]|[은는이가을를의와과에도로서만])",
            re.IGNORECASE,
        )
    return re.compile(r"(?<![" + WORD_BOUNDARY + r"])(?:" + re.escape(term) + r")(?![" + WORD_BOUNDARY + r"])", re.IGNORECASE)


def matched_aliases(value: object, aliases: Iterable[object]) -> List[str]:
    text = str(value or "")
    return [alias for alias in unique_aliases(aliases) if alias_pattern(alias).search(text)]


def _broker_alias_hits(value: object, aliases: Iterable[object]) -> List[str]:
    text = str(value or "")
    hits: List[str] = []
    for alias in unique_aliases(aliases):
        for suffix in BROKER_SUFFIXES:
            if re.search(re.escape(alias) + r"\s*" + re.escape(suffix), text, re.IGNORECASE):
                hits.append(alias + suffix)
    return unique_aliases(hits)


def _leading_other_subject(title: str, aliases: Iterable[str]) -> str:
    comparison = COMPARISON_SUBJECT_RE.search(title)
    if comparison:
        subject = str(comparison.group("subject") or "").strip(" ,:-")
        if subject and not matched_aliases(subject, aliases):
            return subject
    match = LEADING_SUBJECT_RE.search(title)
    if not match:
        return ""
    subject = str(match.group("subject") or "").strip()
    return "" if matched_aliases(subject, aliases) else subject


def _roundup_title(title: str, symbol: str, aliases: Iterable[str]) -> bool:
    lowered = title.casefold()
    if not any(marker in lowered for marker in ROUNDUP_MARKERS):
        return False
    company_count = 1 if matched_aliases(title, aliases) else 0
    for alias in other_company_aliases(symbol):
        if alias_pattern(alias).search(title):
            company_count += 1
        if company_count >= 2:
            return True
    return False


@dataclass(frozen=True)
class TargetEntityResolution:
    symbol: str
    role: str
    state: str
    target_subject_confirmed: bool
    title_aliases: List[str] = field(default_factory=list)
    body_aliases: List[str] = field(default_factory=list)
    other_subject: str = ""
    reason_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": ENTITY_RESOLUTION_VERSION,
            "symbol": self.symbol,
            "role": self.role,
            "state": self.state,
            "targetSubjectConfirmed": self.target_subject_confirmed,
            "titleAliases": list(self.title_aliases),
            "bodyAliases": list(self.body_aliases),
            "otherSubject": self.other_subject,
            "reasonCodes": list(self.reason_codes),
        }


def resolve_target_entity(
    title: object,
    body_or_summary: object,
    symbol: object,
    name: object = "",
    aliases: Iterable[object] = None,
) -> TargetEntityResolution:
    normalized_symbol = str(symbol or "").upper().strip()
    known_aliases = target_aliases(normalized_symbol, name, aliases)
    title_text = str(title or "").strip()
    body_text = str(body_or_summary or "").strip()
    broker_hits = _broker_alias_hits(title_text, known_aliases)
    title_hits = matched_aliases(title_text, known_aliases)
    body_hits = matched_aliases(body_text, known_aliases)
    other_subject = _leading_other_subject(title_text, known_aliases)
    reasons: List[str] = []

    if broker_hits and not title_hits:
        reasons.append("alias-is-broker-name")
        return TargetEntityResolution(
            normalized_symbol,
            "broker",
            "rejected",
            False,
            [],
            body_hits,
            broker_hits[0],
            reasons,
        )
    if title_hits and _roundup_title(title_text, normalized_symbol, known_aliases):
        reasons.append("multi-company-roundup")
        return TargetEntityResolution(
            normalized_symbol,
            "mentioned",
            "ambiguous",
            False,
            title_hits,
            body_hits,
            "multi-company-roundup",
            reasons,
        )
    if title_hits and other_subject:
        reasons.append("other-company-is-leading-subject")
        return TargetEntityResolution(
            normalized_symbol,
            "mentioned",
            "ambiguous",
            False,
            title_hits,
            body_hits,
            other_subject,
            reasons,
        )
    if title_hits:
        return TargetEntityResolution(
            normalized_symbol,
            "subject",
            "confirmed",
            True,
            title_hits,
            body_hits,
        )
    if body_hits:
        reasons.append("target-only-in-body")
        return TargetEntityResolution(
            normalized_symbol,
            "mentioned",
            "ambiguous",
            False,
            [],
            body_hits,
            "",
            reasons,
        )
    reasons.append("target-not-found")
    return TargetEntityResolution(
        normalized_symbol,
        "unrelated",
        "rejected",
        False,
        [],
        [],
        "",
        reasons,
    )
