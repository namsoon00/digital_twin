"""Bounded cached-source checks; never fetch providers or invoke a model."""
import re
from time import monotonic

from ..domain.research_material import research_material_fingerprint, research_source_packet


class ResearchMaterialReader:
    def __init__(self, repository, cache_seconds=300, route_reader=None):
        self.repository, self.cache_seconds, self.cache = repository, cache_seconds, {}
        self.route_reader = route_reader

    def with_route(self, case, result):
        result = {**result, "fingerprints": list(result["fingerprints"])}
        if self.route_reader:
            try:
                opportunity = self.route_reader(case)
            except Exception:
                opportunity = {}
            if opportunity:
                result["sourceRouteOpportunity"] = opportunity
        return result

    def __call__(self, case):
        sources = tuple(sorted(set((case.get("researchRequest") or {}).get("sourceTypes", []))))
        # Calendar dates explicitly present in the original scope are binding.
        periods = tuple(sorted(set(re.findall(r"\d{4}-\d{2}-\d{2}", (case.get("questionContract") or {}).get("period", "")))))
        key = (case["symbol"], sources, periods)
        cached = self.cache.get(key)
        if cached and monotonic() - cached[0] < self.cache_seconds:
            return self.with_route(case, cached[1])
        kinds = []
        if "official-filing" in sources:
            kinds += ["financial-fact", "filing", "disclosure", "sec-filing", "issuer-ir"]
        if "news" in sources:
            kinds += ["news"]
        fingerprints = set()
        for kind in kinds:
            for item in self.repository.latest(symbol=case["symbol"], kind=kind, limit=16):
                packet = research_source_packet(item)
                period = packet.get("periodEnd") or (packet.get("reportDate")
                    if packet.get("documentType") in {"10-K", "10-Q", "20-F", "40-F"} else "")
                if item.symbol != case["symbol"] or (periods and period not in periods):
                    continue
                signature = research_material_fingerprint(packet)
                if signature:
                    fingerprints.add(signature)
        result = {"version": "research-material-v1", "status": "ready", "fingerprints": sorted(fingerprints)}
        if len(self.cache) >= 256:
            self.cache.clear()
        self.cache[key] = (monotonic(), result)
        return self.with_route(case, result)
