from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PublisherIdentity:
    publisher: str
    distribution_channel: str
    canonical_host: str

    def to_dict(self) -> dict:
        return {
            "publisher": self.publisher,
            "distributionChannel": self.distribution_channel,
            "canonicalHost": self.canonical_host,
        }


def publisher_identity(payload: dict, source: object = "", provider: object = "", url: object = "") -> PublisherIdentity:
    payload = payload if isinstance(payload, dict) else {}
    canonical_url = str(payload.get("articleCanonicalUrl") or url or "").strip()
    try:
        host = (urlsplit(canonical_url).hostname or "").lower()
    except ValueError:
        host = ""
    publisher = str(
        payload.get("articlePublisher")
        or payload.get("sourcePublisher")
        or source
        or host
        or "Unknown"
    ).strip()
    channel = str(payload.get("distributionChannel") or provider or payload.get("provider") or "").strip()
    return PublisherIdentity(publisher, channel, host)
