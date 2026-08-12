from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class NewsTarget:
    symbol: str
    name: str = ""
    market: str = ""

    def normalized_symbol(self) -> str:
        return str(self.symbol or "").upper().strip()


@dataclass(frozen=True)
class NewsArticle:
    article_id: str
    target: NewsTarget
    title: str
    body: str = ""
    summary: str = ""
    publisher: str = ""
    distribution_channel: str = ""
    canonical_url: str = ""
    published_at: str = ""
    lifecycle_state: str = "active"
    metadata: Dict[str, object] = field(default_factory=dict)

    def text(self) -> str:
        return " ".join(part.strip() for part in (self.title, self.body or self.summary) if part and part.strip())
