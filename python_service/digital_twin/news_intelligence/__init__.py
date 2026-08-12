"""Independent news-intelligence bounded context.

Consumers should import the stable functions exposed by ``public`` instead of
depending on implementation modules.
"""

from .public import (
    NEWS_INTELLIGENCE_VERSION,
    annotate_news_eligibility,
    assess_news_eligibility,
    inspect_article_body,
    annotate_source_provenance,
    publisher_identity,
    resolve_target_entity,
    resolve_source_provenance,
    SOURCE_REGISTRY_VERSION,
    SourceRegistry,
    story_identity,
)
from .application.analyze_article import annotate_evidence_eligibility, evidence_eligibility

__all__ = [
    "NEWS_INTELLIGENCE_VERSION",
    "annotate_news_eligibility",
    "assess_news_eligibility",
    "inspect_article_body",
    "annotate_source_provenance",
    "publisher_identity",
    "resolve_target_entity",
    "resolve_source_provenance",
    "SOURCE_REGISTRY_VERSION",
    "SourceRegistry",
    "story_identity",
    "annotate_evidence_eligibility",
    "evidence_eligibility",
]
