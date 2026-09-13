import json
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from digital_twin.modules.market_data.public import CollectionPartition, DatasetDescriptor, ExternalSubject, SourceObservation
from digital_twin.modules.market_data.domain.bls_statistics import BLS_API_URL, BLS_SERIES, parse_bls_statistics
from ...external_signal_utils import guarded_external_call


def fetch_statistics(payload):
    request = Request(BLS_API_URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "User-Agent": "OrbitAlpha/1.0 (official statistics reader)"})
    with urlopen(request, timeout=15) as response:
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError("BLS statistics exceeds size limit")
        return json.loads(body)


class BlsStatisticsAdapter:
    descriptor = DatasetDescriptor(
        dataset_id="official.bls-statistics", provider_id="bls-public-api", capability="official-statistical-vintage",
        cadence_seconds=21600, freshness_seconds=86400, priority=40, rate_limit_seconds=60,
        daily_request_budget=10, failure_threshold=2, circuit_cooldown_seconds=3600,
        enabled_setting="externalBlsStatisticsEnabled", max_partitions=1,
        revision_mode="changes", materiality_policy="calendar-reference",
    )

    def __init__(self, fetch=None, now=None):
        self.fetch_payload = fetch or fetch_statistics
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.guard_state = {}

    def partitions(self, subjects, settings):
        return [CollectionPartition(self.descriptor.dataset_id, "bls-statistics", ExternalSubject("release:bls-statistics", source="bls-public-api"), self.descriptor.priority)]

    def fetch(self, job, settings):
        now = self.now()
        request = {"seriesid": list(BLS_SERIES), "startyear": str(now.year - 2), "endyear": str(now.year)}
        response = guarded_external_call(settings, "bls-public-api", "monthly-series-batch",
            lambda: self.fetch_payload(request), state=self.guard_state, rate_limit_seconds=60)
        result = parse_bls_statistics(response, now)
        fetched = self.now().isoformat().replace("+00:00", "Z")
        return SourceObservation(dataset_id=self.descriptor.dataset_id, provider_id=self.descriptor.provider_id,
            subject_key=job.subject.subject_key, source_revision=result["sourceHash"], source_as_of="", fetched_at=fetched,
            payload={"officialStatistics": result}, quality={"dataUsable": True, "decisionAuthority": False}, watermark={"sourceHash": result["sourceHash"]})
