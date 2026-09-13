from datetime import datetime, timezone

from digital_twin.modules.market_data.public import CollectionPartition, DatasetDescriptor, ExternalSubject, SourceObservation
from digital_twin.modules.market_data.domain.official_release import RELEASE_URLS, latest_fomc_statement_url, parse_official_release
from ...external_signal_utils import default_text_fetcher, external_call_target, guarded_external_call


class OfficialReleaseAdapter:
    """Runs behind the existing leased, rate-limited external-data worker."""

    def __init__(self, source, fetch_text=None, now=None):
        if source not in {"bls", "fomc"}:
            raise ValueError("Unsupported release source")
        self.source = source
        self.fetch_text = fetch_text or default_text_fetcher
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.guard_state = {}
        self.descriptor = DatasetDescriptor(
            dataset_id="official." + source + "-release", provider_id="official-" + source,
            capability="official-release", cadence_seconds=1800, freshness_seconds=7200,
            priority=45, rate_limit_seconds=5, daily_request_budget=120,
            failure_threshold=2, circuit_cooldown_seconds=1800,
            enabled_setting="externalOfficialReleaseEnabled", cadence_setting="externalOfficialReleaseCadenceSeconds",
            max_partitions=2, revision_mode="changes", materiality_policy="calendar-reference",
        )

    def partitions(self, _subjects, settings):
        keys = ["cpi", "employment"] if self.source == "bls" else ["fomc"]
        return [CollectionPartition(self.descriptor.dataset_id, key, ExternalSubject("release:" + key, source="official-release"), self.descriptor.priority) for key in keys]

    def fetch(self, job, settings):
        indicator = job.partition_key
        if indicator not in ({"cpi", "employment"} if self.source == "bls" else {"fomc"}):
            raise ValueError("Official release partition mismatch")
        headers = {"Accept": "text/html", "User-Agent": "OrbitAlpha/1.0 (official release reader)"}
        try:
            timeout = max(1, min(30, float(settings.get("externalApiTimeoutSeconds") or 12)))
        except (TypeError, ValueError):
            timeout = 12
        url = RELEASE_URLS[indicator]
        def fetch(url):
            return guarded_external_call(settings, self.descriptor.provider_id, external_call_target(url),
                lambda: self.fetch_text(url, headers, timeout), state=self.guard_state, rate_limit_seconds=5)

        markup = fetch(url)
        if indicator == "fomc":
            url = latest_fomc_statement_url(markup, self.now())
            markup = fetch(url)
        result = parse_official_release(indicator, markup, url, self.now())
        fetched_at = self.now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        return SourceObservation(
            dataset_id=self.descriptor.dataset_id, provider_id=self.descriptor.provider_id,
            subject_key=job.subject.subject_key, source_revision=result["releasedDate"] + ":" + result["sourceHash"],
            source_as_of=result["releasedAt"] or result["releasedDate"], fetched_at=fetched_at,
            payload={"officialRelease": result}, quality={"dataUsable": True, "usage": "calendar-reference-only", "decisionAuthority": False},
            watermark={"releasedDate": result["releasedDate"], "sourceHash": result["sourceHash"]},
        )
