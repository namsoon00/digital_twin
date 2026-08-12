import re
from dataclasses import dataclass, field
from typing import List


ARTICLE_BODY_QUALITY_VERSION = "news-body-quality-v2"
CONTAMINATION_PATTERNS = (
    ("publisher-navigation", re.compile(r"\b(?:continue reading|read more|more from|recommended stor(?:y|ies))\b", re.IGNORECASE)),
    ("investment-promotion", re.compile(r"\b(?:is now the time to buy|missed nvidia|top \d+ stocks to buy)\b", re.IGNORECASE)),
    ("advertising-block", re.compile(r"\b(?:advertisement|sponsored content|paid post)\b|광고\s*(?:입니다|문의|제휴)", re.IGNORECASE)),
    ("related-news-tail", re.compile(r"(?:관련\s*뉴스|함께\s*본\s*뉴스|추천\s*기사|많이\s*본\s*기사|S&P\s*500\s*기업\s*중)", re.IGNORECASE)),
    ("live-widget", re.compile(r"\[\s*스팟\s*Live\s*\]", re.IGNORECASE)),
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


def inspect_article_body(article_text: object, minimum_chars: int = 280) -> ArticleBodyQuality:
    text = " ".join(str(article_text or "").split())
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
        }
        return ArticleBodyQuality(
            "limited",
            False,
            " · ".join(labels.get(issue, issue) for issue in issues),
            char_count,
            issues,
        )
    return ArticleBodyQuality("usable", True, "정제된 원문 본문을 확보했습니다.", char_count, [])
