import re
from dataclasses import dataclass, field
from typing import Iterable, List


ARTICLE_BODY_QUALITY_VERSION = "news-body-quality-v3"
CONTAMINATION_PATTERNS = (
    ("publisher-navigation", re.compile(r"\b(?:continue reading|read more|more from|recommended stor(?:y|ies))\b", re.IGNORECASE)),
    ("investment-promotion", re.compile(r"\b(?:is now the time to buy|missed nvidia|top \d+ stocks to buy)\b", re.IGNORECASE)),
    ("advertising-block", re.compile(r"\b(?:advertisement|sponsored content|paid post)\b|광고\s*(?:입니다|문의|제휴)", re.IGNORECASE)),
    ("related-news-tail", re.compile(r"(?:관련\s*뉴스|함께\s*본\s*뉴스|추천\s*기사|많이\s*본\s*기사|S&P\s*500\s*기업\s*중)", re.IGNORECASE)),
    ("live-widget", re.compile(r"\[\s*스팟\s*Live\s*\]", re.IGNORECASE)),
    ("publisher-navigation", re.compile(r"(?:최신\s*뉴스|주요\s*뉴스|실시간\s*인기|기사\s*더보기|다음\s*기사|what are you looking for)", re.IGNORECASE)),
)
MOJIBAKE_RE = re.compile(r"(?:�|â€™|â€œ|â€|Ã.|\ufffd)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|\n+")


@dataclass(frozen=True)
class ArticleBodyQuality:
    state: str
    passed: bool
    reason: str
    char_count: int
    issues: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": ARTICLE_BODY_QUALITY_VERSION,
            "state": self.state,
            "passed": self.passed,
            "reason": self.reason,
            "charCount": self.char_count,
            "issues": list(self.issues),
        }


def inspect_article_body(
    article_text: object,
    minimum_chars: int = 280,
    target_terms: Iterable[object] = (),
) -> ArticleBodyQuality:
    raw_text = str(article_text or "")
    text = " ".join(raw_text.split())
    if not text:
        return ArticleBodyQuality("unavailable", False, "원문 본문이 수집되지 않았습니다.", 0, ["body-missing"])
    char_count = len(text)
    minimum = max(80, min(5000, int(minimum_chars or 280)))
    issues: List[str] = []
    if char_count < minimum:
        issues.append("body-too-short")
    if MOJIBAKE_RE.search(text):
        issues.append("text-encoding-corrupt")
    for issue, pattern in CONTAMINATION_PATTERNS:
        match = pattern.search(text)
        if match and (match.start() >= 80 or issue in {"investment-promotion", "live-widget"}):
            issues.append(issue)
    sentences = [part.strip().casefold() for part in SENTENCE_SPLIT_RE.split(text) if len(part.strip()) >= 24]
    if len(sentences) >= 3 and len(set(sentences)) / len(sentences) < 0.72:
        issues.append("repeated-content")
    lines = [" ".join(part.split()) for part in raw_text.splitlines() if len(" ".join(part.split())) >= 8]
    headline_like = [
        line for line in lines
        if len(line) <= 120 and not re.search(r"[.!?。！？]['\"]?$|(?:다|했다|됐다|입니다)[.!。]?$", line)
    ]
    aliases = [str(value or "").strip() for value in target_terms or [] if len(str(value or "").strip()) >= 2]
    target_line_count = len([
        line for line in lines
        if any(re.search(re.escape(alias), line, re.IGNORECASE) for alias in aliases)
    ])
    if len(lines) >= 6 and len(headline_like) >= 4 and len(headline_like) / len(lines) >= 0.55:
        issues.append("headline-list-contamination")
    if aliases and len(lines) >= 6 and target_line_count <= 1 and len(headline_like) >= 4:
        issues.append("target-context-diluted")
    issues = list(dict.fromkeys(issues))
    if issues:
        labels = {
            "body-too-short": "정제된 본문이 너무 짧습니다",
            "text-encoding-corrupt": "본문 문자 인코딩이 손상되었습니다",
            "publisher-navigation": "기사 뒤 탐색 문구가 본문에 섞였습니다",
            "investment-promotion": "투자 홍보 문구가 본문에 섞였습니다",
            "advertising-block": "광고 문구가 본문에 섞였습니다",
            "related-news-tail": "다른 기사 목록이 본문에 섞였습니다",
            "live-widget": "실시간 위젯 문구가 본문에 섞였습니다",
            "repeated-content": "반복 문장이 많습니다",
            "headline-list-contamination": "다른 기사 제목 목록이 본문에 섞였습니다",
            "target-context-diluted": "대상 종목과 관련된 본문 비중이 너무 낮습니다",
        }
        return ArticleBodyQuality(
            "limited",
            False,
            " · ".join(labels.get(issue, issue) for issue in issues),
            char_count,
            issues,
        )
    return ArticleBodyQuality("usable", True, "정제된 원문 본문을 확보했습니다.", char_count, [])
