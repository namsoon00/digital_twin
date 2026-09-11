from dataclasses import dataclass, field
from typing import Dict

from digital_twin.modules.news_intelligence.domain.event_types import (
    ARTICLE_ALERT_ELIGIBLE,
    ARTICLE_ANALYZED,
    ARTICLE_COLLECTED,
    ARTICLE_REASONING_ELIGIBLE,
    ARTICLE_REJECTED,
    ARTICLE_RETRACTED,
    STORY_CREATED,
    STORY_UPDATED,
)

@dataclass(frozen=True)
class NewsIntelligenceEvent:
    name: str
    article_id: str
    payload: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {"name": self.name, "articleId": self.article_id, "payload": dict(self.payload)}
