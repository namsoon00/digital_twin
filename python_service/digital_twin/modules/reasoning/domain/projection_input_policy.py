"""Bounded projection input and cache policy; no runtime settings or stores."""

from __future__ import annotations

from typing import Dict
from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectionInputPolicy:
    settings: Dict[str, object]
    graph_assembly_cache_store: bool = False

    def graph_assembly_cache_enabled(self) -> bool:
        value = self.settings.get("ontologyProjectionGraphCacheEnabled")
        if value is None:
            # Direct recorder construction in focused unit tests remains
            # deterministic. The managed runtime explicitly enables the
            # cache through runtime_settings().
            return False
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def runtime_context_cache_enabled(self) -> bool:
        value = self.settings.get("ontologyProjectionRuntimeContextCacheEnabled")
        if value is None:
            return False
        return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}

    def runtime_context_cache_ttl_seconds(self) -> float:
        try:
            value = float(
                str(
                    self.settings.get("ontologyProjectionRuntimeContextCacheTtlSeconds")
                    or "120"
                )
            )
        except (TypeError, ValueError):
            value = 120.0
        return max(1.0, min(600.0, value))

    def runtime_context_cache_max_entries(self) -> int:
        try:
            value = int(
                float(
                    str(
                        self.settings.get(
                            "ontologyProjectionRuntimeContextCacheMaxEntries"
                        )
                        or "64"
                    )
                )
            )
        except (TypeError, ValueError):
            value = 64
        return max(1, min(256, value))

    def graph_assembly_cache_ttl_seconds(self) -> float:
        try:
            value = float(
                str(self.settings.get("ontologyProjectionGraphCacheTtlSeconds") or "45")
            )
        except (TypeError, ValueError):
            value = 45.0
        return max(1.0, min(300.0, value))

    def graph_assembly_cache_max_entries(self) -> int:
        try:
            value = int(
                float(
                    str(
                        self.settings.get("ontologyProjectionGraphCacheMaxEntries")
                        or "16"
                    )
                )
            )
        except (TypeError, ValueError):
            value = 16
        return max(1, min(128, value))

    def graph_assembly_persistent_cache_enabled(self) -> bool:
        """Enable only when the managed runtime supplies a local MySQL cache.

        Focused recorder tests intentionally construct no durable store. This
        keeps their graph assertions deterministic while production isolated
        workers can reuse an exact source assembly across process boundaries.
        """
        value = self.settings.get("ontologyProjectionGraphPersistentCacheEnabled")
        if value is None or not self.graph_assembly_cache_store:
            return False
        return str(value).strip().lower() not in {
            "",
            "0",
            "false",
            "no",
            "off",
            "disabled",
        }

    def graph_assembly_persistent_cache_ttl_seconds(self) -> float:
        try:
            value = float(
                str(
                    self.settings.get(
                        "ontologyProjectionGraphPersistentCacheTtlSeconds"
                    )
                    or "120"
                )
            )
        except (TypeError, ValueError):
            value = 120.0
        return max(1.0, min(300.0, value))

    def graph_assembly_persistent_cache_max_entries(self) -> int:
        try:
            value = int(
                float(
                    str(
                        self.settings.get(
                            "ontologyProjectionGraphPersistentCacheMaxEntries"
                        )
                        or "64"
                    )
                )
            )
        except (TypeError, ValueError):
            value = 64
        return max(1, min(256, value))

    def graph_assembly_persistent_cache_max_payload_bytes(self) -> int:
        try:
            value = int(
                float(
                    str(
                        self.settings.get(
                            "ontologyProjectionGraphPersistentCacheMaxPayloadBytes"
                        )
                        or 8 * 1024 * 1024
                    )
                )
            )
        except (TypeError, ValueError):
            value = 8 * 1024 * 1024
        return max(64 * 1024, min(32 * 1024 * 1024, value))

    def performance_setting(self, key: str, fallback: float) -> float:
        try:
            return float(str(self.settings.get(key) or fallback))
        except (TypeError, ValueError):
            return float(fallback)

    def decision_episode_context_per_symbol_limit(self) -> int:
        return self.integer_setting(
            "ontologyDecisionEpisodeContextPerSymbolLimit", 3, 1, 12
        )

    def decision_episode_context_maximum_episodes(self) -> int:
        return self.integer_setting(
            "ontologyDecisionEpisodeContextMaxEpisodes", 24, 1, 60
        )

    def decision_episode_context_hypothesis_limit(self) -> int:
        return self.integer_setting(
            "ontologyDecisionEpisodeContextHypothesisLimit", 3, 1, 8
        )

    def decision_episode_context_outcome_limit(self) -> int:
        return self.integer_setting(
            "ontologyDecisionEpisodeContextOutcomeLimit", 8, 1, 16
        )

    def decision_outcome_history_per_symbol_limit(self) -> int:
        return self.integer_setting(
            "ontologyDecisionOutcomeHistoryPerSymbolLimit", 120, 12, 500
        )

    def decision_outcome_history_maximum_episodes(self) -> int:
        return self.integer_setting(
            "ontologyDecisionOutcomeHistoryMaxEpisodes", 600, 12, 2000
        )

    def integer_setting(
        self, key: str, fallback: int, minimum: int, maximum: int
    ) -> int:
        try:
            value = int(float(str(self.settings.get(key) or fallback)))
        except (TypeError, ValueError):
            value = fallback
        return max(minimum, min(maximum, value))

    def hypothesis_lifecycle_abox_projection_enabled(self) -> bool:
        """Keep audit history out of realtime TypeDB input unless explicitly needed.

        Lifecycle records explain a completed generation; they are not source
        facts or native-rule inputs. Their compact prompt summary is attached
        after a verified generation by ``HypothesisLifecycleService``.
        """

        value = self.settings.get("ontologyHypothesisLifecycleAboxProjectionEnabled")
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}
