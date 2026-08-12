from dataclasses import dataclass, field
from typing import Dict


ARTICLE_COLLECTED = "news.article_collected"
ARTICLE_REJECTED = "news.article_rejected"
ARTICLE_ANALYZED = "news.article_analyzed"
STORY_CREATED = "news.story_created"
STORY_UPDATED = "news.story_updated"
ARTICLE_ALERT_ELIGIBLE = "news.article_alert_eligible"
ARTICLE_REASONING_ELIGIBLE = "news.article_reasoning_eligible"
ARTICLE_RETRACTED = "news.article_retracted"


@dataclass(frozen=True)
class NewsIntelligenceEvent:
    name: str
    article_id: str
    payload: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "articleId": self.article_id, "payload": dict(self.payload)}
